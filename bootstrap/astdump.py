"""Canonical AST dump -- the form bootstrap/parser.f is compared against.

claude.md #273. The same idea as the lexer's token dump: pick one
textual form, emit it from both the Python parser and the Festina one,
and diff. A parser is a much bigger surface than a lexer, so the form
has to be generic rather than per-node -- otherwise the dumper itself
becomes a second parser to keep in sync.

    (Kind :field=<value> :field=<value> ...)

Fields are sorted by name, so neither side depends on the other's
declaration order. Values are:

    null            None
    true / false    bool
    123             int
    1.5             float
    "text"          str, with \\\\ \\n \\t \\r escaped
    (Kind ...)      a nested node
    [a b c]         a list of any of the above

Every field an AST node carries is dumped, `line`/`column` included: the
parser's whole job downstream is producing good error locations, and a
port that quietly loses them would still pass a structural-only
comparison.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from festina import ast as ast_mod  # noqa: E402


def _esc(s):
    out = []
    for ch in s:
        if ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        elif ch == '"':
            out.append('\\q')
        else:
            out.append(ch)
    return "".join(out)


def dump_value(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        return '"' + _esc(value) + '"'
    if isinstance(value, ast_mod.Node):
        return dump_node(value)
    if isinstance(value, (list, tuple)):
        return "[" + " ".join(dump_value(v) for v in value) + "]"
    raise TypeError(f"cannot dump {type(value).__name__} in the AST: {value!r}")


def dump_node(node):
    kind = type(node).__name__
    fields = sorted(vars(node).items())
    parts = [f":{name}={dump_value(value)}" for name, value in fields]
    if not parts:
        return f"({kind})"
    return f"({kind} " + " ".join(parts) + ")"


def dump_program(program):
    """One field per line, so a diff points at a statement rather than at
    a single enormous line."""
    return [dump_node(stmt) for stmt in program.body]
