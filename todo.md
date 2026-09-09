# Roadmap

Open work only, shortest useful form. The reasoning behind every closed
item lives in [claude.md](claude.md) (the numbered decision log) and
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
  handlers itself (claude.md #238, [windows.md](windows.md)).

Compiling to `wasm32-wasi` is supported and CI-verified — see
[wasm.md](wasm.md) — and a compiled `.wasm` runs in a browser tab on
this project's own WASI host (`runtime/wasm/browser.html`, verified in
headless Chromium on every push). Graphics/audio are out of scope there
permanently (WASI has no backend for either); what remains open, not
blocking: AddressSanitizer/LeakSanitizer coverage for the target.

## Language & standard library

- **Media formats** stay PNG/JPEG + WAV/MP3, deliberately: each new
  format is a new system dependency for every machine that compiles a
  media-using program. Revisit only with a concrete need.
- **Self-hosting-compiler ergonomics, roadmapped alongside `match`
  (claude.md #252) but not started:**
  - **A raw byte-buffer type** (a generalized, writable `blob`, or a
    new `bytes` type, with `[i] =` assignment and
    `text.toBytes()`/`bytes.toText()` conversions at the boundary —
    sketched in the same conversation as claude.md #251's own "what
    would a raw byte implementation look like" answer). Full new-
    primitive-type surface area, lexer through runtime. Only useful
    once/if something wants to skip shelling out to clang on textual
    LLVM IR, which the in-place string-append work (#243) already
    makes cheap without it — not an obvious near-term need.

## Memory model

Automatic reclamation is escape analysis plus reference counting —
every managed type (`struct`/`arr[T]`/`map[T]`/`text`/`img`/`aud`/
`regex`/`blob`/`http`/`url`/`socket`) carries a refcount header, and
reference cycles are collected by trial deletion, with `free`/`delete`
as the manual override. What remains open:

- **Cycle trials are synchronous and per-release** — every
  still-referenced release of a cycle-capable type walks the value's
  reachable subgraph. Fine for ordinary object graphs (20k dropped
  21-node *disjoint* cycles in ~34 ms, claude.md #120) — but claude.md
  #254 measured the case that number never tested, *shared* structure
  under repeated release-while-live churn, and found a real, cleanly
  linear cost specifically tied to sharing (not just total node count):
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
- **A table-row element off a call-result array leaks the array**
  (`rows()[0]` where the elements are query rows). Rows have no
  refcount header — the array owns them outright — so the element
  cannot be retained past its container. Bind the array to a name first
  and it reclaims normally. claude.md #224 scoped the real fix: a
  per-table row-copy function is straightforward (mirrors the existing
  per-table row-release function, using the already-existing
  `festina_text_own`/`festina_retain` primitives column-by-column), but
  it only closes the leak once paired with genuine scope-exit ownership
  tracking for `TableType` locals generally — the same "always owned
  once bound, always released at scope exit" symmetry `text` itself
  needed six dedicated, individually-verified rounds to get right
  (claude.md #11-16). A real fix is that size, not a quick patch.
- **Text globals are not freed at process exit** — deliberate: they are
  reachable until exit, LeakSanitizer agrees, and freeing them would be
  exit-time busywork.
- **A `throw` out of a runtime-driven callback** (`.forEach(fn)`,
  `.sort(cmp)`, a timer) crosses the runtime's own C frame on the way
  to the catching `try`. Festina-side locals are released (claude.md
  #236); whatever that C frame itself held mid-operation — `qsort`'s
  scratch buffer, an iteration cursor — is not. Error-path-only,
  bounded per throw. (The general "intermediate frame" leak that used
  to be listed here is closed: claude.md #236.)

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
