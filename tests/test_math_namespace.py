"""specification.md 6.7/16.2: `Math` is a namespace, not a value.

`Math.sqrt(x)` is resolved by the name `Math` before any scope is
consulted -- that is what makes it a namespace rather than an object --
so a variable, constant, function or parameter called `Math` could
never be read through `Math.something`. It used to be accepted anyway:
the binding compiled, every `Math.NAME` in the program went on meaning
the built-in, and the only sign anything was wrong was a misleading
`Math has no member 'n'` pointing at the USE rather than at the
declaration that was being ignored. decisions.md #339 rejects the
declaration instead, at its own position.

Semantic-level tests -- the whole rule is a compile error, so there is
nothing to run.
"""
import pytest


class TestMathIsStillTheBuiltinNamespace:
    """The rule must not cost anything that worked before."""

    def test_a_math_function_call_analyzes(self, parser, semantic):
        program = parser.parse("float x = Math.sqrt(9.0)")
        semantic.analyze(program)

    def test_a_math_rounding_call_analyzes(self, parser, semantic):
        program = parser.parse("int n = Math.floor(2.7)")
        semantic.analyze(program)

    def test_a_math_constant_analyzes(self, parser, semantic):
        program = parser.parse("float pi = Math.PI")
        semantic.analyze(program)

    def test_an_unknown_math_member_still_names_math(self, parser, semantic, errors):
        # Unchanged, and now unambiguous: with the declaration rejected
        # there is no longer any binding this message could be talking
        # about instead of the namespace.
        program = parser.parse("log(`${Math.nope}`)")
        with pytest.raises(errors.CompileError, match="Math has no member 'nope'"):
            semantic.analyze(program)

    def test_a_math_function_used_without_calling_it_still_says_so(self, parser, semantic, errors):
        program = parser.parse("log(`${Math.sqrt}`)")
        with pytest.raises(errors.CompileError, match="call it"):
            semantic.analyze(program)


class TestMathMayNotBeDeclared:
    """One rejection per kind of value name (6.7)."""

    def test_a_global_variable_named_math_is_a_compile_error(self, parser, semantic, errors):
        program = parser.parse("text Math = 'hello'")
        with pytest.raises(errors.CompileError, match="built-in math namespace"):
            semantic.analyze(program)

    def test_a_global_constant_named_math_is_a_compile_error(self, parser, semantic, errors):
        program = parser.parse("const text Math = 'hello'")
        with pytest.raises(errors.CompileError, match="built-in math namespace"):
            semantic.analyze(program)

    def test_a_local_variable_named_math_is_a_compile_error(self, parser, semantic, errors):
        # The one the global-scope pre-registration trick could never
        # have caught: a local lives in a child Scope, so there is no
        # name already sitting there to collide with.
        source = """
        void func f() {
            text Math = 'hello'
            log(Math)
        }
        f()
        """
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="built-in math namespace"):
            semantic.analyze(program)

    def test_a_parameter_named_math_is_a_compile_error(self, parser, semantic, errors):
        source = """
        void func f(Math:text) {
            log(Math)
        }
        f('hi')
        """
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="built-in math namespace"):
            semantic.analyze(program)

    def test_a_function_named_math_is_a_compile_error(self, parser, semantic, errors):
        source = """
        int func Math(x:int) {
            return x
        }
        log(`${Math(1)}`)
        """
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="built-in math namespace"):
            semantic.analyze(program)

    def test_a_for_loop_variable_named_math_is_a_compile_error(self, parser, semantic, errors):
        source = "for int Math = 0, Math < 2, Math++ {\n    log(1)\n}"
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="built-in math namespace"):
            semantic.analyze(program)

    def test_a_catch_variable_named_math_is_a_compile_error(self, parser, semantic, errors):
        source = """
        try {
            fail('x')
        } catch (Math:text) {
            log(Math)
        }
        """
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="built-in math namespace"):
            semantic.analyze(program)

    def test_a_struct_typed_global_named_math_is_a_compile_error(self, parser, semantic, errors):
        # The exact program from todo.md. It used to fail at 3:6 with
        # "Math has no member 'n'" -- a message about the namespace,
        # for a field the user really did declare.
        source = """
        struct Thing {
            n:int
        }
        Thing Math
        Math.n = 7
        """
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="built-in math namespace"):
            semantic.analyze(program)

    def test_a_thread_named_math_is_a_compile_error(self, parser, semantic, errors):
        source = """
        thread Math {
            on message(worker:thread, msg:int) {
                worker.reply(msg * 10)
            }
        }
        """
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="built-in math namespace"):
            semantic.analyze(program)

    def test_a_threads_own_function_named_math_is_a_compile_error(self, parser, semantic, errors):
        # A thread's private functions are registered into their own
        # scope by hand rather than through Scope.define, so this is a
        # separate code path and needs its own test.
        source = """
        thread worker {
            int func Math(x:int) {
                return x
            }
            on message(worker:thread, msg:int) {
                worker.reply(Math(msg))
            }
        }
        """
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="built-in math namespace"):
            semantic.analyze(program)


