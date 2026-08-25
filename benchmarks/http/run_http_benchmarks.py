#!/usr/bin/env python3
"""Runs the Festina/Rust/Go/Bun HTTP server benchmarks in
benchmarks/http/ and prints (or writes, with --update-doc) a Markdown
results table for benchmark.md's HTTP section.

claude.md #152's own follow-up to claude.md #151 (the HTTP/WebSocket
feature itself): four equivalent-logic servers (server.f/.rs/.go/.js --
see server.f's own comment for exactly what "equivalent" means here),
each answering the same two routes ('/' plaintext, '/json' a small
JSON body), load-tested with `wrk` (installed separately -- not a
project dependency, the same "the runner skips what's not installed"
spirit run_benchmarks.py already uses for rustc/go/bun).

Unlike run_benchmarks.py's five single-shot programs, an HTTP
benchmark needs a long-running server process, an external load
generator, and a nonzero warm-up + measurement window per run -- this
script is the slower, "run it before/after a change that plausibly
affects the HTTP runtime specifically" counterpart, not something to
run on every commit.

Usage:
    python3 benchmarks/http/run_http_benchmarks.py                # print
    python3 benchmarks/http/run_http_benchmarks.py --update-doc   # also
                                                                    # rewrite
                                                                    # benchmark.md's
                                                                    # HTTP
                                                                    # section
    python3 benchmarks/http/run_http_benchmarks.py --duration 10s --connections 100

Requires `wrk` on PATH (any Linux/macOS package manager has it --
`apt install wrk`/`brew install wrt`) and whichever of festina/rustc/go/bun
are installed; each missing piece is skipped with a note, not a hard
failure.
"""
import argparse
import os
import re
import shutil
import socket
import subprocess
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BENCH_DIR = os.path.dirname(os.path.abspath(__file__))
BENCHMARK_MD = os.path.join(REPO_ROOT, "benchmark.md")

ROUTES = ["/", "/json"]
ROUTE_LABELS = {"/": "plaintext", "/json": "json"}


def _free_tcp_port():
    """Same OS-assigned-port technique tests/conftest.py's own
    _free_tcp_port fixture uses (claude.md #151) -- TOCTOU-accepting,
    not a project-specific concern."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_for_port(port, timeout=10.0):
    """Polls with a real connect() attempt rather than a fixed sleep --
    the same reasoning tests/conftest.py's compile_and_run_server
    fixture already documents: a fixed delay is either flaky under
    load or wastefully long otherwise."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return True
        except OSError:
            time.sleep(0.05)
    return False


class Toolchain:
    name = None

    def available(self):
        raise NotImplementedError

    def build(self, workdir):
        """Returns the argv prefix to launch the server (port appended
        by the caller), or None if the build failed."""
        raise NotImplementedError


class FestinaToolchain(Toolchain):
    name = "Festina"

    def available(self):
        return True

    def build(self, workdir):
        sys.path.insert(0, REPO_ROOT)
        from festina import cli as cli_mod

        src = os.path.join(BENCH_DIR, "server.f")
        out = os.path.join(workdir, "server_festina")
        cli_mod.compile_file(src, out)
        return [out]


class RustToolchain(Toolchain):
    name = "Rust"

    def available(self):
        return shutil.which("rustc") is not None

    def build(self, workdir):
        src = os.path.join(BENCH_DIR, "server.rs")
        out = os.path.join(workdir, "server_rust")
        subprocess.run(["rustc", "-O", src, "-o", out], capture_output=True, check=True)
        return [out]


class GoToolchain(Toolchain):
    name = "Go"

    def available(self):
        return shutil.which("go") is not None

    def build(self, workdir):
        src = os.path.join(BENCH_DIR, "server.go")
        out = os.path.join(workdir, "server_go")
        subprocess.run(["go", "build", "-o", out, src], capture_output=True, check=True)
        return [out]


class BunToolchain(Toolchain):
    name = "Bun"

    def available(self):
        return shutil.which("bun") is not None

    def build(self, workdir):
        # No separate build step -- Bun runs the .js directly, the
        # same "no ahead-of-time build" shape run_benchmarks.py's own
        # BunToolchain already documents.
        return ["bun", "run", os.path.join(BENCH_DIR, "server.js")]


