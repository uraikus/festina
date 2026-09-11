# Roadmap

Open work only, shortest useful form. The reasoning behind every closed
item lives in [decisions.md](decisions.md) (the numbered decision log,
cited as `claude.md #N` throughout the repository) and
[tests/CONTRACT.md](tests/CONTRACT.md) (what is verified, and how).

## Platforms

Linux is the primary, fully verified target. macOS and Windows builds
exist, compile, and type-check against real platform headers in CI —
see [macos.md](macos.md) and [windows.md](windows.md) for exactly
what's supported on each. What remains open:

- **Audio playback on a real Mac and a real Windows machine, and
  windowed mouse/keyboard/window behavior on a real Mac.** Each stays
  behind an explicit opt-in env var (`FESTINA_ENABLE_MACOS_AUDIO`/
  `_GRAPHICS`, `FESTINA_ENABLE_WINDOWS_AUDIO`) until confirmed on real
  hardware. Windows windowing needs no hardware: the CI job opens a
  real Win32 window and drives its mouse, keyboard, resize and close
  handlers itself (see [windows.md](windows.md)).

Compiling to `wasm32-wasi` is supported and CI-verified — see
[wasm.md](wasm.md) — and a compiled `.wasm` runs in a browser tab on
this project's own WASI host (`runtime/wasm/browser.html`, verified in
headless Chromium on every push). Graphics/audio are out of scope there
permanently (WASI has no backend for either), and so, it turns out, is
sanitizer coverage: `clang --target=wasm32-wasi -fsanitize=address` is
rejected by the compiler outright, and the wasm32 compiler-rt package
ships only `builtins` — no sanitizer runtime exists for the target.
Nothing this project can work around, and nothing it needs to: every
allocation the native sanitizer runs exercise is the same C source a
wasm build compiles, whose entire `__wasi__` delta is non-allocating
stubs. Nothing open here.

## Language & standard library

- **Media formats** stay PNG/JPEG + WAV/MP3, deliberately: each new
  format is a new system dependency for every machine that compiles a
  media-using program. Revisit only with a concrete need.
- **A raw byte-buffer type** — a generalized, writable `blob`, or a
  new `bytes` type, with `[i] =` assignment and
  `text.toBytes()`/`bytes.toText()` conversions at the boundary. Open
  but unmotivated: the case usually made for it is skipping a shell-out
  to clang on textual LLVM IR, and that is already true without it —
  `llvm_backend.py` parses the generated IR in-process via libLLVM's C
  API whenever it is available, with `clang`/`cc` only a fallback, and
  in-place string append makes building that IR text cheap as a plain
  `text`. A mutable, indexable byte buffer could still earn its place
  on its own merits (binary protocol and data construction), but a full
  new primitive type costs surface area from the lexer through to the
  runtime, and nothing currently needs one.

## Memory model

Automatic reclamation is escape analysis plus reference counting.
Most managed types (`struct`/`arr[T]`/`map[T]`/`ascii`/`img`/`aud`/
`regex`/`blob`/`http`/`url`/`socket`/table rows) carry a refcount
header;
`text` is the exception — it has no header at all and is instead
copied on alias and freed outright — a live `text` pointer can be a
heap buffer, a bare `.rodata` literal, a borrowed environ pointer or an
X11 stack buffer, and a header would have to be valid for all four. Reference cycles are collected by trial
deletion, with `free`/`delete` as the manual override. What remains
open:

- **Cycle trials are synchronous and per-release** — every
  still-referenced release of a cycle-capable type walks the value's
  reachable subgraph. Fine for ordinary object graphs (20k dropped
  21-node *disjoint* cycles in ~34 ms) — but the case that number
  never tested, *shared* structure under repeated release-while-live
  churn, measures a real, cleanly linear cost specifically tied to
  sharing rather than to total node count:
  a shared ring costs ~9-10x a disjoint one at the same total node/
  iteration count, scaling linearly in both ring size and iteration
  count. The classic deferred-root buffer is the known optimization,
  now motivated by measurement rather than assumption. Still
  deliberately not started: the real algorithm needs the *free* path of
  every cyclic release wrapper to become buffering-aware too (a
  still-buffered node hitting refcount zero can't be freed immediately
  without leaving a dangling pointer in the pending-roots buffer) — new
  correctness-critical surface in code every struct/arr/map-using
  Festina program runs through, and batching still trades lower
  amortized CPU for higher peak memory (collection is delayed). Earns
  its own dedicated round: a fresh plan, and ASan/LeakSanitizer-under-
  stress verification of the deferred-free "zombie" path specifically.
- **Text globals are not freed at process exit** — deliberate: they are
  reachable until exit, LeakSanitizer agrees, and freeing them would be
  exit-time busywork.

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
