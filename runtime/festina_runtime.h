#ifndef FESTINA_RUNTIME_H
#define FESTINA_RUNTIME_H

#include <stdint.h>
#include <sqlite3.h>

/* Note on what does NOT appear in this header: no Cairo, X11, ALSA, or
 * <regex.h> type ever crosses a public function's signature -- every
 * such value is opaqued to `void *` (FestinaRegex* from festina_regex_compile,
 * cairo_surface_t* from festina_load_image, ...). That's what makes it
 * possible to split the implementation (festina_runtime.c/_graphics.c/
 * _audio.c -- see each file's own top comment) into separate translation
 * units with zero header-level coupling to graphics/audio: a program
 * that never calls a graphics/audio function never needs those object
 * files linked in at all, so `cc` never even sees -lcairo/-lX11/-lasound
 * on its command line for it -- see cli.py's per-feature object file
 * selection, driven by CodeGen.uses_graphics/uses_audio. sqlite3 is the
 * one exception (sqlite3* and sqlite3_stmt* appear directly below) since
 * it's a permanent, always-linked core dependency, never an optional one. */

/* windows.md Phase 0 (claude.md #126): called once, unconditionally,
 * as literally the first thing main() does in every compiled program
 * -- currently just the Windows stdout/stderr text-mode fix (see the
 * .c file), but the natural place for any future "before anything
 * else runs" platform setup, so it exists even though today only one
 * platform needs it to do anything. */
void festina_runtime_init(void);

/* claude.md #41: log() */
void festina_log_int(int64_t v);
void festina_log_float(double v);
void festina_log_bool(int8_t v);
void festina_log_text(const char *v);

/* claude.md #42: fail() -- prints to stderr and exits(1). */
void festina_fail(const char *msg);

/* claude.md #158: troubleshoot(event, fields) -- one structured JSON
 * line to stdout; fail(message, fields) -- the structured 2-argument
 * form of fail(), a JSON line to stderr then exit(1). `fields_json` is
 * already-rendered JSON text (codegen's own _to_text on a map[text]
 * argument). */
void festina_troubleshoot(const char *event, const char *fields_json);
void festina_fail_structured(const char *msg, const char *fields_json);

/* claude.md #159: .toStruct()/.toArr() JSON parsing. Every one of
 * these either succeeds and returns a valid result, or calls
 * festina_throw() internally and never returns -- see
 * festina_runtime.c's own comment on this whole group. `cursor` is
 * always the opaque value festina_json_cursor_new returned. */
void *festina_json_cursor_new(const char *text);
void festina_json_cursor_free(void *cursor);
void festina_json_expect_end(void *cursor);
void festina_json_object_start(void *cursor);
void festina_json_array_start(void *cursor);
int8_t festina_json_object_next(void *cursor, int8_t *first);
int8_t festina_json_array_next(void *cursor, int8_t *first);
char *festina_json_read_key(void *cursor);
int8_t festina_json_key_matches(const char *key, const char *field_name);
void festina_json_skip_field_value(void *cursor);
int64_t festina_json_read_int(void *cursor);
double festina_json_read_float(void *cursor);
int8_t festina_json_read_bool(void *cursor);
char *festina_json_read_text(void *cursor);

/* claude.md #162: url / parseURL(text) -- modeled on the WHATWG URL
 * object's own field names (hash/hostname/password/pathname/port/
 * protocol/searchParams/username). One shape only, like RegexType/
 * HttpType (see festina_runtime.c's own doc comment on the parser's
 * real scope: absolute URLs only, no IDNA, no exhaustive RFC 3986
 * validation). Refcounted (the same `{refcount, ...}` header blob/
 * img/aud/http already share) -- festina_release_url is this type's
 * own destructor, dispatched through codegen's _release_fn_for the
 * same way every other refcounted type's is. parseURL() itself
 * THROWS (claude.md #157) on a genuinely malformed URL, catchable by
 * an enclosing try, the same design claude.md #159's JSON parser
 * already established for "this operation can fail with real
 * diagnostic text" runtime primitives. */
void *festina_parse_url(const char *text);
char *festina_url_protocol(void *payload);
char *festina_url_username(void *payload);
char *festina_url_password(void *payload);
char *festina_url_hostname(void *payload);
int64_t festina_url_port(void *payload);
char *festina_url_pathname(void *payload);
char *festina_url_hash(void *payload);
void *festina_url_search_params(void *payload);  /* fresh reference --
                                                   * caller owns it, same
                                                   * "retain on the way out"
                                                   * convention every other
                                                   * shared-live-value field
                                                   * getter already uses */
void festina_release_url(void *payload);

/* claude.md #157: try/catch/throw. See festina_runtime.c's own comment
 * on this whole group for the setjmp/longjmp design and its one
 * documented leak caveat (a throw reached through a called function).
 * The actual setjmp call is emitted directly by codegen (_emit_try) --
 * festina_try_push registers the buffer it produced. */
void festina_try_push(void *buf);
void festina_try_pop(void);
char *festina_try_error(void);
void festina_throw(const char *msg);

/* claude.md #131: close(code) -- runs a declared `on exit(code:int)`
 * handler (if any), then exits with `code`. Lives in the core runtime
 * so it works in every program, windowed or not -- unlike
 * festina_register_close_handler/festina_run_event_loop's own window-
 * close event in festina_runtime_graphics.c, which only ever fires
 * from an actual window. festina_register_exit_handler is called at
 * most once, unconditionally, near the top of main(). */
void festina_register_exit_handler(void (*handler)(int64_t));
void festina_program_exit(int64_t code);

/* claude.md #161: graceful shutdown -- SIGINT (every platform) and
 * SIGTERM (POSIX only -- Windows has no real delivery of it, see
 * festina_runtime.c's own comment) now run the SAME clean-exit path
 * close(code) already uses (`on exit(code:int)` fires, then the
 * process exits) instead of the OS's own default abrupt termination,
 * and -- for a program using openPort()/openSecurePort() -- give
 * already-accepted connections a real chance to finish first (see
 * festina_runtime_http.c's own festina_run_http_loop). Generated
 * code's own main() calls festina_install_shutdown_handler() at most
 * once, and ONLY when the program has one of the three pollable
 * blocking loops below (see festina_runtime.c's own comment on why --
 * installing it anywhere else, including for a program that declares
 * `on exit` but has none of those loops, would silently swallow
 * Ctrl+C with nothing left to ever check for it). festina_shutdown_requested()/
 * _exit_code() are what every blocking loop (http/timer/graphics)
 * polls once per ordinary iteration to notice and act on it. */
void festina_install_shutdown_handler(void);
int64_t festina_shutdown_requested(void);
int64_t festina_shutdown_exit_code(void);

/* claude.md #9, #45: string interpolation support. */
char *festina_str_from_int(int64_t v);
char *festina_str_from_float(double v);
char *festina_str_from_bool(int8_t v);
char *festina_str_concat(const char *a, const char *b);
/* claude.md #114: the string builder behind JSON-like rendering of
 * structs/rows/arrays/maps in log() and `${}`. Structure walking lives
 * in generated IR; byte handling lives here. See the .c doc comment. */
void *festina_sb_new(void);
void festina_sb_append(void *sb, const char *s);
/* claude.md #190: same as festina_sb_append, but for a caller that
 * already knows the byte length -- skips the runtime strlen() rescan.
 * Used for every compile-time-known literal (JSON punctuation, a
 * struct/table field's own pre-baked key) _json_fn_for's generated IR
 * appends. `len <= 0` is a no-op, matching festina_sb_append's own
 * NULL-is-a-no-op contract. */
void festina_sb_append_n(void *sb, const char *s, int64_t len);
void festina_sb_append_json_text(void *sb, const char *s);
void festina_sb_append_json_int(void *sb, int64_t v);
void festina_sb_append_json_float(void *sb, double v);
void festina_sb_append_json_bool(void *sb, int8_t v);
void festina_sb_append_json_bool64(void *sb, int64_t v);
void festina_sb_append_handle(void *sb, const void *handle, const char *label);
char *festina_sb_finish(void *sb);
/* claude.md #116: text.split(text|regex) -> arr[text] (a fresh
 * refcounted array of owned pieces, JS semantics -- see the .c doc
 * comment), and arr.join(sep) -> owned text, `kind` naming the element
 * type since the runtime cannot know an arr[T]'s T. */
void *festina_text_split(const char *s, const char *sep);
void *festina_regex_split(void *compiled, const char *s);
char *festina_arr_join(void *arr, const char *sep, const char *kind);
char *festina_text_own(const char *s);  /* claude.md #83: NULL-safe strdup */

/* claude.md #132: mkdir(path) -> bool (true if IT created the
 * directory, false for every other outcome, including "already
 * exists"); ls(path) -> arr[text] of entry names (built exactly like
 * festina_text_split -- a fresh refcounted array of owned pieces),
 * empty for a missing/unreadable directory rather than failing. */
int8_t festina_mkdir(const char *path);
void *festina_ls(const char *path);

/* claude.md #150: text.toInt() -> int (festina_null_int() -- the same
 * i64-minimum sentinel every other "no valid int here" site in this
 * runtime already answers with -- when nothing parseable is found, JS
 * parseInt()-style otherwise: leading whitespace and an optional sign
 * are skipped, parsing stops at the first non-digit rather than
 * requiring the whole text to be numeric); text[i] -> text (a single
 * UTF-8 code point, matching split('')'s own per-code-point unit, or
 * NULL -- Festina's own text null -- for i<0 or i beyond the last code
 * point, the same "answer null, don't crash" choice claude.md #72
 * already made for a missing map[T] key). Both NULL-safe on a null
 * receiver, treating it as "" like every other text-consuming runtime
 * call here already does. */
int64_t festina_text_to_int(const char *s);
char *festina_text_char_at(const char *s, int64_t index);

/* claude.md #150: argv -- builds a fresh refcounted arr[text] (the same
 * shape festina_text_split's own pieces-array does) from the argc/argv
 * a compiled program's own `main` received; called once, in main()'s
 * own prologue (see codegen.py's _emit_main_and_entry), before
 * anything else runs. */
void *festina_argv_array(int argc, char **argv);

/* claude.md #150: exec(args) -- spawns args[0] with args[1:] as its own
 * argv (PATH-searched, no shell involved -- so no shell-quoting rules
 * to get right or wrong), inheriting this process's own stdin/stdout/
 * stderr, waits for it, and answers its real exit code -- or -1 if the
 * process could never even start (missing executable, ...), the same
 * "a program tests for this, it doesn't crash the whole process over
 * it" choice claude.md #93/#132 already made for file/directory
 * operations. `args` is the arr[text] header pointer exactly like
 * festina_arr_join's own `arr` parameter -- args[0] the program to
 * run, the rest its own arguments. Named festina_process_exec, not
 * festina_exec -- that name was already taken, by an internal `static
 * void festina_exec(sqlite3*, const char*)` DDL helper further down
 * this same file (nothing to do with this one; a genuine naming
 * collision, not a rename of that function). Not available under
 * wasm32-wasi at all (WASI has no process model to spawn into -- see
 * wasm.md's Limitations section); rejected outright at compile time
 * there (see festina/cli.py's _check_wasm_feature_supported), so the
 * .c file's own wasm32-wasi branch is a stub nothing ever actually
 * calls. */
int64_t festina_process_exec(void *args);

/* claude.md #177 (new entry): the non-blocking counterpart --
 * exec(args, callback) -- dispatched onto the exact same background
 * worker pool blob/img/aud's own `.callback()` runs on
 * (festina_async_io_dispatch, below). `user_callback` is the program's
 * real func[int]:void value, carried through as opaque data (it may be
 * an arbitrary runtime value, not just a bare function symbol -- see
 * FestinaExecPayload's own comment); `trampoline` is codegen's own
 * single generated void(ptr) wrapper that reads the exit code and
 * user_callback back out of the payload and calls the latter -- see
 * festina_runtime.c's own FestinaExecPayload/
 * _emit_exec_callback_trampoline doc comments for why a trampoline is
 * needed here at all (an int result, unlike blob/img/aud's own
 * already-ptr-shaped one). Never called with a NULL user_callback/
 * trampoline -- codegen only ever reaches this from the 2-argument
 * exec() form, which always has a real callback; the 1-argument, fully
 * synchronous form still calls festina_process_exec above directly. */
void festina_process_exec_dispatch(void *args, void *user_callback,
                                    void (*trampoline)(void *));

/* claude.md #93: math, files and time -- all libc/libm, both already on
 * every link line, so none of this costs a new dependency.
 *
 * festina_read_file returns NULL (Festina's null text) for anything it
 * cannot read and festina_format_time returns NULL for a format that
 * produces nothing, rather than failing the program: a missing file or
 * a bad format is an ordinary condition a program should be able to
 * test for, the same reasoning claude.md #57 applies to division by
 * zero. The write helpers return 0/1 for the same reason, and count a
 * failing fclose as a failed write (a full disk can fail there even
 * when every fwrite succeeded). festina_random is plain rand() --
 * suitable for gameplay and sampling, explicitly not for cryptography. */
