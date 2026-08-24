"""macos.md / windows.md Phase 0: the platform seams, tested from
every platform.

The porting plans deliberately cut their platform differences as pure
functions of an injectable platform name (see macos.md's "Shared work"
section), so each OS's behavior is unit-tested HERE, on whatever OS
the suite happens to run on -- the Linux CI job verifies the darwin
and win32 branches years before either port has hardware in CI. The
On-macOS/On-Windows classes at the bottom are the same contracts
re-asserted against the real platform; they skip everywhere else and
become live the day the macos-14/windows-latest jobs from the plans
exist.

Also here: the cross-platform contracts that are about PROGRAMS rather
than the toolchain -- byte-exact binary I/O (the fact that makes blobs
CRLF-proof on Windows) and the key-name vocabulary artifact
(runtime/festina_key_names.h) every windowing backend maps into.
"""
import os
import re
import subprocess
import sys

import pytest


_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_KEY_NAMES_H = os.path.join(_REPO_ROOT, "runtime", "festina_key_names.h")


def _stub_which_any(cli_mod, monkeypatch):
    """Any _doctor_report() test that monkeypatches sys.platform to
    "win32" while actually running on real Linux/macOS CI must not let
    ANY of _doctor_report's several real shutil.which(...) calls
    execute (the C compiler check, the pkg-config check, the festina-
    on-PATH check all call it directly or via _which_any) -- shutil.which
    has its OWN internal `sys.platform == "win32"` branch (calling into
    the Windows-only _winapi module), which the spoofed platform string
    triggers for real, crashing with "'NoneType' object has no
    attribute 'NeedCurrentDirectoryForExePath'" on Python 3.12+ where
    _winapi is None on POSIX (claude.md #126, caught by real Linux CI
    running a newer Python than this suite happened to be developed
    against). Patching shutil.which itself, as cli.py imported it,
    sidesteps every call site at once, the same way other
    cross-platform-from-Linux tests here stub out real toolchain calls
    they have no state for."""
    monkeypatch.setattr(cli_mod.shutil, "which", lambda cmd: f"/fake/{cmd}")


class TestDefaultOutputName:
    """windows.md Phase 0: the default output gains `.exe` on Windows
    -- both because the shell needs the extension and because MinGW's
    linker appends it itself, so the name asked for must match the
    file written. Everywhere else the name stays extensionless."""

    def test_linux_name_is_extensionless(self, cli_mod):
        assert cli_mod._default_output_name("game.f", "linux") == "game"

    def test_darwin_name_is_extensionless(self, cli_mod):
        assert cli_mod._default_output_name("game.f", "darwin") == "game"

    def test_windows_name_gains_exe(self, cli_mod):
        assert cli_mod._default_output_name("game.f", "win32") == "game.exe"

    def test_windows_does_not_double_an_existing_exe(self, cli_mod):
        # A source file literally named `tool.exe.f` already yields a
        # .exe-suffixed name; appending again would give tool.exe.exe.
        assert cli_mod._default_output_name("tool.exe.f", "win32") == "tool.exe"

    def test_the_empty_stem_fallback_is_platform_aware_too(self, cli_mod):
        assert cli_mod._default_output_name(".f", "linux") == "a.out"
        assert cli_mod._default_output_name(".f", "win32") == "a.out.exe"

    def test_a_directory_prefix_is_stripped_on_every_platform(self, cli_mod):
        assert cli_mod._default_output_name("src/deep/game.f", "win32") == "game.exe"


