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
#  include <netinet/in.h>
#  include <netinet/tcp.h>
#  include <arpa/inet.h>
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

static void festina_headers_add(FestinaHeader **headers, int64_t *count,
                                const char *name_start, size_t name_len,
                                const char *value_start, size_t value_len) {
    FestinaHeader *grown = realloc(*headers, (size_t)(*count + 1) * sizeof(FestinaHeader));
    if (!grown) festina_fail("out of memory growing a parsed HTTP header list");
    *headers = grown;
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
    int64_t content_length;   /* -1 until the header is parsed */
    uint8_t *body;             /* NULL until the body is fully buffered */
    int64_t body_len;
    int request_ready;
    int responded;    /* ok/redirect/send/upgrade -- see festina_runtime.h */

    /* socket.state -- lazily created (see festina_socket_state), a
     * live map[text] header block: {refcount, count, entries}, this
     * pointer aimed at the `count` field (see festina_runtime.h's own
     * doc comment on the http/socket handle representation for why). */
    void *state_map;
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
    festina_close_fd(c->fd);
    free(c->buf);
    free(c->method);
    free(c->path);
    festina_headers_free(c->headers, c->header_count);
    free(c->body);
    if (c->state_map) festina_release_map(c->state_map);
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

/* ---- HTTP/1.1 request parsing -- request-line + headers + an
 * optional Content-Length body only (see festina_runtime.h's own top
 * comment for the full scope decision: no chunked encoding, no
 * pipelining, no keep-alive). Re-scans the whole accumulated buffer
 * on every call rather than tracking a resume position -- O(request
 * size) per byte-chunk received, accepted for the same "small,
 * script-shaped server" scope the whole feature already targets. ---- */
static void festina_try_parse_request(FestinaConn *c) {
    if (c->request_ready) return;
    /* claude.md #151: find the header terminator first -- nothing
     * about the request-line or headers can be trusted complete until
     * it's found. */
    uint8_t *hdr_end = NULL;
    for (size_t i = 0; i + 3 < c->buf_len; i++) {
        if (c->buf[i] == '\r' && c->buf[i + 1] == '\n'
                && c->buf[i + 2] == '\r' && c->buf[i + 3] == '\n') {
            hdr_end = c->buf + i;
            break;
        }
    }
    if (!hdr_end) return;
    const char *p = (const char *)c->buf;
    const char *limit = (const char *)hdr_end;

    /* Request line: METHOD SP PATH SP VERSION */
    const char *method_start = p;
    while (p < limit && *p != ' ') p++;
    if (p >= limit) { c->alive = 0; return; } /* malformed -- drop it */
    size_t method_len = (size_t)(p - method_start);
    p++; /* past the space */
    const char *path_start = p;
    while (p < limit && *p != ' ') p++;
    if (p >= limit) { c->alive = 0; return; }
    size_t path_len = (size_t)(p - path_start);
    /* The rest of the line (HTTP version) is read but not kept -- this
     * runtime doesn't distinguish HTTP/1.0 from HTTP/1.1 behavior. */
    while (p < limit && *p != '\r') p++;
    if (p < limit) p++;
    if (p < limit && *p == '\n') p++;

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
        festina_headers_add(&c->headers, &c->header_count,
                            name_start, name_len, value_start, value_len);
    }

    const char *cl = festina_headers_get(c->headers, c->header_count, "content-length");
    c->content_length = cl ? strtoll(cl, NULL, 10) : 0;
    if (c->content_length < 0) c->content_length = 0;

    size_t body_start_offset = (size_t)((const uint8_t *)limit - c->buf) + 4;
    size_t have_body = c->buf_len > body_start_offset ? c->buf_len - body_start_offset : 0;
    if (have_body < (size_t)c->content_length) return; /* still waiting for the body */

    c->body_len = c->content_length;
    if (c->body_len > 0) {
        c->body = malloc((size_t)c->body_len);
        if (!c->body) festina_fail("out of memory buffering an HTTP request body");
        memcpy(c->body, c->buf + body_start_offset, (size_t)c->body_len);
    }
    c->request_ready = 1;
}

