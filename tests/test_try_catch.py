"""claude.md #157: try { } catch (name:text) { } / throw <expr>.

Parser/semantic coverage (grammar, the catch-variable type requirement,
scoping) and real compile-and-run coverage (catching, uncaught-behaves-
like-fail, nesting/rethrow, return/break/continue crossing a try/catch,
and the memory-safety fixes a direct Valgrind run against this feature
actually caught during development -- see that class's own docstring)
live together in this one file, since both halves are small.
"""
import pytest


class TestParsing:
    def test_try_catch_parses(self, parser):
        parser.parse("""
        try {
            log('x')
        } catch (e:text) {
            log(e)
        }
        """)

    def test_throw_parses_as_a_statement(self, parser):
        parser.parse("throw 'boom'")

    def test_throw_accepts_any_expression_coerced_to_text(self, parser, semantic):
        # claude.md #157: matches fail()/log()'s own implicit toText
        # (claude.md #35) -- no restriction beyond "it's a valid
        # expression".
        program = parser.parse("throw 5")
        semantic.analyze(program)

    def test_catch_requires_a_type_annotation_of_text(self, parser):
        with pytest.raises(Exception, match="always text"):
            parser.parse("""
            try {
                log('x')
            } catch (e:int) {
                log(e)
            }
            """)

    def test_try_without_catch_is_a_parse_error(self, parser):
        with pytest.raises(Exception):
            parser.parse("try { log('x') }")

    def test_catch_variable_is_scoped_to_the_catch_body_only(self, parser, semantic, errors):
        program = parser.parse("""
        try {
            log('x')
        } catch (e:text) {
            log(e)
        }
        log(e)
        """)
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)


