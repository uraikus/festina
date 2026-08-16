#!/usr/bin/env python3
"""Runs the Festina/Rust/Go/Bun benchmarks in benchmarks/ and prints (or
writes, with --update-doc) a Markdown results table for benchmark.md.

Deliberately cheap to run "frequently" (the whole suite is a handful of
seconds, not minutes): five small equivalent-logic programs per
language (hello/fib/loop_sum/array_sum/string_concat -- see each file's
own comments for what each measures and why), each language's own
native/JIT toolchain doing the actual compiling, timed with repeated
runs and the *minimum* wall time kept (the standard way to reduce OS
scheduling noise without needing a dedicated benchmarking harness like
hyperfine, which isn't a project dependency).

Usage:
    python3 benchmarks/run_benchmarks.py                 # print results
    python3 benchmarks/run_benchmarks.py --update-doc     # also rewrite
                                                            # the results
                                                            # table in
                                                            # benchmark.md

Requires whichever of festina/rustc/go/bun are installed -- a language
missing from PATH is skipped with a note, not a hard failure (the same
"a genuinely missing dependency should degrade gracefully" spirit as
claude.md #59, applied to benchmarking rather than compiling).
"""
import argparse
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BENCH_DIR = os.path.dirname(os.path.abspath(__file__))
BENCHMARK_MD = os.path.join(REPO_ROOT, "benchmark.md")

BENCHMARKS = ["hello", "fib", "loop_sum", "array_sum", "string_concat"]
RUNS_PER_BENCHMARK = 7  # minimum of this many timed runs, after 1 untimed warmup


def _time_runs(cmd, cwd=None, runs=RUNS_PER_BENCHMARK):
    """Minimum wall-clock time (seconds) of `runs` back-to-back
    executions, after one untimed warmup run (page cache, dynamic linker
    resolution, ...) so the very first run's one-time cost doesn't skew
    a result that's supposed to represent steady-state process startup
    + execution cost."""
    subprocess.run(cmd, cwd=cwd, capture_output=True, check=True)
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


class Toolchain:
    name = None

    def available(self):
        raise NotImplementedError

    def build(self, name, workdir):
        """Compile benchmarks/<name>.<ext> into workdir, returning
        (executable_path, build_seconds) -- or (None, None) for a
        language with no separate build step (interpreted/JIT)."""
        raise NotImplementedError

    def run_cmd(self, name, built_path):
        """The argv to actually execute the benchmark once built."""
        raise NotImplementedError


class FestinaToolchain(Toolchain):
    name = "Festina"

    def available(self):
        return True  # this repo's own compiler -- always present here

    def build(self, name, workdir):
        sys.path.insert(0, REPO_ROOT)
        from festina import cli as cli_mod  # local import: keeps this
        # script runnable even when festina/ can't be imported for some
        # other language-only invocation down the line

        src = os.path.join(BENCH_DIR, f"{name}.f")
        out = os.path.join(workdir, name)
        t0 = time.perf_counter()
        cli_mod.compile_file(src, out)
        return out, time.perf_counter() - t0

    def run_cmd(self, name, built_path):
        return [built_path]


class RustToolchain(Toolchain):
    name = "Rust"

    def available(self):
        return shutil.which("rustc") is not None

    def build(self, name, workdir):
        src = os.path.join(BENCH_DIR, f"{name}.rs")
        out = os.path.join(workdir, name)
        t0 = time.perf_counter()
        subprocess.run(["rustc", "-O", src, "-o", out], capture_output=True, check=True)
        return out, time.perf_counter() - t0

    def run_cmd(self, name, built_path):
        return [built_path]


class GoToolchain(Toolchain):
    name = "Go"

    def available(self):
        return shutil.which("go") is not None

    def build(self, name, workdir):
        src = os.path.join(BENCH_DIR, f"{name}.go")
        out = os.path.join(workdir, name)
        t0 = time.perf_counter()
        subprocess.run(["go", "build", "-o", out, src], capture_output=True, check=True)
        return out, time.perf_counter() - t0

    def run_cmd(self, name, built_path):
        return [built_path]


