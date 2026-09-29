"""runtime.md phase 7, slice 1: the allocation seam.

Every colour surface's pixels are allocated by one function,
`festina_surface_create`, which owns the buffer and lends Cairo a
zero-copy view of it. Two claims, held separately:

- NOTHING ELSE allocates one. A second `cairo_image_surface_create` for
  an ARGB32 or RGB24 surface would be a surface whose pixels Cairo owns,
  quietly outside the seam -- read from the source, since no behaviour
  tells the two apart. (A8 masks are deliberately not routed through it.)
- THE SEAM DOES WHAT IT SAYS. The function is cut out of the runtime and
  compiled into a small C harness, so it is the real code under test and
  not a copy of it, with calloc and free counted: the pixels are the
  buffer's, zeroed, four-byte aligned, drawing lands in them, and every
  allocation is freed exactly once -- on destroy, and on each of the two
  paths where Cairo refuses a size after the buffer was allocated.

The leak harness (scripts/leak_stress.sh, Linux) watches the same thing
from the outside under LeakSanitizer; this runs anywhere with a C
compiler and Cairo's headers.
"""
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GRAPHICS_C = os.path.join(ROOT, "runtime", "festina_runtime_graphics.c")


def _source():
    with open(GRAPHICS_C, encoding="utf-8") as fh:
        return fh.read()


def _seam(src):
    """The seam's own text: its key, its free callback, and the function,
    from `static cairo_user_data_key_t` to the function's closing brace."""
    start = src.index("static cairo_user_data_key_t g_surface_buffer_key;")
    end = src.index("static void festina_surface_prefault", start)
    return src[start:end]


def test_every_colour_surface_goes_through_the_seam():
    src = _source()
    outside = src.replace(_seam(src), "")
    stray = re.findall(r"cairo_image_surface_create\(\s*CAIRO_FORMAT_(?:ARGB32|RGB24)", outside)
    assert not stray, (
        f"{len(stray)} colour surface(s) allocated outside festina_surface_create: "
        "their pixels are Cairo's, not ours")
    assert len(re.findall(r"festina_surface_create\(CAIRO_FORMAT_", outside)) >= 10, \
        "the callers have to actually use it"


