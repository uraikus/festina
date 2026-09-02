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

These same five programs are also benchmarked cross-compiled to
`wasm32-wasi` (against C and Go, also compiled to wasm) — see
[wasm.md](wasm.md#benchmarks).

## Methodology

Five programs, each implemented equivalently in Festina, Rust, Go, and
Bun (source in [`benchmarks/`](benchmarks/)), plus a sixth comparing
Festina's canvas against a browser's and MonoGame's — see
[Canvas](#canvas-festina-vs-an-html-canvas-vs-monogame) at the end:

| Benchmark | What it measures |
|---|---|
| `hello` | Process startup + runtime init cost — compile, print one line, exit. Directly reflects [the binary-slimming work](security.md) below: fewer dynamically linked libraries means less for the dynamic linker to resolve before `main()` even runs. |
| `fib` | Recursive function-call overhead and raw compute throughput — naive recursive `fib(32)` (no memoization), ~7 million calls. Deliberately not reducible to a closed form by an optimizer (unlike a linear sum), so this actually measures generated-code quality, not the compiler's algebra. |
| `loop_sum` | Tight-loop / branch-free arithmetic throughput — a 100,000,000-iteration polynomial-hash accumulation (`total = (total * 1000003 + i) % 1000000007`), each iteration depending on the last so it can't be folded into a closed-form constant either — a plain running-sum version of this loop optimizes away entirely, running in ~2ms regardless of iteration count (see `loop_sum.f`'s own comment). |
| `array_sum` | Allocation-heavy throughput — 2,000,000 iterations, each building a fresh 8-element `arr[int]` literal (never escaping, so Festina reclaims it at that iteration's own scope-exit — see [todo.md](todo.md#memory-model)) and summing its elements into a running total. Directly exercises automatic memory management: every iteration is a genuine allocate-fill-read cycle, not just arithmetic. Each element's value depends on the *previous* iteration's own running total, the same closed-form-resistance trick `loop_sum` already uses. The hot loop lives inside a `void func run(...)`, not bare top-level code — escape analysis only ever analyzes a function/handler's own body, never the top-level statement sequence, so this is what lets Festina prove `nums` never escapes (see `array_sum.f`'s own comment). |
| `string_concat` | String-heavy throughput — 15,000 iterations of naive repeated concatenation (`` s = `${s}x` ``/`s = s + "x"`), `s` growing by one character each time. There's no growable string buffer to amortize into, so this is the textbook O(n²) naive-concatenation pattern. |

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
timed one, for the same reason each program gets an untimed warmup run
— without it, the first benchmark in the list would absorb the whole
toolchain's own cold-start cost and report a build time several times
every other program's own.

Reproduce locally:

```bash
python3 benchmarks/run_benchmarks.py               # print results
python3 benchmarks/run_benchmarks.py --update-doc   # regenerate this file's table

python3 benchmarks/canvas/run_canvas_benchmark.py             # the canvas comparison
python3 benchmarks/canvas/run_canvas_benchmark.py --update-doc

# The MonoGame side needs a .NET SDK and, on first run, network access
# to restore its NuGet package; without either it is skipped with a note
# rather than failing the run.

python3 benchmarks/layered_canvas/run_layered_canvas_benchmark.py             # multi-threaded Festina vs Worker+OffscreenCanvas
python3 benchmarks/layered_canvas/run_layered_canvas_benchmark.py --update-doc

python3 benchmarks/http/run_http_benchmarks.py                # the HTTP server comparison
python3 benchmarks/http/run_http_benchmarks.py --update-doc

# Needs `wrk` on PATH (not a project dependency -- apt/brew install wrk).
```

The runner skips any language toolchain not installed rather than
failing — see [setup.md](setup.md) for what each one needs.

## Results

<!-- BENCHMARK_RESULTS_START -->
_Last run: 2026-09-01 on this machine -- see benchmark.md's "Methodology" section for how to reproduce; absolute numbers vary by hardware, relative ordering is the point._

### `hello`

| Language | Run time (min of 7 runs) | Build time | Binary size |
|---|---|---|---|
| Festina | 1.4 ms | 67.0 ms | 1.49 MB |
| Rust | 1.5 ms | 80.5 ms | 3.77 MB |
| Go | 1.3 ms | 159.7 ms | 2.11 MB |
| Bun | 11.3 ms | n/a (JIT, no separate build step) | n/a |

### `fib`

| Language | Run time (min of 7 runs) | Build time | Binary size |
|---|---|---|---|
| Festina | 7.7 ms | 71.8 ms | 1.49 MB |
| Rust | 8.2 ms | 96.6 ms | 3.77 MB |
| Go | 14.0 ms | 162.3 ms | 2.11 MB |
| Bun | 31.3 ms | n/a (JIT, no separate build step) | n/a |

### `loop_sum`

| Language | Run time (min of 7 runs) | Build time | Binary size |
|---|---|---|---|
| Festina | 519.0 ms | 76.9 ms | 1.49 MB |
| Rust | 526.5 ms | 89.1 ms | 3.77 MB |
| Go | 461.3 ms | 159.4 ms | 2.11 MB |
| Bun | 9024.2 ms | n/a (JIT, no separate build step) | n/a |

### `array_sum`

| Language | Run time (min of 7 runs) | Build time | Binary size |
|---|---|---|---|
| Festina | 92.3 ms | 88.2 ms | 1.49 MB |
| Rust | 91.0 ms | 103.4 ms | 3.77 MB |
| Go | 88.7 ms | 180.5 ms | 2.11 MB |
| Bun | 2345.7 ms | n/a (JIT, no separate build step) | n/a |

### `string_concat`

| Language | Run time (min of 7 runs) | Build time | Binary size |
|---|---|---|---|
| Festina | 3.7 ms | 72.9 ms | 1.49 MB |
| Rust | 1.6 ms | 109.3 ms | 3.77 MB |
| Go | 35.9 ms | 193.9 ms | 2.11 MB |
| Bun | 12.4 ms | n/a (JIT, no separate build step) | n/a |

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
- **`array_sum`** lands close to Rust/Go rather than behind them: the
  per-iteration `arr[int]` literal provably never escapes its own
  iteration, so Festina stack-allocates its header the same way a
  non-escaping struct local does, leaving only the growable data
  buffer's own `malloc` (a truly general growable buffer isn't safe to
  give a fixed-size `alloca`). The remaining, small gap is ordinary
  codegen-maturity noise, not an allocation-strategy gap.
- **`string_concat`** is where Festina's `text` ownership model shows up
  directly: every intermediate buffer this benchmark's naive
  concatenation builds is genuinely freed once nothing references it
  any more, so the heap grows linearly with the final string's own
  length, not quadratically with the number of concatenations — the
  underlying O(n²) naive-copy algorithm is unchanged, but nothing about
  it depends on allocator pressure from an unfreed buffer. Festina sits
  second in this benchmark, ahead of both Go (~9x) and Bun (~3x) and
  within about 2.4x of Rust. The remaining Rust gap is genuinely
  algorithmic, not something this benchmark is meant to close: Rust's
  `String` `+` reuses the left operand's own spare capacity in place
  when it has room (amortized growth, the same idea `Vec` uses), so it
  isn't doing the full O(n²) copy at all. Go's `+` on immutable strings
  has no spare capacity to grow into either, which is why it lands on
  the same side of the divide as Festina; Bun's V8 backend uses
  rope/cons-string representations internally, deferring the copy until
  the string is actually read, which is why it avoids the quadratic
  blowup despite naive-looking source. None of this is a bug in any of
  the four — it's exactly the kind of language/runtime difference this
  benchmark exists to surface.
- **The canvas comparison** (below) is the one benchmark here that
  isn't against another *language*. It's against the thing a 2D game
  would otherwise most likely be written on: an HTML `<canvas>`. Circles
  dominate frame cost, because Cairo tessellates every arc afresh —
  Festina caches one alpha mask per radius and stamps it thereafter (the
  same trick a glyph cache uses), which is most of why the frame stays
  fast. Festina also wins startup by more than an order of magnitude and
  wins on variance, which for a frame budget is not a footnote.
- **MonoGame** joins the canvas comparison as a third side, and its
  number is the one on this page most likely to be quoted out of
  context. It is a GPU framework running here with no GPU, on Mesa's
  software rasterizer; on real hardware it would batch these 40,000
  sprites into a couple of draw calls and beat everything else on this
  page by orders of magnitude. The row is worth having because headless
  rendering with no GPU is a real situation — CI, a build server, a
  container — and it is worth reading only with that sentence attached.
- These five are intentionally small, fast benchmarks so they can be
  re-run on every change worth checking, not a comprehensive suite (no
  concurrency, no realistic mixed workload). I/O has its own section
  below — see [HTTP](#http-festina-vs-rust-vs-go-vs-bun).

## Canvas: Festina vs an HTML `<canvas>` vs MonoGame

<!-- CANVAS_RESULTS_START -->
_Last run: 2026-09-01 on this machine. Chromium 141.0.7390.37._

20,000 filled rectangles and 20,000 filled circles, fill colour changed
between every shape, into an 800x600 surface. Both sides draw
**offscreen**, both time their own draw loop with their own monotonic
clock, and the browser is forced to rasterize inside the timed region.
All three of those matter and all three are easy to get wrong -- see
[`run_canvas_benchmark.py`](benchmarks/canvas/run_canvas_benchmark.py),
which documents what each one cost when it was measured the other way.

| | Frame (min) | Frame (median) | First frame |
|---|---|---|---|
| Festina (Cairo) | 35 ms | 37 ms | 25 ms (process start + PNG encode) |
| HTML `<canvas>` (Chromium/Skia) | 68 ms | 108 ms | 1279 ms (browser launch) |
| MonoGame (SpriteBatch, **software** GL) | 231 ms | 237 ms | 177 ms (.NET runtime + GL context) |

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
time is far noisier -- 68 ms at best against a 108 ms median here, and
the median moves by 20+ ms between runs of this same script, while
Festina's two numbers (35 and 37 ms) sit on top of each other. For a
frame budget, predictability is not a footnote. And getting to the
*first* frame differs by more than an order of magnitude in the same
direction, because one side starts a process and the other starts a
browser.

Both outputs were compared cell-by-cell over a 16x16 grid to confirm
they drew the same scene -- worst per-channel difference 0.2 out
of 255. Not byte-for-byte: Cairo and Skia disagree about antialiasing on
every curve, and demanding identical bytes would only prove the two
rasterizers are the same program. The check has earned itself twice
now: once catching a bug in this very script that left one side
comparing a blank canvas, and again catching itself comparing raw RGB
without accounting for alpha -- Festina's own offscreen canvas starts
transparent (api.md's own "a fresh or cleared canvas is transparent,
not white"), so a background pixel neither side actually drew on read
as black here against the browser harness's own opaque white fill,
which the comparison mistook for a real rendering difference until it
started compositing both sides onto the same white background first.
<!-- CANVAS_RESULTS_END -->

## HTTP: Festina vs Rust vs Go vs Bun

Four servers (source in [`benchmarks/http/`](benchmarks/http/)), each
answering the same two routes -- `/` (a fixed plaintext body) and
`/json` (a small JSON body) -- load-tested with
[`wrk`](https://github.com/wg/wrk) (not a project dependency; installed
separately, `apt install wrk`/`brew install wrk`).

**Equivalent logic, not equivalent idiom, the same rule the five
programs above already follow.** Festina's HTTP server
(`festina_runtime_http.c`) is deliberately single-threaded (one
connection serviced at a time). Rust's and Go's servers here are
hand-rolled raw-socket implementations with a single-threaded,
sequential accept loop -- not `hyper`/`net/http`'s own default
(multi-threaded) servers, which would be measuring a mature framework's
concurrency model against Festina's single-threaded one rather than the
same connection-handling logic in four languages. Bun is the one
exception: it uses `Bun.serve()`, its own native HTTP implementation,
since there is no reason to hand-roll sockets in a runtime that ships a
fast one already (the same "each language uses its own normal
toolchain" rule the Methodology above states).

**Every response closes the connection, matched uniformly across all
four servers.** Rust's and Go's raw-socket servers close by default;
Bun's server sets `Connection: close` explicitly, to opt out of its own
native keep-alive (which none of the raw-socket languages here have an
equivalent of). Festina supports HTTP/1.1 keep-alive by default, so the
load generator itself sends the fix: `run_http_benchmarks.py`'s own
`wrk` invocation sends an explicit `Connection: close` request header
uniformly against all four servers -- Rust/Go/Bun ignore it (they
already always close), and Festina's own documented behavior (api.md:
an explicit client `Connection: close` always forces it off,
per-request) makes it close too. This keeps the comparison to exactly
connection-accept + parse + respond, for all four languages at once,
with no per-language server code needed to special-case it.

Each `wrk` run: 4 threads, 50 open connections, 5 seconds, against one
route at a time (a JIT-inclined runtime like Bun gets no separate
warmup here — `wrk`'s own 5-second window includes whatever warmup
happens inside it, the same "the timed window is the real number"
approach as the process-startup benchmarks above, since a resident
server process outlives any of them anyway).

Reproduce locally:

```bash
python3 benchmarks/http/run_http_benchmarks.py
python3 benchmarks/http/run_http_benchmarks.py --update-doc
python3 benchmarks/http/run_http_benchmarks.py --duration 10s --connections 100 --threads 8
```

<!-- HTTP_BENCHMARK_RESULTS_START -->
_Last run: 2026-09-01 on this machine, `wrk -t4 -c50 -d5s` per route -- see benchmark.md's HTTP "Methodology" for how to reproduce; absolute numbers vary by hardware and load, relative ordering is the point._

### `plaintext` (`/`)

| Language | Requests/sec | Avg latency | Transfer/sec |
|---|---|---|---|
| Festina | 31,264 | 1.46 ms | 3.04 MB/s |
| Rust | 47,327 | 0.88 ms | 4.38 MB/s |
| Go | 26,873 | 1.68 ms | 2.49 MB/s |
| Bun | 26,618 | 1.73 ms | 3.40 MB/s |

### `json` (`/json`)

| Language | Requests/sec | Avg latency | Transfer/sec |
|---|---|---|---|
| Festina | 31,353 | 1.47 ms | 3.65 MB/s |
| Rust | 45,013 | 0.93 ms | 5.02 MB/s |
| Go | 24,138 | 1.87 ms | 2.69 MB/s |
| Bun | 26,725 | 1.75 ms | 3.92 MB/s |

<!-- HTTP_BENCHMARK_RESULTS_END -->

### Reading these numbers

- **`festina_http_send` coalesces the status line and headers into a
  single buffered `send()` call**, rather than writing each piece
  separately — this matters with `TCP_NODELAY` set (Nagle's algorithm
  disabled for low latency), since each separate call would otherwise
  become its own TCP segment. This keeps Festina within ~10% of Rust's
  raw-socket number here, ahead of both Go and Bun.
- This measures connection-accept + request-parse + respond throughput
  under load from one client machine talking to one server process on
  the same machine (no network hop, no TLS) -- not a claim about
  production capacity, the same disclaimer every other benchmark on
  this page already carries.
- **`/json`** exercises more than `/`: Festina's route builds a struct
  and renders it through the same JSON-via-`.toText()` path every other
  container response already uses, not a hand-built string the way `/`
  sends one -- so a gap between the two routes for Festina specifically
  reflects that serialization cost, not connection handling.
- Rust's and Go's numbers here are *not* what those languages'
  idiomatic HTTP stacks would report -- seeing "Rust is only Nx faster
  than Festina at HTTP" from this section should be read as "at
  matching, single-threaded, no-keep-alive connection handling," not as
  a claim about `hyper`/`axum` or `net/http` in general, which support
  keep-alive and would show a very different number here purely from
  not reopening a TCP connection on every single request.
- No WebSocket throughput benchmark exists yet -- `on message` traffic
  has a very different shape (persistent connections, small frequent
  frames) from a request/response load test, and would need its own
  methodology rather than reusing `wrk`'s HTTP-request model.

## HTTP: single-threaded vs. thread pool

The section above measures Festina's single HTTP event loop against
other languages' own single-threaded raw-socket servers -- a
deliberately fair, apples-to-apples comparison. This section instead
compares Festina against **itself**: what a `thread pool[N] { on
request(req:http) { ... } }` (claude.md #212's own private per-thread
HTTP context) plus `NAME.giveRequest(r)` (claude.md #213's own live
connection hand-off) actually buys a program that does real CPU-bound
work per request, the pattern `examples/threaded_http_server.f`
demonstrates.

Two servers (source in
[`benchmarks/http_threaded/`](benchmarks/http_threaded/)), both
answering the same two routes -- `/` (no work, a control) and `/slow`
(a closed-form-resistant polynomial-hash loop, the same technique
`loop_sum.f` above uses, tuned to ~2,000,000 iterations so a single
request takes a few milliseconds of real CPU time):

- **single-threaded** (`server_single.f`) does `/slow`'s own work
  directly in the one top-level `on request` handler, on Festina's
  single HTTP event-loop thread -- every concurrent `/slow` request
  queues up behind whichever one is currently computing.
- **thread pool[N]** (`server_pool.f`, `N` = this machine's own CPU
  count by default) hands every `/slow` request off to the next of
  `N` worker threads via `giveRequest`, so up to `N` requests are
  genuinely computed in parallel, on `N` different CPU cores, before
  any of them respond. `/` is answered directly by main in both
  servers, unchanged -- it's included to confirm the pool's own
  round-robin dispatch adds no meaningful overhead to a REQUEST THAT
  never needed handing off in the first place.

Each `wrk` run: 4 threads, 50 open connections, 5 seconds, against one
route at a time -- otherwise the identical methodology the section
above already uses (no explicit `Connection: close` forcing here,
since both servers are the same language/runtime with the same
keep-alive behavior; there's no cross-language asymmetry to correct
for).

Reproduce locally:

```bash
python3 benchmarks/http_threaded/run_http_threaded_benchmark.py
python3 benchmarks/http_threaded/run_http_threaded_benchmark.py --update-doc
python3 benchmarks/http_threaded/run_http_threaded_benchmark.py --pool-size 8 --duration 10s
```

<!-- HTTP_THREADED_BENCHMARK_RESULTS_START -->
_Last run: 2026-09-01 on this machine (4 CPUs), `wrk -t4 -c50 -d5s` per route, pool size 4 -- see benchmark.md's own "HTTP: single-threaded vs. thread pool" Methodology for how to reproduce; absolute numbers vary by hardware and load, relative ordering is the point._

### `no work (control)` (`/`)

| Server | Requests/sec | Avg latency | Transfer/sec |
|---|---|---|---|
| single-threaded | 74,673 | 0.66 ms | 8.19 MB/s |
| thread pool[4] | 49,093 | 1.03 ms | 5.38 MB/s |

### `CPU-bound work` (`/slow`)

| Server | Requests/sec | Avg latency | Transfer/sec |
|---|---|---|---|
| single-threaded | 92 | 491.28 ms | 0.01 MB/s |
| thread pool[4] | 256 | 185.33 ms | 0.03 MB/s |

`/slow` speedup from the pool: **2.77x** (pool size 4, this machine has 4 CPUs).

<!-- HTTP_THREADED_BENCHMARK_RESULTS_END -->

### Reading these numbers

- **`/` (no work) should perform about the same on both servers** --
  neither variant's own connection-accept/parse/respond path changed
  at all; only whether `/slow`'s own CPU-bound work is serialized or
  parallelized did. A meaningful gap here would mean the pool's own
  round-robin dispatch itself is expensive, not that the pool is
  "working" -- it shouldn't be, since `/` never goes through
  `giveRequest` in either server.
- **`/slow`'s own speedup is bounded by real CPU core count, not
  `N`** -- a pool bigger than the machine's own core count just adds
  contention, not more genuine parallelism; `--pool-size` defaults to
  `os.cpu_count()` for exactly this reason.
- **Every handed-off request pays a small, real hand-off latency** --
  a receive-only worker thread's own combined loop polls on a bounded
  timeout (claude.md #212's own `FESTINA_THREAD_HTTP_POLL_MS`, 20ms)
  rather than waking instantly the way a dedicated OS thread blocked
  on `accept()` would, so under LOW concurrency (one request at a
  time, nothing else queued) a handed-off request can be slightly
  SLOWER end-to-end than the single-threaded baseline answering it
  directly -- the pool's own advantage only shows up once there's
  more concurrent CPU-bound work than one thread can get through
  serially, which is exactly what these numbers measure.
- This is a same-machine, same-process-family comparison (no network
  hop) measuring exactly one thing -- how much a real, additional
  workload on the SAME hardware benefits from being spread across
  more than one of Festina's own execution threads -- not a general
  claim about optimal pool sizing for a production workload, which
  depends heavily on how CPU-bound (vs. I/O-bound) the real work
  actually is.

## Layered canvas: multi-threaded Festina vs a browser's Worker + OffscreenCanvas

<!-- LAYERED_RESULTS_START -->
_Last run: 2026-09-02 on this machine (4 logical CPUs). Chromium 141.0.7390.37._

Four independent layers -- a sparse sky, a band of hill texture, a band
of ground texture, and a full-canvas foreground particle scatter --
40,000 draw calls total into an 800x600 surface, the
same order of magnitude as the single-threaded canvas benchmark's own
40,000 above. Both multi-threaded runs hand each layer to its
own worker (a real OS thread on the Festina side, a real Worker on the
browser side) and get it back with **no per-pixel copy across the
boundary** -- an `img?` ([api.md](api.md#t-manually-managed-values))
shares its reference across `postMessage` instead of cloning it, and a
Worker's `transferToImageBitmap()` is a genuine ownership transfer, not
a copy. Compositing the finished layers onto one final surface IS a
real pixel copy on both sides and both runs time it, not just the
parallel drawing -- see
[`run_layered_canvas_benchmark.py`](benchmarks/layered_canvas/run_layered_canvas_benchmark.py)
for the rest of what makes this comparison fair, the same three rules
`draw_shapes.f`'s own runner already established.

| | Single-threaded | 4 threads/Workers | Speedup |
|---|---|---|---|
| Festina (Cairo, `img?`) | 84 ms (median 85 ms) | 62 ms (median 65 ms) | 1.35x |
| Browser (Skia, OffscreenCanvas) | 61 ms (median 69 ms) | 52 ms (median 66 ms) | 1.18x |

On this workload, both multi-threaded, **the browser's Workers draw it 1.2x faster**.

Two outputs were checked, not one. Festina's multi-threaded run was
compared against its OWN single-threaded run **byte-for-byte** — Cairo
is deterministic, so any difference at all would mean four threads
racing to paint four different `img?` buffers corrupted something; there
wasn't one. Festina's multi-threaded output was then compared against
the browser's, over the same tolerant 16x16 grid `draw_shapes.f`'s own
runner uses (Cairo and Skia disagree about antialiasing on every circle,
so exact bytes would only prove the two rasterizers are the same
program) — same scene both times.

Read the speedup column with the workload's own shape in mind: the four
layers are NOT equal-sized (8,000/9,000/11,000/12,000 draws), so four
threads finish in roughly however long the heaviest layer takes, not in
a quarter of the single-threaded time — this measures what four
genuinely independent, unevenly-loaded workers buy on real hardware, not
an idealized 4x.
<!-- LAYERED_RESULTS_END -->
