# Roadmap

Open work only, shortest useful form. The reasoning behind every closed
item lives in [claude.md](claude.md) (the numbered decision log) and
[tests/CONTRACT.md](tests/CONTRACT.md) (what is verified, and how).

## Platforms

Linux is fully supported. macOS and Windows are also fully
implemented and built — [macos.md](macos.md) and [windows.md](windows.md)
are implementation records now, not open plans. Porting turned out to
be exactly what it looked like going in: backend work, not language
work — the compiler and core runtime are portable C/LLVM, and both
ports share the same two backend seams (audio device, windowing).

What's actually left, for both ports, is external to this codebase —
real hardware to confirm on, which this project doesn't have:

- **Audio playback and windowed mouse/keyboard/window behavior**, on
  both a real Mac and a real Windows machine. Each stays behind an
  explicit opt-in env var (`FESTINA_ENABLE_MACOS_AUDIO`/
  `_GRAPHICS`, `FESTINA_ENABLE_WINDOWS_AUDIO`/`_GRAPHICS`) until
  confirmed — everything up to that point (both backends compile,
  type-check against real platform headers, and pass CI on every
  push) is done.
- **One exception that needs no hardware at all**: unlike macOS,
  GitHub's Windows CI runners can create real Win32 windows. The
  windowed-graphics gate has just never actually been lifted for a CI
  run to try it (see [windows.md](windows.md)'s "genuine opportunity,
  not yet taken" note) — the cheapest open item on either platform.

WASM (`wasm32-wasi`) is also implemented and CI-verified — see
[wasm.md](wasm.md). Graphics/audio are out of scope there permanently
(WASI has no backend for either at all, not a hardware-verification
gate), and two things are genuinely still open, neither blocking:
running a compiled `.wasm` in a browser (untested — every test/
benchmark here uses Node's own `node:wasi` host, not a browser's WASI
polyfill), and ASan/LeakSanitizer coverage for the target (unexplored,
same as macOS's own sanitizer tier is explicitly out of scope).

## Language & standard library

- **Media formats** stay PNG/JPEG + WAV/MP3, deliberately (claude.md
  #101): each new format is a new system dependency for every machine
  that compiles a media-using program. Revisit only with a concrete
  need.

## Memory model

Automatic reclamation is escape analysis plus reference counting —
every managed type carries the same refcount header since claude.md
#118 gave `img`/`aud`/`regex` theirs, and reference cycles are
collected by trial deletion since claude.md #120 — with `free`/`delete`
as the manual override (claude.md #74–#83, #111, #118–#120). What
remains:

- **Cycle trials are synchronous and per-release** — every
  still-referenced release of a cycle-capable type walks the value's
  reachable subgraph. Correct, and measured fast for ordinary object
  graphs (20k dropped 21-node cycles in ~34 ms), but a very large,
  heavily-aliased cyclic structure could feel it; the classic
  deferred-root buffer is the known optimization if a real program
  ever does.
- **A table-row element off a call-result array leaks the array**
  (`rows()[0]` where the elements are query rows; claude.md #119
  closed every other computed-index and argument-position chain
  shape). Rows have no refcount header — the array owns them outright
  — so the element cannot be retained past its container. Bind the
  array to a name first and it reclaims normally.
- **Text globals are not freed at process exit** — deliberate: they are
  reachable until exit, LeakSanitizer agrees, and freeing them would be
  exit-time busywork.

## Deliberate behavior (documented, not planned work)

- **Array indexing is not bounds-checked** — a performance choice, see
  [api.md](api.md#indexing-is-not-bounds-checked).
- **`keyDown` auto-repeats while held** (that is how text entry works);
  a held key still fires exactly one `keyUp`. Track held keys yourself
  for edge-triggered input (claude.md #98).
- **`regex(pattern, flags)` is memoized per call site** (claude.md
  #118) — the runtime compares the actual pattern+flags against the
  site's last compilation, so a repeated pattern costs what a literal
  does (~24x cheaper than recompiling) and a changed one recompiles.
  One site *alternating* patterns still recompiles per change — see
  [api.md](api.md#literals-are-compiled-once-regex-is-memoized-per-call-site).
