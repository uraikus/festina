# macOS support — the plan

> **Status:** Phase 0 is implemented — libLLVM discovery, the ld64
> sqlite strategy, the platform-aware doctor, the audio gating error,
> and the `macos-14` CI job (`.github/workflows/ci.yml`) all exist,
> with the darwin branches unit-tested from every platform
> (`tests/test_platform.py`) and the missing-dependency skip letting
> the full suite run on macOS CI. Phase 1 is built (claude.md #121):
> the `festina_pcm_*` device seam is cut, the AudioQueue backend
> exists and is compiled/type-checked by macOS CI, the white-box
> harnesses are re-seated at the seam (no ALSA headers needed), and
> `FESTINA_AUDIO_NULL=1` is the cross-platform test sink. The darwin
> audio gate stays until real-hardware playback is verified
> (`FESTINA_ENABLE_MACOS_AUDIO=1` to try it on a Mac). Phases 2–3 are
> open.

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

## Phase 2 — Graphics: portable canvas + a narrow windowing seam

The 1,477-line graphics TU is mostly portable already: all drawing
(rects, circles, text, paths, transforms, gradients, images, clips,
resizes, `saveCanvas`) targets an offscreen Cairo image surface, and
libjpeg decoding is platform-free. What is X11-specific is exactly the
windowing layer. Two stages:

**2a — Validation under XQuartz (near-zero code).** brew's cairo links
against XQuartz's X11; the existing `cairo-xlib` backend runs
unmodified. Not the shipping answer (XQuartz install, alien window
chrome), but it proves decoding/drawing/event handling on macOS before
any new code exists, and it gives Phase 2b a behavioral reference on
the same machine.

**2b — Native Cocoa backend behind a seam.** Extract the platform
interface the X11 code already implies (each item maps to specific
existing calls):

   - `window_open(w, h, title)` / `window_close()` — XCreateSimpleWindow/XMapWindow/XStoreName/XDestroyWindow, incl. the 10×100ms connect retry
   - `window_present(cairo_image_surface)` — the render() blit (today `cairo_xlib_surface_create/set_size` + paint)
   - `window_client_size(&w, &h)` — clientWidth/clientHeight
   - `events_wait(timeout_seconds)` — today `select()` on `ConnectionNumber` bounded by the next timer deadline
   - `events_drain(handler)` — XPending/XNextEvent loop, emitting **normalized events**: mouseDown/mouseUp/mouse(x,y), keyDown/keyUp(name), resize(w,h), close

   Linux implementation: the existing code, moved. macOS
   implementation: one Objective-C file (`festina_runtime_window_mac.m`,
   compiled by the same clang, linked `-framework Cocoa`) — NSWindow +
   an NSView whose `drawRect:` blits the Cairo image surface via
   CGImage. `_RUNTIME_FEATURES["graphics"]` grows per-platform
   `source`/`pkgs`/`extra_link_flags` (darwin: `cairo` + `libjpeg`,
   no `cairo-xlib`).

   The three genuinely hard points, named now:
   - **Event-loop inversion.** Cocoa requires UI on the main thread
     and prefers owning the loop. Festina's model is compatible —
     top-level code runs, then `festina_run_event_loop()` blocks on
     the main thread — so implement `events_wait` with
     `nextEventMatchingMask:untilDate:` (the timeout carries the timer
     deadline, exactly like the `select` timeout today) rather than
     `[NSApp run]`. Timers keep firing from the same loop, unchanged.
   - **Key-name parity.** `on keyDown(key:text)` currently reports X
     keysym names (`a`, `Return`, `space`, `Left`...). Cocoa reports
     characters/keyCodes. Ship a mapping table in the mac layer to the
     *same* names, and pin the shared vocabulary in a
     platform-independent test list — otherwise every keyboard-driven
     program silently breaks on one platform. Autorepeat semantics
     (`keyDown` repeats while held, exactly one `keyUp`) map cleanly:
     NSEvent's `isARepeat` is the DetectableAutoRepeat equivalent.
   - **What drops out.** The X error handler around `XSetInputFocus`
     (a WM race) and the WM_DELETE_WINDOW protocol are X-specific;
     their jobs (focus, the close button) are ordinary NSWindow
     behavior — `windowShouldClose:` feeds the normalized close event.

   CI honesty: there is no Xvfb equivalent on macOS runners, so
   windowed end-to-end tests (mouse dispatch under a real server)
   remain Linux-CI-only; macOS CI covers the full offscreen suite plus
   the seam's Linux-verified contract, and windowed behavior is
   verified manually per release on real hardware. Say so in
   tests/CONTRACT.md rather than pretending coverage.

Exit criteria: `examples/graphics.f`, `tic_tac_toe.f`, `timers.f` run
in native windows on a Mac; keyboard/mouse/resize/close behave
identically to Linux against the pinned event vocabulary.

## Phase 3 — Packaging and distribution

1. `scripts/package_compiler.sh` already works per-platform
   (PyInstaller emits a Mach-O binary on macOS); add an arm64 build to
   the release flow. Universal binaries are a non-goal initially —
   ship arm64, document Rosetta-free x86_64 as build-from-source.
2. Ad-hoc codesign the packaged binary (`codesign -s -`) so Gatekeeper
   allows local runs; full Developer-ID signing + notarization is a
   distribution decision to take only when there is a distribution
   channel, and is deliberately out of scope here.
3. `setup.md`: a real macOS section replacing today's "should be
   similar in spirit" — the exact brew line per feature tier, the
   Xcode ≥ 15 floor, and the XQuartz note for anyone on Phase-2a-era
   builds.

## Shared work — cut once, both ports consume it

The intersection with [windows.md](windows.md), kept here as the single
reference list. The efficient order is this package **first** — every
item lands and is testable on Linux alone — after which the two ports'
platform implementations are independent and parallelizable:

1. **The audio device seam** (`festina_pcm_open/write/close`) and the
   re-seating of the white-box harness stubs at it. Both platform
   files then use the same N-buffers-plus-semaphore blocking-push
   design (AudioQueue / waveOut).
2. **The windowing seam** (`window_open/close/present/client_size/
   events_wait/events_drain` + normalized events), including the
   decision that `present` takes the Cairo *image surface* everywhere
   (xlib surface / CGImage / DIB are per-platform blits of one thing).
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

Phases land independently and in order — 0 (small: two Python files, a
CI job), 1 (medium: one seam refactor + ~200 lines of AudioQueue), 2b
(the largest: the seam extraction plus one Objective-C file, with 2a
as its cheap rehearsal), 3 (small). Windows is out of scope here but
constrained: the Phase 1/2 seams are the same ones a Windows port
needs (WASAPI, Win32/D2D or the same Cairo blit), so cutting them
platform-shaped rather than macOS-shaped is part of the work. The
`<regex.h>` and `select()` dependencies that todo.md flags as Windows
problems are non-issues on macOS and stay untouched.
