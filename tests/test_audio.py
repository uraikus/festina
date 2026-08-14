"""claude.md #38: aud, loadAudio(), .play()/.stop()/.isPlaying().

Lexer/parser/semantic-level tests only -- see tests/test_codegen.py's
TestAudio for the real compile-and-run end-to-end coverage, including
tests that actually play audio through a real (virtual) ALSA device.
"""
import pytest


class TestAudioType:
    """claude.md #38: aud, loadAudio()."""

    def test_aud_declaration_parses(self, parser):
        parser.parse("aud music = loadAudio('music.wav')")

    def test_aud_is_a_valid_type(self, parser, semantic):
        program = parser.parse("aud music = loadAudio('music.wav')")
        semantic.analyze(program)

    def test_load_audio_wrong_argument_count_is_a_compile_error(self, parser, semantic, errors):
        program = parser.parse("aud music = loadAudio()")
        with pytest.raises(errors.CompileError, match="loadAudio"):
            semantic.analyze(program)

    def test_load_audio_non_text_argument_is_a_compile_error(self, parser, semantic, errors):
        program = parser.parse("aud music = loadAudio(5)")
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)


class TestAudioMethods:
    """claude.md #38: "Supported methods: music.play() music.stop()
    music.isPlaying()" -- claude.md enumerates exactly these three, so
    (unlike log()/fail()/sqlite()'s deliberately open shape) any other
    method call on an aud value is a compile error, not silently
    permissive."""

    def test_play_parses_and_analyzes(self, parser, semantic):
        source = "aud music = loadAudio('music.wav')\nmusic.play()"
        program = parser.parse(source)
        semantic.analyze(program)

    def test_stop_parses_and_analyzes(self, parser, semantic):
        source = "aud music = loadAudio('music.wav')\nmusic.stop()"
        program = parser.parse(source)
        semantic.analyze(program)

    def test_is_playing_returns_bool(self, parser, semantic):
        source = "aud music = loadAudio('music.wav')\nbool playing = music.isPlaying()"
        program = parser.parse(source)
        semantic.analyze(program)

    @pytest.mark.parametrize("method", ["play", "stop", "isPlaying"])
    def test_takes_no_arguments(self, parser, semantic, errors, method):
        source = f"aud music = loadAudio('music.wav')\nmusic.{method}(1)"
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="no arguments"):
            semantic.analyze(program)

    def test_unrecognized_method_is_a_compile_error(self, parser, semantic, errors):
        source = "aud music = loadAudio('music.wav')\nmusic.pause()"
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="pause"):
            semantic.analyze(program)

    def test_play_on_a_non_audio_receiver_is_a_compile_error(self, parser, semantic, errors):
        program = parser.parse("int x = 5\nx.play()")
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)

    def test_is_playing_result_must_match_declared_type(self, parser, semantic, errors):
        source = "aud music = loadAudio('music.wav')\nint p = music.isPlaying()"
        program = parser.parse(source)
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)