double festina_random(void);
char *festina_read_file(const char *path);
int8_t festina_write_file(const char *path, const char *content);
int8_t festina_append_file(const char *path, const char *content);
int8_t festina_file_exists(const char *path);
int8_t festina_delete_file(const char *path);
int64_t festina_now_ms(void);
char *festina_format_time(int64_t ms, const char *format);
int8_t festina_str_eq(const char *a, const char *b);

/*
 * claude.md #8, #29, #31: automatic SQLite database + schema sync.
 *
 * festina_db_open opens (creating if necessary) `path` -- always
 * festina.sqlite in the current working directory (claude.md #29's
 * default) unless the program's entry file overrides it with a
 * DatabaseURL directive (claude.md #70), in which case codegen passes
 * that expression's own runtime value here instead. A NULL or empty
 * path falls back to the "festina.sqlite" default rather than failing
 * -- see this function's own comment in festina_runtime.c for why that
 * can legitimately happen even with a DatabaseURL directive present
 * (e.g. environment.DATABASE_URL when that variable isn't set).
 *
 * festina_sync_table brings a single declared table's schema in line
 * with `col_names`/`col_types` (parallel arrays, `col_types` holding
 * Festina primitive type names e.g. "int"/"text"), creating the table if
 * missing, adding/dropping/altering columns otherwise, and rebuilding
 * through a temporary table (claude.md #28, #31) when SQLite can't alter
 * a column's type in place -- preserving the data in every column that
 * survives the change.
 */
sqlite3 *festina_db_open(const char *path);
void festina_sync_table(sqlite3 *db, const char *table_name,
                         const char **col_names, const char **col_types,
                         int32_t ncols);

/* claude.md #126 round nine: called once, unconditionally, as
 * literally the last thing every compiled program's main() does
 * (mirroring festina_runtime_init() at the start) -- finalizes every
 * cached prepared statement and closes the database, forcing SQLite's
 * own checkpoint-on-last-close rather than leaving committed data
 * sitting in the WAL file for whatever reads it next to sort out. A
 * NULL db (a program with no `table` declarations at all -- see
 * @__festina_db's own default in codegen.py) is a safe no-op. */
void festina_db_close(sqlite3 *db);

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
/* claude.md #113: the literal-SQL fast path -- `slot` is a per-call-site
 * cache (a compiler-emitted private global); the statement is prepared
 * once and reset+reused ever after. See the .c doc comment. */
sqlite3_stmt *festina_sqlite_prepare_cached(sqlite3 *db, const char *sql,
                                            void **slot);
void festina_sqlite_bind_int(sqlite3_stmt *stmt, int32_t idx, int64_t val);
void festina_sqlite_bind_float(sqlite3_stmt *stmt, int32_t idx, double val);
void festina_sqlite_bind_text(sqlite3_stmt *stmt, int32_t idx, const char *val);
void festina_sqlite_bind_blob(sqlite3_stmt *stmt, int32_t idx, const void *data, int64_t len);
void festina_sqlite_bind_null(sqlite3_stmt *stmt, int32_t idx);

/* claude.md #101: an `aud`/`img` table column stores the asset's own
 * encoded bytes as a BLOB, so reading such a row has to turn bytes back
 * into a handle. This translation unit must not reference the graphics
 * or audio ones by name -- that separation is what lets a program using
 * neither link neither (see this file's top-of-file note) -- so main()
 * registers the decoders instead, exactly when the program already
 * links that feature. Unregistered, such a column reads as null rather
 * than crashing. */
void festina_set_audio_decoder(void *(*fn)(const void *, int64_t, const char *));
void festina_set_image_decoder(void *(*fn)(const void *, int64_t, const char *));
/* claude.md #151: the indirection req.toImg()/req.toAud() go
 * through -- NULL if the program never registered a decoder (never
 * actually uses graphics/audio), matching festina_set_*_decoder's own
 * "unset means null" contract. */
void *festina_decode_image_bytes(const void *data, int64_t len, const char *label);
void *festina_decode_audio_bytes(const void *data, int64_t len, const char *label);

/* Runs a prepared statement to completion and finalizes it, discarding
 * any rows (INSERT/UPDATE/DELETE, or a SELECT whose result isn't
 * captured into an arr[Table]). */
void festina_sqlite_exec(sqlite3_stmt *stmt);

/* claude.md #94: single-value queries -- the first column of the first
 * row, then finalize. Receiving a result used to require declaring a
 * `table`, which CREATES one (claude.md #28-31's schema sync), so a
 * `count(*)` left a throwaway table in the database; these need no
 * schema at all. No rows, or a SQL NULL, answers with Festina's own
 * null for that type rather than failing. */
int64_t festina_sqlite_scalar_int(sqlite3_stmt *stmt);
double festina_sqlite_scalar_float(sqlite3_stmt *stmt);
char *festina_sqlite_scalar_text(sqlite3_stmt *stmt);

/* Steps a prepared statement to completion, collecting each row per the
 * layout above, then finalizes it. col_types has col_count entries,
 * one per declared table field in order ("int"/"float"/"bool"/"text"). */
/* claude.md #111: takes the declared column NAMES too, because result
 * columns are matched by name rather than position now (partial and
 * reordered SELECTs used to silently misalign), and each row carries a
 * hidden presence bitmask one slot past its columns, read by
 * festina_row_undefined. */
/* claude.md #188 (uraikus/festina#76 item 5): `want_rowid` adds one
 * MORE hidden slot past the presence mask, holding the query's own
 * `rowid` result column (by name, matched the identical way as every
 * declared column) -- always false for a struct query target
 * (claude.md #112), which has no rowid concept. See the .c doc
 * comment. */
void festina_sqlite_collect_rows(sqlite3_stmt *stmt, int32_t col_count,
                                  const char **col_types, const char **col_names,
                                  int64_t *out_length, void **out_data,
                                  int8_t want_rowid);
int8_t festina_row_undefined(void *row, const char **col_names,
                             int32_t col_count, const char *name);
/* claude.md #111/#175: `delete m[key]` -- removes the entry, releasing
 * its value through the same per-type trampoline whole-map release
 * uses. `capacity` is read-only (delete never grows the table).
 * Returns whether the key existed; a missing key is a safe no-op. */
int8_t festina_map_delete(int64_t *count, void **entries, int64_t capacity,
                          int64_t *tombstones, const char *key,
                          void (*release)(int64_t, const char *));
/* claude.md #111/#118: marks a /pattern/ literal's cached compilation
 * as immortal (the same negative-header sentinel every other immortal
 * value uses), so retain/release/`free` on it are all safe no-ops. Set
 * by generated code right after the literal cache is first filled. */
void festina_regex_mark_cached(void *compiled);
/* claude.md #118: the per-call-site memo for the dynamic regex()
 * builtin -- `slot` is a private [3 x ptr] global codegen emits per
 * call site ({pattern copy, flags copy, compiled}). Same pattern+flags
 * as last time answers the cached compilation; a change releases the
 * slot's reference (safe: regex is refcounted now) and recompiles. The
 * caller always receives its own +1. */
void *festina_regex_compile_memo(const char *pattern, const char *flags,
                                 void **slot);

/*
 * claude.md #67-68 (#107): regex(), .test(), .match(), .replace().
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
 * plus REG_ICASE if `flags` contains 'i') and returns a heap-allocated
 * FestinaRegex* -- a regex_t with claude.md #107's 'g' flag recorded
 * alongside it. 'g' is not a matching property and POSIX has no cflag
 * for it; it says what the caller wants done with the matches, so it
 * has to be carried to festina_regex_replace rather than applied by
 * regcomp. It travels with the compiled pattern rather than with the
 * call site because `regex(p, f)` builds its flags from a runtime text
 * expression -- codegen has nothing to inspect at the .replace() call.
 *
 * 'g' affects .replace() ONLY. .test() ignores it deliberately: in JS a
 * /g regex makes .test() stateful via lastIndex, so the same test
 * against the same string alternates true/false, which is a bug
 * factory rather than a feature. .match() ignores it for a harder
 * reason -- JS's /g makes .match() return an array rather than a
 * string, and a function's return type cannot depend on a flag that
 * `regex(p, f)` only knows at run time. Both are documented in api.md
 * as limits rather than left to be discovered. claude.md #85/#118: a
 * compiled regex is refcounted (i64 header before the payload), so a
 * `regex(...)` temporary, a bound regex's scope exit, and `free` on an
 * aliased binding all go through festina_regex_free's decrement, and
 * only the last reference regfrees. A /pattern/ literal is compiled
 * once, cached for the life of the process, and marked immortal (see
 * festina_regex_mark_cached above). An invalid
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
 * replace() specifically returns the *original* string unchanged (not
 * NULL) when there's no match, per claude.md #68.
 */
void *festina_regex_compile(const char *pattern, const char *flags);
void festina_regex_free(void *compiled);  /* claude.md #85/#118: release; regfree on last ref */
int8_t festina_regex_test(void *compiled, const char *text);
char *festina_regex_match(void *compiled, const char *text);
/* claude.md #107: neither takes a replace_all argument any more.
 * .replaceAll() is gone; a regex replaces every match iff its own
 * pattern carries 'g', and a plain-text search -- which has no flags to
 * carry -- replaces the first match only, exactly like JS's
 * String.prototype.replace with a string argument. */
char *festina_str_replace(const char *text, const char *search,
                           const char *replacement);
char *festina_regex_replace(void *compiled, const char *text,
                             const char *replacement);

