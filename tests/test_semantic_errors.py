"""claude.md #48: compile-time error categories and message format."""
import pytest


class TestErrorFormat:
    """claude.md #48: errors must include file, line, column, category,
    and a human-readable explanation, e.g.
    `main.f:12:5: error: condition must be bool, found text`."""

    def test_compile_error_carries_location_and_message(self, errors):
        err = errors.CompileError(
            file="main.f", line=12, column=5,
            category="invalid condition type",
            message="condition must be bool, found text",
        )
        assert err.file == "main.f"
        assert err.line == 12
        assert err.column == 5
        assert err.category == "invalid condition type"
        assert "condition must be bool, found text" in str(err)

    def test_str_matches_spec_example_shape(self, errors):
        err = errors.CompileError(
            file="main.f", line=12, column=5,
            category="invalid condition type",
            message="condition must be bool, found text",
        )
        # main.f:12:5: error: condition must be bool, found text
        assert str(err) == "main.f:12:5: error: condition must be bool, found text"


class TestErrorCategories:
    """claude.md #48: each listed category must be raised in the
    corresponding situation."""

    def test_unknown_variable(self, parser, semantic, errors):
        program = parser.parse("log(undeclaredThing)")
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)

    def test_unknown_function(self, parser, semantic, errors):
        program = parser.parse("notAFunction()")
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)

    def test_unknown_struct(self, parser, semantic, errors):
        program = parser.parse("NotAStruct thing")
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)

    def test_invalid_function_argument_type(self, parser, semantic, errors):
        source = """
        int func add(a:int, b:int) {
            return a + b
        }
        add('one', 'two')
        """
        program = parser.parse(source)
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)

    def test_invalid_return_type(self, parser, semantic, errors):
        source = """
        int func broken() {
            return 'not an int'
        }
        """
        program = parser.parse(source)
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)

    def test_duplicate_declaration(self, parser, semantic, errors):
        source = """
        int count = 1
        int count = 2
        """
        program = parser.parse(source)
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)

    def test_invalid_field_access(self, parser, semantic, errors):
        source = """
        struct User {
            name:text
        }
        User user
        log(user.nonExistentField)
        """
        program = parser.parse(source)
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)

    def test_reassigning_a_constant_is_rejected(self, parser, semantic, errors):
        # claude.md #22: "constant" -- and "Constants should be
        # available for compiler optimization" only holds if a const
        # genuinely never changes after declaration.
        source = "const int x = 5\nx = 10"
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="constant"):
            semantic.analyze(program)

    def test_incrementing_a_constant_is_rejected(self, parser, semantic, errors):
        # claude.md #66: postfix ++/-- is a separate AST node from plain
        # assignment, so it needs (and already had, independently of
        # the plain-assignment check above) its own constant guard.
        source = "const int x = 5\nx++"
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="constant"):
            semantic.analyze(program)

    def test_void_function_cannot_return_a_value(self, parser, semantic, errors):
        # claude.md #23: "A function that does not return a value uses
        # void" implies the converse -- a void function returning one
        # is a type error, not a silently discarded value (which is
        # what happened before: the expression was still evaluated for
        # its side effects, but the result vanished at codegen with no
        # error raised anywhere).
        program = parser.parse("void func f() {\n    return 5\n}")
        with pytest.raises(errors.CompileError, match="void"):
            semantic.analyze(program)

    def test_non_void_function_cannot_bare_return(self, parser, semantic, errors):
        program = parser.parse("int func f() {\n    return\n}")
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)

    @pytest.mark.parametrize("op", ["==", "!="])
    def test_equality_between_unrelated_types_is_rejected(self, parser, semantic, errors, op):
        # claude.md #18 only ever shows == / != between two values of
        # the same type; claude.md #2's "no implicit coercion"
        # principle rules out comparing an int against text directly.
        # Previously unchecked entirely: `5 == 'x'` passed semantic
        # analysis and produced invalid LLVM IR at codegen instead of a
        # clear compile error.
        program = parser.parse(f"log(5 {op} 'x')")
        with pytest.raises(errors.CompileError, match="compare"):
            semantic.analyze(program)

    @pytest.mark.parametrize("op", ["<", ">", "<=", ">="])
    def test_ordering_operators_require_numeric_operands(self, parser, semantic, errors, op):
        program = parser.parse(f"log('a' {op} 'b')")
        with pytest.raises(errors.CompileError, match="int or float"):
            semantic.analyze(program)

    @pytest.mark.parametrize("name", [
        "drawRect", "drawCircle", "drawText", "drawImage", "regex",
        "setTimeout", "setInterval", "clearTimeout", "clearInterval",
    ])
    def test_declaring_a_function_named_like_a_builtin_is_rejected(self, parser, semantic, errors, name):
        # None of these are lexer keywords (unlike log/fail/sqlite, which
        # can't even be parsed as a function name at all) -- they're only
        # recognized by name inside codegen's Call dispatch, which always
        # checks the builtin name before ever consulting scope. A
        # function declared with one of these names used to compile
        # fine but be permanently unreachable: every call to it resolved
        # to the builtin instead, silently.
        program = parser.parse(f"void func {name}() {{\n    log(1)\n}}")
        with pytest.raises(errors.CompileError, match=name):
            semantic.analyze(program)

    @pytest.mark.parametrize("name", [
        "loadImage", "loadAudio", "readFile", "writeFile", "appendFile",
        "fileExists", "deleteFile",
    ])
    def test_a_removed_builtin_name_is_free_to_declare_again(
            self, parser, semantic, name):
        # claude.md #109: these are no longer builtins, so nothing
        # reserves the names any more -- declaring a function called
        # `readFile` is now ordinary and it is genuinely reachable,
        # unlike before, when every call resolved to the builtin. The
        # helpful "this was removed, use X instead" message only fires
        # when nothing else in scope answers to the name (see
        # _REMOVED_BUILTINS' own note).
        program = parser.parse(f"void func {name}() {{\n    log(1)\n}}\n{name}()")
        semantic.analyze(program)

    @pytest.mark.parametrize("index_expr", ["1.5", "'x'", "true"])
    def test_non_int_array_index_is_rejected(self, parser, semantic, errors, index_expr):
        # claude.md #65: "The index expression must resolve to int" --
        # the inferred index type used to be computed and then simply
        # discarded, never actually checked. A float/text/bool index
        # passed semantic analysis and reached codegen, which emitted a
        # `getelementptr` using the index value's own LLVM
        # representation (a raw double, or a `ptr`) where an `i64`
        # index is required -- invalid IR and a confusing internal
        # "LLVM object emission failed" error, not a clear one.
        source = f"arr[int] a = [1, 2, 3]\nlog(a[{index_expr}])"
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="array index must be int"):
            semantic.analyze(program)

    def test_non_int_array_index_is_rejected_on_a_write_target_too(self, parser, semantic, errors):
        source = "arr[int] a = [1, 2, 3]\na[1.5] = 5"
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="array index must be int"):
            semantic.analyze(program)


class TestTypeCheckingExamples:
    """claude.md #50: valid/invalid examples given directly in the spec."""

    def test_int_literal_assignment_is_valid(self, parser, semantic):
        program = parser.parse("int x = 10")
        semantic.analyze(program)

    def test_int_declared_with_string_literal_is_invalid(self, parser, semantic, errors):
        program = parser.parse("int x = 'hello'")
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)

    def test_bool_condition_from_bool_variable_is_valid(self, parser, semantic):
        source = "bool enabled = true\nif enabled {\n}"
        program = parser.parse(source)
        semantic.analyze(program)

    def test_int_used_directly_as_condition_is_invalid(self, parser, semantic, errors):
        source = "int value = 1\nif value {\n}"
        program = parser.parse(source)
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)
