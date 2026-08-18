# Windows support — the plan

> **Status: Phase 0 is built; two real CI rounds found real bugs, both
> now fixed** (claude.md #126) — every Python-side toolchain seam this
> section lists was in fact already covered by claude.md #39's shared
> work (`.exe` naming, the libLLVM DLL candidates, the GNU-ld
> static-sqlite path, `TestOnWindows`'s skipif-gated exit-criteria
> tests). What was still missing: the regex decision, `festina
> doctor`'s Windows-specific hints (rather than wrongly probing for
> Linux packages like `alsa`/`cairo-xlib`), and a `windows-latest` CI
> job via `msys2/setup-msys2`. All three landed — and the regex
> decision took two real rounds to get right. Round one:
> `mingw-w64-ucrt-x86_64-libgnurx`, this section's originally preferred
> package, IS installable, but `pacman --noconfirm` silently drops it
> because it conflicts with `mingw-w64-ucrt-x86_64-libsystre` (already
> pulled in transitively by the rest of the UCRT64 toolchain) rather
> than erroring — so `libsystre` is the package to install. Round two,
> on the very next real CI run: pkg-config doesn't answer to
> `libsystre` either — its own PKGBUILD declares `Provides`/
> `Conflicts`/`Replaces` against `libgnurx` (a designed drop-in
> replacement, which is why they conflict at all) and ships its
> pkgconfig file under THAT old name, `gnurx.pc`, confirmed via MSYS2's
> own package listing rather than guessed again. `_core_pkgs` now
> installs `libsystre`, asks pkg-config for `gnurx`. `_check_feature_supported`
> gives graphics/audio a clean "not implemented yet, windows.md Phase
> N" error on win32 (unconditional, unlike macOS's real-hardware-
> verification gate — there is no backend at all yet to unlock). That
> same second CI run also caught a bug in round one's OWN fix: four new
> `_doctor_report()` tests spoof `sys.platform` to `"win32"` from real
> Linux CI, which is safe for this project's own code but not for
> `shutil.which` — its internal Windows branch crashes on Python 3.12+
> when actually running on POSIX. Fixed by patching `shutil.which`
> itself in those four tests. What is NOT yet confirmed: that this
> fixed state is itself green on real Windows CI — this project still
> has no Windows/MSYS2 access, so every fix here was verified by
> reasoning from each run's actual log output, the full Linux suite,
> and (for the Python 3.12 bug specifically) a real 3.12.3 venv — never
> by re-running on Windows itself. Phases 1–3 are open.

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

## Phase 0 — Toolchain bring-up: core-only programs compile and run *(built, unverified on real hardware)*

Goal: `festina compile hello.f` produces a runnable `.exe` and the
whole non-graphics, non-audio suite passes under MSYS2 on Windows CI.

