# Changelog

All notable changes to Festina are tracked here, starting from version
0.1. Versions follow `major.minor` (a `major.minor.patch` form is used if
a patch-only release is ever needed); dates are in `YYYY-MM-DD`.

This changelog starts from the point version tracking was introduced —
it is not a reconstruction of the project's earlier history. The full
round-by-round design and implementation record predating 0.1 lives in
[claude.md](claude.md).

## [0.26] - 2026-09-01

### Added

- **Live connection hand-off: `NAME.giveRequest(r)`.** The main
  program, having accepted a live request on its own port, may hand
  it directly to a thread -- that thread's own `on request` fires for
  it, on the connection's own live socket. Legal only from main, only
  onto a thread that has declared its own `on request`, and only for
  a manually-managed `http?` value (reusing `T?` rather than a new
  compile-time move-checker). First cut: plain (non-TLS) connections
  only.

See claude.md #213 for the full design, including a real
ThreadSanitizer-caught data race found and fixed during this phase's
own verification.

## [0.25] - 2026-09-01

### Added

- **Private per-thread HTTP context.** A `thread { }` may now declare
  `on request(req:http)`/`on upgrade(s:socket)`/
  `on socketMessage(s:socket, msg:blob)`/`on socketClose(s:socket)` --
  the identical four handlers the main program's own top-level HTTP/
  WebSocket support already has -- and, once it has declared at least
  one, call `openPort()`/`closePort()`/`openSecurePort()`. This gives
  the thread a fully private connection table and listener set, never
  shared with the main program's own HTTP context or with any other
  thread's, so a program can serve real, concurrent traffic on more
  than one port from more than one OS thread with no coordination
  needed between them. The blocking http client form (`req.send()`
  with zero arguments) also works from inside a thread body, targeting
  any other context's port; a thread must never target its own
  listener from inside that same thread (a documented, structural
  deadlock, not a bug) -- see api.md's new "Per-thread HTTP context"
  section.

See claude.md #212 for the full design, including the `__thread`
conversion of `festina_runtime_http.c`'s own connection/listener/
handler state and a real leak this phase's own sanitizer verification
caught and fixed.

## [0.24] - 2026-09-01

### Added

- **Wider builtin access inside a `thread { }` body.** `regex()`,
  `mkdir()`, and `ls()` are now callable from inside a thread's own
  handlers and private funcs, alongside the blocking, 1-argument
  `exec(args)`. Each is safe with zero runtime changes: `regex()`'s
  memoization slot is a per-call-site codegen global, never shared
  between threads; `mkdir()`/`ls()` are thin, purely local POSIX
  wrappers; `exec(args)`'s `fork()`/`execvp()`/`waitpid()` only ever
  touches the calling thread. The non-blocking, 2-argument
  `exec(args, callback)` form stays rejected inside a thread body —
  its callback always runs on the main program's own OS thread
  regardless of which thread dispatched it, a genuine cross-thread
  isolation violation if allowed.

See claude.md #211 for the full design, including how this was
verified against the actual C runtime rather than assumed from names
alone.

## [0.23] - 2026-09-01

### Added

- **Thread-private helper functions.** A `func` declared directly in a
  `thread { }` body's own top level (a sibling of its state vars/
  `on load`/`on message`/`on exit`) is now callable from that one
  thread's own handlers and other private funcs, with direct read/
  write access to that thread's own state. Two private funcs may call
  each other regardless of declaration order. An ordinary top-level
  `func` remains completely uncallable from inside a thread body,
  unchanged. Each thread pool instance gets its own independent copy
  of every private func, closing over that one instance's own state.

See claude.md #210 for the full design.

## [0.22] - 2026-09-01

### Added

- **`thread NAME[N] { ... }` -- thread pools.** Declares `N` fully
  independent instances of the same thread body, each its own OS
  thread, private state, and inbound queue. Addressed with `NAME[i]`
  (any `int` expression) everywhere a singleton thread's own bare
  `NAME` would be used — `pool[i].postMessage(x)`/`.kill()`/
  `.live(callback)`/`.isAlive()` all work identically to the singleton
  form, just per-instance. An out-of-range index is a silent no-op,
  matching `NAME.isAlive()`'s own established "test, don't fail"
  convention rather than crashing or raising.

