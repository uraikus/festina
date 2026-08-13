"""claude.md #5 (imports), #6 (import resolution)."""
import pytest


class TestImportSyntax:
    """claude.md #5: `import file.f`, no ES-module or require() syntax."""

    def test_import_statement_parses(self, parser):
        parser.parse("import database.f\nimport ui.f")

    def test_es_module_import_syntax_is_rejected(self, parser, errors):
        with pytest.raises(errors.CompileError):
            parser.parse("import { thing } from 'database.f'")

    def test_require_is_rejected(self, parser, errors):
        with pytest.raises(errors.CompileError):
            parser.parse("const db = require('database.f')")


class TestImportResolution:
    """claude.md #6: recursive resolution, single processing per file,
    canonical-path deduplication, circular import detection."""

    def test_recursive_dependency_order_is_resolved(self, imports_mod, write_source):
        root = write_source({
            "main.f": "import ui.f\nimport database.f\n",
            "ui.f": "import graphics.f\n",
            "graphics.f": "log('graphics')\n",
            "database.f": "log('database')\n",
        })
        order = imports_mod.resolve_imports(str(root / "main.f"))
        names = [p.split("/")[-1] for p in order]
        # Every dependency must appear, each exactly once, and a file must
        # come after everything it imports.
        assert sorted(names) == sorted({"main.f", "ui.f", "graphics.f", "database.f"})
        assert names.index("graphics.f") < names.index("ui.f")
        assert names.index("ui.f") < names.index("main.f")
        assert names.index("database.f") < names.index("main.f")

    def test_each_file_is_processed_only_once(self, imports_mod, write_source):
        # database.f is imported both directly and transitively via ui.f;
        # it must appear exactly once in the resolved order.
        root = write_source({
            "main.f": "import ui.f\nimport database.f\n",
            "ui.f": "import database.f\n",
            "database.f": "log('database')\n",
        })
        order = imports_mod.resolve_imports(str(root / "main.f"))
        names = [p.split("/")[-1] for p in order]
        assert names.count("database.f") == 1

    def test_canonical_paths_are_deduplicated(self, imports_mod, write_source):
        # ./utils.f and src/../utils.f resolve to the same canonical file
        # and must not be imported twice.
        root = write_source({
            "main.f": "import ./utils.f\nimport src/../utils.f\n",
            "utils.f": "log('utils')\n",
            "src/placeholder.f": "",
        })
        order = imports_mod.resolve_imports(str(root / "main.f"))
        names = [p.split("/")[-1] for p in order]
        assert names.count("utils.f") == 1

    def test_circular_import_is_detected_without_infinite_recursion(self, imports_mod, errors, write_source):
        root = write_source({
            "a.f": "import b.f\n",
            "b.f": "import a.f\n",
        })
        with pytest.raises(errors.CompileError):
            imports_mod.resolve_imports(str(root / "a.f"))

    def test_self_import_is_detected(self, imports_mod, errors, write_source):
        root = write_source({
            "a.f": "import a.f\n",
        })
        with pytest.raises(errors.CompileError):
            imports_mod.resolve_imports(str(root / "a.f"))
