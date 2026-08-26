"""claude.md #165 (extended to img/aud by #171): <text>.callback(fn:
func[T]:void) -- non-blocking blob/img/aud loading, the file-loading
counterpart to claude.md #163's http client callback. Two spellings:
`blob b = 'path'.callback(fn)` (an ordinary expression -- no
VarDecl-specific bypass needed, unlike claude.md #164's `{...}.send()`,
since .callback()'s receiver is plain text, never a heterogeneous
literal) and the anonymous, fire-and-forget `blob 'path'.callback(fn)`
statement form, parser.py's own sugar. Both work identically for `img`
and `aud`.
"""
import pytest


class TestAsyncIoSemantics:
    def test_callback_on_a_string_literal_analyzes(self, parser, semantic):
        source = """
        void func onLoaded(b:blob) { }
        blob b = 'path.txt'.callback(onLoaded)
        """
        semantic.analyze(parser.parse(source))

    def test_callback_works_on_any_text_expression(self, parser, semantic):
        source = """
        void func onLoaded(b:blob) { }
        text path = 'path.txt'
        blob b = path.callback(onLoaded)
        """
        semantic.analyze(parser.parse(source))

    def test_callback_requires_exactly_one_argument(self, parser, semantic, errors):
        program = parser.parse("""
        void func onLoaded(b:blob) { }
        blob b = 'path.txt'.callback(onLoaded, onLoaded)
        """)
        with pytest.raises(errors.CompileError, match="callback\\(\\) expects exactly 1"):
            semantic.analyze(program)

    def test_callback_rejects_a_non_func_argument(self, parser, semantic, errors):
        program = parser.parse("blob b = 'path.txt'.callback('not a func')")
        with pytest.raises(errors.CompileError, match="callback\\(\\) expects func\\[blob\\]:void"):
            semantic.analyze(program)

    def test_callback_rejects_a_wrong_signature_func(self, parser, semantic, errors):
        program = parser.parse("""
        void func wrong(x:int) { }
        blob b = 'path.txt'.callback(wrong)
        """)
        with pytest.raises(errors.CompileError, match="callback\\(\\) expects func\\[blob\\]:void"):
            semantic.analyze(program)

    def test_callback_works_for_img(self, parser, semantic):
        source = """
        void func onLoaded(i:img) { }
        img i = 'path.png'.callback(onLoaded)
        """
        semantic.analyze(parser.parse(source))

    def test_callback_works_for_aud(self, parser, semantic):
        source = """
        void func onLoaded(a:aud) { }
        aud a = 'path.mp3'.callback(onLoaded)
        """
        semantic.analyze(parser.parse(source))

    def test_callback_rejects_mismatched_declared_type(self, parser, semantic, errors):
        # `fn`'s own signature says img, but the declaration says blob --
        # ordinary assignment-compatibility, same as any other VarDecl.
        program = parser.parse("""
        void func onLoaded(i:img) { }
        blob b = 'path.png'.callback(onLoaded)
        """)
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)

    def test_anonymous_statement_form_parses_and_analyzes(self, parser, semantic, ast_mod):
        source = """
        void func onLoaded(b:blob) { }
        blob 'path.txt'.callback(onLoaded)
        """
        program = parser.parse(source)
        stmt = program.body[-1]
        assert isinstance(stmt, ast_mod.ExprStmt)
        assert isinstance(stmt.expr, ast_mod.Call)
        assert stmt.expr.callee.prop == "callback"
        semantic.analyze(program)

    def test_zero_init_blob_declaration_is_unaffected(self, parser, semantic):
        # claude.md #165's own parser check (`blob` followed by
        # non-IDENT means the anonymous form) must not misfire on the
        # pre-existing `blob name` zero-init declaration (IDENT
        # follows) -- this is the regression it would cause if it did.
        semantic.analyze(parser.parse("blob b"))


