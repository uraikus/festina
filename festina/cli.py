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
    os.path.join(_RUNTIME_DIR, "festina_runtime_window.h"),  # claude.md #123
]

_sqlite_link_cache = {}

# A real sentinel, not None -- _festina_path_fix_plan's own injectable
# parameters (festina_path, meipass, shell_env) each have a legitimate
# "explicitly absent" value of None/"" that a test needs to be able to
# pass in on purpose (e.g. "PATH genuinely doesn't resolve festina" or
# "$SHELL genuinely isn't set"), which None-as-default can't tell apart
# from "the caller didn't pass this argument at all, so go compute the
# real one."
_UNSET = object()


def _default_output_name(entry_path, platform_name=None, target="native"):
    """windows.md Phase 0: on Windows the default output gains `.exe` --
    both because the shell only executes files with an executable
    extension, and because MinGW's linker appends `.exe` itself when
    the requested name has no suffix, so asking for `program` and then
    running `program` would miss the `program.exe` actually written.
    `platform_name` is injectable purely so the win32/darwin branches
    are unit-testable from any platform (tests/test_platform.py).

    claude.md #148: a wasm32-wasi build gets `.wasm` regardless of
    platform_name -- the HOST doing the compiling has no bearing on
    what the OUTPUT actually is (a .wasm binary is not something any
    shell on ANY host executes directly the way a native binary or
    .exe is; see wasm.md for how it's actually run)."""
    platform_name = platform_name or sys.platform
    base = os.path.basename(entry_path)
    if base.endswith(".f"):
        base = base[:-2]
    base = base or "a.out"
    if target == "wasm32-wasi" and not base.lower().endswith(".wasm"):
        base += ".wasm"
    elif platform_name == "win32" and not base.lower().endswith(".exe"):
        base += ".exe"
    return base


def _rename_if_linker_appended_exe(output_path):
    """windows.md Phase 0 (claude.md #126): MinGW's linker appends
    `.exe` to a `-o` path that doesn't already end in one regardless
    of whether that path was _default_output_name's own choice or an
    EXPLICIT caller request -- only the former was actually accounted
    for (that path always already ends in `.exe`, so the linker's
    auto-append never fires). An explicit `-o program` (no suffix)
    silently linked to `program.exe` while `compile_file` kept
    claiming `program` was the output, so the caller's own exact
    request should win: if the linker wrote `output_path + ".exe"`
    instead of `output_path` itself, rename it back.

    claude.md #126 round twelve: this used to skip the rename whenever
    `output_path` already existed -- meaning a SECOND compile to the
    same explicit path (recompiling `program` after changing the
    source, exactly what every one of tests/test_codegen.py's
    TestAutomaticSqliteSchemaSync tests does via compile_and_run's
    reused `tmp_path / "program"`) left the first compile's stale
    binary sitting at `output_path` untouched, while the fresh build
    sat next to it at `output_path + ".exe"`, never picked up. Real
    Windows CI's own diagnostics caught this directly: the "second"
    compiled program's captured stdout was the FIRST program's own
    output verbatim. `os.replace` already atomically overwrites an
    existing destination on Windows -- the `not os.path.exists(...)`
    check was actively wrong, not a needed safety guard, since the
    whole point of this function is putting the just-linked binary at
    the caller's exact requested path, past compile or not."""
    if sys.platform != "win32" or output_path.lower().endswith(".exe"):
        return
    exe_path = output_path + ".exe"
    if os.path.exists(exe_path):
        os.replace(exe_path, output_path)


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
    # claude.md #123/#128: darwin's and windows' own package -- plain
    # Cairo, no X11 half (the windowing backend there is native Cocoa/
    # Win32, not cairo-xlib).
    "cairo": "install Cairo's development package, e.g. `brew install cairo` "
             "on macOS or `pacman -S mingw-w64-ucrt-x86_64-cairo` from a "
             "UCRT64 shell on Windows -- needed for claude.md #37/#39's "
             "img/graphics functions",
    "libjpeg": "install libjpeg's development package, e.g. "
                "`apt install libjpeg-dev` (Debian/Ubuntu), "
                "`brew install jpeg-turbo` (macOS), or "
                "`pacman -S mingw-w64-ucrt-x86_64-libjpeg-turbo` from a "
                "UCRT64 shell (Windows)",
    "libmpg123": "install mpg123's development package, e.g. "
                  "`apt install libmpg123-dev` (Debian/Ubuntu) or "
                  "`brew install mpg123` (macOS)",
    "alsa": "install ALSA's development package, e.g. `apt install libasound2-dev` "
            "on Debian/Ubuntu -- needed for claude.md #38's aud/loadAudio()",
    # windows.md Phase 0: the one core-runtime addition on Windows, not
    # an optional feature tier like the others above -- every program
    # needs it, the same way every program needs sqlite3. Two real
    # Windows CI rounds (claude.md #126) to land on this: round one's
    # mingw-w64-ucrt-x86_64-libgnurx genuinely installs, but pacman
    # --noconfirm silently drops it from the install set because it
    # CONFLICTS with mingw-w64-ucrt-x86_64-libsystre (already present
    # as a transitive dependency of the UCRT64 toolchain) -- so the
    # PACKAGE to install is libsystre. Round two's guess that pkg-config
    # would also answer to that same name was wrong: libsystre's own
    # PKGBUILD declares Provides/Conflicts/Replaces against libgnurx
    # (it's a designed drop-in replacement, which is exactly why they
    # conflict at all) and ships its pkgconfig file under the OLD
    # name -- gnurx.pc, not libsystre.pc -- confirmed via MSYS2's own
    # package listing. So: install libsystre, ask pkg-config for gnurx.
    "gnurx": "install MSYS2's POSIX regex package from a UCRT64 shell, e.g. "
             "`pacman -S mingw-w64-ucrt-x86_64-libsystre` -- needed for "
             "claude.md #67/#68's regex()/.test()/.match()/.replace(), which "
             "every compiled program links whether it uses regex or not "
             "(see windows.md Phase 0)",
}

# `festina doctor --fix`: the same dependencies as _INSTALL_HINTS/
# _PKG_INSTALL_HINTS above, but as literal package names per package
# manager instead of prose a human has to read and copy-paste from.
# Deliberately narrow -- only the three package managers setup.md
# itself documents and actually tests against (apt on Debian/Ubuntu,
# Homebrew on macOS, MSYS2's pacman on Windows). A key with no entry
# for the detected manager (or a manager --fix doesn't recognize at
# all, e.g. dnf/pacman-on-Arch/zypper) means "print the hint above and
# let the person install it by hand" rather than guessing a command
# that might be wrong -- the same "fail loudly and clearly" preference
# claude.md #59 already applies to a missing dependency itself.
#
# "cc" has no "brew" entry on purpose: the actual macOS fix is Xcode
# Command Line Tools (`xcode-select --install`, setup.md's own
# recommendation -- Apple's clang already works, brew's own llvm
# formula is unnecessary and keg-only besides), which pops a GUI
# installer --fix cannot drive non-interactively. _run_doctor_fix
# special-cases that one dependency with its own printed note instead
# of a package list.
_PKG_MANAGER_PACKAGES = {
    "cc": {"apt": ["clang"], "msys2": ["mingw-w64-ucrt-x86_64-clang"]},
    "pkg-config": {"apt": ["pkg-config"], "brew": ["pkg-config"],
                   "msys2": ["mingw-w64-ucrt-x86_64-pkgconf"]},
    "sqlite3": {"apt": ["libsqlite3-dev"], "brew": ["sqlite"],
                "msys2": ["mingw-w64-ucrt-x86_64-sqlite3"]},
    "cairo-xlib": {"apt": ["libcairo2-dev", "libx11-dev"]},  # linux-only check
    "cairo": {"brew": ["cairo"], "msys2": ["mingw-w64-ucrt-x86_64-cairo"]},  # mac/win-only check
    "libjpeg": {"apt": ["libjpeg-dev"], "brew": ["jpeg-turbo"],
                "msys2": ["mingw-w64-ucrt-x86_64-libjpeg-turbo"]},
    "libmpg123": {"apt": ["libmpg123-dev"], "brew": ["mpg123"]},  # linux/mac-only check
    "alsa": {"apt": ["libasound2-dev"]},  # linux-only check
    "gnurx": {"msys2": ["mingw-w64-ucrt-x86_64-libsystre"]},  # windows-only check
    # Optional speed win, not a blocker -- see the check() call site's
    # own "not required(has_clang)" logic. brew/msys2 need nothing
    # separate here: brew's "llvm" formula is the same one "cc"'s fix
    # already covers, and clang alone is enough on Windows too (see
    # setup.md's "no llvm line here either" note).
    "llvm": {"apt": ["llvm"]},
    # claude.md #148: apt-only, like alsa/gnurx above -- wasi-libc and
    # clang's wasm32 compiler-rt are Debian/Ubuntu package names this
    # project has actually installed and verified (runtime/wasm/
    # README.md); no brew/msys2 equivalent has been found or tried, so
    # nothing is claimed for those managers rather than guessing a
    # package name that might not exist.
    "wasm": {"apt": ["wasi-libc", "libclang-rt-18-dev-wasm32"]},
}


