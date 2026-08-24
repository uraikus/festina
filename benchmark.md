# Benchmarks

How Festina compares to Rust, Go, and Bun on a handful of small,
equivalent-logic programs, and to a browser's `<canvas>` and MonoGame on
2D drawing. Not a claim that Festina is faster than any
of these languages in general — a compiled language's real-world
performance depends heavily on what's actually being written and how
mature its optimizer/runtime is, and Festina's is young. This exists to
catch regressions and track progress over time, run against the same
few workloads on every change that plausibly affects performance
(codegen, runtime, or the standard library), not as a marketing claim.

## Methodology

Five programs, each implemented equivalently in Festina, Rust, Go, and
Bun (source in [`benchmarks/`](benchmarks/)), plus a sixth comparing
Festina's canvas against a browser's and MonoGame's — see
[Canvas](#canvas-festina-vs-an-html-canvas-vs-monogame) at the end:

| Benchmark | What it measures |
|---|---|
| `hello` | Process startup + runtime init cost — compile, print one line, exit. Directly reflects [the binary-slimming work](security.md) below: fewer dynamically linked libraries means less for the dynamic linker to resolve before `main()` even runs. |
| `fib` | Recursive function-call overhead and raw compute throughput — naive recursive `fib(32)` (no memoization), ~7 million calls. Deliberately not reducible to a closed form by an optimizer (unlike a linear sum), so this actually measures generated-code quality, not the compiler's algebra. |
| `loop_sum` | Tight-loop / branch-free arithmetic throughput — a 100,000,000-iteration polynomial-hash accumulation (`total = (total * 1000003 + i) % 1000000007`), each iteration depending on the last so it can't be folded into a closed-form constant either (verified: a plain running-sum version of this loop was optimized away entirely, running in ~2ms regardless of iteration count — see `loop_sum.f`'s own comment). |
| `array_sum` | Allocation-heavy throughput — 2,000,000 iterations, each building a fresh 8-element `arr[int]` literal (never escaping, so Festina reclaims it at that iteration's own scope-exit — see [claude.md](claude.md) #74/#76/#81) and summing its elements into a running total. Directly exercises the automatic memory management this project has been building out (see [todo.md](todo.md#memory-model)): every iteration is a genuine allocate-fill-read cycle, not just arithmetic. Each element's value depends on the *previous* iteration's own running total, the same closed-form-resistance trick `loop_sum` already uses. The hot loop lives inside a `void func run(...)`, not bare top-level code — escape analysis (claude.md #74) only ever analyzes a function/handler's own body, never `__festina_main`'s own top-level statement sequence, so this is what actually lets Festina prove `nums` never escapes (see `array_sum.f`'s own comment). |
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

Build time gets one untimed throwaway build per toolchain before any
timed one, for the same reason each program gets an untimed warmup run.
Without it the first benchmark in the list absorbed the whole toolchain's
cold-start cost and reported a build time several times everyone else's
— measured at 5.1 s for Rust's `hello` against 0.1 s for the very next
program it built, which says nothing about `hello`.

Reproduce locally:

```bash
python3 benchmarks/run_benchmarks.py               # print results
python3 benchmarks/run_benchmarks.py --update-doc   # regenerate this file's table

python3 benchmarks/canvas/run_canvas_benchmark.py             # the canvas comparison
python3 benchmarks/canvas/run_canvas_benchmark.py --update-doc

# The MonoGame side needs a .NET SDK and, on first run, network access
# to restore its NuGet package; without either it is skipped with a note
# rather than failing the run.
```

The runner skips any language toolchain not installed rather than
failing — see [setup.md](setup.md) for what each one needs.

## Results

<!-- BENCHMARK_RESULTS_START -->
_Last run: 2026-08-16 on this machine -- see benchmark.md's "Methodology" section for how to reproduce; absolute numbers vary by hardware, relative ordering is the point._

### `hello`

| Language | Run time (min of 7 runs) | Build time | Binary size |
|---|---|---|---|
| Festina | 1.2 ms | 65.3 ms | 1.45 MB |
| Rust | 1.4 ms | 73.1 ms | 3.77 MB |
| Go | 1.3 ms | 147.5 ms | 2.11 MB |
| Bun | 9.3 ms | n/a (JIT, no separate build step) | n/a |

### `fib`

| Language | Run time (min of 7 runs) | Build time | Binary size |
|---|---|---|---|
| Festina | 7.2 ms | 71.5 ms | 1.45 MB |
| Rust | 8.8 ms | 83.3 ms | 3.77 MB |
| Go | 13.7 ms | 131.7 ms | 2.11 MB |
| Bun | 28.0 ms | n/a (JIT, no separate build step) | n/a |

### `loop_sum`

| Language | Run time (min of 7 runs) | Build time | Binary size |
|---|---|---|---|
| Festina | 551.1 ms | 80.1 ms | 1.45 MB |
| Rust | 582.0 ms | 85.9 ms | 3.77 MB |
| Go | 533.2 ms | 138.3 ms | 2.11 MB |
| Bun | 10474.8 ms | n/a (JIT, no separate build step) | n/a |

### `array_sum`

| Language | Run time (min of 7 runs) | Build time | Binary size |
|---|---|---|---|
| Festina | 98.5 ms | 87.7 ms | 1.45 MB |
| Rust | 98.7 ms | 101.5 ms | 3.77 MB |
| Go | 98.7 ms | 134.4 ms | 2.11 MB |
| Bun | 2455.5 ms | n/a (JIT, no separate build step) | n/a |

### `string_concat`

