// claude.md #212/#213: an HTTP server that answers requests on more
// than one real OS thread at once -- Festina's own HTTP event loop
// (festina_run_http_loop, one connection serviced at a time) is
// otherwise single-threaded, so a slow request normally delays every
// other connection's own turn (see api.md's own note on this). A
// `thread pool[N] { on request(req:http) { ... } }` gives each
// worker its own fully private, `on request`-driven context (no
// `openPort()` of its own needed -- see api.md's "Per-thread HTTP
// context"); the main program accepts every connection on the one
// real listening port and hands each live request straight to the
// next worker in line via `NAME.giveRequest(r)` (api.md's "Live
// connection hand-off"), so N requests can genuinely be computed in
// parallel, on N different CPU cores, before any of them respond.
//
// '/slow' simulates real per-request CPU work (not I/O) with a
// closed-form-resistant polynomial-hash loop -- the same technique
// benchmarks/loop_sum.f uses so an optimizer can't fold the loop away
// into a constant. '/' answers immediately, with no work at all, so
// you can watch it stay fast even while '/slow' requests are still
// being crunched on other workers.
//
// Build and run with:
//
//   ./bin/festina examples/threaded_http_server.f -o threaded_http_server
//   ./threaded_http_server
//
// then, in another terminal:
//
//   curl http://127.0.0.1:8080/
//   curl http://127.0.0.1:8080/slow
//
// Fire several `/slow` requests at once (e.g. `for i in 1 2 3 4; do
// curl http://127.0.0.1:8080/slow & done; wait`) and watch them all
// finish in roughly the time ONE takes alone, not four times that --
// see benchmarks/http_threaded/ for a real, measured comparison
// against a single-threaded baseline under load.

int WORKER_COUNT = 4

thread workers[4] {
    // claude.md #195: a thread's own body can't see a top-level
    // global (isolation) -- this is that same 2,000,000 declared
    // thread-private instead, one independent copy per pool
    // instance. Tuned to take a few milliseconds of real CPU time --
    // long enough to make the difference between "one worker" and
    // "four workers" obvious under concurrent load, short enough
    // that a single request still feels instant by hand.
    int workIterations = 2000000
    on request(req:http) {
        int total = 0
        int i = 0
        while i < workIterations {
            total = (total * 1000003 + i) % 1000000007
            i = i + 1
        }
        map[text] headers = {}
        headers['Content-Type'] = 'text/plain'
        req.send({'code': 200, 'body': `computed ${total} on a worker thread\n`,
                   'headers': headers})
    }
}

// Round-robins each live connection across the pool -- an ordinary
// top-level `int`, since main's own `on request` isn't inside any
// thread body and can freely read/write program-wide state the same
// as any other top-level code.
int nextWorker = 0

on request(req:http?) {
    url u = parseURL(req.url)
    if u.pathname == '/slow' {
        workers[nextWorker].giveRequest(req)
        nextWorker = (nextWorker + 1) % WORKER_COUNT
    } else {
        map[text] headers = {}
        headers['Content-Type'] = 'text/plain'
        req.send({'code': 200, 'body': 'instant -- try /slow\n', 'headers': headers})
    }
}

int port = 8080
if argv.length > 1 {
    port = argv[1].toInt()
}
log(`listening on http://127.0.0.1:${port} (routes: / and /slow)`)
openPort(port)
