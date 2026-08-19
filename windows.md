# Windows support — the plan

> **Status: Phase 0 is DONE and confirmed green on real Windows CI** —
> twelve real rounds on the same PR (claude.md #126 has the full
> account — this is the short version, condensed since the per-round
> narrative was getting long), from 26 failures in round one down to a
> fully green run — linux, macOS, windows, and every CodeQL analyzer —
> on round twelve's push. Every Python-side toolchain seam this
> section lists was in fact already covered by claude.md #39's shared
> work (`.exe` naming, the libLLVM DLL candidates, the GNU-ld
> static-sqlite path). Landed across the twelve rounds: the regex
> decision (`_core_pkgs` installs `libsystre`, asks pkg-config for
> `gnurx`), `festina doctor`'s Windows hints, a `windows-latest` CI
> job, `#ifdef _WIN32` branches for `localtime_r`→`localtime_s` and for
> `festina_runtime_init()` (default text-mode stdout turning `\n` into
> `\r\n`), unconditional graphics gating on win32 (offscreen included —
> no window backend exists there at all yet), `_run_tool` resolving
> every command through `shutil.which` first rather than trusting
> `subprocess.run`'s broader Win32 `CreateProcess` search (which checks
> the calling process's own directory before PATH, silently defeating
> `tests/conftest.py`'s `path_without` PATH-only test isolation — which
> in turn had its own bug, symlinking tools under their bare name,
> invisible to `shutil.which`'s Windows PATHEXT search), two doctor-test
> setup bugs, a real bug in `examples/files.f` (hardcoded `/tmp/...`
> paths a native Windows binary resolves under the current drive's
> root, not MSYS2's own `/tmp` mapping), a genuine `festina_log_*` bug
> — no explicit `fflush(stdout)`, so a redirected/piped program's
> output could sit in the C runtime's default block buffer indefinitely
> — and, the one that took longest to pin down: `_rename_if_linker_
> appended_exe`'s guard skipped the post-link rename whenever the
> target path already existed from an earlier compile, so recompiling
> to the same explicit output path (exactly what `TestAutomaticSqlite
> SchemaSync`'s tests do, twice, in every test) silently kept running
> the FIRST compile's stale binary instead of the fresh one — found via
> round eleven's own instrumentation (an mtime check plus captured
> program output), which caught the "second" program printing the
> first program's own output verbatim. Fixed by dropping the
> exists-check; `os.replace` already overwrites atomically on Windows,
> so it was never a needed guard. Verified: real Windows CI, not just
> reasoning from a log — the one thing every prior round's status block
> here had to leave open, since this project has no Windows/MSYS2
> access of its own; every fix along the way was verified by reasoning
> from each run's actual log output, the full Linux suite, and (for one
> Python-version-specific test bug) a real 3.12.3 venv, until the
> twelfth round's own real CI result confirmed all of it at once.
> Phases 1–3 are open.

The Windows counterpart to [macos.md](macos.md), and deliberately its
sibling: the two ports share the same two backend seams (audio device,
windowing), so whichever lands first cuts them and the second only
fills in an implementation. What Windows adds that macOS did not is a
**core-runtime gap** — measured directly, there is exactly one:

| Area | Platform-specific surface | Windows answer |
|---|---|---|
| Core runtime | `<regex.h>` — 20 call sites; `localtime_r` — 1 call site (found by real CI, claude.md #126 — MinGW-w64's UCRT doesn't provide it); everything else is portable (`clock_gettime`/`nanosleep`, `strdup`, binary-mode `fopen`, `remove`, `getenv`) | POSIX regex library (Phase 0, decision below); `#ifdef _WIN32` to `localtime_s` (reversed args, done) |
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

## Phase 0 — Toolchain bring-up: core-only programs compile and run *(built and confirmed green on real Windows CI)*

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
2. **`.exe` awareness in `festina/cli.py`.** Mostly already done before
   this phase began — `_default_output_name` appends `.exe` on `win32`
   (and `festina run` invokes it accordingly); everything else in the
   driver — the runtime-object cache in the temp dir, the `_can_link`
   probe, per-feature link flags — was already path-library-clean. One
   real gap a fourth real CI round found: `_default_output_name`'s own
   docstring already documented that MinGW's linker appends `.exe` to
   a `-o` name that lacks one, but the actual protection only covered
   the *default*-name case (the only caller of that function) — an
   explicit `-o program` still silently linked to `program.exe` while
   `compile_file` kept claiming `program` was the output.
   `_rename_if_linker_appended_exe` now runs after linking and renames
   the linker's real output back to the exact name the caller asked
   for, rather than silently substituting `.exe` into their request.
   That function's own guard had a SECOND bug, found in round twelve:
   it skipped the rename whenever the target path already existed
   from an earlier compile, which is exactly what recompiling to the
   same explicit path (as any "change the source, compile again" test
   or workflow does) triggers on the second and every later compile.
   The freshly linked `program.exe` sat unused next to the untouched,
   stale first binary; `_run_tool` and everything downstream reported
   success because nothing had actually failed, it just quietly kept
   running the old program. Real Windows CI's own instrumentation (an
   added mtime check plus the compiled program's captured stdout) is
   what surfaced this directly -- a "second" test run's own program
   printed the FIRST run's output verbatim. Fixed by dropping the
   exists-check; `os.replace` already overwrites atomically on
   Windows, so it was never a needed guard.
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
   built, and now run for real TWELVE times: runs the whole suite
   headless the same way the macOS job does, with no
   `FESTINA_STRICT_DEPS` (audio/graphics have no Windows backend yet at
   all, so those tiers shed as skips via the same conftest mechanism,
   not a parallel test-selection list), plus compiling and running the
   four windowless examples as real `.exe`s. The sanitizer leak tier
   stays Linux-only, same reasoning as macOS. Those twelve rounds are
   what caught every fix summarized in the status block above (claude.md
   #126 has the full blow-by-blow) — from 26 failures in round one down
   to zero, confirmed green on round twelve's own real Windows CI run.
6. **Filesystem semantics, verified not assumed**: already covered
   before this phase began by `tests/test_platform.py::TestBinaryFidelity`,
   which runs on every platform's CI — every runtime `fopen` is
   binary-mode (`"rb"`/`"wb"`/`"ab"`), so blobs and `save()` round-trip
   byte-identically with no CRLF hazard, and the CRT accepts the
   forward-slash paths the examples use. What that audit didn't cover,
   because it isn't a file-open-mode property at all, is `stdout`: the
   MinGW/UCRT CRT opens the standard streams in TEXT mode by default,
   independently of any `fopen` flag, silently rewriting every `\n` a
   compiled program prints to `\r\n`. Round four's first real run of a
   compiled program on Windows caught this directly (`capfd` showed
   `"hello from run\r\n"` against an expected plain `\n`); fixed with
   `festina_runtime_init()` (`_setmode(_fileno(stdout), _O_BINARY)`,
   `#ifdef _WIN32`, a no-op everywhere else), called unconditionally as
   the first thing every compiled program's `main()` does.

Exit criteria: Windows CI green on the suites above (confirmed, round
twelve); `hello.f`, `fizzbuzz.f`, `config.f`, `files.f` run natively
as `.exe`s (confirmed).

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
