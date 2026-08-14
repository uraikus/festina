"""The `festina` compiler CLI -- claude.md #1, #47, #59.

    festina compile main.f              # -> ./main (native executable)
    festina compile main.f -o app       # -> ./app
    festina compile main.f --emit-llvm  # -> prints LLVM IR to stdout, no linking
    festina run main.f                  # -> compiles to a throwaway temp
                                         #    binary and runs it immediately,
                                         #    forwarding its exit code
    festina doctor                      # -> checks whether the C compiler/
                                         #    pkg-config/sqlite3/etc this
                                         #    module needs are installed, and
                                         #    whether `festina` itself is on
                                         #    PATH
    festina help                        # -> this usage message

Subcommands, not a single bare `festina file.f` -- deliberately: it keeps
`festina run` (which executes the compiled result) unambiguous from
`festina compile` (which never does), rather than inferring intent from
flags the way e.g. `-o` present/absent would have to.

Pipeline (claude.md #47): source -> parse -> semantic analysis -> LLVM IR
-> object file -> link -> native executable. The resulting executable
does not need Python or the festina package to run (claude.md #47).

"Real compilation, minimal setup" (claude.md #59; see setup.md for the
full staged plan and the current dependency list):

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

from . import imports as imports_mod
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
_RUNTIME_GRAPHICS_C = os.path.join(_RUNTIME_DIR, "festina_runtime_graphics.c")
_RUNTIME_AUDIO_C = os.path.join(_RUNTIME_DIR, "festina_runtime_audio.c")
# Both headers are #included by more than one of the .c files above (see
# festina_runtime_internal.h's own doc comment) -- included in every
# object file's cache-freshness check below (_ensure_runtime_object)
# alongside that file's own source, so editing a shared header during
# development invalidates every cached object, not just the one whose
# .c changed.
_RUNTIME_HEADERS = [
    os.path.join(_RUNTIME_DIR, "festina_runtime.h"),
    os.path.join(_RUNTIME_DIR, "festina_runtime_internal.h"),
]

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
             "or `brew install llvm` on macOS -- see setup.md",
    "gcc": "install a C compiler, e.g. `apt install gcc` on Debian/Ubuntu -- see setup.md",
    "cc": "install a C compiler (clang or gcc) -- see setup.md",
}

# claude.md #59's fourth point applies just as much to a pkg-config
# *package* pkg-config itself can't find as to a missing tool -- without
# this, _pkg_config used to silently return an empty flag list (pkg-config
# writes its "package not found" message to stderr and exits nonzero,
# neither of which _pkg_config previously checked), so a missing dev
# package surfaced many steps later as a raw compiler error (e.g.
# "cairo/cairo.h: No such file or directory") instead of naming the
# actual missing dependency.
_PKG_INSTALL_HINTS = {
    "sqlite3": "install its development package, e.g. `apt install libsqlite3-dev` "
               "on Debian/Ubuntu or `brew install sqlite` on macOS",
    "cairo-xlib": "install Cairo's and X11's development packages, e.g. "
                  "`apt install libcairo2-dev libx11-dev` on Debian/Ubuntu -- "
                  "needed for claude.md #37/#39's img/graphics functions",
    "alsa": "install ALSA's development package, e.g. `apt install libasound2-dev` "
            "on Debian/Ubuntu -- needed for claude.md #38's aud/loadAudio()",
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
    if result.returncode != 0:
        package = args[-1]
        hint = _PKG_INSTALL_HINTS.get(
            package, "install its development package and make sure pkg-config can find it")
        raise CompileError(f"'{package}' was not found by pkg-config -- {hint}",
                            category="missing dependency")
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


# Per-feature runtime translation unit metadata -- claude.md #59's "if a
# canvas isn't used, keep the binary slim": festina_runtime.c used to be
# ONE object file with graphics (Cairo/X11) and audio (ALSA) code always
# present in it, so cc's -lcairo/-lX11/-lasound were always on the link
# line -- confirmed empirically that --gc-sections/--as-needed alone
# doesn't fix this (the linker decides a shared library is NEEDED against
# the whole translation unit any live symbol pulls in, before dead-code
# elimination has pruned anything from it, so an unused Cairo/X11/ALSA
# *function* being eliminated doesn't stop the *library* from being
# linked). Splitting into core/graphics/audio translation units (see each
# .c file's own top comment) means the graphics/audio object files --
# and therefore their pkg-config cflags/libs -- are only ever handed to
# `cc` at all for a program that actually uses that feature (see
# compile_file below, driven by CodeGen.uses_graphics/uses_audio from
# festina/codegen.py). "core" has no entry here since it's unconditional
# for every program (see _ensure_runtime_object's cc_source parameter).
_RUNTIME_FEATURES = {
    "graphics": {
        "source": _RUNTIME_GRAPHICS_C,
        "pkg": "cairo-xlib",
        "extra_link_flags": [],
    },
    "audio": {
        "source": _RUNTIME_AUDIO_C,
        "pkg": "alsa",
        # claude.md #38's audio playback runs on a background thread
        # (see festina_runtime.h's doc comment on festina_audio_play) --
        # -pthread is only ever needed for that, so (unlike before the
        # split) it's no longer unconditionally on every link line.
        "extra_link_flags": ["-pthread"],
    },
}


def _ensure_runtime_object(cc, name, source, pkg_config_package):
    """Compile one runtime translation unit (core/graphics/audio) to an
    object file once and reuse it (cached in the system temp dir, keyed
    by mtime and by which `cc` compiled it) instead of recompiling the
    same unchanging file on every `festina compile` invocation. The temp
    dir (rather than alongside the source in runtime/) sidesteps a
    read-only package install being unable to cache anything there.

    Each translation unit gets ONLY the pkg-config cflags it actually
    needs (None for core, which needs nothing beyond sqlite3 -- see the
    caller) -- see _RUNTIME_FEATURES' module docstring note for why this
    matters for the *linked* binary, not just compile-time cflags."""
    cache_dir = os.path.join(tempfile.gettempdir(), "festina-runtime-cache")
    os.makedirs(cache_dir, exist_ok=True)
    cc_key = hashlib.sha1(cc.encode()).hexdigest()[:8]
    obj_path = os.path.join(cache_dir, f"festina_runtime_{name}.{cc_key}.o")

    freshness_sources = [source, *_RUNTIME_HEADERS]
    if (os.path.exists(obj_path)
            and os.path.getmtime(obj_path) >= max(os.path.getmtime(s) for s in freshness_sources)):
        return obj_path

    cflags = _pkg_config("--cflags", "sqlite3")
    if pkg_config_package:
        cflags += _pkg_config("--cflags", pkg_config_package)
    cmd = [cc, "-O2", "-c", source, *cflags, "-o", obj_path]
    result = _run_tool(cmd)
    if result.returncode != 0:
        raise CompileError(f"failed to compile the Festina runtime ({name}):\n{result.stderr}",
                            category="link error")
    return obj_path


def _runtime_objects_and_link_libs(cc, uses_graphics, uses_audio):
    """Every program links core (log/fail/sqlite/regex/timers -- see
    festina_runtime.c's top comment) plus -lm (claude.md #56's
    Math.floor/ceil/round/trunc lower to libm intrinsics -- round() in
    particular isn't inlined by clang/gcc the way floor/ceil/trunc often
    are). graphics/audio's object files -- and only their own
    pkg-config libs -- are added on top exactly when the compiled
    program actually uses that feature, so a program that uses neither
    never gets -lcairo/-lX11/-lasound on cc's command line at all."""
    # core needs no pkg-config package of its own beyond sqlite3 (always
    # included by _ensure_runtime_object itself).
    objects = [_ensure_runtime_object(cc, "core", _RUNTIME_C, None)]
    sqlite_link_flags, _ = _sqlite_link_flags(cc)
    link_libs = [*sqlite_link_flags, "-lm"]

    for name, wants in (("graphics", uses_graphics), ("audio", uses_audio)):
        if not wants:
            continue
        feature = _RUNTIME_FEATURES[name]
        objects.append(_ensure_runtime_object(cc, name, feature["source"], feature["pkg"]))
        link_libs += _pkg_config("--libs", feature["pkg"]) + feature["extra_link_flags"]

    return objects, link_libs


