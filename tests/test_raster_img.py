"""runtime.md phase 7, slice 2: raster.f drawing onto an `img` itself.

Until now raster.f filled an `arr[int]` -- four ints a pixel -- and an
image was made from it afterwards. These tests are for the other target:
the same rasteriser, but the last step, blending a row's coverage into
pixels, goes to `img.__blendRow` and so into the surface's own bytes
(festina_image_blend_row in festina_runtime_graphics.c).

Three claims, each with its own oracle:

* **Against Cairo, byte for byte, wherever coverage is exact.** A
  pixel-aligned rectangle covers whole pixels, so there is one right
  answer, and the blend is pixman's own arithmetic -- opaque or
  translucent, over an opaque, transparent or JPEG-backed (RGB24)
  destination.
* **Against Cairo, within a bound, where it is not.** A circle's edge
  pixels differ by the rasteriser's own measured amount (phase 4) and
  nowhere else: every differing pixel sits on the rim.
* **Against the `arr[int]` target, within one grey level.** Fills,
  strokes, clips and transforms take the same coverage either way, so
  the two targets may differ only by the rounding of two blend
  formulas.
"""
import math
import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.test_raster import (   # noqa: E402
    _decode_png, _png_diff, _run, _with_raster, _RECTS)

_FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

_HEAD = ("color bg = '#204060'\ncolor red = '#ff0000'\ncolor grn = '#00ff00'\n"
         "color yel = '#ffff00'\n")


def _pixels(path):
    w, h, rows, n = _decode_png(path)
    return w, h, [[tuple(r[x * n:x * n + 3]) for x in range(w)] for r in rows]


def _worst(a, b):
    """Largest channel difference between two PNGs, and where."""
    (wa, ha, pa), (wb, hb, pb) = _pixels(a), _pixels(b)
    assert (wa, ha) == (wb, hb)
    worst, at, count = 0, None, 0
    for y in range(ha):
        for x in range(wa):
            d = max(abs(p - q) for p, q in zip(pa[y][x], pb[y][x]))
            if d:
                count += 1
            if d > worst:
                worst, at = d, (x, y)
    return worst, at, count


def _both(tmp_path, cli_mod, cairo_src, mine_src):
    _with_raster(tmp_path)
    assert _run(tmp_path, cli_mod, cairo_src) == "true"
    assert _run(tmp_path, cli_mod, "import raster.f\n" + mine_src) == "true"


