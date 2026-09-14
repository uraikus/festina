// What a program needs to be a COMMAND rather than a library, and the
// two scope bugs that only a command-shaped program reaches.
//
// The bootstrap's five entry points were held back for five slices by
// what looked like a list of ten missing constructs. Two of them were
// real -- `argv` and `close` -- and the other eight were consequences
// of a single shadowing bug: a name declared both at a driver's top
// level and inside one of the passes it imports. Fix that and eight
// "unported constructs" disappeared at once, which is the useful
// lesson about blocker tables and not the useful lesson about
// compilers.
//
// Measured, not assumed. The breakages live in `bootstrap/canary.py`
// and are re-run by tests/test_bootstrap_canary.py, which FAILS if
// this file stops making them visible.
//
//   1. **`argv` is an ordinary `arr[text]` global with one unusual
//      thing about it: its INITIAL value.** There is no declaration
//      for it anywhere -- codegen registers the name itself -- and
//      `main` stores the real argc/argv into it before any user code
//      runs. Everything after that is an ordinary global read, copy,
//      index and free, with no special-casing at all. Leaving the
//      registration out does not produce a wrong program; it produces
//      a compiler that cannot see the name.
//
//   2. **`close(code)` is not `exit(code)`.** It goes through
//      `festina_program_exit`, which runs a declared `on exit(code)`
//      handler first -- a runtime concern, so codegen calls the
//      runtime rather than the libc function directly.
//
//   3. **A declaration INSIDE a function is local even when a global
//      shares its name.** The scalar path always knew this; the
//      managed path read the global table alone and got it wrong
//      silently -- the local got no storage at all, and every read of
//      the name went to the global instead. A driver is where this
//      first bites, because a driver's top level and the passes it
//      imports naturally reach for the same short names.
//
//   4. **`__festina_main` gets a fresh local scope, like every
//      function.** It was inheriting whichever function happened to be
//      emitted LAST, so a top-level declaration that shadowed nothing
//      at all could still resolve to an unrelated body's slot --
//      storing somewhere out of scope and leaving the global it had
//      just declared untouched. The two shapes are opposites and both
//      are below, because fixing either one alone still leaves the
//      other wrong.
//
//   5. **`.keys()` answers `arr[text]`; `.values()` answers `arr[T]`.**
//      Different runtime calls and different element types, and values
//      carries the element type as three constants -- stride, "is
//      refcounted", "is text" -- because the runtime walks buckets
//      without knowing what is in them and only the compiler does.
//
//   6. **Deleting an entry has to give its VALUE back.** A map's
//      buckets are opaque to codegen and codegen's types are opaque to
//      the runtime, so the two meet at a generated trampoline, exactly
//      as the release cascade does. A scalar value owns nothing and
//      passes null; both are below, so the difference is measured
//      rather than assumed.

map[text] labels = {'a': 'alpha', 'b': 'beta'}
map[int] counts = {'x': 1, 'y': 2}
arr[text] sink

// Mechanisms 3 and 4 need a name that is BOTH a global here and a
// local inside a function. `rows` is the global; `rows` inside
// `shadows()` is a different binding entirely, and must get storage of
// its own.
arr[text] rows = ['global']

// Mechanism 3: the managed local that shadows the global above. The
// scalar local beside it is the shape that always worked, so the two
// answers sit together.
int n = 7

int func shadows() {
    arr[text] rows
    rows.push('local')
    rows.push('also local')
    int n = 3
    return rows.length + n
}

// Mechanism 4, the opposite direction: `tally` is a local inside this
// function and nothing else, and the top-level `tally` below shadows
// nothing at all. It must still be a global, and must not resolve to
// this function's slot.
int func hasALocalCalledTally() {
    int tally = 99
    arr[text] parts
    parts.push('p')
    return tally + parts.length
}

// Mechanism 5. Both calls, and a map of each element family, so the
// three constants values() carries are exercised rather than assumed.
int func readsBack() {
    arr[text] ks = labels.keys()
    arr[text] vs = labels.values()
    arr[text] ik = counts.keys()
    arr[int] iv = counts.values()
    // With no binding to own it, the length read is also the release --
    // and WHICH release is the point: the keys of an int-valued map are
    // still text, so this reclaims through the generated arr[text]
    // cascade rather than the generic array release. A keys() that
    // answered the map's own element type would be refused outright at
    // every binding above, which is a ratchet detection rather than a
    // difference; this spelling compiles either way and disagrees.
    int loose = counts.keys().length
    int alsoLoose = counts.values().length
    return ks.length + vs.length + ik.length + iv.length + loose + alsoLoose
}

// Mechanism 6, both shapes: a map whose values own buffers, and one
// whose values own nothing.
int func deletes() {
    map[text] owning = {'k': 'v', 'j': 'w'}
    map[int] scalar = {'k': 1, 'j': 2}
    delete owning['k']
    delete scalar['k']
    return owning['j'].length + scalar['j']
}

// Mechanism 1. `argv[0]` is the program's own path and is always
// present, so this indexes nothing that might not be there -- Festina
// does not bounds-check, and a case file that segfaults still produces
// IR for the harness to compare (cases/indexing.f learned that one).
int func readsArgv() {
    int seen = argv.length
    text first = argv[0]
    arr[text] copied = argv
    return seen + first.length + copied.length
}

int tally = 11

log(shadows())
log(hasALocalCalledTally())
log(readsBack())
log(deletes())
log(readsArgv())
log(rows.length)
log(rows[0])
log(n)
log(tally)
log(sink.length)

// Mechanism 2, last, so everything above still runs. A program that
// ends this way exits 0 exactly as falling off the end would -- the
// difference is the handler `festina_program_exit` would run first,
// which is the whole reason it is not a plain exit().
close(0)
