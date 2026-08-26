/*
 * Festina native runtime -- HTTP/WebSocket translation unit: claude.md
 * #151 (openPort/closePort, `on request`/`on upgrade`/`on message`/
 * `on socketClose`). See festina_runtime.h's own doc comment (right
 * above this file's declarations) for the full design rationale --
 * this file is pure implementation, split out of festina_runtime.c so
 * a program that never calls openPort() never links any of it, the
 * same per-feature split graphics/audio already use (see cli.py's
 * per-feature object file selection, driven by CodeGen.uses_http in
 * festina/codegen.py).
 *
 * Linux/macOS/Windows -- rejected at COMPILE time under wasm32-wasi
 * only (see cli.py's _check_wasm_feature_supported: WASI has no
 * listening-socket support at all), never a link failure. windows.md
 * Phase 2's own "gated pending real-hardware verification, not
 * unimplemented" shape applies here too (claude.md #151's own Windows
 * round) -- FESTINA_ENABLE_WINDOWS_HTTP=1, see
 * cli.py's _check_feature_supported.
 *
 * claude.md #151 (Windows round): the socket API itself needed real
 * porting, not just a recompile -- Winsock2 differs from BSD sockets
 * in exactly enough places to matter: a distinct SOCKET handle type
 * (unsigned, so "< 0" checks that work for a POSIX fd silently never
 * fire), closesocket() not close(), WSAPoll() not poll() (same field
 * names though, confirmed directly -- one typedef swap covers every
 * call site), ioctlsocket()/FIONBIO not fcntl()/O_NONBLOCK, and
 * WSAGetLastError() instead of errno for every socket call's own
 * error (Winsock functions never touch the CRT's errno at all). The
 * seam below (FestinaSocket/FESTINA_INVALID_SOCKET/festina_close_fd/
 * festina_poll/FestinaPollFd/festina_socket_would_block/
 * festina_socket_was_interrupted) is what
 * lets every call site below this point read identically on both
 * platforms -- deliberately ONE file with a seam at the top, the same
 * shape festina_runtime_audio.c already uses for its own much smaller
 * ALSA-vs-waveOut split, rather than a second whole-file duplicate
 * the way graphics' Cocoa/Win32 WINDOWING half needed (that split
 * exists because Cocoa is Objective-C, a real language difference
 * this file has no equivalent of -- see festina_runtime.h's own
 * top-of-file note). Windows also has no SIGPIPE at all for a broken
 * socket (send() just answers an error, no signal ever raised) and
 * needs an explicit WSAStartup() before any socket call -- handled at
 * festina_open_port's own entry point (see its own comment on why
 * there's no matching WSACleanup()), mirrored by the POSIX
 * SIGPIPE-ignore this file already had.
 */
#include <stddef.h>  /* offsetof -- claude.md #162's FestinaHttpValue accessors */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include "festina_runtime.h"
#include "festina_runtime_internal.h"

/* claude.md #151 (Windows round): named festina_close_fd, not
 * festina_socket_close -- a real naming collision found by an actual
 * MinGW compile error (conflicting types) against the PUBLIC
 * festina_socket_close(void *handle) below (s.close()'s own runtime
 * entry point, declared in festina_runtime.h) -- the same class of
 * mistake claude.md #150's festina_exec/festina_process_exec rename
 * already hit once in this codebase. */
#ifdef _WIN32
#  include <winsock2.h>
#  include <ws2tcpip.h>
   typedef SOCKET FestinaSocket;
   typedef WSAPOLLFD FestinaPollFd;
#  define FESTINA_INVALID_SOCKET INVALID_SOCKET
#  define festina_close_fd(fd) closesocket(fd)
#  define festina_poll(fds, n, timeout) WSAPoll((fds), (n), (timeout))
static int festina_socket_would_block(void) { return WSAGetLastError() == WSAEWOULDBLOCK; }
static int festina_socket_was_interrupted(void) { return WSAGetLastError() == WSAEINTR; }
#else
#  include <errno.h>
#  include <fcntl.h>
#  include <signal.h>
#  include <strings.h>    /* strcasecmp */
#  include <unistd.h>
#  include <poll.h>
#  include <sys/socket.h>
#  include <sys/time.h>   /* struct timeval -- claude.md #162's SO_RCVTIMEO/SO_SNDTIMEO */
#  include <netinet/in.h>
#  include <netinet/tcp.h>
#  include <arpa/inet.h>
#  include <netdb.h>      /* getaddrinfo -- claude.md #162's client fetch */
#  include <pthread.h>    /* claude.md #163: the async-callback worker pool below --
                            * POSIX only for now, the same staged-platform-rollout
                            * shape every other http feature here already uses (see
                            * "http -- async client" further down). Already an
                            * accepted dependency in this runtime -- festina_runtime_audio.c
                            * links it too, whenever audio is used. */
   typedef int FestinaSocket;
   typedef struct pollfd FestinaPollFd;
#  define FESTINA_INVALID_SOCKET (-1)
#  define festina_close_fd(fd) close(fd)
#  define festina_poll(fds, n, timeout) poll((fds), (n), (timeout))
static int festina_socket_would_block(void) { return errno == EAGAIN || errno == EWOULDBLOCK; }
static int festina_socket_was_interrupted(void) { return errno == EINTR; }
#endif

/* ---- SHA1 + base64 -- the WebSocket handshake's own two ingredients
 * (RFC 6455 section 1.3: base64(SHA1(Sec-WebSocket-Key + a fixed
 * magic GUID))). Both are small, standard, self-contained -- nothing
 * elsewhere in this runtime already provides either, and vendoring a
 * whole crypto library for twenty lines of well-known algorithm would
 * be a much worse tradeoff than just writing them, the same call this
 * project already made for its own JSON renderer/UTF-8 walking rather
 * than reaching for a dependency every time. Neither is used for
 * anything security-sensitive -- the handshake only needs to prove
 * both sides read the same header, not resist a real adversary. ---- */

typedef struct {
    uint32_t h[5];
    uint64_t len;
    uint8_t block[64];
    size_t block_len;
} FestinaSha1;

static uint32_t festina_sha1_rol(uint32_t v, int bits) {
    return (v << bits) | (v >> (32 - bits));
}

static void festina_sha1_init(FestinaSha1 *s) {
    s->h[0] = 0x67452301; s->h[1] = 0xEFCDAB89; s->h[2] = 0x98BADCFE;
    s->h[3] = 0x10325476; s->h[4] = 0xC3D2E1F0;
    s->len = 0;
    s->block_len = 0;
}

static void festina_sha1_process(FestinaSha1 *s, const uint8_t *chunk) {
    uint32_t w[80];
    for (int i = 0; i < 16; i++) {
        w[i] = ((uint32_t)chunk[i * 4] << 24) | ((uint32_t)chunk[i * 4 + 1] << 16)
             | ((uint32_t)chunk[i * 4 + 2] << 8) | (uint32_t)chunk[i * 4 + 3];
    }
    for (int i = 16; i < 80; i++) {
        w[i] = festina_sha1_rol(w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16], 1);
    }
    uint32_t a = s->h[0], b = s->h[1], c = s->h[2], d = s->h[3], e = s->h[4];
    for (int i = 0; i < 80; i++) {
        uint32_t f, k;
        if (i < 20) { f = (b & c) | ((~b) & d); k = 0x5A827999; }
        else if (i < 40) { f = b ^ c ^ d; k = 0x6ED9EBA1; }
        else if (i < 60) { f = (b & c) | (b & d) | (c & d); k = 0x8F1BBCDC; }
        else { f = b ^ c ^ d; k = 0xCA62C1D6; }
        uint32_t temp = festina_sha1_rol(a, 5) + f + e + k + w[i];
        e = d; d = c; c = festina_sha1_rol(b, 30); b = a; a = temp;
    }
    s->h[0] += a; s->h[1] += b; s->h[2] += c; s->h[3] += d; s->h[4] += e;
}

static void festina_sha1_update(FestinaSha1 *s, const uint8_t *data, size_t len) {
    s->len += (uint64_t)len * 8;
    while (len > 0) {
        size_t take = 64 - s->block_len;
        if (take > len) take = len;
        memcpy(s->block + s->block_len, data, take);
        s->block_len += take;
        data += take;
        len -= take;
        if (s->block_len == 64) {
            festina_sha1_process(s, s->block);
            s->block_len = 0;
        }
    }
}

static void festina_sha1_final(FestinaSha1 *s, uint8_t out[20]) {
    uint64_t bit_len = s->len;
    uint8_t pad = 0x80;
    festina_sha1_update(s, &pad, 1);
    uint8_t zero = 0;
    while (s->block_len != 56) festina_sha1_update(s, &zero, 1);
    uint8_t len_bytes[8];
    for (int i = 0; i < 8; i++) len_bytes[i] = (uint8_t)(bit_len >> (56 - 8 * i));
    /* Append the length directly rather than through festina_sha1_update
     * (which would re-trigger the padding branch above once block_len
     * wraps back to 0 at exactly 56+8=64) -- process the final block by
     * hand instead. */
    memcpy(s->block + 56, len_bytes, 8);
    festina_sha1_process(s, s->block);
    for (int i = 0; i < 5; i++) {
        out[i * 4] = (uint8_t)(s->h[i] >> 24);
        out[i * 4 + 1] = (uint8_t)(s->h[i] >> 16);
        out[i * 4 + 2] = (uint8_t)(s->h[i] >> 8);
        out[i * 4 + 3] = (uint8_t)s->h[i];
    }
}

/* Standard base64 alphabet, padded -- the handshake header value is
 * always exactly 28 characters (20 raw bytes -> ceil(20/3)*4 = 28)
 * for a SHA1 digest specifically, but this is written generically. */
static char *festina_base64_encode(const uint8_t *data, size_t len) {
    static const char alphabet[] =
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    size_t out_len = 4 * ((len + 2) / 3);
    char *out = malloc(out_len + 1);
    if (!out) festina_fail("out of memory base64-encoding a websocket handshake key");
    size_t oi = 0;
    for (size_t i = 0; i < len; i += 3) {
        uint32_t chunk = (uint32_t)data[i] << 16;
        if (i + 1 < len) chunk |= (uint32_t)data[i + 1] << 8;
        if (i + 2 < len) chunk |= (uint32_t)data[i + 2];
        out[oi++] = alphabet[(chunk >> 18) & 0x3F];
        out[oi++] = alphabet[(chunk >> 12) & 0x3F];
        out[oi++] = (i + 1 < len) ? alphabet[(chunk >> 6) & 0x3F] : '=';
        out[oi++] = (i + 2 < len) ? alphabet[chunk & 0x3F] : '=';
    }
    out[oi] = '\0';
    return out;
}

/* ---- header list -- raw parsed name/value pairs for ONE request,
 * name always lowercased at parse time (HTTP header names are
 * case-insensitive; lowercasing once here is simpler than comparing
 * case-insensitively at every lookup site below). Rebuilt from
 * scratch for every request a connection parses (there is at most
 * one, since this version has no keep-alive -- see this file's own
 * top comment / festina_runtime.h). ---- */
typedef struct {
    char *name;
    char *value;
} FestinaHeader;

static void festina_headers_add(FestinaHeader **headers, int64_t *count, int64_t *capacity,
                                const char *name_start, size_t name_len,
                                const char *value_start, size_t value_len) {
    /* claude.md #155: doubling growth, not a realloc-by-exactly-one
     * per header -- every other growable buffer in this file already
     * doubles (the connection read buffer, the listener/connection
     * tables); this one didn't, for no reason tied to headers
     * specifically. A real request's header count is small, but
     * doubling costs nothing extra to have and turns N reallocs into
     * O(log N). */
    if (*count == *capacity) {
        int64_t new_capacity = *capacity ? *capacity * 2 : 8;
        FestinaHeader *grown = realloc(*headers, (size_t)new_capacity * sizeof(FestinaHeader));
        if (!grown) festina_fail("out of memory growing a parsed HTTP header list");
        *headers = grown;
        *capacity = new_capacity;
    }
    char *name = malloc(name_len + 1);
    char *value = malloc(value_len + 1);
    if (!name || !value) festina_fail("out of memory copying an HTTP header");
    for (size_t i = 0; i < name_len; i++) name[i] = (char)tolower((unsigned char)name_start[i]);
    name[name_len] = '\0';
    memcpy(value, value_start, value_len);
    value[value_len] = '\0';
    (*headers)[*count].name = name;
    (*headers)[*count].value = value;
    (*count)++;
}

static const char *festina_headers_get(FestinaHeader *headers, int64_t count, const char *name) {
    for (int64_t i = 0; i < count; i++) {
        if (strcmp(headers[i].name, name) == 0) return headers[i].value;
    }
    return NULL;
}

static void festina_headers_free(FestinaHeader *headers, int64_t count) {
    for (int64_t i = 0; i < count; i++) {
        free(headers[i].name);
        free(headers[i].value);
    }
    free(headers);
}

/* ---- connection state ---- */

typedef enum {
    FESTINA_CONN_READING_REQUEST,
    FESTINA_CONN_WEBSOCKET,
} FestinaConnMode;

typedef struct {
    int64_t conn_id;   /* monotonic, never reused -- see festina_runtime.h */
    FestinaSocket fd;
    int listen_port;
    int alive;          /* 0 once torn down; the slot may be reused later */
    FestinaConnMode mode;
    int ever_upgraded;  /* did this connection ever reach WEBSOCKET mode?
                         * -- on socketClose fires only if so (see
                         * festina_runtime.h's own doc comment: a plain
                         * HTTP connection closing is not a socketClose
                         * event). */

    /* raw accumulation buffer -- HTTP request bytes while mode is
     * READING_REQUEST, WebSocket frame bytes while mode is WEBSOCKET. */
    uint8_t *buf;
    size_t buf_len;
    size_t buf_cap;

    /* HTTP request, once mode == READING_REQUEST and parsing has found
     * a complete request-line + headers (+ body, once content_length
     * bytes have arrived). request_ready stays 0 until then. */
    char *method;
    char *path;   /* see festina_runtime.h's HttpType doc comment --
                   * added beyond the user's own literal spec, since a
                   * request handler has no way to route on anything
                   * without it. */
    FestinaHeader *headers;
    int64_t header_count;
    int64_t header_capacity;   /* claude.md #155: see festina_headers_add */
    int64_t content_length;   /* -1 until the header is parsed */
    uint8_t *body;             /* NULL until the body is fully buffered */
    int64_t body_len;
    int request_ready;
    int responded;    /* ok/redirect/send/upgrade -- see festina_runtime.h */
    /* claude.md #167: whether THIS request's own response should keep
     * the connection open for another one, decided once, right after
     * this request's headers are parsed (see festina_try_parse_request)
     * -- HTTP/1.1 defaults to keep-alive unless the request sent
     * `Connection: close`; HTTP/1.0 defaults to close unless it sent
     * `Connection: keep-alive`. Read by festina_http_ok/_redirect/_send
     * to pick which `Connection:` header to answer with, and by
     * festina_dispatch_request's own tail to decide between
     * festina_conn_reset_for_next_request and festina_conn_teardown. */
    int keep_alive;
    /* claude.md #167: festina_now_seconds() as of the last time this
     * connection had a REASON to be considered idle-since -- reset every
     * time it becomes newly idle (accepted, or just reset for another
     * request) in festina_conn_new_slot/festina_conn_reset_for_next_request.
     * Read by festina_reap_idle_keepalive_connections to close a
     * keep-alive connection nobody is using any more rather than holding
     * its fd/slot open forever -- see that function's own doc comment. */
    double last_activity;
    /* claude.md #167: set once, in festina_conn_reset_for_next_request
     * -- true means this connection has already completed at least one
     * full request/response cycle and is now idle FOR REUSE, as opposed
     * to a freshly accepted connection that simply hasn't sent its
     * FIRST request yet (indistinguishable from an idle reused one by
     * buf_len/headers_parsed alone -- both are "nothing parsed, nothing
     * buffered"). Read only by shutdown's own immediate-close-idle-
     * connections step (festina_run_http_loop) -- reaping THIS kind of
     * idle connection right away, rather than waiting out the grace
     * period, is only safe once it's actually known nothing more is
     * coming; a connection that hasn't sent its first request yet might
     * still be about to (confirmed as a real bug during development: a
     * test connecting right before SIGTERM, then sending its one and
     * only request just after, lost its response entirely without this
     * guard). festina_reap_idle_keepalive_connections' own TIMEOUT-based
     * reap has no such problem -- an idle connection that never sends
     * anything at all for the full idle window is a reasonable target
     * either way, first request or not. */
    int served_a_request;

    /* claude.md #155: festina_try_parse_request's own resumable-scan
     * state -- headers_parsed guards the request-line/header-parsing
     * block to run AT MOST ONCE per connection (a request whose body
     * arrives in a later, separate recv() used to re-run that whole
     * block from scratch on every call, which not only rescanned
     * already-scanned bytes but re-malloc'd method/path over the
     * previous call's own pointers with nothing freeing them first,
     * and re-appended every header onto the still-populated headers
     * array -- a real, confirmed leak+duplication bug, found while
     * designing this fix, not merely a performance one).
     * header_scan_pos is how far the \r\n\r\n search got on the last
     * call that didn't find it, so the next call resumes there instead
     * of rescanning from byte 0. body_start_offset is cached once
     * headers are parsed, so the have-we-got-the-whole-body check on
     * every later call is O(1) instead of re-deriving it from hdr_end
     * (which would require rescanning to even have hdr_end again). */
    int headers_parsed;
    size_t header_scan_pos;
    size_t body_start_offset;

    /* claude.md #168: `Transfer-Encoding: chunked` request bodies --
     * is_chunked is decided alongside content_length, right when headers
     * are parsed (whichever the request actually sent governs; see
     * festina_try_parse_request's own comment on why Transfer-Encoding
     * wins if a request somehow sends both). chunk_scan_pos is
     * festina_chunk_decode_step's own resumable position within `buf`
     * (mirrors header_scan_pos's shape exactly) -- decoded chunk DATA
     * accumulates separately in chunk_body/_len/_cap as complete chunks
     * are found, since the wire encoding (chunk-size lines, trailing
     * CRLFs) is interleaved with the real body bytes and can't just be
     * sliced out of `buf` in place. Once the terminating 0-size chunk
     * and its own final blank line are found, chunk_body/_len are
     * handed off to become this request's own body/body_len (see
     * festina_try_parse_request's own tail) and reset here to NULL/0. */
    int is_chunked;
    size_t chunk_scan_pos;
    uint8_t *chunk_body;
    size_t chunk_body_len;
    size_t chunk_body_cap;

    /* claude.md #168: WebSocket message fragmentation (RFC 6455 §5.4) --
     * see festina_ws_process_next_frame's own doc comment for the full
     * reassembly state machine. ws_frag_active is whether a fragmented
     * text/binary message is currently being reassembled (a FIN=0
     * text/binary frame started it, no terminating FIN=1 continuation
     * frame has arrived yet); ws_frag_opcode is which kind (0x1 text or
     * 0x2 binary) it is, since continuation frames don't repeat it.
     * Control frames (close/ping/pong) are never fragmented and are
     * dispatched immediately regardless of this state -- see RFC 6455's
     * own allowance for interleaving them between another message's
     * fragments. */
    int ws_frag_active;
    uint8_t ws_frag_opcode;
    uint8_t *ws_frag_buf;
    size_t ws_frag_len;
    size_t ws_frag_cap;

    /* socket.state -- lazily created (see festina_socket_state), a
     * live map[text] header block: {refcount, count, entries}, this
     * pointer aimed at the `count` field (see festina_runtime.h's own
     * doc comment on the http/socket handle representation for why). */
    void *state_map;

    /* claude.md #160: openSecurePort()'s own per-connection TLS state
     * -- NULL for a plain (non-TLS) connection, an opaque handle
     * g_tls_conn_new produced for one accepted on a TLS listener. Every
     * read/write/teardown site below checks `tls` rather than keeping
     * a separate is_tls flag: a connection is TLS if and only if it
     * has TLS state, by construction (festina_accept_new_connections
     * sets it at accept time from the listener's own tls_config, never
     * changed afterward). tls_handshake_done gates festina_conn_readable's
     * own dispatch: raw bytes off the wire are still the TLS handshake
     * itself until this flips, never HTTP/WebSocket data.
     * tls_wants_write records whether the last handshake attempt
     * needed to WRITE before it could make more progress (mbedTLS's
     * own WANT_WRITE) -- festina_run_http_loop's poll-set construction
     * adds POLLOUT for this connection only when it's set, so a
     * handshake that stalls on a full TCP send buffer still gets woken
     * up to retry instead of waiting on a POLLIN that may never come. */
    void *tls;
    int tls_handshake_done;
    int tls_wants_write;
} FestinaConn;

