# Test contract for the Festina language spec (`claude.md`)

## Why this exists

`claude.md` at the repository root is a specification for the **Festina**
language and compiler that an AI agent is meant to implement. As of this
commit, the code under `compiler/` (`lexer.py`, `parser.py`, `codegen.py`,
`jsc.py`) does **not** implement that spec — it's a separate, older
prototype that compiles a small subset of JavaScript itself (`var`/`let`,
`function`, no static types, no `table`/`struct`/`arr[T]`, no `sqlite()`
builtin, no reserved words from `claude.md` section 51 at all).

This directory contains a **spec-driven unit test suite** for `claude.md`,
written against a `festina` Python package that does not exist yet. Every
test module calls `pytest.importorskip(...)` on the specific `festina.*`
submodule it needs, so:

- Today, running `pytest tests/` collects everything and **skips** it,
  with a clear reason pointing back at this file. Nothing fails, nothing
  is silently ignored.
- As soon as someone adds `festina/lexer.py`, the lexer tests in
  `test_lexer.py` start actually running (and, once the implementation
  matches the spec, passing) — without touching the test files.
- Each test docstring/comment cites the `claude.md` section number it
  encodes, so a failing test points straight at the rule it's checking.

This is intentionally a spec-first suite (TDD scaffold) for the Festina
language described in `claude.md`, not a test of the existing `compiler/`
prototype (which implements a different, unrelated JS subset and has no
`table`/`struct`/`arr[T]`/`sqlite()`/etc. support at all).

## Assumed public API

Nothing here is set in stone — it's a reasonable, minimal surface inferred
from `claude.md`, meant to give an implementer a concrete target. Adjust
the tests alongside the real API as it's built, keeping each test's cited
spec section as the source of truth.

```
festina/
    errors.py
        class CompileError(Exception):
            file: str
            line: int
            column: int
            category: str      # e.g. "unknown type", "unsupported operator"
            message: str
            # str(err) == "{file}:{line}:{column}: error: {message}"
            # (see claude.md #48, example in #48: main.f:12:5: error: ...)

        class CircularImportError(CompileError): ...

    lexer.py
        KEYWORDS: frozenset[str]      # claude.md #51
        class Token: type, value, line, column
        def tokenize(source: str, filename: str = "<string>") -> list[Token]

    parser.py
        def parse(source: str, filename: str = "<string>") -> ast.Program
        # raises festina.errors.CompileError on invalid syntax

    ast.py
        Program, ImportDecl, VarDecl, ConstDecl, FuncDecl, Param,
        StructDecl, TableDecl, FieldDecl, IfStmt, Ternary, BinOp,
        EqualityOp, Identifier, Literal, ArrayTypeExpr, EventHandler, ...

    types.py
        class PrimitiveType(name): ...   # INT/FLOAT/BOOL/TEXT/BLOB
        class StructType(name): ...
        class TableType(name): ...
        class ArrayType(element): ...
        class ImageType(): ...
        class AudioType(): ...
        def resolve_type(type_expr, symbol_table) -> Type
        # raises CompileError(category="unknown type") when undeclared (#13)

    imports.py
        def resolve_imports(entry_path: str) -> list[str]
        # returns canonical, deduplicated file paths, dependencies before
        # the entry file (#6, #7, #8); raises CircularImportError on
        # a -> b -> a style cycles without infinite recursion (#6)

    semantic.py
        def analyze(program: ast.Program) -> AnalyzedProgram
        # raises CompileError for the categories listed in claude.md #48

    sqlite_schema.py
        TYPE_MAP: dict[str, str]        # claude.md #30
        def plan_sync(declared: dict[str, str],
                       existing: dict[str, str] | None) -> SchemaSyncPlan
        class SchemaSyncPlan:
            create: bool
            add_columns: dict[str, str]
            drop_columns: list[str]
            alter_columns: dict[str, str]

    compiler.py
        def compile_source(source: str, filename: str = "main.f") -> CompileResult
        class CompileResult:
            ast, symbols, tables: dict[str, dict[str, str]]
            entry_function_name: str
```

## Running

```
pip install pytest
pytest tests/
```
