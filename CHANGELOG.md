# Changelog

All notable changes to Festina are tracked here, starting from version
0.1. Versions follow `major.minor` (a `major.minor.patch` form is used if
a patch-only release is ever needed); dates are in `YYYY-MM-DD`.

This changelog starts from the point version tracking was introduced —
it is not a reconstruction of the project's earlier history. The full
round-by-round design and implementation record predating 0.1 lives in
[claude.md](claude.md).

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
