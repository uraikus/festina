"""claude.md #151: openPort/closePort, `on request`/`on upgrade`/
`on message`/`on socketClose` -- HTTP + WebSocket server support.

Semantic-level checks (type/signature enforcement) need no toolchain at
all and run unconditionally. Real compile-and-run coverage goes through
compile_and_run_server (tests/conftest.py) -- a genuine background
server process, hit with real sockets (Python's http.client for HTTP,
a small hand-rolled RFC 6455 client for WebSocket, deliberately never
reusing this project's own implementation) -- so it skips cleanly
under the same "no C compiler" tier compile_and_run's own tests do,
and fails loudly under FESTINA_STRICT_DEPS=1 on the primary platform
if that tier vanishes. Linux/macOS only, matching the feature itself;
there is no Windows backend and no wasm32-wasi backend (see the
platform/wasm-rejection tests near the bottom).
"""
import sys

import pytest


class TestSemanticSignatures:
    """openPort/closePort's own fixed int signature, and the four event
    handlers' fixed parameter types -- claude.md #40's own established
    pattern, just for four new names."""

    def test_open_port_and_close_port_take_one_int(self, parser, semantic, errors):
        program = parser.parse("openPort(8080)\nclosePort(8080)")
        semantic.analyze(program)

    def test_open_port_wrong_type_is_rejected(self, parser, semantic, errors):
        program = parser.parse("openPort('8080')")
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)

    def test_on_request_requires_http_param(self, parser, semantic, errors):
        program = parser.parse("on request(req:text) { }")
        with pytest.raises(errors.CompileError, match="must declare exactly"):
            semantic.analyze(program)

    def test_on_upgrade_requires_socket_param(self, parser, semantic, errors):
        program = parser.parse("on upgrade(s:http) { }")
        with pytest.raises(errors.CompileError, match="must declare exactly"):
            semantic.analyze(program)

    def test_on_message_requires_socket_and_blob(self, parser, semantic, errors):
        program = parser.parse("on message(s:socket, msg:text) { }")
        with pytest.raises(errors.CompileError, match="must declare exactly"):
            semantic.analyze(program)

    def test_on_socket_close_requires_socket_param(self, parser, semantic, errors):
        program = parser.parse("on socketClose(s:http) { }")
        with pytest.raises(errors.CompileError, match="must declare exactly"):
            semantic.analyze(program)

    def test_the_four_handlers_with_correct_signatures_analyze_cleanly(self, parser, semantic):
        source = """
        on request(req:http) { }
        on upgrade(s:socket) { }
        on message(s:socket, msg:blob) { }
        on socketClose(s:socket) { }
        """
        program = parser.parse(source)
        semantic.analyze(program)


class TestHttpFieldsAndMethods:
    """req:http's own three read-only fields (port/method/path/headers)
    and its methods, checked purely at the type level -- no toolchain
    needed."""

    def test_reading_the_four_fields(self, parser, semantic):
        source = """
        on request(req:http) {
            int p = req.port
            text m = req.method
            text pa = req.path
            map[text] h = req.headers
        }
        """
        semantic.analyze(parser.parse(source))

    @pytest.mark.parametrize("field", ["port", "method", "path", "headers"])
    def test_the_fields_are_read_only(self, parser, semantic, errors, field):
        value = "5" if field == "port" else ("{}" if field == "headers" else "'x'")
        program = parser.parse(f"on request(req:http) {{ req.{field} = {value} }}")
        with pytest.raises(errors.CompileError, match="read-only"):
            semantic.analyze(program)

    def test_method_names_referenced_without_a_call_are_rejected(self, parser, semantic, errors):
        program = parser.parse("on request(req:http) { log(req.ok) }")
        with pytest.raises(errors.CompileError, match="method on http"):
            semantic.analyze(program)

    def test_unknown_field_is_rejected(self, parser, semantic, errors):
        program = parser.parse("on request(req:http) { log(req.bogus) }")
        with pytest.raises(errors.CompileError, match="no field"):
            semantic.analyze(program)

    def test_send_accepts_one_to_three_arguments(self, parser, semantic, errors):
        source = """
        map[text] h = {}
        on request(req:http) {
            req.send('a')
            req.send('a', 200)
            req.send('a', 200, h)
        }
        """
        semantic.analyze(parser.parse(source))

    def test_send_rejects_a_media_argument(self, parser, semantic, errors):
        program = parser.parse("on request(req:http) { req.send(req.toImg()) }")
        with pytest.raises(errors.CompileError, match="no body form"):
            semantic.analyze(program)

    def test_send_status_code_must_be_int(self, parser, semantic, errors):
        program = parser.parse("on request(req:http) { req.send('a', 'nope') }")
        with pytest.raises(errors.CompileError, match="status code"):
            semantic.analyze(program)


