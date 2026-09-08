// claude.md #248: outbound keep-alive connection reuse -- real,
// concurrent, at-volume proof that `req.send()`'s new thread-local
// connection cache (festina_client_pool_take/_put in
// festina_runtime_http.c) is genuinely safe under real contention, not
// just correct in the single-threaded case the hand probes already
// confirmed. Four separate driver threads (each with its OWN private
// `__thread` pool -- no locking between them by construction) all hit
// the SAME upstream host:port concurrently, at volume, via claude.md
// #245's bare `pool.postMessage(x)` auto-select so the TOTAL messages
// spread across all four rather than favoring one. A leak in the pool
// slot's own `strdup`'d host string, a double-free of a reused fd, or
// a genuine data race in what's supposed to be purely thread-local
// state would all show up here -- under scripts/leak_stress.sh and
// scripts/thread_tsan_stress.sh respectively -- that a single-threaded
// probe program could never exercise.
//
// Correctness is COUNT-based (a `done` reply counter) plus a
// `failures` counter that must stay 0, the same convention every
// other stress file in this suite already uses.

int TOTAL = 3000
int done = 0
int failures = 0

on message(w:thread, msg:int) {
    done = done + 1
    if msg == 0 {
        failures = failures + 1
    }
    if done >= TOTAL {
        log('http client pool churn done')
        log(done)
        log(failures)
        if failures > 0 {
            close(1)
        }
        close(0)
    }
}

thread drivers[4] {
    on message(sender:thread, msg:int) {
        http req = {'url': 'http://127.0.0.1:18304/', 'method': 'GET'}
        req.send()
        bool ok = req.code == 200 && req.toText() == 'ok'
        if ok {
            postMessage(1)
        } else {
            postMessage(0)
        }
    }
}

on request(req:http) {
    req.send({'code': 200, 'body': 'ok'})
}

openPort(18304)
int i = 0
while i < TOTAL {
    drivers.postMessage(1)
    i = i + 1
}
