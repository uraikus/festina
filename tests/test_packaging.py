"""claude.md #59, "real compilation, minimal setup" stage 2 -- packaging
the Python compiler frontend into a standalone binary via PyInstaller
(see scripts/package_compiler.sh and packaging/festina_entry.py).

Skips cleanly if pyinstaller isn't installed. Deliberately *not* in
requirements-dev.txt (it's in its own requirements-build.txt instead):
nothing about developing or testing festina/ itself should need it
(claude.md #59's own minimal-dependencies principle) -- it's an opt-in,
build-time-only dependency for whoever is producing a distributable
binary, so this test only runs in environments that have explicitly
installed it.

A real PyInstaller build takes tens of seconds, so this builds the
binary exactly once (a session-scoped fixture) and reuses it across
every test in this file, rather than rebuilding per test.
"""
import os
import shutil
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _pyinstaller_available():
    return shutil.which("pyinstaller") is not None


def _c_compiler_available():
    return shutil.which("clang") or shutil.which("gcc") or shutil.which("cc")


@pytest.fixture(scope="session")
def packaged_binary(tmp_path_factory):
    if not _pyinstaller_available():
        pytest.skip("pyinstaller is not installed -- opt-in, build-time-only "
                     "dependency for scripts/package_compiler.sh; see "
                     "tests/CONTRACT.md")
    out_dir = tmp_path_factory.mktemp("festina-dist")
    script = os.path.join(REPO_ROOT, "scripts", "package_compiler.sh")
    result = subprocess.run(
        [script, str(out_dir)],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, (
        f"scripts/package_compiler.sh failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    binary = out_dir / "festina"
    assert binary.exists(), "package_compiler.sh reported success but wrote no binary"
    return str(binary)


class TestPackagedCompilerBinary:
    def test_binary_is_self_contained_no_python_needed(self, packaged_binary, tmp_path):
        # The actual point of stage 2: shadow every `python`/`python3*`
        # this environment has with a command that always fails, so any
        # attempt by the packaged binary to shell out to a system Python
        # interpreter would surface as a failure here.
        if not _c_compiler_available():
            pytest.skip("no C compiler (clang/gcc/cc) on PATH -- cannot "
                        "compile/link the program the packaged binary produces")

        fake_bin = tmp_path / "fake_python_path"
        fake_bin.mkdir()
        for name in ("python", "python3", f"python{sys.version_info.major}.{sys.version_info.minor}"):
            (fake_bin / name).symlink_to("/bin/false")

        src = tmp_path / "hello.f"
        src.write_text("log('packaged binary works')")
        out = tmp_path / "hello"

        env = {"PATH": f"{fake_bin}:/usr/bin:/bin"}
        result = subprocess.run(
            [packaged_binary, str(src), "-o", str(out)],
            cwd=tmp_path, capture_output=True, text=True, env=env, timeout=60,
        )
        assert result.returncode == 0, f"compile failed:\n{result.stderr}"
        assert out.exists()

        run_result = subprocess.run([str(out)], cwd=tmp_path, capture_output=True, text=True, timeout=15)
        assert run_result.returncode == 0
        assert run_result.stdout.strip() == "packaged binary works"

    def test_emit_llvm_flag_works(self, packaged_binary, tmp_path):
        # A lighter-weight check that doesn't need a C compiler at all --
        # exercises the packaged binary's argument parsing and codegen
        # path without linking.
        src = tmp_path / "hello.f"
        src.write_text("log('hi')")
        result = subprocess.run(
            [packaged_binary, str(src), "--emit-llvm"],
            cwd=tmp_path, capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert "define" in result.stdout  # real LLVM IR, not an error message
