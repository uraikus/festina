"""claude.md #148 / wasm.md: WASM export.

Real compile-and-run coverage lives here, via the compile_and_run_wasm
fixture (tests/conftest.py) -- it skips cleanly on a machine without a
working wasm32-wasi clang or Node on PATH, the same "opt-in,
environment-dependent" tier as compile_and_run's own C-compiler skip
and x_display's Xvfb skip, rather than failing outright. The pure
functions (_default_output_name's .wasm branch, _check_wasm_feature_supported)
are unit-tested unconditionally, same as test_platform.py's own
win32/darwin branches -- no toolchain needed to exercise those.
"""
import os

import pytest


class TestDefaultOutputNameWasm:
    """claude.md #148: a wasm32-wasi build always gets .wasm, regardless
    of what platform_name says the HOST is -- the host doing the
    compiling has no bearing on the output format."""

    def test_wasm_target_gets_wasm_extension(self, cli_mod):
        assert cli_mod._default_output_name("game.f", "linux", target="wasm32-wasi") == "game.wasm"

    def test_wasm_target_overrides_win32_exe(self, cli_mod):
        # Not "game.wasm.exe" -- .wasm wins outright, platform_name is
        # irrelevant once target is wasm32-wasi.
        assert cli_mod._default_output_name("game.f", "win32", target="wasm32-wasi") == "game.wasm"

    def test_wasm_target_does_not_double_an_existing_wasm_suffix(self, cli_mod):
        assert cli_mod._default_output_name("tool.wasm.f", target="wasm32-wasi") == "tool.wasm"

    def test_native_target_is_unaffected(self, cli_mod):
        assert cli_mod._default_output_name("game.f", "linux", target="native") == "game"
        assert cli_mod._default_output_name("game.f", "win32", target="native") == "game.exe"


class TestCheckWasmFeatureSupported:
    """No env-var escape hatch, unlike macOS/Windows' own gates -- there
    is no WASI graphics/audio backend to ever turn on."""

    def test_graphics_is_rejected(self, cli_mod):
        with pytest.raises(cli_mod.CompileError) as exc_info:
            cli_mod._check_wasm_feature_supported("graphics")
        assert exc_info.value.category == "unsupported platform feature"
        assert "graphics" in str(exc_info.value)
        assert "wasm.md" in str(exc_info.value)

    def test_audio_is_rejected(self, cli_mod):
        with pytest.raises(cli_mod.CompileError) as exc_info:
            cli_mod._check_wasm_feature_supported("audio")
        assert exc_info.value.category == "unsupported platform feature"
        assert "audio" in str(exc_info.value)
        assert "wasm.md" in str(exc_info.value)

    def test_unknown_feature_is_a_silent_no_op(self, cli_mod):
        # Mirrors _check_feature_supported's own contract: only graphics/
        # audio are gated at all, so an unrecognized name just falls
        # through rather than raising a confusing error about itself.
        cli_mod._check_wasm_feature_supported("something_else")


