// Maps: four mechanisms, none of which the corpus can see.
//
// Measured, not assumed. With the port broken on purpose four separate
// ways -- the literal's header allocated after its entries instead of
// before, the `bool` missing-sentinel changed, a rendered key never
// freed, and `delete` handed capacity by pointer instead of by value --
// the whole 106-file corpus reported no difference at all, because not
// one file that currently matches uses a map for anything. This file is
// the only thing standing behind any of it.
//
//   1. **A map literal allocates its header FIRST**, and an array
//      literal allocates its last. The two look like the same
//      construct and are built in opposite orders, because a map
//      literal is not built from its entries -- it is an empty header
//      that `festina_map_set` mutates once per entry, in source order.
//      (A repeated key inside one literal would be "last one wins" at
//      the IR level and needs no dedup pass -- but semantic analysis
//      rejects one outright, so that half is unreachable from source
//      and is not tested here.)
//
//   2. **Every map runtime call deals in a raw i64, whatever T is.**
//      `festina_map_get` has no idea what a given map's values are, so
//      the compiler reinterprets in both directions -- bitcast for a
//      float, zext/trunc for a bool, the value itself for an int -- and
//      picks the "key not present" answer at compile time. Those
//      sentinels are per-type and unrelated to each other: INT64_MIN, a
//      NaN bit pattern, and 2 for a bool, which is a value no real
//      `bool` can hold. All three are read below.
//
//   3. **festina_map_set strdups the key**, so a key the compiler
//      RENDERED has no owner once the call returns and must be freed
//      exactly there. claude.md #302 makes that reachable from ordinary
//      code: `m[7]` renders `7` into a fresh buffer, so an expression
//      that reads as borrowed produces an owned pointer. Both spellings
//      are here, in all four key positions -- literal, read, write and
//      delete -- because the free is decided per position.
//
//   4. **`delete` is not a set with a different name.** count and
//      tombstones are out-params, because a delete either removes a
//      live entry or converts it into a tombstone; capacity is passed
//      by VALUE, because a delete never grows the table. A set passes
//      all four by pointer. Getting that one argument wrong is a type
//      error LLVM will not catch, since both are i64-shaped in the
//      call.
//
// Scalar value types only. A `map[text]` releases its values through a
// GENERATED per-type cascade rather than the plain release, which is
// its own mechanism and its own file when it lands.

map[int] counts = {}
map[float] rates = {'base': 1.5}
map[bool] flags = {}
int bumps = 0

int func bump(v:int) {
    bumps = bumps + 1
    return v
}

text func nameFor(i:int) {
    return `k${i}`
}

// Mechanism 1: entries that emit instructions of their own, so "header
// first" is observable rather than invisible.
map[int] built = {'a': bump(1), 'b': bump(2), 'c': bump(3)}

// Mechanism 3, in the literal position: a rendered key next to a plain
// one. `9` is an int, so it is rendered into a buffer the set must free;
// `'lit'` is a pointer into .rodata that nothing may free.
map[int] mixedKeys = {'lit': 1, 9: 2}

int idx = 4
text held = 'held'

// Writes, one per key shape.
counts['plain'] = 10
counts[held] = 20
counts[`t${idx}`] = 30
counts[idx] = 40
counts[nameFor(5)] = 50

// Reads, the same five shapes.
log(counts['plain'])
log(counts[held])
log(counts[`t${idx}`])
log(counts[idx])
log(counts[nameFor(5)])

// Mechanism 2: a missing key in each of the three value types, so all
// three sentinels are emitted. They are printed rather than compared,
// because what is being pinned is the constant in the call.
log(counts['absent'])
log(rates['absent'])
log(flags['absent'])

// And present keys of each type, so the reinterpretation back out of
// the raw i64 is exercised in all three directions.
rates['half'] = 0.5
flags['on'] = true
flags['off'] = false
log(rates['base'])
log(rates['half'])
log(flags['on'])
log(flags['off'])

// Mechanism 4, again once per key shape, since the key free is decided
// per position here too.
delete counts['plain']
delete counts[held]
delete counts[`t${idx}`]
delete counts[idx]
log(counts['plain'])

// A map local: frame storage from a literal written right here, heap
// when it escapes, and the no-initializer case -- claude.md #81's own
// rule, which applies to a map exactly as it does to an array. A
// frame-allocated map still owns its heap entry table, which is why
// its scope exit calls festina_map_free_entries rather than nothing.
map[int] sink = {}

int func locals() {
    map[int] fromLiteral = {'a': 1, 'b': 2}
    map[int] noInit
    map[int] aliased = counts
    map[int] escapes = {'c': 3}
    noInit['d'] = 4
    sink = escapes
    return fromLiteral['a'] + fromLiteral['b'] + noInit['d']
         + aliased['k5'] + escapes['c']
}

log(locals())
log(sink['c'])
log(built['a'])
log(built['c'])
log(bumps)
log(mixedKeys['lit'])
log(mixedKeys[9])
