// `[...]` -- one spelling, three different allocation strategies, and
// an evaluation order that is invisible in every array literal made of
// constants.
//
// Measured, not assumed: with the port deliberately broken in four
// separate ways, only ONE of the four showed up anywhere in the corpus.
// This file exists for the other three.
//
//   1. **Every element is evaluated before the header is allocated.**
//      An array of constants emits no instructions for its elements at
//      all, so the ordering cannot be seen in one -- the header could
//      come first and the IR would be identical. `bump()` below is what
//      makes it visible: the calls have to appear above the calloc.
//
//   2. **An empty literal computes no size.** `[]` stores length 0 and
//      calls malloc(0) outright; there is no element type to take the
//      size of and nothing to multiply it by. A literal with elements
//      emits the `getelementptr null, i64 1` / `ptrtoint` / `mul`
//      triple instead. Emitting that triple for an empty literal too
//      would be harmless at runtime and wrong in the IR.
//
//   3. **A container local stack-allocates only from a literal written
//      right here** (claude.md #81). A non-escaping local with no
//      initializer, or one initialized directly from `[...]`, builds
//      its header into the frame -- the element count, and so the
//      buffer size, is known at the declaration. One initialized from
//      ANYTHING else (another binding, a call result, a field) aliases
//      a value whose size this declaration cannot see, so it is always
//      refcounted, escaping or not. Both shapes are below, in the same
//      function, so the difference is per DECLARATION rather than per
//      function.
//
// One half of mechanism 3 is deliberately NOT here: a refcounted
// with-initializer local retains its value unless the source already
// owns a fresh reference, and the only owning source that is not a
// literal is a call returning a container. Those do not compile in the
// port yet, so a file containing one would be classified unported and
// measure none of the three mechanisms above. It belongs here the day
// non-scalar returns land, not before.
//
// The header a literal builds into is the only part of it that can
// live in the frame. Its data buffer is always heap -- which is why a
// stack-allocated container still has a free() at scope exit and a
// stack-allocated struct does not.

arr[int] counted = []
arr[int] seeded = [1, 2, 3]
int bumps = 0

int func bump(v:int) {
    bumps = bumps + 1
    return v
}

// Mechanism 1: the two calls run before the header exists.
arr[int] computed = [bump(10), bump(20)]

// Mechanism 2, at the top level and again inside a function below.
arr[float] nothing = []

// Mechanism 3, all four shapes of container local in one function.
int func strategies() {
    // No initializer, never escapes: frame storage.
    arr[int] bare
    // A literal written right here, never escapes: frame storage too,
    // filled in place rather than calloc'd.
    arr[int] literal = [1, 2]
    // A literal, but the name escapes -- it is assigned into a global
    // below -- so the header is heap after all.
    arr[int] leaks = [3, 4]
    // NOT a literal: aliases another binding, so it is refcounted and
    // retains even though nothing here can reach it.
    arr[int] alias = seeded
    // An empty literal in a local, which stack-allocates like any
    // other literal while its buffer stays a malloc(0) on the heap.
    arr[int] empty = []

    counted = leaks
    return bare.length + literal.length + leaks.length
         + alias.length + empty.length
}

// An escaping local from a literal, on its own, so the heap answer is
// not only ever seen next to the stack one.
arr[float] sink = []

void func escapeOnly() {
    arr[float] f = [1.5, 2.5]
    sink = f
}

// A literal whose elements are themselves reads rather than constants:
// the loads come first, then the header, then the stores.
int base = 5
arr[int] derived = [base, base + 1, base * 2]

log(counted.length)
log(seeded.length)
log(computed.length)
log(computed[1])
log(bumps)
log(nothing.length)
log(strategies())
escapeOnly()
log(sink.length)
log(derived[2])
log(counted.length)