class TestTheErrorPointsAtTheDeclaration:
    def test_the_position_is_the_declaration_not_the_use(self, parser, semantic, errors):
        source = "\n\n\ntext Math = 'hello'\nlog(`${Math.sqrt(9.0)}`)"
        program = parser.parse(source)
        with pytest.raises(errors.CompileError) as excinfo:
            semantic.analyze(program)
        assert excinfo.value.line == 4, (
            f"reported line {excinfo.value.line}; the declaration is on "
            f"line 4 and the (formerly reported) use is on line 5")

    def test_the_message_says_why_rather_than_just_already_declared(self, parser, semantic, errors):
        # There is no earlier `Math` declaration anywhere in the
        # program, so "'Math' is already declared" -- the message every
        # other duplicate gets -- would send a reader looking for one
        # that does not exist. Same reasoning as `environment`.
        program = parser.parse("text Math = 'hello'")
        with pytest.raises(errors.CompileError) as excinfo:
            semantic.analyze(program)
        assert "already declared" not in str(excinfo.value)
        assert "Math.sqrt" in str(excinfo.value)


class TestTheTypeNamespaceIsUntouched:
    """6.7: type names and value names are separate namespaces, and a
    type name is never read as a value -- so `struct Math` conflicts
    with nothing and stays legal. Rejecting it would be a change to the
    language, not a fix to this bug."""

    def test_a_struct_named_math_is_allowed(self, parser, semantic):
        source = """
        struct Math {
            n:int
        }
        Math m
        m.n = 1
        log(`${m.n}`)
        """
        program = parser.parse(source)
        semantic.analyze(program)

    def test_a_struct_named_math_does_not_shadow_the_namespace(self, parser, semantic):
        source = """
        struct Math {
            n:int
        }
        float x = Math.sqrt(9.0)
        """
        program = parser.parse(source)
        semantic.analyze(program)

    def test_an_enum_named_math_is_allowed(self, parser, semantic):
        source = """
        struct Circle {
            r:float
        }
        struct Square {
            side:float
        }
        enum Math = Circle, Square
        Circle c
        c.r = 1.0
        Math m = c
        """
        program = parser.parse(source)
        semantic.analyze(program)

    def test_a_struct_field_named_math_is_allowed(self, parser, semantic):
        # A field lives in its struct's own namespace, not the value
        # namespace, and is only ever reached through a receiver.
        source = """
        struct Holder {
            Math:int
        }
        Holder h
        h.Math = 1
        log(`${h.Math}`)
        """
        program = parser.parse(source)
        semantic.analyze(program)


class TestBareMath:
    def test_a_bare_math_reference_names_the_namespace(self, parser, semantic, errors):
        # It used to say "unknown variable 'Math'", which is false --
        # Math is not unknown, it is a namespace. `environment` has
        # said the true thing since #71.
        program = parser.parse("log(`${Math}`)")
        with pytest.raises(errors.CompileError, match="must be used as Math.NAME"):
            semantic.analyze(program)

    def test_assigning_to_bare_math_is_a_compile_error(self, parser, semantic, errors):
        program = parser.parse("Math = 5")
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)

    def test_calling_math_as_a_function_is_a_compile_error(self, parser, semantic, errors):
        program = parser.parse("log(`${Math(1)}`)")
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)
