/*
 * Festina native runtime -- claude.md #41 (log), #42 (fail), #45 (string
 * interpolation), #29-31 (automatic SQLite database + schema sync),
 * #67-68 (regex, string match/replace), #37/#39/#40 (img, graphics,
 * click/mouse/key/resize/close events), #69 (setTimeout/setInterval),
 * #38 (aud, loadAudio(), .play()/.stop()/.isPlaying()).
 *
 * This is a from-scratch runtime for the statically typed Festina
 * language.
 */
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>       /* clock_gettime/nanosleep -- setTimeout/setInterval */
#include <sys/select.h> /* select() -- multiplexes X11 events with timers */
#include <pthread.h>    /* background audio playback -- claude.md #38 */
#include <alsa/asoundlib.h> /* audio playback -- claude.md #38 */
#include <X11/Xlib.h>
#include <X11/Xutil.h> /* XLookupString/XKeysymToString -- `on key` */
#include <cairo/cairo-xlib.h>
#include "festina_runtime.h"

/* ---- log() / fail() -- claude.md #41, #42 ---- */

void festina_log_int(int64_t v) { printf("%lld\n", (long long)v); }
void festina_log_float(double v) { printf("%g\n", v); }
void festina_log_bool(int8_t v) { printf("%s\n", v ? "true" : "false"); }
void festina_log_text(const char *v) { printf("%s\n", v ? v : ""); }