/*
 * claude.md #37, #39, #40: img, graphics functions, click/mouse events.
 *
 * "Graphics are backed by Cairo" (claude.md #39) and "No GUI import is
 * required" -- read together with #40's click/mouse events firing
 * against "the canvas", this means an actual on-screen window, not a
 * file written to disk. Built on Xlib + Cairo's Xlib surface backend
 * rather than a toolkit like GTK/SDL/Qt: both are already installed
 * everywhere this runtime already needs Cairo (verified in this
 * project's own dev environment), so this adds one new dependency
 * (libX11) rather than a whole GUI toolkit, per claude.md #59. The
 * window is fully decorated (Motif WM hints request MWM_DECOR_ALL -- a
 * widely honored convention, though not part of the core X11 protocol,
 * so a given window manager could still draw its own default instead)
 * -- a title bar and the window manager's normal minimize/maximize/
 * close controls, like any other window, resizable by dragging an
 * edge. claude.md #180 added enterFullscreen()/exitFullscreen() on top
 * of that, toggling true OS fullscreen (X11's own _NET_WM_STATE_
 * FULLSCREEN convention, honored the same way).
 *
 * Canvas size starts at a fixed 800x600 (FESTINA_CANVAS_WIDTH/HEIGHT in
 * festina_runtime.c) -- claude.md has no syntax for declaring a canvas
 * size, so this is an implementation-defined default, not derived from
 * anything in the spec. It can change afterwards if the window is
 * resized (see `on resize` below); festina_client_width/_height always
 * report the *current* size, not the startup default.
 *
 * Drawing model: every draw call paints onto an in-memory Cairo image
 * surface (the "backing store") and immediately blits it to the visible
 * window -- immediate feedback, matching how claude.md's own examples
 * read (call drawRect(), expect to see it), and the backing store is
 * also what repaints the window on an Expose event (e.g. after another
 * window overlapped and moved away), which a bare Cairo Xlib surface
 * can't do on its own since it has no memory of what was drawn before.
 * All shapes/text draw in solid black -- claude.md #39's own examples
 * (drawRect/drawCircle/drawText) take no color argument, so there is
 * nothing to make configurable yet.
 *
 * festina_graphics_init creates the window; festina_run_event_loop
 * (see the "Timers" note further down -- it handles both graphics and
 * timer events, not just graphics despite its history) is the blocking
 * event loop while a window is open (Expose -> repaint from the
 * backing store, ButtonPress -> the registered click handler if any
 * (buttons 4/5 -> the registered scroll-wheel handler instead, see
 * claude.md #181), MotionNotify -> the registered mouse handler if
 * any, KeyPress -> the registered key handler if any, ConfigureNotify
 * with a genuine size
 * change -> resize the backing store and call the registered resize
 * handler if any, the window's close button -> call the registered
 * close handler if any, then return). claude.md #178 (uraikus/
 * festina#79): festina_graphics_init is NOT called directly by
 * generated code any more -- main()'s own prologue only registers
 * event handlers before __festina_main() runs now, so a pre-window
 * setClientWidth/setClientHeight call is honored as the window's
 * initial size rather than overwritten by an eagerly-opened default
 * one. It is instead called lazily, self-guarded (a no-op if already
 * open), from festina_render()'s own first call and from
 * festina_run_event_loop()'s own top (the fallback for a program that
 * never itself calls render()) -- either way, only ever reached when
 * the program actually uses a graphics function, references
 * clientWidth/clientHeight, calls enterFullscreen()/exitFullscreen(),
 * or declares an `on mouseDown`/`mouseUp`/`mouse`/`mouseWheelUp`/
 * `mouseWheelDown`/`key`/`resize`/`close` handler (see
 * CodeGen.uses_graphics in festina/codegen.py) --
 * a program that doesn't never opens a window, exactly like
 * festina_db_open() only ever runs for a program that declares a
 * `table`.
 *
 * festina_load_image supports PNG (via Cairo's own built-in decoder)
 * and, since claude.md #101, JPEG (via libjpeg) -- claude.md #37:
 * "Supported image formats are determined by the runtime." libjpeg
 * rather than a heavier toolkit for the same reason Xlib was picked
 * over a GUI toolkit: the smallest dependency that does the job.
 * Format is sniffed from the MAGIC BYTES, not the file extension -- a
 * blob out of a database column has no extension, and an extension was
 * never evidence of anything anyway.
 *
 * festina_register_mouse_down_handler/_mouse_up_handler/_mouse_handler/
 * _mouse_wheel_up_handler/_mouse_wheel_down_handler take a fixed
 * `void (*)(int64_t, int64_t)` signature,
 * festina_register_key_down_handler/_key_up_handler take a fixed
 * `void (*)(const char *)` signature, and
 * festina_register_resize_handler/_close_handler take a fixed
 * `void (*)(void)` signature -- each matches the parameters claude.md
 * #40's own worked example declares for that event exactly
 * (`on mouseDown(x:int, y:int)`, `on keyDown(key:text)`, `on resize()`,
 * ...); festina/semantic.py's _EVENT_SIGNATURES enforces that any
 * handler for one of these nine names is actually declared that way
 * before codegen ever emits a call here, so a mismatch would otherwise
 * be a silent ABI mismatch rather than a caught compile error. Both key
 * handlers' text comes from the same festina_key_name helper --
 * XLookupString (a key that types a character, e.g. "a", "5", " ")
 * falling back to XKeysymToString (a named key with no text of its own,
 * e.g. "Left", "Escape", "Return") -- so keyUp always reports exactly
 * what keyDown reported for the same physical key. claude.md #98:
 * auto-repeat is filtered, so holding a key does not fire a phantom
 * keyUp between repeats -- see festina_key_event_is_autorepeat.
 * `on resize` intentionally clears the canvas back to white at the new
 * size rather than preserving old content, matching how resizing a browser's
 * `<canvas>` element also clears it (clientWidth/clientHeight below are
 * themselves named after that DOM API). `on close` cannot cancel the
 * close -- there's no "prevent default" mechanism here, it's purely a
 * chance to react (e.g. log a message, save state) before the window
 * actually goes away.
 *
 * festina_client_width/_height report the canvas's *current* size (not
 * a compile-time constant, since `on resize` can change it) -- called
 * for a bare `clientWidth`/`clientHeight` reference in Festina code
 * (see the special case in codegen.py's _emit_expr; there's no
 * "property access without a call" concept anywhere else in this
 * runtime, so this is its own small special case rather than reusing
 * the BUILTIN_FUNCTIONS/_BUILTIN_SIGNATURES machinery the draw
 * functions and loadImage use, which all assume a Call).
 *
 * festina_run_event_loop is what main() calls after __festina_main()
 * returns, whenever a program uses graphics (CodeGen.uses_graphics in
 * festina/codegen.py) -- it used to be named festina_graphics_run, back
 * when graphics was the only thing that could ever block here; it now
 * also fires any pending setTimeout/setInterval callbacks (see the
 * "Timers" note below) on every pass through its select()-driven X11
 * wait, so `on mouseDown` and a setInterval callback both stay responsive
 * together. It lives in festina_runtime_graphics.c, not this file's .c
 * -- a program that never uses graphics calls festina_run_timer_loop
 * instead (declared alongside setTimeout/setInterval below), the same
 * timer-firing behavior with no X11 dependency at all.
 */
#define FESTINA_CANVAS_WIDTH 800
#define FESTINA_CANVAS_HEIGHT 600

void festina_graphics_init(void);
void festina_run_event_loop(void);
void festina_draw_rect(int64_t x, int64_t y, int64_t w, int64_t h);
/* claude.md #133: the optional-`color`-argument forms of drawRect/
 * drawPixel -- paint with `color` for THIS call only (fillStyle/any
 * active gradient are saved and restored around it, untouched
 * afterward), rather than the process-global fillStyle every other
 * draw call uses. Border/alpha are unaffected either way -- only the
 * FILL colour is a per-call override. */
void festina_draw_rect_color(int64_t x, int64_t y, int64_t w, int64_t h, int64_t color);
/* claude.md #188 (uraikus/festina#76 item 8): drawRect(x, y, w, h,
 * fillColor, borderColor) -- overrides BOTH colours for this call
 * only. `border_color < 0` means no border, matching
 * borderColor('none')'s own encoding. See the .c doc comment. */
void festina_draw_rect_colors(int64_t x, int64_t y, int64_t w, int64_t h,
                               int64_t fill_color, int64_t border_color);
void festina_draw_circle(int64_t x, int64_t y, int64_t r);
/* claude.md #188: the same per-call fill/fill+border override forms
 * drawRect already has. */
void festina_draw_circle_color(int64_t x, int64_t y, int64_t r, int64_t color);
void festina_draw_circle_colors(int64_t x, int64_t y, int64_t r,
                                 int64_t fill_color, int64_t border_color);
void festina_draw_text(const char *text, int64_t x, int64_t y);
/* claude.md #133: a single pixel, filled with the current fillStyle
 * (or, for the _color form, `color` for this call only) -- antialiasing
 * is disabled around it so an integer-aligned 1x1 rectangle paints
 * exactly one pixel, deterministically, rather than Cairo's usual edge
 * blending. No border: a 1x1 shape has nothing meaningful to stroke. */
void festina_draw_pixel(int64_t x, int64_t y);
void festina_draw_pixel_color(int64_t x, int64_t y, int64_t color);
/* claude.md #189: getPixelColor(x, y) -- reads one canvas pixel back
 * as a packed `color`, 'none' (-1) out of bounds or where nothing
 * opaque has been painted. See the .c doc comment for the
 * premultiplied-alpha unpacking this needs. */
int64_t festina_get_pixel_color(int64_t x, int64_t y);
void *festina_load_image(const char *path);
/* claude.md #171: <text>.callback(fn) for img -- the img counterpart of
 * festina_blob_load_dispatch, see festina_runtime_graphics.c's own doc
 * comment on festina_image_load_dispatch/festina_image_load_worker for
 * the full design. */
void *festina_image_load_dispatch(const char *path, void (*callback)(void *));
/* claude.md #101: the image counterparts of the two audio entry points
 * above, with one difference -- an image that never came from a file
 * (a clip() or resize() result) has no source bytes, so
 * festina_image_bytes encodes PNG on demand and caches it. */
void *festina_image_from_bytes(const void *data, int64_t len, const char *label);
/* claude.md #110: the image's own path, so save() has somewhere to go.
 * Empty for an image that never came from a file -- a clip() or resize()
 * result, or one decoded out of a database column -- which is exactly
 * the case save(path) exists to serve. */
int8_t festina_image_save(void *img, const char *target);
int8_t festina_image_save_copy(void *img, const char *target);
const void *festina_image_bytes(void *img, int64_t *out_len);
/* claude.md #92: img methods and properties. An `img` value is a
 * pointer to a small box holding the Cairo surface, not the surface
 * itself -- that indirection is what lets resize() change the image in
 * place, so every binding sharing it sees the new size (a Cairo surface
 * cannot be resized in place). clip() returns a NEW image and leaves
 * the source untouched, so one spritesheet can be clipped repeatedly;
 * a region reaching past the edge copies the overlap and leaves the
 * rest transparent rather than failing. Both reject a non-positive
 * width or height, which Cairo would otherwise accept and turn into a
 * surface nothing can draw. */
/* claude.md #93: saves the backing canvas as a PNG via Cairo's own
 * writer, already compiled in alongside the reader loadImage uses. */
int8_t festina_save_canvas(const char *path);

/* claude.md #95: the canvas exists without a window.
 *
 * Drawing paints an offscreen image surface that needs no X server at
 * all; render() is the single call that puts it on screen, opening the
 * window the first time it runs. That split does three things: a
 * program that draws and saves a PNG never opens a window or enters an
 * event loop (so it runs on a build server or over ssh), "does this
 * need a GUI?" becomes answerable by looking for render(), and a frame
 * costs one blit instead of one per shape -- drawing used to flush the
 * whole canvas to X on every single call.
 *
 * clearCanvas() deliberately ignores the current transform (a rotated
 * "erase everything" leaving wedges behind would be a trap);
 * clearRect() honours it, since it names a region in the same
 * coordinates as the drawing around it. */
void festina_render(void);
void festina_clear_canvas(void);
void festina_clear_rect(int64_t x, int64_t y, int64_t w, int64_t h);
/* claude.md #133: the same "erase back to white, honouring the current
 * transform" clearRect() already does, at a circle's and a single
 * pixel's shape instead of a rectangle's. */
void festina_clear_circle(int64_t x, int64_t y, int64_t r);
void festina_clear_pixel(int64_t x, int64_t y);

/* claude.md #94: paths, transforms, gradients and alpha.
 *
 * Every drawing function builds its own short-lived Cairo context, so a
 * transform has to live outside any one of them and be applied to each
 * -- that is what makes translate()/rotate()/scale() affect everything
 * drawn afterwards. saveState()/restoreState() save the whole drawing
 * state (transform, colours, alpha, line width, font), matching the
 * canvas save()/restore() they mirror; restoring a transform while
 * leaving a colour changed is the kind of half-measure that produces
 * baffling bugs.
 *
 * A path is built across separate calls, so one context stays open from
 * beginPath() until fillPath()/strokePath() consumes it -- as in the
 * canvas model, where fill()/stroke() end the current path. Using any
 * of the path builders with no path open is a clean failure naming the
 * missing beginPath().
 *
 * Gradients take exactly two stops. That covers essentially every
 * gradient a program actually draws and needs no new value type, where
 * an n-stop version would need a whole gradient object; a gradient
 * replaces the flat fill until the next fillStyle(). Rotation is in
 * DEGREES -- this language has no angle type to make the unit
 * self-documenting, and Math.PI is there for anyone wanting radians. */
void festina_set_alpha(double alpha);
void festina_fill_linear_gradient(int64_t x0, int64_t y0, int64_t c0,
                                   int64_t x1, int64_t y1, int64_t c1);
void festina_fill_radial_gradient(int64_t x, int64_t y, int64_t radius,
                                   int64_t inner, int64_t outer);
void festina_translate(int64_t x, int64_t y);
void festina_rotate(double degrees);
void festina_scale(double sx, double sy);
void festina_reset_transform(void);
void festina_save_state(void);
void festina_restore_state(void);
void festina_begin_path(void);
void festina_move_to(int64_t x, int64_t y);
void festina_line_to(int64_t x, int64_t y);
void festina_curve_to(int64_t cx1, int64_t cy1, int64_t cx2, int64_t cy2,
                       int64_t x, int64_t y);
void festina_close_path(void);
void festina_fill_path(void);
void festina_stroke_path(void);
int64_t festina_image_width(void *img);
int64_t festina_image_height(void *img);
/* claude.md #189: img.getPixelColor(x, y) -- the img-method
 * counterpart of festina_get_pixel_color above. */
int64_t festina_image_get_pixel_color(void *img, int64_t x, int64_t y);
void *festina_image_clip(void *img, int64_t x, int64_t y, int64_t w, int64_t h);
/* claude.md #188 (uraikus/festina#76 item 4): blankImage(w, h) -- a
 * fresh, fully-transparent img at a given size, with no existing image
 * to derive it from. See the .c doc comment. */
void *festina_blank_image(int64_t w, int64_t h);
/* claude.md #135: saveCanvas() with no path -> a fresh img, a snapshot
 * of the canvas at this instant (see the .c doc comment for why a
 * snapshot rather than a live alias). */
void *festina_canvas_to_image(void);
void festina_image_resize(void *img, int64_t w, int64_t h);
/* claude.md #134: drawRect/drawPixel/drawCircle/drawText as methods on
 * img -- the same four canvas-level functions above, retargeted at an
 * image's own surface, its own local pixel coordinates (no canvas
 * transform applied), but still the same global fillStyle/borderColor/
 * lineWidth/font state every canvas draw call reads. */
