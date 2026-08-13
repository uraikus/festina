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
