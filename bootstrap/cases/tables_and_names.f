// A schema that exists before the program starts, a statement that
// runs and returns nothing, and a name that means one thing on the
// right of an `=` and another on the left.
//
// Three mechanisms that share one property: each is decided somewhere
// OTHER than where it is written. A `table` declaration emits nothing
// at all where it stands -- what it produces is a line in main's own
// prologue, which is built long afterwards. A `sqlite()` call's shape
// is decided by whether anything catches its result. And a
// declaration's own name is not in scope for its own initializer,
// which is the only reason the initializer below means the function.
//
// Measured, not assumed. The breakages live in `bootstrap/canary.py`
// and are re-run by tests/test_bootstrap_canary.py, which FAILS if
// this file stops making them visible.
//
//   1. **A declared `table` reaches the program at RUN time**, not at
//      type-check time. A struct's declaration is spent entirely on
//      the type section; a table's produces a festina_sync_table call
//      in main's prologue, before __festina_main runs a single
//      statement -- so a query on the program's very first line
//      already has a schema to query.
//
//      Every table is synced whether or not anything queries it, which
//      is both simpler than tracking use and free: the call does
//      nothing to a table already shaped right.
//
//   2. **A sync call's three string constants are interned in a fixed
//      order** -- column names, then column types, then the table's
//      own name -- because constants are NUMBERED and the order is
//      therefore observable in the output. Two tables are declared
//      below for the same reason: with one, every ordering that got
//      the first table right would look correct.
//
//   3. **A SQL string that cannot change is compiled once.** A literal
//      gets a private slot of its own per call site and is prepared
//      into sqlite bytecode the first time that line is reached; a
//      template gets the per-call prepare, because the same site can
//      legitimately see different SQL each time. Both spellings are
//      here, since the difference is invisible in either alone.
//
//   4. **The parameter list is call SYNTAX, not an argument.**
//      `[1, 'a', 2.5, true]` holds four types at once, which no arr[T]
//      value can; each element is bound by its own compile-time type
//      instead. A text parameter is copied by sqlite before the call
//      returns, so a temporary built for it is freed right there.
//
//   5. **A declaration's name is not in scope for its own
//      initializer.** `func[int,int]:int cmp = cmp` type-checks
//      because the initializer is resolved in the scope BEFORE the
//      declaration -- the same reason `int n = n + 1` is rejected as
//      an unknown variable and this is not. Bind the name first and
//      the local is initialized from its own uninitialized slot and
//      then called.
//
//   6. **`\n` in a regex names a byte.** A regex literal cannot span
//      lines, so it is the only spelling a newline has in one. Left
//      alone it reaches POSIX as an escaped literal 'n', which matches
//      the letter and misses the newline.

table People {
    id:int
    name:text
}

table Visits {
    id:int
    place:text
    weight:float
    ok:bool
}

// Mechanisms 1, 3 and 4. The literal INSERT is cached per site; the
// template one cannot be, and its own text is the caller's to free
// once the statement has compiled it.
int rows = 0

int func writes() {
    sqlite('INSERT INTO People (id, name) VALUES (?, ?)', [1, 'ada'])
    sqlite('INSERT INTO Visits (id, place, weight, ok) VALUES (?, ?, ?, ?)',
           [7, 'the summit', 2.5, true])
    sqlite(`DELETE FROM People WHERE id = ${rows + 99}`)
    // A null parameter has no type of its own and binds as SQL NULL
    // rather than as any of the four above.
    sqlite('INSERT INTO People (id, name) VALUES (?, ?)', [2, null])
    sqlite('DELETE FROM People WHERE id > 0')
    sqlite('DELETE FROM Visits WHERE id > 0')
    return 0
}

// Mechanism 5. Both halves: a func value initialized from the function
// it shadows, and an ordinary local shadowing a function name with an
// initializer that does NOT mention it -- the control, since the fix
// must not change what that one resolves to either.
int func cmp(a:int, b:int) {
    return a - b
}

int func size(v:int) {
    return v + 1
}

int func shadows() {
    func[int,int]:int cmp = cmp
    int size = 41
    return cmp(7, 3) + size
}

// Mechanism 6. A pattern whose only spelling of the byte is the
// escape, and the letter it would otherwise have matched.
text lines = 'one
two'

int func controls() {
    int n = 0
    if /e\nt/.test(lines) { n = n + 1 }
    if /ent/.test('ent') { n = n + 2 }
    if /e\nt/.test('ent') { n = n + 100 }
    text joined = lines.replace(/\n/, ' ')
    return n + joined.length
}

log(writes())
log(shadows())
log(controls())
log(rows)
