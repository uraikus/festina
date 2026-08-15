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

Five programs, each implemented equivalently in Festina, Rust, Go, and
Bun (source in [`benchmarks/`](benchmarks/)):

| Benchmark | What it measures |
|---|---|
| `hello` | Process startup + runtime init cost — compile, print one line, exit. Directly reflects [the binary-slimming work](security.md) below: fewer dynamically linked libraries means less for the dynamic linker to resolve before `main()` even runs. |
| `fib` | Recursive function-call overhead and raw compute throughput — naive recursive `fib(32)` (no memoization), ~7 million calls. Deliberately not reducible to a closed form by an optimizer (unlike a linear sum), so this actually measures generated-code quality, not the compiler's algebra. |
| `loop_sum` | Tight-loop / branch-free arithmetic throughput — a 100,000,000-iteration polynomial-hash accumulation (`total = (total * 1000003 + i) % 1000000007`), each iteration depending on the last so it can't be folded into a closed-form constant either (verified: a plain running-sum version of this loop was optimized away entirely, running in ~2ms regardless of iteration count — see `loop_sum.f`'s own comment). |
| `array_sum` | Allocation-heavy throughput — 2,000,000 iterations, each building a fresh 8-element `arr[int]` literal (never escaping, so Festina reclaims it at that iteration's own scope-exit — see [claude.md](claude.md) #74/#76/#81) and summing its elements into a running total. Directly exercises the automatic memory management this project has been building out (see [todo.md](todo.md#memory-management)): every iteration is a genuine allocate-fill-read cycle, not just arithmetic. Each element's value depends on the *previous* iteration's own running total, the same closed-form-resistance trick `loop_sum` already uses. The hot loop lives inside a `void func run(...)`, not bare top-level code — escape analysis (claude.md #74) only ever analyzes a function/handler's own body, never `__festina_main`'s own top-level statement sequence, so this is what actually lets Festina prove `nums` never escapes (see `array_sum.f`'s own comment). |
| `string_concat` | String-heavy throughput — 15,000 iterations of naive repeated concatenation (`` s = `${s}x` ``/`s = s + "x"`), `s` growing by one character each time. There's no growable string buffer to amortize into, so this is the textbook O(n²) naive-concatenation pattern — deliberately: it's the "string-heavy workload" gap the notes below used to call out as untested. |

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
_Last run: 2026-08-15 on this machine -- see benchmark.md's "Methodology" section for how to reproduce; absolute numbers vary by hardware, relative ordering is the point._

### `hello`

| Language | Run time (min of 7 runs) | Build time | Binary size |
|---|---|---|---|
| Festina | 1.4 ms | 475.1 ms | 1.44 MB |
| Rust | 1.5 ms | 72.2 ms | 3.77 MB |
| Go | 1.2 ms | 151.1 ms | 2.11 MB |
| Bun | 9.7 ms | n/a (JIT, no separate build step) | n/a |

### `fib`

| Language | Run time (min of 7 runs) | Build time | Binary size |
|---|---|---|---|
| Festina | 7.7 ms | 63.6 ms | 1.44 MB |
| Rust | 7.7 ms | 73.4 ms | 3.77 MB |
| Go | 13.5 ms | 147.9 ms | 2.11 MB |
| Bun | 28.4 ms | n/a (JIT, no separate build step) | n/a |

### `loop_sum`

| Language | Run time (min of 7 runs) | Build time | Binary size |
|---|---|---|---|
| Festina | 519.0 ms | 69.1 ms | 1.44 MB |
| Rust | 523.5 ms | 74.8 ms | 3.77 MB |
| Go | 458.5 ms | 156.5 ms | 2.11 MB |
| Bun | 8963.5 ms | n/a (JIT, no separate build step) | n/a |

### `array_sum`

| Language | Run time (min of 7 runs) | Build time | Binary size |
|---|---|---|---|
| Festina | 91.9 ms | 83.1 ms | 1.44 MB |
| Rust | 89.6 ms | 93.9 ms | 3.77 MB |
| Go | 87.0 ms | 142.5 ms | 2.11 MB |
| Bun | 2272.7 ms | n/a (JIT, no separate build step) | n/a |

### `string_concat`

| Language | Run time (min of 7 runs) | Build time | Binary size |
|---|---|---|---|
| Festina | 3.6 ms | 69.3 ms | 1.44 MB |
| Rust | 1.5 ms | 95.1 ms | 3.77 MB |
| Go | 33.0 ms | 146.7 ms | 2.11 MB |
| Bun | 10.9 ms | n/a (JIT, no separate build step) | n/a |

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
- **`array_sum`** used to show a real, honest 2.4x gap against Rust/Go
  (209ms vs. ~87ms): every iteration's `arr[int]` literal always
  heap-allocated both its own header *and* its data buffer, even though
  `nums` provably never escapes its own iteration. claude.md #81 closes
  the header half of that gap — a non-escaping local declared directly
  from an array/map literal now stack-allocates its header the same way
  a non-escaping struct local already did (claude.md #74/#76), leaving
  only the data buffer's own `malloc` (still heap, since a truly
  general growable buffer isn't safe to give a fixed-size `alloca` —
  see claude.md #81's own boundary). Festina now lands *at* Rust/Go
  here, not behind them — the remaining, much smaller gap is ordinary
  codegen-maturity noise, not an allocation-strategy gap anymore.
- **`string_concat`** used to be the sharpest divergence in the whole
  suite (140ms vs. Rust's 1.7ms), and closing it took three separate
  fixes across two rounds. The first was pure wasted work in template
  codegen: `` `${s}x` `` compiled as `("" + s) + "x"`, concatenating
  with an empty string literal before appending the real one, which
  claude.md #82 removed for roughly half the time (140ms → ~77ms).
  What remained was far larger and wasn't an algorithmic gap at all —
  Festina never freed a `text` value *anywhere* in generated code, at
  any binding site, under any circumstance. This benchmark abandons
  every intermediate buffer it builds, so its heap grew quadratically
  and the program spent essentially all its time asking the kernel for
  more: **816 `brk()` calls, against 3 for equivalent leak-free C.**
  claude.md #83 makes text genuinely owned and genuinely freed, taking
  this benchmark from ~77ms to **3.6ms** — and the underlying O(n²)
  naive-copy algorithm is *unchanged*; that entire gap was allocator
  pressure from the leak, not copying.
- Festina now sits second in `string_concat`, ahead of both Go (~9x)
  and Bun (~3x) and within about 2.4x of Rust. The remaining Rust gap
  is genuinely algorithmic and not something Festina is attempting to
  close here: Rust's `String` `+` reuses the left operand's own spare
  capacity in place when it has room (amortized growth, the same idea
  `Vec` uses), so it isn't doing the full O(n²) copy at all. Go's `+`
  on immutable strings has no spare capacity to grow into either, which
  is why it lands on the same side of the divide as Festina; Bun's V8
  backend uses rope/cons-string representations internally, deferring
  the copy until the string is actually read, which is why it avoids
  the quadratic blowup despite naive-looking source. None of this is a
  bug in any of the four — it's exactly the kind of language/runtime
  difference this benchmark exists to surface.
- These are intentionally small, fast benchmarks so they can be re-run
  on every change worth checking, not a comprehensive suite (no I/O, no
  concurrency, no realistic mixed workload) — see [todo.md](todo.md)
  for what's still missing from Festina itself that would make a
  broader comparison meaningful (HTTP, for one).
