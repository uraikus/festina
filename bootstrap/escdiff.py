"""Differential test: bootstrap/escape.f against festina/escape_analysis.py.

The fifth harness, and the last front-end piece the codegen port needs.
Same shape as difftest.py (lexer), astdiff.py (parser), semdiff.py
(analyzer) and irdiff.py (codegen), over the record sequence
escdump.py defines.

    python bootstrap/escdiff.py             # whole repo corpus
    python bootstrap/escdiff.py a.f b.f     # just these

**Why this stage gets a harness of its own rather than being tested
through the IR it feeds.** Escape analysis decides, for every container
and struct local in a program, whether its storage lives in the frame
or behind a heap refcount header -- so a disagreement shows up in the
IR as a completely different allocation strategy, many lines away from
the name that caused it. Diffing the answer directly says which name,
in which body, and the record sequence is small enough to read.

**The ORDER is compared, not just the contents.** claude.md #74 stage 2
exempts a call argument only once the callee's own body has been
walked, so an implementation that analyzes the same bodies in a
different order produces the same multiset of records while disagreeing
about every exemption. Each record carries its own index for exactly
that reason.

A file whose Festina dump contains an `UNPORTED|...` record is
classified "unported", never "match" and never "differ" -- the rule the
other four harnesses already use, so coverage only moves when something
is really implemented.

tests/test_bootstrap_escape.py drives these functions from pytest.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bootstrap import escdump                                  # noqa: E402
from bootstrap.difftest import REPO_ROOT, corpus, run_text     # noqa: E402

ESCAPE_SOURCE = os.path.join(REPO_ROOT, "bootstrap", "escdumpf.f")


def build_escape(out_path):
    """Compile bootstrap/escdumpf.f, returning the binary's path."""
    result = run_text(
        [sys.executable, "-m", "festina.cli", "compile", ESCAPE_SOURCE,
         "-o", out_path],
        timeout=1800, cwd=REPO_ROOT)
    if result.returncode != 0:
        raise RuntimeError(
            f"compiling {ESCAPE_SOURCE} failed:\n{result.stdout}\n{result.stderr}")
    return out_path


def festina_dump(binary, path):
    result = run_text([binary, os.path.relpath(path, REPO_ROOT)],
                      timeout=300, cwd=REPO_ROOT)
    if result.returncode != 0:
        raise RuntimeError(
            f"{binary} {path} exited {result.returncode}: {result.stderr}")
    lines = result.stdout.split("\n") if result.stdout else []
    return [line for line in lines if line]


def compare(binary, path):
    """(status, detail) for one file.

    status is "match", "rejected", "differ" or "unported". "rejected"
    is separated from "match" for the reason irdiff.compare documents at
    length: the `cases/*.f` files exist to be lexed rather than to be
    valid programs, both sides answer a bare `SEMERR|line|col` for them,
    and counting that as agreement about escape analysis would flatter a
    port that had never analyzed anything.
    """
    got = festina_dump(binary, path)

    unported = sorted({
        line.split("|", 1)[1] for line in got if line.startswith("UNPORTED|")
    })
    if unported:
        return "unported", ", ".join(unported)

    want = escdump.dump_file(os.path.relpath(path, REPO_ROOT))
    if got == want:
        if len(want) == 1 and want[0].startswith("SEMERR"):
            return "rejected", None
        return "match", None

    for i in range(max(len(got), len(want))):
        a = want[i] if i < len(want) else "<missing>"
        b = got[i] if i < len(got) else "<missing>"
        if a != b:
            return "differ", (i, a, b)
    return "differ", (0, f"{len(want)} records", f"{len(got)} records")


def records_reproduced(binary, paths=None):
    """Records the port reproduces exactly, and the total on offer.

    A file counts only when its whole sequence matches, and a file both
    sides merely reject contributes nothing -- that is semantic
    analysis's claim, not this stage's.
    """
    paths = paths or corpus()
    reproduced = total = 0
    for path in paths:
        want = escdump.dump_file(os.path.relpath(path, REPO_ROOT))
        if len(want) == 1 and want[0].startswith("SEMERR"):
            continue
        total += len(want)
        status, _ = compare(binary, path)
        if status == "match":
            reproduced += len(want)
    return reproduced, total


def main(argv):
    import tempfile
    from collections import Counter
    paths = argv[1:] or corpus()
    with tempfile.TemporaryDirectory() as tmp:
        binary = build_escape(os.path.join(tmp, "fesc"))
        matched = differed = rejected = 0
        blockers = []
        for path in paths:
            status, detail = compare(binary, path)
            rel = os.path.relpath(path, REPO_ROOT)
            if status == "match":
                matched += 1
            elif status == "rejected":
                rejected += 1
            elif status == "unported":
                blockers.append(frozenset(
                    r.strip() for r in detail.split(",") if r.strip()))
            else:
                differed += 1
                idx, want, got = detail
                print(f"DIFF  {rel}  at record {idx}")
                print(f"        python:  {want}")
                print(f"        festina: {got}")
        reproduced, total = records_reproduced(binary, paths)
    print(f"\n{matched} match, {differed} differ, {len(blockers)} unported, "
          f"{rejected} rejected by both (of {len(paths)} files)")
    share = (reproduced / total) if total else 0.0
    print(f"records reproduced: {reproduced:,} of {total:,} ({share:.1%})")
    if blockers:
        blocks = Counter()
        only = Counter()
        for rs in blockers:
            for r in rs:
                blocks[r] += 1
            if len(rs) == 1:
                only[next(iter(rs))] += 1
        print("unported constructs -- 'blocks' is files mentioning it, "
              "'only' is files it alone holds back:")
        print(f"    {'blocks':>6} {'only':>5}  construct")
        for reason, count in blocks.most_common():
            print(f"    {count:>6} {only[reason]:>5}  {reason}")
    return 1 if differed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
