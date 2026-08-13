# Test contract for the Festina language spec (`claude.md`)

## Status

The `festina/` package at the repository root now implements the front
end of the spec in `claude.md`: lexing, parsing, type resolution, semantic
analysis, and SQLite schema-sync planning. All 133 tests in this
directory pass against it.

Still unimplemented, and out of scope for `festina/`: LLVM IR generation,
native linking, and producing an actual `festina` executable (claude.md
#47), and the runtime pieces (graphics/Cairo, audio, actually opening
`festina.sqlite` and executing queries). Those need the front end this
package provides, but aren't covered by this test suite.

Separately, `compiler/` (`lexer.py`, `parser.py`, `codegen.py`, `jsc.py`)
remains a different, older prototype that compiles a small JS subset --
unrelated to `festina/` and not exercised by these tests.

## Why the tests are structured this way

Every test module gets its `festina.*` submodule through a conftest.py
fixture (`import_spec_module`) rather than a plain `import festina.x`. If
a module is later removed or renamed, tests against it skip with a clear
reason instead of erroring the whole session -- but a real bug *inside*
an existing module still fails loudly (verified directly: a deliberately
broken stub module raises, it doesn't skip). Each test's docstring/comment
cites the `claude.md` section it encodes, so a failure points straight at
the rule in question.

## Public API implemented

```
festina/
    errors.py
        class CompileError(Exception):
            file, line, column, category, message
            # str(err) == "{file}:{line}:{column}: error: {message}"
        class CircularImportError(CompileError): ...

    lexer.py
        KEYWORDS: frozenset[str]           # claude.md #51 (+ a few
                                            # internal-only control words:
                                            # return/var/let/throw)
        SOURCE_EXTENSION = ".f"            # claude.md #4
        class Token: type, value, line, column
        def tokenize(source, filename="<string>") -> list[Token]
        # backtick templates with ${...} splice the interpolated
        # expression's own tokens into the stream (TSTRING_START/MID/END
        # bracket them); `import <path>` reads the rest of the line as a
        # single PATH token rather than tokenizing it as an expression.

    ast.py
        Program, ImportDecl, VarDecl, Param, FieldDecl, FuncDecl,
        StructDecl, TableDecl, EventHandler, Block, IfStmt, Return,
        ExprStmt, Identifier, NumberLit, StringLit, BoolLit, NullLit,
        TemplateLit, ArrayLit, Assign, Ternary, LogicalOp, BinOp,
        UnaryOp, Member, Call, ArrayTypeExpr

    types.py
        PrimitiveType(name) / StructType(name) / TableType(name) /
        ArrayType(element) / ImageType() / AudioType()   -- frozen
        dataclasses, so equality/hashing work out of the box.
        type_name(t) -> str   # for error messages, e.g. "arr[int]"

    parser.py
        def parse(source, filename="<string>") -> ast.Program
        # raises festina.errors.CompileError for invalid syntax,
        # var/let/throw, ===/ !==, missing return types, untyped
        # params/fields, malformed imports, etc.

    imports.py
        def resolve_imports(entry_path: str) -> list[str]
        # canonical (os.path.realpath), deduplicated, dependency-first
        # order; raises CircularImportError on cycles (including
        # self-imports) without recursing infinitely.

    semantic.py
        def analyze(program, filename="<string>") -> AnalyzedProgram
        # AnalyzedProgram: .symbols (name -> Symbol, global scope),
        # .structs (name -> {field: Type}), .tables (name -> {field:
        # festina-type-name-str}), .imports (list of raw import paths).
        # Single left-to-right pass; every fixture in this repo declares
        # structs/tables/functions before use, so no forward-reference
        # resolution was needed.

    sqlite_schema.py
        TYPE_MAP: dict[str, str]                        # claude.md #30
        def plan_sync(declared, existing) -> SchemaSyncPlan
        class SchemaSyncPlan: create, add_columns, drop_columns,
                               alter_columns
        create_table_ddl(...) / sync_ddl(...)  # best-effort SQL, not
        exercised by the tests

    compiler.py
        def compile_source(source, filename="main.f") -> CompileResult
        class CompileResult: ast, symbols, tables, structs, imports,
                              entry_function_name
```

## Running

```
pip install pytest
pytest tests/
```
