/*
 * Festina native runtime -- TLS translation unit for openSecurePort()
 * (claude.md #160): server-side TLS on top of the same plain-socket
 * connections festina_runtime_http.c already manages, via mbedTLS 2.x
 * (the generation actually installed/tested against -- see setup.md).
 *
 * This file supplies a seven-function hook table
 * (festina_tls_listener_new/_free, festina_tls_conn_new/_free,
 * festina_tls_handshake, festina_tls_recv/_send) that
 * festina_register_tls_hooks() wires into festina_runtime_http.c's own
 * g_tls_* function pointers -- the same cross-translation-unit,
 * opaque-function-pointer pattern g_audio_decoder/g_image_decoder
 * already use (festina_runtime.c/festina_set_audio_decoder), so a
 * program that never calls openSecurePort() never links mbedTLS at all.
 * codegen only emits the registration call (festina/codegen.py's
 * _emit_main_and_entry) when self.uses_https, and cli.py only compiles
 * and links this translation unit -- and only then adds
 * -lmbedtls/-lmbedx509/-lmbedcrypto to the link line -- when
 * gen.uses_https (see cli.py's _RUNTIME_FEATURES["https"]).
 *
 * SCOPE: server-only (openSecurePort has no client-TLS counterpart in
 * this version), one certificate/key pair per listening port (no SNI:
 * a program needing per-hostname certificates calls openSecurePort
 * once per port instead), no client-certificate / mutual TLS (server
 * authmode is unconditionally MBEDTLS_SSL_VERIFY_NONE -- this runtime
 * never asks a connecting client for a certificate), no ALPN (no
 * HTTP/2 negotiation -- every connection is plain HTTP/1.1-over-TLS,
 * same request/response scope festina_runtime_http.c's own top comment
 * already documents). TLS version is whatever mbedTLS's own
 * MBEDTLS_SSL_PRESET_DEFAULT negotiates (TLS 1.2 or 1.3, tested and
 * confirmed against a real TLS 1.2 client -- see claude.md #160's own
 * writeup for the standalone-harness verification this was checked
 * with before any codegen wiring was attempted).
 *
 * KEY MATERIAL: openSecurePort(port, key) takes ONE blob -- a
 * combined PEM buffer holding both the certificate (or a full chain,
 * leaf first) and the unencrypted private key, in either order.
 * mbedtls_x509_crt_parse and mbedtls_pk_parse_key are each handed the
 * WHOLE buffer and independently pick out only the block(s) they
 * recognize (confirmed directly with a standalone harness parsing a
 * real openssl-generated cert+key pair from one concatenated file --
 * see claude.md #160), so there is no need for this runtime to split
 * the blob itself. An encrypted (password-protected) private key is
 * rejected (mbedtls_pk_parse_key's own pwd/pwdlen left NULL/0 here) --
 * out of scope for v1, same as every other "the smallest thing that
 * works" cut this codebase already makes.
 *
 * NON-BLOCKING HANDSHAKE: every connection's underlying fd is already
 * non-blocking (festina_set_nonblocking, festina_runtime_http.c) by
 * the time festina_tls_conn_new is called, so mbedtls_ssl_handshake is
 * itself non-blocking -- it returns MBEDTLS_ERR_SSL_WANT_READ/
 * _WANT_WRITE rather than reading/writing to completion in one call,
 * exactly the resumable shape festina_conn_readable's own call site
 * (festina_runtime_http.c) needs to drive it across however many
 * poll() ticks a real handshake takes. mbedtls_net_send/_recv (mbedTLS's
 * own plain BSD-socket BIO callbacks, vendored nowhere else in this
 * runtime -- they ship as part of mbedTLS itself) are reused as-is
 * rather than hand-written, since a raw non-blocking fd is exactly
 * what they already wrap; only mbedtls_net_context's own one-field
 * struct ({int fd}, confirmed directly against mbedTLS's own header
 * rather than assumed) needs populating per connection.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "festina_runtime.h"

#include <mbedtls/ssl.h>
#include <mbedtls/entropy.h>
#include <mbedtls/ctr_drbg.h>
#include <mbedtls/x509_crt.h>
#include <mbedtls/pk.h>
#include <mbedtls/error.h>
#include <mbedtls/net_sockets.h>

/* One of these per openSecurePort(port, key) call -- the listener-wide
 * TLS config every connection accepted on that port shares (mbedTLS's
 * own convention: mbedtls_ssl_config is meant to be built once and
 * reused across many mbedtls_ssl_context instances, not rebuilt per
 * connection). */
