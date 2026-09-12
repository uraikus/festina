// Template literals, `text` concatenation and equality, the in-place
// append of claude.md #243, and interned string constants.
//
// Four properties here are invisible in any program that does the
// obvious thing once, and each has its own deliberate shape:
//
//   1. **A repeated literal is ONE global.** codegen.py keys its
//      string-constant table on the literal's own text, so `'x'` used
//      three times is `@.str.N` used three times. A port that merely
//      counted would agree on every program where no literal repeats
//      and renumber everything from the first repeat onward. `'-'`
//      below appears four times for exactly this reason.
//
//   2. **An empty literal piece emits no concat.** `` `${a}` `` has an
//      empty part on both sides and `` `${a}${b}` `` one between them;
//      concatenating with "" allocates and copies for nothing. Both
//      shapes are here, and both are the common case rather than a
//      curiosity.
//
//   3. **A bare `` `${x}` `` takes a festina_text_own copy.** It
//      concatenates nothing, so without the copy it would hand back
//      `x`'s own pointer and freeing either would dangle the other.
//      That is the single case where the copy appears.
//
//   4. **`s = `${s}...`` and `s = s + ...` grow s in place.** Not a
//      concat: a festina_text_append onto s's own buffer, with a
//      remembered length trusted only while the binding still holds
//      that exact pointer. The near-miss shapes are here too --
//      `` `x${s}` `` does NOT append (the target is not first), and
//      `s = t + 'x'` does not either -- because the optimization
//      applying where it should not is as wrong as it not applying.

text s = ''
text w = 'world'
text other = 'q'
int n = 7
float f = 1.5
bool b = true

// Ordinary templates: a literal on both sides, one side, neither side,
// and two interpolations running together.
log(`hello ${w}!`)
log(`${w} trails`)
log(`leads ${w}`)
log(`${w}`)
log(`${w}${other}`)
log(`n=${n} f=${f} b=${b}`)

// The same literal four times over: one constant, four uses.
log(`${w}-${other}`)
log(`${n}-${n}`)
log(`-${w}`)
log(`${other}-`)

// A template bound to a name, and one interpolating that binding --
// the second reads a buffer the first allocated, so the ownership
// split between "mine to free" and "someone else's" is visible.
text j = `a${w}b`
log(j)
text k = `${j} then ${w}`
log(k)

// `+` on text: one concat per operator, every intermediate freed the
// moment the next has copied out of it.
text two = w + other
log(two)
text three = w + '/' + other
log(three)

// Equality, which consumes both operands and answers a bool -- nothing
// downstream can reach either buffer afterwards.
if w == 'world' { log('yes') }
if w != other { log('differ') }

// In-place append, every recognized shape.
s = s + 'x'
s = s + 'y' + 'z'
s = `${s}tail`
s = `${s}${n}`
s = `${s}a${n}b${b}`
s = s + other
log(s)

// The near misses: neither of these appends, and getting that wrong
// would corrupt a buffer rather than merely renumber a temp.
text lead = 'core'
lead = `pre${lead}`
log(lead)
text sub = 'core'
sub = w + 'x'
log(sub)

// An append inside a loop, which is what the optimization exists for:
// the remembered length is trusted on every iteration after the first,
// and the pointer check is what makes that safe.
text grown = ''
for int i = 0, i < 4, i++ {
    grown = `${grown}${i}`
}
log(grown)

// A template and an append inside a function, where the counters are
// at different values and the slots are allocas rather than globals.
text func label(v:int) {
    text out = 'v='
    out = out + '['
    out = `${out}${v}`
    out = out + ']'
    return out
}
log(label(n))