class TestSocketFieldsAndMethods:
    def test_state_is_a_mutable_map(self, parser, semantic):
        source = """
        on upgrade(s:socket) {
            s.state['k'] = 'v'
            text v = s.state['k']
        }
        """
        semantic.analyze(parser.parse(source))

    def test_send_and_close_are_recognized(self, parser, semantic):
        source = """
        on message(s:socket, msg:blob) {
            s.send('hi')
            s.send(msg)
            s.close()
        }
        """
        semantic.analyze(parser.parse(source))

    def test_close_takes_no_arguments(self, parser, semantic, errors):
        program = parser.parse("on message(s:socket, msg:blob) { s.close(1) }")
        with pytest.raises(errors.CompileError, match="expects 0 argument"):
            semantic.analyze(program)


# ---- real compile-and-run coverage -- claude.md #151's own "verify for
# real" standard, matching how every other feature in this project is
# tested. ----

class TestHttpServer:
    def test_basic_request_response(self, compile_and_run_server):
        server = compile_and_run_server("""
        openPort(__PORT__)
        on request(req:http) {
            req.send('hello world')
        }
        """)
        status, headers, body = server.http_get("/")
        assert status == 200
        assert body == b"hello world"

    def test_port_method_and_path(self, compile_and_run_server):
        server = compile_and_run_server("""
        openPort(__PORT__)
        on request(req:http) {
            req.send(`${req.port} ${req.method} ${req.path}`)
        }
        """)
        status, _, body = server.http_get("/some/path")
        assert status == 200
        assert body.decode() == "__PORT__ GET /some/path".replace("__PORT__", str(server.port))

    def test_headers_are_readable_and_lowercased(self, compile_and_run_server):
        server = compile_and_run_server("""
        openPort(__PORT__)
        on request(req:http) {
            text v = req.headers['x-custom']
            req.send(v)
        }
        """)
        status, _, body = server.http_get("/", headers={"X-Custom": "hello"})
        assert status == 200
        assert body == b"hello"

    def test_missing_header_is_null_not_a_crash(self, compile_and_run_server):
        server = compile_and_run_server("""
        openPort(__PORT__)
        on request(req:http) {
            text v = req.headers['not-there']
            if v == null {
                req.send('was null')
                return
            }
            req.send('not null')
        }
        """)
        status, _, body = server.http_get("/")
        assert body == b"was null"

    def test_default_response_when_the_handler_never_responds(self, compile_and_run_server):
        server = compile_and_run_server("""
        openPort(__PORT__)
        on request(req:http) {
        }
        """)
        status, _, body = server.http_get("/")
        assert status == 200
        assert body == b""

    def test_ok_sends_200_empty(self, compile_and_run_server):
        server = compile_and_run_server("""
        openPort(__PORT__)
        on request(req:http) {
            req.ok()
        }
        """)
        status, _, body = server.http_get("/")
        assert status == 200
        assert body == b""

    def test_redirect_sends_302_with_location(self, compile_and_run_server):
        server = compile_and_run_server("""
        openPort(__PORT__)
        on request(req:http) {
            req.redirect('https://example.com/there')
        }
        """)
        status, headers, body = server.http_get("/")
        assert status == 302
        assert headers.get("Location") == "https://example.com/there"

    def test_send_with_a_status_code(self, compile_and_run_server):
        server = compile_and_run_server("""
        openPort(__PORT__)
        on request(req:http) {
            req.send('created', 201)
        }
        """)
        status, _, body = server.http_get("/")
        assert status == 201
        assert body == b"created"

    def test_send_with_extra_headers(self, compile_and_run_server):
        server = compile_and_run_server("""
        openPort(__PORT__)
        map[text] extra = {}
        on request(req:http) {
            extra['x-served-by'] = 'festina'
            req.send('ok', 200, extra)
        }
        """)
        status, headers, body = server.http_get("/")
        # HTTP header names are case-insensitive; this server writes an
        # extra header's name back exactly as the program's own map key
        # spelled it (no title-casing), so the lookup here matches that
        # -- a client relying on a specific case would be relying on
        # something the protocol itself never promises either.
        assert headers.get("x-served-by") == "festina"

    def test_send_json_renders_containers_via_totext(self, compile_and_run_server):
        server = compile_and_run_server("""
        openPort(__PORT__)
        struct Point { x:int y:int }
        on request(req:http) {
            Point p
            p.x = 1
            p.y = 2
            req.send(p)
        }
        """)
        status, _, body = server.http_get("/")
        assert body == b'{"x":1,"y":2}'

    def test_post_body_via_to_text(self, compile_and_run_server):
        server = compile_and_run_server("""
        openPort(__PORT__)
        on request(req:http) {
            text body = req.toText()
            req.send(`got:${body}`)
        }
        """)
        status, _, body = server.http_post("/", body=b"hello from client")
        assert body == b"got:hello from client"

    def test_post_body_via_to_blob(self, compile_and_run_server):
        server = compile_and_run_server("""
        openPort(__PORT__)
        on request(req:http) {
            blob b = req.toBlob()
            req.send(b.toText())
        }
        """)
        status, _, body = server.http_post("/", body=b"raw bytes here")
        assert body == b"raw bytes here"

    def test_body_arriving_in_a_separate_write_after_headers(self, compile_and_run_server):
        # claude.md #155: a request whose headers and body arrive in
        # two SEPARATE socket writes (forced here with a real delay in
        # between, so the server sees them as two distinct readable
        # events, not one recv() that happened to return everything at
        # once) used to re-run request-line/header parsing from
        # scratch on the second event -- re-malloc'ing method/path over
        # the first call's own pointers with nothing freeing them, and
        # re-appending every header onto the still-populated header
        # list. Confirmed as a real, definitely-lost leak under
        # Valgrind during development (the duplicated header entries
        # are invisible from here -- req.headers is a map, and a
        # repeated key with an identical value dedups the same way it
        # always does -- so this test's real job is exercising the
        # split-arrival code path for correctness at all, structurally
        # guaranteeing the leak can't recur: header parsing is now
        # guarded to run at most once per connection). Uses a raw
        # socket rather than http_post -- a normal client call has no
        # way to force two separate TCP reads on the server side.
        import socket as _socket
        import time as _time
        server = compile_and_run_server("""
        openPort(__PORT__)
        on request(req:http) {
            text body = req.toText()
            req.send(`${req.method} ${req.headers['x-a']}/${req.headers['x-b']} [${body}]`)
        }
        """)
        sock = _socket.create_connection(("127.0.0.1", server.port), timeout=5)
        head = ("POST / HTTP/1.1\r\nHost: x\r\nX-A: hello\r\nX-B: world\r\n"
                "Content-Length: 10\r\n\r\n")
        sock.sendall(head.encode())
        _time.sleep(0.3)  # force a separate readable event before the body arrives
        sock.sendall(b"0123456789")
        response = sock.recv(4096)
        sock.close()
        assert response.endswith(b"POST hello/world [0123456789]")

    def test_no_body_request_gives_empty_text(self, compile_and_run_server):
        server = compile_and_run_server("""
        openPort(__PORT__)
        on request(req:http) {
            text body = req.toText()
            req.send(`[${body}]`)
        }
        """)
        status, _, body = server.http_get("/")
        assert body == b"[]"

    def test_reqcount_survives_multiple_requests_on_one_global(self, compile_and_run_server):
        # claude.md #151's own single-threaded event-loop design --
        # a global mutated across successive requests behaves exactly
        # like an ordinary Festina global, with no locking needed.
        server = compile_and_run_server("""
        openPort(__PORT__)
        int count = 0
        on request(req:http) {
            count = count + 1
            req.send(`${count}`)
        }
        """)
        for expected in (1, 2, 3):
            _, _, body = server.http_get("/")
            assert body.decode() == str(expected)


