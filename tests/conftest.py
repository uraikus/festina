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


def _require_c_compiler():
    """Shared by compile_and_run/compile_multi_and_run: skip with a
    clear, toolchain-specific reason if no usable C compiler is on
    PATH -- distinct from the SPEC_UNIMPLEMENTED_REASON skips above,
    since codegen.py itself is implemented either way; this is "this
    environment can't link native code," not "the feature doesn't
    exist." Prefers clang but accepts gcc too: as of "real compilation,
    minimal setup" stage 3, festina.llvm_backend compiles the LLVM IR
    itself (when available) rather than handing the .ll file to the C
    compiler, so cc's job is just compiling festina_runtime.c and
    linking plain object files -- work gcc does exactly as well as
    clang. See festina/cli.py's module docstring."""
    cc = shutil.which("clang") or shutil.which("gcc") or shutil.which("cc")
    if not cc:
        pytest.skip("no C compiler (clang/gcc/cc) on PATH -- cannot "
                     "compile/link the Festina runtime and the generated code")
    return cc


@pytest.fixture
def compile_and_run(tmp_path, codegen, cli_mod):
    """Compile a Festina source string to a native executable and run it."""
    cc = _require_c_compiler()

    def _run(source, filename="main.f", args=None):
        src_path = tmp_path / filename
        src_path.write_text(source)
        out_path = tmp_path / "program"
        cli_mod.compile_file(str(src_path), str(out_path), cc=cc)
        result = subprocess.run(
            [str(out_path), *(args or [])],
            cwd=tmp_path, capture_output=True, text=True, timeout=15,
        )
        return result

    return _run


@pytest.fixture
def write_source(tmp_path):
    """Write named Festina source files under a temp dir; return their dir."""

    def _write(files: dict):
        for relpath, content in files.items():
            p = tmp_path / relpath
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
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
    """
    cc = _require_c_compiler()
    if not shutil.which("xdotool"):
        pytest.skip("xdotool isn't installed -- needed to simulate clicks/mouse "
                     "movement against the graphics canvas window")

    def _run(source, filename="main.f"):
        src_path = tmp_path / filename
        src_path.write_text(source)
        out_path = tmp_path / "program"
        cli_mod.compile_file(str(src_path), str(out_path), cc=cc)

        stdout_path = tmp_path / "stdout.log"
        env = dict(os.environ, DISPLAY=x_display)
        proc = subprocess.Popen(
            ["stdbuf", "-oL", str(out_path)],
            cwd=tmp_path, stdout=open(stdout_path, "w"), stderr=subprocess.STDOUT,
            env=env,
        )
        return proc, stdout_path

    return _run
