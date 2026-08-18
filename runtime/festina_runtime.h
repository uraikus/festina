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
/* claude.md #114: the string builder behind JSON-like rendering of
 * structs/rows/arrays/maps in log() and `${}`. Structure walking lives
 * in generated IR; byte handling lives here. See the .c doc comment. */
void *festina_sb_new(void);
void festina_sb_append(void *sb, const char *s);
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
void festina_sqlite_collect_rows(sqlite3_stmt *stmt, int32_t col_count,
                                  const char **col_types, const char **col_names,
                                  int64_t *out_length, void **out_data);
int8_t festina_row_undefined(void *row, const char **col_names,
                             int32_t col_count, const char *name);
/* claude.md #111: `delete m[key]` -- removes the entry, releasing its
 * value through the same per-type trampoline whole-map release uses.
 * Returns whether the key existed; a missing key is a safe no-op. */
int8_t festina_map_delete(int64_t *count, void **entries, const char *key,
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
 * window is undecorated (Motif WM hints -- a widely honored
 * convention, though not part of the core X11 protocol, so a given
 * window manager could still ignore it) so it shows only the canvas,
 * nothing else -- no title bar, menu, or other chrome drawn by this
 * runtime.
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
 * backing store, ButtonPress -> the registered click handler if any,
 * MotionNotify -> the registered mouse handler if any, KeyPress -> the
 * registered key handler if any, ConfigureNotify with a genuine size
 * change -> resize the backing store and call the registered resize
 * handler if any, the window's close button -> call the registered
 * close handler if any, then return). festina_graphics_init is only
 * ever called by generated code when the program actually uses a
 * graphics function, references clientWidth/clientHeight, or declares
 * an `on mouseDown`/`mouseUp`/`mouse`/`key`/`resize`/`close` handler (see
 * CodeGen.uses_graphics in festina/codegen.py) -- a program that
 * doesn't never opens a window, exactly like festina_db_open() only
 * ever runs for a program that declares a `table`.
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
 * festina_register_mouse_down_handler/_mouse_up_handler/_mouse_handler
 * take a fixed `void (*)(int64_t, int64_t)` signature,
 * festina_register_key_down_handler/_key_up_handler take a fixed
 * `void (*)(const char *)` signature, and
 * festina_register_resize_handler/_close_handler take a fixed
 * `void (*)(void)` signature -- each matches the parameters claude.md
 * #40's own worked example declares for that event exactly
 * (`on mouseDown(x:int, y:int)`, `on keyDown(key:text)`, `on resize()`,
 * ...); festina/semantic.py's _EVENT_SIGNATURES enforces that any
 * handler for one of these seven names is actually declared that way
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
void festina_draw_circle(int64_t x, int64_t y, int64_t r);
void festina_draw_text(const char *text, int64_t x, int64_t y);
void *festina_load_image(const char *path);
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
void *festina_image_clip(void *img, int64_t x, int64_t y, int64_t w, int64_t h);
void festina_image_resize(void *img, int64_t w, int64_t h);
void festina_image_free(void *img);
void festina_draw_image(void *img, int64_t x, int64_t y);
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
 * the pointer position at the moment the button changed state. */