See claude.md #209 for the full design.

## [0.21] - 2026-09-01

### Changed

- **`thread` messaging is now a single, unified model** — replaces
  per-thread `NAME.onMessage(callback)` registration with one global
  top-level `on message(worker:thread, msg:T)` handler that declares
  its own message type directly. A thread's own inbound handler now
  uses the identical `(worker:thread, msg:T)` shape (previously
  `on message(p:T)`); `worker` identifies the sender and is `null`
  when the message was sent by the main program. Threads may now
  message each other directly via `NAME.postMessage(x)` from inside
  another thread's own body (lifecycle control — `kill()`/`live()`/
  `isAlive()` — remains main-program-only). **Breaking:** any program
  using `NAME.onMessage(...)` or a thread's old single-parameter
  `on message(p:T)` needs updating to the new form.
- **The top-level WebSocket frame handler is renamed from
  `on message(s:socket, msg:blob)` to `on socketMessage(s:socket,
  msg:blob)`** — frees the `on message` name for the unified messaging
  model above; behavior is unchanged, only the name. **Breaking:**
  update any `on message(s:socket, ...)` declaration to
  `on socketMessage`.

See claude.md #208 for the full design and migration notes.

## [0.20] - 2026-09-01

### Fixed

- **A `thread` with its own `DatabaseURL` now closes its private
  sqlite handle on `kill()`** — previously, a `kill()`/`live()` cycle
  reopened a fresh handle every time without closing the old one (a
  real, small, per-cycle leak: one `sqlite3*` plus an open fd).
  Confirmed via a real LeakSanitizer report before the fix and a clean
  one after (`tests/stress/thread_db_kill_live_churn.f`). See
  claude.md #207.

## [0.19] - 2026-09-01

### Fixed

- **`.toStruct(T)`/`.toArr(T)` now decode `\u` unicode escapes** —
  previously threw `"... not yet supported"` on any JSON string
  containing a `\uXXXX` escape, including a UTF-16 surrogate pair
  (astral-plane codepoints); raw, un-escaped UTF-8 bytes were always
  fine, but a producer that specifically `\u`-escaped non-ASCII text
  could never be parsed. Now decodes both BMP codepoints and surrogate
  pairs into their real UTF-8 encoding, with a clear, catchable throw
  for malformed input (an unpaired surrogate, invalid hex, a truncated
  escape). See claude.md #206.

## [0.18] - 2026-09-01

### Fixed

- **A real heap-use-after-free**: assigning an ordinary, automatically-
  managed struct value into a manually-managed (`T?`) enum binding
  (`enum Shape = Circle; Shape? shape; ...; shape = c` for an existing
  `Circle c`) compiled without error and left `shape` dangling once
  `c` went out of scope and its own automatic release freed it —
  `check_assignable`'s enum member-coercion rule never accounted for
  `manually_managed`. Now correctly rejected as a type mismatch; a
  *fresh* member value (`Shape? shape = makeCircle()`) is unaffected.
- A manually-managed `blob?`/`regex?` thread-message parameter's own
  method calls (`p.write(...)`, `p.test(...)`) inside `on message`
  could fail to compile, or compile to invalid LLVM IR — a handful of
  exact-equality type checks in codegen.py (predating `T?`) never
  accounted for the flag, and `regex`/`http`/`socket` were never
  taught that a manually-managed instance can now reach code paths an
  ordinary one never could. Fixed; see claude.md #205.

### Added

- Real per-type test coverage for a manually-managed value crossing a
  `thread` boundary — `arr[T]`/`map[T]`/`enum`/`img`/`blob`/`aud`/
  `regex`/`url` each get a dedicated compile-and-run round-trip proof
  (`tests/test_manually_managed.py::TestThreadReferenceSharingPerType`),
  alongside struct's own existing one.