typedef struct {
    int64_t refcount;
    int64_t conn_id;
} FestinaConnHandleBlock;

typedef struct {
    int64_t refcount;
    int64_t count;
    void *entries;
} FestinaMapBlock;

/* claude.md #162: http's own real representation -- a genuine value
 * (url/method/code/headers/body all live IN this struct, copied out
 * once at the point of construction) rather than the old
 * {refcount, conn_id} handle that looked everything up fresh from
 * the connection table on every field read. This is what lets an
 * http value outlive its originating request/response entirely --
 * constructed by a program directly (`http x = {...}`), handed back
 * by a client req.send()/`on request`'s own req, or read from long
 * after the connection it came from (if any) has already been torn
 * down -- `.toText()`/etc. only ever touch this struct's own `body`,
 * never reach back into festina_conn_by_id at all anymore.
 *
 * `conn_id` is the ONE thing that still reaches back into the live
 * connection table -- 0 means "not live" (a plain constructed value,
 * or a client response), nonzero means this value is the actual
 * inbound request `on request` was called with, and .ok()/.redirect()/
 * .upgrade()/.send(res) can still push bytes out over it. Every one
 * of those already tolerates conn_id naming a connection that's since
 * been torn down (festina_conn_by_id returns NULL, same "never
 * crashes on a stale reference" contract festina_conn_from_handle
 * itself always had) -- silently doing nothing, same as calling them
 * on a conn_id-0 value in the first place. */
typedef struct {
    int64_t refcount;
    char *url;
    char *method;
    int64_t code;    /* festina_null_int() until a response exists */
    void *headers;    /* map[text] payload, owned */
    uint8_t *body;
    int64_t body_len;
    int64_t conn_id;  /* 0 = not live */
    /* claude.md #163: a bare function pointer -- NULL for "no callback",
     * matching FuncType's own runtime representation exactly (types.py's
     * own doc comment: "a bare function pointer... immortal for the
     * life of the process"), so this needs no refcounting/cleanup of
     * its own, the same reason a `func`-typed struct field never does.
     * Signature is always `void(http)` (checked by
     * semantic.py's own _HTTP_LIT_FIELD_TYPES) -- non-NULL here is
     * exactly what makes req.send() take the non-blocking path (see
     * festina_http_send_client_dispatch, "http -- async client" below). */
    void (*callback)(void *);
} FestinaHttpValue;

#define FESTINA_HTTP_FROM_PAYLOAD(payload) \
    ((FestinaHttpValue *)((char *)(payload) - offsetof(FestinaHttpValue, url)))

/* Forward declarations -- festina_dispatch_request (needs to BUILD a
 * value) and festina_run_http_loop's own fallback-response path (needs
 * to call .ok()) both come well before this file's "http --
 * construction"/"http -- methods" sections below, which is where these
 * are actually defined. */
static void *festina_http_value_new(const char *url, const char *method, int64_t code,
                                     void *headers, const uint8_t *body, int64_t body_len,
                                     int64_t conn_id, void (*callback)(void *));
void festina_release_http(void *payload);
void festina_http_ok(void *payload);

/* claude.md #168: festina_ws_process_one_frame (defined well before
 * this file's "WebSocket -- construction/dispatch" section) needs to
 * dispatch a complete -- possibly freshly reassembled -- message the
 * same way a single-frame one already does. */
static void festina_dispatch_ws_frame(FestinaConn *c, uint8_t opcode,
                                      uint8_t *payload, size_t payload_len);

/* claude.md #163: forward-declared here (defined in "http -- async
 * client" further down, well after festina_run_http_loop's own
 * definition) so that loop's per-iteration exit check and drain step
 * can reference them. Declared unconditionally (not inside the
 * POSIX-only #if guarding the worker pool itself) so this file reads
 * identically on every platform -- g_async_outstanding simply never
 * becomes nonzero on Windows, since festina_http_send_client_dispatch
 * never queues anything there (see that section's own #else branch). */
static int64_t g_async_outstanding = 0;
static int g_async_pool_started = 0;
static int g_async_wake_fds[2] = {-1, -1};   /* self-pipe: [0] read (in the poll set), [1] write --
                                              * never opened on Windows, where g_async_pool_started
                                              * also never becomes true, so festina_run_http_loop
                                              * never actually reads index [0] there either. */
static void festina_async_drain_completed(void);

/* Kept in sync with festina_runtime.c's own static festina_null_int()
 * (INT64_MIN) -- that function is private to that translation unit,
 * so this file (a genuinely different one) can't call it directly;
 * codegen.py's own INT_NULL_CONST is the third place this same
 * value is spelled out, per that constant's own doc comment. */
#define FESTINA_NULL_INT INT64_MIN

static FestinaConn *g_conns = NULL;
static int64_t g_conn_count = 0;       /* high-water mark: slots [0, g_conn_count) are
                                         * valid array bounds -- some inside may be
                                         * dead and sitting on the free list below */
static int64_t g_conn_capacity = 0;
static int64_t g_next_conn_id = 1;

/* Dead slots recycled by index rather than compacted out -- a slot's
 * index has to stay stable for as long as its connection is alive
 * (the hash index below stores indices, not pointers, precisely so a
 * table realloc() elsewhere never invalidates it; a moving-compaction
 * pass would have the same problem for the index it would need to
 * rewrite on every compaction). A LIFO free list (last torn down,
 * first reused) keeps cache locality reasonable under steady churn. */
static int64_t *g_conn_free_slots = NULL;
static int64_t g_conn_free_count = 0;
static int64_t g_conn_free_capacity = 0;

/* claude.md #152 (Windows-round follow-up): conn_id -> slot index,
 * so festina_conn_by_id below is O(1) amortized instead of an O(live
 * connections) linear scan on every single lookup -- and every public
 * festina_http_.../festina_socket_... call does at least one such
 * lookup, several per request once the handler touches req/s more
 * than once. A small open-addressing table (linear probing, tombstone
 * deletion) rather than reaching for a library hash map: conn_id is
 * already a plain int64_t key with no string hashing or collision
 * behavior to get subtly wrong, and this project has no existing
 * generic hash-map runtime helper to reuse (map[T]'s own
 * implementation lives in codegen-emitted IR, not C, and is keyed by
 * text, not int64_t, so it isn't a fit either). conn_id == -1 marks an
 * empty slot, -2 a tombstone (conn_id itself is always >= 1, so
 * neither collides with a real id) -- kept apart so a probe sequence
 * broken by a deletion still finds keys that were inserted past it. */
#define FESTINA_CONN_INDEX_EMPTY (-1)
#define FESTINA_CONN_INDEX_TOMBSTONE (-2)

typedef struct {
    int64_t conn_id;
    int64_t slot;
} FestinaConnIndexEntry;

static FestinaConnIndexEntry *g_conn_index = NULL;
static int64_t g_conn_index_capacity = 0;
/* occupied + tombstoned entries -- tombstones count toward the grow
 * threshold too (an unbounded run of insert/delete pairs would
 * otherwise fill a table with tombstones and degrade every probe to
 * O(capacity) without ever tripping a grow), and get swept away for
 * free the next time festina_conn_index_grow rebuilds the table. */
static int64_t g_conn_index_used = 0;

static uint64_t festina_conn_index_hash(int64_t conn_id) {
    /* A cheap integer mixer (splitmix64's own multiplier), not a
     * plain modulo -- conn_id is sequential, and sequential keys
     * modulo a power-of-two capacity happen to spread fine today, but
     * that's a coincidence of this specific key sequence, not a
     * guarantee the hash function should be relying on. */
    uint64_t x = (uint64_t)conn_id;
    x *= 0x9E3779B97F4A7C15ULL;
    x ^= x >> 32;
    return x;
}

static void festina_conn_index_insert_raw(FestinaConnIndexEntry *table, int64_t capacity,
                                           int64_t conn_id, int64_t slot) {
    uint64_t mask = (uint64_t)capacity - 1;
    uint64_t i = festina_conn_index_hash(conn_id) & mask;
    while (table[i].conn_id != FESTINA_CONN_INDEX_EMPTY &&
           table[i].conn_id != FESTINA_CONN_INDEX_TOMBSTONE) {
        i = (i + 1) & mask;
    }
    table[i].conn_id = conn_id;
    table[i].slot = slot;
}

static void festina_conn_index_grow(void) {
    int64_t new_capacity = g_conn_index_capacity ? g_conn_index_capacity * 2 : 16;
    FestinaConnIndexEntry *new_table = malloc((size_t)new_capacity * sizeof(FestinaConnIndexEntry));
    if (!new_table) festina_fail("out of memory growing the connection index");
    for (int64_t i = 0; i < new_capacity; i++) new_table[i].conn_id = FESTINA_CONN_INDEX_EMPTY;
    int64_t live = 0;
    for (int64_t i = 0; i < g_conn_index_capacity; i++) {
        if (g_conn_index[i].conn_id >= 0) {
            festina_conn_index_insert_raw(new_table, new_capacity,
                                          g_conn_index[i].conn_id, g_conn_index[i].slot);
            live++;
        }
    }
    free(g_conn_index);
    g_conn_index = new_table;
    g_conn_index_capacity = new_capacity;
    g_conn_index_used = live;  /* tombstones didn't survive the rebuild */
}

static void festina_conn_index_put(int64_t conn_id, int64_t slot) {
    /* Grow at a 75% load factor -- checked as (used+1)*4 >= capacity*3
     * to stay in integer arithmetic, the same style
     * festina_conn_new_slot's own capacity doubling already uses. */
    if (g_conn_index_capacity == 0 || (g_conn_index_used + 1) * 4 >= g_conn_index_capacity * 3) {
        festina_conn_index_grow();
    }
    festina_conn_index_insert_raw(g_conn_index, g_conn_index_capacity, conn_id, slot);
    g_conn_index_used++;
}

static int64_t festina_conn_index_get(int64_t conn_id) {
    if (g_conn_index_capacity == 0) return -1;
    uint64_t mask = (uint64_t)g_conn_index_capacity - 1;
    uint64_t i = festina_conn_index_hash(conn_id) & mask;
    for (int64_t probes = 0; probes < g_conn_index_capacity; probes++) {
        if (g_conn_index[i].conn_id == FESTINA_CONN_INDEX_EMPTY) return -1;
        if (g_conn_index[i].conn_id == conn_id) return g_conn_index[i].slot;
        i = (i + 1) & mask;
    }
    return -1;  /* unreachable while the load factor above is enforced --
                 * a full probe of every slot with no empty one found --
                 * kept as a safe fallback rather than an infinite loop. */
}

static void festina_conn_index_remove(int64_t conn_id) {
    if (g_conn_index_capacity == 0) return;
    uint64_t mask = (uint64_t)g_conn_index_capacity - 1;
    uint64_t i = festina_conn_index_hash(conn_id) & mask;
    for (int64_t probes = 0; probes < g_conn_index_capacity; probes++) {
        if (g_conn_index[i].conn_id == FESTINA_CONN_INDEX_EMPTY) return;  /* not present */
        if (g_conn_index[i].conn_id == conn_id) {
            g_conn_index[i].conn_id = FESTINA_CONN_INDEX_TOMBSTONE;
            return;
        }
        i = (i + 1) & mask;
    }
}

typedef struct {
    FestinaSocket fd;
    int port;
    /* claude.md #160: NULL for a plain openPort() listener, an opaque
     * handle g_tls_listener_new produced for one opened via
     * openSecurePort() -- every connection accept()ed on this
     * listener's own fd gets its own per-connection TLS state built
     * from this shared config (see FestinaConn's own tls field). */
    void *tls_config;
} FestinaListener;

static FestinaListener *g_listeners = NULL;
static int64_t g_listener_count = 0;
static int64_t g_listener_capacity = 0;

static void (*g_request_handler)(void *) = NULL;
static void (*g_upgrade_handler)(void *) = NULL;
static void (*g_message_handler)(void *, void *) = NULL;
static void (*g_socketclose_handler)(void *) = NULL;

void festina_register_request_handler(void (*fn)(void *)) { g_request_handler = fn; }
void festina_register_upgrade_handler(void (*fn)(void *)) { g_upgrade_handler = fn; }
void festina_register_message_handler(void (*fn)(void *, void *)) { g_message_handler = fn; }
void festina_register_socketclose_handler(void (*fn)(void *)) { g_socketclose_handler = fn; }

/* claude.md #160: the openSecurePort()/mbedTLS hook table -- NULL
 * (never called) for a program that never links festina_runtime_https.c
 * at all (see that file's own top comment for the cross-translation-
 * unit registration this mirrors from g_audio_decoder/g_image_decoder).
 * Every site below that touches TLS goes through these seven pointers,
 * never an mbedTLS symbol directly -- this translation unit has no
 * mbedTLS #include at all, deliberately, so it stays buildable and
 * linkable with zero TLS dependency for every program that doesn't
 * call openSecurePort(). */
static void *(*g_tls_listener_new)(const uint8_t *pem, int64_t pem_len) = NULL;
static void (*g_tls_listener_free)(void *tls_config) = NULL;
static void *(*g_tls_conn_new)(void *tls_config, int fd) = NULL;
static void (*g_tls_conn_free)(void *tls_state) = NULL;
static int (*g_tls_handshake)(void *tls_state) = NULL;
static long (*g_tls_recv)(void *tls_state, void *buf, int64_t cap) = NULL;
static long (*g_tls_send)(void *tls_state, const void *data, int64_t len) = NULL;

void festina_set_tls_hooks(
        void *(*listener_new)(const uint8_t *, int64_t),
        void (*listener_free)(void *),
        void *(*conn_new)(void *, int),
        void (*conn_free)(void *),
        int (*handshake)(void *),
        long (*recv_fn)(void *, void *, int64_t),
        long (*send_fn)(void *, const void *, int64_t)) {
    g_tls_listener_new = listener_new;
    g_tls_listener_free = listener_free;
    g_tls_conn_new = conn_new;
    g_tls_conn_free = conn_free;
    g_tls_handshake = handshake;
    g_tls_recv = recv_fn;
    g_tls_send = send_fn;
}

/* claude.md #151: an 8MB cap on how much a single connection may
 * buffer (request line + headers + body, or one WebSocket frame's
 * payload) before this runtime just closes it outright -- a broken or
 * hostile peer streaming forever with no terminator would otherwise
 * grow this buffer without bound. Generous enough for real request/
 * response bodies a small server-shaped program would plausibly
 * handle (an uploaded image, a JSON payload), not a hard language
 * guarantee about maximum request size. */
#define FESTINA_HTTP_MAX_BUFFER (8 * 1024 * 1024)

static FestinaConn *festina_conn_by_id(int64_t conn_id) {
    int64_t slot = festina_conn_index_get(conn_id);
    if (slot < 0) return NULL;
    FestinaConn *c = &g_conns[slot];
    /* Defensive, not load-bearing: correct index maintenance already
     * guarantees this holds (the index only ever names a live slot's
     * own conn_id), but a lookup is cheap insurance against the index
     * and the table it points into ever drifting apart in some future
     * change that touches one but not the other. */
    if (!c->alive || c->conn_id != conn_id) return NULL;
    return c;
}

/* Looks a handle up to its live connection, or NULL if that
 * connection is gone -- every public festina_http_.../festina_socket_...
 * function below starts here and tolerates a NULL result, per
 * festina_runtime.h's own "never crashes on a stale handle" design. */
static FestinaConn *festina_conn_from_handle(void *handle) {
    if (!handle) return NULL;
    int64_t conn_id = *(int64_t *)handle;
    return festina_conn_by_id(conn_id);
}

static void *festina_handle_new(int64_t conn_id) {
    FestinaConnHandleBlock *block = malloc(sizeof(FestinaConnHandleBlock));
    if (!block) festina_fail("out of memory allocating an http/socket handle");
    block->refcount = 1;
    block->conn_id = conn_id;
    return &block->conn_id;
}

void festina_release_conn_handle(void *payload) {
    if (!festina_release_check(payload)) return;
    free((char *)payload - sizeof(int64_t));
}

static void *festina_new_empty_text_map(void) {
    FestinaMapBlock *block = calloc(1, sizeof(FestinaMapBlock));
    if (!block) festina_fail("out of memory allocating socket.state");
    block->refcount = 1;
    return &block->count;
}

static FestinaConn *festina_conn_new_slot(void) {
    int64_t slot;
    if (g_conn_free_count > 0) {
        /* Reuse a torn-down connection's own slot -- claude.md #152:
         * this replaces the old compact-on-full pass (moving every
         * live connection down to fill the holes dead ones left)
         * precisely because moving a connection would move its index,
         * and the hash index above stores indices, not pointers, so
         * every moved connection's own index entry would need
         * rewriting on every compaction. A free list sidesteps that
         * entirely: a dead slot's index is simply handed to the next
         * new connection, whose OWN (different, newer) conn_id gets
         * inserted into the index fresh -- the old conn_id was already
         * removed from the index at teardown, so there's no stale
         * entry left to collide with the slot's new occupant. */
        slot = g_conn_free_slots[--g_conn_free_count];
    } else {
        if (g_conn_count == g_conn_capacity) {
            g_conn_capacity = g_conn_capacity ? g_conn_capacity * 2 : 8;
            FestinaConn *grown = realloc(g_conns, (size_t)g_conn_capacity * sizeof(FestinaConn));
            if (!grown) festina_fail("out of memory growing the connection table");
            g_conns = grown;
        }
        slot = g_conn_count++;
    }
    FestinaConn *c = &g_conns[slot];
    memset(c, 0, sizeof(*c));
    c->conn_id = g_next_conn_id++;
    c->alive = 1;
    c->mode = FESTINA_CONN_READING_REQUEST;
    c->content_length = -1;
    c->last_activity = festina_now_seconds();
    festina_conn_index_put(c->conn_id, slot);
    return c;
}

