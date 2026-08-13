"""Variable, constant, and function declaration syntax --
claude.md #21 (variables), #22 (constants), #23 (functions),
#24 (function arguments)."""
import pytest


class TestVariables:
    """claude.md #21: `type name = value`; no `var`/`let`."""

    @pytest.mark.parametrize("source", [
        "int count = 10",
        "text name = 'Festina'",
        "bool enabled = true",
    ])
    def test_typed_declaration_parses(self, parser, source):
        parser.parse(source)

    @pytest.mark.parametrize("keyword", ["var", "let"])
    def test_var_and_let_are_rejected(self, parser, errors, keyword):
        with pytest.raises(errors.CompileError):
            parser.parse(f"{keyword} count = 10")


class TestConstants:
    """claude.md #22: `const type name = value`."""

    def test_const_declaration_parses(self, parser):
        parser.parse("const text name = 'Festina'")

    def test_const_requires_a_type(self, parser, errors):
        # Bare `const name = 'Festina'` omits the required type annotation.
        with pytest.raises(errors.CompileError):
            parser.parse("const name = 'Festina'")


class TestFunctions:
    """claude.md #23: `return_type func name(arguments) { }`."""

    def test_function_with_return_value_parses(self, parser):
        source = """
        text func returnHello() {
            text value = 'hello'
            return value
        }
        """
        parser.parse(source)

    def test_void_function_parses(self, parser):
        source = """
        void func sayHello() {
            log('Hello')
        }
        """
        parser.parse(source)

    def test_function_requires_explicit_return_type(self, parser, errors):
        # JavaScript-style `function sayHello() {}` has no return type and
        # is not valid Festina syntax.
        with pytest.raises(errors.CompileError):
            parser.parse("func sayHello() { log('Hello') }")


class TestFunctionArguments:
    """claude.md #24: arguments use `name:type`."""

    def test_single_typed_argument_parses(self, parser):
        source = """
        text func logStr(str:text) {
            log(str)
            return str
        }
        """
        parser.parse(source)

    def test_multiple_typed_arguments_parse(self, parser):
        source = """
        int func add(a:int, b:int) {
            return a + b
        }
        """
        parser.parse(source)

    def test_untyped_argument_is_rejected(self, parser, errors):
        with pytest.raises(errors.CompileError):
            parser.parse("int func add(a, b) { return a + b }")