/* ---- WebSocket framing (RFC 6455) -- text/binary/close/ping/pong
 * only, no fragmentation, no extensions. See festina_runtime.h's own
 * top comment for the full scope decision. ---- */

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

static int festina_send_all(FestinaSocket fd, const void *data, size_t len) {
    const uint8_t *p = (const uint8_t *)data;
    size_t sent = 0;
    while (sent < len) {
        ssize_t n = send(fd, (const char *)(p + sent), (int)(len - sent), 0);
        if (n < 0) {
            if (festina_socket_was_interrupted()) continue;
            return 0;
        }
        if (n == 0) return 0;
        sent += (size_t)n;
    }
    return 1;
}

/* Writes ONE unmasked server->client frame (server frames are never
 * masked per RFC 6455) -- `opcode` 0x1 text, 0x2 binary, 0x8 close,
 * 0xA pong. */
static void festina_ws_send_frame(FestinaSocket fd, uint8_t opcode, const void *data, size_t len) {
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
    if (!festina_send_all(fd, header, header_len)) return;
    if (len > 0) festina_send_all(fd, data, len);
}

/* Tries to parse and consume ONE complete frame from c->buf. Returns
 * 1 (and shifts the consumed bytes out of the buffer) if a full frame
 * was available, 0 if more bytes are still needed. `*opcode`/`*payload`/
 * `*payload_len` are only meaningful when this returns 1; `*payload`
 * is a freshly malloc'd, already-unmasked copy the caller owns. */
static int festina_ws_try_parse_frame(FestinaConn *c, uint8_t *opcode,
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

    *opcode = fin ? op : 0xFF; /* 0xFF: an unsupported fragmented frame,
                                * see the dispatch site below */
    *payload = out;
    *payload_len = (size_t)len;
    return 1;
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

static void festina_dispatch_request(FestinaConn *c) {
    void *handle = festina_handle_new(c->conn_id);
    if (g_request_handler) g_request_handler(handle);
    /* c may be gone (the peer could theoretically vanish mid-handler
     * via another fd's event, though nothing in THIS handler's own
     * body can close arbitrary other connections) -- re-look-up by id
     * rather than trusting the stale pointer across the callback. */
    FestinaConn *fresh = festina_conn_by_id(c->conn_id);
    festina_release_conn_handle(handle);
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
        /* claude.md #151: nothing in `on request` ever called ok()/
         * redirect()/send()/upgrade() -- rather than leave the client
         * hanging forever, send the same forgiving default every
         * other "the program didn't explicitly handle this" path in
         * this runtime already sends: a plain 200 with an empty body.
         * &fresh->conn_id is a valid handle in its own right here (see
         * festina_conn_from_handle -- a handle IS just a pointer to an
         * int64_t conn_id, with no refcounting done inside
         * festina_http_ok/_send themselves), so this skips the
         * malloc/free a real festina_handle_new() round trip would
         * otherwise cost for a purely-internal call. */
        festina_http_ok(&fresh->conn_id);
    }
    festina_conn_teardown(fresh);
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
        festina_ws_send_frame(c->fd, 0x8, payload, payload_len);
        free(payload);
        festina_conn_teardown(c);
        break;
    case 0x9: /* ping -- answer with a pong carrying the same payload */
        festina_ws_send_frame(c->fd, 0xA, payload, payload_len);
        free(payload);
        break;
    case 0xA: /* pong -- nothing sent this, but tolerate it anyway */
        free(payload);
        break;
    default:
        /* 0x0 (continuation) or 0xFF (a fragmented frame this runtime
         * doesn't reassemble -- see festina_ws_try_parse_frame) --
         * closed as an unsupported-data protocol error (WebSocket
         * close code 1003) rather than silently dropping data a
         * program might be relying on. */
        free(payload);
        {
            uint8_t close_payload[2] = {0x03, 0xEB}; /* 1003, big-endian */
            festina_ws_send_frame(c->fd, 0x8, close_payload, 2);
        }
        festina_conn_teardown(c);
        break;
    }
}