static void festina_conn_teardown(FestinaConn *c) {
    if (!c->alive) return;
    if (c->ever_upgraded && g_socketclose_handler) {
        void *handle = festina_handle_new(c->conn_id);
        g_socketclose_handler(handle);
        festina_release_conn_handle(handle);
    }
    if (c->tls && g_tls_conn_free) g_tls_conn_free(c->tls);
    c->tls = NULL;
    festina_close_fd(c->fd);
    free(c->buf);
    free(c->method);
    free(c->path);
    festina_headers_free(c->headers, c->header_count);
    free(c->body);
    /* claude.md #168: a connection can be torn down mid-chunked-request
     * or mid-fragmented-websocket-message (a malformed chunk/frame, an
     * early disconnect, plain keep-alive teardown after a chunked
     * request) -- both accumulators need freeing here the same as every
     * other per-connection buffer above, or they leak. */
    free(c->chunk_body);
    free(c->ws_frag_buf);
    /* claude.md #167: festina_release_text_map, not the generic
     * festina_release_map -- socket.state's own values are ordinary
     * owned text (set via `s.state[k] = v`, the same map[text]
     * semantics any other Festina map[text] has), see that function's
     * own doc comment for the leak this fixes. */
    if (c->state_map) festina_release_text_map(c->state_map);
    c->alive = 0;
    festina_conn_index_remove(c->conn_id);

    int64_t slot = c - g_conns;
    if (g_conn_free_count == g_conn_free_capacity) {
        g_conn_free_capacity = g_conn_free_capacity ? g_conn_free_capacity * 2 : 8;
        int64_t *grown = realloc(g_conn_free_slots, (size_t)g_conn_free_capacity * sizeof(int64_t));
        if (!grown) festina_fail("out of memory growing the connection free-slot list");
        g_conn_free_slots = grown;
    }
    g_conn_free_slots[g_conn_free_count++] = slot;
}

/* claude.md #167: a keep-alive response's own counterpart to
 * festina_conn_teardown -- the SAME connection (fd, tls state, conn_id,
 * socket.state map) serves another request, so only per-request parsing
 * state is torn down and reset, exactly mirroring what
 * festina_conn_new_slot itself zeroes for a brand new connection. Any
 * bytes already read past THIS request's own body are shifted down to
 * the front of buf rather than discarded -- ordinarily there won't be
 * any (a well-behaved client waits for the response before sending the
 * next request), but a client that pipelines anyway (sends request 2
 * before reading response 1) may already have handed bytes for it to
 * this same recv() call, and simply waiting for another poll()-readable
 * event to notice them could deadlock: nothing else is coming from a
 * client that already sent everything and is now just waiting on
 * responses. This runtime still doesn't PARSE pipelined requests
 * concurrently or reorder anything -- see festina_conn_readable's own
 * dispatch loop, which just calls festina_try_parse_request again
 * immediately after a keep-alive reset, so a second buffered request is
 * picked up on the very next pass rather than left to wait. */
static void festina_conn_reset_for_next_request(FestinaConn *c) {
    /* claude.md #168: a chunked request's own raw byte count isn't
     * `body_start_offset + content_length` at all (content_length stays
     * -1 for one -- see festina_try_parse_request) -- chunk_scan_pos is
     * where the terminating blank line's own decode left off, exactly
     * the boundary between this request's raw bytes and whatever comes
     * next. */
    size_t consumed = c->is_chunked
        ? c->chunk_scan_pos
        : c->body_start_offset + (c->content_length > 0 ? (size_t)c->content_length : 0);
    size_t remaining = consumed < c->buf_len ? c->buf_len - consumed : 0;
    if (remaining > 0) memmove(c->buf, c->buf + consumed, remaining);
    c->buf_len = remaining;

    free(c->method); c->method = NULL;
    free(c->path); c->path = NULL;
    festina_headers_free(c->headers, c->header_count);
    c->headers = NULL;
    c->header_count = 0;
    c->header_capacity = 0;
    free(c->body); c->body = NULL;
    c->body_len = 0;
    c->content_length = -1;
    c->request_ready = 0;
    c->responded = 0;
    c->keep_alive = 0;
    c->headers_parsed = 0;
    c->header_scan_pos = 0;
    c->body_start_offset = 0;
    /* claude.md #168: chunk_body/_len/_cap are already NULL/0/0 by this
     * point in the ordinary case (festina_try_parse_request's own
     * completion tail transfers ownership to c->body/body_len before
     * ever reaching here) -- reset defensively anyway, the same
     * always-safe-to-free spirit every other field above already has. */
    c->is_chunked = 0;
    c->chunk_scan_pos = 0;
    free(c->chunk_body); c->chunk_body = NULL;
    c->chunk_body_len = 0;
    c->chunk_body_cap = 0;
    c->last_activity = festina_now_seconds();
    c->served_a_request = 1;
}

/* claude.md #167: bounds how long a keep-alive connection may sit open
 * with no request in flight before festina_reap_idle_keepalive_
 * connections (below) closes it -- without this, a client that opens a
 * connection, sends one request, and simply never sends another (or
 * closes) would hold an fd and a connection-table slot open forever;
 * nothing else in this runtime limits the NUMBER of connections at all
 * (see security.md), so an unbounded idle lifetime would be a real, if
 * slow, resource-exhaustion path this feature would otherwise introduce
 * that didn't exist before it (every previously-alive connection WAS
 * mid-request, by construction, before keep-alive gave a connection a
 * reason to be alive AND idle at once). 15 seconds is a deliberately
 * modest default -- generous enough for a real client's normal think-
 * time between reusing a connection (a browser loading a page's sub-
 * resources, a script issuing a handful of requests in a loop), short
 * enough that an abandoned connection is reclaimed promptly rather than
 * accumulating. Overridable via FESTINA_HTTP_KEEPALIVE_IDLE_SECONDS --
 * not a documented language-level configuration knob (same tier as
 * FESTINA_SHUTDOWN_GRACE_SECONDS, whose own festina_shutdown_grace_
 * seconds this mirrors exactly), but real, checked-in-tests behavior:
 * it's what lets tests exercise the reap path in a fraction of a second
 * rather than actually waiting out the production default. */
#define FESTINA_HTTP_KEEPALIVE_IDLE_SECONDS_DEFAULT 15.0

static double festina_keepalive_idle_seconds(void) {
    const char *env = getenv("FESTINA_HTTP_KEEPALIVE_IDLE_SECONDS");
    if (env) {
        double v = atof(env);
        if (v > 0.0) return v;
    }
    return FESTINA_HTTP_KEEPALIVE_IDLE_SECONDS_DEFAULT;
}

/* claude.md #167: closes any keep-alive connection that's had no
 * request in flight (mode still READING_REQUEST, nothing buffered,
 * nothing parsed) for longer than festina_keepalive_idle_seconds().
 * Deliberately does NOT touch a connection with a request actually in
 * progress (buf_len > 0 or headers_parsed) -- that's pre-existing
 * behavior this entry doesn't change at all (a slow client mid-request
 * was always left alone, bounded only by FESTINA_HTTP_MAX_BUFFER).
 * Called once per iteration from both festina_run_http_loop and
 * festina_http_service_once (claude.md #166), the same "cheap, no-op
 * when nothing needs it" placement festina_fire_expired_timers/
 * festina_async_drain_completed already have in the former. */
static void festina_reap_idle_keepalive_connections(void) {
    double now = festina_now_seconds();
    double idle_seconds = festina_keepalive_idle_seconds();
    for (int64_t i = 0; i < g_conn_count; i++) {
        FestinaConn *c = &g_conns[i];
        if (!c->alive || c->mode != FESTINA_CONN_READING_REQUEST) continue;
        if (c->buf_len > 0 || c->headers_parsed) continue; /* a request IS in flight */
        if (now - c->last_activity > idle_seconds) {
            festina_conn_teardown(c);
        }
    }
}

/* claude.md #167: the earliest moment ANY currently-idle keep-alive
 * connection will time out, or -1.0 if none are idle at all -- folded
 * into festina_run_http_loop's own poll() timeout the same way the next
 * timer deadline and the shutdown drain deadline already are, so an
 * otherwise-quiet server actually wakes up promptly to reap an
 * abandoned connection instead of only noticing on the NEXT unrelated
 * poll() wakeup (which might be a very long time away, or never, on an
 * otherwise idle server). festina_http_service_once (claude.md #166)
 * has no equivalent need -- the graphics loop that calls it already
 * wakes on its own short, unconditional bound whenever any connection
 * is alive at all (see festina_http_service_outstanding_impl). */
static double festina_earliest_keepalive_deadline(void) {
    double earliest = -1.0;
    double idle_seconds = festina_keepalive_idle_seconds();
    for (int64_t i = 0; i < g_conn_count; i++) {
        FestinaConn *c = &g_conns[i];
        if (!c->alive || c->mode != FESTINA_CONN_READING_REQUEST) continue;
        if (c->buf_len > 0 || c->headers_parsed) continue;
        double deadline = c->last_activity + idle_seconds;
        if (earliest < 0.0 || deadline < earliest) earliest = deadline;
    }
    return earliest;
}

/* claude.md #168: the shared chunked-transfer-encoding decoder (RFC
 * 7230 §4.1) -- decodes as much of a chunked byte stream, starting at
 * `data[*consumed]`, as `len` bytes currently allow, appending each
 * complete chunk's own DATA (not the chunk-size line or its own
 * trailing CRLF) to `*out_body`/`*out_body_len` (grown via realloc as
 * needed, doubling like every other growable buffer in this file) and
 * advancing `*consumed` past everything fully decoded so far.
 *
 * Used TWO ways from the same primitive: incrementally, by
 * festina_try_parse_request below (an inbound chunked REQUEST body may
 * arrive over several separate recv() calls, so `data`/`len` are
 * `c->buf`/`c->buf_len` and `*consumed` is `c->chunk_scan_pos`, resumed
 * across calls exactly the way header_scan_pos already is), and once,
 * in a single pass, by festina_parse_http_response further down (an
 * outbound chunked RESPONSE body has already been fully read by the
 * time that function runs -- festina_client_read_all reads until the
 * peer closes -- so there's nothing to resume across calls there).
 *
 * Returns 1 once the terminating 0-size chunk and its own final blank
 * line have been found (any trailer headers in between are scanned
 * past and discarded, never merged into the request/response's own
 * headers map -- real-world trailers are vanishingly rare and this
 * runtime has no use for them once the body already exists). Returns 0
 * if it ran out of bytes partway through the current chunk -- for the
 * incremental caller this just means "wait for more bytes, try again
 * next time"; for the one-shot caller (which has already seen every
 * byte the peer will ever send) it means the response arrived
 * truncated, treated the same lenient way a short Content-Length body
 * already is elsewhere in this file: whatever decoded so far is simply
 * what the caller gets, not an error.
 *
 * `*ok` is set to 0 only for a genuinely malformed chunk -- an invalid
 * (non-hex, or absurdly long) chunk-size, or chunk data not actually
 * followed by the CRLF the encoding requires. The incremental caller
 * tears the connection down on this; the one-shot caller treats it the
 * same as a truncated response (not a thrown error -- see that
 * function's own doc comment on why body issues don't throw). */
static int festina_chunk_decode_step(const uint8_t *data, size_t len, size_t *consumed,
                                     uint8_t **out_body, size_t *out_body_len,
                                     size_t *out_body_cap, int *ok) {
    *ok = 1;
    for (;;) {
        size_t line_start = *consumed;
        size_t i = line_start;
        while (i + 1 < len && !(data[i] == '\r' && data[i + 1] == '\n')) i++;
        if (i + 1 >= len) return 0; /* not enough bytes yet for the chunk-size line */
        size_t size_len = 0;
        while (line_start + size_len < i && data[line_start + size_len] != ';') size_len++;
        if (size_len == 0 || size_len >= 17) { *ok = 0; return 0; } /* empty or absurd */
        char size_buf[17];
        memcpy(size_buf, data + line_start, size_len);
        size_buf[size_len] = '\0';
        char *endptr;
        unsigned long long chunk_size = strtoull(size_buf, &endptr, 16);
        if (endptr == size_buf || *endptr != '\0') { *ok = 0; return 0; } /* not valid hex */
        size_t data_start = i + 2; /* right past the chunk-size line's own CRLF */
        if (chunk_size == 0) {
            /* Last-chunk -- what follows is zero or more trailer header
             * lines, then one final blank line ends the whole message.
             * Trailers are scanned past a line at a time and discarded. */
            size_t p = data_start;
            for (;;) {
                size_t line_end = p;
                while (line_end + 1 < len && !(data[line_end] == '\r' && data[line_end + 1] == '\n')) line_end++;
                if (line_end + 1 >= len) return 0; /* wait for the rest of the trailer/blank line */
                if (line_end == p) { *consumed = line_end + 2; return 1; } /* blank line -- done */
                p = line_end + 2; /* past this trailer line, keep scanning */
            }
        }
        if (data_start + (size_t)chunk_size + 2 > len) return 0; /* wait for the rest of this chunk */
        if (data[data_start + (size_t)chunk_size] != '\r'
                || data[data_start + (size_t)chunk_size + 1] != '\n') {
            *ok = 0; return 0; /* chunk data not properly CRLF-terminated */
        }
        if (*out_body_len + (size_t)chunk_size > *out_body_cap) {
            size_t new_cap = *out_body_cap ? *out_body_cap * 2 : 4096;
            while (new_cap < *out_body_len + (size_t)chunk_size) new_cap *= 2;
            uint8_t *grown = realloc(*out_body, new_cap);
            if (!grown) festina_fail("out of memory decoding a chunked body");
            *out_body = grown;
            *out_body_cap = new_cap;
        }
        memcpy(*out_body + *out_body_len, data + data_start, (size_t)chunk_size);
        *out_body_len += (size_t)chunk_size;
        *consumed = data_start + (size_t)chunk_size + 2;
        /* Loop -- the next chunk may already be fully buffered too,
         * the same "drain everything currently available" shape the
         * resumable header scan above already uses. */
    }
}

/* ---- HTTP/1.1 request parsing -- request-line + headers + a
 * Content-Length OR chunked (claude.md #168) body, no pipelining. ---- */
static void festina_try_parse_request(FestinaConn *c) {
    if (c->request_ready) return;

    if (!c->headers_parsed) {
        /* claude.md #155: resume the \r\n\r\n search from where the
         * last call left off (header_scan_pos) instead of rescanning
         * the whole buffer from byte 0 every time more bytes arrive --
         * and this whole block runs at most once per connection
         * (headers_parsed), where it used to re-run in full (re-malloc
         * method/path over the previous call's own pointers with
         * nothing freeing them, re-append every header onto the
         * still-populated headers array) on every call that found
         * hdr_end again but was still waiting on the body -- a real,
         * confirmed leak+duplication bug for any request whose body
         * arrives in a later, separate recv() (reproduced directly:
         * headers in one write, body in a second one after a delay,
         * definitely-lost bytes for the doubled method/path/header
         * allocations under Valgrind), not merely a performance one.
         * Backs up 3 bytes from the previous stopping point: a partial
         * "\r\n\r" sitting right at the end of what was scanned last
         * time needs re-testing once the byte that could complete it
         * arrives, since the loop below never tried starting AT that
         * position with all 4 bytes available. */
        size_t scan_start = c->header_scan_pos > 3 ? c->header_scan_pos - 3 : 0;
        uint8_t *hdr_end = NULL;
        for (size_t i = scan_start; i + 3 < c->buf_len; i++) {
            if (c->buf[i] == '\r' && c->buf[i + 1] == '\n'
                    && c->buf[i + 2] == '\r' && c->buf[i + 3] == '\n') {
                hdr_end = c->buf + i;
                break;
            }
        }
        if (!hdr_end) {
            c->header_scan_pos = c->buf_len;
            return;
        }
        const char *p = (const char *)c->buf;
        const char *limit = (const char *)hdr_end;

        /* Request line: METHOD SP PATH SP VERSION */
        const char *method_start = p;
        while (p < limit && *p != ' ') p++;
        /* claude.md #168: festina_conn_teardown, not a bare `c->alive =
         * 0` -- found while adding the equivalent malformed-chunk check
         * below and confirmed pre-existing, not new: setting alive=0
         * alone never actually closes the fd or frees this slot (only
         * festina_conn_teardown's own bookkeeping does that), so a
         * malformed request line used to leak both -- the socket sits
         * open, unpolled, forever, and the connection-table slot never
         * returns to the free list. festina_conn_teardown is safe to
         * call this early: every field it frees (method/path/headers/
         * body/etc) is still NULL at this point in parsing, and
         * free(NULL) is always a no-op. */
        if (p >= limit) { festina_conn_teardown(c); return; } /* malformed -- drop it */
        size_t method_len = (size_t)(p - method_start);
        p++; /* past the space */
        const char *path_start = p;
        while (p < limit && *p != ' ') p++;
        if (p >= limit) { festina_conn_teardown(c); return; }
        size_t path_len = (size_t)(p - path_start);
        /* claude.md #167: the rest of the line (HTTP version) now
         * matters for exactly one thing -- keep-alive's own default
         * when the request sends no `Connection` header at all.
         * `version_start` still points at the space right after
         * `path_start`'s own while loop stopped (not yet consumed),
         * same as before this entry; only whether it's read anywhere
         * is new. */
        const char *version_start = p;
        while (p < limit && *p != '\r') p++;
        const char *version_end = p;
        if (p < limit) p++;
        if (p < limit && *p == '\n') p++;
        const char *v = version_start;
        while (v < version_end && *v == ' ') v++;
        int is_http_1_0 = ((size_t)(version_end - v) >= 8 && strncasecmp(v, "HTTP/1.0", 8) == 0);

        c->method = malloc(method_len + 1);
        c->path = malloc(path_len + 1);
        if (!c->method || !c->path) festina_fail("out of memory parsing an HTTP request");
        memcpy(c->method, method_start, method_len);
        c->method[method_len] = '\0';
        memcpy(c->path, path_start, path_len);
        c->path[path_len] = '\0';

        /* Headers: one "Name: value\r\n" per line until the blank line
         * already located above. */
        while (p < limit) {
            const char *line_start = p;
            while (p < limit && *p != '\r') p++;
            const char *line_end = p;
            if (p < limit) p++;
            if (p < limit && *p == '\n') p++;
            const char *colon = line_start;
            while (colon < line_end && *colon != ':') colon++;
            if (colon >= line_end) continue; /* malformed header line -- skip it */
            const char *name_start = line_start;
            size_t name_len = (size_t)(colon - line_start);
            const char *value_start = colon + 1;
            while (value_start < line_end && *value_start == ' ') value_start++;
            size_t value_len = (size_t)(line_end - value_start);
            festina_headers_add(&c->headers, &c->header_count, &c->header_capacity,
                                name_start, name_len, value_start, value_len);
        }

        /* claude.md #168: Transfer-Encoding wins over Content-Length if
         * a request somehow sends both (invalid per RFC 7230, but this
         * runtime already leans lenient elsewhere rather than adding a
         * whole new rejection path for a rare, already-broken client) --
         * chunked framing is authoritative once present. Exact
         * case-insensitive match against "chunked" alone, the same
         * rigor the Connection/Upgrade headers already get here rather
         * than a comma-separated token parse -- real requests send
         * exactly this one value in practice. */
        const char *te = festina_headers_get(c->headers, c->header_count, "transfer-encoding");
        c->is_chunked = (te && strcasecmp(te, "chunked") == 0);
        const char *cl = festina_headers_get(c->headers, c->header_count, "content-length");
        c->content_length = cl ? strtoll(cl, NULL, 10) : 0;
        if (c->content_length < 0) c->content_length = 0;
        c->body_start_offset = (size_t)((const uint8_t *)limit - c->buf) + 4;
        if (c->is_chunked) c->chunk_scan_pos = c->body_start_offset;
        /* claude.md #167: `Connection: close` always forces it, exact
         * match, case-insensitive -- the same "exact token, not a
         * comma-separated parse" rigor already used for the Upgrade
         * header a few lines above in festina_http_upgrade. Anything
         * else (an explicit `Connection: keep-alive`, or no header at
         * all) falls back to the HTTP version's own default: keep-alive
         * for 1.1+, close for 1.0 -- ordinary HTTP semantics, and this
         * runtime's only use for the version it now bothers to read. */
        const char *conn_hdr = festina_headers_get(c->headers, c->header_count, "connection");
        if (conn_hdr && strcasecmp(conn_hdr, "close") == 0) {
            c->keep_alive = 0;
        } else {
            c->keep_alive = !is_http_1_0;
        }
        c->headers_parsed = 1;
    }

    if (c->is_chunked) {
        /* claude.md #168: incremental -- may need several calls across
         * separate recv()s, same as the Content-Length path below.
         * FESTINA_HTTP_MAX_BUFFER isn't checked again here: it already
         * bounds `buf` itself (festina_conn_readable's own read loop),
         * and chunk-encoded bytes are never fewer than the decoded
         * body they represent (every chunk adds at least "N\r\n"+"\r\n"
         * of its own overhead), so the existing cap on raw bytes
         * received already bounds the decoded body too -- no separate
         * limit needed on chunk_body_len itself. */
        int ok;
        int done = festina_chunk_decode_step(c->buf, c->buf_len, &c->chunk_scan_pos,
                                             &c->chunk_body, &c->chunk_body_len,
                                             &c->chunk_body_cap, &ok);
        if (!ok) { festina_conn_teardown(c); return; } /* malformed chunk -- drop the connection */
        if (!done) return; /* still waiting for more chunks */
        c->body = c->chunk_body;
        c->body_len = (int64_t)c->chunk_body_len;
        c->chunk_body = NULL;
        c->chunk_body_cap = 0;
        c->request_ready = 1;
        return;
    }

    size_t have_body = c->buf_len > c->body_start_offset ? c->buf_len - c->body_start_offset : 0;
    if (have_body < (size_t)c->content_length) return; /* still waiting for the body */

    c->body_len = c->content_length;
    if (c->body_len > 0) {
        c->body = malloc((size_t)c->body_len);
        if (!c->body) festina_fail("out of memory buffering an HTTP request body");
        memcpy(c->body, c->buf + c->body_start_offset, (size_t)c->body_len);
    }
    c->request_ready = 1;
}

