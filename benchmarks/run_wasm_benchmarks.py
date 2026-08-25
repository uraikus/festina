#!/usr/bin/env python3
"""Runs the Festina/C/Go benchmarks in benchmarks/, each cross-compiled
to a wasm32-wasi binary, and prints (or writes, with --update-doc) a
Markdown results table for wasm.md.

The wasm counterpart to run_benchmarks.py -- see that script's own
docstring for the shared methodology (min-of-N timing, one untimed
warmup build/run). The difference here is entirely in HOW each
language's binary is built and run:

  - build: `clang --target=wasm32-wasi` for both Festina (via
    `festina compile --target=wasm32-wasi`) and C directly; `go build`
    with GOOS=wasip1 GOARCH=wasm for Go (stable since Go 1.21 -- no
    GOOS=js/wasm here, which targets the browser's own incompatible ABI,
    not WASI).
  - run: every .wasm binary, regardless of source language, is executed
    the exact same way -- through this project's own
    runtime/wasm/run_wasi.mjs, Node's built-in WASI host. This is
    deliberate: it means the run-time numbers below measure the
    compiled code and Node's WASI syscall overhead, identically for all
    three languages, not three different WASI runtimes' own differing
    overhead.

Rust is not included here (unlike run_benchmarks.py's native table):
`rustc --target=wasm32-wasi` was removed from rustc itself (superseded
by wasm32-wasip1, which needs the separate `wasm32-wasip1` target
component, `rustup target add wasm32-wasip1` -- not installed in the
environment this was authored in, and not a dependency this project
otherwise has any reason to take on). C stands in as the systems-
language wasm comparison instead.

Usage:
    python3 benchmarks/run_wasm_benchmarks.py                # print results
    python3 benchmarks/run_wasm_benchmarks.py --update-doc    # also rewrite
                                                                 # the results
                                                                 # table in
                                                                 # wasm.md

Requires a wasm32-wasi-capable clang (wasi-libc + clang's wasm32
compiler-rt, see wasm.md's "Setup" section), Go 1.21+, and Node.js (for
the WASI host) -- a language whose wasm toolchain isn't available is
skipped with a note, not a hard failure, same spirit as
run_benchmarks.py.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BENCH_DIR = os.path.dirname(os.path.abspath(__file__))
WASM_MD = os.path.join(REPO_ROOT, "wasm.md")
RUN_WASI_MJS = os.path.join(REPO_ROOT, "runtime", "wasm", "run_wasi.mjs")

BENCHMARKS = ["hello", "fib", "loop_sum", "array_sum", "string_concat"]
RUNS_PER_BENCHMARK = 7  # same as run_benchmarks.py, same rationale


def _time_runs(cmd, cwd, runs=RUNS_PER_BENCHMARK):
    subprocess.run(cmd, cwd=cwd, capture_output=True, check=True)  # untimed warmup
    samples = []
    for _ in range(runs):
        t0 = time.perf_counter()
        subprocess.run(cmd, cwd=cwd, capture_output=True, check=True)
        samples.append(time.perf_counter() - t0)
    return min(samples)


def _fmt_ms(seconds):
    return f"{seconds * 1000:.1f} ms"


def _fmt_size(num_bytes):
    if num_bytes is None:
        return "n/a"
    if num_bytes >= 1024 * 1024:
        return f"{num_bytes / (1024 * 1024):.2f} MB"
    return f"{num_bytes / 1024:.1f} KB"


def _run_via_wasi(node, wasm_path, cwd):
    return [node, "--no-warnings", RUN_WASI_MJS, wasm_path, cwd]


class WasmToolchain:
    name = None

    def available(self):
        raise NotImplementedError

    def build(self, name, workdir):
        """Compile benchmarks/<name>.<ext> to a wasm32-wasi .wasm in
        workdir, returning (wasm_path, build_seconds)."""
        raise NotImplementedError


class FestinaWasmToolchain(WasmToolchain):
    name = "Festina"

    def __init__(self, clang):
        self.clang = clang

    def available(self):
        sys.path.insert(0, REPO_ROOT)
        from festina import cli as cli_mod
        return cli_mod._wasm_toolchain_ok(self.clang)

    def build(self, name, workdir):
        sys.path.insert(0, REPO_ROOT)
        from festina import cli as cli_mod

        src = os.path.join(BENCH_DIR, f"{name}.f")
        out = os.path.join(workdir, f"{name}.wasm")
        t0 = time.perf_counter()
        cli_mod.compile_file(src, out, cc=self.clang, target="wasm32-wasi")
        return out, time.perf_counter() - t0


class CWasmToolchain(WasmToolchain):
    name = "C"

    def __init__(self, clang):
        self.clang = clang

    def available(self):
        sys.path.insert(0, REPO_ROOT)
        from festina import cli as cli_mod
        return cli_mod._wasm_toolchain_ok(self.clang)

    def build(self, name, workdir):
        src = os.path.join(BENCH_DIR, f"{name}.c")
        out = os.path.join(workdir, f"{name}.wasm")
        t0 = time.perf_counter()
        subprocess.run([self.clang, "--target=wasm32-wasi", "-O2", src, "-o", out],
                        capture_output=True, check=True)
        return out, time.perf_counter() - t0


class GoWasmToolchain(WasmToolchain):
    name = "Go"

    def available(self):
        return shutil.which("go") is not None

    def build(self, name, workdir):
        src = os.path.join(BENCH_DIR, f"{name}.go")
        out = os.path.join(workdir, f"{name}.wasm")
        t0 = time.perf_counter()
        env = dict(os.environ, GOOS="wasip1", GOARCH="wasm")
        subprocess.run(["go", "build", "-o", out, src], capture_output=True, check=True, env=env)
        return out, time.perf_counter() - t0


def run_all():
    clang = shutil.which("clang")
    node = shutil.which("node")
    toolchains = [FestinaWasmToolchain(clang), CWasmToolchain(clang), GoWasmToolchain()]

    if node is None:
        print("-- Node.js not found on PATH -- can't run any wasm32-wasi binary "
              "(needed for its built-in WASI host). Skipping everything.", file=sys.stderr)
        return {}

    results = {}  # (toolchain_name, benchmark_name) -> dict
    with tempfile.TemporaryDirectory() as workdir:
        for toolchain in toolchains:
            if not toolchain.available():
                print(f"-- {toolchain.name}: no wasm32-wasi toolchain available, skipping", file=sys.stderr)
                continue
            lang_dir = os.path.join(workdir, toolchain.name)
            os.makedirs(lang_dir, exist_ok=True)
            warmup_dir = os.path.join(lang_dir, "_warmup")
            os.makedirs(warmup_dir, exist_ok=True)
            try:
                toolchain.build(BENCHMARKS[0], warmup_dir)
            except subprocess.CalledProcessError:
                pass  # a real failure is reported by the timed build below

            for bench in BENCHMARKS:
                try:
                    built_path, build_seconds = toolchain.build(bench, lang_dir)
                except subprocess.CalledProcessError as e:
                    stderr = e.stderr.decode(errors="replace") if e.stderr else ""
                    print(f"-- {toolchain.name}/{bench}: build failed:\n{stderr}", file=sys.stderr)
                    continue
                run_cwd = os.path.join(lang_dir, f"_run_{bench}")
                os.makedirs(run_cwd, exist_ok=True)
                cmd = _run_via_wasi(node, built_path, run_cwd)
                try:
                    run_seconds = _time_runs(cmd, cwd=run_cwd)
                except subprocess.CalledProcessError as e:
                    stderr = e.stderr.decode(errors="replace") if e.stderr else ""
                    print(f"-- {toolchain.name}/{bench}: run failed:\n{stderr}", file=sys.stderr)
                    continue
                size = os.path.getsize(built_path) if os.path.isfile(built_path) else None
                results[(toolchain.name, bench)] = {
                    "build_seconds": build_seconds,
                    "run_seconds": run_seconds,
                    "size_bytes": size,
                }
                print(f"-- {toolchain.name}/{bench}: "
                      f"run={_fmt_ms(run_seconds)} build={_fmt_ms(build_seconds)} "
                      f"size={_fmt_size(size)}", file=sys.stderr)
    return results


def render_table(results):
    langs = ["Festina", "C", "Go"]
    langs = [l for l in langs if any(k[0] == l for k in results)]
    lines = []
    for bench in BENCHMARKS:
        lines.append(f"### `{bench}` (wasm32-wasi, run via Node's WASI host)")
        lines.append("")
        lines.append("| Language | Run time (min of {} runs) | Build time | .wasm size |".format(RUNS_PER_BENCHMARK))
        lines.append("|---|---|---|---|")
        for lang in langs:
            r = results.get((lang, bench))
            if r is None:
                lines.append(f"| {lang} | not available | | |")
                continue
            lines.append(f"| {lang} | {_fmt_ms(r['run_seconds'])} | {_fmt_ms(r['build_seconds'])} | "
                          f"{_fmt_size(r['size_bytes'])} |")
        lines.append("")
    return "\n".join(lines)


def update_doc(table_md):
    with open(WASM_MD, encoding="utf-8") as f:
        content = f.read()
    start_marker = "<!-- WASM_BENCHMARK_RESULTS_START -->"
    end_marker = "<!-- WASM_BENCHMARK_RESULTS_END -->"
    if start_marker not in content or end_marker not in content:
        raise SystemExit(f"wasm.md is missing {start_marker}/{end_marker} markers -- "
                          "can't know where to splice the results table in")
    date = time.strftime("%Y-%m-%d")
    replacement = f"{start_marker}\n_Last run: {date} on this machine -- see wasm.md's " \
                  f"\"Benchmark methodology\" section for how to reproduce; absolute " \
                  f"numbers vary by hardware, relative ordering is the point._\n\n{table_md}\n{end_marker}"
    new_content = re.sub(
        re.escape(start_marker) + r".*?" + re.escape(end_marker),
        lambda _match: replacement,
        content,
        flags=re.DOTALL,
    )
    with open(WASM_MD, "w", encoding="utf-8") as f:
        f.write(new_content)
    print(f"updated {WASM_MD}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--update-doc", action="store_true",
                     help="rewrite the results table in wasm.md instead of just printing it")
    args = ap.parse_args()

    results = run_all()
    table_md = render_table(results)
    if args.update_doc:
        update_doc(table_md)
    else:
        print(table_md)


if __name__ == "__main__":
    main()
