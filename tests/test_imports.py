"""claude.md #5 (imports), #6 (import resolution)."""
import os

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


class TestBuildProgram:
    """claude.md #5: "An import includes the specified file and all of
    its dependencies in the current compilation unit" -- build_program
    merges every file in the import graph into one ast.Program, in
    dependency order, so cross-file declarations (structs, tables,
    functions, globals) resolve like they were one file all along."""

    def test_merges_every_file_in_dependency_order(self, imports_mod, write_source):
        root = write_source({
            "main.f": "import database.f\nlog('main')\n",
            "database.f": "log('database')\n",
        })
        program = imports_mod.build_program(str(root / "main.f"))
        # database.f's log() comes before main.f's own statements
        # (its own `import database.f` line included, unchanged --
        # ImportDecl is already a no-op in both semantic.py and
        # codegen.py), matching resolve_imports' dependency-first order.
        kinds = [type(stmt).__name__ for stmt in program.body]
        assert kinds == ["ExprStmt", "ImportDecl", "ExprStmt"]
        assert program.body[0].file.endswith("database.f")
        assert program.body[1].file.endswith("main.f")
        assert program.body[2].file.endswith("main.f")

    def test_cross_file_struct_reference_resolves(self, imports_mod, semantic, write_source):
        # A struct declared in one file, used as a function parameter
        # type in another -- only works at all if they're genuinely one
        # compilation unit (claude.md #5), not separately-analyzed files.
        root = write_source({
            "main.f": "import shapes.f\nPoint p\np.x = 1\nlog(describe(p))\n",
            "shapes.f": (
                "struct Point {\n    x:int\n}\n"
                "text func describe(p:Point) {\n    return `x=${p.x}`\n}\n"
            ),
        })
        program = imports_mod.build_program(str(root / "main.f"))
        analyzed = semantic.analyze(program, filename=str(root / "main.f"))
        assert "Point" in analyzed.structs
        assert "describe" in analyzed.symbols

    def test_duplicate_declaration_across_files_is_rejected(self, imports_mod, semantic, errors, write_source):
        root = write_source({
            "main.f": "import other.f\nint counter = 5\n",
            "other.f": "int counter = 0\n",
        })
        program = imports_mod.build_program(str(root / "main.f"))
        with pytest.raises(errors.CompileError, match="already declared"):
            semantic.analyze(program, filename=str(root / "main.f"))

    def test_error_in_an_imported_file_names_that_file_not_the_entry_file(
        self, imports_mod, semantic, errors, write_source
    ):
        root = write_source({
            "main.f": "import broken.f\nlog('start')\n",
            "broken.f": "log(undefinedVariable)\n",
        })
        entry = str(root / "main.f")
        program = imports_mod.build_program(entry)
        with pytest.raises(errors.CompileError) as exc_info:
            semantic.analyze(program, filename=entry)
        assert "broken.f" in str(exc_info.value)
        # CompileError.__str__ is "{file}:{line}:{column}: error: {message}"
        # (festina/errors.py) -- exactly 4 colons past the file path
        # (line/column/"error:"/message boundaries), so rsplit(":", 4)
        # peels off precisely those 4 pieces from the right regardless
        # of what's in the path, leaving the file intact even when the
        # path itself contains a colon (a Windows drive letter, e.g.
        # "D:\...\broken.f", which a naive split(":")[0] would instead
        # truncate to "D").
        assert str(exc_info.value).rsplit(":", 4)[0].endswith("broken.f")

    def test_single_file_program_is_unaffected(self, imports_mod, semantic, write_source):
        # No imports at all -- build_program should behave exactly like
        # parsing that one file directly (the degenerate case).
        root = write_source({"main.f": "log('solo')\n"})
        program = imports_mod.build_program(str(root / "main.f"))
        assert len(program.body) == 1
        semantic.analyze(program, filename=str(root / "main.f"))


