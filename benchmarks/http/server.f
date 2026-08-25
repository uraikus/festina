// claude.md #152's own HTTP benchmark: a minimal server answering two
// routes -- '/' (plaintext) and '/json' (a small JSON body, rendered
// through the same struct-to-JSON req.send() path api.md documents,
// not hand-built text) -- timed by
// benchmarks/http/run_http_benchmarks.py against equivalent
// hand-rolled servers in Rust and Go, plus Bun's own native HTTP
// server. See that script's own docstring and benchmark.md's HTTP
// section for the full methodology, in particular why every server
// here (including this one) closes the connection after each response
// rather than using keep-alive: this runtime's own documented
// scope (api.md's HTTP Limitations -- "No keep-alive. Every response
// closes the connection afterward") is what the other three servers
// deliberately match, so the comparison measures connection-accept +
// parse + respond, not which server happens to support keep-alive.
//
// The port is read from argv (claude.md #150) so the benchmark runner
// can pick a free one rather than hardcoding 8080, defaulting to 8080
// when run directly with no argument.

struct JsonMessage {
    message:text
}

int port = 8080
if argv.length > 1 {
    port = argv[1].toInt()
}

on request(req:http) {
    map[text] headers = {}
    if req.path == '/json' {
        headers['Content-Type'] = 'application/json'
        JsonMessage m
        m.message = 'Hello, world!'
        req.send(m, 200, headers)
    } else {
        headers['Content-Type'] = 'text/plain'
        req.send('Hello, world!', 200, headers)
    }
}

openPort(port)
