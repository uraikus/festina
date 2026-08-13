"""The `festina` compiler CLI -- claude.md #1, #47, #59.

    festina main.f              # -> ./main (native executable)
    festina main.f -o app       # -> ./app
    festina main.f --emit-llvm  # -> prints LLVM IR to stdout, no linking

Pipeline (claude.md #47): source -> parse -> semantic analysis -> LLVM IR
-> object file -> link -> native executable. The resulting executable
does not need Python or the festina package to run (claude.md #47).

"Real compilation, minimal setup" (claude.md #59; see README.md's
"Deployment"/"Setup" sections for the full staged plan and the current
dependency list):

- stage 1: sqlite3 is statically linked into the compiled program when a
  static archive is available (_sqlite_link_flags), so a program built
  here doesn't need libsqlite3.so present on the machine that *runs* it
  -- falls back to a normal dynamic link otherwise.
- stage 2: this module (specifically compile_file/main) is what
  packaging/festina_entry.py + scripts/package_compiler.sh bundle into
  a standalone binary via PyInstaller -- *using* the compiler no longer
  needs a separate Python install, just the packaged binary itself
  (still needs a C compiler/linker at runtime to actually build a
  Festina program, same as ever -- stage 2 only removes the Python
  dependency, not the C toolchain one). See _data_root() below for how
  runtime/festina_runtime.c gets found once this module is no longer
  running from an ordinary file on disk.
- stage 3: the LLVM IR -> object file step is done in-process via
  festina.llvm_backend (libLLVM's C API through ctypes), not by handing
  the .ll file to clang. That used to be the reason `cc` specifically
  had to be clang -- gcc has no .ll frontend at all (verified: it hands
  the file to `ld`, which fails treating it as a broken linker script).
  With that step handled ourselves, whatever's left for `cc` to do
  (compile festina_runtime.c, link plain object files) is compiler-
  agnostic, so any working C compiler/linker now works, not just clang.
  If libLLVM can't be loaded in this process at all,
  _compile_via_clang_ir_frontend below is the original pipeline,
  unchanged, as a fallback -- this is purely additive.

claude.md #59 also requires a genuinely missing dependency to fail with
a clear, actionable error rather than a raw one -- _run_tool below wraps
every external-tool invocation for exactly that (verified: without it, a
missing pkg-config surfaced as a bare "[Errno 2] No such file or
directory: 'pkg-config'"; check=False alone doesn't catch this, it only
suppresses a nonzero *exit code*, not a failure to launch the binary at
all).
"""
import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile

from . import parser as parser_mod
from . import semantic as semantic_mod
from . import codegen as codegen_mod
from . import llvm_backend
from .errors import CompileError

def _data_root():
    """Base directory to resolve bundled data (currently just runtime/)
    against. Under "real compilation, minimal setup" stage 2's packaged
    binary (claude.md #59; see packaging/festina_entry.py and
    scripts/package_compiler.sh), the running process is PyInstaller's
    --onefile self-extraction -- files added via --add-data land in a
    temp dir exposed as sys._MEIPASS, not this file's ordinary on-disk
    location (this module itself is loaded from inside a bundle archive
    at that point, not a real .py file on a real path). Falls back to
    the normal source-tree layout otherwise (dev checkout, or any other
    way of running festina/ that isn't the packaged binary)."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return meipass
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


_RUNTIME_DIR = os.path.join(_data_root(), "runtime")
_RUNTIME_C = os.path.join(_RUNTIME_DIR, "festina_runtime.c")

_sqlite_link_cache = {}


def _default_output_name(entry_path):
    base = os.path.basename(entry_path)
    if base.endswith(".f"):
        base = base[:-2]
    return base or "a.out"


# claude.md #59: a missing dependency must fail with a clear, actionable
# error naming it and how to get it -- not a raw exception. Centralized
# here since every external-tool invocation in this module (pkg-config,
# cc, in either order) can hit "the tool itself doesn't exist," which
# `subprocess.run(..., check=False)` alone does *not* catch: check=False
# only suppresses a nonzero *exit code*, not FileNotFoundError from
# failing to launch a binary that isn't there at all (verified: with
# pkg-config hidden from PATH, this used to surface as a bare
# "[Errno 2] No such file or directory: 'pkg-config'").
_INSTALL_HINTS = {
    "pkg-config": "install it, e.g. `apt install pkg-config` on Debian/Ubuntu "
                  "or `brew install pkg-config` on macOS -- used to locate sqlite3's compiler flags",
    "clang": "install a C compiler, e.g. `apt install clang` on Debian/Ubuntu "
             "or `brew install llvm` on macOS -- see README.md's Setup section",
    "gcc": "install a C compiler, e.g. `apt install gcc` on Debian/Ubuntu -- see README.md's Setup section",
    "cc": "install a C compiler (clang or gcc) -- see README.md's Setup section",
}


def _run_tool(cmd, **kwargs):
    """subprocess.run, but a missing executable becomes a clear
    CompileError naming the tool and how to install it, instead of a raw
    FileNotFoundError."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    except FileNotFoundError:
        tool = cmd[0]
        hint = _INSTALL_HINTS.get(tool, "install it and make sure it's on PATH")
        raise CompileError(f"'{tool}' is not installed or not on PATH -- {hint}",
                            category="missing dependency")


