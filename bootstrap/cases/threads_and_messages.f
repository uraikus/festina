// decisions.md #323: the thread surface -- `thread NAME { ... }`, its
// private state, its three adapters, and both directions of
// postMessage.
//
// Written because the corpus could not see any of it. Every other file
// that declares a thread also reaches for something this port still
// refuses -- a pool, `.callback()`, `.reply()`, `.kill()`, a struct
// payload, a thread's own DatabaseURL -- so each of them is unported as
// a whole and contributes nothing to the comparison. Exactly one
// existing file (tests/stress/thread_wider_builtins_churn.f) stays
// inside the subset, and one witness for a whole subsystem is the
// arrangement decisions.md #312 was written about.
//
// What this file is shaped to make visible, each thing being something
// a wrong answer would change in the emitted IR rather than only at
// run time:
//
//   - the ORDER the three adapters are emitted in, which the shared
//     temp/label counters make observable: on_load, on_message,
//     on_exit, per thread, in declaration order.
//   - a thread with NO handlers at all (`idler`), which still gets all
//     three -- the C runtime reads every one of them unconditionally.
//   - state initializers running inside on_load rather than in
//     __festina_main, on that thread's own OS thread.
//   - a state name that SHADOWS a top-level one (`total` here), which
//     is the whole reason a thread's state is a scope of its own
//     rather than more globals.
//   - the bare `postMessage(x)` send from inside a thread body, which
//     loads this thread's handle and posts OUTBOUND, against
//     `NAME.postMessage(x)` from main, which loads the target's handle
//     and passes main's own singleton as the sender.
//   - a `NAME.postMessage(x)` sent from inside ANOTHER thread's body,
//     where the sender is that thread's handle rather than main's.
//   - two different inbound payload types across two threads (int and
//     text), so the per-thread release function registered in main's
//     prologue is a real choice rather than one constant.
//
// Correctness is count-based: every message posted is answered exactly
// once, so a misrouted or dropped one leaves the count short and the
// program hangs rather than printing.

// Both of `adder`'s own state names also exist HERE, at the top level,
// and that is deliberate: a thread's state is a scope of its own, so a
// port that merely wrote these names into the one global table would
// compile a program that reads and writes the wrong storage rather than
// one that refuses to compile. Only a name that resolves BOTH ways can
// tell those two failures apart.
int total = 0
float scale = 0.5
int seen = 0
int echoSeen = 0
int bareSeen = 0

// Three counters rather than one, because the three things being
// counted arrive on different paths: an ordinary message to main, a
// reply to a named send, and a reply to a bare one. Finishing is
// checked from all three, since which of them lands last is a race.
void func maybeFinish() {
    if seen == 6 && echoSeen == 2 && bareSeen == 6 {
        log(total)
        log(scale)
        log(echoSeen)
        log(bareSeen)
        close(0)
    }
}

void func onEcho(r:int) {
    echoSeen = echoSeen + 1
    maybeFinish()
}

void func onBare(r:int) {
    bareSeen = bareSeen + 1
    maybeFinish()
}

on message(worker:thread, msg:int) {
    total = total + msg
    seen = seen + 1
    // claude.md #217: main answers whoever is being handled right now
    // -- a separate dispatch path from on_message, matched by
    // transaction id rather than by queue order.
    worker.reply(msg + 1)
    // claude.md #218: and a SECOND answer to the same message, every
    // time. The first consumed the only pending slot, so this one has
    // nothing to dispatch to and must be RELEASED rather than leaked
    // -- which is exactly why the release function travels with the
    // reply rather than being looked up on arrival. Once main replies
    // at all, every bare send in the program has to chain a callback,
    // so this is the only shape left that can witness the drop.
    worker.reply(msg + 2)
    maybeFinish()
}

// A thread that replies to a NAMED send, so the two directions of the
// mechanism are both here: main registers the callback and the thread
// answers, against the bare form below where a thread registers and
// MAIN answers.
thread echoer {
    on message(worker:thread, msg:int) {
        worker.reply(msg * 10)
    }
}

// A different inbound payload type, so the release function registered
// for each thread is a real per-thread choice. Declared FIRST because
// `adder` messages it: a thread's name has to be in scope where it is
// used, exactly like any other declaration.
thread speaker {
    on message(worker:thread, msg:text) {
        // claude.md #222: the bare form registers its callback on THIS
        // thread's own handle and asks for the eventual reply to fire
        // on main's OS thread rather than on this one -- which is what
        // makes it need the async-io hooks registered at all.
        postMessage(msg.length).callback(onBare)
    }
}

// State, an initializer, all three handlers, and both send forms.
thread adder {
    int total = 100
    float scale = 2.0
    on load() {
        // Runs on this thread's own OS thread, after the two
        // initializers above and before any message is delivered. The
        // float one is read here rather than left unused, so a wrong
        // store into it is visible and not merely emitted.
        log(scale)
        postMessage(total).callback(onBare)
    }
    on message(worker:thread, msg:int) {
        int scaled = msg * 2
        postMessage(scaled + total).callback(onBare)
        // A send to a DIFFERENT thread from inside this one: the
        // sender is this thread's handle, not main's.
        speaker.postMessage('tick')
    }
    on exit(code:int) {
        log(code)
    }
}

// No state, no handlers: all three adapters are still emitted.
thread idler { }

int i = 0
while i < 2 {
    adder.postMessage(i)
    i = i + 1
}
speaker.postMessage('hello')
echoer.postMessage(7).callback(onEcho)
echoer.postMessage(8).callback(onEcho)
