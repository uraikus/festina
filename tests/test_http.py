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
import os
import struct
import subprocess
import sys
import time

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
    """http's own four read-only fields (url/method/code/headers) and its
    methods, checked purely at the type level -- no toolchain needed.
    claude.md #162: `url`/`code` replace the old `port`/`path` pair, and
    `send()` is now overloaded by arity (0 = outbound client request, 1 =
    a constructed response) instead of taking up to three positional
    data/code/headers arguments."""

    def test_reading_the_four_fields(self, parser, semantic):
        source = """
        on request(req:http) {
            text u = req.url
            text m = req.method
            int c = req.code
            map[text] h = req.headers
        }
        """
        semantic.analyze(parser.parse(source))

    @pytest.mark.parametrize("field", ["url", "method", "code", "headers"])
    def test_the_fields_are_read_only(self, parser, semantic, errors, field):
        value = "5" if field == "code" else ("{}" if field == "headers" else "'x'")
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

    def test_send_with_zero_arguments_is_the_client_form(self, parser, semantic):
        source = """
        http req = {'url': 'http://example.com', 'method': 'GET'}
        req.send()
        """
        semantic.analyze(parser.parse(source))

    def test_send_with_one_http_argument_is_the_server_form(self, parser, semantic):
        source = """
        on request(req:http) {
            http res = {'code': 200, 'body': 'ok'}
            req.send(res)
        }
        """
        semantic.analyze(parser.parse(source))

    def test_send_accepts_an_inline_response_literal(self, parser, semantic):
        source = """
        on request(req:http) {
            req.send({'code': 200, 'body': 'ok'})
        }
        """
        semantic.analyze(parser.parse(source))

    def test_send_rejects_more_than_one_argument(self, parser, semantic, errors):
        program = parser.parse(
            "on request(req:http) { req.send({'code':200}, {'code':201}) }"
        )
        with pytest.raises(errors.CompileError, match="send\\(\\) expects 0 arguments"):
            semantic.analyze(program)

    def test_send_rejects_a_non_http_argument(self, parser, semantic, errors):
        program = parser.parse("on request(req:http) { req.send('not an http value') }")
        with pytest.raises(errors.CompileError, match="expects http"):
            semantic.analyze(program)

    def test_http_literal_rejects_an_unknown_key(self, parser, semantic, errors):
        program = parser.parse("http x = {'bogus': 'x'}")
        with pytest.raises(errors.CompileError, match="no field"):
            semantic.analyze(program)

    def test_http_literal_body_rejects_a_non_sendable_type(self, parser, semantic, errors):
        # img/aud/text/int/float/bool/blob/struct/array/map ARE valid
        # body forms (claude.md #162) -- this checks a genuinely
        # un-sendable type (url) is still rejected.
        program = parser.parse("url u = parseURL('http://x/')\nhttp x = {'body': u}")
        with pytest.raises(errors.CompileError, match="no body form"):
            semantic.analyze(program)

    def test_object_literal_shorthand_expands_key_and_value(self, parser, semantic):
        source = """
        map[text] headers = {'E-Tag': 'abc'}
        http x = {'url': 'http://example.com', 'method': 'GET', headers}
        """
        semantic.analyze(parser.parse(source))

    def test_parse_url_returns_a_url_value(self, parser, semantic):
        source = """
        url u = parseURL('http://example.com:8080/path?a=1#frag')
        text h = u.hostname
        int p = u.port
        text pa = u.pathname
        text fr = u.hash
        map[text] sp = u.searchParams
        """
        semantic.analyze(parser.parse(source))

    def test_url_fields_are_read_only(self, parser, semantic, errors):
        program = parser.parse("url u = parseURL('http://example.com/')\nu.hostname = 'x'")
        with pytest.raises(errors.CompileError, match="read-only"):
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
            req.send({'body': 'hello world'})
        }
        """)
        status, headers, body = server.http_get("/")
        assert status == 200
        assert body == b"hello world"

    def test_url_and_method(self, compile_and_run_server):
        server = compile_and_run_server("""
        openPort(__PORT__)
        on request(req:http) {
            req.send({'body': `${req.method} ${req.url}`})
        }
        """)
        status, _, body = server.http_get("/some/path")
        assert status == 200
        expected_url = f"http://127.0.0.1:{server.port}/some/path"
        assert body.decode() == f"GET {expected_url}"

    def test_headers_are_readable_and_lowercased(self, compile_and_run_server):
        server = compile_and_run_server("""
        openPort(__PORT__)
        on request(req:http) {
            text v = req.headers['x-custom']
            req.send({'body': v})
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
                req.send({'body': 'was null'})
                return
            }
            req.send({'body': 'not null'})
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
            req.send({'code': 201, 'body': 'created'})
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
            req.send({'code': 200, 'body': 'ok', 'headers': extra})
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
            req.send({'body': p})
        }
        """)
        status, _, body = server.http_get("/")
        assert body == b'{"x":1,"y":2}'

    def test_post_body_via_to_text(self, compile_and_run_server):
        server = compile_and_run_server("""
        openPort(__PORT__)
        on request(req:http) {
            text body = req.toText()
            req.send({'body': `got:${body}`})
        }
        """)
        status, _, body = server.http_post("/", body=b"hello from client")
        assert body == b"got:hello from client"

    def test_post_body_via_to_blob(self, compile_and_run_server):
        server = compile_and_run_server("""
        openPort(__PORT__)
        on request(req:http) {
            blob b = req.toBlob()
            req.send({'body': b.toText()})
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
            req.send({'body': `${req.method} ${req.headers['x-a']}/${req.headers['x-b']} [${body}]`})
        }
        """)
        sock = _socket.create_connection(("127.0.0.1", server.port), timeout=5)
        head = ("POST / HTTP/1.1\r\nHost: x\r\nX-A: hello\r\nX-B: world\r\n"
                "Connection: close\r\nContent-Length: 10\r\n\r\n")
        sock.sendall(head.encode())
        _time.sleep(0.3)  # force a separate readable event before the body arrives
        sock.sendall(b"0123456789")
        # A single recv() isn't guaranteed to return the whole response
        # in one call (a real, if rare, flake under load) -- read until
        # the server closes the connection. claude.md #167: HTTP/1.1
        # defaults to keep-alive now, so this request sends its own
        # explicit `Connection: close` to keep this exact read-until-EOF
        # shape rather than switching to a fixed-length read -- the
        # split-arrival behavior under test has nothing to do with
        # keep-alive either way.
        chunks = []
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
        sock.close()
        response = b"".join(chunks)
        assert response.endswith(b"POST hello/world [0123456789]")

    def test_no_body_request_gives_empty_text(self, compile_and_run_server):
        server = compile_and_run_server("""
        openPort(__PORT__)
        on request(req:http) {
            text body = req.toText()
            req.send({'body': `[${body}]`})
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
            req.send({'body': `${count}`})
        }
        """)
        for expected in (1, 2, 3):
            _, _, body = server.http_get("/")
            assert body.decode() == str(expected)


class TestHttpKeepAlive:
    """claude.md #167: HTTP/1.1 keep-alive -- the first item off api.md's
    own http Limitations list (previously "No keep-alive. Every response
    closes the connection afterward"). `server.http_get`/`http_post`
    (tests/conftest.py) each open and close their OWN fresh connection,
    so they can't exercise reuse at all -- every test here drives a raw
    socket or its own `http.client.HTTPConnection` instead, reusing it
    across multiple requests on purpose."""

    def test_two_requests_reuse_one_tcp_connection(self, compile_and_run_server):
        import http.client
        server = compile_and_run_server("""
        openPort(__PORT__)
        on request(req:http) {
            req.send({'code': 200, 'body': `hi ${req.url}`})
        }
        """)
        conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
        conn.request("GET", "/a")
        r1 = conn.getresponse()
        body1 = r1.read()
        sock1 = conn.sock
        assert r1.getheader("Connection") == "keep-alive"

        conn.request("GET", "/b")
        r2 = conn.getresponse()
        body2 = r2.read()
        # http.client only opens a NEW socket if the previous one was
        # closed -- reaching getresponse() a second time on the SAME
        # `conn` object, still holding the SAME socket, is direct proof
        # the server never closed its end after the first response.
        assert conn.sock is sock1
        assert r2.getheader("Connection") == "keep-alive"
        assert body1.endswith(b"/a") and body2.endswith(b"/b")
        conn.close()

    def test_explicit_connection_close_closes_after_one_response(self, compile_and_run_server):
        import http.client
        server = compile_and_run_server("""
        openPort(__PORT__)
        on request(req:http) { req.send({'code': 200, 'body': 'ok'}) }
        """)
        conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
        conn.request("GET", "/", headers={"Connection": "close"})
        r = conn.getresponse()
        r.read()
        assert r.getheader("Connection") == "close"
        # http.client itself already noticed the server's own
        # `Connection: close` and dropped the socket -- direct proof
        # this response actually closed the connection, not just said
        # it would.
        assert conn.sock is None
        conn.close()

    def test_http_1_0_defaults_to_close_with_no_connection_header(self, compile_and_run_server):
        import socket as _socket
        server = compile_and_run_server("""
        openPort(__PORT__)
        on request(req:http) { req.send({'code': 200, 'body': 'ok'}) }
        """)
        sock = _socket.create_connection(("127.0.0.1", server.port), timeout=5)
        sock.sendall(b"GET / HTTP/1.0\r\nHost: x\r\n\r\n")
        data = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
        sock.close()
        assert b"Connection: close" in data
        assert data.endswith(b"ok")

    def test_pipelined_requests_are_all_served_in_order(self, compile_and_run_server):
        import socket as _socket
        server = compile_and_run_server("""
        openPort(__PORT__)
        on request(req:http) { req.send({'code': 200, 'body': req.url}) }
        """)
        sock = _socket.create_connection(("127.0.0.1", server.port), timeout=5)
        # Three requests in ONE write, before reading anything back --
        # the client sent all of it before the server had a chance to
        # respond to any of it. See festina_conn_reset_for_next_request's
        # own doc comment for why this doesn't deadlock.
        sock.sendall(
            b"GET /p1 HTTP/1.1\r\nHost: x\r\n\r\n"
            b"GET /p2 HTTP/1.1\r\nHost: x\r\n\r\n"
            b"GET /p3 HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n"
        )
        data = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
        sock.close()
        # Each response body is its own request's own full URL -- three
        # bodies, in the order the requests were sent.
        assert [p.split(b"\r\n\r\n", 1)[1] for p in data.split(b"HTTP/1.1 200")[1:]] == [
            b"http://x/p1", b"http://x/p2", b"http://x/p3"]

    def test_idle_keepalive_connection_is_reaped(self, compile_and_run_server, monkeypatch):
        import http.client
        # claude.md #167: FESTINA_HTTP_KEEPALIVE_IDLE_SECONDS, the same
        # test-only override shape FESTINA_SHUTDOWN_GRACE_SECONDS
        # already established -- lets this exercise the reap path in a
        # fraction of a second instead of the real 15s default.
        monkeypatch.setenv("FESTINA_HTTP_KEEPALIVE_IDLE_SECONDS", "0.3")
        server = compile_and_run_server("""
        openPort(__PORT__)
        on request(req:http) { req.send({'code': 200, 'body': 'ok'}) }
        """)
        conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
        conn.request("GET", "/")
        r = conn.getresponse()
        r.read()
        conn.sock.settimeout(3)
        assert conn.sock.recv(16) == b""  # server-initiated close, once idle too long
        conn.close()

    def test_a_request_still_completing_is_never_reaped(self, compile_and_run_server, monkeypatch):
        # The idle-reap only ever targets a connection with NO request
        # in flight -- a slow client trickling its own request in over
        # more than the idle window must not be torn down mid-request.
        monkeypatch.setenv("FESTINA_HTTP_KEEPALIVE_IDLE_SECONDS", "0.3")
        import socket as _socket
        import time as _time
        server = compile_and_run_server("""
        openPort(__PORT__)
        on request(req:http) { req.send({'code': 200, 'body': req.toText()}) }
        """)
        sock = _socket.create_connection(("127.0.0.1", server.port), timeout=5)
        sock.sendall(b"POST / HTTP/1.1\r\nHost: x\r\nContent-Length: 5\r\n\r\n")
        _time.sleep(0.5)  # longer than the idle window, but mid-request (headers parsed already)
        sock.sendall(b"hello")
        sock.settimeout(5)
        data = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
        sock.close()
        assert data.endswith(b"hello")


def _recv_one_http_response(sock):
    """Reads exactly one HTTP/1.1 response off a raw, still-OPEN socket
    -- robust against the response arriving across more than one
    recv() call (real, if rare, under load -- TCP makes no promise a
    small response arrives in a single read), unlike a bare
    `sock.recv(4096)`. Needed anywhere the connection is expected to
    stay open afterward (keep-alive) -- there's no EOF to "read until"
    the way TestHttpServer's/TestChunkedTransferEncoding's own
    Connection-close tests already do."""
    import re
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            return data
        data += chunk
    header_block, _, rest = data.partition(b"\r\n\r\n")
    m = re.search(rb"Content-Length:\s*(\d+)", header_block, re.IGNORECASE)
    content_length = int(m.group(1)) if m else 0
    while len(rest) < content_length:
        chunk = sock.recv(4096)
        if not chunk:
            break
        rest += chunk
    return header_block + b"\r\n\r\n" + rest


class TestChunkedTransferEncoding:
    """claude.md #168: `Transfer-Encoding: chunked` -- previously
    unsupported in either direction (api.md's own former "No chunked
    transfer-encoding" limitation). Server-side (an incoming request
    body) needs raw sockets -- `server.http_post` only ever sends
    Content-Length bodies -- so every test here builds the wire bytes
    by hand, the same way TestHttpKeepAlive's own pipelining test does."""

    def test_a_chunked_request_body_is_decoded(self, compile_and_run_server):
        import socket as _socket
        server = compile_and_run_server("""
        openPort(__PORT__)
        on request(req:http) { req.send({'code': 200, 'body': req.toText()}) }
        """)
        sock = _socket.create_connection(("127.0.0.1", server.port), timeout=5)
        sock.sendall(
            b"POST / HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\n"
            b"Connection: close\r\n\r\n"
            b"5\r\nhello\r\n6\r\n world\r\n0\r\n\r\n"
        )
        data = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
        sock.close()
        assert data.endswith(b"hello world")

    def test_a_chunked_request_body_arriving_in_separate_writes_is_decoded(
            self, compile_and_run_server):
        # claude.md #155's own split-arrival concern, now for chunked
        # framing too -- festina_chunk_decode_step has to resume across
        # calls correctly, the same way header/body parsing already did.
        import socket as _socket
        import time as _time
        server = compile_and_run_server("""
        openPort(__PORT__)
        on request(req:http) { req.send({'code': 200, 'body': req.toText()}) }
        """)
        sock = _socket.create_connection(("127.0.0.1", server.port), timeout=5)
        sock.sendall(b"POST / HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\n"
                     b"Connection: close\r\n\r\n")
        _time.sleep(0.2)
        sock.sendall(b"3\r\nfoo\r\n")
        _time.sleep(0.2)
        sock.sendall(b"3\r\nbar\r\n0\r\n\r\n")
        data = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
        sock.close()
        assert data.endswith(b"foobar")

    def test_chunked_combines_with_keep_alive(self, compile_and_run_server):
        import socket as _socket
        server = compile_and_run_server("""
        openPort(__PORT__)
        on request(req:http) { req.send({'code': 200, 'body': req.toText()}) }
        """)
        sock = _socket.create_connection(("127.0.0.1", server.port), timeout=5)
        sock.sendall(b"POST / HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\n\r\n"
                     b"3\r\nfoo\r\n0\r\n\r\n")
        sock.settimeout(5)
        resp1 = _recv_one_http_response(sock)
        assert resp1.endswith(b"foo")
        assert b"Connection: keep-alive" in resp1
        # A second, ordinary request on the SAME connection -- proves
        # the chunked request's own raw byte count was tracked
        # correctly for the keep-alive reset (a wrong count would
        # either desync the next request's own parse or leave stray
        # bytes behind).
        sock.sendall(b"GET /again HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        resp2 = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            resp2 += chunk
        sock.close()
        assert resp2.endswith(b"200 Festina\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")

    def test_a_malformed_chunk_size_drops_the_connection_cleanly(self, compile_and_run_server):
        import socket as _socket
        server = compile_and_run_server("""
        openPort(__PORT__)
        on request(req:http) { req.send({'code': 200, 'body': req.toText()}) }
        """)
        sock = _socket.create_connection(("127.0.0.1", server.port), timeout=5)
        sock.sendall(b"POST / HTTP/1.1\r\nHost: x\r\nTransfer-Encoding: chunked\r\n\r\n")
        sock.sendall(b"ZZZ\r\nbad\r\n0\r\n\r\n")  # not valid hex
        sock.settimeout(5)
        # A real close (EOF), not a hang -- claude.md #168 also fixed a
        # pre-existing bug where a malformed request only ever set
        # alive=0 without actually closing the fd.
        assert sock.recv(16) == b""
        sock.close()

    def test_a_chunked_response_from_an_upstream_server_is_decoded(self, compile_and_run):
        # The client side (claude.md #162's req.send()) against a real,
        # independent server that responds chunked -- a small
        # hand-rolled one-shot socket server, deliberately not reusing
        # this project's own http implementation for either side of
        # this test, the same "never let a shared bug cancel itself
        # out" discipline the WebSocket client tests already follow.
        # compile_and_run, not compile_and_run_server -- this program
        # never calls openPort() at all (it's a CLIENT), so the
        # server fixture's own "poll until this port accepts
        # connections" readiness check would never succeed.
        import socket as _socket
        import threading

        upstream = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        upstream.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
        upstream.bind(("127.0.0.1", 0))
        upstream.listen(1)
        upstream_port = upstream.getsockname()[1]

        def _serve_one():
            conn, _ = upstream.accept()
            conn.recv(4096)
            conn.sendall(
                b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n"
                b"Connection: close\r\n\r\n"
                b"5\r\nhello\r\n6\r\n world\r\n0\r\n\r\n"
            )
            conn.close()
            upstream.close()

        thread = threading.Thread(target=_serve_one, daemon=True)
        thread.start()

        result = compile_and_run(f"""
        http req = {{'url': 'http://127.0.0.1:{upstream_port}/'}}
        req.send()
        log(`code: ${{req.code}}`)
        log(`body: ${{req.toText()}}`)
        """)
        thread.join(timeout=5)
        assert "code: 200" in result.stdout
        assert "body: hello world" in result.stdout


class TestHttpClient:
    """claude.md #162: `req.send()` -- ZERO arguments -- is the CLIENT
    side, mutating an http value in place with the response (there is
    no separate `fetch()` builtin; two explicit user corrections during
    this feature's design removed it in favor of this single, arity-
    overloaded `.send()`). Verified against a real compile_and_run_server
    instance, itself built from this same compiler -- a genuine
    end-to-end round trip, not a mock."""

    def test_client_send_mutates_the_request_in_place(
            self, compile_and_run_server, compile_and_run):
        server = compile_and_run_server("""
        openPort(__PORT__)
        on request(req:http) {
            req.send({'code': 201, 'body': 'from the server'})
        }
        """)
        result = compile_and_run(f"""
        http req = {{'url': 'http://127.0.0.1:{server.port}/', 'method': 'GET'}}
        req.send()
        log(req.code)
        log(req.toText())
        """)
        assert result.returncode == 0, result.stdout
        assert "201" in result.stdout
        assert "from the server" in result.stdout

    def test_client_send_posts_a_body(self, compile_and_run_server, compile_and_run):
        server = compile_and_run_server("""
        openPort(__PORT__)
        on request(req:http) {
            text body = req.toText()
            req.send({'body': `got:${body}`})
        }
        """)
        result = compile_and_run(f"""
        http req = {{'url': 'http://127.0.0.1:{server.port}/', 'method': 'POST',
                      'body': 'hello from a client'}}
        req.send()
        log(req.toText())
        """)
        assert result.returncode == 0, result.stdout
        assert "got:hello from a client" in result.stdout

    def test_client_send_sets_custom_headers(self, compile_and_run_server, compile_and_run):
        server = compile_and_run_server("""
        openPort(__PORT__)
        on request(req:http) {
            text v = req.headers['authorization']
            req.send({'body': v})
        }
        """)
        result = compile_and_run(f"""
        http req = {{'url': 'http://127.0.0.1:{server.port}/', 'method': 'GET',
                      'headers': {{'authorization': 'bearer example'}}}}
        req.send()
        log(req.toText())
        """)
        assert result.returncode == 0, result.stdout
        assert "bearer example" in result.stdout

    def test_client_send_to_an_unreachable_host_throws(self, compile_and_run):
        # claude.md #162: a genuine network failure -- DNS/connect/TLS --
        # throws via the existing throw/catch mechanism (claude.md #157),
        # the same "this can really fail with real diagnostic text"
        # precedent claude.md #159's JSON parser already established,
        # rather than the runtime's usual "test, don't fail" convention.
        result = compile_and_run("""
        http req = {'url': 'http://127.0.0.1:1/', 'method': 'GET'}
        try {
            req.send()
            log('no throw')
        } catch (e:text) {
            log('caught: ' + e)
        }
        """)
        assert result.returncode == 0, result.stdout
        assert "caught:" in result.stdout


class TestHttpCallbackSemantics:
    """claude.md #163: an optional `callback:func[http]:void` field on
    http -- non-null is what makes req.send() (the client, zero-
    argument form) non-blocking. Checked at the type level only here;
    real runtime behavior lives in TestHttpCallbackRuntime below."""

    def test_callback_field_accepts_a_matching_func(self, parser, semantic):
        source = """
        void func onDone(r:http) { }
        http req = {'url': 'http://example.com', 'callback': onDone}
        """
        semantic.analyze(parser.parse(source))

    def test_callback_field_rejects_a_wrong_signature(self, parser, semantic, errors):
        program = parser.parse("""
        void func wrong(x:int) { }
        http req = {'url': 'http://example.com', 'callback': wrong}
        """)
        with pytest.raises(errors.CompileError, match="'callback' expects"):
            semantic.analyze(program)

    def test_callback_is_read_only(self, parser, semantic, errors):
        program = parser.parse("""
        void func onDone(r:http) { }
        on request(req:http) { req.callback = onDone }
        """)
        with pytest.raises(errors.CompileError, match="read-only"):
            semantic.analyze(program)

    def test_reading_callback_back(self, parser, semantic):
        source = """
        void func onDone(r:http) { }
        http req = {'url': 'http://example.com', 'callback': onDone}
        func[http]:void cb = req.callback
        """
        semantic.analyze(parser.parse(source))


class TestHttpShorthandSemantics:
    """claude.md #164: `{...}.send()` (the receiver itself a raw http
    literal) and its two sugars -- `http req = {...}.send()` and the
    fully anonymous `http {...}` statement, which parser.py desugars
    to the identical `{...}.send()` AST shape."""

    def test_bare_maplit_send_analyzes(self, parser, semantic):
        # A bare `{` at statement start always means a block (pre-
        # existing, unrelated to this feature) -- so `{...}.send()`
        # written directly as a top-level statement is unreachable;
        # the only source spelling that reaches this exact AST shape
        # is `http {...}` (below), which parser.py desugars to it.
        source = "http {'url': 'http://example.com', 'method': 'GET'}"
        semantic.analyze(parser.parse(source))

    def test_maplit_send_rejects_an_unknown_key(self, parser, semantic, errors):
        program = parser.parse("http {'bogus': 'x'}")
        with pytest.raises(errors.CompileError, match="no field"):
            semantic.analyze(program)

    def test_chained_assignment_form_analyzes(self, parser, semantic):
        source = "http req = {'url': 'http://example.com', 'method': 'GET'}.send()"
        semantic.analyze(parser.parse(source))

    def test_anonymous_statement_form_parses_and_analyzes(self, parser, semantic, ast_mod):
        source = "http {'url': 'http://example.com', 'method': 'GET'}"
        program = parser.parse(source)
        # claude.md #164: desugars to an ExprStmt wrapping `{...}.send()`
        # -- confirms the parser-level rewrite actually happened, not
        # just that semantic.py tolerated some other shape.
        assert isinstance(program.body[0], ast_mod.ExprStmt)
        call = program.body[0].expr
        assert isinstance(call, ast_mod.Call)
        assert isinstance(call.callee, ast_mod.Member)
        assert call.callee.prop == "send"
        assert isinstance(call.callee.obj, ast_mod.MapLit)
        semantic.analyze(program)

    def test_anonymous_form_is_distinct_from_a_plain_block(self, parser, semantic, ast_mod):
        # A bare `{` at statement-start (no `http` prefix) is still an
        # ordinary block statement, completely unaffected by this
        # shorthand -- claude.md #164's own parser.py comment on why
        # the check is gated on `http` coming FIRST.
        program = parser.parse("{ int x = 1 }")
        assert not isinstance(program.body[0], ast_mod.ExprStmt)
        semantic.analyze(program)

    def test_anonymous_form_with_no_callback_still_analyzes(self, parser, semantic):
        # No callback at all is legal too -- an anonymous BLOCKING
        # send, result entirely discarded (including any thrown
        # failure never being catchable, since nothing named it) --
        # never a compile error, matching this feature's own "never
        # crashes on something merely useless" convention.
        semantic.analyze(parser.parse("http {'url': 'http://example.com'}"))


class TestHttpCallbackRuntime:
    """claude.md #163: req.send()'s non-blocking form -- a non-null
    `callback` makes the client dispatch return immediately, running
    `callback` later, from the main thread, once the request actually
    completes. Real compile-and-run coverage against a real
    compile_and_run_server instance -- two genuinely separate compiled
    processes, exactly like TestHttpClient above."""

    def test_send_with_a_callback_does_not_block(self, compile_and_run_server, compile_and_run):
        server = compile_and_run_server("""
        openPort(__PORT__)
        on request(req:http) {
            req.send({'code': 200, 'body': 'hello'})
        }
        """)
        result = compile_and_run(f"""
        void func onDone(r:http) {{
            log(`callback: ${{r.code}} ${{r.toText()}}`)
            close(0)
        }}
        http req = {{'url': 'http://127.0.0.1:{server.port}/', 'method': 'GET',
                      'callback': onDone}}
        req.send()
        log('dispatched')
        """)
        assert result.returncode == 0, result.stdout
        # claude.md #163's own point: 'dispatched' -- logged
        # immediately after req.send() returns -- must appear BEFORE
        # the callback's own output, proving the call didn't block.
        dispatched_at = result.stdout.index("dispatched")
        callback_at = result.stdout.index("callback:")
        assert dispatched_at < callback_at, result.stdout
        assert "200 hello" in result.stdout

    def test_callback_failure_path_sets_code_null(self, compile_and_run):
        # 127.0.0.1:1 -- nothing listens there -- exercises the
        # __builtin_setjmp-caught-on-the-worker-thread failure path
        # (see festina_runtime_http.c's own "http -- async client"
        # section) rather than the success path above.
        result = compile_and_run("""
        void func onDone(r:http) {
            if r.code == null {
                log(`failed: ${r.toText()}`)
            } else {
                log(`unexpected success: ${r.code}`)
            }
            close(0)
        }
        http req = {'url': 'http://127.0.0.1:1/', 'method': 'GET', 'callback': onDone}
        req.send()
        """)
        assert result.returncode == 0, result.stdout
        assert "failed:" in result.stdout

    def test_multiple_concurrent_callbacks_all_complete(self, compile_and_run_server, compile_and_run):
        server = compile_and_run_server("""
        openPort(__PORT__)
        on request(req:http) { req.ok() }
        """)
        result = compile_and_run(f"""
        int done = 0
        void func onDone(r:http) {{
            done = done + 1
            if done == 8 {{ close(0) }}
        }}
        int i = 0
        while i < 8 {{
            http req = {{'url': 'http://127.0.0.1:{server.port}/', 'method': 'GET',
                          'callback': onDone}}
            req.send()
            i = i + 1
        }}
        log('all 8 dispatched')
        """)
        assert result.returncode == 0, result.stdout
        assert "all 8 dispatched" in result.stdout

    def test_callback_fires_even_after_its_declaring_function_returns(
            self, compile_and_run_server, compile_and_run):
        # claude.md #163's own point about escape analysis: a callback-
        # mode http value built and sent entirely inside a function
        # that returns immediately afterward must still survive to
        # fire its callback later -- the retain inside
        # festina_http_send_client_dispatch is what makes this safe
        # independent of the declaring function's own lexical scope.
        server = compile_and_run_server("""
        openPort(__PORT__)
        on request(req:http) { req.send({'code': 200, 'body': 'still alive'}) }
        """)
        result = compile_and_run(f"""
        void func onDone(r:http) {{
            log(`escaped: ${{r.toText()}}`)
            close(0)
        }}
        void func fireAndForget() {{
            http req = {{'url': 'http://127.0.0.1:{server.port}/', 'method': 'GET',
                          'callback': onDone}}
            req.send()
        }}
        fireAndForget()
        log('fireAndForget returned')
        """)
        assert result.returncode == 0, result.stdout
        assert "fireAndForget returned" in result.stdout
        assert "escaped: still alive" in result.stdout


class TestHttpShorthandRuntime:
    """claude.md #164: the two `{...}.send()`-based shorthands, verified
    end to end -- `http req = {...}.send()` and the fully anonymous
    `http {...}` statement."""

    def test_chained_assignment_form(self, compile_and_run_server, compile_and_run):
        server = compile_and_run_server("""
        openPort(__PORT__)
        on request(req:http) { req.send({'code': 201, 'body': 'chained'}) }
        """)
        result = compile_and_run(f"""
        void func onDone(r:http) {{
            log(`chained: ${{r.code}} ${{r.toText()}}`)
            close(0)
        }}
        http req = {{'url': 'http://127.0.0.1:{server.port}/', 'method': 'GET',
                      'callback': onDone}}.send()
        log('dispatched via chained form')
        """)
        assert result.returncode == 0, result.stdout
        assert result.stdout.index("dispatched") < result.stdout.index("chained:")
        assert "201 chained" in result.stdout

    def test_anonymous_statement_form(self, compile_and_run_server, compile_and_run):
        server = compile_and_run_server("""
        openPort(__PORT__)
        on request(req:http) { req.send({'code': 202, 'body': 'anon'}) }
        """)
        result = compile_and_run(f"""
        void func onDone(r:http) {{
            log(`anon: ${{r.code}} ${{r.toText()}}`)
            close(0)
        }}
        http {{'url': 'http://127.0.0.1:{server.port}/', 'method': 'GET', 'callback': onDone}}
        log('dispatched via anonymous form')
        """)
        assert result.returncode == 0, result.stdout
        assert result.stdout.index("dispatched") < result.stdout.index("anon:")
        assert "202 anon" in result.stdout

    def test_anonymous_form_blocking_with_no_callback(self, compile_and_run_server, compile_and_run):
        # No callback at all -- a plain, blocking, fire-and-forget
        # send whose response is never read anywhere. Mostly a "this
        # doesn't leak or crash" check (see the leak verification in
        # this feature's own claude.md entry); the ordering assertion
        # from the other two tests doesn't apply here since there's no
        # callback output to compare against.
        server = compile_and_run_server("""
        openPort(__PORT__)
        on request(req:http) { req.ok() }
        """)
        result = compile_and_run(f"""
        http {{'url': 'http://127.0.0.1:{server.port}/', 'method': 'GET'}}
        log('sent, blocking, no callback')
        """)
        assert result.returncode == 0, result.stdout
        assert "sent, blocking, no callback" in result.stdout


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


def _ws_mask_frame(fin, opcode, payload):
    """A trimmed, FIN-controllable sibling of conftest.py's own
    `_WsConn._send_frame` -- that one always sets FIN=1 (this project's
    own server never sends a fragmented frame itself, see
    festina_ws_send_frame's own comment), so it can't build the FIN=0
    fragments TestWebSocketFragmentation needs to send. Deliberately a
    separate, standalone implementation rather than importing
    conftest's private helper, matching this file's own established
    "each test module stays self-contained" style (see
    _find_festina_window's own doc comment below for the same call made
    once already)."""
    b0 = (0x80 if fin else 0x00) | opcode
    length = len(payload)
    if length <= 125:
        header = bytes([b0, 0x80 | length])
    elif length <= 65535:
        header = bytes([b0, 0x80 | 126]) + struct.pack(">H", length)
    else:
        header = bytes([b0, 0x80 | 127]) + struct.pack(">Q", length)
    mask = os.urandom(4)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return header + mask + masked


def _ws_recv_exact(sock, n):
    data = b""
    while len(data) < n:
        chunk = sock.recv(n - len(data))
        if not chunk:
            break
        data += chunk
    return data


def _ws_recv_frame(sock):
    """A trimmed sibling of conftest.py's own `_WsConn.recv_frame` --
    same reasoning as _ws_mask_frame above for not importing it. Every
    read goes through _ws_recv_exact, not a bare sock.recv() -- TCP
    makes no promise even a 2-byte header arrives in a single read, and
    a bare recv() here flaked under full-suite load once already (see
    _recv_one_http_response's own doc comment above, the identical
    lesson for HTTP responses)."""
    hdr = _ws_recv_exact(sock, 2)
    opcode = hdr[0] & 0x0F
    length = hdr[1] & 0x7F
    if length == 126:
        length = struct.unpack(">H", _ws_recv_exact(sock, 2))[0]
    elif length == 127:
        length = struct.unpack(">Q", _ws_recv_exact(sock, 8))[0]
    payload = _ws_recv_exact(sock, length)
    return opcode, payload


class TestWebSocketFragmentation:
    """claude.md #168: RFC 6455 §5.4 message fragmentation -- previously
    unsupported (a FIN=0 frame closed the connection outright as an
    unsupported-data protocol error, api.md's own former "No WebSocket
    fragmentation" limitation). Needs raw frame control conftest.py's
    own `_WsConn` doesn't offer (it always sends FIN=1) -- every test
    here does its own handshake via `server.ws_connect` (to reuse its
    already-verified Sec-WebSocket-Accept checking) but drops to
    `_ws_mask_frame`/`_ws_recv_frame` for the frames that matter."""

    def test_a_fragmented_text_message_is_reassembled(self, compile_and_run_server):
        server = compile_and_run_server("""
        openPort(__PORT__)
        on request(req:http) { req.upgrade() }
        on message(s:socket, msg:blob) { s.send(`echo:${msg.toText()}`) }
        """)
        ws, status_line, _, _ = server.ws_connect("/ws")
        assert b"101" in status_line
        ws.sock.sendall(_ws_mask_frame(fin=False, opcode=0x1, payload=b"Hello, "))
        ws.sock.sendall(_ws_mask_frame(fin=True, opcode=0x0, payload=b"World!"))
        opcode, payload = _ws_recv_frame(ws.sock)
        assert opcode == 0x1
        assert payload == b"echo:Hello, World!"
        ws.close()

    def test_a_fragmented_binary_message_is_reassembled(self, compile_and_run_server):
        server = compile_and_run_server("""
        openPort(__PORT__)
        on request(req:http) { req.upgrade() }
        on message(s:socket, msg:blob) { s.send(msg) }
        """)
        ws, _, _, _ = server.ws_connect("/ws")
        ws.sock.sendall(_ws_mask_frame(fin=False, opcode=0x2, payload=b"\x00\x01"))
        ws.sock.sendall(_ws_mask_frame(fin=False, opcode=0x0, payload=b"\x02\x03"))
        ws.sock.sendall(_ws_mask_frame(fin=True, opcode=0x0, payload=b"\x04\x05"))
        opcode, payload = _ws_recv_frame(ws.sock)
        assert opcode == 0x2
        assert payload == b"\x00\x01\x02\x03\x04\x05"
        ws.close()

    def test_a_control_frame_interleaved_between_fragments_is_answered_immediately(
            self, compile_and_run_server):
        # RFC 6455 §5.4: control frames MAY be injected in the middle
        # of a fragmented message. The ping's own pong must arrive
        # BEFORE the still-in-progress message is reassembled, and the
        # message itself must still reassemble correctly afterward --
        # neither disturbs the other's own state.
        server = compile_and_run_server("""
        openPort(__PORT__)
        on request(req:http) { req.upgrade() }
        on message(s:socket, msg:blob) { s.send(`echo:${msg.toText()}`) }
        """)
        ws, _, _, _ = server.ws_connect("/ws")
        ws.sock.sendall(_ws_mask_frame(fin=False, opcode=0x1, payload=b"part1-"))
        ws.sock.sendall(_ws_mask_frame(fin=True, opcode=0x9, payload=b"pingdata"))
        opcode, payload = _ws_recv_frame(ws.sock)
        assert (opcode, payload) == (0xA, b"pingdata")
        ws.sock.sendall(_ws_mask_frame(fin=True, opcode=0x0, payload=b"part2"))
        opcode, payload = _ws_recv_frame(ws.sock)
        assert (opcode, payload) == (0x1, b"echo:part1-part2")
        ws.close()

    def test_an_orphan_continuation_frame_is_a_protocol_error(self, compile_and_run_server):
        server = compile_and_run_server("""
        openPort(__PORT__)
        on request(req:http) { req.upgrade() }
        on message(s:socket, msg:blob) { s.send(msg) }
        """)
        ws, _, _, _ = server.ws_connect("/ws")
        ws.sock.sendall(_ws_mask_frame(fin=True, opcode=0x0, payload=b"orphan"))
        ws.sock.settimeout(5)
        opcode, payload = _ws_recv_frame(ws.sock)
        assert opcode == 0x8  # close
        assert struct.unpack(">H", payload[:2])[0] == 1002  # protocol error
        ws.close()

    def test_a_fragmented_control_frame_is_a_protocol_error(self, compile_and_run_server):
        # RFC 6455 §5.4: control frames MUST NOT be fragmented.
        server = compile_and_run_server("""
        openPort(__PORT__)
        on request(req:http) { req.upgrade() }
        on message(s:socket, msg:blob) { s.send(msg) }
        """)
        ws, _, _, _ = server.ws_connect("/ws")
        ws.sock.sendall(_ws_mask_frame(fin=False, opcode=0x9, payload=b"bad"))
        ws.sock.settimeout(5)
        opcode, payload = _ws_recv_frame(ws.sock)
        assert opcode == 0x8
        assert struct.unpack(">H", payload[:2])[0] == 1002
        ws.close()

    def test_a_new_message_cannot_start_mid_reassembly(self, compile_and_run_server):
        server = compile_and_run_server("""
        openPort(__PORT__)
        on request(req:http) { req.upgrade() }
        on message(s:socket, msg:blob) { s.send(msg) }
        """)
        ws, _, _, _ = server.ws_connect("/ws")
        ws.sock.sendall(_ws_mask_frame(fin=False, opcode=0x1, payload=b"first-"))
        # A second text frame before the first one's own continuation
        # finished -- not a valid continuation at all.
        ws.sock.sendall(_ws_mask_frame(fin=True, opcode=0x1, payload=b"second"))
        ws.sock.settimeout(5)
        opcode, payload = _ws_recv_frame(ws.sock)
        assert opcode == 0x8
        assert struct.unpack(">H", payload[:2])[0] == 1002
        ws.close()

    def test_an_oversized_fragmented_message_is_closed_as_too_big(self, compile_and_run_server):
        # FESTINA_HTTP_MAX_BUFFER (8MB) bounds a reassembled message's
        # cumulative size the same way it bounds everything else this
        # runtime buffers per-connection -- large fragments, not many
        # small ones, to keep this test's own wall-clock cost down.
        server = compile_and_run_server("""
        openPort(__PORT__)
        on request(req:http) { req.upgrade() }
        on message(s:socket, msg:blob) { s.send(msg) }
        """)
        ws, _, _, _ = server.ws_connect("/ws")
        chunk = b"x" * (1024 * 1024)  # 1MB
        ws.sock.sendall(_ws_mask_frame(fin=False, opcode=0x1, payload=chunk))
        for i in range(8):  # 8 more MB -> 9MB total, past the 8MB cap
            ws.sock.sendall(_ws_mask_frame(fin=(i == 7), opcode=0x0, payload=chunk))
        ws.sock.settimeout(10)
        opcode, payload = _ws_recv_frame(ws.sock)
        assert opcode == 0x8
        assert struct.unpack(">H", payload[:2])[0] == 1009  # message too big
        ws.close()


class TestPlatformAndWasmGating:
    """there is no wasm32-wasi backend for http at all -- checked
    without needing a real toolchain (the same tier
    _check_wasm_feature_supported's own graphics/audio/exec tests in
    test_wasm.py already sit in). darwin still gates a backend that
    EXISTS (built, CI-compiled) but awaits real-hardware verification,
    the same shape audio/graphics established there. win32 no longer
    does -- claude.md #169 retired that gate once a real Windows CI run
    (not just the MinGW cross-compile claude.md #151's own Windows
    round had relied on) exercised the winsock2 backend end to end.
    claude.md #166 lifted the original http/graphics exclusivity
    restriction -- see TestGraphicsAndHttp below for that combination's
    own compile-and-run coverage."""

    def test_http_and_graphics_together_compiles_cleanly(self, cli_mod, tmp_path):
        # claude.md #166: this used to be rejected outright at compile
        # time (claude.md #151's original restriction, when main()
        # could only ever block in ONE of festina_run_event_loop/
        # festina_run_http_loop). No CompileError any more -- see
        # TestGraphicsAndHttp for proof both actually WORK together,
        # not just that compilation succeeds.
        #
        # claude.md #170: this program opens a real window (on
        # mouseDown), so on darwin it must go through compile_file_or_skip
        # like every other real-window-opening test -- the darwin
        # graphics gate (still active; unrelated to claude.md #169's
        # win32 one) would otherwise surface as a raw macOS CI failure
        # instead of the skip every other platform-conditional windowed
        # test already gets. Never actually reached before this,
        # because the macOS job's own #157 regression (fixed alongside
        # this) meant the whole suite failed to even compile the
        # runtime until now.
        from tests.conftest import compile_file_or_skip
        src = tmp_path / "main.f"
        src.write_text(
            "openPort(8080)\n"
            "on request(req:http) { req.ok() }\n"
            "on mouseDown(x:int, y:int, button:int) { }\n",
            encoding="utf-8",
        )
        compile_file_or_skip(cli_mod, str(src), str(tmp_path / "out"), cc="clang")  # no raise

    def test_http_is_not_gated_on_windows(self, cli_mod):
        # claude.md #169 retired this gate: a real Windows CI run
        # (triggered specifically to check this -- windows.md Phase 4
        # had only ever been MinGW cross-compiled before) exercised the
        # winsock2 backend end to end -- openPort()/on request/on
        # upgrade/on message/on socketClose all tested clean -- so http
        # no longer waits behind FESTINA_ENABLE_WINDOWS_HTTP the way
        # claude.md #151 originally set it up. One real gap that same
        # run found, graceful shutdown, is documented in api.md rather
        # than gated here -- openPort() itself has always worked with
        # no shutdown handling at all; on exit()/draining is a layer on
        # top this gate was never covering.
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


def _find_festina_window(display, timeout=20):
    """A trimmed copy of test_codegen.py's own _find_window -- not
    imported from there (this file's own established style keeps each
    test module self-contained, matching e.g. test_async_io.py never
    reaching into test_http.py for its own http-server-combined case).
    See that function's own doc comment for why the timeout is generous
    insurance, not a figure ever expected to be approached."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = subprocess.run(
            ["xdotool", "search", "--name", "Festina"],
            env=dict(os.environ, DISPLAY=display),
            capture_output=True, text=True,
        )
        wids = result.stdout.split()
        if wids:
            return wids[0]
        time.sleep(0.2)
    raise AssertionError("the Festina canvas window never appeared")


class TestGraphicsAndHttp:
    """claude.md #166: openPort() combined with graphics -- previously
    rejected outright at compile time (see
    TestPlatformAndWasmGating.test_http_and_graphics_together_compiles_cleanly
    just above). festina_run_event_loop (festina_runtime_graphics.c)
    now services an open port itself through a hook seam
    (festina_set_http_service_hooks, festina_runtime.c/.h), so main()
    still ever blocks in exactly one loop -- these tests prove BOTH
    halves of that combination actually work, not just that compiling
    it no longer raises. Needs a working DISPLAY (run_graphics_program,
    tests/conftest.py -- the same Xvfb-backed tier test_codegen.py's own
    TestGraphics uses), so this skips cleanly under the same tier that
    already does."""

    def test_a_request_is_served_while_the_window_is_open(self, run_graphics_program):
        # No xdotool interaction needed for this one -- just proves the
        # http side of the combination actually answers a real request
        # while festina_run_event_loop, not festina_run_http_loop, is
        # the loop running.
        import http.client
        import socket as _socket
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        source = (
            f"openPort({port})\n"
            "on request(req:http) { req.send({'code': 200, 'body': 'combined-ok'}) }\n"
        )
        proc, _stdout_path = run_graphics_program(source)
        try:
            deadline = time.time() + 10
            connected = False
            while time.time() < deadline:
                if proc.poll() is not None:
                    pytest.fail(f"server process exited early (code {proc.returncode})")
                try:
                    probe = _socket.create_connection(("127.0.0.1", port), timeout=0.2)
                    probe.close()
                    connected = True
                    break
                except OSError:
                    time.sleep(0.05)
            assert connected, "server never started listening while the window was open"

            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/")
            resp = conn.getresponse()
            assert resp.status == 200
            assert resp.read() == b"combined-ok"
            conn.close()
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_window_input_and_requests_both_work_in_the_same_process(
            self, run_graphics_program, x_display):
        # The other half: a real mouse click still reaches `on
        # mouseDown` while a port is open, interleaved with real http
        # requests -- proving festina_run_event_loop's own window-event
        # dispatch is unaffected by also servicing http (and vice
        # versa, confirmed by the two requests below succeeding both
        # before AND after the click).
        import http.client
        import socket as _socket
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        source = (
            f"openPort({port})\n"
            "on request(req:http) { req.send({'code': 200, 'body': 'ok'}) }\n"
            "on mouseDown(x:int, y:int, button:int) { log(`down ${x} ${y}`) }\n"
        )
        proc, stdout_path = run_graphics_program(source)
        try:
            deadline = time.time() + 10
            connected = False
            while time.time() < deadline:
                if proc.poll() is not None:
                    pytest.fail(f"server process exited early (code {proc.returncode})")
                try:
                    probe = _socket.create_connection(("127.0.0.1", port), timeout=0.2)
                    probe.close()
                    connected = True
                    break
                except OSError:
                    time.sleep(0.05)
            assert connected, "server never started listening while the window was open"

            def _get_ok():
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request("GET", "/")
                resp = conn.getresponse()
                status, body = resp.status, resp.read()
                conn.close()
                return status, body

            assert _get_ok() == (200, b"ok")

            wid = _find_festina_window(x_display)
            env = dict(os.environ, DISPLAY=x_display)
            subprocess.run(["xdotool", "mousemove", "--window", wid, "42", "24"],
                            env=env, check=True)
            subprocess.run(["xdotool", "click", "--window", wid, "1"], env=env, check=True)

            deadline = time.time() + 20
            text = ""
            while time.time() < deadline:
                text = stdout_path.read_text()
                if "down 42 24" in text:
                    break
                time.sleep(0.1)
            assert "down 42 24" in text

            assert _get_ok() == (200, b"ok")
        finally:
            proc.terminate()
            proc.wait(timeout=5)
