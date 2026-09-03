"""claude.md #244: the wasm.md benchmarks, run inside a real browser.

Same five programs and the same three languages as
run_wasm_benchmarks.py (Festina, C, Go -- see that file for how each is
built), but instead of Node's `node:wasi` host every .wasm is loaded
into headless Chromium and run on THIS project's own browser WASI host
(runtime/wasm/festina_wasi_browser.js, claude.md #237), inside a Web
Worker, exactly the way browser.html runs a Festina program. All three
languages go through the identical host, so what differs between the
rows is each language's generated code and its own runtime -- not three
different hosts' overheads.

What is timed, and where. The clock is `performance.now()` INSIDE the
worker, around three separate steps, so a browser's own process/launch
cost never appears in any number:

  compile      WebAssembly.compile(bytes)    -- V8 turns the module into
                                                machine code (its baseline
                                                tier first; the optimizing
                                                tier follows in the
                                                background)
  instantiate  WebAssembly.instantiate(module, imports)
  run          instance.exports._start()     -- the program itself,
                                                start to proc_exit

`total` is the sum of the three: what a page pays from having the bytes
in hand to having the program's output. Each program is run 1 untimed
warmup + 7 timed times in the same page (a fresh host and a fresh
instance each time; V8 may serve later compiles of the same bytes from
its own cache, which is also what a real page would see), and the
minimum and median are reported. Every language's stdout is checked to
match the others' before a row is trusted, the same rule the Node runner
applies.

Usage:
    python3 benchmarks/run_wasm_browser_benchmarks.py               # print results
    python3 benchmarks/run_wasm_browser_benchmarks.py --update-doc  # also rewrite wasm.md's table

Needs the same toolchains as run_wasm_benchmarks.py plus Playwright
with a Chromium (`pip install playwright && playwright install
chromium`; FESTINA_CHROMIUM names a different Chromium binary to drive,
exactly as tests/test_wasm_browser.py accepts). A missing toolchain or
browser is reported and skipped rather than failing the run.
"""
import argparse
import http.server
import json
import os
import re
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time

BENCH_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(BENCH_DIR)
WASM_MD = os.path.join(REPO_ROOT, "wasm.md")
HOST_JS = os.path.join(REPO_ROOT, "runtime", "wasm", "festina_wasi_browser.js")

sys.path.insert(0, BENCH_DIR)
from run_wasm_benchmarks import (  # noqa: E402
    BENCHMARKS, CWasmToolchain, FestinaWasmToolchain, GoWasmToolchain, _fmt_ms, _fmt_size)

WARMUPS = 1
RUNS = 7

# The page + worker that do the timing. Served from the same directory
# as the host and the .wasm files (module workers cannot load from
# file://), with the cross-origin-isolation headers that let the host
# sleep with Atomics.wait if a program ever asks to.
_PAGE_HTML = """<!doctype html>
<meta charset="utf-8">
<title>wasm benchmark</title>
<pre id="out"></pre>
<script type="module">
  window.benchResult = null;
  const params = new URLSearchParams(location.search);
  const worker = new Worker("bench_worker.js", { type: "module" });
  worker.onmessage = ({ data }) => {
    if (data.kind === "log") document.getElementById("out").textContent += data.line + "\\n";
    else window.benchResult = data;
  };
  worker.postMessage({ wasm: params.get("wasm"), runs: Number(params.get("runs") || 8) });
</script>
"""

