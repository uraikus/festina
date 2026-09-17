"""claude.md #340: the deferred-root buffer, probed at the C level.

A still-referenced release of a cycle-capable value records the value
as a possible root instead of running its own trial; one collection
then answers a whole batch. Three of that design's properties cannot be
reached from Festina source at all:

- **How much work a batch does.** The whole point is that a second root
  inside a ring the batch has already walked costs nothing, and the only
  way to see that is to count traversal calls.
- **That an un-flushed buffer holds its roots.** LeakSanitizer cannot
  see this one, which is worth stating plainly: the buffer is a live
  `__thread` array holding pointers to those nodes, so LSan classifies
  them as still-reachable rather than leaked. `tests/stress/
  cycle_buffer_churn.f` covers what the sanitizer CAN see (the
  use-after-free it actually caught, and the ordinary leaks); the flush
  needs a different instrument, and this is it.
- **That a buffered node is not freed by another root's sweep.** The
  protection is a bit test inside `festina_cycle_begin_white`, and from
  Festina it is invisible until it is a crash.

So this drives the runtime helpers directly, with hand-written
traversal functions standing in for the ones the compiler generates --
the same shape tests/test_clear.py's `festina_zeroize` probe already
uses.
"""
import os
import subprocess

import pytest


# Two nodes in a ring, each holding the other, plus one external
# reference -- exactly the shape a release-while-live produces. The
# traversal functions count their own calls so a test can assert how
# much walking a batch did.
PROBE = r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "festina_runtime.h"

typedef struct Node { struct Node *next; } Node;

static int gray_calls, scan_calls, white_calls;

static void *make(void) {
    /* header word, then the payload -- the layout every managed value
     * has (see festina_retain's own comment in the runtime). */
    char *base = (char *)calloc(1, sizeof(long long) + sizeof(Node));
    *(long long *)base = 1;
    return base + sizeof(long long);
}

static long long *hdr(void *p) {
    return (long long *)((char *)p - sizeof(long long));
}

static void gray(void *p);
static void scan(void *p);
static void white(void *p);

static void gray(void *p) {
    gray_calls++;
    if (!festina_cycle_begin_gray(p)) return;
    Node *n = (Node *)p;
    if (n->next) { festina_cycle_dec(n->next); gray(n->next); }
}

static void black(void *p) {
    if (!festina_cycle_needs_black(p)) return;
    festina_cycle_set_black(p);
    Node *n = (Node *)p;
    if (n->next) { festina_cycle_inc(n->next); black(n->next); }
}

static void scan(void *p) {
    scan_calls++;
    long long what = festina_cycle_begin_scan(p);
    if (what == 0) return;
    if (what == 1) { black(p); return; }
    Node *n = (Node *)p;
    if (n->next) scan(n->next);
}

static void white(void *p) {
    white_calls++;
    if (!festina_cycle_begin_white(p)) return;
    Node *n = (Node *)p;
    void *child = n->next;
    n->next = NULL;
    if (child) white(child);
    festina_cycle_defer_free((char *)p - sizeof(long long));
}

/* Case 1: one ring, two roots buffered from it. The second root must
 * cost nothing to mark, because the first root's walk already claimed
 * every node. */
static void case_shared_walk(void) {
    void *a = make(), *b = make();
    ((Node *)a)->next = (Node *)b;  (*hdr(b))++;
    ((Node *)b)->next = (Node *)a;  (*hdr(a))++;
    /* Drop the two external references, as a release would. */
    (*hdr(a))--; (*hdr(b))--;
    gray_calls = scan_calls = white_calls = 0;
    festina_cycle_add_root(a, gray, scan, white);
    festina_cycle_add_root(b, gray, scan, white);
    int grays_before = gray_calls;
    festina_cycle_collect();
    printf("shared %d %d\n", grays_before, gray_calls);
}

/* Case 2: a root left in the buffer. Nothing collects it until the
 * flush, and the flush must collect it. */
static void case_flush(void) {
    void *a = make(), *b = make();
    ((Node *)a)->next = (Node *)b;  (*hdr(b))++;
    ((Node *)b)->next = (Node *)a;  (*hdr(a))++;
    (*hdr(a))--; (*hdr(b))--;
    gray_calls = scan_calls = white_calls = 0;
    festina_cycle_add_root(a, gray, scan, white);
    int before = white_calls;
    festina_cycle_flush();
    printf("flush %d %d\n", before, white_calls);
}

