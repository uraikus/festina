// claude.md #79/#80/#81/#96/#97/#102: arrays and maps, grown, drained,
// nested, aliased and reclaimed thousands of times.

struct Item { id:int name:text tags:arr[text] }

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
    i = i + 1
}
log(total)
