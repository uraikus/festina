// String-heavy workload: naive repeated concatenation via a template
// literal, `s` growing by one character every iteration. Each
// concatenation allocates a fresh buffer sized to the *combined*
// length and copies both operands into it (`festina_str_concat`) --
// there is no in-place growable string buffer to fall back on, so this
// is the textbook O(n^2) naive-concatenation pattern, deliberately: it
// exercises exactly the "string-heavy workload" gap benchmark.md's own
// "Reading these numbers" section calls out as untested, and the O(n)
// per-call allocation is real allocator work (unlike loop_sum's own
// pure arithmetic), not something an optimizer could fold into a
// closed form.
text s = ''
for int i = 0, i < 15000, i++ {
    s = `${s}x`
}
log(s)
