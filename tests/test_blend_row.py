"""runtime.md phase 7, slice 2: `festina_image_blend_row` cannot be made to
touch memory outside the image or its coverage array.

The primitive takes a row, a span and a coverage array from Festina code,
none of which C can trust: raster.f computes them, but the primitive is
also a callable method, and an out-of-range row or a span longer than its
array would write before the pixel buffer or read past the array. The
tests in test_raster_img.py check what is drawn; a stray write into
unmapped-by-luck heap draws nothing anyone sees, so this cuts the real
function out of the runtime, compiles it with AddressSanitizer and calls
it with every argument past its edge.

Needs a C compiler that can link -fsanitize=address, and Cairo's headers
(FESTINA_STRICT_DEPS turns a missing one into a failure).
"""
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GRAPHICS_C = os.path.join(ROOT, "runtime", "festina_runtime_graphics.c")


def _cut(src, start, end, keep_end=False):
    a = src.index(start)
    b = src.index(end, a)
    return src[a:b + (len(end) if keep_end else 0)]


def _parts():
    src = open(GRAPHICS_C, encoding="utf-8").read()
    box = _cut(src, "typedef struct {\n    cairo_surface_t *surface;", "} FestinaImageBox;", True)
    pixman = _cut(src, "static inline uint32_t festina_mul_un8", "static inline uint32_t festina_solid_pixel_from_style")
    stale = _cut(src, "static void festina_image_bytes_now_stale", "\n}\n", True)
    blend = _cut(src, "void festina_image_blend_row", "\n}\n", True)
    return "\n".join([box, pixman, stale, blend])


HARNESS = r"""
#include <cairo.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
%(parts)s

/* A coverage array as Festina lays it out: {length, data pointer}, the
 * data in an allocation of exactly `n` doubles so a read past it is an
 * AddressSanitizer error, not a lucky zero. */
static int64_t *coverage(int64_t n, double v) {
    int64_t *payload = malloc(2 * sizeof(int64_t));
    double *data = malloc((size_t)(n ? n : 1) * sizeof(double));
    for (int64_t i = 0; i < n; i++) data[i] = v;
    payload[0] = n;
    memcpy(&payload[1], &data, sizeof(data));
    return payload;
}
static void release(int64_t *payload) { double *d; memcpy(&d, &payload[1], sizeof(d)); free(d); free(payload); }

static int alpha_at(cairo_surface_t *s, int x, int y) {
    cairo_surface_flush(s);
    unsigned char *d = cairo_image_surface_get_data(s);
    uint32_t px; memcpy(&px, d + y * cairo_image_surface_get_stride(s) + x * 4, 4);
    return (int)(px >> 24);
}

int main(void) {
    for (int f = 0; f < 2; f++) {
        cairo_format_t format = f ? CAIRO_FORMAT_RGB24 : CAIRO_FORMAT_ARGB32;
        cairo_surface_t *s = cairo_image_surface_create(format, 6, 3);
        FestinaImageBox box; memset(&box, 0, sizeof(box)); box.surface = s;
        int64_t *full = coverage(6, 1.0), *shortc = coverage(2, 1.0), *none = coverage(0, 1.0),
                *longc = coverage(100, 1.0);
        int64_t rows[] = { -1, -1000000, 3, 99, INT64_MAX, INT64_MIN };
        for (int i = 0; i < 6; i++) festina_image_blend_row(&box, rows[i], 0, 6, full, 9, 9, 9, 1.0);
        int64_t xs[][2] = { {-5, 2}, {4, 99}, {-1000000000, 1000000000}, {5, 3}, {INT64_MIN, INT64_MAX}, {6, 9}, {-9, 0} };
        for (int i = 0; i < 7; i++) festina_image_blend_row(&box, 1, xs[i][0], xs[i][1], full, 9, 9, 9, 1.0);
        festina_image_blend_row(&box, 2, 0, 6, shortc, 9, 9, 9, 1.0);
        festina_image_blend_row(&box, 2, 0, 6, none, 9, 9, 9, 1.0);
        festina_image_blend_row(&box, 1, 0, 99, longc, 9, 9, 9, 1.0);   /* array longer than the row */
        festina_image_blend_row(&box, 0, 0, 6, NULL, 9, 9, 9, 1.0);
        festina_image_blend_row(NULL, 0, 0, 6, full, 9, 9, 9, 1.0);
        printf("format %%d: row 0 alpha %%d %%d, row 1 alpha %%d %%d %%d, row 2 alpha %%d %%d %%d\n", f,
               alpha_at(s, 0, 0), alpha_at(s, 5, 0), alpha_at(s, 0, 1), alpha_at(s, 3, 1),
               alpha_at(s, 5, 1), alpha_at(s, 0, 2), alpha_at(s, 1, 2), alpha_at(s, 2, 2));
        release(full); release(shortc); release(none); release(longc);
        cairo_surface_destroy(s);
    }
    return 0;
}
"""


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    def unavailable(reason):
        if os.environ.get("FESTINA_STRICT_DEPS"):
            pytest.fail(reason)
        pytest.skip(reason)
    cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if not cc:
        unavailable("no C compiler")
    try:
        flags = subprocess.run(["pkg-config", "--cflags", "--libs", "cairo"],
                               capture_output=True, text=True, check=True).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        unavailable("cairo's development files are not installed")
    d = tmp_path_factory.mktemp("blend")
    (d / "h.c").write_text(HARNESS % {"parts": _parts()})
    out = d / "h"
    r = subprocess.run([cc, "-fsanitize=address,undefined", "-fno-sanitize-recover=undefined",
                        "-g", str(d / "h.c"), "-o", str(out)] + flags, capture_output=True, text=True)
    if r.returncode != 0:
        # Distinguish "no sanitizer runtime" (MinGW: `cannot find -lasan`)
        # from a harness that does not compile: the same program without
        # the sanitizers must build, or this is a real error.
        plain = subprocess.run([cc, str(d / "h.c"), "-o", str(d / "plain")] + flags,
                               capture_output=True, text=True)
        assert plain.returncode == 0, plain.stderr
        unavailable("this compiler cannot link -fsanitize=address: " + r.stderr[-200:])
    return subprocess.run([str(out)], capture_output=True, text=True, timeout=60,
                          env=dict(os.environ, ASAN_OPTIONS="detect_leaks=0"))