class TestWasmRun:
    """End-to-end: real compiles, real Node WASI execution. Exercises
    the parts of the core language a wasm32-wasi build actually needs
    to get right that native doesn't: the 32-bit calloc/malloc ABI
    (arrays/maps/structs on the heap) and the __main_argc_argv entry
    bridge (every one of these programs needs to reach main() at all --
    see runtime/festina_runtime_wasm_entry.ll's own top comment for why
    that bridge is raw LLVM IR, not C)."""

    def test_hello_world(self, compile_and_run_wasm):
        result = compile_and_run_wasm("log('hello from wasm')")
        assert result.returncode == 0
        assert result.stdout.strip() == "hello from wasm"

    def test_arithmetic_and_control_flow(self, compile_and_run_wasm):
        result = compile_and_run_wasm(
            "int func fib(n:int) {\n"
            "    if n < 2 { return n }\n"
            "    return fib(n - 1) + fib(n - 2)\n"
            "}\n"
            "log(fib(10))\n"
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "55"

    def test_heap_allocated_containers(self, compile_and_run_wasm):
        # arr/map both go through the codegen's calloc/malloc helpers
        # (_emit_calloc/_emit_malloc) that needed the 32-bit size_t fix
        # -- this is the regression test for that fix actually
        # executing correctly, not just linking cleanly.
        result = compile_and_run_wasm(
            "arr[int] xs = [1, 2, 3, 4, 5]\n"
            "int total = 0\n"
            "for int i = 0, i < xs.length, i++ { total = total + xs[i] }\n"
            "log(total)\n"
            "map[int] m = {}\n"
            "m['a'] = 1\n"
            "m['b'] = 2\n"
            "log(m['a'] + m['b'])\n"
        )
        assert result.returncode == 0
        assert result.stdout.strip().splitlines() == ["15", "3"]

    def test_structs(self, compile_and_run_wasm):
        result = compile_and_run_wasm(
            "struct Point { x:int  y:int }\n"
            "Point p\n"
            "p.x = 3\n"
            "p.y = 4\n"
            "log(p.x + p.y)\n"
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "7"

    def test_sqlite_is_available(self, compile_and_run_wasm):
        # The unconditional core dependency this whole feature hinges
        # on (runtime/wasm/README.md) -- table/sqlite() against the
        # vendored amalgamation, compiled for wasm32-wasi.
        result = compile_and_run_wasm(
            "table People { id:int, name:text }\n"
            "sqlite(\"INSERT INTO People (name) VALUES (?)\", ['Ada'])\n"
            "arr[People] rows = sqlite('SELECT * FROM People')\n"
            "log(rows[0].name)\n"
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "Ada"

    def test_regex_is_available(self, compile_and_run_wasm):
        # windows.md Phase 0's own "regex is unconditional core, not a
        # feature tier" fact applies just as much to wasm -- <regex.h>
        # comes from wasi-libc the same way it comes from MinGW.
        result = compile_and_run_wasm(
            "regex r = /(\\w+)@(\\w+)/\n"
            "log(r.test('foo@bar'))\n"
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "true"

    def test_text_and_string_concat(self, compile_and_run_wasm):
        result = compile_and_run_wasm(
            "text name = 'World'\n"
            "log('Hello, ' + name + '!')\n"
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "Hello, World!"

    def test_exit_code_propagates(self, compile_and_run_wasm):
        # run_wasi.mjs's own contract: wasi.start()'s return code becomes
        # this process's own exit code, the same as a native binary's.
        result = compile_and_run_wasm("close(7)")
        assert result.returncode == 7

    def test_argv_is_populated_from_the_real_wasi_argc_argv(self, compile_and_run_wasm):
        # claude.md #150: the whole point of the __main_argc_argv bridge
        # (see this class's own docstring) -- main()'s real (i32, ptr)
        # parameters, filled in by wasi-libc's own _start before
        # __festina_main() runs, get turned into @argv the same way
        # they do natively. run_wasi.mjs hardcodes WASI's own `args` to
        # `[wasmPath]` (see compile_and_run_wasm's own docstring), so
        # there's exactly one element to check here, not the module
        # path's own exact text (that's an implementation detail of
        # where tmp_path puts the compiled .wasm).
        result = compile_and_run_wasm(
            "log(argv.length)\n"
            "log(argv[0] != null && argv[0] != '')\n"
        )
        assert result.returncode == 0
        assert result.stdout.strip().splitlines() == ["1", "true"]

    def test_to_int_and_text_indexing_work_under_wasm(self, compile_and_run_wasm):
        # Both new features are ordinary core codegen (no wasm-specific
        # branch at all, unlike exec/argv) -- this is here mainly to
        # confirm the wasi-libc <string.h>/UTF-8 walking in
        # festina_text_to_int/festina_text_char_at links and behaves
        # identically to native, not because either needed its own
        # wasm-specific implementation.
        result = compile_and_run_wasm(
            "log('42abc'.toInt())\n"
            "log('nope'.toInt() == null)\n"
            "text s = 'hello'\n"
            "log(s[1])\n"
            "log(s[100] == null)\n"
        )
        assert result.returncode == 0
        assert result.stdout.strip().splitlines() == ["42", "true", "e", "true"]


class TestWasmGraphicsAudioRejection:
    """The graphics/audio check (_check_wasm_feature_supported) fires
    before _compile_via_wasm ever looks at `cc` at all -- so unlike
    TestWasmRun above, these need no real wasm32-wasi toolchain
    installed to exercise; a doomed compile is rejected outright,
    before doing any of the real work that toolchain would be needed
    for (see _compile_via_wasm's own docstring)."""

    def test_graphics_is_rejected_at_compile_time(self, cli_mod, tmp_path):
        src = tmp_path / "main.f"
        src.write_text("drawRect(0, 0, 10, 10)", encoding="utf-8")
        with pytest.raises(cli_mod.CompileError) as exc_info:
            cli_mod.compile_file(str(src), str(tmp_path / "out.wasm"),
                                  cc="clang", target="wasm32-wasi")
        assert exc_info.value.category == "unsupported platform feature"
        assert "graphics" in str(exc_info.value)

    def test_audio_is_rejected_at_compile_time(self, cli_mod, tmp_path):
        src = tmp_path / "main.f"
        src.write_text("aud clip = 'sound.wav'\nclip.play()", encoding="utf-8")
        with pytest.raises(cli_mod.CompileError) as exc_info:
            cli_mod.compile_file(str(src), str(tmp_path / "out.wasm"),
                                  cc="clang", target="wasm32-wasi")
        assert exc_info.value.category == "unsupported platform feature"
        assert "audio" in str(exc_info.value)

    def test_exec_is_rejected_at_compile_time(self, cli_mod, tmp_path):
        # claude.md #150: exec() gated the same way graphics/audio are
        # -- WASI has no process model to spawn into at all, so this is
        # rejected outright rather than compiling something that could
        # never work at runtime.
        src = tmp_path / "main.f"
        src.write_text("arr[text] cmd = ['ls']\nexec(cmd)", encoding="utf-8")
        with pytest.raises(cli_mod.CompileError) as exc_info:
            cli_mod.compile_file(str(src), str(tmp_path / "out.wasm"),
                                  cc="clang", target="wasm32-wasi")
        assert exc_info.value.category == "unsupported platform feature"
        assert "exec" in str(exc_info.value)


class TestWasmCliValidation:
    """cli.py's own guardrails around the wasm target -- checked without
    needing a real wasm32-wasi toolchain, since these are argument-
    validation paths, not compiles."""

    def test_non_clang_cc_is_rejected(self, cli_mod, tmp_path):
        src = tmp_path / "main.f"
        src.write_text("log('hi')", encoding="utf-8")
        with pytest.raises(cli_mod.CompileError) as exc_info:
            cli_mod.compile_file(str(src), str(tmp_path / "out.wasm"),
                                  cc="gcc", target="wasm32-wasi")
        assert "clang" in str(exc_info.value)

    def test_missing_cc_is_rejected(self, cli_mod, tmp_path):
        src = tmp_path / "main.f"
        src.write_text("log('hi')", encoding="utf-8")
        with pytest.raises(cli_mod.CompileError):
            cli_mod.compile_file(str(src), str(tmp_path / "out.wasm"),
                                  cc="definitely-not-a-real-compiler", target="wasm32-wasi")


class TestWasmDoctorCheck:
    """festina doctor's own WASM tier (claude.md #148): optional, like
    graphics/audio, and probed with a real clang invocation rather than
    a guessed install path."""

    def test_wasm_check_appears_in_the_report(self, cli_mod):
        lines, _all_ok, _missing = cli_mod._doctor_report()
        assert any("WASM export" in line for line in lines)

    def test_missing_wasm_toolchain_is_optional_not_required(self, cli_mod, monkeypatch):
        # A machine with no wasm32-wasi support at all must not fail
        # doctor's own required-dependency gate -- same contract as
        # cairo/alsa above it.
        monkeypatch.setattr(cli_mod, "_wasm_toolchain_ok", lambda cc: False)
        lines, all_ok, missing = cli_mod._doctor_report()
        assert all_ok  # sqlite3/pkg-config/cc are the only required checks
        assert ("wasm", False) in missing
        assert any("missing, optional" in line and "WASM" in line for line in lines)