def _run_tool(cmd, **kwargs):
    """subprocess.run, but a missing executable becomes a clear
    CompileError naming the tool and how to install it, instead of a raw
    FileNotFoundError.

    claude.md #126 round eight, found by real Windows CI: this used to
    let subprocess.run resolve cmd[0] itself, which is NOT the same
    search PATH-only tools like shutil.which perform on Windows --
    Win32's CreateProcess (what subprocess.run calls into with no
    executable= override) additionally searches the calling process's
    OWN directory before it ever looks at PATH, per Microsoft's own
    documented search order. On a Windows CI runner where Python itself
    is an MSYS2 UCRT64 package, that directory is the SAME bin/ pkg-
    config and the C toolchain also live in -- so a test that hides a
    tool by restricting PATH (tests/conftest.py's path_without) fooled
    shutil.which-based checks (festina doctor) but not an actual
    subprocess.run call, which found the tool anyway via that extra
    search location and silently succeeded where it should have raised
    "missing dependency". Resolving explicitly via shutil.which FIRST
    makes every tool invocation PATH-only and deterministic on every
    platform, closing that gap rather than working around it in tests."""
    if shutil.which(cmd[0]) is None:
        tool = cmd[0]
        hint = _INSTALL_HINTS.get(tool, "install it and make sure it's on PATH")
        raise CompileError(f"'{tool}' is not installed or not on PATH -- {hint}",
                            category="missing dependency")
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


def _static_sqlite_attempt(platform_name, static_libs, libdir=None):
    """The platform-appropriate spelling of "link libsqlite3.a, not the
    shared library" -- extracted from _sqlite_link_flags so each
    platform's strategy is a pure, unit-testable value
    (tests/test_platform.py; macos.md/windows.md Phase 0).

    GNU ld (Linux, and MinGW on Windows -- windows.md's toolchain
    decision keeps this branch working there unchanged) supports the
    `-Bstatic`/`-Bdynamic` toggles, scoping "static please" to exactly
    the -lsqlite3 between them. macOS's ld64 rejects `-Bstatic`
    outright (previously that just made the _can_link probe fail and
    silently forced dynamic linking -- correct, but never static), so
    the darwin strategy names the archive by explicit path instead,
    from pkg-config's libdir; with no libdir there is nothing to try
    and the answer is None (caller falls back to dynamic).

    Returns the flag list to probe with _can_link, or None."""
    other_static_libs = [lib for lib in static_libs if lib != "-lsqlite3"]
    if platform_name == "darwin":
        if not libdir:
            return None
        return [os.path.join(libdir, "libsqlite3.a"), *other_static_libs]
    return ["-Wl,-Bstatic", "-lsqlite3", "-Wl,-Bdynamic", *other_static_libs]


def _windows_static_runtime_flags(cc, uses_audio, platform_name=None):
    """windows.md Phase 3 item 2 (claude.md #129): the "copy-anywhere"
    half of the DLL story for compiled programs -- a MinGW-built binary
    otherwise depends on a handful of MSYS2 runtime DLLs
    (`libgcc_s_seh-1.dll`, `libwinpthread-1.dll`) that are not part of
    a bare Windows install, unlike sqlite3 (already statically linked
    by _sqlite_link_flags above, windows.md Phase 0's own toolchain
    decision keeping the same `-Bstatic`/`-Bdynamic` GNU ld toggles
    working on both Linux and MinGW-on-Windows).

    `-static-libgcc` is a standard, universally-understood GCC/Clang
    driver flag -- no probe needed, and harmless to pass even when a
    program never actually needs anything from libgcc_s. Winpthread is
    different: only audio actually calls pthread_* (the channel pool's
    own background-playback threads, festina_runtime.h's doc comment
    on festina_audio_play), reached today only via the ALREADY-linked
    dynamic `-pthread` flag in `_RUNTIME_FEATURES["audio"]`
    ["extra_link_flags"] -- adding a SECOND, statically-scoped
    `-lwinpthread` on top of that flag's own implicit linking for the
    same program risks a link-order conflict this project has no
    Windows machine to test, so this only ever applies when audio is
    NOT in play, exactly matching windows.md's own "core-only programs"
    framing for this trick (core and offscreen-only graphics both
    qualify -- neither needs pthread at all).

    Probed the same way _sqlite_link_flags probes -lsqlite3's static
    archive (_can_link, reused verbatim) rather than assumed, since
    this project has no Windows machine to confirm mingw-w64-ucrt-
    x86_64-winpthreads ships libwinpthread.a and not only the shared
    libwinpthread-1.dll -- if it doesn't, this silently falls back to
    the ordinary dynamic dependency instead of a hard link error,
    exactly like the sqlite3 case."""
    platform_name = platform_name or sys.platform
    if platform_name != "win32":
        return []
    flags = ["-static-libgcc"]
    if not uses_audio:
        winpthread_static = ["-Wl,-Bstatic", "-lwinpthread", "-Wl,-Bdynamic"]
        if _can_link(cc, winpthread_static):
            flags += winpthread_static
    return flags


def _sqlite_link_flags(cc):
    """Prefer statically linking sqlite3 into the compiled program, so it
    doesn't need libsqlite3.so present at runtime -- falls back to a
    normal dynamic link if no static archive is available in this build
    environment. Only sqlite3 itself is pinned static; libc/libm etc.
    stay dynamic as usual (this isn't attempting a fully static binary
    the way Go produces -- see the "real compilation, minimal setup"
    discussion this was written for). The static ATTEMPT is
    per-platform (_static_sqlite_attempt above); the probe-then-fall-
    back shape is shared."""
    if cc in _sqlite_link_cache:
        return _sqlite_link_cache[cc]

    cflags = _pkg_config("--cflags", "sqlite3")
    dynamic_libs = _pkg_config("--libs", "sqlite3")
    static_libs = _pkg_config("--static", "--libs", "sqlite3")
    libdir = None
    if sys.platform == "darwin":
        libdir_tokens = _pkg_config("--variable=libdir", "sqlite3")
        libdir = libdir_tokens[0] if libdir_tokens else None

    static_attempt = _static_sqlite_attempt(sys.platform, static_libs, libdir)
    if static_attempt is not None and _can_link(cc, cflags + static_attempt):
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
_RUNTIME_WINDOW_MAC_M = os.path.join(_RUNTIME_DIR, "festina_runtime_window_mac.m")
# windows.md Phase 2 / claude.md #128: the Win32 counterpart to
# _RUNTIME_WINDOW_MAC_M -- unlike Cocoa, plain C, but still its own
# companion object, since none of festina_runtime_graphics.c's X11
# headers exist under MinGW (see that file's own top comment).
_RUNTIME_WINDOW_WIN32_C = os.path.join(_RUNTIME_DIR, "festina_runtime_window_win32.c")

_RUNTIME_FEATURES = {
    "graphics": {
        "source": _RUNTIME_GRAPHICS_C,
        # claude.md #101 added libjpeg alongside Cairo: Cairo decodes
        # PNG on its own but nothing else, and JPEG needs a real
        # decoder. libjpeg rather than a heavier toolkit for the same
        # reason Xlib was picked over a GUI toolkit (claude.md #59) --
        # the smallest dependency that does the job.
        # claude.md #123: the WINDOWING half is per-platform now (see
        # _feature_pkgs_and_flags) -- X11, compiled inline in
        # festina_runtime_graphics.c, on Linux; Cocoa, a separate
        # companion object (_RUNTIME_WINDOW_MAC_M), on darwin. Cairo's
        # drawing itself and libjpeg decoding are shared everywhere.
        "pkgs": ["cairo-xlib", "libjpeg"],
        "extra_link_flags": [],
    },
    "audio": {
        "source": _RUNTIME_AUDIO_C,
        # claude.md #101: libmpg123 is the MP3 counterpart to libjpeg
        # above -- the WAV parser is hand-written (a container simple
        # enough to walk directly), MP3 is not.
        # claude.md #121: the DEVICE half is per-platform now (see
        # _feature_pkgs_and_flags) -- ALSA on Linux, AudioToolbox on
        # darwin -- while libmpg123 and the channel pool are shared.
        "pkgs": ["alsa", "libmpg123"],
        # claude.md #38's audio playback runs on a background thread
        # (see festina_runtime.h's doc comment on festina_audio_play) --
        # -pthread is only ever needed for that, so (unlike before the
        # split) it's no longer unconditionally on every link line.
        "extra_link_flags": ["-pthread"],
    },
}


