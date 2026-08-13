"""The `festina` compiler CLI -- claude.md #1, #47.

    festina main.f              # -> ./main (native executable)
    festina main.f -o app       # -> ./app
    festina main.f --emit-llvm  # -> prints LLVM IR to stdout, no linking

Pipeline (claude.md #47): source -> parse -> semantic analysis -> LLVM IR
-> clang (assembles + links against the Festina runtime) -> native
executable. The resulting executable does not need Python or the
festina package to run (claude.md #47).

sqlite3 is statically linked into the compiled program when a static
archive is available (_sqlite_link_flags), so a program built here
doesn't need libsqlite3.so present on the machine that runs it -- falls
back to a normal dynamic link otherwise, so this still works in
environments that only ship the shared library. This is step 1 of
"minimal setup to use Festina" (see the project's design discussion);
step 2 is removing the *build-time* dependency on a separately-installed
clang/LLVM, which this module doesn't address yet -- `cc` here still
shells out to a real `clang` (or whatever `--cc` points at).
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile

from . import parser as parser_mod
from . import semantic as semantic_mod
from . import codegen as codegen_mod
from .errors import CompileError

_RUNTIME_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "runtime")
_RUNTIME_C = os.path.join(_RUNTIME_DIR, "festina_runtime.c")

_sqlite_link_cache = {}


def _default_output_name(entry_path):
    base = os.path.basename(entry_path)
    if base.endswith(".f"):
        base = base[:-2]
    return base or "a.out"


def _pkg_config(*args):
    result = subprocess.run(["pkg-config", *args], capture_output=True, text=True, check=False)
    return result.stdout.split()


def _can_link(cc, extra_flags):
    """True if a trivial program links with these extra flags. Used to
    probe for a static archive's availability: with -Bstatic active, `-lfoo`
    only matches libfoo.a (never libfoo.so), so the linker fails to find it
    even though the probe program references none of its symbols -- no
    need for a real caller to make this determination."""
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "probe.c")
        out = os.path.join(d, "probe")
        with open(src, "w", encoding="utf-8") as f:
            f.write("int main(void) { return 0; }\n")
        result = subprocess.run([cc, src, *extra_flags, "-o", out], capture_output=True, text=True)
        return result.returncode == 0


def _sqlite_link_flags(cc):
    """Prefer statically linking sqlite3 into the compiled program, so it
    doesn't need libsqlite3.so present at runtime -- falls back to a
    normal dynamic link if no static archive is available in this build
    environment. Only sqlite3 itself is pinned static; libc/libm etc.
    stay dynamic as usual (this isn't attempting a fully static binary
    the way Go produces -- see the "real compilation, minimal setup"
    discussion this was written for)."""
    if cc in _sqlite_link_cache:
        return _sqlite_link_cache[cc]

    cflags = _pkg_config("--cflags", "sqlite3")
    dynamic_libs = _pkg_config("--libs", "sqlite3")
    static_libs = _pkg_config("--static", "--libs", "sqlite3")
    other_static_libs = [lib for lib in static_libs if lib != "-lsqlite3"]

    static_attempt = ["-Wl,-Bstatic", "-lsqlite3", "-Wl,-Bdynamic", *other_static_libs]
    if _can_link(cc, cflags + static_attempt):
        flags = (cflags + static_attempt, True)
    else:
        flags = (cflags + dynamic_libs, False)

    _sqlite_link_cache[cc] = flags
    return flags


def compile_file(entry_path, output_path=None, emit_llvm=False, cc="clang"):
    with open(entry_path, encoding="utf-8") as f:
        source = f.read()

    program = parser_mod.parse(source, filename=entry_path)
    analyzed = semantic_mod.analyze(program, filename=entry_path)
    ir = codegen_mod.generate_ir(program, analyzed, filename=entry_path)

    if emit_llvm:
        return ir

    output_path = output_path or _default_output_name(entry_path)
    with tempfile.NamedTemporaryFile(suffix=".ll", mode="w", delete=False) as tmp:
        tmp.write(ir)
        ir_path = tmp.name
    try:
        sqlite_link_flags, _ = _sqlite_link_flags(cc)
        # -lm: claude.md #56's Math.floor/ceil/round/trunc lower to LLVM
        # intrinsics that call into libm (round() in particular isn't
        # inlined by clang the way floor/ceil/trunc often are).
        cmd = [cc, "-O2", ir_path, _RUNTIME_C, *sqlite_link_flags, "-lm", "-o", output_path]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise CompileError(
                f"native linking failed:\n{result.stderr}",
                file=entry_path, category="link error",
            )
    finally:
        os.unlink(ir_path)
    return output_path


def main(argv=None):
    ap = argparse.ArgumentParser(prog="festina", description="Compile a Festina program to a native executable.")
    ap.add_argument("input", help="entry .f file")
    ap.add_argument("-o", "--output", help="output executable path (default: input filename without .f)")
    ap.add_argument("--emit-llvm", action="store_true", help="print LLVM IR to stdout instead of linking")
    ap.add_argument("--cc", default=shutil.which("clang") or "clang", help="C compiler/linker to invoke (default: clang)")
    args = ap.parse_args(argv)

    try:
        result = compile_file(args.input, args.output, emit_llvm=args.emit_llvm, cc=args.cc)
    except CompileError as e:
        print(str(e), file=sys.stderr)
        return 1
    except OSError as e:
        print(f"festina: {e}", file=sys.stderr)
        return 1

    if args.emit_llvm:
        print(result)
    else:
        _, sqlite_static = _sqlite_link_flags(args.cc)  # cached by compile_file's own call
        note = "" if sqlite_static else " (sqlite3 linked dynamically -- no static libsqlite3.a found)"
        print(f"festina: wrote {result}{note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
