#include <stdio.h>
#include <stdint.h>

/* See array_sum.f's own comment for why each element depends on `total`
 * from the previous outer iteration rather than being a closed-form
 * function of `i` alone -- same anti-optimizer-folding technique
 * loop_sum.f already uses. A plain stack array is the natural C
 * equivalent of Festina's own claude.md #81 stack-allocated array
 * (verified not to escape its own iteration, same as this one). */
int main(void) {
    int64_t total = 0;
    for (int64_t i = 0; i < 2000000; i++) {
        int64_t nums[8] = {
            total % 97,
            (total + i) % 97,
            (total + i * 2) % 97,
            (total + i * 3) % 97,
            (total + i * 4) % 97,
            (total + i * 5) % 97,
            (total + i * 6) % 97,
            (total + i * 7) % 97,
        };
        for (int j = 0; j < 8; j++) {
            total = (total * 1000003 + nums[j]) % 1000000007;
        }
    }
    printf("%lld\n", (long long)total);
    return 0;
}
