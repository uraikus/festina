// String-heavy workload: repeated concatenation via a template
// literal, `s` growing by one character every iteration. This used to
// be the textbook O(n^2) naive-concatenation pattern: each
// concatenation allocated a fresh buffer sized to the *combined*
// length and copied both operands into it (`festina_str_concat`),
// ~112MB moved for 15,000 appends. Since claude.md #243 the compiler
// recognises `s = `${s}...`` (and `s = s + ...`) as an append onto s's
// own exclusively-owned buffer and grows it in place with a tracked
// length -- amortized O(1) per append, O(n) for the loop -- so this
// now measures that path: the allocator's realloc growth and the
// per-iteration template machinery, not a quadratic copy. Kept as the
// "string-heavy workload" benchmark.md's own "Reading these numbers"
// section calls out, with the same 15,000 iterations, so the before/
// after numbers stay comparable.
text s = ''
for int i = 0, i < 15000, i++ {
    s = `${s}x`
}
log(s)
