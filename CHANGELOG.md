# Changelog

All notable changes to Festina are tracked here, starting from version
0.1. Versions follow `major.minor` (a `major.minor.patch` form is used if
a patch-only release is ever needed); dates are in `YYYY-MM-DD`.

This changelog starts from the point version tracking was introduced —
it is not a reconstruction of the project's earlier history. The full
round-by-round design and implementation record predating 0.1 lives in
[claude.md](claude.md).

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