class TestExactWhereCoverageIsExact:
    def test_opaque_rects_are_byte_identical(self, tmp_path, cli_mod):
        cairo = "".join(
            f"color c{i} = '#{r:02x}{g:02x}{b:02x}'\na.drawRect({x}, {y}, {w}, {h}, c{i})\n"
            for i, (x, y, w, h, r, g, b) in enumerate(_RECTS))
        mine = "".join(f"rasFillRectImg(a, {x}.0, {y}.0, {w}.0, {h}.0, {r}, {g}, {b}, 1.0)\n"
                       for (x, y, w, h, r, g, b) in _RECTS)
        _both(tmp_path, cli_mod,
              "img a = blankImage(40, 30)\n" + cairo + "log(a.save('cairo.png'))\n",
              "img a = blankImage(40, 30)\n" + mine + "log(a.save('mine.png'))\n")
        assert _png_diff(str(tmp_path / "cairo.png"), str(tmp_path / "mine.png")) == []

    def test_translucent_layers_over_an_opaque_ground_are_byte_identical(
            self, tmp_path, cli_mod):
        _both(tmp_path, cli_mod,
              _HEAD + "img a = blankImage(40, 30)\na.drawRect(0, 0, 40, 30, bg)\n"
              "fillAlpha(0.5)\na.drawRect(3, 4, 12, 9, red)\na.drawRect(9, 8, 14, 10, grn)\n"
              "a.drawRect(6, 6, 20, 4, yel)\nlog(a.save('cairo.png'))\n",
              "img a = blankImage(40, 30)\nrasFillRectImg(a, 0.0, 0.0, 40.0, 30.0, 32, 64, 96, 1.0)\n"
              "rasFillRectImg(a, 3.0, 4.0, 12.0, 9.0, 255, 0, 0, 0.5)\n"
              "rasFillRectImg(a, 9.0, 8.0, 14.0, 10.0, 0, 255, 0, 0.5)\n"
              "rasFillRectImg(a, 6.0, 6.0, 20.0, 4.0, 255, 255, 0, 0.5)\n"
              "log(a.save('mine.png'))\n")
        worst, at, count = _worst(str(tmp_path / "cairo.png"), str(tmp_path / "mine.png"))
        assert worst == 0, (worst, at, count)

    def test_awkward_colours_and_alphas_round_the_way_pixman_does(self, tmp_path, cli_mod):
        """255 and 0 are exact under any rounding, so the layers above
        could not tell a wrong premultiply from a right one. These
        colours, at these alphas, do."""
        rects = [(1, 1, 12, 9, 200, 100, 50, 0.4), (5, 4, 12, 9, 17, 231, 143, 0.7),
                 (8, 2, 9, 12, 99, 3, 250, 0.15)]
        cairo = "".join(f"color c{i} = '#{r:02x}{g:02x}{b:02x}'\nfillAlpha({a})\n"
                        f"a.drawRect({x}, {y}, {w}, {h}, c{i})\n"
                        for i, (x, y, w, h, r, g, b, a) in enumerate(rects))
        mine = "".join(f"rasFillRectImg(a, {x}.0, {y}.0, {w}.0, {h}.0, {r}, {g}, {b}, "
                       f"{a})\n"
                       for (x, y, w, h, r, g, b, a) in rects)
        _both(tmp_path, cli_mod,
              _HEAD + "img a = blankImage(24, 16)\na.drawRect(0, 0, 24, 16, bg)\n" + cairo
              + "log(a.save('cairo.png'))\n",
              "img a = blankImage(24, 16)\nrasFillRectImg(a, 0.0, 0.0, 24.0, 16.0, 32, 64, 96, 1.0)\n"
              + mine + "log(a.save('mine.png'))\n")
        worst, at, count = _worst(str(tmp_path / "cairo.png"), str(tmp_path / "mine.png"))
        assert worst == 0, (worst, at, count)

    def test_a_translucent_layer_on_a_transparent_ground_is_byte_identical(
            self, tmp_path, cli_mod):
        _both(tmp_path, cli_mod,
              _HEAD + "img a = blankImage(20, 20)\nfillAlpha(0.3)\n"
              "a.drawRect(2, 2, 10, 10, red)\na.drawRect(6, 6, 10, 10, grn)\n"
              "log(a.save('cairo.png'))\n",
              "img a = blankImage(20, 20)\n"
              "rasFillRectImg(a, 2.0, 2.0, 10.0, 10.0, 255, 0, 0, 0.3)\n"
              "rasFillRectImg(a, 6.0, 6.0, 10.0, 10.0, 0, 255, 0, 0.3)\n"
              "log(a.save('mine.png'))\n")
        # fillAlpha(0.3) and 0.3 here: the same double reaches both.
        worst, at, count = _worst(str(tmp_path / "cairo.png"), str(tmp_path / "mine.png"))
        assert worst == 0, (worst, at, count)

    def test_a_jpeg_backed_image_is_blended_exactly_too(self, tmp_path, cli_mod):
        """A decoded JPEG has no alpha channel (an RGB24 surface): the
        primitive reads its top byte as opaque and leaves it so."""
        shutil.copy(os.path.join(_FIXTURES, "gradient.jpg"), tmp_path / "gradient.jpg")
        _both(tmp_path, cli_mod,
              _HEAD + "img a = 'gradient.jpg'\nfillAlpha(0.5)\n"
              "a.drawRect(1, 1, 10, 8, red)\nlog(a.save('cairo.png'))\n",
              "img a = 'gradient.jpg'\nrasFillRectImg(a, 1.0, 1.0, 10.0, 8.0, 255, 0, 0, 0.5)\n"
              "log(a.save('mine.png'))\n")
        worst, at, count = _worst(str(tmp_path / "cairo.png"), str(tmp_path / "mine.png"))
        assert worst == 0, (worst, at, count)
        # ... and it did draw: the jpeg alone differs from the result. (A
        # loaded image saves the file's own bytes until something draws
        # on it, so the plain picture is a clip of it, a fresh surface.)
        _run(tmp_path, cli_mod, "img a = 'gradient.jpg'\nimg p = a.clip(0, 0, 16, 16)\n"
             "log(p.save('plain.png'))\n")
        assert _worst(str(tmp_path / "plain.png"), str(tmp_path / "mine.png"))[2] > 50


    def test_drawing_on_a_loaded_image_stops_it_saving_the_files_own_bytes(
            self, tmp_path, cli_mod):
        """claude.md #101: an image loaded from a file keeps the file's
        bytes for save() until something draws on it. Drawing through
        __blendRow is drawing."""
        shutil.copy(os.path.join(_FIXTURES, "gradient.jpg"), tmp_path / "gradient.jpg")
        _with_raster(tmp_path)
        assert _run(tmp_path, cli_mod, "img a = 'gradient.jpg'\nlog(a.save('same.png'))\n") == "true"
        assert _run(tmp_path, cli_mod,
                    "import raster.f\nimg a = 'gradient.jpg'\n"
                    "rasFillRectImg(a, 1.0, 1.0, 4.0, 4.0, 255, 0, 0, 1.0)\n"
                    "log(a.save('drawn.png'))\n") == "true"
        original = open(tmp_path / "gradient.jpg", "rb").read()
        assert open(tmp_path / "same.png", "rb").read() == original
        assert open(tmp_path / "drawn.png", "rb").read()[:8] == b"\x89PNG\r\n\x1a\n"