def _core_pkgs(platform_name=None):
    """windows.md Phase 0: the one core-runtime dependency ADDITION on
    Windows. `<regex.h>` (festina_runtime.c's regex()/`.test()`/etc,
    linked into every program -- see that file's own top comment) is
    part of libc on Linux and BSD/macOS libc alike, so core needs no
    pkg-config package of its own there. MinGW-w64 doesn't ship a POSIX
    regex implementation at all, so on win32 this pulls in a package
    that provides one, discovered across two real Windows CI rounds
    (claude.md #126): windows.md's originally preferred `libgnurx`
    genuinely installs, but pacman drops it from the install set
    because it CONFLICTS with `libsystre` (a POSIX regex.h/regcomp/
    regexec wrapper around TRE, already pulled in as a transitive
    dependency of the UCRT64 toolchain) -- so `libsystre` is the
    package to INSTALL. But libsystre's own PKGBUILD declares
    Provides/Conflicts/Replaces against libgnurx (it's a designed
    drop-in replacement -- exactly why they conflict) and ships its
    pkgconfig file under the OLD name, `gnurx.pc`, not `libsystre.pc`
    -- so `gnurx` is the name to ask pkg-config FOR, a different string
    than the package name entirely. windows.md's fallback for this
    kind of surprise (vendoring musl's regcomp/regexec/regfree) turned
    out not to be needed either time: gnurx is a real, already-present
    POSIX regex.h implementation, not a divergent one. Injectable
    platform_name for the unit tests, exactly like
    _static_sqlite_attempt/_feature_pkgs_and_flags above."""
    platform_name = platform_name or sys.platform
    return ["gnurx"] if platform_name == "win32" else []


def _feature_pkgs_and_flags(name, platform_name=None):
    """claude.md #121 / macos.md Phase 1: a feature's pkg-config
    packages and extra link flags, per platform. The table above holds
    the Linux answer (the platform everything was built on); this
    adjusts it where another platform's device layer differs -- on
    darwin the audio device is AudioToolbox (a system framework, no
    pkg-config file), so `alsa` drops out and `-framework AudioToolbox`
    comes in. Injectable platform_name for the unit tests."""
    platform_name = platform_name or sys.platform
    feature = _RUNTIME_FEATURES[name]
    pkgs = list(feature["pkgs"])
    flags = list(feature["extra_link_flags"])
    if name == "audio" and platform_name == "darwin":
        pkgs.remove("alsa")
        flags += ["-framework", "AudioToolbox"]
    elif name == "audio" and platform_name == "win32":
        # windows.md Phase 1: the device layer is winmm's waveOut, a
        # system DLL with no pkg-config file of its own (same shape as
        # AudioToolbox above) -- `alsa` drops out (Linux-only) and
        # `-lwinmm` comes in. libmpg123 stays: MSYS2 UCRT64 ships a real
        # pkg-config package for it, same as every other platform.
        pkgs.remove("alsa")
        flags += ["-lwinmm"]
    elif name == "graphics" and platform_name == "darwin":
        # claude.md #123: on darwin festina_runtime_graphics.c has ZERO
        # X11 code compiled into it (guarded `#ifndef __APPLE__`), so
        # it needs only Cairo's own core package, not the xlib backend
        # -- and the Cocoa windowing companion object needs the Cocoa
        # framework, a system framework with no pkg-config file of its
        # own.
        pkgs.remove("cairo-xlib")
        pkgs.append("cairo")
        flags += ["-framework", "Cocoa"]
    elif name == "graphics" and platform_name == "win32":
        # windows.md Phase 2: same shape as darwin above -- on win32
        # festina_runtime_graphics.c also has ZERO X11 code compiled
        # into it (guarded `#if !defined(__APPLE__) && !defined(_WIN32)`),
        # so `cairo-xlib` drops out in favor of plain `cairo` (MSYS2
        # UCRT64 ships a real pkg-config package for it, same as
        # darwin's Homebrew). The Win32 windowing companion object
        # (_RUNTIME_WINDOW_WIN32_C) needs GDI (StretchDIBits, window
        # creation) and USER32 (RegisterClassEx/CreateWindowEx/message
        # pump) -- system DLLs with import libraries but no pkg-config
        # file, the same shape winmm was for Phase 1's audio.
        pkgs.remove("cairo-xlib")
        pkgs.append("cairo")
        flags += ["-lgdi32", "-luser32"]
    return pkgs, flags


def _feature_extra_object(cc, name, platform_name=None):
    """claude.md #123 / #128: the one companion object a feature needs
    beyond its own `_RUNTIME_FEATURES[name]["source"]` -- graphics on
    darwin, where Cocoa cannot be compiled as part of
    festina_runtime_graphics.c's plain C translation unit at all and so
    lives in its own Objective-C file (_RUNTIME_WINDOW_MAC_M); and
    graphics on win32, where the Win32 windowing backend COULD in
    principle share festina_runtime_graphics.c's own translation unit
    (it is plain C too, unlike Cocoa) but stays a separate file anyway,
    matching darwin's shape one-for-one rather than inventing a second
    convention for exactly one platform. Returns None everywhere else.
    Reuses _ensure_runtime_object's own cache-by-mtime machinery --
    clang infers Objective-C from the .m extension with no extra flag
    needed, so compiling either companion object is the same shape as
    any other runtime object file."""
    platform_name = platform_name or sys.platform
    if name == "graphics" and platform_name == "darwin":
        # claude.md #126: this file #includes <cairo.h> (CGImage/cairo
        # interop in drawRect:) same as festina_runtime_graphics.c
        # does, so it needs cairo's own pkg-config cflags too -- an
        # empty list here meant it silently never got them, and this
        # translation unit is the ONE place real macOS CI could ever
        # catch that, since nothing else on any platform compiles it.
        return _ensure_runtime_object(cc, "window_mac", _RUNTIME_WINDOW_MAC_M, ["cairo"])
    if name == "graphics" and platform_name == "win32":
        # windows.md Phase 2: same cairo-cflags need as window_mac.m
        # above, for the same reason -- festina_runtime_window_win32.c
        # #includes <cairo.h> directly (the StretchDIBits blit reads
        # cairo_image_surface_get_data/width/height straight off the
        # backing surface).
        return _ensure_runtime_object(cc, "window_win32", _RUNTIME_WINDOW_WIN32_C, ["cairo"])
    return None


def _ensure_runtime_object(cc, name, source, pkg_config_packages):
    """Compile one runtime translation unit (core/graphics/audio) to an
    object file once and reuse it (cached in the system temp dir, keyed
    by mtime and by which `cc` compiled it) instead of recompiling the
    same unchanging file on every `festina compile` invocation. The temp
    dir (rather than alongside the source in runtime/) sidesteps a
    read-only package install being unable to cache anything there.

    Each translation unit gets ONLY the pkg-config cflags it actually
    needs (None for core, which needs nothing beyond sqlite3 -- see the
    caller) -- see _RUNTIME_FEATURES' module docstring note for why this
    matters for the *linked* binary, not just compile-time cflags. A
    feature may need several packages (claude.md #101: graphics is
    Cairo/X11 *and* libjpeg, audio is ALSA *and* libmpg123).

    WASM export (claude.md #148) deliberately does NOT reuse this
    function -- see _ensure_wasm_object below -- since its own cflags
    come from nowhere pkg-config knows about at all (a vendored sqlite3
    header, not the system's pkg-config'd one), and folding that
    fundamentally different cflags story into this one would complicate
    a function every native platform already relies on for something
    only one target needs."""
    cache_dir = os.path.join(tempfile.gettempdir(), "festina-runtime-cache")
    os.makedirs(cache_dir, exist_ok=True)
    cc_key = hashlib.sha1(cc.encode()).hexdigest()[:8]
    obj_path = os.path.join(cache_dir, f"festina_runtime_{name}.{cc_key}.o")

    freshness_sources = [source, *_RUNTIME_HEADERS]
    if (os.path.exists(obj_path)
            and os.path.getmtime(obj_path) >= max(os.path.getmtime(s) for s in freshness_sources)):
        return obj_path

    cflags = _pkg_config("--cflags", "sqlite3")
    for pkg in pkg_config_packages or ():
        cflags += _pkg_config("--cflags", pkg)
    cmd = [cc, "-O2", "-c", source, *cflags, "-o", obj_path]
    result = _run_tool(cmd)
    if result.returncode != 0:
        raise CompileError(f"failed to compile the Festina runtime ({name}):\n{result.stderr}",
                            category="link error")
    return obj_path


# ---- WASM export (claude.md #148) ----
#
# WASI (Preview 1) via clang's own --target=wasm32-wasi, verified end to
# end against a real, immediately-runnable target -- Node.js's built-in
# WASI support -- rather than only reasoned about, the same "verify for
# real, not just in theory" standard this whole log holds every other
# platform to. See wasm.md for the full design writeup, benchmarks
# against C/Go compiled to wasm, and documented limitations; this
# section is the implementation.
_WASM_TARGET = "wasm32-wasi"
_WASM_DIR = os.path.join(_RUNTIME_DIR, "wasm")
_WASM_SQLITE_C = os.path.join(_WASM_DIR, "sqlite3.c")
_WASM_ENTRY_C = os.path.join(_RUNTIME_DIR, "festina_runtime_wasm_entry.c")


