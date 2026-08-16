"""Regression coverage for examples/*.f -- these are the programs
README.md and api.md point readers at, so they need to keep working the
same way any other documented behavior does (this repo's own stated
convention: every behavior gets a test, every doc stays accurate).
Interactive/graphics examples that need a real (or virtual) X server are
covered in test_codegen.py's TestGraphics/TestTimers area instead, next
to the module-level Xvfb helpers those already use -- see
TestExampleGraphics/TestExampleTicTacToe there.
"""
import glob
import os
import subprocess

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLES_DIR = os.path.join(REPO_ROOT, "examples")

# claude.md #37/#39's graphics and #38's audio both need an environment
# this parametrized "just compiles" sweep doesn't set up (a display,
# an ALSA device) -- graphics.f/tic_tac_toe.f get their own dedicated,
# fully-interactive coverage in test_codegen.py instead (see this
# module's docstring); audio.f is exercised below via audio_null_env,
# same as tests/test_audio.py's own coverage.
ALL_EXAMPLES = sorted(
    os.path.basename(p) for p in glob.glob(os.path.join(EXAMPLES_DIR, "*.f"))
)


class TestAllExamplesCompile:
    """Every file in examples/ should at least compile successfully --
    catches an example silently rotting out of sync with a language/
    runtime change even if nothing else here happens to exercise it."""

    @pytest.mark.parametrize("filename", ALL_EXAMPLES)
    def test_compiles(self, cli_mod, tmp_path, filename):
        from tests.conftest import _require_c_compiler
        cc = _require_c_compiler()
        src = os.path.join(EXAMPLES_DIR, filename)
        out = tmp_path / "program"
        cli_mod.compile_file(src, str(out), cc=cc)
        assert out.exists()


def _run_example(cli_mod, tmp_path, filename, cwd=None, env=None, timeout=15):
    from tests.conftest import _require_c_compiler
    cc = _require_c_compiler()
    src = os.path.join(EXAMPLES_DIR, filename)
    out = tmp_path / "program"
    cli_mod.compile_file(src, str(out), cc=cc)
    run_env = dict(os.environ, **env) if env else None
    return subprocess.run(
        [str(out)], cwd=cwd or REPO_ROOT, capture_output=True, text=True,
        timeout=timeout, env=run_env,
    )