class TestWhereCoverageIsNot:
    def test_a_circles_edge_is_within_the_rasterisers_bound_and_only_the_edge(
            self, tmp_path, cli_mod):
        cx, cy, r = 25, 15, 9
        _both(tmp_path, cli_mod,
              _HEAD + f"img a = blankImage(50, 30)\na.drawRect(0, 0, 50, 30, bg)\n"
              f"fillAlpha(0.5)\na.drawCircle({cx}, {cy}, {r}, grn)\nlog(a.save('cairo.png'))\n",
              f"img a = blankImage(50, 30)\nrasFillRectImg(a, 0.0, 0.0, 50.0, 30.0, 32, 64, 96, 1.0)\n"
              f"rasFillCircleImg(a, {cx}.0, {cy}.0, {r}.0, 0, 255, 0, 0.5)\nlog(a.save('mine.png'))\n")
        (_, _, pa), (_, _, pb) = (_pixels(str(tmp_path / "cairo.png")),
                                  _pixels(str(tmp_path / "mine.png")))
        worst, differing = 0, 0
        for y in range(30):
            for x in range(50):
                d = max(abs(p - q) for p, q in zip(pa[y][x], pb[y][x]))
                if d:
                    differing += 1
                    dist = math.hypot(x + 0.5 - cx, y + 0.5 - cy)
                    assert abs(dist - r) <= 1.5, (x, y, dist, d)   # only on the rim
                    worst = max(worst, d)
        assert 0 < differing < 90, differing
        # The 27 of runtime.md's phase 4 is the bound for a fully opaque
        # fill; this one is half transparent, so it is roughly half.
        assert worst <= 27, worst


_BOTH_SCENE = """
int W = 60
int H = 40
arr[int] px = rasNewSurface(W, H)
img a = blankImage(W, H)
rasFillRect(px, W, H, 0, 0, W, H, 30, 60, 90, 255)
rasFillRectImg(a, 0.0, 0.0, 60.0, 40.0, 30, 60, 90, 1.0)

arr[float] tri = [5.0, 5.0, 30.0, 8.0, 12.0, 30.5]
arr[int] tends = [3]
rasFillPath(px, W, H, tri, tends, RAS_NONZERO, 250, 40, 40, 128)
rasFillPathImg(a, tri, tends, RAS_NONZERO, 250, 40, 40, 0.5019607843137255)

arr[float] line = [10.0, 35.0, 50.0, 12.0, 55.0, 30.0]
arr[int] lends = [3]
arr[int] closed = [0]
rasStrokePath(px, W, H, line, lends, closed, 3.5, 40, 250, 40, 200)
rasStrokePathImg(a, line, lends, closed, 3.5, 40, 250, 40, 0.7843137254901961)

arr[float] m = rasIdentity()
rasTranslate(m, 20.0, 3.0)
rasRotate(m, 20.0)
arr[float] sq = [0.0, 0.0, 12.0, 0.0, 12.0, 12.0, 0.0, 12.0]
arr[int] sends = [4]
rasFillPathT(px, W, H, sq, sends, RAS_NONZERO, rasSolid(255, 255, 0, 160), m)
rasFillPathTImg(a, sq, sends, RAS_NONZERO, 255, 255, 0, 0.6274509803921569, m)
rasStrokePathT(px, W, H, line, lends, closed, 2.0, rasSolid(255, 0, 255, 255), m)
rasStrokePathTImg(a, line, lends, closed, 2.0, 255, 0, 255, 1.0, m)

arr[float] cpts = []
arr[int] cends = []
rasCircle(cpts, cends, 45.0, 20.0, 9.0)
arr[float] mask = rasClipMask(W, H, cpts, cends, RAS_NONZERO)
rasFillPathClip(px, W, H, tri, tends, RAS_NONZERO, 0, 0, 0, 255, mask)
rasFillPathImgClip(a, tri, tends, RAS_NONZERO, 0, 0, 0, 1.0, mask)
rasStrokePathClip(px, W, H, line, lends, closed, 9.0, 255, 255, 255, 255, mask)
rasStrokePathImgClip(a, line, lends, closed, 9.0, 255, 255, 255, 1.0, mask)

img b = imageFromPixels(px, W, H)
log(b.save('arr.png'))
log(a.save('img.png'))
"""


