/*
 * Festina native runtime -- core translation unit: claude.md #41 (log),
 * #42 (fail), #45 (string interpolation), #29-31 (automatic SQLite
 * database + schema sync), #32-34 (sqlite() queries), #67-68 (regex,
 * string match/replace), #69 (setTimeout/setInterval, minus the
 * graphics-aware half of the blocking loop -- see below).
 *
 * This is a from-scratch runtime for the statically typed Festina
 * language.
 *
 * Split into three translation units -- this file (core), plus
 * festina_runtime_graphics.c (Cairo/X11) and festina_runtime_audio.c
 * (ALSA) -- so a compiled program that never uses graphics or audio
 * never needs those object files (and therefore never needs -lcairo/
 * -lX11/-lasound on cc's command line at all) to build or run. See
 * festina_runtime.h's top-of-file note for why this was necessary: a
 * single object file with everything in it defeats even
 * --gc-sections/--as-needed at eliminating an unused shared-library
 * dependency, because the linker's "is -lfoo needed" decision is made
 * against the whole translation unit any live symbol pulls in, before
 * dead-code elimination has pruned anything from it -- verified
 * empirically (readelf -d kept showing libcairo/libX11/libasound as
 * NEEDED even once readelf --dyn-syms showed zero actual undefined
 * references to any of their symbols). Splitting so graphics/audio code
 * lives in object files that are only ever *passed to the linker* when
 * a program actually uses them sidesteps the problem entirely -- see
 * cli.py's per-feature object file selection, driven by
 * CodeGen.uses_graphics/uses_timers/uses_audio in festina/codegen.py.
 *
 * This core file has no dependency beyond libc, sqlite3, and POSIX
 * <regex.h>/<time.h> -- every program links it, since log()/fail()/
 * string interpolation and the database are always potentially in use
 * (same as festina_db_open() only actually being *called* for a program
 * with a `table` declaration, even though the core object file itself is
 * always linked in).
 */
#include <errno.h>
#include <regex.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>       /* clock_gettime/nanosleep -- setTimeout/setInterval */
#include "festina_runtime.h"
#include "festina_runtime_internal.h"

/* ---- log() / fail() -- claude.md #41, #42 ---- */

void festina_log_int(int64_t v) { printf("%lld\n", (long long)v); }
void festina_log_float(double v) { printf("%g\n", v); }
/* claude.md #97: bool's null is the reserved third bit pattern 2 (see
 * codegen's BOOL_NULL_CONST), and printing it with a plain `v ? true :
 * false` rendered it as "true" -- indistinguishable from a genuine
 * true, which made claude.md #96's "popping an empty array gives you
 * null" impossible to actually observe for an arr[bool]. Only the
 * sentinel takes this branch, so no real boolean's output changes. */
void festina_log_bool(int8_t v) {
    printf("%s\n", v == 2 ? "null" : (v ? "true" : "false"));
}
void festina_log_text(const char *v) { printf("%s\n", v ? v : ""); }

void festina_fail(const char *msg) {
    fprintf(stderr, "fail: %s\n", msg ? msg : "");
    exit(1);
}

/* ---- environment variables -- claude.md #71 ---- */

char *festina_getenv(const char *name) {
    /* getenv()'s own return value is either a pointer owned by the
     * process environment (must not be freed, mutated, or held past a
     * later setenv/putenv touching the same name) or NULL if unset --
     * NULL is already exactly Festina's own null-for-text sentinel
     * (claude.md #71: "or null if it is not set"), so this needs no
     * translation at all. Not strdup'd: nothing in this runtime ever
     * frees a text value (see the module docstring's "no GC yet"
     * note), and the process's own environment block outlives every
     * compiled Festina program's own execution regardless, so aliasing
     * it directly is safe here specifically (unlike, say, festina_
     * map_set's key, which *does* copy -- see its own comment on why
     * that one case is different). */
    if (!name) name = "";
    return getenv(name);
}

/* ---- string interpolation -- claude.md #9, #45 ---- */

char *festina_str_from_int(int64_t v) {
    char buf[32];
    snprintf(buf, sizeof(buf), "%lld", (long long)v);
    return strdup(buf);
}

char *festina_str_from_float(double v) {
    char buf[64];
    snprintf(buf, sizeof(buf), "%g", v);
    return strdup(buf);
}

char *festina_str_from_bool(int8_t v) {
    /* claude.md #97: same three-way split festina_log_bool uses -- a
     * bool interpolated into a template has to render its null the
     * same way logging it does, or `${b}` and `log(b)` would disagree
     * about the same value. */
    return strdup(v == 2 ? "null" : (v ? "true" : "false"));
}

/* ---- JSON-ish rendering -- claude.md #114 ----
 *
 * `${someStruct}` and log(someArr) render containers as JSON-like text.
 * The structure walking lives in generated IR (only the compiler knows
 * a struct's layout); everything that touches BYTES lives here, as a
 * small growable string builder plus append helpers that know the
 * null sentinels. One builder per rendering, O(n) overall -- repeated
 * festina_str_concat would have been O(n^2) and this is exactly the
 * feature people reach for in a loop. */
static int64_t festina_null_int(void);   /* defined with the sqlite helpers below */

typedef struct {
    char *data;
    size_t len;
    size_t cap;
} FestinaSB;

void *festina_sb_new(void) {
    FestinaSB *sb = malloc(sizeof(FestinaSB));
    if (!sb) festina_fail("out of memory rendering a value");
    sb->cap = 64;
    sb->len = 0;
    sb->data = malloc(sb->cap);
    if (!sb->data) festina_fail("out of memory rendering a value");
    sb->data[0] = '\0';
    return sb;
}

static void festina_sb_grow(FestinaSB *sb, size_t add) {
    if (sb->len + add + 1 <= sb->cap) return;
    while (sb->cap < sb->len + add + 1) sb->cap *= 2;
    char *grown = realloc(sb->data, sb->cap);
    if (!grown) festina_fail("out of memory rendering a value");
    sb->data = grown;
}

void festina_sb_append(void *sbv, const char *s) {
    if (!s) return;
    FestinaSB *sb = (FestinaSB *)sbv;
    size_t n = strlen(s);
    festina_sb_grow(sb, n);
    memcpy(sb->data + sb->len, s, n + 1);
    sb->len += n;
}

/* A JSON string: quoted, with the escapes JSON requires. A NULL text
 * renders as the JSON null literal, unquoted -- the same three-way
 * honesty festina_str_from_bool already applies. */
void festina_sb_append_json_text(void *sbv, const char *s) {
    FestinaSB *sb = (FestinaSB *)sbv;
    if (!s) { festina_sb_append(sb, "null"); return; }
    festina_sb_grow(sb, 2);
    sb->data[sb->len++] = '"';
    for (const unsigned char *p = (const unsigned char *)s; *p; p++) {
        char esc[8];
        const char *out = esc;
        switch (*p) {
        case '"':  out = "\\\""; break;
        case '\\': out = "\\\\"; break;
        case '\n': out = "\\n"; break;
        case '\r': out = "\\r"; break;
        case '\t': out = "\\t"; break;
        default:
            if (*p < 0x20) { snprintf(esc, sizeof(esc), "\\u%04x", *p); }
            else { esc[0] = (char)*p; esc[1] = '\0'; }
        }
        size_t n = strlen(out);
        festina_sb_grow(sb, n);
        memcpy(sb->data + sb->len, out, n);
        sb->len += n;
    }
    festina_sb_grow(sb, 1);
    sb->data[sb->len++] = '"';
    sb->data[sb->len] = '\0';
}

void festina_sb_append_json_int(void *sbv, int64_t v) {
    if (v == festina_null_int()) { festina_sb_append(sbv, "null"); return; }
    char buf[32];
    snprintf(buf, sizeof(buf), "%lld", (long long)v);
    festina_sb_append(sbv, buf);
}

void festina_sb_append_json_float(void *sbv, double v) {
    /* JSON has no NaN/Infinity, and Festina's float null IS a NaN --
     * both render as null, which is also what JSON.stringify does. */
    if (v != v || v > 1.7e308 || v < -1.7e308) {
        festina_sb_append(sbv, "null");
        return;
    }
    char buf[64];
    snprintf(buf, sizeof(buf), "%g", v);
    festina_sb_append(sbv, buf);
}

void festina_sb_append_json_bool(void *sbv, int8_t v) {
    festina_sb_append(sbv, v == 2 ? "null" : (v ? "true" : "false"));
}

/* A bool that lives in an 8-byte slot (a table row's column), where
 * null is the INT sentinel rather than 2. */
void festina_sb_append_json_bool64(void *sbv, int64_t v) {
    if (v == festina_null_int()) { festina_sb_append(sbv, "null"); return; }
    festina_sb_append(sbv, v ? "true" : "false");
}

/* An opaque handle (blob/img/aud/regex) inside a rendered container:
 * `null` when null, a placeholder naming the type otherwise -- its
 * bytes have no honest JSON form, and silently dumping binary into a
 * debug string would be worse than saying what it is. */
void festina_sb_append_handle(void *sbv, const void *handle, const char *label) {
    if (!handle) { festina_sb_append(sbv, "null"); return; }
    festina_sb_append(sbv, label);
}

char *festina_sb_finish(void *sbv) {
    FestinaSB *sb = (FestinaSB *)sbv;
    char *out = sb->data;
    free(sb);
    return out;
}

/* ---- split and join -- claude.md #116 ----
 *
 * `sentence.split(sep)` -> arr[text], with sep either a text or a
 * regex; `words.join(sep)` -> text. JS semantics throughout, because
 * that is the dialect every other string operation here speaks:
 * empty pieces between adjacent separators are KEPT ('a,,b' -> three
 * pieces), a separator at the edge yields an edge empty, an
 * empty-match regex splits between characters without looping forever,
 * and join renders a null element as an empty string.
 *
 * The result array is built right here: the same {refcount | len, data}
 * layout every arr[text] has (festina_sqlite_collect_rows already
 * builds compatible arrays), refcount starting at 1, each piece an
 * owned buffer -- so scope exit, aliasing, free and element release
 * all apply to a split result with nothing new. */

typedef struct {
    char **items;
    int64_t count;
    int64_t cap;
} FestinaPieces;

static void festina_pieces_push(FestinaPieces *p, const char *start, size_t len) {
    if (p->count == p->cap) {
        p->cap = p->cap ? p->cap * 2 : 8;
        char **grown = realloc(p->items, (size_t)p->cap * sizeof(char *));
        if (!grown) festina_fail("out of memory in split()");
        p->items = grown;
    }
    char *piece = malloc(len + 1);
    if (!piece) festina_fail("out of memory in split()");
    memcpy(piece, start, len);
    piece[len] = '\0';
    p->items[p->count++] = piece;
}

/* Wraps the collected pieces in a fresh refcounted arr[text]. */
static void *festina_pieces_finish(FestinaPieces *p) {
    char *raw = malloc(8 + 16);
    if (!raw) festina_fail("out of memory in split()");
    *(int64_t *)raw = 1;                       /* refcount */
    int64_t *header = (int64_t *)(raw + 8);
    header[0] = p->count;                      /* length */
    memcpy(&header[1], &p->items, sizeof(char **));
    return header;
}

void *festina_text_split(const char *s, const char *sep) {
    FestinaPieces p = {NULL, 0, 0};
    if (!s) s = "";
    if (!sep) {
        /* No separator to split on: the whole string, one piece --
         * what JS's no-argument split does. */
        festina_pieces_push(&p, s, strlen(s));
        return festina_pieces_finish(&p);
    }
    if (!*sep) {
        /* Empty separator: split between characters -- UTF-8 CODE
         * POINTS, not bytes, or every non-ASCII string would shatter
         * into invalid fragments. A continuation byte is 10xxxxxx. */
        const char *c = s;
        while (*c) {
            const char *start = c;
            c++;
            while ((*c & 0xC0) == 0x80) c++;
            festina_pieces_push(&p, start, (size_t)(c - start));
        }
        if (p.count == 0) festina_pieces_push(&p, s, 0);
        return festina_pieces_finish(&p);
    }
    size_t sep_len = strlen(sep);
    const char *cursor = s;
    for (;;) {
        const char *found = strstr(cursor, sep);
        if (!found) {
            festina_pieces_push(&p, cursor, strlen(cursor));
            break;
        }
        festina_pieces_push(&p, cursor, (size_t)(found - cursor));
        cursor = found + sep_len;
    }
    return festina_pieces_finish(&p);
}

/* `kind` names the element type ("text"/"int"/"float"/"bool") -- codegen
 * passes it as a constant, since this runtime cannot know an arr[T]'s T.
 * A null element joins as an empty string, exactly JS's choice. */
char *festina_arr_join(void *arr, const char *sep, const char *kind) {
    if (!sep) sep = "";
    void *sb = festina_sb_new();
    if (arr) {
        int64_t *header = (int64_t *)arr;
        int64_t n = header[0];
        char *data;
        memcpy(&data, &header[1], sizeof(char *));
        int is_text = strcmp(kind, "text") == 0;
        int is_int = strcmp(kind, "int") == 0;
        int is_float = strcmp(kind, "float") == 0;
        for (int64_t i = 0; i < n; i++) {
            if (i > 0) festina_sb_append(sb, sep);
            if (is_text) {
                char *v = ((char **)data)[i];
                if (v) festina_sb_append(sb, v);
            } else if (is_int) {
                int64_t v = ((int64_t *)data)[i];
                if (v != festina_null_int()) {
                    char buf[32];
                    snprintf(buf, sizeof(buf), "%lld", (long long)v);
                    festina_sb_append(sb, buf);
                }
            } else if (is_float) {
                double v = ((double *)data)[i];
                if (v == v) {   /* NaN is Festina's float null */
                    char buf[64];
                    snprintf(buf, sizeof(buf), "%g", v);
                    festina_sb_append(sb, buf);
                }
            } else {            /* bool: one byte per element, 2 = null */
                int8_t v = ((int8_t *)data)[i];
                if (v != 2) festina_sb_append(sb, v ? "true" : "false");
            }
        }
    }
    return festina_sb_finish(sb);
}

