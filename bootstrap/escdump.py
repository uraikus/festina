"""The canonical escape-analysis dump `bootstrap/escape.f` is checked
against (decisions.md #299).

`festina/escape_analysis.py` answers one question per function body --
which names escape -- and codegen turns that answer into a
stack-versus-heap decision for every container and struct local. A port
that disagrees emits different IR, so it needs its own oracle rather
than being tested only through the IR it eventually feeds.

**The oracle instruments the real compiler rather than reimplementing
its traversal.** `escaping_params` (claude.md #74 stage 2) makes the
answer depend on the ORDER bodies are analyzed in: a call argument is
exempted only when the callee has already been walked. Guessing that
order in a separate driver would be guessing the thing most likely to
be wrong, so this hooks `CodeGen._emit_param_bindings` and
`escape_analysis.find_escaping_names` and records what a genuine
`generate_ir` actually asks for, in the order it asks.

Measured that way, the order is: every FuncDecl and EventHandler in
source order, then the top-level statement list, then any arrow
function -- an arrow's body is analyzed where its expression is
emitted, which is after the enclosing body's own analysis.

Records, one per line:

    SEQ|<index>|<kind>|<name>|<comma-separated sorted escaping names>

`kind` is FUNC, HANDLER or TOPLEVEL. The index makes the ORDER part of
the comparison, not just the contents -- two implementations that
analyze the same bodies in a different order can produce the same
multiset of records while disagreeing about every exemption.

A program the front end rejects answers a single `SEMERR|line|col`, the
same convention the other four dumps use.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from festina import ast, codegen as codegen_mod, escape_analysis   # noqa: E402
from festina import imports as imports_mod                         # noqa: E402
from festina import semantic as semantic_mod                       # noqa: E402
from festina.errors import CompileError                            # noqa: E402


def _key_for(decl):
    """FUNC/HANDLER plus the declaration's own name.

    An arrow function reaches here as the synthesized `ast.FuncDecl`
    claude.md #142 builds (`__festina_arrow_N`), so it needs no case of
    its own -- which is also why a port has to synthesize the same names
    to agree.
    """
    name = getattr(decl, "name", "")
    if isinstance(decl, ast.EventHandler):
        return "HANDLER", name
    return "FUNC", name


def dump_file(path):
    """The escape-analysis record sequence for one file.

    Same hazard irdump.py documents: `CodeGen._uid` is a class
    attribute, so a dump taken after another one in the same process
    starts from a moved counter. Nothing here prints a generated name,
    so it cannot change this dump's contents -- resetting keeps the two
    oracles behaving identically rather than leaving a difference for
    someone to trip over later.
    """
    codegen_mod.CodeGen._uid = 0
    records = []

    orig_bindings = codegen_mod.CodeGen._emit_param_bindings
    orig_find = escape_analysis.find_escaping_names

    def patched_bindings(self, decl, *a, **kw):
        escaping = orig_bindings(self, decl, *a, **kw)
        kind, name = _key_for(decl)
        records.append((kind, name, sorted(escaping)))
        return escaping

    def patched_find(block, escaping_params=None):
        result = orig_find(block, escaping_params=escaping_params)
        # Only the top-level statement list arrives here directly (as
        # codegen's own _StmtList wrapper, which is not an ast.Block);
        # every function and handler body is reached through
        # _emit_param_bindings, which records it above with its name.
        if not isinstance(block, ast.Block):
            records.append(("TOPLEVEL", "", sorted(result)))
        return result

    codegen_mod.CodeGen._emit_param_bindings = patched_bindings
    escape_analysis.find_escaping_names = patched_find
    try:
        program = imports_mod.build_program(path)
        analyzed = semantic_mod.analyze(program, filename=path)
        codegen_mod.generate_ir(program, analyzed, filename=path)
    except CompileError as exc:
        return [f"SEMERR|{getattr(exc, 'line', 0)}|{getattr(exc, 'column', 0)}"]
    finally:
        codegen_mod.CodeGen._emit_param_bindings = orig_bindings
        escape_analysis.find_escaping_names = orig_find

    return [f"SEQ|{i}|{kind}|{name}|{','.join(names)}"
            for i, (kind, name, names) in enumerate(records)]


def main(argv):
    if len(argv) != 2:
        print(__doc__.strip().split("\n\n")[0], file=sys.stderr)
        print("usage: python bootstrap/escdump.py FILE.f", file=sys.stderr)
        return 2
    for line in dump_file(argv[1]):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
