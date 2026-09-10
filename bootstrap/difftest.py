"""Differential test: bootstrap/lexer.f against festina/lexer.py.

claude.md #271. The Festina lexer is a PORT, so the only claim worth
making about it is that it produces the same token stream as the Python
one -- not "it looks right", but "it agrees, token for token, over every
.f file in this repository."

Both sides emit the same canonical form, one token per line:

    line:col|KIND|value
    line:col|REGEX|pattern|flags
    line:col|LEXERR|char

and this script diffs them. Run directly for a report:

    python bootstrap/difftest.py            # whole repo corpus
    python bootstrap/difftest.py a.f b.f    # just these

tests/test_bootstrap_lexer.py drives the same functions from pytest.
"""
import ast
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from festina import lexer as py_lexer          # noqa: E402
from festina.errors import CompileError        # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEXER_SOURCE = os.path.join(REPO_ROOT, "bootstrap", "lexdump.f")


def _esc(s):
    """The exact escaping bootstrap/lexer.f's own esc() applies."""
    out = []
    for ch in s:
        if ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "|":
            out.append("\\p")
        elif ch == "\0":
            out.append("\\z")
        else:
            out.append(ch)
    return "".join(out)


# claude.md #272: the one divergence this harness used to carry is gone.
# It was `'a\0b'`: Festina's `text` is NUL-terminated, so the port could
# not reproduce the three-character value the Python lexer produced. The
# fix was to stop producing it -- the `\0` escape is now a compile error
# on both sides (bootstrap/cases/err_nul_escape.f), since accepting an
# escape whose value the language cannot hold is worse than rejecting it.
#
# Kept as an (empty) table rather than deleted: a differential test wants
# somewhere honest to record a divergence it cannot fix, and burying that
# decision in a commit message is how such things get lost.
KNOWN_DIVERGENCES = {}


def _number_value(value):
    """NUMBER carries its kind alongside its value, because `1` and `1.0`
    are different tokens to the parser and would otherwise render the
    same. Floats go through repr(), which bootstrap/lexer.f's
    normalizeFloat() matches by stripping trailing zeros down to (but
    never past) one digit after the point."""
    if isinstance(value, float):
        return "float " + repr(value)
    return "int " + str(value)


def python_dump(source):
    """The Python lexer's token stream, in the canonical form."""
    try:
        tokens = py_lexer.tokenize(source)
    except CompileError as err:
        # bootstrap/lexer.f reports the same rejection as a LEXERR line.
        # The message text is not compared -- only that both lexers stop
        # at the same character, in the same place.
        #
        # The character comes out of the message rather than out of
        # `source` at (line, column), because those coordinates are not
        # always coordinates INTO source: a failure inside a `${...}`
        # interpolation is raised by the recursive tokenize() call, whose
        # line/column are relative to the interpolation fragment. Reading
        # the outer file at that offset reports a character from an
        # unrelated line (this went wrong exactly once, and the Festina
        # side is what caught it).
        ch = ""
        marker = "unexpected character "
        if marker in str(err):
            quoted = str(err).split(marker, 1)[1]
            try:
                ch = ast.literal_eval(quoted.split(" -- ")[0].strip())
            except (ValueError, SyntaxError):
                ch = ""
        return [f"{err.line}:{err.column}|LEXERR|{_esc(ch)}"]

    out = []
    for tok in tokens:
        if tok.type == "REGEX":
            pattern, flags = tok.value
            out.append(f"{tok.line}:{tok.column}|REGEX|{_esc(pattern)}|{_esc(flags)}")
        elif tok.type == "NUMBER":
            out.append(f"{tok.line}:{tok.column}|NUMBER|{_esc(_number_value(tok.value))}")
        elif tok.type == "EOF":
            out.append(f"{tok.line}:{tok.column}|EOF|")
        else:
            out.append(f"{tok.line}:{tok.column}|{tok.type}|{_esc(str(tok.value))}")
    return out


def festina_dump(binary, path):
    """The Festina lexer's token stream, in the canonical form.

    claude.md #272: there is no longer a "cannot read this file at all"
    answer. bootstrap/lexer.f scans the source blob by byte offset, so a
    non-ASCII byte is carried through rather than rejected -- the three
    files that used to be skipped here now lex like any other.
    """
    result = subprocess.run([binary, path], capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"{binary} {path} exited {result.returncode}: {result.stderr}")
    body = result.stdout
    return body.split("\n") if body else []


def build_lexer(out_path):
    """Compile bootstrap/lexer.f, returning the binary's path."""
    result = subprocess.run(
        [sys.executable, "-m", "festina.cli", "compile", LEXER_SOURCE, "-o", out_path],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=600,
    )
    if result.returncode != 0:
        raise RuntimeError(f"compiling {LEXER_SOURCE} failed:\n{result.stdout}\n{result.stderr}")
    return out_path


def corpus():
    """Every .f file in the repository, sorted for a stable report."""
    found = []
    for root, dirs, files in os.walk(REPO_ROOT):
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in files:
            if name.endswith(".f"):
                found.append(os.path.join(root, name))
    return sorted(found)


def compare(binary, path):
    """(status, detail) for one file.

    status is "match", "differ", or "known-divergence".
    """
    with open(path, encoding="utf-8") as f:
        source = f.read()
    got = festina_dump(binary, path)
    want = python_dump(source)

    # The Festina side ends with log()'s own trailing newline.
    while got and got[-1] == "":
        got.pop()

    if got == want:
        return "match", None

    detail = (0, f"{len(want)} tokens", f"{len(got)} tokens")
    for i in range(max(len(got), len(want))):
        a = want[i] if i < len(want) else "<missing>"
        b = got[i] if i < len(got) else "<missing>"
        if a != b:
            detail = (i, a, b)
            break

    rel = os.path.relpath(path, REPO_ROOT).replace(os.sep, "/")
    if rel in KNOWN_DIVERGENCES:
        return "known-divergence", (KNOWN_DIVERGENCES[rel],) + detail[1:]
    return "differ", detail


def main(argv):
    import tempfile
    paths = argv[1:] or corpus()
    with tempfile.TemporaryDirectory() as tmp:
        binary = build_lexer(os.path.join(tmp, "flex"))
        matched = differed = skipped = known = 0
        for path in paths:
            status, detail = compare(binary, path)
            rel = os.path.relpath(path, REPO_ROOT)
            if status == "match":
                matched += 1
            elif status == "skipped-non-ascii":
                skipped += 1
                print(f"SKIP  {rel}  (non-ASCII source -- toAscii() answers null)")
            elif status == "known-divergence":
                known += 1
                reason, want, got = detail
                print(f"KNOWN {rel}  -- {reason}")
                print(f"        python: {want}")
                print(f"        festina: {got}")
            else:
                differed += 1
                idx, want, got = detail
                print(f"DIFF  {rel}  at token {idx}")
                print(f"        python: {want}")
                print(f"        festina: {got}")
    print(f"\n{matched} match, {differed} differ, {known} known-divergence, "
          f"{skipped} skipped (of {len(paths)} files)")
    return 1 if differed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
