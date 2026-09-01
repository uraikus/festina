// claude.md #215: the single-threaded baseline for
// benchmarks/http_threaded/run_http_threaded_benchmark.py -- answers
// '/slow' by doing real, closed-form-resistant CPU work (the same
// polynomial-hash technique benchmarks/loop_sum.f uses) directly in
// the one top-level `on request` handler, on Festina's own single
// HTTP event-loop thread. Under concurrent load this is exactly the
// case api.md's own HTTP-client-form note already describes: "a slow
// [handler] delays every other connection's own turn" -- every
// request here queues up behind whichever one is currently
// crunching, so throughput is bounded by 1 / (time to do the work
// once), no matter how many connections `wrk` opens. server_pool.f is
// the identical logic, spread across a `thread pool[N]` instead, for
// comparison.
//
// The port is read from argv, the same convention
// benchmarks/http/server.f already uses, so the runner can pick a
// free one.

int workIterations = 2000000

on request(req:http) {
    url u = parseURL(req.url)
    map[text] headers = {}
    headers['Content-Type'] = 'text/plain'
    if u.pathname == '/slow' {
        int total = 0
        int i = 0
        while i < workIterations {
            total = (total * 1000003 + i) % 1000000007
            i = i + 1
        }
        req.send({'code': 200, 'body': `computed ${total}\n`, 'headers': headers})
    } else {
        req.send({'code': 200, 'body': 'instant -- try /slow\n', 'headers': headers})
    }
}

int port = 8080
if argv.length > 1 {
    port = argv[1].toInt()
}
openPort(port)