void festina_image_draw_rect(void *img, int64_t x, int64_t y, int64_t w, int64_t h);
void festina_image_draw_rect_color(void *img, int64_t x, int64_t y, int64_t w, int64_t h, int64_t color);
/* claude.md #188 (uraikus/festina#76 item 8): the img-method
 * counterparts of festina_draw_rect_colors/festina_draw_circle_color/
 * _colors above. */
void festina_image_draw_rect_colors(void *img, int64_t x, int64_t y, int64_t w, int64_t h,
                                     int64_t fill_color, int64_t border_color);
void festina_image_draw_pixel(void *img, int64_t x, int64_t y);
void festina_image_draw_pixel_color(void *img, int64_t x, int64_t y, int64_t color);
void festina_image_draw_circle(void *img, int64_t x, int64_t y, int64_t r);
void festina_image_draw_circle_color(void *img, int64_t x, int64_t y, int64_t r, int64_t color);
void festina_image_draw_circle_colors(void *img, int64_t x, int64_t y, int64_t r,
                                       int64_t fill_color, int64_t border_color);
void festina_image_draw_text(void *img, const char *text, int64_t x, int64_t y);
void festina_image_free(void *img);
void festina_draw_image(void *img, int64_t x, int64_t y);
/* claude.md #185 (uraikus/festina#76 item 3): drawImage(img, x, y, w,
 * h) -- the WHOLE image scaled to fit w x h at (x, y). */
void festina_draw_image_scaled(void *img, int64_t x, int64_t y, int64_t w, int64_t h);
/* claude.md #185: the canvas-style 8-argument form -- a source rect
 * (sx, sy, sw, sh) scaled to fit a destination rect (dx, dy, dw, dh). */
void festina_draw_image_region(void *img, int64_t sx, int64_t sy, int64_t sw, int64_t sh,
                                int64_t dx, int64_t dy, int64_t dw, int64_t dh);
/* claude.md #89/#90: canvas drawing style -- process-global state set by
 * fillStyle()/borderColor()/lineWidth()/font() and read by every later
 * draw call, the same "set it, then draw" model the HTML canvas 2D
 * context uses.
 *
 * Everything arrives here already resolved. Festina source writes
 * fillStyle('red') and font('arial 14px bold'), but the compiler turns
 * both into the numeric forms below (festina/colors.py), so this
 * runtime holds no colour-name table, no hex parsing and no font
 * grammar, and does none of that work per draw call.
 *
 * A negative colour component means "no colour at all" -- Festina's
 * 'none'/'transparent' -- which needs no extra argument to express,
 * since no real channel value can be negative. For fonts, a
 * non-positive `px` or a NULL string means "leave that aspect alone",
 * which is what lets font('14px') change only the size.
 *
 * The two measure functions deliberately need no canvas window -- text
 * metrics depend only on the font, so they run against a scratch
 * surface. */
/* claude.md #91: the compiled form of a Festina `font` value. Codegen
 * emits one of these as read-only data per distinct font literal (see
 * _emit_font_constant) and changeFont() is handed a pointer to it, so
 * declaring a font costs no runtime work at all. Layout must stay in
 * step with FESTINA_FONT_LLVM_TYPE in festina/codegen.py. `px <= 0`
 * means "leave the size alone" and a NULL family means "leave the
 * family alone", which is what lets `font small = '12px'` change only
 * the size. */
typedef struct {
    int64_t px;
    int64_t slant;   /* 0 normal, 1 italic */
    int64_t weight;  /* 0 normal, 1 bold */
    const char *family;
} FestinaFont;

void festina_set_fill_rgb(int64_t r, int64_t g, int64_t b);
void festina_set_border_rgb(int64_t r, int64_t g, int64_t b);
/* claude.md #91: a `color` value -- packed 0xRRGGBB, negative = 'none'. */
void festina_set_fill_color(int64_t packed);
void festina_set_border_color(int64_t packed);
void festina_set_font_value(const FestinaFont *f);
void festina_set_line_width(int64_t width);
void festina_set_font(int64_t px, const char *style, const char *family);
int64_t festina_measure_text_width(const char *text);
int64_t festina_measure_text_height(const char *text);
/* claude.md #106: `on click` became `on mouseDown` + `on mouseUp`, the
 * same split claude.md #98 made for the keyboard and for the same
 * reason -- a click is a press and a release, and dragging needs to
 * tell them apart. Both take the same fixed signature and both report
 * the pointer position at the moment the button changed state, plus
 * (claude.md #182) which button -- see FestinaWindowEvent's own doc
 * comment in festina_runtime_window.h for the numbering convention.
 * `on mouse` (continuous movement) has no button of its own to report,
 * so it keeps the plain 2-argument signature. */
void festina_register_mouse_down_handler(void (*handler)(int64_t, int64_t, int64_t));
void festina_register_mouse_up_handler(void (*handler)(int64_t, int64_t, int64_t));
void festina_register_mouse_handler(void (*handler)(int64_t, int64_t));
/* claude.md #181: the scroll wheel, split by direction the same way
 * mouseDown/mouseUp are split by press/release -- see semantic.py's
 * _EVENT_SIGNATURES' own comment. Same fixed `(x, y)` signature as the
 * three mouse handlers just above (the pointer's position at the
 * moment of the scroll). */
void festina_register_mouse_wheel_up_handler(void (*handler)(int64_t, int64_t));
void festina_register_mouse_wheel_down_handler(void (*handler)(int64_t, int64_t));
/* claude.md #98: `on key` became `on keyDown` + `on keyUp`, so what
 * was one registration is now two -- both taking the same fixed
 * `void (*)(const char *)` signature and both fed the same key name,
 * so a program can match a release against the press that started it. */
void festina_register_key_down_handler(void (*handler)(const char *));
void festina_register_key_up_handler(void (*handler)(const char *));
void festina_register_resize_handler(void (*handler)(void));
void festina_register_close_handler(void (*handler)(void));
int64_t festina_client_width(void);
int64_t festina_client_height(void);
/* claude.md #139: screenWidth/screenHeight -- the physical display's
 * own resolution (through the windowing seam, festina_window_screen_
 * size), and setClientWidth/setClientHeight -- resizes the canvas
 * (and, if a window is open, the real OS window too), synchronously,
 * whether or not a window exists yet. See festina_runtime_graphics.c's
 * own comments on festina_set_client_size for the full design. */
int64_t festina_screen_width(void);
int64_t festina_screen_height(void);
/* claude.md #181: devicePixelRatio -- through the windowing seam's own
 * festina_window_device_pixel_ratio, the same "answers even with no
 * window open" shape as festina_screen_width/_height just above. See
 * festina_runtime_window.h's own doc comment for the full design. */
double festina_device_pixel_ratio(void);
void festina_set_client_width(int64_t width);
void festina_set_client_height(int64_t height);
/* claude.md #180: enterFullscreen()/exitFullscreen() -- toggles true OS
 * fullscreen on the open window (or records the desired state for
 * festina_graphics_init to apply once one opens), through the windowing
 * seam's own festina_window_set_fullscreen. See
 * festina_runtime_graphics.c's own comments on festina_enter_fullscreen/
 * festina_exit_fullscreen for the full design. */
void festina_enter_fullscreen(void);
void festina_exit_fullscreen(void);
/* claude.md #182: showCursor()/hideCursor() -- toggles the mouse
 * cursor's visibility over the canvas, through the windowing seam's own
 * festina_window_set_cursor_visible. See
 * festina_runtime_graphics.c's own comments on festina_show_cursor/
 * festina_hide_cursor for the full design. */
void festina_show_cursor(void);
void festina_hide_cursor(void);

/*
 * setTimeout/setInterval/clearTimeout/clearInterval -- claude.md #69.
 * Festina otherwise has no way to schedule work after the fact, the
 * same gap JS's setTimeout/setInterval fill.
 *
 * The callback can only be the bare name of an already-declared
 * `void func name() { ... }` -- Festina has no first-class functions
 * or closures (see codegen.py's "functions are not first-class values
 * yet" CodegenError), so an arbitrary expression or inline function
 * literal was never on the table; festina/semantic.py's _infer_call
 * enforces this structurally (the argument must be an ast.Identifier
 * resolving to a declared, zero-parameter, void-returning function),
 * not through the normal expression-typing path. That function's own
 * LLVM symbol is already exactly the `void (*)(void)` function pointer
 * these take -- the same convention festina_register_resize_handler/
 * _close_handler already use for a callback with no arguments.
 *
 * setTimeout/setInterval both return an int timer id, usable with
 * clearTimeout()/clearInterval() (interchangeably, in fact -- both are
 * simply an alias for "deactivate this id if it exists," matching how
 * JS's clearTimeout()/clearInterval() are also interchangeable and
 * never throw on an unknown or already-fired id).
 *
 * Scheduling is cooperative and single-threaded, like JS's own event
 * loop -- a Festina program is never preempted mid-statement to run a
 * timer callback. Instead, timers only ever fire from inside a blocking
 * loop in main() (see festina/codegen.py's _emit_main_and_entry): with
 * graphics in use, festina_run_event_loop (festina_runtime_graphics.c)
 * fires them on every pass through its select()-driven X11/timer
 * multiplexing (so `on mouseDown` and a `setInterval` callback both stay
 * responsive together); without graphics, festina_run_timer_loop (this
 * file's .c) sleeps until the next deadline and fires it, for as long as
 * there's still an active timer to wait for -- both share the same
 * timer bookkeeping and firing logic (festina_fire_expired_timers,
 * private to this runtime, not part of this public header). A program
 * that only ever calls setTimeout() exits once every one-shot timeout
 * has fired; one that calls setInterval() and never clears it runs
 * forever, exactly like an uncleared setInterval() would in a real JS
 * runtime -- it has to be stopped externally (or via clearInterval())
 * the same way. See festina_run_timer_loop/festina_run_event_loop's own
 * doc comments in festina_runtime.c/_graphics.c for the full loop
 * design, including why an interval callback is rescheduled from "now"
 * rather than from its missed deadline (avoids a burst of catch-up
 * calls after a stall).
 */
int64_t festina_set_timeout(void (*callback)(void), int64_t delay_ms);
int64_t festina_set_interval(void (*callback)(void), int64_t delay_ms);
void festina_clear_timeout(int64_t id);
void festina_clear_interval(int64_t id);
/* The no-graphics counterpart to festina_run_event_loop above -- see
 * this section's own doc comment. Always declared (like every function
 * in this header) even though only one of the two loop functions is
 * ever actually *called* by a given compiled program's main() -- a bare
 * `declare` in the generated LLVM IR never forces linking anything (see
 * festina/codegen.py's _runtime_declares), only an actual `call` does,
 * and codegen only ever emits a call to whichever one the program needs. */
void festina_run_timer_loop(void);

/* claude.md #165: the generic async-io hook seam -- blob/img/aud's own
 * `.callback()` (a background file load, the same non-blocking shape
 * claude.md #163 gave http's client req.send()) needs SOME loop to
 * keep running until outstanding work finishes and to drain completed
 * callbacks on the main thread, but unlike http.callback (where ANY
 * use already guarantees festina_run_http_loop is the one running),
 * a program using ONLY blob.callback() -- no openPort(), no graphics,
 * maybe not even a timer -- has no such guarantee: it could end up in
 * festina_run_timer_loop, festina_run_http_loop, OR
 * festina_run_event_loop depending on what ELSE the program does.
 * All three loops therefore check these same two hooks every
 * iteration (see each one's own festina_async_io_outstanding/_drain
 * call) -- both default to "nothing registered" (a plain 0/no-op) so
 * a program that never uses blob/img/aud's own callback form pays
 * nothing beyond one branch per iteration, and never needs to link
 * festina_runtime_async.c (the ONE translation unit that actually
 * registers them, via festina_register_async_io_hooks below, called
 * from main() only when CodeGen.uses_async_io is set) at all -- the
 * same "only link what a program actually uses" split every other
 * optional feature in this runtime already gets. Mirrors
 * festina_set_tls_client_hooks' own registration-seam shape exactly,
 * for the identical cross-translation-unit reason. */
void festina_set_async_io_hooks(int64_t (*outstanding_fn)(void), void (*drain_fn)(void),
                                void (*run_fn)(void *payload, void (*work_fn)(void *payload),
                                               void (*callback)(void *payload),
                                               void (*release_fn)(void *payload)));
int64_t festina_async_io_outstanding(void);
void festina_async_io_drain(void);
/* codegen's own conditional call site (uses_async_io, mirroring
 * uses_https's own festina_register_tls_hooks() call) -- defined in
 * festina_runtime_async.c, registers ITS OWN outstanding/drain/run
 * functions via festina_set_async_io_hooks above. */