class TestAsyncIoRuntime:
    """Real compile-and-run coverage, matching claude.md #163's own
    TestHttpCallbackRuntime discipline."""

    def test_load_does_not_block(self, tmp_path, compile_and_run):
        (tmp_path / "data.txt").write_text("hello from a background load", encoding="utf-8")
        result = compile_and_run("""
        void func onLoaded(b:blob) {
            log(`loaded: ${b.toText()}`)
            close(0)
        }
        blob b = 'data.txt'.callback(onLoaded)
        log('dispatched')
        """)
        assert result.returncode == 0, result.stdout
        assert result.stdout.index("dispatched") < result.stdout.index("loaded:")
        assert "loaded: hello from a background load" in result.stdout

    def test_program_exits_cleanly_with_no_explicit_close(self, tmp_path, compile_and_run):
        # No openPort()/graphics/setTimeout anywhere -- festina_run_timer_loop
        # is entered ONLY because of uses_async_io (see codegen.py's own
        # widened loop-selection condition), and must correctly wait for
        # the outstanding load, run the callback, THEN exit on its own.
        (tmp_path / "data.txt").write_text("content", encoding="utf-8")
        result = compile_and_run("""
        void func onLoaded(b:blob) {
            log(`loaded: ${b.toText()}`)
        }
        blob b = 'data.txt'.callback(onLoaded)
        log('dispatched')
        """)
        assert result.returncode == 0, result.stdout
        assert "loaded: content" in result.stdout

    def test_unreadable_path_is_graceful_not_a_crash(self, compile_and_run):
        result = compile_and_run("""
        void func onLoaded(b:blob) {
            log(`exists=${b.exists()} text='${b.toText()}'`)
            close(0)
        }
        blob b = 'does/not/exist.txt'.callback(onLoaded)
        """)
        assert result.returncode == 0, result.stdout
        assert "exists=false text=''" in result.stdout

    def test_multiple_concurrent_loads_all_complete(self, tmp_path, compile_and_run):
        for i in range(1, 7):
            (tmp_path / f"file{i}.txt").write_text(f"content {i}", encoding="utf-8")
        result = compile_and_run("""
        int done = 0
        void func onLoaded(b:blob) {
            done = done + 1
            if done == 6 { close(0) }
        }
        blob b1 = 'file1.txt'.callback(onLoaded)
        blob b2 = 'file2.txt'.callback(onLoaded)
        blob b3 = 'file3.txt'.callback(onLoaded)
        blob b4 = 'file4.txt'.callback(onLoaded)
        blob b5 = 'file5.txt'.callback(onLoaded)
        blob b6 = 'file6.txt'.callback(onLoaded)
        log('all 6 dispatched')
        """)
        assert result.returncode == 0, result.stdout
        assert "all 6 dispatched" in result.stdout

    def test_anonymous_statement_form(self, tmp_path, compile_and_run):
        (tmp_path / "data.txt").write_text("anon content", encoding="utf-8")
        result = compile_and_run("""
        void func onLoaded(b:blob) {
            log(`anon loaded: ${b.toText()}`)
            close(0)
        }
        blob 'data.txt'.callback(onLoaded)
        log('anon dispatched')
        """)
        assert result.returncode == 0, result.stdout
        assert result.stdout.index("anon dispatched") < result.stdout.index("anon loaded:")
        assert "anon loaded: anon content" in result.stdout

    def test_combines_with_a_real_http_server(self, tmp_path, compile_and_run_server):
        # claude.md #165's own cross-loop concern: festina_run_http_loop
        # (not festina_run_timer_loop) is the one actually running here,
        # and it needs the SAME async-io hook integration.
        (tmp_path / "data.txt").write_text("via http loop", encoding="utf-8")
        server = compile_and_run_server("""
        void func onLoaded(b:blob) {
            log(`http-loop loaded: ${b.toText()}`)
        }
        on request(req:http) {
            blob b = 'data.txt'.callback(onLoaded)
            req.ok()
        }
        openPort(__PORT__)
        """)
        status, _, _ = server.http_get("/")
        assert status == 200
        import time as _time
        _time.sleep(0.3)  # give the background load a moment to complete
        server.process.terminate()
        server.process.wait(timeout=3)
        out = server.process.stdout.read() if server.process.stdout else ""
        assert "http-loop loaded: via http loop" in out


def _make_png(path, w=2, h=2, color=(255, 0, 0)):
    """A minimal valid PNG, written by hand -- same technique
    conftest.py's own sprite_sheet_png fixture uses, kept local here
    since this module only ever needs a trivially small, solid-colour
    image (never real pixel content) to exercise the decode path."""
    import struct
    import zlib

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    r, g, b = color
    row = bytes([0]) + bytes([r, g, b]) * w  # filter type 0, no alpha
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(row * h))
           + chunk(b"IEND", b""))
    path.write_bytes(png)


def _make_wav(path, duration_s=0.05, sample_rate=8000):
    import wave
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(b"\x00\x00" * int(duration_s * sample_rate))


