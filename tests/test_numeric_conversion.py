"""claude.md #56 (Math), #57 (division/modulo by zero), #58
(struct/table namespace), #143 (int/float mixing always promotes to
float, division always returns float -- superseded claude.md #55's own
"int and float never mix directly" rule).

Front-end (parser/semantic) behavior only -- see test_codegen.py for the
runtime behavior of Math/.toFloat()/division-by-zero on real compiled
programs.
"""
import pytest


class TestImplicitIntFloatCoercion:
    """claude.md #143: int and float mix freely now, in any binary
    operator -- the int side is implicitly coerced to float, "as though
    int.toFloat() had been written." Arithmetic operators (except `/`,
    covered by TestDivisionAlwaysReturnsFloat below) return float only
    when the two operands actually differ; comparison/equality
    operators always return bool, mixed or not."""

    @pytest.mark.parametrize("op", ["+", "-", "*", "%"])
    def test_mixed_arithmetic_infers_float(self, parser, semantic, op):
        program = parser.parse(f"int a = 5\nfloat b = 2.5\nfloat c = a {op} b")
        semantic.analyze(program)  # must not raise -- result is float

    @pytest.mark.parametrize("op", ["+", "-", "*", "%"])
    def test_mixed_arithmetic_result_cannot_be_assigned_to_int(self, parser, semantic, errors, op):
        # The promoted result is genuinely float now -- assigning it
        # back to an int-declared variable is an ordinary declared-vs-
        # actual type mismatch, the same as any other int/float
        # assignment mismatch.
        program = parser.parse(f"int a = 5\nfloat b = 2.5\nint c = a {op} b")
        with pytest.raises(errors.CompileError, match="cannot assign"):
            semantic.analyze(program)

    @pytest.mark.parametrize("op", ["<", ">", "<=", ">=", "==", "!="])
    def test_mixed_comparison_is_valid_and_infers_bool(self, parser, semantic, op):
        program = parser.parse(f"int a = 5\nfloat b = 2.5\nbool c = a {op} b")
        semantic.analyze(program)  # must not raise

    def test_same_type_arithmetic_is_fine(self, parser, semantic):
        program = parser.parse("int a = 5\nint b = 3\nint c = a + b")
        semantic.analyze(program)  # must not raise

    def test_float_arithmetic_is_fine(self, parser, semantic):
        program = parser.parse("float a = 5.0\nfloat b = 3.0\nfloat c = a + b")
        semantic.analyze(program)  # must not raise

    def test_int_to_float_declaration_still_requires_explicit_conversion(self, parser, semantic, errors):
        # claude.md #143 only changed BINARY OPERATORS -- a plain
        # declaration/assignment (no operator at all) is a separate
        # concern and is untouched: `float x = 5` (an int literal) is
        # still a declared-vs-actual type mismatch, exactly as before.
        program = parser.parse("float x = 5")
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)

    def test_to_float_method_on_int_produces_float(self, parser, semantic):
        program = parser.parse("int a = 5\nfloat b = a.toFloat()")
        semantic.analyze(program)  # must not raise

    def test_to_float_method_on_non_int_is_rejected(self, parser, semantic, errors):
        program = parser.parse("float a = 5.0\nfloat b = a.toFloat()")
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)


