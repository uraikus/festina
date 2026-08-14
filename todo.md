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
longer reachable." Today, arrays and struct storage are heap-allocated
(`malloc`/`calloc`) and never freed at all — a real resource leak in any
long-running program, though not a memory-safety issue on its own (see
[security.md](security.md)'s "known, accepted memory-management gap"
note: no use-after-free, no double-free, since nothing is ever freed).

This is deliberately still not implemented, and deliberately not
attempted casually — not because it's unimportant, but because the two
obvious ways to close it are a much bigger, riskier undertaking than
either landing a new language feature (`map[T]`, `break`/`continue`,
...) or fixing a compile-time gap (a mismatched-type error reaching
codegen, a missing null representation):

- **Stack-allocate a struct/array that provably never outlives its
  declaring function**, instead of always heap-allocating. This was
  already tried, in an earlier pass, in the most naive form (stack-
  allocate every local struct unconditionally) — and reverted after it
  was verified to silently corrupt memory: a struct's address can
  genuinely outlive its function (returned, stored in an array or
  another struct's field, ...), and a stack allocation doesn't survive
  that (see `festina/codegen.py`'s module docstring, "Struct storage is
  always heap-allocated"). Doing this correctly needs real escape
  analysis (does this value's address ever get returned, stored into a
  longer-lived array/struct/global, or passed to something that might
  retain it?) — genuinely tricky to get exhaustively right, and a wrong
  answer here doesn't fail loudly, it silently reads garbage at some
  later, disconnected point in the program, exactly the failure mode
  already verified once.
- **Reference counting** (or a real tracing GC) for heap-allocated
  arrays/structs — increment on every assignment/parameter pass/store
  into another value, decrement when a scope's variables go out of
  scope, free at zero. This is the more complete answer to what claude.md
  #43 actually asks for, but touches nearly every place a value is
  assigned, passed, stored, or a scope exits — a large surface area to
  get right, and an incorrect refcount (an early free, or a missed
  increment before something else drops its own reference) *is* a real
  memory-safety bug — a genuine regression from today's "leaks but is
  otherwise memory-safe" state, not a wash. Cycles are also a real
  question to resolve one way or another (rare in Festina's language
  model, given no closures/first-class functions and no direct self-
  referential struct fields, but not provably impossible without
  checking).

Given the size and risk of either path, this needs a dedicated design
pass (and, given this project's own established pattern, almost
certainly a `claude.md` addition first, spelling out exactly which
strategy and what tradeoffs it accepts) rather than being folded into an
unrelated change as a side effect. Not attempted here for that reason,
not because it was overlooked.

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
