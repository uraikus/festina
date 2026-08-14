#ifndef FESTINA_RUNTIME_H
#define FESTINA_RUNTIME_H

#include <stdint.h>
#include <sqlite3.h>
#include <regex.h>

/* claude.md #41: log() */
void festina_log_int(int64_t v);
void festina_log_float(double v);
void festina_log_bool(int8_t v);
void festina_log_text(const char *v);

/* claude.md #42: fail() -- prints to stderr and exits(1). */
void festina_fail(const char *msg);

/* claude.md #9, #45: string interpolation support. */
char *festina_str_from_int(int64_t v);
char *festina_str_from_float(double v);
char *festina_str_from_bool(int8_t v);
char *festina_str_concat(const char *a, const char *b);
int8_t festina_str_eq(const char *a, const char *b);

/*
 * claude.md #8, #29, #31: automatic SQLite database + schema sync.
 *
 * festina_db_open opens (creating if necessary) festina.sqlite in the
 * current working directory -- claude.md #29 fixes this filename, the
 * programmer never supplies a path.
 *
 * festina_sync_table brings a single declared table's schema in line
 * with `col_names`/`col_types` (parallel arrays, `col_types` holding
 * Festina primitive type names e.g. "int"/"text"), creating the table if
 * missing, adding/dropping/altering columns otherwise, and rebuilding
 * through a temporary table (claude.md #28, #31) when SQLite can't alter
 * a column's type in place -- preserving the data in every column that
 * survives the change.
 */
sqlite3 *festina_db_open(void);
void festina_sync_table(sqlite3 *db, const char *table_name,
                         const char **col_names, const char **col_types,
                         int32_t ncols);

/*
 * claude.md #32-34: sqlite() queries.
 *
 * festina_sqlite_prepare, the festina_sqlite_bind_* family,
 * festina_sqlite_exec, and festina_sqlite_collect_rows split what a single
 * sqlite() call does into separate, fixed-signature calls -- codegen
 * knows the parameter count and each parameter's type at compile time
 * (claude.md #33's params are always a literal array), so it can just
 * emit one bind call per element instead of this needing to accept a
 * variadic or tagged-union argument list.
 *
 * Row layout (festina_sqlite_collect_rows): claude.md #34 says a query
 * against a declared table produces arr[TableType] but doesn't define a
 * runtime representation any more than #26 does for arrays generally
 * (see festina/codegen.py's module docstring on both). This runtime
 * packs each row as `col_count` consecutive 8-byte slots -- int64_t,
 * a double's raw bits, or a pointer, depending on that column's
 * declared Festina type -- deliberately never narrower than 8 bytes so
 * every slot is naturally aligned on its own and there's no struct-
 * packing/alignment computation to replicate between this file and
 * festina/codegen.py's IR generation (which reads a row the same way:
 * a byte offset of `field_index * 8` into the row). Columns are mapped
 * to the declared table's fields *by position*, not by name -- this
 * assumes (as claude.md's own #34 example does) that the query's result
 * columns are the declared table's columns, in declared order.
 * SQL NULL becomes the same reserved sentinel int/float/text null
 * already uses elsewhere (claude.md #57) -- see festina_null_int() /
 * festina_null_float() below, kept in sync with
 * festina/codegen.py's INT_NULL_CONST / FLOAT_NULL_CONST.
 */
sqlite3_stmt *festina_sqlite_prepare(sqlite3 *db, const char *sql);
void festina_sqlite_bind_int(sqlite3_stmt *stmt, int32_t idx, int64_t val);
void festina_sqlite_bind_float(sqlite3_stmt *stmt, int32_t idx, double val);
void festina_sqlite_bind_text(sqlite3_stmt *stmt, int32_t idx, const char *val);
void festina_sqlite_bind_null(sqlite3_stmt *stmt, int32_t idx);

/* Runs a prepared statement to completion and finalizes it, discarding
 * any rows (INSERT/UPDATE/DELETE, or a SELECT whose result isn't
 * captured into an arr[Table]). */
void festina_sqlite_exec(sqlite3_stmt *stmt);

/* Steps a prepared statement to completion, collecting each row per the
 * layout above, then finalizes it. col_types has col_count entries,
 * one per declared table field in order ("int"/"float"/"bool"/"text"). */
void festina_sqlite_collect_rows(sqlite3_stmt *stmt, int32_t col_count,
                                  const char **col_types,
                                  int64_t *out_length, void **out_data);

/*
 * claude.md #67-68: regex(), .test(), .match(), .replace()/.replaceAll().
 *
 * Built on POSIX extended regular expressions (regcomp/regexec from
 * <regex.h>) rather than a bundled regex engine or an external library
 * like PCRE -- claude.md #59's minimal-dependencies principle: POSIX
 * regex is already part of libc on every platform this compiler
 * already requires libc on, so this adds zero new dependencies, at the
 * cost of a less expressive dialect than PCRE/JS regex (no
 * lookaround, no non-greedy quantifiers, no \d-style shorthand
 * classes -- POSIX ERE's own limitations, not something this file
 * works around).
 *
 * festina_regex_compile compiles `pattern` once per call (REG_EXTENDED,
 * plus REG_ICASE if `flags` contains 'i') and returns the resulting
 * regex_t*, heap-allocated and never freed -- same "no GC yet, things
 * leak" tradeoff every other heap allocation in this runtime already
 * makes (see festina/codegen.py's module docstring). An invalid
 * pattern calls festina_fail() with regerror()'s message -- claude.md
 * #67: pattern validity is a runtime concern, the Python compiler
 * doesn't parse regex syntax itself.
 *
 * festina_regex_match / festina_str_replace / festina_regex_replace
 * all return a NULL char* for "no match" rather than a sentinel string
 * -- NULL is already exactly how Festina represents a null `text`
 * value (see festina/codegen.py's "Null for int/float" docstring note:
 * text is pointer-backed, so the ordinary C NULL pointer *is* the null
 * sentinel, unlike int/float which need INT_NULL_CONST/FLOAT_NULL_CONST).
 * replace()/replaceAll() specifically return the *original* string
 * unchanged (not NULL) when there's no match, per claude.md #68.
 */
void *festina_regex_compile(const char *pattern, const char *flags);
int8_t festina_regex_test(void *compiled, const char *text);
char *festina_regex_match(void *compiled, const char *text);
char *festina_str_replace(const char *text, const char *search,
                           const char *replacement, int8_t replace_all);
char *festina_regex_replace(void *compiled, const char *text,
                             const char *replacement, int8_t replace_all);

#endif
