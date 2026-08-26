# Setup

Three different dependency lists, and they're not the same size: the
compiler frontend, a compiled program's own runtime dependencies, and a
packaged binary's needs are genuinely different sets — see
[api.md](api.md#compilation-pipeline) for the pipeline itself, and
[security.md](security.md#slim-binaries) for why the runtime dependency
list below is conditional per program.

## Installing with one command

```bash
curl -fsSL https://raw.githubusercontent.com/uraikus/festina/main/install.sh | sh
```

`install.sh` (Linux, macOS, and MSYS2 UCRT64 bash on Windows — the one
supported Windows toolchain/shell; a native PowerShell installer would
first need to bootstrap MSYS2 itself) clones a fresh checkout into
`$FESTINA_INSTALL_DIR` (default `~/.festina`; re-running it updates an
existing one in place), then hands off entirely to `festina doctor
--fix` below for both dependency checking/installing and adding
`festina` to `PATH` — there's no prebuilt binary to download here;
installing from source is how the one-line install works. `--yes`/`-y`
as a script argument skips every confirmation prompt, for a fully
non-interactive install.

## To *use* the compiler from a checkout

`bin/festina compile program.f` on a fresh system (or `bin/festina run
program.f` to skip the intermediate binary and just run it -- see
[api.md](api.md#cli) for the full command list, including `festina
doctor`, which checks every dependency below for you and tells you
exactly what's missing -- and `festina doctor --fix`, which installs
it for you via whichever of `apt`/Homebrew/MSYS2's `pacman` this
machine has and adds `festina` to `PATH` too, after confirming each
change with you first):

| Dependency | Why | Required? |
|---|---|---|
| Python 3 | Runs the compiler frontend itself (`bin/festina` execs `python3 -m festina.cli`) — only if running from source; see the packaged-binary option below to avoid this entirely | Required (unless using the packaged binary) |
| A C compiler (`clang` or `gcc`) | Compiles the runtime and links the final binary | Required (either works) |
| `libsqlite3-dev` (headers) | The runtime's core translation unit does `#include <sqlite3.h>` — needed to compile *any* program, since `festina.sqlite` support is always on | Required |
| `libcairo2-dev` + `libx11-dev` + `libjpeg-dev` (headers) | Only needed to compile a *program that actually uses graphics* (`img`/`draw*`/`on mouseDown`/etc.) — the graphics runtime translation unit isn't even compiled otherwise. `libjpeg` decodes JPEG; Cairo handles PNG on its own | Required only if you'll compile graphics-using programs |
| `libasound2-dev` + `libmpg123-dev` (headers) | Same story, for `aud` — ALSA for playback, `libmpg123` for MP3 decoding (WAV is parsed directly, with no library at all) | Required only if you'll compile audio-using programs |
| `libmbedtls-dev` (headers) | Same story, for `openSecurePort()` — mbedTLS 2.x provides the TLS handshake/record layer; the `festina_runtime_https.c` translation unit isn't even compiled otherwise | Required only if you'll compile programs that call `openSecurePort()` |
| `pkg-config` | Locates sqlite3's (and, conditionally, Cairo/X11's/libjpeg's/ALSA's/libmpg123's/mbedTLS's) compile/link flags | Required |
| `llvm` (provides `libLLVM`) | Lets `festina/llvm_backend.py` compile IR directly (the fast path, and the one that makes `gcc` usable at all) | Recommended — without it, the C compiler must specifically be `clang`, since only clang can parse the `.ll` IR text this compiler falls back to handing it directly (`gcc` hands it to `ld`, which fails treating it as a corrupt linker script) |

Missing any of these fails with a specific, actionable error rather
than a raw traceback — naming the tool and how to get it. If you don't
know ahead of time whether every program you'll ever compile needs
graphics/audio, the simplest move is installing all seven system
packages up front (below) — a *compiled program* only ends up
depending on the ones it actually uses (that's the whole point of
[security.md](security.md#slim-binaries)'s binary-slimming split);
it's only the *compiler's own build-time* dependency list that's
conditional per program.

Notably absent from this list entirely: anything for
`regex()`/`.test()`/`.match()`/`.replace()` — they're built on POSIX
extended regular expressions (`<regex.h>`), already part of libc
everywhere this list's C compiler already requires libc, so regex
support adds zero new dependencies. Timers are the same story:
`clock_gettime`/`nanosleep`/`select` are all POSIX, already part of
libc too.

Debian/Ubuntu:

```bash
sudo apt install clang libsqlite3-dev libcairo2-dev libx11-dev libjpeg-dev \
                 libasound2-dev libmpg123-dev libmbedtls-dev pkg-config
```

`clang` conveniently pulls in `libLLVM` as a dependency, covering both
the fast path and its fallback in one line. (`gcc` works too for the
fast path, but only if `libLLVM` is separately present — `clang` is the
simpler single recommendation.)

macOS (Homebrew):

```bash
xcode-select --install                                # Xcode >= 15 -- the floor
brew install pkg-config sqlite cairo jpeg-turbo mpg123 mbedtls # graphics + audio + TLS tiers
```

There's no `llvm` line: Apple clang, from the CommandLineTools Xcode
already installs, consumes the generated LLVM IR (`.ll`) directly,
including its opaque-`ptr` form — brew's own (very large) `llvm` bottle
is unnecessary here, and the libLLVM fast path is a Linux-only
convenience covered by the Debian/Ubuntu line above. brew's `sqlite` is
keg-only, so its `.pc` file needs `PKG_CONFIG_PATH` set explicitly:

```bash
export PKG_CONFIG_PATH="$(brew --prefix sqlite)/lib/pkgconfig:$PKG_CONFIG_PATH"
```

No `XQuartz` and no X11 of any kind: graphics on macOS is a native
Cocoa window (`runtime/festina_runtime_window_mac.m`), not an X11
server running under emulation, so there's nothing X11 to install and
no window server other than the one macOS already runs. Both the audio
(AudioQueue) and graphics (Cocoa) backends compile on every push, but
stay gated behind `FESTINA_ENABLE_MACOS_AUDIO=1` /
`FESTINA_ENABLE_MACOS_GRAPHICS=1` until confirmed on real hardware —
compiling an audio- or window-opening program on darwin without the
relevant env var fails with a specific error naming the gate, exactly
like the missing-tool errors above; `festina doctor` reports the same
status.

Windows (MSYS2 UCRT64) — this is the one and only supported Windows
toolchain, and **MSVC is explicitly out of scope**:

```bash
# From an MSYS2 UCRT64 shell specifically -- not the plain MSYS shell
# (festina doctor flags that one as the wrong one) and not
# MINGW64/CLANG64 either.
pacman -S mingw-w64-ucrt-x86_64-clang mingw-w64-ucrt-x86_64-python \
          mingw-w64-ucrt-x86_64-sqlite3 mingw-w64-ucrt-x86_64-pkgconf \
          mingw-w64-ucrt-x86_64-libsystre                  # core -- required
pacman -S mingw-w64-ucrt-x86_64-cairo \
          mingw-w64-ucrt-x86_64-libjpeg-turbo               # graphics tier
pacman -S mingw-w64-ucrt-x86_64-mpg123                      # audio tier
pacman -S mingw-w64-ucrt-x86_64-mbedtls                     # TLS tier (openSecurePort)
```

`libsystre` provides MinGW-w64's UCRT with POSIX `<regex.h>` support
(the UCRT itself doesn't ship one) — pkg-config asks for it under the
OLD name `gnurx`, not `libsystre` (a real, and non-obvious, package-
name-vs-pkg-config-name split; `festina doctor` explains it if
missing). No `llvm` line here either, for the same reason as macOS
above: `mingw-w64-ucrt-x86_64-clang` already covers both the fast path
and its fallback, no separate libLLVM package needed.

Graphics (Win32) and HTTP/WebSocket (winsock2) both work out of the
box. Audio (waveOut) is the one tier still gated behind
`FESTINA_ENABLE_WINDOWS_AUDIO=1` — not for lack of code, but because
`windows-latest` GitHub Actions runners have no audio device at all, so
this gate awaits confirmation on real hardware with one;
`festina doctor` reports its status the same way the macOS audio gate
just above does. Graceful shutdown (Ctrl-C/`SIGTERM` → `on exit()`,
see [api.md](api.md#graceful-shutdown)) has one narrower Windows gap
of its own: Windows has no real `SIGTERM` delivery, so the
connection-drain grace period only applies to Ctrl-C there.

### The DLL story for compiled Windows programs

A MinGW-built program can depend on a handful of MSYS2 runtime DLLs
that aren't part of a bare Windows install. Festina's compiler
statically links two of them into every Windows binary it produces —
`-static-libgcc` always, plus a probed static `-lwinpthread` whenever
the program doesn't use `aud` (audio already links winpthread
dynamically via its own `-pthread` flag, so this is skipped rather
than risking a link-order conflict) — so a core-only or
offscreen-graphics-only program (`hello.exe`, and anything that never
calls `aud`) is genuinely copy-anywhere: no MSYS2 install needed on
the machine that *runs* it, only on the one that *compiled* it. A
program that uses graphics, audio, or openSecurePort() still needs its own feature DLLs
findable at runtime (`libcairo-2.dll`, `libjpeg-8.dll`,
`libmpg123-0.dll`, `libmbedtls.dll` and friends, ...) — either run it from an MSYS2 UCRT64 shell
(already on `PATH` there) or copy them alongside the `.exe` from
`/ucrt64/bin`. Check any specific binary's own dependencies with
`objdump -p your_program.exe | grep 'DLL Name'` — the Windows analog
of `ldd` used below.

## To *use* a packaged `festina` binary

Built via `./scripts/package_compiler.sh`, or downloaded from wherever a
maintainer published one: the same list above, minus Python 3 — the
packaged binary embeds its own interpreter. Building the binary yourself
needs one more thing, PyInstaller — a build-time-only dependency, not
something the resulting binary or `festina/` itself needs:

```bash
pip install -r requirements-build.txt  # pyinstaller
./scripts/package_compiler.sh          # -> ./dist/festina (./dist/festina.exe on Windows)
./dist/festina compile examples/hello.f -o hello
```

On macOS the script also ad-hoc codesigns the result (`codesign -s -`)
so Gatekeeper allows running it locally without a prompt — a
self-signature, not an identity; it doesn't make the binary trusted on
anyone else's machine. Distributing to other people's Macs is a
separate, deliberately out-of-scope decision (real Developer-ID
signing + notarization) that only matters once there's an actual
distribution channel.

On Windows, `--add-data` needs a `;` between source and destination
rather than `:` — a real PyInstaller platform difference the script
itself detects and handles, nothing to do by hand — and the resulting
binary is `festina.exe`, automatically, the same way MinGW's linker
already appends `.exe` to every OTHER compiled Festina program.

## To *run* a program someone already compiled with Festina

This is the list that shrank: a compiled program only dynamically links
what it actually uses. Every program needs libc/libm (plus
`libsqlite3.so`, conditionally — see "Static-linking sqlite3" below);
a program that never uses graphics functions never links
`libcairo.so`/`libX11.so` and their own transitive dependencies
(fontconfig, freetype, libpng, the X11 client-side stack, ...) at all,
and one that never uses audio never links `libasound.so` — confirmed
with `ldd` on real compiled binaries (see
[security.md](security.md#slim-binaries) for the full story, including
why this needed the runtime split into separate translation units rather
than just optimizer flags). Check any specific binary with `ldd` to see
exactly what it needs:

```bash
ldd ./your_compiled_program
```

On Windows, `ldd` isn't a MinGW/Windows concept — use
`objdump -p your_program.exe | grep 'DLL Name'` instead (see the DLL
story note above: a core-only or offscreen-graphics-only program needs
nothing beyond what a bare Windows install already has).

### Static-linking sqlite3

A Festina program built here doesn't need `libsqlite3.so` present at
runtime if a static `libsqlite3.a` archive was available in the *build*
environment (falls back to a normal dynamic link otherwise) — check
`festina: wrote ...` compiler output, which notes when it fell back.

### Compiling to WASM

`festina compile --target=wasm32-wasi program.f -o program.wasm` (or
`festina run --target=wasm32-wasi program.f`) cross-compiles to a
standalone `wasm32-wasi` binary instead of a native executable — see
[wasm.md](wasm.md) for the full design writeup, setup, and known
limitations (graphics/audio aren't available under WASI at all).

## Running the test suite

```bash
pip install -r requirements-dev.txt   # pytest
pytest tests/                         # see counts below
```

Some tests need extra tools that aren't Python packages, so they're not
in any requirements file:

| Extra tool | Needed for | Install |
|---|---|---|
| `pyinstaller` | `tests/test_packaging.py` (2 tests) | `pip install -r requirements-build.txt` |
| `Xvfb` + `xdotool` + `xwd` | Interactive graphics tests — clicking, moving the mouse, pressing keys, resizing a real (virtual) window, and reading canvas pixels back to check that colours and fonts actually render (`TestGraphics`, `TestCanvasStyleRendersRealPixels`, plus `TestTimers`'s combined graphics+timers case) | `sudo apt install xvfb xdotool x11-apps` on Debian/Ubuntu (`xwd` ships in `x11-apps`) — a real `$DISPLAY` works too, if one is already available |
| `openbox` (+ optional `xprop`, from `x11-utils`) | One regression test for a window-manager interaction a bare Xvfb instance (no WM at all) can never reproduce (`TestGraphics::test_graphics_init_does_not_crash_under_a_real_window_manager`; 1 test) | `sudo apt install openbox x11-utils` on Debian/Ubuntu — `xprop` is only used to poll for the WM's own readiness signal instead of a fixed sleep; the test still runs (with a fixed sleep instead) without it |
| `wasi-libc` + `libclang-rt-*-dev-wasm32` + Node.js | Real compile-and-run WASM export tests (`tests/test_wasm.py`'s `TestWasmRun`; see [wasm.md](wasm.md)) | `sudo apt install wasi-libc libclang-rt-18-dev-wasm32` on Debian/Ubuntu (substitute your clang's own version), plus Node.js on PATH — `festina doctor` reports whether both are present |

Every one of those skips cleanly and independently when its tool isn't
present — the suite still passes, just with fewer tests run. Audio's
tests (`tests/test_audio.py`) need none of the above: the null-device
technique they use (see `conftest.py`'s `audio_null_env`) needs no extra
tool install, only the same C compiler everything else here requires.

```bash
bin/festina compile examples/hello.f -o hello
./hello
```

## Benchmark toolchains (optional)

Only needed to run [benchmark.md](benchmark.md)'s comparisons yourself
— not a dependency of Festina itself:

| Tool | Install |
|---|---|
| `rustc` | [rustup.rs](https://rustup.rs) |
| `go` | [go.dev/dl](https://go.dev/dl/) |
| `bun` | [bun.sh](https://bun.sh) |

```bash
python3 benchmarks/run_benchmarks.py --update-doc
```

skips any of the three not installed rather than failing.