char *festina_str_concat(const char *a, const char *b) {
    if (!a) a = "";
    if (!b) b = "";
    size_t la = strlen(a), lb = strlen(b);
    char *out = malloc(la + lb + 1);
    if (!out) festina_fail("out of memory in festina_str_concat");
    memcpy(out, a, la);
    memcpy(out + la, b, lb + 1);
    return out;
}

/* claude.md #83: text values are reference-managed by copying, not
 * counting -- every text-typed binding (local, global, struct field,
 * array element, map value) always holds either NULL or a fresh,
 * EXCLUSIVELY-owned heap buffer, never a raw alias into a string
 * literal constant or another binding's own buffer. codegen calls this
 * whenever a text-typed binding's own new value comes from a source
 * that might already be referenced elsewhere (an existing identifier,
 * a field/element read, a ternary, ...) -- the same "aliasing, needs
 * its own copy" classification claude.md #77 already established for
 * struct/arr[T]/map[T], just implemented with `strdup` instead of a
 * refcount increment, since text has no shared/refcounted
 * representation to increment in the first place. A source that's
 * already known-fresh (a call result, a template literal's own
 * concatenation) skips this and is taken directly -- see
 * _is_owning_text_source's own comment. NULL-safe, matching every
 * other NULL-tolerant helper in this file (a text value's zero value
 * is a plain NULL pointer, the same as struct/arr[T]/map[T]'s own). */
char *festina_text_own(const char *s) {
    if (!s) return NULL;
    char *out = strdup(s);
    if (!out) festina_fail("out of memory in festina_text_own");
    return out;
}

/* ---- claude.md #93: math, files and time ----
 *
 * Everything here is libc or libm, both already on every link line
 * (see cli.py's own link_libs), so none of it costs a new dependency --
 * claude.md #59's minimal-dependency principle applied to the other
 * direction: use what is already there before reaching for anything.
 */

/* Math.random() -- seeded once, lazily, from the clock. Deliberately
 * plain rand(): this is for gameplay and sampling, not cryptography,
 * and claiming otherwise by reaching for a CSPRNG would be worse than
 * being clear about it (see api.md). */
double festina_random(void) {
    static int seeded = 0;
    if (!seeded) {
        struct timespec ts;
        clock_gettime(CLOCK_REALTIME, &ts);
        srand((unsigned int)(ts.tv_sec ^ ts.tv_nsec));
        seeded = 1;
    }
    /* RAND_MAX + 1.0 keeps this in [0, 1) -- dividing by RAND_MAX would
     * make 1.0 reachable, which every other language's random() excludes
     * and which breaks the common `arr[floor(random() * length)]`. */
    return rand() / (RAND_MAX + 1.0);
}

/* claude.md #93: whole-file text I/O. readFile returns NULL (Festina's
 * null text) for anything it cannot read, rather than failing the
 * program -- a missing file is an ordinary condition a program should
 * be able to test for, the same reasoning claude.md #57 applies to
 * division by zero. */
char *festina_read_file(const char *path) {
    if (!path) return NULL;
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;
    if (fseek(f, 0, SEEK_END) != 0) { fclose(f); return NULL; }
    long size = ftell(f);
    if (size < 0) { fclose(f); return NULL; }
    rewind(f);
    char *buf = malloc((size_t)size + 1);
    if (!buf) { fclose(f); festina_fail("out of memory reading a file"); }
    size_t got = fread(buf, 1, (size_t)size, f);
    fclose(f);
    buf[got] = '\0';
    return buf;
}

static int8_t festina_put_file(const char *path, const char *content, const char *mode) {
    if (!path) return 0;
    if (!content) content = "";
    FILE *f = fopen(path, mode);
    if (!f) return 0;
    size_t len = strlen(content);
    size_t wrote = fwrite(content, 1, len, f);
    /* fclose can fail on a full disk even when every fwrite succeeded,
     * so its result is part of "did this write actually land". */
    int closed = fclose(f);
    return (wrote == len && closed == 0) ? 1 : 0;
}

int8_t festina_write_file(const char *path, const char *content) {
    return festina_put_file(path, content, "wb");
}

int8_t festina_append_file(const char *path, const char *content) {
    return festina_put_file(path, content, "ab");
}

int8_t festina_file_exists(const char *path) {
    if (!path) return 0;
    FILE *f = fopen(path, "rb");
    if (!f) return 0;
    fclose(f);
    return 1;
}

int8_t festina_delete_file(const char *path) {
    if (!path) return 0;
    return remove(path) == 0 ? 1 : 0;
}

/* ---- blob -- claude.md #36, given its real meaning by claude.md #109 ----
 *
 * claude.md #36's only worked example was always `blob data =
 * 'path/to/file'`, but for a long time `blob` was implemented as a
 * second name for `text`: the declaration stored the PATH and nothing
 * ever read the file. #109 makes the example mean what it says. A blob
 * is the file's BYTES, loaded at the declaration, and it keeps the path
 * it came from so it can be written back, appended to, tested for and
 * deleted -- the five things claude.md #93 spelled as free functions
 * taking a path over and over.
 *
 * The shape is deliberately the one `img` and `aud` already have (see
 * claude.md #101): decoded/loaded content plus the bytes it came from,
 * so the same value serves both a program and a SQLite BLOB column and
 * a round trip is byte-identical. `length` is a real byte count, not
 * strlen -- a blob is binary and may contain NUL. The buffer is
 * NUL-terminated anyway, one byte past `length`, so handing it to
 * .toText() and to every C string function this runtime already has
 * costs no copy.
 *
 * Unlike img/aud, a blob is REFERENCE COUNTED, using the same i64
 * header immediately before the payload that structs/arrays/maps use
 * (festina_retain/festina_release_check). `blob a = b` shares one
 * handle rather than re-reading the file, and reassigning a blob
 * releases whatever it held -- so the last reference to a file's
 * contents frees them, and an earlier reference keeps them alive. That
 * is the behavior #109 asked for and it is exactly what the existing
 * refcount protocol provides; nothing new was needed but a destructor
 * that also frees the two inner strings. */
typedef struct {
    char *path;      /* strdup'd; "" for a blob that came from a column */
    char *bytes;     /* always NUL-terminated at [length] */
    int64_t length;  /* real byte count -- binary content may embed NUL */
} FestinaBlob;

/* Reads a whole file and reports its real length. festina_read_file
 * above cannot serve a blob: it hands back a NUL-terminated buffer with
 * the length thrown away, which is fine for text and loses the tail of
 * anything binary. */
static char *festina_read_file_sized(const char *path, int64_t *out_len) {
    *out_len = 0;
    if (!path || !*path) return NULL;
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;
    if (fseek(f, 0, SEEK_END) != 0) { fclose(f); return NULL; }
    long size = ftell(f);
    if (size < 0) { fclose(f); return NULL; }
    rewind(f);
    char *buf = malloc((size_t)size + 1);
    if (!buf) { fclose(f); festina_fail("out of memory reading a file"); }
    size_t got = fread(buf, 1, (size_t)size, f);
    fclose(f);
    buf[got] = '\0';
    *out_len = (int64_t)got;
    return buf;
}

static void *festina_blob_alloc(char *path, char *bytes, int64_t length) {
    char *raw = malloc(sizeof(int64_t) + sizeof(FestinaBlob));
    if (!raw) festina_fail("out of memory allocating a blob");
    *(int64_t *)raw = 1;
    FestinaBlob *b = (FestinaBlob *)(raw + sizeof(int64_t));
    b->path = path;
    b->bytes = bytes;
    b->length = length;
    return b;
}

/* An unreadable path is NOT a failure -- it yields an empty blob whose
 * .exists() is false, matching the rule claude.md #93 set for the file
 * functions this replaces: "nothing here fails the program". A blob
 * declared from a path that does not exist yet is the ordinary way to
 * create a file, since .write() only needs the path. */
void *festina_blob_open(const char *path) {
    if (!path) path = "";
    int64_t len = 0;
    char *bytes = festina_read_file_sized(path, &len);
    if (!bytes) { bytes = strdup(""); len = 0; }
    if (!bytes) festina_fail("out of memory allocating a blob");
    char *copy = strdup(path);
    if (!copy) festina_fail("out of memory allocating a blob");
    return festina_blob_alloc(copy, bytes, len);
}

/* claude.md #109: a blob read back out of a SQLite BLOB column. It has
 * bytes and no path, so .toText() works and .exists()/.write()/
 * .append()/.delete() all answer false -- there is no file to act on,
 * and inventing a temporary one would be worse than saying so. */
void *festina_blob_from_bytes(const void *data, int64_t len) {
    if (len < 0) len = 0;
    char *bytes = malloc((size_t)len + 1);
    if (!bytes) festina_fail("out of memory allocating a blob");
    if (data && len > 0) memcpy(bytes, data, (size_t)len);
    bytes[len] = '\0';
    char *copy = strdup("");
    if (!copy) festina_fail("out of memory allocating a blob");
    return festina_blob_alloc(copy, bytes, len);
}

/* The blob counterpart of the per-struct release wrappers codegen
 * generates (claude.md #78): decrement, and only on the last reference
 * free the two inner strings before the storage itself. */
void festina_blob_release(void *payload) {
    if (!payload) return;
    if (!festina_release_check(payload)) return;
    FestinaBlob *b = (FestinaBlob *)payload;
    free(b->path);
    free(b->bytes);
    free((char *)payload - sizeof(int64_t));
}

/* A fresh copy, because every text this runtime hands back is owned by
 * the caller (claude.md #83) -- returning b->bytes directly would let a
 * text binding free a buffer the blob still owns. */
char *festina_blob_to_text(void *payload) {
    if (!payload) return NULL;
    FestinaBlob *b = (FestinaBlob *)payload;
    char *out = malloc((size_t)b->length + 1);
    if (!out) festina_fail("out of memory in blob.toText()");
    memcpy(out, b->bytes, (size_t)b->length);
    out[b->length] = '\0';
    return out;
}

const void *festina_blob_bytes(void *payload, int64_t *out_len) {
    if (out_len) *out_len = 0;
    if (!payload) return NULL;
    FestinaBlob *b = (FestinaBlob *)payload;
    if (out_len) *out_len = b->length;
    return b->bytes;
}

/* Replaces the in-memory bytes as well as the file, so .toText()
 * immediately after .write() reports what was written rather than what
 * the file held at declaration time. If the write fails the blob is
 * left alone -- reporting content that never reached the disk would be
 * worse than reporting stale content. */
static int8_t festina_blob_store(FestinaBlob *b, const char *content,
                                  const char *mode, int append) {
    if (!b->path || !*b->path) return 0;
    if (!content) content = "";
    if (!festina_put_file(b->path, content, mode)) return 0;
    size_t add = strlen(content);
    if (append) {
        char *grown = malloc((size_t)b->length + add + 1);
        if (!grown) festina_fail("out of memory in blob.append()");
        memcpy(grown, b->bytes, (size_t)b->length);
        memcpy(grown + b->length, content, add);
        grown[b->length + add] = '\0';
        free(b->bytes);
        b->bytes = grown;
        b->length += (int64_t)add;
    } else {
        char *fresh = malloc(add + 1);
        if (!fresh) festina_fail("out of memory in blob.write()");
        memcpy(fresh, content, add + 1);
        free(b->bytes);
        b->bytes = fresh;
        b->length = (int64_t)add;
    }
    return 1;
}

int8_t festina_blob_write(void *payload, const char *content) {
    if (!payload) return 0;
    return festina_blob_store((FestinaBlob *)payload, content, "wb", 0);
}

int8_t festina_blob_append(void *payload, const char *content) {
    if (!payload) return 0;
    return festina_blob_store((FestinaBlob *)payload, content, "ab", 1);
}

int8_t festina_blob_exists(void *payload) {
    if (!payload) return 0;
    return festina_file_exists(((FestinaBlob *)payload)->path);
}

/* Deletes the FILE. The blob itself is an ordinary reference-counted
 * value and is unaffected -- its bytes stay readable, which is what
 * makes "delete it but keep what it said" expressible. */
int8_t festina_blob_delete(void *payload) {
    if (!payload) return 0;
    return festina_delete_file(((FestinaBlob *)payload)->path);
}

/* ---- saving a handle's bytes -- claude.md #110 ----
 *
 * One policy, shared by blob, img and aud, because all three are the
 * same shape of value (claude.md #101/#109: content plus the bytes it
 * came from) and "write those bytes somewhere" should not mean three
 * slightly different things.
 *
 *   save()           -- write to the path this handle already has.
 *   save(path)       -- adopt `path`, then write there. The handle's own
 *                       path CHANGES, so everything else that acts on it
 *                       (a blob's exists()/delete()) follows it.
 *   saveCopy(path)   -- write to `path` and leave the handle's own path
 *                       alone. The argument is required, enforced in the
 *                       compiler rather than here, so omitting it is a
 *                       compile error rather than a runtime surprise.
 *
 * `target` is always a complete FILE path -- there is no directory
 * shorthand. A directory would have to borrow a filename from
 * somewhere, and the one handle that most needs saving (a clip, a
 * database column) is exactly the one with no filename to borrow, so
 * the shorthand would work only where it was least useful. Passing one
 * anyway answers false, like any other unwritable target.
 *
 * A handle with no path is the case this exists for. An `img` from
 * clip(), an `aud` or `blob` out of a database column -- none has ever
 * been on disk, so save() with no argument has nothing to write to and
 * FAILS the program rather than returning false. That is a bug in the
 * program, not a condition of the filesystem, and the two deserve
 * different treatment: an I/O failure (full disk, unwritable directory)
 * still returns false the way every other file operation here does. */

