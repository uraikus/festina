"""The oracle codegen is ported against: festina/codegen.py's own IR.

The other three stages needed a canonical form invented for them -- a
token line, an AST dump, a binding record -- because their outputs are
Python objects with no text form of their own. Codegen needs none: its
output IS text, and the comparison is the text, line for line. That
makes this the strongest oracle in the project and the only one with no
design decisions in it.

It does have one hazard, and it is not obvious:

**`CodeGen._uid` is a CLASS attribute.** `_unique()` does
`CodeGen._uid += 1`, so the counter keeps climbing across every CodeGen
instance in a process. Generating IR for the same file twice in one
process therefore produces two different texts -- identical in
structure, with every generated name shifted by a constant:

    -@__festina_stmtcache_7       +@__festina_stmtcache_17
    -%a.1  %b.2  %dx.3            +%a.11 %b.12 %dx.13

Nothing is wrong with the compiler: the CLI compiles one file per
process, and a class-level counter is a sound way to keep generated
names unique. But an oracle that ran in-process would hand the Festina
side a moving target, and the failure would look like a port bug in
every file after the first rather than like a harness bug.

`dump_file` resets the counter for exactly this reason, and
`test_bootstrap_codegen.py` pins that the reset is real by checking a
second dump of the same file against a genuinely fresh subprocess.

Usage:

    python bootstrap/irdump.py path/to/file.f      # print the IR

A file the front end rejects dumps `SEMERR|line|col` alone, exactly as
semdump.py does -- position, never message text, so the port is never
asked to reproduce English.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from festina import codegen as codegen_mod   # noqa: E402
from festina import imports as imports_mod   # noqa: E402
from festina import semantic as semantic_mod # noqa: E402
from festina.errors import CompileError      # noqa: E402


def _reset_uid():
    """Put `CodeGen._uid` back to its module-load value.

    See this module's own docstring for why. Reaching into the compiler
    from the harness is deliberate, and the right direction: the
    alternative is a reset hook in codegen.py that exists only for
    tests, and claude.md #280 already settled that the harness carries
    the test-only machinery, not the compiler.
    """
    codegen_mod.CodeGen._uid = 0


def dump_file(path):
    """The IR for one file, as a list of lines.

    A rejected program answers a single `SEMERR|line|col` record, so a
    file the front end refuses is still a comparable outcome rather
    than a crash -- the same convention semdump.py uses.
    """
    _reset_uid()
    try:
        program = imports_mod.build_program(path)
        analyzed = semantic_mod.analyze(program, filename=path)
        text = codegen_mod.generate_ir(program, analyzed, filename=path)
    except CompileError as exc:
        return [f"SEMERR|{getattr(exc, 'line', 0)}|{getattr(exc, 'column', 0)}"]
    lines = text.split("\n")
    # generate_ir's text ends with a newline, so the split leaves one
    # empty element that is an artifact of the terminator rather than a
    # line of the module. The Festina side's captured stdout ends the
    # same way and irdiff drops it there too; dropping it on only one
    # side would make every single file differ on its last line.
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def main(argv):
    if len(argv) != 2:
        print(__doc__.strip().split("\n\n")[0], file=sys.stderr)
        print("usage: python bootstrap/irdump.py FILE.f", file=sys.stderr)
        return 2
    for line in dump_file(argv[1]):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