def compile_file(entry_path, output_path=None, emit_llvm=False, cc="clang"):
    # claude.md #5, #6: resolves entry_path's full import graph (a plain
    # single-file program is the degenerate case -- just entry_path on
    # its own) and merges every file into one ast.Program, in dependency
    # order, each top-level statement tagged with the file it actually
    # came from so errors below still name the right file.
    program = imports_mod.build_program(entry_path)
    analyzed = semantic_mod.analyze(program, filename=entry_path)
    # Built directly (rather than via the generate_ir() wrapper still
    # used by callers that only want the IR text, e.g. tests) so this
    # function can read gen.uses_graphics/uses_audio after generation --
    # claude.md #59's "if a canvas isn't used, keep the binary slim"
    # needs to know which optional runtime object files this specific
    # program actually needs (see _runtime_objects_and_link_libs).
    gen = codegen_mod.CodeGen(analyzed, filename=entry_path)
    ir = gen.generate(program)

    if emit_llvm:
        return ir

    output_path = output_path or _default_output_name(entry_path)
    # gen.uses_graphics alone isn't quite enough here: loadImage() alone
    # deliberately does NOT set it (see _emit_graphics_call's doc comment
    # -- decoding a PNG needs no window), but festina_load_image() still
    # lives in the graphics object file, so a program that only ever
    # calls loadImage() still needs it linked even though it never opens
    # a canvas. gen.uses_graphics_code is the superset that also covers
    # that case.
    needs_graphics = gen.uses_graphics or gen.uses_graphics_code
    runtime_objects, link_libs = _runtime_objects_and_link_libs(cc, needs_graphics, gen.uses_audio)

    if llvm_backend.available():
        _compile_via_libllvm(ir, entry_path, output_path, cc, runtime_objects, link_libs)
    else:
        _compile_via_clang_ir_frontend(ir, entry_path, output_path, cc, needs_graphics, gen.uses_audio, link_libs)
    return output_path