void festina_register_async_io_hooks(void);
/* claude.md #165: the ALWAYS-linked-core entry point for dispatching an
 * async-io job -- blob/img/aud's own `.callback()`-non-null dispatch
 * functions (festina_blob_load_dispatch and its img/aud counterparts)
 * call THIS, never festina_runtime_async.c's festina_async_io_run
 * directly (that would be an unconditional cross-translation-unit
 * symbol reference from always-linked core into a conditionally-linked
 * file -- a hard link failure for every program that doesn't use
 * async-io at all). This wrapper goes through the g_async_io_run_fn
 * hook above instead, exactly like festina_async_io_outstanding/_drain
 * already do, and falls back to a synchronous inline run if no hook is
 * registered (unreachable in practice: codegen only emits a call to
 * festina_blob_load_dispatch when uses_async_io is set, which is also
 * what gates linking festina_runtime_async.c and calling
 * festina_register_async_io_hooks from main() -- but a real, correct
 * fallback rather than a silent no-op or a crash). */
void festina_async_io_dispatch(void *payload, void (*work_fn)(void *payload),
                               void (*callback)(void *payload),
                               void (*release_fn)(void *payload));

/* claude.md #166: the http-servicing hook seam -- see this trio's own
 * doc comment in festina_runtime.c for the full reasoning. Lets
 * festina_run_event_loop (festina_runtime_graphics.c) service an open
 * openPort()/openSecurePort() listener without a direct reference into
 * festina_runtime_http.c, the same "always-safe default, no-op unless
 * registered" shape as festina_set_async_io_hooks just above. This is
 * what makes openPort() combinable with graphics -- previously rejected
 * outright at compile time (festina/cli.py). */
void festina_set_http_service_hooks(int64_t (*outstanding_fn)(void), void (*ready_fn)(void));
int64_t festina_http_service_outstanding(void);
void festina_http_service_ready(void);
/* codegen's own conditional call site (uses_http, mirroring
 * uses_async_io's own festina_register_async_io_hooks() call) -- defined
 * in festina_runtime_http.c, registers ITS OWN outstanding/ready
 * functions via festina_set_http_service_hooks above. Registered
 * whenever a program uses http at all (not just when it ALSO uses
 * graphics) -- harmless either way, since festina_run_event_loop is the
 * only thing that ever calls through these hooks, and it's simply never
 * linked into a program that doesn't open a window. */
void festina_register_http_service_hooks(void);

/* claude.md #195 Phase 2: `thread NAME { ... }` -- one pthread per
 * declared thread, two mutex+condvar-guarded FIFO queues (inbound,
 * main -> thread; outbound, thread -> main), living in the new,
 * conditionally-linked festina_runtime_thread.c (CodeGen.uses_threads,
 * mirroring every other optional feature's own -pthread-linked object
 * file -- see festina/cli.py's _RUNTIME_FEATURES["threads"]).
 *
 * FestinaThreadHandle is opaque here on purpose -- codegen only ever
 * carries it around as a plain `ptr` (a global per declared thread,
 * `@__festina_thread_NAME_handle`), the same way it already treats
 * every other runtime-owned handle type (blob/img/aud) as an opaque
 * pointer with no LLVM-visible layout of its own.
 *
 * Message payloads cross both queues as a single `void *payload`: for
 * Phase 2's int/float/bool message types this is a freshly malloc'd
 * 8-byte box holding the raw bit pattern (codegen picks the right
 * load/store width for whichever of the three it is); for text it is
 * simply the malloc'd, NUL-terminated, exclusively-owned string
 * pointer itself (an ordinary festina_text_own() copy) -- no wrapper
 * needed, since text is already "a plain owned buffer" the same way a
 * box is; claude.md #197 Phase 3 widens this to struct/arr[T]/map[T]/
 * enum (each already `ptr`-shaped, and a CLONE of one of these is
 * already a fresh, independent top-level allocation, so it needs no
 * further wrapper either -- see codegen.py's own
 * _thread_payload_is_passthrough). Releasing a delivered payload is
 * therefore no longer always a plain `free()` -- see
 * festina_thread_register's own `in_release`/`out_release`
 * parameters below, and codegen.py's _thread_payload_release_fn. */
typedef struct FestinaThreadHandle FestinaThreadHandle;

/* Registers a new thread (its registry slot, queues, and the three
 * handler function pointers this thread's own `on load`/`on message`/
 * `on exit` bodies compiled to -- NULL for any of the three that
 * weren't declared), but does not spawn the OS thread yet -- see
 * festina_thread_spawn below. Codegen calls this once per declared
 * `thread`, in main()'s own prologue (_emit_main_and_entry), before
 * __festina_main() runs any top-level statement.
 *
 * claude.md #197 Phase 3: `in_release`/`out_release` -- the function
 * to call, on the receiving side, to release ONE delivered payload
 * once its handler/callback has consumed it: `free` for a plain
 * scalar box or an owned text buffer, or a real Festina release
 * cascade (codegen's own _release_fn_for(type_), which already has
 * the exact `void(*)(void*)` shape these need with no adapter) for a
 * struct/arr[T]/map[T]/enum message type -- see
 * codegen.py's own _thread_payload_release_fn. Always a real,
 * callable function pointer, never NULL (a thread with no inbound/
 * outbound type at all still gets `free`, simply never actually
 * invoked on that side, so the runtime never needs a NULL check
 * before calling either one). */
FestinaThreadHandle *festina_thread_register(void (*on_load)(void),
                                             void (*on_message)(void *payload),
                                             void (*on_exit)(int64_t code),
                                             void (*in_release)(void *payload),
                                             void (*out_release)(void *payload));
/* Actually starts the OS thread -- on_load() (if any) runs first, on
 * that new thread, then the worker loop below begins. Split from
 * festina_thread_register so festina_thread_live() (a kill()'d thread
 * coming back) can respawn without re-registering a whole new handle
 * (and therefore a whole new, empty pair of queues) each time. */
void festina_thread_spawn(FestinaThreadHandle *h);
/* `NAME.postMessage(x)` from the MAIN thread's own call sites: clones
 * x into `payload` (codegen's own job, see above) and enqueues it on
 * h's INBOUND queue, waking the worker if it's blocked waiting. */
void festina_thread_post(FestinaThreadHandle *h, void *payload);
/* `postMessage(x)` called from INSIDE this thread's own body: enqueues
 * on h's own OUTBOUND queue instead -- drained on the MAIN thread only
 * (see festina_thread_drain below), never processed by the worker
 * itself. */
void festina_thread_post_outbound(FestinaThreadHandle *h, void *payload);
/* `NAME.onMessage(callback)`: registers the trampoline codegen built
 * for h's own inferred outbound type (unboxes `payload` and makes the
 * real indirect call through whatever Festina callback value the
 * call site's own global currently holds -- see
 * _emit_exec_callback_trampoline's own doc comment in codegen.py for
 * the shape this mirrors). Only ever called from the main thread,
 * before festina_thread_drain can ever actually invoke it for a given
 * message -- see festina_thread_drain's own doc comment on what
 * happens to anything posted before this call runs. */
void festina_thread_set_out_callback(FestinaThreadHandle *h, void (*out_callback)(void *payload));
/* `NAME.kill()`: blocking -- signals the worker to stop, pthread_joins
 * it, then discards anything still sitting in its inbound queue
 * (a real, deliberate choice: "kill" means stop now, not "finish
 * everything already queued first" -- flagged here since the request
 * this feature was built from doesn't say either way). A no-op if h
 * is already not alive. isAlive() is guaranteed false the moment this
 * returns. */
void festina_thread_kill(FestinaThreadHandle *h);
/* `NAME.live(callback)`: respawns a killed thread (running on_load()
 * again) and calls callback(true) once the new OS thread has actually
 * been created. If h is already alive, this is a no-op that still
 * calls callback(true) (already alive trivially satisfies "came back
 * to life"). callback(false) is dead code today: pthread_create
 * failure goes through festina_fail (an immediate, unrecoverable
 * abort) the same way every other "out of resources" runtime failure
 * in this codebase already does, rather than a value this callback
 * could ever observe -- a real bool parameter is still correct, and
 * future-proof, either way. */
void festina_thread_live(FestinaThreadHandle *h, void (*callback)(int8_t alive));
/* `NAME.isAlive()`: reads the plain flag, no lock -- same "an int-
 * sized read/write is atomic enough for this runtime's own existing
 * conventions" reasoning festina_async_io_outstanding's own doc
 * comment already gives for g_outstanding. */
int8_t festina_thread_is_alive(FestinaThreadHandle *h);

/* The hook seam every OTHER optional feature in this header already
 * has one of (mirrors festina_set_async_io_hooks/
 * festina_set_http_service_hooks exactly, for the identical
 * cross-translation-unit reason: core must never reference
 * festina_runtime_thread.c's own symbols directly, or a program with
 * no `thread` declaration at all would need -pthread and that object
 * file linked anyway). festina_thread_outstanding() -- true whenever
 * ANY declared thread is still alive, which is what makes "a thread
 * should idle" actually keep the process running -- and
 * festina_thread_drain() -- delivers every declared thread's own
 * outbound queue to its registered onMessage() callback, one thread's
 * worth at a time, IF that thread has one registered yet (a message
 * posted before the corresponding `.onMessage()` call has run -- a
 * real possibility, since every thread spawns in main()'s prologue,
 * before __festina_main()'s own top-level statements, one of which is
 * what registers it -- simply stays queued rather than being silently
 * dropped; the next drain after registration flushes it) -- are polled
 * once per iteration by all three of this runtime's blocking loops
 * (festina_run_timer_loop here, festina_run_http_loop, and
 * festina_run_event_loop), the same "all three poll the same two
 * hooks" shape festina_async_io_outstanding/_drain already
 * established. festina_thread_kill_all() is called from
 * festina_program_exit, below the exit handler (if any), so a still-
 * idle declared thread never survives as an orphaned process/OS thread
 * once the main program is on its way out. */
void festina_set_thread_hooks(int64_t (*outstanding_fn)(void), void (*drain_fn)(void),
                              void (*kill_all_fn)(void));
int64_t festina_thread_outstanding(void);
void festina_thread_drain(void);
void festina_thread_kill_all(void);
/* codegen's own conditional call site (uses_threads, mirroring
 * uses_async_io's own festina_register_async_io_hooks() call) --
 * defined in festina_runtime_thread.c, registers ITS OWN outstanding/
 * drain/kill_all functions via festina_set_thread_hooks above. */
void festina_register_thread_hooks(void);

