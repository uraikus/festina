// claude.md #210: thread-private helper functions -- real message-
// passing concurrency at real volume, under ASan/LeakSanitizer AND
// ThreadSanitizer, with EVERY message actually processed by a real
// private func call (not just an inline handler body), and a private
// func calling ANOTHER private func on every message too. Complements
// thread_churn.f (no private funcs at all) and thread_pool_churn.f (a
// pool, no private funcs) -- this is the one file that actually
// exercises the new codegen path (_emit_func's thread-mangled-symbol/
// outer_env/thread_ctx parameters, and _emit_call's per-thread "funcs"
// table lookup) under real, sustained concurrent load.
//
// Both a plain thread AND a pool exercise it, so a leaked/raced
// per-instance mangled symbol in the pool case (each instance's own
// private func closing over ITS OWN state, not the pool's other
// instances') would show up here, not just in thread_pool_churn.f's
// own simpler coverage.
//
// Correctness here is COUNT-based, deliberately -- a single combined
// counter across BOTH the plain thread's own replies and the pool's,
// never split by source. An earlier draft of this file DID try to
// keep the two apart with a value-range tag (`msg >= 1000000` meaning
// "from the pool"), the same trick thread_pool_churn.f's own sibling
// test in test_codegen.py uses at small, deliberately bounded values
// -- and it was a real, found bug at THIS file's own volume: `total`
// (the plain thread's own accumulated running sum, fed through two
// chained private funcs) grows UNBOUNDED over thousands of messages
// and quickly exceeds the 1000000 tag threshold itself, silently
// misrouting later replies into the wrong counter and hanging the
// whole file waiting for a count that could now never be reached --
// not a compiler bug, a test-design one. See
// tests/test_codegen.py::TestThreads::test_two_independent_threads_
// do_not_collide's own comment for why value-based routing is fine at
// small, bounded values but not here.

int TOTAL = 2000
int POOL_SIZE = 3
int PER_INSTANCE = 1000
int repliesSeen = 0

void func maybeDone() {
    if repliesSeen >= TOTAL + POOL_SIZE * PER_INSTANCE {
        log('private func churn done')
        log(repliesSeen)
        close(0)
    }
}

on message(worker:thread, msg:int) {
    if worker == null {
        // never sent by main directly (this handler only ever
        // receives from `worker`/`pool`, both real threads) -- kept
        // here anyway as a direct proof `worker == null` itself is
        // false on this path, mirroring claude.md #208's own coverage.
        log('unexpected: sent by main')
    }
    repliesSeen = repliesSeen + 1
    maybeDone()
}

thread worker {
    int total = 0
    int func triple(x:int) {
        return helper(x) * 3
    }
    int func helper(x:int) {
        return x + 1
    }
    on message(worker:thread, msg:int) {
        total = total + triple(msg)
        postMessage(total)
    }
}

thread pool[3] {
    int total = 0
    void func addToTotal(x:int) {
        total = total + scaled(x)
    }
    int func scaled(x:int) {
        return x * 2
    }
    on message(worker:thread, msg:int) {
        addToTotal(msg)
        postMessage(total)
    }
}

int i = 0
while i < TOTAL {
    worker.postMessage(i)
    i = i + 1
}
int p = 0
while p < POOL_SIZE {
    int j = 0
    while j < PER_INSTANCE {
        pool[p].postMessage(j)
        j = j + 1
    }
    p = p + 1
}
