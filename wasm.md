# WASM export

**Implemented and tested.** `festina compile --target=wasm32-wasi` and
`festina run --target=wasm32-wasi` cross-compile a Festina program to a
standalone `wasm32-wasi` binary — arithmetic, control flow, functions,
structs, `arr`/`map`, `text`, `regex`, and `table`/`sqlite()` (SQLite
itself, vendored, compiled for wasm32-wasi — see
[`runtime/wasm/README.md`](runtime/wasm/README.md)) all work, verified
by real compiles and real executions, not just a clean link. `festina
doctor` reports whether this machine's toolchain can build one; the
`linux` CI job installs that toolchain and Node and actually runs a
compiled `.wasm` example on every push (`.github/workflows/ci.yml`).

**What's out of scope, on purpose:** graphics and audio (WASI has no
display server or audio device model at all — not "not yet", genuinely
absent) — see [Limitations](#limitations) below for the full
accounting. A compiled `.wasm` runs in a browser tab too, on this
project's own WASI host — see [In a browser](#in-a-browser).

This file is the design writeup, implementation record, and benchmark
results, kept current as a reference.

## Why wasm32-wasi, not bare wasm32

Two wasm targets exist in LLVM/clang: `wasm32-unknown-unknown` (no
libc, no syscalls — every I/O call needs hand-written JS glue supplied
by whatever embeds the module) and `wasm32-wasi` (WASI Preview 1 — a
real libc, via [wasi-libc](https://github.com/WebAssembly/wasi-libc),
sitting on top of a small, capability-based syscall interface). Festina's
core runtime (`festina_runtime.c`) is ordinary POSIX C — file I/O,
`clock_gettime`, `regex.h`, SQLite's own VFS layer — none of which
`wasm32-unknown-unknown` provides any answer for at all.
`wasm32-wasi` is the only target where the *existing* runtime source
compiles with zero changes: the whole core translation unit compiles
against wasi-libc unmodified. The codegen and linking work this target
needs (below) is real, but narrow — a 32-bit pointer-width fix and a
one-line entry-point bridge, not a rewrite of how Festina generates
code.

## Design

**Compiling:** `_compile_via_wasm` in `festina/cli.py` hands the same
LLVM IR text every other target's `_compile_via_clang_ir_frontend`
fallback path already produces straight to
`clang --target=wasm32-wasi -O2 -flto`, linked against three object
files (`_wasm_runtime_objects`):

1. **`festina_runtime.c`**, compiled against the vendored
   `runtime/wasm/sqlite3.h` instead of a system one — core, the same
   translation unit every native target uses. Compiled to LLVM bitcode
   (`-flto`) so the link optimizes across the program/runtime boundary
   — see [Binary size](#binary-size) below for why that matters.
2. **`runtime/wasm/sqlite3.c`** — SQLite's own single-file amalgamation
   build, vendored because the core runtime's table/`sqlite()` support
   is compiled against sqlite3 symbols and there is no system
   `libsqlite3` for `wasm32-wasi` to link against at all — see
   [`runtime/wasm/README.md`](runtime/wasm/README.md) for exactly why
   vendoring it (something this project doesn't do for native targets,
   which link the system's own libsqlite3) is the only option here. A
   plain `-O2` object, not bitcode: it is the one translation unit
   whose ~20-second compile the object cache exists to amortize, and
   the linker drops every function of it from a program that never
   touches a database (below).
3. **`runtime/festina_runtime_wasm_entry.ll`** — a small entry-point
   bridge, see [below](#the-main-entry-point) and
   [below](#why-the-entry-bridge-is-raw-ir-not-c).

### Binary size

A `.wasm` for a program that never declares a `table` or calls
`sqlite()` is about **31 KB** (`hello`); one that does is about
1.1 MB, almost all of it SQLite. Two things made the first number
possible, both found by measuring a 1.47 MB `hello.wasm`
(claude.md #242):

- **SQLite is dead-code eliminated when nothing uses it.** `wasm-ld`
  drops unreferenced functions by default, but the whole ~1 MB of
  SQLite was kept alive by exactly one reference: `main()`'s closing
  `festina_db_close()` on a database handle that is null for every
  such program. The linker cannot see through a call into a
  separately compiled object, so it kept the call, the function, and
  everything SQLite it reached. With the core runtime as bitcode and
  `-flto` on the link, that call folds away; codegen now also omits it
  outright for a program with no database, so the result does not
  hinge on the optimizer.
- **No DWARF.** The sysroot's own `libc.a` ships with debug sections,
  and they were being copied into every output — 375 KB that nothing
  reads. The link passes `--strip-debug`, which removes them but keeps
  the `name` section, so a browser's stack trace still names the wasm
  function it was in.

A smaller module also loads faster: Node (and a browser) compiles the
whole module before the first instruction runs, about 6 ms for the old
1.47 MB. The generated code itself runs at the same speed either way.
If a toolchain's `wasm-ld` was built without LTO support the link is
retried without `-flto` (SQLite then stays in), rather than failing
over an optimization.

No libLLVM in-process object-emission path is used for wasm at all
(unlike native, where it's the fast path when available) — this
project has not verified libLLVM can emit wasm32 objects directly, so
`clang` itself always does the compiling, the same "hand clang the .ll
text" fallback native already has, just unconditional here rather than
a fallback.

**Running:** a `.wasm` file isn't something any OS execs directly.
`festina run --target=wasm32-wasi` and the benchmark runner both
execute a compiled binary through
[`runtime/wasm/run_wasi.mjs`](runtime/wasm/run_wasi.mjs), a small
script built on Node's own built-in `node:wasi` module (Node is
already a listed [setup.md](setup.md) dependency for running the
compiler frontend itself from a checkout, so this adds no *new*
dependency for that path — though a packaged `festina` binary user
does need Node specifically installed to run a `.wasm` it produces).
The invoking shell's own working directory is passed through as a WASI
"preopen", mapped to the wasm program's own `/` — this is what lets
`table`/`sqlite()`, `blob`, `mkdir`/`ls` do real file I/O inside the
sandbox, resolving relative paths against the same directory a native
compiled binary already would.

### Pointer width

Every native target Festina supports is 64-bit; `wasm32-wasi`'s libc
has a 32-bit `size_t`, and LLVM requires an external `declare` to match
its call sites exactly, with no implicit truncation. `CodeGen.__init__`
takes a `target` parameter and tracks `self.pointer_bits` (32 for
`wasm32-wasi`, 64 everywhere else); three helpers (`_size_arg`,
`_emit_calloc`, `_emit_malloc`) either pass a size straight through
(64-bit targets — a byte-identical no-op there) or emit a `trunc i64
... to i32` first for the `calloc`/`malloc` call-site ABI boundary
specifically. Every other `i64`/pointer conversion elsewhere in
codegen.py (`ptrtoint`/`inttoptr`) is genuinely safe across pointer
widths in LLVM and needs no target-specific handling.

### The `main` entry point

wasi-libc's own `_start` doesn't call a function literally named
`main` — ordinary C compilation silently renames a user's `main` to
`__main_void` (a no-arg `main`) or `__main_argc_argv` (an
argc/argv-taking `main`) via macro machinery in the C frontend before
the compiler ever sees it. Festina's codegen emits raw LLVM IR text
directly (`_emit_main_and_entry`), which never goes through that
renaming, so the literal `define i32 @main(...)` it always emits links
clean on every native target (where `main` really is the expected
symbol) but needs a bridge for wasm32-wasi: a small object,
[`runtime/festina_runtime_wasm_entry.ll`](runtime/festina_runtime_wasm_entry.ll),
linked only for the wasm build, that calls the real `main` under its
own name and re-exports the result as `__main_argc_argv`. `main`'s own
signature is `(i32 %argc, ptr %argv)` — the real C ABI entry point on
every native target already — so the bridge's argument list follows
along unchanged.

### Why the entry bridge is raw IR, not C

A C version of the bridge above (`extern int main(int, char **); int
__main_argc_argv(int argc, char **argv) { return main(argc, argv); }`)
compiles and links without error, but hangs at runtime: the same
C-frontend macro that renames a *defined* `int main(int, char**)` to
`__main_argc_argv` for `wasm32-wasi` also rewrites *any reference* to
the literal identifier `main` in a translation unit compiled for that
target — including an `extern` declaration and a call built from it.
A C bridge's own `return main(argc, argv)` gets silently rewritten to
`return __main_argc_argv(argc, argv)` — calling itself, not Festina's
real `main` (visible directly in the object's own relocation record:
`R_WASM_FUNCTION_INDEX_LEB __main_argc_argv+0` at the call site, not a
reference to `main` at all). At `-O0` this produces genuine infinite
recursion; at `-O2` the same self-call becomes a silent infinite
loop — indistinguishable from a hang, no error at all.

Writing the bridge as raw LLVM IR text
(`festina_runtime_wasm_entry.ll`, not `.c`) avoids this entirely: it
bypasses the C frontend (and its renaming macro), so `declare i32
@main(i32, ptr)` there can only ever mean the real external symbol —
confirmed via relocation inspection (`U main`, not `U
__main_argc_argv`) and via actual execution (correct exit code,
correct `argv`, no hang). The alternative of renaming codegen's own
generated `main` symbol was rejected for the same reason the bridge
itself exists: it would make codegen target-aware for something that's
really wasi-libc's own linking convention, not a property of the
generated program.

## Setup

`festina doctor` reports whether this machine can build for
`wasm32-wasi` (optional, like graphics/audio — a compiler that can't
cross-compile to wasm is still a fully working compiler for everything
else). On Debian/Ubuntu:

```bash
apt install wasi-libc libclang-rt-18-dev-wasm32
```

(the compiler-rt package name is tied to your installed clang's own
version — substitute accordingly, e.g. `libclang-rt-19-dev-wasm32` for
clang 19). Once both are installed, plain `clang --target=wasm32-wasi`
auto-discovers wasi-libc's headers/libs at their standard
`/usr/include/wasm32-wasi` and `/usr/lib/wasm32-wasi` locations — no
manual `-I`/`-L`/`--sysroot` flags. `doctor`'s own check is a real
functional probe (compiling a trivial program with that exact flag),
not a guess at install paths, so it stays accurate across distros
where those paths differ. Running a compiled `.wasm` needs Node.js on
PATH, for its built-in WASI host.

## Usage

```bash
festina compile --target=wasm32-wasi program.f -o program.wasm
node runtime/wasm/run_wasi.mjs program.wasm .        # "." = the preopened directory

# or, compile-and-run in one step, same as native `festina run`:
festina run --target=wasm32-wasi program.f
```

`--cc` must resolve to `clang` specifically for a wasm build (checked,
with a clear error) — only clang can target `wasm32-wasi` at all; the
gcc/cc fallback native builds have doesn't apply here.

## In a browser

A browser has no built-in WASI host, so `runtime/wasm/` ships one
(claude.md #237): `festina_wasi_browser.js`, a dependency-free ES
module implementing WASI Preview 1 — every import a compiled Festina
program names — over an in-memory filesystem, with `/` preopened
exactly as `run_wasi.mjs` preopens a real directory. `browser.html` is
the smallest page that uses it: serve the directory over HTTP (module
workers can't load from `file://`) and open

```
browser.html?wasm=program.wasm
```

The program runs in a Web Worker (`festina_wasi_worker.js`), so a
Festina program's synchronous `main()` and its timer loop never block
the page; stdout/stderr stream into the page as they are written, and
when the program exits `window.festinaResult` holds `{code, stdout,
stderr, files}` — `files` being every file the program left in its
sandbox, as bytes. `window.festinaRun(url, {files: {...}})` runs
another program on demand, seeding its filesystem. Timers
(`poll_oneoff`) sleep with `Atomics.wait` when the page is
cross-origin isolated (COOP/COEP headers) and otherwise spin inside
the worker.

The same host runs under Node without a browser —
`node runtime/wasm/run_wasi_js.mjs program.wasm <preopen-dir>`, the
directory loaded into the in-memory filesystem first and written back
afterwards — which is how the host itself is tested independently of
any browser. `tests/test_wasm_browser.py` covers both: files,
directories, SQLite's own database file, timers, `argv`, exit codes and
stderr through the host under Node, and the page itself in headless
Chromium through Playwright (the `linux` CI job installs it; the tests
skip cleanly where it is absent).

Not covered by the host, because WASI itself has none: anything in
[Limitations](#limitations) below. And it is a host for *this
project's* programs, not a general WASI polyfill — a `.wasm` that
imports something no Festina program does gets `ENOSYS` back.

## Limitations

WASI genuinely has no answer for either of these — not a "not yet",
the way macOS/Windows graphics and audio are gated pending real-hardware
verification ([macos.md](macos.md), [windows.md](windows.md)). Both
fail at compile time, before any of the real work
(compiling the vendored SQLite amalgamation, linking) happens:

- **Graphics** — `drawRect`/`drawCircle`/`drawText`/`img`/`render()`,
  mouse and key events, `on mouseDown`/`on key`/... — WASI has no
  display server or windowing model of any kind.
- **Audio** — `aud`, `play()`/`playLoop()`/`stopAudioPlayer()`/
  `isAudioPlayerPlaying()` — WASI has no audio device model of any
  kind.
- **`exec()`** — WASI has no process model to spawn into at all: no
  fork/exec/spawn of any kind.
- **`openPort()`/`on request`/`on upgrade`/`on message`/`on
  socketClose`** — WASI Preview 1 has no listening-socket support of
  any kind.
- **`openSecurePort()`** — needs everything `openPort()` needs plus
  mbedTLS, so it's rejected for the identical reason.
- **`try`/`catch`/`throw`** — wasi-libc has no setjmp/longjmp at all
  (they need WebAssembly exception handling, which this project's plain
  wasm32-wasi build doesn't use), and a `try` is a direct call to
  libc's `setjmp` (claude.md #235). `.toStruct()`/`.toArr()` are *not*
  affected (claude.md #233 — their cleanup is plain runtime C, not a
  catch frame): they compile and run here, and a parse failure ends
  the program the way any uncaught `throw` does, since there is no
  `try` to catch it.

A few more things worth knowing, that aren't compile-time errors:

- **`argv` always comes back as a single element.** `argv` works under
  wasm32-wasi — WASI has its own real argc/argv, and `main`'s bridge
  forwards it the same way it does natively — but `run_wasi.mjs`
  hardcodes WASI's own `args` to `[wasmPath]` and nothing else, so
  `argv.length` is always `1` for anything run through this project's
  own runner. A different WASI host that supplies real extra arguments
  would see them show up in `argv` correctly; this is `run_wasi.mjs`'s
  own limitation, not a language one.
- **Filesystem access is sandboxed to one preopened directory**, WASI's
  own capability model (`runtime/wasm/run_wasi.mjs`'s own top comment)
  — `blob`, `mkdir`, `ls`, and SQLite's own file all resolve against
  whatever directory the host granted (the invoking shell's cwd, for
  both `festina run --target=wasm32-wasi` and the benchmark runner),
  not the whole real filesystem the way a native binary can see.
- **No ASan/LeakSanitizer coverage for this target.** The rest of this
  project verifies its memory management with real
  ASan/LeakSanitizer runs (`scripts/leak_stress.sh`,
  `tests/test_leak_stress.py`); whether sanitizer builds work at all
  under `wasm32-wasi` has not been investigated (macOS's own sanitizer
  tier is out of scope too, for an unrelated reason — LeakSanitizer is
  unreliable on darwin). This target's own memory-management codegen is
  instead verified by running real programs end-to-end and checking
  correct output, not by a sanitizer run.
- **Static linking is the only linking there is.** There's no
  dynamic-vs-static sqlite3 choice to make for wasm — the vendored
  amalgamation is always compiled in.
- **Two WASI hosts, one contract.** Every benchmark here runs a
  compiled `.wasm` through Node's `node:wasi` module; the browser host
  ([In a browser](#in-a-browser)) is this project's own JavaScript and
  is tested to the same behaviour (files, timers, exit codes) under
  Node and in headless Chromium. Its filesystem is in memory: nothing a
  program writes in a tab touches the real disk unless the page saves
  `festinaResult.files` somewhere itself.

## Benchmarks

The same five programs [benchmark.md](benchmark.md) already tracks
natively (`hello`/`fib`/`loop_sum`/`array_sum`/`string_concat` — see
that file for what each one measures and why), each also implemented
in C (`benchmarks/*.c`) and reused as-is for Go (`benchmarks/*.go`),
all three cross-compiled to `wasm32-wasi` and run through the
*identical* WASI host (`runtime/wasm/run_wasi.mjs`/`node:wasi`) — so
these numbers measure each language's generated code and Node's WASI
syscall overhead identically, not three different WASI runtimes' own
differing overhead. Every language's wasm output is checked to produce
byte-identical stdout to its own native build before any run is
trusted (`hello`→`hello`, `fib`→`2178309`, `loop_sum`→`828998288`,
`array_sum`→`707863693`, `string_concat`→15,000 `x`s — all three
languages agree, natively and under wasm).

Rust is not included here (unlike benchmark.md's native table): rustc
dropped `wasm32-wasi` as a target name (superseded by
`wasm32-wasip1`, which needs the separate `rustup target add
wasm32-wasip1` component, not otherwise needed by this project). C
stands in as the systems-language wasm comparison instead; Go uses its
own stable `GOOS=wasip1 GOARCH=wasm` support (Go 1.21+) — not
`GOOS=js GOARCH=wasm`, which targets the browser's own different,
incompatible ABI, not WASI.

### Benchmark methodology

Same shape as benchmark.md's own native methodology: 1 untimed warmup
build/run, then the *minimum* of 7 timed runs, for both build and run
time. `.wasm` file size stands in for benchmark.md's "binary size".
Reproduce locally:

```bash
python3 benchmarks/run_wasm_benchmarks.py               # print results
python3 benchmarks/run_wasm_benchmarks.py --update-doc   # regenerate this file's tables
```

Needs a wasm32-wasi-capable clang (see [Setup](#setup) above), Go
1.21+, and Node.js; a missing toolchain is skipped with a note rather
than failing the run, same spirit as `run_benchmarks.py`.

<!-- WASM_BENCHMARK_RESULTS_START -->
_Last run: 2026-09-03 on this machine -- see wasm.md's "Benchmark methodology" section for how to reproduce; absolute numbers vary by hardware, relative ordering is the point._

### `hello` (wasm32-wasi, run via Node's WASI host)

| Language | Run time (min of 7 runs) | Build time | .wasm size |
|---|---|---|---|
| Festina | 50.1 ms | 100.3 ms | 31.7 KB |
| C | 47.1 ms | 88.4 ms | 45.8 KB |
| Go | 73.5 ms | 163.4 ms | 2.31 MB |

### `fib` (wasm32-wasi, run via Node's WASI host)

| Language | Run time (min of 7 runs) | Build time | .wasm size |
|---|---|---|---|
| Festina | 59.6 ms | 120.0 ms | 31.6 KB |
| C | 60.7 ms | 92.9 ms | 92.1 KB |
| Go | 111.0 ms | 177.6 ms | 2.31 MB |

### `loop_sum` (wasm32-wasi, run via Node's WASI host)

| Language | Run time (min of 7 runs) | Build time | .wasm size |
|---|---|---|---|
| Festina | 807.9 ms | 115.1 ms | 31.6 KB |
| C | 832.0 ms | 92.2 ms | 92.1 KB |
| Go | 979.3 ms | 162.9 ms | 2.31 MB |

### `array_sum` (wasm32-wasi, run via Node's WASI host)

| Language | Run time (min of 7 runs) | Build time | .wasm size |
|---|---|---|---|
| Festina | 194.0 ms | 132.4 ms | 31.8 KB |
| C | 217.7 ms | 90.0 ms | 92.2 KB |
| Go | 277.8 ms | 155.0 ms | 2.31 MB |

### `string_concat` (wasm32-wasi, run via Node's WASI host)

| Language | Run time (min of 7 runs) | Build time | .wasm size |
|---|---|---|---|
| Festina | 51.0 ms | 139.8 ms | 33.8 KB |
| C | 53.2 ms | 92.3 ms | 93.8 KB |
| Go | 122.5 ms | 167.3 ms | 2.31 MB |

<!-- WASM_BENCHMARK_RESULTS_END -->

### Reading these numbers

Not a claim that Festina beats C or Go at wasm — this exists to catch
regressions and track progress over time, the same disclaimer
benchmark.md itself leads with. What the numbers above actually show:

- **Festina is within a few percent of hand-written C** on `fib`
  (both dominated by call overhead, not allocation) and `loop_sum`
  (pure arithmetic, all three languages converge to the same ~910ms,
  suggesting Node's WASI dispatch overhead — not code quality — is the
  floor on a loop this tight).
- **Festina's `.wasm` is smaller than C's on these five programs**
  — since claude.md #242 the vendored SQLite is dead-code eliminated
  from any program that never touches a database, and the sysroot's
  debug sections are stripped (see [Binary size](#binary-size) above);
  before that every Festina binary carried all 1.47 MB of it
  unconditionally, the way native binaries still statically link
  libsqlite3. Go's runtime — goroutine scheduler, GC — ships in every
  binary regardless of whether a given program uses any of it.
- **Where the gap to native comes from.** Against benchmark.md's native
  table, `hello` under wasm is ~50 ms against 1.4 ms — but ~30 ms of
  that is Node's own startup and another ~18 ms is importing
  `node:wasi` and instantiating any module at all (C's 46 KB `hello`
  measures the same), so it is the host, not the program. On the
  compute benchmarks the remaining ratio is what V8's wasm tier is
  known for: `loop_sum` 1.5x native, `fib` and `array_sum` about 2x
  (every memory access is bounds-checked, calls are dearer).
  `string_concat` used to be the outlier at ~5x its native time once
  startup was subtracted: the benchmark was O(n²) copying — 15,000
  concatenations of a string growing to 15,000 characters, ~112 MB
  through `memcpy` — and a wasm `memcpy` is a compiled loop, not the
  SIMD one glibc has, so the same copies simply cost more. Since
  claude.md #243 that pattern compiles as an in-place append (see
  api.md's "Strings"), the copying is gone on every target, and the
  wasm run sits a few milliseconds above the host's own floor.
  Link-time optimization across the program/runtime boundary was
  measured too and changes none of these by more than noise; the wins
  from it are all size.
- **Go is consistently the slowest of the three to start** (`hello`),
  most visible on the smallest program, where there's no real work to
  amortize a heavier runtime-init cost against.
- **`array_sum` is the one case where Festina's own generated code
  measurably outran hand-written C** in the run that produced the
  table above — plausible (Festina's escape analysis keeps this
  benchmark's array off the heap entirely, same as the C version's
  plain stack array; the two are closer in shape than the numbers
  might suggest, and re-runs should be expected to vary), but treat any
  close call between two of these as noise, not a verdict, the same
  caveat benchmark.md's own native table gives `array_sum`.

## Testing

`tests/test_wasm.py` plus the `compile_and_run_wasm` fixture
(`tests/conftest.py`) — real compiles and real executions through
`run_wasi.mjs`, not just checking that codegen produces plausible IR:
arithmetic/control flow/recursion, heap-allocated `arr`/`map`
(exercising the 32-bit pointer-width codegen path end to end, not just
at link time), structs, `table`/`sqlite()` against the vendored
amalgamation, `regex`, string concatenation, exit code propagation, the
graphics/audio compile-time rejections, and `festina doctor`'s own WASM
check. Skips cleanly (not a failure) on a machine without a working
wasm32-wasi clang or without Node — except under `FESTINA_STRICT_DEPS=1`
(the Linux CI job), where that skip becomes a hard failure instead, the
same discipline every other optional tier in this suite already has.
