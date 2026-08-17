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
double-free, since nothing was ever freed). Seven stages below have
since narrowed that: a provably-safe struct is now stack-allocated
outright (stage 3), a provably-safe `arr[T]`/`map[T]`'s data is still
heap-allocated but now freed (stages 1/2), a struct-typed global
(always) or escaping local (stage 4) is reference counted and freed
once nothing references it anymore, including a value handed back
through `return` itself, or discarded outright at its own call site,
a struct's own struct-typed fields are reference counted the same way
(stage 5), an escaping `arr[T]`/`map[T]` value is now reference
counted too, the identical treatment, plus a real pre-existing memory-
safety bug fixed as a side effect (stage 6), and an `arr[T]`/`map[T]`'s
own individual elements/values are now reference counted too, once
their own type is itself refcounted, closing the last remaining
memory-safety gap this whole effort had found and precisely
characterized along the way (stage 7) — every gap this section
originally described is now closed.

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

> **No longer true, as of claude.md #106.** `struct Node { next:Node }`
> and forward-referencing structs both compile now — the restriction
> this paragraph leaned on was an ordering accident in
> `analyze_struct`, not a property of the type system, and it was
> removed deliberately so linked lists and trees could be written. The
> refcounting described above is unchanged and still correct for
> acyclic data; what changed is that a cycle is now constructible, and
> a cycle still leaks. See the "What's still ahead" note below.

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
place: a struct's own struct-typed fields (closed next, see "Stage 5"
below), and `arr[T]`/`map[T]` values that themselves escape (see "What's
still ahead").

### Stage 5: reference counting for a struct's own struct-typed fields (done — claude.md #78)

Explored as part of stage 3 and *deliberately not attempted* at the
time, after finding a real soundness hazard, not merely left alone by
inertia: the only way to populate a struct-typed field is `outer.field
= someExistingLocal` (there's no struct-literal initializer syntax),
which stores that local's own pointer into the field — an alias, not a
copy (confirmed directly by inspecting the generated IR: `store ptr
%t11, ptr %t10`, the same pointer value, not a fresh allocation).
`someExistingLocal` was already correctly marked escaping by stages
1/2's own existing rule (an assignment *value* always escapes), so it
was never at risk of being double-freed under its own name — but
without this stage, nothing ever retained the field's own reference to
it either, which cuts both ways: `someExistingLocal`'s own ordinary
scope-exit release could free memory `outer.field` still pointed to (a
genuine use-after-free the moment it was read again — still entirely
legal Festina code, a live variable in its own scope until *it* goes
out of scope), and a struct freed by stage 4 never released whatever
its own struct-typed fields still pointed to (a leak, since nothing
else was ever going to). Retaining on every local assignment, and on
every function's own return value (stage 4's own widenings), never
answered this: both only ever retain a *binding* (a local variable's
own slot, or the value handed back through `return`) when it starts
referencing a value — neither touches a struct's own *field*, a
different storage location with no binding of its own to retain into.
This stage is that missing piece.

**Design, in two parts, matching claude.md #78's own two paragraphs:**

1. *Retain on field write.* Every `outer.field = value` assignment,
   where `field` is struct-typed, now retains the new value first
   (skipped only when `value`'s own source is a plain function call --
   the identical `_is_owning_struct_source` check stage 4's own local-
   scope widening already uses) and releases whatever the field
   previously held -- always safe, since a struct's own fields start
   out null (its zero-initialized storage, never populated any other
   way) and both `festina_retain`/`festina_release` already null-check.
   Implemented in `_emit_assign`'s Member-target branch, gated on
   `not expr.target.computed` so it never fires for `arr[i] = v` /
   `map[key] = v` even when the array/map's own element type happens to
   be a struct -- `arr[T]`/`map[T]` values aren't refcounted containers
   at all yet (a separate, still-open item, see "What's still ahead"),
   so there would be no scope-exit release site to pair a retain there
   with.
2. *Cascade on release.* When a struct value is released and its
   refcount reaches zero, each of its own struct-typed fields is now
   released too, *before* its own storage is freed -- recursively, so a
   field's own struct-typed field is released the same way. Since the
   C runtime is entirely type-blind (every value it ever touches is
   just a `void *`), this needed a real per-struct-type function
   generated at compile time, not a small addition to the existing
   generic `festina_release`: a new `CodeGen._release_fn_for_struct(type_)`
   returns the plain, unchanged `@festina_release` for a struct with no
   struct-typed field of its own (the overwhelming majority -- no new
   function, no extra indirection, exactly as cheap as before this
   stage), or a lazily-generated, cached
   `@__festina_release_struct_<Name>` wrapper for one that has at least
   one. That wrapper decrements the refcount via a new runtime function,
   `festina_release_check` (the same decrement-and-check `festina_release`
   itself now delegates to, split out specifically so codegen can
   interpose the field-cascade between the decrement and the actual
   `free()` call), and only if it just reached zero, releases each
   struct-typed field (via *that* field's own release function, found
   by calling `_release_fn_for_struct` again -- recursion handles
   arbitrary nesting depth for free) before freeing its own storage.
   This recursion always terminates and can never produce a cycle of
   wrapper functions, for the identical reason claude.md #77 already
   gives for why reference cycles are structurally impossible in
   Festina: a struct field's type must always be declared *before* the
   struct containing it, so the graph of "which struct types reference
   which others through their own fields" is a DAG by construction.
   (claude.md #106 removed that declaration-order rule, so the type
   graph is no longer a DAG. `_release_fn_for_struct` memoizes each
   struct type's wrapper before recursing into its fields, so a
   self-referential type generates one wrapper and terminates; what
   #106 genuinely broke is the *runtime* cycle case, where the release
   counts never reach zero. See below.)
   Every existing release call site (`_emit_free_active_locals`,
   `_emit_global_struct_retain_release`, `_emit_local_struct_retain_release`,
   `return`'s own release-on-exit, the discarded-call-result release)
   now goes through this dispatch instead of calling the plain
   `@festina_release` directly.

**A real bug found and fixed while verifying this, worth recording in
full:** a struct local proven safe by stages 1/3 to live on the *stack*
(never heap-allocated, never itself refcounted at all) can still have a
struct-typed field written into it -- and part 1 above retains that
field's own reference regardless of whether the *container* is stack-
or heap-allocated, since the field write itself doesn't know or care.
For a heap-allocated container, that retained reference gets released
when the container itself is (part 2, above). For a *stack*-allocated
container, nothing was releasing it at all -- the container's own
storage is simply reused/discarded at scope-exit, with no release call
of any kind, heap or otherwise. This produced a genuine new leak
(confirmed via a real AddressSanitizer/LeakSanitizer run: three
different functions in a combined stress program each leaked exactly
2000 objects, one per call, matching a stack-allocated outer struct
with a written-but-never-released field one-for-one) -- not a
correctness/corruption bug (the extra, never-released reference only
ever holds a value's refcount *too high*, so it can only delay a free
indefinitely, never trigger one too early), but still a real regression
from this stage's own stated goal. Fixed the same way an `arr[T]`/
`map[T]` local's own data/entries buffer is already handled despite its
header being stack-allocated: a new `_StackStructFieldsOnly` marker
(wrapping the struct's type) tells `_emit_free_active_locals` to
release *only* the struct's own struct-typed field references at
scope-exit, never the struct's own (nonexistent, since stack-allocated)
refcount header -- `_emit_block` now schedules a stack-allocated struct
local for this treatment whenever `_struct_has_own_struct_field` says
its own type has at least one struct-typed field, mirroring exactly how
an array/map local is scheduled today. Found and fixed *before*
shipping, as part of this stage's own verification, not after.

Verified the same three ways as every stage before it: new IR-level
tests (retain present for an aliasing field-write source, absent for an
owning one; a struct with no struct-typed fields keeps using the plain
generic release with no wrapper generated at all; a struct with a
nested field gets a dedicated wrapper that itself calls the generic
release for its own field; three levels of nesting (A → B → C) produce
two wrapper functions, not three, since C has no struct-typed field of
its own), new compile-and-run tests (nested field reads/writes,
reassigning a field releases the old value, self-assignment of a field
doesn't crash, freeing an outer struct correctly reaches its nested
field) --
`tests/test_codegen.py::TestAutomaticMemoryReclamation` grew from 83 to
93 -- and real, properly-instrumented AddressSanitizer/LeakSanitizer
runs against a combined stress program (the exact aliasing hazard this
stage exists to close: writing a local into a field, then reading both
the local and the field well past where the local's own scope-exit
release would have fired under the old code; deep three-level nesting
escaping through a global; field reassignment; self-assignment of a
field; two independently-tracked globals each holding their own nested
structure; 2000 iterations) -- zero ASan errors, and (after the
stack-allocated-container fix above) zero leaks. Every earlier stage's
own combined verification program was re-run through the same corrected
pipeline to confirm no regression -- all came back clean.

### Stage 6: reference counting for escaping `arr[T]`/`map[T]` values (done — claude.md #79)

The last of the three remaining struct-related gaps closed, this stage
does for arrays/maps what stage 4 did for structs — but needed a real
representation change first, not just a new tracking rule, because
`arr[T]`/`map[T]` never had a struct's own single-pointer identity to
begin with.

**The problem the old representation had, found while designing this
stage, not assumed:** an `arr[T]`/`map[T]` value used to *be* the
`{length, data}` (or `{count, entries}`) pair itself — `_llvm_type`
returned `FESTINA_ARRAY_LLVM_TYPE`/`FESTINA_MAP_LLVM_TYPE` directly, a
plain two-word aggregate *value*, copied by value on every assignment.
Two bindings made to alias each other (`map[int] b = a`) each got
their *own* independent copy of that pair, sharing the same
data/entries pointer only until one of them changed. This was merely
imprecise for `arr[T]` — arrays never grow after construction (no
`.push`, fixed size from their own literal), so two aliased array
copies could never actually diverge. It was a **real, exploitable
memory-safety bug** for `map[T]` specifically, confirmed directly, not
assumed: `map[T]` *does* grow (`npcHealths[key] = v` can add a new
key, reallocating the entries buffer via `festina_map_set`), and that
realloc only ever wrote the new `count`/`entries` back into the
*mutating* binding's own copy — every *other* binding that had ever
been made to alias it kept its own, now-stale copy of `entries`,
pointing at memory `realloc` may have already moved or freed.

```festina
map[int] a = {'x': 1}
map[int] b = a
b['y'] = 2      // grows b's own entries buffer via realloc
log(a['y'])     // reads through a's now-stale, possibly-freed pointer
```

Reproduced directly: this exact program **segfaults** on `main`,
unrelated to this stage's own work in any way (never touches
`_emit_map_set`, `_try_addressable`, or either type's own assignment
path) — a pre-existing bug that had simply never been exercised by any
existing test, since nothing in the suite grew a map through one alias
and read it back through another.

**The fix, and why it closes both problems at once:** `arr[T]`/
`map[T]` is now a single `ptr` to its own heap-allocated storage —
`_llvm_type(ArrayType)`/`_llvm_type(MapType)` both return `"ptr"` now,
the identical representation a struct value already has, with the
identical `{i64 refcount, payload...}` header layout
(`_emit_fresh_heap_header`, shared with an escaping struct local's own
allocation). Two bindings made to alias each other now share the
*exact same* header, not independent copies that merely started out
agreeing — `festina_map_set`'s own realloc updates the one canonical
header everyone points at, so a growth through either binding is
correctly, immediately visible through the other, every time.
Verified directly: the exact reproduction above now prints `2`, not a
segfault. This is a genuine consequence of the representation change
this stage needed anyway for refcounting to make sense at all, not a
separately-motivated bug fix bolted on alongside it — refcounting a
value that could still silently diverge into two independently-mutable
copies would have been refcounting the wrong thing.

**What actually changed, mechanically:** every array/map-specific
codegen site that used to GEP/extractvalue a *value* now does the same
thing one level of indirection further in (load the `ptr`, then GEP
off *that*) — `.length`, `arr[i]`/`map[key]` reads, `.forEach()`,
`_emit_map_set`/`_emit_map_get`, sqlite row collection. Construction
(`_emit_array_lit`/`_emit_map_lit`) now heap-allocates via the same
`_emit_fresh_heap_header` helper an escaping struct uses, rather than
a scratch stack alloca. `_try_addressable` — previously needed to tell
an "addressable" map target (whose own storage slot a grown entries
pointer could be written back into) apart from a merely "valuable" one
— was deleted outright: every arr[T]/map[T] expression's own *value*
is now the header's address itself, exactly what `festina_map_set`
needs to mutate directly, no separate addressability concept left to
maintain. `_global_var_defs`/`_zero_value` give array/map globals the
identical immortal-sentinel treatment a struct global already gets.

**Retain/release rule, identical to stages 4/5's own:** every binding
site — a local's own declaration, a plain reassignment, a global, a
struct field (widening claude.md #78's own field-write logic, which
already generalized cleanly once `_release_fn_for` existed), a `return`
value, a discarded call result — retains the new value unless its
source is "owning," and releases whatever the binding previously held.
"Owning" gains one new case beyond a plain function call: an array or
map *literal* (`[1, 2, 3]`, `{...}`) — structs have no literal syntax
at all, so this case never arose for them, but a literal allocates a
fresh header exactly like a call's own return value does, nothing else
referencing it the instant it's produced.

**Release itself needed no per-type codegen-generated wrapper the way
a struct's own cascade does** (`_release_fn_for_struct`'s lazily-built
`@__festina_release_struct_<Name>` functions) — unlike a struct, whose
own field layout varies by Festina type and needs the compiler's own
per-type knowledge, *every* `arr[T]`'s header has the identical
`{i64,ptr}` shape regardless of T (same for `map[T]`), so two fixed
runtime functions, `festina_release_array`/`festina_release_map`
(built on the same `festina_release_check` decrement-and-check split
claude.md #78 introduced), handle every array and every map
respectively. `_release_fn_for` is the one dispatch point every
release call site in codegen.py now goes through, choosing between
these two fixed functions and `_release_fn_for_struct`'s own per-type
dispatch.

**A non-escaping local's own stack-allocation optimization (stages
1-3) is unaffected in how its *header* is allocated** — still a plain
stack `alloca`, never heap/refcounted, exactly as before this stage.
Its data/entries buffer is still always heap-allocated regardless (a
dynamically-sized buffer was never safe to give a fixed-size alloca)
and still needs freeing at that local's own scope-exit — this stage
only adds a *second*, different scope-exit action (releasing the whole
header) for the case where the header itself escapes, via a new
`_StackArrayOrMap` marker mirroring `_StackStructFieldsOnly`'s own
shape from stage 5.

**Verified the same three ways as every stage before it:** new
IR-level tests (a with-initializer array/map local is refcounted, not
stack-allocated; array-typed struct field writes retain/don't retain
correctly; a struct with an array field gets a dedicated cascade
wrapper that calls `festina_release_array`; no `extractvalue` on
either payload type appears anywhere in generated IR anymore), new
compile-and-run tests (**the map-aliasing-through-growth bug above,
now a dedicated regression test**; array/map function parameters alias
the caller's own value; returning an array/map keeps the correct
value; discarded array/map call results don't crash; a recursive
function summing an array parameter; struct fields of array/map type
read/write correctly; a global array repeatedly reassigned in a loop,
the identical motivating case claude.md #77 originally had for
structs) — `tests/test_codegen.py::TestAutomaticMemoryReclamation`
grew from 93 to 107 — and real, properly-instrumented
AddressSanitizer/LeakSanitizer runs against a combined stress program
(every case above, plus two independently-tracked globals, a
loop-scoped stack-allocated array/map, 1500-2000 iterations each) —
zero ASan errors, zero leaks, including for every discarded call
result. Every earlier stage's own combined verification program (14 in
total, spanning stages 1 through 5) was re-run through the same
pipeline to confirm no regression — all came back clean. Full suite
before this stage: 778 tests; after: 790.