def run_program(entry_path, cc="clang"):
    """`festina run` -- compile entry_path to a throwaway temp executable
    and run it immediately, the same way `go run`/`cargo run` do: no
    lasting output file, stdin/stdout/stderr inherited directly from this
    process (not captured) so an interactive program -- graphics, audio,
    timers -- behaves exactly like it would if compiled with `festina
    compile` and then run by hand. The temp binary is always cleaned up
    afterward, compile failure or not.

    A CompileError from compile_file (bad source, missing dependency,
    ...) propagates to the caller unchanged, same as compile_file's own
    contract -- main() below is what turns that into a clean stderr
    message and a nonzero exit code, exactly like it already does for
    `festina compile`. Returns the *compiled program's own* exit code on
    success, so `festina run x.f && ...` composes the same way a real
    compile-then-execute pair would."""
    with tempfile.TemporaryDirectory(prefix="festina-run-") as d:
        out_path = os.path.join(d, "program")
        compile_file(entry_path, out_path, cc=cc)
        result = subprocess.run([out_path])
        return result.returncode


def _compile_via_libllvm(ir, entry_path, output_path, cc, runtime_objects, link_libs):
    """Stage 3: compile IR to an object file ourselves via libLLVM. The
    only work left for `cc` is compiling the runtime translation units
    (cached) and linking plain object files -- no longer requires clang
    specifically, since it never sees LLVM IR text at all."""
    with tempfile.TemporaryDirectory() as d:
        obj_path = os.path.join(d, "program.o")
        try:
            llvm_backend.emit_object_file(ir, obj_path, filename=entry_path)
        except llvm_backend.LLVMBackendError as e:
            raise CompileError(f"LLVM object emission failed:\n{e}",
                                file=entry_path, category="codegen error")
        cmd = [cc, obj_path, *runtime_objects, *link_libs, "-o", output_path]
        result = _run_tool(cmd)
        if result.returncode != 0:
            raise CompileError(f"native linking failed:\n{result.stderr}",
                                file=entry_path, category="link error")


def _compile_via_clang_ir_frontend(ir, entry_path, output_path, cc, needs_graphics, needs_audio, link_libs):
    """Fallback used only when libLLVM couldn't be loaded in this process
    -- the original pipeline, handing the .ll file straight to `cc`
    (which then must actually be clang, or another compiler with an LLVM
    IR frontend -- unlike the libLLVM path above, this one does require
    that specifically). Compiles the runtime .c sources directly rather
    than through the cached-object-file path above, same as this
    fallback always has -- still only the sources the program actually
    needs (needs_graphics/needs_audio, from compile_file), for the same
    binary-slimming reason."""
    runtime_sources = [_RUNTIME_C]
    pkg_configs = ["sqlite3"]
    extra_link_flags = []
    if needs_graphics:
        runtime_sources.append(_RUNTIME_GRAPHICS_C)
        pkg_configs.append("cairo-xlib")
    if needs_audio:
        runtime_sources.append(_RUNTIME_AUDIO_C)
        pkg_configs.append("alsa")
        extra_link_flags.append("-pthread")
    cflags = []
    for pkg in pkg_configs:
        cflags += _pkg_config("--cflags", pkg)

    with tempfile.NamedTemporaryFile(suffix=".ll", mode="w", delete=False) as tmp:
        tmp.write(ir)
        ir_path = tmp.name
    try:
        cmd = [cc, "-O2", ir_path, *runtime_sources, *cflags, *link_libs, *extra_link_flags, "-o", output_path]
        result = _run_tool(cmd)
        if result.returncode != 0:
            raise CompileError(f"native linking failed:\n{result.stderr}",
                                file=entry_path, category="link error")
    finally:
        os.unlink(ir_path)


