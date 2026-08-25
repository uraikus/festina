"""claude.md #38/#99/#100/#146: aud, `aud m = 'path'`, .play()/
.playLoop()/.isPlaying(), stopAudioPlayer(), and
isAudioPlayerPlaying(channel).

Lexer/parser/semantic-level tests only -- see tests/test_codegen.py's
TestAudio for the real compile-and-run end-to-end coverage, including
tests that actually play audio through a real (virtual) ALSA device.
"""
import pytest


class TestAudioType:
    """claude.md #38: aud, declared from a path (claude.md #109 removed
    loadAudio(), leaving the path form as the only spelling)."""

    def test_aud_declaration_parses(self, parser):
        parser.parse("aud music = 'music.wav'")

    def test_aud_is_a_valid_type(self, parser, semantic):
        program = parser.parse("aud music = 'music.wav'")
        semantic.analyze(program)

    def test_load_audio_is_gone_and_says_what_to_use_instead(
            self, parser, semantic, errors):
        # claude.md #109: removed rather than aliased.
        program = parser.parse("aud music = loadAudio('music.wav')")
        with pytest.raises(errors.CompileError, match="loadAudio"):
            semantic.analyze(program)

    def test_the_load_audio_error_shows_the_path_form(self, parser, semantic, errors):
        program = parser.parse("aud music = loadAudio('music.wav')")
        with pytest.raises(errors.CompileError) as excinfo:
            semantic.analyze(program)
        assert "aud music = 'music.mp3'" in str(excinfo.value)


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

    def test_stop_is_back_and_takes_no_arguments(self, parser, semantic):
        # claude.md #109: stop() returned, meaning the thing claude.md
        # #100 named as its only honest reading -- stop every channel
        # playing this clip. #100 removed it because that is rarely what
        # an overlapping-effects program wants; #109 covers that case by
        # having play() return its channel, so the two coexist.
        source = "aud music = 'music.wav'\nmusic.stop()"
        program = parser.parse(source)
        semantic.analyze(program)

    def test_stop_rejects_a_channel_argument(self, parser, semantic, errors):
        # It names the CLIP, not a channel. stopAudioPlayer(n) is how
        # one channel is addressed, and the error says so.
        source = "aud music = 'music.wav'\nmusic.stop(0)"
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

    @pytest.mark.parametrize("method", ["isPlaying", "stop"])
    def test_takes_no_arguments(self, parser, semantic, errors, method):
        # claude.md #99/#109: play/playLoop take an optional channel;
        # isPlaying and stop take none. Both are clip-wide, which is the
        # same question asked two ways -- "is this sound audible
        # anywhere" and "silence it everywhere".
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

    def test_is_audio_player_playing_takes_a_required_int_channel(self, parser, semantic):
        # claude.md #146: a free function, like stopAudioPlayer -- but
        # unlike stopAudioPlayer's OPTIONAL channel (bare = "every
        # channel"), the channel here is required: there is no sensible
        # "any channel" reading for a query.
        program = parser.parse("bool p = isAudioPlayerPlaying(0)")
        semantic.analyze(program)

    def test_is_audio_player_playing_result_must_match_declared_type(
            self, parser, semantic, errors):
        program = parser.parse("int p = isAudioPlayerPlaying(0)")
        with pytest.raises(errors.CompileError, match="cannot assign"):
            semantic.analyze(program)

    def test_is_audio_player_playing_rejects_zero_arguments(self, parser, semantic, errors):
        program = parser.parse("bool p = isAudioPlayerPlaying()")
        with pytest.raises(errors.CompileError, match="1 argument"):
            semantic.analyze(program)

    def test_is_audio_player_playing_rejects_too_many_arguments(self, parser, semantic, errors):
        program = parser.parse("bool p = isAudioPlayerPlaying(1, 2)")
        with pytest.raises(errors.CompileError, match="1 argument"):
            semantic.analyze(program)

    def test_is_audio_player_playing_rejects_a_non_int_channel(self, parser, semantic, errors):
        program = parser.parse("bool p = isAudioPlayerPlaying('a')")
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