/* ---- WebSocket framing (RFC 6455) -- text/binary/close/ping/pong,
 * with fragmentation reassembly (claude.md #168), no extensions. See
 * festina_runtime.h's own top comment for the full scope decision. ---- */

#define FESTINA_WS_GUID "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

static char *festina_ws_accept_key(const char *client_key) {
    FestinaSha1 sha;
    festina_sha1_init(&sha);
    festina_sha1_update(&sha, (const uint8_t *)client_key, strlen(client_key));
    festina_sha1_update(&sha, (const uint8_t *)FESTINA_WS_GUID, strlen(FESTINA_WS_GUID));
    uint8_t digest[20];
    festina_sha1_final(&sha, digest);
    return festina_base64_encode(digest, 20);
}

/* claude.md #160: takes the FestinaConn itself, not a raw fd -- a TLS
 * connection's writes have to go through g_tls_send (mbedtls_ssl_write
 * under the hood), not a plain send() syscall, and c->tls is the only
 * place that's recorded. Every call site already had the FestinaConn
 * in hand (`c->fd` was itself always a field access off one), so this
 * is a signature change with no new bookkeeping needed at any caller. */
static int festina_send_all(FestinaConn *c, const void *data, size_t len) {
    if (c->tls) {
        const uint8_t *p = (const uint8_t *)data;
        size_t sent = 0;
        while (sent < len) {
            /* claude.md #155's own festina_send_all precedent (see the
             * plain-socket branch below): a write that would block is
             * treated as outright failure here too, not retried via
             * the event loop -- g_tls_send already folds mbedTLS's
             * WANT_READ/WANT_WRITE into that same "just fail" answer
             * (see festina_runtime_https.c's own comment on why). */
            long n = g_tls_send(c->tls, p + sent, (int64_t)(len - sent));
            if (n <= 0) return 0;
            sent += (size_t)n;
        }
        return 1;
    }
    const uint8_t *p = (const uint8_t *)data;
    size_t sent = 0;
    while (sent < len) {
        ssize_t n = send(c->fd, (const char *)(p + sent), (int)(len - sent), 0);
        if (n < 0) {
            if (festina_socket_was_interrupted()) continue;
            return 0;
        }
        if (n == 0) return 0;
        sent += (size_t)n;
    }
    return 1;
}

/* claude.md #155: a small growable buffer for building a response's
 * status-line + headers block in memory before sending it in ONE
 * festina_send_all call, instead of one call per line -- with
 * TCP_NODELAY set (Nagle's algorithm disabled, see
 * festina_accept_new_connections), every separate send() used to
 * become its own TCP segment, not just its own syscall, so a response
 * with a couple of extra headers was several packets instead of one.
 * Starts pointed at a small caller-owned stack buffer (every real
 * response's status+headers block fits easily) and only actually
 * allocates on the heap if that's not enough -- the common case pays
 * no malloc at all. The body itself is deliberately NOT copied into
 * this buffer and sent separately: copying a large body in would cost
 * more than the syscall it saves, the same reasoning that already
 * kept the body out of any single buffer here. */
typedef struct {
    char *data;
    size_t len;
    size_t cap;
    int on_heap;
} FestinaSendBuf;

static void festina_sendbuf_init(FestinaSendBuf *b, char *stack_storage, size_t stack_cap) {
    b->data = stack_storage;
    b->len = 0;
    b->cap = stack_cap;
    b->on_heap = 0;
}

static void festina_sendbuf_reserve(FestinaSendBuf *b, size_t extra) {
    if (b->len + extra <= b->cap) return;
    size_t new_cap = b->cap ? b->cap * 2 : 256;
    while (new_cap < b->len + extra) new_cap *= 2;
    char *grown = b->on_heap ? realloc(b->data, new_cap) : malloc(new_cap);
    if (!grown) festina_fail("out of memory building an HTTP response header block");
    if (!b->on_heap) memcpy(grown, b->data, b->len); /* first spill off the stack */
    b->data = grown;
    b->cap = new_cap;
    b->on_heap = 1;
}

static void festina_sendbuf_append(FestinaSendBuf *b, const char *s, size_t n) {
    if (n == 0) return;
    festina_sendbuf_reserve(b, n);
    memcpy(b->data + b->len, s, n);
    b->len += n;
}

static void festina_sendbuf_free(FestinaSendBuf *b) {
    if (b->on_heap) free(b->data);
}

/* sizeof(lit)-1 rather than a hand-counted length or strlen() on a
 * literal -- compiler-computed, so it can never drift from the
 * literal's own text the way a manually maintained number could. */
#define FESTINA_APPEND_LIT(b, lit) festina_sendbuf_append((b), (lit), sizeof(lit) - 1)

/* Writes ONE unmasked server->client frame (server frames are never
 * masked per RFC 6455) -- `opcode` 0x1 text, 0x2 binary, 0x8 close,
 * 0xA pong. */
static void festina_ws_send_frame(FestinaConn *c, uint8_t opcode, const void *data, size_t len) {
    uint8_t header[10];
    size_t header_len = 0;
    header[0] = 0x80 | (opcode & 0x0F); /* FIN=1, no fragmentation from this runtime */
    if (len <= 125) {
        header[1] = (uint8_t)len;
        header_len = 2;
    } else if (len <= 0xFFFF) {
        header[1] = 126;
        header[2] = (uint8_t)(len >> 8);
        header[3] = (uint8_t)len;
        header_len = 4;
    } else {
        header[1] = 127;
        for (int i = 0; i < 8; i++) header[2 + i] = (uint8_t)((uint64_t)len >> (56 - 8 * i));
        header_len = 10;
    }
    if (!festina_send_all(c, header, header_len)) return;
    if (len > 0) festina_send_all(c, data, len);
}

/* Tries to parse and consume ONE complete frame from c->buf. Returns
 * 1 (and shifts the consumed bytes out of the buffer) if a full frame
 * was available, 0 if more bytes are still needed. `*opcode`/`*payload`/
 * `*payload_len` are only meaningful when this returns 1; `*payload`
 * is a freshly malloc'd, already-unmasked copy the caller owns. */
static int festina_ws_try_parse_frame(FestinaConn *c, int *fin_out, uint8_t *opcode,
                                      uint8_t **payload, size_t *payload_len) {
    if (c->buf_len < 2) return 0;
    uint8_t b0 = c->buf[0];
    uint8_t b1 = c->buf[1];
    int fin = (b0 & 0x80) != 0;
    uint8_t op = b0 & 0x0F;
    int masked = (b1 & 0x80) != 0;
    uint64_t len = b1 & 0x7F;
    size_t pos = 2;
    if (len == 126) {
        if (c->buf_len < pos + 2) return 0;
        len = ((uint64_t)c->buf[pos] << 8) | c->buf[pos + 1];
        pos += 2;
    } else if (len == 127) {
        if (c->buf_len < pos + 8) return 0;
        len = 0;
        for (int i = 0; i < 8; i++) len = (len << 8) | c->buf[pos + i];
        pos += 8;
    }
    uint8_t mask_key[4] = {0, 0, 0, 0};
    if (masked) {
        if (c->buf_len < pos + 4) return 0;
        memcpy(mask_key, c->buf + pos, 4);
        pos += 4;
    }
    if (c->buf_len < pos + len) return 0; /* payload not fully arrived yet */

    uint8_t *out = NULL;
    if (len > 0) {
        out = malloc((size_t)len);
        if (!out) festina_fail("out of memory reading a websocket frame");
        for (uint64_t i = 0; i < len; i++) {
            out[i] = c->buf[pos + i] ^ (masked ? mask_key[i % 4] : 0);
        }
    }
    size_t frame_total = pos + (size_t)len;
    memmove(c->buf, c->buf + frame_total, c->buf_len - frame_total);
    c->buf_len -= frame_total;

    /* claude.md #168: reports the wire frame HONESTLY now -- fin/opcode
     * exactly as sent, no more collapsing FIN=0 into a synthetic 0xFF
     * "unsupported" opcode. Reassembly (or rejecting a genuinely
     * malformed fragment sequence) is festina_ws_process_one_frame's
     * own job now, not this function's. */
    *fin_out = fin;
    *opcode = op;
    *payload = out;
    *payload_len = (size_t)len;
    return 1;
}

/* claude.md #168: appends `len` bytes to this connection's own
 * in-progress fragmented-message reassembly buffer, growing it as
 * needed (doubling, like every other growable buffer in this file) --
 * bounded by FESTINA_HTTP_MAX_BUFFER, the same cap every other
 * connection-scoped buffer here already has. Unlike a chunked HTTP
 * body (claude.md #168's other half), nothing else in this runtime
 * implicitly bounds a reassembled WebSocket message's cumulative size:
 * each wire frame is fully consumed out of `c->buf` as soon as it's
 * parsed (festina_ws_try_parse_frame's own memmove above), so `buf`'s
 * own cap only ever bounds ONE frame at a time, never the sum of many.
 * Returns 0 (append refused, nothing appended) if growing would exceed
 * the cap -- the caller closes the connection with WebSocket close
 * code 1009 ("Message Too Big") in that case, the same real close code
 * a production WebSocket server would use, rather than silently
 * truncating the message or growing without bound for a hostile or
 * simply very large peer. */
static int festina_ws_frag_append(FestinaConn *c, const uint8_t *data, size_t len) {
    if (c->ws_frag_len + len > (size_t)FESTINA_HTTP_MAX_BUFFER) return 0;
    if (c->ws_frag_len + len > c->ws_frag_cap) {
        size_t new_cap = c->ws_frag_cap ? c->ws_frag_cap * 2 : 4096;
        while (new_cap < c->ws_frag_len + len) new_cap *= 2;
        uint8_t *grown = realloc(c->ws_frag_buf, new_cap);
        if (!grown) festina_fail("out of memory reassembling a websocket message");
        c->ws_frag_buf = grown;
        c->ws_frag_cap = new_cap;
    }
    memcpy(c->ws_frag_buf + c->ws_frag_len, data, len);
    c->ws_frag_len += len;
    return 1;
}

/* claude.md #168: closes the connection with a WebSocket protocol-error
 * close frame (code 1002) -- the shared tail every "this shouldn't have
 * happened" branch in festina_ws_process_one_frame below reaches: a
 * fragmented control frame, a continuation with nothing being
 * reassembled, or a new message starting while one is already in
 * progress. `payload` (if any) is freed here -- every call site below
 * hands off a frame it has already decided not to use for anything
 * else. */
static void festina_ws_protocol_error(FestinaConn *c, uint8_t *payload) {
    free(payload);
    uint8_t close_payload[2] = {0x03, 0xEA}; /* 1002, big-endian */
    festina_ws_send_frame(c, 0x8, close_payload, 2);
    festina_conn_teardown(c);
}

/* claude.md #168: parses and handles exactly ONE wire frame, reassembling
 * a fragmented text/binary message (RFC 6455 §5.4) across however many
 * calls it takes rather than rejecting FIN=0 outright the way this
 * runtime used to. A fragmented message is a FIN=0 text/binary frame
 * (its opcode says which kind), followed by one or more FIN=0
 * continuation frames (opcode 0x0), ending with a FIN=1 continuation
 * frame -- dispatched then, with the ORIGINAL opcode and the full
 * concatenated payload, exactly the same shape a single, ordinary
 * FIN=1 message already dispatches with (festina_dispatch_ws_frame
 * itself needed no changes at all). Control frames (close/ping/pong)
 * are never fragmented and are handled immediately regardless of
 * whether a text/binary message is mid-reassembly -- RFC 6455 §5.4
 * explicitly allows interleaving them between another message's own
 * fragments, and this runtime's own reassembly state is untouched by
 * one passing through.
 *
 * Returns 1 if a complete wire frame was consumed (whether or not that
 * completed a whole MESSAGE -- a continuation frame that doesn't finish
 * the reassembly yet still counts, so the caller's own loop keeps
 * trying in case a further frame is already buffered too), 0 if
 * there's no complete wire frame available yet. */
static int festina_ws_process_one_frame(FestinaConn *c) {
    int fin;
    uint8_t opcode;
    uint8_t *payload;
    size_t payload_len;
    if (!festina_ws_try_parse_frame(c, &fin, &opcode, &payload, &payload_len)) return 0;

    switch (opcode) {
    case 0x8: case 0x9: case 0xA: /* close/ping/pong -- never fragmented */
        if (!fin) { festina_ws_protocol_error(c, payload); return 1; }
        festina_dispatch_ws_frame(c, opcode, payload, payload_len);
        return 1;

    case 0x1: case 0x2: /* text/binary -- starts a (possibly new) message */
        if (c->ws_frag_active) { festina_ws_protocol_error(c, payload); return 1; }
        if (fin) {
            /* The ordinary, overwhelmingly common case, unchanged from
             * before this entry: one complete frame IS the whole
             * message. */
            festina_dispatch_ws_frame(c, opcode, payload, payload_len);
            return 1;
        }
        /* FIN=0 -- the first fragment of a new message. */
        c->ws_frag_active = 1;
        c->ws_frag_opcode = opcode;
        c->ws_frag_len = 0;
        if (payload_len > 0 && !festina_ws_frag_append(c, payload, payload_len)) {
            free(payload);
            uint8_t close_payload[2] = {0x03, 0xF1}; /* 1009, big-endian */
            festina_ws_send_frame(c, 0x8, close_payload, 2);
            festina_conn_teardown(c);
            return 1;
        }
        free(payload);
        return 1;

    case 0x0: /* continuation */
        if (!c->ws_frag_active) { festina_ws_protocol_error(c, payload); return 1; }
        if (payload_len > 0 && !festina_ws_frag_append(c, payload, payload_len)) {
            free(payload);
            uint8_t close_payload[2] = {0x03, 0xF1};
            festina_ws_send_frame(c, 0x8, close_payload, 2);
            festina_conn_teardown(c);
            return 1;
        }
        free(payload);
        if (fin) {
            /* Reassembly complete -- dispatch with the ORIGINAL opcode
             * and the full reassembled payload. Ownership of
             * ws_frag_buf transfers into the dispatch call (it frees
             * the payload it's handed, same as any other dispatched
             * frame) -- only the bookkeeping fields are reset here. */
            uint8_t msg_opcode = c->ws_frag_opcode;
            uint8_t *msg_payload = c->ws_frag_buf;
            size_t msg_len = c->ws_frag_len;
            c->ws_frag_active = 0;
            c->ws_frag_buf = NULL;
            c->ws_frag_len = 0;
            c->ws_frag_cap = 0;
            festina_dispatch_ws_frame(c, msg_opcode, msg_payload, msg_len);
        }
        return 1;

    default:
        /* A genuinely unrecognized opcode (reserved by the spec) --
         * unsupported, same close-1003 behavior this runtime already
         * had for every opcode it doesn't understand. */
        free(payload);
        {
            uint8_t close_payload[2] = {0x03, 0xEB}; /* 1003, big-endian */
            festina_ws_send_frame(c, 0x8, close_payload, 2);
        }
        festina_conn_teardown(c);
        return 1;
    }
}

/* ---- accept/read/dispatch -- the loop body ---- */

static void festina_conn_ensure_capacity(FestinaConn *c, size_t extra) {
    if (c->buf_len + extra <= c->buf_cap) return;
    size_t new_cap = c->buf_cap ? c->buf_cap * 2 : 4096;
    while (new_cap < c->buf_len + extra) new_cap *= 2;
    if (new_cap > FESTINA_HTTP_MAX_BUFFER) new_cap = FESTINA_HTTP_MAX_BUFFER;
    uint8_t *grown = realloc(c->buf, new_cap);
    if (!grown) festina_fail("out of memory growing a connection's read buffer");
    c->buf = grown;
    c->buf_cap = new_cap;
}

/* claude.md #162: reconstructs the one field the OLD design never
 * needed to (there was no `.url`, only separate `.port`/`.path`) --
 * scheme from whether this connection came in on a TLS listener,
 * host from the request's own Host header (falling back to
 * "127.0.0.1:<port>" when the client sent none at all -- HTTP/1.0
 * clients, or a deliberately minimal one), path straight from the
 * request line. An honest reconstruction, not a claim of knowing the
 * "real" externally-visible URL a reverse proxy in front of this
 * server might present instead -- the same kind of best-effort
 * inference `req.headers`/etc. already are. */
static char *festina_build_inbound_url(FestinaConn *c) {
    const char *scheme = c->tls ? "https" : "http";
    const char *host = festina_headers_get(c->headers, c->header_count, "host");
    char host_buf[80];
    if (!host) {
        snprintf(host_buf, sizeof(host_buf), "127.0.0.1:%d", c->listen_port);
        host = host_buf;
    }
    const char *path = c->path ? c->path : "/";
    size_t len = strlen(scheme) + 3 + strlen(host) + strlen(path) + 1;
    char *url = malloc(len);
    if (!url) festina_fail("out of memory building an inbound request's url");
    snprintf(url, len, "%s://%s%s", scheme, host, path);
    return url;
}

