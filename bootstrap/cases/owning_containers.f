// A container the expression OWNS, indexed -- and a `map[text]`,
// whose values own buffers the map itself knows nothing about.
//
// These are the mechanisms `bootstrap/codegen.f` needed to compile
// itself, and for one slice they were measured by nothing but
// `bootstrap/codegen.f`. Every one of the canaries below was caught,
// and every one of them was caught by that single 58,000-line file --
// so the whole set would have gone silent together the moment it
// stopped matching for any unrelated reason. A mechanism whose only
// witness is the largest file in the corpus is measured in name only.
// This file is the small, stable witness.
//
// Measured, not assumed. The breakages live in `bootstrap/canary.py`
// and are re-run by tests/test_bootstrap_canary.py, which FAILS if
// this file stops making them visible.
//
//   1. **Indexing a container you own is the same dilemma a field read
//      is, and gets the same answer.** `parts[0]` on a container with
//      a binding borrows a pointer into storage that outlives the
//      read. `raw.split(' ')[0]` borrows a pointer into storage
//      NOTHING will ever free -- releasing the array before the
//      element escapes would free the element too, and not releasing
//      it leaks the whole container. claude.md #119: mint the
//      element's own ownership FIRST -- copy a text one, retain a
//      refcounted one -- and only then release the container, whose
//      own element cascade decrements the just-retained value straight
//      back to the one reference the expression holds. A scalar
//      element needs no minting: its loaded value survives the
//      container by copy, so the container is simply released.
//
//   2. **A `map[text]` asks a DIFFERENT ownership question than a
//      `map[Struct]` does.** A refcounted value wants to know whether
//      a retain is owed; a text value whether a COPY is -- and the two
//      predicates disagree, because a concatenation is an owning text
//      source (every `+` in a text context mallocs) and no kind of
//      refcounted source at all. Asking the refcounted question about
//      a text value copies a buffer that was already exclusively owned
//      and drops the original on the floor.
//
//   3. **A text entry frees the old buffer BEFORE the set; a
//      refcounted one releases AFTER.** Not a stylistic difference:
//      claude.md #120 defers the refcounted release so a cycle trial
//      can never find the entry still pointing at the value whose
//      count it just dropped. There is no cycle trial behind a `free`,
//      so the text half has nothing to defer for and does it in the
//      order that reads naturally.
//
//   4. **A map's values are released through a generated
//      TRAMPOLINE.** A map's entries are opaque to codegen in a way an
//      array's flat buffer is not -- there is no data pointer to walk
//      -- so the cascade hands `festina_map_for_each` a generated
//      function that takes a raw i64 and a key. Its body is one call,
//      and WHICH call is the whole mechanism: a text value is FREED
//      (no header to release), a struct value is RELEASED. Unlike the
//      per-type release wrappers the trampoline is never cached: each
//      map release generates its own.
//
//   5. **`Math.floorDiv` floors; `sdiv` truncates toward zero.** The
//      two agree on every non-negative pair, so a test that only ever
//      divides positives measures nothing. `-7 floorDiv 2` is -4, not
//      -3, and the adjustment is a whole branch: subtract one when the
//      remainder is non-zero AND its sign differs from the divisor's.
//      Division by zero answers null (claude.md #57), which is the
//      reason there is a branch at all rather than four arithmetic
//      instructions.
//
//   6. **A float literal's window is decided by its VALUE, not its
//      spelling.** The digits are parsed exactly -- integer digits
//      times a power of ten -- so the parse refuses anything past the
//      point where an i64 stops being exact. Trailing zeros in the
//      FRACTION carry no information and are stripped first, which is
//      exact rather than approximate: dropping one divides both the
//      digit integer and the power of ten by the same 10. Without it
//      `4503599627370496.0` -- 2^52, exactly representable, and in
//      this port's own source -- is refused for a zero that says
//      nothing.

text raw = 'alpha beta gamma'
map[text] names = {'first': 'alpha'}
arr[text] sink
int bumps = 0

