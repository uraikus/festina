"""bootstrap/parser.f -- Festina's parser, written in Festina
(claude.md #273, completed in #275).

Checked the same way the lexer's port is: both parsers emit one
canonical AST dump and they must agree, node for node, over every .f
file in the repository. All 89 of them do.

The UNPORTED machinery that carried the port while it was partial is
kept rather than removed: a construct with no implementation still
reports itself instead of mis-parsing, which is what the grammar wants
the next time it grows.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bootstrap import astdiff, difftest  # noqa: E402

from tests.conftest import (_require_bootstrap_platform,  # noqa: E402
                            _require_c_compiler)


@pytest.fixture(scope="module")
def parser_binary(tmp_path_factory):
    """bootstrap/astdumpf.f, compiled once for the whole module."""
    _require_bootstrap_platform()
    _require_c_compiler()
    out = tmp_path_factory.mktemp("bootstrap") / "fparse"
    return astdiff.build_parser(str(out))


def _corpus_ids():
    return [os.path.relpath(p, difftest.REPO_ROOT) for p in difftest.corpus()]


class TestBootstrapParserMatchesPython:

    @pytest.mark.parametrize("rel", _corpus_ids())
    def test_ast_matches_the_python_parser(self, parser_binary, rel):
        path = os.path.join(difftest.REPO_ROOT, rel)
        status, detail = astdiff.compare(parser_binary, path)
        if status == "unported":
            # Nothing reports unported today (claude.md #275 finished the
            # port). Kept so a newly-added construct announces itself
            # rather than silently mis-parsing -- and the coverage floor
            # below is what stops that skip from hiding a regression.
            pytest.skip(f"not ported yet: {detail}")
        assert status == "match", (
            f"{rel}: node {detail[0]}\n"
            f"  python:  {detail[1]}\n"
            f"  festina: {detail[2]}")

    def test_enough_of_the_corpus_actually_parses(self, parser_binary):
        """Guards the other direction from the skip above: if a change
        made everything report "unported", every comparison would skip
        and this suite would pass while testing nothing."""
        statuses = [astdiff.compare(parser_binary, p)[0] for p in difftest.corpus()]
        matched = statuses.count("match")
        assert matched >= 85, (
            f"only {matched} corpus files parse identically -- coverage went "
            f"backwards, or something made them all report as unported")


class TestConditionParensAreNotSpecialCased:
    """claude.md #274, found while writing bootstrap/parser.f: parse_if
    and parse_while used to eat a leading LPAREN as "optional condition
    parens", which truncated any condition that merely BEGINS with a
    parenthesised sub-expression."""

    def test_a_parenthesised_first_operand_does_not_end_the_condition(
            self, compile_and_run):
        result = compile_and_run(
            "bool a = true\n"
            "bool b = false\n"
            "int i = 0\n"
            "while (a || b) && i < 3 { i++ }\n"
            "log(i)")
        assert result.stdout == "3\n"

    def test_the_same_for_if(self, compile_and_run):
        result = compile_and_run(
            "bool a = true\n"
            "bool b = false\n"
            "if (a || b) && a { log('yes') } else { log('no') }")
        assert result.stdout == "yes\n"

    def test_a_fully_parenthesised_condition_still_works(self, compile_and_run):
        result = compile_and_run(
            "int i = 0\n"
            "while (i < 2) { i++ }\n"
            "if (i == 2) { log('kept working') }")
        assert result.stdout == "kept working\n"

    def test_a_bare_condition_still_works(self, compile_and_run):
        result = compile_and_run(
            "bool ready = true\n"
            "int i = 0\n"
            "while i < 2 { i++ }\n"
            "if ready { log(i) }")
        assert result.stdout == "2\n"

    def test_the_parenthesised_group_keeps_its_own_precedence(self, compile_and_run):
        # (a || b) && c is not a || (b && c) -- with a false and b true
        # and c false, the first is false and the second is true.
        result = compile_and_run(
            "bool a = false\n"
            "bool b = true\n"
            "bool c = false\n"
            "if (a || b) && c { log('wrong grouping') } else { log('right') }")
        assert result.stdout == "right\n"