def _pkg_config(*args):
    result = _run_tool(["pkg-config", *args], check=False)
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
        result = _run_tool([cc, src, *extra_flags, "-o", out])
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


def _ensure_runtime_object(cc):
    """Compile festina_runtime.c to an object file once and reuse it
    (cached in the system temp dir, keyed by mtime and by which `cc`
    compiled it) instead of recompiling the same unchanging file on
    every `festina compile` invocation. The temp dir (rather than
    alongside the source in runtime/) sidesteps a read-only package
    install being unable to cache anything there."""
    cache_dir = os.path.join(tempfile.gettempdir(), "festina-runtime-cache")
    os.makedirs(cache_dir, exist_ok=True)
    cc_key = hashlib.sha1(cc.encode()).hexdigest()[:8]
    obj_path = os.path.join(cache_dir, f"festina_runtime.{cc_key}.o")

    if os.path.exists(obj_path) and os.path.getmtime(obj_path) >= os.path.getmtime(_RUNTIME_C):
        return obj_path

    cflags = _pkg_config("--cflags", "sqlite3")
    cmd = [cc, "-O2", "-c", _RUNTIME_C, *cflags, "-o", obj_path]
    result = _run_tool(cmd)
    if result.returncode != 0:
        raise CompileError(f"failed to compile the Festina runtime:\n{result.stderr}", category="link error")
    return obj_path


def compile_file(entry_path, output_path=None, emit_llvm=False, cc="clang"):
    with open(entry_path, encoding="utf-8") as f:
        source = f.read()

    program = parser_mod.parse(source, filename=entry_path)
    analyzed = semantic_mod.analyze(program, filename=entry_path)
    ir = codegen_mod.generate_ir(program, analyzed, filename=entry_path)

    if emit_llvm:
        return ir

    output_path = output_path or _default_output_name(entry_path)
    # -lm: claude.md #56's Math.floor/ceil/round/trunc lower to LLVM
    # intrinsics that call into libm (round() in particular isn't inlined
    # by clang/gcc the way floor/ceil/trunc often are).
    sqlite_link_flags, _ = _sqlite_link_flags(cc)
    link_libs = [*sqlite_link_flags, "-lm"]

    if llvm_backend.available():
        _compile_via_libllvm(ir, entry_path, output_path, cc, link_libs)
    else:
        _compile_via_clang_ir_frontend(ir, entry_path, output_path, cc, link_libs)
    return output_path


def _compile_via_libllvm(ir, entry_path, output_path, cc, link_libs):
    """Stage 3: compile IR to an object file ourselves via libLLVM. The
    only work left for `cc` is compiling festina_runtime.c (cached) and
    linking plain object files -- no longer requires clang specifically,
    since it never sees LLVM IR text at all."""
    with tempfile.TemporaryDirectory() as d:
        obj_path = os.path.join(d, "program.o")
        try:
            llvm_backend.emit_object_file(ir, obj_path, filename=entry_path)
        except llvm_backend.LLVMBackendError as e:
            raise CompileError(f"LLVM object emission failed:\n{e}",
                                file=entry_path, category="codegen error")
        runtime_obj = _ensure_runtime_object(cc)
        cmd = [cc, obj_path, runtime_obj, *link_libs, "-o", output_path]
        result = _run_tool(cmd)
        if result.returncode != 0:
            raise CompileError(f"native linking failed:\n{result.stderr}",
                                file=entry_path, category="link error")


def _compile_via_clang_ir_frontend(ir, entry_path, output_path, cc, link_libs):
    """Fallback used only when libLLVM couldn't be loaded in this process
    -- the original pipeline, handing the .ll file straight to `cc`
    (which then must actually be clang, or another compiler with an LLVM
    IR frontend -- unlike the libLLVM path above, this one does require
    that specifically)."""
    with tempfile.NamedTemporaryFile(suffix=".ll", mode="w", delete=False) as tmp:
        tmp.write(ir)
        ir_path = tmp.name
    try:
        cmd = [cc, "-O2", ir_path, _RUNTIME_C, *link_libs, "-o", output_path]
        result = _run_tool(cmd)
        if result.returncode != 0:
            raise CompileError(f"native linking failed:\n{result.stderr}",
                                file=entry_path, category="link error")
    finally:
        os.unlink(ir_path)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="festina", description="Compile a Festina program to a native executable.")
    ap.add_argument("input", help="entry .f file")
    ap.add_argument("-o", "--output", help="output executable path (default: input filename without .f)")
    ap.add_argument("--emit-llvm", action="store_true", help="print LLVM IR to stdout instead of linking")
    default_cc = shutil.which("clang") or shutil.which("gcc") or shutil.which("cc") or "clang"
    ap.add_argument("--cc", default=default_cc,
                     help="C compiler/linker to invoke (default: clang, gcc, or cc, whichever is found first)")
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
        notes = []
        if not sqlite_static:
            notes.append("sqlite3 linked dynamically -- no static libsqlite3.a found")
        if not llvm_backend.available():
            notes.append("libLLVM unavailable -- compiled via clang's IR frontend instead")
        suffix = f" ({'; '.join(notes)})" if notes else ""
        print(f"festina: wrote {result}{suffix}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