# ---- festina doctor -- claude.md #59's "fail loudly and clearly" applied
# proactively, before a compile is even attempted, rather than only
# reactively (that's what _run_tool/_pkg_config above already do). Reuses
# the exact same install hints (_INSTALL_HINTS/_PKG_INSTALL_HINTS) so a
# `doctor` report and a real compile failure always say the same thing
# about the same missing tool. ----

def _which_any(*names):
    """The first of these tool names found on PATH, as (name, full path),
    or (None, None) if none of them are -- mirrors compile_file's own
    clang/gcc/cc fallback order (see main()'s default_cc), just without
    committing to any one of them the way an actual compile has to."""
    for name in names:
        path = shutil.which(name)
        if path:
            return name, path
    return None, None


def _pkg_config_has(package):
    """True if pkg-config can find this package's .pc file. Unlike
    _pkg_config above (which raises a CompileError -- correct for an
    actual compile, which cannot proceed without it), doctor's checks are
    all non-fatal by design: one missing optional dependency shouldn't
    stop the rest of the report from running. Always False if pkg-config
    itself isn't even on PATH (checked separately, its own doctor line)."""
    if shutil.which("pkg-config") is None:
        return False
    result = subprocess.run(["pkg-config", "--exists", package], capture_output=True)
    return result.returncode == 0


def _doctor_report():
    """Builds `festina doctor`'s report as a list of plain-text lines,
    plus whether every REQUIRED dependency is present. graphics/audio are
    deliberately NOT required -- claude.md #59/security.md's binary-
    slimming split means a compiler that can't build a graphics program
    is still a fully working compiler for everything else; a program
    that never uses graphics/audio never even asks for cairo-xlib/alsa's
    flags (see _RUNTIME_FEATURES above). Returns (lines, all_required_ok)
    so main() can turn the second value into an exit code without
    re-parsing the printed text."""
    lines = []
    all_ok = True

    def check(ok, required, label, hint=None):
        nonlocal all_ok
        if ok:
            status = "OK"
        elif required:
            status = "MISSING"
            all_ok = False
        else:
            status = "missing, optional"
        lines.append(f"  [{status:^17}] {label}")
        if not ok and hint:
            lines.append(f"  {'':19} -> {hint}")

    lines.append("Festina compiler dependencies")
    lines.append("==============================")

    cc_name, cc_path = _which_any("clang", "gcc", "cc")
    check(cc_name is not None, True,
          f"C compiler ({cc_name} at {cc_path})" if cc_name else "C compiler (clang, gcc, or cc)",
          "install one, e.g. `apt install clang` on Debian/Ubuntu, or `brew install llvm` on macOS -- see setup.md")

    pkgconf_path = shutil.which("pkg-config")
    check(pkgconf_path is not None, True,
          f"pkg-config (at {pkgconf_path})" if pkgconf_path else "pkg-config",
          _INSTALL_HINTS["pkg-config"])

    check(_pkg_config_has("sqlite3"), True,
          "sqlite3 dev headers (required -- every Festina program has SQLite built in, claude.md #10/#28-31)",
          _PKG_INSTALL_HINTS["sqlite3"])

    check(_pkg_config_has("cairo-xlib"), False,
          "cairo-xlib dev headers (optional -- only used by graphics: drawRect, on click, img, ...)",
          _PKG_INSTALL_HINTS["cairo-xlib"])

    check(_pkg_config_has("alsa"), False,
          "alsa dev headers (optional -- only used by audio: loadAudio(), .play(), ...)",
          _PKG_INSTALL_HINTS["alsa"])

    llvm_ok = llvm_backend.available()
    has_clang = shutil.which("clang") is not None
    if llvm_ok:
        check(True, False, "libLLVM (fast path -- in-process object emission, works with any C compiler above)")
    else:
        # Not required on its own (the clang-IR-frontend fallback covers
        # it), UNLESS that fallback's own specific requirement -- clang,
        # not just any C compiler -- also isn't met; then neither
        # pipeline can actually finish a compile, which *is* required.
        check(False, not has_clang,
              "libLLVM (not found -- falls back to handing LLVM IR text to clang directly)",
              None if has_clang else
              "clang was not found either, and only clang can parse the raw IR text that fallback "
              "needs -- install `llvm` (e.g. `apt install llvm` on Debian/Ubuntu) or clang itself")

    lines.append("")
    lines.append("festina on PATH")
    lines.append("================")
    festina_path = shutil.which("festina")
    if festina_path:
        lines.append(f"  [{'OK':^17}] 'festina' resolves to {festina_path}")
    else:
        lines.append(f"  [{'not on PATH':^17}] 'festina' -- you're running it another way right "
                      f"now (bin/festina, python3 -m festina.cli, or a packaged binary by its own path)")
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            # Running the packaged binary itself (PyInstaller --onefile);
            # sys.executable is that binary's own real path, so this can
            # give a concrete, copy-pasteable command rather than generic
            # advice.
            lines.append(f"  {'':19} this is the packaged binary -- put it on PATH directly, e.g.:")
            lines.append(f"  {'':19}   sudo ln -s {sys.executable} /usr/local/bin/festina")
        else:
            bin_dir = os.path.join(_data_root(), "bin")
            lines.append(f"  {'':19} add this checkout's bin/ directory to PATH:")
            lines.append(f"  {'':19}   export PATH=\"$PATH:{bin_dir}\"")
            lines.append(f"  {'':19} add that line to ~/.bashrc / ~/.zshrc (or your shell's equivalent "
                          f"startup file) to keep it, then restart your shell -- or run "
                          f"scripts/package_compiler.sh and put the resulting standalone binary "
                          f"somewhere already on PATH instead (see setup.md)")
    return lines, all_ok


