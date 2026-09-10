// A table-row column read off a CALL-RESULT array -- `rows()[0].name`
// -- in every position that shape can appear in.
//
// Rows carry the ordinary refcount header, so a row read out of an
// owning array is retained and the array released, netting the one
// reference the expression owns. Each column type exercises a different
// half of that: a text column must be COPIED before the row's own
// reference could go, a blob column RETAINED, and an int column needs
// neither. Getting any of them wrong is a use-after-free or a double
// free, not a leak -- which is why this runs under ASan and not only
// LeakSanitizer.

table People { id:int  name:text }

sqlite('DELETE FROM People')
sqlite('INSERT INTO People (id, name) VALUES (?, ?)', [1, 'ada'])
sqlite('INSERT INTO People (id, name) VALUES (?, ?)', [2, 'grace'])

arr[People] func rows() {
    arr[People] r = sqlite('SELECT * FROM People ORDER BY id')
    return r
}

int total = 0
int names = 0

for int i = 0, i < 500, i++ {
    // The canary shape: bound straight to a text local.
    text got = rows()[0].name
    if got == 'ada' { names = names + 1 }

    // A scalar column -- copied by value, so nothing is minted and the
    // array is simply released.
    total = total + rows()[1].id

    // Discarded entirely: the chain still has to release the array even
    // when nobody wants the answer.
    text ignored = rows()[1].name

    // Inside an interpolation, inside a comparison, and as a call
    // argument -- three positions where the escaping copy is consumed
    // by something other than a plain binding.
    text line = `${rows()[0].name}/${rows()[1].name}`
    if line == 'ada/grace' { names = names + 1 }
    if rows()[1].name == 'grace' { names = names + 1 }
    names = names + count(rows()[0].name)

    // Still borrowed, still documented as leaking nothing here: the
    // array IS bound to a name, so it reclaims on its own and the row
    // is a plain borrow into it.
    arr[People] bound = sqlite('SELECT * FROM People ORDER BY id')
    People p = bound[0]
    if p.name == 'ada' { names = names + 1 }
}

log(total)
log(names)

int func count(s:text) {
    return s.length > 0 ? 1 : 0
}