void festina_register_mouse_down_handler(void (*handler)(int64_t, int64_t));
void festina_register_mouse_up_handler(void (*handler)(int64_t, int64_t));
void festina_register_mouse_handler(void (*handler)(int64_t, int64_t));
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
 * never reach on its own. Complete (not just "handles everything but
 * cycles") for Festina specifically -- or so it was, until claude.md
 * #106 allowed `struct Node { next:Node }` and made a reference cycle
 * constructible. A cycle is now a permanent leak (see todo.md); every
 * acyclic value is still reclaimed exactly as described.
 * See festina_retain/festina_release's
 * own doc comment in festina_runtime.c for the full design (the
 * refcount header layout, the negative-refcount immortal sentinel used
 * for a global's own untouched static initial storage, and why no
 * cycle-breaking machinery is needed at all).
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
 * claude.md #72: map[T] -- { key: value, ... } literals,
 * npcHealths[key] read/write, npcHealths.forEach(callback).
 *
 * A map value is a `{ i64 count, ptr entries }` pair at the LLVM level
 * (festina/codegen.py's FESTINA_MAP_LLVM_TYPE) -- the same two-field
 * shape as arr[T]'s own `{ i64 length, ptr data }`, just never
 * interchangeable with it (see FESTINA_MAP_LLVM_TYPE's own comment):
 * `entries` points to a flat array of FestinaMapEntry { key, value }
 * pairs (opaque to codegen, only ever passed straight through as a
 * `void *`), found by a linear scan (festina_map_find in
 * festina_runtime.c -- not a hash table; see that function's own
 * comment on why that's a deliberate, documented tradeoff, not an
 * oversight).
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
 * festina_map_set takes `count`/`entries` BY ADDRESS (pointers into the
 * map value's own storage slot, not the map "object" -- there isn't a
 * separate one), since adding a new key may need to grow the backing
 * array and the caller needs to see that change; festina_map_get and
 * festina_map_for_each only ever read, so they take `count`/`entries`
 * directly (already extracted from an ordinary map value with
 * `extractvalue`, no addressability needed).
 *
 * A missing key: "the result is null" (claude.md #72) --
 * festina_map_get returns `default_value` outright when the key isn't
 * found, already computed by codegen as the correct null representation
 * for this map's value type (int/float/pointer all have their own,
 * different encoding -- see the module docstring's "Null for int/float"
 * note; this function has no idea what T is, so it can't make that
 * choice itself).
 */
void festina_map_set(int64_t *count, void **entries, const char *key, int64_t value);
int64_t festina_map_get(int64_t count, void *entries, const char *key, int64_t default_value);
void festina_map_for_each(int64_t count, void *entries, void (*callback)(int64_t, const char *));

/* claude.md #74/#75: called by generated code when a map[T] local,
 * proven never to escape its declaring function, goes out of scope.
 * Frees each entry's own strdup'd key (see festina_map_set's own
 * comment -- always a private copy, never aliased with anything
 * Festina-visible, so this is always safe regardless of anything
 * escape analysis does or doesn't know) and then the entries buffer
 * itself. A no-op for a map that was declared but never grown
 * (entries is NULL, count is 0 -- the loop below simply doesn't run,
 * and free(NULL) is a defined no-op). */
void festina_map_free_entries(int64_t count, void *entries);

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
 * Ownership of a removed element TRANSFERS to whoever receives it
 * (pop/shift hand it back, splice hands it to the returned array), so
 * nothing here releases anything -- that would free a value the caller
 * is about to be given. pop/shift leave *out untouched when there is
 * nothing to remove, because codegen has already stored the element
 * type's own null there. splice clamps exactly as JavaScript's does,
 * negative start included, so `splice(i, 1)` at a boundary is a no-op
 * rather than a crash. */
void festina_array_push(void *hdr, int64_t elem_size, const void *value);
void festina_array_unshift(void *hdr, int64_t elem_size, const void *value);
int8_t festina_array_pop(void *hdr, int64_t elem_size, void *out);
int8_t festina_array_shift(void *hdr, int64_t elem_size, void *out);
void festina_array_splice(void *hdr, int64_t elem_size, int64_t start,
                           int64_t count, void *dst_hdr);
/* claude.md #97: the first index holding `value`, or -1 if absent.
 * -1 rather than null because the answer is an index and every use of
 * one is a comparison or a splice argument. Compares the raw 8-byte
 * slot, which is right for int/float/bool and for identity on
 * struct/arr/map; `text` sets is_text so equal strings in different
 * buffers still match. */
int64_t festina_array_index_of(void *hdr, int64_t elem_size,
                                const void *value, int8_t is_text);
void festina_release_array(void *payload);
void festina_release_map(void *payload);

#endif