/* claude.md #162: builds the SAME fresh map[text] claude.md #151's own
 * original festina_http_headers accessor used to build on every call
 * -- now built exactly once, at dispatch time, and stored directly in
 * the http value itself (see festina_http_headers's own new "return
 * the live map, retained" doc comment for why that's a real
 * improvement, not just a rename). */
static void *festina_build_headers_map(FestinaConn *c) {
    void *map = festina_new_empty_text_map();
    FestinaMapBlock *block = (FestinaMapBlock *)((char *)map - sizeof(int64_t));
    for (int64_t i = 0; i < c->header_count; i++) {
        char *owned_value = festina_text_own(c->headers[i].value);
        festina_map_set(&block->count, &block->entries, c->headers[i].name,
                        (int64_t)(intptr_t)owned_value);
    }
    return map;
}

static void festina_dispatch_request(FestinaConn *c) {
    char *url = festina_build_inbound_url(c);
    void *headers = festina_build_headers_map(c);
    /* claude.md #162: `code` is festina_null_int() -- a live inbound
     * request has no status code of its own (see the http type's own
     * doc comment: null until a response exists) -- and conn_id is
     * THIS connection's own, so .ok()/.redirect()/.upgrade()/.send(res)
     * all reach it correctly. */
    void *payload = festina_http_value_new(url, c->method, FESTINA_NULL_INT, headers,
                                           c->body, c->body_len, c->conn_id, NULL);
    free(url);
    if (g_request_handler) g_request_handler(payload);
    /* c may be gone (the peer could theoretically vanish mid-handler
     * via another fd's event, though nothing in THIS handler's own
     * body can close arbitrary other connections) -- re-look-up by id
     * rather than trusting the stale pointer across the callback. */
    FestinaConn *fresh = festina_conn_by_id(c->conn_id);
    festina_release_http(payload);
    if (!fresh) return;
    if (fresh->mode == FESTINA_CONN_WEBSOCKET) {
        fresh->ever_upgraded = 1;
        if (g_upgrade_handler) {
            void *sock_handle = festina_handle_new(fresh->conn_id);
            g_upgrade_handler(sock_handle);
            festina_release_conn_handle(sock_handle);
        }
        return; /* stays open for WebSocket framing */
    }
    if (!fresh->responded) {
        /* claude.md #151/#162: nothing in `on request` ever called
         * ok()/redirect()/send()/upgrade() -- rather than leave the
         * client hanging forever, send the same forgiving default
         * every other "the program didn't explicitly handle this"
         * path in this runtime already sends: a plain 200 with an
         * empty body. A throwaway on-stack FestinaHttpValue with just
         * conn_id set (everything else zeroed, and never touched by
         * festina_http_ok) reaches festina_live_conn's own lookup
         * correctly -- the same "skip the real allocation for a
         * purely-internal call" trick claude.md #151's own
         * `&fresh->conn_id`-as-handle used, adapted to this value's
         * real (larger) shape. */
        FestinaHttpValue fallback_value;
        memset(&fallback_value, 0, sizeof(fallback_value));
        fallback_value.conn_id = fresh->conn_id;
        festina_http_ok(&fallback_value.url);
    }
    /* claude.md #167: keep_alive was decided once, when this request's
     * own headers were parsed (festina_try_parse_request) -- read here
     * rather than re-derived, since by now `fresh->headers`/etc have
     * already been consulted by whichever of ok()/redirect()/send()
     * above actually answered (or the fallback just above did). A
     * websocket upgrade never reaches this line at all (the mode check
     * above already returned), so keep-alive vs. close is only ever a
     * question for a connection still in FESTINA_CONN_READING_REQUEST. */
    if (fresh->keep_alive) {
        festina_conn_reset_for_next_request(fresh);
    } else {
        festina_conn_teardown(fresh);
    }
}

static void festina_dispatch_ws_frame(FestinaConn *c, uint8_t opcode,
                                      uint8_t *payload, size_t payload_len) {
    switch (opcode) {
    case 0x1: /* text */
    case 0x2: { /* binary */
        void *blob = festina_blob_from_bytes(payload, (int64_t)payload_len);
        free(payload);
        if (g_message_handler) {
            void *handle = festina_handle_new(c->conn_id);
            g_message_handler(handle, blob);
            festina_release_conn_handle(handle);
        }
        festina_blob_release(blob);
        break;
    }
    case 0x8: /* close */
        festina_ws_send_frame(c, 0x8, payload, payload_len);
        free(payload);
        festina_conn_teardown(c);
        break;
    case 0x9: /* ping -- answer with a pong carrying the same payload */
        festina_ws_send_frame(c, 0xA, payload, payload_len);
        free(payload);
        break;
    case 0xA: /* pong -- nothing sent this, but tolerate it anyway */
        free(payload);
        break;
    default:
        /* claude.md #168: unreachable in practice -- festina_ws_process_
         * one_frame (the only caller) never hands this function anything
         * but 0x1/0x2 (a complete or freshly-reassembled text/binary
         * message) or 0x8/0x9/0xA (a control frame); every other opcode,
         * fragmented or not, is already handled -- and closed on if
         * genuinely invalid -- before dispatch is ever reached. A real,
         * safe fallback rather than a silent no-op if that invariant is
         * ever wrong. */
        free(payload);
        festina_conn_teardown(c);
        break;
    }
}

static void festina_conn_readable(FestinaConn *c) {
    /* claude.md #160: raw bytes off the wire are still the TLS
     * handshake itself until this connection's own handshake
     * completes -- driven here, across however many poll() ticks it
     * takes (mbedtls_ssl_handshake is itself non-blocking on this
     * runtime's already-non-blocking fds, returning WANT_READ/
     * WANT_WRITE rather than blocking to completion). Every one of
     * THIS function's own call sites (festina_run_http_loop, once per
     * readable-or-writable poll event) reaches this unconditionally,
     * same as a plain connection's first readable byte would. */
    if (c->tls && !c->tls_handshake_done) {
        int hs = g_tls_handshake(c->tls);
        if (hs < 0) { festina_conn_teardown(c); return; }
        if (hs != 1) { c->tls_wants_write = (hs == 2); return; }
        c->tls_handshake_done = 1;
        c->tls_wants_write = 0;
        /* Falls through to the read loop below immediately -- the
         * handshake's own last flight and the client's first
         * application-data record can arrive in the same physical
         * recv(), already fully consumed by mbedTLS's internal BIO
         * reads during the handshake call above, so waiting for
         * another POLLIN here could stall forever. */
    }
    for (;;) {
        festina_conn_ensure_capacity(c, 4096);
        if (c->buf_len >= c->buf_cap) { festina_conn_teardown(c); return; }
        ssize_t n;
        if (c->tls) {
            long r = g_tls_recv(c->tls, c->buf + c->buf_len, (int64_t)(c->buf_cap - c->buf_len));
            if (r == -1) break;                      /* would block */
            if (r <= -2) { festina_conn_teardown(c); return; } /* fatal */
            if (r == 0) { festina_conn_teardown(c); return; }  /* peer closed */
            n = (ssize_t)r;
        } else {
            n = recv(c->fd, (char *)(c->buf + c->buf_len), (int)(c->buf_cap - c->buf_len), 0);
            if (n < 0) {
                if (festina_socket_would_block()) break;
                if (festina_socket_was_interrupted()) continue;
                festina_conn_teardown(c);
                return;
            }
            if (n == 0) { /* peer closed */
                festina_conn_teardown(c);
                return;
            }
        }
        c->buf_len += (size_t)n;
        if (c->buf_len >= (size_t)FESTINA_HTTP_MAX_BUFFER
                && !(c->mode == FESTINA_CONN_READING_REQUEST && c->request_ready)) {
            festina_conn_teardown(c);
            return;
        }
    }
    if (c->mode == FESTINA_CONN_READING_REQUEST) {
        /* claude.md #167: a loop, not a single try-then-dispatch --
         * festina_dispatch_request's own keep-alive path
         * (festina_conn_reset_for_next_request) can leave buf already
         * holding a SECOND complete request if the client sent more
         * than one before this recv() loop, above, drained everything
         * currently on the wire (see that function's own doc comment
         * on why simply waiting for another poll()-readable event
         * could deadlock in that case). Each iteration re-checks
         * c->alive/request_ready itself, so the loop naturally stops
         * the moment there's no complete request left buffered --
         * ordinarily that's true after exactly one iteration, since a
         * well-behaved client doesn't send request 2 before it's read
         * response 1. */
        for (;;) {
            festina_try_parse_request(c);
            if (!c->alive) return; /* malformed request -- already torn down */
            if (!c->request_ready) return; /* need more bytes -- wait for the next poll() */
            festina_dispatch_request(c);
            /* dispatch may have torn this connection down (no keep-
             * alive, or the peer vanished mid-handler -- see
             * festina_dispatch_request's own comment), switched it to
             * WebSocket mode, or reset it for another request; refetch
             * by id and stop looping unless it's still here and still
             * reading a plain HTTP request. */
            c = festina_conn_by_id(c->conn_id);
            if (!c || c->mode != FESTINA_CONN_READING_REQUEST) return;
        }
    } else {
        /* claude.md #168: festina_ws_process_one_frame handles exactly
         * one wire frame per call -- including fragmentation
         * reassembly across however many of them a message takes --
         * and reports whether one was actually consumed, the same
         * "keep draining whatever's already buffered" shape this loop
         * already had. */
        for (;;) {
            if (!festina_ws_process_one_frame(c)) break;
            if (!festina_conn_by_id(c->conn_id)) break; /* torn down mid-dispatch */
        }
    }
}

static void festina_set_nonblocking(FestinaSocket fd) {
#ifdef _WIN32
    u_long mode = 1;
    ioctlsocket(fd, FIONBIO, &mode);
#else
    int flags = fcntl(fd, F_GETFL, 0);
    if (flags >= 0) fcntl(fd, F_SETFL, flags | O_NONBLOCK);
#endif
}

static void festina_accept_new_connections(FestinaSocket listen_fd, int port, void *tls_config) {
    for (;;) {
        FestinaSocket fd = accept(listen_fd, NULL, NULL);
        if (fd == FESTINA_INVALID_SOCKET) {
            if (festina_socket_would_block()) return;
            if (festina_socket_was_interrupted()) continue;
            return;
        }
        festina_set_nonblocking(fd);
        int one = 1;
        setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, (const char *)&one, sizeof(one));
        FestinaConn *c = festina_conn_new_slot();
        c->fd = fd;
        c->listen_port = port;
        if (tls_config) {
            /* claude.md #160: the fd handed to mbedTLS's own BIO
             * callbacks has to be a plain `int` (mbedtls_net_context's
             * one field) -- true of FestinaSocket already on POSIX
             * (typedef int), and matches mbedTLS's own established
             * Windows convention of storing a SOCKET truncated to int
             * (see festina_runtime_https.c's own top comment: not a
             * new risk this file introduces). */
            c->tls = g_tls_conn_new(tls_config, (int)fd);
            if (!c->tls) { festina_conn_teardown(c); continue; }
        }
    }
}

/* claude.md #160: the shared body of festina_open_port/
 * _open_secure_port -- everything except deciding whether `tls_config`
 * is non-NULL, which the two public entry points do differently
 * (plain openPort() always passes NULL; openSecurePort() builds one
 * via g_tls_listener_new first and passes that). Takes ownership of a
 * non-NULL tls_config immediately: every early-return path below frees
 * it via g_tls_listener_free before returning (openPort/closePort's
 * own established "never fails the program, just silently doesn't
 * open" contract extends to openSecurePort too -- a bad port number or
 * a bind()/listen() failure shouldn't leak the TLS config it'll never
 * use). */
static void festina_open_port_impl(int64_t port, void *tls_config) {
    if (port < 1 || port > 65535) { /* never fails the program -- see festina_runtime.h */
        if (tls_config && g_tls_listener_free) g_tls_listener_free(tls_config);
        return;
    }
#ifdef _WIN32
    /* claude.md #151 (Windows round): Winsock needs an explicit
     * WSAStartup() before any socket call -- idempotent to call more
     * than once (Winsock reference-counts it internally), so this is
     * safe on every openPort() rather than gated to "only the first".
     * WSACleanup() is deliberately never called: the process exiting
     * tears everything down anyway (this runtime's own established
     * "no GC yet" convention -- see the module docstring), and a
     * clean shutdown here would need tracking how many WSAStartup
     * calls actually happened, for no benefit. */
    static int wsa_started = 0;
    if (!wsa_started) {
        WSADATA wsa_data;
        WSAStartup(MAKEWORD(2, 2), &wsa_data);
        wsa_started = 1;
    }
#else
    /* claude.md #151: a real, silent-crash bug caught by an actual
     * stress test, not reasoned about in advance -- send()/recv() on a
     * connection the PEER has already reset (a client that closes
     * early, or a genuine network hiccup) raises SIGPIPE, whose
     * DEFAULT disposition is to terminate the whole process with no
     * error message at all -- indistinguishable from a silent hang
     * until traced. write()/send() already report this the POSIX way
     * (return -1, errno == EPIPE) wherever this file checks -- see
     * festina_send_all -- so the signal itself is pure noise this
     * server needs ignored, not handled. Idempotent, so it's safe to
     * call on every openPort() rather than only the first. Windows has
     * no SIGPIPE for a broken socket at all (send() just answers an
     * error, same as this file already checks for) -- nothing to
     * ignore there, hence the #else. */
    signal(SIGPIPE, SIG_IGN);
#endif
    for (int64_t i = 0; i < g_listener_count; i++) {
        if (g_listeners[i].port == (int)port) { /* already open -- silent no-op */
            if (tls_config && g_tls_listener_free) g_tls_listener_free(tls_config);
            return;
        }
    }
    FestinaSocket fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd == FESTINA_INVALID_SOCKET) {
        if (tls_config && g_tls_listener_free) g_tls_listener_free(tls_config);
        return;
    }
#ifndef _WIN32
    /* claude.md #151 (Windows round): SO_REUSEADDR means something
     * more permissive on Windows (lets a DIFFERENT process bind the
     * SAME port concurrently, not just "skip TIME_WAIT" the way it
     * does on POSIX) -- a real foot-gun there, not the same option
     * with a platform quirk, so it's simply not set on Windows at
     * all rather than ported as-is. */
    int one = 1;
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, (const char *)&one, sizeof(one));
#endif
    struct sockaddr_in addr;
    memset(&addr, 0, sizeof(addr));
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = INADDR_ANY;
    addr.sin_port = htons((uint16_t)port);
    if (bind(fd, (struct sockaddr *)&addr, sizeof(addr)) != 0) {
        festina_close_fd(fd);
        if (tls_config && g_tls_listener_free) g_tls_listener_free(tls_config);
        return;
    }
    if (listen(fd, 128) != 0) {
        festina_close_fd(fd);
        if (tls_config && g_tls_listener_free) g_tls_listener_free(tls_config);
        return;
    }
    festina_set_nonblocking(fd);

    if (g_listener_count == g_listener_capacity) {
        g_listener_capacity = g_listener_capacity ? g_listener_capacity * 2 : 4;
        FestinaListener *grown = realloc(g_listeners, (size_t)g_listener_capacity * sizeof(FestinaListener));
        if (!grown) festina_fail("out of memory growing the listener table");
        g_listeners = grown;
    }
    g_listeners[g_listener_count].fd = fd;
    g_listeners[g_listener_count].port = (int)port;
    g_listeners[g_listener_count].tls_config = tls_config;
    g_listener_count++;
}

void festina_open_port(int64_t port) {
    festina_open_port_impl(port, NULL);
}

void festina_open_secure_port(int64_t port, const uint8_t *key, int64_t key_len) {
    /* claude.md #160: never fails the program on a bad port number
     * (matches festina_open_port_impl's own contract for one), but a
     * malformed/mismatched certificate or key IS a program-authoring
     * mistake worth failing loudly for -- g_tls_listener_new itself
     * calls festina_fail with the real mbedTLS error text on any parse
     * failure (see festina_runtime_https.c), so nothing further needs
     * checking here beyond routing to it in the first place. A program
     * that links no TLS hooks at all (g_tls_listener_new NULL --
     * unreachable in practice, since codegen only emits a call to this
     * function when self.uses_https, which is exactly when it also
     * registers the hooks) is treated the same as an invalid port: a
     * silent no-op, not a crash. */
    if (port < 1 || port > 65535 || !g_tls_listener_new) return;
    void *tls_config = g_tls_listener_new(key, key_len);
    festina_open_port_impl(port, tls_config);
}

static void festina_close_listener_at(int64_t i) {
    festina_close_fd(g_listeners[i].fd);
    if (g_listeners[i].tls_config && g_tls_listener_free) {
        g_tls_listener_free(g_listeners[i].tls_config);
    }
    g_listeners[i] = g_listeners[g_listener_count - 1];
    g_listener_count--;
}

void festina_close_port(int64_t port) {
    for (int64_t i = 0; i < g_listener_count; i++) {
        if (g_listeners[i].port == (int)port) {
            festina_close_listener_at(i);
            return;
        }
    }
}

/* claude.md #161: graceful shutdown's own first move -- close every
 * listening socket outright (rather than merely excluding them from
 * the next poll() call) so the OS immediately refuses any new
 * connection attempt (ECONNREFUSED) instead of it sitting in the
 * kernel's accept queue with nothing ever calling accept() on it.
 * Reuses festina_close_listener_at, the exact same per-listener
 * cleanup festina_close_port already does (fd close + TLS config
 * free) -- walked backwards so removing index i by swapping in the
 * last element (that function's own compaction trick) never skips
 * the element that swap just moved into position i. */
static void festina_close_all_listeners(void) {
    for (int64_t i = g_listener_count - 1; i >= 0; i--) {
        festina_close_listener_at(i);
    }
}

/* ---- the combined loop -- HTTP/WebSocket I/O + timers ---- */

/* claude.md #155: persistent, doubling-growth poll-set buffers rather
 * than malloc'd and freed fresh on every single loop tick -- the same
 * buffer-reuse idea festina_conn_ensure_capacity already uses for a
 * connection's own read buffer, applied to the loop that drives every
 * connection. Never freed (this runtime's own established "no GC yet,
 * process exit tears everything down" convention, same as g_conns/
 * g_listeners themselves). */
static FestinaPollFd *g_poll_fds = NULL;
static int64_t *g_poll_conn_ids = NULL;
static size_t g_poll_cap = 0;

/* claude.md #161: graceful shutdown's own drain state -- set once, the
 * first loop iteration after festina_shutdown_requested() goes true.
 * FESTINA_SHUTDOWN_GRACE_SECONDS bounds how long already-open
 * connections get to finish on their own before this loop gives up on
 * them and exits anyway: generous for this runtime's own one-shot
 * request-then-close HTTP/1.1 model (a normal response finishes in
 * milliseconds), but bounded, since a long-lived WebSocket connection
 * that never closes on its own would otherwise hold shutdown open
 * forever. */
static int g_http_draining = 0;
static double g_http_drain_deadline = 0.0;
#define FESTINA_SHUTDOWN_GRACE_SECONDS_DEFAULT 10.0

/* Overridable via FESTINA_SHUTDOWN_GRACE_SECONDS -- not a documented
 * language feature (see api.md's own note: this is a debug/test knob,
 * not language-level configuration), but real, checked-in-tests
 * behavior: it's what lets tests/test_graceful_shutdown.py exercise
 * the forced-cutoff path (a connection that genuinely never closes)
 * in a fraction of a second rather than actually waiting out the
 * production default. */