class TestAsyncIoImgAudRuntime:
    """claude.md #171: img/aud's own `.callback()` -- the same
    non-blocking load TestAsyncIoRuntime above already covers for blob,
    now exercised for the two media types, including the "test, don't
    fail" graceful-failure contract festina_image_load_worker/
    festina_audio_load_worker had to be GIVEN (festina_load_image/
    festina_load_audio's own synchronous path still calls fail() on
    exactly the same three failure shapes -- see
    TestGraphics::test_invalid_image_path_is_a_clear_runtime_error and
    its neighbor, unaffected by any of this)."""

    def test_img_callback_does_not_block(self, tmp_path, compile_and_run, monkeypatch):
        monkeypatch.delenv("DISPLAY", raising=False)
        _make_png(tmp_path / "pic.png")
        result = compile_and_run("""
        void func onLoaded(i:img) {
            log(`loaded: ${i.width}x${i.height}`)
            close(0)
        }
        img i = 'pic.png'.callback(onLoaded)
        log('dispatched')
        """)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.index("dispatched") < result.stdout.index("loaded:")
        assert "loaded: 2x2" in result.stdout

    def test_aud_callback_does_not_block(self, tmp_path, compile_and_run, monkeypatch):
        monkeypatch.delenv("DISPLAY", raising=False)
        _make_wav(tmp_path / "clip.wav")
        result = compile_and_run("""
        void func onLoaded(a:aud) {
            log('loaded')
            close(0)
        }
        aud a = 'clip.wav'.callback(onLoaded)
        log('dispatched')
        """)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.index("dispatched") < result.stdout.index("loaded")

    def test_img_callback_unreadable_path_is_graceful(self, compile_and_run, monkeypatch):
        # festina_load_image (the synchronous path) calls festina_fail()
        # on exactly this input -- the point of .callback() is that its
        # OWN worker never does, leaving the 1x1 placeholder in place.
        monkeypatch.delenv("DISPLAY", raising=False)
        result = compile_and_run("""
        void func onLoaded(i:img) {
            log(`w=${i.width} h=${i.height}`)
            close(0)
        }
        img i = 'does/not/exist.png'.callback(onLoaded)
        """)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "w=1 h=1" in result.stdout

    def test_img_callback_corrupt_data_is_graceful(self, tmp_path, compile_and_run, monkeypatch):
        monkeypatch.delenv("DISPLAY", raising=False)
        (tmp_path / "bad.png").write_bytes(b"this is not an image at all")
        result = compile_and_run("""
        void func onLoaded(i:img) {
            log(`w=${i.width}`)
            close(0)
        }
        img i = 'bad.png'.callback(onLoaded)
        """)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "w=1" in result.stdout

    def test_aud_callback_unreadable_path_is_graceful(self, compile_and_run, monkeypatch):
        monkeypatch.delenv("DISPLAY", raising=False)
        result = compile_and_run("""
        void func onLoaded(a:aud) {
            log('done')
            close(0)
        }
        aud a = 'does/not/exist.mp3'.callback(onLoaded)
        """)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "done" in result.stdout

    def test_aud_callback_corrupt_data_is_graceful(self, tmp_path, compile_and_run, monkeypatch):
        monkeypatch.delenv("DISPLAY", raising=False)
        (tmp_path / "bad.mp3").write_bytes(b"not audio data at all, just garbage bytes")
        result = compile_and_run("""
        void func onLoaded(a:aud) {
            log('done')
            close(0)
        }
        aud a = 'bad.mp3'.callback(onLoaded)
        """)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "done" in result.stdout

    def test_sync_img_load_still_fails_loudly(self, tmp_path, compile_and_run, monkeypatch):
        # The ordinary, non-callback path is completely unaffected --
        # festina_load_image/festina_image_from_bytes still call
        # festina_fail() on exactly the inputs they always did.
        monkeypatch.delenv("DISPLAY", raising=False)
        (tmp_path / "bad.png").write_bytes(b"nope")
        result = compile_and_run("img icon = 'bad.png'\nlog('unreachable')")
        assert result.returncode == 1
        assert "not a PNG or JPEG" in result.stderr
        assert "unreachable" not in result.stdout

    def test_concurrent_img_and_aud_callbacks_all_complete(self, tmp_path, compile_and_run,
                                                             monkeypatch):
        # Several img AND aud background loads in flight at once,
        # racing each other and the main thread -- what
        # festina_decode_mp3's own pthread_once fix and the
        # ThreadSanitizer-clean img decode path (see
        # festina_decode_image_surface's own doc comment in
        # festina_runtime_graphics.c) are actually for.
        monkeypatch.delenv("DISPLAY", raising=False)
        for i in range(1, 5):
            _make_png(tmp_path / f"p{i}.png")
            _make_wav(tmp_path / f"a{i}.wav")
        result = compile_and_run("""
        int done = 0
        void func onImg(i:img) { done = done + 1; if done == 8 { close(0) } }
        void func onAud(a:aud) { done = done + 1; if done == 8 { close(0) } }
        img p1 = 'p1.png'.callback(onImg)
        img p2 = 'p2.png'.callback(onImg)
        img p3 = 'p3.png'.callback(onImg)
        img p4 = 'p4.png'.callback(onImg)
        aud a1 = 'a1.wav'.callback(onAud)
        aud a2 = 'a2.wav'.callback(onAud)
        aud a3 = 'a3.wav'.callback(onAud)
        aud a4 = 'a4.wav'.callback(onAud)
        log('all dispatched')
        """)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "all dispatched" in result.stdout

    def test_anonymous_statement_form_works_for_img(self, tmp_path, compile_and_run, monkeypatch):
        monkeypatch.delenv("DISPLAY", raising=False)
        _make_png(tmp_path / "pic.png")
        result = compile_and_run("""
        void func onLoaded(i:img) {
            log('anon img loaded')
            close(0)
        }
        img 'pic.png'.callback(onLoaded)
        log('anon dispatched')
        """)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.index("anon dispatched") < result.stdout.index("anon img loaded")
