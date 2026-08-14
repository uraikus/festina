"""claude.md #71: environment.NAME / environment[keyExpr].

Lexer/parser/semantic-level tests only -- see tests/test_codegen.py's
TestEnvironment for the real compile-and-run end-to-end coverage.
"""
import pytest


class TestEnvironmentAccess:
    def test_dot_access_parses(self, parser, semantic):
        program = parser.parse("log(environment.HOME)")
        semantic.analyze(program)

    def test_dot_access_infers_text(self, parser, semantic):
        program = parser.parse("text home = environment.HOME")
        semantic.analyze(program)

    def test_computed_access_with_a_string_literal_parses(self, parser, semantic):
        program = parser.parse("log(environment['HOME'])")
        semantic.analyze(program)

    def test_computed_access_with_a_variable_key_parses(self, parser, semantic):
        source = "text k = 'HOME'\nlog(environment[k])"
        program = parser.parse(source)
        semantic.analyze(program)

    def test_computed_access_with_a_non_text_key_is_a_compile_error(self, parser, semantic, errors):
        program = parser.parse("log(environment[5])")
        with pytest.raises(errors.CompileError, match="must be text"):
            semantic.analyze(program)

    def test_bare_reference_is_a_compile_error(self, parser, semantic, errors):
        program = parser.parse("log(environment)")
        with pytest.raises(errors.CompileError, match="environment.NAME"):
            semantic.analyze(program)

    def test_used_in_a_condition_after_null_check_parses(self, parser, semantic):
        source = """
        text apiKey = environment.API_KEY
        if apiKey == null {
            fail('API_KEY is not set')
        }
        """
        program = parser.parse(source)
        semantic.analyze(program)


class TestEnvironmentIsReadOnly:
    def test_dot_assignment_is_a_compile_error(self, parser, semantic, errors):
        program = parser.parse("environment.HOME = '/tmp'")
        with pytest.raises(errors.CompileError, match="read-only"):
            semantic.analyze(program)

    def test_computed_assignment_is_a_compile_error(self, parser, semantic, errors):
        program = parser.parse("environment['HOME'] = '/tmp'")
        with pytest.raises(errors.CompileError, match="read-only"):
            semantic.analyze(program)


class TestEnvironmentIsReserved:
    def test_declaring_a_variable_named_environment_is_a_compile_error(self, parser, semantic, errors):
        # A specific, named message (Scope.define) rather than the
        # generic "already declared" every other duplicate declaration
        # gets -- there's no earlier `environment` declaration in this
        # program to point a user back to, so the generic message alone
        # wouldn't explain why.
        program = parser.parse("int environment = 5")
        with pytest.raises(errors.CompileError, match="reserved for reading environment variables"):
            semantic.analyze(program)

    def test_declaring_a_function_named_environment_is_a_compile_error(self, parser, semantic, errors):
        program = parser.parse("void func environment() {\n    log(1)\n}")
        with pytest.raises(errors.CompileError, match="reserved for reading environment variables"):
            semantic.analyze(program)
