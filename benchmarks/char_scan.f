// Lexer-shaped workload: walk a source-sized buffer character by
// character, counting identifier runs. This is the operation a scanner
// performs more than any other, and the one claude.md #256 added the
// `ascii` type for: one byte per character means the character count
// IS the byte count, so `.length` and `s[i]`/`charCodeAt(i)` are O(1)
// reads off the value's own header instead of the UTF-8 walks `text`
// needs. The same loop over a `text` is quadratic -- a walk from byte
// zero on every index -- which is why this benchmark uses `ascii`:
// it is the right tool for the job, not a thumb on the scale.
//
// The buffer is built by DOUBLING rather than by appending in a loop.
// `ascii` has no in-place append optimisation (claude.md #243 covers
// `text` only), so `src = src + unit` repeated would be O(n^2) and
// would measure allocation rather than scanning. Doubling is 15
// allocations for ~1.7MB. Five scan passes then dominate the build.
ascii unit = 'int func compute(a:int, b:int) { return a + b * 2 } '
ascii src = unit
for int d = 0, d < 15, d++ {
    src = src + src
}

int total = 0
for int pass = 0, pass < 5, pass++ {
    int n = src.length
    int tokens = 0
    int inWord = 0
    for int i = 0, i < n, i++ {
        int c = src.charCodeAt(i)
        int isAlpha = 0
        if c >= 97 && c <= 122 { isAlpha = 1 }
        if c >= 65 && c <= 90 { isAlpha = 1 }
        if isAlpha == 1 {
            if inWord == 0 { tokens = tokens + 1 }
            inWord = 1
        } else {
            inWord = 0
        }
    }
    total = total + tokens
}
log(total)
