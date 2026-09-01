#!/usr/bin/env python3
"""Runs the two Festina HTTP servers in benchmarks/http_threaded/
(single-threaded baseline vs. a `thread pool[N]` + `giveRequest`
worker pool -- claude.md #212/#213) under real concurrent load with
`wrk` (installed separately -- not a project dependency, the same
"the runner skips what's not installed" spirit
benchmarks/http/run_http_benchmarks.py already uses), and prints (or
writes, with --update-doc) a Markdown results table for
benchmark.md's own "HTTP: single-threaded vs. thread pool" section.

Both servers answer the identical two routes -- '/' (no work, answers
immediately) and '/slow' (a real, closed-form-resistant CPU-bound
loop -- the same technique benchmarks/loop_sum.f uses so an optimizer
can't fold it away into a constant). The only difference is WHERE
'/slow"'s own work runs: server_single.f does it directly on
Festina's one HTTP event-loop thread (so every concurrent '/slow'
request queues up behind whichever one is currently computing);
server_pool.f hands each '/slow' request off to the next of N worker
threads via `NAME.giveRequest(r)`, so up to N requests are genuinely
computed in parallel. '/' is included as a control: it should perform
about the same on both servers, since neither variant's own
connection-accept/parse/respond path changed at all -- only whether
the CPU-bound work is serialized or parallelized.

Usage:
    python3 benchmarks/http_threaded/run_http_threaded_benchmark.py                # print
    python3 benchmarks/http_threaded/run_http_threaded_benchmark.py --update-doc
    python3 benchmarks/http_threaded/run_http_threaded_benchmark.py --pool-size 8 --duration 10s

Requires `wrk` on PATH (any Linux/macOS package manager has it --
`apt install wrk`/`brew install wrk`); skipped with a note, not a hard
failure, if it's missing.
"""
import argparse
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BENCH_DIR = os.path.dirname(os.path.abspath(__file__))
BENCHMARK_MD = os.path.join(REPO_ROOT, "benchmark.md")

ROUTES = ["/", "/slow"]
ROUTE_LABELS = {"/": "no work (control)", "/slow": "CPU-bound work"}
VARIANTS = [
    ("single-threaded", "server_single.f"),
    ("thread pool", None),  # server_pool.f is generated from the .template, see build_pool_server
]


def _free_tcp_port():
    # Same OS-assigned-port, TOCTOU-accepting technique
    # tests/conftest.py's own _free_tcp_port fixture and
    # benchmarks/http/run_http_benchmarks.py already use.
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_for_port(port, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return True
        except OSError:
            time.sleep(0.05)
    return False


def build_single_server(workdir):
    sys.path.insert(0, REPO_ROOT)
    from festina import cli as cli_mod

    src = os.path.join(BENCH_DIR, "server_single.f")
    out = os.path.join(workdir, "server_single")
    cli_mod.compile_file(src, out)
    return out


def build_pool_server(workdir, pool_size):
    sys.path.insert(0, REPO_ROOT)
    from festina import cli as cli_mod

    # claude.md #209: `thread NAME[N]` requires N to be a compile-time
    # literal, so the pool size can't be read from argv the way the
    # port is -- substituted as a plain token instead, the same
    # technique tests/conftest.py's own __PORT__ substitution already
    # uses (see that fixture's own comment on why not str.format():
    # Festina source is full of its own bare `{`/`}`).
    template_path = os.path.join(BENCH_DIR, "server_pool.f.template")
    with open(template_path, encoding="utf-8") as f:
        source = f.read()
    source = source.replace("__POOL_SIZE__", str(pool_size))
    src = os.path.join(workdir, "server_pool.f")
    with open(src, "w", encoding="utf-8") as f:
        f.write(source)
    out = os.path.join(workdir, "server_pool")
    cli_mod.compile_file(src, out)
    return out


_WRK_REQ_SEC_RE = re.compile(r"Requests/sec:\s*([\d.]+)")
_WRK_LATENCY_RE = re.compile(r"Latency\s+([\d.]+)(us|ms|s)")
_WRK_TRANSFER_RE = re.compile(r"Transfer/sec:\s*([\d.]+)(KB|MB|GB)")


def _parse_wrk_output(text):
    req_sec = _WRK_REQ_SEC_RE.search(text)
    latency = _WRK_LATENCY_RE.search(text)
    transfer = _WRK_TRANSFER_RE.search(text)
    latency_ms = None
    if latency:
        value, unit = float(latency.group(1)), latency.group(2)
        latency_ms = {"us": value / 1000, "ms": value, "s": value * 1000}[unit]
    transfer_mb_s = None
    if transfer:
        value, unit = float(transfer.group(1)), transfer.group(2)
        transfer_mb_s = {"KB": value / 1024, "MB": value, "GB": value * 1024}[unit]
    return {
        "requests_per_sec": float(req_sec.group(1)) if req_sec else None,
        "avg_latency_ms": latency_ms,
        "transfer_mb_s": transfer_mb_s,
    }


def _run_wrk(url, duration, connections, threads):
    cmd = ["wrk", f"-t{threads}", f"-c{connections}", f"-d{duration}", "--latency", url]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"wrk failed against {url}:\n{result.stdout}\n{result.stderr}")
    return _parse_wrk_output(result.stdout)


