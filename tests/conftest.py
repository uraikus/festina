"""Shared fixtures for the claude.md-driven Festina spec test suite.

See tests/CONTRACT.md for why these tests target a `festina` package that
doesn't exist yet, and what its assumed API looks like.
"""
import importlib
import os
import random
import shutil
import subprocess
import sys
import time

import pytest

SPEC_UNIMPLEMENTED_REASON = (
    "festina.{mod} is not implemented yet -- claude.md describes the "
    "Festina language spec, but the `festina` package doesn't have this "
    "module (yet). See tests/CONTRACT.md for the assumed API this suite "
    "targets."
)


def import_spec_module(modname):
    """Import `festina.<modname>` or skip the test with a clear reason.

    Use this instead of a bare `pytest.importorskip` so every skip message
    points back at tests/CONTRACT.md.
    """
    full = f"festina.{modname}"
    try:
        return importlib.import_module(full)
    except ModuleNotFoundError as exc:
        # Only swallow "the module itself is missing" -- a real ImportError
        # inside an existing module (e.g. a typo) should still fail loudly.
        if exc.name is not None and (exc.name == full or full.startswith(exc.name + ".")):
            pytest.skip(SPEC_UNIMPLEMENTED_REASON.format(mod=modname))
        raise


@pytest.fixture
def lexer():
    return import_spec_module("lexer")


@pytest.fixture
def parser():
    return import_spec_module("parser")


@pytest.fixture
def errors():
    return import_spec_module("errors")


@pytest.fixture
def types_mod():
    return import_spec_module("types")


@pytest.fixture
def imports_mod():
    return import_spec_module("imports")


@pytest.fixture
def semantic():
    return import_spec_module("semantic")


@pytest.fixture
def sqlite_schema():
    return import_spec_module("sqlite_schema")


@pytest.fixture
def compiler_mod():
    return import_spec_module("compiler")


@pytest.fixture
def codegen():
    return import_spec_module("codegen")


@pytest.fixture
def cli_mod():
    return import_spec_module("cli")


@pytest.fixture
def llvm_backend():
    return import_spec_module("llvm_backend")


def compile_file_or_skip(cli_mod, *args, **kwargs):
    """macos.md Phase 0: compile, turning "this MACHINE lacks a
    feature's dev packages" and "this PLATFORM has no backend for the
    feature yet" (audio on macOS until Phase 1 is hardware-verified)
    into a pytest.skip instead of a failure. This single rule is what
    lets a new platform's CI job run the WHOLE suite and shed exactly
    the tiers it lacks, with no parallel test-selection list to drift.
    The Linux CI job sets FESTINA_STRICT_DEPS=1 to forbid these skips
    there, so a dependency quietly vanishing from the primary
    platform's CI image fails loudly instead of shrinking coverage.
    Shared by the compile_and_run fixture and every test that calls
    compile_file directly (test_examples, TestSlimBinaries's audio
    case)."""
    try:
        return cli_mod.compile_file(*args, **kwargs)
    except Exception as err:
        category = getattr(err, "category", None)
        skippable = category in ("missing dependency",
                                 "unsupported platform feature")
        if skippable and not os.environ.get("FESTINA_STRICT_DEPS"):
            pytest.skip(f"{category}: {err}")
        raise


def _require_c_compiler():
    """Shared by compile_and_run/compile_multi_and_run: skip with a
    clear, toolchain-specific reason if no usable C compiler is on
    PATH -- distinct from the SPEC_UNIMPLEMENTED_REASON skips above,
    since codegen.py itself is implemented either way; this is "this
    environment can't link native code," not "the feature doesn't
    exist." Prefers clang but accepts gcc too: as of "real compilation,
    minimal setup" stage 3, festina.llvm_backend compiles the LLVM IR
    itself (when available) rather than handing the .ll file to the C
    compiler, so cc's job is just compiling the runtime translation
    units and linking plain object files -- work gcc does exactly as
    well as clang. See festina/cli.py's module docstring."""
    cc = shutil.which("clang") or shutil.which("gcc") or shutil.which("cc")
    if not cc:
        pytest.skip("no C compiler (clang/gcc/cc) on PATH -- cannot "
                     "compile/link the Festina runtime and the generated code")
    return cc