static void festina_conn_readable(FestinaConn *c) {
    for (;;) {
        festina_conn_ensure_capacity(c, 4096);
        if (c->buf_len >= c->buf_cap) { festina_conn_teardown(c); return; }
        ssize_t n = recv(c->fd, (char *)(c->buf + c->buf_len), (int)(c->buf_cap - c->buf_len), 0);
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
        c->buf_len += (size_t)n;
        if (c->buf_len >= (size_t)FESTINA_HTTP_MAX_BUFFER
                && !(c->mode == FESTINA_CONN_READING_REQUEST && c->request_ready)) {
            festina_conn_teardown(c);
            return;
        }
    }
    if (c->mode == FESTINA_CONN_READING_REQUEST) {
        festina_try_parse_request(c);
        if (!c->alive) return; /* malformed request -- already torn down */
        if (c->request_ready) festina_dispatch_request(c);
    } else {
        for (;;) {
            uint8_t opcode;
            uint8_t *payload;
            size_t payload_len;
            if (!festina_ws_try_parse_frame(c, &opcode, &payload, &payload_len)) break;
            festina_dispatch_ws_frame(c, opcode, payload, payload_len);
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

static void festina_accept_new_connections(FestinaSocket listen_fd, int port) {
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
    }
}

void festina_open_port(int64_t port) {
    if (port < 1 || port > 65535) return; /* never fails the program -- see festina_runtime.h */
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
        if (g_listeners[i].port == (int)port) return; /* already open -- silent no-op */
    }
    FestinaSocket fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd == FESTINA_INVALID_SOCKET) return;
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
    if (bind(fd, (struct sockaddr *)&addr, sizeof(addr)) != 0) { festina_close_fd(fd); return; }
    if (listen(fd, 128) != 0) { festina_close_fd(fd); return; }
    festina_set_nonblocking(fd);

    if (g_listener_count == g_listener_capacity) {
        g_listener_capacity = g_listener_capacity ? g_listener_capacity * 2 : 4;
        FestinaListener *grown = realloc(g_listeners, (size_t)g_listener_capacity * sizeof(FestinaListener));
        if (!grown) festina_fail("out of memory growing the listener table");
        g_listeners = grown;
    }
    g_listeners[g_listener_count].fd = fd;
    g_listeners[g_listener_count].port = (int)port;
    g_listener_count++;
}

void festina_close_port(int64_t port) {
    for (int64_t i = 0; i < g_listener_count; i++) {
        if (g_listeners[i].port == (int)port) {
            festina_close_fd(g_listeners[i].fd);
            g_listeners[i] = g_listeners[g_listener_count - 1];
            g_listener_count--;
            return;
        }
    }
}

/* ---- the combined loop -- HTTP/WebSocket I/O + timers ---- */

void festina_run_http_loop(void) {
    for (;;) {
        int64_t live_conns = 0;
        for (int64_t i = 0; i < g_conn_count; i++) if (g_conns[i].alive) live_conns++;
        if (g_listener_count == 0 && live_conns == 0 && festina_next_timer_deadline() < 0.0) {
            return; /* nothing left to wait for at all */
        }

        size_t nfds = (size_t)(g_listener_count + live_conns);
        FestinaPollFd *fds = malloc((nfds > 0 ? nfds : 1) * sizeof(FestinaPollFd));
        if (!fds) festina_fail("out of memory building the http loop's poll set");
        size_t fdi = 0;
        for (int64_t i = 0; i < g_listener_count; i++) {
            fds[fdi].fd = g_listeners[i].fd;
            fds[fdi].events = POLLIN;
            fdi++;
        }
        /* index -> conn_id, so a poll-ready slot can be matched back to
         * its connection even if the table was compacted between here
         * and the dispatch below (it isn't, within one pass, but the
         * indirection costs nothing and stays correct either way). */
        int64_t *fd_conn_ids = malloc((live_conns > 0 ? (size_t)live_conns : 1) * sizeof(int64_t));
        if (!fd_conn_ids) festina_fail("out of memory building the http loop's poll set");
        for (int64_t i = 0; i < g_conn_count; i++) {
            if (!g_conns[i].alive) continue;
            fds[fdi].fd = g_conns[i].fd;
            fds[fdi].events = POLLIN;
            fd_conn_ids[fdi - (size_t)g_listener_count] = g_conns[i].conn_id;
            fdi++;
        }

        double deadline = festina_next_timer_deadline();
        int timeout_ms = -1;
        if (deadline >= 0.0) {
            double remaining = deadline - festina_now_seconds();
            timeout_ms = remaining > 0.0 ? (int)(remaining * 1000.0) + 1 : 0;
        }

        int rc = festina_poll(fds, nfds, timeout_ms);
        if (rc < 0 && !festina_socket_was_interrupted()) { free(fds); free(fd_conn_ids); return; }

        if (rc > 0) {
            for (int64_t i = 0; i < g_listener_count; i++) {
                if (fds[i].revents & (POLLIN | POLLHUP | POLLERR)) {
                    festina_accept_new_connections(fds[i].fd, g_listeners[i].port);
                }
            }
            for (size_t i = (size_t)g_listener_count; i < nfds; i++) {
                if (!(fds[i].revents & (POLLIN | POLLHUP | POLLERR))) continue;
                FestinaConn *c = festina_conn_by_id(fd_conn_ids[i - (size_t)g_listener_count]);
                if (c) festina_conn_readable(c);
            }
        }
        free(fds);
        free(fd_conn_ids);

        festina_fire_expired_timers();
    }
}

