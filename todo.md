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
double-free, since nothing was ever freed). Four stages below have
since narrowed that: a provably-safe struct is now stack-allocated
outright (stage 3), a provably-safe `arr[T]`/`map[T]`'s data is still
heap-allocated but now freed (stages 1/2), and a struct-typed global
(always) or escaping local (stage 4) is reference counted and freed
once nothing references it anymore, including a value handed back
through `return` itself, or discarded outright at its own call site —
anything not covered by any stage is still heap-allocated (structs) or
still leaks (arrays/maps, and a struct's own nested struct/array/
map-typed fields — see below), exactly as originally described here.

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

### Stage 4: reference counting for escaping struct globals and locals (done — claude.md #77)

The remainder stages 1-3 can never reach on their own: a value proven
to genuinely escape has nothing for pure escape analysis to do, since
something else might still need it when its own declaring scope ends.
This stage tracks how many bindings reference a struct value and frees
it only once that count reaches zero — reference counting, not a
tracing GC, and *complete* for Festina specifically, not the usual
"handles everything but cycles" partial answer: a struct field's type
must always be declared *before* the struct containing it (the same
rule that already governs function forward references), so no struct
can ever reference itself, directly or through any chain of fields —
verified directly, not assumed, by writing `struct Node { next:Node }`
and confirming it fails to compile, then confirming the same for two
structs referencing each other in either declaration order. Reference
cycles are not just rare in Festina — they are structurally impossible.

**Scope, deliberately narrower than the full design originally
sketched (at first — widened in the same stage shortly after, see
below):** a struct-typed *global*'s value is fully reference counted
— every reassignment (including its own declaration, if that
declaration has an initializer) retains the new value and releases the
old one, freeing it if nothing else references it anymore; the very
first assignment is never a special case, since a global's own
untouched initial value carries a sentinel refcount both `festina_retain`/
`festina_release` treat as an unconditional no-op (it was never
heap-allocated, so it must never reach `free()`). A struct-typed
*local* was, in this stage's first pass, only released at its own
scope-exit (the same points stages 1-3 already track) when it was
declared **without** an initializer, was **never** itself returned, and
was **never** itself the target of a plain reassignment anywhere in its
own function — a local failing any of these three conditions leaked
exactly as it did before this stage. This was not the complete answer
stages 1-3 themselves eventually reached for non-escaping locals
(interprocedural analysis, every nesting shape) — it was the narrowest
slice that was actually sound at the time, because retaining on every
local assignment (needed to widen this safely) wasn't implemented yet.
That narrowing was closed shortly after, in the same stage — see
"Widening this stage's own local scope" below.

**Two real bugs found and fixed while building this, both worth
recording in full rather than glossed over as routine debugging:**

1. A *pre-existing* bug, not introduced by this stage: `Point r =
   someFunc()` for a **local** `r` silently discarded the call's
   return value and left `r` at its stack-allocated zero value instead
   — `_emit_stmt`'s own struct VarDecl handling never looked at
   `stmt.init` at all (a top-level/global `Point r = someFunc()`
   already worked, but through a completely different code path,
   `_emit_toplevel_stmt`, untouched by the bug). Found by writing
   exactly this pattern as part of this stage's own verification
   program and noticing a `fail()` fire that shouldn't have; traced to
   the generated IR (`alloca %struct.Point` + `store ...
   zeroinitializer`, the call's own return value never referenced
   again) before fixing it, not patched blind from the symptom. Fixed
   by making a struct VarDecl-with-initializer simply alias whatever
   its initializer evaluates to, the same as an ordinary `r = expr`
   assignment would, with no allocation of its own.
