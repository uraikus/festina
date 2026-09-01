// claude.md #202 Phase 2: `T?` crossing a `thread` boundary shares the
// raw reference instead of deep-cloning it -- this is the real,
// at-volume ASan/LeakSanitizer AND ThreadSanitizer proof for that
// sharing mechanism specifically, complementing thread_churn.f's own
// (deep-clone) coverage of every OTHER message shape.
//
// Deliberately race-FREE BY DESIGN, not merely by luck: each iteration
// constructs a brand-new `Item?`, posts it to `reader`, and never
// touches it again afterward -- ownership passes to `reader` exclusively
// at that point (the same "borrowed, then exclusively owned by whoever
// received it" contract an ordinary manually-managed value already has
// within one thread, just now crossing a real OS thread boundary).
// `reader` reads the field it needs and frees the value itself --
// required, since nothing auto-manages a `T?` value on either side --
// so this is simultaneously the ASan leak check (miss the `free` and
// every one of these leaks, unmissable at this volume) and the TSan
// proof that SHARING the pointer introduces no race of its own: no two
// threads ever read OR write the same struct at the same time, so a
// clean TSan run here demonstrates the plumbing itself is race-free,
// not that concurrent access to a manually-managed value is somehow
// safe (it categorically is not, and is not what this file attempts to
// prove -- see claude.md #202's own design note).

struct Item { n:int label:text }

int TOTAL = 5000
int repliesSeen = 0
int sum = 0

on message(worker:thread, msg:int) {
    sum = sum + msg
    repliesSeen = repliesSeen + 1
    if repliesSeen == TOTAL {
        log(sum)
        close(0)
    }
}

thread reader {
    on message(worker:thread, msg:Item?) {
        int n = msg.n
        free msg
        postMessage(n)
    }
}

int i = 0
while i < TOTAL {
    Item? it
    it.n = i
    it.label = `item${i}`
    reader.postMessage(it)
    i = i + 1
}
