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
    // purpose -- that is the case the release is safe for. The chained
    // form (makeOuter(i).inner.n) still leaks, deliberately: releasing
    // the parent there would free the very field just loaded. See
    // todo.md.
    total = total + makeOuter(i).count

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

    // Scalar queries have their own path.
    total = total + sqliteInt('SELECT count(*) FROM Person')
    i = i + 1
}
log(total)
