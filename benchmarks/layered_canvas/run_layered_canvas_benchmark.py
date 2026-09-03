#!/usr/bin/env python3
"""claude.md #239: multi-threaded Festina (img? passed by reference
across threads) against a browser's Worker + OffscreenCanvas equivalent.

Four independent layers -- sky, hill, ground, a foreground particle
scatter -- 40,000 draw calls total, the same order of magnitude as the
single-threaded canvas benchmark's own 40,000
(benchmarks/canvas/draw_shapes.f). Each side is run two ways:

- **Single-threaded**: all four layers painted one after another on the
  one thread/context each runtime already has. The baseline every
  speedup number below is measured against.
- **Multi-threaded**: each layer painted by its own worker (a real OS
  thread on the Festina side; a real Worker on the browser side), the
  finished layer handed back with NO per-pixel copy across the boundary
  -- an `img?` (api.md's "T? -- manually-managed values") shares its
  reference across a `postMessage` instead of cloning it, and a
  Worker's `transferToImageBitmap()` is a genuine ownership TRANSFER
  (Structured Clone's own documented zero-copy path for exactly this
  object), not a copy either. Both sides composite the finished layers
  onto one final surface afterward, which IS a real pixel copy on both
  sides (Cairo's own `.clip()` and the browser's own `drawImage()`) --
  compositing was never free, only the cross-thread HANDOFF was made
  free, and both benchmarks below time that composite step too, not
  just the parallel drawing.

Two comparisons matter here, and they answer different questions:

1. **Festina MT vs Festina single-threaded** -- what threading buys a
   Festina program on this workload, with the two Cairo outputs
   compared BYTE-FOR-BYTE (not merely approximately): Cairo is
   deterministic, so pixel-identical output is the strongest available
   proof that four threads racing to draw four different images
   corrupted nothing.
2. **Festina MT vs the browser's own Worker + OffscreenCanvas MT** --
   the headline comparison this benchmark exists for. Outputs compared
   with the same tolerant tile grid draw_shapes' own runner uses (Cairo
   and Skia disagree about antialiasing on every circle; demanding
   identical bytes across engines would only prove they're the same
   rasterizer).

The same three measurement disciplines draw_shapes' own runner already
established apply here unchanged: both sides draw OFFSCREEN (no window,
no DOM canvas), the browser is forced to rasterize inside the timed
region (`convertToBlob` on its own final composited surface), and both
sides time with their own monotonic clock rather than by subtracting
process times.

Usage:
    python3 benchmarks/layered_canvas/run_layered_canvas_benchmark.py
    python3 benchmarks/layered_canvas/run_layered_canvas_benchmark.py --update-doc
"""
import argparse
import base64
import hashlib
import os
import re
import shutil
import statistics
import struct
import subprocess
import sys
import tempfile
import time
import zlib

BENCH_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(BENCH_DIR))
BENCHMARK_MD = os.path.join(REPO_ROOT, "benchmark.md")
FESTINA = os.path.join(REPO_ROOT, "bin", "festina")

CANVAS_W, CANVAS_H = 800, 600
TOTAL_SHAPES = 8000 + 9000 + 11000 + 12000   # 40,000 -- matches draw_shapes.f
RUNS = 9           # fewer than draw_shapes' 15: four real OS threads (or
                    # four real Workers) get spawned/torn down every run
                    # here, not just drawn into, so each run costs more
                    # wall time to begin with -- min-of-9 still comfortably
                    # separates signal from scheduling noise on this
                    # workload (measured spread: a few ms on the Festina
                    # side, tens of ms on the browser side).
WARMUPS = 1

CHROME_CANDIDATES = [
    os.environ.get("CHROMIUM_PATH", ""),
    "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
]


def _find_chromium():
    for path in CHROME_CANDIDATES:
        if path and os.path.exists(path):
            return path
    for name in ("chromium", "chromium-browser", "google-chrome"):
        found = shutil.which(name)
        if found:
            return found
    root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")
    if os.path.isdir(root):
        for entry in sorted(os.listdir(root)):
            candidate = os.path.join(root, entry, "chrome-linux", "chrome")
            if os.path.exists(candidate):
                return candidate
    return None


# ---- a minimal PNG reader, so this needs no image library --------------
# (identical to benchmarks/canvas/run_canvas_benchmark.py's own copy --
# duplicated rather than imported so this runner stays independently
# runnable, matching every other benchmark runner in this project)

