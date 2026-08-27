"""claude.md #7 (entry file), #8 (program startup), #52 (worked example)."""
import pytest


EXAMPLE_PROGRAM = """
import database.f
import ui.f

table People {
    id:int
    name:text
}

struct User {
    id:int
    name:text
}

const text appName = 'Festina'

text func greet(user:User) {
    text message = `Hello ${user.name}`
    log(message)
    return message
}

on mouseDown(x:int, y:int, button:int) {
    log(`Pressed at ${x}, ${y}`)
}

drawRect(0, 0, 100, 100)

User user

user.id = 1
user.name = 'Patrick'

greet(user)

arr[People] people = sqlite('SELECT * FROM People')

log(appName)
"""


class TestEntryFunctionGeneration:
    """claude.md #7: executable statements in the entry file are wrapped
    into an automatically generated entry function; no programmer-defined
    main()."""

    def test_top_level_statements_parse_without_a_main_function(self, parser):
        parser.parse("log('Hello')")

    def test_compiled_program_exposes_a_generated_entry_function(self, compiler_mod, write_source):
        root = write_source({"main.f": "log('Hello')"})
        result = compiler_mod.compile_source(open(root / "main.f").read(), filename="main.f")
        assert result.entry_function_name
        # The name is implementation-defined, but the programmer never
        # declared a `main` function in the source above -- the compiler
        # must have synthesized the entry point itself.
        assert "main" not in getattr(result, "symbols", {})


class TestFullExampleProgram:
    """claude.md #52: the full worked example must be valid Festina --
    it exercises tables, structs, consts, functions, events, graphics
    calls, struct field assignment, and a sqlite() query together.

    This parses/analyzes the file in isolation, without resolving
    `database.f`/`ui.f` against the filesystem -- multi-file import
    resolution is covered separately in test_imports.py. A single-file
    `parser.parse` is expected to build `ImportDecl` AST nodes without
    touching disk; only `festina.imports.resolve_imports` (driven by the
    high-level compiler) needs the imported files to actually exist.
    """

    def test_example_program_parses(self, parser):
        parser.parse(EXAMPLE_PROGRAM)

    def test_example_program_passes_semantic_analysis(self, parser, semantic):
        program = parser.parse(EXAMPLE_PROGRAM)
        semantic.analyze(program)

    def test_example_program_declares_expected_symbols(self, parser, semantic):
        program = parser.parse(EXAMPLE_PROGRAM)
        analyzed = semantic.analyze(program)
        assert "People" in analyzed.tables
        assert "greet" in analyzed.symbols
        assert "appName" in analyzed.symbols