@pytest.fixture
def compile_and_run(tmp_path, codegen, cli_mod):
    """Compile a Festina source string to a native executable and run it.

    macos.md Phase 0: a compile that fails because this MACHINE lacks a
    feature's dependencies (missing dev package) or this PLATFORM lacks
    the feature's backend entirely (audio on macOS until Phase 1) turns
    into a pytest.skip rather than a failure -- that is what lets the
    macOS CI job run the WHOLE suite and degrade to skips for exactly
    the tests a missing tier covers, instead of maintaining a parallel
    test-selection list that would drift. The Linux CI job sets
    FESTINA_STRICT_DEPS=1 to forbid these skips there, so a
    dependency quietly vanishing from the primary platform's CI image
    still fails loudly instead of shrinking coverage."""
    cc = _require_c_compiler()

    def _run(source, filename="main.f", args=None, env=None):
        src_path = tmp_path / filename
        # Explicit UTF-8 both ways -- festina/imports.py reads source
        # files as UTF-8 (festina/cli.py's own open() calls), and the
        # compiled program's stdout is UTF-8 too. Without it, Python's
        # locale-default encoding is used instead, which is NOT UTF-8
        # on Windows -- confirmed by real Windows CI (claude.md #126):
        # a non-ASCII literal got mis-encoded on write, and decoding
        # the program's real UTF-8 stdout under the wrong codec crashed
        # the test outright rather than merely rendering it wrong.
        src_path.write_text(source, encoding="utf-8")
        out_path = tmp_path / "program"
        compile_file_or_skip(cli_mod, str(src_path), str(out_path), cc=cc)
        run_env = dict(os.environ, **env) if env else None
        result = subprocess.run(
            [str(out_path), *(args or [])],
            cwd=tmp_path, capture_output=True, text=True, timeout=15,
            env=run_env, encoding="utf-8",
        )
        return result

    return _run


@pytest.fixture
def compile_and_run_wasm(tmp_path, codegen, cli_mod):
    """WASM counterpart to compile_and_run (claude.md #148, wasm.md):
    compiles to wasm32-wasi and executes the result through the same
    Node-based WASI host cli_mod.run_program itself uses
    (runtime/wasm/run_wasi.mjs), rather than reimplementing that here.

    Skips cleanly (not a failure) when either half of the toolchain
    this needs is missing -- a clang that can actually target
    wasm32-wasi (`_wasm_toolchain_ok`, the same real functional probe
    `festina doctor` uses, not just "clang exists"), or Node on PATH to
    run the compiled .wasm -- the same "opt-in, environment-dependent"
    tier as compile_and_run's own C-compiler skip and x_display's own
    Xvfb skip. Like compile_file_or_skip, FESTINA_STRICT_DEPS=1 turns
    that skip into a hard failure instead -- the Linux CI job sets it so
    this whole tier can't silently vanish there the way every other
    optional tier already can't (see .github/workflows/ci.yml, which
    installs wasi-libc/libclang-rt-*-dev-wasm32 and Node specifically so
    this fixture is never skipped on the primary platform).

    Unlike compile_and_run, there's no `args=` parameter: Festina
    programs have no way to read argv at all (no language builtin for
    it -- see api.md), and run_wasi.mjs's own WASI `args` is just
    `[wasmPath]` for that reason, so there is nothing here to pass
    through.
    """
    clang = shutil.which("clang")
    node = shutil.which("node")
    missing = None
    if not clang or not cli_mod._wasm_toolchain_ok(clang):
        missing = ("no working wasm32-wasi clang on PATH -- needs wasi-libc and "
                    "clang's wasm32 compiler-rt installed (see wasm.md)")
    elif not node:
        missing = "Node.js isn't on PATH -- needed to run a compiled .wasm via its built-in WASI support"
    if missing:
        if os.environ.get("FESTINA_STRICT_DEPS"):
            pytest.fail(missing)
        pytest.skip(missing)

    def _run(source, filename="main.f"):
        src_path = tmp_path / filename
        src_path.write_text(source, encoding="utf-8")
        out_path = tmp_path / "program.wasm"
        compile_file_or_skip(cli_mod, str(src_path), str(out_path), cc=clang, target="wasm32-wasi")
        result = subprocess.run(
            [node, "--no-warnings", cli_mod._WASM_RUN_SCRIPT, str(out_path), str(tmp_path)],
            cwd=tmp_path, capture_output=True, text=True, timeout=30, encoding="utf-8",
        )
        return result

    return _run