typedef struct {
    mbedtls_ssl_config conf;
    mbedtls_x509_crt cert;
    mbedtls_pk_context pkey;
    mbedtls_entropy_context entropy;
    mbedtls_ctr_drbg_context ctr_drbg;
} FestinaTlsListener;

/* One of these per accepted TLS connection. */
typedef struct {
    mbedtls_ssl_context ssl;
    mbedtls_net_context net;   /* just {int fd} -- see this file's own
                                 * top comment */
} FestinaTlsConn;

/* mbedtls_strerror's own message, folded into festina_fail's existing
 * "name the real problem, don't just die silently" convention (claude.md
 * #59) -- a bad cert/key/handshake failure names the actual mbedTLS
 * error text, not just "openSecurePort failed". */
static void festina_tls_fail(const char *what, int mbedtls_err) {
    char err_buf[256];
    mbedtls_strerror(mbedtls_err, err_buf, sizeof err_buf);
    char msg[512];
    snprintf(msg, sizeof msg, "openSecurePort: %s: %s", what, err_buf);
    festina_fail(msg);
}

void *festina_tls_listener_new(const uint8_t *pem, int64_t pem_len) {
    FestinaTlsListener *l = calloc(1, sizeof(*l));
    if (!l) festina_fail("out of memory setting up TLS");
    mbedtls_ssl_config_init(&l->conf);
    mbedtls_x509_crt_init(&l->cert);
    mbedtls_pk_init(&l->pkey);
    mbedtls_entropy_init(&l->entropy);
    mbedtls_ctr_drbg_init(&l->ctr_drbg);

    static const char *pers = "festina_openSecurePort";
    int rc = mbedtls_ctr_drbg_seed(&l->ctr_drbg, mbedtls_entropy_func, &l->entropy,
                                    (const unsigned char *)pers, strlen(pers));
    if (rc != 0) festina_tls_fail("seeding the TLS random number generator", rc);

    /* mbedTLS's PEM parsers want a NUL-terminated buffer (they detect
     * PEM vs. DER by scanning for "-----BEGIN" text, which needs a
     * real string, not just length-delimited bytes) -- a blob's own
     * bytes aren't guaranteed NUL-terminated, so copy+terminate rather
     * than assume or mutate the caller's buffer. */
    uint8_t *pem_nt = malloc((size_t)pem_len + 1);
    if (!pem_nt) festina_fail("out of memory parsing a TLS certificate/key");
    memcpy(pem_nt, pem, (size_t)pem_len);
    pem_nt[pem_len] = '\0';

    /* Parses every "-----BEGIN CERTIFICATE-----" block in the buffer
     * (a leaf cert alone, or a leaf + chain -- chained via ->next),
     * ignoring the PRIVATE KEY block entirely. */
    rc = mbedtls_x509_crt_parse(&l->cert, pem_nt, (size_t)pem_len + 1);
    if (rc != 0) {
        free(pem_nt);
        festina_tls_fail("parsing the TLS certificate", rc);
    }
    /* Parses the PRIVATE KEY block, ignoring the CERTIFICATE block(s)
     * entirely -- an encrypted key (password-protected) fails here
     * with a clear mbedTLS error, since pwd/pwdlen are left NULL/0
     * (see this file's own top comment: out of scope for v1). */
    rc = mbedtls_pk_parse_key(&l->pkey, pem_nt, (size_t)pem_len + 1, NULL, 0);
    free(pem_nt);
    if (rc != 0) festina_tls_fail("parsing the TLS private key", rc);

    rc = mbedtls_ssl_config_defaults(&l->conf, MBEDTLS_SSL_IS_SERVER,
                                      MBEDTLS_SSL_TRANSPORT_STREAM,
                                      MBEDTLS_SSL_PRESET_DEFAULT);
    if (rc != 0) festina_tls_fail("configuring TLS defaults", rc);
    mbedtls_ssl_conf_rng(&l->conf, mbedtls_ctr_drbg_random, &l->ctr_drbg);
    /* No client-certificate / mutual TLS in this version -- see this
     * file's own top comment. */
    mbedtls_ssl_conf_authmode(&l->conf, MBEDTLS_SSL_VERIFY_NONE);
    rc = mbedtls_ssl_conf_own_cert(&l->conf, &l->cert, &l->pkey);
    if (rc != 0) festina_tls_fail("attaching the certificate/key to TLS", rc);
    return l;
}

