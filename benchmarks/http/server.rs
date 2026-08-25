// See server.f's own comment for what this benchmark measures and why
// every server here shares the same connection-per-request,
// single-threaded-accept-loop shape (matching this project's own
// festina_runtime_http.c: single-threaded, no keep-alive). No external
// crate -- just std::net, the same "rustc -O directly, no cargo" build
// convention every other benchmarks/*.rs file here already uses.

use std::env;
use std::io::{Read, Write};
use std::net::TcpListener;

fn main() {
    let port: u16 = env::args()
        .nth(1)
        .and_then(|s| s.parse().ok())
        .unwrap_or(8080);
    let listener = TcpListener::bind(("127.0.0.1", port)).expect("bind failed");

    for stream in listener.incoming() {
        let mut stream = match stream {
            Ok(s) => s,
            Err(_) => continue,
        };

        // Read until the request line + headers are fully buffered (a
        // blank line) -- only the first line's path actually matters
        // to this handler, but the body has to be drained off the wire
        // before responding the same way every server here does.
        let mut buf = [0u8; 8192];
        let mut n = 0;
        while n < buf.len() {
            match stream.read(&mut buf[n..]) {
                Ok(0) => break,
                Ok(read) => {
                    n += read;
                    if buf[..n].windows(4).any(|w| w == b"\r\n\r\n") {
                        break;
                    }
                }
                Err(_) => break,
            }
        }

        let request = String::from_utf8_lossy(&buf[..n]);
        let path = request
            .lines()
            .next()
            .and_then(|line| line.split_whitespace().nth(1))
            .unwrap_or("/");

        let (content_type, body): (&str, String) = if path == "/json" {
            ("application/json", "{\"message\":\"Hello, world!\"}".to_string())
        } else {
            ("text/plain", "Hello, world!".to_string())
        };

        let response = format!(
            "HTTP/1.1 200 OK\r\nContent-Type: {}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
            content_type,
            body.len(),
            body
        );
        let _ = stream.write_all(response.as_bytes());
    }
}