/*
 * claude.md #38: aud, loadAudio(), .play()/.stop()/.isPlaying().
 *
 * Playback goes through a real ALSA ("default") output device --
 * ALSA (libasound) rather than a toolkit like SDL_mixer or a
 * PulseAudio client, since it's the lowest-level standard Linux audio
 * API and this project already leans toward the smallest dependency
 * that does the job (claude.md #59; the same reasoning that picked
 * Xlib over a GUI toolkit for graphics).
 *
 * festina_load_audio supports WAV (16-bit PCM), parsed directly here
 * since RIFF is simple enough to walk, and -- since claude.md #101 --
 * MP3 via libmpg123, which claude.md's own `.mp3` example always
 * implied. libmpg123 is the audio counterpart of libjpeg above and was
 * chosen the same way. Format is sniffed from content, not from the
 * file extension. Anything else (a compressed WAV, 8/24/32-bit PCM,
 * Ogg, FLAC, ...) fails at load time with a clear message naming both
 * supported formats, not a crash or silent garbage.
 *
 * festina_audio_play_on(): calling play() opens (and configures) the ALSA
 * device *synchronously*, right there in the play() call itself -- not
 * on the background thread described below -- so a missing or unusable
 * audio device fails loudly and immediately at the call site
 * (festina_fail(), same as "could not open the X display" for
 * graphics) rather than silently doing nothing on a thread with no way
 * to report the failure back. Once the device is open, the actual
 * writing of PCM data happens on a background pthread, so a playing
 * clip doesn't block the rest of the program -- matching what having a
 * separate isPlaying() to poll, and a separate stop() to interrupt,
 * both imply about play() being non-blocking. Calling play() again
 * while a clip is already playing no longer cuts the first playback
 * off (claude.md #98). Each `aud` owns a POOL of voices -- one thread
 * and one ALSA handle per simultaneous playback, all streaming the
 * same decoded PCM buffer read-only, so N voices cost N devices and
 * never N copies of the audio. play() takes the first idle voice; the
 * clip's own samples are loaded once, at loadAudio() time, and are
 * never re-decoded per voice.
 *
 * claude.md #99 made the pool PROCESS-GLOBAL rather than per-`aud`,
 * and named its slots CHANNELS. Two different clips have to be able to
 * share one -- `adventureMusic.playLoop(0)` then
 * `battleMusic.playLoop(0)` hands channel 0 over -- which cannot be
 * expressed at all when each clip owns its own pool and "channel 0"
 * means two different things. It is also what lets
 * stopAudioPlayer(0) be a plain free function instead of something
 * that would have to name a clip to find the channel.
 *
 * The pool defaults to 10 channels, overridable with
 * setMaxAudioPlayers(n) (festina_set_max_audio_players below). When
 * every unreserved channel within the limit is busy, the OLDEST is
 * stolen -- at the limit something has to give, and the sound that has
 * been playing longest is closest to finishing anyway, whereas
 * dropping the NEW play would silence a rapid-fire effect at exactly
 * the moment it fires fastest. At a limit of 1 this reduces exactly to
 * the old restart-from-the-beginning behaviour, which is what makes
 * setMaxAudioPlayers(1) a real way to ask for it back.
 *
 * playLoop() RESERVES its channel: a reserved channel is never chosen
 * by automatic assignment and never stolen, so a looping music track
 * cannot be evicted by an ordinary sound effect. Only an explicit
 * play(n)/playLoop(n) on that exact channel, stopAudioPlayer(n), or
 * that clip's own stop() releases it. An explicit play(n) both takes
 * the channel over and hands it back to the pool, since a one-shot has
 * nothing to reserve it for.
 *
 * claude.md #100 removed the per-clip stop and claude.md #109 brought
 * it back, with the meaning #100 itself identified as its only honest
 * one: festina_audio_stop_clip() stops EVERY channel playing that
 * clip. #100's objection -- that this is almost never what a program
 * firing overlapping effects wants -- is true and was never a reason
 * to withhold it, because "silence this sound wherever it is" is a
 * real thing to want and the alternative was bookkeeping the runtime
 * already does. What #100 was really missing is the other half:
 * festina_audio_play_on() now RETURNS the channel it chose, so a
 * program can address one playback without naming a channel up front.
 * The two answer different questions and now coexist.
 *
 * Channels remain how one playback is addressed:
 * festina_stop_audio_player(n) for one, festina_stop_audio_player(-1)
 * for all. All three *join* rather than merely signalling, so a
 * stopped channel is guaranteed idle the instant the call returns, not
 * just "idle soon"; stopping a channel or a clip with nothing on it is
 * a safe no-op.
 *
 * festina_audio_is_playing() is clip-wide for the same reason
 * festina_audio_stop_clip() is: "is this sound audible anywhere" and
 * "silence it everywhere" are the same question asked two ways.
 *
 * A voice that reaches its own natural end clears itself but stays
 * *joinable*: whoever next claims that slot joins the finished thread
 * first. That is what makes the pool genuinely reusable rather than
 * leaking one thread per play() over a long-running game -- the
 * previous single-voice design could get away with never joining a
 * naturally-finished thread precisely because it only ever had one.
 *
 * Audio does not keep a program running the way an uncleared
 * setInterval does (see festina_set_interval above) -- if main()
 * reaches its natural end while a clip is still playing, the process
 * exits anyway (mirroring a page's audio stopping when the tab
 * closes), it does not wait for playback to finish.
 */
void *festina_load_audio(const char *path);
/* claude.md #171: <text>.callback(fn) for aud -- the aud counterpart of
 * festina_blob_load_dispatch, see festina_runtime_audio.c's own doc
 * comment on festina_audio_load_dispatch/festina_audio_load_worker for
 * the full design. */
void *festina_audio_load_dispatch(const char *path, void (*callback)(void *));
/* claude.md #110: the clip's own path, so save() has somewhere to go.
 * Empty for a clip decoded from bytes (a database column). */
int8_t festina_audio_save(void *audio, const char *target);
int8_t festina_audio_save_copy(void *audio, const char *target);
/* claude.md #101: decoding from memory is the primitive; loading a path
 * is "read the file, then decode the bytes". `label` only names the
 * source in an error message. festina_audio_bytes hands back the bytes
 * the clip was decoded from, for storing an `aud` in a sqlite BLOB
 * column -- so a round trip is byte-identical and an MP3 stays an MP3
 * rather than becoming a much larger WAV. */
void *festina_audio_from_bytes(const void *data, int64_t len, const char *label);
const void *festina_audio_bytes(void *audio, int64_t *out_len);
/* claude.md #101: frees a clip codegen has proven this scope created
 * and never shared -- the aud counterpart of festina_image_free, which
 * `img` has had since claude.md #92. Stops any channel still playing it
 * first, so "freed while a thread is streaming it" cannot happen. */
void festina_audio_free(void *audio);
/* claude.md #38/#99: `channel` names a channel and `explicit_channel`
 * says whether the program actually named one (a bare play() passes 0
 * and gets automatic assignment); `looping` selects playLoop() over
 * play(). One entry point rather than four, because the four differ
 * only in these two flags -- claiming a channel, opening a device and
 * spawning a thread are identical for all of them.
 *
 * claude.md #109: returns the channel actually used, or -1 if nothing
 * was played (a null clip, or every channel reserved with none left to
 * claim). Automatic assignment picks a channel the caller could not
 * otherwise learn, which left the pool addressable only by naming a
 * channel by hand -- that is, by not using the pool. */
int64_t festina_audio_play_on(void *audio, int64_t channel, int8_t explicit_channel,
                               int8_t looping);
int8_t festina_audio_is_playing(void *audio);
/* claude.md #109: stop every channel playing this clip. See the
 * "claude.md #100 removed the per-clip stop" note above. */
void festina_audio_stop_clip(void *audio);
/* claude.md #99: stopAudioPlayer(n) -- stop one channel and release its
 * reservation. A negative channel means every channel, which is what a
 * bare stopAudioPlayer() compiles to. */
void festina_stop_audio_player(int64_t channel);
/* isAudioPlayerPlaying(channel): true while that CHANNEL is playing
 * anything, regardless of clip -- the counterpart festina_audio_
 * is_playing (above) can't stand in for, since that one only ever
 * answers about a clip whose value the caller still has in hand.
 * Clamped into [0, 64) exactly like festina_stop_audio_player. */
int8_t festina_channel_is_playing(int64_t channel);
/* claude.md #98: the channel-pool limit. Clamped into [1, 64] rather
 * than rejected -- this is a tuning knob, and failing a program over a
 * number that is merely unreasonable would be a worse trade than
 * giving it the nearest workable one. The getter exists so a program
 * can read back what it actually got after clamping. claude.md #99:
 * this bounds AUTOMATIC assignment only; an explicitly named channel is
 * honoured anywhere in [0, 64). */
void festina_set_max_audio_players(int64_t max);
int64_t festina_get_max_audio_players(void);

/*
 * claude.md #71: environment.NAME / environment[keyExpr].
 *
 * festina_getenv wraps getenv() directly -- its NULL-if-unset return is
 * already exactly Festina's own null-for-text sentinel, so there's no
 * translation to do (see this function's own comment in
 * festina_runtime.c for why the result isn't copied/strdup'd either).
 */
char *festina_getenv(const char *name);

/*
 * claude.md #77: reference counting for struct/arr[T]/map[T] values
 * escape analysis (claude.md #74/#75/#76) proves DO escape their
 * declaring function -- the remainder that pure escape analysis can
 * never reach on its own. claude.md #106 allowed `struct Node
 * { next:Node }` and made a reference cycle constructible; claude.md
 * #120 answers it with trial deletion (the festina_cycle_* helpers
 * below), so a garbage cycle is collected rather than leaked. See
 * festina_retain/festina_release's own doc comment in
 * festina_runtime.c for the full design (the refcount header layout
 * and the negative-refcount immortal sentinel used for a global's own
 * untouched static initial storage).
 *
 * `payload` is the pointer Festina code itself sees (past the hidden
 * header) -- both functions are always safe to call on any struct
 * value, including a null one (a struct-typed field or global that was
 * never assigned) and including a global's own immortal static
 * storage.
 */
void festina_retain(void *payload);
void festina_release(void *payload);
/* claude.md #78: the decrement-and-check half, so a caller that owns
 * something INSIDE the payload can free it between the decrement and
 * the storage's own free(). Returns 1 exactly when this was the last
 * reference. Declared here since claude.md #109, when festina_blob_
 * release became the first in-runtime user (codegen's generated
 * per-struct wrappers call it through their own declaration). */
int8_t festina_release_check(void *payload);

/* claude.md #120: the type-blind state half of cycle collection --
 * synchronous single-root trial deletion (Bacon-Rajan), driven by
 * compiler-generated per-type traversal functions whenever a value of
 * a possibly-cyclic TYPE is released but still referenced. Color state
 * lives in bits 61-62 of the ordinary refcount header (black=0
 * outside every trial), and every helper is null- and immortal-safe.
 * See the block comment in festina_runtime.c. */
int8_t festina_cycle_candidate(void *p);
int8_t festina_cycle_begin_gray(void *p);
void festina_cycle_dec(void *p);
void festina_cycle_inc(void *p);
int64_t festina_cycle_begin_scan(void *p);
void festina_cycle_set_black(void *p);
int8_t festina_cycle_needs_black(void *p);
int8_t festina_cycle_begin_white(void *p);
void festina_cycle_visit_array(void *payload, void (*fn)(void *));
void festina_cycle_visit_map(void *payload, void (*fn)(void *));
void festina_cycle_dispose_array(void *payload);
void festina_cycle_dispose_map(void *payload);

/*
 * claude.md #36, given its real meaning by claude.md #109: a `blob` is
 * a file's BYTES, loaded from the path it is declared with, keeping
 * that path so the file can be written, appended to, tested for and
 * deleted through the same value. Same content-plus-origin shape as
 * `img` and `aud` (claude.md #101), so a blob serves both a program
 * and a SQLite BLOB column and round-trips byte-identically.
 *
 * Reference counted, unlike img/aud: `blob a = b` shares one handle,
 * reassignment releases the old one, and the last reference frees the
 * contents. It reuses the ordinary struct/arr/map refcount header, so
 * the only new machinery is a destructor that frees the two inner
 * strings first. See festina_runtime.c's own doc comment.
 *
 * Nothing here fails the program, matching the rule claude.md #93 set
 * for the file functions this replaces: an unreadable path yields an
 * empty blob whose .exists() is false, which is also how a file that
 * does not exist yet is created (.write() needs only the path). A blob
 * from festina_blob_from_bytes has bytes and NO path, so its
 * .exists()/.write()/.append()/.delete() all answer false.
 */
void *festina_blob_open(const char *path);
/* claude.md #165: codegen's own entry point for `.callback()` --
 * `callback` NULL is identical to calling festina_blob_open directly;
 * non-NULL returns an empty (not-yet-loaded) blob immediately and
 * fills it in on a background thread, firing `callback` from the main
 * thread once done. See festina_runtime.c's own doc comment. */
void *festina_blob_load_dispatch(const char *path, void (*callback)(void *));
void *festina_blob_from_bytes(const void *data, int64_t len);
void festina_blob_release(void *payload);
char *festina_blob_to_text(void *payload);   /* owned copy, per claude.md #83 */
const void *festina_blob_bytes(void *payload, int64_t *out_len);
int8_t festina_blob_write(void *payload, const char *content);
int8_t festina_blob_append(void *payload, const char *content);
int8_t festina_blob_exists(void *payload);
int8_t festina_blob_delete(void *payload);  /* deletes the FILE, not the blob */

/*
 * claude.md #110: writing a handle's bytes back out. One policy shared
 * by blob, img and aud, since all three are the same shape of value
 * (content plus the bytes it came from) and "save this somewhere" should
 * not mean three slightly different things.
 *
 *   save()          -- write to the path the handle already has.
 *   save(path)      -- adopt `path`, then write there; the handle's own
 *                      path CHANGES, so a blob's exists()/delete()
 *                      follow it afterwards.
 *   saveCopy(path)  -- write there and leave the handle's path alone.
 *                      The argument is required (enforced in semantic.py,
 *                      so it is a compile error rather than a runtime one).
 *
 * `target` NULL/empty is the no-argument save(). `adopt` selects save()
 * over saveCopy(). `what` names the type in an error message.
 *
 * A handle with NO path is what this exists for: an img from clip(), or
 * anything out of a database column, has never been on disk. save() with
 * no argument then FAILS the program rather than returning false -- a
 * program asking to save something to nowhere has a bug, where an
 * unwritable directory is a condition of the filesystem and still
 * answers false, the same as every other file operation here.
 *
 * `target` is always a complete FILE path -- there is no directory
 * shorthand. One would have to borrow a filename from somewhere, and the
 * handle that most needs saving (a clip, a database column) is exactly
 * the one with no filename to borrow, so the shorthand would work only
 * where it was least useful. Passing a directory answers false, like any
 * other unwritable target.
 *
 * The path is adopted only on SUCCESS: pointing a handle at a file that
 * was never written would leave exists() answering false about a path
 * the program was just told it has.
 */
