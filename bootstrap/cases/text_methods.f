// decisions.md #328: the eight `text` methods -- `slice`, `indexOf`,
// `startsWith`, `endsWith`, `toLowerCase`, `toUpperCase`, `repeat` and
// `toFloat`.
//
// Written alongside the methods, because otherwise nothing in the
// corpus would use them and the differential harness could not see any
// of them however carefully both implementations were written.
//
// What this file is shaped to make visible:
//
//   - every index counting CODE POINTS. This is the property the whole
//     set turns on: `slice` must never split a character, and
//     `indexOf`'s answer must be an index `slice` can take. A
//     byte-indexed implementation gives the same answers for ASCII and
//     different ones the moment a non-ASCII character appears before
//     the index, which is why every check below has a multi-byte
//     version beside it.
//   - clamping at both ends of `slice`, and an `end` below `start`.
//   - `indexOf`'s optional second argument, which is the one place in
//     the set where a call site emits a default rather than a value.
//   - case conversion being ASCII-only: `é` and `ö` have to survive
//     unchanged, which a byte-wise `c - 32` would destroy.
//   - `toFloat` accepting exactly what §16.3 says and no more --
//     `inf`, `nan` and hex floats are all `strtod` input and none of
//     them is an answer this method gives.
//   - the receiver being freed AFTER the arguments are emitted, which
//     `built.indexOf(base.slice(1, 2))` below is shaped to exercise:
//     the argument is itself a fresh text allocated from another
//     receiver, so a call site that freed in the wrong order would be
//     reading released memory.

text base = 'Hello, World'
text uni = 'héllo wörld'

// slice, clamped both ways and inverted.
log(base.slice(0, 5))
log(base.slice(7, 99))
log(base.slice(0 - 4, 2))
log(base.slice(5, 2) == '')

// indexOf, with and without a starting point, hit and miss.
log(base.indexOf('World'))
log(base.indexOf('o'))
log(base.indexOf('o', 5))
log(base.indexOf('zzz'))
log(base.indexOf(''))

log(base.startsWith('Hello'))
log(base.startsWith('hello'))
log(base.endsWith('World'))
log(base.endsWith('World!'))

log(base.toLowerCase())
log(base.toUpperCase())

log('ab'.repeat(3))
log('ab'.repeat(0) == '')
log('ab'.repeat(0 - 5) == '')

// toFloat, exactly to its clause.
log('12'.toFloat())
log('  -2.5xyz'.toFloat())
log('.5'.toFloat())
log('1e3'.toFloat())
log('1e'.toFloat())
log('inf'.toFloat())
log('nan'.toFloat())
log('0x10'.toFloat())
log('abc'.toFloat())

// The same questions over multi-byte input, where a byte-indexed
// implementation answers differently.
log(uni.length)
log(uni.slice(0, 5))
log(uni.slice(1, 2))
log(uni.indexOf('wörld'))
log(uni.slice(uni.indexOf('wörld'), uni.length))
log(uni.toUpperCase())
log(uni.toLowerCase())

// Composition, and the argument-before-receiver free ordering.
text built = base.slice(0, 5) + uni.slice(0, 2)
log(built)
log(built.indexOf(base.slice(1, 2)))
// Both halves fresh, so both frees are really emitted and their
// placement relative to the call is observable rather than implied.
log(base.slice(0, 5).indexOf(base.slice(1, 2)))
log(uni.slice(0, 5).toUpperCase())
log(base.toLowerCase().endsWith('world'))
log('-'.repeat(base.slice(0, 3).length))
