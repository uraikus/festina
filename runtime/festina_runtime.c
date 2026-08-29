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
#include <ctype.h>   /* isdigit/tolower -- claude.md #159's JSON parser */
#include <errno.h>
#include <regex.h>
#if !defined(__wasi__)
#include <signal.h>  /* sig_atomic_t/signal/SIGINT/SIGTERM -- claude.md #161's
                       * graceful shutdown. wasi-libc's own <signal.h> is an
                       * unconditional #error unless compiled with
                       * -D_WASI_EMULATED_SIGNAL (confirmed directly) -- WASI
                       * has no signal model at all, so this is skipped
                       * outright for that target, not merely left with
                       * unused declarations (see the matching #else stubs
                       * below, right where sig_atomic_t would otherwise be
                       * used). */
#endif
#include <stdarg.h>  /* va_list -- claude.md #159's festina_json_throwf */
#include <stddef.h>  /* offsetof -- claude.md #162's url accessors */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>       /* clock_gettime/nanosleep -- setTimeout/setInterval */
#include <dirent.h>     /* opendir/readdir/closedir -- claude.md #132's ls().
                          * POSIX; MinGW-w64 ships its own real <dirent.h>
                          * providing the identical opendir/readdir/closedir
                          * shape (a long-standing, widely-relied-on part of
                          * its POSIX compatibility layer), so this is used
                          * unconditionally rather than #ifdef'd like
                          * festina_mkdir below -- unconfirmed by real
                          * Windows CI yet, same open item every other
                          * blind-written win32 piece of this runtime has
                          * had since windows.md Phase 0. */
#include "festina_runtime.h"
#include "festina_runtime_internal.h"
#ifdef _WIN32
#include <direct.h> /* _mkdir -- claude.md #132's mkdir(), MSVCRT/UCRT's
                      * own single-argument spelling (no mode bits -- NTFS
                      * permissions aren't POSIX mode bits, so there is
                      * nothing a second argument would mean) */
#include <fcntl.h> /* _O_BINARY -- festina_runtime_init's stdout/stderr fix */
#include <io.h>    /* _setmode/_fileno -- MSVCRT/UCRT, not POSIX unistd.h */
#include <process.h> /* _spawnvp/_P_WAIT -- claude.md #150's exec() */
#else
#include <sys/stat.h> /* mkdir(path, mode) -- POSIX */
#if !defined(__wasi__)
#include <fcntl.h>    /* F_SETFD/FD_CLOEXEC -- the self-pipe in festina_process_exec */
#include <unistd.h>   /* fork/execvp/pipe/close/read/write */
#include <sys/wait.h> /* waitpid/WIFEXITED/WEXITSTATUS */
#endif
/* wasm32-wasi gets neither -- WASI has no process model at all
 * (confirmed directly: <sys/wait.h> doesn't even exist in wasi-libc's
 * own sysroot, not just "declares fork() but it always fails").
 * festina_process_exec's own body below is a stub for that target, never
 * actually reached by a real program -- a wasm compile that tries to
 * use exec() is rejected outright at compile time (see festina/cli.py's
 * _check_wasm_feature_supported) -- but this translation unit is still
 * compiled UNCONDITIONALLY for every wasm build (core, like regex --
 * see this file's own top comment), so it still needs to compile
 * cleanly regardless of whether the specific program being built ever
 * calls exec() at all. */
#endif

/* windows.md Phase 0 (claude.md #126): the MinGW/UCRT C runtime opens
 * stdout/stderr in TEXT mode by default, which silently rewrites every
 * '\n' a program prints to '\r\n' at the point of the write -- found
 * by real Windows CI comparing a compiled program's actual output
 * against a plain "\n"-terminated expectation. Every other platform's
 * libc has no such translation to begin with, so this is a no-op
 * everywhere but win32. Called once, unconditionally, as literally the
 * first thing every compiled program's main() does (see codegen.py). */
void festina_runtime_init(void) {
#ifdef _WIN32
    _setmode(_fileno(stdout), _O_BINARY);
    _setmode(_fileno(stderr), _O_BINARY);
#endif
}

/* ---- log() / fail() -- claude.md #41, #42 ---- */

/* claude.md #126 round nine: every log() call flushes explicitly
 * rather than trusting stdout's own default buffering mode. Once
 * stdout is redirected to a file or pipe (as any subprocess-captured
 * or piped program's is), the C runtime switches from line-buffered to
 * fully block-buffered by default -- a handful of short log() lines
 * can sit unflushed in that buffer for a long time, invisible to
 * anything reading the file/pipe concurrently rather than after the
 * process exits. `stdbuf -oL` (the usual fix -- force line buffering
 * from outside the process) works on Linux/macOS because it's an
 * LD_PRELOAD/DYLD_INSERT_LIBRARIES interposition trick against the
 * SAME libc the target binary links -- it can't do anything for a
 * compiled Festina program on Windows, which is a plain native UCRT64
 * PE binary, not something built against MSYS2's own runtime the way
 * MSYS2's own `stdbuf` is. Real Windows CI's timer test (an uncleared
 * setInterval, read from a still-running process's stdout after a
 * short wait) is a direct, real-world instance of exactly the
 * consequence any log()-heavy long-running program's redirected
 * output would have without this -- not just a test artifact. An
 * explicit fflush after each call is small, portable, and correct
 * everywhere, unlike relying on `setvbuf(..., _IOLBF, ...)`, which
 * Microsoft's own C runtime has long treated the same as full
 * buffering rather than true line buffering. */
void festina_log_int(int64_t v) { printf("%lld\n", (long long)v); fflush(stdout); }
void festina_log_float(double v) { printf("%g\n", v); fflush(stdout); }
/* claude.md #97: bool's null is the reserved third bit pattern 2 (see
 * codegen's BOOL_NULL_CONST), and printing it with a plain `v ? true :
 * false` rendered it as "true" -- indistinguishable from a genuine
 * true, which made claude.md #96's "popping an empty array gives you
 * null" impossible to actually observe for an arr[bool]. Only the
 * sentinel takes this branch, so no real boolean's output changes. */
void festina_log_bool(int8_t v) {
    printf("%s\n", v == 2 ? "null" : (v ? "true" : "false"));
    fflush(stdout);
}
void festina_log_text(const char *v) { printf("%s\n", v ? v : ""); fflush(stdout); }

void festina_fail(const char *msg) {
    fprintf(stderr, "fail: %s\n", msg ? msg : "");
    exit(1);
}

/* ---- troubleshoot() / structured fail() -- claude.md #158 ----
 *
 * A UTC RFC3339-ish timestamp ("...Z", second precision -- structured
 * log consumers expect UTC, unlike formatTime's own local-time
 * convention, claude.md #93), shared by both functions below. */
static void festina_write_log_timestamp(char *buf, size_t n) {
    time_t now = time(NULL);
    struct tm parts;
#ifdef _WIN32
    gmtime_s(&parts, &now);
#else
    gmtime_r(&now, &parts);
#endif
    strftime(buf, n, "%Y-%m-%dT%H:%M:%SZ", &parts);
}

/* claude.md #158: troubleshoot(event, fields) -- one JSON line to
 * stdout: {"timestamp":"...","level":"info","event":"...","fields":{...}}.
 * `fields_json` arrives ALREADY RENDERED as valid JSON text -- codegen
 * calls the exact same container-to-JSON path .toText() already uses
 * for any map[T]/arr[T]/struct (see codegen.py's own _json_fn_for via
 * _to_text), so this function never inspects a Festina value directly,
 * only assembles the surrounding envelope around two already-text
 * pieces. NULL-safe on both (an empty/missing fields map renders as
 * the JSON object "{}"). */
void festina_troubleshoot(const char *event, const char *fields_json) {
    char ts[32];
    festina_write_log_timestamp(ts, sizeof(ts));
    void *sb = festina_sb_new();
    festina_sb_append(sb, "{\"timestamp\":\"");
    festina_sb_append(sb, ts);
    festina_sb_append(sb, "\",\"level\":\"info\",\"event\":");
    festina_sb_append_json_text(sb, event);
    festina_sb_append(sb, ",\"fields\":");
    festina_sb_append(sb, fields_json ? fields_json : "{}");
    festina_sb_append(sb, "}");
    char *line = festina_sb_finish(sb);
    printf("%s\n", line);
    fflush(stdout);
    free(line);
}

/* claude.md #158: fail(message, fields) -- the 2-argument structured
 * form. Unlike plain fail(message)'s stable "fail: <message>\n" line
 * (unchanged, still what an uncaught throw also produces -- claude.md
 * #157's own "throw is never riskier than fail()" contract depends on
 * that staying exactly as it is), this prints a full JSON envelope to
 * stderr instead -- "level":"error", key "message" rather than
 * "event" (a structured error line reads as an event announcing
 * itself, not the failure it actually is) -- then exits(1), same as
 * every other fail() path. */
void festina_fail_structured(const char *msg, const char *fields_json) {
    char ts[32];
    festina_write_log_timestamp(ts, sizeof(ts));
    void *sb = festina_sb_new();
    festina_sb_append(sb, "{\"timestamp\":\"");
    festina_sb_append(sb, ts);
    festina_sb_append(sb, "\",\"level\":\"error\",\"message\":");
    festina_sb_append_json_text(sb, msg);
    festina_sb_append(sb, ",\"fields\":");
    festina_sb_append(sb, fields_json ? fields_json : "{}");
    festina_sb_append(sb, "}");
    char *line = festina_sb_finish(sb);
    fprintf(stderr, "%s\n", line);
    free(line); /* harmless either way -- exit() is next -- kept for hygiene */
    exit(1);
}

/* ---- try/catch/throw -- claude.md #157 ----
 *
 * setjmp/longjmp exception emulation -- but NOT the classic "wrap
 * setjmp in a small C helper function" shape a first attempt at this
 * reached for (and which a direct test caught as genuinely broken --
 * see claude.md #157's own account): setjmp() only captures a jump
 * target valid while ITS OWN calling function's stack frame is still
 * live. A helper that calls setjmp() and then returns 0 back to its
 * own caller has already made that frame invalid by the time some
 * LATER, unrelated throw tries to jump back into it -- undefined
 * behavior in the C standard's own terms, and empirically a silent
 * "just keeps running past the throw" or an outright crash depending
 * on what else has since reused that stack space.
 *
 * The fix: the actual setjmp call happens directly in the FESTINA
 * FUNCTION THAT CONTAINS THE try STATEMENT -- codegen's own _emit_try
 * emits it as raw LLVM IR (the llvm.eh.sjlj.setjmp/llvm.eh.sjlj.longjmp
 * intrinsics, the same portable, fixed-size-buffer mechanism clang
 * itself lowers __builtin_setjmp/__builtin_longjmp to -- chosen over
 * calling libc's own setjmp/longjmp symbols directly from hand-written
 * IR specifically because THEIR symbol names and jmp_buf layout are
 * platform/libc-specific in exactly the way an intrinsic isn't). That
 * frame is exactly as long-lived as the try statement needs it to be:
 * it can't return before hitting one of the exit paths codegen's own
 * _TryFrameMarker mechanism already instruments.
 *
 * longjmp itself has NO equivalent placement constraint -- only the
 * ORIGINATING setjmp call cares where it's made, so festina_throw
 * below is free to be an ordinary runtime C function using
 * __builtin_longjmp on whatever buffer festina_try_push registered.
 *
 * The catch-frame stack is `__thread`-local (claude.md #163), not a
 * plain global -- generated Festina code itself still only ever runs
 * on the single main thread (every OTHER piece of runtime state --
 * globals, refcounts -- is exactly as unsynchronized as it always
 * was, and stays that way), but claude.md #163's background http
 * worker pool calls this SAME festina_throw/try_push/try_error
 * machinery -- via a hand-written __builtin_setjmp catch frame, not
 * generated IR -- from its own worker threads, to convert a network
 * failure into a queued callback result rather than let it escape
 * across threads. A plain, shared g_festina_catch_top would let a
 * worker thread's own push/pop race the main thread's unrelated
 * try/catch activity, or -- worse -- longjmp into a stack frame that
 * isn't even on the current thread's stack. Thread-local storage
 * gives each thread (main or worker) its own completely independent
 * catch-frame stack and pending-error slot, with zero synchronization
 * needed, since nothing here is ever actually SHARED between threads
 * in the first place -- confirmed directly with a standalone harness
 * (two threads, one throwing, one not, both racing the main thread's
 * own independent try/catch) before this went anywhere near the real
 * runtime.
 *
 * THE ONE REAL, DOCUMENTED LIMITATION (see api.md and claude.md #157):
 * longjmp unwinds the C stack directly, bypassing every LLVM-generated
 * cleanup instruction in every frame between the throw and the
 * catching try -- EXCEPT the one frame that matters most, which
 * codegen's own ThrowStmt handling covers explicitly (a plain, direct
 * _emit_free_active_locals call, right before the festina_throw call
 * itself -- unlike every other frame's cleanup, this one is emitted
 * BEFORE, not after, so it isn't dead code the longjmp skips). So: a
 * throw is leak-free for every local active in the FUNCTION THAT
 * DIRECTLY CONTAINS the throw statement, whether that's the try's own
 * body or a function it calls (or a function THAT calls, arbitrarily
 * deep) -- exactly like Return already is for that same function. The
 * real gap is narrower than "any called function": any INTERMEDIATE
 * frame on the call chain between the try and the actual throw -- a
 * function that merely CALLS something which eventually throws,
 * without itself containing a throw or try -- never runs any of its
 * own cleanup at all, because longjmp skips past its remaining code
 * entirely, the same way it skips a frame with no cleanup story of its
 * own. Confirmed empirically, not just reasoned about: a direct
 * Valgrind run showed 0 leaked bytes throwing from the SAME function a
 * try calls, and from a function THAT function calls in turn -- and a
 * real, reproducible "N bytes in N blocks definitely lost" (N = call
 * count) the moment a genuine intermediate frame sat between them.
 * This is a leak, never a use-after-free or corruption (nothing is
 * freed that shouldn't be, only some things that should be freed
 * eventually aren't) -- the same correctness class this runtime already accepts
 * for the one documented row-array chain shape in security.md. */

typedef struct FestinaCatchFrame {
    void *buf;  /* codegen's own [5 x ptr] alloca -- see _emit_try */
    struct FestinaCatchFrame *prev;
} FestinaCatchFrame;

static __thread FestinaCatchFrame *g_festina_catch_top = NULL;
/* Owned by the runtime between a throw and the moment festina_try_error()
 * hands it over; NULL the rest of the time. Only ever holds ONE message
 * at a time (per thread) -- a throw can only reach here once nothing
 * between it and the catch has already unwound, so there's never a
 * second pending throw to overwrite this before the first is
 * collected. */
static __thread char *g_festina_error_message = NULL;

/* Registers buf (codegen's own alloca'd sjlj buffer, already populated
 * by ITS direct llvm.eh.sjlj.setjmp call, which returned 0 -- the
 * normal, first-arrival path) as the new top catch frame. Called by
 * generated code once, right after that setjmp -- never on the
 * "returned via a longjmp" (nonzero) path. */
