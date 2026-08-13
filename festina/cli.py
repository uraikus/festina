"""The `festina` compiler CLI -- claude.md #1, #47.

    festina main.f              # -> ./main (native executable)
    festina main.f -o app       # -> ./app
    festina main.f --emit-llvm  # -> prints LLVM IR to stdout, no linking

Pipeline (claude.md #47): source -> parse -> semantic analysis -> LLVM IR
-> clang (assembles + links against the Festina runtime) -> native
executable. The resulting executable does not need Python or the
festina package to run (claude.md #47).
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


def _default_output_name(entry_path):
    base = os.path.basename(entry_path)
    if base.endswith(".f"):
        base = base[:-2]
    return base or "a.out"


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
        cflags = subprocess.run(["pkg-config", "--cflags", "sqlite3"],
                                 capture_output=True, text=True, check=False).stdout.split()
        libs = subprocess.run(["pkg-config", "--libs", "sqlite3"],
                               capture_output=True, text=True, check=False).stdout.split()
        # -lm: claude.md #56's Math.floor/ceil/round/trunc lower to LLVM
        # intrinsics that call into libm (round() in particular isn't
        # inlined by clang the way floor/ceil/trunc often are).
        cmd = [cc, "-O2", ir_path, _RUNTIME_C, *cflags, *libs, "-lm", "-o", output_path]
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
        print(f"festina: wrote {result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