def _run_doctor():
    lines, all_ok = _doctor_report()
    print("\n".join(lines))
    print()
    if all_ok:
        print("All required dependencies are installed. The optional ones (graphics/audio) "
              "only matter for a program that actually uses drawRect/on click/img/loadAudio/etc.")
        return 0
    print("One or more REQUIRED dependencies are missing -- see the MISSING lines above.")
    return 1


def _build_arg_parser():
    default_cc = shutil.which("clang") or shutil.which("gcc") or shutil.which("cc") or "clang"
    cc_help = "C compiler/linker to invoke (default: clang, gcc, or cc, whichever is found first)"

    ap = argparse.ArgumentParser(prog="festina", description="Compile and run Festina programs.")
    # dest="command", not required=True: an unrecognized/missing
    # subcommand falls through to main()'s own help-and-exit-1 handling
    # below, rather than argparse's own less friendly "the following
    # arguments are required" message.
    sub = ap.add_subparsers(dest="command", metavar="command")

    compile_p = sub.add_parser("compile", help="compile a Festina program to a native executable")
    compile_p.add_argument("input", help="entry .f file")
    compile_p.add_argument("-o", "--output", help="output executable path (default: input filename without .f)")
    compile_p.add_argument("--emit-llvm", action="store_true", help="print LLVM IR to stdout instead of linking")
    compile_p.add_argument("--cc", default=default_cc, help=cc_help)

    run_p = sub.add_parser("run", help="compile a Festina program and immediately run it")
    run_p.add_argument("input", help="entry .f file")
    run_p.add_argument("--cc", default=default_cc, help=cc_help)

    sub.add_parser("doctor", help="check whether the compiler's own dependencies are installed")
    sub.add_parser("help", help="show this help message")
    return ap


def main(argv=None):
    ap = _build_arg_parser()
    args = ap.parse_args(argv)

    if args.command in (None, "help"):
        ap.print_help()
        # A bare `festina` with no subcommand at all is a usage mistake
        # (exit 1); `festina help` is a deliberate, successful request
        # for the same text (exit 0) -- same distinction `git`/`cargo`
        # draw between "no command" and "explicitly asked for help".
        return 0 if args.command == "help" else 1

    if args.command == "doctor":
        return _run_doctor()

    if args.command == "run":
        try:
            return run_program(args.input, cc=args.cc)
        except CompileError as e:
            print(str(e), file=sys.stderr)
            return 1
        except OSError as e:
            print(f"festina: {e}", file=sys.stderr)
            return 1

    # args.command == "compile"
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