2. A bug this stage's *own first pass* introduced: `Point q; q = p;`
   (`q` reassigned, after its own declaration, to alias `p`'s storage)
   with both `p` and `q` later assigned to two *different* globals
   meant both were scheduled for release at their own, independent
   scope-exits — with nothing retaining the extra reference `q = p`
   created, the second release decremented a refcount the first
   release had already brought to its true value, and one more
   reassignment later, actually freed memory a still-live global still
   pointed to. Found the same way: written as part of the verification
   program, traced by hand (a debug build of the runtime printing every
   retain/release call) to confirm the exact mechanism before fixing
   it, not just accepting "the test now passes." Fixed by excluding
   any struct local that's ever the target of a plain reassignment
   (`escape_analysis.find_reassigned_names`, new) from scope-exit
   release scheduling entirely — the third of the three conditions
   above. (This exclusion-based fix was itself superseded, later in
   this same stage, by retaining on every local reassignment instead of
   sidestepping the ones that needed it — see "Widening this stage's
   own local scope" below; `find_reassigned_names` was removed once
   nothing depended on it anymore.)

**A significant methodology finding, independent of either bug above,
surfaced while chasing the second one:** `clang -fsanitize=address -c
file.ll` does **not** instrument raw LLVM IR text the way it
instruments C source. ASan's per-function opt-in (the
`sanitize_address` attribute) is normally added by clang's own C
frontend during compilation, which is bypassed entirely when the input
is already `.ll` — verified directly by compiling a hand-written
`.ll` file containing an unambiguous calloc+free+read-after-free
through this exact pipeline and finding it produced zero
`sanitize_address`/`__asan_report*` symbols and did not catch the bug,
while the byte-for-byte-equivalent pattern written as `.c` source
produced 28 such symbols and caught it immediately. This means every
prior AddressSanitizer verification claim in this document and
`security.md`/`tests/CONTRACT.md`, for stages 1 through 3 and
interprocedural analysis, checked LeakSanitizer's allocation-tracking
correctly (that tracks the allocator's own calls directly, unaffected
by instrumentation) but never actually exercised ASan's
corruption-detection (heap-buffer-overflow, use-after-free,
double-free) against the *generated program's own* memory accesses —
only against the hand-written runtime `.c` file linked alongside it.
The fix is straightforward once identified: add the `sanitize_address`
attribute to every `define` in the generated `.ll` file before
compiling. Re-ran the earlier stages' own combined verification
programs (stage 1/2's nested-block and interprocedural-analysis
stress tests, stage 3's recursion and map-key stress tests) through
the corrected pipeline specifically to check for anything the flawed
methodology might have missed silently — all came back clean, zero
ASan errors, with this stage's own two bugs (both found via the exact
same corrected pipeline) being the only real findings from this whole
retrospective check. Their own underlying *design* reasoning holds up;
only this stage's own new retain/release logic was ever actually
unverified for corruption, and both bugs it had are now fixed and
covered by regression tests. See tests/CONTRACT.md for the full
methodology writeup and reproduction.

Verified the same way as every stage before it, this time with the
corrected instrumentation from the start once it was found: new unit
tests (`tests/test_escape_analysis.py::TestFindReturnedNames` plus a
`find_reassigned_names` walker, mirroring `find_returned_names`'s own
shape), new IR-level and compile-and-run tests in
`tests/test_codegen.py::TestAutomaticMemoryReclamation` (51 → 67,
including a dedicated regression test for each of the two bugs above),
and real AddressSanitizer/LeakSanitizer runs (properly instrumented)
against a combined program exercising global reassignment in a loop
(the exact scenario originally found and documented as a leak, back
when stage 1 first shipped — now correctly freed, not leaked), a
sometimes-returned local (correctly still leaks, by this stage's own
stated scope), a reassigned local aliasing another tracked local
(correctly no longer double-released), self-assignment, and two
independently-tracked globals, all run for hundreds of iterations —
zero ASan errors, and the only leak reported matches exactly the one
value this stage's own documented scope says should still leak (a
returned local), not a byte more.

### Widening this stage's own local scope (done — claude.md #77, same stage)

