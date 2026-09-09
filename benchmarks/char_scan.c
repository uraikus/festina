#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* See char_scan.f's own comment: a lexer-shaped workload -- walk a
 * source-sized buffer character by character, counting identifier
 * runs. Built by doubling (15 allocations for ~1.7MB) rather than by
 * appending in a loop, so the five scan passes dominate rather than
 * the build. */
int main(void) {
    const char *unit = "int func compute(a:int, b:int) { return a + b * 2 } ";
    size_t len = strlen(unit);
    char *src = malloc(len + 1);
    memcpy(src, unit, len + 1);
    for (int d = 0; d < 15; d++) {
        char *next = malloc(len * 2 + 1);
        memcpy(next, src, len);
        memcpy(next + len, src, len);
        next[len * 2] = '\0';
        free(src);
        src = next;
        len *= 2;
    }
    long total = 0;
    for (int pass = 0; pass < 5; pass++) {
        long tokens = 0;
        int in_word = 0;
        for (size_t i = 0; i < len; i++) {
            unsigned char c = (unsigned char)src[i];
            int is_alpha = (c >= 97 && c <= 122) || (c >= 65 && c <= 90);
            if (is_alpha) {
                if (!in_word) tokens++;
                in_word = 1;
            } else {
                in_word = 0;
            }
        }
        total += tokens;
    }
    printf("%ld\n", total);
    free(src);
    return 0;
}
