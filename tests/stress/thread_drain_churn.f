// claude.md #231 (uraikus/festina#91): `NAME.drain()` -- real churn
// under AddressSanitizer/LeakSanitizer and ThreadSanitizer both. This
// is genuinely NEW shared state (`dispatching`/`drained_cond` on
// FestinaThreadHandle, guarded by the SAME in_lock the queue itself
// already uses) that nothing before this entry ever exercised -- a
// race here (the worker's own festina_thread_mark_drained racing
// festina_thread_wait_drained's own predicate check) would show up
// directly under TSan, and a leaked/double-freed message would show
// up under ASan/LeakSanitizer.
//
// Two shapes, both at volume: an ordinary thread, drained after every
// single send (the tightest possible send/drain interleaving -- the
// shape #91's own bug report needed); and a pool, drained across all
// instances, proving drain() composes with pool[i] indexing the same
// way kill()/live()/isAlive() already do.

int CYCLES = 800
int cycle = 0
int seen = 0

on message(w:thread, msg:int) {
    seen = seen + 1
}

thread worker {
    on message(w:thread, msg:int) {
        postMessage(msg + 1)
    }
}

while cycle < CYCLES {
    worker.postMessage(cycle)
    worker.drain()
    cycle = cycle + 1
}

thread pool[4] {
    on message(w:thread, msg:int) { }
}

int p = 0
while p < 200 {
    pool[p % 4].postMessage(p)
    pool[p % 4].drain()
    p = p + 1
}

log('drain churn done')
log(cycle)
close(0)