class TestTheTwoTargetsAgree:
    def _run_scene(self, tmp_path, cli_mod):
        _with_raster(tmp_path)
        assert _run(tmp_path, cli_mod, "import raster.f\n" + _BOTH_SCENE) == "true\ntrue"
        return str(tmp_path / "arr.png"), str(tmp_path / "img.png")

    def test_fills_strokes_clips_and_transforms_differ_by_at_most_one_level(
            self, tmp_path, cli_mod):
        arr, img = self._run_scene(tmp_path, cli_mod)
        worst, at, count = _worst(arr, img)
        assert worst <= 1, (worst, at, count)

    def test_the_scene_is_not_trivially_the_same(self, tmp_path, cli_mod):
        """Every shape of the scene has to have drawn: a comparison of
        two blank images agrees perfectly."""
        arr, img = self._run_scene(tmp_path, cli_mod)
        _, _, px = _pixels(img)
        ground = (30, 60, 90)
        drawn = sum(1 for row in px for p in row if p != ground)
        assert drawn > 900, drawn
        colours = {p for row in px for p in row}
        assert len(colours) > 200      # soft edges, translucency, overlaps

    def test_the_comparison_can_fail(self, tmp_path, cli_mod):
        """One shape drawn on one target only: the difference must be
        seen, at more than the rounding bound."""
        _with_raster(tmp_path)
        src = ("import raster.f\nint W = 20\nint H = 20\narr[int] px = rasNewSurface(W, H)\n"
               "img a = blankImage(W, H)\n"
               "rasFillRect(px, W, H, 0, 0, W, H, 30, 60, 90, 255)\n"
               "rasFillRectImg(a, 0.0, 0.0, 20.0, 20.0, 30, 60, 90, 1.0)\n"
               "rasFillRectImg(a, 5.0, 5.0, 4.0, 4.0, 250, 0, 0, 1.0)\n"
               "img b = imageFromPixels(px, W, H)\nlog(b.save('arr.png'))\nlog(a.save('img.png'))\n")
        assert _run(tmp_path, cli_mod, src) == "true\ntrue"
        worst, at, count = _worst(str(tmp_path / "arr.png"), str(tmp_path / "img.png"))
        assert (worst, count) == (220, 16), (worst, at, count)


