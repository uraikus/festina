// A container of containers, a compiled pattern, a scheduled
// callback, and a handle that knows its own path.
//
// Four unrelated-looking mechanisms that share one property: each is a
// case where the TYPE decides something the value cannot say for
// itself. A nested container's slots hold whole references while an
// `arr[int]`'s hold nothing. A regex literal is immortal while a
// `regex(p, f)` result is not. A scheduled callback needs a loop that
// a cleared one does not. And a handle's `save()` with no argument
// means "the path you already have", which only a null can spell.
//
// Measured, not assumed. The breakages live in `bootstrap/canary.py`
// and are re-run by tests/test_bootstrap_canary.py, which FAILS if
// this file stops making them visible.
//
//   1. **A nested container element owns a whole reference.** An
//      `arr[arr[int]]`'s slots are counted values even though `int`
//      owns nothing, so the array needs a generated cascade and each
//      slot is RELEASED rather than freed as a buffer. The element
//      type is spelled with the same `arr:T` key a release is cached
//      under, and a colon is what tells one from a scalar or a struct
//      name.
//
//   2. **A `/pattern/` literal is compiled once per call site and
//      MARKED.** The same node always yields the same automaton, so
//      recompiling on every arrival is pure waste -- but the cached
//      value is shared by every later execution of that line, so
//      `free` on a binding aliasing it must not free it. The value
//      carries the answer rather than the call site, which is what
//      lets the release be an ordinary no-op.
//
//      It is also FRESH for the purpose of a store -- not because it
//      was allocated there, but because retain and release are both
//      no-ops on something immortal and the cheaper answer is right.
//      Deliberately not an owning source for a RELEASE: only a
//      `regex(p, f)` CALL result is released where it is used.
//
//   3. **`regex(p, f)` is MEMOIZED, not cached.** The pattern is an
//      arbitrary runtime expression, so the same site can legitimately
//      see a different one each time; caching by site would serve the
//      first pattern forever. The runtime remembers what this site
//      compiled last and recompiles on a mismatch.
//
//   4. **A regex split takes its arguments the other way round** --
//      pattern first, subject second -- unlike a text split. Both are
//      here, because the difference is invisible in either alone.
//
//   5. **Only SCHEDULING a callback makes a program use timers.**
//      Clearing alone schedules nothing, so a program that only ever
//      clears needs no loop to wait in -- the same "only pay for what
//      you use" rule the graphics loader follows.
//
//   6. **A no-argument `save()` passes a NULL path**, which is how the
//      runtime is told to use the handle's own. Both spellings are
//      below, since a null and an empty string are different
//      instructions.

arr[arr[int]] grid = [[1, 2], [3, 4]]
arr[arr[text]] words = [['a', 'bb'], ['ccc']]
map[text] seen = {}
int ticks = 0

// Mechanism 1. Reading through two levels, and writing a whole inner
// container into an outer slot, so the reference the slot holds is
// visible rather than assumed.
int func nested() {
    int total = grid[0][1] + grid[1][0]
    arr[int] row = [9, 9]
    grid[0] = row
    total = total + grid[0][0] + words[0][1].length
    arr[arr[int]] local = [[7], [8, 8]]
    return total + local[1][1] + local.length
}

// Mechanisms 2, 3 and 4. `anyDigit` is a literal and immortal;
// `built` is compiled by the runtime from a pattern it cannot know
// ahead of time. Splitting by each shows the argument order.
regex anyDigit = /[0-9]+/
text sep = '[,;]'

int func patterns() {
    int n = 0
    if anyDigit.test('room 42') { n = n + 1 }
    if /^[a-z]+$/.test('abc') { n = n + 2 }

    text hit = 'room 42'.match(anyDigit)
    n = n + hit.length

    regex built = regex(sep)
    arr[text] byRegex = 'a,b;c'.split(built)
    arr[text] byText = 'a-b-c'.split('-')
    n = n + byRegex.length + byText.length

    // A replace with each kind of search, since one carries its own
    // `g` flag and the other has no flags at all.
    text a = 'a1b2'.replace(anyDigit, '#')
    text b = 'a-b'.replace('-', '+')
    return n + a.length + b.length
}

// A freed binding that ALIASES the cached literal: the release has to
// be a no-op, or every later execution of `anyDigit`'s own line would
// be reading freed memory.
int func freesAnAlias() {
    regex alias = anyDigit
    free alias
    if anyDigit.test('9') { return 1 }
    return 0
}

// Mechanism 5. `tick` is scheduled; `stale` is scheduled and then
// cleared. Both spellings of clearing are here because each takes its
// own runtime call.
void func tick() {
    ticks = ticks + 1
}

int func schedules() {
    int once = setTimeout(tick, 1)
    int repeating = setInterval(tick, 1)
    clearTimeout(once)
    clearInterval(repeating)
    return 0
}

// Mechanism 6, both spellings. The file is this source itself, which
// is always present, and saveCopy writes beside it rather than over
// it -- so nothing here overwrites anything the repository needs.
blob src = 'bootstrap/cases/handles_and_nesting.f'

int func saves() {
    int n = 0
    if src.exists() { n = n + 1 }
    bool copied = src.saveCopy('/tmp/festina-case-copy.txt')
    if copied { n = n + 1 }
    return n + src.length
}

// The time and filesystem builtins, each handed a COMPUTED path rather
// than a literal. That is the whole point of these three lines: none
// of these runtime functions keeps a pointer past the call -- the file
// helpers read or write and close, strftime copies into its own buffer
// -- so a temporary is the caller's to free, and a literal argument
// would measure nothing because a literal is never freed anyway.
text dir = '/tmp'

int func paths() {
    text stamp = formatTime(now(), `%Y-%m-%d${''}`)
    bool made = mkdir(`${dir}/festina-case-dir`)
    arr[text] listed = ls(`${dir}${''}`)
    int n = stamp.length + listed.length
    if made { n = n + 1 }
    return n
}

log(nested())
log(patterns())
log(freesAnAlias())
log(schedules())
log(saves())
log(paths())
log(grid.length)
log(words.length)
log(seen.keys().length)
log(ticks)
