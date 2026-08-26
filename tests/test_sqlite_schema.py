"""claude.md #29-34, #46: automatic SQLite database, type mapping, table
creation and schema synchronization, queries."""
import pytest


class TestTypeMapping:
    """claude.md #30: Festina -> SQLite primitive type mapping."""

    def test_type_map_matches_spec(self, sqlite_schema):
        assert sqlite_schema.TYPE_MAP == {
            "int": "INTEGER",
            "float": "REAL",
            "bool": "INTEGER",
            "text": "TEXT",
            "blob": "BLOB",
        }


class TestSchemaSyncPlan:
    """claude.md #28, #31: create missing tables, add missing columns,
    remove undeclared columns, update incompatible columns, preserve data."""

    def test_missing_table_is_created(self, sqlite_schema):
        declared = {"id": "int", "name": "text"}
        plan = sqlite_schema.plan_sync(declared, existing=None)
        assert plan.create is True
        assert plan.add_columns == {}
        assert plan.drop_columns == []

    def test_matching_table_needs_no_changes(self, sqlite_schema):
        declared = {"id": "int", "name": "text"}
        existing = {"id": "INTEGER", "name": "TEXT"}
        plan = sqlite_schema.plan_sync(declared, existing)
        assert plan.create is False
        assert plan.add_columns == {}
        assert plan.drop_columns == []
        assert plan.alter_columns == {}

    def test_missing_column_is_added(self, sqlite_schema):
        declared = {"id": "int", "name": "text", "age": "int"}
        existing = {"id": "INTEGER", "name": "TEXT"}
        plan = sqlite_schema.plan_sync(declared, existing)
        assert plan.add_columns == {"age": "int"}

    def test_obsolete_column_is_removed(self, sqlite_schema):
        # claude.md #31 worked example: People(id, name, obsolete) ->
        # People(id, name).
        declared = {"id": "int", "name": "text"}
        existing = {"id": "INTEGER", "name": "TEXT", "obsolete": "TEXT"}
        plan = sqlite_schema.plan_sync(declared, existing)
        assert plan.drop_columns == ["obsolete"]

    def test_column_rename_via_declaration_change(self, sqlite_schema):
        # claude.md #31 worked example: People(id, name) redeclared with
        # `full_name` instead of `name` -- from the schema's point of view
        # `name` is now undeclared and `full_name` is new.
        declared = {"id": "int", "full_name": "text"}
        existing = {"id": "INTEGER", "name": "TEXT"}
        plan = sqlite_schema.plan_sync(declared, existing)
        assert plan.add_columns == {"full_name": "text"}
        assert plan.drop_columns == ["name"]

    def test_incompatible_column_type_is_altered(self, sqlite_schema):
        declared = {"id": "int", "age": "text"}
        existing = {"id": "INTEGER", "age": "INTEGER"}
        plan = sqlite_schema.plan_sync(declared, existing)
        assert plan.alter_columns == {"age": "text"}


class TestSqliteQuerySyntax:
    """claude.md #32, #33: sqlite() global function, no import required,
    parameterized queries use an array of parameters."""

    def test_plain_query_parses(self, parser):
        source = """
        table People {
            id:int
            name:text
        }
        arr[People] people = sqlite('SELECT * FROM People')
        """
        parser.parse(source)

    def test_parameterized_query_parses(self, parser):
        source = "sqlite('INSERT INTO People (id, name) VALUES (?, ?)', [1, 'Patrick'])"
        parser.parse(source)


class TestQueryResultTypes:
    """claude.md #34: a query against a declared table type-resolves to
    arr[TableType]."""

    def test_query_result_resolves_to_array_of_table_type(self, parser, semantic, types_mod):
        source = """
        table People {
            id:int
            name:text
        }
        arr[People] people = sqlite('SELECT * FROM People')
        """
        program = parser.parse(source)
        analyzed = semantic.analyze(program)
        resolved = analyzed.symbols["people"].type
        assert resolved == types_mod.ArrayType(types_mod.TableType("People"))


