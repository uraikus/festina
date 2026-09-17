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

    def test_a_local_named_environment_is_a_compile_error(self, parser, semantic, errors):
        # claude.md #339: the pre-registration above only ever collided
        # with a GLOBAL declaration, so this used to be accepted -- and
        # the error arrived at the first READ instead ("must be accessed
        # as environment.NAME"), or never, if the local was written and
        # not read. Same hole `Math` had, same fix: the name is checked
        # in every scope, not just the one the pre-registration sits in.
        source = """
        void func f() {
            text environment = 'x'
            log('ok')
        }
        f()
        """
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="reserved for reading environment variables"):
            semantic.analyze(program)

    def test_a_parameter_named_environment_is_a_compile_error(self, parser, semantic, errors):
        source = """
        void func f(environment:text) {
            log('ok')
        }
        f('x')
        """
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="reserved for reading environment variables"):
            semantic.analyze(program)

    def test_a_for_loop_variable_named_environment_is_a_compile_error(self, parser, semantic, errors):
        source = ("for int environment = 0, environment < 2, environment++ {\n"
                  "    log(1)\n}")
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="reserved for reading environment variables"):
            semantic.analyze(program)

    def test_a_catch_variable_named_environment_is_a_compile_error(self, parser, semantic, errors):
        source = """
        try {
            fail('x')
        } catch (environment:text) {
            log('ok')
        }
        """
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="reserved for reading environment variables"):
            semantic.analyze(program)

    def test_a_threads_own_function_named_environment_is_a_compile_error(self, parser, semantic, errors):
        # A thread's private functions bypass Scope.define, so this is
        # its own code path -- see the identical `Math` test.
        source = """
        thread worker {
            int func environment(x:int) {
                return x
            }
            on message(w:thread, msg:int) {
                w.reply(environment(msg))
            }
        }
        """
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="reserved for reading environment variables"):
            semantic.analyze(program)

    def test_the_message_names_only_what_is_actually_checked(self, parser, semantic, errors):
        # claude.md #339: it used to end "...variable, constant,
        # function, struct, or table", and `struct environment` is
        # accepted -- 6.7 keeps type names and value names apart. A
        # message that lists a rule the compiler does not have sends a
        # reader looking for a bug that is not there.
        program = parser.parse("int environment = 5")
        with pytest.raises(errors.CompileError) as excinfo:
            semantic.analyze(program)
        assert "struct" not in str(excinfo.value)
        assert "table" not in str(excinfo.value)

    def test_a_struct_named_environment_is_allowed(self, parser, semantic):
        source = """
        struct environment {
            n:int
        }
        environment e
        e.n = 1
        log(`${e.n}`)
        """
        program = parser.parse(source)
        semantic.analyze(program)