int8_t festina_save_bytes(const char *target, char **own_path,
                          const void *data, int64_t len,
                          const char *what, int8_t adopt) {
    const char *current = (own_path && *own_path) ? *own_path : "";
    char *resolved = NULL;

    if (!target || !*target) {
        /* The no-argument save(). */
        if (!*current) {
            char msg[256];
            snprintf(msg, sizeof(msg),
                     "this %s has no path to save() to -- it did not come from a "
                     "file (a clip, or a database column), so pass one: "
                     "save('path/to/file')", what);
            festina_fail(msg);
        }
        resolved = strdup(current);
    } else {
        resolved = strdup(target);
    }
    if (!resolved) festina_fail("out of memory resolving a save path");

    FILE *f = fopen(resolved, "wb");
    if (!f) { free(resolved); return 0; }
    size_t want = len > 0 ? (size_t)len : 0;
    size_t wrote = want ? fwrite(data, 1, want, f) : 0;
    /* fclose can fail on a full disk after every fwrite succeeded, so it
     * is part of "did this write actually land" -- same rule
     * festina_put_file already follows. */
    int closed = fclose(f);
    int8_t ok = (wrote == want && closed == 0) ? 1 : 0;

    /* The path is adopted only on SUCCESS. Pointing a handle at a file
     * that was never written would leave exists() answering false about
     * a path the program was just told it now has. */
    if (ok && adopt && own_path) {
        free(*own_path);
        *own_path = resolved;
    } else {
        free(resolved);
    }
    return ok;
}

int8_t festina_blob_save(void *payload, const char *target) {
    if (!payload) return 0;
    FestinaBlob *b = (FestinaBlob *)payload;
    return festina_save_bytes(target, &b->path, b->bytes, b->length, "blob", 1);
}

int8_t festina_blob_save_copy(void *payload, const char *target) {
    if (!payload) return 0;
    FestinaBlob *b = (FestinaBlob *)payload;
    return festina_save_bytes(target, &b->path, b->bytes, b->length, "blob", 0);
}

/* claude.md #93: milliseconds since the Unix epoch -- the same unit and
 * origin JavaScript's Date.now() uses, which is the convention this
 * language's timers already follow (setTimeout takes milliseconds). */
int64_t festina_now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    return (int64_t)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

/* claude.md #93: strftime against local time. Returns NULL (null text)
 * rather than failing when the format produces nothing that fits, so a
 * bad format string is testable instead of fatal. */
char *festina_format_time(int64_t ms, const char *format) {
    if (!format) format = "%Y-%m-%d %H:%M:%S";
    time_t secs = (time_t)(ms / 1000);
    struct tm parts;
    if (!localtime_r(&secs, &parts)) return NULL;
    char buf[512];
    size_t n = strftime(buf, sizeof(buf), format, &parts);
    if (n == 0) return NULL;
    return strdup(buf);
}

int8_t festina_str_eq(const char *a, const char *b) {
    if (!a) a = "";
    if (!b) b = "";
    return strcmp(a, b) == 0;
}

/* ---- automatic SQLite database + schema sync -- claude.md #8, #28-31 ---- */

static void festina_check(sqlite3 *db, int rc, const char *what) {
    if (rc != SQLITE_OK) {
        char msg[512];
        snprintf(msg, sizeof(msg), "%s: %s", what, sqlite3_errmsg(db));
        festina_fail(msg);
    }
}

static void festina_exec(sqlite3 *db, const char *sql) {
    char *errmsg = NULL;
    int rc = sqlite3_exec(db, sql, NULL, NULL, &errmsg);
    if (rc != SQLITE_OK) {
        char msg[1024];
        snprintf(msg, sizeof(msg), "sqlite error running %s: %s", sql,
                 errmsg ? errmsg : "unknown error");
        sqlite3_free(errmsg);
        festina_fail(msg);
    }
}

sqlite3 *festina_db_open(const char *path) {
    /* claude.md #70: DatabaseURL overrides the default -- codegen always
     * passes a real string (either the compiled-in "festina.sqlite"
     * constant, or the DatabaseURL expression's own value), but a NULL
     * or empty path is treated the same as "no override" defensively,
     * since a DatabaseURL expression is still an arbitrary runtime text
     * value (e.g. environment.DATABASE_URL) that could come back unset. */
    if (!path || !*path) path = "festina.sqlite";
    sqlite3 *db = NULL;
    int rc = sqlite3_open(path, &db);
    if (rc != SQLITE_OK) {
        char msg[512];
        snprintf(msg, sizeof(msg), "cannot open %s: %s", path,
                 db ? sqlite3_errmsg(db) : "unknown error");
        festina_fail(msg);
    }
    /* claude.md #113: WAL journaling with synchronous=NORMAL. SQLite's
     * shipped defaults (rollback journal, synchronous=FULL) fsync on
     * every autocommitted statement, which priced a plain INSERT loop
     * at ~1ms per row -- measured: 20,000 inserts took 16.7 seconds,
     * of which sqlite's own work was a rounding error. WAL+NORMAL is
     * the standard application-embedded configuration: transactions
     * survive an application crash unconditionally, and only an
     * OS-level crash or power loss can lose the most recent commits
     * (never corrupt the database). For the programs this language is
     * for -- games, tools -- that is the right trade, and the same one
     * every browser and phone OS ships sqlite with. Errors are ignored
     * deliberately: a read-only filesystem or an exotic VFS that
     * cannot do WAL just keeps the old defaults and still works. */
    sqlite3_exec(db, "PRAGMA journal_mode=WAL;", NULL, NULL, NULL);
    sqlite3_exec(db, "PRAGMA synchronous=NORMAL;", NULL, NULL, NULL);
    return db;
}

/* claude.md #113: prepared-statement caching for LITERAL SQL.
 *
 * sqlite3_prepare_v2 re-parses, re-plans and re-compiles the SQL into a
 * fresh bytecode program on every call. When the SQL is a compile-time
 * string literal it can never change, so that work is pure waste after
 * the first call -- the identical reasoning claude.md #85 applied to
 * /pattern/ regex literals, arrived at the same way: the compiler knows
 * the text is constant, so it allocates one cache slot per CALL SITE
 * (a private global, null until first reached) and routes the call
 * through here instead of festina_sqlite_prepare. A dynamic SQL string
 * (a template literal, a variable) keeps the uncached path, since the
 * same call site can legitimately see different SQL each time.
 *
 * The registry below is what lets every existing consumer stay
 * oblivious: collect_rows, exec and the scalar helpers all end their
 * statement through festina_sqlite_finish, which RESETS a registered
 * statement (returning it to the cache slot's custody, bindings
 * cleared) and finalizes an unregistered one exactly as before. The
 * registry is a linear array scanned per finish -- its size is the
 * number of distinct literal sqlite() call sites in the program, a
 * few dozen at the outside, not a per-row or per-call quantity. */
static sqlite3_stmt **g_cached_stmts = NULL;
static int g_cached_stmt_count = 0;
static int g_cached_stmt_cap = 0;

sqlite3_stmt *festina_sqlite_prepare_cached(sqlite3 *db, const char *sql,
                                            void **slot) {
    if (*slot) {
        sqlite3_stmt *stmt = (sqlite3_stmt *)*slot;
        sqlite3_reset(stmt);
        sqlite3_clear_bindings(stmt);
        return stmt;
    }
    sqlite3_stmt *stmt = festina_sqlite_prepare(db, sql);
    if (g_cached_stmt_count == g_cached_stmt_cap) {
        g_cached_stmt_cap = g_cached_stmt_cap ? g_cached_stmt_cap * 2 : 16;
        sqlite3_stmt **grown = realloc(g_cached_stmts,
                                       (size_t)g_cached_stmt_cap * sizeof(*grown));
        if (!grown) festina_fail("out of memory caching a statement");
        g_cached_stmts = grown;
    }
    g_cached_stmts[g_cached_stmt_count++] = stmt;
    *slot = stmt;
    return stmt;
}

/* Finalize -- unless the statement is one of the cached ones, in which
 * case reset it for its next use. Every statement consumer ends its
 * statement through this. */
static void festina_sqlite_finish(sqlite3_stmt *stmt) {
    for (int i = 0; i < g_cached_stmt_count; i++) {
        if (g_cached_stmts[i] == stmt) {
            sqlite3_reset(stmt);
            return;
        }
    }
    sqlite3_finalize(stmt);
}

/* claude.md #30 */
static const char *festina_sql_type(const char *festina_type) {
    if (strcmp(festina_type, "int") == 0) return "INTEGER";
    if (strcmp(festina_type, "float") == 0) return "REAL";
    if (strcmp(festina_type, "bool") == 0) return "INTEGER";
    if (strcmp(festina_type, "text") == 0) return "TEXT";
    if (strcmp(festina_type, "blob") == 0) return "BLOB";
    /* claude.md #101: an `aud`/`img` column stores the asset's own
     * encoded bytes, so BLOB is the only honest SQL type for it. TEXT
     * (what these used to fall through to) would have silently
     * truncated at the first NUL byte in a PNG header. */
    if (strcmp(festina_type, "aud") == 0) return "BLOB";
    if (strcmp(festina_type, "img") == 0) return "BLOB";
    return "TEXT";
}

/* claude.md #101: decoders for the two media types, registered by
 * main() rather than called by name. This translation unit must not
 * reference anything in the graphics or audio ones -- that separation
 * is what lets a program which uses neither link neither (see
 * festina_runtime.h's top-of-file note) -- so a row containing an
 * `aud`/`img` column reaches its decoder through a pointer that
 * codegen fills in exactly when the program already links that
 * feature. Left NULL otherwise, in which case such a column reads as
 * null rather than crashing. */
static void *(*g_audio_decoder)(const void *, int64_t, const char *) = NULL;
static void *(*g_image_decoder)(const void *, int64_t, const char *) = NULL;

void festina_set_audio_decoder(void *(*fn)(const void *, int64_t, const char *)) {
    g_audio_decoder = fn;
}

void festina_set_image_decoder(void *(*fn)(const void *, int64_t, const char *)) {
    g_image_decoder = fn;
}

/* festina_sync_table below builds several SQL statements incrementally
 * across a loop over the declared columns, in the form
 * `pos += snprintf(buf + pos, sizeof(buf) - pos, ...)`. That pattern
 * looks bounds-safe (it's the textbook idiom for it) but genuinely
 * isn't: snprintf's return value is how many bytes *would* have been
 * written if the buffer were big enough, not how many actually fit --
 * so once accumulated output exceeds the buffer, `pos` exceeds
 * `sizeof(buf)`, and the *next* iteration's `sizeof(buf) - pos` is
 * computed as unsigned arithmetic between a smaller and a larger value,
 * silently underflowing to a huge number close to SIZE_MAX. snprintf is
 * then told it has ~18 exabytes of buffer to write into and gladly
 * writes straight past the real (2048-or-so-byte) stack array --
 * verified directly: a table with enough columns (or long enough
 * column/table names) that the generated SQL exceeds one of these
 * buffers reliably stack-smashes and crashes under AddressSanitizer.
 * Called at the top of every loop iteration that accumulates into one
 * of these buffers (and once more after the loop, before any final
 * fixed-text append), so `sizeof(buf) - pos` is never computed once
 * `pos` has already reached or passed `buf_size` -- turning what would
 * be undetected memory corruption into a clear, actionable
 * festina_fail() instead (a table with columns that simply can't fit
 * in a fixed-size buffer is a real, if unusual, condition to handle,
 * not something to grow the buffers arbitrarily large to rule out). */
static void festina_check_sql_buffer(int pos, size_t buf_size, const char *what) {
    if (pos < 0 || (size_t)pos >= buf_size) {
        char msg[256];
        snprintf(msg, sizeof(msg),
                 "festina_sync_table: %s is too long for this compiler's fixed-size "
                 "buffer (too many columns, or column/table names too long)", what);
        festina_fail(msg);
    }
}

#define FESTINA_MAX_COLS 64

