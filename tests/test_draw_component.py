"""runtime.md phase 7, slice 4: the graphics runtime draws through draw.f.

Where a draw call used to build a Cairo context, a path and a fill, it now
asks draw.f (runtime/festina/draw.f, over raster.f): rectangles and circles
with their borders, a fill that is a gradient, pixels, the clearing calls,
and paths built with beginPath -- on the canvas and on an img, under any
transform and at any fillAlpha. FESTINA_CAIRO_DRAW=1 sends every call back
to Cairo, which is the oracle here: the same scene drawn both ways.

What is demanded, and why it is not byte identity (runtime.md phase 7,
decision 4: the edge pixels move):

* **Flat regions are exact.** A pixel that Cairo's own picture shows as
  part of a flat area -- it and its four neighbours the same premultiplied
  colour -- must be the same here, within one level for rounding. Interiors
  of fills, the bodies of strokes and untouched ground are all in that set.
* **Everything else is within a bound**, measured, on the premultiplied
  values (a straight RGBA picture at alpha 2 has a meaningless colour).
  The bound is 48 of 255; the scene below measures 36, at edges, where the
  two rasterisers sample differently.
* **The picture has to have been drawn by draw.f at all**: the two runs
  must differ (Cairo and raster.f never agree on every edge), or the
  comparison could be of Cairo with itself.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.test_codegen import _png_raw   # noqa: E402

BOUND = 48

_HEAD = """color white = '#ffffff'
color ink = '#203040'
color red = '#c83232'
color grn = '#32c832'
color blu = '#3232c8'
color yel = '#e0c020'
color none = 'none'
"""

# One scene, drawn onto the canvas (T = "") or an img (T = "a.").
_SCENE = """{T}drawRect(0, 0, 220, 160, white)
fillStyle(red)
borderColor(ink)
lineWidth(3)
{T}drawRect(10, 10, 50, 30)
fillAlpha(0.5)
{T}drawRect(40, 25, 50, 30)
{T}drawCircle(110, 35, 22)
fillAlpha(1.0)
borderColor(none)
{T}drawCircle(160, 35, 25)
borderColor(ink)
lineWidth(6)
{T}drawCircle(200, 100, 14, blu, grn)
{T}drawRect(150, 70, 30, 40, yel, ink)
fillLinearGradient(10, 70, red, 70, 110, blu)
{T}drawRect(10, 70, 60, 40)
fillRadialGradient(100, 120, 30, yel, grn)
{T}drawCircle(100, 120, 28)
fillStyle(ink)
{T}drawPixel(5, 150)
{T}drawPixel(6, 150, red)
{T}translate(120, 20)
{T}rotate(25.0)
fillAlpha(0.7)
fillStyle(blu)
borderColor(red)
lineWidth(2)
{T}drawRect(0, 40, 40, 25)
{T}drawCircle(30, 90, 12)
{T}drawPixel(2, 2)
fillLinearGradient(0, 0, yel, 40, 0, blu)
{T}drawRect(0, 70, 40, 12)
{T}resetTransform()
{T}scale(0.7, 1.4)
fillAlpha(1.0)
fillStyle(grn)
{T}drawCircle(40, 40, 15)
{T}drawRect(120, 60, 30, 20)
fillAlpha(0.6)
fillLinearGradient(130, 10, red, 190, 10, blu)
{T}drawRect(130, 10, 60, 12)
fillRadialGradient(60, 90, 20, yel, blu)
{T}drawCircle(60, 90, 18)
{T}resetTransform()
{T}clearRect(75, 120, 14, 14)
{T}clearCircle(30, 135, 9)
{T}clearPixel(20, 135)
{T}translate(5, 7)
{T}rotate(12.0)
{T}clearRect(150, 120, 20, 10)
{T}clearCircle(180, 130, 6)
{T}clearPixel(190, 131)
"""

# Paths exist on the canvas only.
_PATHS = """resetTransform()
fillAlpha(0.8)
fillStyle(yel)
borderColor(ink)
lineWidth(3)
beginPath()
moveTo(180, 10)
lineTo(215, 14)
curveTo(225, 30, 212, 45, 190, 50)
lineTo(176, 38)
closePath()
fillPath()
beginPath()
moveTo(150, 55)
curveTo(165, 45, 185, 65, 205, 55)
lineTo(208, 68)
strokePath()
translate(10, 5)
scale(1.0, 0.6)
beginPath()
moveTo(20, 100)
lineTo(60, 100)
lineTo(40, 140)
closePath()
fillPath()
beginPath()
moveTo(70, 100)
lineTo(110, 100)
lineTo(90, 140)
closePath()
strokePath()
beginPath()
moveTo(120, 100)
lineTo(160, 100)
lineTo(140, 140)
strokePath()
"""


def _program(png_canvas, png_layer):
    return (_HEAD + "clearCanvas()\n" + _SCENE.format(T="") + _PATHS
            + "img a = blankImage(220, 160)\n" + _SCENE.format(T="a.")
            + f"log(a.save('{png_layer}'))\nlog(saveCanvas('{png_canvas}'))\n")


def _draw(compile_and_run, monkeypatch, canvas, layer, cairo):
    monkeypatch.delenv("DISPLAY", raising=False)
    env = {"FESTINA_CAIRO_DRAW": "1"} if cairo else {}
    result = compile_and_run(_program(canvas, layer), env=env)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["true", "true"], result.stdout


def _premul(path):
    w, h, stride, bpp, out = _png_raw(path)
    assert bpp in (3, 4)   # a picture with no transparency saves as RGB
    px = []
    for y in range(h):
        row = []
        for x in range(w):
            o = y * stride + x * bpp
            r, g, b = out[o:o + 3]
            a = out[o + 3] if bpp == 4 else 255
            row.append(((r * a + 127) // 255, (g * a + 127) // 255, (b * a + 127) // 255, a))
        px.append(row)
    return w, h, px


def _compare(ours, cairo):
    (w, h, a), (_, _, b) = _premul(ours), _premul(cairo)
    worst = 0
    differing = 0
    flat_bad = []
    for y in range(h):
        for x in range(w):
            d = max(abs(p - q) for p, q in zip(a[y][x], b[y][x]))
            if d:
                differing += 1
                worst = max(worst, d)
            here = b[y][x]
            around = [b[yy][xx] for yy, xx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1))
                      if 0 <= yy < h and 0 <= xx < w]
            if d > 1 and all(n == here for n in around):
                flat_bad.append((x, y, a[y][x], here))
    return worst, differing, flat_bad


@pytest.fixture(scope="module")
def pictures(tmp_path_factory):
    # A module fixture would need the function-scoped compile_and_run; each
    # test draws its own pair instead (a few seconds each).
    return None


def test_a_scene_of_every_kind_of_call_matches_cairo_where_it_can(compile_and_run, tmp_path,
                                                                    monkeypatch):
    _draw(compile_and_run, monkeypatch, "o_c.png", "o_l.png", cairo=False)
    _draw(compile_and_run, monkeypatch, "c_c.png", "c_l.png", cairo=True)
    for kind in ("c", "l"):
        worst, differing, flat_bad = _compare(str(tmp_path / f"o_{kind}.png"),
                                              str(tmp_path / f"c_{kind}.png"))
        assert flat_bad == [], (kind, len(flat_bad), flat_bad[:6])
        assert worst <= BOUND, (kind, worst)
        # draw.f really drew it: a rasteriser that agreed with Cairo on
        # every edge of every curve would be a coincidence, and identical
        # files mean the env var or the hooks did nothing.
        assert differing > 200, (kind, differing)


def test_the_scene_is_a_real_one(compile_and_run, tmp_path, monkeypatch):
    _draw(compile_and_run, monkeypatch, "o_c.png", "o_l.png", cairo=False)
    w, h, px = _premul(str(tmp_path / "o_c.png"))
    colours = {p for row in px for p in row}
    assert len(colours) > 600, len(colours)
    drawn = sum(1 for row in px for p in row if p != (0, 0, 0, 0) and p != (255, 255, 255, 255))
    assert drawn > 5000, drawn


def test_a_stamped_circle_is_the_circle_draw_f_draws(compile_and_run, tmp_path, monkeypatch):
    """The runtime's direct circle stamp caches one coverage per radius
    (claude.md #104, #240) and blends it by hand; the cache is made by
    draw.f now, by the rasteriser every other circle is drawn with. So at
    an integer centre the stamp and draw.f's own drawing of the circle
    (FESTINA_NO_DIRECT_FILL=1 sends every call there) agree -- to one
    level, since the stamp moves a coverage computed at one position to
    another and the arithmetic of the two is not bit for bit the same."""
    monkeypatch.delenv("DISPLAY", raising=False)
    shapes = "fillAlpha(1.0)\nfillStyle(red)\n" + "".join(
        f"{{T}}drawCircle({30 + 52 * (r % 8)}, {30 + 52 * (r // 8)}, {r})\n"
        for r in (1, 2, 3, 4, 5, 7, 9, 12, 15, 19, 24, 30, 6, 8, 10, 13)) \
        + "fillAlpha(0.5)\nfillStyle(blu)\n" + "".join(
            f"{{T}}drawCircle({30 + 52 * (r % 8)}, {130 + 52 * (r // 8)}, {r})\n"
            for r in (1, 2, 3, 4, 5, 7, 9, 12, 15, 19, 24, 30, 6, 8, 10, 13))
    program = (_HEAD + "clearCanvas()\n" + shapes.format(T="")
               + "img a = blankImage(430, 240)\n" + shapes.format(T="a.")
               + "log(a.save('layer.png'))\nlog(saveCanvas('canvas.png'))\n")
    # the harness writes into one directory, so run the two modes in turn
    # and move the pictures aside
    for name, env in (("d", {}), ("n", {"FESTINA_NO_DIRECT_FILL": "1"})):
        result = compile_and_run(program, env=env)
        assert result.returncode == 0 and result.stdout.split() == ["true", "true"], result.stderr
        for kind in ("layer", "canvas"):
            os.replace(tmp_path / f"{kind}.png", tmp_path / f"{name}_{kind}.png")
    for kind in ("layer", "canvas"):
        (w, h, a), (_, _, b) = (_premul(str(tmp_path / f"d_{kind}.png")),
                                _premul(str(tmp_path / f"n_{kind}.png")))
        worst = max(max(abs(p - q) for p, q in zip(a[y][x], b[y][x]))
                    for y in range(h) for x in range(w))
        assert worst <= 1, (kind, worst)
        drawn = sum(1 for row in a for p in row if p[3])
        assert drawn > 8000, drawn


def test_whole_pixel_boxes_with_borders_are_cairos_to_one_level(compile_and_run, tmp_path,
                                                                 monkeypatch):
    """A rectangle that is only scaled and moved is drawn analytically (a
    box, and its border the difference of two boxes) rather than through the
    scanline path. At whole-pixel coordinates there is one right answer --
    coverage 0, 1, or 1/2 at the edge of an odd-width line -- and Cairo
    draws it, so these are compared to Cairo at one level (the rounding of
    a translucent blend): every line width from 1 to 6, translucent and
    opaque, negative extents, and a border so wide the inner box
    disappears."""
    monkeypatch.delenv("DISPLAY", raising=False)
    body = ""
    for i, lw in enumerate((1, 2, 3, 4, 5, 6)):
        body += (f"lineWidth({lw})\nfillAlpha({1.0 if i % 2 == 0 else 0.5})\n"
                 f"{{T}}drawRect({8 + 40 * i}, 8, 30, 24, red)\n"
                 f"{{T}}drawRect({8 + 40 * i}, 40, 9, 9, red)\n")
    body += ("lineWidth(12)\nfillAlpha(1.0)\n{T}drawRect(10, 70, 8, 8, red)\n"
             "lineWidth(2)\n{T}drawRect(70, 90, -20, -15, red)\n"
             "fillAlpha(0.6)\n{T}translate(5, 3)\n{T}drawRect(100, 70, 30, 20, red)\n"
             "{T}resetTransform()\n{T}scale(2.0, 1.0)\n{T}drawRect(60, 100, 20, 10, red)\n"
             "fillAlpha(1.0)\n{T}resetTransform()\n")
    head = _HEAD + "borderColor(ink)\n"
    program = (head + "clearCanvas()\nfillStyle(white)\n" + "drawRect(0, 0, 260, 130)\nfillStyle(red)\n"
               + body.format(T="") + "img a = blankImage(260, 130)\n"
               + "a.drawRect(0, 0, 260, 130, white)\nfillStyle(red)\n" + body.format(T="a.")
               + "log(a.save('layer.png'))\nlog(saveCanvas('canvas.png'))\n")
    for name, env in (("o", {}), ("c", {"FESTINA_CAIRO_DRAW": "1"})):
        result = compile_and_run(program, env=env)
        assert result.returncode == 0 and result.stdout.split() == ["true", "true"], result.stderr
        for kind in ("layer", "canvas"):
            os.replace(tmp_path / f"{kind}.png", tmp_path / f"{name}_{kind}.png")
    for kind in ("layer", "canvas"):
        worst, differing, _ = _compare(str(tmp_path / f"o_{kind}.png"), str(tmp_path / f"c_{kind}.png"))
        assert worst <= 1, (kind, worst, differing)
        w, h, px = _premul(str(tmp_path / f"o_{kind}.png"))
        assert sum(1 for row in px for p in row if p[:3] != (255, 255, 255) and p[3]) > 3000


def test_rotated_borders_and_thin_rectangles_are_cairos_picture(compile_and_run, tmp_path,
                                                                 monkeypatch):
    """A bordered rectangle under any matrix is drawn as the ring between
    the rectangle grown by half the pen and shrunk by it, each corner
    mitred, filled with the exact area -- unless the pen leaves no hole, or
    the rectangle has no width, where the general stroker is used. Rotated,
    skewed and unevenly scaled, wide pens and thin rectangles, against
    Cairo's own stroke of the same rectangle: the edges' coverage differs
    a little (exact against sampled), the picture does not."""
    monkeypatch.delenv("DISPLAY", raising=False)
    body = ("fillAlpha(1.0)\nlineWidth(3)\n{T}translate(40, 40)\n{T}rotate(25.0)\n"
            "{T}drawRect(0, 0, 50, 30, red)\n{T}resetTransform()\n"
            "lineWidth(7)\nfillAlpha(0.6)\n{T}translate(130, 30)\n{T}rotate(70.0)\n"
            "{T}drawRect(-10, -8, 40, 22, red)\n{T}resetTransform()\n"
            "lineWidth(2)\nfillAlpha(1.0)\n{T}translate(30, 100)\n{T}scale(2.0, 0.6)\n{T}rotate(15.0)\n"
            "{T}drawRect(0, 0, 40, 30, red)\n{T}resetTransform()\n"
            "lineWidth(5)\n{T}translate(120, 90)\n{T}rotate(40.0)\n"
            "{T}drawRect(0, 0, 4, 40, red)\n{T}drawRect(20, 0, 5, 5, red)\n{T}drawRect(40, 0, 30, 0, red)\n"
            "{T}resetTransform()\n")
    program = (_HEAD + "borderColor(ink)\nclearCanvas()\nfillStyle(white)\ndrawRect(0, 0, 220, 160)\nfillStyle(red)\n"
               + body.format(T="") + "img a = blankImage(220, 160)\n"
               + "a.drawRect(0, 0, 220, 160, white)\nfillStyle(red)\n" + body.format(T="a.")
               + "log(a.save('layer.png'))\nlog(saveCanvas('canvas.png'))\n")
    for name, env in (("o", {}), ("c", {"FESTINA_CAIRO_DRAW": "1"})):
        result = compile_and_run(program, env=env)
        assert result.returncode == 0 and result.stdout.split() == ["true", "true"], result.stderr
        for kind in ("layer", "canvas"):
            os.replace(tmp_path / f"{kind}.png", tmp_path / f"{name}_{kind}.png")
    for kind in ("layer", "canvas"):
        worst, differing, flat_bad = _compare(str(tmp_path / f"o_{kind}.png"), str(tmp_path / f"c_{kind}.png"))
        assert worst <= 24, (kind, worst, differing)
        assert not flat_bad, (kind, flat_bad[:5])