The narrow scope above excluded a struct local from scope-exit release
tracking the moment it was declared with an initializer or was ever
the target of a plain reassignment, because retaining a value only
when something *else* might already reference it wasn't implemented
for locals yet — only for globals. This widening implements exactly
that: a new `CodeGen._is_owning_struct_source(expr)` classifies a
source expression as "owning" (a fresh, uniquely-owned value, no
retain needed — its own +1 transfers cleanly into the new binding)
only when it's a plain `ast.Call`; every other shape (reading an
existing identifier, a struct field, a ternary, ...) is conservatively
treated as "aliasing" — retain needed, since something else might
already reference the same value. This mirrors the project's
established "when unprovable, be conservative in the safe direction"
rule the same way every prior stage has. Two new codegen methods apply
this: `_emit_local_struct_retain_release` (the local-variable
counterpart to the existing `_emit_global_struct_retain_release`,
called from `_emit_assign`'s Identifier branch for a plain local
reassignment) and an equivalent inline check in `_emit_stmt`'s
VarDecl-with-initializer handling. With retaining now correct for both
cases, `_emit_block`'s own scope-exit scheduling no longer needs to
exclude them: a with-init local is now always eligible for release
tracking (it never stack-allocates — see `claude.md #76` — so it's
never wrong to schedule it), and a no-init local is eligible exactly
when escape analysis already says it escapes, same as before. The
now-unnecessary exclusion machinery (`escape_analysis.find_reassigned_names`
and `CodeGen._reassigned_names`) was removed entirely rather than left
dead. The one condition that still excludes a local from this
section's coverage — being returned anywhere in the function — is
untouched by this widening: `Return` still doesn't retain, so a
locally-declared struct that's ever returned still leaks, exactly as
stage 4's first pass already documented.

