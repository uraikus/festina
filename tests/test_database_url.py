"""claude.md #70: DatabaseURL = <expr>, the entry file's own first
statement.

Covers festina.imports.build_program's extraction/position validation
directly (it needs real files on disk, unlike everything else in this
suite) and semantic.py's type check on the extracted expression --
see tests/test_codegen.py's TestDatabaseURL for the real
compile-and-run end-to-end coverage (the actual database path used).
"""
import pytest


class TestDatabaseURLPosition:
    def test_as_the_only_statement_is_extracted(self, imports_mod, write_source):
        root = write_source({"main.f": "DatabaseURL = 'custom.sqlite'\nlog('hi')"})
        program = imports_mod.build_program(str(root / "main.f"))
        assert program.database_url is not None
        # The directive itself must not remain as an ordinary statement.
        assert len(program.body) == 1

    def test_not_present_leaves_database_url_none(self, imports_mod, write_source):
        root = write_source({"main.f": "log('hi')"})
        program = imports_mod.build_program(str(root / "main.f"))
        assert program.database_url is None

    def test_after_other_code_is_a_compile_error(self, imports_mod, write_source, errors):
        root = write_source({"main.f": "log('hi')\nDatabaseURL = 'late.sqlite'"})
        with pytest.raises(errors.CompileError, match="first statement"):
            imports_mod.build_program(str(root / "main.f"))

    def test_after_an_import_is_a_compile_error(self, imports_mod, write_source, errors):
        root = write_source({
            "lib.f": "int x = 1",
            "main.f": "import lib.f\nDatabaseURL = 'late.sqlite'",
        })
        with pytest.raises(errors.CompileError, match="first statement"):
            imports_mod.build_program(str(root / "main.f"))

    def test_in_an_imported_file_is_not_recognized(self, imports_mod, write_source):
        # claude.md #70: "DatabaseURL has no effect in an imported
        # file" -- it just flows through as an ordinary (and, since
        # DatabaseURL is never a real declared variable, ultimately
        # invalid) statement instead of being extracted.
        root = write_source({
            "lib.f": "DatabaseURL = 'sneaky.sqlite'",
            "main.f": "import lib.f\nlog('hi')",
        })
        program = imports_mod.build_program(str(root / "main.f"))
        assert program.database_url is None

    def test_before_the_first_import_is_still_valid(self, imports_mod, write_source):
        root = write_source({
            "lib.f": "int x = 1",
            "main.f": "DatabaseURL = 'first.sqlite'\nimport lib.f\nlog('hi')",
        })
        program = imports_mod.build_program(str(root / "main.f"))
        assert program.database_url is not None


class TestDatabaseURLType:
    def test_string_literal_is_valid(self, parser, semantic, imports_mod, write_source):
        root = write_source({"main.f": "DatabaseURL = 'custom.sqlite'\nlog('hi')"})
        program = imports_mod.build_program(str(root / "main.f"))
        semantic.analyze(program, filename=str(root / "main.f"))

    def test_template_string_is_valid(self, semantic, imports_mod, write_source):
        root = write_source({"main.f": "DatabaseURL = `custom.sqlite`\nlog('hi')"})
        program = imports_mod.build_program(str(root / "main.f"))
        semantic.analyze(program, filename=str(root / "main.f"))

    def test_non_text_value_is_a_compile_error(self, semantic, imports_mod, write_source, errors):
        root = write_source({"main.f": "DatabaseURL = 5\nlog('hi')"})
        program = imports_mod.build_program(str(root / "main.f"))
        with pytest.raises(errors.CompileError, match="must be text"):
            semantic.analyze(program, filename=str(root / "main.f"))