_WORKER_JS = """import { FestinaWasi } from "./festina_wasi_browser.js";

self.onmessage = async ({ data }) => {
  try {
    const bytes = new Uint8Array(await (await fetch(data.wasm)).arrayBuffer());
    const samples = [];
    let stdout = null;
    for (let i = 0; i < data.runs; i++) {
      const host = new FestinaWasi({ args: ["program.wasm"] });
      const t0 = performance.now();
      const module = await WebAssembly.compile(bytes);
      const t1 = performance.now();
      const instance = await WebAssembly.instantiate(module, { wasi_snapshot_preview1: host.imports });
      const t2 = performance.now();
      const code = host.start(instance);
      const t3 = performance.now();
      if (code !== 0) throw new Error("exit code " + code + "\\n" + host.stderr);
      if (stdout === null) stdout = host.stdout;
      else if (host.stdout !== stdout) throw new Error("stdout changed between runs");
      samples.push({ compile: t1 - t0, instantiate: t2 - t1, run: t3 - t2, total: t3 - t0 });
    }
    self.postMessage({ kind: "done", samples, stdout, size: bytes.length });
  } catch (err) {
    self.postMessage({ kind: "error", message: String(err && err.stack ? err.stack : err) });
  }
};
"""


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _serve(directory):
    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=directory, **kw)

        def end_headers(self):
            self.send_header("Cross-Origin-Opener-Policy", "same-origin")
            self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def log_message(self, *a):
            pass

    Handler.extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map,
                              ".js": "text/javascript", ".mjs": "text/javascript",
                              ".wasm": "application/wasm"}
    port = _free_port()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{port}"


def _launch_chromium(pw):
    launch_options = [{}]
    override = os.environ.get("FESTINA_CHROMIUM")
    if override:
        launch_options.insert(0, {"executable_path": override})
    failure = None
    for options in launch_options:
        try:
            return pw.chromium.launch(**options)
        except Exception as exc:  # no browser binary
            failure = exc
    raise RuntimeError(f"Playwright's Chromium isn't available: {failure}")


def _chrome_version(browser):
    try:
        return f"Chromium {browser.version}"
    except Exception:
        return "Chromium"


def run_all():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("-- playwright isn't installed (pip install playwright; playwright install chromium); "
              "skipping everything.", file=sys.stderr)
        return {}, None

    clang = shutil.which("clang")
    toolchains = [FestinaWasmToolchain(clang), CWasmToolchain(clang), GoWasmToolchain()]
    results = {}   # (language, benchmark) -> dict
    with tempfile.TemporaryDirectory() as workdir:
        shutil.copy(HOST_JS, os.path.join(workdir, "festina_wasi_browser.js"))
        with open(os.path.join(workdir, "bench.html"), "w") as f:
            f.write(_PAGE_HTML)
        with open(os.path.join(workdir, "bench_worker.js"), "w") as f:
            f.write(_WORKER_JS)

        built = {}   # (language, benchmark) -> relative url
        for toolchain in toolchains:
            if not toolchain.available():
                print(f"-- {toolchain.name}: no wasm32-wasi toolchain available, skipping", file=sys.stderr)
                continue
            lang_dir = os.path.join(workdir, toolchain.name)
            os.makedirs(lang_dir, exist_ok=True)
            for bench in BENCHMARKS:
                try:
                    path, _ = toolchain.build(bench, lang_dir)
                except subprocess.CalledProcessError as e:
                    stderr = e.stderr.decode(errors="replace") if e.stderr else ""
                    print(f"-- {toolchain.name}/{bench}: build failed:\n{stderr}", file=sys.stderr)
                    continue
                built[(toolchain.name, bench)] = f"{toolchain.name}/{os.path.basename(path)}"

        server, url = _serve(workdir)
        chromium = None
        try:
            with sync_playwright() as pw:
                browser = _launch_chromium(pw)
                chromium = _chrome_version(browser)
                page = browser.new_page()
                for (lang, bench), rel in built.items():
                    page.goto(f"{url}/bench.html?wasm={rel}&runs={WARMUPS + RUNS}")
                    page.wait_for_function("window.benchResult !== null", timeout=600000)
                    data = page.evaluate("window.benchResult")
                    if data["kind"] != "done":
                        print(f"-- {lang}/{bench}: failed in the browser:\n{data['message']}", file=sys.stderr)
                        continue
                    samples = data["samples"][WARMUPS:]
                    results[(lang, bench)] = {
                        "stdout": data["stdout"],
                        "size_bytes": data["size"],
                        **{f"{k}_min": min(s[k] for s in samples) / 1000.0
                           for k in ("compile", "instantiate", "run", "total")},
                        **{f"{k}_median": statistics.median(s[k] for s in samples) / 1000.0
                           for k in ("compile", "instantiate", "run", "total")},
                    }
                    print(f"-- {lang}/{bench}: compile={_fmt_ms(results[(lang, bench)]['compile_min'])} "
                          f"run={_fmt_ms(results[(lang, bench)]['run_min'])} "
                          f"total={_fmt_ms(results[(lang, bench)]['total_min'])} "
                          f"size={_fmt_size(data['size'])}", file=sys.stderr)
                browser.close()
        finally:
            server.shutdown()
            server.server_close()

    # Every language must have printed the same thing -- a benchmark that
    # ran the wrong program is not a benchmark.
    for bench in BENCHMARKS:
        outputs = {lang: r["stdout"] for (lang, b), r in results.items() if b == bench}
        if len(set(outputs.values())) > 1:
            print(f"-- {bench}: stdout DIFFERS between languages, dropping the row: "
                  f"{ {k: v[:40] for k, v in outputs.items()} }", file=sys.stderr)
            for lang in outputs:
                results.pop((lang, bench), None)
    return results, chromium


