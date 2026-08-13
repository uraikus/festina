"""claude.md #17 (truthiness), #18 (equality), #19 (conditionals),
#20 (ternary operator)."""
import pytest


class TestConditionalSyntax:
    """claude.md #19: parentheses around the condition are optional."""

    def test_condition_without_parens_parses(self, parser):
        parser.parse("if test {\n    log('yes')\n}")

    def test_condition_with_parens_parses(self, parser):
        parser.parse("if (test) {\n    log('yes')\n}")


class TestTernary:
    """claude.md #20: JavaScript-style ternary, condition must be bool."""

    def test_ternary_parses(self, parser):
        parser.parse("text result = test ? 'yes' : 'no'")

    def test_ternary_condition_must_be_bool(self, parser, semantic, errors):
        source = "int x = 1\ntext result = x ? 'yes' : 'no'"
        program = parser.parse(source)
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)


class TestEquality:
    """claude.md #18: `==`/`!=` supported; `===`/`!==` are compile errors."""

    @pytest.mark.parametrize("op", ["==", "!="])
    def test_supported_equality_operator_parses(self, parser, op):
        parser.parse(f"if value {op} 10 {{\n    log('Ten')\n}}")

    @pytest.mark.parametrize("op", ["===", "!=="])
    def test_strict_equality_operator_is_a_compile_error(self, parser, semantic, errors, op):
        source = f"if value {op} 10 {{\n    log('Ten')\n}}"
        # The spec allows this to be caught either while parsing or during
        # semantic analysis -- either is an acceptable place to reject it,
        # as long as *some* stage raises before code generation.
        try:
            program = parser.parse(source)
        except errors.CompileError:
            return
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)


class TestTruthiness:
    """claude.md #17: no JS truthy/falsy coercion; conditions must be bool."""

    @pytest.mark.parametrize("condition_source", [
        "int value = 0\nif value {\n    log('x')\n}",
        "int value = 1\nif value {\n    log('x')\n}",
        "text value = ''\nif value {\n    log('x')\n}",
        "text value = 'hello'\nif value {\n    log('x')\n}",
        "text value = null\nif value {\n    log('x')\n}",
        "arr[int] value\nif value {\n    log('x')\n}",
    ])
    def test_non_bool_condition_is_rejected(self, parser, semantic, errors, condition_source):
        program = parser.parse(condition_source)
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)

    def test_bool_condition_is_accepted(self, parser, semantic):
        source = "bool enabled = true\nif enabled {\n    log('x')\n}"
        program = parser.parse(source)
        semantic.analyze(program)
