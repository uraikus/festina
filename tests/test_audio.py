"""claude.md #38/#99/#100: aud, `aud m = 'path'`, .play()/
.playLoop()/.isPlaying(), and stopAudioPlayer().

Lexer/parser/semantic-level tests only -- see tests/test_codegen.py's
TestAudio for the real compile-and-run end-to-end coverage, including
tests that actually play audio through a real (virtual) ALSA device.
"""
import pytest


class TestAudioType:
    """claude.md #38: aud, loadAudio()."""

    def test_aud_declaration_parses(self, parser):
        parser.parse("aud music = 'music.wav'")

    def test_aud_is_a_valid_type(self, parser, semantic):
        program = parser.parse("aud music = 'music.wav'")
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
        source = "aud music = 'music.wav'\nmusic.play()"
        program = parser.parse(source)
        semantic.analyze(program)

    def test_stop_is_gone_and_says_what_to_use_instead(self, parser, semantic, errors):
        # claude.md #100: one clip can be playing on several channels at
        # once, so "stop this clip" never named one thing.
        source = "aud music = 'music.wav'\nmusic.stop()"
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="stopAudioPlayer"):
            semantic.analyze(program)

    def test_a_path_declares_an_aud_directly(self, parser, semantic):
        # claude.md #100: the same one-directional text -> X allowance
        # blob/color/font already have.
        program = parser.parse("aud music = 'music.wav'\nmusic.play()")
        semantic.analyze(program)

    def test_a_path_may_be_any_text_expression(self, parser, semantic):
        # Unlike color/font this is not resolved at compile time, so it
        # is not restricted to a literal.
        program = parser.parse("text dir = 'sounds/'\naud music = dir + 'x.wav'")
        semantic.analyze(program)

    def test_a_non_text_initializer_is_still_rejected(self, parser, semantic, errors):
        program = parser.parse("aud music = 5")
        with pytest.raises(errors.CompileError, match="cannot assign"):
            semantic.analyze(program)

    def test_is_playing_returns_bool(self, parser, semantic):
        source = "aud music = 'music.wav'\nbool playing = music.isPlaying()"
        program = parser.parse(source)
        semantic.analyze(program)

    @pytest.mark.parametrize("method", ["isPlaying"])
    def test_takes_no_arguments(self, parser, semantic, errors, method):
        # claude.md #99/#100: play/playLoop take an optional channel and
        # stop() is gone, so isPlaying is the only argument-free method
        # left. It stays clip-wide: "is this sound audible anywhere" has
        # a single answer even when several channels are playing it.
        source = f"aud music = 'music.wav'\nmusic.{method}(1)"
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="no arguments"):
            semantic.analyze(program)

    @pytest.mark.parametrize("method", ["play", "playLoop"])
    def test_play_takes_an_optional_int_channel(self, parser, semantic, method):
        # claude.md #99.
        source = (f"aud music = 'music.wav'\n"
                  f"music.{method}()\nmusic.{method}(2)")
        program = parser.parse(source)
        semantic.analyze(program)

    @pytest.mark.parametrize("method", ["play", "playLoop"])
    def test_play_rejects_too_many_arguments(self, parser, semantic, errors, method):
        source = f"aud music = 'music.wav'\nmusic.{method}(1, 2)"
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="0 or 1 argument"):
            semantic.analyze(program)

    @pytest.mark.parametrize("method", ["play", "playLoop"])
    def test_play_rejects_a_non_int_channel(self, parser, semantic, errors, method):
        source = f"aud music = 'music.wav'\nmusic.{method}('two')"
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="channel must be int"):
            semantic.analyze(program)

    def test_stop_audio_player_takes_an_optional_int_channel(self, parser, semantic):
        # claude.md #99: a free function, not a method -- channels are
        # process-global, so there is no clip to hang it off.
        program = parser.parse("stopAudioPlayer()\nstopAudioPlayer(0)")
        semantic.analyze(program)

    def test_stop_audio_player_rejects_a_bad_call(self, parser, semantic, errors):
        for source in ["stopAudioPlayer(1, 2)", "stopAudioPlayer('a')"]:
            program = parser.parse(source)
            with pytest.raises(errors.CompileError):
                semantic.analyze(program)

    def test_unrecognized_method_is_a_compile_error(self, parser, semantic, errors):
        source = "aud music = 'music.wav'\nmusic.pause()"
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="pause"):
            semantic.analyze(program)

    def test_play_on_a_non_audio_receiver_is_a_compile_error(self, parser, semantic, errors):
        program = parser.parse("int x = 5\nx.play()")
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)

    def test_is_playing_result_must_match_declared_type(self, parser, semantic, errors):
        source = "aud music = 'music.wav'\nint p = music.isPlaying()"
        program = parser.parse(source)
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)
