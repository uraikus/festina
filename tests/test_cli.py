"""festina compile / run / doctor / help (festina/cli.py).

TestMissingDependencyErrors in test_codegen.py already covers a real
*compile* failing with a clear, actionable error when a dependency is
missing (claude.md #59) -- this file covers the CLI surface built on top
of that: the four subcommands themselves, `run`'s compile-then-execute-
and-forward-the-exit-code behavior, and `doctor`'s proactive version of
the same missing-dependency checks. See tests/CONTRACT.md for the full
design writeup.
"""
import os
import shutil

import pytest

from tests.conftest import _require_c_compiler


class TestRun:
    """festina run entry.f -- festina.cli.run_program: compile to a
    throwaway temp executable and run it immediately, stdio inherited
    (not captured) and its own exit code forwarded."""

    def test_run_compiles_and_executes_the_program(self, cli_mod, tmp_path, capfd):
        cc = _require_c_compiler()
        src = tmp_path / "main.f"
        src.write_text("log('hello from run')")
        exit_code = cli_mod.run_program(str(src), cc=cc)
        assert exit_code == 0
        # capfd captures at the OS file-descriptor level, so it sees the
        # child process's directly-inherited stdout too, not just this
        # process's own -- exactly what run_program's "no capture_output"
        # choice is meant to preserve for an interactive program.
        assert capfd.readouterr().out == "hello from run\n"

    def test_run_forwards_the_programs_own_nonzero_exit_code(self, cli_mod, tmp_path):
        # claude.md #42: fail() calls exit(1) in the C runtime.
        cc = _require_c_compiler()
        src = tmp_path / "main.f"
        src.write_text("fail('boom')")
        assert cli_mod.run_program(str(src), cc=cc) == 1

    def test_run_raises_a_compile_error_for_bad_source_without_running_anything(self, cli_mod, errors, tmp_path):
        cc = _require_c_compiler()
        src = tmp_path / "main.f"
        src.write_text("int x = 'not an int'")
        with pytest.raises(errors.CompileError):
            cli_mod.run_program(str(src), cc=cc)


class TestDoctor:
    """festina doctor -- festina.cli._doctor_report: the exact same
    checks/hints _run_tool/_pkg_config raise CompileError with on a real
    compile failure, just non-fatal and proactive."""

    def test_reports_ok_when_everything_needed_is_present(self, cli_mod):
        _require_c_compiler()
        if not shutil.which("pkg-config"):
            pytest.skip("pkg-config not on PATH in this environment")
        lines, all_ok = cli_mod._doctor_report()
        assert all_ok is True
        joined = "\n".join(lines)
        assert "OK" in joined
        assert "C compiler" in joined

    def test_missing_pkg_config_is_reported_with_its_install_hint(self, cli_mod, path_without):
        path_without("pkg-config")
        lines, all_ok = cli_mod._doctor_report()
        assert all_ok is False
        joined = "\n".join(lines)
        assert "pkg-config" in joined
        assert "MISSING" in joined
        assert cli_mod._INSTALL_HINTS["pkg-config"] in joined

    def test_missing_pkg_config_also_fails_the_dependent_sqlite3_check(self, cli_mod, path_without):
        # sqlite3's own check (_pkg_config_has) can't succeed without
        # pkg-config to ask in the first place -- both should be
        # reported, not just the first one found.
        path_without("pkg-config")
        lines, all_ok = cli_mod._doctor_report()
        joined = "\n".join(lines)
        assert "sqlite3" in joined and "MISSING" in joined

    def test_missing_c_compiler_is_reported_as_required(self, cli_mod, path_without):
        path_without("clang", "gcc", "cc")
        lines, all_ok = cli_mod._doctor_report()
        assert all_ok is False
        assert any("MISSING" in l and "C compiler" in l for l in lines)

    def test_missing_graphics_or_audio_deps_are_optional_not_fatal(self, cli_mod, monkeypatch):
        _require_c_compiler()
        if not shutil.which("pkg-config"):
            pytest.skip("pkg-config not on PATH in this environment")
        # Simulate cairo-xlib/alsa both being absent while sqlite3 (the
        # only *required* pkg-config package) is still found, without
        # depending on this machine's actual package set either way.
        monkeypatch.setattr(cli_mod, "_pkg_config_has", lambda pkg: pkg == "sqlite3")
        lines, all_ok = cli_mod._doctor_report()
        joined = "\n".join(lines)
        assert "missing, optional" in joined
        assert all_ok is True  # optional deps missing must not flip the overall result

    def test_missing_libllvm_and_clang_together_is_required(self, cli_mod, monkeypatch, path_without):
        path_without("clang")
        if shutil.which("gcc") is None and shutil.which("cc") is None:
            pytest.skip("no gcc/cc fallback on this machine to exercise the nuanced case")
        monkeypatch.setattr(cli_mod.llvm_backend, "available", lambda: False)
        lines, all_ok = cli_mod._doctor_report()
        # Neither the fast (libLLVM) nor fallback (clang IR frontend)
        # pipeline could finish a compile in this combination.
        assert all_ok is False

    def test_missing_libllvm_alone_is_not_fatal_when_clang_is_present(self, cli_mod, monkeypatch):
        if not shutil.which("clang"):
            pytest.skip("no clang on this machine to exercise the fallback-still-works case")
        if not shutil.which("pkg-config"):
            pytest.skip("pkg-config not on PATH in this environment")
        monkeypatch.setattr(cli_mod.llvm_backend, "available", lambda: False)
        lines, all_ok = cli_mod._doctor_report()
        assert all_ok is True  # clang's own IR frontend fallback still works
        assert any("libLLVM" in l for l in lines)

    def test_reports_festina_on_path_when_resolvable(self, cli_mod, tmp_path, monkeypatch):
        bin_dir = tmp_path / "fake_bin"
        bin_dir.mkdir()
        fake = bin_dir / "festina"
        fake.write_text("#!/bin/sh\n")
        fake.chmod(0o755)
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
        lines, _ = cli_mod._doctor_report()
        joined = "\n".join(lines)
        assert "resolves to" in joined
        assert str(fake) in joined

    def test_reports_how_to_add_festina_to_path_when_it_is_missing(self, cli_mod, path_without):
        # path_without's curated tool set never includes "festina"
        # itself, so this environment doesn't have it on PATH either --
        # exactly the case this doctor branch is meant to catch.
        path_without()
        lines, _ = cli_mod._doctor_report()
        joined = "\n".join(lines)
        assert "not on PATH" in joined
        assert "export PATH=" in joined
        assert "bin" in joined


