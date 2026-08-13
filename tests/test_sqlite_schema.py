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