void festina_sync_table(sqlite3 *db, const char *table_name,
                         const char **col_names, const char **col_types,
                         int32_t ncols) {
    if (ncols > FESTINA_MAX_COLS) {
        festina_fail("festina_sync_table: too many columns (raise FESTINA_MAX_COLS)");
    }

    /* claude.md #28: compare the declared table against festina.sqlite. */
    char pragma_sql[256];
    snprintf(pragma_sql, sizeof(pragma_sql), "PRAGMA table_info(%s);", table_name);

    sqlite3_stmt *stmt = NULL;
    festina_check(db, sqlite3_prepare_v2(db, pragma_sql, -1, &stmt, NULL), "reading table schema");

    char existing_names[FESTINA_MAX_COLS][128];
    char existing_types[FESTINA_MAX_COLS][32];
    int n_existing = 0;
    int rc;
    while ((rc = sqlite3_step(stmt)) == SQLITE_ROW) {
        if (n_existing >= FESTINA_MAX_COLS) break;
        const unsigned char *name = sqlite3_column_text(stmt, 1);
        const unsigned char *type = sqlite3_column_text(stmt, 2);
        snprintf(existing_names[n_existing], sizeof(existing_names[0]), "%s", name ? (const char *)name : "");
        snprintf(existing_types[n_existing], sizeof(existing_types[0]), "%s", type ? (const char *)type : "");
        n_existing++;
    }
    sqlite3_finalize(stmt);

    /* claude.md #28: table doesn't exist yet -- create it. */
    if (n_existing == 0) {
        char sql[2048];
        int pos = snprintf(sql, sizeof(sql), "CREATE TABLE IF NOT EXISTS %s (", table_name);
        for (int i = 0; i < ncols; i++) {
            festina_check_sql_buffer(pos, sizeof(sql), "CREATE TABLE statement");
            pos += snprintf(sql + pos, sizeof(sql) - (size_t)pos, "%s%s %s", i ? ", " : "",
                             col_names[i], festina_sql_type(col_types[i]));
        }
        festina_check_sql_buffer(pos, sizeof(sql), "CREATE TABLE statement");
        snprintf(sql + pos, sizeof(sql) - (size_t)pos, ");");
        festina_exec(db, sql);
        return;
    }

    /* claude.md #28: diff the declared schema against the existing one. */
    int needs_drop = 0, needs_alter = 0;
    for (int i = 0; i < n_existing; i++) {
        int declared = 0;
        for (int j = 0; j < ncols; j++) {
            if (strcmp(existing_names[i], col_names[j]) == 0) { declared = 1; break; }
        }
        if (!declared) needs_drop = 1;
    }
    for (int j = 0; j < ncols; j++) {
        for (int i = 0; i < n_existing; i++) {
            if (strcmp(existing_names[i], col_names[j]) == 0) {
                if (strcmp(existing_types[i], festina_sql_type(col_types[j])) != 0) needs_alter = 1;
                break;
            }
        }
    }

    if (!needs_drop && !needs_alter) {
        /* claude.md #28: only missing columns to add -- ALTER TABLE suffices. */
        for (int j = 0; j < ncols; j++) {
            int exists = 0;
            for (int i = 0; i < n_existing; i++) {
                if (strcmp(existing_names[i], col_names[j]) == 0) { exists = 1; break; }
            }
            if (!exists) {
                char sql[512];
                snprintf(sql, sizeof(sql), "ALTER TABLE %s ADD COLUMN %s %s;",
                         table_name, col_names[j], festina_sql_type(col_types[j]));
                festina_exec(db, sql);
            }
        }
        return;
    }

    /*
     * claude.md #28, #31: SQLite can't drop or retype a column in
     * place in general, so rebuild through a temporary table, copying
     * over every column that survives the change ("preserve existing
     * data whenever possible").
     */
    char new_table[192];
    snprintf(new_table, sizeof(new_table), "%s__festina_new", table_name);

    char create_sql[2048];
    int pos = snprintf(create_sql, sizeof(create_sql), "CREATE TABLE %s (", new_table);
    for (int i = 0; i < ncols; i++) {
        festina_check_sql_buffer(pos, sizeof(create_sql), "CREATE TABLE statement");
        pos += snprintf(create_sql + pos, sizeof(create_sql) - (size_t)pos, "%s%s %s", i ? ", " : "",
                         col_names[i], festina_sql_type(col_types[i]));
    }
    festina_check_sql_buffer(pos, sizeof(create_sql), "CREATE TABLE statement");
    snprintf(create_sql + pos, sizeof(create_sql) - (size_t)pos, ");");
    festina_exec(db, create_sql);

    char dest_cols[1024] = "";
    char src_cols[1024] = "";
    int dpos = 0, spos = 0;
    int first = 1;
    for (int j = 0; j < ncols; j++) {
        for (int i = 0; i < n_existing; i++) {
            if (strcmp(existing_names[i], col_names[j]) == 0) {
                festina_check_sql_buffer(dpos, sizeof(dest_cols), "column list");
                dpos += snprintf(dest_cols + dpos, sizeof(dest_cols) - (size_t)dpos, "%s%s", first ? "" : ", ", col_names[j]);
                festina_check_sql_buffer(spos, sizeof(src_cols), "column list");
                if (strcmp(existing_types[i], festina_sql_type(col_types[j])) != 0) {
                    spos += snprintf(src_cols + spos, sizeof(src_cols) - (size_t)spos, "%sCAST(%s AS %s)",
                                      first ? "" : ", ", col_names[j], festina_sql_type(col_types[j]));
                } else {
                    spos += snprintf(src_cols + spos, sizeof(src_cols) - (size_t)spos, "%s%s", first ? "" : ", ", col_names[j]);
                }
                first = 0;
                break;
            }
        }
    }

    if (dpos > 0) {
        char insert_sql[2560];
        snprintf(insert_sql, sizeof(insert_sql), "INSERT INTO %s (%s) SELECT %s FROM %s;",
                 new_table, dest_cols, src_cols, table_name);
        festina_exec(db, insert_sql);
    }

    char drop_sql[256];
    snprintf(drop_sql, sizeof(drop_sql), "DROP TABLE %s;", table_name);
    festina_exec(db, drop_sql);

    char rename_sql[256];
    snprintf(rename_sql, sizeof(rename_sql), "ALTER TABLE %s RENAME TO %s;", new_table, table_name);
    festina_exec(db, rename_sql);
}

/* ---- sqlite() queries -- claude.md #32-34 ---- */

/* Kept in sync with festina/codegen.py's INT_NULL_CONST / FLOAT_NULL_CONST
 * -- see that module's "Null for int/float" docstring note. */
static int64_t festina_null_int(void) {
    return INT64_MIN;
}

static double festina_null_float(void) {
    uint64_t bits = 0x7FF8000000000000ULL;  /* a quiet NaN */
    double d;
    memcpy(&d, &bits, sizeof(d));
    return d;
}

sqlite3_stmt *festina_sqlite_prepare(sqlite3 *db, const char *sql) {
    sqlite3_stmt *stmt = NULL;
    int rc = sqlite3_prepare_v2(db, sql, -1, &stmt, NULL);
    if (rc != SQLITE_OK) {
        char msg[1024];
        snprintf(msg, sizeof(msg), "sqlite error preparing '%s': %s", sql, sqlite3_errmsg(db));
        festina_fail(msg);
    }
    return stmt;
}

void festina_sqlite_bind_int(sqlite3_stmt *stmt, int32_t idx, int64_t val) {
    sqlite3_bind_int64(stmt, idx, val);
}

void festina_sqlite_bind_float(sqlite3_stmt *stmt, int32_t idx, double val) {
    sqlite3_bind_double(stmt, idx, val);
}

void festina_sqlite_bind_text(sqlite3_stmt *stmt, int32_t idx, const char *val) {
    if (val) {
        sqlite3_bind_text(stmt, idx, val, -1, SQLITE_TRANSIENT);
    } else {
        sqlite3_bind_null(stmt, idx);
    }
}

/* claude.md #101: binds an `aud`/`img` as its own encoded bytes.
 * SQLITE_TRANSIENT so sqlite copies immediately -- the asset owns those
 * bytes and outlives nothing in particular, and a borrowed pointer
 * would be a live landmine the first time one was freed. */
void festina_sqlite_bind_blob(sqlite3_stmt *stmt, int32_t idx, const void *data, int64_t len) {
    if (data && len > 0) {
        sqlite3_bind_blob64(stmt, idx, data, (sqlite3_uint64)len, SQLITE_TRANSIENT);
    } else {
        sqlite3_bind_null(stmt, idx);
    }
}

void festina_sqlite_bind_null(sqlite3_stmt *stmt, int32_t idx) {
    sqlite3_bind_null(stmt, idx);
}

/* claude.md #94: single-value queries.
 *
 * Receiving a result previously meant declaring a `table` to hold the
 * row shape -- and a table declaration CREATES a real table (claude.md
 * #28-31's automatic schema sync), so asking for `count(*)` or one
 * json_extract() left a throwaway table sitting in the database
 * forever. These three take the first column of the first row and
 * finalize, so a scalar query costs no schema at all.
 *
 * A query returning no rows answers with Festina's own null for that
 * type, rather than failing: "no rows matched" is an ordinary result a
 * program should be able to test for, the same reasoning claude.md #57
 * applies to division by zero. */
int64_t festina_sqlite_scalar_int(sqlite3_stmt *stmt) {
    int64_t out = festina_null_int();
    if (sqlite3_step(stmt) == SQLITE_ROW
            && sqlite3_column_type(stmt, 0) != SQLITE_NULL) {
        out = sqlite3_column_int64(stmt, 0);
    }
    festina_sqlite_finish(stmt);
    return out;
}

double festina_sqlite_scalar_float(sqlite3_stmt *stmt) {
    double out = festina_null_float();
    if (sqlite3_step(stmt) == SQLITE_ROW
            && sqlite3_column_type(stmt, 0) != SQLITE_NULL) {
        out = sqlite3_column_double(stmt, 0);
    }
    festina_sqlite_finish(stmt);
    return out;
}

char *festina_sqlite_scalar_text(sqlite3_stmt *stmt) {
    char *out = NULL;
    if (sqlite3_step(stmt) == SQLITE_ROW
            && sqlite3_column_type(stmt, 0) != SQLITE_NULL) {
        const unsigned char *txt = sqlite3_column_text(stmt, 0);
        /* Copied: sqlite owns that buffer only until the next step or
         * finalize, and Festina text is always an owned buffer
         * (claude.md #83). */
        if (txt) out = strdup((const char *)txt);
    }
    festina_sqlite_finish(stmt);
    return out;
}

void festina_sqlite_exec(sqlite3_stmt *stmt) {
    int rc;
    while ((rc = sqlite3_step(stmt)) == SQLITE_ROW) {
        /* discard row data -- e.g. a SELECT whose result isn't captured */
    }
    if (rc != SQLITE_DONE) {
        sqlite3 *db = sqlite3_db_handle(stmt);
        char msg[512];
        snprintf(msg, sizeof(msg), "sqlite error executing statement: %s", sqlite3_errmsg(db));
        festina_sqlite_finish(stmt);
        festina_fail(msg);
    }
    festina_sqlite_finish(stmt);
}

/* claude.md #34: row layout is col_count 8-byte slots per row -- see
 * this function's doc comment in festina_runtime.h for the full
 * rationale (matches how festina/codegen.py reads a row back). */
/* claude.md #111: result columns are matched to declared columns BY
 * NAME, not by position. Positional matching was a real bug hiding
 * behind the `SELECT *` habit: `SELECT name FROM t` against
 * `table t { id:int name:text }` used to read the name's text into the
 * id slot as an integer, and `SELECT id` read a result column that did
 * not exist for `name` (formally undefined behavior in sqlite). Names
 * are compared case-insensitively, matching SQL's own treatment of
 * identifiers.
 *
 * Each row also carries one extra hidden slot after the columns: a
 * presence BITMASK, bit c set when declared column c appeared in the
 * result set at all. That is what festina_row_undefined reads -- the
 * difference between "the database said NULL" and "the query never
 mentioned this column" is real (a program deciding whether to trust a
 * value needs it) and nothing else records it. Columns past the 64th
 * are always reported as present; a table that wide has other
 * problems first. */
void festina_sqlite_collect_rows(sqlite3_stmt *stmt, int32_t col_count,
                                  const char **col_types, const char **col_names,
                                  int64_t *out_length, void **out_data) {
    /* Which RESULT column serves each declared column, or -1. Computed
     * once -- the mapping is a property of the statement, not the row. */
    int32_t *src = malloc((size_t)(col_count > 0 ? col_count : 1) * sizeof(int32_t));
    if (!src) festina_fail("out of memory in festina_sqlite_collect_rows");
    int result_cols = sqlite3_column_count(stmt);
    for (int32_t c = 0; c < col_count; c++) {
        src[c] = -1;
        for (int r = 0; r < result_cols; r++) {
            const char *rn = sqlite3_column_name(stmt, r);
            if (rn && sqlite3_stricmp(rn, col_names[c]) == 0) { src[c] = r; break; }
        }
    }
    int64_t capacity = 8;
    void **rows = malloc(capacity * sizeof(void *));
    if (!rows) festina_fail("out of memory in festina_sqlite_collect_rows");
    int64_t count = 0;
    int rc;

    while ((rc = sqlite3_step(stmt)) == SQLITE_ROW) {
        if (count >= capacity) {
            capacity *= 2;
            void **grown = realloc(rows, capacity * sizeof(void *));
            if (!grown) festina_fail("out of memory in festina_sqlite_collect_rows");
            rows = grown;
        }

        /* +1: the presence mask lives after the columns, so every
         * existing field offset is untouched. */
        int64_t *row = malloc(((size_t)col_count + 1) * sizeof(int64_t));
        if (!row) festina_fail("out of memory in festina_sqlite_collect_rows");
        uint64_t present = 0;

        for (int32_t c = 0; c < col_count; c++) {
            const char *t = col_types[c];
            int32_t rc_col = src[c];
            if (rc_col >= 0 && c < 64) present |= ((uint64_t)1 << c);
            int is_null = rc_col < 0
                || sqlite3_column_type(stmt, rc_col) == SQLITE_NULL;
            int c_ = rc_col < 0 ? 0 : rc_col;  /* never read when is_null */
            if (strcmp(t, "float") == 0) {
                double d = is_null ? festina_null_float() : sqlite3_column_double(stmt, c_);
                memcpy(&row[c], &d, sizeof(double));
            } else if (strcmp(t, "aud") == 0 || strcmp(t, "img") == 0) {
                /* claude.md #101: rebuild the asset from the stored
                 * bytes. sqlite3_column_blob's pointer is only valid
                 * until the next step, which is exactly why the decoder
                 * copies what it needs rather than borrowing. */
                void *handle = NULL;
                void *(*decode)(const void *, int64_t, const char *) =
                    strcmp(t, "aud") == 0 ? g_audio_decoder : g_image_decoder;
                if (!is_null && decode) {
                    const void *blob = sqlite3_column_blob(stmt, c_);
                    int blob_len = sqlite3_column_bytes(stmt, c_);
                    if (blob && blob_len > 0) handle = decode(blob, blob_len, "<database>");
                }
                memcpy(&row[c], &handle, sizeof(void *));
            } else if (strcmp(t, "blob") == 0) {
                /* claude.md #109: a blob column round-trips its BYTES,
                 * not its path -- a path is meaningful only on the
                 * machine that stored it, while the contents are the
                 * thing worth keeping. This needs no registered decoder
                 * the way aud/img do (claude.md #101): those decoders
                 * live in the graphics/audio translation units, which a
                 * program only links when it uses them, whereas
                 * festina_blob_from_bytes is right here in the core.
                 *
                 * Reading the BLOB rather than the text: a blob may
                 * legitimately contain NUL, and sqlite3_column_text
                 * would stop at the first one. That was the bug the
                 * previous shared text/blob branch had -- it treated a
                 * blob column as a C string, which is exactly the
                 * truncation claude.md #101 called out for media
                 * columns and fixed only for aud/img. */
                void *handle = NULL;
                if (!is_null) {
                    const void *data = sqlite3_column_blob(stmt, c_);
                    int len = sqlite3_column_bytes(stmt, c_);
                    handle = festina_blob_from_bytes(data, len < 0 ? 0 : len);
                }
                memcpy(&row[c], &handle, sizeof(void *));
            } else if (strcmp(t, "text") == 0) {
                char *copy = NULL;
                if (!is_null) {
                    const unsigned char *txt = sqlite3_column_text(stmt, c_);
                    copy = strdup(txt ? (const char *)txt : "");
                }
                memcpy(&row[c], &copy, sizeof(char *));
            } else {
                /* int, bool -- claude.md #30 maps bool to SQLite INTEGER too */
                row[c] = is_null ? festina_null_int() : sqlite3_column_int64(stmt, c_);
            }
        }

        /* Columns past 64 report as present -- see the doc comment. */
        if (col_count > 64) present = ~(uint64_t)0;
        memcpy(&row[col_count], &present, sizeof(uint64_t));
        rows[count++] = row;
    }
    free(src);

    if (rc != SQLITE_DONE) {
        sqlite3 *db = sqlite3_db_handle(stmt);
        char msg[512];
        snprintf(msg, sizeof(msg), "sqlite error reading rows: %s", sqlite3_errmsg(db));
        festina_sqlite_finish(stmt);
        festina_fail(msg);
    }
    festina_sqlite_finish(stmt);

    *out_length = count;
    *out_data = rows;
}