@pytest.fixture
def audio_null_env(tmp_path):
    """A HOME override whose .asoundrc redirects ALSA's "default" PCM
    device to ALSA's own built-in null plugin -- a real ALSA mechanism
    (not festina-specific, the same $HOME/.asoundrc lookup a real
    desktop's ALSA config would use), the audio equivalent of `DISPLAY`
    pointing at a throwaway Xvfb for graphics: lets claude.md #38's
    play()/stop()/isPlaying() actually open a "device" and stream real
    PCM data to it without needing real sound hardware or a running
    audio server, neither of which this dev environment has (verified:
    no /dev/snd node at all, `snd_pcm_open(..., "default", ...)` fails
    with "cannot find card '0'" otherwise). Unlike Xvfb, this needs no
    extra tool install -- the null plugin ships inside alsa-lib itself,
    which festina_runtime_audio.c links against whenever a program
    actually uses audio (see festina/cli.py's per-feature object file
    selection) -- so tests using this don't need their own opt-in skip
    tier the way the Xvfb-based graphics tests do; they only need the
    same C-compiler availability compile_and_run already requires.
    Pass the returned dict as compile_and_run's `env=` argument.
    """
    home = tmp_path / "alsa_home"
    home.mkdir()
    (home / ".asoundrc").write_text("pcm.!default {\n    type null\n}\n")
    return {"HOME": str(home)}


@pytest.fixture
def sprite_sheet_png(tmp_path):
    """A 128x64 PNG laid out as a 4x2 grid of 32x32 solid-colour tiles,
    written fresh into tmp_path and returned as a path.

    Generated rather than committed: claude.md #92's clip()/resize()
    tests need to assert real pixel colours at known tile coordinates,
    which means the fixture's exact layout is part of the test, and a
    checked-in binary would hide that. Encoded by hand (zlib + the four
    PNG chunks) so this needs no image library -- the compiler itself
    has none, and neither should its tests.
    """
    import struct
    import zlib

    width, height, tile = 128, 64, 32
    # index = (row * 4) + column, so tile 0 is red at (0,0) and tile 5
    # is cyan at (32,32) -- both asserted by name in the tests.
    colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0),
              (255, 0, 255), (0, 255, 255), (128, 0, 128), (0, 128, 128)]
    rows = []
    for y in range(height):
        row = bytearray([0])  # PNG filter type 0 for this scanline
        for x in range(width):
            r, g, b = colors[(y // tile) * 4 + (x // tile)]
            row += bytes([r, g, b, 255])
        rows.append(bytes(row))

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(b"".join(rows)))
           + chunk(b"IEND", b""))
    path = tmp_path / "sheet.png"
    path.write_bytes(png)
    return str(path)


@pytest.fixture
def write_source(tmp_path):
    """Write named Festina source files under a temp dir; return their dir."""

    def _write(files: dict):
        for relpath, content in files.items():
            p = tmp_path / relpath
            p.parent.mkdir(parents=True, exist_ok=True)
            # Explicit UTF-8 -- see compile_and_run's own write_text
            # call for why the locale default (not UTF-8 on Windows)
            # is the wrong choice here.
            p.write_text(content, encoding="utf-8")
        return tmp_path

    return _write


@pytest.fixture
def compile_multi_and_run(tmp_path, codegen, cli_mod, write_source):
    """Like compile_and_run, but for a multi-file program (claude.md #5,
    #6): takes {relpath: source} plus which file is the entry point,
    writes them all, compiles the entry (pulling in its own imports),
    and runs the result."""
    cc = _require_c_compiler()

    def _run(files: dict, entry="main.f", args=None):
        root = write_source(files)
        out_path = tmp_path / "program"
        cli_mod.compile_file(str(root / entry), str(out_path), cc=cc)
        result = subprocess.run(
            [str(out_path), *(args or [])],
            cwd=tmp_path, capture_output=True, text=True, timeout=15,
            encoding="utf-8",
        )
        return result

    return _run