static double festina_shutdown_grace_seconds(void) {
    const char *env = getenv("FESTINA_SHUTDOWN_GRACE_SECONDS");
    if (env) {
        double v = atof(env);
        if (v > 0.0) return v;
    }
    return FESTINA_SHUTDOWN_GRACE_SECONDS_DEFAULT;
}

static int64_t festina_alive_conn_count(void) {
    int64_t n = 0;
    for (int64_t i = 0; i < g_conn_count; i++) {
        if (g_conns[i].alive) n++;
    }
    return n;
}

void festina_run_http_loop(void) {
    for (;;) {
        /* claude.md #161: checked once per iteration, this loop's own
         * natural poll point (the same shape festina_run_timer_loop's
         * own check has). The first tick after a signal arrives closes
         * every listener OUTRIGHT (not just excluded from the next
         * poll() call) so the OS immediately refuses any new connection
         * attempt instead of it sitting unaccepted -- from then on,
         * the listener-building loop just below emits nothing (the
         * listener table is already empty), so this loop keeps
         * servicing whatever connections were already open, same as
         * always, until either none are left or the grace period
         * above elapses. */
        if (festina_shutdown_requested()) {
            if (!g_http_draining) {
                g_http_draining = 1;
                g_http_drain_deadline = festina_now_seconds() + festina_shutdown_grace_seconds();
                festina_close_all_listeners();
                /* claude.md #167: an idle keep-alive connection that's
                 * ALREADY served at least one request -- no request in
                 * flight, just open and waiting to be reused -- has
                 * nothing left to finish, so it shouldn't hold up the
                 * grace period the way a connection genuinely mid-
                 * request does; close it right away, the same instant
                 * every listener above already is. served_a_request is
                 * the load-bearing part of this check, not an
                 * optimization -- a freshly accepted connection that
                 * simply hasn't sent its FIRST request yet looks
                 * IDENTICAL by every other field (buf_len == 0,
                 * !headers_parsed), and closing it here would silently
                 * drop a request that was genuinely about to arrive
                 * (confirmed as a real bug during development, not
                 * theoretical -- see FestinaConn's own doc comment on
                 * this field). */
                for (int64_t i = 0; i < g_conn_count; i++) {
                    FestinaConn *ic = &g_conns[i];
                    if (ic->alive && ic->mode == FESTINA_CONN_READING_REQUEST
                            && ic->buf_len == 0 && !ic->headers_parsed && ic->served_a_request) {
                        festina_conn_teardown(ic);
                    }
                }
            }
            /* claude.md #163: an outstanding background request also
             * has to finish before shutdown gives up on it, the same
             * grace period an already-open connection already gets --
             * it's real in-flight work, even though it isn't in
             * g_conns at all (it owns its own raw socket, on a worker
             * thread, entirely separate from this connection table). */
            if ((festina_alive_conn_count() == 0 && g_async_outstanding == 0)
                    || festina_now_seconds() >= g_http_drain_deadline) {
                festina_program_exit(festina_shutdown_exit_code());
            }
        }
        /* claude.md #155: over-allocate to the known upper bound
         * (g_listener_count + g_conn_count, the connection table's own
         * high-water mark -- includes any dead-but-not-yet-reused
         * slots, so it's never an undercount) instead of first
         * counting exactly how many connections are alive -- trades a
         * small amount of possibly-wasted array size for skipping one
         * whole linear pass over the connection table every tick.
         * claude.md #163: +1 reserves room for the async wake-pipe fd
         * appended one slot past nfds below, whether or not the async
         * pool has actually been started by the time this runs. */
        size_t max_nfds = (size_t)(g_listener_count + g_conn_count) + 1;
        if (max_nfds > g_poll_cap) {
            size_t new_cap = g_poll_cap ? g_poll_cap * 2 : 16;
            while (new_cap < max_nfds) new_cap *= 2;
            FestinaPollFd *grown_fds = realloc(g_poll_fds, new_cap * sizeof(FestinaPollFd));
            if (!grown_fds) festina_fail("out of memory growing the http loop's poll set");
            g_poll_fds = grown_fds;
            int64_t *grown_ids = realloc(g_poll_conn_ids, new_cap * sizeof(int64_t));
            if (!grown_ids) festina_fail("out of memory growing the http loop's poll set");
            g_poll_conn_ids = grown_ids;
            g_poll_cap = new_cap;
        }

        size_t fdi = 0;
        for (int64_t i = 0; i < g_listener_count; i++) {
            g_poll_fds[fdi].fd = g_listeners[i].fd;
            g_poll_fds[fdi].events = POLLIN;
            fdi++;
        }
        /* index -> conn_id, so a poll-ready slot can be matched back to
         * its connection even if the table was compacted between here
         * and the dispatch below (it isn't, within one pass, but the
         * indirection costs nothing and stays correct either way). */
        for (int64_t i = 0; i < g_conn_count; i++) {
            if (!g_conns[i].alive) continue;
            g_poll_fds[fdi].fd = g_conns[i].fd;
            /* claude.md #160: a connection whose TLS handshake last
             * stalled wanting to WRITE (a full TCP send buffer, rare
             * but possible even for small handshake flights) also
             * needs POLLOUT, or it could wait forever on a POLLIN that
             * may never come -- see FestinaConn's own tls_wants_write
             * doc comment. */
            g_poll_fds[fdi].events = (short)(POLLIN | (g_conns[i].tls_wants_write ? POLLOUT : 0));
            g_poll_conn_ids[fdi - (size_t)g_listener_count] = g_conns[i].conn_id;
            fdi++;
        }
        size_t nfds = fdi;

        /* claude.md #165: an outstanding blob/img/aud background load
         * (a SEPARATE pool from this file's own g_async_outstanding,
         * which is http-specific) also has to keep this loop alive --
         * see festina_runtime.h's own doc comment on the shared hook
         * seam for why a program using ONLY http features could still
         * have this kind of work outstanding (e.g. `on request`
         * loading a file in the background while also serving http). */
        int64_t async_io_outstanding = festina_async_io_outstanding();
        if (nfds == 0 && festina_next_timer_deadline() < 0.0
                && g_async_outstanding == 0 && async_io_outstanding == 0) {
            return; /* nothing left to wait for at all */
        }

        /* claude.md #163: the async wake-pipe fd, appended ONE SLOT
         * PAST nfds -- deliberately outside the [0, nfds) range the
         * listener/connection loops below iterate, so neither of them
         * needs to know it exists at all. Only added once the pool has
         * actually been spawned (g_async_pool_started) -- resting at
         * program start, exactly like every other feature here that's
         * only linked, not necessarily used. */
        size_t poll_nfds = nfds;
        if (g_async_pool_started) {
            g_poll_fds[nfds].fd = g_async_wake_fds[0];
            g_poll_fds[nfds].events = POLLIN;
            poll_nfds = nfds + 1;
        }

        double deadline = festina_next_timer_deadline();
        /* claude.md #161: while draining, the grace-period deadline
         * ALSO bounds how long poll() may block -- otherwise, with no
         * timer active and every open connection sitting idle (a
         * WebSocket that never sends anything else), poll() would
         * block with timeout_ms == -1 (forever) and this loop would
         * never come back around to re-check
         * festina_now_seconds() >= g_http_drain_deadline at all,
         * silently defeating the whole grace period -- confirmed
         * directly (a stuck WebSocket connection genuinely hung this
         * loop past its 10-second deadline, only ending because the
         * TEST's own client eventually closed the socket on its own,
         * not because of anything this loop did) before this fix. */
        if (g_http_draining && (deadline < 0.0 || g_http_drain_deadline < deadline)) {
            deadline = g_http_drain_deadline;
        }
        /* claude.md #167: same bounding trick, for the same reason --
         * an idle keep-alive connection's own reap deadline needs to
         * wake this loop up promptly, not whenever the next unrelated
         * event happens to. */
        double keepalive_deadline = festina_earliest_keepalive_deadline();
        if (keepalive_deadline >= 0.0 && (deadline < 0.0 || keepalive_deadline < deadline)) {
            deadline = keepalive_deadline;
        }
        int timeout_ms = -1;
        if (deadline >= 0.0) {
            double remaining = deadline - festina_now_seconds();
            timeout_ms = remaining > 0.0 ? (int)(remaining * 1000.0) + 1 : 0;
        }
        /* claude.md #165: this loop has no fd of its own for the
         * generic async-io pool (a separate pool from THIS file's own
         * http-specific one, which DOES have a wake-pipe fd already in
         * the poll set above) -- bounding the timeout is the only way
         * to notice a completed background blob/img/aud load promptly.
         * Same 20ms granularity festina_run_timer_loop uses. */
        if (async_io_outstanding > 0 && (timeout_ms < 0 || timeout_ms > 20)) {
            timeout_ms = 20;
        }

        int rc = festina_poll(g_poll_fds, poll_nfds, timeout_ms);
        if (rc < 0 && !festina_socket_was_interrupted()) return;

        if (rc > 0) {
            for (int64_t i = 0; i < g_listener_count; i++) {
                if (g_poll_fds[i].revents & (POLLIN | POLLHUP | POLLERR)) {
                    festina_accept_new_connections(g_poll_fds[i].fd, g_listeners[i].port,
                                                   g_listeners[i].tls_config);
                }
            }
            for (size_t i = (size_t)g_listener_count; i < nfds; i++) {
                /* claude.md #160: POLLOUT included here too -- a
                 * mid-handshake connection that requested it (see the
                 * events-mask construction above) needs to be woken by
                 * it, exactly the same as POLLIN wakes every other
                 * connection's own festina_conn_readable call. */
                if (!(g_poll_fds[i].revents & (POLLIN | POLLOUT | POLLHUP | POLLERR))) continue;
                FestinaConn *c = festina_conn_by_id(g_poll_conn_ids[i - (size_t)g_listener_count]);
                if (c) festina_conn_readable(c);
            }
        }

        festina_fire_expired_timers();
        /* claude.md #163: unconditional, once per iteration, the same
         * placement as festina_fire_expired_timers just above -- cheap
         * to call when nothing has completed (a no-op when the async
         * pool was never started at all). */
        festina_async_drain_completed();
        /* claude.md #165: the generic blob/img/aud pool's own drain --
         * a no-op when festina_runtime_async.c was never linked. */
        festina_async_io_drain();
        /* claude.md #167: reap any keep-alive connection that's been
         * idle too long -- see its own doc comment. Cheap (a linear
         * scan already the same size as the poll-set-building one just
         * above) and a no-op whenever nothing's actually timed out. */
        festina_reap_idle_keepalive_connections();
    }
}

/* claude.md #166: exactly one non-blocking servicing pass over open
 * listeners/connections -- accept anything pending, read/write/dispatch
 * anything ready right now, then drain this file's own http-specific
 * async-request completions (claude.md #163's client-callback pool).
 * Deliberately a SEPARATE, smaller implementation from
 * festina_run_http_loop's own poll loop above rather than a shared/
 * refactored one -- that loop is already fully tested end to end
 * (including graceful shutdown's own drain deadline, which has nothing
 * to do with this one-shot embedded case, and which this function does
 * NOT replicate -- see festina_http_service_ready's own doc comment for
 * what that means for a combined graphics+http program's shutdown
 * behavior); duplicating the poll-set-building/dispatch here avoids
 * risking a regression in it, the same "don't refactor stable, tested
 * code just to save a few lines" call claude.md #165 already made for
 * async_io's own pool vs. http's. Called ONLY through the hook seam in
 * festina_runtime.c/.h, from festina_run_event_loop
 * (festina_runtime_graphics.c) -- never from festina_run_http_loop
 * itself. Timers are NOT fired here and the generic async-io pool is
 * NOT drained here either -- whichever loop calls this already owns
 * both (festina_run_event_loop already calls festina_fire_expired_timers/
 * festina_async_io_drain every iteration on its own). */
static void festina_http_service_once(int timeout_ms) {
    size_t max_nfds = (size_t)(g_listener_count + g_conn_count) + 1;
    if (max_nfds > g_poll_cap) {
        size_t new_cap = g_poll_cap ? g_poll_cap * 2 : 16;
        while (new_cap < max_nfds) new_cap *= 2;
        FestinaPollFd *grown_fds = realloc(g_poll_fds, new_cap * sizeof(FestinaPollFd));
        if (!grown_fds) festina_fail("out of memory growing the http loop's poll set");
        g_poll_fds = grown_fds;
        int64_t *grown_ids = realloc(g_poll_conn_ids, new_cap * sizeof(int64_t));
        if (!grown_ids) festina_fail("out of memory growing the http loop's poll set");
        g_poll_conn_ids = grown_ids;
        g_poll_cap = new_cap;
    }

    size_t fdi = 0;
    for (int64_t i = 0; i < g_listener_count; i++) {
        g_poll_fds[fdi].fd = g_listeners[i].fd;
        g_poll_fds[fdi].events = POLLIN;
        fdi++;
    }
    for (int64_t i = 0; i < g_conn_count; i++) {
        if (!g_conns[i].alive) continue;
        g_poll_fds[fdi].fd = g_conns[i].fd;
        g_poll_fds[fdi].events = (short)(POLLIN | (g_conns[i].tls_wants_write ? POLLOUT : 0));
        g_poll_conn_ids[fdi - (size_t)g_listener_count] = g_conns[i].conn_id;
        fdi++;
    }
    size_t nfds = fdi;
    size_t poll_nfds = nfds;
    if (g_async_pool_started) {
        g_poll_fds[nfds].fd = g_async_wake_fds[0];
        g_poll_fds[nfds].events = POLLIN;
        poll_nfds = nfds + 1;
    }
    if (poll_nfds == 0) return; /* no listener open and nothing connected */

    int rc = festina_poll(g_poll_fds, poll_nfds, timeout_ms);
    if (rc < 0 && !festina_socket_was_interrupted()) return;
    if (rc > 0) {
        for (int64_t i = 0; i < g_listener_count; i++) {
            if (g_poll_fds[i].revents & (POLLIN | POLLHUP | POLLERR)) {
                festina_accept_new_connections(g_poll_fds[i].fd, g_listeners[i].port,
                                               g_listeners[i].tls_config);
            }
        }
        for (size_t i = (size_t)g_listener_count; i < nfds; i++) {
            if (!(g_poll_fds[i].revents & (POLLIN | POLLOUT | POLLHUP | POLLERR))) continue;
            FestinaConn *c = festina_conn_by_id(g_poll_conn_ids[i - (size_t)g_listener_count]);
            if (c) festina_conn_readable(c);
        }
    }
    festina_async_drain_completed();
    /* claude.md #167: same reap as festina_run_http_loop's own -- see
     * its doc comment. The caller (festina_run_event_loop,
     * festina_runtime_graphics.c) already re-invokes this function on a
     * short bound whenever any connection is alive at all (see
     * festina_http_service_outstanding_impl below), so no separate
     * deadline-bounding is needed here the way festina_run_http_loop's
     * own poll() timeout needs one. */
    festina_reap_idle_keepalive_connections();
}

/* claude.md #166: festina_set_http_service_hooks' outstanding_fn --
 * "is there any http-related reason festina_run_event_loop should keep
 * its own wait short" -- a listener open, a live connection, or a
 * pending background client request (claude.md #163) all count, the
 * same three things festina_run_http_loop's own "nothing left to wait
 * for" check already looks at. */
static int64_t festina_http_service_outstanding_impl(void) {
    return (int64_t)g_listener_count + festina_alive_conn_count() + g_async_outstanding;
}

/* claude.md #166: festina_set_http_service_hooks' ready_fn -- always a
 * ZERO-timeout pass (see festina_http_service_once above): the caller,
 * festina_run_event_loop, already bounds ITS OWN wait to a short
 * interval whenever festina_http_service_outstanding() is nonzero (the
 * same shape it already uses for outstanding async-io work), so by the
 * time this runs there's no reason to also block here -- either
 * something is ready right now (handled immediately) or nothing is
 * (checked again next iteration, at most one bounded wait later).
 *
 * NOTE on graceful shutdown: unlike festina_run_http_loop's own grace-
 * period draining (claude.md #161 -- closing listeners and giving
 * already-open connections up to FESTINA_SHUTDOWN_GRACE_SECONDS to
 * finish before exiting anyway), a combined graphics+http program's
 * shutdown goes through festina_run_event_loop's own path instead: a
 * Ctrl-C/SIGTERM there tears the window down and exits immediately, with
 * no equivalent drain window for an in-flight http connection. A real,
 * documented gap for v1 of this combination (see api.md), not something
 * this function attempts to paper over -- doing so correctly would mean
 * teaching the graphics loop its own version of the same grace-period
 * bookkeeping, a bigger change than "make the combination possible at
 * all" needed to take on in one pass. */
static void festina_http_service_ready_impl(void) {
    festina_http_service_once(0);
}

/* claude.md #166: codegen's own conditional call site (uses_http,
 * mirroring uses_async_io's own festina_register_async_io_hooks() call)
 * -- registers this file's own outstanding/ready functions into the
 * shared hook seam festina_runtime.c declares. Called unconditionally
 * whenever a program uses http at all, whether or not it also uses
 * graphics -- see festina_runtime.h's own doc comment on why that's
 * harmless. */
void festina_register_http_service_hooks(void) {
    festina_set_http_service_hooks(festina_http_service_outstanding_impl,
                                   festina_http_service_ready_impl);
}

/* ---- http -- construction / destruction ----
 *
 * claude.md #162: the ONE place a FestinaHttpValue is actually
 * allocated -- festina_dispatch_request (an inbound request),
 * festina_http_literal_new (codegen's own `http x = {...}` literal
 * construction), and the client response path
 * (festina_http_send_client, further below) all funnel through this. */
static void *festina_http_value_new(const char *url, const char *method, int64_t code,
                                     void *headers /* owned, or NULL for a fresh empty one */,
                                     const uint8_t *body, int64_t body_len, int64_t conn_id,
                                     void (*callback)(void *)) {
    FestinaHttpValue *v = calloc(1, sizeof(*v));
    if (!v) festina_fail("out of memory building an http value");
    v->refcount = 1;
    v->url = festina_text_own(url ? url : "");
    v->method = festina_text_own(method ? method : "");
    v->code = code;
    v->headers = headers ? headers : festina_new_empty_text_map();
    if (body_len > 0) {
        v->body = malloc((size_t)body_len);
        if (!v->body) festina_fail("out of memory building an http value's body");
        memcpy(v->body, body, (size_t)body_len);
        v->body_len = body_len;
    }
    v->conn_id = conn_id;
    v->callback = callback;
    return &v->url;
}

void festina_release_http(void *payload) {
    if (!festina_release_check(payload)) return;
    FestinaHttpValue *v = FESTINA_HTTP_FROM_PAYLOAD(payload);
    free(v->url);
    free(v->method);
    /* claude.md #167: festina_release_text_map, not the generic
     * festina_release_map -- an http value's own headers map is always
     * owned text (festina_build_headers_map/req.headers construction,
     * or a user-built http literal's own map), see that function's own
     * doc comment for the leak this fixes. */
    festina_release_text_map(v->headers);
    free(v->body);
    free(v);
}

