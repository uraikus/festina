"""claude.md #53: features explicitly excluded unless added later."""
import pytest


class TestExcludedSyntax:
    @pytest.mark.parametrize("source,label", [
        ("var x = 1", "var"),
        ("let x = 1", "let"),
        ("if x === 1 {\n}", "==="),
        ("if x !== 1 {\n}", "!=="),
        ("throw 'error'", "throw"),
        ("const db = require('database.f')", "require()"),
    ])
    def test_excluded_construct_is_rejected(self, parser, errors, source, label):
        with pytest.raises(errors.CompileError):
            parser.parse(source)

    def test_js_truthiness_is_not_applied_to_conditions(self, parser, semantic, errors):
        # A non-bool value used directly as a condition must be rejected,
        # not silently coerced the way JavaScript would.
        program = parser.parse("text value = 'hello'\nif value {\n    log('x')\n}")
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)


class TestFailReplacesThrow:
    """claude.md #42: use fail() instead of throw."""

    def test_fail_call_parses(self, parser):
        source = """
        if test != true {
            fail('Test failed')
        }
        """
        parser.parse(source)
