// Bytes copied between two arrays, and a type that exists to avoid a
// call.
//
// Two mechanisms with nothing in common except the reason they are
// here together: each was caught by exactly ONE corpus file, and a
// mechanism whose only witness is a file that could stop matching for
// any unrelated reason is measured in name only.
//
// Measured, not assumed. The breakages live in `bootstrap/canary.py`
// and are re-run by tests/test_bootstrap_canary.py, which FAILS if
// this file stops making them visible.
//
//   1. **A splice-insert copies raw element BYTES**, which is what
//      makes its ownership rule different from every other array
//      operation's. `push` has one value with one source expression to
//      ask about; this has a whole ARRAY, read by a plain memcpy with
//      no notion of a Festina type, which goes on managing its own
//      elements independently of whatever this array now does with the
//      copies. So the newly written range takes its own reference
//      UNCONDITIONALLY -- a refcounted element retained in place, a
//      text one replaced by a fresh copy, and anything else left
//      alone, because the bytes already are a complete independent
//      value.
//
//   2. **The data pointer is read again AFTER the call.** A splice
//      that inserts more than it removes may have realloc'd the
//      buffer, so the pointer from before it is stale -- in exactly
//      the growing case, which a shrinking test would never catch.
//      Both directions are below for that reason.
//
//   3. **An ascii's length is a LOAD from its own header**, at
//      payload-16, where a text's is an O(n) code-point walk. This is
//      the entire reason the type exists, so a port that called
//      `festina_ascii_length` instead would give identical answers and
//      be a different compiler.
//
//   4. **`charCodeAt` on an ascii is emitted INLINE and BRANCHLESS.**
//      No call, and no new basic blocks -- so the expression stays a
//      straight-line value, and LLVM can hoist the loop-invariant
//      length load without having to prove a guard. A scan loop is the
//      only place it is ever hot, and it is where a call per character
//      showed up in a benchmark.
//
//   5. **An ascii LITERAL is immortal**, folded into .rodata with the
//      standard negative sentinel in its own inline header, so retain,
//      release and `free` on one are all no-ops through the very
//      checks every other immortal value goes through -- and the
//      common lexer comparison allocates nothing at all.
//
//   6. **An ascii LOCAL is declared without a retain**, and released
//      at scope exit anyway. The original lists `ascii` among the
//      types a scope exit releases and NOT among the ones the
//      declaration branch claims a reference for, so its local binds
//      exactly like a scalar: alloca, store, nothing else.

struct Tag {
    name:text
    n:int
}

arr[text] sink = []
int total = 0

Tag func mkTag(n:int) {
    Tag t
    t.name = `tag ${n}`
    t.n = n
    return t
}

// Mechanisms 1 and 2. Every combination that tells them apart: a
// GROWING splice and a SHRINKING one, a text element type and a struct
// one, and an insert array that is a fresh literal (released here once
// spliced) beside a named one (left alive afterward).
int func splices() {
    arr[text] words = ['a', 'b', 'c', 'd']
    arr[text] extra = ['x', 'y']

    // Shrinking: two out, one in. The buffer cannot have grown, so a
    // stale data pointer would still be valid here -- which is why
    // this case alone proves nothing about the reload.
    arr[text] gone = words.splice(1, 2, ['one'])

    // Growing: none out, three in. This is the case where reusing the
    // pointer from before the call reads freed memory.
    arr[text] grown = words.splice(0, 0, ['p', 'q', 'r'])

    // A NAMED insert array, which this expression does not own and
    // must not release -- it is still live below.
    arr[text] byName = words.splice(0, 1, extra)

    int n = gone.length + grown.length + byName.length
    n = n + words.length + extra.length

    // A struct element type: the copied bytes are references, so each
    // one in the new range needs its own retain rather than a copy.
    arr[Tag] tags = [mkTag(1), mkTag(2)]
    arr[Tag] src = [mkTag(3)]
    arr[Tag] goneT = tags.splice(0, 1, src)
    arr[Tag] goneT2 = tags.splice(0, 0, [mkTag(4)])
    n = n + goneT.length + goneT2.length + tags.length + src.length

    // The two-argument form, which inserts nothing at all.
    arr[text] plain = ['m', 'n', 'o']
    arr[text] cut = plain.splice(1, 1)
    return n + cut.length + plain.length
}

// Mechanisms 3, 4, 5 and 6.
ascii alphabet = 'abcdefghijklmnopqrstuvwxyz'
ascii marker = 'm'

int func scans() {
    int n = 0
    int i = 0
    // The scan loop the inline charCodeAt exists for. The BRANCH on
    // the character is not decoration: a plain accumulation over this
    // loop is a reduction LLVM vectorizes into AVX-512, which is
    // itself evidence the inlining works -- a call per character could
    // never vectorize -- but it also puts EVEX-encoded instructions in
    // the binary that valgrind 3.22 cannot decode, and a case file
    // that cannot be run under valgrind measures half of what it
    // should. Comparing each character keeps the loop scalar without
    // weakening what it witnesses: the header load, the bounds select
    // and the byte load are all still emitted inline.
    while i < alphabet.length {
        if alphabet.charCodeAt(i) == 109 { n = n + 1 }
        i++
    }

    // A local bound from an INDEX -- one of the immortal
    // single-character singletons, and the shape whose declaration
    // must not retain.
    ascii ch = alphabet[12]
    if ch == marker { n = n + 1 }

    // And a local bound from another BINDING, which is the same
    // no-retain declaration over a value something else owns.
    ascii alias = alphabet
    n = n + alias.length

    // A literal on the other side of a comparison converts at compile
    // time, so this allocates nothing.
    if alphabet[0] == 'a' { n = n + 1 }

    // Out-of-range and negative both answer null through the same
    // branchless select.
    if alphabet.charCodeAt(999) == null { n = n + 1 }
    if alphabet.charCodeAt(0 - 1) == null { n = n + 1 }
    return n
}

// The conversions, both directions, and the operators that mix the two
// types.
int func converts() {
    text asText = alphabet.toText()
    ascii back = asText.toAscii()
    ascii joined = marker + 'ore'
    text mixed = `${marker} and ${joined}`
    ascii part = alphabet.slice(0, 3)
    int n = asText.length + back.length + joined.length + mixed.length
    return n + part.length
}

log(splices())
log(scans())
log(converts())
log(sink.length)
log(total)
