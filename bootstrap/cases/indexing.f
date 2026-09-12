// `.length` and `[i]` -- the two commonest reads in any real program,
// and three different mechanisms behind two spellings.
//
//   text.length   a runtime call: a UTF-8 code-point walk, because a
//                 `text` carries no length of its own
//   arr.length    a field of the header the array already has
//   xs[i]         the header's data pointer, then an element GEP
//
// Two orderings here are observable rather than cosmetic, and both are
// easy to get backwards:
//
//   1. **The index is emitted before the data pointer.** So an index
//      expression with side effects of its own runs first. `sideEffect`
//      below is what makes that visible: get the order wrong and the
//      counter it bumps is read at a different time.
//   2. **On a write, the whole SLOT is computed before the value.**
//      The index belongs to the target, and a target's side effects
//      precede the value's.
//
// A receiver the expression itself allocated (`build().length`) has no
// owner left once the length is taken, so it is freed there -- the one
// place `.length` is not a pure read.

// Festina does not bounds-check an index, so every array here is
// created at the length it is about to be written through. An empty
// one would not fail loudly; it would write past a zero-length buffer
// and the file would measure nothing but a segfault.

text name = 'hello'
arr[int] nums = [0, 0, 0, 0]
arr[float] rates = [0.0, 0.0]
int bumped = 0

int func sideEffect(v:int) {
    bumped = bumped + 1
    return v
}

text func build() {
    return 'abcd'
}

// Both lengths, on a global of each type.
log(name.length)
log(nums.length)

// An owning receiver: the call's result is freed after its length.
log(build().length)

// Writes, with a constant index, a variable index and a computed one.
int i = 1
nums[0] = 10
nums[i] = 20
nums[i + 1] = 30
rates[i] = 1.5

// Reads of each of those.
log(nums[0])
log(nums[i])
log(nums[i + 1])
log(rates[i])

// The index runs before the data pointer is loaded, which only shows
// when the index has an effect of its own.
log(nums[sideEffect(0)])
log(bumped)

// A write whose value is itself computed: the slot first, then the
// value, then the store.
int j = 2
nums[j] = j + 5
log(nums[j])

// Indexing inside a loop, where the object and index are reloaded every
// iteration rather than hoisted.
int total = 0
for int k = 0, k < 3, k++ {
    total = total + nums[k]
}
log(total)

// `.length` as part of a larger expression, so it is a value rather
// than a statement of its own.
if name.length > 3 && nums.length >= 0 {
    log(name.length + nums.length)
}