void festina_try_push(void *buf) {
    FestinaCatchFrame *frame = malloc(sizeof(FestinaCatchFrame));
    if (!frame) { fprintf(stderr, "festina: out of memory (try)\n"); exit(1); }
    frame->buf = buf;
    frame->prev = g_festina_catch_top;
    g_festina_catch_top = frame;
}

/* Only ever called right after festina_try_push() pushed the SAME
 * frame this pops (codegen's own _TryFrameMarker handling in
 * _emit_free_active_locals is the only caller, on every NORMAL exit
 * from a try body); a THROWING exit pops its own frame itself, from
 * inside festina_throw, before the jump. */
void festina_try_pop(void) {
    FestinaCatchFrame *frame = g_festina_catch_top;
    if (!frame) return; /* defensive; should never happen from generated code */
    g_festina_catch_top = frame->prev;
    free(frame);
}

/* Hands ownership of the thrown message over to generated code as an
 * ordinary, exclusively-owned text value -- the runtime's own copy
 * (see festina_throw) becomes the caller's from this point on, so this
 * never needs calling twice for the same throw. */
char *festina_try_error(void) {
    char *msg = g_festina_error_message;
    g_festina_error_message = NULL;
    return msg ? msg : strdup("");
}

/* Never returns. With no enclosing try reachable, behaves exactly like
 * fail(msg) -- throw is always at least as safe as fail(), never a
 * riskier way to end the program.
 *
 * TAKES OWNERSHIP of msg -- unlike every other runtime call taking a
 * `ptr` text argument, this does NOT make its own copy (codegen's own
 * ThrowStmt handling already made an exclusively-owned one, via a
 * plain festina_text_own, specifically so it stays valid regardless of
 * what _emit_free_active_locals frees right afterward -- see that
 * comment for why a second copy here would be redundant AND would
 * itself leak, since nothing downstream would ever free it once this
 * call diverts control away for good). Otherwise: pops the frame it's
 * unwinding TO (not any frame still open between here and there --
 * those are simply never visited, which is this mechanism's one
 * documented leak -- see this file's own top comment), and jumps via
 * __builtin_longjmp -- safe to call from here (an ordinary, nested
 * runtime function) even though the matching setjmp is not, since only
 * setjmp cares about its own call site's frame lifetime; longjmp has
 * no equivalent restriction.
 *
 * wasm32-wasi AND macOS both get a stub, the identical shape
 * festina_process_exec's own wasm32-wasi branch already uses just
 * below (see this file's own top-of-file comment on why):
 * __builtin_longjmp is flatly rejected by clang for both targets
 * ("not supported for the current target", confirmed directly for
 * each -- LLVM's wasm32 backend has no SjLj lowering at all outside
 * emscripten's own EH pass, which this project doesn't use; LLVM's
 * AArch64 backend (claude.md #170, found via a real macos-14 CI run --
 * Apple Silicon, what every current Mac and every GitHub macOS runner
 * actually is -- compiling this file unconditionally, try/throw or
 * not) has no SjLj lowering either, even though the identical builtin
 * compiles fine for x86_64-apple-macos, an architecture this project
 * doesn't target), so this whole file would fail to compile for EVERY
 * program on either platform, try/throw or not, without this split --
 * this translation unit is still compiled UNCONDITIONALLY on both.
 * try/throw is rejected outright at compile time instead (festina/
 * cli.py's _check_wasm_feature_supported for wasm32-wasi, gated on
 * codegen's own uses_try; _check_darwin_try_supported for macOS, same
 * gate, same reasoning) -- this stub degrading every throw to fail()'s
 * own behavior instead of a hard compile error would be surprising,
 * silently platform-dependent semantics rather than a clear, honest
 * "not supported here"; it exists purely so this file compiles, never
 * to be reached by a real program on either platform. */
#if !defined(__wasi__) && !defined(__APPLE__)
void festina_throw(const char *msg) {
    if (g_festina_catch_top == NULL) {
        festina_fail(msg);
        return; /* unreachable -- festina_fail() always exits */
    }
    free(g_festina_error_message);
    g_festina_error_message = (char *)msg;
    FestinaCatchFrame *frame = g_festina_catch_top;
    g_festina_catch_top = frame->prev;
    void *buf = frame->buf;
    free(frame);
    __builtin_longjmp(buf, 1);
}
#else
void festina_throw(const char *msg) {
    festina_fail(msg); /* never actually reached -- see this function's own comment above */
}
#endif

static int64_t festina_null_int(void);      /* defined with the sqlite helpers below */
static double festina_null_float(void);     /* defined with the sqlite helpers below */

/* ---- JSON parsing: .toStruct()/.toArr() -- claude.md #159 ----
 *
 * A hand-written recursive-descent parser, but structured so every
 * low-level primitive below either succeeds and returns a valid
 * result, or calls festina_throw() directly and never returns --
 * reusing claude.md #157's own throw/catch machinery as this whole
 * feature's ENTIRE error-handling story, rather than threading error
 * values through hand-generated LLVM IR. This means codegen's own
 * generated per-struct/per-array parsing functions (see
 * codegen.py's _from_json_fn_for) are plain, straight-line/looping
 * code with no separate failure path to branch on at all -- a parse
 * failure anywhere unwinds exactly the way any other throw does, all
 * the way to the nearest enclosing try/catch (or behaves like fail()
 * if there is none).
 *
 * v1 SCOPE CUT (documented in api.md/todo.md, not silent): a target
 * struct's fields and a target array's element type must be
 * int/float/bool/text -- nested struct/arr[T]/map[T] aren't supported
 * yet, rejected at COMPILE TIME with a clear error naming the
 * unsupported field/element and its type. festina_json_skip_value
 * below is still fully general regardless (an unrecognized struct key
 * can still legally hold arbitrarily nested JSON, and needs to be
 * correctly skipped past either way -- see its own comment).
 *
 * ONE REAL, DOCUMENTED LIMITATION, the SAME structural class claude.md
 * #157 already accepted (see festina_throw's own comment above): a
 * throw from anywhere inside codegen's own generated
 * __festina_from_json_struct_N/__festina_from_json_arr_N leaves
 * whatever that ONE call had already built (the struct's own header,
 * any field text already read, any array elements already pushed)
 * permanently unreclaimed -- that function is HAND-WRITTEN LLVM IR,
 * not a real Festina function body going through _emit_block's own
 * _active_free_locals tracking, so nothing in generated code ever gets
 * the chance to free it on the way out. A SUCCESSFUL parse leaks
 * NOTHING (confirmed directly under Valgrind, including 30 repeated
 * calls in a loop) -- this is strictly an error-path leak, bounded to
 * at most one partially-built struct/array per FAILED call, never
 * unbounded or accumulating across successful ones. Fixing this
 * properly would mean real exception-safe cleanup for values built
 * mid-expression-evaluation generally (this language has no RAII/
 * unwind-table story at all today) -- a substantially larger
 * undertaking than this feature's own reasonable scope, tracked in
 * todo.md rather than attempted here. */

typedef struct FestinaJsonCursor {
    const char *s;
    int64_t len;
    int64_t pos;
} FestinaJsonCursor;

static void festina_json_throwf(const char *fmt, ...) {
    char buf[256];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(buf, sizeof(buf), fmt, ap);
    va_end(ap);
    /* strdup'd, not the stack buffer itself -- festina_throw() TAKES
     * OWNERSHIP of what it's given (claude.md #157/#158's own
     * convention), and buf's own storage is gone the instant this
     * function returns (which festina_throw never actually lets
     * happen here, but the call itself still needs a heap pointer). */
    festina_throw(strdup(buf));
}

static void festina_json_skip_ws(FestinaJsonCursor *c) {
    while (c->pos < c->len) {
        char ch = c->s[c->pos];
        if (ch == ' ' || ch == '\t' || ch == '\n' || ch == '\r') c->pos++;
        else break;
    }
}

static int festina_json_peek(FestinaJsonCursor *c) {
    festina_json_skip_ws(c);
    if (c->pos >= c->len) return -1;
    return (unsigned char)c->s[c->pos];
}

/* Consumes `ch` if it's next (after skipping ws); throws otherwise. */
static void festina_json_expect(FestinaJsonCursor *c, char ch) {
    int p = festina_json_peek(c);
    if (p != (unsigned char)ch) {
        if (p < 0) festina_json_throwf("expected '%c' but reached the end of input", ch);
        else festina_json_throwf("expected '%c' at position %lld, found '%c'",
                                  ch, (long long)c->pos, (char)p);
    }
    c->pos++;
}

/* Consumes `ch` if it's next; returns 1/0, never throws -- used for
 * "is there another element/key, or is this the end" checks, where
 * "no" is a normal, expected outcome rather than a parse error. */
static int festina_json_try_eat(FestinaJsonCursor *c, char ch) {
    if (festina_json_peek(c) != (unsigned char)ch) return 0;
    c->pos++;
    return 1;
}

/* True (and consumes "null") if the next token is a JSON null literal;
 * false (cursor untouched) otherwise. Every scalar reader below checks
 * this first -- a JSON null is always legal for any supported target
 * type here, becoming that type's own zero/null value (the same
 * "database NULL renders as null text/element renders as null" -- but
 * mirrored, reading rather than writing -- api.md's own existing JSON
 * rendering rule already documents). */
static int festina_json_try_null(FestinaJsonCursor *c) {
    festina_json_skip_ws(c);
    if (c->pos + 4 <= c->len && memcmp(c->s + c->pos, "null", 4) == 0) { c->pos += 4; return 1; }
    return 0;
}

static char *festina_json_parse_string(FestinaJsonCursor *c) {
    festina_json_skip_ws(c);
    if (c->pos >= c->len || c->s[c->pos] != '"') {
        festina_json_throwf("expected a string at position %lld", (long long)c->pos);
    }
    c->pos++;
    size_t cap = 32, len = 0;
    char *out = malloc(cap);
    if (!out) festina_json_throwf("out of memory parsing a JSON string");
    for (;;) {
        if (c->pos >= c->len) { free(out); festina_json_throwf("unterminated string"); }
        unsigned char ch = (unsigned char)c->s[c->pos++];
        char decoded;
        if (ch == '"') { out[len] = 0; return out; }
        if (ch == '\\') {
            if (c->pos >= c->len) { free(out); festina_json_throwf("unterminated escape sequence"); }
            char esc = c->s[c->pos++];
            switch (esc) {
                case '"': decoded = '"'; break;
                case '\\': decoded = '\\'; break;
                case '/': decoded = '/'; break;
                case 'b': decoded = '\b'; break;
                case 'f': decoded = '\f'; break;
                case 'n': decoded = '\n'; break;
                case 'r': decoded = '\r'; break;
                case 't': decoded = '\t'; break;
                default:
                    free(out);
                    /* claude.md #159: \u unicode escapes are a
                     * documented v1 scope cut, not silently mishandled
                     * -- raw (unescaped) non-ASCII UTF-8 bytes in a
                     * string are unaffected and parse completely
                     * normally; this only affects a producer that
                     * specifically chooses to \u-escape. */
                    if (esc == 'u') festina_json_throwf("\\u unicode escapes are not yet supported");
                    else festina_json_throwf("invalid escape sequence '\\%c'", esc);
                    return NULL; /* unreachable */
            }
        } else if (ch < 0x20) {
            free(out);
            festina_json_throwf("unescaped control character in a JSON string");
            return NULL; /* unreachable */
        } else {
            decoded = (char)ch;
        }
        if (len + 2 > cap) {
            cap *= 2;
            char *grown = realloc(out, cap);
            if (!grown) { free(out); festina_json_throwf("out of memory parsing a JSON string"); }
            out = grown;
        }
        out[len++] = decoded;
    }
}

/* Parses a JSON number TOKEN and returns it as a double -- the caller
 * decides int vs float interpretation (festina_json_read_int below
 * truncates). JSON itself doesn't syntactically distinguish "5" from
 * "5.0"; neither does this language's own numeric coercion (assigning
 * a whole-number float where an int is expected, or vice versa, both
 * already just work), so this deliberately doesn't reject "5.0" for an
 * int field/element -- consistent with, not stricter than, Festina's
 * own existing int/float rules. */
static double festina_json_parse_number(FestinaJsonCursor *c) {
    festina_json_skip_ws(c);
    int64_t start = c->pos;
    if (c->pos < c->len && c->s[c->pos] == '-') c->pos++;
    if (c->pos >= c->len || !isdigit((unsigned char)c->s[c->pos])) {
        festina_json_throwf("expected a number at position %lld", (long long)start);
    }
    if (c->s[c->pos] == '0') c->pos++;
    else { while (c->pos < c->len && isdigit((unsigned char)c->s[c->pos])) c->pos++; }
    if (c->pos < c->len && c->s[c->pos] == '.') {
        c->pos++;
        if (c->pos >= c->len || !isdigit((unsigned char)c->s[c->pos]))
            festina_json_throwf("malformed number at position %lld", (long long)start);
        while (c->pos < c->len && isdigit((unsigned char)c->s[c->pos])) c->pos++;
    }
    if (c->pos < c->len && (c->s[c->pos] == 'e' || c->s[c->pos] == 'E')) {
        c->pos++;
        if (c->pos < c->len && (c->s[c->pos] == '+' || c->s[c->pos] == '-')) c->pos++;
        if (c->pos >= c->len || !isdigit((unsigned char)c->s[c->pos]))
            festina_json_throwf("malformed number at position %lld", (long long)start);
        while (c->pos < c->len && isdigit((unsigned char)c->s[c->pos])) c->pos++;
    }
    int64_t n = c->pos - start;
    char buf[64];
    if (n >= (int64_t)sizeof(buf)) festina_json_throwf("number literal too long");
    memcpy(buf, c->s + start, (size_t)n);
    buf[n] = 0;
    return strtod(buf, NULL);
}

static int8_t festina_json_parse_bool(FestinaJsonCursor *c) {
    festina_json_skip_ws(c);
    if (c->pos + 4 <= c->len && memcmp(c->s + c->pos, "true", 4) == 0) { c->pos += 4; return 1; }
    if (c->pos + 5 <= c->len && memcmp(c->s + c->pos, "false", 5) == 0) { c->pos += 5; return 0; }
    festina_json_throwf("expected true or false at position %lld", (long long)c->pos);
    return 0; /* unreachable */
}

/* Recursively skips over ANY well-formed JSON value (string, number,
 * bool, null, object, array) without building anything -- used for a
 * struct field the target Festina struct doesn't declare (an unknown
 * JSON key is a normal, forward-compatible thing to see, not an error
 * -- api.md's own documented lenient-parsing contract). Fully general
 * regardless of this v1's own scalars-only SUPPORTED-field scope (see
 * this section's own top comment) -- an unknown key's own value can
 * still be arbitrarily nested either way, and needs to be correctly
 * skipped past regardless of whether this version could ever build a
 * Festina value from it. */
