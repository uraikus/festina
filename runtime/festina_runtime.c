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
void festina_log_bool(int8_t v) { printf("%s\n", v ? "true" : "false"); }
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
    return strdup(v ? "true" : "false");
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
    return db;
}

/* claude.md #30 */
static const char *festina_sql_type(const char *festina_type) {
    if (strcmp(festina_type, "int") == 0) return "INTEGER";
    if (strcmp(festina_type, "float") == 0) return "REAL";
    if (strcmp(festina_type, "bool") == 0) return "INTEGER";
    if (strcmp(festina_type, "text") == 0) return "TEXT";
    if (strcmp(festina_type, "blob") == 0) return "BLOB";
    return "TEXT";
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
    sqlite3_finalize(stmt);
    return out;
}

double festina_sqlite_scalar_float(sqlite3_stmt *stmt) {
    double out = festina_null_float();
    if (sqlite3_step(stmt) == SQLITE_ROW
            && sqlite3_column_type(stmt, 0) != SQLITE_NULL) {
        out = sqlite3_column_double(stmt, 0);
    }
    sqlite3_finalize(stmt);
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
    sqlite3_finalize(stmt);
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
        sqlite3_finalize(stmt);
        festina_fail(msg);
    }
    sqlite3_finalize(stmt);
}

/* claude.md #34: row layout is col_count 8-byte slots per row -- see
 * this function's doc comment in festina_runtime.h for the full
 * rationale (matches how festina/codegen.py reads a row back). */
void festina_sqlite_collect_rows(sqlite3_stmt *stmt, int32_t col_count,
                                  const char **col_types,
                                  int64_t *out_length, void **out_data) {
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

        int64_t *row = malloc(col_count * sizeof(int64_t));
        if (!row) festina_fail("out of memory in festina_sqlite_collect_rows");

        for (int32_t c = 0; c < col_count; c++) {
            const char *t = col_types[c];
            int is_null = sqlite3_column_type(stmt, c) == SQLITE_NULL;
            if (strcmp(t, "float") == 0) {
                double d = is_null ? festina_null_float() : sqlite3_column_double(stmt, c);
                memcpy(&row[c], &d, sizeof(double));
            } else if (strcmp(t, "text") == 0 || strcmp(t, "blob") == 0) {
                char *copy = NULL;
                if (!is_null) {
                    const unsigned char *txt = sqlite3_column_text(stmt, c);
                    copy = strdup(txt ? (const char *)txt : "");
                }
                memcpy(&row[c], &copy, sizeof(char *));
            } else {
                /* int, bool -- claude.md #30 maps bool to SQLite INTEGER too */
                row[c] = is_null ? festina_null_int() : sqlite3_column_int64(stmt, c);
            }
        }

        rows[count++] = row;
    }

    if (rc != SQLITE_DONE) {
        sqlite3 *db = sqlite3_db_handle(stmt);
        char msg[512];
        snprintf(msg, sizeof(msg), "sqlite error reading rows: %s", sqlite3_errmsg(db));
        sqlite3_finalize(stmt);
        festina_fail(msg);
    }
    sqlite3_finalize(stmt);

    *out_length = count;
    *out_data = rows;
}

/* ---- regex(), .test(), .match(), .replace()/.replaceAll() -- claude.md #67-68 ---- */

void *festina_regex_compile(const char *pattern, const char *flags) {
    if (!pattern) pattern = "";
    if (!flags) flags = "";
    regex_t *compiled = malloc(sizeof(regex_t));
    if (!compiled) festina_fail("out of memory in festina_regex_compile");

    int cflags = REG_EXTENDED;
    if (strchr(flags, 'i')) cflags |= REG_ICASE;

    int rc = regcomp(compiled, pattern, cflags);
    if (rc != 0) {
        char errbuf[256];
        regerror(rc, compiled, errbuf, sizeof(errbuf));
        char msg[512];
        snprintf(msg, sizeof(msg), "invalid regex pattern '%s': %s", pattern, errbuf);
        free(compiled);
        festina_fail(msg);
    }
    return compiled;
}

/* claude.md #85: releases a regex_t compiled by festina_regex_compile.
 * Only ever called for a regex produced by a runtime `regex(...)` call
 * and consumed as a temporary in the same expression -- a /pattern/
 * literal is compiled once and cached for the life of the process (see
 * _emit_cached_regex_lit), so it is deliberately never freed. regfree()
 * releases what regcomp() allocated INSIDE the regex_t; the regex_t
 * itself was a separate malloc and needs its own free(). */
void festina_regex_free(void *compiled) {
    if (!compiled) return;
    regfree((regex_t *)compiled);
    free(compiled);
}

int8_t festina_regex_test(void *compiled, const char *text) {
    if (!compiled) return 0;
    if (!text) text = "";
    return regexec((regex_t *)compiled, text, 0, NULL, 0) == 0;
}

char *festina_regex_match(void *compiled, const char *text) {
    if (!compiled || !text) return NULL;
    regmatch_t m;
    if (regexec((regex_t *)compiled, text, 1, &m, 0) != 0) return NULL;
    regoff_t len = m.rm_eo - m.rm_so;
    char *out = malloc((size_t)len + 1);
    if (!out) festina_fail("out of memory in festina_regex_match");
    memcpy(out, text + m.rm_so, (size_t)len);
    out[len] = '\0';
    return out;
}

char *festina_str_replace(const char *text, const char *search,
                           const char *replacement, int8_t replace_all) {
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
        const char *found = (did_replace && !replace_all) ? NULL : strstr(cursor, search);
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

char *festina_regex_replace(void *compiled, const char *text,
                             const char *replacement, int8_t replace_all) {
    if (!text) text = "";
    if (!replacement) replacement = "";
    if (!compiled) return strdup(text);
    regex_t *re = (regex_t *)compiled;

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
             * later iteration of replaceAll. */
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