int8_t festina_save_bytes(const char *target, char **own_path,
                          const void *data, int64_t len,
                          const char *what, int8_t adopt);
int8_t festina_blob_save(void *payload, const char *target);
int8_t festina_blob_save_copy(void *payload, const char *target);

/*
 * claude.md #72, rebuilt into a real hash table by #175: map[T] --
 * { key: value, ... } literals, npcHealths[key] read/write,
 * npcHealths.forEach(callback).
 *
 * A map value is a `{ i64 count, ptr entries, i64 capacity, i64
 * tombstones }` header at the LLVM level (festina/codegen.py's
 * FESTINA_MAP_LLVM_TYPE): `entries` points to an OPEN-ADDRESSING hash
 * table of FestinaMapEntry { key, value } buckets (opaque to codegen,
 * only ever passed straight through as a `void *`), linear-probed and
 * FNV-1a hashed (festina_map_find/_find_slot/_map_hash in
 * festina_runtime.c) -- the same shape as this runtime's other hash
 * table, festina_conn_index_* in festina_runtime_http.c, just keyed by
 * text instead of int64_t. `count` is the live entry count, `capacity`
 * the bucket array's length (a power of two), `tombstones` the count
 * of deleted-but-not-yet-reclaimed buckets (grown away on the next
 * rehash -- see festina_map_grow's own comment).
 *
 * Every map value type's payload -- int, float, bool, text, blob,
 * struct, table, img, aud, regex (never another arr[T]/map[T]; see
 * types.MapType's own doc comment for why those don't fit) -- travels
 * through these three functions as a raw i64 regardless of T, since
 * this runtime has no idea what T a given map's values actually are;
 * festina/codegen.py reinterprets to/from each value's real LLVM
 * representation at every call site (_map_value_to_i64/
 * _i64_to_map_value), including inside a small per-call trampoline
 * function for .forEach()'s callback (_emit_map_foreach_trampoline),
 * needed because the callback's own LLVM signature depends on T (e.g.
 * `double` for a map[float]) and can't be called through an i64-typed
 * function pointer directly without a real calling-convention mismatch
 * on plenty of real ABIs.
 *
 * festina_map_set takes `count`/`entries`/`capacity`/`tombstones` BY
 * ADDRESS (pointers into the map value's own storage slot, not the map
 * "object" -- there isn't a separate one), since adding a new key may
 * need to rehash the whole table and the caller needs to see that
 * change; festina_map_get and festina_map_for_each only ever read, so
 * they take `entries`/`capacity` directly (already extracted from an
 * ordinary map value with `extractvalue`, no addressability needed --
 * neither needs `count`, since a bucket scan is driven by `capacity`,
 * not a dense `[0,count)` range).
 *
 * A missing key: "the result is null" (claude.md #72) --
 * festina_map_get returns `default_value` outright when the key isn't
 * found, already computed by codegen as the correct null representation
 * for this map's value type (int/float/pointer all have their own,
 * different encoding -- see the module docstring's "Null for int/float"
 * note; this function has no idea what T is, so it can't make that
 * choice itself).
 */
void festina_map_set(int64_t *count, void **entries, int64_t *capacity, int64_t *tombstones,
                     const char *key, int64_t value);
int64_t festina_map_get(void *entries, int64_t capacity, const char *key, int64_t default_value);
void festina_map_for_each(void *entries, int64_t capacity, void (*callback)(int64_t, const char *));
/* claude.md #186 (uraikus/festina#76 item 7): map[T].keys() -> arr[text]
 * and map[T].values() -> arr[T] -- `dst` is always a fresh header
 * codegen already allocated (_emit_fresh_heap_header); these only fill
 * in its length/data fields. See the implementation's own comment for
 * values()'s `elem_size`/`is_refcounted`/`is_text` arguments. */
void festina_map_keys(void *entries, int64_t capacity, void *dst);
void festina_map_values(void *entries, int64_t capacity, int64_t elem_size,
                         int8_t is_refcounted, int8_t is_text, void *dst);

/* claude.md #74/#75/#175: called by generated code when a map[T]
 * local, proven never to escape its declaring function, goes out of
 * scope. Frees each live bucket's own strdup'd key (see
 * festina_map_set's own comment -- always a private copy, never
 * aliased with anything Festina-visible, so this is always safe
 * regardless of anything escape analysis does or doesn't know) and
 * then the entries buffer itself. A no-op for a map that was declared
 * but never grown (entries is NULL, capacity is 0 -- the loop below
 * simply doesn't run, and free(NULL) is a defined no-op). */
void festina_map_free_entries(void *entries, int64_t capacity);

/* claude.md #197 Phase 3: `thread`'s own deep-clone of a map[T]
 * message/field -- the clone-side mirror of festina_map_for_each.
 * Walks `src_entries`'s own live buckets, strdup's each key, and
 * calls `value_clone_fn` (codegen's own per-value-type i64-in/i64-out
 * trampoline, see _emit_map_value_clone_trampoline) to produce each
 * cloned value before inserting it into a FRESH destination table via
 * festina_map_set -- writing the new count/entries/capacity/
 * tombstones back through the four out-parameters, the same "results
 * land in an out-pointer" shape festina_sqlite_collect_rows already
 * uses. The destination starts genuinely empty (not sized to match
 * `src_capacity`) and grows on its own via festina_map_set's existing
 * load-factor check, so its own bucket layout never has to mirror the
 * source's. A no-op (every out-parameter left at its zero value) when
 * `src_entries` is NULL -- an unpopulated source map. */
void festina_map_clone(void *src_entries, int64_t src_capacity,
                       int64_t *dst_count, void **dst_entries, int64_t *dst_capacity,
                       int64_t *dst_tombstones, int64_t (*value_clone_fn)(int64_t));

/*
 * claude.md #79: releases an arr[T]/map[T] value -- see each
 * function's own doc comment in festina_runtime.c. Like
 * festina_retain/festina_release, always safe to call on any arr[T]/
 * map[T] value, including a null one.
 */
/* claude.md #96: array methods. The header layout is the one
 * festina/codegen.py's FESTINA_ARRAY_LLVM_TYPE describes ({length,
 * data}), shared here the same way the sqlite row layout already is,
 * since these have to resize a buffer codegen allocated. Values move by
 * BYTES with the element size passed in, so one set of functions covers
 * every arr[T] instead of a family per element type.
 *
 * claude.md #174: each gained a `capacity` 2nd parameter -- NULL for a
 * plain arr[T] (festina_array_resize's own unchanged exact-size-realloc
 * behavior), or the address of an `amor arr[T]`'s own tracked capacity
 * field (FESTINA_AMOR_ARRAY_LLVM_TYPE's 3rd field, byte-compatible with
 * plain arr[T]'s {length, data} prefix) for geometric doubling growth
 * instead. See festina_array_resize's own comment for the full layout
 * reasoning.
 *
 * Ownership of a removed element TRANSFERS to whoever receives it
 * (pop/shift hand it back, splice hands it to the returned array), so
 * nothing here releases anything -- that would free a value the caller
 * is about to be given. pop/shift leave *out untouched when there is
 * nothing to remove, because codegen has already stored the element
 * type's own null there. splice clamps exactly as JavaScript's does,
 * negative start included, so `splice(i, 1)` at a boundary is a no-op
 * rather than a crash. `dst_hdr` (the removed-elements result array) is
 * always a plain, freshly malloc'd-to-exactly-the-right-size array
 * regardless of whether the SOURCE array is amor or plain -- it never
 * grows again after being built, so it needs no capacity of its own. */
void festina_array_push(void *hdr, int64_t *capacity, int64_t elem_size, const void *value);
void festina_array_unshift(void *hdr, int64_t *capacity, int64_t elem_size, const void *value);
int8_t festina_array_pop(void *hdr, int64_t *capacity, int64_t elem_size, void *out);
int8_t festina_array_shift(void *hdr, int64_t *capacity, int64_t elem_size, void *out);
void festina_array_splice(void *hdr, int64_t *capacity, int64_t elem_size, int64_t start,
                           int64_t count, void *dst_hdr);
/* claude.md #130: the 3-argument splice(start, count, insertArr) form --
 * JavaScript's splice(start, deleteCount, ...items), spelled with an
 * explicit array in place of variadic items since Festina has no
 * variadic parameters. Removes `count` elements starting at `start`
 * (handed back through `dst_hdr`, exactly like the 2-argument form
 * above) and inserts `insert_len` raw elements from `insert_data` in
 * their place -- codegen retains/copies each inserted element itself
 * afterward (see codegen.py's _emit_retain_or_own_range), since this
 * function only moves bytes and has no notion of a Festina type. */
void festina_array_splice_insert(void *hdr, int64_t *capacity, int64_t elem_size, int64_t start,
                                  int64_t count, const void *insert_data,
                                  int64_t insert_len, void *dst_hdr);
/* claude.md #97: the first index holding `value`, or -1 if absent.
 * -1 rather than null because the answer is an index and every use of
 * one is a comparison or a splice argument. Compares the raw 8-byte
 * slot, which is right for int/float/bool and for identity on
 * struct/arr/map; `text` sets is_text so equal strings in different
 * buffers still match. */
int64_t festina_array_index_of(void *hdr, int64_t elem_size,
                                const void *value, int8_t is_text);
/* claude.md #184 (uraikus/festina#76 item 2): in-place, stable sort.
 * `cmp` is always codegen's own generated per-element-type trampoline
 * (_emit_sort_comparator_trampoline); `userdata` is the real Festina
 * comparator function value, a bare pointer passed straight through
 * unchanged on every comparison -- not qsort()/qsort_r()/qsort_s(),
 * whose userdata-carrying variants disagree on argument order across
 * glibc/BSD/Windows. See the implementation's own comment. */
void festina_array_sort(void *hdr, int64_t elem_size,
                         int (*cmp)(const void *, const void *, void *),
                         void *userdata);
void festina_release_array(void *payload);
void festina_release_map(void *payload);
/* claude.md #167: the value-aware counterpart to festina_release_map,
 * for a map[text]-shaped payload this runtime built directly in C
 * (never through codegen, which already generates its own value-aware
 * wrapper per Festina-level map[text] variable) -- frees each entry's
 * own owned text VALUE before deferring to the same entries/header
 * cleanup festina_release_map itself uses. See that function's own doc
 * comment for why the generic one is wrong for this shape. */
void festina_release_text_map(void *payload);