@pytest.fixture
def x_display():
    """A working DISPLAY to render into -- prefers an already-set,
    real one; otherwise spins up a throwaway Xvfb (virtual framebuffer
    X server) instance and tears it down afterwards. Skips cleanly if
    neither is available: claude.md #37/#39's graphics functions need a
    real X server to open a window against, but nothing about
    developing or testing festina/ otherwise does -- same "opt-in,
    environment-dependent" tier as compile_and_run's C-compiler skip
    and test_packaging.py's pyinstaller skip.
    """
    existing = os.environ.get("DISPLAY")
    if existing:
        yield existing
        return

    xvfb = shutil.which("Xvfb")
    if not xvfb:
        pytest.skip("no DISPLAY set and Xvfb isn't installed -- needed to test "
                     "claude.md #37/#39's graphics functions against a real window")

    display_num = f":{random.randint(100, 9999)}"
    proc = subprocess.Popen(
        [xvfb, display_num, "-screen", "0", "1024x768x24"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    # Poll for the server actually accepting connections (via xdotool,
    # already required by anything using this fixture) rather than a
    # fixed sleep -- a flat 0.5s was reliable running this file alone
    # but flaky as part of the full suite (more system load, presumably
    # a slower Xvfb startup), so wait for a real readiness signal
    # instead of guessing a longer constant.
    deadline = time.time() + 10
    ready = False
    while time.time() < deadline:
        if proc.poll() is not None:
            pytest.fail(f"Xvfb on {display_num} exited immediately (see its exit code: {proc.returncode})")
        probe = subprocess.run(
            ["xdotool", "getdisplaygeometry"],
            env=dict(os.environ, DISPLAY=display_num),
            capture_output=True, text=True,
        )
        if probe.returncode == 0:
            ready = True
            break
        time.sleep(0.1)
    if not ready:
        proc.terminate()
        pytest.fail(f"Xvfb on {display_num} never became ready to accept connections")

    try:
        yield display_num
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture
def path_without(tmp_path, monkeypatch):
    """A PATH containing everything currently on PATH except the named
    tool(s), by symlinking every other resolvable tool into an empty
    dir and pointing PATH at just that dir. Shared between
    test_codegen.py's TestMissingDependencyErrors (a real compile with a
    tool hidden fails with a clear, actionable CompileError -- claude.md
    #59) and test_cli.py's TestDoctor (festina doctor reports the exact
    same tool as missing, with the exact same install hint, checked
    proactively rather than only on a real compile failure)."""
    def _make(*hidden_tools):
        bin_dir = tmp_path / "bin_without_tool"
        bin_dir.mkdir()
        needed = {"python3", "bash", "sh", "env", "dirname", "basename",
                  "pkg-config", "clang", "gcc", "cc", "ld", "as"}
        for name in needed - set(hidden_tools):
            found = shutil.which(name)
            if found:
                # claude.md #126 round nine, found by real Windows CI:
                # symlinking under the bare logical NAME ("pkg-config",
                # no extension) left every "still resolvable" tool
                # actually unresolvable by shutil.which on Windows --
                # its PATHEXT search only ever tries name+ext
                # candidates ("pkg-config.EXE", ...), never the bare
                # name itself, so a symlink literally named "pkg-config"
                # was invisible to it. This went unnoticed as long as
                # festina/cli.py's own _run_tool handed commands
                # straight to subprocess.run, which resolves executables
                # via Win32's own broader CreateProcess search (see
                # _run_tool's docstring) rather than shutil.which's
                # PATH-only one -- fixing _run_tool to gate through
                # shutil.which first (this same round) is what exposed
                # this pre-existing fixture bug for the first time.
                # os.path.basename(found) preserves whatever real
                # extension `found` actually has (".exe" on Windows,
                # none on POSIX), so the symlink's own name is exactly
                # what shutil.which's search will look for.
                (bin_dir / os.path.basename(found)).symlink_to(found)
        monkeypatch.setenv("PATH", str(bin_dir))
        return str(bin_dir)
    return _make


@pytest.fixture
def run_graphics_program(tmp_path, codegen, cli_mod, x_display):
    """Compile a Festina source string that opens the graphics canvas
    and run it (line-buffered, so log() output is visible without
    needing a clean exit -- see the note in TestGraphics about why a
    bare Xvfb instance, with no window manager, can't reliably close
    the window the same way a real desktop would). Returns
    (process, stdout_path) -- the caller drives it with xdotool and
    reads stdout_path, then must terminate the process itself (a
    graphics program blocks in its event loop until the window closes,
    so there's no "wait for it to exit" the way compile_and_run has).

    `display` defaults to the injected `x_display` (a bare Xvfb
    instance) but can be overridden per-call -- `x_display_with_wm`'s
    own regression test passes its WM-backed display explicitly, to run
    the exact same compile-and-launch path against a real window
    manager instead.
    """
    cc = _require_c_compiler()
    if not shutil.which("xdotool"):
        pytest.skip("xdotool isn't installed -- needed to simulate clicks/mouse "
                     "movement against the graphics canvas window")

    def _run(source, filename="main.f", display=None):
        src_path = tmp_path / filename
        src_path.write_text(source, encoding="utf-8")
        out_path = tmp_path / "program"
        cli_mod.compile_file(str(src_path), str(out_path), cc=cc)

        stdout_path = tmp_path / "stdout.log"
        env = dict(os.environ, DISPLAY=display or x_display)
        proc = subprocess.Popen(
            ["stdbuf", "-oL", str(out_path)],
            cwd=tmp_path, stdout=open(stdout_path, "w"), stderr=subprocess.STDOUT,
            env=env,
        )
        return proc, stdout_path

    return _run


@pytest.fixture
def x_display_with_wm(x_display):
    """Wraps x_display with a real window manager running against it --
    `openbox`, a real, actively-used, EWMH-compliant one, not a
    minimal/legacy stand-in -- so a graphics test can run against
    exactly the class of window manager this fixture exists for:
    `x_display`'s own bare Xvfb instance has no window manager at all,
    so it can never reproduce a WM-reparenting race no matter how many
    times a graphics program is run against it (confirmed directly:
    `festina_graphics_init`'s own best-effort XSetInputFocus call,
    harmless under a WM-less Xvfb, reproducibly crashed the whole
    program with a BadMatch under a real WM before the fix this
    fixture's own regression test exists to guard -- reproduced under
    both `openbox` and, initially, `twm`; `twm` was dropped for this
    fixture after it turned out to have its own separate, unrelated
    hang deep inside `cairo_xlib_surface_create`, apparently a quirk of
    that particular 1990s-era WM's own grab handling -- confirmed
    absent under `openbox`, which is what any real user is actually
    likely to be running). Skips cleanly if `openbox` isn't installed --
    same opt-in tier as `x_display`'s own Xvfb skip; `openbox` isn't one
    of setup.md's documented dependencies, only this one regression
    test's own.

    Waits for a real readiness signal, not a fixed sleep, the same way
    `x_display` itself polls Xvfb via `xdotool getdisplaygeometry`:
    unlike `twm`, `openbox` is EWMH-compliant and sets
    `_NET_SUPPORTING_WM_CHECK` on the root window as soon as it's ready
    to manage windows, which `xprop` (part of the same `x11-utils`
    package tier as `xdotool`) can poll for directly.
    """
    openbox = shutil.which("openbox")
    if not openbox:
        pytest.skip("openbox isn't installed -- needed to test graphics init against "
                     "a real window manager, not just a bare Xvfb instance")
    proc = subprocess.Popen([openbox], env=dict(os.environ, DISPLAY=x_display),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + 10
    ready = False
    xprop = shutil.which("xprop")
    while time.time() < deadline:
        if proc.poll() is not None:
            pytest.fail(f"openbox on {x_display} exited immediately (see its exit code: {proc.returncode})")
        if xprop:
            probe = subprocess.run(
                [xprop, "-root", "_NET_SUPPORTING_WM_CHECK"],
                env=dict(os.environ, DISPLAY=x_display),
                capture_output=True, text=True,
            )
            if probe.returncode == 0 and "_NET_SUPPORTING_WM_CHECK" in probe.stdout:
                ready = True
                break
        else:
            # xprop isn't installed -- fall back to a fixed, generous
            # sleep instead of a real readiness poll (openbox itself
            # isn't a documented setup.md dependency at all, so xprop's
            # own absence shouldn't hard-fail this fixture too).
            time.sleep(1)
            ready = True
            break
        time.sleep(0.1)
    if not ready:
        proc.terminate()
        pytest.fail(f"openbox on {x_display} never became ready to manage windows")
    try:
        yield x_display
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
