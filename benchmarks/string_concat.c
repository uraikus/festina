#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* See string_concat.f's own comment: naive repeated concatenation, `s`
 * growing by one character every iteration -- each concatenation
 * allocates a fresh buffer sized to the combined length and copies both
 * operands into it, the textbook O(n^2) pattern, matching what
 * festina_str_concat does under the hood for `${s}x`-style
 * interpolation (no in-place growable buffer to fall back on there
 * either). */
int main(void) {
    char *s = malloc(1);
    s[0] = '\0';
    size_t len = 0;
    for (int i = 0; i < 15000; i++) {
        size_t new_len = len + 1;
        char *next = malloc(new_len + 1);
        memcpy(next, s, len);
        next[len] = 'x';
        next[new_len] = '\0';
        free(s);
        s = next;
        len = new_len;
    }
    puts(s);
    free(s);
    return 0;
}
