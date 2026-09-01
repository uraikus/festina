// claude.md #213: `NAME.giveRequest(r)` -- live connection hand-off --
// real, concurrent, at-volume proof of the highest-stakes cross-thread
// resource transfer in the whole thread-extensions plan: main accepts
// a real inbound HTTP connection, detaches it from its OWN
// `__thread`-backed connection table (festina_conn_detach), hands it
// to `worker` via the new GIVE_REQUEST message kind, and `worker`'s
// own `on request` -- running on a COMPLETELY DIFFERENT OS thread from
// the one that accepted the connection -- answers it directly on the
// SAME underlying socket. `driver` (a THIRD thread, deliberately not
// `worker` itself) makes the real blocking client request that
// triggers each cycle, so nothing here can hit the self-directed-
// client-request deadlock claude.md #212's own api.md section already
// documents (worker answering a request it itself is blocked waiting
// on would never resolve).
//
// Correctness is COUNT-based (a `done` reply counter) plus a
// `failures` counter that must stay 0, the same convention every
// other stress file in this suite already uses -- a body that came
// back wrong (a mix-up between connection tables, or the retain/
// release accounting claude.md #213's own doc comment on
// festina_conn_detach works through in detail coming out unbalanced)
// fails loudly (`close(1)`) instead of silently miscounting; a leak in
// that same accounting shows up under scripts/leak_stress.sh instead,
// which is the whole reason this file exists as a SEPARATE stress
// program rather than only a handful of ordinary pytest cases.
//
// TOTAL=100: each cycle is a REAL TCP connect+request+response round
// trip (not an in-process postMessage), so this is deliberately a
// smaller volume than this suite's own pure-message-passing stress
// files -- 100 real round trips is already enough to make a genuine
// race in the connection hand-off itself (as opposed to ordinary HTTP
// serving, already covered by thread_http_context_churn.f) show up
// reliably under ThreadSanitizer, while keeping the whole run's wall-
// clock time reasonable even under TSan's own real slowdown.

int TOTAL = 100
int done = 0
int failures = 0

on message(w:thread, msg:int) {
    done = done + 1
    if msg == 0 {
        failures = failures + 1
    }
    if done >= TOTAL {
        log('giveRequest churn done')
        log(done)
        log(failures)
        if failures > 0 {
            close(1)
        }
        close(0)
    }
}

thread worker {
    int served = 0
    on request(req:http) {
        served = served + 1
        req.send({'code': 200, 'body': 'handled by worker'})
    }
}

thread driver {
    on message(sender:thread, msg:int) {
        http req = {'url': 'http://127.0.0.1:18302/', 'method': 'GET'}
        req.send()
        bool ok = req.code == 200 && req.toText() == 'handled by worker'
        if ok {
            postMessage(1)
        } else {
            postMessage(0)
        }
    }
}

on request(req:http?) {
    worker.giveRequest(req)
}

openPort(18302)
int i = 0
while i < TOTAL {
    driver.postMessage(1)
    i = i + 1
}
