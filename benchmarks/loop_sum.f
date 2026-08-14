// A polynomial-hash-style accumulation, not a plain running sum -- a
// plain `total = total + i` loop is a well-known pattern optimizing
// compilers (LLVM in particular) can collapse into a closed-form
// expression at compile time, which would benchmark the optimizer's
// algebra rather than the generated loop's actual runtime cost (verified:
// a plain sum loop up to 200,000,000 here ran in ~2ms, meaning it had
// already been folded away entirely). Each iteration genuinely depends
// on the previous one (modular multiply-add), so there's no closed form
// to fold it into -- every language here has to actually run the loop.
int total = 0
for int i = 0, i < 100000000, i++ {
    total = (total * 1000003 + i) % 1000000007
}
log(total)
