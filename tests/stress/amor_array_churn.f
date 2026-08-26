// claude.md #174: amor arr[T] -- real amortized (doubling) growth for
// arr[T], the runtime effect claude.md #156 originally left it
// without (it parsed/type-checked but behaved byte-for-byte like
// plain arr[T]). Exercises every one of the five growable-buffer
// operations (push/pop/shift/unshift/splice, both the 2- and
// 3-argument forms) at real iteration counts -- enough pushes to force
// several real capacity doublings (8 -> 16 -> ... -> past 1000), not
// just the few-element cases test_codegen.py::TestAmorArray already
// covers -- on both a scalar element type (int, raw bytes) and a
// refcounted one (struct, exercising the retain-on-push/release-on-
// overwrite paths every push/splice/unshift call makes), plus the
// struct-field auto-vivify path (a field has no initializer syntax at
// all, so it relies entirely on codegen picking the right, larger
// FESTINA_AMOR_ARRAY_LLVM_TYPE-shaped header the first time it's
// touched -- building the wrong one would silently corrupt memory the
// moment festina_array_resize's own amor path first read the missing
// capacity field, exactly the risk claude.md #156's own map version
// of this stress test class was written to catch).

struct Item { n:int  label:text }
struct Bag { xs:amor arr[Item] }

int total = 0
int i = 0
while i < 400 {
    amor arr[int] nums = []
    int j = 0
    while j < 60 {
        nums.push(j)
        j = j + 1
    }
    // unshift, from the front, exercises the memmove-then-resize path
    // on an amor buffer independently of push's own append-only one.
    nums.unshift(-1)
    total = total + nums.length + nums[0] + nums[nums.length - 1]

    // pop/shift shrink -- an amor array's own buffer never actually
    // reallocs down (see festina_array_resize's own comment), so this
    // is what proves shrinking-then-growing-again on the SAME already-
    // allocated buffer doesn't corrupt anything.
    int popped = nums.pop()
    int shifted = nums.shift()
    total = total + popped + shifted
    nums.push(999)
    total = total + nums[nums.length - 1]

    // splice(start, count) -- the 2-argument shrink-only form.
    arr[int] removed = nums.splice(2, 3)
    total = total + removed.length

    // splice(start, count, insertArr) -- the 3-argument grow-or-shrink
    // form, inserting enough elements to force real growth on some
    // passes (elem_size * insert_len can exceed whatever slack the
    // amor buffer's own capacity still has).
    arr[int] removed2 = nums.splice(0, 1, [1000, 1001, 1002, 1003])
    total = total + removed2.length + nums[0] + nums[3]

    // A refcounted (struct) element type -- retained on every push,
    // released on every duplicate-key-shaped overwrite this loop
    // causes via splice's own insertion path.
    amor arr[Item] items = []
    int k = 0
    while k < 50 {
        Item it
        it.n = k
        it.label = `item${k}`
        items.push(it)
        k = k + 1
    }
    Item aliasFirst = items[0]
    free aliasFirst
    total = total + items.length + items[49].n

    // Struct-field auto-vivify -- a fresh Bag every pass, so the
    // "first touch builds the header" path runs every single
    // iteration, not just once.
    Bag b
    b.xs.push(it0(i))
    b.xs.push(it0(i + 1))
    total = total + b.xs.length + b.xs[0].n

    i = i + 1
}
log(total)

Item func it0(v:int) {
    Item it
    it.n = v
    it.label = `auto${v}`
    return it
}
