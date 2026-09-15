// decisions.md #325: `thread NAME[N] { ... }` -- N independent threads
// sharing one declared body, and the four things a pool instance
// answers to besides a send.
//
// Written because the canary registry said so, in its own output: three
// of this slice's six breakages were visible to exactly one corpus file
// each and one was visible to nothing at all. A mechanism whose only
// witness is a stress file goes unmeasured the day that file stops
// matching for some unrelated reason, and `pool[i].isAlive()` appears
// nowhere in the corpus, so nothing could see it at all.
//
// What this file is shaped to make visible, each being something a
// wrong answer changes in the emitted IR rather than only at run time:
//
//   - the INDEXED send, whose receiver costs two loads: the constant
//     array's slot names an instance's handle GLOBAL, and that names
//     the instance's actual handle. Collapsing the two would read a
//     pointer to a pointer as a handle.
//   - the BARE send, which round-robins where its scan starts so an
//     all-idle pool spreads across instances instead of favouring
//     index 0 on every call.
//   - an OUT-OF-RANGE index, which is a silent no-op rather than a
//     wild load -- and `isAlive` on one, which is the single method of
//     the five that answers a value and therefore cannot simply skip:
//     the in-range and out-of-range paths have to meet in a phi, and
//     an instance that does not exist is not alive.
//   - `kill()` against `drain()`, which are different runtime calls
//     with opposite meanings: one stops a worker, the other waits for
//     its queue to empty.
//   - `live()`, whose callback needs no trampoline at all -- its own
//     void(i8) signature is already what the runtime expects, unlike a
//     reply callback, which has to decode an opaque payload first.
//
// Correctness is count-based: every message posted is answered exactly
// once, so a misrouted or dropped one leaves the count short and the
// program hangs rather than printing.

int seen = 0
int total = 0

on message(worker:thread, msg:int) {
    seen = seen + 1
    total = total + msg
    if seen == 6 {
        log(seen)
        log(total)
        close(0)
    }
}

// The pool that actually carries traffic: three instances, one body.
thread pool[3] {
    // Private state, per instance rather than per pool -- three
    // independent copies of this, not one shared between them.
    int bump = 100
    on message(worker:thread, msg:int) {
        postMessage(msg + bump)
    }
}

// A second pool, never sent to, so kill/live/isAlive can be exercised
// without starving the count above.
thread spare[2] {
    on message(worker:thread, msg:int) {
        postMessage(msg)
    }
}

void func onLive(ok:bool) {
    log(ok)
}

// Three indexed sends, one per instance.
int i = 0
while i < 3 {
    pool[i].postMessage(i)
    i = i + 1
}

// Three bare sends, which pick their own instance.
int j = 0
while j < 3 {
    pool.postMessage(j)
    j = j + 1
}

// An index that names no instance: the send is a no-op, and isAlive
// answers false rather than reading whatever lies past the array.
pool[9].postMessage(99)
log(pool[9].isAlive())
log(spare[0].isAlive())

// kill and live against one specific instance, and drain against
// another -- three different runtime calls, deliberately adjacent so a
// canary that swaps any two of them changes this file's own IR.
spare[0].kill()
spare[0].live(onLive)
spare[1].drain()