/* claude.md #162: codegen's own entry point for `http x = {...}` --
 * every argument already fully evaluated/coerced by the time this is
 * called (url/method default to "" and code to festina_null_int()
 * when the literal doesn't mention that key at all -- see
 * _emit_http_lit in codegen.py). Takes ownership of `headers` (may be
 * NULL, meaning "no headers key in the literal" -- a fresh empty map
 * is made instead) and copies `body`/`body_len` (the caller's own
 * buffer is freed by codegen right after this call returns, the same
 * "copy in, caller frees its own temporary" convention
 * festina_blob_from_bytes already uses). */
void *festina_http_literal_new(const char *url, const char *method, int64_t code,
                               void *headers, const uint8_t *body, int64_t body_len,
                               void (*callback)(void *)) {
    return festina_http_value_new(url, method, code, headers, body, body_len, 0, callback);
}

/* claude.md #163: codegen's own read-back for `.callback` -- a bare
 * function pointer, cast to `void*` for the same reason every OTHER
 * FuncType-typed field read already returns one (types.py's own doc
 * comment: the runtime value behind a FuncType IS a bare function
 * pointer). NULL reads back as NULL -- the generic FuncType-callee
 * dispatch in codegen.py already treats a null callee as... actually
 * calling a null function pointer would crash, so nothing in
 * generated code ever calls `.callback` directly; only THIS runtime's
 * own background dispatch does, after already checking it for NULL. */
void *festina_http_callback(void *payload) {
    return (void *)FESTINA_HTTP_FROM_PAYLOAD(payload)->callback;
}

/* ---- http -- fields ---- */

char *festina_http_url(void *payload) {
    return festina_text_own(FESTINA_HTTP_FROM_PAYLOAD(payload)->url);
}

char *festina_http_method(void *payload) {
    return festina_text_own(FESTINA_HTTP_FROM_PAYLOAD(payload)->method);
}

int64_t festina_http_code(void *payload) {
    return FESTINA_HTTP_FROM_PAYLOAD(payload)->code;
}

void *festina_http_headers(void *payload) {
    /* claude.md #162: the SAME live map every read, retained on the
     * way out -- unlike claude.md #151's own original behavior (a
     * fresh rebuild from the connection's raw header list on every
     * call), headers now live directly in the value itself, so
     * there's nothing left to rebuild; this exactly mirrors
     * festina_socket_state's own "same live value, retained" shape. */
    void *headers = FESTINA_HTTP_FROM_PAYLOAD(payload)->headers;
    festina_retain(headers);
    return headers;
}

/* ---- http -- methods (server side: .ok()/.redirect()/.upgrade()/
 * .send(res) all still need the underlying LIVE connection -- a
 * silent no-op whenever conn_id is 0 or the connection named by it is
 * already gone, the exact same "never crashes on a value that
 * doesn't apply" tolerance festina_conn_from_handle's own stale-
 * handle case already established) ---- */

static FestinaConn *festina_live_conn(void *payload) {
    int64_t conn_id = FESTINA_HTTP_FROM_PAYLOAD(payload)->conn_id;
    return conn_id ? festina_conn_by_id(conn_id) : NULL;
}

/* claude.md #167: the one piece of every server-side response that now
 * depends on this request's own keep-alive decision (festina_try_parse_
 * request, c->keep_alive) -- shared by all three response writers below
 * rather than duplicated three times. Answering the client's own
 * question honestly matters here, not just internally: a client that
 * asked for keep-alive and gets told `Connection: close` back would
 * (correctly, per HTTP/1.1) close its own end too, silently defeating
 * the whole feature for that request even though this server intended
 * to keep it open. */
static void festina_append_connection_header(FestinaSendBuf *buf, FestinaConn *c) {
    if (c->keep_alive) {
        FESTINA_APPEND_LIT(buf, "Connection: keep-alive\r\n\r\n");
    } else {
        FESTINA_APPEND_LIT(buf, "Connection: close\r\n\r\n");
    }
}

void festina_http_ok(void *payload) {
    FestinaConn *c = festina_live_conn(payload);
    if (!c || c->responded) return;
    c->responded = 1;
    char stack_storage[256];
    FestinaSendBuf buf;
    festina_sendbuf_init(&buf, stack_storage, sizeof(stack_storage));
    FESTINA_APPEND_LIT(&buf, "HTTP/1.1 200 Festina\r\nContent-Length: 0\r\n");
    festina_append_connection_header(&buf, c);
    festina_send_all(c, buf.data, buf.len);
    festina_sendbuf_free(&buf);
}

void festina_http_redirect(void *payload, const char *url) {
    FestinaConn *c = festina_live_conn(payload);
    if (!c || c->responded) return;
    c->responded = 1;
    /* claude.md #155: one buffer, one send() -- same reasoning as
     * festina_http_send below. Appending `url` directly (rather than
     * through a fixed-size snprintf stack buffer, the previous shape)
     * also drops an incidental length cap this never needed to have --
     * FestinaSendBuf grows to fit whatever's appended. */
    char stack_storage[256];
    FestinaSendBuf buf;
    festina_sendbuf_init(&buf, stack_storage, sizeof(stack_storage));
    FESTINA_APPEND_LIT(&buf, "HTTP/1.1 302 Found\r\nLocation: ");
    if (url) festina_sendbuf_append(&buf, url, strlen(url));
    FESTINA_APPEND_LIT(&buf, "\r\nContent-Length: 0\r\n");
    festina_append_connection_header(&buf, c);
    festina_send_all(c, buf.data, buf.len);
    festina_sendbuf_free(&buf);
}

void festina_http_upgrade(void *payload) {
    FestinaConn *c = festina_live_conn(payload);
    if (!c || c->responded) return;
    const char *key = festina_headers_get(c->headers, c->header_count, "sec-websocket-key");
    const char *upgrade_hdr = festina_headers_get(c->headers, c->header_count, "upgrade");
    if (!key || !upgrade_hdr || strcasecmp(upgrade_hdr, "websocket") != 0) {
        return; /* not a real websocket handshake -- silent no-op, see festina_runtime.h */
    }
    char *accept_key = festina_ws_accept_key(key);
    char response[512];
    int len = snprintf(response, sizeof(response),
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Accept: %s\r\n\r\n", accept_key);
    free(accept_key);
    if (len > 0) festina_send_all(c, response, (size_t)len);
    c->responded = 1;
    c->mode = FESTINA_CONN_WEBSOCKET;
    /* Any request bytes buffered past the header/body this request
     * already consumed belong to the FIRST websocket frame, not a new
     * HTTP request -- but since a websocket handshake request has no
     * body (content_length is always 0 for one in practice), buf
     * already holds nothing but consumed bytes; reset it clean. */
    c->buf_len = 0;
}

void *festina_http_to_blob(void *payload) {
    FestinaHttpValue *v = FESTINA_HTTP_FROM_PAYLOAD(payload);
    return festina_blob_from_bytes(v->body ? v->body : (const uint8_t *)"", v->body_len);
}

void *festina_http_to_img(void *payload) {
    FestinaHttpValue *v = FESTINA_HTTP_FROM_PAYLOAD(payload);
    if (!v->body) return NULL;
    /* claude.md #151: through festina_decode_image_bytes, NOT
     * festina_image_from_bytes directly -- that symbol lives in the
     * graphics translation unit, which this file must not reference
     * unconditionally (a plain http-only program that never calls
     * .toImg() would otherwise be forced to link Cairo/X11/libjpeg
     * just to satisfy this one reference -- see
     * festina_decode_image_bytes's own comment in festina_runtime.c). */
    return festina_decode_image_bytes(v->body, v->body_len, "http-body");
}

void *festina_http_to_aud(void *payload) {
    FestinaHttpValue *v = FESTINA_HTTP_FROM_PAYLOAD(payload);
    if (!v->body) return NULL;
    return festina_decode_audio_bytes(v->body, v->body_len, "http-body");
}

char *festina_http_to_text(void *payload) {
    FestinaHttpValue *v = FESTINA_HTTP_FROM_PAYLOAD(payload);
    if (!v->body) return festina_text_own("");
    char *out = malloc((size_t)v->body_len + 1);
    if (!out) festina_fail("out of memory converting an http body to text");
    memcpy(out, v->body, (size_t)v->body_len);
    out[v->body_len] = '\0';
    return out;
}

/* festina_map_for_each's callback has no userdata slot -- fine here
 * since this whole runtime is single-threaded (see festina_runtime.h's
 * own top comment), so a single scratch global for "the FestinaSendBuf
 * extra headers are currently being appended to" is always unambiguous:
 * nothing else can run between festina_http_send setting it and the
 * forEach call finishing on the same thread. claude.md #155: this used
 * to be the fd itself, with each header snprintf'd into its own stack
 * buffer and sent immediately -- now it appends directly into the
 * response's own FestinaSendBuf instead (name, ": ", value, "\r\n"
 * appended as their own pieces, cheaper than routing every header
 * through snprintf when the value is just a plain string), so the
 * whole response -- including any extra headers -- goes out in ONE
 * festina_send_all call. */
/* claude.md #163: __thread, not a plain global -- festina_map_for_each
 * has no user-data parameter to route this through directly, and this
 * scratch variable used to be safe as a plain global purely because
 * only the single main thread ever called festina_http_send_client
 * (the SERVER side, festina_http_send, is still main-thread-only and
 * would have been fine as a plain global forever). Once
 * festina_http_send_client became callable from multiple background
 * worker threads at once, a plain global here became a genuine data
 * race -- confirmed directly by ThreadSanitizer (multiple concurrent
 * worker threads corrupting each other's own header-writing pass)
 * before this fix, clean after it. Each thread's own bracketing
 * assignment (set at the top of the header-writing pass, cleared
 * right after) only ever touches ITS OWN copy. */
static __thread FestinaSendBuf *g_http_send_header_buf = NULL;

static void festina_write_extra_header(int64_t value, const char *key) {
    if (!g_http_send_header_buf) return;
    const char *header_value = (const char *)(intptr_t)value;
    festina_sendbuf_append(g_http_send_header_buf, key, strlen(key));
    FESTINA_APPEND_LIT(g_http_send_header_buf, ": ");
    if (header_value) festina_sendbuf_append(g_http_send_header_buf, header_value, strlen(header_value));
    FESTINA_APPEND_LIT(g_http_send_header_buf, "\r\n");
}

/* claude.md #162: req.send(res:http) -- the server side, unchanged in
 * spirit from before this entry (claude.md #151/#155), just reading
 * code/headers/body off `res_payload` (a constructed http value)
 * instead of three separate arguments. `res_payload` may be NULL
 * (req.send() called with no response value at all makes no sense,
 * but codegen never actually emits that -- ok() has its own dedicated
 * fast path above precisely so 200-empty never needs a throwaway
 * http value built just to describe it) -- defensive, not load-
 * bearing. */
void festina_http_send(void *req_payload, void *res_payload) {
    FestinaConn *c = festina_live_conn(req_payload);
    if (!c || c->responded || !res_payload) return;
    c->responded = 1;
    FestinaHttpValue *res = FESTINA_HTTP_FROM_PAYLOAD(res_payload);
    int64_t code = (res->code == FESTINA_NULL_INT) ? 200 : res->code;

    /* claude.md #155: one buffer, one send() for the whole status
     * line + headers block -- see FestinaSendBuf's own comment for
     * why (TCP_NODELAY means every separate send() used to be its own
     * TCP segment, not just its own syscall). The body is sent as its
     * own second call rather than copied in here -- see the same
     * comment for why that copy isn't worth it. */
    char stack_storage[512];
    FestinaSendBuf buf;
    festina_sendbuf_init(&buf, stack_storage, sizeof(stack_storage));

    char status_line[64];
    int sl_len = snprintf(status_line, sizeof(status_line), "HTTP/1.1 %d Festina\r\n", (int)code);
    if (sl_len > 0) festina_sendbuf_append(&buf, status_line, (size_t)sl_len);

    FestinaMapBlock *block = (FestinaMapBlock *)((char *)res->headers - sizeof(int64_t));
    g_http_send_header_buf = &buf;
    festina_map_for_each(block->count, block->entries, festina_write_extra_header);
    g_http_send_header_buf = NULL;

    char cl_line[64];
    int cl_len = snprintf(cl_line, sizeof(cl_line), "Content-Length: %lld\r\n",
                          (long long)(res->body_len < 0 ? 0 : res->body_len));
    if (cl_len > 0) festina_sendbuf_append(&buf, cl_line, (size_t)cl_len);
    festina_append_connection_header(&buf, c);

    festina_send_all(c, buf.data, buf.len);
    festina_sendbuf_free(&buf);
    if (res->body_len > 0 && res->body) festina_send_all(c, res->body, (size_t)res->body_len);
}

/* ---- http -- client side: the zero-argument req.send() (claude.md
 * #162) -- an OUTBOUND request, built from this value's own url/
 * method/headers/body, with the response overwriting code/headers/
 * body in place afterward (url/method are left alone -- they still
 * describe what was SENT, which stays useful after the fact). Plain
 * HTTP only in this function; TLS is handled by
 * festina_http_send_client itself dispatching to the g_tls_client_*
 * hooks (festina_runtime_https.c) exactly the way the server side
 * dispatches to g_tls_handshake/_recv/_send -- see that file's own
 * comment for why mbedTLS never appears by name in this translation
 * unit at all. ---- */

static void *(*g_tls_client_connect)(int fd, const char *hostname) = NULL;
static long (*g_tls_client_recv)(void *tls_state, void *buf, int64_t cap) = NULL;
static long (*g_tls_client_send)(void *tls_state, const void *data, int64_t len) = NULL;
static void (*g_tls_client_close)(void *tls_state) = NULL;

void festina_set_tls_client_hooks(
        void *(*client_connect)(int, const char *),
        long (*recv_fn)(void *, void *, int64_t),
        long (*send_fn)(void *, const void *, int64_t),
        void (*close_fn)(void *)) {
    g_tls_client_connect = client_connect;
    g_tls_client_recv = recv_fn;
    g_tls_client_send = send_fn;
    g_tls_client_close = close_fn;
}

/* A tiny transport union so the request-building/response-parsing
 * code below (festina_http_send_client itself) never has to branch on
 * TLS-or-not more than once, at connect time -- every subsequent
 * send/recv goes through these two function pointers regardless of
 * which transport is actually underneath, the same "one shared
 * abstraction over plain-socket-or-TLS" shape festina_send_all/
 * festina_conn_readable already established for the SERVER side. */
typedef struct {
    FestinaSocket fd;
    void *tls; /* NULL for plain HTTP */
} FestinaClientTransport;

static long festina_client_send_all(FestinaClientTransport *t, const void *data, size_t len) {
    if (t->tls) {
        const uint8_t *p = (const uint8_t *)data;
        size_t sent = 0;
        while (sent < len) {
            long n = g_tls_client_send(t->tls, p + sent, (int64_t)(len - sent));
            if (n <= 0) return 0;
            sent += (size_t)n;
        }
        return 1;
    }
    const uint8_t *p = (const uint8_t *)data;
    size_t sent = 0;
    while (sent < len) {
        ssize_t n = send(t->fd, (const char *)(p + sent), (int)(len - sent), 0);
        if (n < 0) {
            if (festina_socket_was_interrupted()) continue;
            return 0;
        }
        if (n == 0) return 0;
        sent += (size_t)n;
    }
    return 1;
}

/* Reads until the peer closes (or a hard error), growing `*buf`/`*len`
 * as needed -- the client side has no Content-Length to trust ahead
 * of time the way the request-line parser does (claude.md #151's own
 * scope: no chunked transfer-encoding either direction), so this
 * simply reads everything the server sends and the header parser
 * below finds Content-Length inside it afterward. Bounded by the same
 * FESTINA_HTTP_MAX_BUFFER cap the server side already enforces, for
 * the identical "a hostile or broken peer streaming forever" reason. */
static void festina_client_read_all(FestinaClientTransport *t, uint8_t **buf, size_t *len) {
    size_t cap = 8192;
    *buf = malloc(cap);
    if (!*buf) festina_fail("out of memory reading an http response");
    *len = 0;
    for (;;) {
        if (*len + 4096 > cap) {
            cap *= 2;
            if (cap > FESTINA_HTTP_MAX_BUFFER) cap = FESTINA_HTTP_MAX_BUFFER;
            uint8_t *grown = realloc(*buf, cap);
            if (!grown) festina_fail("out of memory reading an http response");
            *buf = grown;
        }
        if (*len >= (size_t)FESTINA_HTTP_MAX_BUFFER) break;
        long n;
        if (t->tls) {
            n = g_tls_client_recv(t->tls, *buf + *len, (int64_t)(cap - *len));
            if (n == -1) continue; /* would-block on a blocking client socket
                                     * shouldn't happen, but retry rather than
                                     * spin-fail if it ever does */
            if (n <= 0) break;
        } else {
            ssize_t r = recv(t->fd, (char *)(*buf + *len), (int)(cap - *len), 0);
            if (r < 0) {
                if (festina_socket_was_interrupted()) continue;
                break;
            }
            if (r == 0) break;
            n = r;
        }
        *len += (size_t)n;
    }
}

/* Parses "METHOD-less" -- an HTTP RESPONSE -- status line + headers +
 * body out of the raw bytes festina_client_read_all collected, into
 * `out_code`/`out_headers`(a fresh map[text])/`out_body`/
 * `out_body_len`. THROWS (claude.md #157) on a response that doesn't
 * even have a parseable status line -- the one genuinely ambiguous
 * "is this malformed or did the connection just drop" case a client
 * can hit, and worth surfacing with real text rather than silently
 * answering an all-zero response. A missing/short body (fewer bytes
 * than Content-Length claimed, connection closed early) is NOT
 * treated as an error -- whatever arrived is just what the caller
 * gets, the same lenient spirit claude.md #159's own JSON parser
 * applies to shapes that are unusual but not actually broken. */
static void festina_parse_http_response(const uint8_t *data, size_t len,
                                        int64_t *out_code, void **out_headers,
                                        uint8_t **out_body, int64_t *out_body_len) {
    const char *p = (const char *)data;
    const char *end = (const char *)data + len;
    if (len < 12 || memcmp(p, "HTTP/1.", 7) != 0) {
        festina_throw(festina_text_own("fetch: the server's response didn't start with a "
                                       "valid HTTP status line"));
        return; /* unreachable */
    }
    const char *sp1 = memchr(p, ' ', (size_t)(end - p));
    if (!sp1) { festina_throw(festina_text_own("fetch: malformed HTTP status line")); return; }
    *out_code = strtoll(sp1 + 1, NULL, 10);

    const char *hdr_start = memchr(p, '\n', (size_t)(end - p));
    if (!hdr_start) { festina_throw(festina_text_own("fetch: malformed HTTP response (no headers)")); return; }
    hdr_start++;

    void *headers = festina_new_empty_text_map();
    FestinaMapBlock *hblock = (FestinaMapBlock *)((char *)headers - sizeof(int64_t));
    int64_t content_length = -1;
    int is_chunked = 0;
    const char *line = hdr_start;
    while (line < end) {
        const char *line_end = memchr(line, '\n', (size_t)(end - line));
        if (!line_end) line_end = end;
        const char *trimmed_end = line_end;
        if (trimmed_end > line && trimmed_end[-1] == '\r') trimmed_end--;
        if (trimmed_end == line) { line = line_end + 1; break; } /* blank line: end of headers */
        const char *colon = memchr(line, ':', (size_t)(trimmed_end - line));
        if (colon) {
            size_t name_len = (size_t)(colon - line);
            const char *value_start = colon + 1;
            while (value_start < trimmed_end && *value_start == ' ') value_start++;
            char *name = malloc(name_len + 1);
            if (!name) festina_fail("out of memory parsing an http response header");
            for (size_t i = 0; i < name_len; i++) name[i] = (char)tolower((unsigned char)line[i]);
            name[name_len] = '\0';
            size_t value_len = (size_t)(trimmed_end - value_start);
            char *owned_value = malloc(value_len + 1);
            if (!owned_value) festina_fail("out of memory parsing an http response header");
            memcpy(owned_value, value_start, value_len);
            owned_value[value_len] = '\0';
            if (strcmp(name, "content-length") == 0) content_length = strtoll(owned_value, NULL, 10);
            /* claude.md #168: same "chunked wins over Content-Length"
             * precedence as the server-side request parser above. */
            if (strcmp(name, "transfer-encoding") == 0 && strcasecmp(owned_value, "chunked") == 0) {
                is_chunked = 1;
            }
            festina_map_set(&hblock->count, &hblock->entries, name, (int64_t)(intptr_t)owned_value);
            free(name);
        }
        line = line_end + 1;
    }
    *out_headers = headers;

    if (is_chunked) {
        /* claude.md #168: a single pass, not incremental -- every byte
         * the server will ever send has already been read by
         * festina_client_read_all (it reads until the peer closes).
         * A truncated or malformed chunked body is treated the same
         * lenient way a short Content-Length one already is just
         * below: whatever decoded so far is simply the body, not a
         * thrown error -- see festina_chunk_decode_step's own doc
         * comment. */
        size_t consumed = (size_t)(line - (const char *)data);
        uint8_t *decoded = NULL;
        size_t decoded_len = 0, decoded_cap = 0;
        int ok;
        festina_chunk_decode_step(data, len, &consumed, &decoded, &decoded_len, &decoded_cap, &ok);
        *out_body = decoded;
        *out_body_len = (int64_t)decoded_len;
        return;
    }

    size_t have_body = (size_t)(end - line);
    size_t body_len = (content_length >= 0 && (size_t)content_length < have_body)
                       ? (size_t)content_length : have_body;
    if (body_len > 0) {
        *out_body = malloc(body_len);
        if (!*out_body) festina_fail("out of memory reading an http response body");
        memcpy(*out_body, line, body_len);
        *out_body_len = (int64_t)body_len;
    } else {
        *out_body = NULL;
        *out_body_len = 0;
    }
}

void festina_http_send_client(void *payload) {
    FestinaHttpValue *v = FESTINA_HTTP_FROM_PAYLOAD(payload);
    void *url = festina_parse_url(v->url); /* throws on a malformed url -- fine,
                                            * the same catchable failure shape
                                            * every other genuinely-can-fail
                                            * primitive in this runtime uses */
    char *protocol = festina_url_protocol(url);
    char *hostname = festina_url_hostname(url);
    char *pathname = festina_url_pathname(url);
    int64_t port_field = festina_url_port(url);
    int is_tls = strcmp(protocol, "https:") == 0;
    int port = (port_field != FESTINA_NULL_INT) ? (int)port_field : (is_tls ? 443 : 80);

    struct addrinfo hints, *addr_result = NULL;
    memset(&hints, 0, sizeof(hints));
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    char port_str[16];
    snprintf(port_str, sizeof(port_str), "%d", port);
    int gai_rc = getaddrinfo(hostname, port_str, &hints, &addr_result);
    if (gai_rc != 0 || !addr_result) {
        char msg[300];
        snprintf(msg, sizeof(msg), "fetch: could not resolve '%s'", hostname);
        free(protocol); free(hostname); free(pathname);
        festina_release_url(url);
        festina_throw(festina_text_own(msg));
        return; /* unreachable */
    }

    FestinaSocket fd = FESTINA_INVALID_SOCKET;
    for (struct addrinfo *ai = addr_result; ai; ai = ai->ai_next) {
        fd = socket(ai->ai_family, ai->ai_socktype, ai->ai_protocol);
        if (fd == FESTINA_INVALID_SOCKET) continue;
        if (connect(fd, ai->ai_addr, (int)ai->ai_addrlen) == 0) break;
        festina_close_fd(fd);
        fd = FESTINA_INVALID_SOCKET;
    }
    freeaddrinfo(addr_result);
    if (fd == FESTINA_INVALID_SOCKET) {
        char msg[300];
        snprintf(msg, sizeof(msg), "fetch: could not connect to '%s:%d'", hostname, port);
        free(protocol); free(hostname); free(pathname);
        festina_release_url(url);
        festina_throw(festina_text_own(msg));
        return; /* unreachable */
    }
    /* claude.md #162: a blocking client socket, deliberately -- fetch()/
     * req.send() blocks the whole single-threaded program until it
     * completes, the same already-established "a slow on request
     * handler delays every other connection" tradeoff this runtime's
     * own design already accepts (see festina_runtime.h's top
     * comment), extended here to "a slow fetch() blocks everything
     * else too." A finite timeout still matters -- an unresponsive
     * server must not hang the program forever. */
#ifdef _WIN32
    DWORD timeout_ms = 30000;
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, (const char *)&timeout_ms, sizeof(timeout_ms));
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, (const char *)&timeout_ms, sizeof(timeout_ms));
#else
    struct timeval timeout_tv = { 30, 0 };
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout_tv, sizeof(timeout_tv));
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout_tv, sizeof(timeout_tv));
#endif

    FestinaClientTransport transport;
    transport.fd = fd;
    transport.tls = NULL;
    if (is_tls) {
        if (!g_tls_client_connect) {
            festina_close_fd(fd);
            free(protocol); free(hostname); free(pathname);
            festina_release_url(url);
            festina_throw(festina_text_own(
                "fetch: this program was not compiled with TLS support "
                "(this shouldn't happen -- an https:// URL always links it)"));
            return; /* unreachable */
        }
        transport.tls = g_tls_client_connect((int)fd, hostname);
        if (!transport.tls) {
            festina_close_fd(fd);
            char msg[300];
            snprintf(msg, sizeof(msg), "fetch: TLS handshake with '%s' failed", hostname);
            free(protocol); free(hostname); free(pathname);
            festina_release_url(url);
            festina_throw(festina_text_own(msg));
            return; /* unreachable */
        }
    }

    char stack_storage[512];
    FestinaSendBuf buf;
    festina_sendbuf_init(&buf, stack_storage, sizeof(stack_storage));
    festina_sendbuf_append(&buf, v->method, strlen(v->method));
    FESTINA_APPEND_LIT(&buf, " ");
    festina_sendbuf_append(&buf, pathname, strlen(pathname));
    FESTINA_APPEND_LIT(&buf, " HTTP/1.1\r\nHost: ");
    festina_sendbuf_append(&buf, hostname, strlen(hostname));
    FESTINA_APPEND_LIT(&buf, "\r\n");
    FestinaMapBlock *hblock = (FestinaMapBlock *)((char *)v->headers - sizeof(int64_t));
    g_http_send_header_buf = &buf;
    festina_map_for_each(hblock->count, hblock->entries, festina_write_extra_header);
    g_http_send_header_buf = NULL;
    char cl_line[64];
    int cl_len = snprintf(cl_line, sizeof(cl_line), "Content-Length: %lld\r\n",
                          (long long)(v->body_len > 0 ? v->body_len : 0));
    if (cl_len > 0) festina_sendbuf_append(&buf, cl_line, (size_t)cl_len);
    FESTINA_APPEND_LIT(&buf, "Connection: close\r\n\r\n");

    int ok = festina_client_send_all(&transport, buf.data, buf.len);
    if (ok && v->body_len > 0 && v->body) {
        ok = festina_client_send_all(&transport, v->body, (size_t)v->body_len);
    }
    festina_sendbuf_free(&buf);

    if (!ok) {
        if (transport.tls) g_tls_client_close(transport.tls);
        festina_close_fd(fd);
        char msg[300];
        snprintf(msg, sizeof(msg), "fetch: writing the request to '%s' failed", hostname);
        free(protocol); free(hostname); free(pathname);
        festina_release_url(url);
        festina_throw(festina_text_own(msg));
        return; /* unreachable */
    }

    uint8_t *resp_data = NULL;
    size_t resp_len = 0;
    festina_client_read_all(&transport, &resp_data, &resp_len);
    if (transport.tls) g_tls_client_close(transport.tls);
    festina_close_fd(fd);
    free(protocol); free(hostname); free(pathname);
    festina_release_url(url);

    int64_t new_code;
    void *new_headers = NULL;
    uint8_t *new_body = NULL;
    int64_t new_body_len = 0;
    festina_parse_http_response(resp_data, resp_len, &new_code, &new_headers, &new_body, &new_body_len);
    free(resp_data);

    /* claude.md #162: url/method are left alone -- they still
     * describe what was SENT. code/headers/body are overwritten with
     * the response, freeing whatever v held before (the request's own
     * headers/body, no longer needed once the request has actually
     * gone out). */
    v->code = new_code;
    /* claude.md #167: festina_release_text_map, not the generic
     * festina_release_map -- an http value's own headers map is always
     * owned text (festina_build_headers_map/req.headers construction,
     * or a user-built http literal's own map), see that function's own
     * doc comment for the leak this fixes. */
    festina_release_text_map(v->headers);
    v->headers = new_headers;
    free(v->body);
    v->body = new_body;
    v->body_len = new_body_len;
}
#undef FESTINA_APPEND_LIT

