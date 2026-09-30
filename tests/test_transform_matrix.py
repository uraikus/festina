"""runtime.md phase 7, slice 4: the transform's arithmetic is the runtime's
own, and it is Cairo's to the bit.

translate(), rotate() and scale() used to call cairo_matrix_translate and
its siblings; those are plain arithmetic, and Cairo is leaving, so the
runtime computes them itself (festina_matrix_* in
festina_runtime_graphics.c). What keeps that honest: the real functions
are cut out of the runtime, compiled beside Cairo, and run on the same
random sequences of operations as cairo_matrix_*, and every matrix must
come out identical -- the six doubles compared as bytes, not within a
tolerance, because a matrix is the input of everything drawn after it.
"""
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GRAPHICS_C = os.path.join(ROOT, "runtime", "festina_runtime_graphics.c")

HARNESS = r"""
#include <cairo.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
%(helpers)s

int main(void) {
    uint64_t x = 88172645463325252ULL;
    long checked = 0, differ = 0;
    for (int seq = 0; seq < 20000; seq++) {
        cairo_matrix_t a, b;
        cairo_matrix_init_identity(&a);
        festina_matrix_identity(&b);
        for (int step = 0; step < 12; step++) {
            x ^= x << 13; x ^= x >> 7; x ^= x << 17;
            double p = ((double)(int64_t)(x %% 2001) - 1000.0) / 7.0;
            double q = ((double)(int64_t)((x >> 20) %% 2001) - 1000.0) / 13.0;
            double deg = ((double)(int64_t)((x >> 40) %% 7201) - 3600.0) / 10.0;
            switch ((x >> 8) %% 3) {
            case 0: cairo_matrix_translate(&a, p, q); festina_matrix_translate(&b, p, q); break;
            case 1: cairo_matrix_rotate(&a, deg * 3.14159265358979323846 / 180.0);
                    festina_matrix_rotate(&b, deg * 3.14159265358979323846 / 180.0); break;
            default: if (p != 0.0 && q != 0.0) { cairo_matrix_scale(&a, p / 50.0, q / 50.0);
                                                 festina_matrix_scale(&b, p / 50.0, q / 50.0); }
            }
            checked++;
            if (memcmp(&a, &b, sizeof(a)) != 0) differ++;
        }
    }
    printf("checked %%ld differ %%ld\n", checked, differ);
    return 0;
}
"""


def _helpers():
    src = open(GRAPHICS_C, encoding="utf-8").read()
    a = src.index("static void festina_matrix_identity")
    b = src.index("/* saveState()/restoreState() save the whole drawing state", a)
    return src[a:b]


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if not cc:
        pytest.skip("no C compiler")
    try:
        flags = subprocess.run(["pkg-config", "--cflags", "--libs", "cairo"],
                               capture_output=True, text=True, check=True).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        if os.environ.get("FESTINA_STRICT_DEPS"):
            pytest.fail("cairo's development files are not installed")
        pytest.skip("cairo's development files are not installed")
    d = tmp_path_factory.mktemp("matrix")
    (d / "m.c").write_text(HARNESS % {"helpers": _helpers()})
    out = d / "m"
    r = subprocess.run([cc, "-O1", str(d / "m.c"), "-o", str(out), "-lm"] + flags,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return subprocess.run([str(out)], capture_output=True, text=True, timeout=60)


def test_the_runtimes_matrices_are_cairos_to_the_bit(run):
    assert run.stdout.split() == ["checked", "240000", "differ", "0"], run.stdout + run.stderr
