// Allocation-heavy workload: a fresh, fixed-size arr[int] literal built
// and discarded every outer-loop iteration (never escaping, so Festina
// reclaims each one at that iteration's own scope-exit -- see claude.md
// #74/#76), summing its own elements into a running total. Each
// element's own value depends on `total` from the *previous* outer
// iteration (not a closed-form function of `i` alone), the same
// technique loop_sum.f already uses to keep an optimizing compiler from
// folding the whole loop into a constant -- verified necessary here too,
// not just carried over by habit: an earlier version seeding elements
// from `i` alone (`[i, i+1, ..., i+7]`, summing to a closed-form
// `8*i+28`) is exactly the kind of arithmetic a real optimizer can
// collapse away, which would benchmark the optimizer's algebra instead
// of the actual allocate-fill-read-free cost this benchmark exists to
// measure.
int total = 0
for int i = 0, i < 2000000, i++ {
    arr[int] nums = [total % 97, (total + i) % 97, (total + i * 2) % 97, (total + i * 3) % 97, (total + i * 4) % 97, (total + i * 5) % 97, (total + i * 6) % 97, (total + i * 7) % 97]
    for int j = 0, j < nums.length, j++ {
        total = (total * 1000003 + nums[j]) % 1000000007
    }
}
log(total)