/* Case 3: two roots in one ring, and the FIRST one's sweep reaches the
 * second. While the second is still buffered it must survive that
 * sweep; its own turn is what frees it. The observable is that the
 * header the buffer still points at is readable after the whole
 * collection -- which under ASan is the difference between a pass and
 * a use-after-free report. */
static void case_buffered_survives_a_sweep(void) {
    void *a = make(), *b = make();
    ((Node *)a)->next = (Node *)b;  (*hdr(b))++;
    ((Node *)b)->next = (Node *)a;  (*hdr(a))++;
    (*hdr(a))--; (*hdr(b))--;
    festina_cycle_add_root(a, gray, scan, white);
    festina_cycle_add_root(b, gray, scan, white);
    festina_cycle_collect();
    printf("survived 1\n");
}

int main(void) {
    case_shared_walk();
    case_flush();
    case_buffered_survives_a_sweep();
    festina_cycle_flush();
    return 0;
}
"""


@pytest.fixture(scope="module")
def probe_output(tmp_path_factory):
    from tests.conftest import _require_c_compiler
    cc = _require_c_compiler()
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tmp = tmp_path_factory.mktemp("cycbuf")
    src = tmp / "cycprobe.c"
    src.write_text(PROBE)
    exe = tmp / "cycprobe"
    build = subprocess.run(
        [cc, "-O1", "-I", os.path.join(root, "runtime"), str(src),
         os.path.join(root, "runtime", "festina_runtime.c"),
         "-o", str(exe), "-lsqlite3", "-lm", "-lpthread", "-ldl"],
        capture_output=True, text=True, encoding="utf-8")
    if build.returncode != 0:
        pytest.skip(f"cannot link the runtime here: {build.stderr[-300:]}")
    out = subprocess.run([str(exe)], capture_output=True, text=True,
                         encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr[-500:]
    parsed = {}
    for line in out.stdout.split("\n"):
        if not line:
            continue
        parts = line.split()
        parsed[parts[0]] = [int(x) for x in parts[1:]]
    return parsed


class TestABatchWalksSharedStructureOnce:
    def test_adding_a_root_does_no_walking_of_its_own(self, probe_output):
        """The change decisions.md #254 asked for, stated as a number.
        Buffering two roots must not traverse anything at all -- the old
        code ran a full trial per release, which is the cost the buffer
        exists to remove."""
        grays_before, _ = probe_output["shared"]
        assert grays_before == 0, (
            f"{grays_before} gray traversals happened while merely "
            f"BUFFERING two roots; buffering must not walk")

    def test_the_second_root_in_a_walked_ring_is_free(self, probe_output):
        """Two roots, one two-node ring, counted exactly.

        Root a's mark is three calls: a, its child b, and b's own child
        edge back to a, which finds a already gray and returns. Root b's
        mark is then a single call that returns at once, because the
        first walk already claimed it. Four.

        Two independent trials -- what every release used to run -- is
        six: three for a, and three again for b once the first trial has
        restored everything to black. The gap is small here because the
        ring is two nodes; it is the whole 9-10x of decisions.md #254 at
        a ring of twenty thousand, because the saved walk is the size of
        the shared structure and the batch is what shares it."""
        _, grays_after = probe_output["shared"]
        assert grays_after == 4, (
            f"{grays_after} gray calls for a 2-node ring with 2 roots; "
            f"expected 4 -- 6 would mean each root walked the ring for "
            f"itself, which is the cost the buffer exists to remove")


class TestTheFlushIsWhatCollectsTheLastBatch:
    def test_nothing_is_collected_while_the_root_only_sits_there(self, probe_output):
        before, _ = probe_output["flush"]
        assert before == 0, (
            f"{before} sweeps ran before the flush; a buffered root is "
            f"supposed to wait")

    def test_the_flush_collects_it(self, probe_output):
        """specification.md 13.3: a cycle whose last outside reference
        has been released is collected before the program exits. Without
        the flush main() emits, the final partial batch is not -- and
        LeakSanitizer cannot say so, because the buffer holds those
        pointers and keeps them reachable."""
        _, after = probe_output["flush"]
        assert after > 0, (
            "the flush swept nothing, so a cycle left in a partial "
            "batch would survive the program")


class TestABufferedRootSurvivesAnotherRootsSweep:
    def test_the_collection_completes(self, probe_output):
        """Under ASan this is the test; without the buffered-bit check
        in festina_cycle_begin_white the first root's sweep frees the
        second, and reading its header on its own turn is a
        use-after-free. Without ASan it still asserts the collection
        runs to the end rather than crashing."""
        assert probe_output["survived"] == [1]
