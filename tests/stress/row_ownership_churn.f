// claude.md #265: a table row carries the ordinary refcount header, so
// it is an ordinary refcounted value everywhere -- bound, aliased,
// passed, returned, stored in a container, freed by hand.
//
// Every shape below was broken before that. Two of them CRASHED (a row
// returned from a function that owned its array: the array was released
// on the way out and freed the row the caller was about to read), one
// leaked its whole array on every access, and the rest only worked
// because the array they borrowed from was leaked instead of reclaimed.
//
// The property that makes all of them work now is one sentence: the
// array owns one reference to each of its rows, and anything that wants
// a row to outlive its array takes another. So this program is a
// double-free test as much as a leak test -- an extra release anywhere
// frees a row the array is still going to release, and ASan says so.

table People { id:int  name:text }

sqlite('DELETE FROM People')
sqlite('INSERT INTO People (id, name) VALUES (?, ?)', [1, 'ada'])
sqlite('INSERT INTO People (id, name) VALUES (?, ?)', [2, 'grace'])

arr[People] func rows() {
    arr[People] r = sqlite('SELECT * FROM People ORDER BY id')
    return r
}

// A row returned from a function that OWNS the array it came from.
// This was the use-after-free: the local array died at the return.
People func firstOwned() {
    arr[People] r = sqlite('SELECT * FROM People ORDER BY id')
    return r[0]
}

// The same thing one binding further on, so the row escapes through a
// local rather than straight out of the index expression.
People func firstViaLocal() {
    arr[People] r = sqlite('SELECT * FROM People ORDER BY id')
    People p = r[0]
    return p
}

// Borrowing from an array the CALLER owns -- always worked, and must
// keep working: the row outlives the call because the caller's array
// does, not because of anything this function did.
People func pick(xs:arr[People]) {
    return xs[1]
}

text func nameOf(p:People) {
    return p.name
}

int names = 0
int ids = 0

for int i = 0, i < 500, i++ {
    // Returned out of a function that owned the array.
    People a = firstOwned()
    if a.name == 'ada' { names = names + 1 }
    People b = firstViaLocal()
    ids = ids + b.id

    // Bound off a call-result array nobody holds.
    People c = rows()[1]
    if c.name == 'grace' { names = names + 1 }

    // Bound off an array that IS held, then outliving it by hand.
    arr[People] held = sqlite('SELECT * FROM People ORDER BY id')
    People d = held[0]
    free held                      // the row survives on d's own reference
    if d.name == 'ada' { names = names + 1 }
    free d

    // Borrowed from a caller-owned array, aliased, and mutated through
    // the alias -- rows alias, and that must stay true.
    arr[People] owned = sqlite('SELECT * FROM People ORDER BY id')
    People e = pick(owned)
    People alias = e
    alias.name = 'renamed'
    if e.name == 'renamed' { names = names + 1 }

    // Passed as an argument, both borrowed and freshly produced.
    names = names + (nameOf(e) == 'renamed' ? 1 : 0)
    names = names + (nameOf(rows()[0]) == 'ada' ? 1 : 0)

    // Stored in containers that outlive the array they came from.
    arr[People] collected
    collected.push(rows()[0])
    map[People] byKey
    byKey['g'] = rows()[1]
    if collected[0].name == 'ada' { names = names + 1 }
    if byKey['g'].name == 'grace' { names = names + 1 }

    // A column read straight off a call-result row, in the positions
    // that used to leak the array every time.
    text line = `${rows()[0].name}/${rows()[1].name}`
    if line == 'ada/grace' { names = names + 1 }
    ids = ids + rows()[1].id
}

log(names)
log(ids)
