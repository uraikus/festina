"""Differential test: bootstrap/semantic.f against festina/semantic.py.

The same shape as difftest.py (lexer) and astdiff.py (parser), over the
dump semdump.py defines. Both implementations emit the canonical form
described there and this diffs them across every .f file in the repo.

    python bootstrap/semdiff.py            # whole repo corpus
    python bootstrap/semdiff.py a.f b.f    # just these

A file whose Festina dump contains an `UNPORTED|...` record is
classified "unported", not "match" and not "differ" -- the same rule
astdiff.py uses, and for the same reason: coverage must only move when
something is really implemented, and a construct that silently produces
the wrong answer has to show up as a difference rather than as progress.

tests/test_bootstrap_semantic.py drives these functions from pytest.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bootstrap import semdump                                # noqa: E402
from bootstrap.difftest import REPO_ROOT, corpus, run_text   # noqa: E402

SEMANTIC_SOURCE = os.path.join(REPO_ROOT, "bootstrap", "semdumpf.f")


def build_semantic(out_path):
    """Compile bootstrap/semdumpf.f, returning the binary's path."""
    result = run_text(
        [sys.executable, "-m", "festina.cli", "compile", SEMANTIC_SOURCE,
         "-o", out_path],
        timeout=1200, cwd=REPO_ROOT)
    if result.returncode != 0:
        raise RuntimeError(
            f"compiling {SEMANTIC_SOURCE} failed:\n{result.stdout}\n{result.stderr}")
    return out_path


def festina_dump(binary, path):
    result = run_text([binary, path], timeout=180)
    if result.returncode != 0:
        raise RuntimeError(
            f"{binary} {path} exited {result.returncode}: {result.stderr}")
    body = result.stdout
    return [ln for ln in body.split("\n") if ln] if body else []


def compare(binary, path):
    """(status, detail) for one file.

    status is "match", "differ", or "unported".
    """
    got = festina_dump(binary, path)

    unported = sorted({
        line.split("|", 1)[1] for line in got if line.startswith("UNPORTED|")
    })
    if unported:
        return "unported", ", ".join(unported)

    want = semdump.dump_file(path)
    if got == want:
        return "match", None

    for i in range(max(len(got), len(want))):
        a = want[i] if i < len(want) else "<missing>"
        b = got[i] if i < len(got) else "<missing>"
        if a != b:
            return "differ", (i, a, b)
    return "differ", (0, f"{len(want)} records", f"{len(got)} records")


def main(argv):
    import tempfile
    from collections import Counter
    paths = argv[1:] or corpus()
    with tempfile.TemporaryDirectory() as tmp:
        binary = build_semantic(os.path.join(tmp, "fsem"))
        matched = differed = 0
        reasons = Counter()
        for path in paths:
            status, detail = compare(binary, path)
            rel = os.path.relpath(path, REPO_ROOT)
            if status == "match":
                matched += 1
            elif status == "unported":
                reasons[detail] += 1
            else:
                differed += 1
                idx, want, got = detail
                print(f"DIFF  {rel}  at record {idx}")
                print(f"        python:  {want}")
                print(f"        festina: {got}")
    unported_total = sum(reasons.values())
    print(f"\n{matched} match, {differed} differ, {unported_total} unported "
          f"(of {len(paths)} files)")
    if reasons:
        print("unported constructs, by how many files each blocks:")
        for reason, count in reasons.most_common():
            print(f"    {count:3}  {reason}")
    return 1 if differed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
