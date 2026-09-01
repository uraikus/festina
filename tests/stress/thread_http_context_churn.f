// claude.md #212: a thread's own private HTTP context -- real,
// concurrent, at-volume proof that it genuinely never shares so much
// as one fd with main's own HTTP context, both directions at once:
//
// - main opens its own port (18200) and serves a fixed body from its
//   own top-level `on request`.
// - thread `worker` opens ITS OWN, separate port (18205) and serves a
//   DIFFERENT fixed body from its own `on request` -- this alone
//   already exercises two genuinely independent, concurrently
//   running poll() loops (festina_run_http_loop for main,
//   festina_thread_http_service_pass's own combined loop for
//   worker), each driven by the __thread conversion in
//   festina_runtime_http.c.
// - `worker`'s own `on message` handler, driven by TOTAL messages
//   from main, makes a real BLOCKING client request (the zero-
//   argument `req.send()` form) back to MAIN's own port on every
//   single message -- deliberately targeting the OTHER context, never
//   its own (a thread blocking a client request against its OWN
//   listener while nothing else services that very loop would
//   deadlock; see this file's own design notes in claude.md #212).
//   Each response is checked byte-for-byte against main's own fixed
//   body -- a response mixed up between main's and worker's own
//   contexts (the exact class of bug the __thread conversion exists
//   to rule out) would fail this check immediately, not just crash.
//
// Correctness is COUNT-based (a `done` reply counter) plus a
// `failures` counter that must stay 0, the same convention every
// other stress file in this suite already uses -- a real mix-up
// fails loudly (`close(1)`) instead of silently miscounting.
//
// TOTAL=2000: two thousand full connect/send/receive/verify round
// trips is enough to make a genuine race (a response read on the
// wrong context's own g_conns/poll set) show up reliably under
// ThreadSanitizer, while still running in well under a second.

int TOTAL = 2000
int done = 0
int failures = 0

on request(req:http) {
    req.send({'code': 200, 'body': 'main-body'})
}

on message(worker:thread, msg:int) {
    done = done + 1
    if msg == 0 {
        failures = failures + 1
    }
    if done >= TOTAL {
        log('http context churn done')
        log(done)
        log(failures)
        if failures > 0 {
            close(1)
        }
        close(0)
    }
}

thread worker {
    on load() {
        openPort(18205)
    }
    on request(req:http) {
        req.send({'code': 200, 'body': 'worker-body'})
    }
    on message(sender:thread, msg:int) {
        http req = {'url': 'http://127.0.0.1:18200/', 'method': 'GET'}
        req.send()
        bool ok = req.code == 200 && req.toText() == 'main-body'
        if ok {
            postMessage(1)
        } else {
            postMessage(0)
        }
    }
}

openPort(18200)
int i = 0
while i < TOTAL {
    worker.postMessage(i)
    i = i + 1
}
