# Windows support — the plan

The Windows counterpart to [macos.md](macos.md), and deliberately its
sibling: the two ports share the same two backend seams (audio device,
windowing), so whichever lands first cuts them and the second only
fills in an implementation. What Windows adds that macOS did not is a
**core-runtime gap** — measured directly, there is exactly one:

| Area | Platform-specific surface | Windows answer |
|---|---|---|
| Core runtime | `<regex.h>` — 20 call sites; everything else is portable (`clock_gettime`/`nanosleep`, `strdup`, binary-mode `fopen`, `remove`, `getenv`) | POSIX regex library (Phase 0, decision below) |
| Windowing/events | the 5-function seam from macos.md Phase 2b | Win32 + the Cairo image-surface blit |
| Audio device | the 3-function seam from macos.md Phase 1 | waveOut |

## The toolchain decision, made first: MSYS2 / MinGW-w64

One supported toolchain: **clang or gcc from MSYS2's MinGW-w64
environment**. MSVC is explicitly out of scope. This single decision
dissolves most of the apparent porting surface:

- `clock_gettime`/`nanosleep` — provided by MinGW-w64 (winpthreads).
- `pthread` (the audio channel pool's threads) — winpthreads, linked
  by the same `-pthread` flag already on the audio link line.
- `pkg-config` and every library Festina uses — packaged: sqlite3,
  cairo, libjpeg-turbo, mpg123 all exist as `mingw-w64-*` packages.
- The GNU-ld static-sqlite trick (`-Wl,-Bstatic`) that macOS had to
  replace **works unchanged** — MinGW's ld is GNU ld.
- The compiler driver flags cli.py emits (`-O2 -c -o`, `-l...`) are
  the same driver dialect.

MSVC would instead mean: no `regex.h` and no POSIX layer, a different
driver dialect, no pkg-config culture, and a second CI matrix — all
cost, no user-visible gain over shipping MinGW-built binaries (which
are ordinary, dependency-light PE executables any Windows runs).

## Phase 0 — Toolchain bring-up: core-only programs compile and run

Goal: `festina compile hello.f` produces a runnable `.exe` and the
whole non-graphics, non-audio suite passes under MSYS2 on Windows CI.

1. **Regex.** The one core gap. Preferred: MSYS2's POSIX regex
   package (`mingw-w64-*-libgnurx` — the standard `regex.h`/`libregex`
   shim for MinGW), added as a per-platform pkg/lib in cli.py's core
   link line. Fallback, if its ERE behavior diverges from glibc's
   under the existing regex test suite (the suite decides — it pins
   `[0-9]+`, flags, `/g` replace-all, split-on-empty-match, and error
   messages): vendor musl's self-contained `regcomp/regexec/regfree`
   (~3k lines, MIT) into `runtime/`, used on Windows only. Either way
   the language surface stays POSIX ERE everywhere, which api.md
   already promises.
2. **`.exe` awareness in `festina/cli.py`.** `_default_output_name`
   appends `.exe` on `win32` (and `festina run` invokes it
   accordingly); everything else in the driver — the runtime-object
   cache in the temp dir, the `_can_link` probe, per-feature link
   flags — is path-library-clean already.
3. **`festina/llvm_backend.py` — find libLLVM's DLL.** Add MSYS2
   candidates (`libLLVM-*.dll` on the MinGW bin path) next to the
   existing lookup; the clang fallback (MSYS2 clang consumes the
   generated `.ll` directly) covers the gap regardless, exactly as on
   macOS.
4. **`festina doctor` — Windows hints**: the one-line MSYS2 install
   (`pacman -S mingw-w64-ucrt-x86_64-{clang,sqlite3,pkgconf,libgnurx}`),
   detection of being in the wrong MSYS2 environment (MSYS vs
   UCRT64), and a note that plain `cmd.exe` + MSVC is unsupported.
5. **CI: a `windows-latest` job via the `msys2/setup-msys2` action**,
   running everything headless: full lexer/parser/semantic/IR suites,
   compile_and_run for core/sqlite/timers/regex/text/blob, and the
   offscreen graphics suite once Phase 2's cairo package is in
   (`saveCanvas` needs no window on any platform). The
   sanitizer leak tier stays Linux-only, same reasoning as macOS.
6. **Filesystem semantics, verified not assumed**: every runtime
   `fopen` is already binary-mode (`"rb"`/`"wb"`/`"ab"` — checked), so
   blobs and `save()` round-trip byte-identically with no CRLF
   hazard; the CRT accepts the forward-slash paths the examples use.
   One test pins each of those two facts on the Windows job.

Exit criteria: Windows CI green on the suites above; `hello.f`,
`fizzbuzz.f`, `config.f`, `files.f` run natively as `.exe`s.

## Phase 1 — Audio: the shared device seam, then waveOut

Prerequisite: the 3-function device seam from macos.md Phase 1
(`festina_pcm_open/write/close`) — cut once, whichever port gets there
first. The channel pool, WAV parser, mpg123 decoding and pthread use
all compile under MinGW unchanged.

The Windows implementation is **waveOut** (winmm — plain C, shipped
with Windows since forever, no COM): `waveOutOpen` per channel,
`waveOutWrite` of prepared `WAVEHDR` blocks, and a semaphore counting
free blocks reproduces ALSA's blocking push exactly — the same
N-buffers-plus-semaphore shape the macOS AudioQueue shim uses. WASAPI
is deliberately not the first target: it is COM-based, event-driven,
and buys latency Festina's `play()/stop()` surface doesn't expose.
Link: `-lwinmm` as the darwin/linux-conditional in
`_RUNTIME_FEATURES["audio"]` (no pkg-config package needed).

Windows always software-mixes, so — like CoreAudio — the EBUSY
`free_oldest` retry loop simply never fires. The white-box harnesses,
re-seated at the seam by the macOS plan, run on Windows CI as-is; the
`FESTINA_AUDIO_NULL=1` shim from that plan covers end-to-end
play/stop/isPlaying tests with no audio device.

Exit criteria: `examples/audio.f` plays on Windows; channel-pool
white-box and null-shim end-to-end tests green on Windows CI.

## Phase 2 — Graphics: the shared windowing seam, then Win32

Prerequisite: the 5-function windowing seam from macos.md Phase 2b
(`window_open/close`, `window_present`, `window_client_size`,
`events_wait(timeout)`, `events_drain(handler)` emitting normalized
events). All drawing stays in portable Cairo (MSYS2's cairo package),
libjpeg decoding is unchanged.

The Windows layer is one C file (`festina_runtime_window_win32.c` —
no Objective-C-style split needed here, Win32 is plain C):

- **Window**: `RegisterClassEx`/`CreateWindowEx`/`ShowWindow`;
  `WM_CLOSE` feeds the normalized close event (the WM_DELETE_WINDOW
  analog); title via `CreateWindowEx`'s name; client size from
  `GetClientRect`.
- **Present**: the Cairo ARGB32 image surface is exactly a 32bpp
  top-down DIB — `StretchDIBits`/`SetDIBitsToDevice` from `WM_PAINT`,
  no cairo-win32 backend needed (same blit shape as the mac CGImage
  path, on purpose: the seam's `present` takes the image surface on
  every platform).
- **Event loop**: `events_wait(timeout)` is
  `MsgWaitForMultipleObjects` with the timer deadline as its
  millisecond timeout — the precise Win32 analog of today's `select`
  on the X connection fd — and `events_drain` is the
  `PeekMessage`/`TranslateMessage`/`DispatchMessage` pump.
- **Input**: `WM_LBUTTONDOWN/UP`, `WM_MOUSEMOVE`, `WM_KEYDOWN/UP` +
  `WM_CHAR`. Key names map from virtual-key codes to the **shared
  key-name vocabulary** the macOS plan pins (`a`, `Return`, `space`,
  `Left`, ...) — the vocabulary test is cross-platform property
  number one. Autorepeat matches natively: `WM_KEYDOWN` repeats
  while held (bit 30 distinguishes repeats if ever needed), one
  `WM_KEYUP` — exactly claude.md #98's contract.

CI note, opposite of macOS: GitHub's Windows runners **can create
real Win32 windows** (no Xvfb equivalent needed), so the windowed
end-to-end tier — window opens, resize/close dispatch — is expected
to run on Windows CI; verify early in the phase and record the
outcome in tests/CONTRACT.md either way.

Exit criteria: `examples/graphics.f`, `tic_tac_toe.f`, `timers.f` run
in native windows; keyboard/mouse/resize/close behave identically to
Linux against the pinned event vocabulary.

## Phase 3 — Packaging and distribution

1. `scripts/package_compiler.sh` is bash — it runs under MSYS2, and
   PyInstaller on Windows emits `festina.exe`; add a Windows build to
   the release flow (the script's `:`-separated `--add-data` needs
   the `;` separator on Windows — PyInstaller's documented
   platform difference, a two-line fix).
2. **DLL story for compiled programs**: a MinGW-built program may
   depend on a handful of MSYS2 runtime DLLs. Decide per tier: link
   `-static-libgcc` (and winpthreads static) for core-only programs
   so `hello.exe` is copy-anywhere; graphics/audio programs ship
   alongside their cairo/jpeg/mpg123 DLLs, or document the MSYS2
   requirement. Pin whichever choice with an `ldd`-equivalent
   (`objdump -p | grep 'DLL Name'`) test, mirroring
   TestSlimBinaries.
3. `setup.md`: a real Windows section — the MSYS2 environment to use
   (UCRT64), the pacman one-liner per feature tier, and the explicit
   MSVC-unsupported statement.

## Order and shared work

Phase 0 is independent and can start any time (small: regex package
decision + two Python files + CI). Phases 1 and 2 each split into
seam-cutting (shared with macOS — done once) and the Win32/waveOut
implementations (each comparable in size to their macOS twins; the
graphics layer is if anything simpler, being plain C with no run-loop
inversion — Win32 message pumps compose with the existing
block-with-timeout loop directly). Phase 3 is small. The regex
decision is the only Phase 0 item with real uncertainty, and the
existing regex suite — not judgement — settles it.
