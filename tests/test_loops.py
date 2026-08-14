"""claude.md #60 (for loops), #61 (while loops), #63 (array length),
#66 (postfix increment/decrement).

Lexer/parser/semantic-level tests only -- see tests/test_codegen.py for
the real compile-and-run end-to-end coverage of these same sections.
"""
import pytest


class TestForLoopSyntax:
    """claude.md #60: `for initialization, condition, update { }`."""

    def test_for_loop_parses(self, parser):
        parser.parse("for int x = 0, x < 10, x++ {\n    log(x)\n}")

    def test_for_loop_array_iteration_example_parses(self, parser):
        # The exact worked example from claude.md #60.
        source = (
            "arr[int] array = [1, 2, 3]\n"
            "for int x = 0, x < array.length, x++ {\n"
            "    log(array[x])\n"
            "}"
        )
        parser.parse(source)

    def test_for_loop_condition_must_be_bool(self, parser, semantic, errors):
        source = "for int x = 0, x, x++ {\n    log(x)\n}"
        program = parser.parse(source)
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)

    def test_for_loop_init_variable_scoped_to_loop_body(self, parser, semantic, errors):
        # claude.md #60: "The initialization variable is scoped to the
        # loop body" -- it must not be visible after the loop.
        source = "for int x = 0, x < 3, x++ {\n    log(x)\n}\nlog(x)"
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="unknown variable"):
            semantic.analyze(program)

    def test_for_loop_init_variable_visible_in_body(self, parser, semantic):
        source = "for int x = 0, x < 3, x++ {\n    log(x)\n}"
        program = parser.parse(source)
        semantic.analyze(program)


class TestWhileLoopSyntax:
    """claude.md #61: `while condition { }`."""

    def test_while_loop_parses(self, parser):
        parser.parse("int e = 0\nwhile e < 10 {\n    log(e)\n    e++\n}")

    def test_infinite_while_true_parses(self, parser):
        parser.parse("while true {\n    log('running')\n}")

    def test_while_condition_must_be_bool(self, parser, semantic, errors):
        # claude.md #61: "Festina does not perform truthy/falsy
        # conversion when evaluating loop conditions."
        source = "int e = 1\nwhile e {\n    log('x')\n}"
        program = parser.parse(source)
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)

    def test_while_condition_bool_is_accepted(self, parser, semantic):
        source = "bool running = true\nwhile running {\n    running = false\n}"
        program = parser.parse(source)
        semantic.analyze(program)


class TestBreakAndContinue:
    """claude.md #73: break/continue, valid only inside a for/while loop,
    targeting the nearest enclosing one."""

    def test_break_in_a_for_loop_parses(self, parser, semantic):
        source = "for int i = 0, i < 10, i++ {\n    break\n}"
        program = parser.parse(source)
        semantic.analyze(program)

    def test_continue_in_a_for_loop_parses(self, parser, semantic):
        source = "for int i = 0, i < 10, i++ {\n    continue\n}"
        program = parser.parse(source)
        semantic.analyze(program)

    def test_break_in_a_while_loop_parses(self, parser, semantic):
        source = "while true {\n    break\n}"
        program = parser.parse(source)
        semantic.analyze(program)

    def test_continue_in_a_while_loop_parses(self, parser, semantic):
        source = "while true {\n    continue\n}"
        program = parser.parse(source)
        semantic.analyze(program)

    def test_break_inside_an_if_inside_a_loop_parses(self, parser, semantic):
        # break/continue reach through if/block nesting to the nearest
        # *loop*, not just their immediately enclosing block.
        source = "for int i = 0, i < 10, i++ {\n    if i == 5 {\n        break\n    }\n}"
        program = parser.parse(source)
        semantic.analyze(program)

    def test_break_outside_any_loop_is_a_compile_error(self, parser, semantic, errors):
        program = parser.parse("break")
        with pytest.raises(errors.CompileError, match="'break' can only be used inside a for/while loop"):
            semantic.analyze(program)

    def test_continue_outside_any_loop_is_a_compile_error(self, parser, semantic, errors):
        program = parser.parse("continue")
        with pytest.raises(errors.CompileError, match="'continue' can only be used inside a for/while loop"):
            semantic.analyze(program)

    def test_break_inside_an_if_but_outside_any_loop_is_still_a_compile_error(self, parser, semantic, errors):
        # An if isn't a loop -- being nested inside one doesn't excuse it.
        source = "if true {\n    break\n}"
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="'break' can only be used"):
            semantic.analyze(program)

    def test_break_inside_a_function_declared_outside_any_loop_is_a_compile_error(self, parser, semantic, errors):
        # A function's own body starts its own loop context -- it can
        # never inherit "inside a loop" from wherever it happens to be
        # declared (functions aren't declared inside loop bodies in
        # practice, but the body's own statements are what matters here).
        source = "void func f() {\n    break\n}"
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="'break' can only be used"):
            semantic.analyze(program)


class TestArrayLength:
    """claude.md #63: every array has a built-in read-only `.length`."""

    def test_length_access_parses(self, parser):
        parser.parse("arr[int] values = [1, 2, 3]\nlog(values.length)")

    def test_length_type_is_int(self, parser, semantic):
        source = "arr[int] values = [1, 2, 3]\nint n = values.length"
        program = parser.parse(source)
        semantic.analyze(program)

    def test_length_is_read_only(self, parser, semantic, errors):
        source = "arr[int] values = [1, 2, 3]\nvalues.length = 5"
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="read-only"):
            semantic.analyze(program)

    def test_length_on_non_array_is_an_error(self, parser, semantic, errors):
        source = "int x = 5\nlog(x.length)"
        program = parser.parse(source)
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)


class TestPostfixIncrementDecrement:
    """claude.md #66: postfix ++/--, int variables only."""

    @pytest.mark.parametrize("op", ["++", "--"])
    def test_postfix_op_parses(self, parser, op):
        parser.parse(f"int i = 0\ni{op}")

    @pytest.mark.parametrize("op", ["++", "--"])
    def test_postfix_op_on_int_variable_is_accepted(self, parser, semantic, op):
        source = f"int i = 0\ni{op}"
        program = parser.parse(source)
        semantic.analyze(program)

    @pytest.mark.parametrize("op", ["++", "--"])
    def test_postfix_op_on_float_is_a_compile_error(self, parser, semantic, errors, op):
        source = f"float f = 0.0\nf{op}"
        program = parser.parse(source)
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)

    @pytest.mark.parametrize("op", ["++", "--"])
    def test_postfix_op_on_constant_is_a_compile_error(self, parser, semantic, errors, op):
        source = f"const int i = 0\ni{op}"
        program = parser.parse(source)
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)

    def test_postfix_op_as_for_loop_update_expression(self, parser, semantic):
        # claude.md #60's own worked examples: i++ / i--.
        program = parser.parse("for int x = 10, x > 0, x-- {\n    log(x)\n}")
        semantic.analyze(program)
