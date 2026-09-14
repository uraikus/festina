// An `arr[text]` is not an `arr[int]` with a different element size.
// Every ownership decision the language makes for a `text` binding it
// also has to make for each SLOT of such an array, and the machinery
// for that is a whole mechanism the scalar case never reaches.
//
// Measured, not assumed: with the port broken on purpose four ways --
// the generated cascade replaced by the generic release, the element
// loop skipped for a frame-allocated array, a literal's elements
// aliased instead of copied, and an element write storing without
// reclaiming what the slot held -- none of it was visible to the
// corpus, because not one file that currently matches holds a
// container of anything but a scalar.
//
// Those breakages are no longer prose: they live in
// `bootstrap/canary.py` and are re-run by
// tests/test_bootstrap_canary.py, which FAILS if this file ever stops
// making them visible. A drift here is a failing test now, not a
// silently unmeasured mechanism.
//
//   1. **The generic release is wrong the moment an element owns
//      something.** `@festina_release_array` frees the buffer and the
//      header; it knows nothing about what the slots hold. A container
//      whose elements own something needs a cascade GENERATED for that
//      element type -- one that drops the refcount itself, because the
//      element loop has to run strictly between the refcount check and
//      the free, so the two cannot simply call each other.
//
//   2. **A generated function lands before the function that asked for
//      it.** It is generated lazily, at the first release site that
//      needs it, which is somewhere inside a body -- and the original
//      builds each body in a list of its own and appends it only at
//      the end. So a cascade first needed by the second of three
//      functions is emitted between the first and the second, with
//      temp numbers taken from the middle of the second's. Two
//      functions here need the same cascade and one does not, so the
//      placement is observable rather than trivially right.
//
//   3. **A frame-allocated array still has elements to release.** Its
//      header is in the frame and is never freed, but each slot's own
//      buffer still has an owner that is going away, so scope exit runs
//      the same element loop inline before freeing the data buffer.
//      For a scalar element there is nothing to run and the length
//      loaded just above it really is unused (decisions.md #301) --
//      both shapes are below.
//
//   4. **A literal COPIES; a write RECLAIMS first.** An array literal
//      writes into fresh malloc'd memory, so every slot is written
//      exactly once and there is no stale value: copy and store. An
//      element write into a built array has to give back what the slot
//      already held -- and read it BEFORE making the copy, so
//      `xs[i] = xs[i]` cannot free the buffer it is about to copy from.
//
// Scalar and `text` elements only. A container of a struct, or a map
// of anything that owns something, needs more: a map's entries are
// opaque to codegen in a way an array's flat buffer is not, so its
// cascade goes through a generated per-value-type trampoline.

arr[text] words = ['alpha', 'beta']
arr[text] sink
arr[int] numbers = [1, 2]
text held = 'held'

// Mechanism 2: `middle` and `last` both need the cascade, `first` does
// not, so where the generated function lands is visible.
int func first() {
    arr[int] local = [1, 2, 3]
    local.push(4)
    return local.length
}

int func middle() {
    arr[text] escaping
    escaping.push('x')
    sink = escaping
    return sink.length
}

int func last() {
    arr[text] alsoEscaping = ['y']
    sink = alsoEscaping
    return alsoEscaping.length
}

// Mechanism 3: both storage answers for a container of text, and the
// scalar shape beside them so the difference is visible rather than
// assumed.
int func frameAllocated() {
    arr[text] mine
    mine.push('one')
    mine.push(held)
    arr[int] scalars
    scalars.push(7)
    return mine.length + scalars.length
}

// Mechanism 4: a literal's copy, and a write's reclaim-then-copy,
// including the self-assignment that makes the ORDER load-bearing.
void func writes() {
    words[0] = 'replaced'
    words[1] = held
    words[0] = words[0]
}

// push takes the same decision per call: a literal is copied, a
// binding is copied, and an expression that already owns a buffer is
// taken as it stands.
text func make() {
    return `made${1}`
}

int func pushes() {
    arr[text] xs
    xs.push('literal')
    xs.push(held)
    xs.push(make())
    return xs.length
}

// pop and shift hand the element BACK rather than releasing it, so the
// caller owns what comes out -- the opposite direction from push.
int func removes() {
    arr[text] xs = ['a', 'b', 'c']
    text gone = xs.pop()
    text also = xs.shift()
    xs.unshift('front')
    return xs.length + gone.length + also.length
}

// An empty pop answers this element type's own NULL, which for an int
// is NOT zero -- zero is a perfectly ordinary element.
int func emptyPop() {
    arr[int] none
    return none.pop()
}

log(first())
log(middle())
log(last())
log(frameAllocated())
writes()
log(words[0])
log(words[1])
log(pushes())
log(removes())
log(emptyPop())
log(numbers.length)
log(sink.length)