/* claude.md #151: openPort/on request/on upgrade/on message/on
 * socketClose -- a single-threaded HTTP + WebSocket server, in its
 * own translation unit (festina_runtime_http.c) so a program that
 * never calls openPort() never links any of it, the same per-feature
 * split graphics/audio already use. Linux/macOS/Windows -- see that
 * file's own top comment for the winsock2 porting Windows needed
 * (a real seam, not a recompile: a distinct SOCKET handle type,
 * closesocket()/WSAPoll()/ioctlsocket() in place of
 * close()/poll()/fcntl(), WSAGetLastError() in place of errno).
 * There is no WASI backend at all (WASI Preview 1, this project's own
 * wasm target, has no listening-socket support) -- rejected at
 * COMPILE time (_check_platform_feature_supported/
 * _check_wasm_feature_supported), never a link failure.
 *
 * DESIGN, single-threaded event loop (per this feature's own explicit
 * scoping): every connection is serviced from the SAME thread
 * festina_run_http_loop() runs on, via poll() -- the same "one thread
 * total" model setTimeout/setInterval and the graphics event loop
 * already use, extended here rather than reinvented. This is what
 * keeps festina_retain/festina_release non-atomic plain increments/
 * decrements everywhere else in this runtime; a thread-per-connection
 * model would need every one of those to become atomic (or locked),
 * a much bigger, whole-runtime change genuinely out of scope for a
 * server feature specifically. The real cost: a slow `on request`/
 * `on message` handler (one that blocks, or just does a lot of work)
 * delays every OTHER connection's own turn -- acceptable for the
 * kind of small, script-shaped server program this language already
 * targets, not a general-purpose production HTTP server replacement.
 *
 * DESIGN, socket VALUES: a refcounted opaque handle
 * (`festina_release_conn_handle` below) -- NOT a pointer to live
 * connection state, a tiny malloc'd `{refcount, conn_id}` pair. Every
 * runtime call that takes one (festina_socket_send_text, ...) looks
 * `conn_id` up in the connection table fresh, on every call, and
 * silently does nothing (or answers a null/false/-1, matching
 * whatever "nothing happened" already means for that call) if the
 * connection is no longer there -- the same "never fails the
 * program" convention exec()/mkdir()/the file builtins already use,
 * extended to cover a REAL use-after-teardown case a server
 * genuinely has to tolerate (a client disconnects mid-handler, or a
 * program stores `s` somewhere that outlives the connection). conn_id
 * is a monotonic counter, never reused, specifically so a stale id
 * can never alias a DIFFERENT, later connection that happens to reuse
 * the same fd -- the classic fd-reuse-after-close bug this
 * indirection exists to rule out by construction.
 *
 * DESIGN, http VALUES (claude.md #162, superseding the ORIGINAL
 * "same handle shape as socket" design claude.md #151 shipped):
 * http is a genuine refcounted VALUE now, not a handle -- url/method/
 * code/headers/body all live directly in it (see
 * festina_runtime_http.c's own FestinaHttpValue doc comment), copied
 * out once at construction time rather than looked up fresh from the
 * connection table on every field read. This is what lets an http
 * value be constructed directly by a program (`http x = {...}`),
 * returned by a client req.send(), or simply outlive its originating
 * connection (if it ever had one) with everything still readable.
 * conn_id is the one field that still reaches back into the
 * connection table -- 0 for a value with no live connection behind it
 * at all, in which case .ok()/.redirect()/.upgrade()/.send(res) are
 * silent no-ops (the exact same "never crashes on a value that
 * doesn't apply" tolerance the old handle design already had), while
 * .toText()/.toBlob()/.toImg()/.toAud() work identically either way,
 * live or not.
 *
 * DESIGN, http/1.1 scope: request-line + headers + a Content-Length OR
 * chunked (claude.md #168) body, both request and response direction;
 * no pipelining as a genuine wire optimization (a pipelining client's
 * own buffered-ahead requests are still all served correctly, just one
 * at a time, off a single connection -- see festina_conn_readable's
 * own dispatch loop). claude.md #167 added HTTP/1.1 keep-alive: a
 * response leaves the connection open for another request unless the
 * request sent `Connection: close` (HTTP/1.0 still defaults to close
 * unless it explicitly asks for keep-alive) -- so the per-connection
 * state machine is accept -> read a request -> dispatch -> respond ->
 * EITHER reset for another request on the same fd OR close, not the
 * once-unconditional straight line to close this comment used to
 * describe. See api.md's own http Limitations section (and its
 * Keep-alive subsection) for the honest, current accounting of what
 * this does and doesn't do.
 *
 * DESIGN, WebSocket scope: RFC 6455 text/binary data frames, close
 * frames, and fragmentation (claude.md #168 -- reassembled correctly
 * now, including a control frame interleaved between another message's
 * own fragments per §5.4), but no ping/pong keepalive SENT by this
 * runtime (a received ping is answered with a pong automatically, a
 * received pong is read and ignored, neither ever crashes the
 * connection), no permessage-deflate or any other extension. A
 * received frame -- text or binary -- always reaches `on message` as
 * a `blob` (never as `text` directly): the language has no "this
 * value might be text or might be bytes" type to hand back instead,
 * and a blob's own .toText() is one call away for a program that
 * knows its peer only ever sends text frames.
 */
void festina_open_port(int64_t port);
void festina_close_port(int64_t port);

/* claude.md #160: openSecurePort(port, key) -- the TLS counterpart to
 * openPort() above, sharing the same listener table, connection
 * table, and single-threaded poll() event loop (a TLS listener is
 * just a FestinaListener with a non-NULL tls_config; a TLS connection
 * just a FestinaConn with non-NULL tls -- see festina_runtime_http.c's
 * own struct comments). `key` is a combined PEM blob (certificate,
 * or a full chain leaf-first, and the matching UNENCRYPTED private
 * key, in either order) -- see festina_runtime_https.c's own top
 * comment for the full design writeup (mbedTLS 2.x, server-only, no
 * client certs, no SNI, no ALPN) and setup.md for the new system
 * dependency this introduces. Same "never fails the program on a bad
 * port number" contract as openPort() -- but a malformed/mismatched
 * certificate or key DOES fail the program (via festina_fail, with
 * the real mbedTLS error text), the same "test, don't fail" vs. "a
 * program-authoring mistake" line claude.md #59 already draws for
 * every other builtin. */
void festina_open_secure_port(int64_t port, const uint8_t *key, int64_t key_len);

/* claude.md #160: registers festina_runtime_https.c's own seven-
 * function TLS hook table (see that file's own top comment) --
 * generated code's own main() calls this, and ONLY this, exactly
 * once, exactly when self.uses_https (festina/codegen.py's
 * _emit_main_and_entry), so a program that never calls
 * openSecurePort() never references (and therefore never needs
 * linked) a single mbedTLS-touching symbol. festina_set_tls_hooks
 * itself lives in festina_runtime_http.c (it stores into that file's
 * own static g_tls_* pointers) and is declared here only so
 * festina_runtime_https.c -- a different translation unit -- can call
 * it from festina_register_tls_hooks below. */
void festina_register_tls_hooks(void);
void festina_set_tls_hooks(
    void *(*listener_new)(const uint8_t *pem, int64_t pem_len),
    void (*listener_free)(void *tls_config),
    void *(*conn_new)(void *tls_config, int fd),
    void (*conn_free)(void *tls_state),
    int (*handshake)(void *tls_state),
    long (*recv_fn)(void *tls_state, void *buf, int64_t cap),
    long (*send_fn)(void *tls_state, const void *data, int64_t len));

void festina_register_request_handler(void (*fn)(void *req));
void festina_register_upgrade_handler(void (*fn)(void *sock));
void festina_register_message_handler(void (*fn)(void *sock, void *msg));
void festina_register_socketclose_handler(void (*fn)(void *sock));

/* The blocking loop main() enters when the program calls openPort()
 * anywhere (self.uses_http in codegen.py) -- folds in
 * festina_next_timer_deadline()/festina_fire_expired_timers() exactly
 * the way festina_run_event_loop (graphics) already does, so a
 * program combining openPort() with setTimeout/setInterval gets both
 * serviced from this one loop rather than two competing blocking
 * calls. Exits once there is truly nothing left to wait for: no open
 * listening port, no live connection, and no active timer -- the
 * same "exits once the event loop is empty" rule
 * festina_run_timer_loop's own doc comment already states, widened
 * to cover open sockets too. An open listening port with nothing
 * else going on therefore keeps a program running forever (it has
 * to -- that's what "listening" means), the same way an uncleared
 * setInterval() already does. */
void festina_run_http_loop(void);

/* http -- claude.md #162's redesign: a genuine refcounted VALUE (see
 * festina_runtime_http.c's own FestinaHttpValue doc comment), not the
 * old {refcount, conn_id} handle -- url/method/code/headers/body all
 * live directly in it now. `url`/`method`/`code`/`headers` are
 * read-only via dot-access (see semantic.py's _infer_member HttpType
 * branch; codegen never emits a store through any of these) -- the
 * only way to SET them is the literal-construction syntax
 * (festina_http_literal_new below) at creation time.
 *
 * festina_http_literal_new is codegen's own entry point for
 * `http x = {...}` -- takes ownership of `headers` (NULL means "the
 * literal named no headers key", answered with a fresh empty map
 * instead), copies body/body_len (the caller's own temporary buffer
 * stays the caller's to free afterward, same convention
 * festina_blob_from_bytes already uses).
 *
 * festina_http_url/_method return an owned text COPY (this value's
 * own field is never handed out directly, so a caller mutating the
 * returned text -- impossible in this language, but still -- could
 * never reach back into the value itself). festina_http_headers
 * returns the SAME live map every call, already retained on the way
 * out (contrast the OLD festina_http_headers, which rebuilt a fresh
 * one on every single read) -- the identical "same live value,
 * retained" contract festina_socket_state below already has.
 *
 * claude.md #163: `callback` is a 5th field -- a bare function pointer
 * (NULL for none), the same runtime representation every other
 * `func`-typed value already has, so it needs no separate release
 * function of its own. Non-NULL is what makes festina_http_send_client_dispatch
 * (below) take req.send()'s non-blocking path. */
void *festina_http_literal_new(const char *url, const char *method, int64_t code,
                               void *headers, const uint8_t *body, int64_t body_len,
                               void (*callback)(void *));
char *festina_http_url(void *payload);
char *festina_http_method(void *payload);
int64_t festina_http_code(void *payload);
void *festina_http_headers(void *payload);
void *festina_http_callback(void *payload);  /* the bare function pointer, or NULL */

/* http -- methods. ok/redirect/upgrade/send(res) are each a no-op
 * (not an error) if this value isn't bound to a live, still-open
 * connection (a plain constructed value, a client response, or a
 * connection that's already responded once or is no longer live) --
 * "only the FIRST response action wins, and only a LIVE request can
 * respond at all" is enforced here, not left to the caller to avoid
 * by hand. toBlob/toImg/toAud/toText read this value's own body
 * directly -- no connection lookup at all, so these work identically
 * whether `payload` is live or not. */
void festina_http_ok(void *payload);
void festina_http_redirect(void *payload, const char *url);
void festina_http_upgrade(void *payload);
void *festina_http_to_blob(void *payload);   /* the body, fresh blob */
void *festina_http_to_img(void *payload);    /* body decoded as an image */
void *festina_http_to_aud(void *payload);    /* body decoded as audio */
char *festina_http_to_text(void *payload);   /* the body, as owned text */
/* req.send(res:http) -- the SERVER side: sends res's own code
 * (defaulting to 200 when res.code is still null)/headers/body as
 * this LIVE request's response. `res_payload` is only read from, never
 * mutated or its ownership taken. */
void festina_http_send(void *req_payload, void *res_payload);
/* req.send() (zero-argument) / codegen's own client dispatch -- the
 * CLIENT side: an outbound request built from `payload`'s own url/
 * method/headers/body, MUTATING `payload` in place afterward: code/
 * headers/body are overwritten with the response (url/method are left
 * alone -- they still describe what was sent). THROWS (claude.md #157)
 * on a genuine network/protocol failure (DNS resolution, connect, TLS
 * handshake, an unparseable response) -- catchable by an enclosing
 * try, the same design claude.md #159's JSON parser already
 * established for "this can fail with real diagnostic text" runtime
 * primitives. Blocking -- see festina_runtime_http.c's own comment on
 * why that's an accepted, already-established tradeoff, not an
 * oversight. https:// needs festina_set_tls_client_hooks (below)
 * registered first -- codegen only omits that when the program can
 * prove no https:// URL could ever reach this call, which in practice
 * (a runtime string) it never can, so every program using req.send()
 * on the client side links mbedTLS the same way openSecurePort()
 * does. */
void festina_http_send_client(void *payload);
/* claude.md #163: codegen's own entry point for req.send() (zero
 * arguments) -- replaces the old direct festina_http_send_client call.
 * Checks `payload`'s own `.callback` field at RUNTIME: NULL takes the
 * exact festina_http_send_client path above (still fully blocking,
 * unchanged behavior); non-NULL hands the request to a background
 * worker pool instead and returns immediately, running `callback`
 * later from the main thread's own event loop once the request
 * completes (success or a caught network failure -- see
 * festina_runtime_http.c's own "http -- async client" section).
 * POSIX only for now (Linux/macOS) -- on Windows this is currently
 * identical to calling festina_http_send_client directly; `callback`
 * is simply not consulted there yet (see api.md). */
void festina_http_send_client_dispatch(void *payload);
/* claude.md #162: registers festina_runtime_https.c's own TLS CLIENT
 * hooks (mirroring festina_set_tls_hooks' own SERVER-side registration
 * -- see that function's own doc comment for the identical cross-
 * translation-unit reasoning) -- called from festina_register_tls_hooks
 * itself, not separately, so a program linking mbedTLS at all gets
 * both halves registered together. */
void festina_set_tls_client_hooks(
    void *(*client_connect)(int fd, const char *hostname),
    long (*recv_fn)(void *tls_state, void *buf, int64_t cap),
    long (*send_fn)(void *tls_state, const void *data, int64_t len),
    void (*close_fn)(void *tls_state));
void festina_release_http(void *payload);

/* s:socket -- state/send/close. festina_socket_state returns the
 * SAME live, already-retained map[text] every call for this
 * connection (not a fresh copy) -- writes through it
 * (`s.state['k'] = v`, ordinary map codegen once the pointer is in
 * hand) persist for the connection's whole lifetime, released only
 * when the connection itself is torn down. Returns NULL if the
 * connection is no longer live (see this header's own top comment on
 * why every socket call tolerates that rather than crashing). */
void *festina_socket_state(void *handle);
void festina_socket_send_text(void *handle, const char *text);
void festina_socket_send_binary(void *handle, const void *data, int64_t len);
void festina_socket_close(void *handle);

/* claude.md #162: socket's OWN release function now -- http moved to
 * festina_release_http above (a real value with real contents to
 * free), the SocketType branch of codegen's own _release_fn_for is
 * the only dispatch still reaching this one. Frees only the tiny
 * {refcount, conn_id} handle itself, never the underlying connection
 * (owned by the connection table, torn down separately when the
 * connection actually closes). */
void festina_release_conn_handle(void *payload);

#endif