class TestDivisionAlwaysReturnsFloat:
    """claude.md #143: `/` always returns float, unconditionally -- even
    for two ints, unlike every other arithmetic operator here (+, -, *,
    %), which only promotes to float when the two operands actually
    differ."""

    def test_int_over_int_division_infers_float(self, parser, semantic):
        program = parser.parse("int a = 10\nint b = 3\nfloat c = a / b")
        semantic.analyze(program)  # must not raise

    def test_int_over_int_division_cannot_be_assigned_to_int(self, parser, semantic, errors):
        program = parser.parse("int a = 10\nint b = 3\nint c = a / b")
        with pytest.raises(errors.CompileError, match="cannot assign"):
            semantic.analyze(program)

    def test_float_over_float_division_is_still_float(self, parser, semantic):
        program = parser.parse("float a = 10.0\nfloat b = 3.0\nfloat c = a / b")
        semantic.analyze(program)  # must not raise

    def test_mixed_division_is_float(self, parser, semantic):
        program = parser.parse("int a = 10\nfloat b = 3.0\nfloat c = a / b")
        semantic.analyze(program)  # must not raise

    def test_modulo_between_two_ints_is_still_int(self, parser, semantic, errors):
        # claude.md #143's own "division always returns float" is
        # specific to `/` -- `%` is not "division" and keeps its old
        # int-when-both-int behavior (see TestImplicitIntFloatCoercion
        # above for the case where % genuinely DOES mix int and float).
        program = parser.parse("int a = 10\nint b = 3\nint c = a % b")
        semantic.analyze(program)  # must not raise


class TestToText:
    """int/float/bool.toText() -- an explicit spelling of the same
    stringification template interpolation already does implicitly for
    these three types."""

    @pytest.mark.parametrize("decl", ["int a = 5", "float a = 5.0", "bool a = true"])
    def test_to_text_on_int_float_bool_produces_text(self, parser, semantic, decl):
        program = parser.parse(f"{decl}\ntext s = a.toText()")
        semantic.analyze(program)  # must not raise

    def test_to_text_on_text_is_rejected(self, parser, semantic, errors):
        program = parser.parse("text a = 'x'\ntext s = a.toText()")
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)

    def test_to_text_with_an_argument_is_rejected(self, parser, semantic, errors):
        # toText() takes no arguments -- with one, it doesn't match the
        # recognized zero-arg pattern at all, so it falls through to
        # the generic "unknown member call" handling, same as any other
        # unrecognized Call-on-Member.
        program = parser.parse("int a = 5\ntext s = a.toText(1)")
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)


class TestMath:
    """claude.md #56: Math.floor/ceil/round/trunc convert float -> int."""

    @pytest.mark.parametrize("fn", ["floor", "ceil", "round", "trunc"])
    def test_math_function_on_float_produces_int(self, parser, semantic, fn):
        program = parser.parse(f"float price = 19.99\nint rounded = Math.{fn}(price)")
        semantic.analyze(program)  # must not raise

    def test_math_function_on_int_is_rejected(self, parser, semantic, errors):
        program = parser.parse("int a = 5\nint b = Math.floor(a)")
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)

    def test_math_function_wrong_argument_count_is_rejected(self, parser, semantic, errors):
        program = parser.parse("float a = 1.0\nint b = Math.floor(a, a)")
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)


class TestDivisionAndModuloByZero:
    """claude.md #57: division/modulo by zero is not a compile-time
    concern (the divisor isn't always known statically) -- these must
    still parse and type-check normally. The "returns null" runtime
    behavior is tested end-to-end in test_codegen.py."""

    def test_division_parses_and_type_checks(self, parser, semantic):
        # claude.md #143: / always returns float now, even for two ints.
        program = parser.parse("int a = 10\nint b = 0\nfloat c = a / b")
        semantic.analyze(program)  # must not raise

    def test_modulo_parses_and_type_checks(self, parser, semantic):
        program = parser.parse("int a = 10\nint b = 0\nint c = a % b")
        semantic.analyze(program)  # must not raise


class TestStructTableNamespace:
    """claude.md #58: struct/table names and variable/function/constant
    names are resolved independently -- no duplicate-declaration error
    across the two namespaces, only within each one."""

    def test_variable_may_reuse_a_struct_name(self, parser, semantic):
        source = """
        struct User {
            name:text
        }
        int User = 5
        log(User)
        """
        program = parser.parse(source)
        semantic.analyze(program)  # must not raise

    def test_variable_may_reuse_a_table_name(self, parser, semantic):
        source = """
        table People {
            id:int
        }
        text People = 'not the table'
        log(People)
        """
        program = parser.parse(source)
        semantic.analyze(program)  # must not raise
