/*
 * Festina native runtime -- claude.md #41 (log), #42 (fail), #45 (string
 * interpolation), #29-31 (automatic SQLite database + schema sync).
 *
 * This is a from-scratch runtime for the statically typed Festina
 * language, kept deliberately separate from runtime/runtime.c (the
 * dynamically typed JSValue runtime that backs the older, unrelated
 * compiler/jsc.py JS-subset prototype).
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
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
            pos += snprintf(sql + pos, sizeof(sql) - pos, "%s%s %s", i ? ", " : "",
                             col_names[i], festina_sql_type(col_types[i]));
        }
        snprintf(sql + pos, sizeof(sql) - pos, ");");
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
        pos += snprintf(create_sql + pos, sizeof(create_sql) - pos, "%s%s %s", i ? ", " : "",
                         col_names[i], festina_sql_type(col_types[i]));
    }
    snprintf(create_sql + pos, sizeof(create_sql) - pos, ");");
    festina_exec(db, create_sql);

    char dest_cols[1024] = "";
    char src_cols[1024] = "";
    int dpos = 0, spos = 0;
    int first = 1;
    for (int j = 0; j < ncols; j++) {
        for (int i = 0; i < n_existing; i++) {
            if (strcmp(existing_names[i], col_names[j]) == 0) {
                dpos += snprintf(dest_cols + dpos, sizeof(dest_cols) - dpos, "%s%s", first ? "" : ", ", col_names[j]);
                if (strcmp(existing_types[i], festina_sql_type(col_types[j])) != 0) {
                    spos += snprintf(src_cols + spos, sizeof(src_cols) - spos, "%sCAST(%s AS %s)",
                                      first ? "" : ", ", col_names[j], festina_sql_type(col_types[j]));
                } else {
                    spos += snprintf(src_cols + spos, sizeof(src_cols) - spos, "%s%s", first ? "" : ", ", col_names[j]);
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
