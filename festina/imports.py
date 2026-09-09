"""Import resolution -- claude.md #5, #6.

Recursive resolution, canonical-path deduplication (each file processed
once), and circular-import detection without infinite recursion.
"""
import hashlib
import os
import pickle
import tempfile

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


_GRAMMAR_EPOCH_FILES = ("lexer.py", "parser.py", "ast.py", "imports.py")
_grammar_epoch_cache = [None]  # memoized once per process; never changes mid-run


def _grammar_epoch_hash():
    """claude.md #253: a hash of the four source files that decide what
    a parsed AST for a given source string actually LOOKS like -- the
    lexer's own keyword/token set, the parser's grammar, every
    `ast.Node` shape, and this cache's own logic. Folded into every
    cache key (see _parse_cached below) so upgrading festina's own
    grammar automatically invalidates every existing cache entry, with
    no manual version bump ever needed -- the same problem
    `_ensure_runtime_object`'s own mtime-vs-`_RUNTIME_HEADERS` check
    solves for the C runtime cache, solved here for a Python-object
    cache instead (which can't use mtime -- see _parse_cached's own
    comment on why this cache is content-hash keyed, not mtime-keyed,
    unlike its C-runtime sibling).

    Located via THIS module's own `__file__` -- not `cli._data_root()`
    -- since `cli.py` already imports `imports.py`; importing back
    would be circular. Memoized: these four files cannot change
    mid-process, so hashing them once per compile (not once per
    imported file) is enough."""
    if _grammar_epoch_cache[0] is None:
        pkg_dir = os.path.dirname(os.path.abspath(__file__))
        h = hashlib.sha256()
        for name in _GRAMMAR_EPOCH_FILES:
            with open(os.path.join(pkg_dir, name), "rb") as f:
                h.update(f.read())
        _grammar_epoch_cache[0] = h.hexdigest()[:16]
    return _grammar_epoch_cache[0]


def _parse_cache_path(source, epoch):
    key = hashlib.sha256(epoch.encode() + b"\0" + source.encode("utf-8")).hexdigest()
    cache_dir = os.path.join(tempfile.gettempdir(), "festina-parse-cache")
    os.makedirs(cache_dir, exist_ok=True)
    return os.path.join(cache_dir, f"{key}.pkl")


def _parse_cached(source, path):
    """claude.md #253: `festina.parser.parse(source, filename=path)`,
    cached on disk across separate `festina compile` invocations, keyed
    by the exact source text (not mtime -- a cached, silently-reused
    PARSED AST is a correctness risk a wrong `.o` file mostly isn't:
    that fails to link, this could compile successfully into the wrong
    program, so this cache is deliberately harder to fool than
    _ensure_runtime_object's own mtime check) plus _grammar_epoch_hash
    (so a festina upgrade can never serve a stale-shaped AST). Every
    failure mode -- cache dir unwritable, disk full, a corrupt or
    cross-version-incompatible pickle -- degrades silently to "parse it
    fresh, and best-effort try to cache it for next time": correctness
    never depends on this working, matching claude.md #93's own "an
    unreadable path is not a failure" rule applied here to a cache
    instead of a blob. `FESTINA_NO_PARSE_CACHE=1` (mirroring
    `FESTINA_NO_DIRECT_FILL=1`'s own escape-hatch precedent) skips both
    the read and the write outright, for ruling the cache out while
    debugging something else entirely."""
    if os.environ.get("FESTINA_NO_PARSE_CACHE") == "1":
        return parser_mod.parse(source, filename=path)
    cache_path = _parse_cache_path(source, _grammar_epoch_hash())
    try:
        with open(cache_path, "rb") as f:
            return pickle.load(f)
    except Exception:
        pass  # missing, corrupt, or cross-version -- fall through and reparse
    program = parser_mod.parse(source, filename=path)
    try:
        fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(cache_path))
        try:
            with os.fdopen(fd, "wb") as f:
                pickle.dump(program, f)
            os.replace(tmp_path, cache_path)  # atomic -- no reader ever sees a torn file
        except Exception:
            os.unlink(tmp_path)
            raise
    except Exception:
        pass  # caching is a pure optimization; nothing here may fail the compile
    return program


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
        program = _parse_cached(source, path)
        stmts = program.body
        if path == entry_real:
            database_url, stmts = _extract_database_url(stmts, path)
        for stmt in stmts:
            stmt.file = path
        body.extend(stmts)
    merged = ast_mod.Program(body)
    merged.database_url = database_url
    return merged
