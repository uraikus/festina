// claude.md #217/#222: `t.reply(response)` / `NAME.postMessage(x).
// callback(fn)` -- real, concurrent, at-volume proof of the request/
// response mechanism. `.callback(fn)`'s `fn` fires on MAIN's own OS
// thread regardless of which thread registered it -- for a worker
// sending to main via the BARE form (`worker2` below), that means
// marshaled through festina_async_io_dispatch (claude.md #222 fixed
// this: it used to fire inline on the WORKER's own thread instead, a
// real cross-thread-isolation hazard this file's own churn at volume
// now exercises the fix for). Every send registers a fresh txn id and
// a pending-callback slot on worker2's OWN list (see
// FestinaPendingCallback's own "self-only, no lock needed" doc
// comment); every reply looks one up and removes it -- a leak or a
// wrong-slot mix-up at these counts is unmissable, and a data race in
// that "self-only" design would show up under ThreadSanitizer.
//
// bareRepliesSeen/bareSum are touched ONLY by onBareReply, which now
// only ever runs on MAIN's own OS thread (via festina_async_io_drain,
// called from the same single-threaded main loop as everything else
// touching them) -- no OTHER thread ever reads or writes them.
// expectedBareSum is computed by main's own top-level code, which --
// like every other close()-in-a-callback test in this suite -- always
// finishes before main's own event loop starts (nothing async-driven
// dispatches until __festina_main() itself returns), so onBareReply's
// own read of it, once replies start arriving, is safe by
// construction, not by luck.
//
// claude.md #230 (uraikus/festina#89): worker2's own batch above sends
// all 5,000 messages BEFORE the first reply is ever dispatched, so its
// own pending-callback list never drains to empty until the very last
// removal -- exactly the shape that never exercised the real bug this
// entry fixes (festina_thread_dispatch_reply removed a node without
// fixing pending_callbacks_tail when that node happened to be the
// TAIL, which only happens when the list drains to a single entry and
// back to empty -- precisely what a SEQUENTIAL round trip, register-
// send-reply-dispatch-THEN-register-again, does on every single
// iteration). worker3 below is that missing shape, at volume: each
// reply's own callback is what sends the NEXT message, so the list is
// genuinely emptied and repopulated 1,000 times over the run -- a
// regression here reproduces #89 almost immediately (its own repro
// needed only a second round trip) rather than needing all 1,000.

int BARE_TOTAL = 5000
int bareRepliesSeen = 0
int bareSum = 0
int expectedBareSum = 0

int SEQ_TOTAL = 1000
int seqCount = 0
int seqSum = 0
int expectedSeqSum = 0

void func maybeFinish() {
    if bareRepliesSeen >= BARE_TOTAL && seqCount >= SEQ_TOTAL {
        log('reply/callback churn done')
        log(bareRepliesSeen)
        log(seqCount)
        log(bareSum == expectedBareSum)
        log(seqSum == expectedSeqSum)
        if bareSum != expectedBareSum || seqSum != expectedSeqSum {
            close(1)
        }
        close(0)
    }
}

void func onBareReply(r:int) {
    bareRepliesSeen = bareRepliesSeen + 1
    bareSum = bareSum + r
    maybeFinish()
}

thread worker3 {
    on message(worker:thread, msg:int) {
        worker.reply(msg + 1)
    }
}

void func onSeqReply(r:int) {
    seqCount = seqCount + 1
    seqSum = seqSum + r
    if seqCount < SEQ_TOTAL {
        worker3.postMessage(seqCount).callback(onSeqReply)
    } else {
        maybeFinish()
    }
}

on message(worker:thread, msg:int) {
    worker.reply(msg + 1)
    // claude.md #218: a SECOND reply to the same message, every single
    // time -- the first one consumed the only pending callback slot,
    // so this one has nothing to dispatch to and must be released
    // rather than leaked (it used to be enqueued, dropped on arrival,
    // and its payload leaked with it). 5,000 of these per run, so a
    // regression is unmissable under scripts/leak_stress.sh.
    worker.reply(msg + 2)
}

thread worker2 {
    // claude.md #195: a thread body can't see a top-level variable --
    // its own private copy of the same count.
    int total = 5000
    on load() {
        int k = 0
        while k < total {
            postMessage(k).callback(onBareReply)
            k = k + 1
        }
    }
}

int t = 0
while t < BARE_TOTAL {
    expectedBareSum = expectedBareSum + (t + 1)
    t = t + 1
}
// worker3's own chain replies 1, 2, 3, ..., SEQ_TOTAL in order (each
// onSeqReply(r) call's own r equals that call's own 1-based position),
// so the expected sum is the ordinary sum of 1..SEQ_TOTAL.
int s = 0
while s < SEQ_TOTAL {
    expectedSeqSum = expectedSeqSum + (s + 1)
    s = s + 1
}

worker3.postMessage(0).callback(onSeqReply)
