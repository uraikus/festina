# Roadmap

Open work only, shortest useful form. The reasoning behind every closed
item lives in [claude.md](claude.md) (the numbered decision log) and
[tests/CONTRACT.md](tests/CONTRACT.md) (what is verified, and how).

## Platforms

Linux is the supported platform today. Porting is backend work, not
language work — the compiler and core runtime are portable C/LLVM.

- **macOS** — graphics (Cairo/X11 → Quartz, or document XQuartz), audio
  (ALSA → CoreAudio), Homebrew dependency paths, and packaging
  (`scripts/package_compiler.sh`).
- **Windows** — the same two backends, plus POSIX seams in the core:
  `<regex.h>` (regex), `<sys/select.h>`/`clock_gettime` (the timer/event
  loop). MSYS2 or a small shim layer are the candidate routes.
- **Static sqlite3 linking** for fully self-contained binaries — see
  [setup.md](setup.md#static-linking-sqlite3).

## Language & standard library

- **HTTP / networking** — the largest missing capability, and the one
  that will most reshape [security.md](security.md) when it lands.
- **Per-playback audio addressing** — `play()` returns its channel and
  `stop()` is clip-wide (claude.md #109), but there is still no
  `isPlaying(channel)`.
- **Media formats** stay PNG/JPEG + WAV/MP3, deliberately (claude.md
  #101): each new format is a new system dependency for every machine
  that compiles a media-using program. Revisit only with a concrete
  need.

## Memory model

Automatic reclamation is escape analysis plus reference counting —
every managed type carries the same refcount header since claude.md
#118 gave `img`/`aud`/`regex` theirs — with `free`/`delete` as the
manual override (claude.md #74–#83, #111, #118). What remains:

- **Reference cycles leak** (`a.next = a`; constructible since
  claude.md #106). Refcounting cannot free a cycle; the complete answer
  is a tracing collector or weak references. Until then, break cycles
  by hand (`child.parent = null` — verified to reclaim fully).
- **Two chain shapes still leak** (claude.md #117 closed the rest): a
  call-based chain passed directly as a function *argument* leaks its
  +1 (parameters are borrows, so nobody owns the release), and a
  computed-index receiver (`getRows()[0]`) leaks its array. Bind to a
  name first and both reclaim normally.
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