**One more real bug found and precisely characterized while verifying
this stage, deliberately not fixed here — see "what's still ahead"
below:** a struct-typed value stored as an array *element* (not a
struct *field* — that case is claude.md #78's own, already closed) can
still be read after the local it originally came from has gone out of
scope and been released, exactly the same shape of hazard claude.md
#78 closed for fields, still open for elements. Confirmed directly,
not assumed: a dedicated reproduction (a struct built fresh, stored as
an array's sole element, the array escaping through a global while the
original struct local's own function returns) produces a genuine
**heap-use-after-free**, caught by AddressSanitizer. Because this
stage never touches individual element/value storage at all — only an
arr[T]/map[T]'s own *header* is refcounted, never what's stored
*inside* it — this hazard is exactly as open after this stage as it
was before; this stage neither creates it nor closes it, and the
distinction (a fixed field list a struct's own declaration already
enumerates, vs. a dynamically-sized, runtime-indexed collection) is
precisely why it needs its own separate design pass, the same
reasoning claude.md #79's own boundary paragraph gives.

### Stage 7: reference counting for an `arr[T]`/`map[T]`'s own elements/values (done — claude.md #80)

The exact gap stage 6 confirmed still open, closed: an array element or
map value whose own type is itself refcounted (struct, `arr[T]`, or
`map[T]`) is now retained when stored and released when overwritten or
when the container holding it is freed — the same AddressSanitizer-
caught heap-use-after-free stage 6's own writeup describes (a struct
built fresh inside a function, stored as an array's sole element, the
array assigned to a global before that function returns; reading the
global's element afterward reads through a pointer its own original
binding already released) no longer reproduces, confirmed directly by
re-running the identical reproduction program: it now prints the
correct value on every one of 2000 iterations instead of crashing, and
a fresh AddressSanitizer/LeakSanitizer build of it reports zero errors
and zero leaks.

**Sound for the same structural reason stages 4-6 already lean on, one
level down:** Festina's grammar gives every `arr[T]`/`map[T]` type a
syntactically fresh, finite type expression at each nesting level —
there is no way to write a self-referential array or map type the way
stage 4's own argument already rules out for structs — so releasing an
`arr[arr[T]]`'s own elements (each one itself an `arr[T]`, which may in
turn have its own elements to release, and so on) always terminates, on
a nesting depth fixed at compile time by the program's own source text.

