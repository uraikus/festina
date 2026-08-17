// claude.md #77/#78/#85/#97/#102: struct values through every shape that
// changes who owns them -- returned, aliased, nested, auto-vivified,
// stored in a container, discarded outright -- plus sqlite result rows,
// which are their own allocation shape with their own release function.

table Person {
    id:int
    name:text
    note:text
}

struct Inner { n:int label:text }
struct Summary { total:int  biggest:text }
struct Outer { count:int inner:Inner xs:arr[int] m:map[int] }

Outer func makeOuter(n:int) {
    Outer o
    o.count = n
    o.inner.n = n
    o.inner.label = `label ${n}`
    o.xs.push(n)
    o.m[`k${n}`] = n
    return o
}

Inner func pick(a:Inner, b:Inner, useA:bool) {
    if useA { return a }
    return b
}

sqlite('DELETE FROM Person')
int i = 0
while i < 300 {
    sqlite('INSERT INTO Person (id, name, note) VALUES (?, ?, ?)',
            [i, `person ${i}`, null])
    i = i + 1
}

int total = 0
i = 0
while i < 400 {
    // Returned struct, then reached through without ever binding it.
    Outer o = makeOuter(i)
    total = total + o.inner.n + o.xs.length + o.m[`k${i}`]
    // claude.md #102: a call result reached for one field and then
    // discarded, with nothing binding it. The field is a SCALAR on
    // purpose -- that is the case the release is safe for.
    total = total + makeOuter(i).count

    // claude.md #108: the same thing reached through a CHAIN, which
    // #102 could not cover and which leaked the whole object graph.
    // The decision is made at the outermost link now, where the type of
    // the value that escapes is finally known.
    total = total + makeOuter(i).inner.n

    // .length off a chain, and off a call result directly -- neither
    // ever reached the member-load path before #108, so `rows(x).length`
    // leaked despite #102's own docstring claiming otherwise.
    total = total + makeOuter(i).xs.length

    // A member load inside a call ARGUMENT is not part of the outer
    // chain, and must still be released on its own schedule.
    total = total + makeOuter(makeOuter(i).count).inner.n

    // A ternary between two locals: whichever loses still has to go.
    Inner a
    a.n = 1
    a.label = `a${i}`
    Inner b
    b.n = 2
    b.label = `b${i}`
    Inner chosen = pick(a, b, i % 2 == 0)
    total = total + chosen.n

    // Deeply unassigned fields, reached rather than written.
    Outer fresh
    total = total + fresh.inner.n + fresh.xs.length

    // Query rows: text columns, null columns, and the whole array
    // released together at scope exit.
    arr[Person] rows = sqlite('SELECT * FROM Person LIMIT 20')
    total = total + rows.length
    total = total + rows[0].id
    if rows[0].note == null {
        total = total + 1
    }
    Person first = rows[0]
    total = total + first.id

    // claude.md #112: a struct as the query target -- aliased and
    // computed columns land in struct fields, each row converted into a
    // real refcounted struct whose text fields the conversion now owns.
    arr[Summary] sums = sqlite(
        "SELECT count(*) AS total, max(name) AS biggest FROM Person")
    total = total + sums[0].total
    if sums[0].biggest == null { log('unreachable') }
    Summary keepSum = sums[0]
    free sums
    if keepSum.biggest == null { log('unreachable') }

    // Scalar queries have their own path.
    total = total + sqliteInt('SELECT count(*) FROM Person')
    i = i + 1
}
log(total)