struct Row {
    label:text
    n:int
}

// Mechanism 1, the text half. `parts[0]` on a BINDING is a borrowed
// pointer and takes no copy; the same index straight off `split` owns
// what it hands back. Both spellings, so the difference is visible
// rather than asserted.
int func borrowedElement() {
    arr[text] parts = raw.split(' ')
    text one = parts[0]
    return one.length + parts.length
}

int func ownedElement() {
    text first = raw.split(' ')[0]
    // Read straight into an expression, with no binding to own it:
    // the copy is still minted and the array still released.
    int n = raw.split(' ')[1].length
    return first.length + n
}

// Mechanism 1, the refcounted half. The array is released, and its own
// element cascade decrements the row the index just retained back to
// the single reference this expression holds.
arr[Row] func makeRows() {
    arr[Row] out
    Row a
    a.label = 'a'
    a.n = 4
    out.push(a)
    Row b
    b.label = `b${1}`
    b.n = 5
    out.push(b)
    return out
}

int func ownedRow() {
    Row got = makeRows()[1]
    return got.n + makeRows()[0].n
}

// Mechanism 1, the scalar half: nothing to mint, the container simply
// released. Beside the two above so "no minting" is a measured answer
// rather than an absence.
arr[int] func makeNumbers() {
    arr[int] ns = [10, 20, 30]
    return ns
}

int func ownedScalar() {
    return makeNumbers()[2]
}

// Mechanisms 2 and 3. `alias` is an identifier -- borrowed, so the set
// copies. `owned` is a concatenation -- already exclusively this
// expression's, so the set takes it as it stands. The third write
// overwrites a live entry, which is what makes the old buffer's free
// reachable at all.
text held = 'held'

text func rendered() {
    return `made${bumps}`
}

int func writes() {
    names['alias'] = held
    names['owned'] = 'x' + held
    names['fromCall'] = rendered()
    names['first'] = 'replaced'
    names['first'] = names['first']
    return names['alias'].length + names['owned'].length
}

// Mechanism 4, both shapes. A map local that does not escape still has
// its values released at scope exit, through the trampoline generated
// for its value type.
int func localTextMap() {
    map[text] mine = {'k': 'v'}
    mine['j'] = rendered()
    return mine['k'].length + mine['j'].length
}

int func localRowMap() {
    map[Row] rows
    Row r
    r.label = 'r'
    r.n = 6
    rows['only'] = r
    return rows['only'].n
}

// A map read through an owning receiver: the same mint-then-release as
// an array index, on the other container shape.
map[text] func makeMap() {
    map[text] m = {'key': 'value'}
    return m
}

int func ownedMapRead() {
    text v = makeMap()['key']
    return v.length
}

// Mechanism 5. Negative operands both ways round, since floor and
// truncation agree on every non-negative pair.
int func floors() {
    int a = Math.floorDiv(7, 2)
    int b = Math.floorDiv(0 - 7, 2)
    int c = Math.floorDiv(7, 0 - 2)
    int d = Math.floorDiv(0 - 7, 0 - 2)
    int e = Math.floorDiv(6, 3)
    return a + b + c + d + e
}

// Division by zero answers this type's own null rather than trapping,
// which is the reason the construct is a branch at all.
int func floorsByZero() {
    int z = Math.floorDiv(1, 0)
    if z == null { return 1 }
    return 0
}

// Mechanism 6: a fraction that is nothing but zeros, on a value that
// only fits once they are stripped.
float func wideLiteral() {
    return 4503599627370496.0
}

float func ordinaryLiteral() {
    return 1.250
}

log(borrowedElement())
log(ownedElement())
log(ownedRow())
log(ownedScalar())
log(writes())
log(localTextMap())
log(localRowMap())
log(ownedMapRead())
log(floors())
log(floorsByZero())
log(wideLiteral())
log(ordinaryLiteral())
log(names['first'])
log(sink.length)
log(bumps)