Unlike stage 4's first pass, this widening found no new bugs during
verification — attributed to reusing the already-fixed, already-ASan-
verified `_emit_global_struct_retain_release` pattern as a template,
applying the owning/aliasing classification conservatively from the
start, and using the corrected (`sanitize_address`-attributed) ASan
methodology from the very first test rather than discovering the
instrumentation gap mid-verification as stage 4's first pass did.
Verified the same three ways as every stage before it: new IR-level
tests asserting retain is present for an aliasing source and absent
for an owning one, in both the VarDecl-with-initializer and plain-
reassignment positions; new compile-and-run tests for a with-init
local that never further escapes, a reassignment chain through two
globals, a struct-field read used as a reassignment source, and
self-reassignment, plus a combined stress test (3000 iterations,
`tests/test_codegen.py::TestAutomaticMemoryReclamation`, 67 → 76); and
real, properly-instrumented AddressSanitizer/LeakSanitizer runs against
a fresh combined stress program (`makeAndDiscard`, a reassignment
chain into two globals, reassignment from a call result, multiple
reassignments of one local, a loop-local reassigned every iteration
with `break`/`continue` interaction, and self-reassignment stress, 500
iterations) — zero ASan errors and zero leaks — plus a second, narrowly
targeted program confirming a *discarded* return value (`make(n)` used
as a bare statement, never bound to any variable) still leaks exactly
as before, since it never reaches any retain/release-aware codepath;
that gap is unrelated to this widening and remains open, tracked below.
Every earlier stage's own combined stress-test program
(`chaos2.f`, `interproc2.f`, `stackalloc1.f`, `mapkeys1.f`,
`recur_stack.f`, `rc_loop.f`, plus the debug-build traces from stage
4's own bug hunts) was re-run through the same corrected pipeline to
confirm this widening doesn't regress anything already proven — all
came back clean.

One notable, unplanned side effect worth recording precisely rather
than overclaiming or underclaiming: the combined verification program
from stage 4's first pass (`rc_combined.f`) went from 9999 leaked
objects to **zero**, because the caller's own `Point r =
sometimesReturned(...)` — a with-init local — is now correctly tracked
and released once its own scope ends. This closes much of the
"returned value leaks" gap for the common case where a caller captures
a call's result in a local, but *not* because `Return`'s own
retain/release logic was touched — it wasn't. A return value that's
discarded outright, never bound to anything, still leaks unconditionally
(confirmed by the dedicated `discard_check.f` program above). Retaining
every value a function returns, closing that last gap, is still open —
see "what's still ahead" below.

### Retaining a function's own return value (done — claude.md #77, same stage)

The one condition still excluding a struct local from this stage's
scope-exit release tracking after the widening above: `Return` itself
never retained the value it handed back to its caller, so a local
that's ever returned still leaked, regardless of the caller-side
widening. This closes that gap by applying the identical owning/
aliasing treatment `_emit_local_struct_retain_release` already gives a
plain assignment to `Return`'s own value: retain it first whenever its
source isn't a plain function call, then let `_emit_free_active_locals`
release every active local exactly as it already does on every other
function exit — no separate exclusion needed anymore for a name that's
ever returned, since retain-then-release-everything nets out to
exactly one surviving reference on whichever path actually executes.

This is a genuine correctness fix, not just a leak-closing one: a
struct-typed *parameter* returned directly (`Point func identity(p:
Point) { return p }`) is aliasing the *caller's* own storage, not a
fresh value — without a retain there, the caller's own local would be
left as the sole holder of a reference count that never accounted for
the second binding (the call's own return value) now also pointing at
it, so the caller's own local going out of scope first would free
memory a still-live return-value binding pointed to, a genuine
use-after-free once read. Traced through by hand before implementing,
not just reasoned about afterward: `identity(x)`'s own caller keeps
reading both `x` and the value it got back, well past where `x`'s own
release would ordinarily have fired, specifically to catch this.

With the name-based exclusion gone, `escape_analysis.find_returned_names`
and its own walker helpers, and `CodeGen._returned_names`, were removed
entirely — nothing needs the "is this name ever a bare Return value"
question anymore, since the new logic is keyed off the Return
statement's own source expression, not a whole-function, name-based
approximation of it. This is also strictly more precise than the old
name-based exclusion ever was: the old approach only ever recognized a
*bare* Return value (`return p`), so `return p.x` (not itself a
soundness issue, `p.x` isn't a tracked binding) and `return cond ? a :
b` (a genuine gap — neither `a` nor `b` is a bare Identifier, so
neither was ever excluded, meaning whichever branch actually ran could
have been released out from under the caller on the way out, before
this fix) were invisible to it. The Ternary case in particular was a
latent soundness gap the old exclusion-based design could never have
closed without abandoning the name-based approach entirely — retain-
on-Return closes it as a natural consequence of no longer needing to
ask "which *name* is this" at all.

Verified the same three ways as every increment in this stage: new
IR-level tests (retain present for a bare-identifier Return, absent for
a call-result Return), and new compile-and-run tests for the parameter-
aliasing soundness case above, a Ternary return between two locals
(confirming the untaken branch is freed and the taken one survives
correctly), and a combined stress program folding all of this
together with the earlier local-scope widening (2000 iterations,
`make`/`identity`/`pick`/`sometimesReturned`/`chainReturn` all
exercised together, `tests/test_codegen.py::TestAutomaticMemoryReclamation`,
76 → 80). Real, properly-instrumented AddressSanitizer/LeakSanitizer
runs against that same combined program came back with zero ASan
errors and a leak count of exactly 2000 objects — matching the 2000
deliberately-discarded `make(i)` calls in the same program one-for-one,
confirming every OTHER case (captured-by-a-local, parameter-passthrough,
ternary, sometimes-returned-and-also-global) is now correctly freed and
nothing else is. Every earlier verification program from this stage
(`widen1.f`, `discard_check.f`, `field_source.f`, plus stages 1-3's own
`chaos2.f`/`interproc2.f`/`stackalloc1.f`/`mapkeys1.f`/`recur_stack.f`,
and stage 4's own `rc_loop.f`/`rc_debug3.f`/`rc_debug4.f`/`rc_combined.f`)
was re-run through the same corrected pipeline — all came back clean,
confirming no regression.

### Releasing a discarded return value (done — claude.md #77, same stage)

The last struct-return leak left standing after the retain-on-Return
fix above: a call result never bound to anything at all (`make(n)`
used as a bare statement, not `Point r = make(n)`). Nothing in this
stage's tracking would otherwise ever reach this value — it's not a
local (no `VarDecl` binds it), not a global (no assignment references
it), and `Return`'s own retain only protects a value being handed
*back* to a caller, not one a caller is about to throw away entirely.

Closed with no new analysis needed, just one more application of a
fact this stage already relies on everywhere else: a function call's
own return value is always "owning" — freshly produced, nothing else
referencing it yet, the instant it comes back. `_emit_stmt`'s
`ast.ExprStmt` handling now checks whether the statement's own
expression is a bare `ast.Call` whose return type is a struct, and if
so, releases the value immediately, right after evaluating it. This is
provably correct, not merely conservative, in a way most of this
stage's other decisions aren't: since the value is "owning" and this
`ExprStmt` is the only place it's ever referenced, this call site is
*by construction* that value's sole reference — no other binding could
possibly also hold it, so releasing it here can never free something
still needed elsewhere the way an imprecise heuristic might. Discarding
the value doesn't skip the call itself, of course — any side effects
inside the called function (writing a global, incrementing a counter,
...) still run exactly as before; only the struct value the call
happens to return is released once its own statement has finished with
it.

Verified the same three ways as every increment in this stage: a new
IR-level test confirming the release call appears right after a
discarded struct-returning call, a negative IR-level test confirming a
discarded *void* call emits no extra release (this only ever fires for
a `StructType` result), a compile-and-run test confirming the call's
own side effect (a global counter increment) still happens exactly
`iterations` times even though its return value is thrown away every
time, and real AddressSanitizer/LeakSanitizer runs: the exact
`discard_check.f` program that previously leaked 2000 objects (one per
discarded call, confirmed and documented when the local-scope widening
above first shipped) now reports **zero** leaks, and the combined
`return_widen1.f` program from the retain-on-Return fix above — which
previously leaked exactly 2000 objects, one per its own deliberately-
discarded `make(i)` call — is now also fully leak-free. Every earlier
verification program across all of stage 4 (`widen1.f`, `field_source.f`,
stages 1-3's own `chaos2.f`/`interproc2.f`/`stackalloc1.f`/`mapkeys1.f`/
`recur_stack.f`, and stage 4's own `rc_loop.f`/`rc_debug3.f`/
`rc_debug4.f`/`rc_combined.f`) was re-run through the same corrected
pipeline to confirm no regression — all came back clean.

With this, every struct value stage 4 set out to cover (globals, and
every shape a local or a call's own return value can take) is now
fully, correctly reference counted — the only remaining struct-related
gaps are the ones sections 74-77 never claimed to cover in the first
place: a struct's own nested struct/array/map-typed fields, and
`arr[T]`/`map[T]` values that themselves escape (see below).

### What's still ahead

- **Nested struct/array/map fields within a freed struct.** Explored
  as part of stage 3 and *deliberately not attempted* after finding a
  real soundness hazard, not merely left alone by inertia: the only way
  to populate a struct-typed field is `outer.field = someExistingLocal`
  (there's no struct-literal initializer syntax), which stores that
  local's own pointer into the field — an alias, not a copy (confirmed
  directly by inspecting the generated IR: `store ptr %t11, ptr %t10`,
  the same pointer value, not a fresh allocation). `someExistingLocal`
  is already correctly marked escaping by stages 1/2's own existing
  rule (an assignment *value* always escapes), so it was never going to
  be double-freed under its own name — but freeing `outer.field`'s
  value when `outer` itself goes out of scope would free that *same*
  memory through a different name, and if `someExistingLocal` is read
  again anywhere after that point (still entirely legal Festina code —
  it's a live variable in its own scope until *it* goes out of scope),
  that read would be a genuine use-after-free. This is a structurally
  different, harder problem than stages 1/2's own syntactic "does this
  name appear outside a safe position" question — real aliasing/
  ownership analysis (does anything else still reference this field's
  value when the struct holding it stops existing). Retaining on every
  local assignment, and on every function's own return value (both done
  now, see above), turned out not to answer this one: both widenings
  only ever retain a *binding* (a local variable's own slot, or the
  value handed back through `return`) when it starts referencing a
  value — neither touches a struct's own *field*, which is a different
  storage location with no binding of its own to retain into. Still
  needs its own design pass, same bar as every stage here.
- **Reference counting for `arr[T]`/`map[T]` values that escape** —
  stage 4 covers structs only. An escaping array/map still leaks
  exactly as before this stage; extending reference counting to
  arrays/maps needs their own allocation sites (literal construction,
  `festina_map_set`'s own realloc) to also carry the same header, a
  larger surface than the struct-only allocation sites stage 4 touched.
- **A real tracing GC** was never seriously considered as an
  alternative to reference counting here, given section 77's own
  finding that reference cycles are structurally impossible in
  Festina's current type system (no self-referential or
  forward-referencing struct/array/map element types) — a tracing
  collector's main advantage over refcounting is handling cycles
  refcounting can't, which isn't a problem this language can currently
  produce. Worth revisiting only if a future language change
  (closures, first-class functions, or forward-referencing types)
  reintroduces the possibility.

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
