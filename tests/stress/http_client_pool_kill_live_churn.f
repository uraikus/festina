// claude.md #248: the leak-freedom half of outbound connection reuse
// that only a sanitizer run can actually confirm, mirroring
// thread_db_kill_live_churn.f's own kill()/live() shape exactly.
// `worker` here declares no `on request`/`openPort()` of its own --
// it's a pure OUTBOUND client, the exact shape codegen.py's widened
// `has_http_context or self.uses_http` condition exists for (a
// client-only thread used to get NO teardown hook wired at all, so
// festina_thread_http_teardown -- and with it, this thread's own
// pooled connections -- would never run on kill(), leaking every
// pooled slot's `strdup`'d host string forever). Each `on load()` makes
// 20 real requests to a real upstream before the thread is killed and
// respawned -- 300 cycles is enough pooled state, built and torn down
// repeatedly, for LeakSanitizer to catch a real regression here with
// certainty, not just get lucky.
//
// The upstream is a SEPARATE, never-killed `upstream` thread with its
// own private port -- deliberately NOT main's own port. `worker.kill()`
// blocks (joins) the calling thread until `worker` actually stops; if
// `worker` were mid-request against MAIN's own port when killed, main
// would be off blocked inside kill() at the exact moment it needed to
// be servicing the very request `worker` is waiting on -- the
// self-directed-request deadlock this project's docs already warn
// about (thread_giverequest_churn.f's own "driver, a THIRD thread,
// deliberately not worker itself" precedent, the identical hazard,
// found here first as a genuine hang before being designed around the
// same way). `upstream` is never killed, so it's always there to
// answer, and kill()/live() stay fully deterministic, exactly like
// thread_db_kill_live_churn.f's own.

// §20.2: every thread starts before main's first top-level statement,
// in NO defined order -- so `worker` below can be making requests while
// `upstream` has not reached its own openPort() yet. That is a real
// race and it fired: `fail: fetch: could not connect to
// '127.0.0.1:18305'`, four runs out of four under the load of a full
// leak_stress sweep, and intermittently on its own.
//
// It cannot be retried away. A failed req.send() is festina_fail(),
// which exits(1) and is not catchable by try/catch (api.md's own
// "no enclosing try" rule is about `throw`, and this is not one), so
// `worker` gets exactly one attempt and it has to be after `upstream`
// is listening. The other port-using stress programs in this directory
// sidestep the question rather than answer it -- their client work
// hangs off `on message`, which main only sends after its OWN
// top-level openPort() -- but this one's whole subject is what a
// thread's `on load()` does, so the requests have to stay there.
//
// So `upstream` publishes a marker and `worker` waits for it. The
// marker is sound because openPort() binds and listens SYNCHRONOUSLY
// (festina_runtime_http.c: bind() then listen(fd, 128) before it
// returns), so a client that can see the file can already connect --
// the backlog holds the connection until upstream's own loop accepts
// it. A marker written before listen() would just move the race.
//
// Only the first of the 301 `on load()` runs ever waits; every later
// one finds the file already there and spends one stat() on it.
thread upstream {
    on load() {
        openPort(18305)
        blob ready = 'upstream.ready'
        ready.write('1')
    }
    on request(req:http) {
        req.send({'code': 200, 'body': 'ok'})
    }
}

thread worker {
    on load() {
        // Bounded, and it falls THROUGH to the requests when it runs
        // out rather than reporting the timeout itself. An upstream
        // that never came up then fails on the next line with the
        // same "could not connect to '127.0.0.1:18305'" this wait
        // exists to prevent -- which is the honest outcome, and says
        // more than a message about a marker file would.
        //
        // A `fail()` here would be louder still, and cost more than it
        // is worth: bootstrap/codegen.f has not ported fail(), so
        // writing one moves this file from COMPARED to "not ported
        // yet" in the IR differential. Buying a better error message
        // with a silently skipped corpus file is a bad trade.
        blob ready = 'upstream.ready'
        int deadline = now() + 10000
        while !ready.exists() && now() < deadline {
        }
        int i = 0
        while i < 20 {
            http req = {'url': 'http://127.0.0.1:18305/', 'method': 'GET'}
            req.send()
            i = i + 1
        }
    }
}

int CYCLES = 300
int cycle = 0
while cycle < CYCLES {
    worker.kill()
    worker.live(void (ok:bool) => log(''))
    cycle = cycle + 1
}
log('done')
close(0)
