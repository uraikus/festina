"""Code generation -- claude.md #47 (executable generation), plus the
runtime-facing halves of #7/#8 (entry point + startup), #26 (arrays),
#28-34 (automatic SQLite schema sync, sqlite() queries), #41/#42
(log/fail), #45 (string interpolation).

Two kinds of tests here:

- Tests needing no C toolchain at all -- either they only check that
  festina.codegen raises a CompileError (nothing left actually needs
  this treatment; every claude.md construct this compiler targets now
  generates real code), or, like TestUnrecognizedEventName below, they
  only inspect the generated IR text directly.
- End-to-end tests actually compile a Festina program to a native
  executable (via the `compile_and_run` fixture) and check its real
  stdout/exit code/festina.sqlite -- these skip cleanly if no C compiler
  is on PATH, since that's an environment limitation, not a missing
  Festina feature.
"""
import os
import shutil
import sqlite3
import subprocess
import time
import wave

import pytest

_EXAMPLES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples")


# ---- no C toolchain needed -- IR-text-only checks ----

class TestUnrecognizedEventName:
    def _generate(self, parser, semantic, codegen, source, filename="main.f"):
        program = parser.parse(source, filename=filename)
        analyzed = semantic.analyze(program, filename=filename)
        return codegen.generate_ir(program, analyzed, filename=filename)

    def test_unrecognized_event_name_still_compiles_but_is_never_called(self, parser, semantic, codegen):
        # claude.md #40 only ever shows "click" and "mouse" -- any other
        # name still compiles (it's checked like any other code) but
        # there's no event source for it, so it's simply dead code, not
        # a compile error. See TestGraphics for the same point end to
        # end (the declared handler never fires).
        source = "on somethingElse(a:int) {\n    log(a)\n}"
        ir = self._generate(parser, semantic, codegen, source)
        assert "@__festina_on_somethingElse" in ir


# ---- end-to-end: real compiled, real executed programs ----

class TestArithmeticAndControlFlow:
    """claude.md #14-16, #18-20, #23-24: functions, expressions, if/else,
    ternary all produce correct runtime behavior, not just valid IR."""

    def test_function_call_and_arithmetic(self, compile_and_run):
        source = """
        int func add(a:int, b:int) {
            return a + b
        }
        log(add(2, 3))
        log(add(10, -4))
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["5", "6"]

    def test_if_else_branches(self, compile_and_run):
        source = """
        int func classify(n:int) {
            if n > 0 {
                return 1
            } else {
                return -1
            }
        }
        log(classify(5))
        log(classify(-5))
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["1", "-1"]

    def test_ternary_result(self, compile_and_run):
        source = """
        int x = 7
        text label = x > 5 ? 'big' : 'small'
        log(label)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "big"

    def test_logical_and_or_short_circuit(self, compile_and_run):
        # claude.md doesn't spell out short-circuit evaluation explicitly,
        # but it's the JavaScript-familiar behavior claude.md #45 asks for
        # ("Festina should retain familiar JavaScript conventions").
        source = """
        bool func sideEffect(tag:text) {
            log(tag)
            return true
        }
        bool r1 = false && sideEffect('should-not-print-1')
        bool r2 = true || sideEffect('should-not-print-2')
        log(r1)
        log(r2)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["false", "true"]

    def test_float_and_bool_and_text_log(self, compile_and_run):
        source = """
        float pi = 3.5
        bool enabled = true
        text name = 'Festina'
        log(pi)
        log(enabled)
        log(name)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["3.5", "true", "Festina"]


class TestStrings:
    """claude.md #9, #45: template string interpolation."""

    def test_template_interpolation(self, compile_and_run):
        source = """
        text func greet(name:text) {
            return `Hello, ${name}!`
        }
        log(greet('World'))
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "Hello, World!"

    def test_multiple_interpolations(self, compile_and_run):
        source = """
        int x = 3
        int y = 4
        log(`(${x}, ${y})`)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "(3, 4)"


class TestBlob:
    """claude.md #36: blob. Regression coverage for two real bugs a
    spec-compliance pass found: claude.md's own only worked example
    ("blob data = 'path/to/file'") failed semantic analysis outright
    (a string literal infers as `text`, and blob/text were fully
    incompatible with no exception -- meaning blob could never
    actually hold a value at all, since nothing else in the language
    constructs one either), and log() on the one blob value that
    *could* somehow exist crashed the compiler itself with a bare
    Python KeyError (blob passed the "is this a PrimitiveType" check
    but had no entry in log()'s dispatch dict) rather than compiling
    or raising a clean CompileError -- previously unreachable in
    practice for the same reason, but a real crash risk once blob
    became constructible."""

    def test_blob_declaration_and_log_match_the_spec_example(self, compile_and_run):
        result = compile_and_run("blob data = 'path/to/file'\nlog(data)")
        assert result.returncode == 0
        assert result.stdout.strip() == "path/to/file"

    def test_blob_and_text_equality(self, compile_and_run):
        source = (
            "blob data = 'hello'\n"
            "text t = 'hello'\n"
            "log(data == t)\n"
            "log(data == 'nope')\n"
        )
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["true", "false"]


class TestStructs:
    """claude.md #27: structs are native in-memory objects with typed,
    assignable fields."""

    def test_struct_field_assignment_and_read(self, compile_and_run):
        source = """
        struct Point {
            x:int
            y:int
        }
        Point p
        p.x = 3
        p.y = 4
        log(p.x + p.y)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "7"

    def test_struct_passed_to_function(self, compile_and_run):
        source = """
        struct User {
            id:int
            name:text
        }
        int func idOf(u:User) {
            return u.id
        }
        User user
        user.id = 42
        user.name = 'Patrick'
        log(idOf(user))
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "42"

    def test_struct_returned_by_value_from_function(self, compile_and_run):
        # Regression test: struct backing storage used to be a stack
        # alloca even for locals, so returning one handed the caller a
        # pointer into a popped stack frame -- verified to silently print
        # garbage before the fix (calloc'd storage instead; see
        # festina/codegen.py's module docstring).
        source = """
        struct Point {
            x:int
            y:int
        }
        Point func origin() {
            Point p
            p.x = 0
            p.y = 0
            return p
        }
        Point func makePoint(a:int, b:int) {
            Point p
            p.x = a
            p.y = b
            return p
        }
        Point o = origin()
        log(o.x)
        log(o.y)
        Point q = makePoint(7, 8)
        log(q.x)
        log(q.y)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["0", "0", "7", "8"]

    def test_local_struct_fields_default_to_zero(self, compile_and_run):
        # calloc, not malloc, for struct backing storage -- a local
        # struct's unset fields should read as zero, same as a global
        # struct's (`zeroinitializer`), not garbage.
        source = """
        struct Counters {
            hits:int
            misses:int
        }
        Counters c
        log(c.hits)
        log(c.misses)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["0", "0"]


class TestArrays:
    """claude.md #26: arr[T] with elements sized/typed at compile time,
    from a literal, read and written by index. `.length` (#63) and
    for/while loops (#60/#61) are covered in TestArrayLength and
    TestLoops below. No growth and no bounds checking -- claude.md
    doesn't specify either (see the module docstring in
    festina/codegen.py), so they aren't implemented."""

    def test_literal_index_read(self, compile_and_run):
        result = compile_and_run("arr[int] nums = [10, 20, 30]\nlog(nums[0])\nlog(nums[2])")
        assert result.stdout.splitlines() == ["10", "30"]

    def test_indexed_write(self, compile_and_run):
        source = """
        arr[int] nums = [1, 2, 3]
        nums[1] = 99
        log(nums[1])
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "99"

    def test_index_by_variable_expression(self, compile_and_run):
        source = """
        arr[int] nums = [5, 6, 7]
        int i = 2
        log(nums[i])
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "7"

    def test_array_of_floats_and_text(self, compile_and_run):
        source = """
        arr[float] prices = [1.5, 2.5, 3.0]
        arr[text] names = ['a', 'b', 'c']
        log(prices[1])
        log(names[2])
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["2.5", "c"]

    def test_declaration_without_initializer(self, compile_and_run):
        # claude.md #26's own examples (`arr[int] numbers`) have no
        # initializer.
        result = compile_and_run("arr[int] empty\nlog('declared fine')")
        assert result.returncode == 0
        assert result.stdout.strip() == "declared fine"

    def test_nested_arrays(self, compile_and_run):
        # claude.md #26: "Nested arrays are valid: arr[arr[int]] matrix".
        source = """
        arr[arr[int]] matrix = [[1, 2], [3, 4]]
        log(matrix[0][1])
        log(matrix[1][0])
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["2", "3"]

    def test_array_as_function_parameter_and_return_value(self, compile_and_run):
        source = """
        int func sum3(nums:arr[int]) {
            return nums[0] + nums[1] + nums[2]
        }
        arr[int] func makeRange(a:int, b:int) {
            return [a, b, a + b]
        }
        log(sum3([1, 2, 3]))
        arr[int] r = makeRange(3, 4)
        log(r[0])
        log(r[1])
        log(r[2])
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["6", "3", "4", "7"]

    def test_array_of_structs(self, compile_and_run):
        source = """
        struct Point {
            x:int
            y:int
        }
        Point p1
        p1.x = 1
        p1.y = 2
        Point p2
        p2.x = 3
        p2.y = 4
        arr[Point] points = [p1, p2]
        log(points[0].x)
        log(points[1].y)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["1", "4"]


