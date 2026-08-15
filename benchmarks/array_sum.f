// Allocation-heavy workload: a fresh, fixed-size arr[int] literal built
// and discarded every outer-loop iteration (never escaping, so Festina
// reclaims each one at that iteration's own scope-exit -- see claude.md
// #74/#76/#81), summing its own elements into a running total. Each
// element's own value depends on `total` from the *previous* outer
// iteration (not a closed-form function of `i` alone), the same
// technique loop_sum.f already uses to keep an optimizing compiler from
// folding the whole loop into a constant -- verified necessary here too,
// not just carried over by habit: an earlier version seeding elements
// from `i` alone (`[i, i+1, ..., i+7]`, summing to a closed-form
// `8*i+28`) is exactly the kind of arithmetic a real optimizer can
// collapse away, which would benchmark the optimizer's algebra instead
// of the actual allocate-fill-read cost this benchmark exists to
// measure.
//
// The hot loop lives inside a function, not directly at top level, so
// escape analysis (claude.md #74) -- which only ever analyzes a
// function/handler's own body, never __festina_main's own top-level
// statement sequence -- can actually see that `nums` never escapes its
// own iteration and give it claude.md #81's stack-allocated header,
// same as fib.f already uses a function for its own (necessarily
// recursive) hot path rather than bare top-level code.
int total = 0

void func run(iterations:int) {
    for int i = 0, i < iterations, i++ {
        arr[int] nums = [total % 97, (total + i) % 97, (total + i * 2) % 97, (total + i * 3) % 97, (total + i * 4) % 97, (total + i * 5) % 97, (total + i * 6) % 97, (total + i * 7) % 97]
        for int j = 0, j < nums.length, j++ {
            total = (total * 1000003 + nums[j]) % 1000000007
        }
    }
}

run(2000000)
log(total)