/* ---- req:http -- fields ---- */

int64_t festina_http_port(void *handle) {
    FestinaConn *c = festina_conn_from_handle(handle);
    return c ? (int64_t)c->listen_port : 0;
}

char *festina_http_method(void *handle) {
    FestinaConn *c = festina_conn_from_handle(handle);
    return festina_text_own(c && c->method ? c->method : "");
}

char *festina_http_path(void *handle) {
    FestinaConn *c = festina_conn_from_handle(handle);
    return festina_text_own(c && c->path ? c->path : "");
}

void *festina_http_headers(void *handle) {
    void *map = festina_new_empty_text_map();
    FestinaConn *c = festina_conn_from_handle(handle);
    if (!c) return map;
    FestinaMapBlock *block = (FestinaMapBlock *)((char *)map - sizeof(int64_t));
    for (int64_t i = 0; i < c->header_count; i++) {
        char *owned_value = festina_text_own(c->headers[i].value);
        festina_map_set(&block->count, &block->entries, c->headers[i].name,
                        (int64_t)(intptr_t)owned_value);
    }
    return map;
}

/* ---- req:http -- methods ---- */

void festina_http_ok(void *handle) {
    festina_http_send(handle, NULL, 0, 200, NULL);
}

void festina_http_redirect(void *handle, const char *url) {
    FestinaConn *c = festina_conn_from_handle(handle);
    if (!c || c->responded) return;
    c->responded = 1;
    const char *status_line = "HTTP/1.1 302 Found\r\n";
    festina_send_all(c->fd, status_line, strlen(status_line));
    char loc[1024];
    int loc_len = snprintf(loc, sizeof(loc), "Location: %s\r\n", url ? url : "");
    if (loc_len > 0) festina_send_all(c->fd, loc, (size_t)loc_len);
    const char *tail = "Content-Length: 0\r\nConnection: close\r\n\r\n";
    festina_send_all(c->fd, tail, strlen(tail));
}

void festina_http_upgrade(void *handle) {
    FestinaConn *c = festina_conn_from_handle(handle);
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
    if (len > 0) festina_send_all(c->fd, response, (size_t)len);
    c->responded = 1;
    c->mode = FESTINA_CONN_WEBSOCKET;
    /* Any request bytes buffered past the header/body this request
     * already consumed belong to the FIRST websocket frame, not a new
     * HTTP request -- but since a websocket handshake request has no
     * body (content_length is always 0 for one in practice), buf
     * already holds nothing but consumed bytes; reset it clean. */
    c->buf_len = 0;
}

void *festina_http_to_blob(void *handle) {
    FestinaConn *c = festina_conn_from_handle(handle);
    if (!c) return festina_blob_from_bytes("", 0);
    return festina_blob_from_bytes(c->body ? c->body : (const uint8_t *)"", c->body_len);
}

