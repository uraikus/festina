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
import sys

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
        lines, all_ok, _missing = cli_mod._doctor_report()
        assert all_ok is True
        joined = "\n".join(lines)
        assert "OK" in joined
        assert "C compiler" in joined

    def test_missing_pkg_config_is_reported_with_its_install_hint(self, cli_mod, path_without):
        path_without("pkg-config")
        lines, all_ok, _missing = cli_mod._doctor_report()
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
        lines, all_ok, _missing = cli_mod._doctor_report()
        joined = "\n".join(lines)
        assert "sqlite3" in joined and "MISSING" in joined

    def test_missing_c_compiler_is_reported_as_required(self, cli_mod, path_without):
        path_without("clang", "gcc", "cc")
        lines, all_ok, _missing = cli_mod._doctor_report()
        assert all_ok is False
        assert any("MISSING" in l and "C compiler" in l for l in lines)

    def test_missing_graphics_or_audio_deps_are_optional_not_fatal(self, cli_mod, monkeypatch):
        _require_c_compiler()
        if not shutil.which("pkg-config"):
            pytest.skip("pkg-config not on PATH in this environment")
        # Simulate cairo-xlib/alsa both being absent while every
        # *required* pkg-config package -- sqlite3 always, plus
        # windows.md Phase 0's gnurx on win32 (claude.md #126 round
        # six: the plain `pkg == "sqlite3"` version of this line left
        # gnurx REQUIRED-and-missing on real Windows CI, correctly
        # flipping all_ok to False -- a gap in this test's own platform-
        # blind setup, not a doctor-report bug) -- is still found,
        # without depending on this machine's actual package set either
        # way.
        required = {"sqlite3", *cli_mod._core_pkgs()}
        monkeypatch.setattr(cli_mod, "_pkg_config_has", lambda pkg: pkg in required)
        lines, all_ok, _missing = cli_mod._doctor_report()
        joined = "\n".join(lines)
        assert "missing, optional" in joined
        assert all_ok is True  # optional deps missing must not flip the overall result

    def test_missing_libllvm_and_clang_together_is_required(self, cli_mod, monkeypatch, path_without):
        path_without("clang")
        if shutil.which("gcc") is None and shutil.which("cc") is None:
            pytest.skip("no gcc/cc fallback on this machine to exercise the nuanced case")
        monkeypatch.setattr(cli_mod.llvm_backend, "available", lambda: False)
        lines, all_ok, _missing = cli_mod._doctor_report()
        # Neither the fast (libLLVM) nor fallback (clang IR frontend)
        # pipeline could finish a compile in this combination.
        assert all_ok is False

    def test_missing_libllvm_alone_is_not_fatal_when_clang_is_present(self, cli_mod, monkeypatch):
        if not shutil.which("clang"):
            pytest.skip("no clang on this machine to exercise the fallback-still-works case")
        if not shutil.which("pkg-config"):
            pytest.skip("pkg-config not on PATH in this environment")
        monkeypatch.setattr(cli_mod.llvm_backend, "available", lambda: False)
        lines, all_ok, _missing = cli_mod._doctor_report()
        assert all_ok is True  # clang's own IR frontend fallback still works
        assert any("libLLVM" in l for l in lines)

    def test_reports_festina_on_path_when_resolvable(self, cli_mod, tmp_path, monkeypatch):
        bin_dir = tmp_path / "fake_bin"
        bin_dir.mkdir()
        # claude.md #126 round six: shutil.which resolves "festina" on
        # Windows via PATHEXT extension search, not the bare name -- a
        # file literally named "festina" with no extension (0o755 is
        # also a no-op there, NTFS has no execute-permission bit) was
        # never findable at all, so _doctor_report's "resolves to" line
        # never appeared and this assertion failed, found by real
        # Windows CI. Same shell-needs-an-executable-extension reasoning
        # windows.md/_default_output_name already established.
        fake_name = "festina.exe" if sys.platform == "win32" else "festina"
        fake = bin_dir / fake_name
        fake.write_text("#!/bin/sh\n")
        fake.chmod(0o755)
        monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
        lines, _, _missing = cli_mod._doctor_report()
        joined = "\n".join(lines)
        assert "resolves to" in joined
        # claude.md #126 round eight: shutil.which's Windows PATHEXT
        # search returns the extension it matched (".EXE", uppercase,
        # from PATHEXT itself) rather than preserving this fake file's
        # own on-disk case -- an exact string match failed on real
        # Windows CI even though the right file was genuinely found.
        # os.path.normcase makes the comparison match Windows' own
        # case/separator insensitivity (a no-op on POSIX).
        assert os.path.normcase(str(fake)) in os.path.normcase(joined)

    def test_reports_how_to_add_festina_to_path_when_it_is_missing(self, cli_mod, path_without):
        # path_without's curated tool set never includes "festina"
        # itself, so this environment doesn't have it on PATH either --
        # exactly the case this doctor branch is meant to catch.
        path_without()
        lines, _, _missing = cli_mod._doctor_report()
        joined = "\n".join(lines)
        assert "not on PATH" in joined
        assert "export PATH=" in joined
        assert "bin" in joined


