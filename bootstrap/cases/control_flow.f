// decisions.md #292: control flow and local storage, for the two
// pieces of bootstrap/codegen.f that the repository corpus tests with
// nothing at all.
//
// Both were confirmed by breaking them on purpose:
//
//  * **Alloca hoisting** (claude.md #191). Disabling it left all five
//    matching corpus files still matching -- `fib.f`'s only slot is a
//    parameter, which is already at the top of its entry block, and no
//    other matching file declares a local anywhere. A local declared
//    inside a loop body is what makes the pass observable, and a loop
//    that allocated a fresh slot per iteration is the exact bug #191
//    exists to prevent.
//
//  * **Terminator tracking.** An `if` arm ending in `return` must NOT
//    get the usual branch to `if.end`, because LLVM allows one
//    terminator per block. `fib.f` does cover this one; nothing covers
//    it alongside hoisting, so a single file that does both keeps them
//    from drifting apart.

int total = 0

// A function with a local, and an `if` arm that returns: the arm gets
// no fall-through branch, while the `else` block still appears empty
// but present.
int func classify(n:int) {
    if n < 0 {
        return 0
    }
    int doubled = n * 2
    return doubled
}

// A local declared inside a loop body -- one slot hoisted to entry and
// reused, not one per iteration.
int func accumulate(limit:int) {
    int acc = 0
    for int i = 0, i < limit, i++ {
        int step = i + 1
        acc = acc + step
    }
    return acc
}

// An `if` with no `else` at all, inside a `while`, with its own local.
void func report(n:int) {
    int seen = 0
    while seen < n {
        int shown = seen * 3
        if shown > 5 {
            log(shown)
        }
        seen++
    }
}

log(classify(4))
log(classify(0 - 1))
log(accumulate(5))
report(4)
total = accumulate(3)
log(total)

// A bare loop at the top level, so __festina_main gets a hoisted slot
// of its own rather than only the functions having them.
for int k = 0, k < 2, k++ {
    int inner = k + 100
    log(inner)
}

float ratio = 1.5
while ratio < 4.0 {
    ratio = ratio * 2.0
}
log(ratio)

bool done = true
if done == true {
    log(done)
} else {
    log(false)
}

// decisions.md #323: an `if` whose arms BOTH end in a terminator emits
// no end block at all -- the label is still allocated, so the numbering
// does not shift, it simply names nothing, and the `if` terminates
// whatever contains it. The comment at the top of this file covers ONE
// arm ending in `return`; nothing in the corpus covered both, and the
// port emitted an unreachable `if.endN: br label %if.endM` for every
// occurrence until a chained `else if` whose every arm returned was
// finally written down.
int func pick(k:int) {
    if k == 1 {
        return 1
    } else {
        return 2
    }
}

// The chained form, nested inside a loop, which is where the extra
// block was first seen: the inner `if` terminates, so the OUTER one's
// own end block has to be reached from the first arm alone.
int func chain(k:int) {
    int i = 0
    while i < 3 {
        if k == 0 {
        } else if k == 1 {
            if i == 0 {
                return 10
            } else {
                return 11
            }
        } else if k == 2 {
            return 12
        } else {
            return 13
        }
        i = i + 1
    }
    return 14
}

log(pick(1))
log(pick(2))
log(chain(0))
log(chain(1))
log(chain(2))
log(chain(3))
