"""runtime.md phase 7, slice 3: the coverage speed-ups changed no pixel.

Slice 3 made `rasRowCoverage` several times faster -- a row's work
limited to the columns the shape touches, each row's sub-scanlines
walking only the edges that reach it, and the whole pixels inside a span
summed once instead of added sixteen times -- and each of those is a
place to be quietly wrong. So the output of the version before them is
pinned: `tests/fixtures/raster_golden.fsrc` draws 260 pseudo-random shapes
(polygons under both fill rules, circles, fractional rectangles,
strokes, clipped fills, transformed fills, gradients, clears) onto both
raster.f targets, on an odd-sized surface with shapes running off every
edge and one coverage buffer shared throughout, and logs a hash of each
target's pixels. The two numbers below were recorded from the code as it
stood at the end of slice 2 (commit e0ab0aa), before any of it.

The scene's extension is `.fsrc`, not `.f`: the bootstrap differential
compiles every `.f` file in the repository as its corpus, and this one
imports raster.f from beside itself, which only the test arranges.

If a deliberate change to coverage ever moves them, that is a decision
(runtime.md, phase 7, decision 4) and not a number to update: look at
what moved first.
"""
import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.test_raster import _decode_png, _run, _with_raster   # noqa: E402

_FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures",
                        "raster_golden.fsrc")

#: The `arr[int]` target's pixels, then the `img` target's.
GOLDEN = ("716613692", "119937398")


def _scene(tmp_path, cli_mod):
    _with_raster(tmp_path)
    return _run(tmp_path, cli_mod, open(_FIXTURE, encoding="utf-8").read()).split()


def test_the_pixels_are_the_ones_before_the_speed_ups(tmp_path, cli_mod):
    out = _scene(tmp_path, cli_mod)
    assert tuple(out[:2]) == GOLDEN, out


def test_the_scene_is_a_real_one(tmp_path, cli_mod):
    """A hash of two blank images matches itself. Both targets must show
    a busy picture: many colours, soft edges, shapes across the surface."""
    _scene(tmp_path, cli_mod)
    for name in ("golden_arr.png", "golden_img.png"):
        w, h, rows, n = _decode_png(str(tmp_path / name))
        assert (w, h) == (97, 71)
        colours = {bytes(r[x * n:x * n + 3]) for r in rows for x in range(w)}
        assert len(colours) > 1500, (name, len(colours))
        # every quarter of the surface has something drawn in it
        for y0, y1, x0, x1 in ((0, 35, 0, 48), (0, 35, 48, 97), (35, 71, 0, 48), (35, 71, 48, 97)):
            seen = {bytes(rows[y][x * n:x * n + 3]) for y in range(y0, y1) for x in range(x0, x1)}
            assert len(seen) > 100, (name, y0, x0)