class _FakeStdin:
    """A minimal stand-in for sys.stdin that only answers isatty() --
    _run_doctor_fix's own confirmation prompt reads its actual answer
    through the mocked `input()` builtin below, never through stdin
    directly, so nothing else needs implementing. Used instead of
    monkeypatching attributes onto the real sys.stdin object, which
    pytest's own capture machinery already wraps."""

    def isatty(self):
        return True


class TestDoctorFix:
    """festina doctor --fix -- festina.cli._run_doctor_fix: turns
    _doctor_report's exact same missing-dependency list into a real
    install command for the detected package manager instead of just
    printing hint text for a human to copy by hand, confirmed first
    (skippable with --yes). _doctor_report is monkeypatched throughout
    to a fixed (lines, all_ok, missing) triple, so these tests exercise
    _run_doctor_fix's own decision logic -- confirm, build and dedupe
    the package list, run, re-check -- independent of this machine's
    real dependency state (TestDoctor above already covers
    _doctor_report itself producing that list correctly)."""

    def _stub_report(self, cli_mod, monkeypatch, missing, all_ok=None):
        if all_ok is None:
            all_ok = not any(required for _key, required in missing)
        monkeypatch.setattr(cli_mod, "_doctor_report",
                             lambda: (["Festina compiler dependencies"], all_ok, missing))

    def test_nothing_to_fix_when_nothing_is_missing(self, cli_mod, monkeypatch, capsys):
        self._stub_report(cli_mod, monkeypatch, [])
        assert cli_mod._run_doctor_fix() == 0
        assert "nothing to fix" in capsys.readouterr().out

    def test_no_supported_manager_is_reported_and_fails_when_something_required_is_missing(
            self, cli_mod, monkeypatch, capsys):
        self._stub_report(cli_mod, monkeypatch, [("cc", True)])
        monkeypatch.setattr(cli_mod, "_detect_package_manager", lambda: None)
        assert cli_mod._run_doctor_fix() == 1
        out = capsys.readouterr().out
        assert "doctor --fix only knows how to drive apt" in out

    def test_no_supported_manager_still_exits_zero_when_only_optional_is_missing(
            self, cli_mod, monkeypatch):
        self._stub_report(cli_mod, monkeypatch, [("cairo", False)])
        monkeypatch.setattr(cli_mod, "_detect_package_manager", lambda: None)
        assert cli_mod._run_doctor_fix() == 0

    def test_declining_the_confirmation_runs_nothing(self, cli_mod, monkeypatch, capsys):
        self._stub_report(cli_mod, monkeypatch, [("sqlite3", True), ("pkg-config", True)])
        monkeypatch.setattr(cli_mod, "_detect_package_manager", lambda: "apt")
        monkeypatch.setattr(cli_mod.sys, "stdin", _FakeStdin())
        monkeypatch.setattr("builtins.input", lambda prompt="": "n")
        ran = []
        monkeypatch.setattr(cli_mod.subprocess, "run", lambda cmd, **k: ran.append(cmd))
        assert cli_mod._run_doctor_fix() == 1
        assert ran == []
        out = capsys.readouterr().out
        # sqlite3 + pkg-config, deduplicated and combined into one command,
        # not two separate installs.
        assert "About to run:" in out and "apt install -y" in out
        assert "libsqlite3-dev" in out
        assert "pkg-config" in out

    def test_non_interactive_without_yes_refuses_to_prompt_or_install(
            self, cli_mod, monkeypatch, capsys):
        self._stub_report(cli_mod, monkeypatch, [("sqlite3", True)])
        monkeypatch.setattr(cli_mod, "_detect_package_manager", lambda: "apt")
        monkeypatch.setattr(cli_mod.sys, "stdin", type("S", (), {"isatty": lambda self: False})())

        def _unexpected_input(prompt=""):
            raise AssertionError("must not prompt when stdin is not a tty")
        monkeypatch.setattr("builtins.input", _unexpected_input)
        ran = []
        monkeypatch.setattr(cli_mod.subprocess, "run", lambda cmd, **k: ran.append(cmd))
        assert cli_mod._run_doctor_fix() == 1
        assert ran == []
        assert "--yes" in capsys.readouterr().out

    def test_yes_flag_skips_confirmation_and_installs(self, cli_mod, monkeypatch, capsys):
        # A stateful stub: missing before the "install", fixed after --
        # simulates a real successful install rather than a static
        # report that could never actually change between the two
        # _doctor_report() calls _run_doctor_fix itself makes (before
        # installing, and again afterward to confirm it worked).
        reports = iter([
            (["Festina compiler dependencies"], False, [("cc", True)]),
            (["Festina compiler dependencies"], True, []),
        ])
        monkeypatch.setattr(cli_mod, "_doctor_report", lambda: next(reports))
        monkeypatch.setattr(cli_mod, "_detect_package_manager", lambda: "apt")

        def _unexpected_input(prompt=""):
            raise AssertionError("must not prompt with --yes")
        monkeypatch.setattr("builtins.input", _unexpected_input)

        calls = []

        def _fake_run(cmd, **kwargs):
            calls.append(cmd)
            return type("Result", (), {"returncode": 0})()
        monkeypatch.setattr(cli_mod.subprocess, "run", _fake_run)

        assert cli_mod._run_doctor_fix(assume_yes=True) == 0
        assert len(calls) == 1
        assert "install" in calls[0] and "-y" in calls[0] and calls[0][-1] == "clang"
        out = capsys.readouterr().out
        assert "Re-checking" in out
        assert "now installed" in out

    def test_a_nonzero_install_exit_code_is_propagated_without_re_checking(
            self, cli_mod, monkeypatch, capsys):
        self._stub_report(cli_mod, monkeypatch, [("cc", True)])
        monkeypatch.setattr(cli_mod, "_detect_package_manager", lambda: "apt")
        report_calls = []
        original = cli_mod._doctor_report

        def _counting_report():
            report_calls.append(1)
            return original()
        # original() here is already the stub from _stub_report (set via
        # monkeypatch.setattr above it in MRO order) -- count calls to it.
        monkeypatch.setattr(cli_mod, "_doctor_report", _counting_report)

        def _fake_run(cmd, **kwargs):
            return type("Result", (), {"returncode": 42})()
        monkeypatch.setattr(cli_mod.subprocess, "run", _fake_run)

        assert cli_mod._run_doctor_fix(assume_yes=True) == 42
        assert report_calls == [1], "must not re-check doctor after a failed install"
        assert "exited with status 42" in capsys.readouterr().out

    def test_still_missing_after_install_is_reported(self, cli_mod, monkeypatch, capsys):
        self._stub_report(cli_mod, monkeypatch, [("cc", True)])
        monkeypatch.setattr(cli_mod, "_detect_package_manager", lambda: "apt")

        def _fake_run(cmd, **kwargs):
            return type("Result", (), {"returncode": 0})()
        monkeypatch.setattr(cli_mod.subprocess, "run", _fake_run)

        assert cli_mod._run_doctor_fix(assume_yes=True) == 1
        assert "still missing" in capsys.readouterr().out

    def test_a_key_with_no_mapping_for_the_manager_is_named_as_unfixable(
            self, cli_mod, monkeypatch, capsys):
        # "llvm" only has an apt entry (_PKG_MANAGER_PACKAGES) -- on
        # brew there's genuinely nothing separate to install for it (see
        # that dict's own docstring), so alongside a real fixable
        # dependency it should be called out by name, not silently
        # dropped.
        self._stub_report(cli_mod, monkeypatch, [("sqlite3", True), ("llvm", False)])
        monkeypatch.setattr(cli_mod, "_detect_package_manager", lambda: "brew")
        monkeypatch.setattr("builtins.input", lambda prompt="": "n")
        monkeypatch.setattr(cli_mod.sys, "stdin", _FakeStdin())
        assert cli_mod._run_doctor_fix() == 1
        out = capsys.readouterr().out
        assert "brew install" in out and "sqlite" in out

    def test_nothing_installable_for_this_manager_is_reported_plainly(
            self, cli_mod, monkeypatch, capsys):
        # llvm's ONLY entry is apt -- on brew there is nothing at all to
        # install for it, and it's the only thing missing.
        self._stub_report(cli_mod, monkeypatch, [("llvm", False)])
        monkeypatch.setattr(cli_mod, "_detect_package_manager", lambda: "brew")
        assert cli_mod._run_doctor_fix() == 0  # optional-only, so still success
        assert "Nothing doctor --fix knows how to install for brew" in capsys.readouterr().out

    def test_mac_missing_compiler_gets_the_xcode_select_note(self, cli_mod, monkeypatch, capsys):
        self._stub_report(cli_mod, monkeypatch, [("cc", True)])
        monkeypatch.setattr(cli_mod, "_detect_package_manager", lambda: "brew")
        monkeypatch.setattr("builtins.input", lambda prompt="": "n")
        monkeypatch.setattr(cli_mod.sys, "stdin", _FakeStdin())
        code = cli_mod._run_doctor_fix()
        out = capsys.readouterr().out
        assert "xcode-select --install" in out
        # "cc" has no brew package mapping at all -- nothing to install,
        # even though the note above was printed.
        assert code == 1


class TestMainDispatchDoctorFix:
    """The --fix/--yes flags wired through main()'s own argv parsing,
    not just calling _run_doctor_fix directly."""

    def test_doctor_fix_flag_is_recognized(self, cli_mod, monkeypatch, capsys):
        monkeypatch.setattr(cli_mod, "_run_doctor_fix", lambda assume_yes=False: 0)
        assert cli_mod.main(["doctor", "--fix"]) == 0

    def test_doctor_without_fix_does_not_call_run_doctor_fix(self, cli_mod, monkeypatch):
        called = []
        monkeypatch.setattr(cli_mod, "_run_doctor_fix", lambda assume_yes=False: called.append(1) or 0)
        cli_mod.main(["doctor"])
        assert called == []

    def test_yes_flag_is_forwarded_to_run_doctor_fix(self, cli_mod, monkeypatch):
        seen = []
        monkeypatch.setattr(cli_mod, "_run_doctor_fix", lambda assume_yes=False: seen.append(assume_yes) or 0)
        cli_mod.main(["doctor", "--fix", "--yes"])
        assert seen == [True]


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