| Language | Run time (min of 7 runs) | Build time | Binary size |
|---|---|---|---|
| Festina | 3.5 ms | 73.8 ms | 1.45 MB |
| Rust | 1.3 ms | 99.4 ms | 3.77 MB |
| Go | 28.2 ms | 129.5 ms | 2.11 MB |
| Bun | 11.3 ms | n/a (JIT, no separate build step) | n/a |

<!-- BENCHMARK_RESULTS_END -->

## Reading these numbers

- **`hello`** is dominated by process startup, not language performance
  — a graphics/audio-free Festina binary (see
  [security.md](security.md#slim-binaries)) dynamically links only
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
- **The canvas comparison** (below) is the one benchmark here that
  isn't against another *language*. It's against the thing a 2D game
  would otherwise most likely be written on: an HTML `<canvas>`. It
  started out with Festina 1.4x **slower**, and that got written down in
  bold before anything was done about it — which is what made the fix
  findable. Splitting the frame by shape type showed circles were 90% of
  it, because Cairo tessellates every arc afresh; caching one alpha mask
  per radius (claude.md #104) took the frame from 90 ms to 31 ms and the
  result from 1.4x behind to 2.1x ahead. Festina also wins startup by
  more than an order of magnitude and wins on variance, which for a
  frame budget is not a footnote.
- **MonoGame** joins the canvas comparison as a third side, and its
  number is the one on this page most likely to be quoted out of
  context. It is a GPU framework running here with no GPU, on Mesa's
  software rasterizer; on real hardware it would batch these 40,000
  sprites into a couple of draw calls and beat everything else on this
  page by orders of magnitude. The row is worth having because headless
  rendering with no GPU is a real situation — CI, a build server, a
  container — and it is worth reading only with that sentence attached.
- These are intentionally small, fast benchmarks so they can be re-run
  on every change worth checking, not a comprehensive suite (no I/O, no
  concurrency, no realistic mixed workload) — see [todo.md](todo.md)
  for what's still missing from Festina itself that would make a
  broader comparison meaningful (HTTP, for one).

## Canvas: Festina vs an HTML `<canvas>` vs MonoGame

<!-- CANVAS_RESULTS_START -->
_Last run: 2026-08-16 on this machine. Chromium 141.0.7390.37._

20,000 filled rectangles and 20,000 filled circles, fill colour changed
between every shape, into an 800x600 surface. Both sides draw
**offscreen**, both time their own draw loop with their own monotonic
clock, and the browser is forced to rasterize inside the timed region.
All three of those matter and all three are easy to get wrong -- see
[`run_canvas_benchmark.py`](benchmarks/canvas/run_canvas_benchmark.py),
which documents what each one cost when it was measured the other way.

| | Frame (min) | Frame (median) | First frame |
|---|---|---|---|
| Festina (Cairo) | 31 ms | 32 ms | 16 ms (process start + PNG encode) |
| HTML `<canvas>` (Chromium/Skia) | 60 ms | 62 ms | 240 ms (browser launch) |
| MonoGame (SpriteBatch, **software** GL) | 181 ms | 410 ms | 166 ms (.NET runtime + GL context) |

> **The MonoGame row needs its caveat read before its number.**
> MonoGame is a GPU framework, and this machine has no GPU — its GL
> context is Mesa's `llvmpipe`, a software implementation of the whole
> graphics pipeline. It is therefore paying in software for vertex
> transform, rasterization setup and per-pixel texture sampling that
> real hardware does for free. On an actual GPU these 40,000 sprites
> batch into a couple of draw calls and finish in well under a
> millisecond — which no CPU rasterizer on this page can approach.
> What this row measures is the headless, no-GPU case (CI, a build
> server, a container), and nothing else.
>
> It is also by far the noisiest row: `llvmpipe` is multithreaded and
> so is far more exposed to whatever else the machine is doing than
> single-threaded Cairo. Consecutive runs of the same binary measured
> 173, 180, 193, 285 and 498 ms. The runner launches the process five
> times and keeps the best, which lands near the floor most of the
> time — but treat this number as "a few hundred milliseconds",
> not as a figure precise to the millisecond the way the other two
> rows are.

On this workload **Festina draws it 1.9x faster**.

That took one change, and finding it took measuring rather than
guessing. The first version of this benchmark had Festina 1.4x SLOWER,
and the obvious culprit -- a fresh Cairo context per draw call -- turned
out to account for 4 ms of 90. Splitting the frame by shape type found
the real one immediately: 20,000 rectangles cost 10 ms and 20,000
circles cost 76 ms, because `cairo_arc` + `cairo_fill` tessellates the
curve into Beziers and scan-converts a general polygon every single
time. Rasterizing each radius once into an alpha mask and stamping it
thereafter -- what a glyph cache does -- took circles to 20 ms and the
frame from 90 ms to 31 ms (claude.md #104). The remaining split is
11 ms of rectangles, 20 ms of circles, and setting the fill colour
20,000 times is too cheap to measure.

Two things are worth reading alongside the headline. The browser's frame
time is far noisier -- 60 ms at best against a 62 ms median here, and
the median moves by 20+ ms between runs of this same script, while
Festina's two numbers (31 and 32 ms) sit on top of each other. For a
frame budget, predictability is not a footnote. And getting to the
*first* frame differs by more than an order of magnitude in the same
direction, because one side starts a process and the other starts a
browser.

Both outputs were compared cell-by-cell over a 16x16 grid to confirm
they drew the same scene -- worst per-channel difference 0.2 out of 255.
Not byte-for-byte: Cairo and Skia disagree about antialiasing on every
curve, and demanding identical bytes would only prove the two
rasterizers are the same program. The check has already earned itself
once, catching a bug in this very script that left one side comparing a
blank canvas.
<!-- CANVAS_RESULTS_END -->
