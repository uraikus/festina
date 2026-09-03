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


class TestThrowUnwindsIntermediateFrames:
    """claude.md #236: a throw reached THROUGH a function that merely
    calls something which eventually throws -- no try, no throw of its
    own -- releases that function's locals on the way to the catching
    try. claude.md #157 documented this as the mechanism's one leak
    (longjmp skips every intermediate frame's scope-exit code); every
    managed local is registered on the runtime's per-thread cleanup
    stack as it is bound now (codegen.py's _track_local) and
    festina_throw releases the entries above the catching frame. The
    leak-freedom itself is measured by tests/stress/throw_unwind_churn.f
    under ASan (scripts/leak_stress.sh); these pin the behaviour around
    it and the IR shape."""

    def _ir(self, parser, semantic, codegen, source):
        program = parser.parse(source)
        gen = codegen.CodeGen(semantic.analyze(program), host_platform="linux")
        return gen.generate(program)

    def test_a_program_without_try_registers_nothing(self, parser, semantic, codegen):
        # No try anywhere: a throw is fail(), there is nothing to unwind
        # to, and the IR must be exactly what it was -- no push, no pop,
        # no unwind functions, for a program that never pays for them.
        ir = self._ir(parser, semantic, codegen, """
        void func f() {
            text s = 'x'
            arr[int] xs = [1]
            throw s
        }
        f()
        """)
        assert "@__festina_unwind_" not in ir
        assert "call void @festina_cleanup_push" not in ir
        assert "call void @festina_cleanup_pop" not in ir

    def test_a_program_with_try_registers_every_tracked_local(self, parser, semantic, codegen):
        ir = self._ir(parser, semantic, codegen, """
        struct P { name:text }
        void func g() { throw 'boom' }
        void func f() {
            text s = 'x'
            arr[int] xs = [1]
            map[int] m = {'k': 1}
            P p
            p.name = s
            arr[P] ps = [p]
            g()
        }
        try { f() } catch (e:text) { log(e) }
        """)
        pushes = [l for l in ir.splitlines() if "call void @festina_cleanup_push(ptr %" in l]
        # s, xs, m, p (escapes into ps), ps -- one registration each --
        # plus the catch variable `e` itself, a tracked text local of
        # the catch body
        assert len(pushes) == 6, pushes
        assert all("@__festina_unwind_" in l for l in pushes)
        # every registration names a generated unwind function that
        # takes the slot, and every ordinary exit pops what it
        # releases (one call per scope exit, counted)
        assert ir.count("define void @__festina_unwind_") >= 3
        assert "call void @festina_cleanup_pop_n(i64 " in ir
        # and the throw itself no longer frees anything inline: the
        # runtime does, from the stack
        g_body = ir[ir.index("define void @g"):]
        g_body = g_body[:g_body.index("\n}\n")]
        assert "festina_throw" in g_body

    def test_intermediate_frames_locals_do_not_disturb_the_survivors(self, compile_and_run):
        # The observable half of the fix: everything declared BEFORE
        # the try, at every level, is intact afterwards, and the
        # program keeps running -- through three frames, a loop-body
        # local, and an escaping parameter.
        source = """
        struct P { id:int  name:text }
        arr[text] trail = []
        void func deepest(i:int) {
            text t = `deep ${i}`
            throw t
        }
        void func middle(i:int) {
            text s = `mid ${i}`
            arr[int] xs = [i, i + 1]
            map[text] m = {'k': s}
            P p
            p.id = i
            p.name = s
            arr[P] ps = [p]
            int k = 0
            while k < 3 {
                text inner = `loop ${k}`
                if k == 2 { deepest(i) }
                k = k + 1
            }
            trail.push('unreachable')
        }
        void func outer(held:arr[int], i:int) {
            held = [i]
            text o = `outer ${i}`
            middle(i)
            trail.push('unreachable')
        }
        text before = 'kept'
        arr[int] keep = [9, 8]
        int i = 0
        while i < 3 {
            try {
                outer([1, 2, 3], i)
            } catch (e:text) {
                trail.push(e)
            }
            i = i + 1
        }
        log(trail.join(','))
        log(before)
        log(keep.length)
        """
        result = compile_and_run(source)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.splitlines() == ["deep 0,deep 1,deep 2", "kept", "2"]

    def test_a_rethrow_from_an_intermediate_catch_crosses_both_frames(self, compile_and_run):
        source = """
        void func deepest() { throw 'inner' }
        void func rethrower() {
            text a = 'a'
            try {
                text b = 'b'
                deepest()
            } catch (e:text) {
                text c = `re-${e}`
                throw c
            }
            log('unreachable')
        }
        try { rethrower() } catch (e:text) { log(e) }
        log('done')
        """
        result = compile_and_run(source)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.splitlines() == ["re-inner", "done"]

    def test_a_json_failure_two_frames_down_is_caught_and_the_program_continues(
            self, compile_and_run):
        source = """
        struct Person { id:int  name:text }
        void func parses(src:text) {
            text label = `parsing ${src}`
            Person who = src.toStruct(Person)
            log(who.name)
        }
        void func via(i:int) {
            arr[text] scratch = [`${i}`]
            parses(`{"id": ${i}, "name": ${i}}`)
        }
        try { via(1) } catch (e:text) { log('caught') }
        via(2)
        """
        result = compile_and_run(source)
        # the second call is uncaught: it must behave exactly like
        # fail() -- after the first was caught cleanly
        assert result.returncode == 1
        assert result.stdout.splitlines() == ["caught"]
        assert result.stderr.strip().startswith("fail:")

    def test_the_stack_stays_balanced_across_many_ordinary_calls(self, compile_and_run):
        # Every push has its pop on the non-throwing path: if it didn't,
        # 20000 calls would leave 60000 stale entries whose slots are
        # long gone, and the throw at the end would release through
        # them -- a crash, not a caught message.
        source = """
        struct P { name:text }
        void func quiet(i:int) {
            text s = `q ${i}`
            arr[int] xs = [i]
            P p
            p.name = s
            arr[P] ps = [p]
            if i < 0 { throw s }
        }
        int i = 0
        while i < 20000 {
            quiet(i)
            i = i + 1
        }
        try { quiet(-1) } catch (e:text) { log(e) }
        """
        result = compile_and_run(source)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip() == "q -1"

    def test_a_worker_thread_unwinds_on_its_own_stack(self, compile_and_run):
        # The cleanup stack is per thread: a throw inside a worker's own
        # handler, caught by a try in that same worker, releases only
        # that worker's locals, while main is mid-way through its own
        # tracked locals.
        source = """
        int got = 0
        on message(w:thread, msg:text) {
            log(msg)
            got = got + 1
            if got == 3 { close(0) }
        }
        thread worker {
            void func deep(i:int) {
                text t = `w ${i}`
                throw t
            }
            on message(w:thread, msg:int) {
                text mine = 'worker-local'
                try { deep(msg) } catch (e:text) { postMessage(e) }
            }
        }
        text keep = 'main-local'
        worker.postMessage(1)
        worker.postMessage(2)
        worker.postMessage(3)
        """
        result = compile_and_run(source)
        assert result.returncode == 0, result.stdout + result.stderr
        assert sorted(result.stdout.split()) == ["1", "2", "3", "w", "w", "w"]
