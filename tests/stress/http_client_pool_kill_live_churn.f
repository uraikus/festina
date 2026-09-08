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

thread upstream {
    on load() {
        openPort(18305)
    }
    on request(req:http) {
        req.send({'code': 200, 'body': 'ok'})
    }
}

thread worker {
    on load() {
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
