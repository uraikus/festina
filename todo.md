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
longer reachable." Arrays and struct storage are heap-allocated
(`malloc`/`calloc`); until `claude.md #74` (stage 1, below) landed,
none of it was ever freed — a real resource leak in any long-running
program, though never a memory-safety issue on its own (see
[security.md](security.md)'s note: no use-after-free, no double-free,
since nothing was ever freed).

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

### What's still ahead

- **Escape analysis is *always still followed by a real `calloc` +
  `free`***, not true stack allocation — a real speed cost (allocator
  traffic) that a genuine stack alloca would avoid entirely. Widening
  stage 1's proof to also cover interprocedural reasoning (does a value
  passed as a call argument actually get retained by the callee, or is
  it provably only read/used transiently?) is what would let it safely
  swap in a stack alloca instead of calloc+free for the values that
  qualify — the nested-block and per-iteration-loop coverage stage 1
  already has is not itself the blocker for this anymore, since a
  stack alloca inside a loop body is exactly as sound as one inside a
  plain function body (both are popped/reused the same way on the next
  iteration/call); it's specifically the still-conservative "any call
  argument escapes unconditionally" rule that would need real
  interprocedural analysis before this could be considered. Still
  governed by claude.md #74 (or a new stage within it), not a new
  design.

  A naive, unconditional version of stack allocation was tried once,
  before stage 1 existed, and reverted after it was verified to
  silently corrupt memory (a struct's address can genuinely outlive its
  function -- returned, stored in an array or another struct's field --
  and a stack allocation doesn't survive that; see
  `festina/codegen.py`'s module docstring). Stage 1's own escape
  analysis is the proof mechanism that naive attempt was missing --
  widening it is the difference between "provably safe, narrow" (stage
  1 today) and "provably safe, wide enough to also justify stack
  allocation instead of calloc+free."
- **Reference counting** (or a real tracing GC) for the values escape
  analysis can't (or structurally never could) clear — genuinely shared
  values, or ones stored somewhere long-lived on purpose (globals,
  caches). This is the complete answer for the remainder stage 1's
  approach can never reach on its own (a value that provably *does*
  escape has nothing for escape analysis to do), but touches nearly
  every place a value is assigned, passed, stored, or a scope exits — a
  large surface area to get right, and an incorrect refcount (an early
  free, or a missed increment) *is* a real memory-safety bug, a genuine
  regression from "leaks but is memory-safe," not a wash. Cycles are
  also a real question to resolve one way or another (rare in Festina's
  language model, given no closures/first-class functions, but not
  provably impossible without checking). Needs its own `claude.md`
  addition and dedicated design pass before implementation, same as
  every stage here — not attempted yet, not overlooked.

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
