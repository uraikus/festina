"""Import resolution -- claude.md #5, #6.

Recursive resolution, canonical-path deduplication (each file processed
once), and circular-import detection without infinite recursion.
"""
import os

from . import ast as ast_mod
from . import lexer as lexer_mod
from . import parser as parser_mod
from .errors import CompileError, CircularImportError


def _scan_import_paths(source):
    """Return the raw import path strings a file's `import` statements
    reference, in source order."""
    tokens = lexer_mod.tokenize(source)
    paths = []
    i = 0
    while i < len(tokens):
        if tokens[i].type == "import" and i + 1 < len(tokens) and tokens[i + 1].type == "PATH":
            paths.append(tokens[i + 1].value)
            i += 2
            continue
        i += 1
    return paths


def resolve_imports(entry_path):
    """Return canonical, deduplicated file paths in dependency order --
    every file a dependency of comes before it, entry file last."""
    entry_path = os.path.realpath(entry_path)
    order = []
    visited = set()
    in_progress = []  # ordered stack, for a readable cycle message

    def visit(path):
        if path in visited:
            return
        if path in in_progress:
            cycle = " -> ".join(os.path.basename(p) for p in in_progress[in_progress.index(path):] + [path])
            raise CircularImportError(
                f"circular import detected: {cycle}",
                file=path, category="circular import",
            )
        if not os.path.isfile(path):
            raise CompileError(
                f"cannot find imported file '{path}'",
                file=path, category="invalid import",
            )
        in_progress.append(path)
        source = open(path, encoding="utf-8").read()
        from_dir = os.path.dirname(path)
        for raw in _scan_import_paths(source):
            dep = raw if os.path.isabs(raw) else os.path.join(from_dir, raw)
            dep = os.path.realpath(dep)
            visit(dep)
        in_progress.pop()
        visited.add(path)
        order.append(path)

    visit(entry_path)
    return order


def _is_database_url_assignment(stmt):
    """claude.md #70: `DatabaseURL = <expr>` -- syntactically nothing
    but an ordinary assignment-expression-statement (Festina has no
    dedicated grammar for this; DatabaseURL isn't a lexer keyword or a
    pre-declared variable), recognized here purely by matching the
    exact AST shape a parsed `Identifier("DatabaseURL") = expr`
    statement has."""
    return (isinstance(stmt, ast_mod.ExprStmt)
            and isinstance(stmt.expr, ast_mod.Assign)
            and isinstance(stmt.expr.target, ast_mod.Identifier)
            and stmt.expr.target.name == "DatabaseURL")


def _extract_database_url(body, path):
    """claude.md #70: pulls a `DatabaseURL = <expr>` directive out of
    the ENTRY file's own top-level statement list (called only for the
    entry file -- see build_program below; an imported file's own
    top-level statements never pass through here at all, so the same
    assignment written in one just flows through as ordinary code and
    fails semantic analysis with "unknown variable 'DatabaseURL'"
    instead of silently doing something).

    Position is enforced here, not semantic.py, since it's fundamentally
    about *this file's own statement order before multi-file merging* --
    by the time semantic.py sees the merged Program, the entry file's
    statements are no longer contiguous or first (resolve_imports puts
    dependencies before the entry file, so the entry file's own
    statements are actually LAST in the merged body).

    Returns (value_expr_or_None, remaining_body) -- `body` itself is
    left untouched; codegen.py reads the returned expression off
    ast.Program.database_url (see build_program) and evaluates it in
    main()'s own prologue, before festina_db_open() -- never as an
    ordinary top-level statement, which would run far too late (inside
    __festina_main(), after the database is already open)."""
    for i, stmt in enumerate(body):
        if _is_database_url_assignment(stmt):
            if i != 0:
                raise CompileError(
                    "DatabaseURL = ... must be the first statement in the entry "
                    "file, before any other code or import",
                    file=path, line=getattr(stmt.expr, "line", 0),
                    column=getattr(stmt.expr, "column", 0),
                    category="invalid syntax",
                )
            return stmt.expr.value, body[1:]
    return None, body


def build_program(entry_path):
    """Resolve entry_path's full import graph and parse every file into
    one merged ast.Program, in dependency order -- claude.md #5: "An
    import includes the specified file and all of its dependencies in
    the current compilation unit," i.e. a single translation unit (like
    C's #include), not per-file namespacing or runtime modules. A
    program with no imports at all is the same thing degenerately (just
    entry_path on its own), so this is also the normal single-file
    compile path now -- see festina/cli.py's compile_file.

    Each top-level statement is tagged with the file it actually came
    from (`.file`) so downstream errors (semantic analysis, codegen)
    still name the right file even though everything from here on is
    one ast.Program -- see semantic.analyze's and codegen.CodeGen's own
    notes on how that tag gets used (both re-read it once per top-level
    statement rather than once for the whole compile).

    The returned Program also carries a `database_url` attribute
    (claude.md #70) -- the entry file's own DatabaseURL directive's
    value expression, or None if it didn't have one."""
    entry_real = os.path.realpath(entry_path)
    body = []
    database_url = None
    for path in resolve_imports(entry_path):
        with open(path, encoding="utf-8") as f:
            source = f.read()
        program = parser_mod.parse(source, filename=path)
        stmts = program.body
        if path == entry_real:
            database_url, stmts = _extract_database_url(stmts, path)
        for stmt in stmts:
            stmt.file = path
        body.extend(stmts)
    merged = ast_mod.Program(body)
    merged.database_url = database_url
    return merged