void festina_tls_listener_free(void *handle) {
    FestinaTlsListener *l = handle;
    if (!l) return;
    mbedtls_ssl_config_free(&l->conf);
    mbedtls_x509_crt_free(&l->cert);
    mbedtls_pk_free(&l->pkey);
    mbedtls_ctr_drbg_free(&l->ctr_drbg);
    mbedtls_entropy_free(&l->entropy);
    free(l);
}

void *festina_tls_conn_new(void *listener_handle, int fd) {
    FestinaTlsListener *l = listener_handle;
    FestinaTlsConn *c = calloc(1, sizeof(*c));
    if (!c) festina_fail("out of memory accepting a TLS connection");
    mbedtls_ssl_init(&c->ssl);
    c->net.fd = fd;
    int rc = mbedtls_ssl_setup(&c->ssl, &l->conf);
    if (rc != 0) {
        /* Setup failure here (out-of-memory inside mbedTLS, in
         * practice) tears the connection down rather than failing the
         * whole program -- a single bad connection is not fatal, the
         * same "never fails the program" convention every other
         * per-connection runtime path in festina_runtime_http.c
         * already follows. */
        mbedtls_ssl_free(&c->ssl);
        free(c);
        return NULL;
    }
    mbedtls_ssl_set_bio(&c->ssl, &c->net, mbedtls_net_send, mbedtls_net_recv, NULL);
    return c;
}

void festina_tls_conn_free(void *tls_handle) {
    FestinaTlsConn *c = tls_handle;
    if (!c) return;
    /* No close_notify sent here -- the underlying fd is about to be
     * closed outright by festina_conn_teardown's own festina_close_fd
     * call regardless, and a close_notify write could itself block on
     * a non-blocking socket with no event loop left to drive a retry
     * from at teardown time. A peer sees an abrupt connection close
     * rather than a clean TLS shutdown, same as this runtime's own
     * unconditional "every response closes the connection" HTTP/1.1
     * scope already accepts for the plain-socket path (no keep-alive,
     * no graceful half-close there either). */
    mbedtls_ssl_free(&c->ssl);
    free(c);
}

int festina_tls_handshake(void *tls_handle) {
    FestinaTlsConn *c = tls_handle;
    int rc = mbedtls_ssl_handshake(&c->ssl);
    if (rc == 0) return 1;                          /* done */
    if (rc == MBEDTLS_ERR_SSL_WANT_READ) return 0;   /* wait for POLLIN */
    if (rc == MBEDTLS_ERR_SSL_WANT_WRITE) return 2;  /* wait for POLLOUT */
    return -1;                                       /* fatal -- tear down */
}

long festina_tls_recv(void *tls_handle, void *buf, int64_t cap) {
    FestinaTlsConn *c = tls_handle;
    int rc = mbedtls_ssl_read(&c->ssl, buf, (size_t)cap);
    if (rc == MBEDTLS_ERR_SSL_WANT_READ || rc == MBEDTLS_ERR_SSL_WANT_WRITE) return -1;
    if (rc == MBEDTLS_ERR_SSL_PEER_CLOSE_NOTIFY || rc == 0) return 0;
    if (rc < 0) return -2;
    return (long)rc;
}

long festina_tls_send(void *tls_handle, const void *data, int64_t len) {
    FestinaTlsConn *c = tls_handle;
    int rc = mbedtls_ssl_write(&c->ssl, data, (size_t)len);
    /* claude.md #155/festina_send_all's own precedent: a plain-socket
     * write that would block is already treated as outright failure
     * on this runtime's non-blocking connections (see
     * festina_runtime_http.c's own festina_send_all -- it never
     * retries on EAGAIN either), not resumed via the event loop -- so
     * WANT_READ/WANT_WRITE here get the identical "just fail" answer,
     * not a special retry path this runtime's plain-socket send
     * doesn't have either. */
    if (rc == MBEDTLS_ERR_SSL_WANT_READ || rc == MBEDTLS_ERR_SSL_WANT_WRITE) return -1;
    if (rc < 0) return -2;
    return (long)rc;
}

