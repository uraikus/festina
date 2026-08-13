"""claude.md #55 (numeric conversion), #56 (Math), #57 (division/modulo
by zero), #58 (struct/table namespace).

Front-end (parser/semantic) behavior only -- see test_codegen.py for the
runtime behavior of Math/.toFloat()/division-by-zero on real compiled
programs.
"""
import pytest


class TestNoImplicitIntFloatConversion:
    """claude.md #55: int and float never mix directly, in any binary
    operator -- not just arithmetic."""

    @pytest.mark.parametrize("op", ["+", "-", "*", "/", "%", "<", ">", "<=", ">=", "==", "!="])
    def test_mixed_operand_operator_is_rejected(self, parser, semantic, errors, op):
        program = parser.parse(f"int a = 5\nfloat b = 2.5\nlog(a {op} b)")
        with pytest.raises(errors.CompileError, match="int and float"):
            semantic.analyze(program)

    def test_same_type_arithmetic_is_fine(self, parser, semantic):
        program = parser.parse("int a = 5\nint b = 3\nint c = a + b")
        semantic.analyze(program)  # must not raise

    def test_float_arithmetic_is_fine(self, parser, semantic):
        program = parser.parse("float a = 5.0\nfloat b = 3.0\nfloat c = a + b")
        semantic.analyze(program)  # must not raise

    def test_int_to_float_requires_explicit_conversion(self, parser, semantic, errors):
        # float x = 5 (an int literal) was already rejected before this
        # change (declared-vs-actual type mismatch); this just confirms
        # the explicit conversion is what makes it valid.
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
        program = parser.parse("int a = 10\nint b = 0\nint c = a / b")
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