class TestHelpAndNoCommand:
    """`git`/`cargo`'s own distinction: no subcommand at all is a usage
    mistake (exit 1); `festina help` is a deliberate, successful request
    for the same text (exit 0)."""

    def test_bare_invocation_prints_help_and_exits_nonzero(self, cli_mod, capsys):
        assert cli_mod.main([]) == 1
        assert "usage: festina" in capsys.readouterr().out

    def test_help_command_prints_help_and_exits_zero(self, cli_mod, capsys):
        assert cli_mod.main(["help"]) == 0
        assert "usage: festina" in capsys.readouterr().out

    def test_unrecognized_subcommand_is_an_argparse_usage_error(self, cli_mod):
        with pytest.raises(SystemExit):
            cli_mod.main(["not-a-real-command"])


class TestMainDispatch:
    """End-to-end through main()'s own argv parsing, not just calling
    compile_file/run_program/_doctor_report directly -- makes sure the
    subcommand wiring itself (not just the underlying functions) works."""

    def test_compile_subcommand_writes_the_output_binary(self, cli_mod, tmp_path):
        cc = _require_c_compiler()
        src = tmp_path / "main.f"
        src.write_text("log('hi')")
        out = tmp_path / "out"
        assert cli_mod.main(["compile", str(src), "-o", str(out), "--cc", cc]) == 0
        assert out.exists()

    def test_run_subcommand_executes_and_forwards_output(self, cli_mod, tmp_path, capfd):
        cc = _require_c_compiler()
        src = tmp_path / "main.f"
        src.write_text("log('via main run')")
        assert cli_mod.main(["run", str(src), "--cc", cc]) == 0
        assert capfd.readouterr().out == "via main run\n"

    def test_doctor_subcommand_prints_the_report(self, cli_mod, capsys):
        code = cli_mod.main(["doctor"])
        assert code in (0, 1)
        assert "Festina compiler dependencies" in capsys.readouterr().out

    def test_compile_subcommand_reports_a_compile_error_to_stderr(self, cli_mod, tmp_path, capsys):
        cc = _require_c_compiler()
        src = tmp_path / "main.f"
        src.write_text("int x = 'nope'")
        assert cli_mod.main(["compile", str(src), "--cc", cc]) == 1
        assert "error" in capsys.readouterr().err