class TestArrayLength:
    """claude.md #63: every array has a built-in read-only `.length`."""

    def test_length_of_literal(self, compile_and_run):
        result = compile_and_run("arr[int] values = [1, 2, 3]\nlog(values.length)")
        assert result.stdout.strip() == "3"

    def test_length_of_empty_array(self, compile_and_run):
        result = compile_and_run("arr[int] values = []\nlog(values.length)")
        assert result.stdout.strip() == "0"

    def test_length_updates_after_reassignment(self, compile_and_run):
        source = """
        arr[int] values = [1, 2]
        values = [1, 2, 3, 4, 5]
        log(values.length)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "5"


class TestMaps:
    """claude.md #72: map[T] -- { key: value, ... } literals, indexed
    get/set, .forEach()."""

    def test_literal_with_string_and_variable_keys(self, compile_and_run):
        source = """
        text npc2Id = 'npc2'
        map[int] npcHealths = {'npc1': 10, npc2Id: 15}
        log(npcHealths['npc1'])
        log(npcHealths[npc2Id])
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["10", "15"]

    def test_map_of_text_values(self, compile_and_run):
        source = """
        text npc2Id = 'npc2'
        map[text] npcNames = {'npc1': 'jim', npc2Id: 'john'}
        log(npcNames['npc1'])
        log(npcNames[npc2Id])
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["jim", "john"]

    def test_missing_key_returns_null(self, compile_and_run):
        # claude.md #72: "If the key is not present in the map, the
        # result is null" -- text's null already prints as an empty
        # line (see TestRegex's identical match()-with-no-match test).
        source = """
        map[text] m = {'a': 'x'}
        log('before')
        log(m['missing'])
        log('after')
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["before", "", "after"]

    def test_missing_key_on_int_map_returns_the_int_null_sentinel(self, compile_and_run):
        # Same null representation int already uses everywhere else
        # (e.g. division by zero -- claude.md #57) -- not a special
        # case invented for maps.
        div_by_zero = compile_and_run("int a = 1\nint b = 0\nlog(a / b)")
        missing_key = compile_and_run("map[int] m = {'a': 1}\nlog(m['missing'])")
        assert missing_key.stdout == div_by_zero.stdout

    def test_empty_map_literal(self, compile_and_run):
        result = compile_and_run("map[int] m = {}\nlog(m['x'])")
        assert result.returncode == 0

    def test_duplicate_key_in_a_literal_last_one_wins(self, compile_and_run):
        # Two *literal* string keys colliding (`{'a': 1, 'a': 2}`) is now
        # a compile-time error instead (see tests/test_maps.py's
        # TestMapLiteral -- knowable for free, almost always a typo).
        # "last value wins" for a genuine runtime collision still holds
        # and is still exercised here, via a variable key that happens
        # to equal an earlier literal key only at runtime.
        source = "text k = 'a'\nmap[int] m = {'a': 1, k: 2}\nlog(m['a'])"
        result = compile_and_run(source)
        assert result.stdout.strip() == "2"

    def test_write_adds_a_new_key(self, compile_and_run):
        source = """
        map[int] m = {'a': 1}
        m['b'] = 2
        log(m['a'])
        log(m['b'])
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["1", "2"]

    def test_write_replaces_an_existing_key(self, compile_and_run):
        source = """
        map[int] m = {'a': 1}
        m['a'] = 30
        log(m['a'])
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "30"

    def test_write_with_a_variable_key(self, compile_and_run):
        source = """
        text npc2Id = 'npc2'
        map[int] npcHealths = {'npc1': 10, npc2Id: 15}
        npcHealths[npc2Id] = 30
        log(npcHealths[npc2Id])
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "30"

    def test_zero_initialized_map_variable_behaves_as_empty(self, compile_and_run):
        source = """
        map[text] m
        log(m['x'])
        m['x'] = 'now set'
        log(m['x'])
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["", "now set"]

    def test_map_as_a_struct_field(self, compile_and_run):
        source = """
        struct Holder {
            scores:map[int]
        }
        Holder h
        h.scores = {'a': 1}
        h.scores['b'] = 2
        log(h.scores['a'])
        log(h.scores['b'])
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["1", "2"]

    def test_map_assignment_on_a_non_addressable_target_is_a_clear_error(self, parser, semantic, codegen, errors):
        source = """
        map[int] func getMap() {
            map[int] m = {'a': 1}
            return m
        }
        getMap()['a'] = 5
        """
        program = parser.parse(source)
        analyzed = semantic.analyze(program)
        with pytest.raises(errors.CompileError, match="plain variable or field"):
            codegen.generate_ir(program, analyzed)

    def test_forEach_visits_every_entry(self, compile_and_run):
        source = """
        void func logHealth(h:int, key:text) {
            log(`${key} ${h.toText()}`)
        }
        map[int] npcHealths = {'npc1': 10, 'npc2': 15}
        npcHealths.forEach(logHealth)
        """
        result = compile_and_run(source)
        assert sorted(result.stdout.splitlines()) == ["npc1 10", "npc2 15"]

    def test_forEach_with_a_float_valued_map(self, compile_and_run):
        # Exercises the .forEach() trampoline's float (double)
        # reinterpretation path specifically -- see
        # _emit_map_foreach_trampoline's own comment on why a real
        # trampoline is needed at all, not just for int.
        source = """
        void func logPrice(v:float, key:text) {
            log(`${key} ${v}`)
        }
        map[float] prices = {'apple': 1.5}
        prices.forEach(logPrice)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "apple 1.5"

    def test_forEach_with_a_bool_valued_map(self, compile_and_run):
        source = """
        void func logFlag(v:bool, key:text) {
            log(`${key} ${v}`)
        }
        map[bool] flags = {'ready': true}
        flags.forEach(logFlag)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "ready true"

    def test_forEach_with_a_struct_valued_map(self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        void func logPoint(v:Point, key:text) {
            log(`${key}: (${v.x},${v.y})`)
        }
        Point origin
        origin.x = 0
        origin.y = 0
        map[Point] points = {'origin': origin}
        points.forEach(logPoint)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "origin: (0,0)"


class TestLoops:
    """claude.md #60 (for loops), #61 (while loops), #66 (postfix ++/--)."""

    def test_for_loop_array_iteration_example(self, compile_and_run):
        # The exact worked example from claude.md #60.
        source = """
        arr[int] array = [10, 20, 30]
        for int x = 0, x < array.length, x++ {
            log(array[x])
        }
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["10", "20", "30"]

    def test_for_loop_counts_from_zero(self, compile_and_run):
        result = compile_and_run("for int x = 0, x < 5, x++ {\n    log(x)\n}")
        assert result.stdout.splitlines() == ["0", "1", "2", "3", "4"]

    def test_for_loop_with_decrement_update(self, compile_and_run):
        result = compile_and_run("for int x = 3, x > 0, x-- {\n    log(x)\n}")
        assert result.stdout.splitlines() == ["3", "2", "1"]

    def test_for_loop_body_never_runs_when_condition_starts_false(self, compile_and_run):
        source = "for int x = 0, x < 0, x++ {\n    log('never')\n}\nlog('after')"
        result = compile_and_run(source)
        assert result.stdout.strip() == "after"

    def test_for_loop_init_variable_does_not_leak_past_the_loop(self, compile_and_run):
        # claude.md #60: "The initialization variable is scoped to the
        # loop body" -- a same-named outer variable must be unaffected.
        source = """
        int x = 99
        for int x = 0, x < 3, x++ {
        }
        log(x)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "99"

    def test_nested_for_loops(self, compile_and_run):
        source = """
        for int i = 0, i < 2, i++ {
            for int j = 0, j < 2, j++ {
                log(i * 10 + j)
            }
        }
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["0", "1", "10", "11"]

    def test_while_loop_counts_up(self, compile_and_run):
        source = """
        int e = 0
        while e < 5 {
            log(e)
            e++
        }
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["0", "1", "2", "3", "4"]

    def test_while_loop_body_never_runs_when_condition_starts_false(self, compile_and_run):
        source = "int e = 10\nwhile e < 5 {\n    log('never')\n}\nlog('after')"
        result = compile_and_run(source)
        assert result.stdout.strip() == "after"

    def test_while_true_exits_via_return_inside_the_loop(self, compile_and_run):
        # `return` from the enclosing function still works as a way out
        # of an infinite loop's body too, same as before claude.md #73
        # added break/continue -- this isn't the only way out anymore
        # (see TestBreakAndContinue below), just still a valid one.
        source = """
        void func run() {
            int count = 0
            while true {
                log(count)
                count++
                if count == 3 {
                    return
                }
            }
        }
        run()
        log('done')
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["0", "1", "2", "done"]

    def test_postfix_increment_and_decrement(self, compile_and_run):
        source = """
        int i = 5
        i++
        log(i)
        i--
        i--
        log(i)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["6", "4"]

    def test_postfix_increment_returns_pre_increment_value(self, compile_and_run):
        # Standard postfix semantics: `i++` evaluates to the value *before*
        # incrementing.
        source = "int i = 5\nlog(i++)\nlog(i)"
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["5", "6"]

    def test_iterative_fibonacci(self, compile_and_run):
        # A real loop-driven computation, not just a counter -- exercises
        # for + array-free accumulation together.
        source = """
        int func fibIter(n:int) {
            int a = 0
            int b = 1
            for int i = 0, i < n, i++ {
                int next = a + b
                a = b
                b = next
            }
            return a
        }
        log(fibIter(10))
        log(fibIter(20))
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["55", "6765"]


class TestBreakAndContinue:
    """claude.md #73."""

    def test_break_stops_the_loop_immediately(self, compile_and_run):
        source = """
        for int i = 0, i < 10, i++ {
            if i == 5 {
                break
            }
            log(i)
        }
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["0", "1", "2", "3", "4"]

    def test_continue_skips_the_rest_of_that_iteration(self, compile_and_run):
        source = """
        for int i = 0, i < 5, i++ {
            if i % 2 == 0 {
                continue
            }
            log(i)
        }
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["1", "3"]

    def test_claude_mds_own_worked_example(self, compile_and_run):
        # claude.md #73's own example: "This logs 1, 3."
        source = """
        for int i = 0, i < 10, i++ {
            if i == 5 {
                break
            }
            if i % 2 == 0 {
                continue
            }
            log(i)
        }
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["1", "3"]

    def test_continue_in_a_for_loop_still_runs_the_update_expression(self, compile_and_run):
        # claude.md #73: "the update expression still runs before the
        # condition is checked again" -- if continue skipped straight to
        # the condition instead, i would never advance and this would
        # loop forever (or time out); it doesn't.
        source = """
        for int i = 0, i < 5, i++ {
            continue
        }
        log('done')
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "done"

    def test_break_in_a_while_loop(self, compile_and_run):
        source = """
        int i = 0
        while true {
            if i >= 3 {
                break
            }
            log(i)
            i++
        }
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["0", "1", "2"]

    def test_continue_in_a_while_loop(self, compile_and_run):
        source = """
        int i = 0
        while i < 5 {
            i++
            if i % 2 == 0 {
                continue
            }
            log(i)
        }
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["1", "3", "5"]

    def test_break_only_exits_the_innermost_loop(self, compile_and_run):
        source = """
        for int i = 0, i < 3, i++ {
            for int j = 0, j < 3, j++ {
                if j == 1 {
                    break
                }
                log(`${i},${j}`)
            }
        }
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["0,0", "1,0", "2,0"]

    def test_continue_only_affects_the_innermost_loop(self, compile_and_run):
        source = """
        for int i = 0, i < 2, i++ {
            for int j = 0, j < 3, j++ {
                if j == 1 {
                    continue
                }
                log(`${i},${j}`)
            }
        }
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["0,0", "0,2", "1,0", "1,2"]

    def test_statements_after_break_in_the_same_block_are_not_run(self, compile_and_run):
        source = """
        for int i = 0, i < 3, i++ {
            if i == 1 {
                break
                log('unreachable')
            }
            log(i)
        }
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["0"]


class TestAutomaticMemoryReclamation:
    """claude.md #74, stage 1: a local struct/arr[T]/map[T] declared
    directly in a function/event handler's own top-level body is freed
    automatically at every return, when tests/test_escape_analysis.py's
    find_escaping_names proves it never escapes. That module is tested
    exhaustively on its own (every syntactic escaping/non-escaping
    pattern, no C compiler needed) -- this class checks the other half:
    that the analysis's result actually gets wired into real generated
    IR (free() calls landing in exactly the right place) and, more
    importantly, that real compiled programs still produce correct
    output in both the freed and (still-leaking, unchanged) escaping
    cases."""

    def _ir(self, parser, semantic, codegen, source, filename="main.f"):
        program = parser.parse(source, filename=filename)
        analyzed = semantic.analyze(program, filename=filename)
        return codegen.generate_ir(program, analyzed, filename=filename)

    # ---- IR-level: no C compiler needed ----

    def test_non_escaping_struct_local_is_stack_allocated(self, parser, semantic, codegen):
        # claude.md #43/#74/#75: a struct local proven never to escape
        # its declaring function is now a real stack alloca, not a
        # calloc+free pair -- see _emit_stmt's own VarDecl comment for
        # why reusing the exact same escape-analysis proof is sound for
        # allocation, not just for freeing.
        source = """
        struct Point { x:int y:int }
        void func f() {
            Point p
            p.x = 1
            log(p.x)
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "%p.storage" in ir
        assert "alloca %struct.Point" in ir
        assert "store %struct.Point zeroinitializer" in ir
        assert "call ptr @calloc(" not in ir
        assert "call void @free(" not in ir

    def test_non_escaping_array_local_frees_its_data_pointer(self, parser, semantic, codegen):
        source = """
        void func f() {
            arr[int] a = [1, 2, 3]
            log(a[0])
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        # The free path reaches the data pointer via a direct GEP to
        # field 1 + load, matching how every other array data-pointer
        # read in codegen.py gets there -- not a load-the-whole-header
        # + extractvalue (see _emit_free_active_locals's own comment).
        assert "getelementptr %struct._FestinaArray, ptr" in ir
        assert "call void @free(" in ir

    def test_non_escaping_map_local_frees_its_entries_pointer(self, parser, semantic, codegen):
        # claude.md #74/#75: a map's entries buffer has its own nested
        # per-entry key allocation (see festina_map_set's own comment),
        # so freeing it goes through festina_map_free_entries -- which
        # frees each entry's key too -- not a plain @free(entries) that
        # would leak them (see _emit_free_active_locals's MapType
        # branch).
        source = """
        void func f() {
            map[int] m = {'a': 1}
            log(m['a'])
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "getelementptr %struct._FestinaMap, ptr" in ir
        assert "call void @festina_map_free_entries(" in ir

    def test_returned_struct_is_not_freed(self, parser, semantic, codegen):
        source = """
        struct Point { x:int y:int }
        Point func makePoint() {
            Point p
            p.x = 1
            return p
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "call void @free(" not in ir

    def test_struct_passed_to_a_non_retaining_function_is_now_stack_allocated(
            self, parser, semantic, codegen):
        # claude.md #74 stage 2 (interprocedural): takesPoint only reads
        # p.x (a Member.obj-safe use, same rule as any local's own
        # fields) -- its own parameter never escapes within its own
        # body, so escaping_params[takesPoint] = {} and f()'s own `p`,
        # passed there and never used any other way, is now provably
        # safe too. This is the exact case stage 1's own module
        # docstring called out as its stated limitation ("even if the
        # called function provably doesn't retain it") -- combined with
        # the stack-allocation swap (claude.md #43/#74/#75), f()'s own
        # `p` is now a stack alloca, not a calloc'd allocation at all.
        source = """
        struct Point { x:int y:int }
        void func takesPoint(p:Point) {
            log(p.x)
        }
        void func f() {
            Point p
            takesPoint(p)
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        f_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define void @f("))
        f_body = "\n".join(ir.splitlines()[f_start:])
        assert "alloca %struct.Point" in f_body
        assert "call ptr @calloc(" not in f_body

    def test_loop_local_struct_is_stack_allocated_and_reused_across_iterations(
            self, parser, semantic, codegen):
        # claude.md #43/#74/#75: the loop-body/break/continue-scoped
        # freeing machinery still applies to *when* a struct local's
        # storage is considered dead, even though "dead" no longer
        # means "call free() here" for a struct -- it means the very
        # next textual reach of the same VarDecl (the next iteration)
        # re-zeros the *same* stack address rather than allocating a
        # fresh one, since LLVM's alloca reserves one fixed slot for
        # the whole enclosing function regardless of which basic block
        # contains it. The alloca and its zeroinitializer store must
        # both be inside the loop body (so re-zeroing genuinely
        # happens every iteration, not just once before the loop), and
        # there must be no calloc/free anywhere in the function at all.
        source = """
        struct Point { x:int y:int }
        void func f() {
            for int i = 0, i < 3, i++ {
                Point p
                p.x = i
                log(p.x)
            }
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        lines = ir.splitlines()
        body_start = next(i for i, l in enumerate(lines) if l.strip().startswith("for.body"))
        body_end = next(i for i in range(body_start, len(lines)) if lines[i].strip().startswith("br label %for.update"))
        body_lines = lines[body_start:body_end]
        assert any("alloca %struct.Point" in l for l in body_lines)
        assert any("store %struct.Point zeroinitializer" in l for l in body_lines)
        assert "call ptr @calloc(" not in ir
        assert "call void @free(" not in ir

    def test_nested_if_declared_struct_is_stack_allocated(self, parser, semantic, codegen):
        source = """
        struct Point { x:int y:int }
        void func f(cond:bool) {
            if cond {
                Point p
                p.x = 1
                log(p.x)
            }
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "alloca %struct.Point" in ir
        assert "call ptr @calloc(" not in ir

    def test_struct_passed_to_a_retaining_function_is_still_not_freed(self, parser, semantic, codegen):
        # The mirror case: retains actually stores its own parameter
        # into a global (an unconditional hard escape, same rule as
        # ever) -- escaping_params[retains] = {0}, so f()'s own `p`,
        # passed at that same position, still correctly escapes too.
        # Interprocedural analysis only ever widens what's PROVEN safe;
        # it must never widen what's proven unsafe.
        source = """
        struct Point { x:int y:int }
        Point stash
        void func retains(p:Point) {
            stash = p
        }
        void func f() {
            Point p
            retains(p)
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "call void @free(" not in ir

    def test_struct_assigned_into_a_global_is_not_freed(self, parser, semantic, codegen):
        source = """
        struct Point { x:int y:int }
        Point g
        void func f() {
            Point p
            g = p
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "call void @free(" not in ir

    def test_reassigned_struct_is_not_freed(self, parser, semantic, codegen):
        source = """
        struct Point { x:int y:int }
        void func f() {
            Point p
            Point q
            p = q
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "call void @free(" not in ir

    def test_a_loop_local_array_is_freed_inside_the_loop_body(self, parser, semantic, codegen):
        # claude.md #74's nested-block extension: a loop-body-declared
        # non-escaping local is now freed at the end of *every*
        # iteration -- exactly one free() call, and it must be inside
        # the loop body's own block (part of the runtime back-edge
        # cycle), not just once after the loop as a whole exits. Uses
        # arr[int], not a struct -- since the stack-allocation swap
        # (claude.md #43/#74/#75), a non-escaping struct local no
        # longer goes through this free-scheduling machinery at all
        # (see test_a_loop_local_struct_is_reused_across_iterations_via_the_same_alloca
        # for the struct/stack-allocation equivalent of this same
        # shape); arr[T]'s data buffer still always calloc's/frees
        # regardless of escaping-ness (a dynamically-growing buffer
        # isn't safe to give a fixed-size alloca), so this is still the
        # right type to exercise the free-scheduling logic itself with.
        source = """
        void func f() {
            for int i = 0, i < 3, i++ {
                arr[int] p = [i]
                log(p[0])
            }
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        lines = ir.splitlines()
        body_start = next(i for i, l in enumerate(lines) if l.strip().startswith("for.body"))
        body_end = next(i for i in range(body_start, len(lines)) if lines[i].strip().startswith("br label %for.update"))
        body_lines = lines[body_start:body_end]
        assert sum("call void @free(" in l for l in body_lines) == 1
        assert ir.count("call void @free(") == 1

    def test_a_nested_if_declared_array_is_freed_at_the_ifs_own_end(self, parser, semantic, codegen):
        source = """
        void func f(cond:bool) {
            if cond {
                arr[int] p = [1]
                log(p[0])
            }
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "call void @free(" in ir

    def test_break_frees_the_loop_local_but_not_an_outer_scope_local(self, parser, semantic, codegen):
        source = """
        void func f() {
            arr[int] outer = [100]
            for int i = 0, i < 5, i++ {
                arr[int] inner = [i]
                if inner[0] == 2 {
                    break
                }
                log(inner[0])
            }
            log(outer[0])
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        lines = ir.splitlines()
        break_block = next(i for i, l in enumerate(lines) if l.strip().startswith("if.then"))
        break_block_end = next(i for i in range(break_block, len(lines)) if lines[i].strip().startswith("br label %for.end"))
        break_lines = lines[break_block:break_block_end]
        # Exactly one free() on the break path -- inner's, not outer's
        # (outer is declared outside the loop, merely used inside it).
        assert sum("call void @free(" in l for l in break_lines) == 1
        # outer is still freed exactly once overall, at the function's
        # own end (after the loop, whichever way it was exited).
        assert ir.count("call void @free(") == 3  # inner (break path) + inner (fall-through path) + outer

    def test_continue_frees_locals_declared_since_the_loop_body_began(self, parser, semantic, codegen):
        source = """
        void func f() {
            for int i = 0, i < 5, i++ {
                arr[int] p = [i]
                if p[0] % 2 == 0 {
                    continue
                }
                log(p[0])
            }
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        lines = ir.splitlines()
        continue_block = next(i for i, l in enumerate(lines) if l.strip().startswith("if.then"))
        continue_block_end = next(i for i in range(continue_block, len(lines)) if lines[i].strip().startswith("br label %for.update"))
        continue_lines = lines[continue_block:continue_block_end]
        assert sum("call void @free(" in l for l in continue_lines) == 1

    def test_nested_if_inside_a_loop_frees_correctly_on_both_the_break_and_fallthrough_paths(self, parser, semantic, codegen):
        source = """
        void func f() {
            for int i = 0, i < 5, i++ {
                arr[int] p = [i]
                if p[0] == 3 {
                    break
                }
                log(p[0])
            }
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        # One free() on the break path (p, via break's own free-before-
        # branch), one on the normal fall-through-to-next-iteration path
        # (p, via the loop body's own natural end) -- two total, both p,
        # never both taken on the same iteration.
        assert ir.count("call void @free(") == 2

    def test_early_return_before_the_declaration_has_no_free_on_that_path(self, parser, semantic, codegen):
        source = """
        void func f(cond:bool) {
            if cond {
                return
            }
            arr[int] p = [1]
            log(p[0])
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        lines = ir.splitlines()
        func_start = next(i for i, l in enumerate(lines) if l.startswith("define void @f("))
        func_end = next(i for i in range(func_start, len(lines)) if lines[i] == "}")
        func_lines = lines[func_start:func_end]
        # The very first `ret void` (the early-return path, before p is
        # declared) must not be preceded by a free() call anywhere
        # earlier in the function; the second (the fall-through path,
        # after p is declared) must be.
        ret_indices = [i for i, l in enumerate(func_lines) if l.strip() == "ret void"]
        assert len(ret_indices) == 2
        assert not any("call void @free(" in l for l in func_lines[:ret_indices[0] + 1])
        assert any("call void @free(" in l for l in func_lines[ret_indices[0] + 1:ret_indices[1] + 1])

    def test_event_handler_locals_are_analyzed_too(self, parser, semantic, codegen):
        source = """
        on click(x:int, y:int) {
            arr[int] p = [x]
            log(p[0])
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "call void @free(" in ir

    def test_event_handler_struct_local_is_stack_allocated_too(self, parser, semantic, codegen):
        source = """
        struct Point { x:int y:int }
        on click(x:int, y:int) {
            Point p
            p.x = x
            log(p.x)
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "alloca %struct.Point" in ir
        assert "call ptr @calloc(" not in ir

    # ---- end-to-end: real compiled programs, real output ----

    def test_non_escaping_struct_local_still_produces_correct_output(self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        void func f() {
            Point p
            p.x = 5
            p.y = 10
            log(p.x + p.y)
        }
        f()
        log('done')
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["15", "done"]

    def test_returning_a_struct_still_works_correctly(self, compile_and_run):
        # The exact shape of bug this stage must never reintroduce --
        # see security.md's original "returning a struct by value"
        # fix, and this session's own live demonstration of what
        # freeing an escaping local does to it.
        source = """
        struct Point { x:int y:int }
        Point func makePoint(a:int, b:int) {
            Point p
            p.x = a
            p.y = b
            return p
        }
        Point q1 = makePoint(10, 20)
        Point q2 = makePoint(999, 888)
        log(q1.x)
        log(q1.y)
        log(q2.x)
        log(q2.y)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["10", "20", "999", "888"]

    def test_recursive_function_with_a_non_escaping_struct_local_keeps_each_calls_own_value(
            self, compile_and_run):
        # The single most important correctness question the stack-
        # allocation swap (claude.md #43/#74/#75) raises: does each
        # recursive call really get its own, distinct stack slot for
        # p, or could a wrong assumption about LLVM's calling
        # convention let a deeper call's own p.x overwrite an
        # outstanding shallower call's? recur(n)'s own p.x must still
        # read back correctly *after* its own recursive call to
        # recur(n-1) returns -- if calls shared one slot, this would
        # read n-1's (or some other call's) value instead of n's own.
        source = """
        struct Point { x:int y:int }
        int func recur(n:int) {
            Point p
            p.x = n
            if n == 0 {
                return p.x
            }
            int inner = recur(n - 1)
            return p.x * 100 + inner
        }
        log(recur(3))
        log(recur(5))
        """
        result = compile_and_run(source)
        # recur(0)=0, recur(1)=100, recur(2)=300, recur(3)=600,
        # recur(4)=1000, recur(5)=1500 -- hand-derived, each building on
        # the previous: recur(n) = n*100 + recur(n-1).
        assert result.stdout.splitlines() == ["600", "1500"]

    def test_struct_assigned_into_a_global_keeps_its_value(self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        Point g
        void func f() {
            Point p
            p.x = 7
            g = p
        }
        f()
        log(g.x)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "7"

    def test_struct_passed_to_another_function_keeps_its_value(self, compile_and_run):
        # Since claude.md #74 stage 2, takesPoint's own read-only use of
        # its parameter (see test_struct_passed_to_a_non_retaining_function_is_now_freed)
        # means p is now provably safe throughout f() too -- p ends up
        # freed at the end of f(), but only *after* both reads (inside
        # takesPoint and f()'s own trailing log(p.x)) have already
        # happened, so the values here must still come out correct.
        source = """
        struct Point { x:int y:int }
        void func takesPoint(p:Point) {
            log(p.x)
        }
        void func f() {
            Point p
            p.x = 3
            takesPoint(p)
            log(p.x)
        }
        f()
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["3", "3"]

    def test_reassignment_keeps_the_new_values_valid(self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        void func f() {
            Point p
            p.x = 1
            Point q
            q.x = 2
            p = q
            log(p.x)
        }
        f()
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "2"

    def test_early_return_before_declaration_runs_correctly_on_both_paths(self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        void func f(cond:bool) {
            if cond {
                log('early')
                return
            }
            Point p
            p.x = 42
            log(p.x)
        }
        f(true)
        f(false)
        log('done')
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["early", "42", "done"]

    def test_non_escaping_array_and_map_locals_still_produce_correct_output(self, compile_and_run):
        source = """
        void func useArray() {
            arr[int] a = [1, 2, 3]
            log(a[0] + a[1] + a[2])
        }
        void func useMap() {
            map[int] m = {'a': 1, 'b': 2}
            log(m['a'] + m['b'])
        }
        useArray()
        useMap()
        log('done')
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["6", "3", "done"]

    def test_calling_a_freeing_function_many_times_does_not_crash(self, compile_and_run):
        # Not a memory-bound proof (see tests/CONTRACT.md for why an
        # RSS-based measurement isn't reliable against this compiler's
        # own O2 pipeline, which can eliminate an unobserved allocation
        # entirely) -- a basic robustness check that repeated alloc/free
        # of the same struct across many calls doesn't corrupt anything
        # an allocator would notice (a bad free()/double free is exactly
        # the kind of thing that tends to crash loudly under real
        # allocator bookkeeping, especially across enough iterations to
        # exercise its free-list reuse).
        source = """
        struct Point { x:int y:int }
        void func useLocal(n:int) {
            Point p
            p.x = n
            p.y = n * 2
        }
        int total = 0
        for int i = 0, i < 50000, i++ {
            useLocal(i)
            total = total + 1
        }
        log(total)
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.strip() == "50000"

    def test_nested_if_declared_struct_produces_correct_output(self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        void func f(cond:bool) {
            if cond {
                Point p
                p.x = 5
                log(p.x)
            }
            log('after')
        }
        f(true)
        f(false)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["5", "after", "after"]

    def test_loop_local_struct_produces_correct_output_every_iteration(self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        void func f() {
            for int i = 0, i < 5, i++ {
                Point p
                p.x = i * i
                log(p.x)
            }
        }
        f()
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["0", "1", "4", "9", "16"]

    def test_break_and_continue_with_loop_local_structs_produce_correct_output(self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        void func withBreak() {
            for int i = 0, i < 5, i++ {
                Point p
                p.x = i
                if p.x == 2 {
                    break
                }
                log(p.x)
            }
            log('after break loop')
        }
        void func withContinue() {
            for int i = 0, i < 5, i++ {
                Point p
                p.x = i
                if p.x % 2 == 0 {
                    continue
                }
                log(p.x)
            }
        }
        withBreak()
        withContinue()
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["0", "1", "after break loop", "1", "3"]

    def test_outer_scope_struct_survives_break_and_continue_from_a_nested_loop(self, compile_and_run):
        # The critical case: a struct declared OUTSIDE a loop and merely
        # used (not declared) inside it must keep its correct value --
        # break/continue only free locals declared since the loop's own
        # body began, never anything from an outer scope.
        source = """
        struct Point { x:int y:int }
        void func f() {
            Point outer
            outer.x = 100
            for int i = 0, i < 3, i++ {
                Point inner
                inner.x = i
                if inner.x == 1 {
                    break
                }
                log(inner.x + outer.x)
            }
            log(outer.x)
        }
        f()
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["100", "100"]

    def test_while_loop_local_struct_produces_correct_output(self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        void func f() {
            int i = 0
            while i < 4 {
                Point p
                p.x = i
                if p.x % 3 == 0 {
                    Point r
                    r.x = p.x * 100
                    log(r.x)
                }
                i = i + 1
            }
        }
        f()
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["0", "300"]

    def test_struct_escaping_a_loop_into_a_global_still_works_correctly(self, compile_and_run):
        # A value that genuinely escapes (assigned into a global) from
        # inside a loop must never be freed, on any path, including
        # break/continue -- it's excluded from every frame entirely,
        # the same escape_analysis.find_escaping_names result as ever.
        source = """
        struct Point { x:int y:int }
        Point g
        void func f() {
            for int i = 0, i < 3, i++ {
                Point p
                p.x = i
                g = p
            }
        }
        f()
        log(g.x)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "2"

    def test_many_loop_iterations_with_nested_if_and_break_continue_does_not_crash(self, compile_and_run):
        # A heavier robustness check than the stage-1 version above --
        # this one exercises the actual new machinery: a loop-local
        # struct freed every iteration, an if nested inside the loop
        # body with its own struct local, and break/continue both firing
        # repeatedly across many iterations, all in the same loop.
        source = """
        struct Point { x:int y:int }
        void func run(n:int) {
            Point outer
            outer.x = 0
            for int i = 0, i < n, i++ {
                Point p
                p.x = i
                if p.x % 7 == 0 {
                    continue
                }
                if p.x % 13 == 0 {
                    break
                }
                Point q
                q.x = p.x * 2
                outer.x = outer.x + q.x
            }
            log(outer.x)
        }
        for int i = 0, i < 20000, i++ {
            run(50)
        }
        log('done')
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.splitlines()[-1] == "done"

    # ---- edge cases beyond the two increments' own core scenarios ----
    # Added in a follow-up robustness pass over the whole feature (not a
    # new increment -- no new codegen.py behavior was needed for any of
    # these; they exercise combinations the two increments' own tests
    # didn't specifically spell out). Verified against real generated IR
    # and real compiled output here, and additionally against a combined
    # 5000-iteration program exercising every one of these patterns
    # together under AddressSanitizer/LeakSanitizer (see tests/CONTRACT.md)
    # -- zero ASan errors, zero leaks.

    def test_break_in_a_nested_loop_frees_only_the_inner_loops_own_locals(self, parser, semantic, codegen):
        # A loop-local declared in an OUTER loop's body, merely used (not
        # re-declared) inside a nested inner loop, must survive the inner
        # loop's own break -- free_depth is captured fresh by each
        # _emit_for call, so the inner loop's break must never reach past
        # its own body's frame down into the outer loop's. arr[T], not a
        # struct -- see test_a_loop_local_array_is_freed_inside_the_loop_body's
        # own note on why arr[T] is what still exercises this machinery
        # since the stack-allocation swap.
        source = """
        void func f() {
            for int i = 0, i < 3, i++ {
                arr[int] mid = [i]
                for int j = 0, j < 3, j++ {
                    arr[int] inner = [j]
                    if inner[0] == 1 {
                        break
                    }
                }
                log(mid[0])
            }
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        lines = ir.splitlines()
        inner_break_block = next(i for i, l in enumerate(lines) if l.strip().startswith("if.then"))
        inner_break_end = next(i for i in range(inner_break_block, len(lines))
                                if lines[i].strip().startswith("br label %for.end"))
        break_lines = lines[inner_break_block:inner_break_end]
        # Exactly one free() on the inner break path -- inner's own, not mid's.
        assert sum("call void @free(" in l for l in break_lines) == 1

    def test_break_in_a_nested_loop_does_not_corrupt_the_outer_loops_local(self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        void func f() {
            for int i = 0, i < 3, i++ {
                Point mid
                mid.x = i * 100
                for int j = 0, j < 3, j++ {
                    Point inner
                    inner.x = j
                    if inner.x == 1 {
                        break
                    }
                }
                log(mid.x)
            }
        }
        f()
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["0", "100", "200"]

    def test_return_from_a_nested_if_after_the_loop_locals_declaration_frees_it(self, compile_and_run):
        # A return nested two levels deep inside a loop body (an if
        # inside the loop), textually AFTER the loop-local's own
        # declaration -- down_to=0 on Return must reach through the
        # if-then's own frame *and* the loop body's frame *and* the
        # function's own top-level frame, all at once.
        source = """
        struct Point { x:int y:int }
        int func f(n:int) {
            for int i = 0, i < n, i++ {
                Point p
                p.x = i
                if p.x == 3 {
                    return p.x * 100
                }
            }
            return -1
        }
        log(f(10))
        log(f(2))
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["300", "-1"]

    def test_return_from_a_nested_if_before_the_loop_locals_declaration_has_no_free_on_that_path(
            self, parser, semantic, codegen):
        # The mirror of test_early_return_before_the_declaration_has_no_free_on_that_path,
        # but with the early return nested inside a loop body instead of
        # directly in the function body -- the loop-local's VarDecl
        # hasn't been walked yet when this path's Return fires, so its
        # frame must still be empty on this specific path.
        source = """
        struct Point { x:int y:int }
        int func f(n:int) {
            for int i = 0, i < n, i++ {
                if i == 2 {
                    return 777
                }
                Point p
                p.x = i
            }
            return -1
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        lines = ir.splitlines()
        func_start = next(i for i, l in enumerate(lines) if l.startswith("define i64 @f("))
        func_end = next(i for i in range(func_start, len(lines)) if lines[i] == "}")
        func_lines = lines[func_start:func_end]
        ret_lines = [i for i, l in enumerate(func_lines) if l.strip().startswith("ret i64 777")]
        assert len(ret_lines) == 1
        assert not any("call void @free(" in l for l in func_lines[:ret_lines[0] + 1])

    def test_return_from_a_nested_if_before_the_loop_locals_declaration_produces_correct_output(
            self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        int func f(n:int) {
            for int i = 0, i < n, i++ {
                if i == 2 {
                    return 777
                }
                Point p
                p.x = i
            }
            return -1
        }
        log(f(10))
        log(f(1))
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["777", "-1"]

    def test_else_if_chain_frees_each_branchs_own_struct_local(self, compile_and_run):
        # Each `else if` is its own nested IfStmt (see parser.py's
        # parse_if), so each branch's then-block is its own _emit_block
        # frame -- a struct declared in one arm must never affect another
        # arm's freeing, and the un-taken arms' structs must never even
        # be allocated (ordinary control flow, unrelated to #74, but
        # worth pinning down together with the rest of this).
        source = """
        struct Point { x:int y:int }
        int func f(n:int) {
            if n == 0 {
                Point a
                a.x = 10
                return a.x
            } else if n == 1 {
                Point b
                b.x = 20
                return b.x
            } else if n == 2 {
                Point c
                c.x = 30
                return c.x
            } else {
                Point d
                d.x = 40
                return d.x
            }
        }
        log(f(0))
        log(f(1))
        log(f(2))
        log(f(3))
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["10", "20", "30", "40"]

    def test_bare_nested_block_frees_its_own_local(self, compile_and_run):
        # A standalone `{ }` block (not attached to if/while/for) is
        # still routed through the same _emit_block -- and two sibling
        # bare blocks reusing the same local name must not collide or
        # double-free (each is its own Env/frame).
        source = """
        struct Point { x:int y:int }
        int func f() {
            int total = 0
            {
                Point p
                p.x = 5
                total = total + p.x
            }
            {
                Point p
                p.x = 6
                total = total + p.x
            }
            return total
        }
        log(f())
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "11"

    def test_sibling_if_else_branches_can_declare_the_same_local_name_without_double_free(
            self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        int func f(cond:bool) {
            if cond {
                Point p
                p.x = 1
                return p.x
            } else {
                Point p
                p.x = 2
                return p.x
            }
        }
        log(f(true))
        log(f(false))
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["1", "2"]

    def test_combined_nested_loops_elif_and_bare_blocks_do_not_crash_under_heavy_iteration(
            self, compile_and_run):
        # The same combination verified separately by hand under
        # AddressSanitizer/LeakSanitizer (see tests/CONTRACT.md) -- here
        # as an ordinary correctness-and-no-crash pytest check: nested
        # for-in-for with an inner break, return after and before a
        # loop-local's own declaration, a 4-way else-if chain, bare
        # blocks with a shadowed name, and if/else sibling shadowing,
        # all run back to back many times.
        source = """
        struct Point { x:int y:int }
        int func nestedForBreak() {
            Point outer
            outer.x = 1000
            int total = 0
            for int i = 0, i < 4, i++ {
                Point mid
                mid.x = i
                for int j = 0, j < 4, j++ {
                    Point inner
                    inner.x = j
                    if inner.x == 2 {
                        break
                    }
                    total = total + inner.x
                }
                total = total + mid.x
            }
            total = total + outer.x
            return total
        }
        int func elifChain(n:int) {
            if n == 0 {
                Point a
                a.x = 10
                return a.x
            } else if n == 1 {
                Point b
                b.x = 20
                return b.x
            } else {
                Point c
                c.x = 30
                return c.x
            }
        }
        int func bareBlocks() {
            int total = 0
            {
                Point p
                p.x = 5
                total = total + p.x
            }
            {
                Point p
                p.x = 6
                total = total + p.x
            }
            return total
        }
        for int i = 0, i < 3000, i++ {
            if nestedForBreak() != 1010 {
                fail('nestedForBreak drifted')
            }
            if elifChain(i % 3) != (10 + (i % 3) * 10) {
                fail('elifChain drifted')
            }
            if bareBlocks() != 11 {
                fail('bareBlocks drifted')
            }
        }
        log('done')
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.strip() == "done"

    # ---- interprocedural (claude.md #74 stage 2) ----
    # See festina/escape_analysis.py's own module docstring and
    # tests/test_escape_analysis.py::TestInterproceduralEscapingParams
    # for the analysis in isolation (no C compiler, no codegen at all).
    # These check the other half: that CodeGen actually builds a
    # correct escaping_params table one function at a time, in program
    # order, and wires it into real generated IR and real compiled
    # output -- including the two cases the analysis-only tests can't
    # reach on their own (a real multi-function program, and a real
    # self-recursive function).

    def test_transitive_chain_of_non_retaining_calls_stack_allocates_the_original_local(
            self, parser, semantic, codegen):
        # a() -> b() -> c(), and c only reads its own parameter -- the
        # "safe" result has to propagate through b's own analysis (b's
        # parameter is only ever used as a pass-through argument to c)
        # before a's own local can be proven safe. Requires
        # escaping_params[c] to already exist by the time b is
        # analyzed, and escaping_params[b] to already exist by the time
        # a is analyzed -- exactly the program-order guarantee
        # CodeGen.escaping_params's own comment describes. Checks for a
        # stack alloca, not a free() -- since the stack-allocation swap
        # (claude.md #43/#74/#75), a struct proven safe this way never
        # goes through calloc+free at all.
        source = """
        struct Point { x:int y:int }
        void func c(q:Point) {
            log(q.x)
        }
        void func b(r:Point) {
            c(r)
        }
        void func a() {
            Point p
            b(p)
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        a_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define void @a("))
        a_body = "\n".join(ir.splitlines()[a_start:])
        assert "alloca %struct.Point" in a_body
        assert "call ptr @calloc(" not in a_body

    def test_transitive_chain_stops_freeing_at_the_first_retaining_link(
            self, parser, semantic, codegen):
        # Same shape, but c now retains its own parameter (stores it
        # into a global) -- the escaping-ness must propagate all the
        # way back UP the chain: b's own argument to c escapes, so b's
        # own parameter escapes, so a's own local passed to b escapes
        # too. Nothing anywhere in this chain should be freed.
        source = """
        struct Point { x:int y:int }
        Point stash
        void func c(q:Point) {
            stash = q
        }
        void func b(r:Point) {
            c(r)
        }
        void func a() {
            Point p
            b(p)
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "call void @free(" not in ir

    def test_self_recursive_function_passing_its_own_param_stays_conservative(
            self, parser, semantic, codegen):
        # f calls itself, passing its own struct parameter straight
        # through. self.escaping_params has no entry for 'f' yet at the
        # point f's own body is being walked (f isn't registered until
        # AFTER its own analysis completes), so this recursive call
        # site falls back to the original unconditional "any call
        # argument escapes" default, exactly like a call to an unknown
        # builtin would -- p is marked escaping via that one use, even
        # though every OTHER use of p in f is a safe field read. A
        # caller of f() must see that same conservative result: it must
        # not free its own local either, even though nothing in this
        # program actually corrupts anything either way.
        source = """
        struct Point { x:int y:int }
        void func f(p:Point, n:int) {
            log(p.x)
            if n > 0 {
                f(p, n - 1)
            }
        }
        void func g() {
            Point p
            f(p, 3)
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "call void @free(" not in ir

    def test_only_the_provably_safe_parameter_position_is_exempted(
            self, parser, semantic, codegen):
        # mixed(a, b): a is only ever read (safe), b is stored into a
        # global (escapes) -- escaping_params[mixed] must end up
        # {1}, not {0, 1} or {} -- and f()'s own call must stack-
        # allocate exactly the argument at the safe position (p,
        # position 0) and leave the other (q, position 1) calloc'd,
        # exactly as it always was.
        source = """
        struct Point { x:int y:int }
        Point stash
        void func mixed(a:Point, b:Point) {
            log(a.x)
            stash = b
        }
        void func f() {
            Point p
            Point q
            mixed(p, q)
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        f_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define void @f("))
        f_body = "\n".join(ir.splitlines()[f_start:])
        p_line = next(l for l in f_body.splitlines() if l.strip().startswith("%p.storage."))
        q_line = next(l for l in f_body.splitlines() if l.strip().startswith("%q.storage."))
        assert "alloca %struct.Point" in p_line
        assert "call ptr @calloc(" in q_line

    def test_transitive_chain_produces_correct_output(self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        int func c(q:Point) {
            return q.x * 10
        }
        int func b(r:Point) {
            return c(r) + 1
        }
        int func a(n:int) {
            Point p
            p.x = n
            return b(p)
        }
        log(a(5))
        log(a(7))
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["51", "71"]

    def test_multi_param_mixed_escaping_produces_correct_output(self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        Point stash
        void func mixed(a:Point, b:Point) {
            log(a.x)
            stash = b
        }
        void func f() {
            Point p
            p.x = 1
            Point q
            q.x = 2
            mixed(p, q)
        }
        f()
        log(stash.x)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["1", "2"]

    def test_self_recursive_function_produces_correct_output_and_does_not_crash(
            self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        void func f(p:Point, n:int) {
            log(p.x)
            if n > 0 {
                f(p, n - 1)
            }
        }
        void func g() {
            Point p
            p.x = 42
            f(p, 3)
        }
        g()
        log('done')
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["42", "42", "42", "42", "done"]

    def test_interprocedural_combination_does_not_crash_under_heavy_iteration(
            self, compile_and_run):
        # The same combination verified separately by hand under
        # AddressSanitizer/LeakSanitizer (see tests/CONTRACT.md): a
        # transitive non-retaining chain, a transitive retaining chain,
        # a self-recursive function, and a multi-parameter
        # mixed-escaping function, all called many times in a loop.
        source = """
        struct Point { x:int y:int }
        Point stash
        int func readOnly(q:Point) {
            return q.x
        }
        int func passThrough(r:Point) {
            return readOnly(r)
        }
        int func nonRetainingChain(n:int) {
            Point p
            p.x = n
            return passThrough(p)
        }
        void func retains(q:Point) {
            stash = q
        }
        void func retainingChain(r:Point) {
            retains(r)
        }
        void func mixed(a:Point, b:Point) {
            log(a.x)
            stash = b
        }
        void func recur(p:Point, n:int) {
            if n > 0 {
                recur(p, n - 1)
            }
        }
        for int i = 0, i < 3000, i++ {
            if nonRetainingChain(i) != i {
                fail('nonRetainingChain drifted')
            }
            Point retainMe
            retainMe.x = i
            retainingChain(retainMe)
            if stash.x != i {
                fail('retainingChain drifted')
            }
            Point a
            a.x = i
            Point b
            b.x = i * 2
            mixed(a, b)
            if stash.x != i * 2 {
                fail('mixed drifted')
            }
            Point r
            r.x = i
            recur(r, 5)
        }
        log('done')
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.splitlines()[-1] == "done"


def _find_window(display, timeout=20):
    # 20s, not the 10s an isolated run needs comfortably -- TestGraphics
    # compiles a fresh binary (a real gcc invocation) and spawns a fresh
    # Xvfb instance per interactive test, back to back; under real
    # contention (the full suite, or just a loaded sandbox) that
    # occasionally pushes a single window's startup past 10s even though
    # the underlying Xvfb/window-creation code itself is reliable in
    # isolation (verified directly, outside pytest, with no failures in
    # 15 repeated runs). Module-level (not a method) so TestTimers's one
    # combined graphics+timers test can reuse it too.
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = subprocess.run(
            ["xdotool", "search", "--name", "Festina"],
            env=dict(os.environ, DISPLAY=display),
            capture_output=True, text=True,
        )
        wids = result.stdout.split()
        if wids:
            return wids[0]
        time.sleep(0.2)
    raise AssertionError("the Festina canvas window never appeared")


def _wait_for_output(stdout_path, predicate, timeout=20):
    # Polls instead of a fixed sleep-then-assert, for the same reason
    # x_display polls for Xvfb readiness instead of a fixed sleep (see
    # conftest.py): reliable running one test in isolation but flaky
    # under full-suite load, since dispatch latency (X server ->
    # compiled handler -> log() -> this process's read) isn't constant.
    # Returns the last text read so a timed-out caller's own assert
    # still shows what actually came back, not just "timed out".
    # Module-level for the same reason _find_window is.
    deadline = time.time() + timeout
    text = ""
    while time.time() < deadline:
        text = stdout_path.read_text()
        if predicate(text):
            return text
        time.sleep(0.1)
    return text


class TestGraphics:
    """claude.md #37 (image), #39 (graphics), #40 (events) -- a real
    X11 window rendered via Cairo, not a file written to disk (see
    festina/codegen.py's module docstring's "Graphics" note for the
    full design).

    Three tiers, cheapest first:
    - Compilation only (no display needed at all) -- catches codegen/
      linking bugs (e.g. a wrong declare signature) fast.
    - The "no display available" error path -- also doesn't need a
      real display; it specifically tests running with none.
    - Real interactive tests against a window (via the x_display /
      run_graphics_program fixtures -- prefers an already-set DISPLAY,
      otherwise spins up a throwaway Xvfb instance; skips cleanly if
      neither Xvfb nor xdotool is available). These were also verified
      manually beyond what's automated here: actually capturing the
      rendered window (via xwd) and visually confirming drawRect/
      drawCircle/drawText/drawImage all render at the right positions,
      in the right shapes -- not automated here (would add xwd/netpbm
      as further test-only dependencies for something the manual check
      already gave high confidence in), but the click/mouse/key/resize
      dispatch tests below do run automatically, since xdotool + log()
      output is enough to prove real input reaches the compiled handler
      without needing to capture pixels at all.

    `on close` is the one handler NOT covered here even though the
    others in this tier are: it fires on the exact same WM_DELETE_WINDOW
    ClientMessage the window's own close-button handling already uses,
    and (as already true of that close-button path before `on close`
    ever existed) a bare Xvfb instance runs no window manager to
    translate `xdotool windowclose` into that message -- verified
    directly: it leaves the process running rather than triggering the
    handler. An environment limitation of the test setup, not a gap in
    the app's own (standard) handling of that protocol -- see
    festina_runtime.c's festina_handle_graphics_event for the actual
    dispatch, right alongside the click/mouse/key/resize dispatch this
    class does verify.
    """

    def test_compiles_and_links_successfully(self, cli_mod, tmp_path):
        # No display needed -- just confirms codegen emits valid IR and
        # the result links against Cairo/X11 successfully (claude.md
        # #59: graphics is a real new link-time dependency -- see
        # festina/cli.py's cairo-xlib wiring).
        if not (shutil.which("clang") or shutil.which("gcc") or shutil.which("cc")):
            pytest.skip("no C compiler (clang/gcc/cc) on PATH")
        source = """
        img icon = loadImage('nonexistent.png')
        drawRect(0, 0, 100, 100)
        drawCircle(50, 50, 25)
        drawText('Hello', 20, 20)
        drawImage(icon, 10, 10)
        log(`${clientWidth}x${clientHeight}`)

        on click(x:int, y:int) {
            log(`click at ${x}, ${y}`)
        }
        on mouse(x:int, y:int) {
            log(`mouse at ${x}, ${y}`)
        }
        on key(key:text) {
            log(`key ${key}`)
        }
        on resize() {
            log(`resize ${clientWidth} ${clientHeight}`)
        }
        on close() {
            log('closing')
        }
        """
        src_path = tmp_path / "main.f"
        src_path.write_text(source)
        out_path = tmp_path / "program"
        result_path = cli_mod.compile_file(str(src_path), str(out_path))
        assert result_path == str(out_path)
        assert out_path.exists()

    def test_missing_display_is_a_clear_runtime_error(self, compile_and_run, monkeypatch):
        monkeypatch.delenv("DISPLAY", raising=False)
        result = compile_and_run("drawRect(0, 0, 10, 10)")
        assert result.returncode == 1
        assert "X display" in result.stderr

    def test_invalid_image_path_is_a_clear_runtime_error(self, compile_and_run, monkeypatch):
        monkeypatch.delenv("DISPLAY", raising=False)
        result = compile_and_run("img icon = loadImage('/nonexistent/path.png')\nlog('unreachable')")
        assert result.returncode == 1
        assert "could not load image" in result.stderr
        assert "unreachable" not in result.stdout

    def test_program_without_graphics_never_opens_a_window(self, compile_and_run, monkeypatch):
        # self.uses_graphics gates festina_graphics_init() -- a program
        # that never calls a graphics function or declares on
        # click/mouse must behave exactly as before: no window, no
        # blocking event loop, normal immediate exit. Verified here by
        # deliberately having no display available at all and
        # confirming the program still succeeds (if it tried to open a
        # window, it would fail exactly like the test above).
        monkeypatch.delenv("DISPLAY", raising=False)
        result = compile_and_run("log('no graphics here')")
        assert result.returncode == 0
        assert result.stdout.strip() == "no graphics here"

    def test_click_dispatches_to_handler_with_correct_coordinates(self, run_graphics_program, x_display):
        source = "on click(x:int, y:int) {\n    log(`click ${x} ${y}`)\n}"
        proc, stdout_path = run_graphics_program(source)
        try:
            wid = _find_window(x_display)
            env = dict(os.environ, DISPLAY=x_display)
            subprocess.run(["xdotool", "mousemove", "--window", wid, "150", "220"], env=env, check=True)
            subprocess.run(["xdotool", "click", "--window", wid, "1"], env=env, check=True)
            text = _wait_for_output(stdout_path, lambda t: t.strip() != "")
            assert text.strip() == "click 150 220"
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_mouse_move_dispatches_to_handler_with_correct_coordinates(self, run_graphics_program, x_display):
        source = "on mouse(x:int, y:int) {\n    log(`mouse ${x} ${y}`)\n}"
        proc, stdout_path = run_graphics_program(source)
        try:
            wid = _find_window(x_display)
            env = dict(os.environ, DISPLAY=x_display)
            subprocess.run(["xdotool", "mousemove", "--window", wid, "300", "400"], env=env, check=True)
            text = _wait_for_output(stdout_path, lambda t: "mouse 300 400" in t)
            assert "mouse 300 400" in text
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_key_dispatches_printable_and_named_keys(self, run_graphics_program, x_display):
        # claude.md #40: `on key(key:text)`. A printable key (e.g. "a")
        # comes back as its own character; a non-printable one (e.g.
        # Escape, whose ASCII value is an unprintable control code) is
        # not a useful `text` value, so it falls back to X11's own key
        # name instead -- see festina_runtime.c's festina_handle_graphics_event.
        source = "on key(key:text) {\n    log(`key ${key}`)\n}"
        proc, stdout_path = run_graphics_program(source)
        try:
            wid = _find_window(x_display)
            env = dict(os.environ, DISPLAY=x_display)
            # The window needs real keyboard focus for KeyPress events to
            # reach it at all -- see festina_graphics_init's XSetInputFocus
            # call, needed since a bare Xvfb instance runs no window
            # manager to hand focus over the way a real desktop would.
            time.sleep(0.3)
            subprocess.run(["xdotool", "key", "--window", wid, "a"], env=env, check=True)
            subprocess.run(["xdotool", "key", "--window", wid, "Escape"], env=env, check=True)
            text = _wait_for_output(stdout_path, lambda t: len(t.splitlines()) >= 2)
            assert text.splitlines() == ["key a", "key Escape"]
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_client_size_matches_the_initial_canvas_before_any_resize(self, run_graphics_program, x_display):
        source = "log(`${clientWidth}x${clientHeight}`)"
        proc, stdout_path = run_graphics_program(source)
        try:
            _find_window(x_display)  # also proves a bare reference opens a window
            text = _wait_for_output(stdout_path, lambda t: t.strip() != "")
            assert text.strip() == "800x600"
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_resize_dispatches_to_handler_and_updates_client_size(self, run_graphics_program, x_display):
        source = "on resize() {\n    log(`resize ${clientWidth} ${clientHeight}`)\n}"
        proc, stdout_path = run_graphics_program(source)
        try:
            wid = _find_window(x_display)
            env = dict(os.environ, DISPLAY=x_display)
            subprocess.run(["xdotool", "windowsize", wid, "640", "480"], env=env, check=True)
            text = _wait_for_output(stdout_path, lambda t: "resize 640 480" in t)
            assert "resize 640 480" in text
        finally:
            proc.terminate()
            proc.wait(timeout=5)


class TestExampleGraphicsAndGame:
    """Interactive regression coverage for examples/graphics.f and
    examples/tic_tac_toe.f -- the two examples that need a real (or
    virtual) X server, so they can't join tests/test_examples.py's
    plain compile-and-check-stdout sweep. Lives here, not there, so it
    can reuse this file's own _find_window/_wait_for_output helpers and
    x_display/run_graphics_program fixtures, the same as TestGraphics
    above and TestTimers's combined graphics+timers test below."""

    def test_graphics_demo_dispatches_click_key_and_resize(self, run_graphics_program, x_display):
        source = open(os.path.join(_EXAMPLES_DIR, "graphics.f")).read()
        proc, stdout_path = run_graphics_program(source)
        try:
            wid = _find_window(x_display)
            env = dict(os.environ, DISPLAY=x_display)
            text = _wait_for_output(stdout_path, lambda t: "canvas started at 800x600" in t)
            assert "canvas started at 800x600" in text

            time.sleep(0.3)  # keyboard focus -- see TestGraphics's own note on this
            subprocess.run(["xdotool", "mousemove", "--window", wid, "100", "100"], env=env, check=True)
            subprocess.run(["xdotool", "click", "--window", wid, "1"], env=env, check=True)
            subprocess.run(["xdotool", "key", "--window", wid, "a"], env=env, check=True)
            subprocess.run(["xdotool", "windowsize", wid, "900", "700"], env=env, check=True)

            text = _wait_for_output(stdout_path, lambda t: "resized to 900x700" in t)
            assert "clicked at 100, 100" in text
            assert "key pressed: a" in text
            assert "resized to 900x700" in text
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_tic_tac_toe_detects_a_win(self, run_graphics_program, x_display):
        # Same board layout as the example's own source (GRID_X=250,
        # GRID_Y=150, CELL=100) -- clicks the top row for X, the middle
        # row for O, then the rest of the top row, so X wins with three
        # across the top: (0,0), (1,0), (2,0).
        source = open(os.path.join(_EXAMPLES_DIR, "tic_tac_toe.f")).read()
        proc, stdout_path = run_graphics_program(source)
        try:
            wid = _find_window(x_display)
            env = dict(os.environ, DISPLAY=x_display)

            def click(x, y):
                subprocess.run(["xdotool", "mousemove", "--window", wid, str(x), str(y)], env=env, check=True)
                subprocess.run(["xdotool", "click", "--window", wid, "1"], env=env, check=True)
                time.sleep(0.15)

            click(300, 200)  # X: top-left
            click(300, 300)  # O: middle-left
            click(400, 200)  # X: top-middle
            click(400, 300)  # O: middle-middle
            click(500, 200)  # X: top-right -- completes the top row

            text = _wait_for_output(stdout_path, lambda t: "X wins!" in t)
            assert "X wins!" in text
        finally:
            proc.terminate()
            proc.wait(timeout=5)


class TestTimers:
    """claude.md #69: setTimeout/setInterval/clearTimeout/clearInterval.
    Added because Festina otherwise has no way to schedule work after
    the fact, the same gap JS's setTimeout/setInterval fill. See
    festina/semantic.py's _infer_call (the setTimeout/setInterval
    branch) and runtime/festina_runtime.h's doc comment for the full
    design.

    Most of this needs no display at all -- a timers-only program never
    opens a window (CodeGen.uses_timers is a separate flag from
    uses_graphics) -- so these mostly use compile_and_run like any other
    runtime-behavior test. The one exception,
    test_timers_and_graphics_work_together, is the one thing that
    genuinely needs a real window: proving festina_run_event_loop's
    select()-based multiplexing keeps both a `setInterval` callback and
    `on click` responsive together, not just one or the other.
    """

    def test_timeout_fires_once_after_the_delay(self, compile_and_run):
        source = (
            "void func onTimeout() {\n"
            "    log('fired')\n"
            "}\n"
            "log('start')\n"
            "setTimeout(onTimeout, 10)\n"
        )
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["start", "fired"]

    def test_interval_fires_repeatedly_until_cleared(self, compile_and_run):
        source = (
            "int count = 0\n"
            "int intervalId = 0\n"
            "void func onInterval() {\n"
            "    count = count + 1\n"
            "    log(`tick ${count}`)\n"
            "    if (count >= 3) {\n"
            "        clearInterval(intervalId)\n"
            "    }\n"
            "}\n"
            "intervalId = setInterval(onInterval, 5)\n"
        )
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["tick 1", "tick 2", "tick 3"]

    def test_clear_timeout_cancels_a_pending_callback(self, compile_and_run):
        source = (
            "void func shouldNotFire() {\n"
            "    log('should not happen')\n"
            "}\n"
            "void func shouldFire() {\n"
            "    log('fired')\n"
            "}\n"
            "int id = setTimeout(shouldNotFire, 50)\n"
            "clearTimeout(id)\n"
            "setTimeout(shouldFire, 5)\n"
        )
        result = compile_and_run(source)
        assert result.returncode == 0
        assert "should not happen" not in result.stdout
        assert "fired" in result.stdout

    def test_callback_can_schedule_another_timer(self, compile_and_run):
        # Proves a timer created *from inside* a firing callback is
        # still picked up by the same run -- festina_run_event_loop
        # recomputes the earliest deadline fresh on every pass rather
        # than fixing it once at loop entry, and festina_add_timer's
        # array can safely grow (realloc) from inside a callback that's
        # itself being called from a loop iterating that same array.
        source = (
            "int calls = 0\n"
            "void func step() {\n"
            "    calls = calls + 1\n"
            "    log(`step ${calls}`)\n"
            "    if (calls < 3) {\n"
            "        setTimeout(step, 5)\n"
            "    }\n"
            "}\n"
            "setTimeout(step, 5)\n"
        )
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["step 1", "step 2", "step 3"]

    def test_program_with_only_timeouts_exits_once_they_all_fire(self, compile_and_run):
        # No graphics, no uncleared interval -- festina_run_event_loop
        # must actually return once there's nothing left to wait for,
        # not block forever; compile_and_run's own subprocess timeout
        # (15s) would turn a regression here into a hard failure rather
        # than a slow pass, but this asserts on the normal, fast path.
        source = "void func cb() {\n    log('done')\n}\nsetTimeout(cb, 5)\n"
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "done"

    def test_uncleared_interval_keeps_the_program_running(self, tmp_path, codegen, cli_mod):
        # The one JS-matching behavior compile_and_run's fixed 15s
        # timeout can't cleanly demonstrate (it would just make the test
        # slow, not prove anything a shorter wait doesn't already show)
        # -- drive it directly instead: start the compiled program in
        # the background, confirm it's still alive and still logging
        # after a short wait, then kill it ourselves. No DISPLAY needed
        # at all here -- this is exactly the "timers without graphics"
        # case CodeGen.uses_timers exists to keep separate from
        # uses_graphics.
        if not (shutil.which("clang") or shutil.which("gcc") or shutil.which("cc")):
            pytest.skip("no C compiler (clang/gcc/cc) on PATH")
        source = "void func tick() {\n    log('tick')\n}\nsetInterval(tick, 5)\n"
        src_path = tmp_path / "main.f"
        src_path.write_text(source)
        out_path = tmp_path / "program"
        cli_mod.compile_file(str(src_path), str(out_path))
        stdout_path = tmp_path / "stdout.log"
        proc = subprocess.Popen(
            ["stdbuf", "-oL", str(out_path)],
            cwd=tmp_path, stdout=open(stdout_path, "w"), stderr=subprocess.STDOUT,
        )
        try:
            time.sleep(0.3)
            assert proc.poll() is None, "an uncleared setInterval should keep the program running"
            lines = stdout_path.read_text().splitlines()
            assert len(lines) >= 2, f"expected multiple 'tick's by now, got {lines!r}"
            assert all(line == "tick" for line in lines)
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_timers_and_graphics_work_together(self, run_graphics_program, x_display):
        source = (
            "void func tick() {\n"
            "    log('tick')\n"
            "}\n"
            "on click(x:int, y:int) {\n"
            "    log(`click ${x} ${y}`)\n"
            "}\n"
            "drawRect(0, 0, 10, 10)\n"
            "setInterval(tick, 15)\n"
        )
        proc, stdout_path = run_graphics_program(source)
        try:
            wid = _find_window(x_display)
            env = dict(os.environ, DISPLAY=x_display)
            text = _wait_for_output(stdout_path, lambda t: "tick" in t)
            assert "tick" in text, "the interval never fired alongside the open window"

            subprocess.run(["xdotool", "mousemove", "--window", wid, "5", "5"], env=env, check=True)
            subprocess.run(["xdotool", "click", "--window", wid, "1"], env=env, check=True)
            text = _wait_for_output(stdout_path, lambda t: "click 5 5" in t)
            assert "click 5 5" in text
            # The interval kept firing before *and* after the click --
            # proves festina_run_event_loop's select() call is genuinely
            # multiplexing both, not just alternating or starving one.
            assert text.count("tick") >= 2
        finally:
            proc.terminate()
            proc.wait(timeout=5)


def _write_wav(path, duration_s=0.2, sample_rate=8000, channels=1):
    """A minimal, valid 16-bit PCM WAV file (silence -- only the
    container format matters for claude.md #38's loadAudio())."""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(b"\x00\x00" * int(duration_s * sample_rate) * channels)


class TestAudio:
    """claude.md #38: aud, loadAudio(), .play()/.stop()/.isPlaying().

    Unlike TestGraphics, none of this needs an opt-in skip tier: the
    null-device trick audio_null_env uses (see conftest.py) needs no
    extra tool install the way Xvfb does -- ALSA's null plugin ships
    inside alsa-lib itself, which festina_runtime.c already links
    against unconditionally. So every test here only needs what
    compile_and_run already requires: a working C compiler.

    Two of the deterministic-by-design guarantees these tests lean on
    (see festina_runtime.c's festina_audio_play/_stop for why each is
    actually guaranteed, not just usually true): isPlaying() is true
    the instant play() returns (the flag is set synchronously, before
    the background playback thread is even spawned), and isPlaying()
    is false the instant stop() returns (stop() joins the thread before
    returning, it doesn't just signal it and hope).
    """

    def test_compiles_and_links_successfully(self, cli_mod, tmp_path):
        # No audio device needed -- just confirms codegen emits valid IR
        # and the result links against ALSA/pthread successfully
        # (claude.md #59: audio is a real new link-time dependency --
        # see festina/cli.py's alsa wiring).
        if not (shutil.which("clang") or shutil.which("gcc") or shutil.which("cc")):
            pytest.skip("no C compiler (clang/gcc/cc) on PATH")
        source = """
        aud music = loadAudio('nonexistent.wav')
        music.play()
        music.stop()
        log(music.isPlaying())
        """
        src_path = tmp_path / "main.f"
        src_path.write_text(source)
        out_path = tmp_path / "program"
        result_path = cli_mod.compile_file(str(src_path), str(out_path))
        assert result_path == str(out_path)
        assert out_path.exists()

    def test_missing_audio_device_is_a_clear_runtime_error(self, compile_and_run, tmp_path):
        wav_name = "clip.wav"
        _write_wav(tmp_path / wav_name)
        # A fresh, empty HOME (no .asoundrc) -- guarantees ALSA's
        # "default" device resolution fails the same way it does in
        # this dev sandbox generally (verified: no /dev/snd node at
        # all), regardless of whatever the real test-runner's own
        # environment happens to have.
        empty_home = tmp_path / "empty_home"
        empty_home.mkdir()
        result = compile_and_run(
            f"aud music = loadAudio('{wav_name}')\nmusic.play()\nlog('unreachable')",
            env={"HOME": str(empty_home)},
        )
        assert result.returncode == 1
        assert "could not open an audio output device" in result.stderr
        assert "unreachable" not in result.stdout

    def test_invalid_audio_path_is_a_clear_runtime_error(self, compile_and_run):
        result = compile_and_run(
            "aud music = loadAudio('/nonexistent/path.wav')\nlog('unreachable')"
        )
        assert result.returncode == 1
        assert "could not open audio file" in result.stderr
        assert "unreachable" not in result.stdout

    def test_non_wav_file_is_a_clear_runtime_error(self, compile_and_run, tmp_path):
        (tmp_path / "bad.wav").write_bytes(b"this is not a wav file at all")
        result = compile_and_run("aud music = loadAudio('bad.wav')\nlog('unreachable')")
        assert result.returncode == 1
        assert "only 16-bit PCM WAV audio is supported" in result.stderr
        assert "unreachable" not in result.stdout

    def test_is_playing_true_immediately_after_play(self, compile_and_run, tmp_path, audio_null_env):
        _write_wav(tmp_path / "clip.wav")
        source = (
            "aud music = loadAudio('clip.wav')\n"
            "music.play()\n"
            "log(music.isPlaying())\n"
        )
        result = compile_and_run(source, env=audio_null_env)
        assert result.returncode == 0
        assert result.stdout.strip() == "true"

    def test_is_playing_false_immediately_after_stop(self, compile_and_run, tmp_path, audio_null_env):
        _write_wav(tmp_path / "clip.wav")
        source = (
            "aud music = loadAudio('clip.wav')\n"
            "music.play()\n"
            "music.stop()\n"
            "log(music.isPlaying())\n"
        )
        result = compile_and_run(source, env=audio_null_env)
        assert result.returncode == 0
        assert result.stdout.strip() == "false"

    def test_stop_when_nothing_playing_is_a_safe_no_op(self, compile_and_run, tmp_path, audio_null_env):
        _write_wav(tmp_path / "clip.wav")
        source = "aud music = loadAudio('clip.wav')\nmusic.stop()\nlog(music.isPlaying())\n"
        result = compile_and_run(source, env=audio_null_env)
        assert result.returncode == 0
        assert result.stdout.strip() == "false"

    def test_calling_play_again_while_playing_restarts_without_crashing(
        self, compile_and_run, tmp_path, audio_null_env
    ):
        _write_wav(tmp_path / "clip.wav", duration_s=1.0)
        source = (
            "aud music = loadAudio('clip.wav')\n"
            "music.play()\n"
            "music.play()\n"
            "log(music.isPlaying())\n"
        )
        result = compile_and_run(source, env=audio_null_env)
        assert result.returncode == 0
        assert result.stdout.strip() == "true"

    def test_timers_and_audio_work_together(self, compile_and_run, tmp_path, audio_null_env):
        # A short clip finishes (isPlaying() -> false) on its own, with
        # no stop() call -- checked from a setTimeout callback, proving
        # audio playback and the timer event loop coexist correctly
        # (the background playback thread doesn't block __festina_main()
        # or festina_run_event_loop() on the main thread).
        _write_wav(tmp_path / "clip.wav", duration_s=0.05)
        source = (
            "aud music = loadAudio('clip.wav')\n"
            "void func check() {\n"
            "    log(`playing after delay: ${music.isPlaying()}`)\n"
            "}\n"
            "music.play()\n"
            "setTimeout(check, 200)\n"
        )
        result = compile_and_run(source, env=audio_null_env)
        assert result.returncode == 0
        assert result.stdout.strip() == "playing after delay: false"


class TestRegex:
    """claude.md #67 (regular expressions), #68 (string match/replace)."""

    def test_test_matches_and_does_not_match(self, compile_and_run):
        source = """
        regex digits = regex('[0-9]+')
        log(digits.test('room 42'))
        log(digits.test('no numbers'))
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["true", "false"]

    def test_case_insensitive_flag(self, compile_and_run):
        source = """
        regex greeting = regex('^hello$', 'i')
        log(greeting.test('HELLO'))
        log(greeting.test('goodbye'))
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["true", "false"]

    def test_match_returns_first_match(self, compile_and_run):
        source = """
        regex digits = regex('[0-9]+')
        log('room 42, building 7'.match(digits))
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "42"

    def test_match_with_no_match_returns_null(self, compile_and_run):
        # claude.md #68: match() returns null (claude.md #25: null is
        # valid for every type) if there's no match -- text's null is
        # represented as a plain NULL pointer (see festina_runtime.h's
        # doc comment on festina_regex_match), which festina_log_text
        # already prints as an empty line.
        source = """
        regex digits = regex('[0-9]+')
        text found = 'no numbers here'.match(digits)
        log('before')
        log(found)
        log('after')
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["before", "", "after"]

    def test_replace_with_literal_text_search(self, compile_and_run):
        result = compile_and_run("log('room 42'.replace('room', 'suite'))")
        assert result.stdout.strip() == "suite 42"

    def test_replace_only_replaces_first_occurrence(self, compile_and_run):
        result = compile_and_run("log('a-b-c'.replace('-', '_'))")
        assert result.stdout.strip() == "a_b-c"

    def test_replace_all_replaces_every_occurrence(self, compile_and_run):
        result = compile_and_run("log('a-b-c'.replaceAll('-', '_'))")
        assert result.stdout.strip() == "a_b_c"

    def test_replace_with_no_match_returns_original_unchanged(self, compile_and_run):
        result = compile_and_run("log('hello world'.replace('zzz', 'nope'))")
        assert result.stdout.strip() == "hello world"

    def test_replace_all_with_regex_search(self, compile_and_run):
        result = compile_and_run("log('a1b2c3'.replaceAll(regex('[0-9]'), '-'))")
        assert result.stdout.strip() == "a-b-c-"

    def test_replace_with_regex_search_first_match_only(self, compile_and_run):
        result = compile_and_run("log('a1b2c3'.replace(regex('[0-9]'), '-'))")
        assert result.stdout.strip() == "a-b2c3"

    def test_replace_all_zero_width_match_does_not_hang(self, compile_and_run):
        # claude.md #54's ambiguity rule doesn't cover this -- it's a
        # straightforward correctness requirement, not something to
        # leave unresolved: a pattern that can match zero-width (e.g.
        # "x*" where there's no "x") must not spin the runtime forever.
        result = compile_and_run("log('abc'.replaceAll(regex('x*'), '-'))")
        assert result.returncode == 0
        assert result.stdout.strip() == "-a-b-c-"

    def test_original_text_value_is_unchanged_after_replace(self, compile_and_run):
        source = """
        text original = 'room 42'
        text renamed = original.replace('room', 'suite')
        log(original)
        log(renamed)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["room 42", "suite 42"]

    def test_invalid_pattern_is_a_clear_runtime_error(self, compile_and_run):
        # claude.md #67: "An invalid pattern is a runtime error (fail()),
        # not a compile-time error."
        result = compile_and_run("regex bad = regex('[unclosed')\nlog('unreachable')")
        assert result.returncode == 1
        assert "invalid regex pattern" in result.stderr
        assert "unreachable" not in result.stdout


class TestRegexLiteral:
    """claude.md #67: /pattern/flags -- same end-to-end behavior as
    TestRegex above (a literal compiles down to exactly the same
    festina_regex_compile() call), just via the new literal syntax."""

    def test_test_matches_and_does_not_match(self, compile_and_run):
        source = """
        regex digits = /[0-9]+/
        log(digits.test('room 42'))
        log(digits.test('no numbers'))
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["true", "false"]

    def test_i_flag_matches_case_insensitively(self, compile_and_run):
        source = """
        regex greeting = /^hello$/i
        log(greeting.test('HELLO'))
        log(greeting.test('goodbye'))
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["true", "false"]

    def test_g_flag_is_accepted_and_still_only_case_sensitive_by_default(self, compile_and_run):
        # 'g' alone has no additional effect (see the parser's own
        # comment on _SUPPORTED_REGEX_FLAGS) -- this just confirms
        # accepting it doesn't silently turn on case-insensitivity too.
        source = """
        regex digits = /[a-z]+/g
        log(digits.test('room'))
        log(digits.test('ROOM'))
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["true", "false"]

    def test_word_shorthand_class_matches_via_glibcs_gnu_extension(self, compile_and_run):
        # \w -- not official POSIX ERE syntax, but glibc's regcomp()
        # supports it as a GNU extension even in REG_EXTENDED mode
        # (verified directly against this runtime's own libc) -- this is
        # exactly the case that makes the JS-familiar shorthand classes
        # work in practice, not just the narrower official POSIX escapes.
        result = compile_and_run(r"log(/\w+/.test('hello world'))")
        assert result.stdout.strip() == "true"

    def test_combined_flags_from_the_readme_example(self, compile_and_run):
        result = compile_and_run(r"log(/\w+/gi.test('Hello'))")
        assert result.stdout.strip() == "true"

    def test_match_returns_first_match(self, compile_and_run):
        result = compile_and_run("log('room 42, building 7'.match(/[0-9]+/))")
        assert result.stdout.strip() == "42"

    def test_replace_all_with_regex_literal_search(self, compile_and_run):
        result = compile_and_run("log('a1b2c3'.replaceAll(/[0-9]/, '-'))")
        assert result.stdout.strip() == "a-b-c-"

    def test_escaped_slash_in_a_literal_pattern_matches_a_literal_slash(self, compile_and_run):
        result = compile_and_run(r"log(/a\/b/.test('a/b'))")
        assert result.stdout.strip() == "true"

    def test_used_directly_without_a_named_variable(self, compile_and_run):
        # No `regex x = ...` in between -- the literal is itself a
        # complete expression, usable anywhere a regex value is.
        result = compile_and_run("log(/[0-9]+/.test('42'))")
        assert result.stdout.strip() == "true"

    def test_a_real_division_right_after_this_feature_still_works(self, compile_and_run):
        # End-to-end confirmation that adding regex literals didn't
        # break ordinary division anywhere a human could plausibly
        # confuse the two -- see test_lexer.py::TestRegexLiterals for
        # the exhaustive disambiguation matrix at the tokenizer level.
        result = compile_and_run("int a = 10\nint b = 2\nlog(a / b)")
        assert result.stdout.strip() == "5"


class TestNumericConversion:
    """claude.md #55 (no implicit int/float conversion), #56 (Math),
    #57 (division/modulo by zero returns null). See
    tests/test_numeric_conversion.py for the parser/semantic-only tests;
    these check the actual runtime behavior of a compiled program."""

    @pytest.mark.parametrize("fn,expected", [
        ("floor", "19"), ("ceil", "20"), ("round", "20"), ("trunc", "19"),
    ])
    def test_math_function_runtime_result(self, compile_and_run, fn, expected):
        result = compile_and_run(f"float price = 19.99\nlog(Math.{fn}(price))")
        assert result.stdout.strip() == expected

    def test_to_float_runtime_result(self, compile_and_run):
        source = """
        int a = 5
        float b = 2.5
        float c = a.toFloat() + b
        log(c)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "7.5"

    def test_to_text_runtime_result(self, compile_and_run):
        source = """
        int i = 42
        float f = 3.14
        bool b = true
        log(i.toText())
        log(f.toText())
        log(b.toText())
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["42", "3.14", "true"]

    def test_to_text_matches_template_interpolation(self, compile_and_run):
        result = compile_and_run("int i = 42\nlog(i.toText())\nlog(`${i}`)")
        lines = result.stdout.splitlines()
        assert lines[0] == lines[1]

    def test_mixed_int_float_rejected_end_to_end(self, compile_and_run, errors):
        # Confirms the whole pipeline (not just semantic.py in isolation)
        # rejects this -- semantic analysis raises before ever reaching
        # a linker, so this is a CompileError, not a nonzero exit code.
        with pytest.raises(errors.CompileError, match="int and float"):
            compile_and_run("int a = 5\nfloat b = 2.5\nfloat c = a + b")

    def test_int_division_by_zero_returns_null(self, compile_and_run):
        # claude.md #57: must not crash (SIGFPE) and must not silently
        # compute garbage -- the sentinel is intentionally an
        # implementation detail (see codegen.py's module docstring), so
        # this only checks the process survives and produces *a* value,
        # not the exact sentinel bit pattern.
        source = """
        int a = 10
        int b = 0
        int result = a / b
        log('survived')
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "survived"

    def test_int_modulo_by_zero_returns_null(self, compile_and_run):
        source = "int a = 10\nint b = 0\nint result = a % b\nlog('survived')"
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "survived"

    def test_float_division_by_zero_returns_null(self, compile_and_run):
        source = "float a = 5.0\nfloat b = 0.0\nfloat result = a / b\nlog('survived')"
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "survived"

    def test_int_division_by_nonzero_is_unaffected(self, compile_and_run):
        result = compile_and_run("int a = 10\nint b = 4\nlog(a / b)\nlog(a % b)")
        assert result.stdout.splitlines() == ["2", "2"]

    def test_null_int_and_float_assignment(self, compile_and_run):
        # Regression test: `null` used to lower to the LLVM keyword
        # `null` unconditionally, which is only valid for pointer types --
        # `int x = null` failed to link before this fix.
        result = compile_and_run("int a = null\nfloat b = null\nlog('assigned fine')")
        assert result.returncode == 0
        assert result.stdout.strip() == "assigned fine"

    def test_null_bool_assignment(self, compile_and_run):
        # Regression test: same "null is only valid IR for a pointer
        # type" link failure as int/float, but bool needed one extra
        # step to fix -- see festina/codegen.py's module docstring's
        # "Null for bool" note (bool widened from i1 to i8 to make room
        # for a third, reserved value).
        result = compile_and_run("bool a = null\nlog('assigned fine')")
        assert result.returncode == 0
        assert result.stdout.strip() == "assigned fine"

    def test_comparing_a_null_int_against_the_null_literal(self, compile_and_run):
        # Regression test, found alongside the bool-null fix: `x == null`
        # for a concretely-typed int/float/bool operand used to reach
        # codegen as `icmp eq i64 %x, null` -- also invalid IR (null is
        # only valid for a pointer type), independent of bool at all.
        result = compile_and_run("int a = 1 / 0\nlog(a == null)")
        assert result.stdout.strip() == "true"

    def test_comparing_a_non_null_int_against_the_null_literal(self, compile_and_run):
        result = compile_and_run("int a = 5\nlog(a == null)")
        assert result.stdout.strip() == "false"

    def test_comparing_a_null_float_against_the_null_literal(self, compile_and_run):
        # Unlike int/bool, this is "false", not "true" -- FLOAT_NULL_CONST
        # is a real NaN, and IEEE-754's `oeq`/`one` ("ordered" compares,
        # which is what claude.md's == / != already lower to -- see
        # _emit_binop's fcmp dict) are false for *any* comparison
        # involving NaN, including a NaN compared with itself. This test
        # exists to confirm the fix at least compiles/runs cleanly now
        # (it used to be an LLVM IR parse error -- see the int case
        # above) -- not to claim float null-checking via == is reliable
        # the way it is for int/bool, which it structurally can't be
        # without changing every == / != to unordered compares, a
        # separate, unrelated design decision this fix doesn't make.
        result = compile_and_run("float a = 1.0 / 0.0\nlog(a == null)")
        assert result.stdout.strip() == "false"

    def test_comparing_a_null_bool_against_the_null_literal(self, compile_and_run):
        result = compile_and_run("bool a = null\nlog(a == null)")
        assert result.stdout.strip() == "true"

    def test_comparing_a_non_null_bool_against_the_null_literal(self, compile_and_run):
        result = compile_and_run("bool a = true\nlog(a == null)")
        assert result.stdout.strip() == "false"

    def test_null_bool_survives_a_function_call_round_trip(self, compile_and_run):
        source = """
        bool func identity(b:bool) {
            return b
        }
        log(identity(null) == null)
        log(identity(false) == null)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["true", "false"]

    def test_null_bool_struct_field(self, compile_and_run):
        source = """
        struct Flag { value:bool }
        Flag f
        f.value = null
        log(f.value == null)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "true"

    def test_ordinary_bool_logic_is_unaffected_by_the_widened_representation(self, compile_and_run):
        # claude.md #21/#22-ish sanity check: none of &&/||/!/==/!= on
        # ordinary (non-null) bool values should have changed behavior
        # just because the underlying storage widened from i1 to i8.
        source = """
        bool a = true
        bool b = false
        log(a && b)
        log(a || b)
        log(!a)
        log(!b)
        log(a == b)
        log(a != b)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["false", "true", "false", "true", "false", "true"]

    def test_float_literal_needing_scientific_notation(self, compile_and_run):
        # Regression test: _format_double used repr(), which switches to
        # scientific notation for small/large magnitudes (e.g. 1e-07) --
        # LLVM's float-literal grammar rejected that as invalid syntax.
        result = compile_and_run("float tiny = 0.0000001\nlog('compiled fine')")
        assert result.returncode == 0
        assert result.stdout.strip() == "compiled fine"


class TestFail:
    """claude.md #42: fail() is the runtime failure mechanism."""

    def test_fail_exits_nonzero_with_message(self, compile_and_run):
        source = """
        bool ok = false
        if ok != true {
            fail('Test failed')
        }
        log('unreachable')
        """
        result = compile_and_run(source)
        assert result.returncode == 1
        assert "Test failed" in result.stderr
        assert "unreachable" not in result.stdout


class TestEntryPoint:
    """claude.md #7: the programmer never defines main(); top-level
    executable statements run automatically."""

    def test_program_runs_without_a_declared_main(self, compile_and_run):
        result = compile_and_run("log('hello from entry')")
        assert result.returncode == 0
        assert result.stdout.strip() == "hello from entry"


class TestMultiFileCompilation:
    """claude.md #5, #6: `import file.f` pulls a file and its whole
    dependency graph into the current compilation unit -- one real
    compiled program spanning multiple source files, not per-file
    namespacing or runtime modules."""

    def test_struct_and_function_shared_across_files(self, compile_multi_and_run):
        files = {
            "main.f": (
                "import shapes.f\n"
                "Point p\n"
                "p.x = 3\n"
                "p.y = 4\n"
                "log(describe(p))\n"
            ),
            "shapes.f": (
                "struct Point {\n    x:int\n    y:int\n}\n"
                "text func describe(p:Point) {\n"
                "    return `(${p.x}, ${p.y})`\n"
                "}\n"
            ),
        }
        result = compile_multi_and_run(files)
        assert result.returncode == 0
        assert result.stdout.strip() == "(3, 4)"

    def test_transitive_imports(self, compile_multi_and_run):
        # main.f -> ui.f -> database.f
        files = {
            "main.f": "import ui.f\nshowGreeting()\n",
            "ui.f": "import database.f\nvoid func showGreeting() {\n    log(greeting())\n}\n",
            "database.f": "text func greeting() {\n    return 'hello from database.f'\n}\n",
        }
        result = compile_multi_and_run(files)
        assert result.returncode == 0
        assert result.stdout.strip() == "hello from database.f"

    def test_diamond_import_is_not_duplicated(self, compile_multi_and_run):
        # main.f imports both ui.f and database.f directly, and ui.f also
        # imports database.f -- claude.md #6: "each source file must be
        # processed only once." If database.f's `table Items` were
        # emitted twice, this would fail to compile (duplicate LLVM
        # globals) rather than merely produce a wrong answer.
        files = {
            "main.f": (
                "import ui.f\n"
                "import database.f\n"
                "sqlite('INSERT INTO Items (id) VALUES (1)')\n"
                "arr[Items] items = sqlite('SELECT * FROM Items')\n"
                "log(items.length)\n"
            ),
            "ui.f": "import database.f\n",
            "database.f": "table Items {\n    id:int\n}\n",
        }
        result = compile_multi_and_run(files)
        assert result.returncode == 0
        assert result.stdout.strip() == "1"

    def test_table_declared_in_imported_file_syncs_schema(self, compile_multi_and_run, tmp_path):
        files = {
            "main.f": "import schema.f\nlog('synced')\n",
            "schema.f": "table People {\n    id:int\n    name:text\n}\n",
        }
        result = compile_multi_and_run(files)
        assert result.returncode == 0
        assert result.stdout.strip() == "synced"
        assert (tmp_path / "festina.sqlite").exists()

    def test_compile_error_in_an_imported_file_is_reported(self, cli_mod, errors, tmp_path):
        # Full-pipeline version of TestBuildProgram's equivalent check in
        # test_imports.py -- goes through festina.cli.compile_file itself.
        # No C compiler needed: this fails during semantic analysis,
        # before compile_file ever reaches the link step.
        files = {
            "main.f": "import broken.f\nlog('start')\n",
            "broken.f": "log(undefinedVariable)\n",
        }
        for relpath, content in files.items():
            p = tmp_path / relpath
            p.write_text(content)
        with pytest.raises(errors.CompileError) as exc_info:
            cli_mod.compile_file(str(tmp_path / "main.f"), str(tmp_path / "out"))
        assert "broken.f" in str(exc_info.value)


class TestAutomaticSqliteSchemaSync:
    """claude.md #8, #28-31: festina.sqlite is created/opened
    automatically and each declared table's schema is synchronized
    before the entry function runs -- worked examples from #31."""

    def _schema(self, db_path, table):
        conn = sqlite3.connect(db_path)
        try:
            return conn.execute(f"PRAGMA table_info({table})").fetchall()
        finally:
            conn.close()

    def test_missing_table_is_created(self, compile_and_run, tmp_path):
        source = """
        table People {
            id:int
            name:text
        }
        log('synced')
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        db = tmp_path / "festina.sqlite"
        assert db.exists()
        cols = {row[1]: row[2] for row in self._schema(db, "People")}
        assert cols == {"id": "INTEGER", "name": "TEXT"}

    def test_missing_column_is_added_and_data_preserved(self, compile_and_run, tmp_path):
        compile_and_run("table People {\n    id:int\n    name:text\n}\nlog('v1')")
        db = tmp_path / "festina.sqlite"
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO People (id, name) VALUES (1, 'Patrick')")
        conn.commit()
        conn.close()

        result = compile_and_run(
            "table People {\n    id:int\n    name:text\n    age:int\n}\nlog('v2')",
            filename="v2.f",
        )
        assert result.returncode == 0
        cols = {row[1]: row[2] for row in self._schema(db, "People")}
        assert cols == {"id": "INTEGER", "name": "TEXT", "age": "INTEGER"}
        rows = sqlite3.connect(db).execute("SELECT id, name FROM People").fetchall()
        assert rows == [(1, "Patrick")]

    def test_obsolete_column_is_dropped_data_preserved(self, compile_and_run, tmp_path):
        # claude.md #31 worked example: People(id, name, obsolete) -> People(id, name).
        compile_and_run(
            "table People {\n    id:int\n    name:text\n    obsolete:text\n}\nlog('v1')"
        )
        db = tmp_path / "festina.sqlite"
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO People (id, name, obsolete) VALUES (1, 'Patrick', 'junk')")
        conn.commit()
        conn.close()

        result = compile_and_run(
            "table People {\n    id:int\n    name:text\n}\nlog('v2')", filename="v2.f",
        )
        assert result.returncode == 0
        cols = {row[1] for row in self._schema(db, "People")}
        assert cols == {"id", "name"}
        rows = sqlite3.connect(db).execute("SELECT id, name FROM People").fetchall()
        assert rows == [(1, "Patrick")]

    def test_column_rename_via_declaration_change(self, compile_and_run, tmp_path):
        # claude.md #31 worked example: People(id, name) -> People(id, full_name).
        compile_and_run("table People {\n    id:int\n    name:text\n}\nlog('v1')")
        db = tmp_path / "festina.sqlite"
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO People (id, name) VALUES (1, 'Patrick')")
        conn.commit()
        conn.close()

        result = compile_and_run(
            "table People {\n    id:int\n    full_name:text\n}\nlog('v2')", filename="v2.f",
        )
        assert result.returncode == 0
        cols = {row[1] for row in self._schema(db, "People")}
        assert cols == {"id", "full_name"}
        rows = sqlite3.connect(db).execute("SELECT id FROM People").fetchall()
        assert rows == [(1,)]  # id survives the rebuild; the old `name` data does not

    def test_incompatible_column_type_is_altered_data_cast(self, compile_and_run, tmp_path):
        compile_and_run("table Items {\n    id:int\n    price:int\n}\nlog('v1')")
        db = tmp_path / "festina.sqlite"
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO Items (id, price) VALUES (1, 100)")
        conn.commit()
        conn.close()

        result = compile_and_run(
            "table Items {\n    id:int\n    price:float\n}\nlog('v2')", filename="v2.f",
        )
        assert result.returncode == 0
        cols = {row[1]: row[2] for row in self._schema(db, "Items")}
        assert cols == {"id": "INTEGER", "price": "REAL"}
        rows = sqlite3.connect(db).execute("SELECT id, price FROM Items").fetchall()
        assert rows == [(1, 100.0)]

    def test_no_tables_declared_means_no_db_file(self, compile_and_run, tmp_path):
        # claude.md #29: the database is only ever touched automatically --
        # a program with no `table` declarations shouldn't create one.
        result = compile_and_run("log('no tables here')")
        assert result.returncode == 0
        assert not (tmp_path / "festina.sqlite").exists()

    def test_a_table_too_wide_for_the_runtimes_sql_buffer_fails_cleanly(self, compile_and_run):
        # Security regression test: festina_sync_table builds several
        # SQL statements incrementally across a loop over the declared
        # columns, using the (deceptively unsafe -- see
        # festina_check_sql_buffer's own comment in festina_runtime.c)
        # `pos += snprintf(buf + pos, sizeof(buf) - pos, ...)` idiom.
        # Once accumulated output exceeds the fixed-size buffer, the
        # *next* call's "remaining space" computation used to
        # underflow (unsigned arithmetic) to a huge number, hand
        # snprintf permission to write far past the buffer, and
        # genuinely stack-smash -- verified directly with
        # AddressSanitizer before this was fixed. A table with enough
        # columns (or long enough names) to overflow the 2048-byte
        # CREATE TABLE buffer must now fail cleanly instead.
        cols = "\n".join(
            f"    col_{i}_{'x' * 60}:text" for i in range(40)
        )
        source = f"table Big {{\n{cols}\n}}\nlog('unreachable')"
        result = compile_and_run(source)
        assert result.returncode == 1
        assert "too long for this compiler's fixed-size buffer" in result.stderr
        assert "unreachable" not in result.stdout


class TestDatabaseURL:
    """claude.md #70: DatabaseURL = <expr>, the entry file's own first
    statement, overriding festina.sqlite's default location."""

    def test_no_directive_uses_the_default_filename(self, compile_and_run, tmp_path):
        result = compile_and_run("table People {\n    id:int\n}\nlog('built')")
        assert result.returncode == 0
        assert (tmp_path / "festina.sqlite").exists()

    def test_string_literal_directive_changes_the_path(self, compile_and_run, tmp_path):
        source = "DatabaseURL = 'custom.sqlite'\ntable People {\n    id:int\n}\nlog('built')"
        result = compile_and_run(source)
        assert result.returncode == 0
        assert (tmp_path / "custom.sqlite").exists()
        assert not (tmp_path / "festina.sqlite").exists()

    def test_directive_from_environment_variable(self, compile_and_run, tmp_path):
        source = "DatabaseURL = environment.DB_PATH\ntable People {\n    id:int\n}\nlog('built')"
        result = compile_and_run(source, env={"DB_PATH": "from_env.sqlite"})
        assert result.returncode == 0
        assert (tmp_path / "from_env.sqlite").exists()

    def test_data_actually_lands_in_the_configured_database(self, compile_and_run, tmp_path):
        source = """
        DatabaseURL = 'game.sqlite'
        table People {
            id:int
            name:text
        }
        sqlite('INSERT INTO People (id, name) VALUES (?, ?)', [1, 'Patrick'])
        arr[People] people = sqlite('SELECT * FROM People')
        log(people[0].name)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "Patrick"
        import sqlite3
        conn = sqlite3.connect(tmp_path / "game.sqlite")
        rows = conn.execute("SELECT name FROM People").fetchall()
        assert rows == [("Patrick",)]


class TestEnvironment:
    """claude.md #71: environment.NAME / environment[keyExpr]."""

    def test_reads_a_set_variable(self, compile_and_run):
        result = compile_and_run("log(environment.FESTINA_TEST_VAR)", env={"FESTINA_TEST_VAR": "hello"})
        assert result.stdout.strip() == "hello"

    def test_unset_variable_is_null(self, compile_and_run, monkeypatch):
        monkeypatch.delenv("FESTINA_DEFINITELY_UNSET_VAR", raising=False)
        result = compile_and_run("log('before')\nlog(environment.FESTINA_DEFINITELY_UNSET_VAR)\nlog('after')")
        assert result.stdout.splitlines() == ["before", "", "after"]

    def test_computed_access_with_a_variable_key(self, compile_and_run):
        source = "text k = 'FESTINA_TEST_VAR'\nlog(environment[k])"
        result = compile_and_run(source, env={"FESTINA_TEST_VAR": "computed"})
        assert result.stdout.strip() == "computed"

    def test_null_check_pattern(self, compile_and_run, monkeypatch):
        monkeypatch.delenv("FESTINA_DEFINITELY_UNSET_VAR", raising=False)
        source = """
        text apiKey = environment.FESTINA_DEFINITELY_UNSET_VAR
        if apiKey == null {
            log('not set')
        } else {
            log('set')
        }
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "not set"


class TestSqliteQueries:
    """claude.md #32-34: sqlite() queries, parameterized queries, and
    query result types (arr[Table])."""

    def test_select_into_arr_table_with_field_access(self, compile_and_run):
        source = """
        table People {
            id:int
            name:text
            score:float
        }
        sqlite('INSERT INTO People (id, name, score) VALUES (?, ?, ?)', [1, 'Patrick', 9.5])
        sqlite('INSERT INTO People (id, name, score) VALUES (?, ?, ?)', [2, 'Ada', 10.0])
        arr[People] people = sqlite('SELECT * FROM People ORDER BY id')
        log(people[0].id)
        log(people[0].name)
        log(people[0].score)
        log(people[1].name)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["1", "Patrick", "9.5", "Ada"]

    def test_parameterized_insert_and_select(self, compile_and_run):
        # claude.md #33's own example: sqlite(sql, [1, 'Patrick']) -- a
        # heterogeneously-typed literal bound as parameters.
        source = """
        table People {
            id:int
            name:text
        }
        sqlite('INSERT INTO People (id, name) VALUES (?, ?)', [1, 'Patrick'])
        sqlite('INSERT INTO People (id, name) VALUES (?, ?)', [2, 'Ada'])
        arr[People] found = sqlite('SELECT * FROM People WHERE id = ?', [2])
        log(found[0].name)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "Ada"

    def test_null_column_value_round_trips(self, compile_and_run):
        # claude.md #57: a SQL NULL column comes back as the same
        # reserved null sentinel used elsewhere for a null float (a quiet
        # NaN -- see test_float_division_by_zero_returns_null and the
        # module docstring's "Null for int/float" note), not a crash or a
        # 0.0.
        source = """
        table People {
            id:int
            score:float
        }
        sqlite('INSERT INTO People (id, score) VALUES (?, ?)', [1, null])
        arr[People] people = sqlite('SELECT * FROM People')
        log(people[0].score)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "nan"

    def test_columns_map_by_position_not_name(self, compile_and_run):
        # claude.md #34: "The table declaration defines the expected
        # fields and their types" -- a query returning the same columns
        # in the same order (even via a differently-aliased SELECT) still
        # maps positionally onto the declared table.
        source = """
        table People {
            id:int
            name:text
        }
        sqlite('INSERT INTO People (id, name) VALUES (?, ?)', [1, 'Patrick'])
        arr[People] people = sqlite('SELECT id AS whatever, name AS anything FROM People')
        log(people[0].name)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "Patrick"

    def test_exec_only_query_discards_result(self, compile_and_run):
        # A statement whose result isn't captured into an arr[Table]
        # (here, INSERT) just runs to completion.
        source = """
        table People {
            id:int
        }
        sqlite('INSERT INTO People (id) VALUES (1)')
        log('done')
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "done"

    def test_non_literal_params_argument_is_a_clear_error(self, parser, semantic, codegen, errors):
        # claude.md #33's params must be a literal array -- see the
        # module docstring's "Query rows" note for why (a real arr[T]
        # value can't represent claude.md's own heterogeneously-typed
        # example).
        source = """
        table People {
            id:int
        }
        arr[int] ids = [1]
        sqlite('SELECT * FROM People WHERE id = ?', ids)
        """
        program = parser.parse(source, filename="main.f")
        analyzed = semantic.analyze(program, filename="main.f")
        with pytest.raises(errors.CompileError, match="literal array"):
            codegen.generate_ir(program, analyzed, filename="main.f")

    def test_schema_sync_and_query_against_same_table_do_not_collide(self, compile_and_run):
        # _table_arrays' globals are shared between schema sync (main())
        # and query codegen -- this would previously have redefined the
        # same LLVM globals (a link error) if not cached.
        source = """
        table People {
            id:int
            name:text
        }
        sqlite('INSERT INTO People (id, name) VALUES (?, ?)', [1, 'Patrick'])
        arr[People] people = sqlite('SELECT * FROM People')
        log(people[0].name)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "Patrick"


class TestMinimalRuntimeDependencies:
    """"Real compilation, minimal setup" stage 1: a compiled program
    shouldn't need libsqlite3.so installed on the machine that runs it,
    if a static sqlite3 archive was available at compile time (see
    festina/cli.py's _sqlite_link_flags)."""

    def test_compiled_binary_does_not_dynamically_link_libsqlite3(self, compile_and_run, tmp_path):
        # compile_and_run's own fixture skip (no clang) covers this test
        # too; ldd is the other half of the toolchain this needs.
        if not shutil.which("ldd"):
            pytest.skip("ldd not on PATH -- cannot inspect the binary's dynamic dependencies")
        from festina import cli as cli_mod

        _, statically_linked = cli_mod._sqlite_link_flags(shutil.which("clang"))
        if not statically_linked:
            pytest.skip("no static libsqlite3.a available in this environment -- "
                        "_sqlite_link_flags already fell back to dynamic linking, "
                        "which is the correct behavior, just not what this test checks")

        compile_and_run("table People {\n    id:int\n}\nlog('built')")
        binary = tmp_path / "program"
        assert binary.exists()

        ldd_output = subprocess.run(["ldd", str(binary)], capture_output=True, text=True).stdout
        assert "libsqlite3" not in ldd_output


class TestSlimBinaries:
    """claude.md #59's binary-slimming requirement: "if a canvas isn't
    used, make sure the binary remains slim". The runtime is split into
    core/graphics/audio translation units (runtime/festina_runtime.c/
    _graphics.c/_audio.c) specifically so a compiled program's `cc`
    invocation only ever gets -lcairo/-lX11/-lasound on its command line
    when that program actually uses graphics/audio (see festina/cli.py's
    _runtime_objects_and_link_libs, driven by CodeGen.uses_graphics/
    uses_graphics_code/uses_audio) -- confirmed here by inspecting the
    linked binary's actual dynamic dependencies (ldd), not just that
    compilation succeeded, since --gc-sections/--as-needed alone was
    verified NOT to remove an unused library from a single-object-file
    build (the linker's "is this needed" decision is made against the
    whole translation unit before dead-code elimination runs)."""

    def _ldd(self, binary):
        if not shutil.which("ldd"):
            pytest.skip("ldd not on PATH -- cannot inspect the binary's dynamic dependencies")
        return subprocess.run(["ldd", str(binary)], capture_output=True, text=True).stdout

    def test_graphics_and_audio_free_binary_links_neither(self, compile_and_run, tmp_path):
        compile_and_run("log('hi')")
        ldd_output = self._ldd(tmp_path / "program")
        assert "libcairo" not in ldd_output
        assert "libX11" not in ldd_output
        assert "libasound" not in ldd_output

    def test_graphics_binary_links_cairo_and_x11_but_not_alsa(self, tmp_path):
        if not (shutil.which("clang") or shutil.which("gcc") or shutil.which("cc")):
            pytest.skip("no C compiler (clang/gcc/cc) on PATH")
        from festina import cli as cli_mod

        src = tmp_path / "main.f"
        src.write_text("drawRect(1, 1, 2, 2)")
        out = tmp_path / "program"
        cli_mod.compile_file(str(src), str(out), cc=shutil.which("clang") or shutil.which("gcc") or shutil.which("cc"))
        ldd_output = self._ldd(out)
        assert "libcairo" in ldd_output
        assert "libX11" in ldd_output
        assert "libasound" not in ldd_output

    def test_audio_binary_links_alsa_but_not_cairo_or_x11(self, tmp_path):
        if not (shutil.which("clang") or shutil.which("gcc") or shutil.which("cc")):
            pytest.skip("no C compiler (clang/gcc/cc) on PATH")
        from festina import cli as cli_mod

        # Loading a nonexistent file fails at *runtime*, not compile
        # time (claude.md #38's loadAudio() has no compile-time path
        # validation) -- irrelevant here, this only checks what got
        # linked, never runs the binary.
        src = tmp_path / "main.f"
        src.write_text("aud music = loadAudio('nonexistent.wav')")
        out = tmp_path / "program"
        cli_mod.compile_file(str(src), str(out), cc=shutil.which("clang") or shutil.which("gcc") or shutil.which("cc"))
        ldd_output = self._ldd(out)
        assert "libasound" in ldd_output
        assert "libcairo" not in ldd_output
        assert "libX11" not in ldd_output

    def test_loadimage_alone_still_links_successfully_without_opening_a_window(self, tmp_path):
        # Regression test: loadImage() deliberately does NOT set
        # CodeGen.uses_graphics (see _emit_graphics_call's doc comment --
        # decoding a PNG needs no X server), but festina_load_image()
        # still lives in the graphics object file, not core. Without
        # CodeGen.uses_graphics_code as a separate, broader linking
        # signal, this compiled fine but failed to *link*
        # ("undefined reference to festina_load_image").
        if not (shutil.which("clang") or shutil.which("gcc") or shutil.which("cc")):
            pytest.skip("no C compiler (clang/gcc/cc) on PATH")
        from festina import cli as cli_mod

        src = tmp_path / "main.f"
        src.write_text("img icon = loadImage('nonexistent.png')")
        out = tmp_path / "program"
        cli_mod.compile_file(str(src), str(out), cc=shutil.which("clang") or shutil.which("gcc") or shutil.which("cc"))
        assert out.exists()


class TestMinimalBuildDependencies:
    """"Real compilation, minimal setup" stage 3: using Festina shouldn't
    require clang *specifically*. Before this stage, cc had to be clang
    because it was handed the .ll file directly (the only common
    compiler with an LLVM-IR-text frontend); now festina.llvm_backend
    compiles that step itself via libLLVM, so cc's remaining job --
    compiling festina_runtime.c and linking plain object files -- is
    compiler-agnostic. See festina/cli.py's module docstring."""

    def test_gcc_works_as_cc_when_libllvm_is_available(self, parser, semantic, codegen, tmp_path, llvm_backend):
        if not shutil.which("gcc"):
            pytest.skip("gcc not on PATH")
        if not llvm_backend.available():
            pytest.skip("libLLVM unavailable -- gcc-as-cc only works via the "
                        "libLLVM path (the clang-IR-frontend fallback needs clang)")
        from festina import cli as cli_mod

        src = tmp_path / "main.f"
        src.write_text("table People {\n    id:int\n    name:text\n}\nlog('built with gcc')")
        out = tmp_path / "program"
        cli_mod.compile_file(str(src), str(out), cc="gcc")
        assert out.exists()

        result = subprocess.run([str(out)], cwd=tmp_path, capture_output=True, text=True, timeout=15)
        assert result.returncode == 0
        assert result.stdout.strip() == "built with gcc"

    def test_falls_back_to_clang_ir_frontend_when_libllvm_unavailable(self, parser, semantic, codegen, tmp_path, monkeypatch):
        clang = shutil.which("clang")
        if not clang:
            pytest.skip("clang not on PATH -- nothing to fall back to")
        from festina import cli as cli_mod, llvm_backend

        class _Unavailable:
            lib = None

        monkeypatch.setattr(llvm_backend, "_binding_instance", _Unavailable())
        assert llvm_backend.available() is False

        src = tmp_path / "main.f"
        src.write_text("log('built via fallback')")
        out = tmp_path / "program"
        cli_mod.compile_file(str(src), str(out), cc=clang)
        assert out.exists()

        result = subprocess.run([str(out)], cwd=tmp_path, capture_output=True, text=True, timeout=15)
        assert result.returncode == 0
        assert result.stdout.strip() == "built via fallback"


class TestMissingDependencyErrors:
    """claude.md #59: a genuinely missing dependency must fail with a
    clear, actionable message naming it and how to install it -- not a
    raw exception. Verified directly: subprocess.run(..., check=False)
    does *not* catch "the executable doesn't exist at all" the way it
    catches a nonzero exit code, so this needs its own handling
    (festina/cli.py's _run_tool). path_without is a shared conftest.py
    fixture -- test_cli.py's TestDoctor reuses it too."""

    def test_missing_pkg_config_gives_actionable_error(self, parser, semantic, codegen, cli_mod, errors, tmp_path, path_without, monkeypatch):
        path_without("pkg-config")
        # claude.md #59's per-feature object file split (see
        # festina/cli.py's _RUNTIME_FEATURES) means a graphics/audio-free
        # program like this one never calls pkg-config for cairo-xlib/
        # alsa at all -- only sqlite3, which is always needed. sqlite3's
        # own flags are cached process-wide (_sqlite_link_cache, keyed by
        # `cc`) across every compile_file call in this test session, so
        # without clearing it here, an earlier test's successful compile
        # would make this one a silent cache hit that never calls
        # pkg-config, never noticing it's missing.
        monkeypatch.setattr(cli_mod, "_sqlite_link_cache", {})
        src = tmp_path / "main.f"
        src.write_text("log('hi')")
        with pytest.raises(errors.CompileError, match="pkg-config.*install"):
            cli_mod.compile_file(str(src), str(tmp_path / "out"), cc="clang")

    def test_missing_cc_gives_actionable_error(self, parser, semantic, codegen, cli_mod, errors, tmp_path, path_without):
        path_without("clang", "gcc", "cc")
        src = tmp_path / "main.f"
        src.write_text("log('hi')")
        with pytest.raises(errors.CompileError, match="clang.*install"):
            cli_mod.compile_file(str(src), str(tmp_path / "out"), cc="clang")
