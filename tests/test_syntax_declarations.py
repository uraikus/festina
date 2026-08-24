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


class TestFunctionHoisting:
    """claude.md #140: a function is registered (name and signature)
    everywhere in the program before the real analysis pass ever checks
    a single call -- "hoisting" -- so calling one from above its own
    textual declaration is not an ordering error, matching claude.md
    #106's identical treatment of struct/table names. Only the
    semantic-level (declaration-order-independence) half of the feature
    lives here; tests/test_codegen.py's own TestFunctionHoisting
    compiles and RUNS the equivalent programs, including the real
    memory-management regression a nested FuncDecl's own body used to
    trigger."""

    def test_calling_a_function_declared_later_is_not_an_error(self, parser, semantic):
        source = """
        log(greet('world'))

        text func greet(name:text) {
            return 'Hello, ' + name
        }
        """
        program = parser.parse(source)
        semantic.analyze(program)

    def test_mutual_recursion_between_two_functions_is_not_an_error(self, parser, semantic):
        # Whichever of the two is declared first necessarily calls the
        # other before ITS OWN declaration -- impossible to write at all
        # under a strict declared-before-use rule, the clearest case
        # hoisting exists for.
        source = """
        bool func isEven(n:int) {
            if (n == 0) { return true }
            return isOdd(n - 1)
        }

        bool func isOdd(n:int) {
            if (n == 0) { return false }
            return isEven(n - 1)
        }
        """
        program = parser.parse(source)
        semantic.analyze(program)

    def test_a_function_nested_inside_a_block_is_hoisted_the_same_way(self, parser, semantic):
        # A FuncDecl can appear anywhere a statement can (the parser
        # doesn't restrict it to the top level), and analyze_func has
        # always treated one as an ordinary GLOBAL declaration
        # regardless of nesting -- so this must be callable from
        # anywhere too, not just from the point the enclosing `if`
        # happens to be reached.
        source = """
        log(nested())

        if (true) {
            int func nested() { return 42 }
        }
        """
        program = parser.parse(source)
        semantic.analyze(program)

    def test_a_function_nested_inside_another_function_is_hoisted_globally(self, parser, semantic):
        source = """
        int func outer() {
            return inner()
        }

        void func setup() {
            int func inner() { return 7 }
        }
        """
        program = parser.parse(source)
        semantic.analyze(program)

    def test_a_forward_reference_to_a_function_that_never_exists_is_still_an_error(self, parser, semantic, errors):
        # Hoisting only changes ORDER-sensitivity -- a call to a name
        # with no FuncDecl anywhere in the program is still unresolved.
        source = "log(neverDeclared())"
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="unknown function"):
            semantic.analyze(program)

    def test_two_functions_with_the_same_name_is_still_a_duplicate_declaration_error(self, parser, semantic, errors):
        # The pre-pass that registers every signature up front is what
        # makes hoisting possible, but it must still be exactly one
        # registration per NAME -- reusing one is caught the identical
        # way it always was.
        source = """
        int func f() { return 1 }
        int func f() { return 2 }
        """
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="already declared"):
            semantic.analyze(program)

    def test_a_function_cannot_forward_reference_a_struct_declared_later(self, parser, semantic, errors):
        # Hoisting is specific to FUNCTIONS calling functions -- a
        # function's own parameter/return type still needs its struct
        # to be a real, resolvable type, and claude.md #106's own
        # struct-name pre-pass already covers that case independently
        # (structs are pre-registered by NAME before this function-
        # signature pre-pass runs at all, so this is expected to keep
        # compiling, not regress into an error -- see analyze()'s own
        # ordering of the two pre-passes).
        source = """
        Point func makePoint() {
            Point p
            return p
        }

        struct Point { x:int, y:int }
        """
        program = parser.parse(source)
        semantic.analyze(program)