## [0.17] - 2026-09-01

### Fixed

- A manually-managed (`T?`) declaration's own initializer can now be a
  **fresh construction** of the matching plain type — a `regex`/
  `arr[T]`/`map[T]` literal, a `regex()` call, or any function call at
  all (including a struct-returning factory function). Previously
  `regex? r = /pattern/`, `arr[int]? xs = [1, 2, 3]`, and
  `Circle? c = makeCircle()` were all compile errors, since a fresh
  literal or a call's own return value always infers as the plain,
  unflagged type and `T?`/`T` are genuinely non-interchangeable. Safe
  because a freshly-constructed value has no other binding referencing
  it yet — reading an *existing* plain binding into a `T?` position is
  still, correctly, rejected. See
  [api.md](api.md#t-manually-managed-values).

## [0.16] - 2026-09-01

### Added

- `postMessage`/`on message` now share the raw reference for a
  manually-managed (`T?`) value crossing a `thread` boundary, instead
  of deep-cloning it like every other value type — a mutation made on
  one side is visible on the other, since it's the identical
  underlying value, not a copy. Sound because nothing on either side's
  automatic bookkeeping ever touches a manually-managed value's
  refcount, so there is nothing for two threads to race on.
- A manually-managed parameter can now be `free`d — it was never
  "borrowed" the way an ordinary parameter is, since nothing
  auto-manages it on either side of a call.

See [claude.md #203](claude.md) for the full design and implementation
record.

## [0.15] - 2026-09-01

### Added

- `T?` — a trailing `?` after a type at a variable/parameter
  declaration opts that one binding out of automatic memory management
  entirely (no retain on alias, no release at scope exit or
  reassignment). `free`/`delete` work unchanged and become the *only*
  release it ever gets. A genuinely distinct type from `T` (mirroring
  `amor arr[T]`'s own relationship to plain `arr[T]`) — no implicit
  decay either direction. Applies to struct/`arr[T]`/`map[T]`/`enum`/
  `blob`/`img`/`aud`/`http`/`socket`/`url`/`regex`; accepted but inert
  on `int`/`float`/`bool`/`text`/`color`/`font`/`table`. `arr[T?]`, a
  `T?` struct field, a `T?` return type, and `const T? x` are all
  compile errors this round. See [api.md](api.md#t-manually-managed-values).

### Fixed

- A user-defined function with a manually-managed parameter
  (`func f(p:Circle?)`) was permanently uncallable — the call-site
  argument check re-derived the parameter's type without its own `?`,
  rejecting the one argument type that could ever match it.
- `blob?`/`img?`/`aud?` failed to parse at all, misrouted into the
  unrelated anonymous `.callback()` form.
- `.test()`/`.play()`/`.playLoop()`/`.stop()`/`.isPlaying()`/`.send()`/
  `.clip()`/`.resize()`/`.getPixelColor()`/`.save()`/`.saveCopy()` and
  the `text -> blob` coercion stopped recognizing a manually-managed
  receiver of the matching type, via several pre-existing exact-equality
  type checks never meant to distinguish more than one shape of blob/
  regex/img/aud/http/socket.
- A grammar-ambiguity disambiguation helper (`Circle? c` vs. a bare
  ternary statement) used an absolute token index where a relative
  offset was passed in, silently misrouting a `T?` declaration found
  anywhere but the very first statement of a file.

See [claude.md #202](claude.md) for the full design and implementation
record. Crossing a `thread` boundary with a manually-managed value
(`on message`/`postMessage`) is not yet supported — planned follow-up
work, not a permanent restriction.

## [0.14] - 2026-08-31

### Documentation

- Final consolidation pass closing out `thread NAME { ... }` (claude.md
  #195-#201): api.md's own "Threads" section no longer calls itself
  "an early phase" and now documents the thread-private-helper-function
  restriction it was missing; `todo.md` gains three previously-untracked,
  already-true open items (singleton threads, no thread-private helper
  functions, a thread's own sqlite handle not explicitly closed on
  `kill()`); `README.md`'s own stale top-level test count corrected.
  No functional changes. See [claude.md #201](claude.md).

## [0.13] - 2026-08-31

### Fixed

- Documented that a `thread`'s own `on exit(code:int)` always receives
  `code` `0` — including when torn down by main-thread death — never
  the process's own real exit code. Not a behavior change (this is
  what the runtime already did); found and clarified while verifying
  `thread` process-exit interaction end to end. See
  [api.md](api.md#threads) and [claude.md #200](claude.md).

## [0.12] - 2026-08-31

### Added

- A `thread`'s own first statement may be `DatabaseURL = '<literal>'`,
  giving it a private SQLite handle — never shared with the main
  program or any other thread — so it may call `sqlite()`/
  `sqliteInt()`/`sqliteFloat()`/`sqliteText()` (a thread that didn't
  declare one still may not). A compile-time check rejects two
  contexts (a thread and the main program, or two threads) that would
  resolve to the same database file, main program's own default
  included. See [api.md](api.md#threads) and
  [claude.md #199](claude.md).

### Fixed

- A real (if narrow) data race, found while building the above: the
  media-decoder registration in `main()`'s own prologue used to run
  after every declared `thread` was already spawned, and the literal-
  SQL prepared-statement cache (claude.md #113) had no synchronization
  at all — both harmless as long as only the main thread ever queried
  SQLite, which per-thread `DatabaseURL` is what first breaks. Fixed
  before either was ever reachable; verified race-free under
  ThreadSanitizer.

## [0.11] - 2026-08-31

### Added

- `thread`'s own message types widen to include `blob`/`img`/`aud`/
  `url` — each deep-cloned (never shared) across the boundary, the
  same guarantee `struct`/`arr[T]`/`map[T]`/`enum` already got.
  Drawing/clip/resize/pixel methods on an `img` value (e.g.
  `pic.drawRect(...)`) work from inside a thread body, verified
  race-free under ThreadSanitizer. See [api.md](api.md#threads) and
  [claude.md #198](claude.md).

## [0.10] - 2026-08-31

### Added

- `thread`'s own message types widen to `struct`/`arr[T]`/`map[T]`/
  `enum` — each deep-cloned (never shared) across the boundary,
  built recursively from any mix of `int`/`float`/`bool`/`text`/
  `color`/`font`. A self-referencing (cyclic) `struct`/`arr[T]`/
  `map[T]` type is rejected with a clear error, not a hang; `blob`/
  `img`/`aud`/`url` message types remain not yet implemented. See
  [api.md](api.md#threads) and [claude.md #197](claude.md).

### Fixed

- A fresh, with-initializer `enum`-typed local variable — `Choice c
  = someExpr` declared directly inside a loop or function body, as
  opposed to reassigning an already-declared one — was never freed
  at scope exit, leaking its own box (and, for a boxed `text`
  member, that buffer too) on every declaration. The identical gap
  is fixed for `http`/`socket`/`url`-typed locals, found by the same
  audit.
- `NAME.postMessage(x)`, when `x` coerces into a compound message
  type (e.g. a `text` literal posted against an `enum` inbound
  type), no longer frees the coerced result with the wrong release
  function.

## [0.9] - 2026-08-31

### Added

- `thread NAME { ... }`: an isolated background worker with its own
  OS thread, its own private state, and message queues to and from
  the main program. `on load()`/`on message(p:T)`/`on exit(code:int)`
  handlers; `NAME.postMessage(x)`/`NAME.onMessage(callback)` for
  message passing (`int`/`float`/`bool`/`text` today); `NAME.kill()`/
  `NAME.live(callback)`/`NAME.isAlive()` for lifecycle control. Every
  message crossing the boundary is a deep, independent copy — no two
  threads ever share a mutable value. A thread's own body can see
  only its own state/locals, function names, and type names, never a
  global variable/constant or an ordinary top-level function call.
  See [api.md](api.md#threads) and
  [claude.md #195](claude.md)/[#196](claude.md).

## [0.8] - 2026-08-31

### Security

- The HTTP server no longer hangs or aborts on two malformed inputs
  reachable from a single unauthenticated request: a chunked
  `chunk-size` near 2^64 (which overflowed size arithmetic into an
  infinite buffer-growth loop) and a WebSocket frame declaring a
  ~16-exabyte payload (which reached a failing `malloc` that aborted
  the process). Both are now rejected against the existing 8MB cap.
  The chunked-decoder fix also protects the `req.send()` client
  parsing a hostile server's response.
- `.toStruct()` no longer overflows the stack on deeply nested JSON in
  an unknown field (reachable via `req.toStruct()` on a network body);
  nesting past 1000 levels now throws the same catchable error every
  other malformed input does.

### Fixed

- A value assigned to a global (or otherwise escaping) only inside a
  `try` or `catch` body is no longer freed while still referenced —
  escape analysis didn't look inside try/catch bodies, so such a value
  was stack-allocated and reclaimed at scope exit (a use-after-free).
- A refcounted local (array, map, struct, text) declared before a
  `try` that throws is no longer double-freed. A throw caught in the
  same function freed that local, and the normal scope exit after the
  catch freed it again (glibc aborted with "double free detected").
- The two branches of a `?:` are now required to have the same type;
  a mismatch (e.g. `c ? 'text' : someBlob`) used to compile and render
  garbage at runtime. `null` is still allowed in either branch.
- `&&` and `||` now require bool operands, matching `if`/`while`
  conditions; `1 && 2` used to compile and print `null`.
- A `?:` with a `null` branch (`c ? 1 : null`, `c ? null : 7`) now
  compiles — it produced invalid IR or crashed the compiler before.
- Passing a text literal or template to an `img`/`blob`/`aud`
  parameter (`show(`sprite${n}.png`)`) no longer corrupts the heap or
  leaks the loaded handle — the argument coercion mishandled the
  freshly minted handle's ownership.
- An `arr[blob]`, `arr[img]`, or `arr[aud]` no longer leaks its
  elements when the array is released — element handles were freed as
  a plain buffer with no per-element release.
- `.push()`/`.unshift()`/`.indexOf()` of a path string into an
  `arr[blob]`/`arr[img]`/`arr[aud]` no longer leaks the loaded handle.
- Interpolating a freshly built container into a template
  (`` `${make()}` ``, `` `${[1,2,3]}` ``) no longer leaks the
  container — only the rendered text was being freed.

- Integer fields in `.toStruct()` keep full 64-bit precision. They
  were parsed through a `double`, silently corrupting any value past
  2^53 — and `INT64_MAX` in particular read back as `null`.
- A finite float equal to nearly `DBL_MAX` renders as its own value in
  JSON output instead of `null` (the NaN/Infinity guard used a literal
  slightly below `DBL_MAX`).
- `img.getPixelColor()` on a JPEG-loaded image returns the real color
  instead of `null` for every pixel (JPEG surfaces store no alpha
  channel; the reader had treated the unused byte as alpha 0).
- A malformed WAV file (sample-rate 0) is now a normal load failure
  instead of cascading into a shutdown of all other playing audio with
  a misleading "no audio device" error.
- Several catchable-error paths no longer leak: a failed `fetch`
  response (up to 8MB per failed request, once per retry), an invalid
  `parseURL` port (~5 allocations per call), and a corrupt JPEG
  decode (its decoded surface, via a `setjmp`-clobbered local).

- Long-running loops that declare locals (a struct, an array, any
  variable) no longer overflow the stack. Codegen emitted each
  `alloca` at its declaration site — inside the loop body — so every
  iteration permanently grew the stack until the function returned;
  a loop declaring a six-field struct segfaulted at roughly
  150,000–300,000 iterations with flat heap usage. Every static
  alloca is now hoisted to its function's entry block: one slot per
  declaration, reused each iteration (verified to 3,000,000
  iterations). Locals are still re-zeroed at their declaration site
  every iteration, so behavior is otherwise unchanged.

See [claude.md #191](claude.md) for the full diagnosis.

## [0.7] - 2026-08-29

### Changed

- `.toText()` JSON-style rendering (`log()`/template interpolation of a
  struct, table row, `arr[T]`, or `map[T]`, and explicit `.toText()`
  calls on any of them) is faster, with no change in output. Every
  compile-time-known literal the renderer appends (JSON punctuation, a
  struct field's own baked `"name":` key) now skips a runtime
  `strlen()` rescan of a length the compiler already knew, and string
  escaping now bulk-copies runs of bytes that don't need escaping
  instead of handling one byte at a time. Measured ~2.5x faster
  (~215ms → ~85ms median over 5 runs) on a 100,000-iteration
  text-heavy struct-rendering benchmark.

See [claude.md #190](claude.md) for the full measurement methodology.

## [0.6] - 2026-08-29

### Added

- `getPixelColor(x, y)`: reads one pixel back off the canvas as a
  `color` — `null` for a coordinate outside the canvas, or a fully
  transparent pixel. Correctly undoes Cairo's premultiplied alpha, so
  a pixel painted under `fillAlpha` reads back as the color that was
  actually painted, not one darkened by the alpha in effect at the
  time.
- `img.getPixelColor(x, y)`: the same, reading an `img`'s own surface.

### Fixed

- `color == null` (and `!=`) generated invalid LLVM IR and failed to
  compile at all, for any program that tried it — entirely
  independent of the above. `color` is an `i64`-shaped value, and the
  bare `null` literal was routed through the "null" *pointer* keyword
  unconditionally. Now resolves to `color`'s own existing `-1`/'none'
  sentinel, the same value an uninitialized `color` already reads as.

See [api.md](api.md#drawing-is-offscreen-render-puts-it-on-screen).

## [0.5] - 2026-08-27

### Added

- `Math.floorDiv(a, b)`: integer division rounding toward negative
  infinity (unlike `/`'s own truncate-toward-zero), for tile/grid
  calculations that previously needed `Math.floor(a / b)` spelled out
  by hand.
- `blankImage(w, h)`: a fresh, fully-transparent `img` at a given
  size, with no existing image or canvas needed to derive it from.
- `row.rowid`: a table row's own SQLite identity, read-only — only
  populated when the query's own SQL explicitly selects `rowid`.
- `drawRect`/`drawCircle` (and their `img`-method equivalents) accept
  a further optional trailing `borderColor` argument, after the fill
  color — overrides it for that one call only. `drawCircle` also
  gains the fill-only override it previously lacked entirely.

### Documentation

- Clarified that the canvas's own real alpha channel is only real
  off-screen — a transparent region reads back as opaque white once
  `render()` puts it on screen, even though the same content saved via
  `saveCanvas()` still carries its real alpha.

See [api.md](api.md#types) and
[api.md](api.md#drawing-is-offscreen-render-puts-it-on-screen).

## [0.4] - 2026-08-27

### Added

- `arr[T].sort(cmpFn)`: an in-place, stable, comparator-based sort —
  `cmpFn:func[T,T]:int`, JavaScript's/C `qsort()`'s own convention.
  See [api.md](api.md#sorting-sortcmpfn).
- `drawImage(img, x, y, w, h)`: scales the whole image to fit a `w`×`h`
  box, without mutating the source image the way `img.resize()` does.
- `drawImage(img, sx, sy, sw, sh, dx, dy, dw, dh)`: the full
  canvas-style form — a source rect cut out of the image, scaled into
  a destination rect. See
  [api.md](api.md#drawing-is-offscreen-render-puts-it-on-screen).
- `map[T].keys()`/`map[T].values()`: a plain, independent snapshot
  array (`arr[text]`/`arr[T]`), no callback needed — sidesteps
  `.forEach()`'s bare/no-closures callback restriction for the common
  "collect entries matching a condition" case.

## [0.3.1] - 2026-08-27

### Fixed

- `fillAlpha` had no effect on `drawImage` — two images drawn back to
  back under different `fillAlpha` values came out pixel-for-pixel
  identical. `drawImage` now respects it, blending the image into
  whatever's underneath exactly like every other draw call. See
  [api.md](api.md#gradients-and-transparency).

## [0.3] - 2026-08-27

### Added

- `on mouseWheelUp(x:int, y:int)`/`on mouseWheelDown(x:int, y:int)`:
  scroll wheel events, split by direction. Fixes a real pre-existing
  bug on X11/Linux where scrolling the wheel silently also fired a
  spurious `mouseDown`+`mouseUp` pair.
- `devicePixelRatio:float`: a read-only global reporting the display's
  pixel density (`1.0` normally, ~`2.0` on Retina/HiDPI).
- `showCursor()`/`hideCursor()`: toggle the mouse cursor's visibility
  over the canvas.
- Real right-click and middle-click support on macOS and Windows —
  previously only the left button worked on either platform.

### Changed

- **Breaking:** `on mouseDown`/`on mouseUp` now require a third
  argument, `button:int` — `on mouseDown(x:int, y:int, button:int)`.
  `1` = left, `2` = middle, `3` = right, `8` = back, `9` = forward.
  `on mouse` (continuous movement) is unaffected.

See [api.md](api.md#mouse-events) and
[api.md](api.md#drawing-is-offscreen-render-puts-it-on-screen).

## [0.2.3] - 2026-08-27

### Added

- `enterFullscreen()`/`exitFullscreen()`: toggle true OS fullscreen on
  the graphics window. See
  [api.md](api.md#drawing-is-offscreen-render-puts-it-on-screen).

### Changed

- The graphics window is now fully decorated — a title bar and the
  OS's normal minimize/maximize/close controls, resizable by dragging
  an edge — instead of the previous borderless, canvas-only look.

## [0.2.2] - 2026-08-27

### Fixed

- A program calling `setClientWidth`/`setClientHeight` near the top of
  its own boot sequence briefly opened a real, on-screen window at the
  hardcoded 800×600 default before correcting itself, since the window
  used to open (and its size reset back to that default) before the
  program's own top-level code ever ran. The window now opens lazily,
  after any such call has already taken effect, directly at the
  requested size. See [api.md](api.md#graphics).

## [0.2.1] - 2026-08-26

### Fixed

- The macOS and Windows windowing backends could present stale or blank
  pixel content — most visibly, `img.clip()` (including the
  `saveCanvas().clip(...)` idiom) reliably showing the clipped region
  only on its first use in a process. Both backends now flush the
  surface before reading its pixels directly, as Cairo's own API
  requires.
- Assigning a field on a manually-declared table row (not obtained from
  a query) segfaulted. A table row is a borrowed handle onto one row of
  a query result, not an independently constructible value — declaring
  one with no initializer is now a clear compile-time error, pointing
  at `struct` as the way to build a value by hand
  (see [api.md](api.md#structs-as-query-targets)).

### Documentation

- Clarified that `on ...` event handlers are active as soon as they're
  declared, regardless of position in the file — the same hoisting
  `func` declarations already get. See
  [api.md](api.md#graphics).

## [0.2] - 2026-08-26

### Added

- `exec(args, callback)`: a non-blocking counterpart to `exec(args)`.
  Dispatches the same spawn to a background worker thread and returns
  immediately; `callback:func[int]:void` receives the real exit code once
  the child process exits. See [api.md](api.md#running-other-programs).

## [0.1] - 2026-08-26

### Added

- Version tracking: `festina.__version__`, and `festina --version` on the
  CLI.
- This changelog.

### Changed

- Project documentation (`README.md`, `api.md`, and every other `.md`
  file except `claude.md` and `tests/CONTRACT.md`) rewritten to describe
  the software as it stands today, rather than narrating how it got
  there.
