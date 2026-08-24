#include <stdio.h>
#include <stdint.h>

/* See loop_sum.f's own comment: a polynomial-hash-style accumulation,
 * not a plain running sum -- a plain sum is exactly the kind of loop an
 * optimizing compiler can fold into a closed-form expression at compile
 * time. Every iteration here genuinely depends on the previous one. */
int main(void) {
    int64_t total = 0;
    for (int64_t i = 0; i < 100000000; i++) {
        total = (total * 1000003 + i) % 1000000007;
    }
    printf("%lld\n", (long long)total);
    return 0;
}
