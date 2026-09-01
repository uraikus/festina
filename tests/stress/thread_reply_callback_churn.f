// claude.md #217: `t.reply(response)` / `NAME.postMessage(x).
// callback(fn)` -- real, concurrent, at-volume proof of the request/
// response mechanism's own most novel piece: `.callback(fn)`'s `fn`
// runs back on whichever OS thread originally registered it, which
// for a worker sending to main via the BARE form (`worker2` below) is
// that WORKER's own OS thread, not main's -- a genuinely new
// characteristic no earlier `thread` feature has (blob/img/aud's own
// `.callback()`, claude.md #165/#171, always marshals its callback
// back onto MAIN via festina_async_io_drain; this one deliberately
// doesn't, so the reply can be delivered as directly as an ordinary
// message already is). Every send registers a fresh txn id and a
// pending-callback slot on worker2's OWN list (see
// FestinaPendingCallback's own "self-only, no lock needed" doc
// comment); every reply looks one up and removes it -- a leak or a
// wrong-slot mix-up at these counts is unmissable, and a data race in
// that "self-only" design would show up under ThreadSanitizer.
//
// bareRepliesSeen/bareSum are touched ONLY by onBareReply, which only
// ever runs on worker2's own OS thread, one dispatch at a time (this
// runtime's own inbound queue is strictly serial per thread) -- no
// OTHER code anywhere in this program reads or writes them, so
// there's no second thread to race against despite this being the
// first stress program where a *callback*, not just an `on message`
// handler, runs off main's own OS thread. expectedBareSum is computed
// by main's own top-level code, which -- like every other close()-in-
// a-callback test in this suite -- provably finishes BEFORE main ever
// drains a reply (drain only runs once top-level code returns and
// enters the main loop), so onBareReply's own read of it, once replies
// start arriving, is safe by construction, not by luck.

int BARE_TOTAL = 5000
int bareRepliesSeen = 0
int bareSum = 0
int expectedBareSum = 0

void func onBareReply(r:int) {
    bareRepliesSeen = bareRepliesSeen + 1
    bareSum = bareSum + r
    if bareRepliesSeen >= BARE_TOTAL {
        log('reply/callback churn done')
        log(bareRepliesSeen)
        log(bareSum == expectedBareSum)
        if bareSum != expectedBareSum {
            close(1)
        }
        close(0)
    }
}

on message(worker:thread, msg:int) {
    worker.reply(msg + 1)
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