void *festina_http_to_img(void *handle) {
    FestinaConn *c = festina_conn_from_handle(handle);
    if (!c || !c->body) return NULL;
    /* claude.md #151: through festina_decode_image_bytes, NOT
     * festina_image_from_bytes directly -- that symbol lives in the
     * graphics translation unit, which this file must not reference
     * unconditionally (a plain http-only program that never calls
     * .toImg() would otherwise be forced to link Cairo/X11/libjpeg
     * just to satisfy this one reference -- see
     * festina_decode_image_bytes's own comment in festina_runtime.c). */
    return festina_decode_image_bytes(c->body, c->body_len, "http-request-body");
}

void *festina_http_to_aud(void *handle) {
    FestinaConn *c = festina_conn_from_handle(handle);
    if (!c || !c->body) return NULL;
    return festina_decode_audio_bytes(c->body, c->body_len, "http-request-body");
}

char *festina_http_to_text(void *handle) {
    FestinaConn *c = festina_conn_from_handle(handle);
    if (!c || !c->body) return festina_text_own("");
    char *out = malloc((size_t)c->body_len + 1);
    if (!out) festina_fail("out of memory converting an HTTP request body to text");
    memcpy(out, c->body, (size_t)c->body_len);
    out[c->body_len] = '\0';
    return out;
}

/* festina_map_for_each's callback has no userdata slot -- fine here
 * since this whole runtime is single-threaded (see festina_runtime.h's
 * own top comment), so a single scratch global for "the fd the
 * headers currently being written belong to" is always unambiguous:
 * nothing else can run between festina_http_send setting it and the
 * forEach call finishing on the same thread. */
static FestinaSocket g_http_send_extra_headers_fd = FESTINA_INVALID_SOCKET;

static void festina_write_extra_header(int64_t value, const char *key) {
    if (g_http_send_extra_headers_fd == FESTINA_INVALID_SOCKET) return;
    const char *header_value = (const char *)(intptr_t)value;
    char line[1024];
    int n = snprintf(line, sizeof(line), "%s: %s\r\n", key, header_value ? header_value : "");
    if (n > 0) festina_send_all(g_http_send_extra_headers_fd, line, (size_t)n);
}

void festina_http_send(void *handle, const void *data, int64_t len,
                       int64_t code, void *extra_headers) {
    FestinaConn *c = festina_conn_from_handle(handle);
    if (!c || c->responded) return;
    c->responded = 1;
    char status_line[64];
    int sl_len = snprintf(status_line, sizeof(status_line), "HTTP/1.1 %d Festina\r\n", (int)code);
    festina_send_all(c->fd, status_line, (size_t)sl_len);
    if (extra_headers) {
        FestinaMapBlock *block = (FestinaMapBlock *)((char *)extra_headers - sizeof(int64_t));
        g_http_send_extra_headers_fd = c->fd;
        festina_map_for_each(block->count, block->entries, festina_write_extra_header);
        g_http_send_extra_headers_fd = FESTINA_INVALID_SOCKET;
    }
    char cl_line[64];
    int cl_len = snprintf(cl_line, sizeof(cl_line), "Content-Length: %lld\r\n",
                          (long long)(len < 0 ? 0 : len));
    festina_send_all(c->fd, cl_line, (size_t)cl_len);
    const char *conn_line = "Connection: close\r\n\r\n";
    festina_send_all(c->fd, conn_line, strlen(conn_line));
    if (len > 0 && data) festina_send_all(c->fd, data, (size_t)len);
}

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
    festina_ws_send_frame(c->fd, 0x1, text, text ? strlen(text) : 0);
}

void festina_socket_send_binary(void *handle, const void *data, int64_t len) {
    FestinaConn *c = festina_conn_from_handle(handle);
    if (!c || c->mode != FESTINA_CONN_WEBSOCKET) return;
    festina_ws_send_frame(c->fd, 0x2, data, len > 0 ? (size_t)len : 0);
}

void festina_socket_close(void *handle) {
    FestinaConn *c = festina_conn_from_handle(handle);
    if (!c) return;
    if (c->mode == FESTINA_CONN_WEBSOCKET) {
        uint8_t close_payload[2] = {0x03, 0xE8}; /* 1000, normal closure */
        festina_ws_send_frame(c->fd, 0x8, close_payload, 2);
    }
    festina_conn_teardown(c);
}