def _ensure_wasm_object(cc, name, source, include_dirs=()):
    """The WASM counterpart to _ensure_runtime_object -- same cache-once
    -and-reuse shape (a real compile-time cost: the vendored sqlite3.c
    amalgamation alone takes ~20 real seconds even at -O2), but a
    genuinely different cflags story, so kept separate rather than
    parameterizing the native function for a target it was never meant
    to know about. `festina_wasm_{name}` (not `festina_runtime_{name}`)
    keeps this cache namespace-separate from native's own -- the two
    are never the same object even when `cc` (plain "clang", no
    --target flag baked into the string itself) happens to match."""
    cache_dir = os.path.join(tempfile.gettempdir(), "festina-runtime-cache")
    os.makedirs(cache_dir, exist_ok=True)
    cc_key = hashlib.sha1(cc.encode()).hexdigest()[:8]
    obj_path = os.path.join(cache_dir, f"festina_wasm_{name}.{cc_key}.o")

    freshness_sources = [source, *_RUNTIME_HEADERS]
    if (os.path.exists(obj_path)
            and os.path.getmtime(obj_path) >= max(os.path.getmtime(s) for s in freshness_sources)):
        return obj_path

    include_flags = [f"-I{d}" for d in include_dirs]
    cmd = [cc, f"--target={_WASM_TARGET}", "-O2", "-c", source, *include_flags, "-o", obj_path]
    result = _run_tool(cmd)
    if result.returncode != 0:
        raise CompileError(f"failed to compile the Festina WASM runtime ({name}):\n{result.stderr}",
                            category="link error")
    return obj_path


def _wasm_runtime_objects(cc):
    """core (linked against the vendored sqlite3.h, see runtime/wasm/
    README.md) + the vendored sqlite3.c itself + the __main_void entry
    bridge (see festina_runtime_wasm_entry.c's own top comment for why
    that one exists at all) -- no graphics, no audio, ever: see
    _check_wasm_feature_supported, called unconditionally before this
    even runs, for why."""
    return [
        _ensure_wasm_object(cc, "core", _RUNTIME_C, include_dirs=[_WASM_DIR]),
        _ensure_wasm_object(cc, "sqlite3", _WASM_SQLITE_C),
        _ensure_wasm_object(cc, "entry", _WASM_ENTRY_C),
    ]


def _check_wasm_feature_supported(feature):
    """Unlike _check_feature_supported's macOS/Windows gates (a real
    backend EXISTS, awaiting hardware verification -- overridable once
    that happens), there is no graphics or audio backend for WASI at
    all to verify: no display server, no audio device model WASI
    exposes -- see wasm.md's own "Limitations" section for the full
    accounting. No env var escape hatch, because there is nothing an
    override could actually turn on."""
    if feature == "graphics":
        raise CompileError(
            "graphics (drawRect/drawCircle/drawText/img/render()/mouse & "
            "key events/...) is not supported when compiling to WASM -- "
            "WASI has no display server or windowing model at all. See "
            "wasm.md's Limitations section.",
            category="unsupported platform feature")
    if feature == "audio":
        raise CompileError(
            "audio (aud/play()/playLoop()/...) is not supported when "
            "compiling to WASM -- WASI has no audio device model at all. "
            "See wasm.md's Limitations section.",
            category="unsupported platform feature")


def _wasm_toolchain_ok(cc):
    """`festina doctor`'s own WASM check (claude.md #148). Deliberately a
    REAL functional probe -- actually invoking `cc --target=wasm32-wasi`
    on a trivial C snippet -- rather than guessing at wasi-libc/
    libclang_rt's install paths (which vary by distro/package version:
    this project found them at /usr/lib/wasm32-wasi and
    /usr/lib/llvm-18/lib/clang/18/lib/wasi on the Debian box this was
    built on, but hardcoding either path here would be exactly the kind
    of unverified guess this codebase's own doctor checks elsewhere
    (_pkg_config_has, _which_any) avoid by construction. A round-trip
    compile is the only check that can't give a false "OK" -- clang
    itself is happy to accept --target=wasm32-wasi as a flag and then
    fail deep in the link step if wasi-libc's headers/libs aren't
    actually there."""
    if shutil.which(cc) is None:
        return False
    try:
        result = subprocess.run(
            [cc, f"--target={_WASM_TARGET}", "-x", "c", "-", "-o", os.devnull],
            input="int main(void) { return 0; }\n",
            capture_output=True, text=True, timeout=30)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _check_feature_supported(feature, platform_name=None):
    """macos.md/windows.md Phase 0: a feature whose backend does not
    exist yet on this platform fails with a message that says exactly
    that -- and where the work is planned -- instead of a pkg-config
    error telling a Mac or Windows user to install a library that does
    not exist on their OS. `platform_name` is injectable so every
    branch is unit-testable from any platform (tests/test_platform.py).

    Every branch here now gates a backend that EXISTS (built,
    CI-compiled) but awaits real-hardware verification, overridable via
    an env var for exactly that verification -- windows.md Phase 2's
    graphics gate joined this shape alongside Phase 1's own audio gate
    and every darwin gate (claude.md #128); there is no remaining
    "nothing built yet, raises unconditionally" branch left on any
    platform. All raise the same category so the conftest skip picks
    them up uniformly.

    `feature` here is a narrower question than "is this object file
    linked" (see needs_graphics/wants_window in compile_file): audio
    has no OFFSCREEN mode, so its own gate covers the whole feature,
    but graphics does -- drawing to an image surface and saveCanvas()
    never touches the seam at all -- so the caller only invokes the
    "graphics" gate when a program actually opens a window
    (gen.uses_graphics, not the broader uses_graphics_code)."""
    platform_name = platform_name or sys.platform
    if feature == "audio" and platform_name == "darwin":
        # claude.md #121: the AudioQueue backend EXISTS (compiled and
        # null-shim-tested by macOS CI) but has not been verified
        # against a real output device on hardware, so the gate stays
        # until it has -- overridable for exactly that verification.
        if os.environ.get("FESTINA_ENABLE_MACOS_AUDIO"):
            return
        raise CompileError(
            "audio is not yet verified on macOS -- the AudioQueue "
            "backend is built (macos.md Phase 1) but awaits real-"
            "hardware verification; set FESTINA_ENABLE_MACOS_AUDIO=1 "
            "to try it. Everything except aud/play() works today.",
            category="unsupported platform feature")
    if feature == "audio" and platform_name == "win32":
        # windows.md Phase 1: the waveOut backend now EXISTS (built,
        # compiled by windows CI against real <mmsystem.h> headers) but
        # has not been verified against a real output device on real
        # hardware -- same gate, same override, same reason as the
        # darwin branch above.
        if os.environ.get("FESTINA_ENABLE_WINDOWS_AUDIO"):
            return
        raise CompileError(
            "audio is not yet verified on Windows -- the waveOut "
            "backend is built (windows.md Phase 1) but awaits real-"
            "hardware verification; set FESTINA_ENABLE_WINDOWS_AUDIO=1 "
            "to try it. Everything except aud/play() works today.",
            category="unsupported platform feature")
    if feature == "graphics" and platform_name == "darwin":
        # claude.md #123: the Cocoa windowing backend EXISTS (compiled
        # and type-checked by macOS CI) but, like AudioQueue before it,
        # has not been confirmed against a real window/mouse/keyboard
        # on real hardware -- same gate, same override, same reason.
        # Offscreen drawing (drawRect()+saveCanvas(), no render(), no
        # event handlers) never reaches this check at all -- see this
        # function's own docstring.
        if os.environ.get("FESTINA_ENABLE_MACOS_GRAPHICS"):
            return
        raise CompileError(
            "windowed graphics (render(), or an on mouseDown/mouseUp/"
            "mouse/keyDown/keyUp/resize/close handler) is not yet "
            "verified on macOS -- the Cocoa backend is built (macos.md "
            "Phase 2) but awaits real-hardware verification; set "
            "FESTINA_ENABLE_MACOS_GRAPHICS=1 to try it. Drawing to an "
            "offscreen canvas and saveCanvas() work today with no "
            "window involved at all.",
            category="unsupported platform feature")
    if feature == "graphics" and platform_name == "win32":
        # windows.md Phase 2 / claude.md #128: the Win32 windowing
        # backend now EXISTS (built, compiled by windows CI against
        # real <windows.h> headers) -- same shape as the darwin
        # graphics gate above, INVERTED from the "nothing built yet"
        # unconditional raise this branch used to be (claude.md #126
        # round six). Because a real window_win32 companion object now
        # provides festina_window_open and friends, offscreen-only use
        # (drawRect()+saveCanvas(), no render(), no event handler) also
        # links again on win32 -- _runtime_objects_and_link_libs's
        # win32-only offscreen-gate-exemption carve-out is retired
        # along with this branch's old unconditional raise; see that
        # function's own updated docstring.
        if os.environ.get("FESTINA_ENABLE_WINDOWS_GRAPHICS"):
            return
        raise CompileError(
            "windowed graphics (render(), or an on mouseDown/mouseUp/"
            "mouse/keyDown/keyUp/resize/close handler) is not yet "
            "verified on Windows -- the Win32 backend is built "
            "(windows.md Phase 2) but awaits real-hardware verification; "
            "set FESTINA_ENABLE_WINDOWS_GRAPHICS=1 to try it. Drawing to "
            "an offscreen canvas and saveCanvas() work today with no "
            "window involved at all.",
            category="unsupported platform feature")


