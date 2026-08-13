"""Code generation -- claude.md #47 (executable generation), plus the
runtime-facing halves of #7/#8 (entry point + startup), #26 (arrays),
#28-31 (automatic SQLite schema sync), #41/#42 (log/fail), #45 (string
interpolation).

Two kinds of tests here:

- CodegenError tests for not-yet-implemented constructs (sqlite()
  queries, graphics, audio, events) only need festina.codegen itself --
  no C toolchain required, so they always run.
- End-to-end tests actually compile a Festina program to a native
  executable (via the `compile_and_run` fixture) and check its real
  stdout/exit code/festina.sqlite -- these skip cleanly if no C compiler
  is on PATH, since that's an environment limitation, not a missing
  Festina feature.
"""
import sqlite3

import pytest


# ---- not-yet-implemented constructs raise clearly, no toolchain needed ----

class TestNotImplementedYet:
    def _generate(self, parser, semantic, codegen, source, filename="main.f"):
        program = parser.parse(source, filename=filename)
        analyzed = semantic.analyze(program, filename=filename)
        return codegen.generate_ir(program, analyzed, filename=filename)

    def test_sqlite_query_is_not_implemented(self, parser, semantic, codegen, errors):
        source = "table People {\n    id:int\n}\narr[People] people = sqlite('SELECT * FROM People')"
        with pytest.raises(errors.CompileError, match="sqlite"):
            self._generate(parser, semantic, codegen, source)

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
    from a literal, read and written by index. No .length, no growth, no
    loop construct -- claude.md doesn't specify any of those (see the
    module docstring in festina/codegen.py), so they aren't implemented."""

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