def run_all(duration, connections, threads, pool_size):
    if shutil.which("wrk") is None:
        print("-- wrk: not installed, cannot run this benchmark", file=sys.stderr)
        return {}

    results = {}  # (variant_name, route) -> dict
    with tempfile.TemporaryDirectory() as workdir:
        builders = {
            "single-threaded": lambda: build_single_server(workdir),
            f"thread pool[{pool_size}]": lambda: build_pool_server(workdir, pool_size),
        }
        for variant_name, builder in builders.items():
            try:
                server_path = builder()
            except Exception as e:  # noqa: BLE001 -- report and move on, same as the cross-language runner
                print(f"-- {variant_name}: build failed: {e}", file=sys.stderr)
                continue

            port = _free_tcp_port()
            proc = subprocess.Popen([server_path, str(port)], cwd=workdir,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                if not _wait_for_port(port):
                    print(f"-- {variant_name}: server never opened port {port}, skipping",
                          file=sys.stderr)
                    continue
                for route in ROUTES:
                    url = f"http://127.0.0.1:{port}{route}"
                    try:
                        r = _run_wrk(url, duration, connections, threads)
                    except RuntimeError as e:
                        print(f"-- {variant_name}{route}: {e}", file=sys.stderr)
                        continue
                    results[(variant_name, route)] = r
                    print(f"-- {variant_name}{route}: "
                          f"{r['requests_per_sec']:.0f} req/s, "
                          f"{r['avg_latency_ms']:.2f} ms avg latency, "
                          f"{r['transfer_mb_s']:.2f} MB/s", file=sys.stderr)
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
    return results


def render_table(results, pool_size):
    variants = [f"single-threaded", f"thread pool[{pool_size}]"]
    variants = [v for v in variants if any(k[0] == v for k in results)]
    lines = []
    for route in ROUTES:
        lines.append(f"### `{ROUTE_LABELS[route]}` (`{route}`)")
        lines.append("")
        lines.append("| Server | Requests/sec | Avg latency | Transfer/sec |")
        lines.append("|---|---|---|---|")
        for variant in variants:
            r = results.get((variant, route))
            if r is None or r.get("requests_per_sec") is None:
                lines.append(f"| {variant} | not available | | |")
                continue
            lines.append(
                f"| {variant} | {r['requests_per_sec']:,.0f} | "
                f"{r['avg_latency_ms']:.2f} ms | {r['transfer_mb_s']:.2f} MB/s |"
            )
        lines.append("")
    single = results.get(("single-threaded", "/slow"))
    pooled = results.get((f"thread pool[{pool_size}]", "/slow"))
    if single and pooled and single.get("requests_per_sec") and pooled.get("requests_per_sec"):
        speedup = pooled["requests_per_sec"] / single["requests_per_sec"]
        lines.append(f"`/slow` speedup from the pool: **{speedup:.2f}x** "
                      f"(pool size {pool_size}, this machine has {os.cpu_count()} CPUs).")
        lines.append("")
    return "\n".join(lines)


def update_doc(table_md, duration, connections, threads, pool_size):
    with open(BENCHMARK_MD, encoding="utf-8") as f:
        content = f.read()
    start_marker = "<!-- HTTP_THREADED_BENCHMARK_RESULTS_START -->"
    end_marker = "<!-- HTTP_THREADED_BENCHMARK_RESULTS_END -->"
    if start_marker not in content or end_marker not in content:
        raise SystemExit(f"benchmark.md is missing {start_marker}/{end_marker} markers -- "
                          "can't know where to splice the threaded-HTTP results table in")
    date = time.strftime("%Y-%m-%d")
    replacement = (
        f"{start_marker}\n_Last run: {date} on this machine ({os.cpu_count()} CPUs), "
        f"`wrk -t{threads} -c{connections} -d{duration}` per route, pool size "
        f"{pool_size} -- see benchmark.md's own \"HTTP: single-threaded vs. thread "
        f"pool\" Methodology for how to reproduce; absolute numbers vary by "
        f"hardware and load, relative ordering is the point._\n\n{table_md}\n{end_marker}"
    )
    new_content = re.sub(
        re.escape(start_marker) + r".*?" + re.escape(end_marker),
        lambda _match: replacement,
        content,
        flags=re.DOTALL,
    )
    with open(BENCHMARK_MD, "w", encoding="utf-8") as f:
        f.write(new_content)
    print(f"updated {BENCHMARK_MD}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--update-doc", action="store_true",
                     help="rewrite the threaded-HTTP results table in benchmark.md instead of just printing it")
    ap.add_argument("--duration", default="5s", help="wrk -d value per route (default: 5s)")
    ap.add_argument("--connections", type=int, default=50, help="wrk -c value (default: 50)")
    ap.add_argument("--threads", type=int, default=4, help="wrk -t value (default: 4)")
    ap.add_argument("--pool-size", type=int, default=os.cpu_count() or 4,
                     help="thread pool[N] size for the pooled server (default: this machine's CPU count)")
    args = ap.parse_args()

    results = run_all(args.duration, args.connections, args.threads, args.pool_size)
    table_md = render_table(results, args.pool_size)
    if args.update_doc:
        update_doc(table_md, args.duration, args.connections, args.threads, args.pool_size)
    else:
        print(table_md)


if __name__ == "__main__":
    main()