def _runtime_objects_and_link_libs(cc, uses_graphics, uses_audio, wants_window=False):
    """Every program links core (log/fail/sqlite/regex/timers -- see
    festina_runtime.c's top comment) plus -lm (claude.md #56's
    Math.floor/ceil/round/trunc lower to libm intrinsics -- round() in
    particular isn't inlined by clang/gcc the way floor/ceil/trunc often
    are). graphics/audio's object files -- and only their own
    pkg-config libs -- are added on top exactly when the compiled
    program actually uses that feature, so a program that uses neither
    never gets -lcairo/-lX11/-lasound on cc's command line at all.

    `wants_window` (claude.md #123) is narrower than `uses_graphics`
    here: it is compile_file's gen.uses_graphics (a real window will
    actually open at runtime -- render() or an event handler), not the
    broader "the graphics object file is needed at all" this function's
    own `uses_graphics` parameter means (which also covers a purely
    offscreen drawRect()+saveCanvas() program). Only `wants_window`
    reaches _check_feature_supported's platform gate -- offscreen
    drawing must never hit it, on any platform, since it never touches
    the windowing seam at all (see that function's own docstring). The
    graphics object -- and its companion windowing object on darwin/
    win32, which the offscreen path also needs linked (see
    _feature_extra_object's own comment) -- is still linked whenever
    `uses_graphics` (broad) is true, gate or no gate.

    claude.md #126 round four (found by real Windows CI) added a win32-
    only carve-out here: at that point Windows had no window backend at
    all -- no window_win32 companion object existed the way window_mac.m
    did -- so even an OFFSCREEN-only program failed at the *linker*
    stage with `_festina_window_open` and friends undefined, since
    festina_runtime_graphics.c references those symbols unconditionally
    regardless of whether a given program ever calls render() or an
    event handler. windows.md Phase 2 / claude.md #128 retires that
    carve-out: festina_runtime_window_win32.c now provides those
    symbols, so offscreen use links -- and is gate-exempt -- on win32
    exactly like every other platform, checked identically rather than
    with a platform-specific branch here."""
    # core needs no pkg-config package of its own beyond sqlite3 (always
    # included by _ensure_runtime_object itself) on Linux/macOS;
    # windows.md Phase 0 adds gnurx on win32 for <regex.h> -- see
    # _core_pkgs's own docstring.
    core_pkgs = _core_pkgs()
    objects = [_ensure_runtime_object(cc, "core", _RUNTIME_C, core_pkgs)]
    sqlite_link_flags, _ = _sqlite_link_flags(cc)
    link_libs = [*sqlite_link_flags, "-lm"]
    for pkg in core_pkgs:
        link_libs += _pkg_config("--libs", pkg)

    for name, wants in (("graphics", uses_graphics), ("audio", uses_audio)):
        if not wants:
            continue
        skip_gate = name == "graphics" and not wants_window
        if not skip_gate:
            _check_feature_supported(name)
        feature = _RUNTIME_FEATURES[name]
        pkgs, extra_flags = _feature_pkgs_and_flags(name)
        objects.append(_ensure_runtime_object(cc, name, feature["source"], pkgs))
        extra_object = _feature_extra_object(cc, name)
        if extra_object:
            objects.append(extra_object)
        for pkg in pkgs:
            link_libs += _pkg_config("--libs", pkg)
        link_libs += extra_flags

    link_libs += _windows_static_runtime_flags(cc, uses_audio)
    return objects, link_libs


def compile_file(entry_path, output_path=None, emit_llvm=False, cc="clang", target="native"):
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
    # claude.md #148: target is threaded into CodeGen itself, not just
    # this function's own linking choices below -- wasm32-wasi's 32-bit
    # size_t needs real codegen differences (see CodeGen.__init__'s own
    # note on self.pointer_bits), not just a different link recipe.
    gen = codegen_mod.CodeGen(analyzed, filename=entry_path, target=target)
    ir = gen.generate(program)

    if emit_llvm:
        return ir

    output_path = output_path or _default_output_name(entry_path, target=target)
    # gen.uses_graphics alone isn't quite enough here: loadImage() alone
    # deliberately does NOT set it (see _emit_graphics_call's doc comment
    # -- decoding a PNG needs no window), but festina_load_image() still
    # lives in the graphics object file, so a program that only ever
    # calls loadImage() still needs it linked even though it never opens
    # a canvas. gen.uses_graphics_code is the superset that also covers
    # that case.
    needs_graphics = gen.uses_graphics or gen.uses_graphics_code

    if target == "wasm32-wasi":
        _compile_via_wasm(ir, entry_path, output_path, cc, needs_graphics, gen.uses_audio)
        return output_path

    runtime_objects, link_libs = _runtime_objects_and_link_libs(
        cc, needs_graphics, gen.uses_audio, wants_window=gen.uses_graphics)

    if llvm_backend.available():
        _compile_via_libllvm(ir, entry_path, output_path, cc, runtime_objects, link_libs)
    else:
        _compile_via_clang_ir_frontend(ir, entry_path, output_path, cc, needs_graphics, gen.uses_audio, link_libs)
    _rename_if_linker_appended_exe(output_path)
    return output_path


_WASM_RUN_SCRIPT = os.path.join(_WASM_DIR, "run_wasi.mjs")


def run_program(entry_path, cc="clang", target="native"):
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
    compile-then-execute pair would.

    claude.md #148: target=wasm32-wasi runs the compiled .wasm through
    Node's own WASI support (runtime/wasm/run_wasi.mjs) instead of
    executing it directly -- a .wasm file isn't something any OS's
    shell can exec on its own the way a native binary or .exe is."""
    with tempfile.TemporaryDirectory(prefix="festina-run-") as d:
        # windows.md Phase 0: through _default_output_name so the temp
        # binary is `program.exe` on Windows -- MinGW's linker appends
        # .exe itself when the name has no suffix, and running the name
        # we ASKED for rather than the file it WROTE would fail.
        out_path = os.path.join(d, _default_output_name("program.f", target=target))
        compile_file(entry_path, out_path, cc=cc, target=target)
        if target == "wasm32-wasi":
            node = shutil.which("node")
            if node is None:
                raise CompileError(
                    "running a WASM binary needs Node.js on PATH, for its "
                    "built-in WASI support -- see wasm.md.",
                    file=entry_path, category="missing dependency")
            # cwd=os.getcwd(), not `d`: a compiled program's own relative
            # paths (festina.sqlite, blob/mkdir/ls targets) resolve
            # against the INVOKING shell's directory, exactly like a
            # native compiled binary's own cwd-relative access already
            # does -- the preopen just has to name that same directory.
            result = subprocess.run(
                [node, "--no-warnings", _WASM_RUN_SCRIPT, out_path, os.getcwd()])
            return result.returncode
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
    binary-slimming reason.

    claude.md #126 round four: this is the path macOS CI actually runs
    (ci.yml deliberately skips installing the libLLVM bottle there --
    the libLLVM fast path is exercised by the Linux job only), and it
    had never been updated for _feature_pkgs_and_flags/
    _feature_extra_object's own per-platform darwin swaps at all -- it
    used _RUNTIME_FEATURES[name]["pkgs"] (the Linux table) directly and
    never linked _RUNTIME_WINDOW_MAC_M in. That was invisible as long
    as festina_runtime_window_mac.m itself never compiled (blocked by
    the cairo.h bugs the earlier rounds fixed); the moment it did, every
    graphics program -- offscreen ones included, since they link the
    same object -- failed at link time with `_festina_window_open` and
    friends undefined, the real windowing symbols only the (never
    linked) Cocoa companion object provides."""
    runtime_sources = [_RUNTIME_C]
    pkg_configs = ["sqlite3", *_core_pkgs()]
    extra_link_flags = []
    if needs_graphics:
        runtime_sources.append(_RUNTIME_GRAPHICS_C)
        pkgs, flags = _feature_pkgs_and_flags("graphics")
        pkg_configs += pkgs
        extra_link_flags += flags
        extra_object = _feature_extra_object(cc, "graphics")
        if extra_object:
            runtime_sources.append(extra_object)
    if needs_audio:
        runtime_sources.append(_RUNTIME_AUDIO_C)
        # -pthread comes back from _feature_pkgs_and_flags itself (the
        # audio feature's own extra_link_flags, claude.md #38) -- no
        # need to add it again here.
        pkgs, flags = _feature_pkgs_and_flags("audio")
        pkg_configs += pkgs
        extra_link_flags += flags
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


