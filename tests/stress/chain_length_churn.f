// claude.md #262: `.length` read off a member chain whose receiver is
// NOT an array.
//
// The `.length` branch has participated in the member chain since #108,
// but only its arr[T] case ever DRAINED the chain -- the blob, text and
// ascii cases dropped the parked bases on the floor, on the stated
// reasoning that "no `make().someBlob.length` shape exists the way
// `make().inner.items.length` does for arr[T]". Any struct with a blob,
// text or ascii field is that shape, and every one of them leaked the
// whole object the field came from.
//
// The trap, and the reason this file exists rather than a one-line
// patch: the drop was MASKING an over-release in the other direction.
// The per-receiver helper the branch used instead answers "owning" for
// a chain whose base is a call, while the inner chain link never
// actually minted anything -- so it released a field it did not own and
// got away with it only because the leaked object's cascade never ran
// to release it a second time. Draining without also removing that
// release is a heap-use-after-free, confirmed under ASan before the fix
// was written. So this program is a use-after-free test first and a
// leak test second.

struct Inner {
    b:blob
    t:text
    a:ascii
    xs:arr[int]
}

struct Outer {
    inner:Inner
    label:text
}

// Written first, so the path exists wherever this runs from.
blob shared = 'chain_length.txt'
shared.write('twelve bytes')

Inner func mkInner() {
    Inner x
    // Deliberately a SHARED blob, not a fresh one: if the chain
    // over-releases the field, `shared` is what dangles, and the
    // reads after the loop are what catch it.
    x.b = shared
    x.t = 'hello there'
    x.a = 'abcd'
    x.xs = [1, 2, 3]
    return x
}

Outer func mkOuter() {
    Outer o
    o.inner = mkInner()
    o.label = 'outer'
    return o
}

blob func mkBlob() {
    blob b = 'chain_length.txt'
    return b
}

int total = 0

for int i = 0, i < 2000, i++ {
    // One link: a field off a call-result struct. All four field types,
    // so the array case (which always drained) sits next to the three
    // that did not.
    total = total + mkInner().b.length
    total = total + mkInner().t.length
    total = total + mkInner().a.length
    total = total + mkInner().xs.length

    // Two links: the parked base is two frames up, and the middle link
    // is an alias that must NOT be released on its own.
    total = total + mkOuter().inner.b.length
    total = total + mkOuter().inner.t.length
    total = total + mkOuter().inner.a.length
    total = total + mkOuter().label.length

    // No chain at all -- the receiver IS the owning call, so the
    // single-receiver release still applies and must keep working.
    total = total + mkBlob().length

    // No chain and no ownership: a plain binding, borrowed, released
    // at its own scope exit.
    blob plain = shared
    total = total + plain.length
    text word = 'abc'
    total = total + word.length
}

// If any of the above over-released the shared blob, this is a
// use-after-free rather than a number.
log(shared.length > 0)
log(total)
