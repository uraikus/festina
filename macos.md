# macOS support

Native builds work today: toolchain, compiler, audio, graphics, and
packaging are all built and CI-verified on Apple Silicon (`macos-14`
GitHub Actions, on every push). What's gated on real-hardware
confirmation, and what's a genuine, permanent limitation, are covered
below.

## `try`/`catch`/`throw` works (since 0.43)

Earlier versions rejected `try`/`catch`/`throw` outright on darwin:
generated code called LLVM's SjLj intrinsics, which have no lowering
for AArch64 (`arm64-apple-macos14` — every current Mac and every macOS
CI runner). A `try` is a direct call to libc's own `_setjmp` now, and a
`throw` is libc's `longjmp` (claude.md #235), which Darwin has always
had — the CI job runs a real caught throw
(`tests/test_platform.py::TestOnMacOS::test_try_catch_works`). Nothing
about `try`/`catch` is gated or macOS-specific any more; see
[api.md](api.md#try--catch--throw) for the feature itself.

## What's gated on real hardware

Audio playback, windowed mouse/keyboard/window behavior, and
`openPort()`/HTTP each stay behind an explicit opt-in environment
variable until confirmed on real Mac hardware:

- `FESTINA_ENABLE_MACOS_AUDIO=1` — audio playback (AudioQueue)
- `FESTINA_ENABLE_MACOS_GRAPHICS=1` — windowed graphics (native Cocoa)
- `FESTINA_ENABLE_MACOS_HTTP=1` — `openPort()`/`on request`/`on
  upgrade`/`on message`/`on socketClose` (plain POSIX sockets, nothing
  Linux-specific about them, but not yet run against real macOS
  hardware) — see [api.md](api.md#http-and-websocket-servers)

Everything about each of these compiles and type-checks against real
platform headers (AudioToolbox/AppKit/Cocoa) on every CI push; only
actually opening a device/window/socket on real hardware is
unconfirmed. Offscreen drawing (`saveCanvas`, no open window) and
packaging need no gate at all and work today.

## Porting surface

The compiler is pure Python, the generated IR is target-neutral, and
the platform-specific API surface of the runtime is small and
precisely bounded:

| Area | Platform-specific calls | Where |
|---|---|---|
| Windowing/events | 20 X11 calls + 2 `cairo_xlib_*` | `festina_runtime_graphics.c` |
| Audio device | 6 ALSA calls (`snd_pcm_open/set_params/writei/recover/close`, `snd_strerror`) | `festina_runtime_audio.c` |
| Core runtime | none — POSIX only (`regex.h`, `clock_gettime`, `select`, pthread), all in macOS libc | `festina_runtime.c` |

Everything else the runtime leans on is portable and Homebrew-packaged:
Cairo (drawing), libjpeg, libmpg123, sqlite3 (macOS even ships it).

## Toolchain

- **`festina/llvm_backend.py` finds Homebrew's libLLVM** at its
  keg-only install path (`/opt/homebrew/opt/llvm/lib/libLLVM.dylib` on
  arm64, `/usr/local/opt/llvm/lib/libLLVM.dylib` on x86_64) —
  `ctypes.util.find_library("LLVM")` alone doesn't see it there. The
  clang fallback works too: Apple clang (Xcode ≥ 15, the floor) accepts
  the generated `.ll` directly, including its opaque-`ptr` IR.
- **SQLite links statically when possible.** `festina/cli.py` probes
  the explicit archive path (`pkg-config --variable=libdir sqlite3` +
  `/libsqlite3.a`, which Homebrew's sqlite provides) rather than the
  `-Wl,-Bstatic/-Bdynamic` flags ld64 rejects; falls back to
  `-lsqlite3` (the OS-shipped dylib) otherwise.
- **`festina doctor`** reports macOS-specific install hints: on darwin,
  compiling an audio program without `FESTINA_ENABLE_MACOS_AUDIO=1`
  fails with a message naming the gate (`_check_feature_supported`)
  rather than a pkg-config error naming a library macOS doesn't have.

## Audio

AudioQueue (AudioToolbox, plain C) sits behind a 3-function device seam
the portable pool/decoding/channel logic in `festina_runtime_audio.c`
calls through:

- `festina_pcm_open(channels, rate) → handle-or-error`
- `festina_pcm_write(handle, frames, count) → ok` (blocking)
- `festina_pcm_close(handle)`

N preallocated buffers plus a counting semaphore reproduce
blocking-push exactly: `write` copies frames into a free buffer,
enqueues it, and blocks on the semaphore when all buffers are in
flight; the completion callback posts it. Per-channel queues mirror
Linux's per-channel handles one-to-one. Link flags add `-framework
AudioToolbox`; the `alsa` pkg-config package is dropped on darwin
(`libmpg123` is kept for MP3 decoding). For CI, where there's no real
audio device, `FESTINA_AUDIO_NULL=1` makes `festina_pcm_*` a timed sink
at the shim level.

## Graphics

The graphics translation unit is mostly portable already: all drawing
(rects, circles, text, paths, transforms, gradients, images, clips,
resizes, `saveCanvas`) targets an offscreen Cairo image surface, and
libjpeg decoding is platform-free. Only the windowing layer is
platform-specific, behind `runtime/festina_runtime_window.h`:

- `festina_window_open(width, height, title)` / `festina_window_close(void)`
- `festina_window_present(cairo_surface_t *backing)` — hand the offscreen backing surface to the platform to blit
- `festina_window_events_wait(double timeout_seconds)` — block for at most one OS-native event, bounded by the next timer deadline
- `festina_window_events_drain(...)` — pump every pending OS event as a normalized `MOUSE_DOWN`/`MOUSE_UP`/`MOUSE_MOVE`/`KEY_DOWN`/`KEY_UP`/`RESIZE`/`CLOSE` event

macOS implements this in `festina_runtime_window_mac.m` (Objective-C, a
separate translation unit, linked `-framework Cocoa`): an `NSWindow`
plus an `NSView` whose `drawRect:` blits the Cairo image surface via
`CGImage` (`CAIRO_FORMAT_ARGB32` maps exactly onto
`kCGBitmapByteOrder32Little | kCGImageAlphaPremultipliedFirst` on any
little-endian Mac). No XQuartz, no X11 of any kind.

Three things worth knowing about the Cocoa backend specifically:

- **Event loop.** Cocoa requires UI on the main thread and prefers
  owning the loop; Festina's model (top-level code runs, then
  `festina_run_event_loop()` blocks on the main thread) is compatible.
  `festina_window_events_wait` peeks with
  `nextEventMatchingMask:untilDate:...dequeue:NO` (the timeout carries
  the timer deadline, the same role `select`'s timeout plays on Linux);
  `festina_window_events_drain` fully pumps the queue into a small ring
  buffer that window/view delegate callbacks (resize, close) also push
  into, then drains it through the caller's handler. Timers keep firing
  from the same loop, unchanged.
- **Key names match Linux's.** `on keyDown(key:text)` reports the same
  names on both platforms — Cocoa's characters/keyCodes are mapped
  through a small keyCode table onto the shared vocabulary pinned in
  `runtime/festina_key_names.h` (guarded by
  `tests/test_platform.py::TestKeyNameVocabulary`). Autorepeat maps
  cleanly onto NSEvent's `isARepeat`.
- **No Xvfb equivalent exists for macOS CI runners**, so windowed
  end-to-end tests (real mouse/keyboard dispatch) are Linux-CI-only;
  macOS CI covers the full offscreen suite plus a compile-only Cocoa
  type-check against real AppKit/Foundation/CoreGraphics headers. See
  tests/CONTRACT.md for the exact split.

A Festina program only hits the `FESTINA_ENABLE_MACOS_GRAPHICS` gate if
it would actually open a window (declares `render()` or any window
event handler); an offscreen-only program (`saveCanvas`, no
`render()`) is never gated on any platform.

## Packaging

`scripts/package_compiler.sh` produces a Mach-O binary via PyInstaller
and ad-hoc codesigns it (`codesign -s -`, guarded on `uname -s` and on
`codesign` being on PATH) so Gatekeeper allows local runs with no
prompt — a self-signature, not an identity; it doesn't make the binary
trusted on anyone else's machine. Full Developer-ID signing and
notarization is a distribution decision deliberately out of scope until
there's an actual distribution channel. The `macos-14` CI job runs this
script for real on every push (Apple Silicon runners, so that step *is*
the arm64 build) and smoke-tests the result (compiles and runs
`examples/hello.f`). Universal binaries are a non-goal — arm64 ships;
x86_64 without Rosetta is build-from-source. See [setup.md](setup.md)
for the exact brew line and PATH requirements.

## Shared with Windows

The intersection with [windows.md](windows.md):

1. **The audio device seam** (`festina_pcm_open/write/close`), with
   each platform providing its own blocking-push primitive (a counting
   semaphore for AudioQueue, a condition variable for waveOut, a
   blocking write on Linux/ALSA).
2. **The windowing seam** (`festina_window_open/close/present/
   events_wait/events_drain` + normalized events,
   `runtime/festina_runtime_window.h`) — Linux (X11), macOS (Cocoa),
   and Windows (Win32) all implement it; `present` always takes the
   Cairo *image surface* (an xlib surface / CGImage / DIB is each
   platform's own blit of that one thing), and redraw-on-expose is each
   backend's own job rather than a seam-level event.
3. **The key-name vocabulary** — `runtime/festina_key_names.h`, guarded
   by `tests/test_platform.py::TestKeyNameVocabulary`.
4. **The `FESTINA_AUDIO_NULL` test shim** at the device seam, for CI
   with no real audio device.
5. **The headless CI tier definition** — which suites run with no
   display/audio device; all three OS jobs use the same selection.
6. **The sanitizer tier**: LeakSanitizer is not reliably available on
   darwin/arm64, so the leak-stress tier stays Linux-only; memory-model
   verification runs strongest there.
7. **Per-platform structure**, filled in per port: `_RUNTIME_FEATURES`
   (pkgs/link flags/sources by OS), `_find_libllvm` candidate paths,
   `_default_output_name`, `_static_sqlite_attempt`, `festina doctor`'s
   hint table, and `package_compiler.sh`'s release matrix.
8. **Cross-platform contract tests** that run everywhere —
   binary-fidelity round trips, forward-slash path handling
   (`tests/test_platform.py::TestBinaryFidelity`), plus the per-OS
   `TestOnMacOS`/`TestOnWindows` suites.

Not shared: the regex gap and `.exe` mechanics are Windows-only, the
ld64 sqlite probe is macOS-only, and Cocoa's run-loop inversion has no
Win32 counterpart.