/* claude.md #111: row.undefined('name') -- true when the named declared
 * column was NOT in the query's result set (or was `delete`d, which
 * clears its presence bit), distinguishing that from a column the
 * database genuinely returned as NULL. An unknown column name fails the
 * program: asking about a column the table does not have is a typo, and
 * answering true or false would bury it. */
int8_t festina_row_undefined(void *row, const char **col_names,
                             int32_t col_count, const char *name) {
    if (!row) return 1;
    if (!name) name = "";
    for (int32_t c = 0; c < col_count; c++) {
        if (sqlite3_stricmp(col_names[c], name) == 0) {
            if (c >= 64) return 0;
            uint64_t present;
            memcpy(&present, &((int64_t *)row)[col_count], sizeof(uint64_t));
            return (present & ((uint64_t)1 << c)) ? 0 : 1;
        }
    }
    char msg[256];
    snprintf(msg, sizeof(msg),
             "undefined('%s'): this table has no column by that name", name);
    festina_fail(msg);
    return 1; /* unreachable */
}

/* ---- regex(), .test(), .match(), .replace() -- claude.md #67-68, #107 ---- */

/* claude.md #107: a compiled regex is no longer a bare regex_t. The 'g'
 * flag has to travel WITH the pattern rather than with the call site,
 * because `regex(p, f)` builds its flags from a runtime text expression
 * the compiler cannot inspect -- so there is nothing for codegen to
 * read at the .replace() call and the decision has to be made here.
 * `re` is first in the struct deliberately: every regexec call below
 * takes `&r->re`, and a bare cast would still land on the right bytes
 * if one were ever missed. */
typedef struct {
    regex_t re;
    int8_t global;
} FestinaRegex;

/* claude.md #118: a regex is REFERENCE COUNTED now, carrying the same
 * i64 header immediately before the payload that structs/arrays/maps/
 * blobs use. Two things needed it at once: `free` on an aliased regex
 * binding had to become a safe decrement like every other refcounted
 * type's, and festina_regex_compile_memo below can only evict a
 * superseded compilation safely if a binding that still aliases it
 * keeps it alive. A /pattern/ literal's process-lifetime cached form
 * uses the standard immortal sentinel (a NEGATIVE header, see
 * festina_retain's own comment) instead of the separate `cached` flag
 * it used to carry -- retain/release/free on one are all no-ops through
 * the exact same check every other immortal value already goes
 * through. */
/* claude.md #122 / macos.md Phase 0's first real-hardware finding:
 * `\w`/`\d`/`\s`/`\b` are GNU extensions of glibc's regcomp(), and
 * api.md promises them -- but macOS's BSD libc silently treats `\s` as
 * a literal 's', so /\s+/ matched nothing and three regex tests failed
 * on the first macos-14 CI run. The portable answer is translation,
 * not a vendored engine: expand the GNU class escapes into the POSIX
 * bracket classes every implementation defines, on EVERY platform, so
 * one behavior exists and it is the one already tested. Inside a
 * bracket expression a backslash is literal per POSIX (and glibc
 * agrees), so translation applies outside brackets only; [:class:]
 * bodies are walked so their ']' does not end the bracket early.
 *
 * `\b` has no POSIX spelling at all. glibc supports it natively, so on
 * Linux it passes through untouched; BSD instead has the [[:<:]] and
 * [[:>:]] word-boundary brackets, so on __APPLE__ `\b` becomes the
 * opening form when a word character (or an escape/class/group that
 * starts one) follows, and the closing form otherwise -- which covers
 * the `\bword\b` shape `\b` exists for. */
static char *festina_regex_expand_gnu(const char *pattern) {
    size_t len = strlen(pattern);
    char *out = malloc(len * 13 + 16);
    if (!out) festina_fail("out of memory compiling a regex");
    size_t o = 0, i = 0;
    int in_bracket = 0;
    size_t bracket_elems = 0;   /* ']' as the first element is literal */

    while (i < len) {
        char c = pattern[i];
        if (in_bracket) {
            if (c == '[' && i + 1 < len &&
                    (pattern[i + 1] == ':' || pattern[i + 1] == '.' || pattern[i + 1] == '=')) {
                char kind = pattern[i + 1];
                out[o++] = c; out[o++] = kind; i += 2;
                while (i + 1 < len && !(pattern[i] == kind && pattern[i + 1] == ']'))
                    out[o++] = pattern[i++];
                if (i + 1 < len) { out[o++] = kind; out[o++] = ']'; i += 2; }
                bracket_elems++;
                continue;
            }
            if (c == ']' && bracket_elems > 0) in_bracket = 0;
            else if (c != '^' || bracket_elems > 0) bracket_elems++;
            out[o++] = c; i++;
            continue;
        }
        if (c == '\\' && i + 1 < len) {
            char n = pattern[i + 1];
            const char *rep = NULL;
            switch (n) {
                case 's': rep = "[[:space:]]"; break;
                case 'S': rep = "[^[:space:]]"; break;
                case 'd': rep = "[[:digit:]]"; break;
                case 'D': rep = "[^[:digit:]]"; break;
                case 'w': rep = "[[:alnum:]_]"; break;
                case 'W': rep = "[^[:alnum:]_]"; break;
#ifdef __APPLE__
                case 'b': {
                    char next = (i + 2 < len) ? pattern[i + 2] : '\0';
                    int opening = ((next >= 'a' && next <= 'z') || (next >= 'A' && next <= 'Z')
                                   || (next >= '0' && next <= '9') || next == '_'
                                   || next == '\\' || next == '[' || next == '(');
                    rep = opening ? "[[:<:]]" : "[[:>:]]";
                    break;
                }
#endif
                default: break;
            }
            if (rep) {
                size_t rlen = strlen(rep);
                memcpy(out + o, rep, rlen);
                o += rlen;
            } else {
                out[o++] = c;
                out[o++] = n;
            }
            i += 2;
            continue;
        }
        if (c == '[') {
            in_bracket = 1;
            bracket_elems = 0;
        }
        out[o++] = c;
        i++;
    }
    out[o] = '\0';
    return out;
}

void *festina_regex_compile(const char *pattern, const char *flags) {
    if (!pattern) pattern = "";
    if (!flags) flags = "";
    char *raw = malloc(sizeof(int64_t) + sizeof(FestinaRegex));
    if (!raw) festina_fail("out of memory in festina_regex_compile");
    *(int64_t *)raw = 1;
    FestinaRegex *compiled = (FestinaRegex *)(raw + sizeof(int64_t));

    int cflags = REG_EXTENDED;
    if (strchr(flags, 'i')) cflags |= REG_ICASE;
    /* claude.md #107: 'g' means "every match" for .replace(), the same
     * thing it means in JS. POSIX has no cflag for it -- it is not a
     * matching property at all, it is a property of what the caller
     * wants done with the matches -- so it is recorded here and read
     * back by festina_regex_replace. */
    compiled->global = strchr(flags, 'g') != NULL;

    /* claude.md #122: expanded form fed to regcomp; the ORIGINAL
     * pattern in any error message, since that is what the program
     * wrote. regcomp copies what it needs, so the expansion is freed
     * either way. */
    char *expanded = festina_regex_expand_gnu(pattern);
    int rc = regcomp(&compiled->re, expanded, cflags);
    free(expanded);
    if (rc != 0) {
        char errbuf[256];
        regerror(rc, &compiled->re, errbuf, sizeof(errbuf));
        char msg[512];
        snprintf(msg, sizeof(msg), "invalid regex pattern '%s': %s", pattern, errbuf);
        free(raw);
        festina_fail(msg);
    }
    return compiled;
}

/* claude.md #85/#118: the regex counterpart of festina_blob_release --
 * decrement, and only on the last reference regfree() what regcomp()
 * allocated inside the regex_t before freeing the storage. A cached
 * /pattern/ literal's immortal header makes festina_release_check
 * answer 0, so `free` on one is a safe no-op with no flag to consult. */
void festina_regex_free(void *compiled) {
    if (!compiled) return;
    if (!festina_release_check(compiled)) return;
    regfree(&((FestinaRegex *)compiled)->re);
    free((char *)compiled - sizeof(int64_t));
}

/* claude.md #111/#118: marks a compiled regex as the process-lifetime
 * cached form -- called by generated code right after the literal cache
 * is first filled. Sets the standard immortal sentinel, so every
 * retain/release path (including `free` on a binding that aliases the
 * literal) no-ops on it without a special case. */
void festina_regex_mark_cached(void *compiled) {
    if (compiled) *(int64_t *)((char *)compiled - sizeof(int64_t)) = -1;
}

/* claude.md #118: the per-call-site memo for the dynamic regex(pattern,
 * flags) builtin. `slot` is a private [3 x ptr] global codegen emits
 * for each regex() call site: {pattern copy, flags copy, compiled}.
 * The same pattern+flags as last time answers the cached compilation
 * (~24x cheaper than recompiling, measured in api.md); a different
 * pattern releases the slot's reference to the old one and compiles
 * fresh. That release is what the refcount header makes safe: a
 * binding that still aliases the superseded regex keeps it alive, and
 * only the last reference regfrees it -- without the header this
 * eviction was a use-after-free waiting to happen, which is why
 * regex() recompiled per evaluation until now.
 *
 * The caller always receives its own +1 (retained here on a hit, the
 * fresh count on a miss -- where the slot takes an extra retain for
 * itself), so a memoized result is released by exactly the same
 * scope-exit/temporary machinery a festina_regex_compile result always
 * was. The slot's own reference intentionally lives until the pattern
 * changes or the process exits -- the same reachable-until-exit rule
 * the literal cache follows. */
void *festina_regex_compile_memo(const char *pattern, const char *flags,
                                 void **slot) {
    if (!pattern) pattern = "";
    if (!flags) flags = "";
    if (slot[2] && strcmp((const char *)slot[0], pattern) == 0
            && strcmp((const char *)slot[1], flags) == 0) {
        festina_retain(slot[2]);
        return slot[2];
    }
    festina_regex_free(slot[2]);
    free(slot[0]);
    free(slot[1]);
    slot[0] = strdup(pattern);
    slot[1] = strdup(flags);
    if (!slot[0] || !slot[1]) festina_fail("out of memory in regex()");
    void *compiled = festina_regex_compile(pattern, flags);
    slot[2] = compiled;
    festina_retain(compiled);
    return compiled;
}

/* claude.md #116: the regex half of split() -- lives here with the
 * other FestinaRegex consumers; the pieces machinery it uses is up
 * with the text half. */