def _decode_png(path):
    data = open(path, "rb").read()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{path} is not a PNG"
    pos, idat, width, height, ctype = 8, b"", 0, 0, 0
    while pos < len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        tag = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + length]
        if tag == b"IHDR":
            width, height, _bits, ctype = struct.unpack(">IIBB", chunk[:10])
        elif tag == b"IDAT":
            idat += chunk
        pos += 12 + length
    raw = zlib.decompress(idat)
    bpp = 4 if ctype == 6 else 3
    stride = width * bpp
    out, prev, p = bytearray(), bytearray(stride), 0
    for _ in range(height):
        f = raw[p]; p += 1
        line = bytearray(raw[p:p + stride]); p += stride
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            if f == 1: line[i] = (line[i] + a) & 255
            elif f == 2: line[i] = (line[i] + b) & 255
            elif f == 3: line[i] = (line[i] + (a + b) // 2) & 255
            elif f == 4:
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 255
        out += line
        prev = line
    return width, height, bpp, bytes(out)


def _tile_means(path, grid=16):
    """Mean RGB per cell of a grid x grid downsample, composited onto
    white first when the source has an alpha channel -- see
    run_canvas_benchmark.py's own identical helper for the full
    reasoning (a fresh/cleared canvas is transparent, not white, on the
    Festina side; the browser harness fills white before drawing)."""
    width, height, bpp, pixels = _decode_png(path)
    cells = []
    for gy in range(grid):
        for gx in range(grid):
            x0, x1 = gx * width // grid, (gx + 1) * width // grid
            y0, y1 = gy * height // grid, (gy + 1) * height // grid
            totals, count = [0, 0, 0], 0
            for y in range(y0, y1, 4):
                row = y * width * bpp
                for x in range(x0, x1, 4):
                    o = row + x * bpp
                    if bpp == 4:
                        a = pixels[o + 3] / 255.0
                        totals[0] += pixels[o] * a + 255 * (1 - a)
                        totals[1] += pixels[o + 1] * a + 255 * (1 - a)
                        totals[2] += pixels[o + 2] * a + 255 * (1 - a)
                    else:
                        totals[0] += pixels[o]; totals[1] += pixels[o + 1]; totals[2] += pixels[o + 2]
                    count += 1
            cells.append(tuple(t / max(count, 1) for t in totals))
    return cells


def compare_images(a, b, tolerance=40):
    ca, cb = _tile_means(a), _tile_means(b)
    worst = 0.0
    for pa, pb in zip(ca, cb):
        worst = max(worst, max(abs(x - y) for x, y in zip(pa, pb)))
    return worst, worst <= tolerance


def _sha256(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


# ---- Festina: single-threaded and multi-threaded ------------------------

def _run_festina(workdir, src_name, binary_name, png_name):
    src = os.path.join(BENCH_DIR, src_name)
    binary = os.path.join(workdir, binary_name)
    t0 = time.perf_counter()
    subprocess.run([FESTINA, "compile", src, "-o", binary],
                    check=True, capture_output=True, cwd=REPO_ROOT)
    build_s = time.perf_counter() - t0

    env = dict(os.environ)
    env.pop("DISPLAY", None)   # proves no window is involved, same as draw_shapes'
    totals, draws = [], []
    for i in range(WARMUPS + RUNS):
        t0 = time.perf_counter()
        result = subprocess.run([binary], cwd=workdir, capture_output=True,
                                 text=True, env=env, timeout=300)
        elapsed = time.perf_counter() - t0
        assert result.returncode == 0, result.stderr
        lines = result.stdout.split()
        assert lines[-1] == "true", result.stdout
        if i >= WARMUPS:
            totals.append(elapsed)
            draws.append(int(lines[0]) / 1000.0)
    return {
        "build_s": build_s,
        "total_s": min(totals),
        "draw_s": min(draws),
        "draw_median_s": statistics.median(draws),
        "png": os.path.join(workdir, png_name),
    }


def run_festina_single(workdir):
    return _run_festina(workdir, "layers_single.f", "festina_layers_single", "festina_layers_single.png")


def run_festina_threaded(workdir):
    return _run_festina(workdir, "layers_threaded.f", "festina_layers_mt", "festina_layers_mt.png")


# ---- Browser: single-threaded and multi-threaded (Worker+OffscreenCanvas) -

def _layers_source():
    with open(os.path.join(BENCH_DIR, "layers.js")) as f:
        return f.read()


# Draws all four layers on the SAME context/thread that ran this harness
# -- OffscreenCanvas works on a page's own main thread as well as inside
# a Worker, so this needs no <canvas> element and no window either,
# matching the Festina side's headless surfaces. convertToBlob forces a
# real rasterization (Skia batches and defers, same reasoning
# draw_shapes.js's own getImageData readback documents), and it's INSIDE
# the timed region on purpose.
_SINGLE_HARNESS = """
async (args) => {
    const [source] = args;
    const build = new Function(source + '; return {drawSky, drawHill, drawGround, drawFx};');
    const fns = build();
    const t0 = performance.now();
    const layers = [];
    for (const name of ['drawSky', 'drawHill', 'drawGround', 'drawFx']) {
        const c = new OffscreenCanvas(%d, %d);
        const ctx = c.getContext('2d');
        fns[name](ctx);
        layers.push(c);
    }
    const out = new OffscreenCanvas(%d, %d);
    const octx = out.getContext('2d');
    for (const l of layers) octx.drawImage(l, 0, 0);
    const blob = await out.convertToBlob({ type: 'image/png' });
    const elapsed_ms = performance.now() - t0;
    const buf = new Uint8Array(await blob.arrayBuffer());
    let binary = '';
    for (let i = 0; i < buf.length; i++) binary += String.fromCharCode(buf[i]);
    return { elapsed_ms, png_b64: btoa(binary) };
}
""" % (CANVAS_W, CANVAS_H, CANVAS_W, CANVAS_H)


# One real Worker per layer, each building its OWN OffscreenCanvas (never
# transferred FROM a DOM canvas -- constructed directly in the worker, so
# there is no main-thread canvas at all, the closest browser analog to
# Festina's own blankImage()). transferToImageBitmap() hands the
# finished layer back as a Transferable -- Structured Clone's own
# documented zero-copy path -- posted with an explicit transfer list, the
# direct browser counterpart of img?'s reference-sharing postMessage.
_MT_HARNESS = """
async (args) => {
    const [source] = args;
    const names = ['drawSky', 'drawHill', 'drawGround', 'drawFx'];
    function makeWorker(fnName) {
        const code = source + `
            self.onmessage = () => {
                const c = new OffscreenCanvas(%d, %d);
                const ctx = c.getContext('2d');
                ${fnName}(ctx);
                const bitmap = c.transferToImageBitmap();
                self.postMessage({ bitmap }, [bitmap]);
            };
        `;
        const blob = new Blob([code], { type: 'application/javascript' });
        return new Worker(URL.createObjectURL(blob));
    }
    const workers = names.map(makeWorker);

    const t0 = performance.now();
    const bitmaps = await Promise.all(workers.map(w => new Promise(resolve => {
        w.onmessage = (e) => resolve(e.data.bitmap);
        w.postMessage('go');
    })));
    const out = new OffscreenCanvas(%d, %d);
    const octx = out.getContext('2d');
    for (const bmp of bitmaps) octx.drawImage(bmp, 0, 0);
    const blob = await out.convertToBlob({ type: 'image/png' });
    const elapsed_ms = performance.now() - t0;
    workers.forEach(w => w.terminate());

    const buf = new Uint8Array(await blob.arrayBuffer());
    let binary = '';
    for (let i = 0; i < buf.length; i++) binary += String.fromCharCode(buf[i]);
    return { elapsed_ms, png_b64: btoa(binary) };
}
""" % (CANVAS_W, CANVAS_H, CANVAS_W, CANVAS_H)


def _run_browser(workdir, chromium, harness, png_name):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        t0 = time.perf_counter()
        browser = p.chromium.launch(executable_path=chromium)
        page = browser.new_page()
        page.set_content("<html><body></body></html>")
        startup_s = time.perf_counter() - t0

        source = _layers_source()
        samples, png_data = [], None
        for i in range(WARMUPS + RUNS):
            result = page.evaluate(harness, [source])
            if i >= WARMUPS:
                samples.append(result["elapsed_ms"] / 1000.0)
            png_data = result["png_b64"]
        browser.close()

    png_path = os.path.join(workdir, png_name)
    with open(png_path, "wb") as f:
        f.write(base64.b64decode(png_data))
    return {"startup_s": startup_s, "draw_s": min(samples),
            "draw_median_s": statistics.median(samples), "png": png_path}


def run_browser_single(workdir, chromium):
    return _run_browser(workdir, chromium, _SINGLE_HARNESS, "browser_layers_single.png")


def run_browser_threaded(workdir, chromium):
    return _run_browser(workdir, chromium, _MT_HARNESS, "browser_layers_mt.png")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--update-doc", action="store_true")
    args = ap.parse_args()

    chromium = _find_chromium()
    if chromium is None:
        print("layered canvas benchmark: no Chromium found -- skipping the browser half")
    try:
        import playwright  # noqa: F401
    except ImportError:
        print("layered canvas benchmark: playwright is not installed -- skipping the browser half")
        chromium = None

    workdir = tempfile.mkdtemp(prefix="festina-layered-canvas-")

    fest_single = run_festina_single(workdir)
    fest_mt = run_festina_threaded(workdir)

    # Festina MT vs Festina single: byte-identical is the bar, not merely
    # "close enough" -- Cairo is deterministic, so any difference at all
    # would mean the four threads racing to draw four different img?
    # buffers corrupted something.
    same_hash = _sha256(fest_single["png"]) == _sha256(fest_mt["png"])
    mt_speedup = fest_single["draw_s"] / fest_mt["draw_s"]
    print(f"Festina  single-threaded  min {fest_single['draw_s']*1000:6.1f} ms  median "
          f"{fest_single['draw_median_s']*1000:6.1f} ms")
    print(f"Festina  4 threads (img?) min {fest_mt['draw_s']*1000:6.1f} ms  median "
          f"{fest_mt['draw_median_s']*1000:6.1f} ms  ({mt_speedup:.2f}x vs single-threaded)")
    print(f"Output   Festina single vs Festina MT: "
          f"{'byte-identical' if same_hash else 'DIFFERENT -- a threading bug'}")
    if not same_hash:
        sys.exit(1)

    browser_single = browser_mt = None
    if chromium:
        browser_single = run_browser_single(workdir, chromium)
        browser_mt = run_browser_threaded(workdir, chromium)
        b_speedup = browser_single["draw_s"] / browser_mt["draw_s"]
        print(f"Browser  single-threaded  min {browser_single['draw_s']*1000:6.1f} ms  median "
              f"{browser_single['draw_median_s']*1000:6.1f} ms  (browser startup "
              f"{browser_single['startup_s']*1000:.0f} ms)")
        print(f"Browser  4 Workers (OffscreenCanvas) min {browser_mt['draw_s']*1000:6.1f} ms  "
              f"median {browser_mt['draw_median_s']*1000:6.1f} ms  ({b_speedup:.2f}x vs single-threaded)")

        worst, same = compare_images(fest_mt["png"], browser_mt["png"])
        print(f"Output   Festina MT vs Browser MT, worst per-channel difference over a "
              f"16x16 grid: {worst:.1f} ({'same scene' if same else 'DIFFERENT -- results not comparable'})")
        if not same:
            sys.exit(1)

        ratio = browser_mt["draw_s"] / fest_mt["draw_s"]
        verdict = (f"the browser's Workers draw it {1/ratio:.1f}x faster"
                   if ratio < 1 else f"Festina's threads draw it {ratio:.1f}x faster")
        print(f"On this workload, both multi-threaded: {verdict}.")

    if args.update_doc and browser_mt:
        _update_doc(fest_single, fest_mt, browser_single, browser_mt, chromium)
        print(f"wrote {BENCHMARK_MD}")
    return 0


def _update_doc(fest_single, fest_mt, browser_single, browser_mt, chromium):
    fs_min, fs_med = fest_single["draw_s"] * 1000, fest_single["draw_median_s"] * 1000
    fm_min, fm_med = fest_mt["draw_s"] * 1000, fest_mt["draw_median_s"] * 1000
    bs_min, bs_med = browser_single["draw_s"] * 1000, browser_single["draw_median_s"] * 1000
    bm_min, bm_med = browser_mt["draw_s"] * 1000, browser_mt["draw_median_s"] * 1000
    fest_speedup = fs_min / fm_min
    browser_speedup = bs_min / bm_min
    ratio = bm_min / fm_min
    verdict = (f"the browser's Workers draw it {1/ratio:.1f}x faster"
               if ratio < 1 else f"Festina's threads draw it {ratio:.1f}x faster")

    table = f"""<!-- LAYERED_RESULTS_START -->
_Last run: {time.strftime('%Y-%m-%d')} on this machine ({os.cpu_count()} logical CPUs). {_chrome_version(chromium)}._

Four independent layers -- a sparse sky, a band of hill texture, a band
of ground texture, and a full-canvas foreground particle scatter --
{TOTAL_SHAPES:,} draw calls total into an {CANVAS_W}x{CANVAS_H} surface, the
same order of magnitude as the single-threaded canvas benchmark's own
{TOTAL_SHAPES:,} above. Both multi-threaded runs hand each layer to its
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
| Festina (`img?`) | {fs_min:.0f} ms (median {fs_med:.0f} ms) | {fm_min:.0f} ms (median {fm_med:.0f} ms) | {fest_speedup:.2f}x |
| Browser (Skia, OffscreenCanvas) | {bs_min:.0f} ms (median {bs_med:.0f} ms) | {bm_min:.0f} ms (median {bm_med:.0f} ms) | {browser_speedup:.2f}x |

On this workload, both multi-threaded, **{verdict}**.

Two outputs were checked, not one. Festina's multi-threaded run was
compared against its OWN single-threaded run **byte-for-byte** — Cairo
is deterministic, so any difference at all would mean four threads
racing to paint four different `img?` buffers corrupted something; there
wasn't one. Festina's multi-threaded output was then compared against
the browser's, over the same tolerant 16x16 grid `draw_shapes.f`'s own
runner uses (Cairo and Skia disagree about antialiasing on every circle,
so exact bytes would only prove the two rasterizers are the same
program) — same scene both times.

Read the speedup column with the workload's own shape in mind. The four
layers are NOT equal-sized (8,000/9,000/11,000/12,000 draws), so four
threads finish in roughly however long the heaviest layer takes, not in
a quarter of the single-threaded time — this measures what four
genuinely independent, unevenly-loaded workers buy on real hardware, not
an idealized 4x. And on the Festina side the parallel part is now
small: after claude.md #240 the 40,000 draw calls take about 4 ms on
one thread, so the ~4 ms of serial work both runs share — four
full-surface `clip()` copies to turn each `img?` back into a plain
`img`, then four composites onto the canvas — is roughly half of either
number, and no amount of threading touches it.

When this benchmark was first written (claude.md #239) Festina drew it
in 84 ms single-threaded and 62 ms with four threads, and the browser's
Workers were 1.2x faster than Festina's threads. Measuring where those
62 ms went found two things (claude.md #240). Circles onto an `img` were
tessellated by Cairo on every call, 32–40 ms per layer; they are now
stamped from a cached per-radius coverage mask, blended directly into
the pixels, and byte-identical. And the four threads were not running
in parallel at all: each painted a freshly allocated 1.92 MB surface,
and the page faults that materialize fresh memory on first touch
serialize across threads inside one process, so four threads' worth of
faults took four threads' worth of time — every layer finished in the
11–12 ms a single thread needed for all four. Surfaces are now faulted
in when created (one `madvise` on Linux), which took the four-thread
draw from 12 ms to 3.4 ms in an isolated reproduction and is what the
table above reflects.
<!-- LAYERED_RESULTS_END -->"""

    with open(BENCHMARK_MD) as f:
        doc = f.read()
    pattern = re.compile(r"<!-- LAYERED_RESULTS_START -->.*?<!-- LAYERED_RESULTS_END -->", re.S)
    if pattern.search(doc):
        doc = pattern.sub(table, doc)
    else:
        doc = doc.rstrip() + (
            "\n\n## Layered canvas: multi-threaded Festina vs a browser's "
            "Worker + OffscreenCanvas\n\n" + table + "\n")
    with open(BENCHMARK_MD, "w") as f:
        f.write(doc)


def _chrome_version(chromium):
    try:
        out = subprocess.run([chromium, "--version"], capture_output=True, text=True, timeout=30)
        return out.stdout.strip() or "unknown build"
    except Exception:
        return "unknown build"


if __name__ == "__main__":
    sys.exit(main())