HARNESS = r"""
#include <cairo.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>

static long allocs = 0, frees = 0;
static void *counted_calloc(size_t n, size_t m) { void *p = calloc(n, m); if (p) allocs++; return p; }
static void counted_free(void *p) { if (p) frees++; free(p); }
#define calloc counted_calloc
#define free counted_free

/* ---- the runtime's own seam, verbatim ---- */
%(seam)s
/* ---- end ---- */

#undef calloc
#undef free

int main(void) {
    int ok = 1;
    cairo_format_t formats[2] = { CAIRO_FORMAT_ARGB32, CAIRO_FORMAT_RGB24 };
    for (int f = 0; f < 2; f++) {
        cairo_surface_t *s = festina_surface_create(formats[f], 37, 23);   /* an odd width */
        if (cairo_surface_status(s) != CAIRO_STATUS_SUCCESS) { puts("FAIL status"); return 1; }
        unsigned char *d = cairo_image_surface_get_data(s);
        int stride = cairo_image_surface_get_stride(s);
        size_t bytes = (size_t)stride * 23;
        int zero = 1; for (size_t i = 0; i < bytes; i++) if (d[i]) zero = 0;
        printf("format %%d: size %%dx%%d stride %%d aligned4 %%d zeroed %%d\n", (int)formats[f],
               cairo_image_surface_get_width(s), cairo_image_surface_get_height(s), stride,
               (int)(((uintptr_t)d & 3) == 0), zero);
        cairo_t *cr = cairo_create(s);
        cairo_set_source_rgba(cr, 1, 0, 0, 1);
        cairo_rectangle(cr, 2, 3, 5, 4);
        cairo_fill(cr);
        cairo_destroy(cr);
        cairo_surface_flush(s);
        uint32_t px = *(uint32_t *)(d + 4 * stride + 4 * 3);
        printf("  drawn pixel in the buffer: %%08x (same pointer: %%d)\n", px,
               (int)(cairo_image_surface_get_data(s) == d));
        cairo_surface_destroy(s);
    }
    /* Cairo's own verdicts, and no leak from any of them. */
    int sizes[4][2] = { {0, 10}, {10, 0}, {-3, 4}, {40000, 10} };
    for (int i = 0; i < 4; i++) {
        cairo_surface_t *s = festina_surface_create(CAIRO_FORMAT_ARGB32, sizes[i][0], sizes[i][1]);
        cairo_surface_t *plain = cairo_image_surface_create(CAIRO_FORMAT_ARGB32, sizes[i][0], sizes[i][1]);
        printf("size %%dx%%d refused: %%d, plain cairo refuses: %%d\n", sizes[i][0], sizes[i][1],
               (int)(cairo_surface_status(s) != CAIRO_STATUS_SUCCESS),
               (int)(cairo_surface_status(plain) != CAIRO_STATUS_SUCCESS));
        cairo_surface_destroy(plain);
        cairo_surface_destroy(s);
    }
    /* Shared ownership: the buffer survives until the LAST reference goes. */
    cairo_surface_t *s = festina_surface_create(CAIRO_FORMAT_ARGB32, 8, 8);
    long before = frees;
    cairo_surface_reference(s);
    cairo_surface_destroy(s);
    printf("freed early with a reference still held: %%d\n", (int)(frees != before));
    cairo_surface_destroy(s);
    printf("allocs %%ld frees %%ld\n", allocs, frees);
    return 0;
}
"""


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if not cc:
        _skip("no C compiler")
    try:
        flags = subprocess.run(["pkg-config", "--cflags", "--libs", "cairo"],
                               capture_output=True, text=True, check=True).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        _skip("cairo's development files are not installed")
    d = tmp_path_factory.mktemp("seam")
    (d / "h.c").write_text(HARNESS % {"seam": _seam(_source())})
    out = d / "h"
    r = subprocess.run([cc, str(d / "h.c"), "-o", str(out)] + flags,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    run = subprocess.run([str(out)], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stdout + run.stderr
    return run.stdout


def _skip(reason):
    if os.environ.get("FESTINA_STRICT_DEPS"):
        pytest.fail(reason)
    pytest.skip(reason)


def test_the_pixels_are_the_buffers_and_zeroed(harness):
    lines = harness.splitlines()
    for fmt in (0, 1):   # CAIRO_FORMAT_ARGB32 = 0, RGB24 = 1
        row = next(l for l in lines if l.startswith(f"format {fmt}:"))
        assert "size 37x23" in row and "aligned4 1" in row and "zeroed 1" in row, row
    assert lines[lines.index(next(l for l in lines if l.startswith("format 0:"))) + 1].strip() \
        == "drawn pixel in the buffer: ffff0000 (same pointer: 1)"


def test_cairos_verdict_on_a_bad_size_is_kept(harness):
    # Whatever Cairo says about a size -- a zero side is fine to it, a
    # negative or over-large one is not -- the seam says the same.
    rows = re.findall(r"size (\S+) refused: (\d), plain cairo refuses: (\d)", harness)
    assert [r[0] for r in rows] == ["0x10", "10x0", "-3x4", "40000x10"]
    for size, ours, theirs in rows:
        assert ours == theirs, size
    assert {r[0] for r in rows if r[1] == "1"} == {"-3x4", "40000x10"}


def test_the_buffer_outlives_all_but_the_last_reference(harness):
    assert "freed early with a reference still held: 0" in harness


def test_every_allocation_is_freed_exactly_once(harness):
    m = re.search(r"allocs (\d+) frees (\d+)", harness)
    allocs, frees = int(m.group(1)), int(m.group(2))
    assert allocs >= 4, "the harness has to allocate through the seam"
    assert allocs == frees, f"{allocs} allocated, {frees} freed"
