// claude.md #302: a non-text map key is RENDERED as if by .toText(),
// and every rendering allocates. The map copies the key with strdup, so
// the buffer the compiler made has no owner left once the call returns
// -- one leaked string per set, per get and per delete otherwise, which
// at loop volume is the difference between a program and a memory leak.
//
// Every key-taking position is exercised because each frees in a
// different place: a get frees at the call site, a set inside
// _emit_map_set, a delete in the delete statement's own emitter, and a
// literal through the set path with the key expression's own ownership
// answer rather than the source expression's.

map[int] byInt = {}
map[text] byFloat = {}
map[int] byBool = {}

int i = 0
while i < 3000 {
    byInt[i] = i * 2
    byFloat[i * 1.5] = 'v'
    byBool[i % 2 == 0] = i

    int got = byInt[i]
    text alsoGot = byFloat[i * 1.5]

    delete byInt[i]
    i++
}

// A literal whose keys are rendered too, rebuilt every iteration so the
// literal path churns rather than running once.
int j = 0
while j < 1000 {
    map[text] lit = {j: 'a', j + 1: 'b', true: 'c'}
    delete lit[j]
    j++
}

log(byInt.keys().length)
log(byFloat.keys().length)
log(byBool.keys().length)