void *festina_regex_split(void *compiled, const char *s) {
    FestinaPieces p = {NULL, 0, 0};
    if (!s) s = "";
    if (!compiled) {
        festina_pieces_push(&p, s, strlen(s));
        return festina_pieces_finish(&p);
    }
    regex_t *re = &((FestinaRegex *)compiled)->re;
    const char *start = s;    /* start of the piece being accumulated */
    const char *cursor = s;   /* where the next match is searched from */
    for (;;) {
        regmatch_t m;
        int eflags = (cursor == s) ? 0 : REG_NOTBOL;
        if (regexec(re, cursor, 1, &m, eflags) != 0) {
            festina_pieces_push(&p, start, strlen(start));
            break;
        }
        if (m.rm_so == m.rm_eo) {
            /* An empty match splits BETWEEN characters, JS-style:
             * 'abc'.split(/x*<em>/) is ['a','b','c'], with no trailing
             * empty. Emitting the piece behind the cursor and stepping
             * one character forward is both the split and the
             * guarantee of progress. */
            const char *at = cursor + m.rm_so;
            if (at > start) {
                festina_pieces_push(&p, start, (size_t)(at - start));
                start = at;
            }
            if (!*at) break;
            cursor = at + 1;
        } else {
            const char *match_start = cursor + m.rm_so;
            festina_pieces_push(&p, start, (size_t)(match_start - start));
            cursor += m.rm_eo;
            start = cursor;
        }
    }
    if (p.count == 0) festina_pieces_push(&p, s, strlen(s));
    return festina_pieces_finish(&p);
}


int8_t festina_regex_test(void *compiled, const char *text) {
    if (!compiled) return 0;
    if (!text) text = "";
    /* claude.md #107: 'g' is deliberately ignored here. In JS it makes
     * .test() STATEFUL -- a /g regex carries a lastIndex that advances
     * on every call, so the same test against the same string returns
     * true then false -- which is a famous source of bugs and not
     * something worth reproducing. */
    return regexec(&((FestinaRegex *)compiled)->re, text, 0, NULL, 0) == 0;
}

char *festina_regex_match(void *compiled, const char *text) {
    if (!compiled || !text) return NULL;
    regmatch_t m;
    /* claude.md #107: 'g' is ignored here too, for a harder reason --
     * see festina_runtime.h's doc comment. JS's /g makes .match()
     * return an ARRAY instead of a string, and this function's return
     * type cannot depend on a flag that `regex(p, f)` only knows at
     * run time. */
    if (regexec(&((FestinaRegex *)compiled)->re, text, 1, &m, 0) != 0) return NULL;
    regoff_t len = m.rm_eo - m.rm_so;
    char *out = malloc((size_t)len + 1);
    if (!out) festina_fail("out of memory in festina_regex_match");
    memcpy(out, text + m.rm_so, (size_t)len);
    out[len] = '\0';
    return out;
}

/* claude.md #107: no replace_all parameter any more. `.replaceAll()`
 * is gone, and a plain-text search carries no flags, so a text search
 * replaces the first match and nothing else -- exactly what JS's
 * String.prototype.replace does with a string argument. Replacing
 * every occurrence is spelled `/search/g` now. */
char *festina_str_replace(const char *text, const char *search,
                           const char *replacement) {
    if (!text) text = "";
    if (!replacement) replacement = "";
    if (!search || !*search) {
        /* claude.md #68: no match -> return the original value unchanged. */
        return strdup(text);
    }

    size_t search_len = strlen(search);
    size_t replacement_len = strlen(replacement);
    size_t capacity = strlen(text) + replacement_len + 1;
    char *out = malloc(capacity);
    if (!out) festina_fail("out of memory in festina_str_replace");
    size_t out_len = 0;
    const char *cursor = text;
    int did_replace = 0;

    while (1) {
        /* Once a single (non-"All") replacement has happened, treat
         * every further position as "no match" so the rest of `cursor`
         * gets copied through unchanged below. */
        const char *found = did_replace ? NULL : strstr(cursor, search);
        if (!found) break;

        size_t prefix_len = (size_t)(found - cursor);
        size_t needed = out_len + prefix_len + replacement_len + 1;
        if (needed > capacity) {
            while (capacity < needed) capacity *= 2;
            char *grown = realloc(out, capacity);
            if (!grown) festina_fail("out of memory in festina_str_replace");
            out = grown;
        }
        memcpy(out + out_len, cursor, prefix_len);
        out_len += prefix_len;
        memcpy(out + out_len, replacement, replacement_len);
        out_len += replacement_len;

        cursor = found + search_len;
        did_replace = 1;
    }

    size_t rest_len = strlen(cursor);
    size_t needed = out_len + rest_len + 1;
    if (needed > capacity) {
        capacity = needed;
        char *grown = realloc(out, capacity);
        if (!grown) festina_fail("out of memory in festina_str_replace");
        out = grown;
    }
    memcpy(out + out_len, cursor, rest_len + 1);

    return out;
}

/* claude.md #107: how many matches to replace comes from the PATTERN's
 * own 'g' flag now, not from which method was called. */
char *festina_regex_replace(void *compiled, const char *text,
                             const char *replacement) {
    if (!text) text = "";
    if (!replacement) replacement = "";
    if (!compiled) return strdup(text);
    regex_t *re = &((FestinaRegex *)compiled)->re;
    int8_t replace_all = ((FestinaRegex *)compiled)->global;

    size_t replacement_len = strlen(replacement);
    size_t capacity = strlen(text) + replacement_len + 1;
    char *out = malloc(capacity);
    if (!out) festina_fail("out of memory in festina_regex_replace");
    size_t out_len = 0;
    const char *cursor = text;
    int did_replace = 0;

    while (1) {
        regmatch_t m;
        int no_match;
        if (did_replace && !replace_all) {
            no_match = 1;
        } else {
            /* REG_NOTBOL once we're past the true start of the string --
             * otherwise a `^`-anchored pattern would incorrectly match
             * again at the start of *this* remaining substring on every
             * later iteration of a /g replace. */
            int eflags = (cursor == text) ? 0 : REG_NOTBOL;
            no_match = regexec(re, cursor, 1, &m, eflags) != 0;
        }
        if (no_match) break;

        size_t prefix_len = (size_t)m.rm_so;
        size_t match_len = (size_t)(m.rm_eo - m.rm_so);
        size_t needed = out_len + prefix_len + replacement_len + 1;
        if (needed > capacity) {
            while (capacity < needed) capacity *= 2;
            char *grown = realloc(out, capacity);
            if (!grown) festina_fail("out of memory in festina_regex_replace");
            out = grown;
        }
        memcpy(out + out_len, cursor, prefix_len);
        out_len += prefix_len;
        memcpy(out + out_len, replacement, replacement_len);
        out_len += replacement_len;

        const char *match_end = cursor + m.rm_eo;
        did_replace = 1;
        if (match_len > 0) {
            cursor = match_end;
            continue;
        }
        /* Zero-length match (e.g. pattern "x*" matching where there's no
         * 'x') -- without advancing past it, the next regexec call would
         * find the exact same empty match at the exact same position
         * forever. Copy the one byte at match_end through untouched and
         * advance past it, same approach JS/Python's regex replace use
         * for this case. */
        if (*match_end == '\0') {
            cursor = match_end;
            break;
        }
        size_t needed2 = out_len + 2;
        if (needed2 > capacity) {
            capacity = needed2;
            char *grown = realloc(out, capacity);
            if (!grown) festina_fail("out of memory in festina_regex_replace");
            out = grown;
        }
        out[out_len++] = *match_end;
        cursor = match_end + 1;
    }

    size_t rest_len = strlen(cursor);
    size_t needed = out_len + rest_len + 1;
    if (needed > capacity) {
        capacity = needed;
        char *grown = realloc(out, capacity);
        if (!grown) festina_fail("out of memory in festina_regex_replace");
        out = grown;
    }
    memcpy(out + out_len, cursor, rest_len + 1);

    return out;
}

/* ---- setTimeout/setInterval -- claude.md #69. Added because Festina
 * otherwise has no way to schedule work after the fact. See
 * festina_runtime.h's doc comment for the full design (why the
 * callback is a bare function name, how this combines with the
 * graphics event loop, when a program with pending timers actually
 * exits). Pure POSIX (<time.h> only) -- no X11 dependency, so this
 * lives in core regardless of whether a given program also uses
 * graphics; festina_runtime_graphics.c's festina_run_event_loop calls
 * back into festina_next_timer_deadline()/festina_fire_expired_timers()
 * (festina_runtime_internal.h) to stay in sync with this state when
 * both graphics and timers are in use. ---- */

typedef struct {
    int64_t id;
    void (*callback)(void);
    int64_t interval_ms;  /* only meaningful when is_interval */
    int64_t is_interval;
    double next_fire_time; /* CLOCK_MONOTONIC seconds */
    int64_t active;        /* 0 once cleared, or once a one-shot has fired */
} FestinaTimer;

static FestinaTimer *g_timers = NULL;
static int64_t g_timer_count = 0;
static int64_t g_timer_capacity = 0;
static int64_t g_next_timer_id = 1;

double festina_now_seconds(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec + (double)ts.tv_nsec / 1e9;
}

static int64_t festina_add_timer(void (*callback)(void), int64_t delay_ms, int64_t is_interval) {
    if (g_timer_count == g_timer_capacity) {
        /* Compact out cleared/fired-and-done entries before growing --
         * keeps a long-running program that creates+clears many
         * one-shot timeouts over time from growing this array
         * unboundedly. */
        int64_t write = 0;
        for (int64_t read = 0; read < g_timer_count; read++) {
            if (g_timers[read].active) g_timers[write++] = g_timers[read];
        }
        g_timer_count = write;
    }
    if (g_timer_count == g_timer_capacity) {
        g_timer_capacity = g_timer_capacity ? g_timer_capacity * 2 : 8;
        FestinaTimer *grown = realloc(g_timers, (size_t)g_timer_capacity * sizeof(FestinaTimer));
        if (!grown) festina_fail("out of memory growing the timer list");
        g_timers = grown;
    }
    int64_t id = g_next_timer_id++;
    FestinaTimer *t = &g_timers[g_timer_count++];
    t->id = id;
    t->callback = callback;
    t->interval_ms = delay_ms;
    t->is_interval = is_interval;
    t->next_fire_time = festina_now_seconds() + (double)delay_ms / 1000.0;
    t->active = 1;
    return id;
}

int64_t festina_set_timeout(void (*callback)(void), int64_t delay_ms) {
    return festina_add_timer(callback, delay_ms, 0);
}

int64_t festina_set_interval(void (*callback)(void), int64_t delay_ms) {
    return festina_add_timer(callback, delay_ms, 1);
}

static void festina_clear_timer_id(int64_t id) {
    for (int64_t i = 0; i < g_timer_count; i++) {
        if (g_timers[i].id == id) {
            g_timers[i].active = 0;
            return;
        }
    }
    /* Clearing an id that doesn't exist (already fired, already
     * cleared, or never valid) is a silent no-op -- matching
     * clearTimeout()/clearInterval() in JS, which never throw either. */
}

void festina_clear_timeout(int64_t id) { festina_clear_timer_id(id); }
void festina_clear_interval(int64_t id) { festina_clear_timer_id(id); }

/* The earliest next_fire_time among all active timers, or -1.0 if none
 * are active -- festina_run_event_loop (festina_runtime_graphics.c)
 * uses this to bound its select() timeout; festina_run_timer_loop below
 * uses it directly. */
double festina_next_timer_deadline(void) {
    double earliest = -1.0;
    for (int64_t i = 0; i < g_timer_count; i++) {
        if (!g_timers[i].active) continue;
        if (earliest < 0.0 || g_timers[i].next_fire_time < earliest) {
            earliest = g_timers[i].next_fire_time;
        }
    }
    return earliest;
}

/* Fires every timer whose next_fire_time has passed. Indexes into
 * g_timers fresh on every access (never caches a pointer across a
 * callback() call) since a callback is ordinary Festina code and can
 * itself call setTimeout/setInterval (growing/reallocating g_timers)
 * or clearTimeout/clearInterval (deactivating an entry, including its
 * own) -- both need to be safe to do from inside a firing callback. */
void festina_fire_expired_timers(void) {
    double now = festina_now_seconds();
    for (int64_t i = 0; i < g_timer_count; i++) {
        if (!g_timers[i].active || g_timers[i].next_fire_time > now) continue;
        void (*callback)(void) = g_timers[i].callback;
        if (g_timers[i].is_interval) {
            /* Reschedule from *now*, not from the missed deadline, so a
             * slow callback (or a long gap before the process got to
             * run at all) doesn't cause a burst of catch-up calls --
             * "at least this often," not "exactly this often". */
            g_timers[i].next_fire_time = now + (double)g_timers[i].interval_ms / 1000.0;
        } else {
            g_timers[i].active = 0;
        }
        callback();
        now = festina_now_seconds(); /* the callback took real time */
    }
}

/* The blocking loop main() enters (via festina_run_timer_loop, see
 * festina/codegen.py's _emit_main_and_entry) for a program that uses
 * setTimeout/setInterval but never opens a graphics window --
 * festina_run_event_loop (festina_runtime_graphics.c) is the graphics-
 * aware equivalent, sharing this same timer state through
 * festina_next_timer_deadline()/festina_fire_expired_timers() above.
 * Sleeps until the next timer deadline and fires it, forever, until
 * there's truly nothing left to wait for (no active timers) -- matching
 * Node's empty event loop exiting the process. An uncleared
 * setInterval() therefore keeps a graphics-free program running
 * forever, exactly like in a real JS runtime; it needs to be stopped
 * externally (or via clearInterval()) the same way. */
void festina_run_timer_loop(void) {
    while (1) {
        double earliest = festina_next_timer_deadline();
        if (earliest < 0.0) return; /* nothing left to wait for */
        double remaining = earliest - festina_now_seconds();
        if (remaining > 0.0) {
            struct timespec ts;
            ts.tv_sec = (time_t)remaining;
            ts.tv_nsec = (long)((remaining - (double)ts.tv_sec) * 1e9);
            nanosleep(&ts, NULL);
        }
        festina_fire_expired_timers();
    }
}

/* ---- reference counting -- claude.md #77 ---- */

