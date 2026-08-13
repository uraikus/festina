"""Shared fixtures for the claude.md-driven Festina spec test suite.

See tests/CONTRACT.md for why these tests target a `festina` package that
doesn't exist yet, and what its assumed API looks like.
"""
import importlib

import pytest

SPEC_UNIMPLEMENTED_REASON = (
    "festina.{mod} is not implemented yet -- claude.md describes the "
    "Festina language spec, but no `festina` package exists in this repo "
    "yet (compiler/ is an unrelated JS-subset prototype). "
    "See tests/CONTRACT.md for the assumed API this suite targets."
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
def write_source(tmp_path):
    """Write named Festina source files under a temp dir; return their dir."""

    def _write(files: dict):
        for relpath, content in files.items():
            p = tmp_path / relpath
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        return tmp_path

    return _write
