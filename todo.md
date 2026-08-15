# Roadmap

What's next, roughly in priority order. Not a promise of timeline — see
[`tests/CONTRACT.md`](tests/CONTRACT.md) and
[api.md](api.md) for what's already implemented and working today.

## macOS support

Currently Linux-only, verified against a real (or virtual) X server and
ALSA device — neither exists on macOS. To get a checkout running there:

- **Graphics**: the runtime's graphics translation unit
  (`runtime/festina_runtime_graphics.c`) is built directly on Xlib —
  macOS has no native X11 server (XQuartz is a third-party install, not
  built in). Either require XQuartz as a documented dependency (least
  work, worst experience) or add a second graphics backend behind the
  same `festina_runtime.h` function surface (`festina_graphics_init`,
  `festina_draw_*`, event registration, ...) targeting Cocoa/AppKit
  directly — the C runtime's public API is already fully opaqued to
  `void*`/primitive types (see [security.md](security.md#binary-slimming)'s
  note on why that split was possible at all), so a second backend
  would slot in the same way the graphics/audio split already does:
  compiled in and linked only when needed, per platform this time
  instead of per feature.
- **Audio**: same shape of problem — `festina_runtime_audio.c` is ALSA-
  specific (Linux only). macOS's native equivalent is CoreAudio; needs
  its own backend behind `loadAudio`/`.play()`/`.stop()`/`.isPlaying()`.
- **Build**: `pkg-config`/Homebrew paths, and confirming
  `libLLVM`/clang's toolchain behaves the same way on macOS as it does
  via the Debian/Ubuntu path this repo's tests currently exercise (see
  [setup.md](setup.md) — the Homebrew install line there is written but
  not yet verified in CI).
- **Packaging**: `scripts/package_compiler.sh` (PyInstaller) should work
  largely as-is, but the packaged binary itself needs testing on macOS,
  not just Linux.

## Windows support

Bigger gap than macOS — several of the runtime's POSIX assumptions don't
hold at all:

- **`<regex.h>`** (`claude.md #67/#68`'s regex support) isn't part of
  MSVC's C runtime — needs either a POSIX-compatibility shim (MinGW
  ships one) or swapping to a different regex approach on that target.
- **Graphics/audio**: same backend problem as macOS, but targeting
  Win32/GDI (or Direct2D) and WASAPI/DirectSound instead.
