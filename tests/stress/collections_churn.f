// claude.md #79/#80/#81/#96/#97/#102/#132: arrays and maps, grown,
// drained, nested, aliased and reclaimed thousands of times.

struct Item { id:int name:text tags:arr[text] }

// claude.md #132: mkdir() is idempotent -- calling it thousands of
// times only ever creates the directory once, everything after answers
// false rather than failing.
mkdir('./stress_dir')

arr[int] func squares(n:int) {
    arr[int] out = []
    int i = 0
    while i < n { out.push(i * i) i = i + 1 }
    return out
}

int total = 0
int i = 0
while i < 1500 {
    // Grow and drain, exercising every resize path.
    arr[int] xs = []
    int j = 0
    while j < 12 { xs.push(j) j = j + 1 }
    xs.unshift(99)
    arr[int] removed = xs.splice(2, 3)
    total = total + removed.length + xs.pop() + xs.shift()
    total = total + xs.indexOf(7)

    // claude.md #132: mkdir()/ls() -- a bounded working set (20 names
    // cycled by index) so ls()'s own allocation loop runs thousands of
    // times without the directory itself growing without bound.
    bool alreadyThere = mkdir('./stress_dir')
    total = total + (alreadyThere ? 1 : 0)
    blob marker = `./stress_dir/f${i % 20}.txt`
    marker.write(`${i}`)
    arr[text] listed = ls('./stress_dir')
    total = total + listed.length

    // A returned array: ownership transfers out of the function.
    arr[int] sq = squares(8)
    total = total + sq[7]

    // Nested arrays -- the outer array owns each inner one.
    arr[arr[int]] grid = []
    int k = 0
    while k < 4 { grid.push(squares(3)) k = k + 1 }
    total = total + grid[3][2]

    // Maps: fresh keys, overwritten keys, and struct/array values.
    map[int] counts = {}
    map[text] names = {}
    k = 0
    while k < 10 {
        counts[`c${k}`] = k
        counts[`c${k}`] = k * 2
        names[`n${k % 3}`] = `value ${k}`
        k = k + 1
    }
    total = total + counts['c9']

    // Structs holding managed fields, including an auto-vivified one.
    Item it
    it.id = i
    it.name = `item ${i}`
    it.tags.push('a')
    it.tags.push(`t${i}`)
    arr[Item] items = []
    items.push(it)
    total = total + items[0].tags.length

    // Aliasing: two names, one underlying value.
    arr[int] alias = xs
    alias.push(1)
    total = total + xs.length

    // claude.md #111: delete removes entries (releasing their values),
    // and free releases a whole binding by hand -- a decrement, so the
    // alias above keeps the array alive until its own release. Freeing
    // and deleting per iteration makes any imbalance one leak or one
    // double-free per pass.
    delete names[`n${i % 3}`]
    delete counts.c4
    delete counts.nothing
    free counts
    free names
    free alias
    if xs.length == 0 { log('unreachable') }
    delete it.name
    if it.name != null { log('unreachable') }
    free it
    free items
    i = i + 1
}
log(total)
