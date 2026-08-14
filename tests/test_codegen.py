"""Code generation -- claude.md #47 (executable generation), plus the
runtime-facing halves of #7/#8 (entry point + startup), #26 (arrays),
#28-34 (automatic SQLite schema sync, sqlite() queries), #41/#42
(log/fail), #45 (string interpolation).

Two kinds of tests here:

- CodegenError tests for not-yet-implemented constructs (graphics,
  audio, events) only need festina.codegen itself -- no C toolchain
  required, so they always run.
- End-to-end tests actually compile a Festina program to a native
  executable (via the `compile_and_run` fixture) and check its real
  stdout/exit code/festina.sqlite -- these skip cleanly if no C compiler
  is on PATH, since that's an environment limitation, not a missing
  Festina feature.
"""
import shutil
import sqlite3
import subprocess

import pytest


# ---- not-yet-implemented constructs raise clearly, no toolchain needed ----

class TestNotImplementedYet:
    def _generate(self, parser, semantic, codegen, source, filename="main.f"):
        program = parser.parse(source, filename=filename)
        analyzed = semantic.analyze(program, filename=filename)
        return codegen.generate_ir(program, analyzed, filename=filename)

    def test_graphics_call_is_not_implemented(self, parser, semantic, codegen, errors):
        with pytest.raises(errors.CompileError, match="drawRect"):
            self._generate(parser, semantic, codegen, "drawRect(0, 0, 10, 10)")

    def test_event_handler_is_not_implemented(self, parser, semantic, codegen, errors):
        with pytest.raises(errors.CompileError, match="event handler"):
            self._generate(parser, semantic, codegen, "on click(x:int, y:int) {\n    log(x)\n}")

    def test_img_declaration_is_not_implemented(self, parser, semantic, codegen, errors):
        with pytest.raises(errors.CompileError):
            self._generate(parser, semantic, codegen, "img profile = loadImage('a.png')")


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
        # claude.md has no `break`/`continue` -- the only documented way
        # out of an infinite loop's body is `return` from the enclosing
        # function (see festina/codegen.py's module docstring).
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
    (festina/cli.py's _run_tool)."""

    @pytest.fixture
    def path_without(self, tmp_path, monkeypatch):
        """A PATH containing everything currently on PATH except the
        named tool(s), by symlinking every other resolvable tool into an
        empty dir and pointing PATH at just that dir."""
        def _make(*hidden_tools):
            bin_dir = tmp_path / "bin_without_tool"
            bin_dir.mkdir()
            needed = {"python3", "bash", "sh", "env", "dirname", "basename",
                      "pkg-config", "clang", "gcc", "cc", "ld", "as"}
            for name in needed - set(hidden_tools):
                found = shutil.which(name)
                if found:
                    (bin_dir / name).symlink_to(found)
            monkeypatch.setenv("PATH", str(bin_dir))
            return str(bin_dir)
        return _make

    def test_missing_pkg_config_gives_actionable_error(self, parser, semantic, codegen, cli_mod, errors, tmp_path, path_without):
        path_without("pkg-config")
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
