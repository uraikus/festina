# Roadmap

Open work only, shortest useful form. The reasoning behind every closed
item lives in [claude.md](claude.md) (the numbered decision log) and
[tests/CONTRACT.md](tests/CONTRACT.md) (what is verified, and how).

## Platforms

Linux is the primary, fully verified target. macOS and Windows builds
exist, compile, and type-check against real platform headers in CI —
see [macos.md](macos.md) and [windows.md](windows.md) for exactly
what's supported on each. What remains open:

- **Audio playback and windowed mouse/keyboard/window behavior**, on
  both a real Mac and a real Windows machine. Each stays behind an
  explicit opt-in env var (`FESTINA_ENABLE_MACOS_AUDIO`/
  `_GRAPHICS`, `FESTINA_ENABLE_WINDOWS_AUDIO`/`_GRAPHICS`) until
  confirmed on real hardware.
- **One exception that needs no hardware at all**: unlike macOS,
  GitHub's Windows CI runners can create real Win32 windows, so the
  windowed-graphics gate on Windows could be lifted for a CI run to try
  it directly — see [windows.md](windows.md). The cheapest open item on
  either platform.

Compiling to `wasm32-wasi` is supported and CI-verified — see
[wasm.md](wasm.md). Graphics/audio are out of scope there permanently
(WASI has no backend for either), and two things remain open, neither
blocking: running a compiled `.wasm` in a browser (every test/benchmark
here uses Node's own `node:wasi` host, not a browser's WASI polyfill),
and AddressSanitizer/LeakSanitizer coverage for the target.

## Language & standard library

- **Media formats** stay PNG/JPEG + WAV/MP3, deliberately: each new
  format is a new system dependency for every machine that compiles a
  media-using program. Revisit only with a concrete need.
- **`thread` declarations are singletons** — declared once by name,
  with no way to spawn more than one instance of the same `thread`
  block (a worker pool, say). See [api.md](api.md#threads).
- **A thread-private helper function** (an ordinary `func` callable
  only from inside one thread's own body, closing over that thread's
  own state) **isn't supported** — every top-level `func` is checked
  for purity and either fully callable from any thread or not callable
  from one at all; there's no way to declare one that's already scoped
  to a single thread. See [api.md](api.md#threads).

## Memory model

Automatic reclamation is escape analysis plus reference counting —
every managed type (`struct`/`arr[T]`/`map[T]`/`text`/`img`/`aud`/
`regex`/`blob`/`http`/`url`/`socket`) carries a refcount header, and
reference cycles are collected by trial deletion, with `free`/`delete`
as the manual override. What remains open:

- **Cycle trials are synchronous and per-release** — every
  still-referenced release of a cycle-capable type walks the value's
  reachable subgraph. Correct, and measured fast for ordinary object
  graphs (20k dropped 21-node cycles in ~34 ms), but a very large,
  heavily-aliased cyclic structure could feel it; the classic
  deferred-root buffer is the known optimization if a real program
  ever does.
- **A table-row element off a call-result array leaks the array**
  (`rows()[0]` where the elements are query rows). Rows have no
  refcount header — the array owns them outright — so the element
  cannot be retained past its container. Bind the array to a name first
  and it reclaims normally.
- **Text globals are not freed at process exit** — deliberate: they are
  reachable until exit, LeakSanitizer agrees, and freeing them would be
  exit-time busywork.
- **A `throw` reached from a called function leaks that function's own
  locals**, and, structurally the same issue, **`.toStruct()`/
  `.toArr()` leak whatever they'd already built when a parse fails
  partway through**, since neither the intermediate C stack frames a
  `longjmp` skips nor codegen's own hand-written JSON-parsing functions
  ever go through normal scope-exit tracking. Both are error-path-only
  (a successful run leaks nothing) and bounded, never unbounded or
  accumulating. A real fix would mean exception-safe cleanup for values
  built mid-expression-evaluation generally — this language has no
  RAII/unwind-table story at all today, and building one is a
  genuinely large undertaking.

## Deliberate behavior (documented, not planned work)

- **Array indexing is not bounds-checked** — a performance choice, see
  [api.md](api.md#indexing-is-not-bounds-checked).
- **`keyDown` auto-repeats while held** (that is how text entry works);
  a held key still fires exactly one `keyUp`. Track held keys yourself
  for edge-triggered input.
- **`regex(pattern, flags)` is memoized per call site** — the runtime
  compares the actual pattern+flags against the site's last
  compilation, so a repeated pattern costs what a literal does (~24x
  cheaper than recompiling) and a changed one recompiles. One site
  *alternating* patterns still recompiles per change — see
  [api.md](api.md#literals-are-compiled-once-regex-is-memoized-per-call-site).