class TestRuntimeBehavior:
    """claude.md #157: real compile-and-run coverage, including the two
    real bugs a direct test against this exact feature caught during
    development (documented in claude.md #157 and in
    runtime/festina_runtime.c's own try/catch/throw comment) -- neither
    was found by reasoning alone:

    1. A first setjmp/longjmp design called setjmp inside a small
       runtime helper function that then returned 0 back to generated
       code -- silently broken (the catch simply never fired, or the
       process crashed) because setjmp only captures a jump target
       valid while its OWN calling function's frame is still live, and
       that helper's frame was long gone by the time a later throw
       tried to jump back into it. Fixed by emitting the setjmp call
       directly in the function containing the try statement.
    2. ThrowStmt's own cleanup (freeing every local active in the
       throwing function, so a throw caught in the SAME function never
       leaks) initially freed a _TryFrameMarker entry too, which
       popped the very catch frame the throw was about to target --
       turning a caught throw into an uncaught one. Fixed by having
       that one cleanup call skip _TryFrameMarker entries (the runtime
       itself pops exactly the frame it unwinds to, at the moment of
       the actual jump).
    3. A throw of a bare local text identifier hitting a real
       use-after-free -- text_val aliased the SAME buffer
       _emit_free_active_locals was about to free for the try body's
       own scope-exit. Fixed the same way Return's own identical text
       branch already was: an explicit festina_text_own copy before
       freeing anything.
    """

    def test_catch_runs_when_the_try_body_throws(self, compile_and_run):
        source = """
        try {
            log('in try')
            throw 'boom'
            log('unreachable')
        } catch (e:text) {
            log(`caught: ${e}`)
        }
        log('after')
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["in try", "caught: boom", "after"]

    def test_catch_does_not_run_when_nothing_throws(self, compile_and_run):
        source = """
        try {
            log('in try')
        } catch (e:text) {
            log('unreachable catch')
        }
        log('after')
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["in try", "after"]

    def test_throw_from_a_called_function_is_caught(self, compile_and_run):
        source = """
        void func risky(x:int) {
            if (x < 0) {
                throw `negative: ${x}`
            }
            log(x)
        }
        try {
            risky(5)
            risky(-1)
            log('unreachable')
        } catch (e:text) {
            log(`caught: ${e}`)
        }
        log('after')
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["5", "caught: negative: -1", "after"]

    def test_uncaught_throw_behaves_exactly_like_fail(self, compile_and_run):
        result = compile_and_run("""
        log('before')
        throw 'boom'
        log('after')
        """)
        assert result.returncode == 1
        assert result.stdout.strip() == "before"
        assert "boom" in result.stderr

    def test_nested_try_catch_and_rethrow(self, compile_and_run):
        source = """
        try {
            try {
                throw 'inner'
            } catch (e:text) {
                log(`inner caught: ${e}`)
                throw `rethrown: ${e}`
            }
        } catch (e:text) {
            log(`outer caught: ${e}`)
        }
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == [
            "inner caught: inner", "outer caught: rethrown: inner",
        ]

    def test_return_from_inside_try_and_catch(self, compile_and_run):
        source = """
        int func f(x:int) {
            try {
                if (x < 0) { throw 'neg' }
                return x * 2
            } catch (e:text) {
                return -1
            }
        }
        log(f(5))
        log(f(-5))
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["10", "-1"]

    def test_break_and_continue_cross_a_try_catch_inside_a_loop(self, compile_and_run):
        source = """
        int i = 0
        while (i < 5) {
            i = i + 1
            try {
                if (i == 2) { continue }
                if (i == 4) { throw 'four' }
                log(`loop ${i}`)
            } catch (e:text) {
                log(`loop caught: ${e}`)
                continue
            }
        }
        log('done')
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == [
            "loop 1", "loop 3", "loop caught: four", "loop 5", "done",
        ]

    def test_thrown_non_text_value_coerces_to_text(self, compile_and_run):
        source = """
        try {
            throw 42
        } catch (e:text) {
            log(e)
        }
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "42"

    def test_locals_declared_before_a_direct_throw_are_not_leaked(self, compile_and_run):
        # claude.md #157 bug #2/#3 above -- this exact shape (a struct
        # with an arr[T] field, plus a bare text local, both declared
        # before a throw with nothing in between) is what a direct
        # Valgrind run against this feature caught as "definitely
        # lost" before the fix. Not itself a leak check (this suite
        # doesn't run under Valgrind), but pins the OBSERVABLE
        # behavior the fix depends on: the bare text local is thrown
        # by VALUE, not corrupted, meaning it survived being read
        # AFTER (in program order) the point where it also gets freed.
        source = """
        struct Bag { xs:arr[int] }
        try {
            Bag b
            b.xs = [1, 2, 3]
            text s = 'hello direct'
            throw s
            log('unreachable')
        } catch (e:text) {
            log(`caught: ${e}`)
        }
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "caught: hello direct"

    def test_a_value_escaping_only_inside_a_try_body_survives(self, compile_and_run):
        # claude.md #192: escape_analysis never walked try/catch bodies,
        # so a value assigned to an escaping target (here the global g)
        # only inside a try was judged non-escaping -- stack-allocated
        # and freed at scope exit while g still pointed into f's
        # reclaimed frame. Reading g after f returns crashed (a real
        # use-after-free, verified under ASan). g must now hold the live
        # array.
        source = """
        arr[int] g = []
        void func f() {
            arr[int] xs = [10, 20, 30]
            try { g = xs } catch (e:text) { log(e) }
        }
        f()
        log(g.length)
        log(g[2])
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["3", "30"]

    def test_a_refcounted_local_before_a_throwing_try_is_not_double_freed(
            self, compile_and_run):
        # claude.md #192: a throw freed every active local down to the
        # function base, but a throw CAUGHT in the same function keeps
        # running afterward, and a local declared BEFORE the try is
        # still live then -- its ordinary scope-exit release runs after
        # the catch. Freeing it in the throw too double-freed it (glibc
        # aborts with "double free detected"). The throw now frees only
        # down to the nearest enclosing try. `xs` must survive to be read
        # after the try, and the program must exit cleanly.
        source = """
        void func f() {
            arr[int] xs = [1, 2, 3]
            try { throw 'boom' } catch (e:text) { log(e) }
            log(xs[0])
        }
        f()
        log('ok')
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["boom", "1", "ok"]

    def test_a_value_escaping_inside_a_catch_body_survives(self, compile_and_run):
        # The catch-body counterpart of the above -- the escaping
        # assignment happens on the throwing path.
        source = """
        arr[int] g = []
        void func f() {
            arr[int] xs = [7, 8, 9]
            try { throw 'boom' } catch (e:text) { g = xs }
        }
        f()
        log(g.length)
        log(g[0])
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["3", "7"]
