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


class TestFuncTypeSyntax:
    """claude.md #141: func[T, T, ...]:R -- a first-class function TYPE,
    parseable anywhere an ordinary type is (a variable/constant
    declaration, a function parameter, a struct field, an arr[T]/
    map[T] element type). Parser-level only -- tests/test_syntax_
    declarations.py's own TestFirstClassFunctions covers semantic
    analysis, and tests/test_codegen.py's covers compiling and running
    programs that actually use one."""

    def test_zero_argument_void_func_type_parses(self, parser):
        parser.parse("func[]:void cb")

    def test_func_type_with_arguments_and_a_return_type_parses(self, parser):
        parser.parse("func[int, text]:bool cb")

    def test_as_a_function_parameter_type_parses(self, parser):
        source = """
        void func apply(fn:func[text]:void, arg:text) {
            fn(arg)
        }
        """
        parser.parse(source)

    def test_as_a_struct_field_type_parses(self, parser):
        parser.parse("struct Holder { cb:func[text]:void }")

    def test_as_an_array_element_type_parses(self, parser):
        parser.parse("arr[func[int]:int] fns")

    def test_as_a_map_value_type_parses(self, parser):
        parser.parse("map[func[text]:void] handlers")

    def test_bare_func_with_no_return_type_still_reports_the_original_error(self, parser, errors):
        # claude.md #141's own parser change only exempts `func[...]`
        # (immediately followed by `[`) from this rejection -- a bare
        # `func name(...)`, missing its return type, is still the
        # exact pre-existing mistake this message describes.
        with pytest.raises(errors.CompileError, match="explicit return type"):
            parser.parse("func sayHello() { log(1) }")


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


class TestFirstClassFunctions:
    """claude.md #141: func[T, T, ...]:R -- a first-class reference to a
    function, usable as an argument, struct property, map value, or
    array value. A bare function-name Identifier (not immediately
    called) infers as this type; calling THROUGH a func-typed value
    (a variable, a struct field, an array element, a map value) is an
    indirect call, arity/argument-type checked against the value's own
    signature rather than a declared function's params."""

    def test_assigning_a_matching_function_by_name_is_valid(self, parser, semantic):
        source = """
        void func greet(name:text) { log(name) }
        func[text]:void cb = greet
        """
        program = parser.parse(source)
        semantic.analyze(program)

    def test_calling_through_a_func_typed_variable_is_valid(self, parser, semantic):
        source = """
        void func greet(name:text) { log(name) }
        func[text]:void cb = greet
        cb('world')
        """
        program = parser.parse(source)
        semantic.analyze(program)

    def test_passing_a_function_as_an_argument_is_valid(self, parser, semantic):
        source = """
        void func greet(name:text) { log(name) }
        void func apply(fn:func[text]:void, arg:text) { fn(arg) }
        apply(greet, 'hi')
        """
        program = parser.parse(source)
        semantic.analyze(program)

    def test_storing_in_a_struct_field_and_calling_it_is_valid(self, parser, semantic):
        source = """
        void func greet(name:text) { log(name) }
        struct Holder { cb:func[text]:void }
        Holder h
        h.cb = greet
        h.cb('yo')
        """
        program = parser.parse(source)
        semantic.analyze(program)

    def test_storing_in_an_array_and_calling_by_index_is_valid(self, parser, semantic):
        source = """
        int func inc(x:int) { return x + 1 }
        int func dec(x:int) { return x - 1 }
        arr[func[int]:int] fns = [inc, dec]
        log(fns[0](5))
        """
        program = parser.parse(source)
        semantic.analyze(program)

    def test_storing_in_a_map_and_calling_by_key_is_valid(self, parser, semantic):
        source = """
        void func greet(name:text) { log(name) }
        map[func[text]:void] handlers
        handlers['g'] = greet
        handlers['g']('map-call')
        """
        program = parser.parse(source)
        semantic.analyze(program)

    def test_null_is_a_valid_func_typed_value(self, parser, semantic):
        program = parser.parse("func[text]:void cb = null")
        semantic.analyze(program)

    def test_assigning_a_function_with_a_mismatched_signature_is_rejected(self, parser, semantic, errors):
        source = """
        void func greet(name:text) { log(name) }
        func[int]:void cb = greet
        """
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="cannot assign"):
            semantic.analyze(program)

    def test_a_mismatched_return_type_is_rejected(self, parser, semantic, errors):
        source = """
        int func inc(x:int) { return x + 1 }
        func[int]:text cb = inc
        """
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="cannot assign"):
            semantic.analyze(program)

    def test_calling_a_func_typed_variable_with_the_wrong_argument_type_is_rejected(self, parser, semantic, errors):
        source = """
        void func greet(name:text) { log(name) }
        func[text]:void cb = greet
        cb(5)
        """
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="expects text"):
            semantic.analyze(program)

    def test_calling_a_func_typed_variable_with_the_wrong_arity_is_rejected(self, parser, semantic, errors):
        source = """
        void func greet(name:text) { log(name) }
        func[text]:void cb = greet
        cb('a', 'b')
        """
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="expects 1 argument"):
            semantic.analyze(program)

    def test_passing_the_wrong_type_where_a_func_typed_parameter_is_expected_is_rejected(
            self, parser, semantic, errors):
        source = """
        void func apply(fn:func[text]:void, arg:text) { fn(arg) }
        apply(5, 'hi')
        """
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="func\\[text\\]:void"):
            semantic.analyze(program)

    def test_calling_through_a_struct_field_with_the_wrong_argument_type_is_rejected(
            self, parser, semantic, errors):
        source = """
        int func inc(x:int) { return x + 1 }
        struct Holder { fn:func[int]:int }
        Holder h
        h.fn = inc
        h.fn('not an int')
        """
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="expects int"):
            semantic.analyze(program)

    def test_a_local_variable_can_shadow_a_global_functions_own_name(self, parser, semantic):
        # Scope.define already permits a local to reuse a global name
        # (see its own comment) -- calling `greet` inside the shadowing
        # scope must resolve to the LOCAL func-typed variable's own
        # signature, never silently fall back to the shadowed global
        # function's signature.
        source = """
        void func greet(name:text) { log(name) }
        text func other(x:text) { return x }

        void func useShadowed() {
            func[text]:text greet = other
            log(greet('shadowed'))
        }
        """
        program = parser.parse(source)
        semantic.analyze(program)