class TestThePrimitive:
    """`img.__blendRow(row, x0, x1, coverage, r, g, b, a)` on its own:
    what it clips, what it clamps, what it will not touch."""

    def _pixels_after(self, tmp_path, cli_mod, calls, w=6, h=3):
        src = (f"img a = blankImage({w}, {h})\n"
               "arr[float] full = [1.0, 1.0, 1.0, 1.0, 1.0, 1.0]\n"
               "arr[float] half = [0.5, 0.5, 0.5, 0.5, 0.5, 0.5]\n"
               "arr[float] short = [1.0, 1.0]\n"
               "arr[float] mixed = [0.0, 1.0, 0.0, 2.5, -1.0, 0.25]\n"
               + calls +
               "arr[int] px = a.toPixels()\nint i = 0\nwhile i < px.length {\n"
               "    log(px[i + 3])\n    i = i + 4\n}\n")
        out = _run(tmp_path, cli_mod, src)
        vals = [int(v) for v in out.split()]
        return [vals[r * 6:(r + 1) * 6] for r in range(3)]

    def test_it_blends_across_the_span_it_is_given(self, tmp_path, cli_mod):
        rows = self._pixels_after(tmp_path, cli_mod, "a.__blendRow(1, 1, 4, full, 9, 9, 9, 1.0)\n")
        assert rows == [[0] * 6, [0, 255, 255, 255, 0, 0], [0] * 6]

    def test_coverage_scales_the_colours_alpha(self, tmp_path, cli_mod):
        rows = self._pixels_after(tmp_path, cli_mod, "a.__blendRow(0, 0, 6, half, 9, 9, 9, 0.7843137254901961)\n")
        assert rows[0] == [100] * 6      # MUL_UN8(200, 128) = 100

    def test_zero_negative_and_over_full_coverage(self, tmp_path, cli_mod):
        rows = self._pixels_after(tmp_path, cli_mod, "a.__blendRow(0, 0, 6, mixed, 9, 9, 9, 1.0)\n")
        assert rows[0] == [0, 255, 0, 255, 0, 64]

    def test_a_row_outside_the_surface_is_ignored(self, tmp_path, cli_mod):
        rows = self._pixels_after(
            tmp_path, cli_mod,
            "a.__blendRow(-1, 0, 6, full, 9, 9, 9, 1.0)\na.__blendRow(3, 0, 6, full, 9, 9, 9, 1.0)\n"
            "a.__blendRow(99, 0, 6, full, 9, 9, 9, 1.0)\n")
        assert rows == [[0] * 6] * 3

    def test_a_span_is_clipped_to_the_surface_and_to_the_coverage(self, tmp_path, cli_mod):
        rows = self._pixels_after(
            tmp_path, cli_mod,
            "a.__blendRow(0, -5, 2, full, 9, 9, 9, 1.0)\n"       # left of the surface
            "a.__blendRow(1, 4, 99, full, 9, 9, 9, 1.0)\n"       # right of it
            "a.__blendRow(2, 0, 6, short, 9, 9, 9, 1.0)\n"       # beyond the coverage array
            "a.__blendRow(2, 5, 5, full, 9, 9, 9, 1.0)\n"        # empty
            "a.__blendRow(2, 5, 3, full, 9, 9, 9, 1.0)\n")       # backwards
        assert rows == [[255, 255, 0, 0, 0, 0], [0, 0, 0, 0, 255, 255], [255, 255, 0, 0, 0, 0]]

    def test_a_transparent_colour_draws_nothing(self, tmp_path, cli_mod):
        rows = self._pixels_after(
            tmp_path, cli_mod,
            "a.__blendRow(0, 0, 6, full, 9, 9, 9, 0.0)\na.__blendRow(1, 0, 6, full, 9, 9, 9, -0.03)\n")
        assert rows == [[0] * 6] * 3

    def test_channels_are_clamped_not_wrapped(self, tmp_path, cli_mod):
        src = ("img a = blankImage(2, 1)\narr[float] full = [1.0, 1.0]\n"
               "a.__blendRow(0, 0, 1, full, 300, -20, 999, 1.0)\n"
               "a.__blendRow(0, 1, 2, full, 1, 1, 1, 2.0)\n"
               "arr[int] px = a.toPixels()\nlog(px[0]); log(px[1]); log(px[2]); log(px[3])\n"
               "log(px[4]); log(px[5]); log(px[6]); log(px[7])\n")
        assert _run(tmp_path, cli_mod, src).split() == ["255", "0", "255", "255"] + \
            ["1", "1", "1", "255"]

    def test_it_is_rejected_at_compile_time_when_misused(self, tmp_path, cli_mod):
        from tests.conftest import compile_file_or_skip, _require_c_compiler
        from festina.errors import CompileError
        cases = {
            "img a = blankImage(2, 2)\na.__blendRow(0, 0, 2)\n": "expects 8 argument",
            "img a = blankImage(2, 2)\narr[int] c = [1]\na.__blendRow(0, 0, 1, c, 1, 1, 1, 1.0)\n":
                "argument 4 expects arr[float]",
            "img a = blankImage(2, 2)\nlog(a.__blendRow)\n": "is a method on img",
        }
        for i, (src, message) in enumerate(cases.items()):
            f = tmp_path / f"bad{i}.f"
            f.write_text(src, encoding="utf-8")
            with pytest.raises(CompileError) as caught:
                compile_file_or_skip(cli_mod, str(f), str(tmp_path / f"bad{i}"),
                                     cc=_require_c_compiler())
            assert message in str(caught.value), (message, str(caught.value))