- **`<sys/select.h>`/`nanosleep`/`clock_gettime`** (the timer event
  loop — `festina_run_event_loop`/`festina_run_timer_loop`, see
  [security.md](security.md#binary-slimming)) are POSIX-only; Windows
  has its own equivalents (`WaitForMultipleObjects`,
  `QueryPerformanceCounter`, ...) that would need their own codepath.
- **Static sqlite3 linking** (see [setup.md](setup.md#static-linking-sqlite3))
  and **pkg-config** itself both work differently (or need an
  alternative, e.g. vcpkg) on Windows — the whole dependency-detection
  path in `festina/cli.py` assumes a pkg-config-shaped world.
- **Toolchain**: whether `festina/llvm_backend.py`'s libLLVM approach
  works unmodified against an MSVC/MinGW-produced `libLLVM`, or needs
  its own fallback story the way the clang-IR-frontend fallback exists
  for "no libLLVM" today.

Given the size of this gap, Windows support is likely to land in stages
(e.g. WSL/MinGW-based support first, native MSVC later), similar to how
"real compilation, minimal setup" (`claude.md #59`) was staged rather
than attempted as one change — see [api.md](api.md#compilation-pipeline).

## HTTP

Festina currently has no networking support at all — no HTTP client, no
HTTP server, no sockets of any kind. This is the single biggest gap
between "a language with SQLite/graphics/audio built in" and "a language
you'd reach for to build an actual application," and it's also why
[benchmark.md](benchmark.md) can't yet compare anything more realistic
than CPU-bound micro-benchmarks against Rust/Go/Bun (all three of which
have HTTP in their standard library or a dominant idiomatic choice).

Rough shape, following the same pattern established by SQLite/graphics/
audio (a small set of global functions/builtins, backed by a new runtime
translation unit that only links in when used — see
[security.md](security.md#binary-slimming)):

- An HTTP **client** first (lower risk, smaller surface —
  request/response as structs or a handful of builtin functions), likely
  on top of a minimal bundled implementation rather than a new external
  dependency, in keeping with `claude.md #59`'s minimal-dependencies
  principle (the same reasoning that picked Xlib over a GUI toolkit and
  ALSA over SDL_mixer).
- An HTTP **server** after that — needs a story for how request handling
  interacts with the existing timer/graphics event loop
  (`festina_run_event_loop`/`festina_run_timer_loop`), since Festina is
  currently single-threaded and cooperative outside of audio's one
  background-thread carve-out.
- TLS is its own decision point (bundled library vs. OS-provided vs.
  plaintext-only for a first cut) — deliberately not decided here.

Not started — no design doc, no `claude.md` section number reserved for
it yet. First step is likely a `claude.md` addition (the spec has led
every feature built so far, including this one's own audit process) and
a benchmarks addition once real (server) benchmarks are possible.

## Memory management

`claude.md #43` promises "automatic memory management" — the compiler
should "automatically release or reclaim memory when values are no
longer reachable." Arrays and struct storage were originally always
heap-allocated (`malloc`/`calloc`) and never freed — a real resource
leak in any long-running program, though never a memory-safety issue on
its own (see [security.md](security.md)'s note: no use-after-free, no
double-free, since nothing was ever freed). Three stages below have
since narrowed that: a provably-safe struct is now stack-allocated
outright (stage 3), and a provably-safe `arr[T]`/`map[T]`'s data is
still heap-allocated but now freed (stages 1/2) — anything not provably
safe under any stage is still heap-allocated (structs) or still leaks
(arrays/maps), exactly as originally described here.

### Stage 1: non-escaping locals (done — claude.md #74)

A local struct/`arr[T]`/`map[T]` declared directly in a function, event
handler, `if` branch, `while` body, or `for` body is freed automatically
as soon as control leaves the block it was declared in, when
`festina/escape_analysis.py` can prove — from the syntax of the whole
enclosing function/handler alone — that its address never left it
(never returned, never passed as a call argument, never stored into a
global or another value, never reassigned). Critically, "as soon as
control leaves the block" is not "when the function eventually
returns": a value declared inside a loop body is freed at the end of
*every* iteration that reaches the end of that body, and `break`/
`continue` leaving a loop early free everything declared since that
loop's own body began before actually transferring control — the
same as reaching the loop body's natural end would. This is what
actually matters for a long-running loop: without it, a loop-local
value would leak once per iteration, unbounded, no matter how the
function-level version of this same idea was scoped. See `claude.md
#74` for the exact rule and its explicitly stated remaining
limitations (below).

Verified three ways, not just reasoned about, at each step of building
this out (first the function-top-level-only version, then widened to
cover nested `if`/`while`/`for` bodies including `break`/`continue`
interaction): exhaustive unit tests of the analysis itself
(`tests/test_escape_analysis.py`, every syntactic escaping/non-escaping
pattern, no C compiler needed — unchanged by the nested-block widening,
since the analysis was always whole-function-scoped from the start),
end-to-end compile-and-run tests
(`tests/test_codegen.py::TestAutomaticMemoryReclamation`, including the
exact "return a struct by value" pattern that broke the earlier naive
stack-allocation attempt below, and the critical "a value declared
outside a loop and merely used inside it survives that loop's own
break/continue" case), and real AddressSanitizer/LeakSanitizer runs
against combined programs exercising every escaping/non-escaping
pattern together, including deeply nested `if`-inside-loop with both
`break` and `continue` firing across many iterations — zero ASAN
errors both times, and LeakSanitizer's reported leaks matched the
hand-derived expected count exactly in every run, including one where
the "extra" leak turned out to be a real, distinct, already-documented
gap (a global repeatedly reassigned to a freshly escaping value orphans
its previous one — a structurally different kind of leak than anything
escape analysis for non-escaping locals could ever address, confirmed
by removing that one line and re-running to zero leaks).

This was deliberately shipped as two separate, individually reviewable
increments — first function-top-level-only, then widened to nested
blocks and per-iteration loop freeing — rather than attempting the
whole thing in one pass, exactly as originally planned.

### Stage 2: interprocedural call-argument analysis (done — claude.md #75)

Stage 1's own stated limitation: a value passed as a call argument was
*always* treated as escaping, even when the called function provably
never retains it. This stage closes that gap for calls to any function
declared in the same program (calls to builtins, and calls through a
field/element access rather than a plain name, are unaffected and stay
exactly as conservative as before).

For each function, in declaration order, `festina/escape_analysis.py`'s
same walker now also determines which of *that function's own
parameters* ever escape within its own body — the identical rule
stage 1 already applies to locals, applied to parameters instead. That
result is recorded (`CodeGen.escaping_params`, a plain `{func_name:
set[int]}` dict) once the function's body is fully analyzed, so any
*later* function's own analysis can use it: a call argument at a
position some earlier function's own analysis already proved safe is
exempted from the default call-argument rule at that one call site —
it may still escape some other way, through some other use, which is
judged entirely independently. This composes transitively for free: if
A calls B, and B passes its own parameter straight through to C, A's
own argument is only as safe as C's own analysis of the position it
ultimately reaches, with no explicit chaining logic needed anywhere —
by the time B's own body is analyzed, C's result already exists to
consult, and by the time A's is, B's does too.

The one subtlety this needed: Festina requires a function be declared
before it's called (semantic.py rejects forward references — see
claude.md #48's "unknown function" error), which means the only way a
function can call itself is directly, by its own name, *before* its
own analysis has completed. This turns out to need no special-casing
at all: a self-recursive call's lookup against `escaping_params` is
just an ordinary dict miss (that function's own entry hasn't been
written yet), which already falls back to the same conservative "any
call argument escapes" default a call to an unanalyzed builtin gets —
correctly safe, if more conservative than a fixpoint analysis over the
recursive call itself could in principle prove. Also crucially: because
forward references are rejected, every *other* possible callee is
guaranteed to already be a fully-resolved key in `escaping_params` by
the time it's consulted — no whole-program pre-pass or graph fixpoint
was needed, just building the table incrementally, one function at a
time, in the same single pass `CodeGen` already emits every function
body in.

Verified the same three ways as stage 1, with the leak-count match
given extra weight here specifically because a bug in this stage's
reasoning is the first one in this whole feature that could plausibly
free a value still in use elsewhere (stage 1's own worst-case bug was
always just "leaks a bit more than provable," never corruption, since
it never widened *what* gets freed based on another function's
behavior) — not something to wave through on "the design sounds
right" alone. A combined program (a 3-function non-retaining chain, a
3-function retaining chain, a self-recursive function passed a struct
parameter, and a two-parameter function where only one position
escapes, all exercised together for hundreds of iterations, wrapped in
its own function so its own locals are subject to analysis rather than
accidentally exercising the separate, pre-existing "`__festina_main`'s
own top-level statements are never analyzed at all" limitation)
produced **zero** AddressSanitizer errors (no use-after-free, no
double-free, no heap-buffer-overflow) and never tripped any of its own
embedded correctness checks (`fail(...)` on any value drifting from
its hand-computed expectation) across hundreds of iterations, run with
leak detection off specifically to get trustworthy full program output
for that check (AddressSanitizer's own leak-report exit path can skip
flushing already-buffered stdout, unrelated to anything this feature
does — verified by re-running with leak detection on for a *separate*
leak-count check, matching the hand-derived expected count of "exactly
the values proven to genuinely escape (an argument passed to a
retaining chain, and the self-recursive one, both by design still
leak, unchanged from before this stage) to within LeakSanitizer's own
known conservative-stack-scanning under-count of a small few" almost
exactly).

### Stage 3: stack allocation and map entry keys (done — claude.md #76)

Two closures of previously-named gaps, shipped together since neither
widens what stages 1/2 prove safe — both only change what happens once
something already is proven safe, or close an incomplete
implementation of a promise stage 1 already made.

**Stack allocation.** A struct local proven safe by stage 1 or 2 is now
a real stack `alloca` (explicitly zeroed with `store %struct.T
zeroinitializer`, matching `calloc`'s own zero-init behavior — the two
allocation strategies have to stay indistinguishable to any Festina
program, not just individually correct) instead of a `calloc`+`free`
pair. This is sound for exactly the reason freeing early was sound: an
address that never escapes needs nothing beyond its own function's
stack frame, and a stack alloca's own lifetime already matches exactly
the points the old `free()` call would have fired at (block exit,
every loop iteration, `break`/`continue`) — LLVM's `alloca` reserves
one fixed address for the whole enclosing function regardless of which
basic block contains it, so a loop-body-declared one is simply reused,
address and all, on the next iteration, and a recursive call still
gets its own genuinely distinct address (ordinary calling-convention
behavior, unrelated to this choice — verified directly with a
hand-traced recursive-accumulator test, not just reasoned about: see
`tests/test_codegen.py`'s
`test_recursive_function_with_a_non_escaping_struct_local_keeps_each_calls_own_value`).
`arr[T]`/`map[T]` locals are deliberately unaffected — their data/
entries buffer can grow after declaration (`.push()`, a map literal
past its initial size), so its size isn't known at declaration time,
and a stack allocation needs a size known in advance; these still
`calloc`+free their buffer exactly as stage 1 shipped.

**Map entry keys.** `festina_map_free_entries` (new, in
`runtime/festina_runtime.c`) frees each entry's own `strdup`'d key
before freeing the entries buffer itself, closing stage 1's own
originally-stated "a freed map's own per-entry keys" gap. This needed
no new soundness reasoning at all, unlike the struct/array/map-*value*
cases below: a map entry's key was never a Festina-visible value to
begin with, just a private byte-for-byte copy this runtime made for
its own bookkeeping the instant the entry was created (see
`festina_map_set`'s own comment) — nothing else could ever be aliased
to it.

Verified the same way as every stage before it: new unit/IR/
compile-and-run tests (`tests/test_codegen.py::TestAutomaticMemoryReclamation`
grew from 47 to 51, several existing IR-shape assertions rewritten from
"a `free()` call appears" to "an `alloca`+`zeroinitializer` pair
appears, no `calloc`/`free` at all" — the correctness-only
compile-and-run tests needed **zero** changes, confirming the swap is
exactly as semantically transparent as intended), and real
AddressSanitizer/LeakSanitizer runs across four different combined
programs: a recursion-focused stress test (deep and wide recursive
struct locals, loop-local structs reused thousands of times, nested-if
struct locals) and a map-key-focused one (thousands of map creations
with several keys each, some escaping loop iterations via `break`/
`continue`) written fresh for this stage, plus re-running both
combined programs from stages 1/2's own verification unchanged, to
confirm the swap didn't regress anything already proven — zero ASan
errors and zero leaks across all four (the two chains/loop-heavy
programs from stages 1/2 had already-understood, unrelated leaks from
their own intentionally-escaping values; those counts stayed the same
give or take LeakSanitizer's own already-documented conservative
under-count, confirming the escaping/leaking *set* is unchanged by
this stage, only the mechanism for the non-escaping set is).

### What's still ahead

- **Nested struct/array/map fields within a freed struct.** Explored
  as part of stage 3 above and *deliberately not attempted* after
  finding a real soundness hazard, not merely left alone by inertia:
  the only way to populate a struct-typed field is `outer.field =
  someExistingLocal` (there's no struct-literal initializer syntax),
  which stores that local's own pointer into the field — an alias, not
  a copy (confirmed directly by inspecting the generated IR: `store
  ptr %t11, ptr %t10`, the same pointer value, not a fresh allocation).
  `someExistingLocal` is already correctly marked escaping by stages
  1/2's own existing rule (an assignment *value* always escapes), so it
  was never going to be double-freed under its own name — but freeing
  `outer.field`'s value when `outer` itself goes out of scope would
  free that *same* memory through a different name, and if
  `someExistingLocal` is read again anywhere after that point (still
  entirely legal Festina code — it's a live variable in its own scope
  until *it* goes out of scope), that read would be a genuine
  use-after-free. Confirmed concretely, not just reasoned about: wrote
  exactly that program, inspected its IR, and traced the aliasing by
  hand before concluding this needs real aliasing/ownership analysis
  (does anything else still reference this field's value when the
  struct holding it stops existing) rather than the syntactic "does
  this name appear outside a safe position" question stages 1/2 both
  answer. That's a structurally different, harder problem — arguably
  the same problem as reference counting below, just asked from the
  freeing side instead of the retaining side — not a small extension
  of the existing rule, and not attempted here for exactly that reason.
  Needs its own design pass, same bar as every stage here.
- **Reference counting** (or a real tracing GC) for the values escape
  analysis can't (or structurally never could) clear — genuinely shared
  values, or ones stored somewhere long-lived on purpose (globals,
  caches). This is the complete answer for the remainder escape
  analysis (stages 1-3) can never reach on its own (a value that
  provably *does* escape has nothing for escape analysis to do), but
  touches nearly
  every place a value is assigned, passed, stored, or a scope exits — a
  large surface area to get right, and an incorrect refcount (an early
  free, or a missed increment) *is* a real memory-safety bug, a genuine
  regression from "leaks but is memory-safe," not a wash. Cycles are
  also a real question to resolve one way or another (rare in Festina's
  language model, given no closures/first-class functions, but not
  provably impossible without checking). A real design for this would
  also need to settle the nested-field aliasing question just above,
  since a struct field holding a refcounted value is exactly the kind
  of "does something else still reference this" question refcounting
  exists to answer generally — the two bullets here are likely one
  design effort, not two. Needs its own `claude.md` addition and
  dedicated design pass before implementation, same as every stage
  here — not attempted yet, not overlooked.

## Smaller, not yet tracked elsewhere

Not roadmap items in the same sense as the three above — known gaps
called out in [`tests/CONTRACT.md`](tests/CONTRACT.md) and
[api.md](api.md) that stay deliberately unresolved per `claude.md #54`'s
ambiguity rule (unspecified stays unresolved rather than invented),
listed here only so they aren't lost:

- No garbage collection / automatic memory management for arrays and
  structs (`claude.md #43` promises this; not implemented -- see
  "Memory management" below, a deliberately separate, larger writeup
  rather than a one-line bullet here).
- `regex(pattern, flags)` -- the dynamic builtin call, not a
  `/pattern/flags` literal (those are now cached, compiled once per
  source location on first reach -- see tests/CONTRACT.md) -- still
  recompiles its pattern on every call. Inherent to it: pattern is a
  general runtime expression, so the same call site can legitimately see
  a different pattern on different calls (e.g. `regex(userPattern)`
  inside a loop), and caching by call site the way the literal case does
  would be a correctness bug, not a caching gap to close.