class BunToolchain(Toolchain):
    name = "Bun"

    def available(self):
        return shutil.which("bun") is not None

    def build(self, name, workdir):
        # Bun runs .js directly -- no separate ahead-of-time build step
        # (its JIT compiles at run time), so there's no binary size or
        # build time to report, same as any other JIT/interpreted
        # runtime would have in this comparison.
        return os.path.join(BENCH_DIR, f"{name}.js"), None

    def run_cmd(self, name, built_path):
        return ["bun", "run", built_path]


TOOLCHAINS = [FestinaToolchain(), RustToolchain(), GoToolchain(), BunToolchain()]


def run_all():
    results = {}  # (toolchain_name, benchmark_name) -> dict
    with tempfile.TemporaryDirectory() as workdir:
        for toolchain in TOOLCHAINS:
            if not toolchain.available():
                print(f"-- {toolchain.name}: not installed, skipping", file=sys.stderr)
                continue
            lang_dir = os.path.join(workdir, toolchain.name)
            os.makedirs(lang_dir, exist_ok=True)
            # One untimed throwaway build before any timed one, for the
            # same reason each program gets an untimed warmup RUN: the
            # first build a toolchain does in a session pays for a cold
            # page cache and, for rustc/go, for their own one-time
            # startup work. Without this the first benchmark in the list
            # absorbed all of it and reported a build time several times
            # everyone else's -- measured at 5.1 s for Rust's `hello`
            # against 0.1 s for the very next program it built, which
            # says nothing about `hello`.
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
                    print(f"-- {toolchain.name}/{bench}: build failed:\n{e.stderr.decode(errors='replace')}",
                          file=sys.stderr)
                    continue
                cmd = toolchain.run_cmd(bench, built_path)
                run_seconds = _time_runs(cmd)
                size = None
                if built_path and os.path.isfile(built_path) and os.access(built_path, os.X_OK):
                    size = os.path.getsize(built_path)
                results[(toolchain.name, bench)] = {
                    "build_seconds": build_seconds,
                    "run_seconds": run_seconds,
                    "size_bytes": size,
                }
                print(f"-- {toolchain.name}/{bench}: "
                      f"run={_fmt_ms(run_seconds)} "
                      f"build={'n/a' if build_seconds is None else _fmt_ms(build_seconds)} "
                      f"size={_fmt_size(size)}", file=sys.stderr)
    return results


def render_table(results):
    langs = [t.name for t in TOOLCHAINS if any(k[0] == t.name for k in results)]
    lines = []
    for bench in BENCHMARKS:
        lines.append(f"### `{bench}`")
        lines.append("")
        lines.append("| Language | Run time (min of {} runs) | Build time | Binary size |".format(RUNS_PER_BENCHMARK))
        lines.append("|---|---|---|---|")
        for lang in langs:
            r = results.get((lang, bench))
            if r is None:
                lines.append(f"| {lang} | not installed | | |")
                continue
            build = "n/a (JIT, no separate build step)" if r["build_seconds"] is None else _fmt_ms(r["build_seconds"])
            lines.append(f"| {lang} | {_fmt_ms(r['run_seconds'])} | {build} | {_fmt_size(r['size_bytes'])} |")
        lines.append("")
    return "\n".join(lines)


def update_doc(table_md):
    with open(BENCHMARK_MD, encoding="utf-8") as f:
        content = f.read()
    start_marker = "<!-- BENCHMARK_RESULTS_START -->"
    end_marker = "<!-- BENCHMARK_RESULTS_END -->"
    if start_marker not in content or end_marker not in content:
        raise SystemExit(f"benchmark.md is missing {start_marker}/{end_marker} markers -- "
                          "can't know where to splice the results table in")
    date = time.strftime("%Y-%m-%d")
    replacement = f"{start_marker}\n_Last run: {date} on this machine -- see benchmark.md's " \
                  f"\"Methodology\" section for how to reproduce; absolute numbers vary by " \
                  f"hardware, relative ordering is the point._\n\n{table_md}\n{end_marker}"
    # A plain string replacement (not a lambda) makes re.sub interpret
    # backslash-digit sequences in `replacement` as backreferences --
    # harmless today (the table has none) but a landmine for whatever
    # gets pasted into a results table later, so sidestep it entirely.
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
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--update-doc", action="store_true",
                     help="rewrite the results table in benchmark.md instead of just printing it")
    args = ap.parse_args()

    results = run_all()
    table_md = render_table(results)
    if args.update_doc:
        update_doc(table_md)
    else:
        print(table_md)


if __name__ == "__main__":
    main()
