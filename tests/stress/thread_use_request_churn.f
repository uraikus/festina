// claude.md #246: `on request use NAME` -- parser sugar for
// `on request(req:http?) { NAME.giveRequest(req) }` -- plus the bare
// `pool.giveRequest(r)` it desugars to when NAME is a pool. This is the
// concurrent, at-volume proof that the sugar's desugared form actually
// exercises the SAME bare-pool handle-selection path claude.md #245's
// own thread_pool_auto_churn.f already stresses for postMessage, but
// now for a live connection hand-off: `driver` makes a real blocking
// client request, main's own `on request use pool` line hands the
// connection to whichever of the pool's 3 instances
// festina_thread_pool_select currently reads as idle, and that instance
// answers directly on the underlying socket from its own OS thread.
//
// Correctness is COUNT-based (a `done` reply counter) plus a `failures`
// counter that must stay 0, matching thread_giverequest_churn.f's own
// convention. TOTAL=100 for the same reason that file gives: each cycle
// is a real TCP round trip, so 100 is enough to make a genuine race in
// the auto-selected hand-off show up under ThreadSanitizer while keeping
// wall-clock time reasonable under TSan's own slowdown.

int TOTAL = 100
int done = 0
int failures = 0

on message(w:thread, msg:int) {
    done = done + 1
    if msg == 0 {
        failures = failures + 1
    }
    if done >= TOTAL {
        log('on request use churn done')
        log(done)
        log(failures)
        if failures > 0 {
            close(1)
        }
        close(0)
    }
}

thread pool[3] {
    int served = 0
    on request(req:http) {
        served = served + 1
        req.send({'code': 200, 'body': 'handled by pool'})
    }
}

thread driver {
    on message(sender:thread, msg:int) {
        http req = {'url': 'http://127.0.0.1:18303/', 'method': 'GET'}
        req.send()
        bool ok = req.code == 200 && req.toText() == 'handled by pool'
        if ok {
            postMessage(1)
        } else {
            postMessage(0)
        }
    }
}

on request use pool

openPort(18303)
int i = 0
while i < TOTAL {
    driver.postMessage(1)
    i = i + 1
}
