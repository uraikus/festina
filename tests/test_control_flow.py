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

    def test_ternary_with_a_fresh_text_branch_survives_past_the_statement(self, compile_and_run):
        # claude.md #173: a Ternary used to be treated as "aliasing" no
        # matter what its own branches were -- correct here (the taken
        # branch is a template literal, a genuinely fresh buffer) only
        # by accident, since the leak this used to cause is invisible
        # without a sanitizer (see tests/stress/ternary_ownership_churn.f
        # for the ASan-confirmed leak coverage) -- this pins that the
        # VALUE itself is still correct and usable well past the
        # ternary's own statement, on both branches.
        result = compile_and_run("""
        text a = true ? `fresh ${1 + 1}` : 'literal'
        text b = false ? `fresh ${1 + 1}` : 'literal'
        log(a)
        log(b)
        """)
        assert result.stdout.splitlines() == ["fresh 2", "literal"]

    def test_ternary_with_a_fresh_struct_branch_survives_past_the_statement(self, compile_and_run):
        result = compile_and_run("""
        struct S { n:int }
        S func make(v:int) { S s  s.n = v  return s }
        S shared = make(0)
        S a = true ? make(7) : shared
        S b = false ? make(7) : shared
        log(a.n)
        log(b.n)
        """)
        assert result.stdout.splitlines() == ["7", "0"]


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