static void festina_json_skip_value(FestinaJsonCursor *c) {
    if (festina_json_try_null(c)) return;
    int p = festina_json_peek(c);
    if (p == '"') { char *s = festina_json_parse_string(c); free(s); return; }
    if (p == 't' || p == 'f') { festina_json_parse_bool(c); return; }
    if (p == '{') {
        c->pos++;
        if (festina_json_try_eat(c, '}')) return;
        for (;;) {
            char *k = festina_json_parse_string(c);
            free(k);
            festina_json_expect(c, ':');
            festina_json_skip_value(c);
            if (festina_json_try_eat(c, ',')) continue;
            festina_json_expect(c, '}');
            return;
        }
    }
    if (p == '[') {
        c->pos++;
        if (festina_json_try_eat(c, ']')) return;
        for (;;) {
            festina_json_skip_value(c);
            if (festina_json_try_eat(c, ',')) continue;
            festina_json_expect(c, ']');
            return;
        }
    }
    if (p == '-' || (p >= '0' && p <= '9')) { festina_json_parse_number(c); return; }
    if (p < 0) festina_json_throwf("unexpected end of input");
    else festina_json_throwf("unexpected character '%c' at position %lld", (char)p, (long long)c->pos);
}

/* Case-insensitive key match -- struct fields match a JSON key the
 * same way a query column already does (claude.md #111's own
 * case-insensitive convention, mirrored here for consistency, not
 * re-derived). */
static int festina_json_key_eq(const char *a, const char *b) {
    while (*a && *b) {
        if (tolower((unsigned char)*a) != tolower((unsigned char)*b)) return 0;
        a++; b++;
    }
    return *a == *b;
}

/* ---- Public entry points -- codegen calls these directly. ---- */

void *festina_json_cursor_new(const char *text) {
    FestinaJsonCursor *c = malloc(sizeof(FestinaJsonCursor));
    if (!c) festina_json_throwf("out of memory starting a JSON parse");
    c->s = text ? text : "";
    c->len = (int64_t)strlen(c->s);
    c->pos = 0;
    return c;
}

void festina_json_cursor_free(void *cursor) { free(cursor); }

/* Rejects trailing garbage after the top-level value -- called once,
 * by the OUTERMOST generated function, after the whole struct/array
 * has been consumed. `'{}extra'.toStruct(T)` is a parse error, not a
 * silently-ignored suffix. */
void festina_json_expect_end(void *cursor) {
    FestinaJsonCursor *c = cursor;
    if (festina_json_peek(c) >= 0) {
        festina_json_throwf("unexpected trailing data at position %lld", (long long)c->pos);
    }
}

void festina_json_object_start(void *cursor) { festina_json_expect((FestinaJsonCursor *)cursor, '{'); }
void festina_json_array_start(void *cursor) { festina_json_expect((FestinaJsonCursor *)cursor, '['); }

/* Called at the START of each object-field loop iteration -- returns 1
 * (and consumes the closing '}') once the object has ended, 0
 * otherwise (leaving the cursor positioned at the next key). `*first`
 * is an in/out flag the generated loop owns as its own local, tracking
 * whether a leading ',' needs consuming first. */
int8_t festina_json_object_next(void *cursor, int8_t *first) {
    FestinaJsonCursor *c = cursor;
    if (festina_json_try_eat(c, '}')) return 1;
    if (!*first) festina_json_expect(c, ',');
    *first = 0;
    return 0;
}

/* The array counterpart -- identical shape, closing ']'. */
int8_t festina_json_array_next(void *cursor, int8_t *first) {
    FestinaJsonCursor *c = cursor;
    if (festina_json_try_eat(c, ']')) return 1;
    if (!*first) festina_json_expect(c, ',');
    *first = 0;
    return 0;
}

char *festina_json_read_key(void *cursor) {
    FestinaJsonCursor *c = cursor;
    char *key = festina_json_parse_string(c);
    festina_json_expect(c, ':');
    return key;
}

int8_t festina_json_key_matches(const char *key, const char *field_name) {
    return (int8_t)festina_json_key_eq(key, field_name);
}

void festina_json_skip_field_value(void *cursor) { festina_json_skip_value((FestinaJsonCursor *)cursor); }

int64_t festina_json_read_int(void *cursor) {
    FestinaJsonCursor *c = cursor;
    if (festina_json_try_null(c)) return festina_null_int();
    return (int64_t)festina_json_parse_number(c);
}

double festina_json_read_float(void *cursor) {
    FestinaJsonCursor *c = cursor;
    if (festina_json_try_null(c)) return festina_null_float();
    return festina_json_parse_number(c);
}

int8_t festina_json_read_bool(void *cursor) {
    FestinaJsonCursor *c = cursor;
    if (festina_json_try_null(c)) return 2; /* claude.md #97's bool-null sentinel */
    return festina_json_parse_bool(c);
}

char *festina_json_read_text(void *cursor) {
    FestinaJsonCursor *c = cursor;
    if (festina_json_try_null(c)) return NULL;
    return festina_json_parse_string(c);
}

/* ---- url / parseURL() -- claude.md #162 ----
 *
 * Deliberately modeled on the WHATWG URL object's own field names
 * (hash/hostname/password/pathname/port/protocol/searchParams/
 * username) rather than inventing new ones, since that's the shape
 * asked for directly. Absolute URLs only (`scheme://...`) -- no
 * relative-URL resolution against a base, no IDNA/punycode host
 * normalization, no exhaustive RFC 3986 validation; a pragmatic
 * subset that parses the URLs a real program actually constructs
 * (an API endpoint, a webhook target), not a general-purpose URL
 * library. parseURL() THROWS (claude.md #157's own catchable
 * exception mechanism, reused here exactly the way claude.md #159's
 * JSON parser already reuses it) on a genuinely malformed URL -- no
 * `://`, or an unparseable port -- rather than returning some
 * default/empty value a caller could easily miss.
 *
 * Refcounted like blob/img/aud/http (the same `{refcount, ...}`
 * header every one of those shares -- see _is_refcounted's own
 * comment in codegen.py), constructed once by festina_parse_url and
 * read through afterward by seven small accessors, one per field --
 * `port` is the one exception (kept as a public field access via
 * festina_url_port returning i64 directly, no separate accessor
 * split needed since it was never text to begin with). */
typedef struct {
    int64_t refcount;
    char *protocol;    /* includes the trailing ':' -- e.g. "https:" --
                        * matching the WHATWG URL object's own convention */
    char *username;
    char *password;
    char *hostname;
    int64_t port;       /* festina_null_int() when the URL named no
                         * explicit port (the scheme's own default
                         * applies implicitly -- this never guesses
                         * what that default is) */
    char *pathname;     /* always starts with '/' */
    char *hash;         /* includes the leading '#' if present, else "" */
    void *search_params; /* map[text] payload (see festina_runtime_http.c's
                          * own festina_new_empty_text_map for the identical
                          * {refcount, count, entries} shape) -- percent-
                          * decoded keys/values, '+' decoded to a space in
                          * the query string specifically (classic
                          * application/x-www-form-urlencoded convention),
                          * NOT in the path/hash. */
} FestinaUrlValue;

static char *festina_url_slice(const char *start, const char *end) {
    size_t len = (size_t)(end - start);
    char *out = malloc(len + 1);
    if (!out) festina_fail("out of memory parsing a URL");
    memcpy(out, start, len);
    out[len] = '\0';
    return out;
}