1. **Regex.** The one core gap, and the item with a two-round real
   story now: landed first as planned, with MSYS2's `libgnurx` package
   as the per-platform pkg-config addition to cli.py's core link line
   (`_core_pkgs`, win32-only; empty everywhere else, where `<regex.h>`
   is already part of libc) — then corrected twice against two real
   `windows-latest` runs. Round one: `libgnurx` genuinely installs, but
   `pacman --noconfirm` silently drops it from the install set because
   it CONFLICTS with `libsystre` (already present, pulled in
   transitively by the rest of the UCRT64 toolchain), so `pkg-config
   --cflags libgnurx` came up empty two steps later with no error at
   the install step to explain why — `libsystre` is the package to
   install. Round two: pkg-config doesn't answer to `libsystre` either
   — its PKGBUILD declares `Provides`/`Conflicts`/`Replaces` against
   `libgnurx` (a designed drop-in replacement, which is why they
   conflict at all) and ships its pkgconfig file under THAT old name,
   `gnurx.pc`, confirmed via MSYS2's own package listing. `_core_pkgs`
   now installs `libsystre`, asks pkg-config for `gnurx` -- a real,
   already-installed POSIX regex.h/regcomp/regexec wrapper around TRE,
   not the divergent-ERE fallback this item originally reserved
   (vendoring musl's `regcomp/regexec/regfree`), which never became
   necessary either round. Whether `gnurx`'s ERE behavior actually
   matches glibc's under the existing regex test suite is what the
   NEXT real Windows run decides; nothing about that suite is
   platform-specific, so it remains the referee.
2. **`.exe` awareness in `festina/cli.py`.** Already done before this
   phase began — `_default_output_name` appends `.exe` on `win32` (and
   `festina run` invokes it accordingly); everything else in the
   driver — the runtime-object cache in the temp dir, the `_can_link`
   probe, per-feature link flags — was already path-library-clean.
3. **`festina/llvm_backend.py` — find libLLVM's DLL.** Already done
   before this phase began — `_platform_libllvm_paths` covers the
   MSYS2 candidates (`$MSYSTEM_PREFIX`, the UCRT64/MinGW64/CLANG64
   roots); the clang fallback (MSYS2 clang consumes the generated
   `.ll` directly) covers the gap regardless, exactly as on macOS.
4. **`festina doctor` — Windows hints**: done. The Windows-specific
   report lines (rather than wrongly checking for Linux packages like
   `alsa`/`cairo-xlib`): POSIX regex as a REQUIRED line (like sqlite3,
   checked via pkg-config's `gnurx` name but hinting the real package
   to install, `libsystre`), graphics/audio as "not yet implemented,
   windows.md Phase 1/2" lines, and detection of the plain `MSYS` shell
   (as opposed to UCRT64/MINGW64/CLANG64) via `$MSYSTEM`. The pacman
   one-liner (`pacman -S mingw-w64-ucrt-x86_64-{clang,sqlite3,pkgconf,libsystre}`)
   is now that hint's actual text, and the plain `cmd.exe` + MSVC
   note lives in setup.md's own Windows section (todo, tracked
   separately from this phase's own scope).
5. **CI: a `windows-latest` job via the `msys2/setup-msys2` action**,
   built, and now run for real TWICE: runs the whole suite headless
   the same way the macOS job does, with no `FESTINA_STRICT_DEPS`
   (audio/graphics have no Windows backend yet at all, so those tiers
   shed as skips via the same conftest mechanism, not a parallel
   test-selection list), plus compiling and running the four
   windowless examples as real `.exe`s. The sanitizer leak tier stays
   Linux-only, same reasoning as macOS. Those two runs are what caught
   the libgnurx/libsystre package conflict, the libsystre/gnurx
   pkg-config name mismatch, and a `shutil.which`-on-Python-3.12 crash
   in the fix's own new tests (all claude.md #126) plus two small,
   independent test-harness bugs from round one (non-UTF-8 locale
   defaults on Windows corrupting a non-ASCII literal; a file-path
   assertion breaking on a drive-letter colon) — all fixed, but a
   THIRD real run is still what's needed to confirm this round's own
   fixes land clean.
6. **Filesystem semantics, verified not assumed**: already covered
   before this phase began by `tests/test_platform.py::TestBinaryFidelity`,
   which runs on every platform's CI — every runtime `fopen` is
   binary-mode (`"rb"`/`"wb"`/`"ab"`), so blobs and `save()` round-trip
   byte-identically with no CRLF hazard, and the CRT accepts the
   forward-slash paths the examples use.

Exit criteria (open until a real Windows CI run happens): Windows CI
green on the suites above; `hello.f`, `fizzbuzz.f`, `config.f`,
`files.f` run natively as `.exe`s.

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

The full shared-work list — the seams, the key-name vocabulary, the
test shims, the per-platform cli/llvm_backend structure, and which of
it is already done — lives in **macos.md's "Shared work" section**,
kept in one place so the two plans cannot drift. Sequencing from the
Windows side: Phase 0 is done (small, as expected: the regex package
decision plus doctor hints and a CI job — the `.exe` naming and
libLLVM DLL candidates were already landed by claude.md #39, before
this phase even began). Phases 1 and 2 each split into seam-cutting
(shared with macOS, already done — both seams exist and have a Linux
+ macOS implementation apiece) and the still-open Win32/waveOut
implementations (each comparable in size to their macOS twins; the
graphics layer is if anything simpler, being plain C with no run-loop
inversion — Win32 message pumps compose with the existing
block-with-timeout loop directly). Phase 3 is small. The regex
decision is the only Phase 0 item with real uncertainty left, and
it's not yet settled: the existing regex suite is the referee, but
only a real Windows CI run can put the question to it.
