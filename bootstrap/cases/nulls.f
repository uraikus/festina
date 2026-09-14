// `null` is the one expression with no type of its own, and every
// Festina type spells its null differently.
//
// There is no single bit pattern here to emit. An `int` null is i64's
// minimum, a `float` null is a NaN, a `bool` null is 2 -- a value no
// real bool can hold -- and everything else is the LLVM null pointer.
// So `null` can only be emitted somewhere a type is already known, and
// the whole mechanism is about finding that type.
//
// Measured, not assumed: with the port broken on purpose, the corpus
// saw nothing. Those breakages live in `bootstrap/canary.py` and are
// re-run by tests/test_bootstrap_canary.py, which FAILS if this file
// stops making them visible.
//
//   1. **The type comes from the position**, and there are a lot of
//      positions: a declaration, an assignment, an array element, a
//      map value, an element write, a push, and a call argument, whose
//      type comes from the callee's SIGNATURE rather than from
//      anything at the call site. Each is below, for each of the four
//      spellings that differ.
//
//   2. **A comparison takes its type from the OTHER side, which
//      reverses the evaluation order.** `x == null` emits x first and
//      then resolves the null against x's type. `null == x` has to do
//      the same thing -- which means emitting the RIGHT operand first.
//      Invisible unless the non-null side has effects of its own,
//      which is what `bump()` is for below.
//
//      `null == null` has no context on either side and is left
//      unresolved rather than guessed at (claude.md #54's ambiguity
//      rule), so it is not written here: it would not compile.
//
//   3. **An argument's null takes the PARAMETER's type.** Nothing at
//      the call site says what `takes(null, null)` means; the answer
//      is in the declaration, which is why the port has to carry a
//      parameter-type table and not just a return-type one.

int bumps = 0

int func bump(v:int) {
    bumps = bumps + 1
    return v
}

text func named() {
    return 'n'
}

int func takesScalars(a:int, b:float, c:bool, d:text) {
    return a
}

// Mechanism 1, in a declaration: all four spellings.
int declInt = null
float declFloat = null
bool declBool = null
text declText = null

log(declInt)
log(declFloat)
log(declBool)
log(declText)

// The same four through an assignment, which is a different path --
// a fresh binding has nothing to reclaim and an assignment does.
int asInt = 1
float asFloat = 1.5
bool asBool = true
text asText = 'x'
asInt = null
asFloat = null
asBool = null
asText = null
log(asInt)
log(asFloat)
log(asBool)
log(asText)

// Container positions: a literal element, a map value, an element
// write, a map write, and a push.
arr[int] xs = [1, null, 3]
arr[text] ts = ['a', null]
map[int] m = {'a': null}
map[float] mf = {'b': null}
xs[0] = null
ts[0] = null
m['c'] = null
xs.push(null)
ts.push(null)
log(xs.length)
log(ts.length)
log(m['a'])
log(mf['b'])

// Mechanism 3: the parameter's type, not the call site's.
log(takesScalars(null, null, null, null))

// Mechanism 2: the order flip. `bump()` runs exactly once per
// comparison, and `bumps` at the end says in which order.
if bump(1) == null {
    log(100)
}
if null == bump(2) {
    log(101)
}
if bump(3) != null {
    log(102)
}
if null != named() {
    log(103)
}
log(bumps)

// A null test against a binding of each type, which is the shape real
// code actually writes.
//
// The float line is deliberately here and deliberately does NOT fire.
// A float null is a NaN, and IEEE-754 says a NaN compares unequal to
// everything including itself, so `f == null` is false even when `f`
// IS the null -- while `log(f)` happily prints `nan`. That is a
// property of the language's choice of representation rather than
// anything this port does, and both implementations agree on it, which
// is the only claim this file makes. It is written down here because a
// reader who deleted the line as "dead" would be removing the one
// place the asymmetry is visible.
if declInt == null { log(200) }
if declFloat == null { log(201) }
if declBool == null { log(202) }
if declText == null { log(203) }
if xs[1] == null { log(204) }
