// Rows that outlive the query, handles kept in containers, and a
// database path the program chooses for itself.
//
// Three mechanisms that share one property: each is a value whose
// STORAGE was laid out by something other than codegen, so nothing
// generic can be pointed at it. A sqlite row is a flat block the
// runtime built, with its refcount header one slot before the offsets
// every column is measured from. A blob is a handle whose contents
// hang off a pointer the slot holds. And the database path is decided
// before any of the program's own statements run.
//
// Measured, not assumed. The breakages live in `bootstrap/canary.py`
// and are re-run by tests/test_bootstrap_canary.py, which FAILS if
// this file stops making them visible.
//
//   1. **A row's release is generated per TABLE, and frees its own
//      text columns first.** Which columns hold a heap pointer is
//      decided by the identical rule the runtime used when BUILDING
//      the row, read off the same declared column types: a `text`
//      column was strdup'd, everything else is a plain i64. Then the
//      allocation itself -- from its BASE, one i64 before the payload,
//      never from the payload every column offset starts at.
//
//   2. **A row is reference counted**, so a row an array gave out
//      survives the array it came from. `free`ing the array while a
//      binding still holds one of its rows is the case that proves it:
//      without a count the row would be gone and the binding dangling.
//
//   3. **The runtime's row buffer IS an arr[T] data pointer.** One
//      8-byte pointer per row is already the layout an array of
//      pointer-shaped elements expects, so the collecting query builds
//      a fresh header around the buffer as it stands rather than
//      copying it.
//
//   4. **A handle ELEMENT owns a whole reference**, exactly as a
//      struct element does -- and gets its own destructor, never plain
//      free. An `arr[blob]` that escaped, was reassigned or was
//      returned used to leak one open file handle per element for as
//      long as the array lived, because the predicate deciding "does
//      this element own anything" listed containers and structs and
//      stopped there.
//
//   5. **A map entry's OLD value is released by its own type.** The
//      struct spelling and the element spelling agree for a struct, so
//      the narrower one survived a long time -- but a map of rows, of
//      handles or of containers each need their own release, and the
//      generic one drops a row's columns, a blob's buffer or an inner
//      array's elements on the floor.
//
//   6. **`DatabaseURL` is spent in main's prologue, not where it is
//      written.** It runs before the database is opened, and so before
//      every ordinary global's own initializer -- which is exactly why
//      it may read the environment and may not read another global.

DatabaseURL = environment.FESTINA_CASE_DB

table People {
    id:int
    name:text
}

table Scores {
    id:int
    score:float
    passed:bool
}

int total = 0

// Mechanisms 1, 2 and 3. The rows are queried, bound, outlived and
// released, in every order that tells one mechanism from another.
int func rows() {
    sqlite('DELETE FROM People')
    sqlite('INSERT INTO People (id, name) VALUES (?, ?)', [1, 'ada'])
    sqlite('INSERT INTO People (id, name) VALUES (?, ?)', [2, 'grace'])
    // A parameter list holding four types at once, which no arr[T]
    // value can -- and a null, which has no type of its own.
    sqlite('DELETE FROM Scores')
    sqlite('INSERT INTO Scores (id, score, passed) VALUES (?, ?, ?)',
           [1, 2.5, true])
    sqlite('INSERT INTO Scores (id, score, passed) VALUES (?, ?, ?)',
           [2, 0.5, null])
    // A template, which cannot be cached per site the way a literal is.
    sqlite(`DELETE FROM People WHERE id = ${total + 99}`)

    arr[People] people = sqlite('SELECT * FROM People ORDER BY id')
    int n = people.length + people[0].id + people[1].name.length

    // The row outliving the array it came from, by its own count.
    People kept = people[0]
    free people
    n = n + kept.name.length
    free kept

    arr[Scores] scores = sqlite('SELECT * FROM Scores ORDER BY id')
    n = n + scores.length + scores[0].id
    return n
}

// Mechanism 4, both spellings: a literal built from coerced paths, and
// a push. The array escapes into a struct field, which is what makes
// its elements' own references real rather than notional.
struct Holder {
    files:arr[blob]
}

Holder shelf

int func handles() {
    arr[blob] fs = ['bootstrap/cases/rows_and_handles.f']
    fs.push('bootstrap/cases/rows_and_handles.f')
    shelf.files = fs
    return shelf.files.length + fs.length
}

// Mechanism 5. The same key written twice, so the second write has a
// live value of the right type to give back -- once for a row, once
// for a handle.
int func overwrites() {
    arr[People] ps = sqlite('SELECT * FROM People ORDER BY id')
    map[People] byKey = {}
    byKey['x'] = ps[0]
    byKey['x'] = ps[1]

    map[blob] byPath = {}
    byPath['f'] = 'bootstrap/cases/rows_and_handles.f'
    byPath['f'] = 'bootstrap/cases/rows_and_handles.f'
    return byKey['x'].id + byKey.keys().length + byPath.keys().length
}

// A row as a PARAMETER and as a return value, since a borrowed row and
// an owned one are bound through different paths.
text func nameOf(p:People) {
    return p.name
}

People func firstPerson() {
    arr[People] ps = sqlite('SELECT * FROM People ORDER BY id')
    return ps[0]
}

int func passes() {
    People p = firstPerson()
    int n = nameOf(p).length
    free p
    return n
}

log(rows())
log(handles())
log(overwrites())
log(passes())
log(total)