def test_no_argument_makes_it_touch_memory_it_does_not_own(run):
    assert run.returncode == 0, run.stderr[-3000:]


def test_and_what_is_in_range_is_still_drawn(run):
    # row 1 gets the spans that overlap the surface -- {-5,2} {4,99} and
    # the two enormous ones -- so every column is drawn; row 0 and the
    # rows past the surface are untouched, and row 2 only ever got the
    # two-element coverage.
    lines = [l for l in run.stdout.splitlines() if l.startswith("format")]
    same = "row 0 alpha 0 0, row 1 alpha 255 255 255, row 2 alpha 255 255 0"
    assert lines == [f"format 0: {same}", f"format 1: {same}"], lines


# ---- festina_over_un8x4 against the definition it replaced ----
#
# The compositing step was written a channel at a time and then, for
# speed, the way pixman writes it: two channels to a 32-bit lane. It is
# what the direct fills, the text mask and __blendRow all composite with,
# so a difference of one in any channel is a visible change everywhere;
# this holds the fast form to the plain one on random pairs (half of
# them shaped like real premultiplied sources) and on every combination
# of the values where carries and rounding live.

OVER_HARNESS = r"""
#include <stdint.h>
#include <stdio.h>
%(parts)s

static uint32_t reference(uint32_t s, uint32_t d) {
    uint32_t sa = s >> 24;
    if (sa == 0xFF) return s;
    if (s == 0) return d;
    uint32_t ia = 0xFF - sa, out = 0;
    for (int shift = 0; shift < 32; shift += 8) {
        uint32_t c = festina_mul_un8((d >> shift) & 0xFF, ia) + ((s >> shift) & 0xFF);
        if (c > 0xFF) c = 0xFF;
        out |= c << shift;
    }
    return out;
}

int main(void) {
    uint64_t x = 88172645463325252ULL;
    long checked = 0, differ = 0;
    for (long i = 0; i < 30000000L; i++) {
        x ^= x << 13; x ^= x >> 7; x ^= x << 17;
        uint32_t s = (uint32_t)x, d = (uint32_t)(x >> 32);
        if (i & 1) {
            uint32_t a = s >> 24;
            s = (a << 24) | ((((s >> 16) & 0xFF) %% (a + 1)) << 16)
                          | ((((s >> 8) & 0xFF) %% (a + 1)) << 8) | ((s & 0xFF) %% (a + 1));
        }
        checked++;
        if (reference(s, d) != festina_over_un8x4(s, d)) differ++;
    }
    uint32_t v[] = { 0, 1, 0x7F, 0x80, 0xFE, 0xFF };
    for (int i = 0; i < 6 * 6 * 6 * 6; i++)
        for (int j = 0; j < 6 * 6 * 6 * 6; j++) {
            uint32_t s = 0, d = 0;
            for (int k = 0, a = i, b = j; k < 4; k++, a /= 6, b /= 6) {
                s |= v[a %% 6] << (8 * k);
                d |= v[b %% 6] << (8 * k);
            }
            checked++;
            if (reference(s, d) != festina_over_un8x4(s, d)) differ++;
        }
    printf("checked %%ld differ %%ld\n", checked, differ);
    return 0;
}
"""


@pytest.fixture(scope="module")
def over_run(tmp_path_factory):
    cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if not cc:
        pytest.skip("no C compiler")
    d = tmp_path_factory.mktemp("over")
    src = open(GRAPHICS_C, encoding="utf-8").read()
    parts = _cut(src, "static inline uint32_t festina_mul_un8",
                 "/* One 8-bit channel exactly")
    (d / "o.c").write_text(OVER_HARNESS % {"parts": parts})
    out = d / "o"
    r = subprocess.run([cc, "-O2", str(d / "o.c"), "-o", str(out)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return subprocess.run([str(out)], capture_output=True, text=True, timeout=120)


def test_the_fast_over_equals_the_channel_at_a_time_definition(over_run):
    m = re.search(r"checked (\d+) differ (\d+)", over_run.stdout)
    assert m, over_run.stdout + over_run.stderr
    assert int(m.group(1)) > 30_000_000
    assert m.group(2) == "0", over_run.stdout
