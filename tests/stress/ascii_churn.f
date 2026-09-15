// claude.md #256: the `ascii` type under churn -- a refcounted string
// whose length lives in its own header. Everything here is shaped to
// catch the two failure modes that matter: a missing release (the
// header is 16 bytes back, so a free at the wrong offset corrupts
// rather than leaks) and a release of one of the 128 immortal
// single-character singletons `s[i]` hands back without allocating.
ascii base = 'the quick brown fox jumps over the lazy dog'
int total = 0
int hits = 0
int aliasTotal = 0

for int i = 0, i < 2000, i++ {
    // s[i] -- an immortal singleton every time, never freed, never
    // allocated. The loop drops each one on the next iteration.
    ascii ch = base[i % base.length]
    if ch == 'o' { hits = hits + 1 }
    total = total + base.charCodeAt(i % base.length)

    // Fresh, genuinely heap-allocated ascii values: a slice and a
    // concatenation, both dropped at the end of the iteration.
    ascii part = base.slice(4, 9)
    ascii joined = part + '-' + ch
    total = total + joined.length

    // Round-tripping through text exercises both conversion
    // directions, each of which allocates its own buffer.
    text asText = part.toText()
    ascii back = asText.toAscii()
    if back == part { total = total + 1 }
}

// uraikus/archtelos-browser's finding 2: ALIASING one ascii binding
// from another. Everything above this line allocates a fresh value per
// iteration and drops it, which is why this file ran clean for a whole
// release while `ascii b = a` was freeing a's buffer out from under it:
// the declaration branch never claimed the reference the scope-exit
// branch went on to drop. Each shape below is one of the six the bug
// report reproduced it with, and every one of them is a released alias
// whose original is still live and still read afterwards.
struct Holder { value:ascii }

void func aliasParameter(srcIn:ascii) {
    ascii src = srcIn
    aliasTotal = aliasTotal + src.length
}

void func aliasStoredParameter(m:map[ascii], k:text, v:ascii) {
    m[k] = v
}

for int a = 0, a < 2000, a++ {
    ascii owned = base.slice(4, 9)

    // a local aliasing another local, both read after the alias dies
    ascii alias1 = owned
    aliasTotal = aliasTotal + alias1.length

    // a parameter rebound to a local inside the callee
    aliasParameter(owned)

    // an array element, a struct field and a map entry, each aliased
    // out into a local that is released at the end of this iteration
    // while the container still owns the value
    arr[ascii] elems = [owned, base.slice(0, 3)]
    ascii fromElem = elems[0]
    aliasTotal = aliasTotal + fromElem.length

    Holder h
    h.value = owned
    ascii fromField = h.value
    aliasTotal = aliasTotal + fromField.length

    map[ascii] byKey = {}
    byKey['k'] = owned
    ascii fromMap = byKey['k']
    aliasTotal = aliasTotal + fromMap.length

    // a parameter stored into a map, where the caller's argument was
    // itself a local -- the map outlives the call and must own its own
    // reference to it
    map[ascii] stored = {}
    aliasStoredParameter(stored, 'p', owned)
    aliasTotal = aliasTotal + stored['p'].length

    // an IMMORTAL aliased the same way: a literal and a global, neither
    // of which may be freed however many aliases are dropped
    ascii lit = 'immortal'
    ascii litAlias = lit
    ascii globalAlias = base
    aliasTotal = aliasTotal + litAlias.length + globalAlias.length

    // and the original, still intact after every alias above has been
    // released -- a read that segfaults or reports garbage the moment
    // the retain goes missing again
    aliasTotal = aliasTotal + owned.length
}

log(total)
log(hits)
log(base.length)
log(aliasTotal)
