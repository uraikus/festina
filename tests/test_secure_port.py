"""claude.md #160: openSecurePort(port:int, key:blob) -- the TLS
counterpart to claude.md #151's openPort(). Parser/semantic coverage
(the fixed (int, blob) signature, blob-only for the key argument) and
real compile-and-run coverage (a genuine TLS handshake + HTTP exchange
against Python's own `ssl` module as an independent client) live
together in this one file, the same shape test_http.py already uses
for openPort() itself. See runtime/festina_runtime_https.c's own top
comment for the mbedTLS design this exercises, and conftest.py's
compile_and_run_secure_server fixture for how a real self-signed test
certificate gets generated per test."""
import pytest


class TestParsingAndSignature:
    def test_open_secure_port_takes_int_and_blob(self, parser, semantic, errors):
        program = parser.parse("""
        blob key = 'server.pem'
        openSecurePort(8443, key)
        """)
        semantic.analyze(program)

    def test_wrong_port_type_is_rejected(self, parser, semantic, errors):
        program = parser.parse("""
        blob key = 'server.pem'
        openSecurePort('8443', key)
        """)
        with pytest.raises(errors.CompileError, match="argument 1"):
            semantic.analyze(program)

    def test_wrong_key_type_is_rejected(self, parser, semantic, errors):
        program = parser.parse("openSecurePort(8443, 'not a blob')")
        with pytest.raises(errors.CompileError, match="argument 2"):
            semantic.analyze(program)

    def test_wrong_arity_is_rejected(self, parser, semantic, errors):
        program = parser.parse("openSecurePort(8443)")
        with pytest.raises(errors.CompileError, match="expects 2 argument"):
            semantic.analyze(program)

    def test_shares_on_request_with_plain_http(self, parser, semantic):
        # claude.md #160: openSecurePort() shares the whole `on
        # request`/`on upgrade`/`on message`/`on socketClose` surface
        # openPort() already has -- no separate handler story for TLS.
        program = parser.parse("""
        blob key = 'server.pem'
        on request(req:http) { req.ok() }
        openSecurePort(8443, key)
        """)
        semantic.analyze(program)


class TestRuntimeBehavior:
    """Real compiled-binary coverage -- a genuine TLS 1.2/1.3 handshake
    (whichever mbedTLS and the Python `ssl` client negotiate) against a
    compiled Festina program, driven from festina_run_http_loop's own
    poll() event loop exactly the way a real deployment would use it."""

    def test_basic_https_request_response(self, compile_and_run_secure_server):
        server = compile_and_run_secure_server("""
        blob key = '__CERT_PATH__'
        on request(req:http) {
            req.send({'code': 200, 'body': 'secure hello'})
        }
        openSecurePort(__PORT__, key)
        """)
        status, _headers, body = server.https_get("/")
        assert status == 200
        assert body == b"secure hello"

    def test_default_response_when_the_handler_never_responds(self, compile_and_run_secure_server):
        server = compile_and_run_secure_server("""
        blob key = '__CERT_PATH__'
        on request(req:http) { }
        openSecurePort(__PORT__, key)
        """)
        status, _headers, body = server.https_get("/")
        assert status == 200
        assert body == b""

    def test_request_fields_are_readable_over_tls(self, compile_and_run_secure_server):
        server = compile_and_run_secure_server("""
        blob key = '__CERT_PATH__'
        on request(req:http) {
            req.send({'code': 200, 'body': `${req.method} ${req.url}`})
        }
        openSecurePort(__PORT__, key)
        """)
        status, _headers, body = server.https_get("/hello")
        assert status == 200
        assert body.decode() == f"GET https://127.0.0.1:{server.port}/hello"

    def test_plaintext_request_to_a_tls_port_does_not_get_a_plaintext_response(
            self, compile_and_run_secure_server):
        # claude.md #160: a raw, non-TLS client talking to a TLS-only
        # port should never see a valid HTTP response come back -- the
        # bytes it sent are just the opening of a TLS handshake mbedTLS
        # never recognizes, from mbedTLS's own point of view raw
        # "GET / HTTP/1.1..." text is simply not a valid TLS
        # ClientHello record.
        server = compile_and_run_secure_server("""
        blob key = '__CERT_PATH__'
        on request(req:http) { req.ok() }
        openSecurePort(__PORT__, key)
        """)
        sock = server.raw_connect()
        sock.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
        sock.settimeout(2)
        try:
            data = sock.recv(4096)
        except OSError:
            data = b""
        sock.close()
        assert not data.startswith(b"HTTP/1.1 200")

    def test_multiple_requests_across_separate_connections(self, compile_and_run_secure_server):
        # claude.md #160's own leak-adjacent concern: does the
        # per-connection TLS state (mbedtls_ssl_context) actually get
        # torn down and rebuilt cleanly across many separate TLS
        # connections, not just survive one? (Not a Valgrind run itself
        # -- pins the OBSERVABLE behavior across repeated real
        # handshakes.)
        server = compile_and_run_secure_server("""
        blob key = '__CERT_PATH__'
        on request(req:http) { req.send({'code': 200, 'body': 'ok'}) }
        openSecurePort(__PORT__, key)
        """)
        for _ in range(10):
            status, _headers, body = server.https_get("/")
            assert status == 200
            assert body == b"ok"
