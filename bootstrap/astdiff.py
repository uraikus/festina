"""Differential test: bootstrap/parser.f against festina/parser.py.

claude.md #273, the same shape as bootstrap/difftest.py does for the
lexer: both parsers emit one canonical AST dump (see astdump.py) and this
diffs them over every .f file in the repository.

The parser is a PARTIAL port, and this reports that honestly rather than
counting it as agreement. A file whose Festina dump contains an
`(UNPORTED ...)` node is classified "unported", not "match" and not
"differ" -- so the coverage number moves only when a construct is
actually implemented.

    python bootstrap/astdiff.py            # whole repo corpus
    python bootstrap/astdiff.py a.f b.f    # just these

tests/test_bootstrap_parser.py drives the same functions from pytest.
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bootstrap import astdump                 # noqa: E402
from bootstrap.difftest import REPO_ROOT, corpus   # noqa: E402
from festina import parser as py_parser       # noqa: E402
from festina.errors import CompileError       # noqa: E402

PARSER_SOURCE = os.path.join(REPO_ROOT, "bootstrap", "astdumpf.f")


def build_parser(out_path):
    """Compile bootstrap/astdumpf.f, returning the binary's path."""
    result = subprocess.run(
        [sys.executable, "-m", "festina.cli", "compile", PARSER_SOURCE, "-o", out_path],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=900,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"compiling {PARSER_SOURCE} failed:\n{result.stdout}\n{result.stderr}")
    return out_path


def python_dump(source):
    """The Python parser's AST, in the canonical form. A rejected source
    reports where it was rejected, the same single-line shape the Festina
    side uses -- the message text is not compared, only the position."""
    try:
        program = py_parser.parse(source, filename="main.f")
    except CompileError as err:
        return [f"PARSEERR|{err.line}|{err.column}"]
    return astdump.dump_program(program)


def festina_dump(binary, path):
    result = subprocess.run([binary, path], capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        raise RuntimeError(f"{binary} {path} exited {result.returncode}: {result.stderr}")
    body = result.stdout
    return body.split("\n") if body else []


def compare(binary, path):
    """(status, detail) for one file.

    status is "match", "differ", or "unported".
    """
    with open(path, encoding="utf-8") as f:
        source = f.read()
    got = festina_dump(binary, path)
    while got and got[-1] == "":
        got.pop()

    unported = sorted({
        line.split('(UNPORTED :what="', 1)[1].split('"', 1)[0]
        for line in got if '(UNPORTED :what="' in line
    })
    if unported:
        return "unported", ", ".join(unported)

    want = python_dump(source)
    # A lexer rejection reaches the Festina side as its own marker; the
    # Python parser reports it as a CompileError like any other, so the
    # two agree on "rejected here" without agreeing on which stage said so.
    if got and got[0].startswith("LEXERR|"):
        got = [got[0].replace("LEXERR|", "PARSEERR|", 1)]

    if got == want:
        return "match", None
    for i in range(max(len(got), len(want))):
        a = want[i] if i < len(want) else "<missing>"
        b = got[i] if i < len(got) else "<missing>"
        if a != b:
            return "differ", (i, a, b)
    return "differ", (0, f"{len(want)} nodes", f"{len(got)} nodes")


def main(argv):
    import tempfile
    from collections import Counter
    paths = argv[1:] or corpus()
    with tempfile.TemporaryDirectory() as tmp:
        binary = build_parser(os.path.join(tmp, "fparse"))
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
                print(f"DIFF  {rel}  at node {idx}")
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
