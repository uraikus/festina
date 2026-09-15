// Parsing and rendering JSON, choosing between two values, and what a
// call site owes when the callee never comes back.
//
// Three mechanisms that only look unrelated. Each is a case where
// something OUTSIDE the expression decides its ownership: a JSON
// builder half-fills a value that nothing else owns yet, a ternary
// hands back whichever arm ran, and a call site holds temporaries
// across a call that may unwind past it.
//
// Measured, not assumed. The breakages live in `bootstrap/canary.py`
// and are re-run by tests/test_bootstrap_canary.py, which FAILS if
// this file stops making them visible.
//
//   1. **A JSON parse is LENIENT.** An unrecognized key's value is
//      skipped rather than refused, so an extra field is fine and a
//      missing one leaves its default. A DUPLICATE key overwrites,
//      last one wins -- which means whatever the earlier one stored
//      has to be given back, the same convention a map literal's own
//      repeated key follows.
//
//   2. **A half-built value is registered for unwinding.** Every
//      builder is hand-written IR outside the ordinary scope
//      tracking, so nothing else could free what it holds: the header
//      it is filling in, and each key text between its read and its
//      free. A parse that throws three levels down releases them in
//      the right order because nested builders push and pop above
//      their caller's entries.
//
//   3. **A map target takes ARBITRARY keys**, where a struct target
//      matches a fixed set and skips the rest. Same JSON object, two
//      completely different loops.
//
//   4. **A render caps its depth at 32 and a value past the cap
//      renders as null.** A cyclic value is constructible, and a
//      debug rendering that crashed the program it is debugging would
//      be worse than an honest truncation. A map renders its LIVE
//      entries only -- buckets are walked by capacity, so an empty
//      slot and a tombstone both have to be recognized, and whether a
//      comma is owed cannot be read off the index the way an array's
//      can.
//
//   5. **A ternary ARM is normalized to something genuinely owned
//      before the phi**, and the result is then an owning source. The
//      old rule -- treat the whole ternary as aliasing -- was right
//      only when BOTH arms were aliasing, and leaked the moment
//      either was fresh: the caller claimed the result once whichever
//      arm ran, so a fresh arm's own correct ownership got an extra
//      claim with nothing to balance it.
//
//      A NULL arm has no type of its own, so when the CONSEQUENT is
//      the null one the two arms are emitted in the opposite order --
//      there is nothing to emit for a null until the type is known.
//
//   6. **A call site's fresh argument temporaries survive a throwing
//      callee.** It owns whatever it built for the call and releases
//      it right after -- but a callee that throws never returns
//      there, so the temporaries are registered for the duration of
//      the call. That was the last thing leaking once every frame's
//      locals were covered.

struct Person {
    id:int
    name:text
    tags:arr[text]
}

struct Wrapper {
    who:Person
    n:int
}

// A map reaches the parser only as a FIELD -- `.toStruct(map[int])` is
// not a spelling the language has -- which is exactly why the map
// builder needs a case of its own rather than riding the struct one.
struct Scores {
    name:text
    values:map[int]
}

arr[text] sink
int caught = 0

// Mechanisms 1 and 3. The same JSON text parsed into a struct (a fixed
// field set, extra keys skipped) and into a map (every key an entry),
// plus a duplicate key so the overwrite path runs.
text doc = '{"id": 7, "name": "ada", "extra": [1,2], "name": "grace"}'
text nums = '{"name": "ada", "values": {"a": 1, "b": 2}}'
text list = '[{"id": 1, "name": "x"}, {"id": 2, "name": "y"}]'
text grid = '[[1, 2], [3, 4, 5]]'

int func parses() {
    Person p = doc.toStruct(Person)
    Scores s = nums.toStruct(Scores)
    arr[Person] ps = list.toArr(Person)
    arr[arr[int]] g = grid.toArr(arr[int])
    return p.id + p.name.length + s.values['a'] + ps.length + ps[1].id
         + g.length + g[1].length
}

// Mechanism 2. The throw comes from INSIDE the parse -- malformed
// input -- with a half-built value on the builder's own frame and a
// live local in the caller besides.
int func parseFails() {
    int n = 0
    try {
        text held = `holding ${caught}`
        Person bad = '{"id": "not a number"}'.toStruct(Person)
        n = bad.id
    } catch (err:text) {
        n = err.length
    }
    return n
}

// Mechanism 4. A struct containing a struct containing an array, all
// rendered through one generated walk, plus a map whose entries have
// been deleted so the tombstone skip runs.
int func renders() {
    Person p
    p.id = 1
    p.name = 'ada'
    p.tags = ['x', 'y']
    Wrapper w
    w.who = p
    w.n = 2

    map[text] holes = {'a': 'one', 'b': 'two', 'c': 'three'}
    delete holes['b']

    arr[int] xs = [1, 2, 3]
    text a = w.toText()
    text b = holes.toText()
    text c = xs.toText()
    text d = `${w} ${holes} ${xs}`
    return a.length + b.length + c.length + d.length
}

// Mechanism 5. Every arm shape: two fresh, two aliasing, one of each,
// and both null positions.
text label = 'aliased'
arr[int] shared = [9]

text func made(i:int) {
    return `made ${i}`
}

arr[int] func freshArr() {
    arr[int] a = [1, 2]
    return a
}

int func chooses(flip:bool) {
    text bothFresh = flip ? made(1) : made(2)
    text oneFresh = flip ? made(3) : label
    text bothAlias = flip ? label : label
    arr[int] refFresh = flip ? freshArr() : shared
    arr[int] refAlias = flip ? shared : shared

    // The null positions, each way round. The non-null arm is what
    // gives the other its type, so which side is null decides the
    // order the two are emitted in.
    arr[int] nullAlt = flip ? freshArr() : null
    arr[int] nullCons = flip ? null : freshArr()

    int n = bothFresh.length + oneFresh.length + bothAlias.length
    n = n + refFresh.length + refAlias.length
    if nullAlt != null { n = n + nullAlt.length }
    if nullCons != null { n = n + nullCons.length }
    return n
}

// Mechanism 6. `explodes` never returns to its caller, so the
// template text and the array literal built for the call are the
// unwinding's to release.
void func explodes(msg:text, xs:arr[int]) {
    throw msg
}

int func guardsArguments() {
    int n = 0
    try {
        explodes(`boom ${caught}`, [1, 2, 3])
    } catch (err:text) {
        n = err.length
    }
    return n
}

log(parses())
log(parseFails())
log(renders())
log(chooses(true))
log(chooses(false))
log(guardsArguments())
log(sink.length)
log(caught)
