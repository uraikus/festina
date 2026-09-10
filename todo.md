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
  (claude.md #252):** `match` and the lex/parse cache (#253) both
  shipped; the cycle-collector item was measured, not built (#254, see
  Memory model below). One item left, and reviewing it found its own
  stated rationale doesn't hold up:
  - **A raw byte-buffer type** (a generalized, writable `blob`, or a
    new `bytes` type, with `[i] =` assignment and
    `text.toBytes()`/`bytes.toText()` conversions at the boundary).
    Previously cited a claude.md #251 "sketch" as prior art — that
    sketch does not exist; #251 is entirely about `.length`, unrelated.
    The one concrete justification ("useful once/if something wants to
    skip shelling out to clang on textual LLVM IR") is also already
    true today, independent of any byte-buffer type: `llvm_backend.py`
    parses the generated IR text in-process via libLLVM's C API
    whenever it's available, with `clang`/`cc` only a fallback when
    it's not. And the in-place string-append work (#243) already made
    building that IR text cheaply mutable as a plain `text`. Left open
    since a mutable, indexable byte buffer could still be useful on its
    own merits (binary protocol/data construction) — but not on the
    self-hosting-compiler premise this bullet used to rest on, and full
    new-primitive-type surface area (lexer through runtime) is a real
    cost against a currently-unmotivated feature.

## Memory model

Automatic reclamation is escape analysis plus reference counting.
Most managed types (`struct`/`arr[T]`/`map[T]`/`ascii`/`img`/`aud`/
`regex`/`blob`/`http`/`url`/`socket`) carry a refcount header;
`text` is the exception — it has no header at all and is instead
copied on alias and freed outright (claude.md #83, and #256 for why a
header cannot be added to it). Reference cycles are collected by trial
deletion, with `free`/`delete` as the manual override. What remains
open:

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
- **A table row bound off a call-result array leaks the array**
  (`People p = rows()[0]`, a row passed as an argument, or one
  returned). Reading a COLUMN off such a row — `rows()[0].name`, the
  shape this was originally reported as — is fixed (claude.md #260):
  the array is parked on the enclosing member chain and released once
  the column that escapes has been copied. What is left is the shapes
  with no such chain to drain it, where the row itself is what escapes.
  Rows have no refcount header — the array owns them outright — so the
  row cannot be retained past its container, and binding it to a name
  first still reclaims normally. claude.md #224 scoped what those
  shapes need and it is unchanged: a per-table row-copy function
  (straightforward — it mirrors the existing per-table row-release
  function, using the already-existing `festina_text_own`/
  `festina_retain` primitives column-by-column), paired with genuine
  scope-exit ownership tracking for `TableType` locals generally, the
  same "always owned once bound, always released at scope exit"
  symmetry `text` itself needed six dedicated, individually-verified
  rounds to get right (claude.md #11-16).
- **`X().someBlob.length` leaks the object the blob came from**, and
  the obvious one-line fix would make it a double free. The `.length`
  branch drains its parked member chain only for an *array* receiver
  and drops it for `blob`/`text`/`ascii` ones (measured: 201
  allocations over 200 iterations). The drop is currently masking an
  over-release in the other direction:
  `_is_owning_refcounted_source(X().someBlob)` answers True — a chain
  whose base is a `Call` — while the inner `_emit_member_load` link
  never actually minted anything, so `_release_owned_receiver` releases
  a blob it does not own, and gets away with it only because the leaked
  object's cascade never runs to release it a second time. Draining the
  parked entry without also fixing that predicate mismatch converts the
  leak into a use-after-free. The real fix is to route those branches
  through `_release_member_chain` (whose own filter excludes
  never-minted intermediate links by construction) — but `text` and
  `ascii` receivers are not in that filter's refcounted family at all,
  so each needs its own treatment. Found while closing #260; a
  predicate-alignment round of its own, not a patch.
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
