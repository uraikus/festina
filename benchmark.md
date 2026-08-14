# Benchmarks

How Festina compares to Rust, Go, and Bun on a handful of small,
equivalent-logic programs. Not a claim that Festina is faster than any
of these languages in general — a compiled language's real-world
performance depends heavily on what's actually being written and how
mature its optimizer/runtime is, and Festina's is young. This exists to
catch regressions and track progress over time, run against the same
few workloads on every change that plausibly affects performance
(codegen, runtime, or the standard library), not as a marketing claim.

## Methodology

Three programs, each implemented equivalently in Festina, Rust, Go, and
Bun (source in [`benchmarks/`](benchmarks/)):

| Benchmark | What it measures |
|---|---|
| `hello` | Process startup + runtime init cost — compile, print one line, exit. Directly reflects [the binary-slimming work](security.md) below: fewer dynamically linked libraries means less for the dynamic linker to resolve before `main()` even runs. |
| `fib` | Recursive function-call overhead and raw compute throughput — naive recursive `fib(32)` (no memoization), ~7 million calls. Deliberately not reducible to a closed form by an optimizer (unlike a linear sum), so this actually measures generated-code quality, not the compiler's algebra. |
| `loop_sum` | Tight-loop / branch-free arithmetic throughput — a 100,000,000-iteration polynomial-hash accumulation (`total = (total * 1000003 + i) % 1000000007`), each iteration depending on the last so it can't be folded into a closed-form constant either (verified: a plain running-sum version of this loop was optimized away entirely, running in ~2ms regardless of iteration count — see `loop_sum.f`'s own comment). |

Each language uses its own normal toolchain and optimization settings
(`festina program.f -o program`, `rustc -O`, `go build`, `bun run` —
Bun has no separate build step, it's a JIT). All four are checked to
produce byte-identical stdout before a run is trusted.

Every benchmark is timed with 1 untimed warmup run (page cache, dynamic
linker resolution, ...) followed by 7 timed runs, keeping the *minimum*
— the standard way to reduce OS scheduling noise without pulling in a
dedicated benchmarking tool. Binary size is the compiled executable's
size on disk (n/a for Bun, which ships no separate binary).

Reproduce locally:

```bash
python3 benchmarks/run_benchmarks.py               # print results
python3 benchmarks/run_benchmarks.py --update-doc   # regenerate this file's table
```

The runner skips any language toolchain not installed rather than
failing — see [setup.md](setup.md) for what each one needs.

## Results

<!-- BENCHMARK_RESULTS_START -->
_Last run: 2026-08-14 on this machine -- see benchmark.md's "Methodology" section for how to reproduce; absolute numbers vary by hardware, relative ordering is the point._

### `hello`

| Language | Run time (min of 7 runs) | Build time | Binary size |
|---|---|---|---|
| Festina | 1.2 ms | 534.6 ms | 1.44 MB |
| Rust | 1.2 ms | 81.1 ms | 3.77 MB |
| Go | 1.4 ms | 174.9 ms | 2.11 MB |
| Bun | 9.4 ms | n/a (JIT, no separate build step) | n/a |

### `fib`

| Language | Run time (min of 7 runs) | Build time | Binary size |
|---|---|---|---|
| Festina | 7.4 ms | 73.8 ms | 1.44 MB |
| Rust | 7.2 ms | 83.8 ms | 3.77 MB |
| Go | 11.6 ms | 146.6 ms | 2.11 MB |
| Bun | 27.0 ms | n/a (JIT, no separate build step) | n/a |

### `loop_sum`

| Language | Run time (min of 7 runs) | Build time | Binary size |
|---|---|---|---|
| Festina | 473.9 ms | 83.7 ms | 1.44 MB |
| Rust | 519.7 ms | 82.4 ms | 3.77 MB |
| Go | 472.6 ms | 147.5 ms | 2.11 MB |
| Bun | 9060.8 ms | n/a (JIT, no separate build step) | n/a |

<!-- BENCHMARK_RESULTS_END -->

## Reading these numbers

- **`hello`** is dominated by process startup, not language performance
  — a graphics/audio-free Festina binary (see
  [security.md](security.md#binary-slimming)) dynamically links only
  libc/libm (plus libz, a transitive dependency of the statically-linked
  sqlite3), the same ballpark as Go's or Rust's own small dynamic
  dependency lists here; a graphics- or audio-using Festina program
  would show up slower purely from the extra shared libraries the
  dynamic linker has to resolve at startup (`libcairo`/`libX11`/
  `libasound` and their own transitive dependencies).
- **`fib`** and **`loop_sum`** are closer to an apples-to-apples
  compiled-code comparison — Rust and Go both compile through mature,
  years-optimized backends (LLVM and Go's own `gc`, respectively);
  Festina also compiles through LLVM (see [api.md](api.md#compilation-pipeline))
  but is a much younger frontend with far less codegen-level tuning, so
  a gap here reflects the compiler's maturity, not a ceiling in the
  language design. Bun's JIT has to warm up during the run itself,
  which a single-shot benchmark like this doesn't isolate from the
  actual computation — a longer-running workload would tell a different
  story for Bun specifically.
- These are intentionally small, fast benchmarks so they can be re-run
  on every change worth checking, not a comprehensive suite (no memory
  allocation patterns, no I/O, no string-heavy workloads) — see
  [todo.md](todo.md) for what's still missing from Festina itself that
  would make a broader comparison meaningful (HTTP, for one).
