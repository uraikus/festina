"""The canonical dump of what semantic analysis produces.

The Festina port of `festina/semantic.py` will be checked the same way
the lexer and parser ports are: both implementations emit this dump and
`semdiff.py` diffs them over the corpus. This module is the Python side.

## What semantic analysis actually produces

Unlike the lexer (a token stream) and the parser (a tree), `analyze()`
returns no annotated program. It is a *checker*: it either raises a
`CompileError` or returns an `AnalyzedProgram` holding the global symbol
table, the structs, tables, enums and threads, and main's message and
reply types. Nothing is written back onto the AST -- codegen re-derives
every expression type itself.

That makes the obvious oracle -- "diff the analyzed program" -- far
weaker than it looks: it says nothing whatsoever about the inside of a
function body. Two analyzers could agree on every global and disagree
about the type of every local, and this dump would not notice.

## What makes it strong instead: every binding, in every scope

`Scope.define(name, symbol, err_node, filename)` is the single
chokepoint through which every binding in the program passes -- globals,
constants, functions, parameters, loop variables, catch variables and
locals nested arbitrarily deep inside function and handler bodies.
Wrapping it records the resolved type of all of them, with the position
they were declared at.

So the dump below is not the analyzed program; it is **the resolved type
of every name the program binds, anywhere**, plus the declared types.
That is what a type checker is *for*, and it is what two independent
implementations must agree on.

No change to `festina/semantic.py` is needed for this -- the wrapper
lives here, in the harness, so the compiler carries no test-only hook.

## The form

One record per line, each field `|`-separated, the whole dump sorted so
that neither side's internal ordering (dict iteration, pass order) can
manufacture a difference:

    DECL|line:col|name|kind|type
    STRUCT|name|field:type|...
    TABLE|name|column:type|...
    ENUM|name|member:type|...
    THREAD|name|in=type|reply=type
    MAIN|msg=type|reply=type

A rejected program produces exactly one line, and the message text is
deliberately not compared -- only that both implementations reject the
same program in the same place:

    SEMERR|line|col

Run directly for a report:

    python bootstrap/semdump.py examples/hello.f
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from festina import imports as py_imports      # noqa: E402
from festina import semantic as py_semantic    # noqa: E402
from festina import types as types_mod         # noqa: E402
from festina.errors import CompileError        # noqa: E402


def _type(t):
    """A type's canonical spelling.

    `types_mod.type_name` is the compiler's own renderer -- the same one
    codegen keys its generated release-function caches on, so it already
    has to distinguish every type the language can tell apart, including
    the trailing `?` of a manually-managed one. Reusing it means the
    oracle cannot disagree with the compiler about what two types being
    'the same' means.
    """
    if t is None:
        return "-"
    try:
        return types_mod.type_name(t)
    except Exception:
        return str(t)


def _fields(pairs):
    return "|".join(f"{n}:{_type(t)}" for n, t in pairs)


class _Recorder:
    """Wraps Scope.define for the duration of one analyze() call.

    Restores the original in a finally block: a leaked patch would make
    every later dump in the same process accumulate the previous file's
    bindings, which is exactly the kind of harness bug that produces
    confident, wrong agreement.
    """

    def __init__(self):
        self.rows = []

    def __enter__(self):
        self._original = py_semantic.Scope.define
        rows = self.rows

        def define(scope, name, symbol, err_node, filename):
            result = self._original(scope, name, symbol, err_node, filename)
            # Position comes from the declaring node. Builtins
            # (argv, environment, Math's constants) are defined with
            # err_node None and land at 0:0, which is correct and
            # stable: both implementations must register the same set.
            line = getattr(err_node, "line", 0) or 0
            column = getattr(err_node, "column", 0) or 0
            kind = getattr(symbol, "kind", "-")
            type_ = getattr(symbol, "type", None)
            rows.append(f"DECL|{line}:{column}|{name}|{kind}|{_type(type_)}")
            return result

        py_semantic.Scope.define = define
        return self

    def __exit__(self, *exc):
        py_semantic.Scope.define = self._original
        return False


def dump_file(path):
    """The canonical dump for one entry file.

    Goes through `imports.build_program`, the same front end
    `festina/cli.py` runs, rather than parsing the file alone. That is
    not a detail: an `import` merges every file's statements into ONE
    program before `analyze()` ever sees them (§6.2), and `DatabaseURL`
    is resolved there too. Parsing a single file in isolation instead
    makes five corpus files fail on names their imports define and two
    more on `DatabaseURL` -- seven rejections that are facts about the
    harness, not about the language, and every one of them would have
    become a spurious requirement on the port.
    """
    try:
        program = py_imports.build_program(path)
    except CompileError as err:
        # A source the front end rejects before analysis still has to be
        # rejected by both implementations, in the same place. Which
        # stage said so is not part of the claim.
        return [f"SEMERR|{err.line}|{err.column}"]

    filename = os.path.basename(path)
    recorder = _Recorder()
    try:
        with recorder:
            analyzed = py_semantic.analyze(program, filename=filename)
    except CompileError as err:
        return [f"SEMERR|{err.line}|{err.column}"]

    out = list(recorder.rows)

    for name, fields in analyzed.structs.items():
        pairs = [(f, _resolve_raw(t, analyzed)) for f, t in fields.items()]
        out.append(f"STRUCT|{name}|{_fields(pairs)}")

    for name, columns in analyzed.tables.items():
        pairs = [(c, _resolve_raw(t, analyzed)) for c, t in columns.items()]
        out.append(f"TABLE|{name}|{_fields(pairs)}")

    for name, info in analyzed.enums.items():
        # Enum members are an ordered sequence, not a mapping -- an
        # enum's tags are declared in an order the language cares
        # about, where a struct's fields are looked up by name.
        pairs = [(getattr(m, "name", str(m)), getattr(m, "type", None))
                 for m in (getattr(info, "members", ()) or ())]
        out.append(f"ENUM|{name}|{_fields(pairs)}")

    for name, info in (analyzed.threads or {}).items():
        inbound = _type(getattr(info, "inbound_type", None))
        reply = _type(getattr(info, "reply_type", None))
        out.append(f"THREAD|{name}|in={inbound}|reply={reply}")

    out.append(f"MAIN|msg={_type(analyzed.main_message_type)}"
               f"|reply={_type(analyzed.main_reply_type)}")

    # Sorted, not emitted in walk order. Dict iteration order and the
    # order of analysis passes are implementation choices, not language
    # facts, and a port is not obliged to reproduce them.
    return sorted(out)


def _resolve_raw(raw, analyzed):
    """A struct field / table column type as analysis left it.

    These are stored as the raw type expressions the parser produced,
    so they need resolving against the program's own struct, table and
    enum names to render as the type they denote.

    There is no common base class to test against -- every type in
    `festina/types.py` is its own frozen dataclass -- so membership is
    decided by the defining module, which is what "is this already a
    resolved type" actually means.
    """
    if type(raw).__module__ == types_mod.__name__:
        return raw
    try:
        return py_semantic.resolve_type_name(
            raw, analyzed.structs, analyzed.tables, analyzed.enums)
    except Exception:
        return raw


def main(argv):
    if len(argv) < 2:
        print(__doc__.strip().splitlines()[0])
        print("usage: python bootstrap/semdump.py FILE.f [FILE.f ...]")
        return 2
    for path in argv[1:]:
        for line in dump_file(path):
            print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
