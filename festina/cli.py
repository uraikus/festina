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


def _default_output_name(entry_path, platform_name=None):
    """windows.md Phase 0: on Windows the default output gains `.exe` --
    both because the shell only executes files with an executable
    extension, and because MinGW's linker appends `.exe` itself when
    the requested name has no suffix, so asking for `program` and then
    running `program` would miss the `program.exe` actually written.
    `platform_name` is injectable purely so the win32/darwin branches
    are unit-testable from any platform (tests/test_platform.py)."""
    platform_name = platform_name or sys.platform
    base = os.path.basename(entry_path)
    if base.endswith(".f"):
        base = base[:-2]
    base = base or "a.out"
    if platform_name == "win32" and not base.lower().endswith(".exe"):
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
    # claude.md #123: darwin's own package -- plain Cairo, no X11 half
    # (the windowing backend there is native Cocoa, not cairo-xlib).
    "cairo": "install Cairo's development package, e.g. `brew install cairo` "
             "on macOS -- needed for claude.md #37/#39's img/graphics functions",
    "libjpeg": "install libjpeg's development package, e.g. "
                "`apt install libjpeg-dev` (Debian/Ubuntu) or "
                "`brew install jpeg-turbo` (macOS)",
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
    return pkgs, flags


def _feature_extra_object(cc, name, platform_name=None):
    """claude.md #123: the one companion object a feature needs beyond
    its own `_RUNTIME_FEATURES[name]["source"]` -- today only graphics
    on darwin, where Cocoa cannot be compiled as part of
    festina_runtime_graphics.c's plain C translation unit at all and so
    lives in its own Objective-C file (_RUNTIME_WINDOW_MAC_M). Returns
    None everywhere else. Reuses _ensure_runtime_object's own cache-by-
    mtime machinery -- clang infers Objective-C from the .m extension
    with no extra flag needed, so compiling it is exactly the same
    shape as any other runtime object file."""
    platform_name = platform_name or sys.platform
    if name == "graphics" and platform_name == "darwin":
        # claude.md #126: this file #includes <cairo.h> (CGImage/cairo
        # interop in drawRect:) same as festina_runtime_graphics.c
        # does, so it needs cairo's own pkg-config cflags too -- an
        # empty list here meant it silently never got them, and this
        # translation unit is the ONE place real macOS CI could ever
        # catch that, since nothing else on any platform compiles it.
        return _ensure_runtime_object(cc, "window_mac", _RUNTIME_WINDOW_MAC_M, ["cairo"])
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
    Cairo/X11 *and* libjpeg, audio is ALSA *and* libmpg123)."""
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


def _check_feature_supported(feature, platform_name=None):
    """macos.md/windows.md Phase 0: a feature whose backend does not
    exist yet on this platform fails with a message that says exactly
    that -- and where the work is planned -- instead of a pkg-config
    error telling a Mac or Windows user to install a library that does
    not exist on their OS. `platform_name` is injectable so every
    branch is unit-testable from any platform (tests/test_platform.py).

    Two different shades of "not supported" live here, both raising the
    same category so the conftest skip picks up either uniformly: the
    darwin branches gate a backend that EXISTS (built, CI-compiled)
    but awaits real-hardware verification, overridable via an env var
    for exactly that verification; the win32 branches gate a backend
    that does not exist in the runtime AT ALL yet (windows.md Phases
    1-2 are still open), so they raise unconditionally -- there is
    nothing to unlock.

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
        # windows.md Phase 1: unlike the darwin case above, there is no
        # waveOut backend in the runtime yet at all -- no env var to
        # unlock, because there is nothing built yet to unlock. This
        # becomes the same real-hardware-verification gate the darwin
        # branch above uses once Phase 1 lands.
        raise CompileError(
            "audio is not yet implemented on Windows -- planned as "
            "windows.md Phase 1 (waveOut). Everything except aud/play() "
            "works today.",
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
        # windows.md Phase 2: same "nothing built yet" honesty as the
        # audio branch above -- no Win32 windowing backend exists in
        # the runtime yet, so this fires unconditionally rather than
        # gating a real backend behind an env var. claude.md #126 round
        # six: unlike darwin, this now covers OFFSCREEN drawing too --
        # _runtime_objects_and_link_libs's own offscreen exemption is
        # scoped away from win32 (see its docstring), precisely because
        # there is no window_win32 companion object the way window_mac.m
        # exists for darwin, so even drawRect()+saveCanvas() alone fails
        # to link on win32 today, not just windowed use.
        raise CompileError(
            "graphics (drawRect, on mouseDown, img, saveCanvas, ...) is "
            "not yet implemented on Windows -- planned as windows.md "
            "Phase 2 (Win32 + the shared Cairo blit). This includes "
            "offscreen-only drawing too, unlike on macOS: there is no "
            "Win32 windowing backend at all yet for the shared graphics "
            "code to link against.",
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
    reaches _check_feature_supported's platform gate on platforms where
    offscreen graphics actually links -- see that function's own
    docstring for why offscreen drawing must never hit it there. The
    graphics object (and its companion Cocoa object on darwin, which
    the offscreen path also needs linked -- see _feature_extra_object's
    own comment) is still linked whenever `uses_graphics` (broad) is
    true, gate or no gate.

    claude.md #126 round four (found by real Windows CI): that
    exemption is itself platform-scoped, not universal. Unlike darwin,
    Windows has no window backend at all yet -- no window_win32
    companion object exists the way window_mac.m does -- so even an
    OFFSCREEN-only program fails at the *linker* stage with
    `_festina_window_open` and friends undefined, since
    festina_runtime_graphics.c references those symbols unconditionally
    regardless of whether a given program ever calls render() or an
    event handler. So on win32 the graphics gate must fire even when
    wants_window is False, unlike on darwin (offscreen genuinely works
    there) or Linux (ungated everywhere, checked directly)."""
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

    offscreen_graphics_is_gate_exempt = sys.platform != "win32"
    for name, wants in (("graphics", uses_graphics), ("audio", uses_audio)):
        if not wants:
            continue
        skip_gate = name == "graphics" and not wants_window and offscreen_graphics_is_gate_exempt
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
    runtime_objects, link_libs = _runtime_objects_and_link_libs(
        cc, needs_graphics, gen.uses_audio, wants_window=gen.uses_graphics)

    if llvm_backend.available():
        _compile_via_libllvm(ir, entry_path, output_path, cc, runtime_objects, link_libs)
    else:
        _compile_via_clang_ir_frontend(ir, entry_path, output_path, cc, needs_graphics, gen.uses_audio, link_libs)
    _rename_if_linker_appended_exe(output_path)
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
        # windows.md Phase 0: through _default_output_name so the temp
        # binary is `program.exe` on Windows -- MinGW's linker appends
        # .exe itself when the name has no suffix, and running the name
        # we ASKED for rather than the file it WROTE would fail.
        out_path = os.path.join(d, _default_output_name("program.f"))
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
          _INSTALL_HINTS["pkg-config"])

    check(_pkg_config_has("sqlite3"), True,
          "sqlite3 dev headers (required -- every Festina program has SQLite built in, claude.md #10/#28-31)",
          _PKG_INSTALL_HINTS["sqlite3"])

    # windows.md Phase 0: also required, like sqlite3 just above -- not
    # an optional feature tier at all, since festina_runtime.c's regex
    # support is unconditional core (see _core_pkgs). Empty on every
    # other platform, where <regex.h> is already part of libc, so the
    # loop is a no-op there.
    for pkg in _core_pkgs():
        check(_pkg_config_has(pkg), True,
              "POSIX regex (required on Windows -- <regex.h> isn't part of MinGW's "
              "libc, claude.md #67/#68's regex()/.test()/.match()/.replace())",
              _PKG_INSTALL_HINTS["gnurx"])

    # claude.md #123: platform-aware, like audio just below -- darwin's
    # graphics runtime carries zero X11 code (guarded `#ifndef
    # __APPLE__`), so it needs Cairo's plain core package, not the xlib
    # backend, and needs no X11/XQuartz dev headers at all any more.
    # windows.md Phase 2 (Win32 windowing) doesn't exist in the runtime
    # yet at all, unlike macOS's Cocoa backend -- so unlike the darwin
    # "not yet" lines below, there is no real dependency to probe for
    # here yet either; the true statement is simply that the feature
    # isn't built.
    if sys.platform == "darwin":
        check(_pkg_config_has("cairo"), False,
              "cairo dev headers (optional -- only used by graphics: drawRect, on mouseDown, img, ...)",
              _PKG_INSTALL_HINTS["cairo"])
    elif sys.platform == "win32":
        lines.append("  [   not yet       ] graphics (drawRect, on mouseDown, img, ...) -- "
                     "no Windows backend yet, planned as windows.md Phase 2")
    else:
        check(_pkg_config_has("cairo-xlib"), False,
              "cairo-xlib dev headers (optional -- only used by graphics: drawRect, on mouseDown, img, ...)",
              _PKG_INSTALL_HINTS["cairo-xlib"])
    if sys.platform != "win32":
        # claude.md #101: JPEG/MP3 decoding. Grouped with their own
        # feature rather than listed as separate tiers -- a program
        # that uses graphics needs libjpeg whether or not it happens
        # to load a .jpg, since the whole translation unit is compiled
        # either way. Skipped on win32 above with the rest of graphics,
        # since there's no graphics translation unit there to need it.
        check(_pkg_config_has("libjpeg"), False,
              "libjpeg dev headers (optional -- only used by graphics: JPEG images)",
              _PKG_INSTALL_HINTS["libjpeg"])
    if sys.platform == "darwin":
        # claude.md #123: windowed use (render(), any event handler)
        # additionally needs the Cocoa backend's real-hardware
        # verification pass -- see festina/cli.py's own gate.
        lines.append("  [   not yet       ] windowed graphics (render(), on mouseDown/.../close) -- "
                     "the Cocoa backend is built but awaits real-hardware verification, "
                     "macos.md Phase 2 (set FESTINA_ENABLE_MACOS_GRAPHICS=1 to try it); "
                     "offscreen drawing + saveCanvas() work today")

    # macos.md Phase 0: the audio lines are platform-aware -- on macOS
    # there is no ALSA to install, and telling a Mac user to go get it
    # would be worse than saying the true thing: the AudioQueue backend
    # is built but awaits real-hardware verification (macos.md Phase 1).
    # windows.md Phase 1 (waveOut) doesn't exist in the runtime yet
    # either -- same "not yet" honesty as graphics above, not audio's
    # own real-hardware-verification gate, since there's no backend to
    # gate at all yet.
    if sys.platform == "darwin":
        lines.append("  [   not yet       ] audio (aud/.play()) -- the AudioQueue "
                     "backend is built but awaits real-hardware verification, "
                     "macos.md Phase 1 (set FESTINA_ENABLE_MACOS_AUDIO=1 to try it)")
    elif sys.platform == "win32":
        lines.append("  [   not yet       ] audio (aud/.play()) -- "
                     "no Windows backend yet, planned as windows.md Phase 1")
    else:
        check(_pkg_config_has("libmpg123"), False,
              "libmpg123 dev headers (optional -- only used by audio: MP3 clips)",
              _PKG_INSTALL_HINTS["libmpg123"])
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
              "only matter for a program that actually uses drawRect/on mouseDown/img/loadAudio/etc.")
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
