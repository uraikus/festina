// claude.md #195 Phase 2: `thread NAME { ... }` -- real message-passing
// concurrency, at real volume, under ASan/LeakSanitizer (see
// scripts/leak_stress.sh's own new "thread" runtime object). Every
// message crossing either queue is a fresh malloc'd box (or, for
// text, a fresh festina_text_own copy) -- a leak of even one per
// message is unmissable at these counts. Exercises both Phase 2
// message shapes (int, the raw-bits box path; text, the "the box IS
// the owned buffer" path) and the two lifecycle operations that
// themselves allocate/tear down real OS resources (kill()/live(),
// each pthread_join()ing a real thread).
//
// All N messages are posted up front, in one tight loop, rather than
// one-reply-triggers-the-next-send -- a ping-pong design was tried
// first and confirmed real but uninteresting: the drain step only
// runs once per festina_run_timer_loop iteration (a bounded ~20ms
// poll when nothing else is scheduled sooner, see that function's own
// doc comment), so a design that waits for each reply before sending
// the next serializes the whole test on that interval instead of
// exercising real throughput. Posting everything up front lets the
// worker drain its own inbound queue at full speed and the main loop
// pick up however many outbound replies have piled up in as few
// drains as it needs.
//
// This test cannot busy-wait for a reply inside top-level code (no
// top-level statement ever runs concurrently with the main loop's own
// drain step), so it drives itself the other way around: once BOTH
// counters reach their targets, whichever onXReply callback notices
// last calls close(0) -- an ordinary main-thread function call, never
// "inside a thread body", so none of claude.md #195's isolation
// restrictions apply to it.

thread pinger {
  on message(p:int) {
    postMessage(p + 1)
  }
}

thread echoer {
  on message(p:text) {
    postMessage(`echo:${p}`)
  }
}

int TOTAL = 20000
int intRepliesSeen = 0
int intSum = 0
int TEXT_TOTAL = 6000
int textRepliesSeen = 0

void func maybeDone() {
    if intRepliesSeen >= TOTAL && textRepliesSeen >= TEXT_TOTAL {
        log('int churn done')
        log(intRepliesSeen)
        log(intSum)
        log('text churn done')
        log(textRepliesSeen)
        close(0)
    }
}

void func onIntReply(x:int) {
    intRepliesSeen = intRepliesSeen + 1
    intSum = intSum + x
    maybeDone()
}

void func onTextReply(x:text) {
    textRepliesSeen = textRepliesSeen + 1
    maybeDone()
}

pinger.onMessage(void (x:int) => onIntReply(x))
echoer.onMessage(void (x:text) => onTextReply(x))

// claude.md #195 Phase 2: a handful of real kill()/live() cycles up
// front too -- each one spawns/joins a genuine OS thread, so this is
// what a leaked pthread resource (not just a leaked message box)
// would show up under.
int k = 0
while k < 20 {
    pinger.kill()
    pinger.live(void (ok:bool) => log(ok))
    k = k + 1
}

int i = 0
while i < TOTAL {
    pinger.postMessage(i)
    i = i + 1
}
int j = 0
while j < TEXT_TOTAL {
    echoer.postMessage(`msg${j}`)
    j = j + 1
}
