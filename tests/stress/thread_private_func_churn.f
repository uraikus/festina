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

int TOTAL = 10000
int wRepliesSeen = 0
int wSum = 0
int POOL_SIZE = 3
int PER_INSTANCE = 3000
int pRepliesSeen = 0

void func maybeDone() {
    if wRepliesSeen >= TOTAL && pRepliesSeen >= POOL_SIZE * PER_INSTANCE {
        log('private func churn done')
        log(wRepliesSeen)
        log(wSum)
        log(pRepliesSeen)
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
    if msg < 1000000 {
        wRepliesSeen = wRepliesSeen + 1
        wSum = wSum + msg
    } else {
        pRepliesSeen = pRepliesSeen + 1
    }
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
        // Tagged >= 1000000 so the top-level handler's own
        // wRepliesSeen/pRepliesSeen split (above) never conflates a
        // pool reply with a `worker` reply -- the two threads' own
        // reply VALUES would otherwise overlap.
        postMessage(1000000 + total)
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
