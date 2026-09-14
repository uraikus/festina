// A `blob`, and the three ordering rules the first self-hosted file
// found the hard way.
//
// `bootstrap/lexer.f` is the first of the bootstrap's own ten files to
// emit byte-identical IR, and getting there turned up three things no
// smaller program had reached. Each is an ORDER rather than a
// behaviour: the emitted IR means the same thing either way, and the
// text differs from the first divergence to the end of the module. A
// 4,552-line file diffed line for line is what found them; nothing
// smaller would have.
//
// Measured, not assumed. The breakages live in `bootstrap/canary.py`
// and are re-run by tests/test_bootstrap_canary.py, which FAILS if
// this file stops making them visible.
//
//   1. **A `blob` is a HANDLE, not a value with storage.** Its global
//      is a bare null pointer rather than the {refcount, payload}
//      header every other refcounted global gets, its destructor is
//      the runtime's own `festina_blob_release` rather than a
//      generated cascade, and its LENGTH is a call -- a blob carries
//      no length field the way an array header does.
//
//   2. **`.slice()` emits its receiver TWICE.** The `ascii` branch
//      claims the name first, emits the receiver, finds it is not an
//      ascii, releases it if it owned one, and falls THROUGH -- so the
//      blob branch emits it again from scratch and the first value is
//      simply unused. That is the shipped compiler's output, not a
//      slip, and a port that tidied it would disagree with the thing
//      it exists to agree with.
//
//   3. **A `for` loop's `continue` jumps to the UPDATE, not the
//      condition** -- so `i++` still runs and the loop still
//      terminates. A `while` loop's goes to the condition, because it
//      has no update to run. Both leave the iteration's own scope
//      first: whatever this pass through the body declared is freed
//      before the branch, exactly as reaching the body's end would.
//
//   4. **Scope exit frees the INNERMOST frame first, and within a
//      frame in declaration order.** The two point opposite ways,
//      which is exactly why guessing gets it wrong: an outer text
//      local is freed AFTER an inner struct one declared later than
//      it. The original notes that the order cannot affect
//      correctness, since each release is independent -- true, and
//      beside the point for a port that has to produce the same text.
//
//   5. **A text FIELD write emits the value before reading what the
//      slot held.** Same shape as an array element write, same reason,
//      and the port had it backwards in one of the two.

blob src = 'bootstrap/cases/blobs_and_scopes.f'
arr[text] sink
int bumps = 0

struct Held {
    name:text
    n:int
}

struct Plain {
    n:int
}

// Mechanism 1: all three blob shapes. `.length` is the one that needs
// saying, because an array's is a header field and a blob's is not.
int func readsBlob(b:blob) {
    return b.byteAt(0) + b.length
}

// Mechanism 2: slice, whose receiver is emitted twice.
text func slicesBlob(b:blob) {
    return b.slice(0, 2)
}

// Mechanism 3: a `for` continue must reach the update or this never
// finishes. The count proves the loop ran the right number of times,
// and the locals prove the scope was unwound on the way out.
int func loops() {
    int seen = 0
    for int i = 0, i < 6, i++ {
        text tag = `t${i}`
        Held h
        h.name = tag
        if i == 1 { continue }
        if i == 4 { break }
        seen = seen + 1
    }
    int j = 0
    while j < 6 {
        j++
        text tag = `w${j}`
        if j == 2 { continue }
        if j == 5 { break }
        seen = seen + 1
    }
    return seen
}

// Mechanism 4: an outer text local and an INNER struct local declared
// after it, so "innermost frame first" and "declaration order within a
// frame" disagree about which is freed first. This is the exact shape
// that diverged in lexer.f.
int func nestedScopes() {
    text outer = `o${1}`
    int total = 0
    for int i = 0, i < 2, i++ {
        Held inner
        inner.name = `i${i}`
        text alsoInner = `a${i}`
        total = total + inner.n + alsoInner.length
    }
    return total + outer.length
}

// Mechanism 5, and the stack-versus-heap answer for a struct that owns
// a field. `kept` escapes into a global list; `loose` does not, so its
// storage is in the frame -- but its text field's buffer is heap
// either way and still has to be reclaimed.
int func fields() {
    Held loose
    loose.name = 'x'
    loose.name = loose.name
    loose.n = 1

    Held kept
    kept.name = `k${2}`
    arr[Held] all
    all.push(kept)
    sink.push(kept.name)
    return loose.n + all.length
}

// A struct reached ONLY through an array, never as a binding of its
// own. That makes the array's cascade the first thing that needs the
// struct's, so the two are generated back to back -- and their
// generation ORDER becomes visible, which it is not when something
// else already produced the struct's. The element's release has to be
// resolved BEFORE the array body starts taking temps, or the two
// functions are numbered the other way round.
//
// The array is deliberately left EMPTY: pushing anything into it would
// need a local of the struct type, which would generate that struct's
// cascade first and hide the very thing this measures.
struct OnlyViaArray {
    label:text
}

arr[OnlyViaArray] boxes

int func neverBoundAlone() {
    arr[OnlyViaArray] xs
    boxes = xs
    return xs.length
}

// A struct with only scalar fields, beside the owning one, so the
// difference between "needs a cascade" and "does not" is visible
// rather than assumed.
int func plainStruct() {
    Plain p
    p.n = 3
    return p.n
}

// Mechanism 1 again, and non-scalar returns: an array of structs
// handed back to a caller takes its own reference BEFORE the scope
// frees run, because a returned local is no longer excluded from them.
arr[Held] func makesSome() {
    arr[Held] out
    Held a
    a.name = 'a'
    out.push(a)
    return out
}

int func usesTheResult() {
    arr[Held] got = makesSome()
    return got.length
}

log(readsBlob(src))
log(slicesBlob(src))
log(loops())
log(nestedScopes())
log(fields())
log(neverBoundAlone())
log(plainStruct())
log(usesTheResult())
log(sink.length)
log(bumps)