/* ---- http -- async client (claude.md #163) ----
 *
 * `req.callback` non-NULL is what makes `req.send()` non-blocking: a
 * small, lazily-spawned pool of worker threads (POSIX only for now --
 * see this file's own top-of-file note on Windows http's own staged
 * rollout; a Windows program simply gets the ordinary blocking
 * behavior regardless of `callback`, a clear documented limitation
 * rather than a silent difference nobody could predict) does the
 * ACTUAL blocking work (reusing festina_http_send_client entirely
 * unchanged -- every connect/TLS/parse detail stays in ONE place),
 * while the callback itself only ever runs on the MAIN thread, from
 * festina_run_http_loop's own per-iteration drain step -- never from
 * a worker thread directly. That split is load-bearing, not a style
 * choice: arbitrary Festina code (the callback body) touches
 * refcounts and globals that are correct ONLY because exactly one
 * thread ever runs generated Festina code at all; running it from a
 * worker would reopen every race this runtime's single-threaded
 * design exists to avoid.
 *
 * A network failure inside festina_http_send_client throws
 * (claude.md #162) -- caught HERE, on the worker's own thread, via a
 * hand-written __builtin_setjmp frame (verified directly, with a
 * standalone two-thread harness, to interoperate correctly with
 * festina_throw's own __builtin_longjmp before this went anywhere
 * near the real runtime -- see festina_runtime.c's own
 * g_festina_catch_top doc comment for why that state had to become
 * __thread-local first) rather than letting it escape across threads.
 * A failed request still fires the callback -- there's no `try` frame
 * left to deliver a throw TO by the time a background result comes
 * back later -- leaving `.code` `null` (explicitly reset here, in
 * case the literal set it to something else, which would otherwise
 * make a failure indistinguishable from a real response) and
 * `.toText()`/`.toBlob()` reading the failure's own message, so
 * `if r.code == null { ... }` inside the callback is how a program
 * tells success from failure. */

#if !defined(_WIN32)

#define FESTINA_HTTP_ASYNC_WORKERS 4

typedef struct FestinaAsyncJob {
    void *payload;   /* the http value, retained (see the dispatch below) */
    struct FestinaAsyncJob *next;
} FestinaAsyncJob;

static pthread_mutex_t g_async_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t g_async_work_cond = PTHREAD_COND_INITIALIZER;
static FestinaAsyncJob *g_async_queue_head = NULL;   /* work waiting for a worker */
static FestinaAsyncJob *g_async_queue_tail = NULL;
static FestinaAsyncJob *g_async_done_head = NULL;    /* finished, awaiting the main thread's drain */
static FestinaAsyncJob *g_async_done_tail = NULL;

static void *festina_async_worker(void *unused) {
    (void)unused;
    for (;;) {
        pthread_mutex_lock(&g_async_lock);
        while (!g_async_queue_head) pthread_cond_wait(&g_async_work_cond, &g_async_lock);
        FestinaAsyncJob *job = g_async_queue_head;
        g_async_queue_head = job->next;
        if (!g_async_queue_head) g_async_queue_tail = NULL;
        pthread_mutex_unlock(&g_async_lock);
        job->next = NULL;

        void *catch_buf[5];
        if (__builtin_setjmp(catch_buf) == 0) {
            festina_try_push(catch_buf);
            festina_http_send_client(job->payload);
            festina_try_pop();
        } else {
            /* festina_throw already popped the frame it's unwinding to
             * (see festina_try_pop's own doc comment) -- nothing left
             * to clean up here beyond collecting the message. */
            char *msg = festina_try_error();
            FestinaHttpValue *v = FESTINA_HTTP_FROM_PAYLOAD(job->payload);
            v->code = FESTINA_NULL_INT;
            free(v->body);
            size_t mlen = strlen(msg);
            v->body = mlen ? malloc(mlen) : NULL;
            if (v->body) { memcpy(v->body, msg, mlen); v->body_len = (int64_t)mlen; }
            else v->body_len = 0;
            free(msg);
        }

        pthread_mutex_lock(&g_async_lock);
        if (g_async_done_tail) g_async_done_tail->next = job; else g_async_done_head = job;
        g_async_done_tail = job;
        pthread_mutex_unlock(&g_async_lock);
        char byte = 1;
        ssize_t rc = write(g_async_wake_fds[1], &byte, 1);
        (void)rc; /* the read side just needs SOMETHING to notice -- a dropped
                   * wake byte under EAGAIN (pipe momentarily full) is harmless,
                   * since festina_async_drain_completed drains the WHOLE done
                   * list on every call regardless of how many wake bytes arrived */
    }
    return NULL; /* unreachable -- worker threads run for the life of the process */
}

/* Spawns the pool and the wake pipe exactly once, on first use -- a
 * program that never uses `callback` never creates a single thread,
 * even though it links this whole file (matching festina_runtime_audio.c's
 * own "the library is linked whenever the FEATURE might be used, the
 * thread only actually spawned once a clip really plays" shape). */
static void festina_async_ensure_pool(void) {
    pthread_mutex_lock(&g_async_lock);
    if (g_async_pool_started) { pthread_mutex_unlock(&g_async_lock); return; }
    g_async_pool_started = 1;
    pthread_mutex_unlock(&g_async_lock);

    if (pipe(g_async_wake_fds) != 0) festina_fail("out of resources starting the async http worker pool");
    int flags = fcntl(g_async_wake_fds[0], F_GETFL, 0);
    fcntl(g_async_wake_fds[0], F_SETFL, flags | O_NONBLOCK);
    for (int i = 0; i < FESTINA_HTTP_ASYNC_WORKERS; i++) {
        pthread_t t;
        if (pthread_create(&t, NULL, festina_async_worker, NULL) != 0) {
            festina_fail("out of resources starting the async http worker pool");
        }
        pthread_detach(t); /* daemon-style -- never joined; the process exiting reclaims them */
    }
}

/* codegen's entry point for the CLIENT form of req.send() (zero
 * arguments) -- replaces the direct festina_http_send_client call
 * that used to be there. The null-vs-non-null check on `callback` has
 * to happen at RUNTIME, not compile time -- codegen has no way to
 * know a variable's own `.callback` field's value in advance, the
 * same reason every other http method already tolerates whatever
 * state the value is actually in at the call site. */
void festina_http_send_client_dispatch(void *payload) {
    FestinaHttpValue *v = FESTINA_HTTP_FROM_PAYLOAD(payload);
    if (!v->callback) {
        festina_http_send_client(payload);
        return;
    }
    festina_async_ensure_pool();
    festina_retain(payload); /* survives after the caller's own scope releases its reference --
                              * balanced by festina_release_http in the drain step below */
    FestinaAsyncJob *job = malloc(sizeof(*job));
    if (!job) festina_fail("out of memory queuing an async http request");
    job->payload = payload;
    job->next = NULL;
    pthread_mutex_lock(&g_async_lock);
    g_async_outstanding++;
    if (g_async_queue_tail) g_async_queue_tail->next = job; else g_async_queue_head = job;
    g_async_queue_tail = job;
    pthread_mutex_unlock(&g_async_lock);
    pthread_cond_signal(&g_async_work_cond);
}

/* Called once per festina_run_http_loop iteration (mirroring
 * festina_fire_expired_timers's own unconditional per-iteration
 * placement) -- runs every completed job's callback on THIS thread
 * (the main thread), the only thread that ever runs generated Festina
 * code. Cheap to call when nothing has completed (two NULL checks
 * under the lock). */
static void festina_async_drain_completed(void) {
    if (!g_async_pool_started) return;
    pthread_mutex_lock(&g_async_lock);
    FestinaAsyncJob *done = g_async_done_head;
    g_async_done_head = g_async_done_tail = NULL;
    pthread_mutex_unlock(&g_async_lock);

    char discard[64];
    while (read(g_async_wake_fds[0], discard, sizeof(discard)) > 0) { } /* drain the pipe --
                                                                          * O_NONBLOCK means this
                                                                          * returns (<=0) once empty
                                                                          * rather than blocking */
    while (done) {
        FestinaAsyncJob *next = done->next;
        FestinaHttpValue *v = FESTINA_HTTP_FROM_PAYLOAD(done->payload);
        void (*callback)(void *) = v->callback;
        if (callback) callback(done->payload);
        festina_release_http(done->payload);
        free(done);
        pthread_mutex_lock(&g_async_lock);
        g_async_outstanding--;
        pthread_mutex_unlock(&g_async_lock);
        done = next;
    }
}

#else /* _WIN32 */

/* claude.md #163: Windows doesn't get the worker pool yet -- same
 * staged rollout every other http feature in this file already has
 * (see the top-of-file note). `callback` is simply never consulted
 * here, so req.send() stays exactly as blocking as it always was on
 * this platform -- a real, documented limitation, not a silent gap:
 * see api.md. */
void festina_http_send_client_dispatch(void *payload) {
    festina_http_send_client(payload);
}

static void festina_async_drain_completed(void) { }

#endif /* _WIN32 */

/* ---- s:socket ---- */

void *festina_socket_state(void *handle) {
    FestinaConn *c = festina_conn_from_handle(handle);
    if (!c) return NULL;
    if (!c->state_map) c->state_map = festina_new_empty_text_map();
    festina_retain(c->state_map);
    return c->state_map;
}

void festina_socket_send_text(void *handle, const char *text) {
    FestinaConn *c = festina_conn_from_handle(handle);
    if (!c || c->mode != FESTINA_CONN_WEBSOCKET) return;
    festina_ws_send_frame(c, 0x1, text, text ? strlen(text) : 0);
}

void festina_socket_send_binary(void *handle, const void *data, int64_t len) {
    FestinaConn *c = festina_conn_from_handle(handle);
    if (!c || c->mode != FESTINA_CONN_WEBSOCKET) return;
    festina_ws_send_frame(c, 0x2, data, len > 0 ? (size_t)len : 0);
}

void festina_socket_close(void *handle) {
    FestinaConn *c = festina_conn_from_handle(handle);
    if (!c) return;
    if (c->mode == FESTINA_CONN_WEBSOCKET) {
        uint8_t close_payload[2] = {0x03, 0xE8}; /* 1000, normal closure */
        festina_ws_send_frame(c, 0x8, close_payload, 2);
    }
    festina_conn_teardown(c);
}