/* ---- TLS CLIENT (claude.md #162): the https:// half of req.send()'s
 * zero-argument client form -- festina_runtime_http.c's own
 * festina_http_send_client dispatches to these four hooks exactly the
 * way the SERVER side dispatches to festina_tls_handshake/_recv/_send
 * above, so this translation unit stays the only one that ever
 * references an mbedTLS symbol by name.
 *
 * SCOPE: server certificate verification is BEST-EFFORT, not
 * guaranteed -- this runtime has no certificate store of its own (no
 * such thing exists in this language), so it looks for the host
 * system's own CA bundle at a short list of well-known paths
 * (Debian/Ubuntu, RHEL/Fedora, Alpine, Homebrew's openssl on both
 * Intel and Apple Silicon Macs). If one is found, verification is
 * REQUIRED (a bad/expired/mismatched certificate fails the fetch,
 * thrown with real diagnostic text). If none is found, verification
 * is silently skipped rather than failing every single https://
 * fetch() outright on a machine that simply doesn't have one at any
 * of these paths (a minimal container image, in particular) -- an
 * honest tradeoff, not a security feature: a program that genuinely
 * needs guaranteed certificate verification on such a machine needs
 * that machine to actually have a CA bundle somewhere findable, which
 * is true of essentially every real Linux distribution's own base
 * image. Windows/macOS are not attempted at all here beyond the
 * Homebrew path above -- both keep their certificate trust store
 * outside the filesystem entirely (Windows' own CryptoAPI store,
 * macOS's Keychain), which would need real platform-specific code
 * this round didn't reach; see setup.md/api.md for the honest
 * accounting. */
static const char *const FESTINA_CA_BUNDLE_PATHS[] = {
    "/etc/ssl/certs/ca-certificates.crt",     /* Debian/Ubuntu */
    "/etc/pki/tls/certs/ca-bundle.crt",       /* RHEL/Fedora/CentOS */
    "/etc/ssl/cert.pem",                       /* Alpine, some BSDs */
    "/usr/local/etc/openssl/cert.pem",         /* Homebrew openssl, Intel Mac */
    "/opt/homebrew/etc/openssl@3/cert.pem",    /* Homebrew openssl, Apple Silicon */
    NULL
};

typedef struct {
    mbedtls_ssl_context ssl;
    mbedtls_ssl_config conf;
    mbedtls_x509_crt ca_chain;
    int has_ca_chain;
    mbedtls_entropy_context entropy;
    mbedtls_ctr_drbg_context ctr_drbg;
    mbedtls_net_context net;
} FestinaTlsClientConn;

static void festina_tls_client_conn_free(FestinaTlsClientConn *c) {
    mbedtls_ssl_free(&c->ssl);
    mbedtls_ssl_config_free(&c->conf);
    mbedtls_x509_crt_free(&c->ca_chain);
    mbedtls_ctr_drbg_free(&c->ctr_drbg);
    mbedtls_entropy_free(&c->entropy);
    free(c);
}

void *festina_tls_client_connect(int fd, const char *hostname) {
    FestinaTlsClientConn *c = calloc(1, sizeof(*c));
    if (!c) festina_fail("out of memory setting up a TLS client connection");
    mbedtls_ssl_init(&c->ssl);
    mbedtls_ssl_config_init(&c->conf);
    mbedtls_x509_crt_init(&c->ca_chain);
    mbedtls_entropy_init(&c->entropy);
    mbedtls_ctr_drbg_init(&c->ctr_drbg);
    c->net.fd = fd;

    static const char *pers = "festina_fetch_client";
    int rc = mbedtls_ctr_drbg_seed(&c->ctr_drbg, mbedtls_entropy_func, &c->entropy,
                                   (const unsigned char *)pers, strlen(pers));
    if (rc != 0) { festina_tls_client_conn_free(c); return NULL; }

    rc = mbedtls_ssl_config_defaults(&c->conf, MBEDTLS_SSL_IS_CLIENT,
                                     MBEDTLS_SSL_TRANSPORT_STREAM, MBEDTLS_SSL_PRESET_DEFAULT);
    if (rc != 0) { festina_tls_client_conn_free(c); return NULL; }
    mbedtls_ssl_conf_rng(&c->conf, mbedtls_ctr_drbg_random, &c->ctr_drbg);

    for (int i = 0; FESTINA_CA_BUNDLE_PATHS[i]; i++) {
        if (mbedtls_x509_crt_parse_file(&c->ca_chain, FESTINA_CA_BUNDLE_PATHS[i]) == 0) {
            c->has_ca_chain = 1;
            break;
        }
    }
    if (c->has_ca_chain) {
        mbedtls_ssl_conf_ca_chain(&c->conf, &c->ca_chain, NULL);
        mbedtls_ssl_conf_authmode(&c->conf, MBEDTLS_SSL_VERIFY_REQUIRED);
    } else {
        mbedtls_ssl_conf_authmode(&c->conf, MBEDTLS_SSL_VERIFY_NONE);
    }

    rc = mbedtls_ssl_setup(&c->ssl, &c->conf);
    if (rc != 0) { festina_tls_client_conn_free(c); return NULL; }
    mbedtls_ssl_set_hostname(&c->ssl, hostname);
    mbedtls_ssl_set_bio(&c->ssl, &c->net, mbedtls_net_send, mbedtls_net_recv, NULL);

    /* Blocking handshake -- fetch()/req.send() already blocks the
     * whole single-threaded program until it completes (see
     * festina_runtime_http.c's own comment on why that's an accepted
     * tradeoff, not an oversight), so a plain retry-on-WANT_READ/
     * _WRITE loop is all this needs -- unlike the SERVER side's
     * non-blocking, poll()-driven handshake (festina_tls_handshake
     * above), which has to resume across many event-loop ticks
     * because it must never block the connections it's sharing a
     * thread with. */
    for (;;) {
        rc = mbedtls_ssl_handshake(&c->ssl);
        if (rc == 0) break;
        if (rc != MBEDTLS_ERR_SSL_WANT_READ && rc != MBEDTLS_ERR_SSL_WANT_WRITE) {
            festina_tls_client_conn_free(c);
            return NULL;
        }
    }
    if (c->has_ca_chain && mbedtls_ssl_get_verify_result(&c->ssl) != 0) {
        festina_tls_client_conn_free(c);
        return NULL;
    }
    return c;
}