class TestParseCache:
    """claude.md #253: a disk-persisted lex/parse cache for repeat
    `festina compile` invocations, keyed by exact source content (not
    mtime) plus a "grammar epoch" hash of festina's own lexer/parser/
    ast/imports source -- see festina/imports.py's own _parse_cached
    for the full design. Correctness never depends on the cache
    working: every failure mode (missing, corrupt, cross-version) must
    degrade silently to an ordinary fresh parse."""

    @pytest.fixture(autouse=True)
    def _isolated_parse_cache_dir(self, tmp_path, monkeypatch, imports_mod):
        """Every test here gets its own empty parse-cache directory.
        `tempfile.gettempdir()/festina-parse-cache` is a REAL directory
        that survives across separate pytest processes -- and even
        across unrelated runs of this very suite on this very machine
        -- so sharing it directly let a leftover entry from an earlier
        run silently satisfy a test expecting a fresh cache miss (this
        is exactly what broke `test_changing_a_files_content_reparses_
        only_that_file` and `test_a_different_grammar_epoch_is_never_
        served_from_the_old_one` under a full-suite run: byte-identical
        fixture content like "log('hi')\\n" recurs across tests, and a
        pickle left over from a previous invocation was still sitting
        on disk). In-process monkeypatching of `tempfile.gettempdir` is
        enough for every test in this class except
        `test_a_dependencys_behavior_change_is_reflected_end_to_end`,
        which shells out to a real `festina` subprocess -- but that one
        never asserts on cache-hit *counts*, only on correctly
        recompiled output, so it is unaffected either way."""
        monkeypatch.setattr(imports_mod.tempfile, "gettempdir", lambda: str(tmp_path))

    def _clear_calls(self, monkeypatch, imports_mod):
        """Wraps parser_mod.parse to count real (non-cached) parses,
        returning the list calls get appended to."""
        calls = []
        real_parse = imports_mod.parser_mod.parse

        def counting_parse(*args, **kwargs):
            calls.append(1)
            return real_parse(*args, **kwargs)

        monkeypatch.setattr(imports_mod.parser_mod, "parse", counting_parse)
        return calls

    def test_a_second_build_hits_the_cache(self, imports_mod, write_source, monkeypatch):
        monkeypatch.delenv("FESTINA_NO_PARSE_CACHE", raising=False)
        root = write_source({"main.f": "log('hi')\n"})
        imports_mod.build_program(str(root / "main.f"))  # warms the cache
        calls = self._clear_calls(monkeypatch, imports_mod)
        imports_mod.build_program(str(root / "main.f"))
        assert calls == []

    def test_changing_a_files_content_reparses_only_that_file(
        self, imports_mod, write_source, monkeypatch
    ):
        monkeypatch.delenv("FESTINA_NO_PARSE_CACHE", raising=False)
        root = write_source({
            "main.f": "import util.f\nlog('main')\n",
            "util.f": "log('v1')\n",
        })
        imports_mod.build_program(str(root / "main.f"))  # warms both entries
        (root / "util.f").write_text("log('v2')\n", encoding="utf-8")
        calls = self._clear_calls(monkeypatch, imports_mod)
        imports_mod.build_program(str(root / "main.f"))
        # Only util.f's new content is a genuine cache miss -- main.f's
        # own content never changed, so exactly one real parse happens,
        # not two.
        assert len(calls) == 1

    def test_a_dependencys_behavior_change_is_reflected_end_to_end(
        self, compile_and_run, tmp_path
    ):
        (tmp_path / "util.f").write_text(
            "int func compute() { return 1 }\n", encoding="utf-8")
        source = "import util.f\nlog(compute())\n"
        result = compile_and_run(source)
        assert result.stdout.strip() == "1"

        # Same entry file, unchanged -- but the IMPORTED file's content
        # changed. The cache must not serve util.f's stale, cached AST.
        (tmp_path / "util.f").write_text(
            "int func compute() { return 2 }\n", encoding="utf-8")
        result = compile_and_run(source)
        assert result.stdout.strip() == "2"

    def test_a_corrupted_cache_file_degrades_to_a_fresh_parse(
        self, imports_mod, write_source, monkeypatch
    ):
        monkeypatch.delenv("FESTINA_NO_PARSE_CACHE", raising=False)
        root = write_source({"main.f": "log('hi')\n"})
        source = (root / "main.f").read_text(encoding="utf-8")
        cache_path = imports_mod._parse_cache_path(source, imports_mod._grammar_epoch_hash())
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "wb") as f:
            f.write(b"not a pickle at all, deliberately corrupt")
        # No exception, and the program still compiles/behaves correctly
        # -- a corrupt cache entry is exactly a cache miss, never a
        # compile failure.
        program = imports_mod.build_program(str(root / "main.f"))
        assert len(program.body) == 1

    def test_a_different_grammar_epoch_is_never_served_from_the_old_one(
        self, imports_mod, write_source, monkeypatch
    ):
        monkeypatch.delenv("FESTINA_NO_PARSE_CACHE", raising=False)
        root = write_source({"main.f": "log('hi')\n"})
        imports_mod.build_program(str(root / "main.f"))  # warms under today's epoch
        monkeypatch.setattr(imports_mod, "_grammar_epoch_hash", lambda: "a-different-epoch")
        calls = self._clear_calls(monkeypatch, imports_mod)
        imports_mod.build_program(str(root / "main.f"))
        # A different epoch is a different cache key entirely -- this
        # must be a real parse, not a hit against the old epoch's entry.
        assert len(calls) == 1

    def test_the_escape_hatch_disables_both_read_and_write(
        self, imports_mod, write_source, monkeypatch
    ):
        monkeypatch.setenv("FESTINA_NO_PARSE_CACHE", "1")
        root = write_source({"main.f": "log('hi')\n"})
        calls = self._clear_calls(monkeypatch, imports_mod)
        imports_mod.build_program(str(root / "main.f"))
        imports_mod.build_program(str(root / "main.f"))
        # Every build is a real parse under the escape hatch -- the
        # second call gets no benefit from the first.
        assert len(calls) == 2
