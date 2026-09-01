// claude.md #209: `thread NAME[N] { ... }` -- real message-passing
// concurrency across N genuinely independent OS threads all running
// the IDENTICAL body, at real volume, under ASan/LeakSanitizer AND
// ThreadSanitizer. Complements thread_churn.f's own coverage (many
// DIFFERENT thread bodies, one instance each) with the opposite shape
// that matters most for a pool specifically: N instances of the SAME
// generated code, each its own private state/handle/queue, running
// truly concurrently -- exactly the shape most likely to expose a
// bug where per-instance codegen accidentally shared something (a
// global instead of a namespaced one, a handle global mixed up with
// another instance's own).
//
// Every message is posted up front (the same "don't ping-pong"
// reasoning thread_churn.f's own top comment explains -- the drain
// step only runs once per festina_run_timer_loop iteration, so
// waiting for each reply before sending the next would serialize the
// whole test on that interval instead of exercising real throughput).
// Correctness here is COUNT-based (repliesSeen reaching exactly
// POOL_SIZE * PER_INSTANCE proves no message was lost, duplicated, or
// misrouted across the whole pool) rather than value-based -- with N
// instances all replying through the SAME global `on message`
// handler, in a real, unordered race between them, there is no
// single expected reply ORDER to assert on (see
// tests/test_codegen.py::TestThreads::test_two_independent_threads_
// do_not_collide's own comment for the identical reasoning, at
// smaller scale).
//
// A handful of real kill()/live() cycles against ONE specific pool
// instance are included too, proving that operating on a single
// index doesn't disturb the other instances' own OS threads (each a
// real pthread_join/pthread_create pair) even while the whole pool is
// otherwise busy.

int POOL_SIZE = 4
int PER_INSTANCE = 4000
int repliesSeen = 0

on message(worker:thread, msg:int) {
    repliesSeen = repliesSeen + 1
    if repliesSeen == POOL_SIZE * PER_INSTANCE {
        log('pool churn done')
        log(repliesSeen)
        close(0)
    }
}

thread pool[4] {
    int total = 0
    on message(worker:thread, msg:int) {
        total = total + msg
        postMessage(total)
    }
}

// claude.md #209: kill()/live() against pool[0] specifically -- each
// cycle spawns/joins a genuine OS thread, exactly like the singleton
// coverage in thread_churn.f, just addressed through an index instead
// of a bare name.
int k = 0
while k < 10 {
    pool[0].kill()
    pool[0].live(void (ok:bool) => log(ok))
    k = k + 1
}

int p = 0
while p < POOL_SIZE {
    int i = 0
    while i < PER_INSTANCE {
        pool[p].postMessage(i)
        i = i + 1
    }
    p = p + 1
}