void festina_fail(const char *msg) {
    fprintf(stderr, "fail: %s\n", msg ? msg : "");
    exit(1);
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

sqlite3 *festina_db_open(void) {
    sqlite3 *db = NULL;
    /* claude.md #29: always festina.sqlite, opened/created automatically. */
    int rc = sqlite3_open("festina.sqlite", &db);
    if (rc != SQLITE_OK) {
        char msg[256];
        snprintf(msg, sizeof(msg), "cannot open festina.sqlite: %s",
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

/* ---- graphics -- claude.md #37, #39, #40 (see festina_runtime.h's doc comment) ---- */

/* Motif WM hints -- the widely-honored (if not core-protocol) X11
 * convention for requesting a window with no title bar/border/menu. */
typedef struct {
    unsigned long flags, functions, decorations;
    long input_mode;
    unsigned long status;
} FestinaMotifWmHints;

static Display *g_display = NULL;
static Window g_window;
static Atom g_wm_delete_atom;
static cairo_surface_t *g_window_surface = NULL;
static cairo_surface_t *g_backing_surface = NULL;
static void (*g_click_handler)(int64_t, int64_t) = NULL;
static void (*g_mouse_handler)(int64_t, int64_t) = NULL;
static void (*g_key_handler)(const char *) = NULL;
static void (*g_resize_handler)(void) = NULL;
static void (*g_close_handler)(void) = NULL;
/* The canvas's *current* size -- starts at FESTINA_CANVAS_WIDTH/HEIGHT
 * but tracks the window's real size after an `on resize`-triggering
 * ConfigureNotify (see festina_handle_graphics_event);
 * festina_client_width/_height read these, not the compile-time
 * constants. */
static int64_t g_canvas_width = FESTINA_CANVAS_WIDTH;
static int64_t g_canvas_height = FESTINA_CANVAS_HEIGHT;

static void festina_graphics_require_init(void) {
    if (!g_display) {
        festina_fail("a graphics function was called but the canvas window "
                      "was never created (internal compiler error)");
    }
}

void festina_graphics_init(void) {
    g_display = XOpenDisplay(NULL);
    if (!g_display) {
        festina_fail("could not open the X display -- claude.md #39's graphics "
                      "functions need a running X server (is $DISPLAY set?)");
    }

    int screen = DefaultScreen(g_display);
    g_window = XCreateSimpleWindow(g_display, RootWindow(g_display, screen), 0, 0,
                                    FESTINA_CANVAS_WIDTH, FESTINA_CANVAS_HEIGHT, 0,
                                    BlackPixel(g_display, screen), WhitePixel(g_display, screen));

    Atom mwm_hints_atom = XInternAtom(g_display, "_MOTIF_WM_HINTS", False);
    FestinaMotifWmHints hints;
    memset(&hints, 0, sizeof(hints));
    hints.flags = 2; /* MWM_HINTS_DECORATIONS */
    hints.decorations = 0;
    XChangeProperty(g_display, g_window, mwm_hints_atom, mwm_hints_atom, 32,
                     PropModeReplace, (unsigned char *)&hints,
                     sizeof(hints) / sizeof(long));

    XStoreName(g_display, g_window, "Festina");
    XSelectInput(g_display, g_window,
                 ExposureMask | ButtonPressMask | PointerMotionMask |
                 KeyPressMask | StructureNotifyMask);
    g_wm_delete_atom = XInternAtom(g_display, "WM_DELETE_WINDOW", False);
    XSetWMProtocols(g_display, g_window, &g_wm_delete_atom, 1);

    XMapWindow(g_display, g_window);
    XSync(g_display, False); /* the map must reach the server before ... */
    /* ... this: with no window manager to hand focus over (as under a
     * bare Xvfb instance -- see tests/test_codegen.py's TestGraphics),
     * nothing else would ever give this window keyboard focus, and `on
     * key` would never fire. A real desktop's WM normally does this on
     * click/map; asking directly is harmless either way. */
    XSetInputFocus(g_display, g_window, RevertToParent, CurrentTime);
    XFlush(g_display);

    g_canvas_width = FESTINA_CANVAS_WIDTH;
    g_canvas_height = FESTINA_CANVAS_HEIGHT;
    g_window_surface = cairo_xlib_surface_create(g_display, g_window, DefaultVisual(g_display, screen),
                                                  FESTINA_CANVAS_WIDTH, FESTINA_CANVAS_HEIGHT);
    g_backing_surface = cairo_image_surface_create(CAIRO_FORMAT_ARGB32,
                                                     FESTINA_CANVAS_WIDTH, FESTINA_CANVAS_HEIGHT);
    cairo_t *cr = cairo_create(g_backing_surface);
    cairo_set_source_rgb(cr, 1, 1, 1); /* white canvas background */
    cairo_paint(cr);
    cairo_destroy(cr);
}

/* Blits the backing store (source of truth for what's been drawn) onto
 * the visible window -- called after every draw call for immediate
 * feedback, and on every Expose event to repaint correctly. */
static void festina_graphics_present(void) {
    cairo_t *cr = cairo_create(g_window_surface);
    cairo_set_source_surface(cr, g_backing_surface, 0, 0);
    cairo_paint(cr);
    cairo_destroy(cr);
    cairo_surface_flush(g_window_surface);
    XFlush(g_display);
}

void festina_draw_rect(int64_t x, int64_t y, int64_t w, int64_t h) {
    festina_graphics_require_init();
    cairo_t *cr = cairo_create(g_backing_surface);
    cairo_set_source_rgb(cr, 0, 0, 0);
    cairo_rectangle(cr, (double)x, (double)y, (double)w, (double)h);
    cairo_fill(cr);
    cairo_destroy(cr);
    festina_graphics_present();
}

void festina_draw_circle(int64_t x, int64_t y, int64_t r) {
    festina_graphics_require_init();
    cairo_t *cr = cairo_create(g_backing_surface);
    cairo_set_source_rgb(cr, 0, 0, 0);
    cairo_arc(cr, (double)x, (double)y, (double)r, 0.0, 2.0 * 3.14159265358979323846);
    cairo_fill(cr);
    cairo_destroy(cr);
    festina_graphics_present();
}

void festina_draw_text(const char *text, int64_t x, int64_t y) {
    festina_graphics_require_init();
    if (!text) text = "";
    cairo_t *cr = cairo_create(g_backing_surface);
    cairo_set_source_rgb(cr, 0, 0, 0);
    cairo_select_font_face(cr, "sans-serif", CAIRO_FONT_SLANT_NORMAL, CAIRO_FONT_WEIGHT_NORMAL);
    cairo_set_font_size(cr, 16);
    cairo_move_to(cr, (double)x, (double)y);
    cairo_show_text(cr, text);
    cairo_destroy(cr);
    festina_graphics_present();
}

void *festina_load_image(const char *path) {
    if (!path) path = "";
    /* claude.md #37: "Supported image formats are determined by the
     * runtime" -- PNG only, via Cairo's own built-in decoder. */
    cairo_surface_t *img = cairo_image_surface_create_from_png(path);
    cairo_status_t status = cairo_surface_status(img);
    if (status != CAIRO_STATUS_SUCCESS) {
        char msg[512];
        snprintf(msg, sizeof(msg), "could not load image '%s': %s (only PNG images are supported)",
                 path, cairo_status_to_string(status));
        cairo_surface_destroy(img);
        festina_fail(msg);
    }
    return img;
}

void festina_draw_image(void *img, int64_t x, int64_t y) {
    festina_graphics_require_init();
    if (!img) return;
    cairo_t *cr = cairo_create(g_backing_surface);
    cairo_set_source_surface(cr, (cairo_surface_t *)img, (double)x, (double)y);
    cairo_paint(cr);
    cairo_destroy(cr);
    festina_graphics_present();
}

void festina_register_click_handler(void (*handler)(int64_t, int64_t)) {
    g_click_handler = handler;
}

void festina_register_mouse_handler(void (*handler)(int64_t, int64_t)) {
    g_mouse_handler = handler;
}

void festina_register_key_handler(void (*handler)(const char *)) {
    g_key_handler = handler;
}

void festina_register_resize_handler(void (*handler)(void)) {
    g_resize_handler = handler;
}

void festina_register_close_handler(void (*handler)(void)) {
    g_close_handler = handler;
}

int64_t festina_client_width(void) {
    festina_graphics_require_init();
    return g_canvas_width;
}

int64_t festina_client_height(void) {
    festina_graphics_require_init();
    return g_canvas_height;
}

/* Handles one already-read X11 event. Returns 0 if this was the
 * window-close request (the caller should stop looping and tear down),
 * 1 otherwise. Factored out of what used to be festina_graphics_run's
 * own while(1) loop body so festina_run_event_loop (below) can drive
 * it either as-is or interleaved with timer processing. */
static int festina_handle_graphics_event(XEvent *ev) {
    if (ev->type == Expose) {
        festina_graphics_present();
    } else if (ev->type == ButtonPress) {
        if (g_click_handler) g_click_handler(ev->xbutton.x, ev->xbutton.y);
    } else if (ev->type == MotionNotify) {
        if (g_mouse_handler) g_mouse_handler(ev->xmotion.x, ev->xmotion.y);
    } else if (ev->type == KeyPress) {
        if (g_key_handler) {
            /* A key that types an ordinary printable character
             * (letters, digits, punctuation, space) comes back as
             * that character through the buffer XLookupString fills
             * in. Anything else -- Enter/Escape/Backspace/arrow
             * keys/... -- either comes back empty or as an
             * unprintable control character (e.g. 0x1B for Escape,
             * 0x0D for Return), neither of which is a useful `text`
             * value, so those fall back to XKeysymToString's X11 key
             * name instead (e.g. "Return", "Escape", "Left") --
             * there's no claude.md-defined naming scheme for these,
             * so this is simply X11's own. */
            char buf[32];
            KeySym keysym;
            int len = XLookupString(&ev->xkey, buf, sizeof(buf) - 1, &keysym, NULL);
            if (len > 0 && (unsigned char)buf[0] >= 0x20 && (unsigned char)buf[0] != 0x7F) {
                buf[len] = '\0';
                g_key_handler(buf);
            } else {
                const char *name = XKeysymToString(keysym);
                g_key_handler(name ? name : "");
            }
        }
    } else if (ev->type == ConfigureNotify) {
        /* ConfigureNotify fires on more than just a resize (e.g. a
         * move), so only treat it as `on resize` when the size
         * genuinely changed. */
        int64_t new_w = ev->xconfigure.width;
        int64_t new_h = ev->xconfigure.height;
        if (new_w != g_canvas_width || new_h != g_canvas_height) {
            g_canvas_width = new_w;
            g_canvas_height = new_h;
            cairo_xlib_surface_set_size(g_window_surface, new_w, new_h);
            /* claude.md #39's own examples never draw relative to a
             * canvas size (there's no syntax for one), so there's no
             * spec-defined way to preserve old content sanely across
             * a resize -- clear back to white at the new size, the
             * same behavior resizing a browser's <canvas> element
             * has, which clientWidth/clientHeight are named after. */
            cairo_surface_destroy(g_backing_surface);
            g_backing_surface = cairo_image_surface_create(CAIRO_FORMAT_ARGB32, new_w, new_h);
            cairo_t *cr = cairo_create(g_backing_surface);
            cairo_set_source_rgb(cr, 1, 1, 1);
            cairo_paint(cr);
            cairo_destroy(cr);
            festina_graphics_present();
            if (g_resize_handler) g_resize_handler();
        }
    } else if (ev->type == ClientMessage) {
        if ((Atom)ev->xclient.data.l[0] == g_wm_delete_atom) {
            if (g_close_handler) g_close_handler();
            return 0;
        }
    }
    return 1;
}

static void festina_graphics_teardown(void) {
    cairo_surface_destroy(g_backing_surface);
    cairo_surface_destroy(g_window_surface);
    XDestroyWindow(g_display, g_window);
    XCloseDisplay(g_display);
}

/* ---- setTimeout/setInterval -- claude.md #69. Added because Festina
 * otherwise has no way to schedule work after the fact. See
 * festina_runtime.h's doc comment for the full design (why the
 * callback is a bare function name, how this combines with the
 * graphics event loop, when a program with pending timers actually
 * exits). ---- */

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

static double festina_now_seconds(void) {
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

/* Fires every timer whose next_fire_time has passed. Indexes into
 * g_timers fresh on every access (never caches a pointer across a
 * callback() call) since a callback is ordinary Festina code and can
 * itself call setTimeout/setInterval (growing/reallocating g_timers)
 * or clearTimeout/clearInterval (deactivating an entry, including its
 * own) -- both need to be safe to do from inside a firing callback. */
static void festina_fire_expired_timers(void) {
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

/* The unified blocking loop main() enters (via festina_run_event_loop)
 * whenever a program uses graphics, timers, or both -- see
 * CodeGen.uses_graphics/uses_timers in festina/codegen.py. With
 * graphics: multiplexes X11 events and timer deadlines on the same
 * select() call (via ConnectionNumber(g_display)) rather than picking
 * one or the other, so `on click`/timers both stay responsive at once;
 * exits when the window closes (timers, if any, are simply abandoned --
 * matching a browser tab unloading). Without graphics: sleeps until the
 * next timer deadline and fires it, forever, until there's truly
 * nothing left to wait for (no active timers) -- matching Node's empty
 * event loop exiting the process. An uncleared setInterval therefore
 * keeps a graphics-free program running forever, exactly like in a real
 * JS runtime; it needs to be stopped externally (or via
 * clearInterval()) the same way. */
void festina_run_event_loop(void) {
    while (1) {
        double earliest = -1.0;
        for (int64_t i = 0; i < g_timer_count; i++) {
            if (!g_timers[i].active) continue;
            if (earliest < 0.0 || g_timers[i].next_fire_time < earliest) {
                earliest = g_timers[i].next_fire_time;
            }
        }

        if (g_display) {
            if (!XPending(g_display)) {
                int xfd = ConnectionNumber(g_display);
                fd_set fds;
                FD_ZERO(&fds);
                FD_SET(xfd, &fds);
                struct timeval tv;
                struct timeval *tvp = NULL;
                if (earliest >= 0.0) {
                    double remaining = earliest - festina_now_seconds();
                    if (remaining < 0.0) remaining = 0.0;
                    tv.tv_sec = (long)remaining;
                    tv.tv_usec = (long)((remaining - (double)tv.tv_sec) * 1e6);
                    tvp = &tv;
                }
                select(xfd + 1, &fds, NULL, NULL, tvp);
            }
            int keep_going = 1;
            while (XPending(g_display)) {
                XEvent ev;
                XNextEvent(g_display, &ev);
                if (!festina_handle_graphics_event(&ev)) {
                    keep_going = 0;
                    break; /* stop on window-close, same as the old loop did */
                }
            }
            festina_fire_expired_timers();
            if (!keep_going) {
                festina_graphics_teardown();
                return;
            }
        } else {
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
}

/* ---- audio -- claude.md #38 (see festina_runtime.h's doc comment) ---- */

typedef struct {
    int16_t *samples;    /* interleaved PCM, 16-bit signed, little-endian */
    size_t frame_count;  /* frames (per channel), not raw samples */
    int channels;
    unsigned int sample_rate;

    pthread_mutex_t lock;    /* guards everything below */
    pthread_t thread;        /* only meaningful while thread_running */
    snd_pcm_t *pcm;          /* only meaningful while thread_running */
    int thread_running;      /* a playback thread is spawned and not yet joined */
    int playing;             /* what isPlaying() reports */
    int stop_requested;      /* set by stop()/a restarting play(), read by the thread */
} FestinaAudio;

/* The playback thread's whole job: stream already-decoded PCM to ALSA
 * in small chunks, checking stop_requested between each one so stop()
 * gets a prompt response rather than waiting for the entire clip to
 * finish. The PCM device itself was already opened+configured by
 * festina_audio_play (synchronously, before this thread was even
 * spawned -- see its own comment for why), so this thread's only jobs
 * are writing frames and, on the way out (however it ends: finished,
 * stopped, or an unrecoverable ALSA error), closing the device and
 * resetting state so isPlaying() reflects reality again. */
static void *festina_audio_thread_main(void *arg) {
    FestinaAudio *a = (FestinaAudio *)arg;
    const snd_pcm_uframes_t chunk_frames = 4096;
    size_t frame = 0;

    while (frame < a->frame_count) {
        pthread_mutex_lock(&a->lock);
        int stop = a->stop_requested;
        pthread_mutex_unlock(&a->lock);
        if (stop) break;

        size_t remaining = a->frame_count - frame;
        snd_pcm_uframes_t this_chunk =
            remaining < chunk_frames ? (snd_pcm_uframes_t)remaining : chunk_frames;
        snd_pcm_sframes_t written = snd_pcm_writei(
            a->pcm, a->samples + frame * (size_t)a->channels, this_chunk);
        if (written < 0) {
            /* snd_pcm_recover handles the common recoverable cases
             * (buffer underrun, a suspended device); anything it can't
             * fix, just stop -- there's no Festina-level way to report
             * a mid-playback error after play() has already returned
             * successfully. */
            written = snd_pcm_recover(a->pcm, (int)written, 0);
            if (written < 0) break;
            continue;
        }
        frame += (size_t)written;
    }

    pthread_mutex_lock(&a->lock);
    snd_pcm_close(a->pcm);
    a->pcm = NULL;
    a->playing = 0;
    a->thread_running = 0;
    pthread_mutex_unlock(&a->lock);
    return NULL;
}

void *festina_load_audio(const char *path) {
    if (!path) path = "";
    FILE *f = fopen(path, "rb");
    if (!f) {
        char msg[512];
        snprintf(msg, sizeof(msg), "could not open audio file '%s': %s", path, strerror(errno));
        festina_fail(msg);
    }

    unsigned char riff_hdr[12];
    if (fread(riff_hdr, 1, 12, f) != 12 ||
            memcmp(riff_hdr, "RIFF", 4) != 0 || memcmp(riff_hdr + 8, "WAVE", 4) != 0) {
        fclose(f);
        char msg[512];
        snprintf(msg, sizeof(msg),
                 "could not load audio '%s': not a WAV file (only 16-bit PCM WAV audio is supported)",
                 path);
        festina_fail(msg);
    }

    int have_fmt = 0, have_data = 0, is_pcm = 0;
    int16_t channels = 0, bits_per_sample = 0;
    uint32_t sample_rate = 0;
    unsigned char *data = NULL;
    uint32_t data_size = 0;

    unsigned char chunk_hdr[8];
    while (fread(chunk_hdr, 1, 8, f) == 8) {
        uint32_t chunk_size = (uint32_t)chunk_hdr[4] | ((uint32_t)chunk_hdr[5] << 8) |
                               ((uint32_t)chunk_hdr[6] << 16) | ((uint32_t)chunk_hdr[7] << 24);
        if (memcmp(chunk_hdr, "fmt ", 4) == 0 && chunk_size >= 16) {
            unsigned char fmt[16];
            if (fread(fmt, 1, 16, f) != 16) break;
            int16_t audio_format = (int16_t)(fmt[0] | (fmt[1] << 8));
            channels = (int16_t)(fmt[2] | (fmt[3] << 8));
            sample_rate = (uint32_t)fmt[4] | ((uint32_t)fmt[5] << 8) |
                          ((uint32_t)fmt[6] << 16) | ((uint32_t)fmt[7] << 24);
            bits_per_sample = (int16_t)(fmt[14] | (fmt[15] << 8));
            is_pcm = (audio_format == 1);
            have_fmt = 1;
            if (chunk_size > 16 && fseek(f, (long)(chunk_size - 16), SEEK_CUR) != 0) break;
        } else if (memcmp(chunk_hdr, "data", 4) == 0) {
            data = malloc(chunk_size ? chunk_size : 1);
            if (!data || fread(data, 1, chunk_size, f) != chunk_size) {
                free(data);
                data = NULL;
                break;
            }
            data_size = chunk_size;
            have_data = 1;
            if (have_fmt) break; /* ignore any chunks after data -- metadata etc. */
        } else {
            /* skip an unrecognized chunk -- chunks are padded to an even
             * length, so an odd-sized chunk has one extra byte to skip. */
            if (fseek(f, (long)chunk_size + (long)(chunk_size % 2), SEEK_CUR) != 0) break;
        }
    }
    fclose(f);

    if (!have_fmt || !is_pcm || !have_data || bits_per_sample != 16 || channels < 1) {
        free(data);
        char msg[512];
        snprintf(msg, sizeof(msg),
                 "could not load audio '%s': only 16-bit PCM WAV audio is supported", path);
        festina_fail(msg);
    }

    FestinaAudio *a = calloc(1, sizeof(FestinaAudio));
    if (!a) festina_fail("out of memory loading audio");
    /* WAV's PCM data is little-endian; every target this compiler
     * generates code for (x86/x86_64, ARM in its default mode) is too,
     * so the bytes malloc'd above are already exactly int16_t samples
     * with no conversion needed -- the same little-endian assumption
     * this runtime already makes for int/float elsewhere. */
    a->samples = (int16_t *)data;
    a->frame_count = data_size / (size_t)(channels * 2);
    a->channels = channels;
    a->sample_rate = sample_rate;
    pthread_mutex_init(&a->lock, NULL);
    return a;
}

void festina_audio_play(void *audio) {
    FestinaAudio *a = (FestinaAudio *)audio;
    if (!a) return;

    pthread_mutex_lock(&a->lock);
    if (a->thread_running) {
        /* claude.md #38 doesn't say what play() does while already
         * playing -- restarting from the beginning is the least
         * surprising choice (matching an <audio> element's own
         * .play() called again mid-playback), so stop the current
         * playback thread first. */
        a->stop_requested = 1;
        pthread_mutex_unlock(&a->lock);
        pthread_join(a->thread, NULL);
        pthread_mutex_lock(&a->lock);
    }

    /* Opened here, synchronously, rather than inside the background
     * thread -- so a missing/unusable audio device fails loudly and
     * immediately at the play() call site (festina_fail(), same as
     * "could not open the X display" for graphics), not silently on a
     * background thread with no way to report it back. */
    snd_pcm_t *pcm = NULL;
    int rc = snd_pcm_open(&pcm, "default", SND_PCM_STREAM_PLAYBACK, 0);
    if (rc >= 0) {
        rc = snd_pcm_set_params(pcm, SND_PCM_FORMAT_S16_LE, SND_PCM_ACCESS_RW_INTERLEAVED,
                                 (unsigned int)a->channels, a->sample_rate, 1, 500000);
    }
    if (rc < 0) {
        pthread_mutex_unlock(&a->lock);
        char msg[256];
        snprintf(msg, sizeof(msg),
                 "could not open an audio output device: %s (is any audio device available?)",
                 snd_strerror(rc));
        festina_fail(msg);
        return; /* unreachable -- festina_fail() calls exit() */
    }

    a->pcm = pcm;
    a->stop_requested = 0;
    a->playing = 1; /* set synchronously, before spawning, so isPlaying()
                        is deterministic immediately after play() returns */
    a->thread_running = 1;
    rc = pthread_create(&a->thread, NULL, festina_audio_thread_main, a);
    if (rc != 0) {
        snd_pcm_close(pcm);
        a->pcm = NULL;
        a->playing = 0;
        a->thread_running = 0;
        pthread_mutex_unlock(&a->lock);
        festina_fail("could not start an audio playback thread");
        return;
    }
    pthread_mutex_unlock(&a->lock);
}

void festina_audio_stop(void *audio) {
    FestinaAudio *a = (FestinaAudio *)audio;
    if (!a) return;

    pthread_mutex_lock(&a->lock);
    if (!a->thread_running) {
        pthread_mutex_unlock(&a->lock);
        return; /* nothing playing -- a safe no-op, matching a real
                    media player's stop() on an already-stopped clip */
    }
    a->stop_requested = 1;
    pthread_mutex_unlock(&a->lock);
    /* Joins (not just signals) before returning, so isPlaying() is
     * guaranteed false the instant stop() returns -- not just "false
     * soon". The thread sets playing/thread_running itself right
     * before it exits (see festina_audio_thread_main). */
    pthread_join(a->thread, NULL);
}

int8_t festina_audio_is_playing(void *audio) {
    FestinaAudio *a = (FestinaAudio *)audio;
    if (!a) return 0;
    pthread_mutex_lock(&a->lock);
    int8_t playing = (int8_t)a->playing;
    pthread_mutex_unlock(&a->lock);
    return playing;
}
