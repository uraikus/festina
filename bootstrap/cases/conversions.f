// Method calls: the dispatch, and the conversion family behind it.
//
// A call whose callee is a `Member` -- `x.f()` -- wears one shape over
// two unrelated things, and telling them apart is the first thing this
// has to get right:
//
//   * a METHOD on a value, whose receiver is emitted and handed to a
//     runtime function; and
//   * a `Math.*` call, whose "receiver" is a namespace that is never
//     emitted at all.
//
// Measured, not assumed: with the port broken on purpose four separate
// ways -- the `'42'.toInt()` constant fold removed, an owning text
// receiver never freed, the float-to-int guard replaced by a bare
// `fptosi`, and the `Math` namespace made conditional on `Math` being
// unbound -- the whole 107-file corpus reported no difference at all.
// Not one file that currently matches calls a method. This file is the
// entire evidential basis for the slice.
//
// Those breakages are no longer prose: they live in
// `bootstrap/canary.py` and are re-run by
// tests/test_bootstrap_canary.py, which FAILS if this file ever stops
// making them visible. A drift here is a failing test now, not a
// silently unmeasured mechanism.
//
//   1. **`'42'.toInt()` is folded at COMPILE time**, and only for a
//      literal receiver; a dynamic one calls the runtime. The two must
//      answer identically for the same text, which means the fold has
//      to reproduce C's `strtoll` and not a language's idea of what a
//      number looks like: leading whitespace skipped, a sign, digits,
//      trailing garbage ignored, no digits at all giving null, and
//      overflow CLAMPED to i64's ends rather than wrapping. Every one
//      of those is below, each with its dynamic twin.
//
//      Worth stating rather than discovering: a clamped NEGATIVE
//      overflow lands on LLONG_MIN, which is exactly the int null
//      sentinel, so `'-99999999999999999999'.toInt()` and
//      `'nope'.toInt()` are indistinguishable afterwards. That is the
//      language's, not this port's -- it follows from choosing i64's
//      minimum as the null -- and both implementations agree on it,
//      which is all this file claims.
//
//   2. **A receiver the expression itself allocated is freed by the
//      method that reads it.** `make().toInt()` has no owner left once
//      the parse is done; `t.toInt()` on a binding must not be freed,
//      because the binding still holds it. Both spellings are here for
//      each method that consumes a text.
//
//   3. **`fptosi` is undefined for a NaN, an infinity, or anything out
//      of i64's range** -- genuinely undefined, not merely unspecified,
//      which is why `Math.floor()` compiles to a NaN test, two range
//      tests, a saturating intrinsic and a select rather than to one
//      instruction. A language that answers null for division by zero
//      cannot answer a stack address for the floor of that same null.
//
//   4. **The `Math` namespace is chosen per METHOD NAME, not per
//      receiver.** `Math.sqrt()` is the namespace even when a variable
//      called `Math` is in scope; `Math.toText()` is that variable's
//      own method, because `toText` is in no Math table. That is the
//      shipped compiler's behavior rather than a design anyone argued
//      for, and a port that tidied it would disagree with the thing it
//      exists to agree with.

// ---- 1. the fold, against its own runtime twin --------------------

text plain = '42'
text spaced = '  -17abc'
text junk = 'nope'
text signed = '+7'
text huge = '99999999999999999999'
text negHuge = '-99999999999999999999'
text empty = ''

// Folded: the receiver is a literal, so nothing here runs at runtime.
log('42'.toInt())
log('  -17abc'.toInt())
log('nope'.toInt())
log('+7'.toInt())
log('99999999999999999999'.toInt())
log('-99999999999999999999'.toInt())
log(''.toInt())
log('\t\n 8'.toInt())

// The same eight through the runtime. If the fold and the runtime ever
// disagree, these pairs are where it shows.
log(plain.toInt())
log(spaced.toInt())
log(junk.toInt())
log(signed.toInt())
log(huge.toInt())
log(negHuge.toInt())
log(empty.toInt())

// ---- 2. owning versus borrowed receivers --------------------------

text func make() {
    return '  9  '
}

// Owning: freed by the method. Borrowed: not.
log(make().toInt())
log(plain.toInt())
log(make().trim())
log(plain.trim())
log(make().charCodeAt(0))
log(plain.charCodeAt(0))

// ---- the conversions themselves -----------------------------------

int n = 65
float f = 2.5
bool b = false

log(n.toText())
log(f.toText())
log(b.toText())
log(n.toFloat())
log(n.toChar())
log(233.toChar())
log('café'.charCodeAt(3))

// ---- 3 and 4. Math ------------------------------------------------

// One from each table, so a family that stopped being emitted shows up
// rather than hiding behind its neighbours.
log(Math.sqrt(4.0))
log(Math.abs(0.0 - 3.0))
log(Math.tan(0.0))
log(Math.pow(2.0, 3.0))
log(Math.atan2(0.0, 1.0))
log(Math.min(1.0, 2.0))
log(Math.max(1.0, 2.0))

// The rounding four, each of which carries the whole float-to-int
// guard -- and one of them applied to a value that really is out of
// range, so the guard is not merely emitted but reached.
log(Math.floor(2.7))
log(Math.ceil(2.1))
log(Math.round(2.5))
log(Math.trunc(0.0 - 2.9))
log(Math.floor(1.0 / 0.0))

// Mechanism 4: a binding called `Math`. `.sqrt()` ignores it and
// `.toText()` does not.
float Math = 1.5
log(Math.sqrt(9.0))
log(Math.toText())