/* claude.md #74/#75/#76 (festina/escape_analysis.py) prove, from
 * syntax alone, that some struct/arr[T]/map[T] values never outlive
 * their declaring function -- those get freed outright (or, for a
 * struct specifically, stack-allocated and never freed at all -- see
 * codegen.py's own module docstring). A value proven to genuinely
 * escape (stored into a global, returned, ...) has nothing for that
 * kind of analysis to do; it's safe by construction to prove it MUST
 * be freed, never to prove it's safe TO free, since something else
 * might still be using it. Reference counting is the answer for that
 * remainder: track how many live Festina-visible bindings currently
 * reference a value, and only actually free it when that count reaches
 * zero.
 *
 * This works completely for Festina specifically because reference
 * cycles are not just rare here, they are structurally impossible:
 * a struct field's type, and an arr[T]/map[T]'s own element type T,
 * must always be a type declared *before* the struct/array/map
 * containing it (the same "no forward references" rule semantics.py
 * already enforces for functions -- verified directly: `struct Node {
 * next:Node }`, and even two mutually-referencing structs declared in
 * either order, both fail to compile with "unknown type"). So the set
 * of types any given value could ever transitively reference, through
 * its own fields/elements, is always a strict subset of the types
 * declared earlier in the same program -- a DAG by construction, never
 * a cycle -- meaning plain reference counting, with no cycle detector
 * or tracing collector, is a *complete* answer here, not the usual
 * "handles everything except cycles" partial one.
 *
 * Layout: every refcounted value's allocation has a single int64_t
 * refcount immediately before the pointer Festina code actually sees
 * (`payload` below) -- a fixed 8-byte offset regardless of the value's
 * own type, since every Festina field type (int/float/bool/text/blob/
 * struct/table/image/audio/regex all lower to i64/double/i8/ptr -- see
 * codegen.py's _llvm_type) has natural alignment no greater than 8
 * bytes, so placing an 8-byte header immediately before a value's own
 * fields never needs extra padding. See codegen.py's own VarDecl/
 * StructType handling (a local) and _global_var_defs (a global) for
 * where this header actually gets allocated/initialized.
 *
 * A NEGATIVE refcount is a sentinel for "immortal, retain/release are
 * always a no-op" -- used for a struct-typed global's own untouched
 * static initial storage (see _global_var_defs), which was never
 * heap-allocated in the first place and must never reach free(). This
 * means codegen never needs to special-case a global's first-ever
 * reassignment (from that static storage to a real heap value): both
 * functions are always safe to call unconditionally, whatever the
 * pointer they're given currently points to. */
void festina_retain(void *payload) {
    if (!payload) return;
    int64_t *header = (int64_t *)((char *)payload - sizeof(int64_t));
    if (*header < 0) return;
    (*header)++;
}

/* claude.md #78: the decrement-and-check half of festina_release,
 * split out so codegen can cascade into releasing a struct's own
 * struct-typed field(s) BEFORE actually freeing its storage, something
 * only the compiler (not this generic, type-blind runtime) knows how
 * to do -- see festina/codegen.py's _release_fn_for_struct. Returns 1
 * (the caller should now free `payload`, after releasing whatever it
 * itself needs to release first) or 0 (nothing further to do: null,
 * immortal, or still referenced elsewhere), the same three outcomes
 * festina_release's own null/sentinel/nonzero-refcount checks already
 * distinguish -- this only defers the actual free() call to the
 * caller instead of performing it here. */
int8_t festina_release_check(void *payload) {
    if (!payload) return 0;
    int64_t *header = (int64_t *)((char *)payload - sizeof(int64_t));
    if (*header < 0) return 0;
    (*header)--;
    return *header == 0;
}

void festina_release(void *payload) {
    if (!payload) return;
    if (festina_release_check(payload)) {
        free((char *)payload - sizeof(int64_t));
    }
}


/* ---- maps -- claude.md #72 ---- */

/* One key/value pair -- `value` is a raw 8-byte payload meaning
 * whatever the compiled program's own map[T] says it means (int64_t
 * bits, a double's raw bits, or a pointer -- see festina/codegen.py's
 * _map_value_to_i64/_i64_to_map_value for the reinterpretation, done
 * entirely on the LLVM IR side, since this runtime has no idea what T
 * a given map's values are, only ever seeing the already-flattened i64
 * payload). The same "one fixed-size slot per value" convention
 * festina_sqlite_collect_rows's row layout and every arr[T]'s own
 * per-element storage already use. */
typedef struct {
    char *key;      /* owned copy -- see festina_map_set's own comment */
    int64_t value;
} FestinaMapEntry;

/* Linear scan, not a hash table -- maps in Festina are meant for small,
 * game/config-shaped key sets (see claude.md #72's own worked example:
 * a handful of NPC health/name entries), and this runtime already
 * favors simple, obviously-correct implementations over algorithmic
 * sophistication elsewhere too (arr[T] itself has no hashing or
 * ordered structure either) -- a deliberate, documented tradeoff
 * (claude.md #54's ambiguity rule: correctness over micro-optimizing
 * something the spec doesn't ask for), not an oversight. A map with a
 * genuinely large number of entries would see O(n) get/set cost;
 * revisit if that becomes a real problem for a real program. */
static FestinaMapEntry *festina_map_find(int64_t count, void *entries, const char *key) {
    FestinaMapEntry *arr = (FestinaMapEntry *)entries;
    for (int64_t i = 0; i < count; i++) {
        if (festina_str_eq(arr[i].key, key)) return &arr[i];
    }
    return NULL;
}

/* claude.md #72: npcHealths['npc1'] = 30 -- and the equivalent
 * per-entry calls a map literal itself builds out of (see
 * festina/codegen.py's _emit_map_lit). `count`/`entries` point INTO
 * the map value's own storage (its LLVM alloca/global slot, or --
 * during literal construction -- a scratch header alloca; see
 * _emit_map_set's own comment), not a separate map "object" -- updating
 * an existing key never needs to touch them, but adding a new one may
 * need to grow the backing array, which has to write the new
 * count/entries back into that same slot for the change to actually be
 * visible to the caller (the same "write results back through an
 * out-pointer" shape festina_sqlite_collect_rows already uses for its
 * own row array).
 *
 * Grows by exactly one entry (a realloc to count+1) rather than
 * doubling capacity -- no separate capacity field is tracked anywhere
 * (this function is otherwise stateless between calls), and maps are
 * expected to stay small (see festina_map_find's own comment), so the
 * extra reallocs a doubling strategy would avoid aren't a real cost in
 * practice; tracking one fewer field is worth more here than the
 * micro-optimization would be. */
void festina_map_set(int64_t *count, void **entries, const char *key, int64_t value) {
    if (!key) key = "";
    FestinaMapEntry *found = festina_map_find(*count, *entries, key);
    if (found) {
        found->value = value;
        return;
    }
    FestinaMapEntry *grown = realloc(*entries, (size_t)(*count + 1) * sizeof(FestinaMapEntry));
    if (!grown) festina_fail("out of memory growing a map");
    /* Copied, not aliased to the caller's own key pointer -- this map
     * may outlive whatever Festina value `key` came from (a local
     * variable going out of scope doesn't free anything in this
     * runtime -- see the module docstring's "no GC yet" note -- but
     * relying on that would be fragile and unclear to read), and a
     * literal key expression's string constant could in principle be
     * reused/interned differently by a future compiler change. Leaks,
     * like every other heap allocation in this runtime -- the same
     * accepted tradeoff, not a new one. */
    grown[*count].key = strdup(key);
    if (!grown[*count].key) festina_fail("out of memory growing a map");
    grown[*count].value = value;
    *entries = grown;
    (*count)++;
}

/* claude.md #111: `delete m.key` / `delete m['key']` -- remove the
 * entry outright, JS-style, rather than setting it to null: a deleted
 * key stops existing (forEach no longer visits it, count drops), which
 * null could never express. `release` is the same per-value-type
 * trampoline festina_map_for_each already uses for whole-map release
 * (codegen's _emit_map_value_release_trampoline), or NULL for a value
 * type with nothing to release. The hole is closed by shifting the
 * tail down one slot -- keeping entry order, which forEach's
 * unspecified-order contract doesn't require but which costs the same
 * as the swap-with-last alternative at these sizes and never surprises
 * anyone. Returns whether the key existed; deleting a missing key is a
 * safe no-op, exactly like JS. */
int8_t festina_map_delete(int64_t *count, void **entries, const char *key,
                          void (*release)(int64_t, const char *)) {
    if (!key) key = "";
    FestinaMapEntry *arr = (FestinaMapEntry *)*entries;
    for (int64_t i = 0; i < *count; i++) {
        if (!festina_str_eq(arr[i].key, key)) continue;
        /* claude.md #120: the entry is REMOVED before its value is
         * released. The release may run a cycle trial that traverses
         * this very map, and an entry still pointing at a value whose
         * count the release just dropped would be double-counted by
         * markGray -- the same store-before-release rule every field
         * write follows now (see codegen's _emit_assign). */
        int64_t value = arr[i].value;
        char *owned_key = arr[i].key;
        memmove(&arr[i], &arr[i + 1],
                (size_t)(*count - i - 1) * sizeof(FestinaMapEntry));
        (*count)--;
        if (release) release(value, owned_key);
        free(owned_key);
        return 1;
    }
    return 0;
}

/* claude.md #72: npcHealths['npc1'] -- "if the key is not present, the
 * result is null." default_value is already the correct null
 * representation for this map's value type, computed by codegen (see
 * _map_missing_default) -- this function has no idea what T is, only
 * ever seeing raw i64 payloads, so it can't make that choice itself. */
int64_t festina_map_get(int64_t count, void *entries, const char *key, int64_t default_value) {
    if (!key) key = "";
    FestinaMapEntry *found = festina_map_find(count, entries, key);
    return found ? found->value : default_value;
}

/* claude.md #72: npcHealths.forEach(callback). "The order entries are
 * visited in is not specified" -- this iterates in insertion order
 * simply because that's how the backing array happens to be laid out,
 * not a guarantee being made deliberately; nothing here should be
 * relied on beyond every current entry being visited exactly once.
 * `count` is captured once, by the caller, before this loop starts --
 * if `callback` itself mutates this same map (adds a key, changes an
 * existing value) or calls .forEach() again, that's explicitly
 * unspecified behavior here, unlike festina_fire_expired_timers (which
 * *is* deliberately hardened against a timer callback growing/clearing
 * the timer list mid-iteration) -- claude.md #72 was never asked to
 * make that same guarantee for maps. */
void festina_map_for_each(int64_t count, void *entries, void (*callback)(int64_t, const char *)) {
    FestinaMapEntry *arr = (FestinaMapEntry *)entries;
    for (int64_t i = 0; i < count; i++) {
        callback(arr[i].value, arr[i].key);
    }
}

/* claude.md #74/#75: see this function's own declaration in
 * festina_runtime.h. Frees what festina_map_set's own comment already
 * establishes is exclusively owned by each entry -- a strdup'd copy of
 * the key, never aliased with any other Festina-visible value -- before
 * freeing the entries buffer itself. This is the one piece of stage
 * 1/2's own remaining coverage gap (claude.md #74's "This stage does
 * not yet analyze" list) that's actually safe to close without any new
 * aliasing reasoning: unlike a struct/array/map VALUE stored into
 * another value's field (which may still be reachable through the
 * variable it came from -- see codegen.py's own note on why THAT case
 * is deliberately not attempted yet), a map entry's key was never a
 * Festina value at all -- just a private byte-for-byte copy this
 * runtime made for its own internal bookkeeping the moment the entry
 * was created. */
void festina_map_free_entries(int64_t count, void *entries) {
    FestinaMapEntry *arr = (FestinaMapEntry *)entries;
    for (int64_t i = 0; i < count; i++) {
        free(arr[i].key);
    }
    free(entries);
}

/* ---- reference counting for arr[T]/map[T] -- claude.md #79 ---- */

/* claude.md #79: an arr[T]/map[T] value is now, like a struct value
 * (claude.md #77), a single `ptr` to a heap-allocated header carrying
 * its own i64 refcount immediately before the payload -- the same
 * layout and the same festina_retain/festina_release_check this file's
 * own "reference counting" section already established, reused
 * unchanged (retaining an array/map needs no type-specific logic at
 * all: `festina_retain` just increments a refcount, regardless of what
 * the payload past it actually is). Only RELEASING needs an
 * array/map-specific function, since -- unlike festina_release, which
 * assumes there's nothing further to free once the refcount hits zero
 * -- an arr[T]/map[T]'s own payload holds a *second* allocation (its
 * data/entries buffer) that also needs freeing at that point.
 *
 * Unlike a struct (whose own field layout varies per Festina struct
 * type, needing the compiler's own per-type knowledge -- see
 * codegen.py's _release_fn_for_struct), every arr[T]/map[T]'s header
 * has the identical two-field shape regardless of T (FESTINA_ARRAY_LLVM_TYPE/
 * FESTINA_MAP_LLVM_TYPE's own `{i64, ptr}`), so a single generic
 * function handles every arr[T] and a single generic function handles
 * every map[T] -- no per-type codegen-generated wrapper needed here at
 * all, unlike the struct case. */
