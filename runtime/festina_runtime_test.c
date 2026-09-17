/* The built-in test suite -- claude.md #341.
 *
 * specification.md 11.7: `test NAME = 'description'` declares a named
 * group of assertions, and calling the binding asserts. Its own
 * translation unit, linked only into a `festina test` build, which is
 * what makes specification.md 11.7.3's promise literal -- an ordinary
 * compile carries neither this code nor the report, and does not
 * acquire the -pthread dependency the lock below needs.
 *
 * That separation is also why the counters here are plain globals
 * rather than `__thread` the way claude.md #340's deferred-root buffer
 * is: a report is about the PROGRAM, and an assertion inside a
 * thread's handler belongs in the same group as one in main. So this
 * is the one piece of mutable global state in the runtime that two
 * threads can reach, and it takes a lock -- which costs nothing that
 * matters, because an assertion is not a hot path and a test build is
 * not a shipped one.
 */
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "festina_runtime.h"

typedef struct {
    char *source;   /* the assertion as rendered from its own syntax tree */
    char *actual;   /* the first argument's value, already text */
} FestinaTestFailure;

typedef struct {
    char *description;
    int64_t passed;
    int64_t failed;
    FestinaTestFailure *failures;
    int64_t failure_len;
    int64_t failure_cap;
} FestinaTestGroup;

static FestinaTestGroup *g_festina_test_groups = NULL;
static int64_t g_festina_test_group_len = 0;
static int64_t g_festina_test_group_cap = 0;
static pthread_mutex_t g_festina_test_lock = PTHREAD_MUTEX_INITIALIZER;
/* Read by main AFTER the report has printed and freed everything, so it
 * is kept separately rather than recomputed from the groups. */
static int64_t g_festina_test_fail_total = 0;

/* Registers one group and answers its id, which is what the compiled
 * program holds in the `test` binding. An id rather than a pointer
 * because the group array grows. */
int64_t festina_test_group(const char *description) {
    pthread_mutex_lock(&g_festina_test_lock);
    if (g_festina_test_group_len == g_festina_test_group_cap) {
        int64_t cap = g_festina_test_group_cap ? g_festina_test_group_cap * 2 : 8;
        FestinaTestGroup *grown = (FestinaTestGroup *)realloc(
            g_festina_test_groups, (size_t)cap * sizeof(FestinaTestGroup));
        if (!grown) {
            pthread_mutex_unlock(&g_festina_test_lock);
            return -1;
        }
        g_festina_test_groups = grown;
        g_festina_test_group_cap = cap;
    }
    FestinaTestGroup *g = &g_festina_test_groups[g_festina_test_group_len];
    g->description = description ? strdup(description) : strdup("");
    g->passed = 0;
    g->failed = 0;
    g->failures = NULL;
    g->failure_len = 0;
    g->failure_cap = 0;
    int64_t id = g_festina_test_group_len++;
    pthread_mutex_unlock(&g_festina_test_lock);
    return id;
}

/* Records one assertion. Both strings are COPIED rather than taken
 * over: `source` is a constant in the program's own .rodata, and
 * `actual` may be anything from a fresh render to a literal the
 * program still owns -- the first version took ownership of `actual`
 * and a `text` argument, whose rendering is the value itself, meant
 * freeing a .rodata literal. That aborted, immediately and loudly,
 * which is the good version of that mistake. */
void festina_test_assert(int64_t group, int8_t passed,
                         const char *source, const char *actual) {
    pthread_mutex_lock(&g_festina_test_lock);
    if (group < 0 || group >= g_festina_test_group_len) {
        pthread_mutex_unlock(&g_festina_test_lock);
        return;
    }
    FestinaTestGroup *g = &g_festina_test_groups[group];
    if (passed) {
        g->passed++;
        pthread_mutex_unlock(&g_festina_test_lock);
        return;
    }
    g->failed++;
    if (g->failure_len == g->failure_cap) {
        int64_t cap = g->failure_cap ? g->failure_cap * 2 : 4;
        FestinaTestFailure *grown = (FestinaTestFailure *)realloc(
            g->failures, (size_t)cap * sizeof(FestinaTestFailure));
        if (!grown) {
            pthread_mutex_unlock(&g_festina_test_lock);
            return;
        }
        g->failures = grown;
        g->failure_cap = cap;
    }
    g->failures[g->failure_len].source = strdup(source ? source : "");
    g->failures[g->failure_len].actual = strdup(actual ? actual : "null");
    g->failure_len++;
    pthread_mutex_unlock(&g_festina_test_lock);
}

/* specification.md 11.7.4: `<n> pass, <m> fail. <p>%`, with the fail
 * count omitted entirely when there are none, and the percentage
 * TRUNCATED rather than rounded -- two of three is 66%. A group with no
 * assertions at all is 100%: nothing failed. */
static void festina_test_print_line(const char *label, int64_t pass, int64_t fail) {
    int64_t total = pass + fail;
    int64_t pct = total == 0 ? 100 : (pass * 100) / total;
    if (fail == 0) {
        printf("%s: %lld pass. %lld%%\n", label, (long long)pass, (long long)pct);
    } else {
        printf("%s: %lld pass, %lld fail. %lld%%\n", label,
               (long long)pass, (long long)fail, (long long)pct);
    }
}

void festina_test_report(void) {
    pthread_mutex_lock(&g_festina_test_lock);
    int64_t total_pass = 0, total_fail = 0;
    for (int64_t i = 0; i < g_festina_test_group_len; i++) {
        FestinaTestGroup *g = &g_festina_test_groups[i];
        festina_test_print_line(g->description, g->passed, g->failed);
        for (int64_t j = 0; j < g->failure_len; j++) {
            printf(" | - fail: %s // %s\n", g->failures[j].source,
                   g->failures[j].actual);
            free(g->failures[j].source);
            free(g->failures[j].actual);
        }
        free(g->failures);
        g->failures = NULL;
        g->failure_len = 0;
        g->failure_cap = 0;
        total_pass += g->passed;
        total_fail += g->failed;
        free(g->description);
        g->description = NULL;
    }
    festina_test_print_line("Overall", total_pass, total_fail);
    fflush(stdout);
    g_festina_test_fail_total = total_fail;
    free(g_festina_test_groups);
    g_festina_test_groups = NULL;
    g_festina_test_group_len = 0;
    g_festina_test_group_cap = 0;
    pthread_mutex_unlock(&g_festina_test_lock);
}

/* What main returns: non-zero exactly when some assertion failed, which
 * is what makes `festina test` usable in a pipeline. */
int64_t festina_test_failures(void) {
    return g_festina_test_fail_total;
}
