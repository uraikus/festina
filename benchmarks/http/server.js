// See server.f's own comment for what this benchmark measures. Uses
// Bun's own native Bun.serve() -- unlike the other three languages
// here, there's no reason to hand-roll a socket loop in a runtime that
// ships its own fast native HTTP server, matching this whole suite's
// "each language uses its own normal toolchain" rule (benchmark.md).
// "Connection: close" is set explicitly on every response so this
// server closes each connection after one response the same way every
// other server here does -- otherwise Bun's own keep-alive support
// (which the hand-rolled Rust/Go/Festina servers don't have) would be
// what wins the comparison, not connection-accept + parse + respond
// speed.
const port = parseInt(process.argv[2] || "8080", 10);

Bun.serve({
    port,
    hostname: "127.0.0.1",
    fetch(req) {
        const url = new URL(req.url);
        if (url.pathname === "/json") {
            return new Response(JSON.stringify({ message: "Hello, world!" }), {
                headers: { "Content-Type": "application/json", "Connection": "close" },
            });
        }
        return new Response("Hello, world!", {
            headers: { "Content-Type": "text/plain", "Connection": "close" },
        });
    },
});
