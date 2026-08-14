"""claude.md #69: setTimeout/setInterval/clearTimeout/clearInterval.
Added because Festina otherwise has no way to schedule work after the
fact, the same gap JS's setTimeout/setInterval fill. See
festina/semantic.py's _infer_call (the setTimeout/setInterval branch)
and runtime/festina_runtime.h's doc comment for the full design.

Parser/semantic-level tests only -- see tests/test_codegen.py's
TestTimers for the real compile-and-run end-to-end coverage.
"""
import pytest


class TestTimerSignatures:
    """The callback can only be the bare name of an already-declared
    `void func name() { ... }` -- Festina has no first-class functions
    or closures, so this is checked structurally, not through the
    normal expression-typing path."""

    def test_set_timeout_parses_and_analyzes(self, parser, semantic):
        source = "void func onTimeout() {\n    log('fired')\n}\nsetTimeout(onTimeout, 1000)"
        program = parser.parse(source)
        semantic.analyze(program)

    def test_set_interval_parses_and_analyzes(self, parser, semantic):
        source = "void func onTick() {\n    log('tick')\n}\nsetInterval(onTick, 1000)"
        program = parser.parse(source)
        semantic.analyze(program)

    @pytest.mark.parametrize("name", ["setTimeout", "setInterval"])
    def test_returns_an_int_timer_id(self, parser, semantic, name):
        source = f"void func cb() {{\n    log('x')\n}}\nint id = {name}(cb, 1000)"
        program = parser.parse(source)
        semantic.analyze(program)

    @pytest.mark.parametrize("name", ["setTimeout", "setInterval"])
    def test_callback_must_be_a_bare_function_name(self, parser, semantic, errors, name):
        # Festina has no first-class functions/closures -- an arbitrary
        # expression (even one that happens to name a variable) is
        # rejected, not just quietly permitted then failing at codegen.
        source = f"int notAFunction = 5\n{name}(notAFunction, 1000)"
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="not a declared function"):
            semantic.analyze(program)

    @pytest.mark.parametrize("name", ["setTimeout", "setInterval"])
    def test_callback_must_be_a_declared_function(self, parser, semantic, errors, name):
        program = parser.parse(f"{name}(neverDeclared, 1000)")
        with pytest.raises(errors.CompileError, match="not a declared function"):
            semantic.analyze(program)

    @pytest.mark.parametrize("name", ["setTimeout", "setInterval"])
    def test_callback_cannot_take_parameters(self, parser, semantic, errors, name):
        source = f"void func cb(a:int) {{\n    log(a)\n}}\n{name}(cb, 1000)"
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="must take no parameters"):
            semantic.analyze(program)

    @pytest.mark.parametrize("name", ["setTimeout", "setInterval"])
    def test_callback_cannot_return_a_value(self, parser, semantic, errors, name):
        source = f"int func cb() {{\n    return 1\n}}\n{name}(cb, 1000)"
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="must take no parameters"):
            semantic.analyze(program)

    @pytest.mark.parametrize("name", ["setTimeout", "setInterval"])
    def test_delay_must_be_int(self, parser, semantic, errors, name):
        source = f"void func cb() {{\n    log('x')\n}}\n{name}(cb, 1.5)"
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="delay argument must be an int"):
            semantic.analyze(program)

    @pytest.mark.parametrize("name", ["setTimeout", "setInterval"])
    def test_wrong_argument_count_is_a_compile_error(self, parser, semantic, errors, name):
        source = f"void func cb() {{\n    log('x')\n}}\n{name}(cb, 1000, 2000)"
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match=str(2)):
            semantic.analyze(program)


class TestClearTimer:
    """clearTimeout/clearInterval -- both take a single int timer id and
    return nothing; neither is checked against which function actually
    created it (matching JS, where clearTimeout()/clearInterval() are
    themselves interchangeable and never throw on an unknown id)."""

    @pytest.mark.parametrize("name", ["clearTimeout", "clearInterval"])
    def test_parses_and_analyzes(self, parser, semantic, name):
        program = parser.parse(f"{name}(1)")
        semantic.analyze(program)

    @pytest.mark.parametrize("name", ["clearTimeout", "clearInterval"])
    def test_argument_must_be_int(self, parser, semantic, errors, name):
        program = parser.parse(f"{name}('not an id')")
        with pytest.raises(errors.CompileError, match="must be an int"):
            semantic.analyze(program)

    @pytest.mark.parametrize("name", ["clearTimeout", "clearInterval"])
    def test_wrong_argument_count_is_a_compile_error(self, parser, semantic, errors, name):
        program = parser.parse(f"{name}(1, 2)")
        with pytest.raises(errors.CompileError, match=str(1)):
            semantic.analyze(program)