/* ---- claude.md #96: array methods ----
 *
 * The header layout is the one festina/codegen.py's own
 * FESTINA_ARRAY_LLVM_TYPE describes -- {length, data} -- shared here
 * the same way the sqlite row layout already is, since these functions
 * have to resize a buffer codegen allocated.
 *
 * Values move by BYTES, with the element size passed in: codegen knows
 * it at compile time for every arr[T], so one set of functions covers
 * every element type instead of a family per type. Ownership of an
 * element being removed transfers to whoever receives it (pop/shift
 * hand it back, splice hands it to the returned array), which is why
 * nothing here releases anything -- doing so would free a value the
 * caller is about to be handed.
 *
 * Growth is a plain realloc per push rather than geometric
 * over-allocation, which would need a capacity field in the header. In
 * practice glibc extends in place for a growing buffer most of the
 * time, and the lists this is for (entities in a scene, rows being
 * accumulated) are small; if that ever stops being true, adding
 * capacity is an additive change to the header, not a redesign.
 */
typedef struct {
    int64_t length;
    void *data;
} FestinaArrayHeader;

static void festina_array_resize(FestinaArrayHeader *a, int64_t elem_size,
                                  int64_t new_length) {
    if (new_length <= 0) {
        free(a->data);
        a->data = NULL;
        a->length = 0;
        return;
    }
    void *grown = realloc(a->data, (size_t)(new_length * elem_size));
    if (!grown) festina_fail("out of memory growing an array");
    a->data = grown;
    a->length = new_length;
}

void festina_array_push(void *hdr, int64_t elem_size, const void *value) {
    FestinaArrayHeader *a = (FestinaArrayHeader *)hdr;
    if (!a || !value) return;
    int64_t at = a->length;
    festina_array_resize(a, elem_size, at + 1);
    memcpy((char *)a->data + at * elem_size, value, (size_t)elem_size);
}

void festina_array_unshift(void *hdr, int64_t elem_size, const void *value) {
    FestinaArrayHeader *a = (FestinaArrayHeader *)hdr;
    if (!a || !value) return;
    int64_t was = a->length;
    festina_array_resize(a, elem_size, was + 1);
    if (was > 0) {
        memmove((char *)a->data + elem_size, a->data, (size_t)(was * elem_size));
    }
    memcpy(a->data, value, (size_t)elem_size);
}

/* pop/shift leave *out untouched when there is nothing to remove --
 * codegen has already stored the element type's own null there, so an
 * empty pop() answers null rather than needing a second return value. */
int8_t festina_array_pop(void *hdr, int64_t elem_size, void *out) {
    FestinaArrayHeader *a = (FestinaArrayHeader *)hdr;
    if (!a || a->length <= 0) return 0;
    memcpy(out, (char *)a->data + (a->length - 1) * elem_size, (size_t)elem_size);
    festina_array_resize(a, elem_size, a->length - 1);
    return 1;
}

int8_t festina_array_shift(void *hdr, int64_t elem_size, void *out) {
    FestinaArrayHeader *a = (FestinaArrayHeader *)hdr;
    if (!a || a->length <= 0) return 0;
    memcpy(out, a->data, (size_t)elem_size);
    int64_t rest = a->length - 1;
    if (rest > 0) {
        memmove(a->data, (char *)a->data + elem_size, (size_t)(rest * elem_size));
    }
    festina_array_resize(a, elem_size, rest);
    return 1;
}

/* JavaScript's own clamping, deliberately: a negative start counts back
 * from the end, everything out of range clamps rather than failing, and
 * a start past the end removes nothing. Anything stricter would make
 * the common `splice(i, 1)` inside a loop a source of crashes at the
 * boundaries instead of a no-op. `dst` is a header codegen already
 * allocated for the result. */
void festina_array_splice(void *hdr, int64_t elem_size, int64_t start,
                           int64_t count, void *dst_hdr) {
    FestinaArrayHeader *a = (FestinaArrayHeader *)hdr;
    FestinaArrayHeader *dst = (FestinaArrayHeader *)dst_hdr;
    if (dst) { dst->length = 0; dst->data = NULL; }
    if (!a || a->length <= 0) return;

    int64_t len = a->length;
    if (start < 0) {
        start = len + start;
        if (start < 0) start = 0;
    }
    if (start > len) start = len;
    if (count < 0) count = 0;
    if (start + count > len) count = len - start;
    if (count == 0) return;

    if (dst) {
        dst->data = malloc((size_t)(count * elem_size));
        if (!dst->data) festina_fail("out of memory in splice()");
        memcpy(dst->data, (char *)a->data + start * elem_size,
               (size_t)(count * elem_size));
        dst->length = count;
    }
    int64_t tail = len - (start + count);
    if (tail > 0) {
        memmove((char *)a->data + start * elem_size,
                (char *)a->data + (start + count) * elem_size,
                (size_t)(tail * elem_size));
    }
    festina_array_resize(a, elem_size, len - count);
}

/* claude.md #97: indexOf -- the first index holding `value`, or -1.
 *
 * -1 rather than null because the answer is an INDEX, and every use of
 * it is a comparison or a splice argument: `if xs.indexOf(v) >= 0` and
 * `xs.splice(xs.indexOf(v), 1)` both read naturally, where a null index
 * would have to be tested separately before it could be used at all.
 * It is also what JavaScript's own indexOf answers, which is the
 * convention this language's array methods already follow.
 *
 * Comparison is by the element's raw 8-byte slot, which is exactly
 * right for int/float/bool and for identity on struct/arr[T]/map[T]
 * (two bindings naming one value share its pointer -- claude.md #79).
 * `text` is the one type where that is wrong, since equal strings are
 * usually different buffers, so codegen passes is_text and this
 * compares with strcmp instead. */
int64_t festina_array_index_of(void *hdr, int64_t elem_size,
                                const void *value, int8_t is_text) {
    FestinaArrayHeader *a = (FestinaArrayHeader *)hdr;
    if (!a || !value || a->length <= 0 || !a->data) return -1;
    for (int64_t i = 0; i < a->length; i++) {
        const char *slot = (const char *)a->data + i * elem_size;
        if (is_text) {
            const char *have = *(const char *const *)slot;
            const char *want = *(const char *const *)value;
            if (have == want) return i;              /* both null, or same buffer */
            if (have && want && strcmp(have, want) == 0) return i;
        } else if (memcmp(slot, value, (size_t)elem_size) == 0) {
            return i;
        }
    }
    return -1;
}

void festina_release_array(void *payload) {
    if (!festina_release_check(payload)) return;
    /* payload is {i64 length, ptr data} -- skip past the i64 to reach
     * the data pointer, the one thing actually worth freeing here (the
     * length is just a plain number, nothing to release). Elements
     * that are themselves refcounted values (a struct-typed element,
     * say) are not individually released here -- see todo.md on why
     * that's still a separate, open gap this section doesn't close. */
    void *data = *(void **)((char *)payload + sizeof(int64_t));
    free(data);
    free((char *)payload - sizeof(int64_t));
}

void festina_release_map(void *payload) {
    if (!festina_release_check(payload)) return;
    /* payload is {i64 count, ptr entries} -- festina_map_free_entries
     * already does exactly the right thing for the data half (each
     * entry's own strdup'd key, then the entries buffer itself -- see
     * its own comment just above), so this only adds the new header
     * free on top. Same "map values aren't individually released"
     * scope limitation as festina_release_array above. */
    int64_t count = *(int64_t *)payload;
    void *entries = *(void **)((char *)payload + sizeof(int64_t));
    festina_map_free_entries(count, entries);
    free((char *)payload - sizeof(int64_t));
}

/* ---- cycle collection -- claude.md #120 ----
 *
 * Reference counting cannot free a cycle (`a.next = a` holds itself at
 * count 1 forever), so releases of values whose TYPE can participate in
 * a cycle run a synchronous trial deletion (the classic Bacon-Rajan
 * test, single-rooted): tentatively remove every reference internal to
 * the subgraph (markGray), see which nodes still have references from
 * outside it (scan restores those and everything they reach --
 * scanBlack), and free what nothing external reaches (collectWhite).
 * The compiler generates the per-type traversal functions -- only it
 * knows a struct's field layout -- and they drive the small,
 * type-blind state helpers here.
 *
 * The trial's color state lives in bits 61-62 of the same i64 header
 * the refcount occupies (black=0, gray=1, white=2): outside a trial
 * every header is a plain count (black), and a trial always ends with
 * every surviving node black again, so festina_retain /
 * festina_release_check never need masking. The count occupies the low
 * 61 bits during a trial; markGray's decrements can never underflow
 * into the color bits, because a node's internal in-edges never exceed
 * its count (each is a counted reference). A NEGATIVE header is the
 * immortal sentinel exactly as everywhere else: an immortal value is
 * never colored, decremented, traversed, or freed -- anything an
 * immortal anchors is reachable by definition, and every helper here
 * checks for it before touching anything. */

#define FESTINA_COLOR_SHIFT 61
#define FESTINA_COLOR_MASK (3LL << FESTINA_COLOR_SHIFT)
#define FESTINA_COUNT_MASK (~FESTINA_COLOR_MASK)
#define FESTINA_GRAY 1LL
#define FESTINA_WHITE 2LL

static int64_t *festina_cycle_header(void *p) {
    return (int64_t *)((char *)p - sizeof(int64_t));
}

/* Whether a just-released-but-still-referenced value should be tried
 * as a cycle root: non-null with a positive header (positive rules out
 * both immortal and colored -- outside a trial, color bits are 0). */
int8_t festina_cycle_candidate(void *p) {
    if (!p) return 0;
    return *festina_cycle_header(p) > 0;
}

/* markGray's node half: claim the node for the gray traversal. The
 * caller (generated code) then decrements and grays each child edge --
 * exactly once per parent, which with the once-per-node claim here is
 * what bounds the walk on a cyclic graph. */
int8_t festina_cycle_begin_gray(void *p) {
    if (!p) return 0;
    int64_t *h = festina_cycle_header(p);
    if (*h < 0) return 0;
    if (((*h & FESTINA_COLOR_MASK) >> FESTINA_COLOR_SHIFT) == FESTINA_GRAY) return 0;
    *h = (*h & FESTINA_COUNT_MASK) | (FESTINA_GRAY << FESTINA_COLOR_SHIFT);
    return 1;
}

/* markGray's edge half: tentatively remove one internal reference. */
void festina_cycle_dec(void *p) {
    if (!p) return;
    int64_t *h = festina_cycle_header(p);
    if (*h < 0) return;
    (*h)--;
}

/* scanBlack's edge half: restore one tentatively-removed reference. */
void festina_cycle_inc(void *p) {
    if (!p) return;
    int64_t *h = festina_cycle_header(p);
    if (*h < 0) return;
    (*h)++;
}

/* scan's node decision. 0: nothing to do here (null, immortal, or not
 * gray -- already decided). 1: external references remain, the caller
 * must scanBlack from this node. 2: no external references -- the node
 * is tentatively garbage (now white); the caller scans its children. */
int64_t festina_cycle_begin_scan(void *p) {
    if (!p) return 0;
    int64_t *h = festina_cycle_header(p);
    if (*h < 0) return 0;
    if (((*h & FESTINA_COLOR_MASK) >> FESTINA_COLOR_SHIFT) != FESTINA_GRAY) return 0;
    if ((*h & FESTINA_COUNT_MASK) > 0) return 1;
    *h = (*h & FESTINA_COUNT_MASK) | (FESTINA_WHITE << FESTINA_COLOR_SHIFT);
    return 2;
}

void festina_cycle_set_black(void *p) {
    if (!p) return;
    int64_t *h = festina_cycle_header(p);
    if (*h < 0) return;
    *h &= FESTINA_COUNT_MASK;
}

/* scanBlack's recursion guard: a child that is not yet black still
 * needs its own subtree's counts restored. */
int8_t festina_cycle_needs_black(void *p) {
    if (!p) return 0;
    int64_t h = *festina_cycle_header(p);
    if (h < 0) return 0;
    return (h & FESTINA_COLOR_MASK) != 0;
}

/* collectWhite's node claim: only a white node is freed, and it is
 * recolored black first so a cyclic graph frees each node exactly
 * once. */
int8_t festina_cycle_begin_white(void *p) {
    if (!p) return 0;
    int64_t *h = festina_cycle_header(p);
    if (*h < 0) return 0;
    if (((*h & FESTINA_COLOR_MASK) >> FESTINA_COLOR_SHIFT) != FESTINA_WHITE) return 0;
    *h &= FESTINA_COUNT_MASK;
    return 1;
}

/* The container traversal loops, type-blind: hand every element/value
 * pointer of an arr[T]/map[T]-of-managed-T to the generated per-type
 * edge function. */
void festina_cycle_visit_array(void *payload, void (*fn)(void *)) {
    int64_t length = *(int64_t *)payload;
    void **data = *(void ***)((char *)payload + sizeof(int64_t));
    for (int64_t i = 0; i < length; i++) fn(data[i]);
}

void festina_cycle_visit_map(void *payload, void (*fn)(void *)) {
    int64_t count = *(int64_t *)payload;
    FestinaMapEntry *entries = *(FestinaMapEntry **)((char *)payload + sizeof(int64_t));
    for (int64_t i = 0; i < count; i++) fn((void *)(intptr_t)entries[i].value);
}

/* collectWhite's container disposal: free the container's own storage
 * WITHOUT releasing its elements -- markGray already removed those
 * counts, and collectWhite's own recursion frees whichever of them are
 * garbage. Mirrors festina_release_array/_map's free logic minus the
 * refcount check the trial has already superseded. */
void festina_cycle_dispose_array(void *payload) {
    void *data = *(void **)((char *)payload + sizeof(int64_t));
    free(data);
    free((char *)payload - sizeof(int64_t));
}

void festina_cycle_dispose_map(void *payload) {
    int64_t count = *(int64_t *)payload;
    FestinaMapEntry *entries = *(FestinaMapEntry **)((char *)payload + sizeof(int64_t));
    for (int64_t i = 0; i < count; i++) free(entries[i].key);
    free(entries);
    free((char *)payload - sizeof(int64_t));
}