TOOLCHAINS = [FestinaToolchain(), RustToolchain(), GoToolchain(), BunToolchain()]

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


def run_all(duration, connections, threads):
    if shutil.which("wrk") is None:
        print("-- wrk: not installed, cannot run any HTTP benchmark", file=sys.stderr)
        return {}

    results = {}  # (toolchain_name, route) -> dict
    import tempfile

    with tempfile.TemporaryDirectory() as workdir:
        for toolchain in TOOLCHAINS:
            if not toolchain.available():
                print(f"-- {toolchain.name}: not installed, skipping", file=sys.stderr)
                continue
            lang_dir = os.path.join(workdir, toolchain.name)
            os.makedirs(lang_dir, exist_ok=True)
            try:
                argv_prefix = toolchain.build(lang_dir)
            except subprocess.CalledProcessError as e:
                stderr = e.stderr.decode(errors="replace") if isinstance(e.stderr, bytes) else e.stderr
                print(f"-- {toolchain.name}: build failed:\n{stderr}", file=sys.stderr)
                continue

            port = _free_tcp_port()
            proc = subprocess.Popen(argv_prefix + [str(port)], cwd=lang_dir,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                if not _wait_for_port(port):
                    print(f"-- {toolchain.name}: server never opened port {port}, skipping",
                          file=sys.stderr)
                    continue
                for route in ROUTES:
                    url = f"http://127.0.0.1:{port}{route}"
                    try:
                        r = _run_wrk(url, duration, connections, threads)
                    except RuntimeError as e:
                        print(f"-- {toolchain.name}{route}: {e}", file=sys.stderr)
                        continue
                    results[(toolchain.name, route)] = r
                    print(f"-- {toolchain.name}{route}: "
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


def render_table(results, duration, connections, threads):
    langs = [t.name for t in TOOLCHAINS if any(k[0] == t.name for k in results)]
    lines = []
    for route in ROUTES:
        lines.append(f"### `{ROUTE_LABELS[route]}` (`{route}`)")
        lines.append("")
        lines.append(f"| Language | Requests/sec | Avg latency | Transfer/sec |")
        lines.append("|---|---|---|---|")
        for lang in langs:
            r = results.get((lang, route))
            if r is None or r.get("requests_per_sec") is None:
                lines.append(f"| {lang} | not available | | |")
                continue
            lines.append(
                f"| {lang} | {r['requests_per_sec']:,.0f} | "
                f"{r['avg_latency_ms']:.2f} ms | {r['transfer_mb_s']:.2f} MB/s |"
            )
        lines.append("")
    return "\n".join(lines)


def update_doc(table_md, duration, connections, threads):
    with open(BENCHMARK_MD, encoding="utf-8") as f:
        content = f.read()
    start_marker = "<!-- HTTP_BENCHMARK_RESULTS_START -->"
    end_marker = "<!-- HTTP_BENCHMARK_RESULTS_END -->"
    if start_marker not in content or end_marker not in content:
        raise SystemExit(f"benchmark.md is missing {start_marker}/{end_marker} markers -- "
                          "can't know where to splice the HTTP results table in")
    date = time.strftime("%Y-%m-%d")
    replacement = (
        f"{start_marker}\n_Last run: {date} on this machine, `wrk -t{threads} "
        f"-c{connections} -d{duration}` per route -- see benchmark.md's HTTP "
        f"\"Methodology\" for how to reproduce; absolute numbers vary by "
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
                     help="rewrite the HTTP results table in benchmark.md instead of just printing it")
    ap.add_argument("--duration", default="5s", help="wrk -d value per route (default: 5s)")
    ap.add_argument("--connections", type=int, default=50, help="wrk -c value (default: 50)")
    ap.add_argument("--threads", type=int, default=4, help="wrk -t value (default: 4)")
    args = ap.parse_args()

    results = run_all(args.duration, args.connections, args.threads)
    table_md = render_table(results, args.duration, args.connections, args.threads)
    if args.update_doc:
        update_doc(table_md, args.duration, args.connections, args.threads)
    else:
        print(table_md)


if __name__ == "__main__":
    main()
