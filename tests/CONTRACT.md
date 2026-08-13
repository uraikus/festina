# Test contract for the Festina language spec (`claude.md`)

## Status

The `festina/` package at the repository root implements the front end
of the spec in `claude.md` (lexing, parsing, type resolution, semantic
analysis) **and** now a real LLVM codegen backend + native C runtime:
`bin/festina program.f -o program` produces a standalone executable that
needs neither Python nor `festina/` to run. Automatic SQLite table
creation and schema synchronization (claude.md #28-31) is implemented
for real against `festina.sqlite`, including the temp-table rebuild path
for dropped/retyped columns, with data preservation verified by the
`claude.md #31` worked examples as tests. `sqlite()` queries (claude.md
#32-34) are implemented too -- `SELECT` into a declared `arr[Table]`
with field access on the resulting rows, and parameterized
`INSERT`/`UPDATE`/`DELETE`/`SELECT` via a literal params array (see
festina/codegen.py's module docstring's "Query rows" note for the row
representation and the params-must-be-a-literal-array restriction).
Arrays (claude.md #26) are
implemented too -- literals, indexed get/set, nesting, function
params/return values, and (claude.md #63) `.length` -- though claude.md
still doesn't specify bounds checking or array growth, so neither of
those exist (see festina/codegen.py's module docstring). claude.md
#55-58 (added after a design review of the first codegen pass turned up
real bugs -- see below) are implemented too: int/float never convert
implicitly in any operator, `int.toFloat()`/`Math.floor/ceil/round/trunc`
are the only conversions, division/modulo by zero returns `null` instead
of crashing, and struct/table names live in their own namespace. So are
claude.md #60/#61 (`for`/`while` loops, including the loop-variable
scoping rule and `while true`) and #66 (postfix `++`/`--` on mutable
`int` variables) -- there's still no `break`/`continue` (claude.md
doesn't define either).

"Real compilation, minimal setup" stages 1 and 3 -- claude.md #59, added
alongside these two stages to make the requirement explicit rather than
implicit in the implementation -- are also done (see README.md's
"Deployment"/"Setup" sections for the full staged plan and the current
dependency list): sqlite3 is statically linked into compiled programs
(no libsqlite3.so needed to *run* one), and `festina/llvm_backend.py`
compiles the generated LLVM IR to an object file itself via libLLVM's C
API rather than handing a .ll file to clang -- clang is no longer
specifically required to *use* Festina, just some working C compiler
(gcc verified working end to end). Per #59's fourth point,
`festina/cli.py`'s `_run_tool` also turns a genuinely missing dependency
(pkg-config, or any C compiler) into a specific, actionable error naming
it and how to install it, rather than a raw exception -- verified
directly by hiding each tool from PATH in turn. All 261 tests in this
directory pass against it (0 skipped, given a working C compiler -- see
below).

claude.md #55-58 exist because of bugs a design review found by actually
running compiled programs, not just reading the code: returning a struct
by value handed the caller a pointer into an already-popped stack frame
(silently printed garbage); `int x = null` / `float x = null` failed to
link (`null` is only valid IR for a pointer type, and i64/double have no
spare bit pattern for it); and a float literal small/large enough that
Python's `repr()` used scientific notation (e.g. `0.0000001`) also failed
to link, for the same "not valid float-literal syntax" reason. Fixing
the null representation properly created an opening to also resolve a
pre-existing inconsistency (assignment strictly rejected mixed int/float,
but arithmetic silently allowed it) -- claude.md #55-56 close that gap by
making the stricter behavior the rule everywhere, with `Math`/`.toFloat()`
as the escape hatch, rather than picking a side ad hoc in code with no
spec backing either way.

See README.md's "Implementation Status" section for the current
implemented-vs-not matrix; the short version: graphics, audio, and event
handlers all parse and type-check but raise a clear `CodegenError` ("not
implemented yet") rather than generating IR. `sqlite()` queries no
longer belong on that list -- see above.

(An earlier, unrelated JS-subset prototype -- `compiler/`, plus
`build.sh`/`jit_run.py`/`run_jit.sh` and `runtime/runtime.c` for
building/running it -- used to live alongside `festina/` in this repo.
It predated `festina/` and was never exercised by these tests; removed
as unrelated clutter once `festina/llvm_backend.py` made its
JIT-without-clang trick (`jit_run.py`'s whole reason for existing)
redundant for the real language too.)

## Why the tests are structured this way

Every test module gets its `festina.*` submodule through a conftest.py
fixture (`import_spec_module`) rather than a plain `import festina.x`. If
a module is later removed or renamed, tests against it skip with a clear
reason instead of erroring the whole session -- but a real bug *inside*
an existing module still fails loudly (verified directly: a deliberately
broken stub module raises, it doesn't skip). Each test's docstring/comment
cites the `claude.md` section it encodes, so a failure points straight at
the rule in question.

`tests/test_codegen.py` additionally uses a `compile_and_run` fixture
that actually compiles+links generated IR against the Festina runtime
and runs the resulting binary. It prefers `clang` but accepts `gcc`/`cc`
too (stage 3 means the C compiler no longer needs an LLVM-IR-text
frontend, just the ability to compile festina_runtime.c and link object
files -- see festina/cli.py and festina/llvm_backend.py's docstrings).
It skips (with a distinct, toolchain-specific reason) if no C compiler
is on `PATH` at all -- unlike the `SPEC_UNIMPLEMENTED_REASON` skips
above, this isn't "the feature doesn't exist," it's "this environment
can't link native code." Tests for constructs codegen genuinely doesn't
support yet (graphics, audio, events) don't need a C compiler at all --
they only call `festina.codegen.generate_ir()` and assert it raises.
The one exception on the sqlite() side is
`test_non_literal_params_argument_is_a_clear_error`, which checks a
compile-time restriction (params must be a literal array) the same
no-C-compiler way, even though `sqlite()` itself is otherwise fully
implemented.

`tests/test_llvm_backend.py` tests `festina.llvm_backend` directly and
only needs libLLVM itself (via its own `llvm_backend` fixture's
`available()` check) -- a narrower requirement than `compile_and_run`'s
full C-compiler skip, since this module doesn't touch a C compiler.
`test_codegen.py`'s `TestMinimalBuildDependencies` covers the two ends
of stage 3 concretely: gcc actually producing a working binary when
libLLVM is available, and the original clang-only pipeline still
working (via `monkeypatch`) when it isn't. `TestMissingDependencyErrors`
covers claude.md #59's fourth point (a missing dependency must fail
clearly) by actually hiding pkg-config/cc from a synthetic PATH (a
`path_without` fixture, via `monkeypatch.setenv`) and asserting on the
resulting error message, rather than just testing `_run_tool` in
isolation.

`tests/test_numeric_conversion.py` covers claude.md #55-58 at the
parser/semantic level only (same `parser`/`semantic`/`errors` fixtures as
the rest of the front-end suite, no `clang` needed); the matching runtime
behavior (Math/`.toFloat()`'s actual output, division-by-zero surviving
and producing *something* rather than crashing) is tested end-to-end in
`test_codegen.py`'s `TestNumericConversion`, plus regression coverage
there for the three bugs #55-58 were written in response to (see
"Status" above): a struct returned by value, and `null`/scientific-notation
float literals compiling and linking successfully.

`tests/test_loops.py` covers claude.md #60/#61/#63/#66 (for/while
loops, `.length`, postfix `++`/--) at the parser/semantic level, same
split as `test_numeric_conversion.py`; the matching end-to-end runtime
behavior (a compiled loop actually iterating the right number of times,
loop-variable scoping surviving a real function frame, an iterative
Fibonacci) lives in `test_codegen.py`'s `TestLoops` and
`TestArrayLength`.

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
        StructDecl, TableDecl, EventHandler, Block, IfStmt, WhileStmt,
        ForStmt, Return, ExprStmt, Identifier, NumberLit, StringLit,
        BoolLit, NullLit, TemplateLit, ArrayLit, Assign, Ternary,
        LogicalOp, BinOp, UnaryOp, PostfixOp, Member, Call, ArrayTypeExpr

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
        # resolution was needed. structs/tables are never cross-checked
        # against Scope (claude.md #58: separate namespace by design, not
        # an accidental gap). claude.md #55/#56: BinOp rejects int/float
        # operands that differ (any operator, not just arithmetic);
        # Math.floor/ceil/round/trunc(x:float) -> int and
        # int_value.toFloat() -> float are recognized as Call-on-Member
        # patterns, not real declarations (no "Math" symbol exists).
        # claude.md #60/#61: WhileStmt/ForStmt conditions must be bool
        # (check_condition_bool, same helper if/ternary use); a ForStmt's
        # init variable gets its own child Scope so it's visible in the
        # condition/update/body but nothing analyzed after the loop can
        # see it. claude.md #63: `.length` on an ArrayType resolves to
        # int and is the *only* valid non-computed field an array has;
        # assigning to it is rejected before the generic Assign
        # type-check runs (that check alone can't tell a read from a
        # write target). claude.md #66: PostfixOp requires its operand be
        # an Identifier resolving to a non-constant int.

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
        # front-end only (parse + analyze); does not invoke codegen.

    codegen.py
        def generate_ir(program, analyzed, filename="main.f") -> str
        # emits opaque-pointer LLVM IR text. Supports: primitives,
        # global/local vars & consts, functions, if/else, for/while loops
        # (_emit_for/_emit_while -- ordinary structured control flow, the
        # same _start_block/label pattern _emit_if uses; a for-loop's
        # init variable lives in a child Env scoped to just that
        # statement), the full expression grammar including postfix
        # ++/-- (_emit_postfix -- load/add-or-sub-1/store on the
        # operand's slot, returns the pre-increment value), structs
        # (heap-allocated via calloc, GEP field access -- see the module
        # docstring's "Struct storage" note for why not a stack alloca),
        # arrays (arr[T] literals + indexed get/set + nesting + .length,
        # all arr[T] lowered to one fixed
        # `%struct._FestinaArray = type { i64, ptr }` -- see the module
        # docstring; .length is `extractvalue` on the array's own value,
        # bypassing the pointer-based field-access path entirely since
        # not every array-typed expression is addressable), automatic
        # table schema sync via the festina_runtime
        # C helpers, sqlite() queries (SELECT into a declared arr[Table],
        # parameterized INSERT/UPDATE/DELETE/SELECT via a literal params
        # array -- table-typed values are `ptr`-to-row like structs, field
        # access is a flat `field_index * 8` byte GEP rather than a named
        # struct type; see the module docstring's "Query rows" note and
        # CodeGen.table_fields/table_field_index/_emit_sqlite_call/
        # _emit_sqlite_bind_params/_emit_sqlite_collect), Math.floor/ceil/
        # round/trunc (LLVM intrinsics) and int.toFloat() (sitofp),
        # division/modulo by zero returning a reserved null sentinel
        # (INT_NULL_CONST / FLOAT_NULL_CONST) via real control flow rather
        # than a trapping instruction. Raises CodegenError (a CompileError
        # subclass, category="not implemented") for graphics, audio, and
        # event handlers -- and also (category="not implemented" but a
        # genuine compile-time restriction, not a missing feature) when
        # sqlite()'s second argument isn't a literal array expression.

    llvm_backend.py
        def available() -> bool           # libLLVM found+loaded in this process?
        def emit_object_file(ir_text, out_path, filename="<ir>") -> None
        class LLVMBackendError(Exception): ...
        # ctypes bindings against libLLVM's C API for ahead-of-time
        # object emission (LLVMTargetMachineEmitToFile), not JIT
        # execution (MCJIT).
        # RelocMode is pinned to PIC to match this system's PIE-by-default
        # linking (verified: LLVMRelocDefault produces relocations `ld`
        # rejects for a PIE). available() is False (never raises) if
        # libLLVM can't be found/loaded or the process architecture isn't
        # one of the target-init symbol names this module knows.

    cli.py
        def compile_file(entry_path, output_path=None, emit_llvm=False,
                          cc="clang") -> str
        # drives parse -> analyze -> generate_ir, then:
        #   llvm_backend.available() -> compile IR to an object file via
        #     llvm_backend directly (stage 3), cc only compiles the
        #     (cached) runtime and links plain object files -- gcc works.
        #   otherwise -> original fallback: hand the .ll file straight to
        #     cc, which must then actually be clang.
        # Single-file only for now (doesn't call festina.imports yet).
        # def main(argv) -> int is the `bin/festina` entry point.
        # _run_tool(cmd) -> subprocess.CompletedProcess: claude.md #59 --
        #   wraps every pkg-config/cc invocation so a genuinely missing
        #   tool raises CompileError("'<tool>' is not installed or not
        #   on PATH -- <install hint>", category="missing dependency")
        #   instead of a raw FileNotFoundError (check=False alone does
        #   NOT catch this case -- it only suppresses a nonzero exit
        #   code, not a failure to launch the binary at all).
```

Runtime ABI: `runtime/festina_runtime.h`/`.c` implement the C side
codegen's `declare`s call into -- `festina_log_*`/`festina_fail` (#41,
#42), `festina_str_*` (string interpolation, #9/#45),
`festina_db_open`/`festina_sync_table` (#8, #28-31: schema
create/add-column/rebuild-with-CAST, using the same declared-vs-existing
diff `sqlite_schema.py` computes, reimplemented in C since the compiled
executable can't depend on Python at runtime), and
`festina_sqlite_prepare`/`_bind_int`/`_bind_float`/`_bind_text`/
`_bind_null`/`_exec`/`_collect_rows` (#32-34: sqlite() queries --
`_collect_rows` packs each result row as `col_count` consecutive 8-byte
slots, exactly the layout codegen's flat `field_index * 8` byte GEP
reads back, so no struct-alignment rule needs to be kept in sync between
the two languages; see festina_runtime.h's doc comment on
`festina_sqlite_collect_rows` and codegen.py's module docstring's "Query
rows" note, which describe the same design from each side).

## Running

```
pip install -r requirements-dev.txt   # pytest
pytest tests/                          # 261 passed, 0 skipped (needs a C compiler)
```