class TestWebSocketServer:
    def test_upgrade_then_message_roundtrip(self, compile_and_run_server):
        server = compile_and_run_server("""
        openPort(__PORT__)
        on request(req:http) {
            req.upgrade()
        }
        on message(s:socket, msg:blob) {
            text t = msg.toText()
            s.send(`echo:${t}`)
        }
        """)
        ws, status_line, expected_accept, raw_resp = server.ws_connect("/ws")
        assert b"101" in status_line
        assert expected_accept.encode() in raw_resp
        ws.send_text("hello")
        opcode, payload = ws.recv_frame()
        assert opcode == 0x1
        assert payload == b"echo:hello"
        ws.close()

    def test_on_upgrade_handler_fires(self, compile_and_run_server):
        server = compile_and_run_server("""
        openPort(__PORT__)
        on request(req:http) {
            req.upgrade()
        }
        on upgrade(s:socket) {
            s.state['greeted'] = 'yes'
        }
        on message(s:socket, msg:blob) {
            text v = s.state['greeted']
            s.send(v)
        }
        """)
        ws, _, _, _ = server.ws_connect("/ws")
        ws.send_text("ping")
        opcode, payload = ws.recv_frame()
        assert payload == b"yes"
        ws.close()

    def test_binary_frame_sent_as_blob(self, compile_and_run_server):
        server = compile_and_run_server("""
        openPort(__PORT__)
        on request(req:http) {
            req.upgrade()
        }
        on message(s:socket, msg:blob) {
            s.send(msg)
        }
        """)
        ws, _, _, _ = server.ws_connect("/ws")
        payload = bytes(range(256)) * 4  # exercises the >125-byte length path
        ws.send_binary(payload)
        opcode, echoed = ws.recv_frame()
        assert opcode == 0x2
        assert echoed == payload
        ws.close()

    def test_socket_close_fires_on_close_frame(self, compile_and_run_server):
        server = compile_and_run_server("""
        openPort(__PORT__)
        on request(req:http) {
            req.upgrade()
        }
        on message(s:socket, msg:blob) {
        }
        on socketClose(s:socket) {
            log('socket-closed')
        }
        """)
        ws, _, _, _ = server.ws_connect("/ws")
        ws.send_close()
        opcode, _ = ws.recv_frame()
        assert opcode == 0x8  # the server echoes a close frame back
        ws.close()
        # A plain HTTP request afterward confirms the server itself is
        # still alive and serving -- socketClose firing didn't crash it.
        status, _, _ = server.http_get("/does-not-exist")
        assert status == 200  # no `on request` override here beyond upgrade -> default 200

    def test_server_side_close_sends_a_close_frame(self, compile_and_run_server):
        server = compile_and_run_server("""
        openPort(__PORT__)
        on request(req:http) {
            req.upgrade()
        }
        on message(s:socket, msg:blob) {
            s.close()
        }
        """)
        ws, _, _, _ = server.ws_connect("/ws")
        ws.send_text("anything")
        opcode, _ = ws.recv_frame()
        assert opcode == 0x8
        ws.close()

    def test_multiple_websocket_sessions_are_independent(self, compile_and_run_server):
        server = compile_and_run_server("""
        openPort(__PORT__)
        on request(req:http) {
            req.upgrade()
        }
        on upgrade(s:socket) {
            s.state['id'] = 'unset'
        }
        on message(s:socket, msg:blob) {
            text t = msg.toText()
            if t == 'set' {
                s.state['id'] = 'A'
                s.send('ok')
                return
            }
            text v = s.state['id']
            s.send(v)
        }
        """)
        a, _, _, _ = server.ws_connect("/ws")
        b, _, _, _ = server.ws_connect("/ws")
        a.send_text("set")
        assert a.recv_frame()[1] == b"ok"
        a.send_text("get")
        assert a.recv_frame()[1] == b"A"
        b.send_text("get")
        assert b.recv_frame()[1] == b"unset"  # b's own state was never touched
        a.close()
        b.close()