class TestIndividualExamples:
    """Beyond "it compiles" (above), these check the actual output for
    the examples with simple, deterministic behavior and no extra
    runtime dependencies (no display, no audio device)."""

    def test_greet_matches_readmes_introduction(self, cli_mod, tmp_path):
        # README.md's own hero example -- literally the same source, so
        # this test also guards against the README and the shipped
        # example drifting apart.
        result = _run_example(cli_mod, tmp_path, "greet.f")
        assert result.stdout == "Hello, Festina!\n"

    def test_hello_tour_runs_end_to_end(self, cli_mod, tmp_path):
        result = _run_example(cli_mod, tmp_path, "hello.f", cwd=tmp_path)
        lines = result.stdout.splitlines()
        assert lines[0] == "visit 1: the summit"
        assert lines[1] == "destination is 7 steps away"
        assert lines[2] == "that is a long walk"  # manhattan distance 7 > 5

    def test_fizzbuzz_matches_the_classic_output(self, cli_mod, tmp_path):
        result = _run_example(cli_mod, tmp_path, "fizzbuzz.f")
        lines = result.stdout.splitlines()
        assert lines[:5] == ["1", "2", "Fizz", "4", "Buzz"]
        assert lines[14] == "FizzBuzz"  # i == 15
        assert lines[-1] == "FizzBuzz"  # i == 30

    def test_arrays_demonstrates_indexing_and_length(self, cli_mod, tmp_path):
        result = _run_example(cli_mod, tmp_path, "arrays.f")
        lines = result.stdout.splitlines()
        assert lines[0] == "259"   # sum3([88, 92, 79])
        assert lines[1] == "100"   # scores[1] after scores[1] = 100
        assert lines[2] == "4"     # grid[1][1]
        assert lines[3:] == ["88", "100", "79"]  # the for/.length loop

    def test_basic_runs_and_greets(self, cli_mod, tmp_path):
        result = _run_example(cli_mod, tmp_path, "basic.f", cwd=tmp_path)
        assert result.stdout == "Hello, Festina!\n"

    def test_multifile_resolves_its_import(self, cli_mod, tmp_path):
        result = _run_example(cli_mod, tmp_path, "multifile.f")
        assert result.stdout.splitlines() == ["(0, 0)", "(3, 4)"]

    def test_regex_demo_runs_correctly(self, cli_mod, tmp_path):
        result = _run_example(cli_mod, tmp_path, "regex.f")
        assert result.stdout.splitlines() == [
            "true",        # digits.test('room 42')
            "false",       # digits.test('no numbers here')
            "42",          # 'room 42, building 7'.match(digits)
            "suite 42",    # 'room 42'.replace('room', 'suite')
            "a-b-c-",      # 'a1b2c3'.replaceAll(/[0-9]/, '-')
            "true",        # /^hello$/i.test('HELLO')
            "true",        # /\w+/gi.test('Hello World')
            "true",        # regex(userPattern).test('suite 42')
        ]

    def test_timers_demo_runs_and_exits_on_its_own(self, cli_mod, tmp_path):
        # Takes a little over 1s (see the source's own comment) -- the
        # default 15s timeout is generous headroom, not something this
        # is expected to approach.
        result = _run_example(cli_mod, tmp_path, "timers.f")
        lines = result.stdout.splitlines()
        assert lines[0] == "scheduling a one-shot timeout and a repeating interval..."
        assert lines[1:6] == [f"tick {i}" for i in range(1, 6)]
        assert lines[6] == "stopping the interval"
        assert lines[7] == "one second has passed"

    def test_audio_demo_plays_through_the_null_alsa_device(self, cli_mod, tmp_path, audio_null_env):
        result = _run_example(cli_mod, tmp_path, "audio.f", env=audio_null_env)
        lines = result.stdout.splitlines()
        assert lines[0] == "playing..."
        assert lines[1] == "isPlaying(): true"
        # claude.md #98: the demo now also shows the channel pool, so
        # two more play() calls layer on top of the first instead of
        # restarting it.
        assert lines[2] == "pooled channels: 10"
        # claude.md #99: a reserved, looping channel, released by name.
        assert lines[3] == "looping on channel 0: true"
        assert lines[4] == "after stopAudioPlayer(0): false"
        assert lines[5].startswith("isPlaying() after 100ms: ")
        assert lines[6] == "stopping early"
        # stop() names the clip, so every channel playing it ends.
        assert lines[7] == "isPlaying() after stop(): false"

    def test_maps_demo_runs_correctly(self, cli_mod, tmp_path):
        result = _run_example(cli_mod, tmp_path, "maps.f")
        lines = result.stdout.splitlines()
        assert lines[:4] == ["10", "15", "jim", "john"]
        # A missing key on an int-valued map logs the same int-null
        # sentinel a plain `int a = 1 / 0` would (claude.md #57) -- not
        # a special maps-only encoding.
        assert lines[4] == "-9223372036854775808"
        assert lines[5:7] == ["30", "5"]
        # forEach's visit order isn't specified (claude.md #72) -- sort
        # before comparing.
        assert sorted(lines[7:]) == ["npc1: 30", "npc2: 15", "npc3: 5"]

    def test_config_demo_uses_the_default_database_without_the_env_var(self, cli_mod, tmp_path, monkeypatch):
        monkeypatch.delenv("FESTINA_DB_PATH", raising=False)
        result = _run_example(cli_mod, tmp_path, "config.f", cwd=tmp_path)
        assert result.stdout.splitlines() == [
            "visit 1: the summit",
            "API_KEY is not set (that is fine -- this is just a demo)",
        ]
        assert (tmp_path / "festina.sqlite").exists()

    def test_config_demo_honors_the_database_url_env_var(self, cli_mod, tmp_path):
        result = _run_example(cli_mod, tmp_path, "config.f", cwd=tmp_path,
                               env={"FESTINA_DB_PATH": "custom_config.sqlite"})
        assert result.returncode == 0
        assert (tmp_path / "custom_config.sqlite").exists()
        assert not (tmp_path / "festina.sqlite").exists()
