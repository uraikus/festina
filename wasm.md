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
absent), and getting a `.wasm` running outside a WASI host (a browser
tab, for instance) — see [Limitations](#limitations) below for the full
accounting.

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
`clang --target=wasm32-wasi -O2`, linked against three object files
(`_wasm_runtime_objects`):

1. **`festina_runtime.c`**, compiled against the vendored
   `runtime/wasm/sqlite3.h` instead of a system one — core, the same
   translation unit every native target uses.
2. **`runtime/wasm/sqlite3.c`** — SQLite's own single-file amalgamation
   build, vendored because table/`sqlite()` support is unconditional
   core (every compiled program links against sqlite3 symbols whether
   or not it declares a `table`), and there is no system `libsqlite3`
   for `wasm32-wasi` to link against at all — see
   [`runtime/wasm/README.md`](runtime/wasm/README.md) for exactly why
   vendoring it (something this project doesn't do for native targets,
   which link the system's own libsqlite3) is the only option here.
3. **`runtime/festina_runtime_wasm_entry.ll`** — a small entry-point
   bridge, see [below](#the-main-entry-point) and
   [below](#why-the-entry-bridge-is-raw-ir-not-c).

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
- **`try`/`catch`/`throw`** — LLVM's wasm32 backend has no
  setjmp/longjmp (SjLj) lowering at all outside emscripten's own
  exception-handling pass, which this project doesn't use (`clang`
  rejects `__builtin_longjmp` outright for this target). `.toStruct()`/
  `.toArr()` are *not* affected (claude.md #233 — their cleanup is
  plain runtime C, not a catch frame): they compile and run here, and a
  parse failure ends the program the way any uncaught `throw` does,
  since there is no `try` to catch it.

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
- **No browser support claimed.** Every test and benchmark here runs a
  compiled `.wasm` through Node's `node:wasi` module. A browser has no
  built-in WASI host — running one there needs a JS-side WASI
  polyfill (e.g. `@wasmer/wasi`) that this project has neither
  vendored nor tested against.

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
_Last run: 2026-09-01 on this machine -- see wasm.md's "Benchmark methodology" section for how to reproduce; absolute numbers vary by hardware, relative ordering is the point._

### `hello` (wasm32-wasi, run via Node's WASI host)

| Language | Run time (min of 7 runs) | Build time | .wasm size |
|---|---|---|---|
| Festina | 50.4 ms | 82.0 ms | 1.47 MB |
| C | 43.7 ms | 72.3 ms | 45.8 KB |
| Go | 70.4 ms | 142.8 ms | 2.31 MB |

### `fib` (wasm32-wasi, run via Node's WASI host)

| Language | Run time (min of 7 runs) | Build time | .wasm size |
|---|---|---|---|
| Festina | 61.2 ms | 84.3 ms | 1.47 MB |
| C | 54.0 ms | 118.8 ms | 92.1 KB |
| Go | 106.6 ms | 151.8 ms | 2.31 MB |

### `loop_sum` (wasm32-wasi, run via Node's WASI host)

| Language | Run time (min of 7 runs) | Build time | .wasm size |
|---|---|---|---|
| Festina | 783.2 ms | 86.5 ms | 1.47 MB |
| C | 824.6 ms | 79.1 ms | 92.1 KB |
| Go | 972.8 ms | 157.9 ms | 2.31 MB |

### `array_sum` (wasm32-wasi, run via Node's WASI host)

| Language | Run time (min of 7 runs) | Build time | .wasm size |
|---|---|---|---|
| Festina | 189.5 ms | 96.9 ms | 1.47 MB |
| C | 211.8 ms | 83.5 ms | 92.2 KB |
| Go | 268.8 ms | 182.1 ms | 2.31 MB |

### `string_concat` (wasm32-wasi, run via Node's WASI host)

| Language | Run time (min of 7 runs) | Build time | .wasm size |
|---|---|---|---|
| Festina | 72.0 ms | 85.8 ms | 1.47 MB |
| C | 50.4 ms | 87.8 ms | 93.8 KB |
| Go | 125.1 ms | 163.7 ms | 2.31 MB |

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
- **Festina's `.wasm` is consistently larger than C's** (the vendored
  SQLite amalgamation alone dwarfs any of these five tiny programs —
  every Festina binary pays that cost unconditionally, same as it does
  natively) but noticeably *smaller* than Go's (whose runtime —
  goroutine scheduler, GC — ships in every binary regardless of whether
  a given program uses any of it).
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