**Array elements and struct fields now share the identical retain-new/
release-old code path for `arr[i] = value`**, exactly as
`outer.field = value` already used for struct field writes (the
`not expr.target.computed` restriction that used to separate the two
cases was removed — by the time that shared code is reached with a
computed target, it's provably the array-element case, since the
`map[key] = value` case returns earlier from its own dedicated branch).
The one-time element store during array-literal construction
(`[a, b, c]`) retains each refcounted element the same way stage 6
already retains an aliased whole-array/whole-map value, skipped for the
same "owning" source shapes stage 6 already exempts (a function call,
or — new here, since it applies one level down too — an array/map
literal used as an element's own source expression); no release-old is
needed there, since a freshly `malloc`'d buffer was never previously
holding a valid pointer at any of its slots.

**`map[T]` needed a different mechanism for both directions**, since a
`FestinaMapEntry`'s own layout is deliberately opaque outside the C
runtime (the same boundary `festina_map_find`'s own comment already
documents, kept intact rather than punched through for this stage).
`map[key] = value`, in both a map literal's own construction and a
later assignment, retains the new value and releases whatever the key
previously held by looking up any existing value first (`_emit_map_set`
now calls the existing `festina_map_get`, with a null default — always
safe, since releasing null is always a no-op, whether the key was
genuinely absent or present but itself null) before the set proceeds.
Releasing every value in a map being freed reuses the existing
`festina_map_for_each` iteration `.forEach()` already relies on,
passing a freshly generated release-flavored trampoline function
instead of a user callback — no new C-side structure access was added
for this stage at all, by design.

**Two lazily-generated, per-element-type release wrappers**
(`_release_fn_for_array`/`_release_fn_for_map`, cached the same way
`_release_fn_for_struct`'s own per-struct-type wrappers already are)
are now generated only for an `arr[T]`/`map[T]` whose own element/value
type is itself refcounted — every other `arr[T]`/`map[T]` keeps using
the plain, generic, element-blind `festina_release_array`/
`festina_release_map` stage 6 already introduced, unchanged.
`_release_fn_for` — the one dispatch point every release call site in
codegen.py already goes through — now delegates `ArrayType`/`MapType`
to these two new methods instead of returning a fixed string directly.
**A non-escaping local's own stack-allocation optimization is
unaffected in how its own header is allocated** — still a plain stack
`alloca`, never itself refcounted, exactly as stages 1-6 already
established — but the `_StackArrayOrMap` scope-exit path now also
releases each element/value, when refcounted, before freeing the
data/entries buffer itself, the identical "the header's own storage was
never heap-allocated, but what it points to still needs freeing"
distinction stage 6 already drew for that buffer.

**Verified the same three ways as every stage before it:** new
IR-level tests (an array-literal element from an identifier retains,
from a call doesn't; an `arr[arr[Box]]`'s own release wrapper cascades
into `arr[Box]`'s own dedicated wrapper rather than the generic one; a
`map[Box]`'s own release wrapper uses `festina_map_for_each`; a
`map[int]` keeps using the plain generic function, no wrapper
generated needlessly), new compile-and-run tests (**the exact use-
after-free reproduction above, now a dedicated regression test, for
both an escaping array and an escaping map**; a nested `arr[arr[int]]`
element survives its own source scope; reassigning an array element or
map value releases the old one correctly; a non-escaping, stack-headered
array/map of structs still frees its own elements) —
`tests/test_codegen.py::TestAutomaticMemoryReclamation` grew from 107
to 118 — and real, properly-instrumented AddressSanitizer/
LeakSanitizer runs against both the exact original reproduction and a
new combined stress program (escaping arrays/maps of structs, nested
`arr[arr[int]]`, element/value reassignment, and non-escaping
stack-headered arrays/maps of structs, 500 iterations each) — zero ASan
errors, zero leaks. Every earlier stage's own stress/reproduction
program (15 in total, spanning stages 1 through 6) was re-run through
the same pipeline to confirm no regression — all came back clean. Full
suite before this stage: 790 tests; after: 801.

With this stage, every memory-safety gap `claude.md #43`'s "automatic
memory management" promise was ever found to have — leaked reclamation,
a struct's own escaping value, a struct's own struct-typed fields, an
escaping `arr[T]`/`map[T]` value, and now an `arr[T]`/`map[T]`'s own
elements/values — is closed.

### Stage 8: stack allocation for a literal-initialized non-escaping arr[T]/map[T] local (done — claude.md #81)

A correctness-neutral follow-up, not a new safety gap: prompted directly
by benchmarking (see [benchmark.md](benchmark.md)'s own `array_sum`),
not found by an audit. Stage 3 already gives a non-escaping struct
local, and a non-escaping *no-initializer* `arr[T]`/`map[T]` local, a
real stack-allocation option instead of a heap/refcounted one — a
*with-initializer* `arr[T]`/`map[T]` local never got this option at
all, even when non-escaping, always routing through the general
array/map-literal construction (stage 6's own), which always
heap-allocates its own header regardless of where the literal ends up
bound. Correct (a literal used as a nested subexpression might
genuinely need that header to outlive its own construction) but
needlessly conservative for the ordinary case of a local declared
directly from a literal and never used anywhere else — confirmed a real
cost, not just a theoretical one, once benchmarked: `array_sum`'s own
2,000,000-iteration loop, building a fresh 8-element `arr[int]` every
iteration, ran a real, honest 2.4x behind Rust/Go's own equivalent
(209ms vs. ~87ms) purely from this one avoidable heap allocation.

Closed for exactly the one case it's provably safe to: a local whose
initializer is an array/map literal written *directly* at the
declaration itself (not merely an expression that happens to evaluate
to one — an identifier bound to some other literal elsewhere doesn't
give this anything provable about its own size), and which escape
analysis already proves non-escaping — sound for the identical reason
stage 1's own no-initializer case already is: "non-escaping" already
rules out this local ever later being the target of a plain
reassignment too (an assignment target always escapes, by
escape_analysis's own existing rule), so there's no risk of a stack-
header local later being pointed at a genuinely different, possibly-
heap value. `_emit_array_lit`/`_emit_map_lit` (stage 6's own
construction logic) now accept a caller-supplied header slot to build
directly into, instead of always allocating their own — the literal's
own data/entries buffer is unchanged, still always heap-allocated (a
dynamically-sized buffer was never safe to give a fixed-size `alloca`),
so this closes only one of the two heap allocations a with-initializer
local used to need, not both.

Verified the same three ways as every stage before it: 10 existing
IR-level tests updated in place (they used a with-initializer
`arr[int]` local specifically *because* it used to always be
refcounted, to exercise loop/break/continue/if free-scheduling timing —
now correctly asserting a bare `@free(` call instead of
`festina_release_array(`, the identical timing, just a different
runtime function since the header itself is no longer refcounted), 2
renamed/rewritten to cover the new non-escaping-stack-allocated shape
directly, 1 new test added to keep the escaping with-initializer case
(still fully refcounted, unaffected) independently covered for maps the
way it already was for arrays — `tests/test_codegen.py::TestAutomaticMemoryReclamation`
grew from 118 to 119 — and real AddressSanitizer/LeakSanitizer runs
against every earlier stage's own stress/reproduction program (16 in
total) plus a dedicated new one at this stage's own established safe
scale, all clean. (A genuinely unrelated finding surfaced while pushing
verification scale higher, worth recording so it isn't mistaken for a
regression later: an *ordinary*, long-established, already-shipped
stack-allocated-struct-in-a-loop pattern — nothing to do with this
stage's own change — reliably trips a stack-overflow under
AddressSanitizer's own heavier per-frame instrumentation somewhere
between 10,000 and 100,000 loop iterations, confirmed by reproducing
the identical failure with a plain struct local completely unrelated to
this stage. The plain, non-instrumented binary runs the real
benchmark's full 2,000,000 iterations correctly and quickly either way
— an ASan-at-that-scale testing-methodology ceiling, not a correctness
bug in generated code.) Directly re-benchmarked afterward:
`array_sum` dropped from 209ms to 86ms, landing at parity with Rust and
Go's own equivalent rather than behind them, with byte-identical output
before and after.

### Stage 9: text values are owned and freed (done — claude.md #83)

The largest single gap in this whole effort, and the one nobody had
looked at: stages 1-8 gave `struct`/`arr[T]`/`map[T]` a complete
ownership story, and `text` was left out of *all* of it. A text value
was never freed anywhere in generated code, at any binding site, under
any circumstance — every reassignment abandoned the previous buffer,
every scope exit abandoned every text local. Found by profiling rather
than by audit: `string_concat`'s 15,000 iterations of `` s = `${s}x` ``
leaked every intermediate it built, so the heap grew quadratically and
the program spent essentially all its runtime in `brk()` — **816 calls,
against 3 for equivalent leak-free C**, which is where ~650ms of a
~655ms benchmark was going.

Closed *without* the refcount-header representation stages 4-7 use.
That representation puts a counter in front of the payload, which every
consumer must know about — and text's payload is a plain `char*` that
sqlite, the regex engine, `festina_log_text`, and every comparison
already take directly, so changing it would touch all of them. Text
gets exclusivity by **copying** instead: every text binding always
holds either NULL or a buffer it owns exclusively, never a bare alias
of a `.str.N` constant or of another binding's buffer, with one new
runtime helper (`festina_text_own`, a NULL-safe `strdup`) and plain
`@free` for release. The payoff is that freeing needs **no escape
analysis at all** — copying happens at each consuming site rather than
by draining the source, so no number of other readers can make freeing
a text local unsafe, and it is freed unconditionally on reassignment
and at scope exit.

Three things had to be fixed alongside it. An uninitialized `text s`
local previously got an alloca and *no store at all*, leaving genuine
garbage in the slot — harmless while text was never freed, an immediate
wild-pointer `free()` afterward. Stage 8's sibling optimization
(claude.md #82) had to be revised: a template that concatenates nothing
(`` `${name}` ``) would otherwise hand back `name`'s own buffer to a
caller that believes it owns what a template returns. And templates had
to start freeing their own intermediates — `festina_str_concat` leaves
both operands untouched, so a four-concatenation template leaks three
buffers unless each is freed as the next one finishes copying out of it.

Verified the same way every stage before it was: 13 new tests
(`tests/test_codegen.py::TestTextReferenceManagement`) plus real
AddressSanitizer/LeakSanitizer runs over locals, globals, uninitialized
locals, reassignment, nested call temporaries, struct fields, array
elements, map values, regex/text methods on temporaries, loop
accumulation and parameter reassignment — all clean, with byte-identical
output. `string_concat` went from ~77ms to **3.6ms** with the O(n²)
naive-copy algorithm completely unchanged.

One follow-up is deliberately left open rather than quietly claimed:

- **Nothing frees a text global at process exit.** Deliberate, matching
  how every other global already behaves — the process is ending — but
  it does mean LeakSanitizer sees them as still-reachable rather than
  freed.

(An earlier version of this note also listed text arguments to the
graphics, sqlite, and timer builtins as unfreed. Stage 11 closed the
graphics and sqlite cases, along with `regex()` and `loadAudio()`; the
timer entry was simply wrong — `setTimeout`/`setInterval` take a
function name and an int delay, and `clearTimeout`/`clearInterval` take
an int id, so no text ever reaches them.)

### Stage 10: a reassigned parameter owns its own reference (done — claude.md #84)

A **real, pre-existing use-after-free**, not introduced by stage 9 —
found while designing text's own parameter handling, which has the same
shape. A `struct`/`arr[T]`/`map[T]` parameter is passed as the caller's
raw pointer, unretained; that borrowed convention is deliberate and
worth keeping. But a callee that *reassigns* its own parameter
(`p = somethingElse`) runs stage 4's ordinary local-reassignment path,
which releases whatever the binding currently holds — and for a
borrowed parameter that is the caller's live value, dropping a refcount
the callee never incremented and freeing it out from under the caller.

Confirming it took some care, because the two most obvious reproductions
both hide it, which is worth recording so a future audit doesn't
conclude the bug isn't there. A global that has never been assigned
still carries the immortal negative-refcount sentinel static storage
starts with, on which retain and release are both no-ops. A global that
*has* been assigned got an unconditional retain on that assignment,
leaving its count at 2, so the erroneous release only brings it to 1 and
the symptom is a leak rather than a crash. Only a heap-allocated
**local** passed to a parameter-reassigning callee exposes it — and
under ASan that is an unambiguous heap-use-after-free with both the
freeing and allocating stacks recorded.

Closed by giving any parameter the callee assigns to its own reference
at binding time (`festina_retain` for struct/arr[T]/map[T], a
`festina_text_own` copy for text), released at the callee's own scope
exit. This required computing escape analysis *before* parameters are
bound rather than after, and giving parameters their own scope-exit
frame outside the body's.

One follow-up left open: **the retain/copy is keyed on the whole
`escaping` set rather than on reassignment alone.** Every reassigned
name is necessarily in that set (escape analysis adds every bare
Identifier assignment target), which is what makes this safe — but the
set is broader, so a text parameter that is merely interpolated or
passed along takes a `strdup` it doesn't need, on every call. Narrowing
it to genuine reassignment is safe (every other escaping use either
borrows the parameter or does its own retain/copy at the storing site)
and wants a `find_reassigned_names` alongside `find_escaping_names`;
left undone here only to avoid threading a second collector through
eight functions of a heavily-tested module late in an unrelated change.

### Stage 11: query rows and runtime-compiled regexes are reclaimed (done — claude.md #85)

Two leak classes stage 9 *surfaced* but did not cause — both
pre-existing, and both unbounded rather than one-off: they grew with the
number of queries run or regexes compiled, not with program size. Found
by running the stage 9 verification programs against a workload that
actually used sqlite and `regex()` together, rather than by audit.

**Query rows.** A sqlite result row is deliberately not shaped like any
other value in this language: `festina_sqlite_collect_rows` builds each
one as a plain `malloc(col_count * sizeof(int64_t))` with each text/blob
column strdup'd into its slot, and — unlike every struct/`arr[T]`/
`map[T]` value since stage 4 — **no refcount header**. `TableType` is
also a separate type class from `StructType`, so every
`isinstance(t, (StructType, ArrayType, MapType))` test in codegen missed
it entirely and nothing ever freed a row or any of its text columns.
Since `arr[People] rows = sqlite(...)` is this language's single most
central idiom, every query leaked its whole row set. What was already
correct is the container: an `arr[T]` is an `arr[T]` whatever its
element type, so the array header and its pointer buffer were always
freed — only the rows those pointers point *at* were not.

The fix respects two constraints at once. A row has no header, so
`festina_release` (which reads the eight bytes before the payload) could
never be pointed at one. And the array owns its rows outright, so a
`People p = rows[0]` local — or a row passed to a function — is only
borrowing one. The per-row free is therefore a bespoke, per-table
generated function reached **solely** from `_release_fn_for_array`'s own
element cascade, and deliberately *not* exposed through
`_release_fn_for`, which would otherwise let an arbitrary
TableType-typed binding free a row out from under the array holding it.
Which columns need freeing uses the identical rule the runtime used when
building the row (`text`/`blob` → strdup, everything else → a plain
i64), with `free(NULL)` covering a column that was SQL NULL.

**Runtime regexes.** Every `regex(pattern)` call compiles a fresh
`regex_t` — several KB once regcomp's automaton is built — that nothing
ever freed, so a `regex(...)` inside a loop leaked one per iteration. A
regex used as a temporary in the expression that compiled it
(`regex(p).test(s)`, the ordinary shape) is now released through a new
`festina_regex_free` (`regfree` for what regcomp allocated inside the
struct, then `free` for the struct), reusing stage 9's own
free-immediately-after-the-consuming-call approach. The owning test
separates the two ways a regex is produced, and getting it wrong either
way is a real bug rather than a missed optimization: only `regex(...)`
is an `ast.Call`, while a `/pattern/` literal is an `ast.RegexLit`
compiled once into a process-lifetime cache that must never be freed —
freeing one would leave every later evaluation running against a
dangling `regex_t`.

Verified with 6 new tests
(`tests/test_codegen.py::TestQueryRowAndRegexReclamation`) covering both
the generated IR and end-to-end behaviour, plus ASan/LeakSanitizer runs
over a sqlite workload (multiple queries, a borrowed row, a text column
copied out) and a regex loop mixing runtime and literal patterns — all
clean. Full suite 829 → 835.

One leak stayed open here and was closed by stage 12 below; the other
is text globals at process exit, unchanged from stage 9.

### Stage 12: a non-escaping regex local is freed (done — claude.md #86)

Stage 11 left "a regex bound to a variable" open, described as bounded
by the number of such declarations. That was wrong: `regex r = regex(p)`
*inside a loop* leaks a full compiled automaton — several KB — on every
iteration, so it was unbounded, and worth closing rather than accepting.

Closed for exactly the provable case: a regex local whose initializer is
a `regex(...)` call, and whose name escape analysis already proves never
leaves the declaring function. Both halves are load-bearing, and
relaxing either frees something still in use:

- A `/pattern/` **literal** initializer is a pointer into the
  process-lifetime cache from `claude.md #67`. Freeing it would leave
  every later evaluation of that same literal running `regexec` against
  freed memory.
- An **escaping** regex has no equivalent of the copy-on-alias trick
  that makes text's freeing unconditional (stage 9). A regex "copy"
  would mean recompiling, and the pattern string isn't retained to
  recompile from, so exclusivity can't be manufactured. It is left to
  leak, deliberately, rather than freed while another binding may still
  point at it.

Reached only through the scope-exit path, never `_release_fn_for` —
routing it through the generic dispatcher would make an `arr[regex]`
element cascade free each element, and those can be cached literals,
reintroducing the exact hazard one level down. Same containment argument
stage 11 makes for a table row's per-row free. 4 new tests
(`tests/test_codegen.py::TestOwnedRegexLocals`) plus ASan runs over a
200-iteration loop, a cached-literal binding, and an escaping binding.

### Stage 13: the X display connection is retried (done — claude.md #87)

Not a leak — a robustness bug, and the cause of the one genuinely flaky
test in the suite. `festina_graphics_init` called `XOpenDisplay` exactly
once; Xlib does no retrying of its own, so a single transient connection
refusal under load killed the whole program with a fatal error naming
entirely the wrong cause ("is `$DISPLAY` set?").

`TestGraphics` had been failing roughly one test per full-suite run and
essentially never in isolation. That was previously attributed to
contention making window startup slow, and "fixed" by doubling the
test's polling timeout to 20s — a misdiagnosis that could never have
worked, since the program had already exited before polling began. A
window actually appears in **~0.2s**, measured, consistently.

The forensics are recorded in claude.md #87 because the failure looks
exactly like a dead or misaddressed X server and is neither: at the
moment of failure the Xvfb process was alive, its socket and lock file
were both present with the lock naming that same live server's pid
(ruling out display-number collision), and `xdotool` connected to that
exact display successfully both immediately before and immediately
after. The connection was simply refused once.

Ten attempts, 100ms apart, so a genuinely absent X server still fails
with the same clear message in about a second. 0 failures in 216 runs
under heavy parallel contention (against 4 in 128 before), three
consecutive clean full-suite runs, and the suite ~25s faster for no
longer timing out on an already-dead process.

### Stage 14: a struct's own text field is freed (done — claude.md #88)

A gap in stage 9's own work, found by ASan while verifying stage 12.
Stage 9 added the machinery to free a struct's text-typed field but
never widened the predicate deciding whether a struct needs field
cleanup at all — it still counted only struct/`arr[T]`/`map[T]` fields.
A struct whose only managed field is a text one fell through **both**
directions that predicate gates: stack-allocated, never scheduled for
field release; heap-allocated, given the generic `festina_release`
instead of a per-struct wrapper. Either way the buffer leaked, so a
struct rebuilt in a loop leaked one per iteration.

Fixed by the widening stage 9 should have included, plus renaming the
predicate to `_struct_has_own_managed_field` — "managed", not
"refcounted", since text is managed by exclusive ownership rather than
by counting. Worth recording *why* stage 9's own verification sweep
missed it: none of its programs happened to leave a struct alive with a
text field in it. 3 new tests
(`tests/test_codegen.py::TestStructTextFieldReclamation`).

### Stage 15: unassigned struct/array/map fields auto-vivify (done — claude.md #97)

Recorded here after stage 14 as "found but not fixed", on the reasoning
that fixing it meant a design decision about who owns a nested field's
allocation. This stage made that decision. Probing it first showed the
recorded scope was too narrow in two directions — **reads** crashed just
as writes did, and `arr[T]`/`map[T]`-typed fields crashed the same way
struct-typed ones did:

```festina
struct Inner { n:int }
struct Bag   { inner:Inner  xs:arr[int] }
Bag b
log(b.inner.n)     // exit 139
log(b.xs.length)   // exit 139
```

The decision: **lazily, on first reach**, not eagerly at the parent's
declaration. Eager allocation needs somewhere to run an initializer, and
a global has none — its storage is a compile-time `zeroinitializer`. Lazy
vivification needs no such place, so one mechanism covers stack locals,
heap locals, globals, parameters and arbitrarily deep nesting. The load
becomes a null test, a make-and-store branch and a phi; the storage is
created once, not per access, which is what makes `o.inner.n = 5`
followed by a read answer 5. The ownership question stages 4–6 raised
answers itself: the vivified value is created by the same
`_emit_fresh_heap_header` path every other value of that type uses, and
is owned by the field exactly as an explicitly assigned one is.
7 new tests
(`tests/test_codegen.py::TestUnassignedNestedFieldsAutoVivify`), plus a
100-iteration ASan run.

### Stage 16: three more leaks and one silent corruption (done — claude.md #97)

Found while ASan-verifying `indexOf`, all pre-existing:

- **A text `+` was not an owning source.** Stage 9 classified only a
  call and a template literal as "already a fresh, exclusively-owned
  buffer". A text `+` compiles to one `festina_str_concat`, which
  mallocs unconditionally — so `text j = a + b` and `return s + '!'`
  each copied an already-exclusive buffer and dropped the original, and
  a chained `a + b + c` leaked its intermediate on top of that. This
  should have been caught with stage 9's own `_emit_template` fix, which
  is the identical bug one expression form over.
- **Computed map keys.** `festina_map_set` strdups its key and
  `festina_map_get` only reads one, so `m[`s${i}`] = v` leaked the key
  it built. Both sites now free it.
- **Top-level block scopes were never tracked.** Stage 4's scope
  tracking only ran inside function/handler bodies, so a local declared
  in a nested block at *top* level — `text row = a + b` inside a
  top-level `while` — was emitted as an ordinary alloca and never
  freed. One buffer per iteration, in exactly the shape a game loop
  takes. The top-level statement list now gets the same whole-body
  escape analysis every function gets.
- **`arr[bool]` was silently corrupt** (not a leak — a wrong answer).
  claude.md #96's array helpers move elements by a byte count passed in from
  codegen, hardcoded to 8. Every element type is 8 bytes wide except
  `bool`, which is `i8`, so `push` wrote byte `8*i` while `xs[i]` read
  byte `i`. The stride now comes from the element type.

### The one remaining leak

- **Text globals at process exit.** Deliberate, and worth stating
  precisely rather than repeating "matches other globals": LeakSanitizer
  already reports these runs **clean**, because a global stays reachable
  through its own variable — it only shows up if you explicitly disable
  global-root scanning (`use_globals=0`), which is an artificial
  configuration. Since every reassignment already frees the previous
  value (stage 9), at most one buffer per global survives. Freeing them
  would be pure exit-time busywork in every binary, for no observable
  benefit; Rust doesn't drop statics and Go doesn't finalize globals
  either.

### What's still ahead

- **A real tracing GC** is now the only complete answer to one real
  leak, where it used to be an answer to nothing. It was originally
  ruled out on section 77's finding that reference cycles were
  structurally impossible — no self-referential or forward-referencing
  struct field types — so the one thing a tracing collector does that
  refcounting cannot was not a problem this language could produce.
  **claude.md #106 changed that**: self-referencing and
  forward-referencing structs compile now, so `a.next = a` is
  writable, and a cycle is exactly what refcounting cannot free.
  Measured at 1,200 bytes over 50 iterations, growing without bound.
  This was a deliberate trade — linked lists and trees are worth more
  than the guarantee was — but it is a real, permanent hole in the
  memory model until something traces. Nothing partial helps: cycle
  detection on release, weak references, or an explicit `unlink` would
  each address it, and each is a bigger design decision than any leak
  fix so far.

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
- **No way to name one playback of a clip.** `stop()` and `isPlaying()`
  are about the `aud` -- since claude.md #98 that means every channel
  playing it. claude.md #99 narrowed this considerably (a program that
  wants to address one playback names its channel:
  `clip.playLoop(0)` / `stopAudioPlayer(0)`), but there is still no
  per-playback `isPlaying`, which would need a handle to a playback --
  the pool-as-language-surface both sections refused. Recorded as a
  real limit, not as something expected to change.
- **An escaping `aud`/`img`/`regex` is never freed.** claude.md #101
  closed the ordinary case -- a non-escaping `aud` or `img` local is now
  reclaimed at scope exit, as is one held in a query row -- so what is
  left is the same conservative escape-analysis boundary a `regex` has
  had since #86: a handle that escapes its function, or one bound to a
  global, lives for the program's lifetime.

  **Correction: this was previously described here as "bounded (one
  allocation per load, not per use)". That is wrong, and measuring it
  is what showed it.** The load is what escapes, and a loop that loads
  repeatedly leaks repeatedly. An `img` aliased inside a function over
  60 iterations leaked 1,010,906 bytes in 472 allocations; a `regex`
  aliased over 200 iterations leaked 678,400 bytes in 4,400
  allocations — both under LeakSanitizer, both growing with the loop
  count. A handle loaded *once* into a global is genuinely one
  allocation and genuinely comparable to text globals at exit; a
  handle loaded inside a loop is an unbounded leak, and the old wording
  hid the difference.

  **claude.md #109 shows what the fix looks like, on a fourth handle
  type.** `blob` is reference counted rather than owned-or-leaked, and
  it needed no new machinery to be: it carries the same `i64` header
  structs have carried since #77, so `festina_retain` /
  `festina_release_check` and the retain-before-release ordering at a
  reassignment all worked on it unchanged. Escape analysis never enters
  into it — a blob binding owns one reference wherever the value came
  from, so an escaping blob is reclaimed like any other. Giving `img`
  and `aud` the same header would close this entry outright; the reason
  it is still open is that neither has a refcount to increment, not
  that anything about them resists one. `regex` is the harder case, as
  a `/pattern/` literal is a process-lifetime cached pointer that must
  never be freed, so it would need an immortal sentinel — which #77's
  header already has, for exactly this shape of problem.
- **A call result reached through a chain that yields a MANAGED value
  still leaks.** Mostly closed by claude.md #108. `make().count`
  (claude.md #102), `make().inner.n`, `rows(x).length` and
  `make().inner.items.length` are all reclaimed now -- the decision
  moved to the outermost link of a member chain, where the type of the
  value that actually escapes is known, and any chain yielding a plain
  copy releases every call result it produced.

  What remains is a chain whose result is itself managed or is a text:
  `Inner got = make().inner`, `text t = make().inner.label`. Releasing
  the parent there recursively releases its struct/arr/map fields and
  frees its text fields, so the value just loaded would be freed before
  the caller saw it -- a use-after-free traded for a leak, which is the
  wrong direction. Fixing these needs a notion of an owned temporary
  that outlives its producing expression, which this codegen does not
  have. Tests pin that the loaded value stays intact in both shapes, so
  the leak cannot quietly become a use-after-free. Measured: 5,520
  bytes over 60 iterations for the text case, 5,388 for the struct
  case.

- ~~**A struct cannot reference its own type.**~~ Fixed by claude.md
  #106. `struct Node { n:int next:Node }` compiles, forward references
  compile, and acyclic linked structures are reclaimed normally. The
  cost is that reference cycles became constructible; see the tracing
  GC note above.
- **A blob out of a database column cannot be written to a file.**
  claude.md #109: a blob read back from a BLOB column has bytes and no
  path — a path is meaningful only on the machine that stored it — so
  its `exists()`/`write()`/`append()`/`delete()` all answer false. Its
  bytes cannot be moved into a path-backed blob either, because
  `toText()` stops at the first NUL and binary content does not
  survive the trip. Reading a binary column and re-inserting it works;
  reading one and saving it as a file does not. Closing this needs
  either a bytes-preserving transfer between two blobs or a
  `saveTo(path)` method — neither was asked for, and picking one
  quietly would be a worse answer than recording where the edge is.
- **Only PNG/JPEG and WAV/MP3.** claude.md #101 added JPEG and MP3, and
  drew the line there deliberately: each new format is a new
  system dependency on every machine that compiles a graphics or audio
  program, and PNG+JPEG / WAV+MP3 covers what a 2D game actually ships.
  Ogg/FLAC/WebP/GIF would each need their own library and none of them
  is the obvious next one.
- **A key held down still repeats `keyDown`.** Deliberate -- that is
  how text entry works, and claude.md #98 only guarantees that a HELD
  key fires exactly one `keyUp`, when it is really let go. A program
  that wants edge-triggered presses tracks which keys it has seen go
  down; the language does not do that for it.
- `regex(pattern, flags)` -- the dynamic builtin call, not a
  `/pattern/flags` literal (those are now cached, compiled once per
  source location on first reach -- see tests/CONTRACT.md) -- still
  recompiles its pattern on every call. Inherent to it: pattern is a
  general runtime expression, so the same call site can legitimately see
  a different pattern on different calls (e.g. `regex(userPattern)`
  inside a loop), and caching by call site the way the literal case does
  would be a correctness bug, not a caching gap to close. Measured over
  200,000 iterations: literal 15 ms, `regex()` hoisted into a variable
  outside the loop 13 ms, `regex()` called inside the loop 367 ms.
  Documented in api.md with the workaround, since the fix is to hoist
  it rather than for the compiler to guess.