def _compile_via_wasm(ir, entry_path, output_path, cc, needs_graphics, needs_audio):
    """claude.md #148: WASM export's own link recipe -- always the .ll-
    text-to-clang path (see _compile_via_clang_ir_frontend's own
    docstring for what that fallback normally covers on native targets;
    here it's not a fallback at all, it's the only path, since there is
    no libLLVM in-process wasm32 object-emission story this project has
    verified -- --target=wasm32-wasi needs clang specifically, not
    "whichever of clang/gcc/cc"). Rejects graphics/audio OUTRIGHT before
    doing any real work -- see _check_wasm_feature_supported -- rather
    than letting a doomed compile run for tens of seconds (the vendored
    sqlite3.c amalgamation alone) only to fail at the link step with
    undefined Cairo/ALSA symbols nothing here could ever provide."""
    if needs_graphics:
        _check_wasm_feature_supported("graphics")
    if needs_audio:
        _check_wasm_feature_supported("audio")
    if shutil.which(cc) is None or "clang" not in os.path.basename(cc).lower():
        raise CompileError(
            f"WASM export needs clang specifically (got --cc={cc!r}) -- "
            f"only clang can target wasm32-wasi at all. See wasm.md.",
            file=entry_path, category="missing dependency")

    runtime_objects = _wasm_runtime_objects(cc)

    with tempfile.NamedTemporaryFile(suffix=".ll", mode="w", delete=False) as tmp:
        tmp.write(ir)
        ir_path = tmp.name
    try:
        cmd = [cc, f"--target={_WASM_TARGET}", "-O2", ir_path, *runtime_objects, "-o", output_path]
        result = _run_tool(cmd)
        if result.returncode != 0:
            raise CompileError(f"WASM linking failed:\n{result.stderr}",
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
    flags (see _RUNTIME_FEATURES above). Returns (lines, all_required_ok,
    missing) so main() can turn the second value into an exit code
    without re-parsing the printed text, and `festina doctor --fix`
    (_run_doctor_fix) can turn the third -- a list of (key, required)
    pairs, one per failed check that named a key -- into real install
    commands via _PKG_MANAGER_PACKAGES, without re-deriving what's
    missing by re-parsing the printed text either."""
    lines = []
    all_ok = True
    missing = []

    def check(ok, required, label, hint=None, key=None):
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
        if not ok and key:
            missing.append((key, required))

    lines.append("Festina compiler dependencies")
    lines.append("==============================")

    cc_name, cc_path = _which_any("clang", "gcc", "cc")
    check(cc_name is not None, True,
          f"C compiler ({cc_name} at {cc_path})" if cc_name else "C compiler (clang, gcc, or cc)",
          "install one, e.g. `apt install clang` on Debian/Ubuntu, or `brew install llvm` on macOS -- see setup.md",
          key="cc")

    if sys.platform == "win32" and os.environ.get("MSYSTEM") == "MSYS":
        # windows.md Phase 0 item 4: `MSYSTEM=MSYS` is the plain
        # POSIX-emulation shell itself, not one of MSYS2's MinGW-w64
        # subsystems -- clang/gcc there (if present at all) produce
        # binaries linked against MSYS2's own runtime DLL, not the
        # ordinary Windows PE executables windows.md's toolchain
        # decision is about. Only fires when MSYSTEM is actually set to
        # the wrong value; unset (not running inside an MSYS2 shell at
        # all) is a different situation doctor has nothing extra to say
        # about.
        lines.append("  [   wrong shell   ] MSYS2 environment is MSYS, not UCRT64/MINGW64/CLANG64")
        lines.append(f"  {'':19} -> run from a UCRT64 shell instead -- look for \"UCRT64\" in your "
                      f"terminal's title/prompt, or launch \"MSYS2 UCRT64\" from the Start menu")

    pkgconf_path = shutil.which("pkg-config")
    check(pkgconf_path is not None, True,
          f"pkg-config (at {pkgconf_path})" if pkgconf_path else "pkg-config",
          _INSTALL_HINTS["pkg-config"], key="pkg-config")

    check(_pkg_config_has("sqlite3"), True,
          "sqlite3 dev headers (required -- every Festina program has SQLite built in, claude.md #10/#28-31)",
          _PKG_INSTALL_HINTS["sqlite3"], key="sqlite3")

    # windows.md Phase 0: also required, like sqlite3 just above -- not
    # an optional feature tier at all, since festina_runtime.c's regex
    # support is unconditional core (see _core_pkgs). Empty on every
    # other platform, where <regex.h> is already part of libc, so the
    # loop is a no-op there.
    for pkg in _core_pkgs():
        check(_pkg_config_has(pkg), True,
              "POSIX regex (required on Windows -- <regex.h> isn't part of MinGW's "
              "libc, claude.md #67/#68's regex()/.test()/.match()/.replace())",
              _PKG_INSTALL_HINTS["gnurx"], key="gnurx")

    # claude.md #123/#128: platform-aware, like audio just below --
    # darwin's and windows' graphics runtimes both carry zero X11 code
    # (guarded `#if !defined(__APPLE__) && !defined(_WIN32)`), so both
    # need Cairo's plain core package, not the xlib backend, and need
    # no X11/XQuartz dev headers at all.
    if sys.platform in ("darwin", "win32"):
        check(_pkg_config_has("cairo"), False,
              "cairo dev headers (optional -- only used by graphics: drawRect, on mouseDown, img, ...)",
              _PKG_INSTALL_HINTS["cairo"], key="cairo")
    else:
        check(_pkg_config_has("cairo-xlib"), False,
              "cairo-xlib dev headers (optional -- only used by graphics: drawRect, on mouseDown, img, ...)",
              _PKG_INSTALL_HINTS["cairo-xlib"], key="cairo-xlib")
    # claude.md #101: JPEG/MP3 decoding. Grouped with their own feature
    # rather than listed as separate tiers -- a program that uses
    # graphics needs libjpeg whether or not it happens to load a .jpg,
    # since the whole translation unit is compiled either way. windows.md
    # Phase 2 (claude.md #128): this now runs on win32 too, since there
    # IS a graphics translation unit there now that needs it, same as
    # every other platform -- it used to be skipped here specifically
    # because there was not yet anything on win32 to need it.
    check(_pkg_config_has("libjpeg"), False,
          "libjpeg dev headers (optional -- only used by graphics: JPEG images)",
          _PKG_INSTALL_HINTS["libjpeg"], key="libjpeg")
    if sys.platform == "darwin":
        # claude.md #123: windowed use (render(), any event handler)
        # additionally needs the Cocoa backend's real-hardware
        # verification pass -- see festina/cli.py's own gate.
        lines.append("  [   not yet       ] windowed graphics (render(), on mouseDown/.../close) -- "
                     "the Cocoa backend is built but awaits real-hardware verification, "
                     "macos.md Phase 2 (set FESTINA_ENABLE_MACOS_GRAPHICS=1 to try it); "
                     "offscreen drawing + saveCanvas() work today")
    elif sys.platform == "win32":
        # windows.md Phase 2 (claude.md #128): the Win32 counterpart --
        # built and CI-compiled, same shape as darwin's line above, no
        # longer the "not yet built at all" honesty this used to be.
        lines.append("  [   not yet       ] windowed graphics (render(), on mouseDown/.../close) -- "
                     "the Win32 backend is built but awaits real-hardware verification, "
                     "windows.md Phase 2 (set FESTINA_ENABLE_WINDOWS_GRAPHICS=1 to try it); "
                     "offscreen drawing + saveCanvas() work today")

    # macos.md Phase 0: the audio lines are platform-aware -- on macOS
    # there is no ALSA to install, and telling a Mac user to go get it
    # would be worse than saying the true thing: the AudioQueue backend
    # is built but awaits real-hardware verification (macos.md Phase 1).
    # windows.md Phase 1: the waveOut backend is now the same shape --
    # built and CI-compiled, awaiting its own real-hardware verification
    # pass, same as graphics now is too (windows.md Phase 2, claude.md
    # #128 -- both windows lines follow the identical built-but-gated
    # pattern now, nothing left in the "not yet built at all" shape).
    if sys.platform == "darwin":
        lines.append("  [   not yet       ] audio (aud/.play()) -- the AudioQueue "
                     "backend is built but awaits real-hardware verification, "
                     "macos.md Phase 1 (set FESTINA_ENABLE_MACOS_AUDIO=1 to try it)")
    elif sys.platform == "win32":
        lines.append("  [   not yet       ] audio (aud/.play()) -- the waveOut "
                     "backend is built but awaits real-hardware verification, "
                     "windows.md Phase 1 (set FESTINA_ENABLE_WINDOWS_AUDIO=1 to try it)")
    else:
        check(_pkg_config_has("libmpg123"), False,
              "libmpg123 dev headers (optional -- only used by audio: MP3 clips)",
              _PKG_INSTALL_HINTS["libmpg123"], key="libmpg123")
        check(_pkg_config_has("alsa"), False,
              "alsa dev headers (optional -- only used by audio: loadAudio(), .play(), ...)",
              _PKG_INSTALL_HINTS["alsa"], key="alsa")

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
              "needs -- install `llvm` (e.g. `apt install llvm` on Debian/Ubuntu) or clang itself",
              key="llvm")

    # claude.md #148: WASM export is its own fully optional feature tier,
    # same shape as graphics/audio just above -- a compiler that can't
    # cross-compile to wasm32-wasi is still a fully working compiler for
    # every native build, so this is never required. Checked with a real
    # clang invocation (_wasm_toolchain_ok), not a guessed install path;
    # `cc` here is deliberately clang specifically (not cc_name from
    # above), since _compile_via_wasm itself requires clang regardless
    # of what --cc the user's native builds are configured to use.
    clang_path = shutil.which("clang")
    if clang_path is None:
        check(False, False,
              "WASM export (optional -- `festina compile --target=wasm32-wasi`, see wasm.md)",
              "needs clang specifically (not gcc/cc) -- " + _INSTALL_HINTS["clang"], key="wasm")
    else:
        check(_wasm_toolchain_ok(clang_path), False,
              "WASM export (optional -- `festina compile --target=wasm32-wasi`, see wasm.md)",
              "clang was found but can't target wasm32-wasi -- install wasi-libc and "
              "clang's wasm32 runtime, e.g. `apt install wasi-libc libclang-rt-18-dev-wasm32` "
              "on Debian/Ubuntu (package name may vary by clang version) -- see wasm.md",
              key="wasm")

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
    return lines, all_ok, missing


def _run_doctor():
    lines, all_ok, _missing = _doctor_report()
    print("\n".join(lines))
    print()
    if all_ok:
        print("All required dependencies are installed. The optional ones (graphics/audio) "
              "only matter for a program that actually uses drawRect/on mouseDown/img/loadAudio/etc.")
        return 0
    print("One or more REQUIRED dependencies are missing -- see the MISSING lines above.")
    print("Run `festina doctor --fix` to try installing them automatically.")
    return 1


def _detect_package_manager():
    """Which of the three package managers `doctor --fix` knows how to
    drive, based on what setup.md itself documents per platform --
    apt on Linux, Homebrew on macOS, MSYS2's pacman on Windows. None of
    the others (dnf, Arch's pacman, zypper, ...) are covered: guessing
    a plausible-looking command for a manager this project has never
    actually run against risks confidently telling someone to run
    something wrong, which is worse than not offering to fix it at all.
    Returns None if no supported manager is found."""
    if sys.platform == "win32":
        return "msys2" if shutil.which("pacman") else None
    if sys.platform == "darwin":
        return "brew" if shutil.which("brew") else None
    if shutil.which("apt") or shutil.which("apt-get"):
        return "apt"
    return None


def _doctor_fix_install_command(manager, packages):
    """The actual command to run for this manager, given a deduplicated
    package list. Prepends `sudo` for apt only when not already root
    (hasattr guard: os.geteuid doesn't exist on Windows) -- brew
    actively refuses to run as root, and MSYS2's pacman needs no
    elevation at all since it manages its own user-writable prefix, not
    the host Windows installation. `-y`/`--noconfirm` on the package
    manager itself is safe to pass unconditionally here: by the time
    this is called, _run_doctor_fix has already gotten the person's own
    confirmation (or --yes) once, so a second manager-level prompt would
    be redundant, not an extra safety check."""
    if manager == "apt":
        apt = "apt" if shutil.which("apt") else "apt-get"
        cmd = [apt, "install", "-y", *packages]
        if hasattr(os, "geteuid") and os.geteuid() != 0:
            cmd = ["sudo", *cmd]
        return cmd
    if manager == "brew":
        return ["brew", "install", *packages]
    if manager == "msys2":
        return ["pacman", "-S", "--noconfirm", *packages]
    return None


def _confirm(assume_yes, prompt="Proceed? [y/N] "):
    """Shared confirmation gate for both of `festina doctor --fix`'s
    kinds of system change (installing packages, editing PATH) --
    refuses outright when running non-interactively without --yes,
    rather than either hanging on a read that will never come or
    silently proceeding without real consent. `input()` itself has no
    such guard built in, so this enforces it explicitly, the same
    two-sided caution git/npm-style installers apply."""
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        print("Not running interactively and --yes wasn't passed -- re-run with "
              "`festina doctor --fix --yes` to proceed without confirming.")
        return False
    answer = input(prompt).strip().lower()
    if answer not in ("y", "yes"):
        print("Not proceeding.")
        return False
    return True


def _fix_missing_dependencies(missing, all_ok, assume_yes):
    """The dependency-installing half of `doctor --fix`: builds one
    deduplicated install command across every missing key for the
    detected package manager, confirms, runs it, and re-checks.
    Returns 0 if every REQUIRED dependency is now present (or already
    was -- `missing` may hold only optional ones), else a nonzero exit
    code -- the install command's own exact returncode when that's
    specifically what failed, 1 for every other kind of failure (no
    supported manager, nothing installable, declined, still missing
    after a successful-exit install). `all_ok` is _doctor_report's own
    already-computed answer for "nothing required missing to begin
    with," passed in rather than re-derived so the early-exit paths
    below don't need their own copy of that logic."""
    if not missing:
        print("All dependencies are already installed.")
        return 0

    manager = _detect_package_manager()
    if manager is None:
        print("doctor --fix only knows how to drive apt (Debian/Ubuntu), Homebrew "
              "(macOS), and MSYS2's pacman (Windows) -- none of those were found "
              "on PATH here. Install the missing dependencies by hand using the "
              "hints above.")
        return 0 if all_ok else 1

    missing_keys = {key for key, _required in missing}
    if manager == "brew" and "cc" in missing_keys:
        print("Note: the actual macOS fix for a missing C compiler is Xcode "
              "Command Line Tools (`xcode-select --install`) -- that pops a GUI "
              "installer doctor --fix can't drive non-interactively, so run it "
              "yourself if clang is still missing after this.")

    packages = []
    seen = set()
    unfixable_required = []
    for key, required in missing:
        pkgs = _PKG_MANAGER_PACKAGES.get(key, {}).get(manager, [])
        if not pkgs:
            if required:
                unfixable_required.append(key)
            continue
        for pkg in pkgs:
            if pkg not in seen:
                seen.add(pkg)
                packages.append(pkg)

    if not packages:
        print(f"Nothing doctor --fix knows how to install for {manager} here -- "
              "see the hints above and install by hand.")
        return 0 if all_ok else 1

    cmd = _doctor_fix_install_command(manager, packages)
    print(f"About to run: {' '.join(cmd)}")
    if unfixable_required:
        print(f"(this still won't cover: {', '.join(unfixable_required)} -- see the hints above for those)")

    if not _confirm(assume_yes):
        return 1

    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"\n'{' '.join(cmd)}' exited with status {result.returncode}.")
        return result.returncode

    print()
    print("Re-checking...")
    print()
    lines2, all_ok2, _missing2 = _doctor_report()
    print("\n".join(lines2))
    print()
    if all_ok2:
        print("All required dependencies are now installed.")
        return 0
    print("Some required dependencies are still missing -- see above.")
    return 1


# `festina doctor --fix` (part two): the same treatment for `festina`
# itself not being resolvable on PATH -- _doctor_report has always
# diagnosed this and printed the fix, this is what actually DOES it.
# A plain-data plan (_festina_path_fix_plan) separate from the code
# that executes it (_apply_festina_path_fix), the same split
# _doctor_fix_install_command/_fix_missing_dependencies already use
# for package installs, and for the identical reason
# _default_output_name takes an injectable platform_name: every branch
# here is a pure function of state the caller can substitute, so each
# platform's plan is unit-testable from any one OS, not just whichever
# one the suite happens to run on.
def _festina_path_fix_plan(platform_name=None, festina_path=_UNSET, meipass=_UNSET,
                            shell_env=_UNSET, bin_dir=None):
    """What doctor --fix could do about `festina` not resolving on
    PATH. Returns None if it already resolves (nothing to fix) or a
    dict describing one of four plans:

    - "symlink": running the packaged binary directly (PyInstaller
      --onefile, sys._MEIPASS set) -- symlink it onto PATH at
      /usr/local/bin/festina, mirroring the hint _doctor_report
      already prints for this exact case.
    - "shell_rc": running from a checkout, on a POSIX shell doctor
      --fix knows how to edit (bash or zsh, the two setup.md's own
      "add that line to ~/.bashrc / ~/.zshrc" hint names) -- append an
      export line to that shell's own startup file.
    - "windows_path": running from a checkout on win32 -- `setx` the
      user's PATH environment variable (best-effort: this project has
      no Windows machine to confirm setx's own well-known PATH-length
      truncation risk isn't hit here, the same "no hardware to test
      against" honesty windows.md/macos.md already apply elsewhere).
    - "unsupported_shell": running from a checkout on a POSIX shell
      doctor --fix does NOT know how to edit (fish, csh, an unset
      $SHELL, ...) -- nothing to automate, same manual instructions
      _doctor_report already prints."""
    platform_name = platform_name or sys.platform
    if festina_path is _UNSET:
        festina_path = shutil.which("festina")
    if festina_path:
        return None

    if meipass is _UNSET:
        meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return {"kind": "symlink", "source": sys.executable, "target": "/usr/local/bin/festina"}

    if bin_dir is None:
        bin_dir = os.path.join(_data_root(), "bin")

    if platform_name == "win32":
        return {"kind": "windows_path", "bin_dir": bin_dir}

    if shell_env is _UNSET:
        shell_env = os.environ.get("SHELL", "")
    shell = os.path.basename(shell_env)
    rc_by_shell = {"bash": "~/.bashrc", "zsh": "~/.zshrc"}
    if shell not in rc_by_shell:
        return {"kind": "unsupported_shell", "bin_dir": bin_dir, "shell": shell or "unknown"}
    rc_file = os.path.expanduser(rc_by_shell[shell])
    return {"kind": "shell_rc", "rc_file": rc_file, "bin_dir": bin_dir,
            "line": f'export PATH="$PATH:{bin_dir}"'}


def _apply_festina_path_fix(plan, assume_yes):
    """Executes a plan from _festina_path_fix_plan. Returns True if
    `festina` should resolve from a NEW shell/session afterward (never
    the current process -- nothing can retroactively change a PATH a
    process already inherited at startup), False if skipped, declined,
    or unsupported."""
    kind = plan["kind"]

    if kind == "unsupported_shell":
        print(f"'festina' is not on PATH, and doctor --fix doesn't know how to edit "
              f"a '{plan['shell']}' startup file automatically -- add this line to "
              f"your shell's own startup file yourself:")
        print(f'  export PATH="$PATH:{plan["bin_dir"]}"')
        return False

    if kind == "symlink":
        source, target = plan["source"], plan["target"]
        # Refuses to clobber something already at that path that ISN'T
        # already this exact symlink -- claude.md #59's own "fail
        # loudly" preference applies here just as much as to guessing a
        # wrong install command: overwriting an unrelated program a
        # person put at /usr/local/bin/festina themselves would be a
        # much worse surprise than just not automating this one case.
        if os.path.exists(target) and not (
                os.path.islink(target) and os.path.realpath(target) == os.path.realpath(source)):
            print(f"Something already exists at {target} that isn't already a symlink "
                  f"to this binary -- not overwriting it. Add {source} to PATH by hand instead.")
            return False
        print(f"About to run: ln -sf {source} {target}")
        if not _confirm(assume_yes):
            return False
        cmd = ["ln", "-sf", source, target]
        if not os.access(os.path.dirname(target), os.W_OK):
            cmd = ["sudo", *cmd]
        result = subprocess.run(cmd)
        if result.returncode != 0:
            print(f"'{' '.join(cmd)}' exited with status {result.returncode}.")
            return False
        print(f"'festina' now resolves to {target}.")
        return True

    if kind == "shell_rc":
        rc_file, line, bin_dir = plan["rc_file"], plan["line"], plan["bin_dir"]
        try:
            with open(rc_file, encoding="utf-8") as f:
                already_there = bin_dir in f.read()
        except FileNotFoundError:
            already_there = False
        if already_there:
            print(f"{rc_file} already references this checkout's bin/ directory -- "
                  f"nothing to add. Restart your shell (or `source {rc_file}`) if it "
                  f"still isn't picking it up.")
            return True
        print(f"About to append to {rc_file}:")
        print(f"  {line}")
        if not _confirm(assume_yes):
            return False
        with open(rc_file, "a", encoding="utf-8") as f:
            f.write(f"\n# Added by `festina doctor --fix`\n{line}\n")
        print(f"Added. Restart your shell (or run `source {rc_file}`) to pick it up.")
        return True

    if kind == "windows_path":
        bin_dir = plan["bin_dir"]
        print(f'About to run: setx PATH "%PATH%;{bin_dir}"')
        if not _confirm(assume_yes):
            return False
        result = subprocess.run(["setx", "PATH", f"%PATH%;{bin_dir}"])
        if result.returncode != 0:
            print(f"'setx' exited with status {result.returncode}.")
            return False
        print("Added -- this only affects NEW terminal sessions (Windows environment "
              "variables aren't retroactive), and setx has a known ~1024-character PATH "
              "truncation limit, so double-check with `echo %PATH%` in a fresh terminal "
              "if `festina` still doesn't resolve there.")
        return True

    return False


def _run_doctor_fix(assume_yes=False):
    """`festina doctor --fix`: run the same report doctor always does,
    then try to actually FIX what it found instead of just printing
    hints for a human to act on by hand -- both installing whatever
    dependencies are missing (_fix_missing_dependencies) and, if
    `festina` itself isn't resolving on PATH, doing whatever this
    platform's own fix for that is too (_festina_path_fix_plan/
    _apply_festina_path_fix). Confirms before making either kind of
    change (skippable with --yes); the exit code reflects the
    dependency side only, exactly like plain `festina doctor` itself
    -- not being on PATH has never been a `required`-flagged doctor
    check (see _doctor_report), so fixing or not fixing it here
    shouldn't change what this command's own success/failure means."""
    lines, all_ok, missing = _doctor_report()
    print("\n".join(lines))
    print()

    path_plan = _festina_path_fix_plan()
    if not missing and path_plan is None:
        print("Everything required is already installed and 'festina' is already "
              "on PATH -- nothing to fix.")
        return 0

    deps_code = _fix_missing_dependencies(missing, all_ok, assume_yes)

    if path_plan is not None:
        print()
        _apply_festina_path_fix(path_plan, assume_yes)

    return deps_code


def _build_arg_parser():
    default_cc = shutil.which("clang") or shutil.which("gcc") or shutil.which("cc") or "clang"
    cc_help = "C compiler/linker to invoke (default: clang, gcc, or cc, whichever is found first)"

    ap = argparse.ArgumentParser(prog="festina", description="Compile and run Festina programs.")
    # dest="command", not required=True: an unrecognized/missing
    # subcommand falls through to main()'s own help-and-exit-1 handling
    # below, rather than argparse's own less friendly "the following
    # arguments are required" message.
    sub = ap.add_subparsers(dest="command", metavar="command")

    # claude.md #148: "native" builds and links a regular executable for
    # the host platform, same as always; "wasm32-wasi" cross-compiles to
    # a standalone .wasm binary instead (see wasm.md) -- both compile
    # and run accept it, since `run` is really "compile, then execute"
    # and a wasm32-wasi binary needs a WASI host (Node) to execute it
    # rather than the OS running it directly.
    target_help = "compilation target: a native executable, or a wasm32-wasi .wasm binary (default: native)"

    compile_p = sub.add_parser("compile", help="compile a Festina program to a native executable")
    compile_p.add_argument("input", help="entry .f file")
    compile_p.add_argument("-o", "--output", help="output executable path (default: input filename without .f)")
    compile_p.add_argument("--emit-llvm", action="store_true", help="print LLVM IR to stdout instead of linking")
    compile_p.add_argument("--cc", default=default_cc, help=cc_help)
    compile_p.add_argument("--target", choices=["native", "wasm32-wasi"], default="native", help=target_help)

    run_p = sub.add_parser("run", help="compile a Festina program and immediately run it")
    run_p.add_argument("input", help="entry .f file")
    run_p.add_argument("--cc", default=default_cc, help=cc_help)
    run_p.add_argument("--target", choices=["native", "wasm32-wasi"], default="native", help=target_help)

    doctor_p = sub.add_parser("doctor", help="check whether the compiler's own dependencies are installed")
    doctor_p.add_argument("--fix", action="store_true",
                           help="try to auto-install missing dependencies via the detected "
                                "package manager (apt/Homebrew/MSYS2 pacman)")
    doctor_p.add_argument("--yes", "-y", action="store_true",
                           help="with --fix, don't prompt for confirmation before installing")
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
        if args.fix:
            return _run_doctor_fix(assume_yes=args.yes)
        return _run_doctor()

    if args.command == "run":
        try:
            return run_program(args.input, cc=args.cc, target=args.target)
        except CompileError as e:
            print(str(e), file=sys.stderr)
            return 1
        except OSError as e:
            print(f"festina: {e}", file=sys.stderr)
            return 1

    # args.command == "compile"
    try:
        result = compile_file(args.input, args.output, emit_llvm=args.emit_llvm, cc=args.cc, target=args.target)
    except CompileError as e:
        print(str(e), file=sys.stderr)
        return 1
    except OSError as e:
        print(f"festina: {e}", file=sys.stderr)
        return 1

    if args.emit_llvm:
        print(result)
    elif args.target == "wasm32-wasi":
        # claude.md #148: the native success message's sqlite-static-vs-
        # dynamic note doesn't apply here -- the wasm build always
        # statically compiles the vendored amalgamation (runtime/wasm/
        # README.md), there's no dynamic-linking story for wasm32-wasi
        # to fall back to -- and the libLLVM fast path is never used for
        # wasm either (_compile_via_wasm always shells out to clang, the
        # same way the IR-frontend fallback does for native), so noting
        # its absence would be misleading rather than informative.
        print(f"festina: wrote {result} (run with a WASI host, e.g. `festina run --target=wasm32-wasi`, "
              f"or `node runtime/wasm/run_wasi.mjs {result} <preopen-dir>`)")
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