/* One hex digit -> its value, or -1 if not a hex digit. */
static int festina_hex_digit(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

/* Percent-decodes [start, end) into a fresh, owned, NUL-terminated
 * string -- '+' becomes a space (query-string convention; callers
 * that don't want that, e.g. a plain path/hash slice, use
 * festina_url_slice above instead, never this). A malformed escape
 * (a trailing '%' or non-hex digits) is passed through literally
 * rather than rejected -- the same "lenient, never fails the
 * program" spirit claude.md #159's own JSON parser applies to
 * unknown-but-harmless input shapes, not to genuinely malformed ones
 * (which still throw, just not here -- this only ever decodes
 * already-delimited query text, never a place a throw would help). */
static char *festina_url_decode(const char *start, const char *end) {
    size_t len = (size_t)(end - start);
    char *out = malloc(len + 1);
    if (!out) festina_fail("out of memory parsing a URL");
    size_t oi = 0;
    for (size_t i = 0; i < len; i++) {
        char c = start[i];
        if (c == '+') {
            out[oi++] = ' ';
        } else if (c == '%' && i + 2 < len) {
            int hi = festina_hex_digit(start[i + 1]);
            int lo = festina_hex_digit(start[i + 2]);
            if (hi >= 0 && lo >= 0) {
                out[oi++] = (char)((hi << 4) | lo);
                i += 2;
            } else {
                out[oi++] = c;
            }
        } else {
            out[oi++] = c;
        }
    }
    out[oi] = '\0';
    return out;
}

/* festina_runtime_http.c has its own helper of this exact name/shape
 * (a fresh {refcount, count, entries, capacity, tombstones} map[text]
 * block) -- not reachable from here (a different translation unit), so
 * this is CORE's own private copy, used by both url search params and
 * by anything else in this file that ever needs a fresh empty
 * map[text]. claude.md #175: count/entries/capacity/tombstones mirror
 * the map[T] header shape codegen.py now emits (FESTINA_MAP_LLVM_TYPE)
 * exactly, field for field, since every festina_map_* call below reads
 * and writes them the same way generated code does. */
typedef struct {
    int64_t refcount;
    int64_t count;
    void *entries;
    int64_t capacity;
    int64_t tombstones;
} FestinaMapBlockCore;

static void *festina_new_empty_text_map(void) {
    FestinaMapBlockCore *block = calloc(1, sizeof(FestinaMapBlockCore));
    if (!block) festina_fail("out of memory allocating a map");
    block->refcount = 1;
    return &block->count;
}

/* Parses `a=1&b=2` (already-decoded of its own leading '?') into a
 * fresh map[text] -- last-key-wins for a repeated parameter, the
 * identical "last one wins" convention claude.md #159's own JSON
 * object parsing already uses for a duplicate key. */
static void *festina_parse_search_params(const char *query, size_t len) {
    void *map = festina_new_empty_text_map();
    FestinaMapBlockCore *block = (FestinaMapBlockCore *)((char *)map - sizeof(int64_t));
    const char *p = query;
    const char *end = query + len;
    while (p < end) {
        const char *pair_end = memchr(p, '&', (size_t)(end - p));
        if (!pair_end) pair_end = end;
        const char *eq = memchr(p, '=', (size_t)(pair_end - p));
        char *key, *value;
        if (eq) {
            key = festina_url_decode(p, eq);
            value = festina_url_decode(eq + 1, pair_end);
        } else {
            key = festina_url_decode(p, pair_end);
            value = festina_text_own("");
        }
        if (key[0] != '\0') {
            festina_map_set(&block->count, &block->entries, &block->capacity,
                            &block->tombstones, key, (int64_t)(intptr_t)value);
        } else {
            free(value);
        }
        free(key);
        p = (pair_end < end) ? pair_end + 1 : end;
    }
    return map;
}

void *festina_parse_url(const char *text) {
    if (!text) text = "";
    const char *scheme_end = strstr(text, "://");
    if (!scheme_end) {
        char msg[256];
        snprintf(msg, sizeof(msg), "parseURL: '%s' has no scheme (expected "
                 "something like 'https://host/path')", text);
        festina_throw(festina_text_own(msg));
        return NULL; /* unreachable -- festina_throw never returns */
    }
    const char *p = scheme_end + 3;

    FestinaUrlValue *u = calloc(1, sizeof(FestinaUrlValue));
    if (!u) festina_fail("out of memory parsing a URL");
    u->refcount = 1;
    u->port = festina_null_int();
    {
        char *scheme = festina_url_slice(text, scheme_end);
        size_t slen = strlen(scheme);
        char *with_colon = malloc(slen + 2);
        if (!with_colon) festina_fail("out of memory parsing a URL");
        memcpy(with_colon, scheme, slen);
        with_colon[slen] = ':';
        with_colon[slen + 1] = '\0';
        free(scheme);
        u->protocol = with_colon;
    }

    /* authority: [user[:password]@]host[:port], up to the first of
     * '/', '?', '#', or the end of the string. */
    const char *authority_end = p;
    while (*authority_end && *authority_end != '/' && *authority_end != '?'
           && *authority_end != '#') authority_end++;
    const char *host_start = p;
    const char *at = memchr(p, '@', (size_t)(authority_end - p));
    if (at) {
        const char *colon = memchr(p, ':', (size_t)(at - p));
        if (colon) {
            u->username = festina_url_decode(p, colon);
            u->password = festina_url_decode(colon + 1, at);
        } else {
            u->username = festina_url_decode(p, at);
            u->password = festina_text_own("");
        }
        host_start = at + 1;
    } else {
        u->username = festina_text_own("");
        u->password = festina_text_own("");
    }
    const char *port_colon = memchr(host_start, ':', (size_t)(authority_end - host_start));
    if (port_colon) {
        u->hostname = festina_url_slice(host_start, port_colon);
        char *port_text = festina_url_slice(port_colon + 1, authority_end);
        if (port_text[0] != '\0') {
            char *end_ptr = NULL;
            long port_val = strtol(port_text, &end_ptr, 10);
            if (end_ptr == port_text || *end_ptr != '\0' || port_val < 0 || port_val > 65535) {
                char msg[256];
                snprintf(msg, sizeof(msg), "parseURL: '%s' has an invalid port", text);
                free(port_text);
                festina_throw(festina_text_own(msg));
                return NULL; /* unreachable */
            }
            u->port = port_val;
        }
        free(port_text);
    } else {
        u->hostname = festina_url_slice(host_start, authority_end);
    }

    p = authority_end;
    const char *path_start = p;
    while (*p && *p != '?' && *p != '#') p++;
    u->pathname = (p > path_start) ? festina_url_slice(path_start, p) : festina_text_own("/");

    if (*p == '?') {
        p++;
        const char *query_start = p;
        while (*p && *p != '#') p++;
        u->search_params = festina_parse_search_params(query_start, (size_t)(p - query_start));
    } else {
        u->search_params = festina_new_empty_text_map();
    }

    if (*p == '#') {
        u->hash = festina_url_slice(p, p + strlen(p));
    } else {
        u->hash = festina_text_own("");
    }

    return &u->protocol;
}

#define FESTINA_URL_FROM_PAYLOAD(payload) \
    ((FestinaUrlValue *)((char *)(payload) - offsetof(FestinaUrlValue, protocol)))

char *festina_url_protocol(void *payload) { return festina_text_own(FESTINA_URL_FROM_PAYLOAD(payload)->protocol); }
char *festina_url_username(void *payload) { return festina_text_own(FESTINA_URL_FROM_PAYLOAD(payload)->username); }
char *festina_url_password(void *payload) { return festina_text_own(FESTINA_URL_FROM_PAYLOAD(payload)->password); }
char *festina_url_hostname(void *payload) { return festina_text_own(FESTINA_URL_FROM_PAYLOAD(payload)->hostname); }
int64_t festina_url_port(void *payload) { return FESTINA_URL_FROM_PAYLOAD(payload)->port; }
char *festina_url_pathname(void *payload) { return festina_text_own(FESTINA_URL_FROM_PAYLOAD(payload)->pathname); }
char *festina_url_hash(void *payload) { return festina_text_own(FESTINA_URL_FROM_PAYLOAD(payload)->hash); }
void *festina_url_search_params(void *payload) {
    void *sp = FESTINA_URL_FROM_PAYLOAD(payload)->search_params;
    festina_retain(sp);
    return sp;
}

void festina_release_url(void *payload) {
    if (!festina_release_check(payload)) return;
    FestinaUrlValue *u = FESTINA_URL_FROM_PAYLOAD(payload);
    free(u->protocol);
    free(u->username);
    free(u->password);
    free(u->hostname);
    free(u->pathname);
    free(u->hash);
    /* claude.md #167: festina_release_text_map, not the generic
     * festina_release_map -- search_params' own values are owned text
     * (festina_parse_search_params builds each one via
     * festina_url_decode, the same shape festina_text_own produces),
     * see that function's own doc comment for the leak this fixes. */
    festina_release_text_map(u->search_params);
    free(u);
}

/* claude.md #131: close(code)/`on exit(code:int)`. Lives in the CORE
 * runtime (not festina_runtime_graphics.c, where g_close_handler and
 * the window-close event live) precisely because close() has to work
 * in every program, windowed or not -- unlike g_close_handler, which
 * only ever fires from an X11/Cocoa/Win32 window-close event and so
 * can only exist in a program that already links a graphics backend.
 * festina_register_exit_handler is called at most once, unconditionally,
 * near the very top of main() (see codegen.py's _emit_main_and_entry)
 * whenever the program declares one; festina_program_exit runs it (if
 * registered) and then exits -- the two are kept as separate functions,
 * matching every other register_*_handler/fire-the-handler pair in this
 * runtime, rather than folding registration into the exit call itself. */
static void (*g_exit_handler)(int64_t) = NULL;

void festina_register_exit_handler(void (*handler)(int64_t)) {
    g_exit_handler = handler;
}

void festina_program_exit(int64_t code) {
    if (g_exit_handler) g_exit_handler(code);
    exit((int)code);
}

/* claude.md #161: graceful shutdown -- SIGINT/SIGTERM stop a program
 * the same clean way close(code) already does (`on exit(code:int)`
 * runs, then the process exits) instead of the OS's own default,
 * abrupt, no-cleanup-at-all termination -- and, for a program that
 * uses openPort()/openSecurePort(), give already-accepted connections
 * a real chance to finish instead of being severed mid-response (see
 * festina_runtime_http.c's own festina_run_http_loop, which is what
 * actually does the draining -- this is only the signal-to-flag
 * plumbing every blocking loop polls).
 *
 * DESIGN: the signal handler itself does the absolute minimum
 * async-signal-safety allows -- setting two `sig_atomic_t` flags and
 * returning, nothing else (no malloc, no I/O, no calling back into
 * arbitrary Festina/C code directly from signal context, which could
 * land mid-way through an in-progress non-reentrant call like
 * malloc() itself and deadlock or corrupt heap state). Every blocking
 * loop (festina_run_http_loop, festina_run_timer_loop,
 * festina_run_event_loop) instead POLLS festina_shutdown_requested()
 * once per ordinary iteration -- the same "check a flag on your own
 * schedule, from safe context" pattern already used for
 * festina_next_timer_deadline()/_fire_expired_timers() -- and does
 * its real cleanup (draining connections, closing a window, ...) from
 * there, in normal (non-signal-handler) execution.
 *
 * DESIGN: festina_install_shutdown_handler() is called from generated
 * code's own main() -- and ONLY there, ONLY when the program actually
 * has one of those three pollable loops (see codegen.py's
 * _emit_main_and_entry) -- deliberately NOT unconditionally
 * from festina_runtime_init() the way that function's own stdout/stderr
 * fix is. A plain script with a hand-written loop and no event loop of
 * any kind has no point in its own execution that could ever check
 * festina_shutdown_requested() at all; installing a handler there would
 * silently swallow Ctrl+C (the flag gets set, but nothing ever polls it,
 * so the program would run to completion instead of stopping) --
 * genuinely worse than doing nothing, since the OS's own default
 * SIGINT/SIGTERM disposition already stops such a program today. Only
 * install where "graceful" has an observable, correct meaning.
 *
 * DESIGN: SIGTERM is POSIX-only (`#ifndef _WIN32`) -- Windows has no
 * real SIGTERM delivery at all (nothing sends it under normal process
 * termination; taskkill/TerminateProcess don't raise it), the same
 * "Windows has no SIGPIPE either" situation festina_runtime_http.c's
 * own festina_open_port already documents for a different signal.
 * SIGINT is registered on every platform -- Windows' CRT does raise it
 * for a real Ctrl+C on a console process. */
#if !defined(__wasi__)
static volatile sig_atomic_t g_shutdown_requested = 0;
static volatile sig_atomic_t g_shutdown_exit_code = 0;

static void festina_shutdown_signal_handler(int sig) {
    g_shutdown_requested = 1;
    /* 128+signal is the conventional POSIX/shell exit-code encoding for
     * "terminated by signal N" (130 for SIGINT, 143 for SIGTERM) --
     * matches what a shell itself would report for the same signal
     * killing an ordinary (non-Festina) process, so a caller scripting
     * around a compiled Festina program sees the familiar convention. */
    g_shutdown_exit_code = 128 + sig;
}

void festina_install_shutdown_handler(void) {
    signal(SIGINT, festina_shutdown_signal_handler);
#ifndef _WIN32
    signal(SIGTERM, festina_shutdown_signal_handler);
#endif
}

int64_t festina_shutdown_requested(void) {
    return g_shutdown_requested;
}

int64_t festina_shutdown_exit_code(void) {
    return g_shutdown_exit_code;
}
#else
/* WASI Preview 1 has no signal model at all -- confirmed directly:
 * wasi-libc's own <signal.h> provides none of sig_atomic_t/signal()/
 * SIGINT/SIGTERM, the identical "genuinely absent, not a hardware-
 * verification gate" situation exec()/http/try already document for
 * this target (see cli.py's _check_wasm_feature_supported). Graceful
 * shutdown simply doesn't apply here -- but this translation unit is
 * compiled UNCONDITIONALLY for every wasm build (core, like regex --
 * see this file's own top comment), and a timers-only wasm program
 * (uses_timers, still perfectly valid under WASI) still emits a call
 * to festina_install_shutdown_handler (see codegen.py's own
 * _emit_main_and_entry) -- so these stubs exist purely so the core
 * object file still compiles and links; the call itself is simply a
 * no-op here. */
void festina_install_shutdown_handler(void) { }
int64_t festina_shutdown_requested(void) { return 0; }
int64_t festina_shutdown_exit_code(void) { return 0; }
#endif

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

/* claude.md #190: the shared "grow, then copy exactly n bytes, then
 * keep the buffer NUL-terminated" step both festina_sb_append and
 * festina_sb_append_n are built from -- the only difference between
 * them is where n comes from (a runtime strlen() scan vs. a length
 * the CALLER already knows). Doesn't itself NUL-terminate mid-string
 * the way festina_sb_append's own old inline version incidentally did
 * (copying s's own trailing NUL along with it) -- callers that build a
 * string out of several appends (every one of the JSON-rendering ones)
 * only need the final byte terminated once, not after every single
 * piece, so this terminates unconditionally at the new length instead,
 * which is cheap (one store) and correct for both single-call and
 * multi-call use. */
static void festina_sb_append_bytes(FestinaSB *sb, const char *s, size_t n) {
    festina_sb_grow(sb, n);
    memcpy(sb->data + sb->len, s, n);
    sb->len += n;
    sb->data[sb->len] = '\0';
}

void festina_sb_append(void *sbv, const char *s) {
    if (!s) return;
    festina_sb_append_bytes((FestinaSB *)sbv, s, strlen(s));
}

/* claude.md #190: same as festina_sb_append, but for a caller that
 * already knows the byte length -- every codegen-emitted JSON
 * punctuation/field-key append (_json_fn_for's own generated IR) comes
 * from a compile-time string constant, whose exact length is already
 * known in Python at codegen time (the same count _encode_c_string
 * computes for the constant's own storage) and was previously thrown
 * away, forcing this exact runtime strlen() rescan on every call --
 * once per struct field, per row, in a loop rendering many values.
 * `len <= 0` is a no-op, matching festina_sb_append's own NULL-is-a-
 * no-op contract (an empty/negative length has nothing to copy). */
void festina_sb_append_n(void *sbv, const char *s, int64_t len) {
    if (!s || len <= 0) return;
    festina_sb_append_bytes((FestinaSB *)sbv, s, (size_t)len);
}

/* A JSON string: quoted, with the escapes JSON requires. A NULL text
 * renders as the JSON null literal, unquoted -- the same three-way
 * honesty festina_str_from_bool already applies.
 *
 * claude.md #190: scans forward for a RUN of bytes needing no escape
 * (the overwhelmingly common case -- ordinary printable ASCII, and any
 * valid UTF-8 continuation/lead byte, both >= 0x20 and neither quote
 * nor backslash) and copies the whole run in one festina_sb_append_
 * bytes call, rather than the byte-at-a-time switch/snprintf/strlen/
 * memcpy sequence the previous version ran unconditionally for EVERY
 * byte, escaped or not. Falls into the single-character path only for
 * a byte that actually needs escaping; behavior (including the exact
 * `\uXXXX` fallback for control characters below 0x20 other than \n/
 * \r/\t) is unchanged from before -- this only changes how many bytes
 * move per underlying copy. */
void festina_sb_append_json_text(void *sbv, const char *s) {
    FestinaSB *sb = (FestinaSB *)sbv;
    if (!s) { festina_sb_append(sb, "null"); return; }
    festina_sb_grow(sb, 2);
    sb->data[sb->len++] = '"';
    const unsigned char *p = (const unsigned char *)s;
    const unsigned char *run_start = p;
    for (; *p; p++) {
        unsigned char c = *p;
        if (c >= 0x20 && c != '"' && c != '\\') continue;  /* safe run continues */
        if (p > run_start) {
            festina_sb_append_bytes(sb, (const char *)run_start, (size_t)(p - run_start));
        }
        char esc[8];
        const char *out;
        switch (c) {
        case '"':  out = "\\\""; break;
        case '\\': out = "\\\\"; break;
        case '\n': out = "\\n"; break;
        case '\r': out = "\\r"; break;
        case '\t': out = "\\t"; break;
        default:
            snprintf(esc, sizeof(esc), "\\u%04x", c);
            out = esc;
        }
        festina_sb_append_bytes(sb, out, strlen(out));
        run_start = p + 1;
    }
    if (p > run_start) {
        festina_sb_append_bytes(sb, (const char *)run_start, (size_t)(p - run_start));
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

/* ---- claude.md #150: text.toInt() / text[i] ----
 *
 * toInt() is JS parseInt()-style: strtoll() already does exactly the
 * skip-leading-whitespace, optional-sign, stop-at-first-non-digit
 * dance this wants, so this just distinguishes "genuinely nothing
 * parseable" (endptr never moved past the start at all) from a real
 * parse -- reusing libc's own parser rather than hand-rolling a second,
 * divergent one. festina_null_int() is declared further down (the
 * sqlite() section) but defined as a plain `static` function above its
 * own first use there -- forward-declared here for that reason. */
static int64_t festina_null_int(void);

int64_t festina_text_to_int(const char *s) {
    if (!s) s = "";
    char *end = NULL;
    long long v = strtoll(s, &end, 10);
    if (end == s) return festina_null_int();  /* nothing parseable at all */
    return (int64_t)v;
}

/* text[i] -> a single UTF-8 code point, the same unit split('') already
 * uses (see festina_text_split's own empty-separator branch just
 * above) -- walked independently here rather than factored out, since
 * this one also has to stop early once it reaches `index`, and NULL on
 * a negative or past-the-end index rather than that function's own
 * "whole string, walked to completion" shape. */
char *festina_text_char_at(const char *s, int64_t index) {
    if (!s) s = "";
    if (index < 0) return NULL;
    const char *c = s;
    int64_t i = 0;
    while (*c) {
        const char *start = c;
        c++;
        while ((*c & 0xC0) == 0x80) c++;
        if (i == index) {
            size_t len = (size_t)(c - start);
            char *out = malloc(len + 1);
            if (!out) festina_fail("out of memory in text indexing");
            memcpy(out, start, len);
            out[len] = '\0';
            return out;
        }
        i++;
    }
    return NULL;  /* index >= the text's own code point count */
}

/* ---- claude.md #150: argv ---- */

void *festina_argv_array(int argc, char **argv) {
    FestinaPieces p = {NULL, 0, 0};
    for (int i = 0; i < argc; i++) {
        const char *a = argv[i] ? argv[i] : "";
        festina_pieces_push(&p, a, strlen(a));
    }
    return festina_pieces_finish(&p);
}

/* ---- claude.md #150: exec(); extended by the exec(args, callback)
 * non-blocking form below (a new claude.md entry) ---- */

/* claude.md #150 (widened): the actual "run this NULL-terminated argv,
 * return the real exit code" logic, one #if branch per platform,
 * extracted out of festina_process_exec below so BOTH the synchronous
 * exec() path and the new non-blocking exec(args, callback) worker
 * (further down) share exactly one copy of this -- particularly the
 * POSIX branch's self-pipe fork/exec/waitpid dance, which is exactly
 * the kind of subtle, easy-to-regress logic that must never exist in
 * two places. Takes an already NULL-terminated argv (no header
 * decoding, no ownership of the strings/array it's given) -- callers
 * decide separately whether that argv borrows its strings (the
 * synchronous path, safe since the caller's own arr[text] outlives the
 * call) or owns independent copies (the async path, needed since its
 * own worker runs after the caller's array may already be gone -- see
 * festina_process_exec_dispatch below). fork() in a multithreaded
 * process only duplicates the calling thread into the child, which
 * execvp()'s immediately after (the standard, safe "fork then exec
 * right away" pattern), and waitpid() here always targets this exact
 * child's own pid, never a wildcard -- so this is just as safe to call
 * from a worker thread as from the main one. */
#if defined(_WIN32)
static int64_t festina_run_argv(char *const *argv_c) {
    /* _P_WAIT: spawn and block until it exits, handing back its exit
     * code directly -- the closest _spawnvp equivalent to fork()+
     * execvp()+waitpid() below. PATH-searched, same as execvp. */
    intptr_t rc = _spawnvp(_P_WAIT, argv_c[0], (const char *const *)argv_c);
    return (rc == -1) ? -1 : (int64_t)rc;
}
#elif defined(__wasi__)
static int64_t festina_run_argv(char *const *argv_c) {
    /* Never actually reached -- see this file's own include-block
     * comment on why wasm32-wasi still needs this to exist and compile
     * cleanly even though nothing can call it in a real program. */
    (void)argv_c;
    return -1;
}
#else
static int64_t festina_run_argv(char *const *argv_c) {
    /* A self-pipe, close-on-exec on both ends -- the standard trick for
     * telling "the process never started at all" (a missing/
     * unexecutable path) apart from "it started and genuinely exited
     * with this same code on its own". Confirmed directly this
     * distinction is NOT free: a first version without the pipe read
     * back exit code 127 for a missing executable -- indistinguishable
     * from a real program that legitimately calls exit(127) itself --
     * because a failed execvp()'s own fallback `_exit(127)` is an
     * ordinary, WIFEXITED-true exit as far as waitpid() can see, not a
     * distinct kind of failure. A successful execvp() replaces the
     * child's whole image, closing the write end for free (CLOEXEC)
     * with nothing ever written to it, so the parent's read below
     * returns EOF (0 bytes) immediately; a FAILED execvp() falls
     * through to the write() first, which is what the parent actually
     * checks for. */
    int pfd[2];
    if (pipe(pfd) != 0) return -1;
    fcntl(pfd[0], F_SETFD, FD_CLOEXEC);
    fcntl(pfd[1], F_SETFD, FD_CLOEXEC);

    pid_t pid = fork();
    int64_t result;
    if (pid < 0) {
        close(pfd[0]);
        close(pfd[1]);
        result = -1;
    } else if (pid == 0) {
        /* Child: execvp only ever returns on failure (the executable
         * wasn't found, wasn't executable, ...) -- _exit (not exit) so
         * a failed exec doesn't run the PARENT's own atexit handlers/
         * flush its buffers a second time in this now-duplicated
         * process. */
        close(pfd[0]);
        execvp(argv_c[0], argv_c);
        int exec_errno = errno;
        ssize_t written = write(pfd[1], &exec_errno, sizeof(exec_errno));
        (void)written;  /* best effort -- about to _exit regardless */
        _exit(127);
    } else {
        close(pfd[1]);
        int exec_errno = 0;
        ssize_t got = read(pfd[0], &exec_errno, sizeof(exec_errno));
        close(pfd[0]);
        int status;
        if (waitpid(pid, &status, 0) < 0) {
            result = -1;
        } else if (got > 0) {
            /* execvp() itself failed in the child -- it never really
             * started, regardless of what its own exit code happened
             * to be. */
            result = -1;
        } else if (WIFEXITED(status)) {
            result = WEXITSTATUS(status);
        } else {
            /* Killed by a signal rather than exiting -- no single
             * non-negative exit-code encoding for that would avoid
             * colliding with a real exit code, so this collapses to
             * the same "couldn't get a real answer" -1 a start failure
             * already uses, rather than inventing a second, narrower
             * sentinel space a caller would have to know about too. */
            result = -1;
        }
    }
    return result;
}
#endif

int64_t festina_process_exec(void *args) {
    if (!args) return -1;
    int64_t *header = (int64_t *)args;
    int64_t n = header[0];
    if (n <= 0) return -1;
    char **data;
    memcpy(&data, &header[1], sizeof(char **));

    /* execvp needs a NULL-terminated argv, unlike Festina's own
     * arr[text] (a plain count + pointer array, no sentinel) -- built
     * fresh here rather than assuming the caller already left room.
     * These entries BORROW the caller's own strings (never strdup'd) --
     * safe because the caller's arr[text] is guaranteed to outlive this
     * whole synchronous call. */
    char **argv_c = malloc((size_t)(n + 1) * sizeof(char *));
    if (!argv_c) return -1;
    for (int64_t i = 0; i < n; i++) argv_c[i] = data[i] ? data[i] : "";
    argv_c[n] = NULL;

    int64_t result = festina_run_argv(argv_c);
    free(argv_c);
    return result;
}

/* claude.md #177 (new entry): exec(args, callback) -- the non-blocking
 * counterpart to exec(args) above, dispatched onto the exact same
 * background worker pool blob/img/aud's own `.callback()` already
 * runs on (festina_async_io_dispatch, see this file's own comment on
 * it and runtime/festina_runtime_async.c). Unlike a blob/img/aud
 * load, there is no natural pointer-shaped "result" value to hand
 * back and mutate in place -- the result is a plain int64_t exit
 * code -- so this uses its own small owned payload instead of reusing
 * a Festina-visible value as one. */

typedef struct FestinaExecPayload {
    /* Offsets 0 and 8, deliberately: codegen's own generated trampoline
     * (festina/codegen.py's _emit_exec_callback_trampoline) reads BOTH
     * fields directly -- `load i64, ptr %payload` for exit_code, then a
     * `getelementptr {i64, ptr}, ptr %payload, i32 0, i32 1` for
     * user_callback -- so the trampoline never needs to know this
     * struct's full shape, only that it starts with exactly these two
     * fields in this order, the same convention this runtime's other
     * raw-i64 payload marshaling (_i64_to_map_value/_map_value_to_i64
     * in codegen.py) already relies on for exit_code alone.
     *
     * user_callback exists at all because, unlike a per-call-site
     * trampoline that could hardcode a fixed symbol (the shape
     * _emit_map_value_release_trampoline itself uses, since there is
     * exactly one release function per type), exec(args, callback)'s
     * own callback is checked the SAME permissive, structural way
     * blob/img/aud's `.callback()` already is (semantic.py, claude.md
     * #177) -- any func[int]:void-typed EXPRESSION, not just a bare
     * declared-function name. That means the real callback can be an
     * ordinary runtime SSA value (a variable, a struct field, ...),
     * which a separately-emitted top-level LLVM function could never
     * reference directly. Routing it through the payload as plain data
     * -- exactly like exit_code itself -- lets ONE generic trampoline
     * serve every call site, compile-time-constant or not. */
    int64_t exit_code;
    void (*user_callback)(int64_t);
    char **argv;   /* owned: every string strdup'd, NULL-terminated --
                    * independent of the caller's own arr[text], which
                    * codegen releases immediately after dispatching
                    * (see festina_process_exec_dispatch's own doc
                    * comment on why this needs its own copy at all,
                    * unlike the synchronous festina_process_exec
                    * above). */
} FestinaExecPayload;

/* Runs on a background worker thread (see festina_runtime_async.c) --
 * matches festina_blob_load_worker's own contract exactly: read
 * whatever the payload already has queued up, write the real result
 * into the SAME payload the main thread will hand to the callback. */
static void festina_exec_worker(void *payload) {
    FestinaExecPayload *p = (FestinaExecPayload *)payload;
    p->exit_code = festina_run_argv(p->argv);
}

/* release_fn, called on the main thread right after the callback --
 * frees the owned deep copy this payload's own argv holds (both the
 * strings and the array), then the payload struct itself. */
static void festina_exec_payload_free(void *payload) {
    FestinaExecPayload *p = (FestinaExecPayload *)payload;
    if (p->argv) {
        for (char **a = p->argv; *a; a++) free(*a);
        free(p->argv);
    }
    free(p);
}

/* codegen's own entry point for exec(args, callback) -- `args` is the
 * identical arr[text] header pointer festina_process_exec itself
 * reads; `user_callback` is the program's real func[int]:void value,
 * carried through as opaque data (see FestinaExecPayload's own comment
 * on why -- it may be an arbitrary runtime value, not just a bare
 * function symbol); `trampoline` is codegen's own single generated
 * void(ptr) wrapper that reads exit_code and user_callback back out of
 * the payload and calls the latter (see
 * _emit_exec_callback_trampoline's own comment for why a trampoline is
 * needed here at all, unlike blob/img/aud's own callback -- those are
 * already ptr-shaped Festina values, an int isn't).
 *
 * Deep-copies `args` into this payload's own argv BEFORE ever queuing
 * the job -- unlike blob's own dispatcher (which only needs to strdup
 * one path string), exec's argument is a whole Festina-managed
 * arr[text], and the worker that actually needs it runs later, quite
 * possibly after codegen has already released the caller's own array
 * (the same release timing the synchronous exec() codegen path
 * already uses, right after this call returns) -- so nothing here can
 * borrow the caller's own strings the way the synchronous path above
 * safely does. */
void festina_process_exec_dispatch(void *args, void *user_callback,
                                    void (*trampoline)(void *)) {
    if (!args) return;
    int64_t *header = (int64_t *)args;
    int64_t n = header[0];
    if (n <= 0) return;
    char **data;
    memcpy(&data, &header[1], sizeof(char **));

    char **argv_c = malloc((size_t)(n + 1) * sizeof(char *));
    if (!argv_c) festina_fail("out of memory dispatching exec()");
    int64_t copied = 0;
    for (; copied < n; copied++) {
        const char *src = data[copied] ? data[copied] : "";
        argv_c[copied] = strdup(src);
        if (!argv_c[copied]) {
            for (int64_t i = 0; i < copied; i++) free(argv_c[i]);
            free(argv_c);
            festina_fail("out of memory dispatching exec()");
        }
    }
    argv_c[n] = NULL;

    FestinaExecPayload *payload = malloc(sizeof(*payload));
    if (!payload) festina_fail("out of memory dispatching exec()");
    payload->exit_code = -1;
    payload->user_callback = (void (*)(int64_t))user_callback;
    payload->argv = argv_c;

    festina_async_io_dispatch(payload, festina_exec_worker, trampoline,
                               festina_exec_payload_free);
}

/* ---- claude.md #132: mkdir()/ls() ----
 *
 * mkdir(path) answers a bool -- true if it created the directory, false
 * for every other outcome (already exists, a missing parent, no
 * permission, ...), never failing the program. The same "a program
 * tests for this, it doesn't stop the program over it" choice claude.md
 * #93 made for the file builtins blob's own methods replaced (claude.md
 * #109) -- extended here to directories rather than files. */
int8_t festina_mkdir(const char *path) {
    if (!path || !*path) return 0;
#ifdef _WIN32
    return _mkdir(path) == 0 ? 1 : 0;
#else
    return mkdir(path, 0777) == 0 ? 1 : 0;
#endif
}

/* ls(path) answers arr[text] of the directory's own entry NAMES (not
 * full paths, and not "." or ".." -- neither is useful to a program
 * that wants to iterate what a directory holds), built exactly like
 * festina_text_split above: the same FestinaPieces accumulator, wrapped
 * in the same fresh refcounted arr[text] by festina_pieces_finish. A
 * missing or unreadable directory answers an EMPTY array rather than
 * failing the program -- the same test-don't-fail choice mkdir() just
 * above makes, and blob.exists() made before it. */
void *festina_ls(const char *path) {
    FestinaPieces p = {NULL, 0, 0};
    if (path && *path) {
        DIR *dir = opendir(path);
        if (dir) {
            struct dirent *entry;
            while ((entry = readdir(dir)) != NULL) {
                if (strcmp(entry->d_name, ".") == 0 || strcmp(entry->d_name, "..") == 0) {
                    continue;
                }
                festina_pieces_push(&p, entry->d_name, strlen(entry->d_name));
            }
            closedir(dir);
        }
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

/* claude.md #165: runs on a background worker thread (see
 * festina_runtime_async.c) -- reads `b->path` (already set, at
 * construction time, by festina_blob_load_dispatch below) and fills
 * in `bytes`/`length` in place, mutating the SAME blob value the
 * caller already got back immediately. Never throws (matches
 * festina_blob_open's own "unreadable path -> empty blob" contract
 * exactly), so this needs none of festina_runtime_http.c's own
 * catch-frame machinery. */
static void festina_blob_load_worker(void *payload) {
    FestinaBlob *b = (FestinaBlob *)payload;
    int64_t len = 0;
    char *bytes = festina_read_file_sized(b->path, &len);
    if (!bytes) { bytes = strdup(""); len = 0; }
    free(b->bytes);
    b->bytes = bytes;
    b->length = len;
}

/* claude.md #165: codegen's own entry point for a `.callback()`-
 * carrying blob construction (`blob b = 'path'.callback(fn)` or the
 * fully anonymous `blob 'path'.callback(fn)`) -- mirrors
 * festina_http_send_client_dispatch's own null-check shape exactly.
 * `callback` NULL is the unchanged, fully synchronous path (identical
 * to festina_blob_open, just routed through here so codegen has one
 * call site regardless of whether a callback is present -- the
 * null-vs-non-null decision has to happen at RUNTIME, the same reason
 * http's own dispatcher does). Non-NULL returns an EMPTY blob
 * (bytes/length zero, `path` already correct) immediately -- exactly
 * as unpopulated, and just as indistinguishable from a genuinely
 * empty/unreadable file, as blob's own pre-existing "test, don't
 * fail" contract already made every OTHER unreadable-path case; the
 * whole point of `callback` is not reading the blob until it fires. */
void *festina_blob_load_dispatch(const char *path, void (*callback)(void *)) {
    if (!callback) return festina_blob_open(path);
    if (!path) path = "";
    char *copy = strdup(path);
    if (!copy) festina_fail("out of memory allocating a blob");
    char *empty = strdup("");
    if (!empty) festina_fail("out of memory allocating a blob");
    void *payload = festina_blob_alloc(copy, empty, 0);
    festina_retain(payload); /* survives after the caller's own scope releases its
                              * reference -- balanced by festina_blob_release,
                              * passed to festina_async_io_run as release_fn below */
    festina_async_io_dispatch(payload, festina_blob_load_worker, callback, festina_blob_release);
    return payload;
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
    /* windows.md Phase 0 (claude.md #126): localtime_r is POSIX, not
     * ISO C, and MinGW-w64's UCRT headers don't provide it -- only
     * Microsoft's own localtime_s, which is otherwise the same
     * thread-safe idea but reverses the argument order (tm* first,
     * time_t* second) and reports success as 0, not a non-NULL
     * pointer. This was the one spot the "core runtime is pure POSIX,
     * no platform branches needed" audit missed, found by the first
     * real Windows CI run that got far enough to compile it. */
#ifdef _WIN32
    if (localtime_s(&parts, &secs) != 0) return NULL;
#else
    if (!localtime_r(&secs, &parts)) return NULL;
#endif
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

/* claude.md #151: the same indirection req.toImg()/req.toAud()
 * (festina_runtime_http.c) go through, for the identical reason the
 * sqlite-column path just below already does -- core must not
 * reference festina_image_from_bytes/festina_audio_from_bytes
 * directly (those live in the graphics/audio translation units), so
 * these two thin wrappers are what any OTHER translation unit calls
 * instead. NULL (no decoder registered -- the program never actually
 * uses graphics/audio, so codegen never registered one) answers NULL
 * rather than crashing, the same "unset decoder" behavior the column
 * path already has. */
void *festina_decode_image_bytes(const void *data, int64_t len, const char *label) {
    return g_image_decoder ? g_image_decoder(data, len, label) : NULL;
}

void *festina_decode_audio_bytes(const void *data, int64_t len, const char *label) {
    return g_audio_decoder ? g_audio_decoder(data, len, label) : NULL;
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

/* claude.md #126 round nine: no compiled program ever explicitly
 * closed its own database handle before this -- main() just returned
 * and let the OS reclaim the file descriptor on process exit, which
 * WORKS (SQLite's WAL format is specifically designed to survive an
 * unclosed/crashed writer -- the next connection recovers it) but
 * skips SQLite's own auto-checkpoint-on-last-close, leaving the
 * database's actual data in the WAL file rather than the main one
 * until something else triggers a checkpoint. That's still readable by
 * any WAL-aware SQLite build, but real Windows CI's SQLite schema-sync
 * tests -- a second, separate process (a plain Python sqlite3
 * connection, not necessarily even the SAME SQLite build/version this
 * binary statically links) reading back a schema the FIRST compiled
 * program had just committed -- kept seeing the OLD schema, exactly
 * the symptom an unwritten-back WAL would produce for a reader that
 * can't or doesn't perform WAL recovery identically. Explicitly
 * closing forces SQLite's own checkpoint, leaving the main .sqlite
 * file itself fully caught up regardless of what reads it next.
 *
 * sqlite3_close() (not the _v2 form) is used deliberately: unlike
 * _v2, which silently defers to a "zombie" close if anything is still
 * unfinalized, plain sqlite3_close() returns SQLITE_BUSY and does
 * NOTHING if it is -- exactly the signal needed to know finalizing the
 * statement cache below actually worked, rather than papering over a
 * bug in it. festina_sqlite_prepare_cached's whole point is to leave
 * cached statements alive across many calls (never finalized during
 * normal operation, only reset) -- so at real program shutdown, unlike
 * any other close, every one of them needs finalizing first or this
 * close does nothing at all. */
void festina_db_close(sqlite3 *db) {
    if (!db) return;
    for (int i = 0; i < g_cached_stmt_count; i++) {
        sqlite3_finalize(g_cached_stmts[i]);
    }
    g_cached_stmt_count = 0;
    /* claude.md #126 round eleven: round nine's own bet that this
     * function would fix the still-open SQLite schema-sync mismatches
     * was refuted by round ten's real Windows log -- unchanged,
     * identical failures with this fix already in place. The finalize
     * loop above was reasoned to be the one thing that could make
     * sqlite3_close return anything other than SQLITE_OK, but that was
     * never actually confirmed on the platform where it matters --
     * this call was, and still is, best-effort. Surfacing a non-OK
     * result to stderr costs nothing (never changes program behavior
     * or the exit code) and gives the next real log a concrete answer
     * instead of another silent maybe, should the tests calling this
     * out in their own failure diagnostics need it. */
    int rc = sqlite3_close(db);
    if (rc != SQLITE_OK) {
        fprintf(stderr, "festina_db_close: sqlite3_close returned %d (%s) -- "
                "a statement or blob handle was still open\n", rc, sqlite3_errmsg(db));
    }
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
 * problems first.
 *
 * claude.md #188 (uraikus/festina#76 item 5): `want_rowid` adds ONE
 * MORE hidden slot, after the presence mask, holding the query's own
 * `rowid` result column (matched by name, the identical mechanism
 * every declared column already uses) -- int's own null if the SQL
 * never selected one (`SELECT rowid, ...` is required; a bare
 * `SELECT *` does not implicitly include it). This is what
 * festina/codegen.py's row.rowid reads. Deliberately a caller-chosen
 * FLAG rather than something col_count/col_names/col_types already
 * imply: this same function also collects rows for a `struct` query
 * target (claude.md #112), which has no rowid concept and must keep
 * its existing row layout completely unchanged -- and even for a
 * `table` row, col_count/col_names/col_types stay exactly the
 * DECLARED schema (the same arrays schema sync's own CREATE TABLE/
 * ALTER TABLE reads), never widened to include `rowid`, so
 * festina_row_undefined's own presence-mask offset (row[col_count])
 * is completely unaffected by this flag either way. */
void festina_sqlite_collect_rows(sqlite3_stmt *stmt, int32_t col_count,
                                  const char **col_types, const char **col_names,
                                  int64_t *out_length, void **out_data,
                                  int8_t want_rowid) {
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
    /* Same by-name search, once, for the synthetic rowid slot -- never
     * part of col_names, so it can never collide with (or be shadowed
     * by) a real declared column of that name. */
    int32_t rowid_src = -1;
    if (want_rowid) {
        for (int r = 0; r < result_cols; r++) {
            const char *rn = sqlite3_column_name(stmt, r);
            if (rn && sqlite3_stricmp(rn, "rowid") == 0) { rowid_src = r; break; }
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
         * existing field offset is untouched. +1 more, only when
         * want_rowid, for the rowid slot right after THAT -- so a
         * struct-query row (want_rowid always false) allocates exactly
         * what it always has. */
        int64_t *row = malloc(((size_t)col_count + 1 + (want_rowid ? 1 : 0)) * sizeof(int64_t));
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
        if (want_rowid) {
            int64_t rid = (rowid_src >= 0 && sqlite3_column_type(stmt, rowid_src) != SQLITE_NULL)
                ? sqlite3_column_int64(stmt, rowid_src) : festina_null_int();
            row[col_count + 1] = rid;
        }
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

/* claude.md #165: the generic async-io hook seam -- see this trio's
 * own doc comment in festina_runtime.h for the full reasoning. All
 * three default to "nothing registered": festina_async_io_outstanding()
 * answers 0, festina_async_io_drain() does nothing, and
 * festina_async_io_dispatch() falls back to running the job inline
 * (synchronously) rather than crashing -- exactly as if
 * festina_runtime_async.c were never linked at all -- which, for a
 * program that never uses blob/img/aud's own `.callback()` form, it
 * never is.
 *
 * `run_fn` (unlike the other two) is NOT optional in practice --
 * festina_blob_load_dispatch (below) only ever calls
 * festina_async_io_dispatch AFTER a program has already used
 * `.callback()` somewhere, which is exactly what makes codegen set
 * uses_async_io and link festina_runtime_async.c at all, so this hook
 * is always registered by the time it's actually needed. The
 * synchronous fallback below exists purely so festina_blob_load_dispatch
 * itself can be UNCONDITIONALLY part of core (linked into every
 * program, whether or not it ever calls `.callback()`) without
 * core ever making a DIRECT symbol reference into the conditionally-
 * linked festina_runtime_async.c -- that direct-call mistake is
 * exactly what broke the very first build of this feature (a hard
 * link failure for every program that never used `.callback()` at
 * all, since festina_blob_load_dispatch calling
 * festina_async_io_run() BY NAME meant the linker needed that symbol
 * to exist regardless, confirmed directly by the immediate full-suite
 * failure this caused before this fix). */
static int64_t (*g_async_io_outstanding_fn)(void) = NULL;
static void (*g_async_io_drain_fn)(void) = NULL;
static void (*g_async_io_run_fn)(void *payload, void (*work_fn)(void *),
                                 void (*callback)(void *), void (*release_fn)(void *)) = NULL;

void festina_set_async_io_hooks(
        int64_t (*outstanding_fn)(void), void (*drain_fn)(void),
        void (*run_fn)(void *, void (*)(void *), void (*)(void *), void (*)(void *))) {
    g_async_io_outstanding_fn = outstanding_fn;
    g_async_io_drain_fn = drain_fn;
    g_async_io_run_fn = run_fn;
}

int64_t festina_async_io_outstanding(void) {
    return g_async_io_outstanding_fn ? g_async_io_outstanding_fn() : 0;
}

void festina_async_io_drain(void) {
    if (g_async_io_drain_fn) g_async_io_drain_fn();
}

void festina_async_io_dispatch(void *payload, void (*work_fn)(void *),
                               void (*callback)(void *), void (*release_fn)(void *)) {
    if (g_async_io_run_fn) {
        g_async_io_run_fn(payload, work_fn, callback, release_fn);
        return;
    }
    /* Unreachable in practice -- see this function's own doc comment
     * above -- but a real, correct synchronous fallback rather than a
     * silent no-op or a crash, if it's ever somehow reached anyway. */
    work_fn(payload);
    if (callback) callback(payload);
    if (release_fn) release_fn(payload);
}

/* claude.md #166: the http-servicing hook seam -- lets
 * festina_run_event_loop (festina_runtime_graphics.c, linked only when a
 * program opens a window) service an open openPort()/openSecurePort()
 * listener without a direct cross-translation-unit reference into
 * festina_runtime_http.c (linked only when a program uses http) --
 * mirrors festina_set_async_io_hooks immediately above exactly, for the
 * identical reason (a program using graphics but not http must never be
 * forced to link http.o just because festina_run_event_loop names one
 * of its symbols directly).
 *
 * This is what lifts claude.md #151's original "openPort() cannot be
 * combined with graphics" restriction: previously, main()'s own loop-
 * selection picked exactly one of festina_run_event_loop/
 * festina_run_http_loop, so a program using both would have one of them
 * silently never run at all -- rejected outright at compile time instead
 * (festina/cli.py). Now festina_run_event_loop also drains ready http
 * work each iteration (festina_http_service_ready below), bounding its
 * own wait the same way it already bounds it for outstanding async-io
 * work -- an open port adds up to FESTINA_ASYNC_IO_POLL_SECONDS of
 * latency to accepting a connection or reading the next byte while a
 * window is open, the same tradeoff already accepted for background
 * blob/img/aud loads. festina_run_http_loop itself is UNCHANGED and
 * still the loop used for a program that opens a port but never a
 * window -- these hooks are purely additive. */
static int64_t (*g_http_service_outstanding_fn)(void) = NULL;
static void (*g_http_service_ready_fn)(void) = NULL;

void festina_set_http_service_hooks(int64_t (*outstanding_fn)(void),
                                    void (*ready_fn)(void)) {
    g_http_service_outstanding_fn = outstanding_fn;
    g_http_service_ready_fn = ready_fn;
}

int64_t festina_http_service_outstanding(void) {
    return g_http_service_outstanding_fn ? g_http_service_outstanding_fn() : 0;
}

void festina_http_service_ready(void) {
    if (g_http_service_ready_fn) g_http_service_ready_fn();
}

/* claude.md #165: bounds a sleep/poll timeout that would otherwise be
 * "block forever" (no active timer) to a short, regular wake -- the
 * only way festina_run_timer_loop's own plain nanosleep (no fd to
 * poll(), unlike festina_run_http_loop's self-pipe) can notice a
 * background blob/img/aud load finishing in a timely way. 20ms is
 * arbitrary but small enough that a completed background load's own
 * callback fires promptly without this loop spinning uselessly the
 * rest of the time (it still only wakes AT ALL when there's a reason
 * to -- an active timer, or outstanding async-io work). */
#define FESTINA_ASYNC_IO_POLL_SECONDS 0.02

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
 * externally (or via clearInterval()) the same way. claude.md #165:
 * "nothing left to wait for" now also means "and no outstanding
 * blob/img/aud background load" -- see festina_async_io_outstanding
 * above. */
void festina_run_timer_loop(void) {
    while (1) {
        /* claude.md #161: checked once per iteration (this loop's own
         * natural poll point -- nanosleep is interrupted, EINTR, by
         * the very signal that sets this flag, so the very next check
         * after a Ctrl+C/SIGTERM sees it almost immediately, not after
         * waiting out the rest of whatever timer was pending). Exits
         * via festina_program_exit rather than a plain return, so a
         * declared `on exit(code:int)` handler still runs -- the same
         * clean-shutdown path close(code) already uses. */
        if (festina_shutdown_requested()) {
            festina_program_exit(festina_shutdown_exit_code());
        }
        double earliest = festina_next_timer_deadline();
        int64_t async_io_outstanding = festina_async_io_outstanding();
        if (earliest < 0.0 && async_io_outstanding == 0) {
            return; /* nothing left to wait for */
        }
        double remaining = earliest;
        if (earliest >= 0.0) {
            remaining = earliest - festina_now_seconds();
        }
        if (async_io_outstanding > 0
                && (earliest < 0.0 || remaining > FESTINA_ASYNC_IO_POLL_SECONDS)) {
            remaining = FESTINA_ASYNC_IO_POLL_SECONDS;
        }
        if (remaining > 0.0) {
            struct timespec ts;
            ts.tv_sec = (time_t)remaining;
            ts.tv_nsec = (long)((remaining - (double)ts.tv_sec) * 1e9);
            nanosleep(&ts, NULL);
        }
        festina_fire_expired_timers();
        festina_async_io_drain();
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


/* ---- maps -- claude.md #72, rebuilt into a real hash table by #175 ---- */

/* One bucket slot -- `value` is a raw 8-byte payload meaning whatever
 * the compiled program's own map[T] says it means (int64_t bits, a
 * double's raw bits, or a pointer -- see festina/codegen.py's
 * _map_value_to_i64/_i64_to_map_value for the reinterpretation, done
 * entirely on the LLVM IR side, since this runtime has no idea what T
 * a given map's values are, only ever seeing the already-flattened i64
 * payload). The same "one fixed-size slot per value" convention
 * festina_sqlite_collect_rows's row layout and every arr[T]'s own
 * per-element storage already use.
 *
 * `key` doubles as this bucket's occupancy state, the same sentinel
 * trick festina_conn_index_* (festina_runtime_http.c) already uses for
 * its own int64_t conn_id key: NULL means the slot has never been used
 * (a probe stops here -- nothing further down the chain), and the
 * reserved FESTINA_MAP_TOMBSTONE pointer means a key WAS deleted here
 * (a probe must keep going -- a live key may sit further down the same
 * chain). Neither value can ever collide with a real key: every real
 * key is a strdup() result, never NULL and never the literal address
 * 1. */
typedef struct {
    char *key;      /* NULL = empty, FESTINA_MAP_TOMBSTONE = deleted,
                      * else an owned strdup'd copy -- see
                      * festina_map_set's own comment */
    int64_t value;
} FestinaMapEntry;

#define FESTINA_MAP_TOMBSTONE ((char *)1)

/* claude.md #175: FNV-1a over the NUL-terminated key. No generic
 * string hash existed anywhere in this runtime before this -- the
 * only other hash table here, festina_conn_index_* in
 * festina_runtime_http.c, mixes an int64_t conn_id, not text. FNV-1a
 * is the standard, simplest-adequate choice for short, arbitrary
 * string keys (map[T] is documented as staying small, config/game-
 * state shaped) -- no claim of cryptographic strength, none needed. */
static uint64_t festina_map_hash(const char *key) {
    uint64_t h = 1469598103934665603ULL; /* FNV offset basis */
    for (const unsigned char *p = (const unsigned char *)key; *p; p++) {
        h ^= (uint64_t)*p;
        h *= 1099511628211ULL; /* FNV prime */
    }
    return h;
}

/* Lookup-only probe: linear probing (claude.md #153's own
 * festina_conn_index_get is the direct prior art -- same shape, keyed
 * by text instead of int64_t here). Stops at the first true match or
 * the first never-used (NULL) slot; a tombstone doesn't stop the probe
 * since a matching live key may sit further down the same chain. */
static FestinaMapEntry *festina_map_find(void *entries, int64_t capacity, const char *key) {
    if (capacity == 0) return NULL;
    FestinaMapEntry *buckets = (FestinaMapEntry *)entries;
    uint64_t mask = (uint64_t)capacity - 1;
    uint64_t i = festina_map_hash(key) & mask;
    for (int64_t probes = 0; probes < capacity; probes++) {
        char *k = buckets[i].key;
        if (k == NULL) return NULL;
        if (k != FESTINA_MAP_TOMBSTONE && festina_str_eq(k, key)) return &buckets[i];
        i = (i + 1) & mask;
    }
    return NULL;
}

/* Insert-probe: like festina_map_find, but returns the slot a NEW key
 * should land in when the key isn't already present -- the first
 * tombstone seen along the chain (reused rather than left dead), or
 * the terminating empty slot if the chain held no tombstone. Returns
 * the EXISTING bucket directly if the key is already present (same
 * probe, no separate lookup needed first). `capacity` must be > 0 --
 * every caller has already grown the table before calling this. */
static FestinaMapEntry *festina_map_find_slot(void *entries, int64_t capacity, const char *key) {
    FestinaMapEntry *buckets = (FestinaMapEntry *)entries;
    uint64_t mask = (uint64_t)capacity - 1;
    uint64_t i = festina_map_hash(key) & mask;
    FestinaMapEntry *first_tombstone = NULL;
    for (int64_t probes = 0; probes < capacity; probes++) {
        char *k = buckets[i].key;
        if (k == NULL) return first_tombstone ? first_tombstone : &buckets[i];
        if (k == FESTINA_MAP_TOMBSTONE) {
            if (!first_tombstone) first_tombstone = &buckets[i];
        } else if (festina_str_eq(k, key)) {
            return &buckets[i];
        }
        i = (i + 1) & mask;
    }
    /* Unreachable under the load factor festina_map_set enforces below
     * (a full-table probe with no empty slot found) -- a safe fallback
     * rather than a crash/infinite loop, matching
     * festina_conn_index_get's own identical fallback. */
    return first_tombstone;
}

/* Rebuilds into a fresh, larger table -- claude.md #175, modeled
 * directly on festina_conn_index_grow. Doubles capacity (or starts at
 * 8 from an empty map), MOVES every live key's existing strdup'd
 * pointer into the new table (never re-strdup's, never frees a live
 * key), and drops every tombstone outright (a rebuilt table starts
 * with none) -- the mechanism that keeps tombstone buildup from
 * degrading probe chains under heavy insert/delete churn, even when
 * the live count itself never grows. Accepted tradeoff, the same one
 * festina_conn_index_grow already carries: capacity never shrinks, so
 * a map churned at a stable live size still grows its own bucket array
 * over time rather than compacting in place -- bounded per real
 * program (tests/test_leak_stress.py's 200-iteration churn tops out
 * around capacity 256, ~4KB), not worth solving here. */
static void festina_map_grow(int64_t *entries_count, void **entries, int64_t *capacity,
                             int64_t *tombstones) {
    (void)entries_count; /* count itself is untouched by a rebuild */
    int64_t old_capacity = *capacity;
    FestinaMapEntry *old_buckets = (FestinaMapEntry *)*entries;
    int64_t new_capacity = old_capacity ? old_capacity * 2 : 8;
    FestinaMapEntry *new_buckets = calloc((size_t)new_capacity, sizeof(FestinaMapEntry));
    if (!new_buckets) festina_fail("out of memory growing a map");
    for (int64_t i = 0; i < old_capacity; i++) {
        char *k = old_buckets[i].key;
        if (k == NULL || k == FESTINA_MAP_TOMBSTONE) continue;
        FestinaMapEntry *slot = festina_map_find_slot(new_buckets, new_capacity, k);
        slot->key = k;
        slot->value = old_buckets[i].value;
    }
    free(old_buckets);
    *entries = new_buckets;
    *capacity = new_capacity;
    *tombstones = 0;
}

/* claude.md #72/#175: npcHealths['npc1'] = 30 -- and the equivalent
 * per-entry calls a map literal itself builds out of (see
 * festina/codegen.py's _emit_map_lit). `count`/`entries`/`capacity`/
 * `tombstones` point INTO the map value's own storage (its LLVM
 * alloca/global slot, or -- during literal construction -- a scratch
 * header alloca; see _emit_map_set's own comment), not a separate map
 * "object" -- updating an existing key never needs to grow anything,
 * but adding a new one may need to rehash the whole table, which has
 * to write the new count/entries/capacity/tombstones back into that
 * same slot for the change to actually be visible to the caller (the
 * same "write results back through an out-pointer" shape
 * festina_sqlite_collect_rows already uses for its own row array).
 *
 * Grows (via festina_map_grow) whenever the table would cross 75% load
 * -- counting tombstones toward "used" the same way
 * festina_conn_index_put's own identical check does, so tombstone
 * buildup from delete-heavy churn still triggers a rehash even when
 * the live count itself stays flat. */
void festina_map_set(int64_t *count, void **entries, int64_t *capacity, int64_t *tombstones,
                     const char *key, int64_t value) {
    if (!key) key = "";
    if (*capacity == 0 || (*count + *tombstones + 1) * 4 >= *capacity * 3) {
        festina_map_grow(count, entries, capacity, tombstones);
    }
    FestinaMapEntry *slot = festina_map_find_slot(*entries, *capacity, key);
    if (slot->key != NULL && slot->key != FESTINA_MAP_TOMBSTONE) {
        slot->value = value;
        return;
    }
    if (slot->key == FESTINA_MAP_TOMBSTONE) (*tombstones)--;
    /* Copied, not aliased to the caller's own key pointer -- this map
     * may outlive whatever Festina value `key` came from (a local
     * variable going out of scope doesn't free anything in this
     * runtime -- see the module docstring's "no GC yet" note -- but
     * relying on that would be fragile and unclear to read), and a
     * literal key expression's string constant could in principle be
     * reused/interned differently by a future compiler change. Leaks,
     * like every other heap allocation in this runtime -- the same
     * accepted tradeoff, not a new one. */
    slot->key = strdup(key);
    if (!slot->key) festina_fail("out of memory growing a map");
    slot->value = value;
    (*count)++;
}

/* claude.md #111/#175: `delete m.key` / `delete m['key']` -- remove
 * the entry outright, JS-style, rather than setting it to null: a
 * deleted key stops existing (forEach no longer visits it, count
 * drops), which null could never express. `release` is the same
 * per-value-type trampoline festina_map_for_each already uses for
 * whole-map release (codegen's _emit_map_value_release_trampoline), or
 * NULL for a value type with nothing to release. The hole is left as a
 * TOMBSTONE, not compacted -- a hash table's own bucket order was
 * never insertion order to begin with, so there is nothing to
 * preserve, and later probes down the same chain still need to see
 * that a key USED to sit here rather than treating this slot as a
 * chain-terminating empty one. `capacity` is read-only here -- delete
 * never grows the table, only festina_map_set does. Returns whether
 * the key existed; deleting a missing key is a safe no-op, exactly
 * like JS. */
int8_t festina_map_delete(int64_t *count, void **entries, int64_t capacity, int64_t *tombstones,
                          const char *key, void (*release)(int64_t, const char *)) {
    if (!key) key = "";
    FestinaMapEntry *found = festina_map_find(*entries, capacity, key);
    if (!found) return 0;
    /* claude.md #120: the entry is REMOVED before its value is
     * released. The release may run a cycle trial that traverses this
     * very map, and an entry still pointing at a value whose count the
     * release just dropped would be double-counted by markGray -- the
     * same store-before-release rule every field write follows now
     * (see codegen's _emit_assign). */
    int64_t value = found->value;
    char *owned_key = found->key;
    found->key = FESTINA_MAP_TOMBSTONE;
    (*count)--;
    (*tombstones)++;
    if (release) release(value, owned_key);
    free(owned_key);
    return 1;
}

/* claude.md #72: npcHealths['npc1'] -- "if the key is not present, the
 * result is null." default_value is already the correct null
 * representation for this map's value type, computed by codegen (see
 * _map_missing_default) -- this function has no idea what T is, only
 * ever seeing raw i64 payloads, so it can't make that choice itself.
 * No `count` parameter (unlike before #175) -- a bucket scan is driven
 * by `capacity`, not a dense [0,count) range, so count was never
 * needed by a read here in the first place. */
int64_t festina_map_get(void *entries, int64_t capacity, const char *key, int64_t default_value) {
    if (!key) key = "";
    FestinaMapEntry *found = festina_map_find(entries, capacity, key);
    return found ? found->value : default_value;
}

/* claude.md #72/#175: npcHealths.forEach(callback). "The order entries
 * are visited in is not specified" -- true before this rewrite (plain
 * insertion order, incidentally) and still true now (bucket order, a
 * function of each key's hash, not insertion order at all); nothing
 * here should be relied on beyond every current entry being visited
 * exactly once. Scans every bucket, skipping the never-used and
 * tombstoned ones. If `callback` itself mutates this same map (adds a
 * key, changes an existing value) or calls .forEach() again, that's
 * explicitly unspecified behavior here, unlike festina_fire_expired_timers
 * (which *is* deliberately hardened against a timer callback growing/
 * clearing the timer list mid-iteration) -- claude.md #72 was never
 * asked to make that same guarantee for maps. */
void festina_map_for_each(void *entries, int64_t capacity, void (*callback)(int64_t, const char *)) {
    FestinaMapEntry *buckets = (FestinaMapEntry *)entries;
    for (int64_t i = 0; i < capacity; i++) {
        char *k = buckets[i].key;
        if (k == NULL || k == FESTINA_MAP_TOMBSTONE) continue;
        callback(buckets[i].value, k);
    }
}

/* claude.md #74/#75/#175: see this function's own declaration in
 * festina_runtime.h. Frees what festina_map_set's own comment already
 * establishes is exclusively owned by each live bucket -- a strdup'd
 * copy of the key, never aliased with any other Festina-visible value
 * -- before freeing the entries buffer itself. Scans every bucket
 * (skipping the never-used and tombstoned ones), not a dense
 * [0,count) range, for the same reason festina_map_for_each does. */
void festina_map_free_entries(void *entries, int64_t capacity) {
    FestinaMapEntry *buckets = (FestinaMapEntry *)entries;
    for (int64_t i = 0; i < capacity; i++) {
        if (buckets[i].key != NULL && buckets[i].key != FESTINA_MAP_TOMBSTONE) {
            free(buckets[i].key);
        }
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

/* claude.md #174: `capacity` is NULL for a plain arr[T] -- exact-size
 * realloc on every call, this function's own unchanged pre-#174
 * behavior -- or a pointer into an `amor arr[T]`'s own tracked
 * capacity field (FESTINA_AMOR_ARRAY_LLVM_TYPE's 3rd field) for
 * geometric doubling growth instead -- map[T]'s own `capacity` field
 * (festina_map_set, claude.md #175) tracks bucket-array size the same
 * way, just always-tracked rather than plain-vs-amor optional, since
 * every map[T] is a hash table now and none of them can be growth-by-
 * exactly-one anymore. Growing
 * (`new_length > *capacity`) doubles (or jumps straight to
 * `new_length` if even double isn't enough -- a single push never
 * needs more than one extra slot, but splice_insert's own inline
 * growth logic, which duplicates this shape rather than calling
 * through it, can ask for many at once); an amor array's own buffer
 * is deliberately NEVER freed/shrunk on the way back down to empty
 * (`new_length <= 0`) or on an ordinary shrink -- the whole point of
 * amortized growth is not paying a realloc on the very next push
 * right after a pop, and capacity already covers whatever the buffer
 * shrinks to, by construction. */
static void festina_array_resize(FestinaArrayHeader *a, int64_t *capacity,
                                  int64_t elem_size, int64_t new_length) {
    if (new_length <= 0) {
        if (!capacity) {
            free(a->data);
            a->data = NULL;
        }
        a->length = 0;
        return;
    }
    if (capacity) {
        if (new_length > *capacity) {
            int64_t new_cap = *capacity ? *capacity * 2 : 8;
            if (new_cap < new_length) new_cap = new_length;
            void *grown = realloc(a->data, (size_t)(new_cap * elem_size));
            if (!grown) festina_fail("out of memory growing an amortized array");
            a->data = grown;
            *capacity = new_cap;
        }
        a->length = new_length;
        return;
    }
    void *grown = realloc(a->data, (size_t)(new_length * elem_size));
    if (!grown) festina_fail("out of memory growing an array");
    a->data = grown;
    a->length = new_length;
}

void festina_array_push(void *hdr, int64_t *capacity, int64_t elem_size, const void *value) {
    FestinaArrayHeader *a = (FestinaArrayHeader *)hdr;
    if (!a || !value) return;
    int64_t at = a->length;
    festina_array_resize(a, capacity, elem_size, at + 1);
    memcpy((char *)a->data + at * elem_size, value, (size_t)elem_size);
}

void festina_array_unshift(void *hdr, int64_t *capacity, int64_t elem_size, const void *value) {
    FestinaArrayHeader *a = (FestinaArrayHeader *)hdr;
    if (!a || !value) return;
    int64_t was = a->length;
    festina_array_resize(a, capacity, elem_size, was + 1);
    if (was > 0) {
        memmove((char *)a->data + elem_size, a->data, (size_t)(was * elem_size));
    }
    memcpy(a->data, value, (size_t)elem_size);
}

/* pop/shift leave *out untouched when there is nothing to remove --
 * codegen has already stored the element type's own null there, so an
 * empty pop() answers null rather than needing a second return value. */
int8_t festina_array_pop(void *hdr, int64_t *capacity, int64_t elem_size, void *out) {
    FestinaArrayHeader *a = (FestinaArrayHeader *)hdr;
    if (!a || a->length <= 0) return 0;
    memcpy(out, (char *)a->data + (a->length - 1) * elem_size, (size_t)elem_size);
    festina_array_resize(a, capacity, elem_size, a->length - 1);
    return 1;
}

int8_t festina_array_shift(void *hdr, int64_t *capacity, int64_t elem_size, void *out) {
    FestinaArrayHeader *a = (FestinaArrayHeader *)hdr;
    if (!a || a->length <= 0) return 0;
    memcpy(out, a->data, (size_t)elem_size);
    int64_t rest = a->length - 1;
    if (rest > 0) {
        memmove(a->data, (char *)a->data + elem_size, (size_t)(rest * elem_size));
    }
    festina_array_resize(a, capacity, elem_size, rest);
    return 1;
}

/* JavaScript's own clamping, deliberately: a negative start counts back
 * from the end, everything out of range clamps rather than failing, and
 * a start past the end removes nothing. Anything stricter would make
 * the common `splice(i, 1)` inside a loop a source of crashes at the
 * boundaries instead of a no-op. `dst` is a header codegen already
 * allocated for the result. */
void festina_array_splice(void *hdr, int64_t *capacity, int64_t elem_size, int64_t start,
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
    festina_array_resize(a, capacity, elem_size, len - count);
}

/* claude.md #130: the 3-argument splice(start, count, insertArr) form --
 * JavaScript's splice(start, deleteCount, ...items) with the variadic
 * items spelled as one explicit arr[T] argument instead (Festina has no
 * variadic parameters). Removed elements are handed back through
 * `dst_hdr` exactly like the 2-argument form above; `insert_data`'s
 * `insert_len` raw elements then take their place. This function only
 * moves bytes -- it has no notion of a Festina type, so a refcounted or
 * text element copied in from `insert_data` is NOT retained/copied
 * here; codegen does that itself afterward, over the destination's own
 * newly-written range (see _emit_retain_or_own_range), the same
 * ownership split every other array method in this file already
 * follows (codegen decides refcounting, this file only decides bytes).
 */
void festina_array_splice_insert(void *hdr, int64_t *capacity, int64_t elem_size, int64_t start,
                                  int64_t count, const void *insert_data,
                                  int64_t insert_len, void *dst_hdr) {
    FestinaArrayHeader *a = (FestinaArrayHeader *)hdr;
    FestinaArrayHeader *dst = (FestinaArrayHeader *)dst_hdr;
    if (dst) { dst->length = 0; dst->data = NULL; }
    if (insert_len < 0) insert_len = 0;
    if (!a) return;

    int64_t len = a->length;
    if (start < 0) {
        start = len + start;
        if (start < 0) start = 0;
    }
    if (start > len) start = len;
    if (count < 0) count = 0;
    if (start + count > len) count = len - start;

    if (dst && count > 0) {
        dst->data = malloc((size_t)(count * elem_size));
        if (!dst->data) festina_fail("out of memory in splice()");
        memcpy(dst->data, (char *)a->data + start * elem_size,
               (size_t)(count * elem_size));
        dst->length = count;
    }

    int64_t tail = len - (start + count);
    int64_t new_length = len - count + insert_len;

    if (new_length <= 0) {
        /* claude.md #174: an amor array's own buffer is never freed on
         * the way back to empty -- see festina_array_resize's own
         * identical comment; this function duplicates that resize
         * logic rather than calling through it (the memmove has to be
         * interleaved with the resize, in a different order depending
         * on whether this call is growing or shrinking). */
        if (!capacity) {
            free(a->data);
            a->data = NULL;
        }
        a->length = 0;
        return;
    }

    if (new_length > len) {
        /* Growing: resize first (realloc preserves the existing bytes
         * up to the old length), then shift the tail right into its
         * final spot -- both source and destination ranges stay within
         * the just-grown buffer. claude.md #174: an amor array only
         * actually reallocs when the new length exceeds its already-
         * tracked capacity, and then doubles (or jumps straight to
         * `new_length` if even that isn't enough) rather than growing
         * to exactly `new_length` -- the same amortized shape
         * festina_array_resize's own growing branch uses. */
        if (capacity) {
            if (new_length > *capacity) {
                int64_t new_cap = *capacity ? *capacity * 2 : 8;
                if (new_cap < new_length) new_cap = new_length;
                void *grown = realloc(a->data, (size_t)(new_cap * elem_size));
                if (!grown) festina_fail("out of memory growing an amortized array");
                a->data = grown;
                *capacity = new_cap;
            }
        } else {
            void *grown = realloc(a->data, (size_t)(new_length * elem_size));
            if (!grown) festina_fail("out of memory growing an array");
            a->data = grown;
        }
        if (tail > 0) {
            memmove((char *)a->data + (start + insert_len) * elem_size,
                    (char *)a->data + (start + count) * elem_size,
                    (size_t)(tail * elem_size));
        }
    } else {
        /* Shrinking (or exactly the same size): shift the tail into
         * its final spot first -- (start + insert_len) + tail ==
         * new_length <= len, so it still fits inside the OLD buffer --
         * then resize down. claude.md #174: an amor array's own
         * capacity already covers `len`, and new_length <= len here,
         * so it covers new_length too -- nothing to reallocate, the
         * same "shrinking never frees/reallocs" contract
         * festina_array_resize's own shrinking case has. */
        if (tail > 0) {
            memmove((char *)a->data + (start + insert_len) * elem_size,
                    (char *)a->data + (start + count) * elem_size,
                    (size_t)(tail * elem_size));
        }
        if (!capacity) {
            void *shrunk = realloc(a->data, (size_t)(new_length * elem_size));
            if (!shrunk) festina_fail("out of memory in splice()");
            a->data = shrunk;
        }
    }
    a->length = new_length;

    if (insert_len > 0 && insert_data) {
        memcpy((char *)a->data + start * elem_size, insert_data,
               (size_t)(insert_len * elem_size));
    }
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

/* claude.md #184 (uraikus/festina#76 item 2): in-place, STABLE sort
 * over an arr[T]'s raw elem_size-byte slots -- JS's own
 * Array.prototype.sort contract, arguably the more surprising outcome
 * NOT to have (two equal-by-cmp elements swapping relative order would
 * be a visible behavior change if the array is ever re-sorted by a
 * different key later).
 *
 * Not plain qsort(): its comparator signature has no user-data slot at
 * all, and the portable-looking fixes (qsort_r/qsort_s) are NOT
 * actually portable across the platforms this runtime targets --
 * glibc's qsort_r takes the userdata argument last, BSD's takes it
 * first, and Windows's qsort_s again differs from both. Rather than
 * `#ifdef`ing three incompatible call shapes, this is a small
 * hand-rolled bottom-up merge sort whose OWN comparator signature
 * carries userdata as a real parameter from the start -- codegen's
 * `cmp` is always its own generated trampoline
 * (_emit_sort_comparator_trampoline), and `userdata` is the real
 * Festina comparator function VALUE (already a bare pointer, claude.md
 * #141), passed straight through unchanged on every call.
 *
 * `cmp` means exactly what a C qsort() comparator's return value
 * means: negative if *a sorts before *b, positive if after, zero if
 * equal. */
void festina_array_sort(void *hdr, int64_t elem_size,
                         int (*cmp)(const void *, const void *, void *),
                         void *userdata) {
    FestinaArrayHeader *a = (FestinaArrayHeader *)hdr;
    if (!a || a->length <= 1 || !a->data || !cmp) return;
    int64_t n = a->length;
    char *base = (char *)a->data;
    char *scratch = malloc((size_t)(n * elem_size));
    if (!scratch) festina_fail("out of memory sorting an array");
    for (int64_t width = 1; width < n; width *= 2) {
        for (int64_t lo = 0; lo < n; lo += 2 * width) {
            int64_t mid = lo + width < n ? lo + width : n;
            int64_t hi = lo + 2 * width < n ? lo + 2 * width : n;
            int64_t i = lo, j = mid, k = lo;
            while (i < mid && j < hi) {
                const void *ea = base + i * elem_size;
                const void *eb = base + j * elem_size;
                /* <= (not <) keeps this stable: a tie is resolved in
                 * favor of the left run, which always holds the
                 * earlier original elements. */
                if (cmp(ea, eb, userdata) <= 0) {
                    memcpy(scratch + k * elem_size, ea, (size_t)elem_size);
                    i++;
                } else {
                    memcpy(scratch + k * elem_size, eb, (size_t)elem_size);
                    j++;
                }
                k++;
            }
            while (i < mid) {
                memcpy(scratch + k * elem_size, base + i * elem_size, (size_t)elem_size);
                i++; k++;
            }
            while (j < hi) {
                memcpy(scratch + k * elem_size, base + j * elem_size, (size_t)elem_size);
                j++; k++;
            }
        }
        memcpy(base, scratch, (size_t)(n * elem_size));
    }
    free(scratch);
}

/* claude.md #186 (uraikus/festina#76 item 7): map[T].keys() -> arr[text]
 * and map[T].values() -> arr[T] -- a plain, independent snapshot array,
 * built once and then walked with an ordinary `for` loop. The actual
 * pain point the issue names: map[T].forEach's callback is bare/no-
 * closures (claude.md #72), so every "collect entries matching a
 * condition" call site had to promote its own accumulator state into
 * extra globals purely so the callback could reach it. `keys()`/
 * `values()` sidestep that entirely for exactly this shape -- no
 * callback needed at all.
 *
 * Both scan by CAPACITY, not count, the same live-entry test
 * festina_map_for_each already uses (a slot is live when its key is
 * neither NULL -- never used -- nor FESTINA_MAP_TOMBSTONE -- deleted).
 * `dst` is always a header codegen already allocated fresh via
 * _emit_fresh_heap_header (refcount=1, `{length: 0, data: NULL}`
 * zeroed) -- these two functions only ever fill in its `length`/`data`
 * fields, the same division of labor festina_array_splice's own `dst`
 * output parameter already has. */
void festina_map_keys(void *entries, int64_t capacity, void *dst) {
    FestinaMapEntry *buckets = (FestinaMapEntry *)entries;
    FestinaArrayHeader *out = (FestinaArrayHeader *)dst;
    int64_t count = 0;
    for (int64_t i = 0; i < capacity; i++) {
        char *k = buckets[i].key;
        if (k && k != FESTINA_MAP_TOMBSTONE) count++;
    }
    if (count == 0) return;
    char **data = malloc((size_t)count * sizeof(char *));
    if (!data) festina_fail("out of memory in festina_map_keys");
    int64_t out_i = 0;
    for (int64_t i = 0; i < capacity; i++) {
        char *k = buckets[i].key;
        if (!k || k == FESTINA_MAP_TOMBSTONE) continue;
        /* A fresh copy, not the map's own internal key pointer -- the
         * map keeps managing that one's lifetime independently (it can
         * be deleted, or the whole map released) long after this array
         * is handed back, so sharing the pointer would leave either
         * side able to invalidate the other's. */
        data[out_i++] = festina_text_own(k);
    }
    out->length = count;
    out->data = data;
}

/* claude.md #186: values() shares keys()'s own scan, but the element
 * representation depends on T -- `elem_size` (1 for bool, 8 for
 * everything else, exactly _elem_size's own domain) picks the write
 * width, and `is_refcounted`/`is_text` (mutually exclusive, both
 * compile-time known from T) pick the ownership operation, matching
 * .push()'s own rule (claude.md #96): a struct/arr/map/img/aud/regex/
 * blob value collected here is RETAINED (the map keeps its own live
 * reference to the same pointer, so without this the returned array
 * and the map would silently share ownership, and whichever released
 * first would leave the other dangling); a text value is COPIED
 * (festina_text_own, text having no shared representation to retain in
 * the first place); anything else (int/float/bool/color) is a plain
 * value needing neither. */
void festina_map_values(void *entries, int64_t capacity, int64_t elem_size,
                         int8_t is_refcounted, int8_t is_text, void *dst) {
    FestinaMapEntry *buckets = (FestinaMapEntry *)entries;
    FestinaArrayHeader *out = (FestinaArrayHeader *)dst;
    int64_t count = 0;
    for (int64_t i = 0; i < capacity; i++) {
        char *k = buckets[i].key;
        if (k && k != FESTINA_MAP_TOMBSTONE) count++;
    }
    if (count == 0) return;
    char *data = malloc((size_t)count * (size_t)elem_size);
    if (!data) festina_fail("out of memory in festina_map_values");
    int64_t out_i = 0;
    for (int64_t i = 0; i < capacity; i++) {
        char *k = buckets[i].key;
        if (!k || k == FESTINA_MAP_TOMBSTONE) continue;
        int64_t v = buckets[i].value;
        if (is_refcounted) {
            festina_retain((void *)(intptr_t)v);
        } else if (is_text) {
            v = (int64_t)(intptr_t)festina_text_own((const char *)(intptr_t)v);
        }
        if (elem_size == 1) {
            int8_t b = (int8_t)v;
            memcpy(data + out_i * elem_size, &b, 1);
        } else {
            memcpy(data + out_i * elem_size, &v, sizeof(v));
        }
        out_i++;
    }
    out->length = count;
    out->data = data;
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
    /* claude.md #175: payload is {i64 count, ptr entries, i64 capacity,
     * i64 tombstones} -- count and tombstones aren't needed to free the
     * data half (festina_map_free_entries scans every bucket up to
     * capacity itself, same as festina_map_for_each -- see its own
     * comment just above), so only entries/capacity are read here. Same
     * "map values aren't individually released" scope limitation as
     * festina_release_array above. */
    void *entries = *(void **)((char *)payload + sizeof(int64_t));
    int64_t capacity = *(int64_t *)((char *)payload + 2 * sizeof(int64_t));
    festina_map_free_entries(entries, capacity);
    free((char *)payload - sizeof(int64_t));
}

/* claude.md #167: found while chasing an unrelated keep-alive leak,
 * confirmed pre-existing and unrelated to it (reproduces identically on
 * a single plain request against the code exactly as it stood before
 * this entry) -- festina_release_map above is deliberately value-blind
 * (see its own doc comment), correct for a map[T] whose values need no
 * freeing (map[int], map[bool], ...) but WRONG for one whose values are
 * themselves owned, heap-allocated text -- every codegen-generated
 * map[text] variable already gets a DIFFERENT, value-aware release
 * function instead of the generic one (_release_fn_for_map in
 * codegen.py, which frees each value through festina_map_for_each
 * before deferring to festina_map_free_entries for the rest) precisely
 * because of this. This runtime builds a handful of map[text] values
 * directly in C, never through codegen, and every one of them was
 * calling the wrong (generic) release: an inbound request's own
 * `req.headers` and any http value's `.headers` in general
 * (festina_runtime_http.c's festina_release_http and its outbound-
 * response overwrite site), `socket.state` (festina_conn_teardown), and
 * `url.searchParams` (festina_release_url, just below) all leaked every
 * value they ever held. This is the C-side equivalent of codegen's own
 * wrapper -- frees each value via festina_map_for_each first, then
 * defers to the exact same festina_map_free_entries/header-free
 * festina_release_map itself uses. */
static void festina_free_map_text_value(int64_t raw, const char *key) {
    (void)key;
    free((void *)(intptr_t)raw);
}

void festina_release_text_map(void *payload) {
    if (!festina_release_check(payload)) return;
    void *entries = *(void **)((char *)payload + sizeof(int64_t));
    int64_t capacity = *(int64_t *)((char *)payload + 2 * sizeof(int64_t));
    festina_map_for_each(entries, capacity, festina_free_map_text_value);
    festina_map_free_entries(entries, capacity);
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
    /* claude.md #175: payload is {i64 count, ptr entries, i64 capacity,
     * i64 tombstones} -- scans every bucket up to capacity, skipping
     * the never-used and tombstoned ones, same as festina_map_for_each. */
    FestinaMapEntry *entries = *(FestinaMapEntry **)((char *)payload + sizeof(int64_t));
    int64_t capacity = *(int64_t *)((char *)payload + 2 * sizeof(int64_t));
    for (int64_t i = 0; i < capacity; i++) {
        char *k = entries[i].key;
        if (k == NULL || k == FESTINA_MAP_TOMBSTONE) continue;
        fn((void *)(intptr_t)entries[i].value);
    }
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
    /* claude.md #175: same {count, entries, capacity, tombstones}
     * layout as festina_cycle_visit_map above -- scans every bucket up
     * to capacity, freeing each live key. */
    FestinaMapEntry *entries = *(FestinaMapEntry **)((char *)payload + sizeof(int64_t));
    int64_t capacity = *(int64_t *)((char *)payload + 2 * sizeof(int64_t));
    for (int64_t i = 0; i < capacity; i++) {
        if (entries[i].key != NULL && entries[i].key != FESTINA_MAP_TOMBSTONE) {
            free(entries[i].key);
        }
    }
    free(entries);
    free((char *)payload - sizeof(int64_t));
}
