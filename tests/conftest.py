"""Shared fixtures for the claude.md-driven Festina spec test suite.

See tests/CONTRACT.md for why these tests target a `festina` package that
doesn't exist yet, and what its assumed API looks like.
"""
import importlib
import shutil
import subprocess
import sys

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


@pytest.fixture
def compile_and_run(tmp_path, codegen, cli_mod):
    """Compile a Festina source string to a native executable and run it.

    Skips with a clear reason if no usable C compiler is on PATH -- this
    is a toolchain-availability skip (distinct from the
    SPEC_UNIMPLEMENTED_REASON skips above), since codegen.py itself is
    implemented either way. Prefers clang but accepts gcc too: as of
    "real compilation, minimal setup" stage 3, festina.llvm_backend
    compiles the LLVM IR itself (when available) rather than handing the
    .ll file to the C compiler, so cc's job is just compiling
    festina_runtime.c and linking plain object files -- work gcc does
    exactly as well as clang. See festina/cli.py's module docstring.
    """
    cc = shutil.which("clang") or shutil.which("gcc") or shutil.which("cc")
    if not cc:
        pytest.skip("no C compiler (clang/gcc/cc) on PATH -- cannot "
                     "compile/link the Festina runtime and the generated code")

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
