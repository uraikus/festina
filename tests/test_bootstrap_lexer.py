"""bootstrap/lexer.f -- Festina's lexer, written in Festina (claude.md #271).

The only claim worth making about a PORT is that it agrees with the
original, so that is what these tests check: bootstrap/lexer.f and
festina/lexer.py must produce the same token stream, token for token,
over every .f file in the repository plus the targeted cases in
bootstrap/cases/ that cover what the corpus doesn't reach.

Compiling the Festina lexer takes a few seconds, so it is built ONCE per
session (module-scoped fixture) and every test reuses the binary.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bootstrap import difftest  # noqa: E402

from tests.conftest import _require_c_compiler  # noqa: E402


@pytest.fixture(scope="module")
def lexer_binary(tmp_path_factory):
    """bootstrap/lexer.f, compiled. Skips (rather than fails) with no C
    compiler, the same tier rule tests/conftest.py's own compile_and_run
    fixture follows -- this needs to link a real native binary."""
    _require_c_compiler()
    out = tmp_path_factory.mktemp("bootstrap") / "flex"
    return difftest.build_lexer(str(out))


def _corpus_ids():
    return [os.path.relpath(p, difftest.REPO_ROOT) for p in difftest.corpus()]


class TestBootstrapLexerMatchesPython:
    """The differential test itself."""

    @pytest.mark.parametrize("rel", _corpus_ids())
    def test_token_stream_matches_the_python_lexer(self, lexer_binary, rel):
        path = os.path.join(difftest.REPO_ROOT, rel)
        status, detail = difftest.compare(lexer_binary, path)
        if status == "known-divergence":
            # claude.md #272 emptied this table -- the one entry it held
            # was fixed in the language rather than tolerated here. Kept
            # so a future divergence has an honest place to be recorded.
            pytest.xfail(detail[0])
        assert status == "match", (
            f"{rel}: token {detail[0]}\n"
            f"  python:  {detail[1]}\n"
            f"  festina: {detail[2]}")

    def test_the_corpus_is_not_empty(self):
        # A differential test over zero files passes vacuously; this is
        # the guard against the walk silently finding nothing.
        assert len(difftest.corpus()) > 50


class TestTheDifferentialTestCanFail:
    """The harness canary, in the spirit of tests/test_leak_stress.py's
    own `test_the_harness_can_actually_fail`. A differential test that
    cannot report a difference is worth nothing, and this suite has
    already been fooled once: the first bootstrap/cases/division_vs_regex.f
    had ONE '/' per line, which passes whether or not the regex-literal
    denylist works at all (a failed regex attempt falls back to division
    on its own). Two '/' on one line is what actually tests it."""

    def test_a_deliberately_wrong_token_stream_is_reported(self, lexer_binary, tmp_path):
        source = "int a = 1\n"
        src = tmp_path / "canary.f"
        src.write_text(source)

        want = difftest.python_dump(source)
        got = difftest.festina_dump(lexer_binary, str(src))
        while got and got[-1] == "":
            got.pop()
        assert got == want, "sanity: the canary source itself should match"

        # Same tokens, one value corrupted -- the comparison must notice.
        mutated = list(want)
        mutated[0] = mutated[0].replace("|int|int", "|int|WRONG")
        assert mutated != got

    def test_the_ambiguous_slash_case_really_exercises_the_denylist(self):
        """The specific shape that caught the first version out: the
        division cases must put two '/' on a single line, or they prove
        nothing about regex disambiguation."""
        path = os.path.join(difftest.REPO_ROOT, "bootstrap", "cases",
                            "division_vs_regex.f")
        with open(path, encoding="utf-8") as f:
            lines = [ln for ln in f if not ln.lstrip().startswith("//")]
        two_slash_lines = [ln for ln in lines if ln.count("/") >= 2]
        assert two_slash_lines, (
            "division_vs_regex.f has no line with two '/' on it, so it no "
            "longer tests regex-vs-division disambiguation at all")