long festina_tls_client_recv(void *tls_handle, void *buf, int64_t cap) {
    FestinaTlsClientConn *c = tls_handle;
    int rc = mbedtls_ssl_read(&c->ssl, buf, (size_t)cap);
    if (rc == MBEDTLS_ERR_SSL_WANT_READ || rc == MBEDTLS_ERR_SSL_WANT_WRITE) return -1;
    if (rc == MBEDTLS_ERR_SSL_PEER_CLOSE_NOTIFY || rc == 0) return 0;
    if (rc < 0) return -2;
    return (long)rc;
}

/* Unlike festina_tls_send (the server side, which leaves WANT_READ/
 * WANT_WRITE to the caller -- see that function's own comment on why
 * a plain-socket write that would block is already just failure on
 * this runtime's non-blocking server connections), this loops until
 * everything is actually sent or a hard error occurs -- the client
 * socket has a real read/write timeout (SO_RCVTIMEO/SO_SNDTIMEO, set
 * by festina_http_send_client before the handshake even starts) to
 * bound how long that can ever take, so spinning on WANT_READ/
 * _WRITE here can't hang forever the way it could on the server's own
 * non-blocking fds. */
long festina_tls_client_send(void *tls_handle, const void *data, int64_t len) {
    FestinaTlsClientConn *c = tls_handle;
    const uint8_t *p = data;
    size_t sent = 0;
    while (sent < (size_t)len) {
        int rc = mbedtls_ssl_write(&c->ssl, p + sent, (size_t)len - sent);
        if (rc == MBEDTLS_ERR_SSL_WANT_READ || rc == MBEDTLS_ERR_SSL_WANT_WRITE) continue;
        if (rc <= 0) return -2;
        sent += (size_t)rc;
    }
    return (long)sent;
}

void festina_tls_client_close(void *tls_handle) {
    FestinaTlsClientConn *c = tls_handle;
    if (!c) return;
    mbedtls_ssl_close_notify(&c->ssl);
    festina_tls_client_conn_free(c);
}

/* Registers this file's own eleven hooks (seven server + four client)
 * into festina_runtime_http.c's g_tls_... and g_tls_client_... function
 * pointers -- called from generated code's own main() (festina/
 * codegen.py's _emit_main_and_entry), and ONLY when self.uses_https,
 * exactly mirroring festina_set_audio_decoder's own call-site
 * convention. */
void festina_register_tls_hooks(void) {
    festina_set_tls_hooks(
        festina_tls_listener_new, festina_tls_listener_free,
        festina_tls_conn_new, festina_tls_conn_free,
        festina_tls_handshake, festina_tls_recv, festina_tls_send);
    festina_set_tls_client_hooks(
        festina_tls_client_connect, festina_tls_client_recv,
        festina_tls_client_send, festina_tls_client_close);
}
