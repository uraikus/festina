"""Differential test: bootstrap/codegen.f against festina/codegen.py.

The fourth and last harness, the same shape as difftest.py (lexer),
astdiff.py (parser) and semdiff.py (analyzer), over the IR irdump.py
defines.

    python bootstrap/irdiff.py             # whole repo corpus
    python bootstrap/irdiff.py a.f b.f     # just these

**Paths are made repo-relative before either side sees them.** The IR
carries its own source path in a comment on line 2, so handing the two
implementations an absolute path would bake this machine's checkout
location into the expected output and compare it against whatever the
Festina side resolved. Relative paths keep the dump a property of the
program rather than of the filesystem it happens to live on -- verified:
with absolute paths every file's IR contains `/home/user/festina`, with
relative paths none does.

A file whose Festina dump contains an `UNPORTED|...` record is
classified "unported", not "match" and not "differ" -- the rule
astdiff.py and semdiff.py already use, so coverage only moves when
something is really implemented.

**Reading the coverage number here needs more care than for the other
three.** 383 of every file's IR lines are the identical runtime
declaration block, which is 94% of the smallest file in the corpus and
23% of all its IR. A port that emitted nothing but that block and the
module header would already be within a couple of dozen lines of
matching `benchmarks/hello.f`, so "N files match" says much less early
on here than the same number did for the lexer. `line_budget()` reports
the file-specific remainder for exactly this reason, and
tests/test_bootstrap_codegen.py asserts against it rather than against
a file count alone.

tests/test_bootstrap_codegen.py drives these functions from pytest.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bootstrap import irdump                                 # noqa: E402
from bootstrap.difftest import REPO_ROOT, corpus, run_text   # noqa: E402

CODEGEN_SOURCE = os.path.join(REPO_ROOT, "bootstrap", "irdumpf.f")

# The runtime `declare` block every module emits verbatim. Measured, not
# assumed: the longest common prefix of all 83 compilable corpus files
# once each one's own source-path comment is set aside.
SHARED_PREAMBLE_LINES = 383


def relative(path):
    """The repo-relative spelling both sides are handed. See the module
    docstring for why this is not cosmetic."""
    return os.path.relpath(os.path.abspath(path), REPO_ROOT)


def build_codegen(out_path):
    """Compile bootstrap/irdumpf.f, returning the binary's path."""
    result = run_text(
        [sys.executable, "-m", "festina.cli", "compile", CODEGEN_SOURCE,
         "-o", out_path],
        timeout=1800, cwd=REPO_ROOT)
    if result.returncode != 0:
        raise RuntimeError(
            f"compiling {CODEGEN_SOURCE} failed:\n{result.stdout}\n{result.stderr}")
    return out_path


def festina_dump(binary, path):
    result = run_text([binary, relative(path)], timeout=300, cwd=REPO_ROOT)
    if result.returncode != 0:
        raise RuntimeError(
            f"{binary} {path} exited {result.returncode}: {result.stderr}")
    body = result.stdout
    lines = body.split("\n") if body else []
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def compare(binary, path):
    """(status, detail) for one file.

    status is "match", "rejected", "differ" or "unported".

    **"rejected" is separated from "match" deliberately, and finding out
    why is the most useful thing this harness has done so far.** Eleven
    corpus files are `cases/*.f`, which exist to be lexed rather than to
    be valid programs, so both implementations answer a bare
    `SEMERR|line|col` for them. Counted as matches they made the first
    run report "12 match" when the port could emit exactly one real
    module -- 22 file-specific lines out of 153,320, or 0.014%. The
    eleven prove only that both sides reject the same programs, which is
    the semantic harness's claim (#286) and not this one's. A number
    that flatters by an order of magnitude is worse than no number.
    """
    got = festina_dump(binary, path)

    unported = sorted({
        line.split("|", 1)[1] for line in got if line.startswith("UNPORTED|")
    })
    if unported:
        return "unported", ", ".join(unported)

    want = irdump.dump_file(relative(path))
    if got == want:
        if len(want) == 1 and want[0].startswith("SEMERR"):
            return "rejected", None
        return "match", None

    for i in range(max(len(got), len(want))):
        a = want[i] if i < len(want) else "<missing>"
        b = got[i] if i < len(got) else "<missing>"
        if a != b:
            return "differ", (i, a, b)
    return "differ", (0, f"{len(want)} lines", f"{len(got)} lines")


def line_budget(paths=None):
    """(total_lines, file_specific_lines) the port has to reproduce.

    The second number is the honest one: it subtracts the shared
    runtime-declaration preamble, which any port emits correctly the
    moment it can print a constant, and skips the files that are only
    ever rejected.
    """
    paths = paths or corpus()
    total = specific = 0
    for path in paths:
        dump = irdump.dump_file(relative(path))
        if len(dump) == 1 and dump[0].startswith("SEMERR"):
            continue
        total += len(dump)
        specific += max(0, len(dump) - SHARED_PREAMBLE_LINES)
    return total, specific


def lines_reproduced(binary, paths=None):
    """File-specific IR lines the port actually emits correctly.

    The coverage number that means something. A file counts only when
    it matches in full, and only its lines beyond the shared preamble
    count -- see this module's docstring, and `compare`'s note on why a
    file count alone reads an order of magnitude too high here.
    """
    paths = paths or corpus()
    reproduced = 0
    for path in paths:
        status, _ = compare(binary, path)
        if status != "match":
            continue
        reproduced += max(0, len(irdump.dump_file(relative(path)))
                          - SHARED_PREAMBLE_LINES)
    return reproduced


def main(argv):
    import tempfile
    from collections import Counter
    paths = argv[1:] or corpus()
    with tempfile.TemporaryDirectory() as tmp:
        binary = build_codegen(os.path.join(tmp, "fir"))
        matched = differed = rejected = 0
        reproduced = 0
        blockers = []
        for path in paths:
            status, detail = compare(binary, path)
            rel = os.path.relpath(path, REPO_ROOT)
            if status == "match":
                matched += 1
                reproduced += max(
                    0, len(irdump.dump_file(relative(path))) - SHARED_PREAMBLE_LINES)
            elif status == "rejected":
                rejected += 1
            elif status == "unported":
                blockers.append(frozenset(
                    r.strip() for r in detail.split(",") if r.strip()))
            else:
                differed += 1
                idx, want, got = detail
                print(f"DIFF  {rel}  at line {idx + 1}")
                print(f"        python:  {want}")
                print(f"        festina: {got}")
        _, budget = line_budget(paths)
    unported_total = len(blockers)
    print(f"\n{matched} match, {differed} differ, {unported_total} unported, "
          f"{rejected} rejected by both (of {len(paths)} files)")
    share = (reproduced / budget) if budget else 0.0
    print(f"file-specific IR reproduced: {reproduced:,} of {budget:,} lines "
          f"({share:.3%})")
    if blockers:
        # Two columns, because only the second one predicts anything.
        # "blocks" counts every file a construct appears in; "only"
        # counts the files where it is the last thing in the way, and so
        # the number that would actually become matches. Closing
        # StructDecl, which blocked 22 files, unlocked zero of them --
        # every one had something else behind it.
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