class TestRenameIfLinkerAppendedExe:
    """claude.md #126 round four: _default_output_name's own docstring
    already said MinGW's linker appends `.exe` to a `-o` name lacking
    one, but the actual guard only ever covered the *default*-name
    path (which already ends in `.exe`, so the append never fires
    there) -- an explicit `-o program` still silently linked to
    `program.exe` while compile_file kept insisting `program` was the
    output. _rename_if_linker_appended_exe runs after linking and
    restores the caller's exact requested name."""

    def test_non_windows_is_always_a_no_op(self, cli_mod, tmp_path, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        out = tmp_path / "program"
        (tmp_path / "program.exe").write_bytes(b"fake")
        cli_mod._rename_if_linker_appended_exe(str(out))
        assert not out.exists()
        assert (tmp_path / "program.exe").exists()

    def test_a_request_that_already_ends_in_exe_is_left_alone(
            self, cli_mod, tmp_path, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        out = tmp_path / "program.exe"
        out.write_bytes(b"real")
        cli_mod._rename_if_linker_appended_exe(str(out))
        assert out.read_bytes() == b"real"

    def test_windows_renames_the_linkers_exe_back_to_the_exact_request(
            self, cli_mod, tmp_path, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        out = tmp_path / "program"
        (tmp_path / "program.exe").write_bytes(b"linked")
        cli_mod._rename_if_linker_appended_exe(str(out))
        assert out.read_bytes() == b"linked"
        assert not (tmp_path / "program.exe").exists()

    def test_windows_does_nothing_if_the_linker_never_appended_exe(
            self, cli_mod, tmp_path, monkeypatch):
        # Nothing to rename -- e.g. a caller who never actually linked.
        monkeypatch.setattr(sys, "platform", "win32")
        out = tmp_path / "program"
        cli_mod._rename_if_linker_appended_exe(str(out))
        assert not out.exists()
        assert not (tmp_path / "program.exe").exists()

    def test_windows_overwrites_a_stale_exact_name_from_an_earlier_compile(
            self, cli_mod, tmp_path, monkeypatch):
        # claude.md #126 round twelve: the earlier version of this
        # function skipped the rename whenever `out` already existed --
        # meaning a SECOND compile to the same explicit path (exactly
        # what recompiling `program` after a source change does) left
        # the FIRST compile's stale binary in place forever, while the
        # fresh build sat unused at `program.exe`. Real Windows CI
        # caught this directly: a "second" compiled program's own
        # captured stdout was the first program's output verbatim.
        # os.replace already overwrites atomically -- the freshly
        # linked binary must always win.
        monkeypatch.setattr(sys, "platform", "win32")
        out = tmp_path / "program"
        out.write_bytes(b"stale, from an earlier compile")
        (tmp_path / "program.exe").write_bytes(b"freshly linked")
        cli_mod._rename_if_linker_appended_exe(str(out))
        assert out.read_bytes() == b"freshly linked"
        assert not (tmp_path / "program.exe").exists()


class TestStaticSqliteAttempt:
    """The per-platform "link libsqlite3.a" strategy extracted from
    _sqlite_link_flags (macos.md/windows.md Phase 0). GNU ld's
    -Bstatic/-Bdynamic toggles serve Linux AND MinGW-on-Windows --
    windows.md's toolchain decision is what keeps those two branches
    identical -- while ld64 needs the archive named by explicit path,
    and no libdir means nothing to try (None -> dynamic fallback)."""

    def test_linux_uses_gnu_ld_bstatic_toggles(self, cli_mod):
        flags = cli_mod._static_sqlite_attempt("linux", ["-lsqlite3", "-lm", "-lz"])
        assert flags == ["-Wl,-Bstatic", "-lsqlite3", "-Wl,-Bdynamic", "-lm", "-lz"]

    def test_windows_mingw_uses_the_same_gnu_ld_toggles(self, cli_mod):
        flags = cli_mod._static_sqlite_attempt("win32", ["-lsqlite3", "-lz"])
        assert flags == ["-Wl,-Bstatic", "-lsqlite3", "-Wl,-Bdynamic", "-lz"]

    def test_darwin_names_the_archive_by_explicit_path(self, cli_mod):
        flags = cli_mod._static_sqlite_attempt(
            "darwin", ["-lsqlite3", "-lz"], libdir="/opt/homebrew/opt/sqlite/lib")
        assert flags == [
            os.path.join("/opt/homebrew/opt/sqlite/lib", "libsqlite3.a"), "-lz"]
        # The ld64-rejected GNU toggle must never appear on darwin.
        assert not any(f.startswith("-Wl,-B") for f in flags)

    def test_darwin_without_a_libdir_has_nothing_to_try(self, cli_mod):
        assert cli_mod._static_sqlite_attempt("darwin", ["-lsqlite3"]) is None


class TestWindowsStaticRuntimeFlags:
    """windows.md Phase 3 item 2 (claude.md #129): the "copy-anywhere"
    DLL story for core/graphics-only compiled programs -- -static-libgcc
    unconditionally (a plain compiler-driver flag, no probe needed) plus
    a probed, -Bstatic/-Bdynamic-scoped -lwinpthread, skipped whenever
    audio is in play since audio already links winpthread dynamically
    via its own unconditional -pthread flag and this project has no
    Windows machine to confirm the two combine safely."""

    def test_non_windows_gets_nothing(self, cli_mod):
        assert cli_mod._windows_static_runtime_flags("clang", False, "linux") == []
        assert cli_mod._windows_static_runtime_flags("clang", False, "darwin") == []

    def test_windows_core_only_gets_static_libgcc_always(self, cli_mod, monkeypatch):
        # _can_link isn't real MinGW toolchain state this test has on
        # Linux -- stubbed True so the winpthread probe is exercised
        # deterministically, the same style TestStaticSqliteAttempt's
        # own callers use elsewhere in this file.
        monkeypatch.setattr(cli_mod, "_can_link", lambda cc, flags: False)
        flags = cli_mod._windows_static_runtime_flags("clang", False, "win32")
        assert flags == ["-static-libgcc"]

    def test_windows_core_only_adds_static_winpthread_when_available(
            self, cli_mod, monkeypatch):
        calls = []
        monkeypatch.setattr(cli_mod, "_can_link",
                            lambda cc, flags: calls.append(flags) or True)
        flags = cli_mod._windows_static_runtime_flags("clang", False, "win32")
        assert flags == ["-static-libgcc", "-Wl,-Bstatic", "-lwinpthread", "-Wl,-Bdynamic"]
        assert calls == [["-Wl,-Bstatic", "-lwinpthread", "-Wl,-Bdynamic"]]

    def test_windows_audio_program_skips_the_winpthread_probe_entirely(
            self, cli_mod, monkeypatch):
        # Audio already links winpthread dynamically via its own
        # unconditional -pthread flag (_RUNTIME_FEATURES["audio"]) --
        # adding a second, statically-scoped -lwinpthread on top risks
        # a link-order conflict this project cannot test for real, so
        # this must not even ATTEMPT the probe when audio is in play.
        calls = []
        monkeypatch.setattr(cli_mod, "_can_link", lambda cc, flags: calls.append(flags) or True)
        flags = cli_mod._windows_static_runtime_flags("clang", True, "win32")
        assert flags == ["-static-libgcc"]
        assert calls == [], "the winpthread probe must never run when audio is in play"

    def test_windows_falls_back_to_dynamic_winpthread_if_unavailable(
            self, cli_mod, monkeypatch):
        monkeypatch.setattr(cli_mod, "_can_link", lambda cc, flags: False)
        flags = cli_mod._windows_static_runtime_flags("clang", False, "win32")
        assert flags == ["-static-libgcc"]
        assert not any("winpthread" in f for f in flags)


class TestCorePkgs:
    """windows.md Phase 0: <regex.h> is core (every program links it,
    festina_runtime.c's own top comment), and it's part of libc
    everywhere except MinGW -- so this is the one core pkg-config
    ADDITION win32 needs, not a feature tier like graphics/audio."""

    def test_win32_needs_gnurx(self, cli_mod):
        # claude.md #126 INVERTED this test twice. Round one: the first
        # real Windows CI run found mingw-w64-ucrt-x86_64-libgnurx
        # conflicts with mingw-w64-ucrt-x86_64-libsystre (already
        # pulled in transitively) and pacman silently drops the
        # conflicting PACKAGE rather than erroring -- libsystre is the
        # one that's actually installed. Round two: libsystre's own
        # pkg-config name isn't "libsystre" either -- its PKGBUILD
        # declares Provides/Conflicts/Replaces against libgnurx (a
        # designed drop-in replacement) and ships its pkgconfig file
        # under that OLD name, gnurx.pc, confirmed via MSYS2's package
        # listing. Install libsystre, ask pkg-config for gnurx.
        assert cli_mod._core_pkgs("win32") == ["gnurx"]

    def test_linux_and_darwin_need_nothing_extra(self, cli_mod):
        assert cli_mod._core_pkgs("linux") == []
        assert cli_mod._core_pkgs("darwin") == []

    def test_core_object_and_link_libs_pick_up_gnurx_on_windows(
            self, cli_mod, monkeypatch):
        # _runtime_objects_and_link_libs must actually pass _core_pkgs()
        # through to both the cached object's own cflags and the final
        # link line -- not just have the pure function return the right
        # answer in isolation. Verified by recording calls, the same
        # style test_offscreen_graphics_never_reaches_the_darwin_gate
        # uses, since this needs no real toolchain state either.
        monkeypatch.setattr(sys, "platform", "win32")
        ensure_calls = []
        monkeypatch.setattr(
            cli_mod, "_ensure_runtime_object",
            lambda cc, name, source, pkgs: ensure_calls.append((name, pkgs)) or "/tmp/fake.o")
        monkeypatch.setattr(cli_mod, "_pkg_config", lambda action, pkg: [f"--{pkg}-{action.strip('-')}"])
        monkeypatch.setattr(cli_mod, "_sqlite_link_flags", lambda cc: ([], False))
        # windows.md Phase 3 (claude.md #129): _runtime_objects_and_link_libs
        # now also probes for a static winpthread on win32 -- stub it out
        # here too, same reason as every other toolchain call this test
        # already stubs.
        monkeypatch.setattr(cli_mod, "_can_link", lambda cc, flags: False)

        _, link_libs = cli_mod._runtime_objects_and_link_libs(
            "clang", uses_graphics=False, uses_audio=False)

        assert ensure_calls[0] == ("core", ["gnurx"])
        assert "--gnurx-libs" in link_libs


class TestLibllvmCandidatePaths:
    """llvm_backend's explicit per-platform libLLVM locations
    (macos.md/windows.md Phase 0): Homebrew's LLVM is keg-only and
    invisible to find_library; MSYS2's DLLs live under the active
    environment's bin (from $MSYSTEM_PREFIX) or the stock install
    roots. Linux needs no help, so its list is empty -- the gate that
    keeps this from perturbing the platform that already works."""

    def test_linux_adds_nothing(self, llvm_backend):
        assert llvm_backend._platform_libllvm_paths("linux", environ={}) == []

    def test_darwin_lists_both_brew_prefixes_arm64_first(self, llvm_backend):
        paths = llvm_backend._platform_libllvm_paths("darwin", environ={})
        assert paths == [
            "/opt/homebrew/opt/llvm/lib/libLLVM.dylib",
            "/usr/local/opt/llvm/lib/libLLVM.dylib",
        ]

    def test_windows_prefers_the_active_msys2_environment(self, llvm_backend):
        paths = llvm_backend._platform_libllvm_paths(
            "win32", environ={"MSYSTEM_PREFIX": r"D:\msys64\ucrt64"})
        assert paths[0] == os.path.join(r"D:\msys64\ucrt64", "bin", "libLLVM.dll")
        # ...falling back to the stock install roots after it.
        assert any(p.startswith(r"C:\msys64\ucrt64") for p in paths)

    def test_windows_tries_versioned_dlls_newest_first(self, llvm_backend):
        paths = llvm_backend._platform_libllvm_paths("win32", environ={})
        versioned = [p for p in paths if re.search(r"libLLVM-\d+\.dll$", p)]
        assert versioned, "versioned DLL names must be tried"
        first_versions = [int(re.search(r"-(\d+)\.dll$", p).group(1))
                          for p in versioned[:3]]
        assert first_versions == sorted(first_versions, reverse=True)

    def test_windows_without_msystem_still_has_candidates(self, llvm_backend):
        paths = llvm_backend._platform_libllvm_paths("win32", environ={})
        assert paths and all("\\msys64\\" in p for p in paths)


class TestFeatureGating:
    """macos.md Phase 0: a feature with no backend on this platform
    fails saying exactly that -- audio on darwin points at macos.md
    Phase 1 instead of telling a Mac user to apt-install ALSA. The
    same category drives the conftest skip, which is what lets the
    macOS CI job run the full suite and shed the audio tier as skips."""

    def test_audio_on_darwin_names_the_plan(self, cli_mod, errors):
        with pytest.raises(errors.CompileError) as excinfo:
            cli_mod._check_feature_supported("audio", "darwin")
        assert "macos.md Phase 1" in str(excinfo.value)
        assert excinfo.value.category == "unsupported platform feature"

    def test_audio_on_linux_is_not_gated(self, cli_mod):
        cli_mod._check_feature_supported("audio", "linux")

    def test_graphics_is_not_gated_on_linux(self, cli_mod):
        cli_mod._check_feature_supported("graphics", "linux")

    def test_audio_on_windows_names_the_plan(self, cli_mod, errors):
        # windows.md Phase 1 INVERTED this test a second time: the
        # waveOut backend now exists (built, CI-compiled), so this is
        # the same already-built-but-unverified gate as darwin's audio
        # branch above, not the "nothing built yet" shape graphics on
        # windows still uses below.
        with pytest.raises(errors.CompileError) as excinfo:
            cli_mod._check_feature_supported("audio", "win32")
        assert "windows.md Phase 1" in str(excinfo.value)
        assert excinfo.value.category == "unsupported platform feature"

    def test_the_windows_audio_gate_is_overridable_for_hardware_verification(
            self, cli_mod, errors, monkeypatch):
        monkeypatch.setenv("FESTINA_ENABLE_WINDOWS_AUDIO", "1")
        cli_mod._check_feature_supported("audio", "win32")   # no raise
        monkeypatch.delenv("FESTINA_ENABLE_WINDOWS_AUDIO")
        with pytest.raises(errors.CompileError):
            cli_mod._check_feature_supported("audio", "win32")

    def test_windowed_graphics_is_gated_on_windows(self, cli_mod, errors):
        # windows.md Phase 2 / claude.md #128 INVERTED this test the
        # same way claude.md #123 inverted the darwin one below: the
        # Win32 windowing seam + backend landed, and -- exactly like
        # every other gate in this class -- being BUILT and CI-compiled
        # is not the same claim as being verified against a real
        # window/mouse/keyboard, so windowed use stays gated until it
        # has been.
        with pytest.raises(errors.CompileError) as excinfo:
            cli_mod._check_feature_supported("graphics", "win32")
        assert "windows.md Phase 2" in str(excinfo.value)
        assert excinfo.value.category == "unsupported platform feature"

    def test_the_windows_graphics_gate_is_overridable(self, cli_mod, monkeypatch):
        monkeypatch.setenv("FESTINA_ENABLE_WINDOWS_GRAPHICS", "1")
        cli_mod._check_feature_supported("graphics", "win32")   # no raise

    def test_windowed_graphics_is_gated_on_darwin(self, cli_mod, errors):
        # claude.md #123 INVERTED this test: the windowing seam +
        # Cocoa backend landed, and -- exactly like #121's audio gate
        # -- being BUILT and CI-compiled is not the same claim as being
        # verified against a real window/mouse/keyboard, so windowed
        # use stays gated until it has been.
        with pytest.raises(errors.CompileError) as excinfo:
            cli_mod._check_feature_supported("graphics", "darwin")
        assert "macos.md Phase 2" in str(excinfo.value)
        assert excinfo.value.category == "unsupported platform feature"

    def test_the_darwin_graphics_gate_is_overridable(self, cli_mod, monkeypatch):
        monkeypatch.setenv("FESTINA_ENABLE_MACOS_GRAPHICS", "1")
        cli_mod._check_feature_supported("graphics", "darwin")   # no raise

    def test_doctor_on_darwin_reports_audio_as_planned_not_missing(
            self, cli_mod, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        lines, _, _missing = cli_mod._doctor_report()
        report = "\n".join(lines)
        assert "macos.md Phase 1" in report
        assert "alsa" not in report, (
            "doctor must not tell a Mac user to install ALSA")

    def test_doctor_on_windows_reports_graphics_and_audio_as_planned_not_missing(
            self, cli_mod, monkeypatch):
        # windows.md Phase 1 / Phase 2 (claude.md #128): both audio's
        # waveOut backend and graphics' Win32 backend are now built but
        # await real-hardware verification -- doctor must say so rather
        # than naming Linux-only packages (cairo-xlib, alsa) a Windows
        # user has no way to install.
        monkeypatch.setattr(sys, "platform", "win32")
        _stub_which_any(cli_mod, monkeypatch)
        lines, _, _missing = cli_mod._doctor_report()
        report = "\n".join(lines)
        assert "windows.md Phase 1" in report
        assert "windows.md Phase 2" in report
        assert "cairo-xlib" not in report and "alsa" not in report, (
            "doctor must not tell a Windows user to install Linux packages")

    def test_doctor_on_windows_reports_posix_regex_as_required(
            self, cli_mod, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        _stub_which_any(cli_mod, monkeypatch)
        monkeypatch.setattr(cli_mod, "_pkg_config_has", lambda pkg: False)
        lines, all_ok, _missing = cli_mod._doctor_report()
        report = "\n".join(lines)
        # The install hint names the real package (libsystre); the
        # pkg-config name it's actually queried under (gnurx) is an
        # implementation detail the report doesn't need to expose.
        assert "libsystre" in report
        assert "MISSING" in report
        assert all_ok is False

    def test_doctor_flags_the_plain_msys_shell_as_wrong(self, cli_mod, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        _stub_which_any(cli_mod, monkeypatch)
        monkeypatch.setenv("MSYSTEM", "MSYS")
        lines, _, _missing = cli_mod._doctor_report()
        assert "wrong shell" in "\n".join(lines)

    def test_doctor_says_nothing_extra_for_ucrt64(self, cli_mod, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        _stub_which_any(cli_mod, monkeypatch)
        monkeypatch.setenv("MSYSTEM", "UCRT64")
        lines, _, _missing = cli_mod._doctor_report()
        assert "wrong shell" not in "\n".join(lines)


class TestDetectPackageManager:
    """`festina doctor --fix`: which of the three package managers
    setup.md documents (apt/Debian-Ubuntu, Homebrew/macOS, MSYS2's
    pacman/Windows) it should drive -- one per platform, by design
    (see _PKG_MANAGER_PACKAGES' own docstring for why this doesn't try
    to guess for dnf/Arch's pacman/zypper/etc). Same sys.platform-spoof
    pattern as TestDoctor above, and the same reason _stub_which_any
    exists: shutil.which has its own real win32 branch that a spoofed
    platform string on real POSIX CI would otherwise crash into."""

    def test_linux_detects_apt(self, cli_mod, monkeypatch):
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(cli_mod.shutil, "which",
                             lambda cmd: "/usr/bin/apt" if cmd == "apt" else None)
        assert cli_mod._detect_package_manager() == "apt"

    def test_linux_falls_back_to_apt_get(self, cli_mod, monkeypatch):
        # Some Debian/Ubuntu images ship apt-get without the newer apt
        # frontend -- doctor --fix should still recognize the family.
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(cli_mod.shutil, "which",
                             lambda cmd: "/usr/bin/apt-get" if cmd == "apt-get" else None)
        assert cli_mod._detect_package_manager() == "apt"

    def test_linux_without_apt_is_unsupported(self, cli_mod, monkeypatch):
        # dnf/Arch pacman/zypper -- deliberately not guessed at, see
        # _PKG_MANAGER_PACKAGES' own docstring.
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(cli_mod.shutil, "which", lambda cmd: None)
        assert cli_mod._detect_package_manager() is None

    def test_darwin_detects_brew(self, cli_mod, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(cli_mod.shutil, "which",
                             lambda cmd: "/opt/homebrew/bin/brew" if cmd == "brew" else None)
        assert cli_mod._detect_package_manager() == "brew"

    def test_darwin_without_brew_is_unsupported(self, cli_mod, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setattr(cli_mod.shutil, "which", lambda cmd: None)
        assert cli_mod._detect_package_manager() is None

    def test_windows_detects_msys2_pacman(self, cli_mod, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(cli_mod.shutil, "which",
                             lambda cmd: "/usr/bin/pacman" if cmd == "pacman" else None)
        assert cli_mod._detect_package_manager() == "msys2"

    def test_windows_without_pacman_is_unsupported(self, cli_mod, monkeypatch):
        # Not running inside an MSYS2 shell at all.
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(cli_mod.shutil, "which", lambda cmd: None)
        assert cli_mod._detect_package_manager() is None


class TestDoctorFixInstallCommand:
    """_doctor_fix_install_command: the exact command line for each
    manager, given an already-deduplicated package list -- pure
    function, no subprocess involved, so every branch is checked
    directly rather than through a real (or even faked) install."""

    def test_apt_as_root_has_no_sudo_prefix(self, cli_mod, monkeypatch):
        monkeypatch.setattr(cli_mod.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")
        monkeypatch.setattr(cli_mod.os, "geteuid", lambda: 0, raising=False)
        cmd = cli_mod._doctor_fix_install_command("apt", ["clang", "pkg-config"])
        assert cmd == ["apt", "install", "-y", "clang", "pkg-config"]

    def test_apt_as_non_root_is_prefixed_with_sudo(self, cli_mod, monkeypatch):
        monkeypatch.setattr(cli_mod.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")
        monkeypatch.setattr(cli_mod.os, "geteuid", lambda: 1000, raising=False)
        cmd = cli_mod._doctor_fix_install_command("apt", ["clang"])
        assert cmd == ["sudo", "apt", "install", "-y", "clang"]

    def test_apt_falls_back_to_apt_get_when_apt_is_absent(self, cli_mod, monkeypatch):
        monkeypatch.setattr(cli_mod.shutil, "which",
                             lambda cmd: None if cmd == "apt" else f"/usr/bin/{cmd}")
        monkeypatch.setattr(cli_mod.os, "geteuid", lambda: 0, raising=False)
        cmd = cli_mod._doctor_fix_install_command("apt", ["clang"])
        assert cmd[0:2] == ["apt-get", "install"]

    def test_brew_never_gets_a_sudo_prefix(self, cli_mod, monkeypatch):
        # Homebrew actively refuses to run as root -- confirming this
        # never accidentally inherits apt's sudo logic.
        monkeypatch.setattr(cli_mod.os, "geteuid", lambda: 0, raising=False)
        cmd = cli_mod._doctor_fix_install_command("brew", ["sqlite", "cairo"])
        assert cmd == ["brew", "install", "sqlite", "cairo"]

    def test_msys2_uses_noconfirm_pacman(self, cli_mod):
        cmd = cli_mod._doctor_fix_install_command(
            "msys2", ["mingw-w64-ucrt-x86_64-clang"])
        assert cmd == ["pacman", "-S", "--noconfirm", "mingw-w64-ucrt-x86_64-clang"]


class TestAudioFeatureConfig:
    """claude.md #121: the audio feature's device half is per-platform
    -- ALSA via pkg-config on Linux, the AudioToolbox framework on
    darwin -- while libmpg123 and -pthread are shared. Pure function of
    the platform name, tested for all of them from any of them."""

    def test_linux_links_alsa_via_pkg_config(self, cli_mod):
        pkgs, flags = cli_mod._feature_pkgs_and_flags("audio", "linux")
        assert pkgs == ["alsa", "libmpg123"]
        assert flags == ["-pthread"]

    def test_darwin_swaps_alsa_for_the_audiotoolbox_framework(self, cli_mod):
        pkgs, flags = cli_mod._feature_pkgs_and_flags("audio", "darwin")
        assert pkgs == ["libmpg123"]
        assert flags == ["-pthread", "-framework", "AudioToolbox"]

    def test_windows_swaps_alsa_for_winmm(self, cli_mod):
        # windows.md Phase 1: winmm's waveOut is a system DLL with no
        # pkg-config file, same shape as AudioToolbox above -- `alsa`
        # drops out, `-lwinmm` comes in, libmpg123 and -pthread stay
        # (MSYS2 UCRT64 ships a real libmpg123 pkg-config package).
        pkgs, flags = cli_mod._feature_pkgs_and_flags("audio", "win32")
        assert pkgs == ["libmpg123"]
        assert flags == ["-pthread", "-lwinmm"]

    def test_darwin_graphics_swaps_cairo_xlib_for_plain_cairo(self, cli_mod):
        # claude.md #123 INVERTED this test: festina_runtime_graphics.c
        # has zero X11 code compiled into it on darwin (guarded
        # `#ifndef __APPLE__`), so it needs only Cairo's own core
        # package -- the xlib backend, and XQuartz along with it, are
        # no longer needed at all, not even for offscreen drawing.
        pkgs, flags = cli_mod._feature_pkgs_and_flags("graphics", "darwin")
        assert pkgs == ["libjpeg", "cairo"]
        assert flags == ["-framework", "Cocoa"]

    def test_linux_graphics_is_unchanged(self, cli_mod):
        pkgs, flags = cli_mod._feature_pkgs_and_flags("graphics", "linux")
        assert pkgs == ["cairo-xlib", "libjpeg"]
        assert flags == []

    def test_windows_graphics_swaps_cairo_xlib_for_plain_cairo(self, cli_mod):
        # windows.md Phase 2: same shape as the darwin test above --
        # festina_runtime_graphics.c has zero X11 code compiled into it
        # on win32 either (guarded `#if !defined(__APPLE__) &&
        # !defined(_WIN32)`), so `cairo-xlib` drops out for plain
        # `cairo`; -lgdi32/-luser32 are the Win32 windowing companion
        # object's own system-DLL link flags, the counterpart to
        # darwin's `-framework Cocoa`.
        pkgs, flags = cli_mod._feature_pkgs_and_flags("graphics", "win32")
        assert pkgs == ["libjpeg", "cairo"]
        assert flags == ["-lgdi32", "-luser32"]

    def test_windows_graphics_extra_object_is_the_win32_companion(self, cli_mod, monkeypatch):
        # windows.md Phase 2: the win32 counterpart to
        # test_the_darwin_window_backend_extra_object_gets_cairo_cflags
        # above -- festina_runtime_window_win32.c #includes <cairo.h>
        # for the same StretchDIBits blit reason.
        calls = []
        monkeypatch.setattr(
            cli_mod, "_ensure_runtime_object",
            lambda cc, name, source, pkgs: calls.append((name, pkgs)) or "/tmp/fake.o")
        cli_mod._feature_extra_object("clang", "graphics", "win32")
        assert calls == [("window_win32", ["cairo"])]

    def test_the_darwin_window_backend_extra_object_is_only_for_graphics(
            self, cli_mod):
        assert cli_mod._feature_extra_object("clang", "audio", "darwin") is None
        assert cli_mod._feature_extra_object("clang", "graphics", "linux") is None

    def test_the_darwin_window_backend_extra_object_gets_cairo_cflags(
            self, cli_mod, monkeypatch):
        # claude.md #126 round four: festina_runtime_window_mac.m
        # #includes <cairo.h> same as festina_runtime_graphics.c does,
        # but _feature_extra_object passed _ensure_runtime_object an
        # EMPTY pkg-config package list on every round before this one
        # -- the file never got cairo's -I cflags at all, regardless of
        # how the #include was spelled, and only real macOS CI (nothing
        # else compiles this file) could ever have caught it. Verified
        # here by recording the exact call rather than actually
        # compiling, which needs real macOS toolchain state this test
        # doesn't have on Linux.
        calls = []
        monkeypatch.setattr(
            cli_mod, "_ensure_runtime_object",
            lambda cc, name, source, pkgs: calls.append((name, pkgs)) or "/tmp/fake.o")
        cli_mod._feature_extra_object("clang", "graphics", "darwin")
        assert calls == [("window_mac", ["cairo"])]

    def test_offscreen_graphics_never_reaches_the_darwin_gate(
            self, cli_mod, monkeypatch):
        # claude.md #123: _runtime_objects_and_link_libs's own
        # wants_window parameter is the narrow question -- a program
        # that only draws to an offscreen canvas (uses_graphics_code,
        # not the real-window uses_graphics) must never hit
        # _check_feature_supported at all on darwin, since it never
        # touches the windowing seam and offscreen genuinely links
        # there (see _feature_extra_object). claude.md #126 round five:
        # this exemption is darwin-specific, NOT universal -- see
        # test_offscreen_graphics_still_reaches_the_windows_gate below
        # for the platform where it must NOT apply. Verified by
        # recording calls rather than actually linking (which needs
        # real toolchain state this test doesn't have on Linux).
        monkeypatch.setattr(sys, "platform", "darwin")
        calls = []
        monkeypatch.setattr(cli_mod, "_check_feature_supported",
                            lambda name, platform_name=None: calls.append(name))
        monkeypatch.setattr(cli_mod, "_ensure_runtime_object", lambda *a, **k: "/tmp/fake.o")
        monkeypatch.setattr(cli_mod, "_feature_extra_object", lambda *a, **k: None)
        monkeypatch.setattr(cli_mod, "_pkg_config", lambda *a, **k: [])
        monkeypatch.setattr(cli_mod, "_sqlite_link_flags", lambda cc: ([], False))

        cli_mod._runtime_objects_and_link_libs(
            "clang", uses_graphics=True, uses_audio=False, wants_window=False)
        assert calls == [], "an offscreen-only program must never hit the gate"

        cli_mod._runtime_objects_and_link_libs(
            "clang", uses_graphics=True, uses_audio=False, wants_window=True)
        assert calls == ["graphics"], "a windowed program must hit the gate"

    def test_offscreen_graphics_never_reaches_the_windows_gate_either(
            self, cli_mod, monkeypatch):
        # windows.md Phase 2 / claude.md #128 INVERTED this test a
        # second time. claude.md #126 round five (found by real Windows
        # CI) had made this the one platform-specific exception to the
        # darwin test above: at that point Windows had no window
        # backend at all -- no window_win32 companion object existed
        # the way window_mac.m did -- so festina_runtime_graphics.c's
        # unconditional references to _festina_window_open and friends
        # could never resolve at link time on win32, offscreen program
        # or not, and the gate had to fire even for wants_window=False
        # just to give a clean error instead of a confusing linker
        # failure. Now that festina_runtime_window_win32.c provides
        # those symbols for real, win32 needs no special case any
        # more -- offscreen use is exempt from the gate exactly like
        # every other platform, verified identically to the darwin
        # test just above.
        monkeypatch.setattr(sys, "platform", "win32")
        calls = []
        monkeypatch.setattr(cli_mod, "_check_feature_supported",
                            lambda name, platform_name=None: calls.append(name))
        monkeypatch.setattr(cli_mod, "_ensure_runtime_object", lambda *a, **k: "/tmp/fake.o")
        monkeypatch.setattr(cli_mod, "_feature_extra_object", lambda *a, **k: None)
        monkeypatch.setattr(cli_mod, "_pkg_config", lambda *a, **k: [])
        monkeypatch.setattr(cli_mod, "_sqlite_link_flags", lambda cc: ([], False))
        monkeypatch.setattr(cli_mod, "_can_link", lambda cc, flags: False)  # claude.md #129

        cli_mod._runtime_objects_and_link_libs(
            "clang", uses_graphics=True, uses_audio=False, wants_window=False)
        assert calls == [], "an offscreen-only program must never hit the gate on win32 either"

        cli_mod._runtime_objects_and_link_libs(
            "clang", uses_graphics=True, uses_audio=False, wants_window=True)
        assert calls == ["graphics"], "a windowed program must still hit the gate"

    def test_the_darwin_gate_is_overridable_for_hardware_verification(
            self, cli_mod, errors, monkeypatch):
        monkeypatch.setenv("FESTINA_ENABLE_MACOS_AUDIO", "1")
        cli_mod._check_feature_supported("audio", "darwin")   # no raise
        monkeypatch.delenv("FESTINA_ENABLE_MACOS_AUDIO")
        with pytest.raises(errors.CompileError):
            cli_mod._check_feature_supported("audio", "darwin")


class TestNullAudioDevice:
    """claude.md #121: FESTINA_AUDIO_NULL=1 turns the device seam into
    an instant sink -- the cross-platform replacement for the
    ALSA-only ~/.asoundrc null-plugin trick, and what lets audio
    end-to-end tests run on machines with no audio stack (macOS CI,
    containers). Verified here on Linux, where a real backend also
    exists, so the shim's behavior is pinned against the real one."""

    def _write_wav(self, path, seconds=0.3, rate=8000):
        import math
        import wave
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            n = int(seconds * rate)
            frames = bytearray()
            for i in range(n):
                v = int(3000 * math.sin(2 * math.pi * 440 * i / rate))
                frames += v.to_bytes(2, "little", signed=True)
            w.writeframes(bytes(frames))

    def test_play_stop_isplaying_work_with_no_audio_device(
            self, compile_and_run, tmp_path):
        wav = tmp_path / "beep.wav"
        self._write_wav(wav)
        source = f"""
        aud clip = '{wav}'
        int ch = clip.playLoop()
        log(ch >= 0)
        log(clip.isPlaying())
        clip.stop()
        log(clip.isPlaying())
        """
        result = compile_and_run(source, env={"FESTINA_AUDIO_NULL": "1"})
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["true", "true", "false"]


class TestKeyNameVocabulary:
    """runtime/festina_key_names.h -- the pinned artifact behind the
    plans' "key-name parity" requirement. The names are X11's keysym
    strings measured from XKeysymToString (not assumed: Page Up/Down
    really are "Prior"/"Next"), and the future Cocoa/Win32 layers map
    INTO this list. This test is what makes silently editing the
    vocabulary a visible act."""

    def _names(self):
        with open(_KEY_NAMES_H, encoding="utf-8") as f:
            source = f.read()
        return re.findall(r'X\("([^"]+)"\)', source)

    def test_the_vocabulary_parses_and_has_no_duplicates(self):
        names = self._names()
        assert names, "the X-macro list must yield names"
        assert len(names) == len(set(names))

    def test_the_core_named_keys_are_present(self):
        # The set a keyboard-driven game or text UI cannot live
        # without; each string is the exact spelling the X backend
        # already delivers to `on keyDown`.
        names = set(self._names())
        for required in ("Return", "Escape", "BackSpace", "Tab", "Delete",
                         "Left", "Right", "Up", "Down", "Home", "End",
                         "Shift_L", "Control_L", "Alt_L", "F1", "F12"):
            assert required in names, f"missing named key {required!r}"

    def test_the_x11_warts_are_pinned_not_prettified(self):
        # "Prior"/"Next" (not Page_Up/Page_Down) is what X programs
        # already match against -- renaming them would break every
        # existing keyboard handler, so the wart is the contract.
        names = set(self._names())
        assert "Prior" in names and "Next" in names
        assert "Page_Up" not in names and "Page_Down" not in names

    def test_printable_characters_are_not_in_the_named_list(self):
        # Tier 1 of the rule: printable keys arrive as their character,
        # so single printable characters must never appear as "names".
        for name in self._names():
            assert not (len(name) == 1 and name.isprintable()), (
                f"{name!r} is a printable character, which tier 1 of the "
                f"key rule already covers -- it must not be a named key")


class TestBinaryFidelity:
    """The program-level contract that makes the Windows port's CRLF
    question a non-question: every runtime fopen is binary-mode, so
    bytes round-trip exactly. Pinned here (and run on every platform's
    CI) rather than trusted, because a single future "w" would corrupt
    every blob on Windows while remaining invisible on Linux."""

    def test_crlf_and_nul_bytes_round_trip_through_a_blob(
            self, compile_and_run, tmp_path):
        # \r\n is what text-mode Windows I/O would mangle; the embedded
        # NUL is what strlen-based length handling would truncate.
        payload = tmp_path / "payload.bin"
        payload.write_bytes(b"line1\r\nline2\x00tail\r\n")
        out = tmp_path / "copy.bin"
        source = f"""
        blob data = '{payload}'
        log(data.saveCopy('{out}'))
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "true"
        assert out.read_bytes() == payload.read_bytes()

    def test_forward_slash_paths_are_the_portable_spelling(
            self, compile_and_run, tmp_path):
        # Every example writes paths with forward slashes; the C
        # runtime passes them to fopen verbatim, which Windows' CRT
        # accepts -- so THIS spelling, not backslashes, is the
        # documented portable one. The test pins that a nested
        # forward-slash path works through the blob machinery.
        nested = tmp_path / "a" / "b"
        nested.mkdir(parents=True)
        target = str(nested / "note.txt").replace(os.sep, "/")
        source = f"""
        blob f = '{target}'
        f.write('portable')
        log(f.toText())
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "portable"


@pytest.mark.skipif(sys.platform != "darwin", reason="runs on the macOS CI job")
class TestOnMacOS:
    """macos.md Phase 0's exit criteria, asserted on real darwin.
    Skipped everywhere else today; the macos-14 job makes them live."""

    def test_default_output_name_stays_extensionless(self, cli_mod):
        assert cli_mod._default_output_name("game.f") == "game"

    def test_the_static_attempt_never_uses_gnu_ld_toggles(self, cli_mod):
        flags = cli_mod._static_sqlite_attempt(
            sys.platform, ["-lsqlite3"], libdir="/tmp")
        assert flags is None or not any(f.startswith("-Wl,-B") for f in flags)

    def test_a_backend_exists_libllvm_or_clang(self, llvm_backend):
        import shutil
        assert llvm_backend.available() or shutil.which("clang"), (
            "neither libLLVM nor clang found -- `brew install llvm`")

    def test_hello_compiles_and_runs(self, compile_and_run):
        result = compile_and_run("log('hello from darwin')")
        assert result.returncode == 0
        assert result.stdout.strip() == "hello from darwin"


@pytest.mark.skipif(sys.platform != "win32", reason="runs on the Windows CI job")
class TestOnWindows:
    """windows.md Phase 0's exit criteria, asserted on real win32
    under MSYS2. Skipped everywhere else today; the windows-latest
    job makes them live."""

    def test_default_output_name_gains_exe(self, cli_mod):
        assert cli_mod._default_output_name("game.f") == "game.exe"

    def test_compile_writes_the_exe_it_names(self, cli_mod, tmp_path):
        src = tmp_path / "hello.f"
        src.write_text("log('hello from windows')")
        out = cli_mod.compile_file(str(src), str(tmp_path / "hello.exe"))
        assert os.path.exists(out)
        result = subprocess.run([out], capture_output=True, text=True, timeout=15)
        assert result.returncode == 0
        assert result.stdout.strip() == "hello from windows"

    def test_posix_regex_is_linked_and_answers(self, compile_and_run):
        # The one core-runtime gap windows.md names: <regex.h> via
        # libsystre (claude.md #126). The language surface must behave
        # identically regardless of which provider is behind it.
        result = compile_and_run("log(/[0-9]+/.test('v42'))")
        assert result.returncode == 0
        assert result.stdout.strip() == "true"

    def test_core_only_binary_has_no_msys2_runtime_dll_dependency(
            self, cli_mod, tmp_path):
        # windows.md Phase 3 item 2 (claude.md #129): pins the
        # "copy-anywhere" claim -static-libgcc/static winpthread make
        # for a core-only program against the real linked binary's own
        # import table -- the Windows analog of TestSlimBinaries's
        # ldd-based checks on Linux, using objdump -p (windows.md's own
        # named tool: `objdump -p | grep 'DLL Name'`) since ldd itself
        # isn't a MinGW/Windows concept.
        src = tmp_path / "hello.f"
        src.write_text("log('hello from windows')")
        out = cli_mod.compile_file(str(src), str(tmp_path / "hello.exe"))
        result = subprocess.run(["objdump", "-p", out], capture_output=True,
                                text=True, timeout=15)
        dll_names = "\n".join(
            line for line in result.stdout.splitlines() if "DLL Name" in line)
        assert "libgcc" not in dll_names.lower()
        assert "libwinpthread" not in dll_names.lower()