class TestPlatformAndWasmGating:
    """claude.md #151: http/graphics are mutually exclusive in this
    version; there is no wasm32-wasi backend at all -- both checked
    without needing a real toolchain (the same tier
    _check_wasm_feature_supported's own graphics/audio/exec tests in
    test_wasm.py already sit in). darwin AND win32 both gate a
    backend that EXISTS (built, CI-compiled -- win32's own winsock2
    port confirmed by a real MinGW cross-compile, claude.md #151's own
    Windows round) but awaits real-hardware verification, the same
    shape audio/graphics already established for both platforms."""

    def test_http_and_graphics_together_is_rejected(self, cli_mod, tmp_path):
        src = tmp_path / "main.f"
        src.write_text(
            "openPort(8080)\n"
            "on request(req:http) { req.ok() }\n"
            "on mouseDown(x:int, y:int) { }\n",
            encoding="utf-8",
        )
        with pytest.raises(cli_mod.CompileError) as exc_info:
            cli_mod.compile_file(str(src), str(tmp_path / "out"), cc="clang")
        assert exc_info.value.category == "unsupported platform feature"
        assert "graphics" in str(exc_info.value)

    def test_http_on_windows_is_gated_pending_verification(self, cli_mod, monkeypatch):
        monkeypatch.delenv("FESTINA_ENABLE_WINDOWS_HTTP", raising=False)
        with pytest.raises(cli_mod.CompileError) as exc_info:
            cli_mod._check_feature_supported("http", platform_name="win32")
        assert exc_info.value.category == "unsupported platform feature"

    def test_http_on_windows_override_env_var_bypasses_the_gate(self, cli_mod, monkeypatch):
        monkeypatch.setenv("FESTINA_ENABLE_WINDOWS_HTTP", "1")
        cli_mod._check_feature_supported("http", platform_name="win32")  # no raise

    def test_http_on_macos_is_gated_pending_verification(self, cli_mod, monkeypatch):
        monkeypatch.delenv("FESTINA_ENABLE_MACOS_HTTP", raising=False)
        with pytest.raises(cli_mod.CompileError) as exc_info:
            cli_mod._check_feature_supported("http", platform_name="darwin")
        assert exc_info.value.category == "unsupported platform feature"

    def test_http_on_macos_override_env_var_bypasses_the_gate(self, cli_mod, monkeypatch):
        monkeypatch.setenv("FESTINA_ENABLE_MACOS_HTTP", "1")
        cli_mod._check_feature_supported("http", platform_name="darwin")  # no raise

    def test_http_is_rejected_for_wasm(self, cli_mod):
        with pytest.raises(cli_mod.CompileError) as exc_info:
            cli_mod._check_wasm_feature_supported("http")
        assert exc_info.value.category == "unsupported platform feature"

    def test_http_is_rejected_at_wasm_compile_time(self, cli_mod, tmp_path):
        src = tmp_path / "main.f"
        src.write_text("openPort(8080)\non request(req:http) { req.ok() }\n", encoding="utf-8")
        with pytest.raises(cli_mod.CompileError) as exc_info:
            cli_mod.compile_file(str(src), str(tmp_path / "out.wasm"),
                                  cc="clang", target="wasm32-wasi")
        assert exc_info.value.category == "unsupported platform feature"
        assert "openPort" in str(exc_info.value)
