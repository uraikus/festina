#ifndef FESTINA_RUNTIME_H
#define FESTINA_RUNTIME_H

#include <stdint.h>
#include <sqlite3.h>

/* Note on what does NOT appear in this header: no Cairo, X11, ALSA, or
 * <regex.h> type ever crosses a public function's signature -- every
 * such value is opaqued to `void *` (regex_t* from festina_regex_compile,
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
 * an `on click`/`mouse`/`key`/`resize`/`close` handler (see
 * CodeGen.uses_graphics in festina/codegen.py) -- a program that
 * doesn't never opens a window, exactly like festina_db_open() only
 * ever runs for a program that declares a `table`.
 *
 * festina_load_image supports PNG only, via Cairo's own built-in
 * decoder -- claude.md #37: "Supported image formats are determined by
 * the runtime," and PNG is the one format Cairo can decode without
 * pulling in another image-format library.
 *
 * festina_register_click_handler/_mouse_handler take a fixed
 * `void (*)(int64_t, int64_t)` signature, festina_register_key_handler
 * takes a fixed `void (*)(const char *)` signature, and
 * festina_register_resize_handler/_close_handler take a fixed
 * `void (*)(void)` signature -- each matches the parameters claude.md
 * #40's own worked example declares for that event exactly
 * (`on click(x:int, y:int)`, `on key(key:text)`, `on resize()`, ...);
 * festina/semantic.py's _EVENT_SIGNATURES enforces that any handler for
 * one of these five names is actually declared that way before codegen
 * ever emits a call here, so a mismatch would otherwise be a silent ABI
 * mismatch rather than a caught compile error. The key handler's text
 * comes from XLookupString (a key that types a character, e.g. "a",
 * "5", " ") falling back to XKeysymToString (a named key with no text
 * of its own, e.g. "Left", "Escape", "Return") -- see
 * festina_handle_graphics_event's own comment in festina_runtime.c.
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
 * wait, so `on click` and a setInterval callback both stay responsive
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
void festina_draw_image(void *img, int64_t x, int64_t y);
void festina_register_click_handler(void (*handler)(int64_t, int64_t));
void festina_register_mouse_handler(void (*handler)(int64_t, int64_t));
void festina_register_key_handler(void (*handler)(const char *));
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
 * multiplexing (so `on click` and a `setInterval` callback both stay
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
 * festina_load_audio only supports WAV (16-bit PCM) -- claude.md's own
 * example names a `.mp3`, but unlike Cairo (which decodes PNG on its
 * own, so images support PNG "for free") nothing this project already
 * depends on can decode MP3 without a real new library, so WAV -- a
 * container simple enough to parse directly in festina_runtime.c with
 * zero decoder dependencies at all -- is the implementation-defined
 * choice here, the same kind of call PNG-only images already made.
 * Anything else (a compressed WAV, 8/24/32-bit PCM, an actual MP3,
 * ...) fails at load time with a clear message, not a crash or silent
 * garbage.
 *
 * festina_audio_play(): calling play() opens (and configures) the ALSA
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
 * while a clip is already playing restarts it from the beginning
 * (stopping the previous playback thread first) -- claude.md #38
 * doesn't say what play()-while-playing should do; this is the least
 * surprising choice, matching a browser's own `<audio>` element.
 *
 * festina_audio_stop() signals the background thread and *joins* it
 * before returning, so festina_audio_is_playing() is guaranteed false
 * the instant stop() returns, not just "false soon" -- calling stop()
 * when nothing is playing is a safe no-op. A clip that reaches its own
 * natural end (never stopped) also sets playing back to false, but its
 * thread is never explicitly joined by anything in that case -- for a
 * typical short-lived compiled Festina program this is an accepted,
 * deliberate tradeoff (the OS reclaims everything at process exit
 * regardless) rather than machinery to track and join every
 * already-finished playback thread that nothing asked to stop.
 *
 * Audio does not keep a program running the way an uncleared
 * setInterval does (see festina_set_interval above) -- if main()
 * reaches its natural end while a clip is still playing, the process
 * exits anyway (mirroring a page's audio stopping when the tab
 * closes), it does not wait for playback to finish.
 */
void *festina_load_audio(const char *path);
void festina_audio_play(void *audio);
void festina_audio_stop(void *audio);
int8_t festina_audio_is_playing(void *audio);

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
 * cycles") for Festina specifically: a struct field's type, and an
 * arr[T]/map[T]'s own element type, must always be declared *before*
 * the struct/array/map containing it (verified directly -- even
 * `struct Node { next:Node }` fails to compile), so no value can ever
 * transitively reference itself. See festina_retain/festina_release's
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
void festina_release_array(void *payload);
void festina_release_map(void *payload);

#endif