class TestTableRowRequiresAnInitializer:
    """claude.md #178: a table-typed value is a BORROWED handle onto
    one row of a query result -- it has never had its own standalone
    allocation story the way struct has (no auto-vivify, never
    retained/released/freed on its own). `Table t` with no initializer
    used to default to null exactly like any other pointer-shaped
    type, and codegen's own field-write path (_member_ptr_from's
    TableType branch) computed a plain byte-offset GEP off that
    pointer with no null check at all -- so the very first `t.field =
    ...` on one segfaulted. Confirmed directly: compiling and running
    the exact reproduction below crashed with SIGSEGV, not a clean
    error, before this check existed.

    Rejected here instead of taught to auto-vivify like a struct would
    -- doing that would mean inventing real ownership/allocation
    semantics for TableType that don't exist anywhere else in this
    compiler. `struct` already covers a hand-built aggregate (api.md's
    own 'Structs as query targets' section) and needs no such check,
    since a struct-typed local already always gets real, zeroed
    storage immediately (codegen.py's own VarDecl StructType branch)."""

    def test_a_bare_table_row_declaration_is_rejected(self, parser, semantic, errors):
        program = parser.parse("""
        table People {
            id:int
            name:text
        }
        People p
        p.id = 5
        """)
        with pytest.raises(errors.CompileError, match="requires an initializer"):
            semantic.analyze(program)

    def test_a_global_bare_table_row_declaration_is_also_rejected(self, parser, semantic, errors):
        program = parser.parse("""
        table People {
            id:int
            name:text
        }
        People p
        """)
        with pytest.raises(errors.CompileError, match="requires an initializer"):
            semantic.analyze(program)

    def test_the_error_points_at_struct_as_the_hand_built_alternative(self, parser, semantic, errors):
        program = parser.parse("""
        table People {
            id:int
            name:text
        }
        People p
        """)
        with pytest.raises(errors.CompileError, match="declare a struct"):
            semantic.analyze(program)

    def test_assigning_from_an_existing_row_is_unaffected(self, parser, semantic):
        # The legitimate, documented pattern this check must not break:
        # a table-typed local aliasing a REAL row a query already
        # produced.
        source = """
        table People {
            id:int
            name:text
        }
        arr[People] rows = sqlite('SELECT * FROM People')
        People p = rows[0]
        p.name = 'Updated'
        """
        semantic.analyze(parser.parse(source))

    def test_a_table_typed_function_parameter_is_unaffected(self, parser, semantic):
        # A parameter always gets a real argument from its caller --
        # this check is about a bare, initializer-less DECLARATION,
        # never a parameter.
        source = """
        table People {
            id:int
            name:text
        }
        void func touch(p:People) {
            p.name = 'Touched'
        }
        """
        semantic.analyze(parser.parse(source))

    def test_a_table_row_for_loop_variable_still_needs_an_initializer(self, parser, semantic, errors):
        # analyze_var_decl is reused for a for-loop's own init clause,
        # so the identical rule applies there -- a for loop obviously
        # never omits the "= ..." in real code, but the check should
        # still fire consistently if it somehow did.
        program = parser.parse("""
        table People {
            id:int
            name:text
        }
        arr[People] rows = sqlite('SELECT * FROM People')
        for People p, false, 0 { }
        """)
        with pytest.raises(errors.CompileError, match="requires an initializer"):
            semantic.analyze(program)


class TestTableRowSegfaultEndToEnd:
    """The same claude.md #178 fix, confirmed through the real compiler
    and a real running binary -- not just semantic.analyze() in
    isolation -- since the original bug was a runtime SIGSEGV, not a
    compile-time crash, and only ever showed up once actual machine
    code ran."""

    def test_a_bare_table_row_declaration_is_rejected_at_compile_time(
            self, cli_mod, errors, tmp_path):
        src = tmp_path / "main.f"
        src.write_text("""
        table People {
            id:int
            name:text
        }
        People p
        p.id = 5
        """, encoding="utf-8")
        with pytest.raises(errors.CompileError, match="requires an initializer"):
            cli_mod.compile_file(str(src), str(tmp_path / "out"))

    def test_assigning_a_real_query_row_field_still_compiles_and_runs(
            self, compile_and_run):
        source = """
        table People {
            id:int
            name:text
        }
        sqlite('INSERT INTO People (id, name) VALUES (?, ?)', [1, 'Patrick'])
        arr[People] rows = sqlite('SELECT * FROM People')
        People p = rows[0]
        p.name = 'Updated'
        log(p.name)
        log(rows[0].id)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "Updated\n1\n"


class TestNoManualInitialization:
    """claude.md #29, #46: no explicit db creation/open/init/path config
    is part of the language -- these are not reserved/builtin names."""

    @pytest.mark.parametrize("call", [
        "openDatabase('festina.sqlite')",
        "initSqlite()",
        "connectDatabase()",
    ])
    def test_manual_db_setup_functions_are_not_builtins(self, parser, semantic, errors, call):
        program = parser.parse(call)
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)
