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
    statement rather than once for the whole compile)."""
    body = []
    for path in resolve_imports(entry_path):
        with open(path, encoding="utf-8") as f:
            source = f.read()
        program = parser_mod.parse(source, filename=path)
        for stmt in program.body:
            stmt.file = path
        body.extend(program.body)
    return ast_mod.Program(body)
