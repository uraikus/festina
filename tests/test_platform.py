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

    def test_audio_on_linux_and_windows_is_not_gated(self, cli_mod):
        cli_mod._check_feature_supported("audio", "linux")
        cli_mod._check_feature_supported("audio", "win32")

    def test_graphics_is_not_gated_on_linux_or_windows(self, cli_mod):
        for platform_name in ("linux", "win32"):
            cli_mod._check_feature_supported("graphics", platform_name)

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
        lines, _ = cli_mod._doctor_report()
        report = "\n".join(lines)
        assert "macos.md Phase 1" in report
        assert "alsa" not in report, (
            "doctor must not tell a Mac user to install ALSA")


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

    def test_the_darwin_window_backend_extra_object_is_only_for_graphics(
            self, cli_mod):
        assert cli_mod._feature_extra_object("clang", "audio", "darwin") is None
        assert cli_mod._feature_extra_object("clang", "graphics", "linux") is None

    def test_offscreen_graphics_never_reaches_the_darwin_gate(
            self, cli_mod, monkeypatch):
        # claude.md #123: _runtime_objects_and_link_libs's own
        # wants_window parameter is the narrow question -- a program
        # that only draws to an offscreen canvas (uses_graphics_code,
        # not the real-window uses_graphics) must never hit
        # _check_feature_supported at all, on darwin or anywhere else,
        # since it never touches the windowing seam. Verified by
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
        # libgnurx or vendored musl. Whichever answer landed, the
        # language surface must behave identically.
        result = compile_and_run("log(/[0-9]+/.test('v42'))")
        assert result.returncode == 0
        assert result.stdout.strip() == "true"
