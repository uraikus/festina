// claude.md #245: `pool.postMessage(x)` -- no index -- auto-selecting
// an idle instance at real volume, from SEVERAL genuinely concurrent
// senders at once. This is the shape most likely to expose a bug in
// festina_thread_pool_select specifically: main and three feeder
// threads all calling it against the SAME pool's own handles array
// and round-robin counter at the same time, which is exactly the
// "two posters race to pick the same idle instance" window that
// function's own doc comment accepts as benign -- ThreadSanitizer's
// job here is confirming that race is genuinely benign (no data race
// on the counter itself, on any pool instance's own in_lock-guarded
// fields, or on the message queues) and not a cover for a real one.
//
// Correctness is COUNT-based, the same reasoning thread_pool_churn.f's
// own top comment gives: with FOUR feeders (main plus three feeder
// threads) all auto-selecting across the SAME four pool instances at
// once, there is no single expected delivery order to assert on --
// repliesSeen reaching exactly the total sent proves no message was
// lost, duplicated, or misrouted, which is what a genuine race in the
// selector or the underlying queues would actually break.

int POOL_SIZE = 4
int FEEDER_COUNT = 3
int PER_FEEDER = 3000
int PER_MAIN = 3000
int repliesSeen = 0
int totalExpected = FEEDER_COUNT * PER_FEEDER + PER_MAIN

on message(worker:thread, msg:int) {
    repliesSeen = repliesSeen + 1
    if repliesSeen == totalExpected {
        log('pool auto churn done')
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

thread feeders[3] {
    on message(worker:thread, msg:int) {
        int perFeeder = 3000
        int i = 0
        while i < perFeeder {
            pool.postMessage(i)
            i = i + 1
        }
    }
}

int f = 0
while f < FEEDER_COUNT {
    feeders[f].postMessage(0)
    f = f + 1
}

int m = 0
while m < PER_MAIN {
    pool.postMessage(m)
    m = m + 1
}