def render_table(results, chromium):
    languages = [t for t in ("Festina", "C", "Go") if any(k[0] == t for k in results)]
    lines = [f"_Last run: {time.strftime('%Y-%m-%d')} on this machine, {chromium or 'Chromium'}; "
             "every number is measured with `performance.now()` inside the worker, "
             "so the browser's own launch cost is in none of them. See \"Benchmark "
             "methodology\" above for the runs/min/median rule._", ""]
    for bench in BENCHMARKS:
        rows = [(lang, results[(lang, bench)]) for lang in languages if (lang, bench) in results]
        if not rows:
            continue
        lines.append(f"### `{bench}` (in Chromium, on the project's own WASI host)")
        lines.append("")
        lines.append("| Language | Run (min of 7) | Run (median) | Compile + instantiate (min) | Total (min) | .wasm size |")
        lines.append("|---|---|---|---|---|---|")
        for lang, r in rows:
            lines.append(f"| {lang} | {_fmt_ms(r['run_min'])} | {_fmt_ms(r['run_median'])} | "
                         f"{_fmt_ms(r['compile_min'] + r['instantiate_min'])} | "
                         f"{_fmt_ms(r['total_min'])} | {_fmt_size(r['size_bytes'])} |")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def update_doc(table_md):
    start_marker = "<!-- WASM_BROWSER_RESULTS_START -->"
    end_marker = "<!-- WASM_BROWSER_RESULTS_END -->"
    with open(WASM_MD) as f:
        doc = f.read()
    block = f"{start_marker}\n{table_md}{end_marker}"
    pattern = re.compile(re.escape(start_marker) + r".*?" + re.escape(end_marker), re.S)
    if pattern.search(doc):
        doc = pattern.sub(lambda _: block, doc)
    else:
        anchor = "### Reading these numbers"
        section = ("### In a browser: Festina vs C vs Go\n\n"
                   "The same five programs, run inside headless Chromium on this project's "
                   "own browser WASI host (`runtime/wasm/festina_wasi_browser.js`) instead of "
                   "Node's -- see [`run_wasm_browser_benchmarks.py`]"
                   "(benchmarks/run_wasm_browser_benchmarks.py) for exactly what is timed.\n\n"
                   + block + "\n\n")
        doc = doc.replace(anchor, section + anchor, 1)
    with open(WASM_MD, "w") as f:
        f.write(doc)
    print(f"updated {WASM_MD}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--update-doc", action="store_true",
                        help="rewrite wasm.md's browser results block")
    parser.add_argument("--json", action="store_true", help="print raw results as JSON")
    args = parser.parse_args()
    results, chromium = run_all()
    if args.json:
        print(json.dumps({f"{k[0]}/{k[1]}": v for k, v in results.items()}, indent=2))
    table = render_table(results, chromium)
    print(table)
    if args.update_doc and results:
        update_doc(table)
    return 0


if __name__ == "__main__":
    sys.exit(main())
