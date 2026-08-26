# Windows support

Toolchain, graphics, HTTP/WebSocket, and packaging all work today and
are confirmed on real Windows CI. Audio compiles and type-checks but
stays gated — not for lack of verification, but because
`windows-latest` CI runners have no audio device at all (`waveOutOpen`
fails with `MMRESULT 2`), so un-gating it would fail every audio test
permanently rather than just skip.

The Windows counterpart to [macos.md](macos.md): the two ports share
the same two backend seams (audio device, windowing). What Windows
needed that macOS didn't is a core-runtime gap:

| Area | Platform-specific surface | Windows answer |
|---|---|---|
| Core runtime | `<regex.h>` -- 20 call sites; `localtime_r` -- 1 call site (MinGW-w64's UCRT doesn't provide either); everything else is portable (`clock_gettime`/`nanosleep`, `strdup`, binary-mode `fopen`, `remove`, `getenv`) | POSIX regex library (below); `#ifdef _WIN32` to `localtime_s` (reversed args) |
| Windowing/events | the 5-function seam macos.md documents | Win32 + the Cairo image-surface blit |
| Audio device | the 3-function seam macos.md documents | waveOut |

## Toolchain: MSYS2 / MinGW-w64

One supported toolchain: clang or gcc from MSYS2's MinGW-w64
environment. MSVC is explicitly out of scope. This single decision
dissolves most of the porting surface:

- `clock_gettime`/`nanosleep` — provided by MinGW-w64 (winpthreads).
- `pthread` (the audio channel pool's threads) — winpthreads, linked
  by the same `-pthread` flag already on the audio link line.
- `pkg-config` and every library Festina uses are packaged: sqlite3,
  cairo, libjpeg-turbo, mpg123 all exist as `mingw-w64-*` packages.
- The GNU-ld static-sqlite trick (`-Wl,-Bstatic`) works unchanged —
  MinGW's ld is GNU ld.
- The compiler driver flags `festina/cli.py` emits (`-O2 -c -o`,
  `-l...`) are the same driver dialect.

MSVC would instead mean no `regex.h`/POSIX layer, a different driver
dialect, no pkg-config culture, and a second CI matrix — all cost, no
user-visible gain over shipping MinGW-built binaries (ordinary,
dependency-light PE executables any Windows runs).

## Toolchain bring-up

`festina compile hello.f` produces a runnable `.exe`, and the whole
non-graphics, non-audio suite passes under MSYS2 on Windows CI.

1. **Regex.** MSYS2's `libsystre` package provides POSIX `<regex.h>`/
   `regcomp`/`regexec` (a wrapper around TRE) — pkg-config asks for it
   under the name `gnurx` (`libsystre`'s own package declares itself a
   drop-in replacement for the old `libgnurx`, and ships its pkgconfig
   file under that old name). `_core_pkgs`'s win32-only addition
   installs `libsystre`, asks pkg-config for `gnurx`. `gnurx`'s ERE
   behavior matches glibc's under the existing (platform-neutral)
   regex test suite.
2. **`.exe` awareness in `festina/cli.py`.** `_default_output_name`
   appends `.exe` on `win32` (and `festina run` invokes it
   accordingly). `_rename_if_linker_appended_exe` runs after linking
   and renames the linker's real output back to the exact name the
   caller asked for — MinGW's linker silently appends `.exe` to a
   `-o` name that lacks one, even when the caller asked for an
   explicit extensionless name.
3. **`festina/llvm_backend.py` finds libLLVM's DLL** via
   `_platform_libllvm_paths`, covering the MSYS2 candidates
   (`$MSYSTEM_PREFIX`, the UCRT64/MinGW64/CLANG64 roots); the clang
   fallback (MSYS2 clang consumes the generated `.ll` directly) covers
   the gap regardless, exactly as on macOS.
4. **`festina doctor`** reports Windows-specific lines: POSIX regex as
   a REQUIRED line (checked via pkg-config's `gnurx` name but hinting
   the real package to install, `libsystre`), and detection of the
   plain `MSYS` shell (as opposed to UCRT64/MINGW64/CLANG64) via
   `$MSYSTEM`, since only UCRT64 is supported. The pacman one-liner
   (`pacman -S mingw-w64-ucrt-x86_64-{clang,sqlite3,pkgconf,libsystre}`)
   is that hint's actual text.
5. **CI**: a `windows-latest` job via the `msys2/setup-msys2` action
   runs the whole suite headless the same way the macOS job does, plus
   compiling and running the four windowless examples as real `.exe`s.
   The sanitizer leak tier stays Linux-only, same reasoning as macOS.
6. **Filesystem semantics**: every runtime `fopen` is binary-mode
   (`"rb"`/`"wb"`/`"ab"`), so blobs and `save()` round-trip
   byte-identically with no CRLF hazard, and the CRT accepts the
   forward-slash paths the examples use
   (`tests/test_platform.py::TestBinaryFidelity`, runs on every
   platform's CI). `stdout` needs separate handling: the MinGW/UCRT
   CRT opens the standard streams in TEXT mode by default,
   independently of any `fopen` flag, silently rewriting every `\n` a
   compiled program prints to `\r\n` — handled in
   `festina_runtime_init()` (`_setmode(_fileno(stdout), _O_BINARY)`,
   `#ifdef _WIN32`, a no-op everywhere else), called unconditionally
   as the first thing every compiled program's `main()` does.

## Audio

Built on the shared 3-function device seam (`festina_pcm_open/write/
close`) macos.md documents — the channel pool, WAV parser, mpg123
decoding, and pthread use all compile under MinGW unchanged.

The Windows implementation is **waveOut** (winmm — plain C, shipped
with Windows since forever, no COM): `waveOutOpen` per channel,
`waveOutWrite` of prepared `WAVEHDR` blocks, and a condition variable
counting free blocks reproduces ALSA's blocking push exactly — the
same N-buffers-plus-blocking-primitive shape the macOS AudioQueue shim
uses (a `pthread_cond_t` here rather than a semaphore, since
MinGW-w64's UCRT pthreads already ship and a condvar is the more
direct match for the "wait until `free_count > 0`" shape). WASAPI is
deliberately not the target: it is COM-based, event-driven, and buys
latency Festina's `play()`/`stop()` surface doesn't expose. Link:
`-lwinmm`.

Windows always software-mixes, so — like CoreAudio — the EBUSY
`free_oldest` retry loop never fires. The channel-pool white-box
harnesses run on Windows CI as-is; `FESTINA_AUDIO_NULL=1` covers
end-to-end play/stop/isPlaying tests with no audio device.

`windows-latest` CI runners have no audio device at all —
`waveOutOpen` fails outright (`MMRESULT 2`). This is why
`FESTINA_ENABLE_WINDOWS_AUDIO=1` stays required: un-gating audio would
make every test that opens a real device fail permanently in CI, not
just skip. `festina doctor` reports this status the same way the
macOS audio gate does.

## Graphics

Built on the shared windowing seam (`festina_window_open/close`,
`festina_window_present`, `festina_window_events_wait(timeout)`,
`festina_window_events_drain(handler)` emitting normalized events)
macos.md documents. All drawing stays in portable Cairo (MSYS2's
`cairo` package), libjpeg decoding unchanged (`libjpeg-turbo`).

The Windows layer is one C file (`festina_runtime_window_win32.c` —
plain C, no Objective-C-style split needed):

- **Window**: `RegisterClassEx`/`CreateWindowEx`/`ShowWindow`, a
  borderless `WS_POPUP` window (no title bar/border/system menu — the
  same "canvas, nothing else" look the X11 and Cocoa backends both
  request, so the requested width/height is the client size directly
  on every platform); `WM_CLOSE` feeds the normalized close event by
  pushing CLOSE and returning 0 without calling `DefWindowProc`,
  letting shared code decide via `on close` and then
  `festina_window_close()`, exactly like the other two backends.
- **Present**: the Cairo ARGB32 image surface is exactly a 32bpp
  top-down DIB — `StretchDIBits` from `WM_PAINT`, no cairo-win32
  backend needed (the same blit shape as the mac CGImage path, since
  the seam's `present` takes the image surface on every platform).
- **Event loop**: `events_wait(timeout)` is
  `MsgWaitForMultipleObjects` with the timer deadline as its
  millisecond timeout — the Win32 analog of `select` on the X
  connection fd and the Cocoa backend's own peek-with-timeout — and
  `events_drain` is the `PeekMessage`/`TranslateMessage`/
  `DispatchMessage` pump, which invokes the WndProc callback that
  pushes input into a small ring buffer (the same push-then-drain
  shape the Cocoa backend uses, since Win32 input, like Cocoa's, is
  callback-driven).
- **Input**: `WM_LBUTTONDOWN/UP`, `WM_MOUSEMOVE`, `WM_KEYDOWN/UP`. No
  separate `WM_CHAR` handler: `WM_CHAR` only ever fires for the down
  half of a press, which would leave `keyUp` unable to report the same
  text a matching `keyDown` did — `ToUnicode` (virtual-key code +
  scancode + current keyboard state) computes the identical
  shift-aware character synchronously, for both halves. Key names map
  from virtual-key codes to the shared key-name vocabulary macos.md
  pins. Autorepeat matches natively: `WM_KEYDOWN` repeats while held
  (bit 30 of `lParam` distinguishes a repeat), one `WM_KEYUP`.
  Left/right Shift/Control/Alt need their own scancode-based
  disambiguation (`WM_KEYDOWN`/`WM_KEYUP` report only the generic
  `VK_SHIFT`/`VK_CONTROL`/`VK_MENU` otherwise) — a standard Win32
  technique.

Unlike macOS, GitHub's Windows runners can create real Win32 windows
(no Xvfb equivalent needed), so windowed graphics is not gated behind
an env var — window creation/rendering is confirmed clean on real
Windows CI. Two tests that assert a "no display available" error don't
apply on Windows at all (`windows-latest` always has a live desktop
session) and are skipped there rather than asserting a condition that
cannot occur. What remains open is narrower: this project's test suite
has no Windows equivalent of the Xvfb-backed `x_display`/`xdotool`
fixtures the windowed-input tests (click/key/resize/close coverage)
run under, so automated confirmation that keyboard/mouse/resize/close
behave identically to Linux against the pinned event vocabulary awaits
either new Windows-native test infrastructure or real hardware.

## Packaging

1. `scripts/package_compiler.sh` (bash, runs under MSYS2) detects
   MSYS2 via bash's own `OSTYPE` (`"msys"`): PyInstaller on Windows
   emits `festina.exe`, and the script's `--add-data` separator
   switches from `:` to `;` there (a documented PyInstaller platform
   difference).
2. **DLL story for compiled programs**, decided per tier:
   `-static-libgcc` unconditionally, plus a probed static
   `-lwinpthread` (reusing the same `_can_link` probe-then-fallback
   machinery the static-sqlite path uses) whenever a program does NOT
   use `aud` (audio already links winpthread dynamically via its own
   `-pthread` flag, and stacking a second static one risks a
   link-order conflict). This makes a core-only program (`hello.exe`)
   copy-anywhere; graphics/audio programs instead ship alongside their
   cairo/jpeg/mpg123 DLLs, or run from an MSYS2 shell — documented in
   [setup.md](setup.md). Pinned by an `ldd`-equivalent test
   (`objdump -p | grep 'DLL Name'`,
   `TestOnWindows::test_core_only_binary_has_no_msys2_runtime_dll_dependency`),
   mirroring `TestSlimBinaries`.
3. [setup.md](setup.md) has a real Windows section — the MSYS2
   environment to use (UCRT64), the pacman one-liner per feature tier,
   and the explicit MSVC-unsupported statement.

A "package and smoke-test the standalone compiler binary" Windows CI
step mirrors the Linux/macOS jobs' own, verifying the whole chain on
every push.

## HTTP/WebSocket

`festina_runtime_http.c` is plain POSIX sockets end to end, with no
per-platform "device" abstraction the way audio/graphics have —
porting it means going through every socket call site directly. A
single file, one `#ifdef _WIN32` seam near the top (`FestinaSocket`,
`FESTINA_INVALID_SOCKET`, `festina_close_fd`, `festina_poll`/
`FestinaPollFd`, `festina_socket_would_block`/
`festina_socket_was_interrupted`), mirroring `festina_runtime_audio.c`'s
own ALSA-vs-waveOut split (a small per-platform difference handled
inline) rather than a second whole-file duplicate. Winsock2 differs
from BSD sockets in exactly enough places to matter:

- A distinct `SOCKET` handle type, and it's unsigned — every
  POSIX-style `if (fd < 0)` error check silently never fires on it.
  `FESTINA_INVALID_SOCKET` (`INVALID_SOCKET` on Windows, `-1` on
  POSIX) and `==` comparisons replace every such check.
- `closesocket()` not `close()`, `ioctlsocket()`/`FIONBIO` not
  `fcntl()`/`O_NONBLOCK`, `WSAGetLastError()` instead of `errno`
  (Winsock functions never touch the CRT's `errno` at all), and
  `recv()`/`send()` taking `char*`/`int` where POSIX takes
  `void*`/`size_t`.
- `WSAPoll()` not `poll()` — identical field names (`.fd`/`.events`/
  `.revents`) to POSIX `struct pollfd`, so one typedef swap
  (`FestinaPollFd`) covers every call site.
- No `SIGPIPE` on Windows for a broken socket at all — `send()` just
  returns an error, never a signal — so the POSIX
  `signal(SIGPIPE, SIG_IGN)` mitigation (see [security.md](security.md))
  has nothing to mirror there; every write already checks its own
  return value regardless.
- An explicit `WSAStartup()` is needed before any socket call, called
  from `festina_open_port`'s own entry point, idempotent by design
  (Winsock reference-counts it internally) so it's safe to call on
  every `openPort()`. No matching `WSACleanup()` — process exit tears
  everything down.
- `SO_REUSEADDR` has a more permissive, port-hijacking-enabling
  meaning on Windows than POSIX, so it is deliberately not set there
  at all.

`_feature_pkgs_and_flags`'s win32 branch links `-lws2_32` (a system
DLL with an import library but no pkg-config file, the same shape
`winmm`/`gdi32`/`user32` already are for audio/graphics).

Confirmed on real Windows CI: `openPort()`/`on request`/`on upgrade`/
`on message`/`on socketClose` all test clean end to end — no gate
needed.

**Graceful shutdown has one Windows-specific gap.** Windows has no
real `SIGTERM` delivery at all: `subprocess.terminate()` (what
`SIGTERM` maps to on Windows) force-kills the process outright — exit
code 1, not the conventional 143, `on exit()` never runs, and an
in-flight connection can see a raw `ConnectionResetError` rather than
finishing within the grace period. See
[api.md](api.md#graceful-shutdown) — `openPort()` itself works fine;
this is a narrower "abrupt shutdown skips the grace period" gap, not a
missing feature. `SIGINT`/Ctrl-C is registered the same way on every
platform and the CRT does raise it on Windows, but this project's own
test fixtures don't yet exercise it there (Python's
`Popen.send_signal(signal.SIGINT)` needs the child launched with
`CREATE_NEW_PROCESS_GROUP` on Windows, which they don't currently do)
— confirming it end-to-end is open work, along with giving
`SIGTERM`-style draining a real Windows equivalent (there is no
drop-in one; the nearest analog, `SetConsoleCtrlHandler`'s
`CTRL_CLOSE_EVENT`, has a much shorter mandatory response window than
the 10-second grace period this project uses).

## Shared with macOS

The full shared-work list — the seams, the key-name vocabulary, the
test shims, the per-platform cli/llvm_backend structure — lives in
[macos.md](macos.md)'s "Shared with Windows" section, kept in one
place so the two files don't drift.
