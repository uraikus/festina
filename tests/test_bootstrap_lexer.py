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

from tests.conftest import (_require_bootstrap_platform,  # noqa: E402
                            _require_c_compiler)


@pytest.fixture(scope="module")
def lexer_binary(tmp_path_factory):
    """bootstrap/lexer.f, compiled. Skips (rather than fails) with no C
    compiler, the same tier rule tests/conftest.py's own compile_and_run
    fixture follows -- this needs to link a real native binary."""
    _require_bootstrap_platform()
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

    def test_subprocess_output_is_decoded_as_utf8_not_by_locale(self, lexer_binary):
        """claude.md #276. `text=True` without an explicit `encoding`
        decodes with the LOCALE's preferred codec -- cp1252 on Windows,
        UTF-8 nearly everywhere else. Every binary these harnesses run
        emits UTF-8, so an unguarded read silently compared mojibake
        against correct text and reported eight differences that did not
        exist: the four corpus files with non-ASCII content, in both
        harnesses.

        Forcing the locale default to cp1252 for any call that does NOT
        name an encoding reproduces Windows exactly, on Linux. This test
        fails if anyone drops the keyword again -- which is the point,
        since Linux CI otherwise cannot see the bug at all."""
        import subprocess
        non_ascii = os.path.join(difftest.REPO_ROOT, "examples", "ascii_scan.f")
        assert difftest.compare(lexer_binary, non_ascii)[0] == "match"

        real_run = subprocess.run

        def locale_cp1252(*args, **kwargs):
            if kwargs.get("text") and "encoding" not in kwargs:
                kwargs["encoding"] = "cp1252"
            return real_run(*args, **kwargs)

        subprocess.run = locale_cp1252
        try:
            status, _ = difftest.compare(lexer_binary, non_ascii)
        finally:
            subprocess.run = real_run
        assert status == "match", (
            "a non-UTF-8 locale changed the comparison's answer -- some "
            "subprocess read is decoding by locale default again instead "
            "of going through difftest.run_text")

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


class TestFloatLiteralsAgreeOutsideTheCorpus:
    """claude.md #342: the float ranges no corpus file can contain.

    A float token compares by its IEEE-754 bit pattern, because that is
    what a float literal is. The old rule compared Python's `repr()`,
    which `bootstrap/lexer.f` could only match when the source spelling
    already was the shortest round-trip form -- so every literal
    carrying more precision than a double holds diverged, as did every
    one outside the plain-decimal range where `repr()` switches to
    exponent form.

    `cases/float_extremes.f` covers what it can, and that is bounded:
    `cgParseFloat` in `bootstrap/codegen.f` converts a decimal exactly
    only inside a window (at most 18 significant digits, digit string
    below 2^53) and reports anything else unported, so the
    large-magnitude and excess-precision halves cannot appear in a
    corpus file at all. They are real all the same, and this is where
    they are measured -- generated rather than checked in, because
    thousands of literals belong in a loop rather than in a source file.
    """

    LITERALS = None

    @classmethod
    def _literals(cls):
        if cls.LITERALS is not None:
            return cls.LITERALS
        import random
        lits = [
            # Plain, and the trailing-zero case the old rule existed for.
            "1.0", "0.5", "1.50", "127.0", "2.718281828459045",
            # repr() switches to exponent form below 1e-4 ...
            "0.00001", "0.0000000001",
            # ... and at 1e16, which the roadmap recorded as 1e17.
            "10000000000000000.0", "123456789012345680.0",
            # More precision than a double holds -- the larger half of
            # the divergence, and nothing to do with exponents.
            "1.23456789012345678901", "3.14159265358979311600",
            "1.0000000000000000055511151231257827",
            # An integer past 2^53: the nearest double ends ...992.
            "9007199254740993.0",
            # Values whose shortest form is famously not their arithmetic.
            "0.30000000000000004", "1.0000000000000002",
        ]
        rng = random.Random(4242)
        for _ in range(600):
            whole = rng.randint(0, 10 ** rng.randint(1, 17))
            frac = rng.randint(0, 10 ** rng.randint(1, 18))
            lits.append(f"{whole}.{frac}")
        # Every decade down to and past the smallest subnormal. The
        # encoder has a separate branch for those, and its first version
        # returned zero for all of them -- a constant written as 2^537
        # that was not a power of two. Nothing in the corpus reaches
        # this far, so only a probe aimed here could say so.
        for zeros in range(0, 330, 3):
            lits.append("0." + "0" * zeros + "13")
        cls.LITERALS = lits
        return lits

    def test_every_generated_literal_agrees(self, lexer_binary, tmp_path):
        lits = self._literals()
        source = "\n".join(f"float v{i} = {lit}" for i, lit in enumerate(lits)) + "\n"
        path = tmp_path / "floats.f"
        path.write_text(source, encoding="utf-8")
        status, detail = difftest.compare(lexer_binary, str(path))
        assert status == "match", (
            f"token {detail[0]}\n  python:  {detail[1]}\n  festina: {detail[2]}")

    def test_the_sample_really_spans_the_hard_ranges(self):
        """A generated sample that quietly stopped covering the ranges it
        was written for would pass while measuring nothing -- the
        `cases/float_bits.f` lesson (decisions.md #290), where a literal
        outside its intended range made the case file vacuous."""
        vals = [float(x) for x in self._literals()]
        tiny = sum(1 for v in vals if 0 < v < 1e-4)
        huge = sum(1 for v in vals if v >= 1e16)
        subnormal = sum(1 for v in vals if 0 < v < 2.2250738585072014e-308)
        excess = sum(1 for x in self._literals()
                     if len(x.replace(".", "").lstrip("0")) > 17)
        assert tiny >= 20, f"only {tiny} literals below the 1e-4 threshold"
        assert huge >= 2, f"only {huge} literals at or above 1e16"
        assert subnormal >= 5, f"only {subnormal} subnormals"
        assert excess >= 3, f"only {excess} literals with excess precision"
