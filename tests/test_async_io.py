"""claude.md #165: <text>.callback(fn:func[blob]:void) -- non-blocking
blob loading, the file-loading counterpart to claude.md #163's http
client callback. Two spellings: `blob b = 'path'.callback(fn)` (an
ordinary expression -- no VarDecl-specific bypass needed, unlike
claude.md #164's `{...}.send()`, since .callback()'s receiver is plain
text, never a heterogeneous literal) and the anonymous, fire-and-forget
`blob 'path'.callback(fn)` statement form, parser.py's own sugar.

img/aud were asked for too but are NOT implemented in this pass (see
semantic.py's own comment on this callback branch for why) -- checked
here only insofar as they're REJECTED with a clear error rather than a
confusing one.
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

    def test_callback_rejects_img_for_now(self, parser, semantic, errors):
        program = parser.parse("""
        void func onLoaded(i:img) { }
        img i = 'path.png'.callback(onLoaded)
        """)
        with pytest.raises(errors.CompileError, match="isn't implemented yet"):
            semantic.analyze(program)

    def test_callback_rejects_aud_for_now(self, parser, semantic, errors):
        program = parser.parse("""
        void func onLoaded(a:aud) { }
        aud a = 'path.mp3'.callback(onLoaded)
        """)
        with pytest.raises(errors.CompileError, match="isn't implemented yet"):
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
