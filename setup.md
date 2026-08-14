# Setup

Three different dependency lists, and they're not the same size — this
is the practical payoff of the staged "real compilation, minimal setup"
plan (`claude.md #59`; see [api.md](api.md#compilation-pipeline) for the
pipeline itself, and [security.md](security.md#binary-slimming) for why
the runtime dependency list below is now conditional per program).

## To *use* the compiler from a checkout

`bin/festina program.f` on a fresh system:

| Dependency | Why | Required? |
|---|---|---|
| Python 3 | Runs the compiler frontend itself (`bin/festina` execs `python3 -m festina.cli`) — only if running from source; see the packaged-binary option below to avoid this entirely | Required (unless using the packaged binary) |
| A C compiler (`clang` or `gcc`) | Compiles the runtime and links the final binary | Required (either works) |
| `libsqlite3-dev` (headers) | The runtime's core translation unit does `#include <sqlite3.h>` — needed to compile *any* program, since `festina.sqlite` support (`claude.md #8/#28-31`) is always on | Required |
| `libcairo2-dev` + `libx11-dev` (headers) | Only needed to compile a *program that actually uses graphics* (`claude.md #37/#39`'s `img`/`draw*`/`on click`/etc.) — the graphics runtime translation unit isn't even compiled otherwise | Required only if you'll compile graphics-using programs |
| `libasound2-dev` (headers) | Same story, for `claude.md #38`'s `aud`/`loadAudio()` | Required only if you'll compile audio-using programs |
| `pkg-config` | Locates sqlite3's (and, conditionally, Cairo/X11's/ALSA's) compile/link flags | Required |
| `llvm` (provides `libLLVM`) | Lets `festina/llvm_backend.py` compile IR directly (the fast path, and the one that makes `gcc` usable at all) | Recommended — without it, the C compiler must specifically be `clang`, since only clang can parse the `.ll` IR text this compiler falls back to handing it directly (verified: `gcc` hands it to `ld`, which fails treating it as a corrupt linker script) |

Missing any of these fails with a specific, actionable error (`claude.md
#59`) rather than a raw traceback — naming the tool and how to get it.
If you don't know ahead of time whether every program you'll ever
compile needs graphics/audio, the simplest move is installing all five
system packages up front (below) — a *compiled program* only ends up
depending on the ones it actually uses (that's the whole point of
[security.md](security.md#binary-slimming)'s binary-slimming split);
it's only the *compiler's own build-time* dependency list that's
conditional per program.

Notably absent from this list entirely: anything for
`regex()`/`.test()`/`.match()`/`.replace()` (`claude.md #67/#68`) —
they're built on POSIX extended regular expressions (`<regex.h>`),
already part of libc everywhere this list's C compiler already requires
libc, so regex support adds zero new dependencies. Timers (`claude.md
#69`) are the same story: `clock_gettime`/`nanosleep`/`select` are all
POSIX, already part of libc too.

Debian/Ubuntu:

```bash
sudo apt install clang libsqlite3-dev libcairo2-dev libx11-dev libasound2-dev pkg-config
```

`clang` conveniently pulls in `libLLVM` as a dependency, covering both
the fast path and its fallback in one line. (`gcc` works too for the
fast path, but only if `libLLVM` is separately present — `clang` is the
simpler single recommendation.) macOS (Homebrew) should be similar in
spirit — `brew install llvm sqlite pkg-config` — though native macOS
support isn't there yet; see [todo.md](todo.md).

## To *use* a packaged `festina` binary

Built via `./scripts/package_compiler.sh`, or downloaded from wherever a
maintainer published one: the same list above, minus Python 3 — the
packaged binary embeds its own interpreter, verified by actually running
it with every `python`/`python3*` on `PATH` replaced by a command that
always fails (`tests/test_packaging.py`). Building the binary yourself
needs one more thing, PyInstaller — a build-time-only dependency, not
something the resulting binary or `festina/` itself needs:

```bash
pip install -r requirements-build.txt  # pyinstaller
./scripts/package_compiler.sh          # -> ./dist/festina
./dist/festina examples/hello.f -o hello
```

## To *run* a program someone already compiled with Festina

This is the list that shrank: a compiled program only dynamically links
what it actually uses. Every program needs libc/libm (plus
`libsqlite3.so`, conditionally — see "Static-linking sqlite3" below);
a program that never uses `claude.md #37/#39`'s graphics functions never
links `libcairo.so`/`libX11.so` and their own transitive dependencies
(fontconfig, freetype, libpng, the X11 client-side stack, ...) at all,
and one that never uses `claude.md #38`'s audio never links
`libasound.so` — confirmed directly with `ldd` on real compiled
binaries, not just reasoned about (see
[security.md](security.md#binary-slimming) for the full story, including
why this needed the runtime split into separate translation units rather
than just optimizer flags). Check any specific binary with `ldd` to see
exactly what it needs:

```bash
ldd ./your_compiled_program
```

### Static-linking sqlite3

A Festina program built here doesn't need `libsqlite3.so` present at
runtime if a static `libsqlite3.a` archive was available in the *build*
environment (falls back to a normal dynamic link otherwise) — check
`festina: wrote ...` compiler output, which notes when it fell back.

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
| `Xvfb` + `xdotool` | Interactive graphics tests — clicking, moving the mouse, pressing keys, resizing a real (virtual) window (`TestGraphics`, plus `TestTimers`'s combined graphics+timers case; 6 tests) | `sudo apt install xvfb xdotool` on Debian/Ubuntu — a real `$DISPLAY` works too, if one is already available |

Every one of those skips cleanly and independently when its tool isn't
present — the suite still passes, just with fewer tests run. Audio's
tests (`tests/test_audio.py`) need none of the above: the null-device
technique they use (see `conftest.py`'s `audio_null_env`) needs no extra
tool install, only the same C compiler everything else here requires.

```bash
bin/festina examples/hello.f -o hello
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
