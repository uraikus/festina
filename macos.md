# macOS support — the plan

> **Status: Phase 0 is DONE and verified on real hardware** — the
> `macos-14` CI job is green (four rounds of real-Apple-Silicon
> iteration, claude.md #121–#122): the full suite passes natively, the
> four windowless examples compile and run, and the darwin branches of
> every toolchain seam are unit-tested from all platforms
> (`tests/test_platform.py`). The rounds also surfaced and fixed a
> genuine language portability bug — GNU regex escapes silently not
> matching under BSD libc — now solved by POSIX-class translation on
> every platform. Phase 1 is built and CI-compiled (the
> `festina_pcm_*` seam, the AudioQueue backend type-checked against
> real AudioToolbox headers, `FESTINA_AUDIO_NULL=1` as the
> cross-platform test sink); the darwin audio gate stays until
> real-hardware playback is verified (`FESTINA_ENABLE_MACOS_AUDIO=1`
> to try it on a Mac). Phase 2 is built and CI-compiled the same way
> (claude.md #123): the `festina_window_*` seam, a native Cocoa
> backend (`festina_runtime_window_mac.m`, type-checked against real
> AppKit/Foundation/CoreGraphics headers on every push), and offscreen
> graphics (`saveCanvas`, no window) unblocked on darwin with no
> XQuartz dependency at all — 2a's XQuartz stage turned out to be
> unnecessary and was skipped in favor of going straight to 2b, since
> the X11 backend's own real (Linux, Xvfb-based) test suite already
> gives 2b's design a verified behavioral reference to implement
> against. The darwin *windowed*-graphics gate stays until real
> mouse/keyboard/window behavior is verified on hardware
> (`FESTINA_ENABLE_MACOS_GRAPHICS=1` to try it on a Mac); offscreen
> drawing is never gated. Phase 3 is done: `scripts/package_compiler.sh`
> ad-hoc codesigns the binary it produces on darwin (`codesign -f -s -`
> — the `-f` matters, since recent PyInstaller already self-signs, see
> below), the macos-14 CI job packages, codesigns, and smoke-tests a
> real arm64 `festina` binary on every push, and [setup.md](setup.md)
> has a real macOS section. Full Developer-ID signing/notarization
> remains deliberately out of scope until there's an actual
> distribution channel.
>
> **The offscreen-graphics claim above got two real tests and failed
> both** (claude.md #126): `festina_runtime_graphics.c` had never
> actually compiled on real macOS hardware before Phase 2 swapped
> cairo-xlib for plain `cairo` (every earlier CI round skipped it as a
> missing dependency), and the very first time it did, `#include
> <cairo/cairo.h>` in the new windowing seam header couldn't find the
> file — Homebrew's cairo pkg-config `-I` flag points directly into the
> headers directory, unlike the implicit `/usr/include` search that
> quietly made the same include style work on Linux. Fixed to the
> portable `#include <cairo.h>` — except `festina_runtime_window_mac.m`
> turned out to have the identical line, independently, and only real
> macOS CI compiling THAT file (nothing else does) caught it on the
> very next run; fixed the same way. The packaging codesign step needed
> `-f`/`--force` for the unrelated PyInstaller self-signing reason
> above, and that same run found `tests/test_packaging.py`'s own
> "prove no system Python is needed" test had replaced PATH outright
> with a hardcoded `/usr/bin:/bin`, dropping Homebrew's bin directory
> where `pkg-config` actually lives — invisible on Linux, where
> pkg-config already lives in `/usr/bin`. Fixed to prepend rather than
> replace. All fixes are one or two lines each, verified against the
> real Linux cairo-xlib pkg-config flags and the full suite, but not
> yet reconfirmed by a real macOS CI run that gets all the way through.
>
> **The `festina_runtime_window_mac.m` include fix above was itself
> incomplete** (claude.md #126, round four): the file's `#include
> <cairo.h>` now resolved as a bare filename, but the file still
> failed to compile, because `_feature_extra_object` — the function
> that compiles this one Objective-C companion object — had always
> passed it an EMPTY pkg-config package list, on every round, so it
> never received cairo's `-I` cflags at all regardless of how the
> `#include` was spelled. The include-spelling fix was necessary but
> not sufficient; the real root cause was one function call away the
> whole time. Fixed by passing `["cairo"]`, the same package
> `festina_runtime_graphics.c` itself gets. Verified against the real
> Linux cairo-xlib pkg-config flags and the full suite; still only real
> macOS CI can compile this file at all, so this too awaits a run that
> gets all the way through.
>
> **Round five's push (a separate, unrelated bug in the fallback
> compile path — see windows.md, since it affects Windows too) got a
> real macOS run down to ONE failure, out of the whole suite**
> (claude.md #126, round six): `TestGraphics::test_compiles_and_links_successfully`
> opens a real window (`on mouseDown`/`mouse`/`key`/`resize`/`close`),
> so on darwin it correctly hits the real-hardware-verification gate —
> but called `compile_file` directly instead of through
> `compile_file_or_skip`, the same gap `TestAudio`'s own analogous test
> already avoided. Fixed to match. Linux and CodeQL were both green on
> this same run — the first genuinely green non-macOS-graphics result
> across six rounds.

A concrete, phased plan for bringing Festina to macOS. The premise
(from [todo.md](todo.md#platforms)) holds up under audit: **porting is
backend work, not language work.** The compiler is pure Python, the
generated IR is target-neutral, and the platform-specific API surface
of the runtime is small and precisely bounded — measured directly:

| Area | Platform-specific calls | Where |
|---|---|---|
| Windowing/events | 20 X11 calls + 2 `cairo_xlib_*` | `festina_runtime_graphics.c` |
| Audio device | 6 ALSA calls (`snd_pcm_open/set_params/writei/recover/close`, `snd_strerror`) | `festina_runtime_audio.c` |
| Core runtime | none — POSIX only (`regex.h`, `clock_gettime`, `select`, pthread), all in macOS libc | `festina_runtime.c` |

Everything else the runtime leans on is portable and Homebrew-packaged:
Cairo (drawing), libjpeg, libmpg123, sqlite3 (macOS even ships it).
The plan is ordered so each phase ends with something runnable and
CI-verified, and no phase blocks on the hardest problem (native
windowing) before the cheap ones prove the toolchain.

## Phase 0 — Toolchain bring-up: core-only programs compile and run

Goal: `festina compile hello.f` and the whole non-graphics,
non-audio test suite pass on an arm64 Mac. No new backends; only
toolchain seams.

1. **`festina/llvm_backend.py` — find brew's libLLVM.**
   `ctypes.util.find_library("LLVM")` won't see Homebrew's keg-only
   LLVM. Add explicit candidates before giving up:
   `/opt/homebrew/opt/llvm/lib/libLLVM.dylib` (arm64) and
   `/usr/local/opt/llvm/lib/libLLVM.dylib` (x86_64). The arch map
   already covers `arm64 → AArch64`, and the module already reads
   `LLVMGetDefaultTargetTriple` from the loaded library, so nothing
   else changes. The clang fallback also works untouched — Apple
   clang (Xcode ≥ 15) accepts the generated `.ll` directly, including
   its opaque-`ptr` IR; document Xcode 15 as the floor.
2. **`festina/cli.py` — un-GNU the sqlite static link.**
   `_sqlite_link_flags` passes `-Wl,-Bstatic/-Bdynamic`, which ld64
   rejects. Today that failure is caught by the `_can_link` probe and
   silently degrades to dynamic — correct but never static. Add a
   `sys.platform == "darwin"` branch that instead probes the explicit
   archive path (`pkg-config --variable=libdir sqlite3` +
   `/libsqlite3.a`, which brew's sqlite provides); fall back to
   `-lsqlite3` (the OS-shipped dylib) as now.
3. **`festina doctor` — macOS install hints.** *(Done, with one
   refinement over the original sketch: on darwin the audio lines
   report "no macOS backend yet — planned as macos.md Phase 1" instead
   of ALSA checks, and compiling an audio program fails with the same
   message — `_check_feature_supported` — rather than a pkg-config
   error naming a library that does not exist on macOS.)* Still open
   here: the missing-CommandLineTools detection (`xcrun
   --show-sdk-path` failing → `xcode-select --install` hint).
4. **CI: a `macos-14` (arm64) GitHub Actions job** running everything
   that needs no display and no audio device: the full
   lexer/parser/semantic/IR suites, `compile_and_run` tests for
   core/sqlite/timers/regex/text/blob, and — worth calling out — the
   **offscreen graphics tests** (`saveCanvas` renders through a pure
   Cairo image surface with no window, so those run headless on macOS
   as soon as brew's cairo links; brew's cairo formula builds with X11
   support, so `cairo-xlib.pc` resolves even before Phase 2 replaces
   it).

   Sanitizer caveat, decided up front: the leak-stress tier stays
   Linux-only. LeakSanitizer is not reliably available on
   darwin/arm64; the harness's existing skip-when-unavailable exit
   code already handles this, and memory-model verification continues
   to run on the Linux job, where it is strongest.

Exit criteria: macOS CI green on the suites above; `examples/hello.f`,
`fizzbuzz.f`, `config.f`, `files.f` run natively.

## Phase 1 — Audio: a 3-function device seam, then CoreAudio

The ALSA usage is the *push* model — each of the 64 pool channels has
its own thread blocking on `snd_pcm_writei` — and only six calls wide.
Rather than `#ifdef`-ing CoreAudio into the channel pool, cut the seam
exactly at the device:

1. **Define the device shim** (`festina_runtime_audio.c` keeps the
   pool, decoding, and channel logic — all portable):
   - `festina_pcm_open(channels, rate) → handle-or-error`
   - `festina_pcm_write(handle, frames, count) → ok` (blocking)
   - `festina_pcm_close(handle)`
   The Linux implementation is the existing six ALSA calls, moved
   behind these three functions. The EBUSY→`free_oldest` retry loop
   stays in the shared code (CoreAudio always software-mixes, so the
   macOS shim simply never reports device-busy).
2. **macOS implementation: AudioQueue** (AudioToolbox, plain C — no
   Objective-C needed). N preallocated buffers plus a counting
   semaphore reproduces blocking-push exactly: `write` copies frames
   into a free buffer, enqueues it, and blocks on the semaphore when
   all buffers are in flight; the completion callback posts it.
   Per-channel queues mirror the per-channel ALSA handles one-to-one.
   Link flags: `-framework AudioToolbox` via a per-platform
   `extra_link_flags` in `_RUNTIME_FEATURES` (and drop the `alsa`
   pkg-config package on darwin; keep `libmpg123` — brew's `mpg123`).
3. **Re-seat the white-box harnesses.** The three channel-pool
   harnesses in `tests/test_codegen.py` currently stub ALSA via macro
   overrides. Re-point the stubs at the three shim functions instead —
   which makes those harnesses platform-neutral and lets the full
   channel-pool white-box suite run on macOS CI with no audio device
   at all.
4. **The null-device test trick** (`~/.asoundrc → pcm.null`) is
   ALSA-only. macOS equivalent for CI: a shim-level env switch
   (`FESTINA_AUDIO_NULL=1` making `festina_pcm_*` a timed sink), used
   only by tests — the same role, one layer lower, and honest about it
   in the fixture's docstring.

Exit criteria: `examples/audio.f` plays on a Mac; channel-pool
white-box suite and play/stop/isPlaying end-to-end tests green on
macOS CI under the null shim.

## Phase 2 — Graphics: portable canvas + a narrow windowing seam *(built, CI-compiled; native-hardware verification open)*

The 1,477-line graphics TU was mostly portable already: all drawing
(rects, circles, text, paths, transforms, gradients, images, clips,
resizes, `saveCanvas`) targets an offscreen Cairo image surface, and
libjpeg decoding is platform-free. What was X11-specific was exactly
the windowing layer, now cut behind `runtime/festina_runtime_window.h`:

   - `festina_window_open(width, height, title)` / `festina_window_close(void)`
   - `festina_window_present(cairo_surface_t *backing)` — hand the offscreen backing surface to the platform to blit
   - `festina_window_events_wait(double timeout_seconds)` — block for at most one OS-native event, bounded by the next timer deadline (as before, the caller — not the backend — computes the deadline)
   - `festina_window_events_drain(void (*handler)(const FestinaWindowEvent *))` — pump every pending OS event, calling `handler` once per **normalized event**: `MOUSE_DOWN`/`MOUSE_UP`/`MOUSE_MOVE` (x, y), `KEY_DOWN`/`KEY_UP` (key_name), `RESIZE` (width, height), `CLOSE`

   One simplification found during extraction that the sketch above
   didn't anticipate: there is no `window_client_size` accessor and no
   synthetic redraw event. Each backend already knows the window's
   current size (it's an open-time parameter, kept current by RESIZE),
   and each backend independently remembers the last surface handed to
   `festina_window_present` and repaints from it on its own
   expose/`drawRect:` callback — a plain platform responsibility, not
   something the seam needs to broker.

   Linux implementation: the existing X11/Xlib code, moved into this
   file unchanged in behavior (XCreateSimpleWindow/XMapWindow/
   XStoreName/XDestroyWindow, the 10×100ms connect retry, `select()`
   on `ConnectionNumber`, XPending/XNextEvent translated into
   `FestinaWindowEvent`s). macOS implementation: one Objective-C file,
   `festina_runtime_window_mac.m` — a separate translation unit
   (Cocoa cannot live in a plain `.c` file), compiled by the same
   clang via its `.m` extension and linked `-framework Cocoa` — NSWindow
   plus an NSView whose `drawRect:` blits the Cairo image surface via
   CGImage (`CAIRO_FORMAT_ARGB32` maps exactly onto
   `kCGBitmapByteOrder32Little | kCGImageAlphaPremultipliedFirst` on
   any little-endian Mac). `_RUNTIME_FEATURES["graphics"]` grows
   per-platform `pkgs`/`extra_link_flags` (darwin: plain `cairo` +
   `libjpeg`, `-framework Cocoa`; no `cairo-xlib`, no XQuartz — 2a was
   skipped entirely, see the status note above).

   The three genuinely hard points, as they actually shook out:
   - **Event-loop inversion.** Cocoa requires UI on the main thread
     and prefers owning the loop. Festina's model is compatible —
     top-level code runs, then `festina_run_event_loop()` blocks on
     the main thread — so `festina_window_events_wait` uses
     `nextEventMatchingMask:untilDate:...dequeue:NO` (a peek, timeout
     carries the timer deadline exactly like `select`'s timeout on
     Linux) and `festina_window_events_drain` fully pumps the queue
     (`dequeue:YES` + `sendEvent:` + `updateWindows`) into a small
     ring buffer that window/view delegate callbacks (resize, close)
     also push into, then drains that buffer through the caller's
     handler. No `[NSApp run]` anywhere. Timers keep firing from the
     same loop, unchanged.
   - **Key-name parity.** `on keyDown(key:text)` reports X keysym
     names on Linux (`a`, `Return`, `space`, `Left`...). Cocoa reports
     characters/keyCodes. The mac layer maps both onto the same names
     via a small keyCode table, and the shared vocabulary is pinned in
     `runtime/festina_key_names.h`, guarded by
     `tests/test_platform.py::TestKeyNameVocabulary` — otherwise every
     keyboard-driven program would silently break on one platform.
     Autorepeat maps cleanly: NSEvent's `isARepeat` is the
     DetectableAutoRepeat equivalent.
   - **What drops out.** The X error handler around `XSetInputFocus`
     (a WM race) and the WM_DELETE_WINDOW protocol are X-specific;
     their jobs (focus, the close button) are ordinary NSWindow
     behavior — `windowShouldClose:` feeds the normalized close event.

   Real-hardware-verification gating, same shape as Phase 1's audio
   gate: `festina_runtime_window_mac.m` is compiled and type-checked
   against the real AppKit/Foundation/CoreGraphics headers on every
   `macos-14` CI push (a dedicated compile-only step, mirroring the
   audio one), but a Festina program that would actually *open a
   window* (declares `render()` or any window event handler — the
   narrow `gen.uses_graphics` flag, as opposed to the broad
   `gen.uses_graphics_code` flag that only means "draws something,
   possibly only offscreen") still hits `_check_feature_supported` and
   fails to compile on darwin unless `FESTINA_ENABLE_MACOS_GRAPHICS=1`
   is set, until confirmed against a real window, mouse, and keyboard.
   Offscreen-only programs (`saveCanvas`, no `render()`) are never
   gated on any platform — that distinction is itself covered by a
   dedicated unit test (`test_offscreen_graphics_never_reaches_the_darwin_gate`).

   CI honesty, unchanged from the original plan: there is no Xvfb
   equivalent on macOS runners, so windowed end-to-end tests (mouse
   dispatch under a real server) remain Linux-CI-only, verified there
   against a real Xvfb + xdotool + openbox window manager; macOS CI
   covers the full offscreen suite plus the compile-only Cocoa
   type-check, and windowed behavior awaits manual verification on
   real hardware. See tests/CONTRACT.md for the exact split.

Exit criteria (open until real-hardware verification):
`examples/graphics.f`, `tic_tac_toe.f`, `timers.f` run in native
windows on a Mac with `FESTINA_ENABLE_MACOS_GRAPHICS=1`;
keyboard/mouse/resize/close behave identically to Linux against the
pinned event vocabulary. Everything up to that point — the seam, both
backends, the gating, the tests, the packaging — is done.

## Phase 3 — Packaging and distribution *(done)*

1. `scripts/package_compiler.sh` already worked per-platform
   (PyInstaller emits a Mach-O binary on macOS unchanged); what it
   lacked was verification. The macos-14 CI job now installs
   PyInstaller and runs it for real on every push — since macos-14
   runners are Apple Silicon, that CI step *is* the arm64 build,
   ad-hoc-codesigned and smoke-tested (compiles and runs
   `examples/hello.f`) exactly as a maintainer packaging a release by
   hand would do it, rather than a path nothing but a human ever
   exercised. The Linux job got the same packaging+smoke-test step for
   the same reason — it was equally unverified in CI before this.
   Universal binaries remain a non-goal — ship arm64, document
   Rosetta-free x86_64 as build-from-source.
2. `scripts/package_compiler.sh` now ad-hoc codesigns the binary it
   just built whenever it's running on Darwin (`codesign -s -`,
   guarded on `uname -s` and on `codesign` being on PATH), so
   Gatekeeper allows local runs with no prompt. The CI step above
   re-verifies the signature with `codesign -v` rather than only
   checking that `codesign` exited zero. Full Developer-ID signing +
   notarization is a distribution decision to take only when there is
   a distribution channel, and stays deliberately out of scope here.
3. [setup.md](setup.md) has a real macOS section now: the exact brew
   line (`pkg-config sqlite cairo jpeg-turbo mpg123`, no `llvm` — Apple
   clang consumes the generated IR directly), the `xcode-select
   --install` / Xcode ≥ 15 floor, brew sqlite's keg-only
   `PKG_CONFIG_PATH` requirement, and — since Phase 2 shipped native
   Cocoa windowing rather than the originally-sketched XQuartz path —
   an explicit "no XQuartz, no X11 at all" note instead of the
   XQuartz-era caveat this bullet originally expected to write.

## Shared work — cut once, both ports consume it

The intersection with [windows.md](windows.md), kept here as the single
reference list. The efficient order is this package **first** — every
item lands and is testable on Linux alone — after which the two ports'
platform implementations are independent and parallelizable:

1. **The audio device seam** (`festina_pcm_open/write/close`) and the
   re-seating of the white-box harness stubs at it. Both platform
   files then use the same N-buffers-plus-semaphore blocking-push
   design (AudioQueue / waveOut).
2. **The windowing seam** (`festina_window_open/close/present/
   events_wait/events_drain` + normalized events, `runtime/
   festina_runtime_window.h`) *(done for macOS — Linux (X11) and macOS
   (Cocoa) backends both implement it; a Windows (Win32/D2D) backend
   is the remaining consumer)*, including the decision that `present`
   takes the Cairo *image surface* everywhere (xlib surface / CGImage
   / DIB are per-platform blits of one thing), and that redraw-on-expose
   is each backend's own job rather than a seam-level event.
3. **The key-name vocabulary** — `runtime/festina_key_names.h`, the
   pinned list both mapping tables target, with
   `tests/test_platform.py::TestKeyNameVocabulary` guarding it.
   *(Done.)*
4. **The `FESTINA_AUDIO_NULL` test shim** at the device seam — one
   audio-CI mechanism for all three platforms.
5. **The headless CI tier definition** — which suites run with no
   display/audio device; both OS jobs consume the same selection.
6. **The sanitizer decision** — leak tier stays Linux-only; one
   CONTRACT.md note.
7. **Per-platform structure refactors, filled in per port**:
   `_RUNTIME_FEATURES` (pkgs/link flags/sources by OS),
   `_find_libllvm` candidate paths *(done —
   `_platform_libllvm_paths`)*, `_default_output_name` *(done)*,
   `_static_sqlite_attempt` *(done)*, `festina doctor`'s hint table,
   and `package_compiler.sh`'s release matrix.
8. **Cross-platform contract tests that already run everywhere** —
   binary-fidelity round trips and forward-slash path handling
   (`tests/test_platform.py::TestBinaryFidelity`), plus the per-OS
   Phase-0 exit-criteria tests (`TestOnMacOS`/`TestOnWindows`),
   skipped until each CI job exists. *(Done.)*

Not shared, despite appearances: the regex gap and `.exe` mechanics
are Windows-only, the ld64 sqlite probe is macOS-only, and Cocoa's
run-loop inversion has no Win32 counterpart.

## Order, size, and what is deliberately not planned

Phases landed independently and in order — 0 (small: two Python
files, a CI job), 1 (medium: one seam refactor + ~200 lines of
AudioQueue), 2 (the largest: the seam extraction plus one
Objective-C file — landed as a single native-Cocoa pass, skipping the
originally-sketched 2a XQuartz rehearsal since it turned out not to be
needed), 3 (small — a CI step, a shell-script guard, a docs section).
All four are done; real-hardware verification of the audio/graphics
gates is the one item macOS support has left. Windows is out of scope
here but
constrained: the Phase 1/2 seams are the same ones a Windows port
needs (WASAPI, Win32/D2D or the same Cairo blit), so cutting them
platform-shaped rather than macOS-shaped was part of the work. The
`<regex.h>` and `select()` dependencies that todo.md flags as Windows
problems are non-issues on macOS and stay untouched.
