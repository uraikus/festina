// A terminal read of a struct-typed FIELD answers what the field holds,
// instead of creating an empty struct for the question to be about
// (decisions.md #333).
//
// Auto-vivification exists so that `b.inner.n` and `b.xs.push(1)` work
// with nothing assigned first, and it did that by creating the value on
// any reach at all. Counting a plain READ as a reach meant a struct
// field could never be observed absent: the test created the thing it
// was testing for. Three separate shapes of that, all below.
//
// The mechanisms this file has to keep visible, each of which is a
// different branch rather than a different spelling of one:
//
//   1. **A terminal read loads the slot.** No `field.make`/`field.done`
//      pair, no calloc -- just a load. `readsAnAbsentLink` is the whole
//      of it, and its IR is one instruction where it used to be a
//      branch and an allocation.
//
//   2. **A receiver read still creates.** `o.inner.n` and
//      `o.inner.n = 5` both have to keep working, so the vivify path
//      must still be emitted for a field reached THROUGH. Both
//      directions are below, because the read and the write reach
//      cgFieldPtr by different routes.
//
//   3. **Struct only.** An arr[T]/map[T] field's zero value is a real
//      empty container, so those still vivify on every reach including
//      a terminal one -- a null array would be worse than an empty one,
//      since `.length` on it reads past the null page rather than
//      faulting. `holdsContainers` pins that the container branch is
//      untouched.
//
//   4. **A weak field is not this case.** It is struct-typed too, and
//      its read is an upgrade through a control block rather than a
//      plain load -- putting the terminal-read branch first turns it
//      into a load of the wrong pointer entirely. `weakIsStillChecked`
//      is here so the two cannot be collapsed.
//
//   5. **The nesting rule.** In `a.b.c`, `a.b` is a receiver and `c` is
//      not, so one link creates and the next does not. `nestedLinks`
//      is the file's only witness to the save/restore rather than
//      set/clear shape of the flag.

struct Link {
    next:Link
    tag:int
}

struct Inner {
    n:int
}

struct Outer {
    inner:Inner
    label:text
}

struct Containers {
    xs:arr[int]
    m:map[int]
}

struct WeakHolder {
    target:weak Link
    tag:int
}

struct Deep {
    mid:Outer
}

// Mechanism 1: the terminal read, in all three spellings the report
// used -- the bare test, the negated test, and the read-back of an
// explicit null assignment.
int func readsAnAbsentLink() {
    Link a
    int score = 0
    if a.next == null { score = score + 1 }
    if a.next != null { score = score + 100 }
    Link b
    a.next = b
    if a.next == null { score = score + 100 }
    a.next = null
    if a.next == null { score = score + 2 }
    return score
}

// Mechanism 1, the shape that was actually blocked: a walk that
// terminates because the last link reads null rather than growing a
// new one forever.
int func walksALinkedList() {
    Link head
    head.tag = 1
    Link second
    second.tag = 2
    head.next = second
    Link third
    third.tag = 4
    second.next = third

    int sum = 0
    Link cur = head
    while cur != null {
        sum = sum + cur.tag
        cur = cur.next
    }
    return sum
}

// Mechanism 2: reached THROUGH, so it is created -- once for the write
// and then found again by the read.
int func reachesThroughAField() {
    Outer o
    o.inner.n = 5
    o.label = 'x'
    int got = o.inner.n
    int score = got
    // Created by the reach above, so the terminal test now says so.
    if o.inner == null { score = score + 100 }
    return score + o.label.length
}

// Mechanism 2 again, read-only: nothing writes through the field, and
// reading `.n` off it still has to find a struct to read from.
int func readsThroughAnUntouchedField() {
    Outer o
    return o.inner.n
}

// Mechanism 3: containers are untouched -- never null, and empty.
int func holdsContainers() {
    Containers c
    int score = 0
    if c.xs == null { score = score + 100 }
    if c.m == null { score = score + 100 }
    c.xs.push(7)
    arr[int] taken = c.xs
    return score + taken.length + c.m.keys().length
}

// Mechanism 4: struct-typed, and still an upgrade rather than a load.
int func weakIsStillChecked() {
    WeakHolder h
    int score = 0
    if h.target == null { score = score + 1 }
    Link l
    l.tag = 9
    h.target = l
    Link back = h.target
    if back == null { return score }
    return score + back.tag
}

// Mechanism 5: two links, where the inner one creates and the outer
// one does not.
int func nestedLinks() {
    Deep d
    int score = 0
    // `d.mid` is a receiver here and `inner` is terminal.
    if d.mid.inner == null { score = score + 1 }
    // Now reach all the way through, which creates both.
    d.mid.inner.n = 3
    if d.mid.inner == null { score = score + 100 }
    return score + d.mid.inner.n
}

log(`${readsAnAbsentLink()}`)
log(`${walksALinkedList()}`)
log(`${reachesThroughAField()}`)
log(`${readsThroughAnUntouchedField()}`)
log(`${holdsContainers()}`)
log(`${weakIsStillChecked()}`)
log(`${nestedLinks()}`)
