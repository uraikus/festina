"""runtime.md phase 7, slice 5: images as sources, without Cairo.

Copying a piece of an image (`clip`, a canvas snapshot, an image drawn onto
itself), putting one down at a whole-pixel offset, and resampling one under
a matrix (`drawImage` scaled and by region, `resize`, any image drawn under
a rotated, scaled or fractionally moved transform) are done in the runtime
now, on the surfaces' own bytes. FESTINA_CAIRO_DRAW=1 sends every one of
them back to Cairo, which is the oracle here: the same scene drawn both
ways, from sources both runs build identically (`imageFromPixels`).

What is demanded, and why:

* **Copies and whole-pixel blits are byte exact** -- Cairo did them with a
  row copy and pixman's OVER, and so do we.
* **Interpolated pixels are within a bound.** Cairo's GOOD filter is
  bilinear above a scale of 0.75 and a box average at or below it, and
  that is reproduced (the rule was measured, not read; see runtime.md,
  "Slice 5"). The positions are computed from the inverse matrix in
  doubles where pixman rounds the matrix to 16.16 and shifts by the
  paint's extents, which moves a sample by at most 1/128 of a pixel:
  at most two levels of 255 on an interpolated channel. Scales whose
  arithmetic is exact in 16.16 (2, 4) agree exactly.
* **The pictures must be pictures:** each scene is checked for being busy,
  or a comparison of two blank canvases would pass.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.test_draw_component import _premul   # noqa: E402

#: A 17 x 13 source of every kind of pixel: transparent, translucent,
#: opaque, saturated and grey, from a fixed generator, so both runs and
#: every platform build the same image.
_SOURCE = """color white = '#ffffff'
color ink = '#203040'
int SEED = 20241
int func rnd(n:int) {
    SEED = ((SEED * 1103515245) + 12345) % 2147483648
    return Math.floorDiv(SEED, 65536) % n
}
arr[int] func buildPixels(w:int, h:int) {
    arr[int] px = []
    int i = 0
    while i < w * h {
        int kind = rnd(8)
        int a = 255
        if kind == 0 { a = 0 }
        if kind == 1 { a = 1 + rnd(254) }
        if kind == 2 { a = 128 }
        px.push(rnd(256))
        px.push(rnd(256))
        px.push(rnd(256))
        px.push(a)
        i = i + 1
    }
    return px
}
img src = imageFromPixels(buildPixels(17, 13), 17, 13)
"""

_W, _H = 200, 150


def _scene(compile_and_run, tmp_path, body, name, env=None, on_img=False):
    """Run `body` (Festina statements drawing `src` onto the target) and
    return the premultiplied picture. The target is the canvas, or an img
    `t` for on_img; the picture is saved as `name`."""
    if on_img:
        program = (_SOURCE + f"img t = blankImage({_W}, {_H})\nt.drawRect(0, 0, {_W}, {_H}, white)\n"
                   + body + f"log(t.save('{name}'))\n")
    else:
        program = (_SOURCE + f"clearCanvas()\ndrawRect(0, 0, {_W}, {_H}, white)\n"
                   + body + f"log(saveCanvas('{name}'))\n")
    result = compile_and_run(program, env=env)
    assert result.returncode == 0 and result.stdout.split() == ["true"], result.stderr
    return _premul(str(tmp_path / name))


def _both(compile_and_run, tmp_path, body, on_img=False):
    ours = _scene(compile_and_run, tmp_path, body, "ours.png", on_img=on_img)
    cairo = _scene(compile_and_run, tmp_path, body, "cairo.png",
                   env={"FESTINA_CAIRO_DRAW": "1"}, on_img=on_img)
    return ours, cairo


def _diff(a, b):
    (w, h, pa), (_, _, pb) = a, b
    worst = 0
    count = 0
    total = 0
    for y in range(h):
        for x in range(w):
            d = max(abs(p - q) for p, q in zip(pa[y][x], pb[y][x]))
            if d:
                count += 1
                total += d
                worst = max(worst, d)
    return worst, count, total


def _busy(pic):
    w, h, px = pic
    colours = {px[y][x] for y in range(h) for x in range(w)}
    return len(colours)


# ---- whole-pixel blits ---------------------------------------------------

_BLITS = """fillAlpha(1.0)
{T}drawImage(src, 10, 10)
{T}drawImage(src, -5, 40)
{T}drawImage(src, 190, 60)
{T}drawImage(src, 20, 140)
fillAlpha(0.5)
{T}drawImage(src, 40, 10)
fillAlpha(0.3)
{T}drawImage(src, 40, 30)
fillAlpha(0.77)
{T}drawImage(src, 70, 20)
fillAlpha(1.0)
{T}translate(120, 30)
{T}drawImage(src, 0, 0)
{T}translate(-3, 40)
fillAlpha(0.6)
{T}drawImage(src, 0, 0)
fillAlpha(1.0)
{T}resetTransform()
"""


@pytest.mark.parametrize("on_img", [False, True], ids=["canvas", "img"])
def test_whole_pixel_blits_are_byte_exact(compile_and_run, tmp_path, monkeypatch, on_img):
    monkeypatch.delenv("DISPLAY", raising=False)
    body = _BLITS.format(T="t." if on_img else "")
    ours, cairo = _both(compile_and_run, tmp_path, body, on_img)
    worst, count, _ = _diff(ours, cairo)
    assert _busy(ours) > 300
    assert worst == 0, (worst, count)


# ---- copies -------------------------------------------------------------

_COPIES = """{T}drawImage(src, 5, 5)
img a = src.clip(3, 2, 9, 8)
{T}drawImage(a, 40, 5)
img b = src.clip(-4, -3, 12, 10)
{T}drawImage(b, 70, 5)
img c = src.clip(10, 8, 20, 20)
{T}drawImage(c, 100, 5)
img d = src.clip(40, 40, 5, 5)
{T}drawImage(d, 140, 5)
img e = src.clip(0, 0, 17, 13)
{T}drawImage(e, 160, 5)
{T}drawImage(src, 10, 60)
{T}drawImage({T}clip(8, 58, 40, 24), 100, 60)
"""


def test_copies_are_byte_exact(compile_and_run, tmp_path, monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    body = _COPIES.replace("{T}clip", "src.clip").replace("{T}", "")
    ours, cairo = _both(compile_and_run, tmp_path, body)
    assert _busy(ours) > 150
    assert _diff(ours, cairo)[0] == 0


def test_a_canvas_snapshot_is_byte_exact(compile_and_run, tmp_path, monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    body = ("drawImage(src, 10, 10)\nfillAlpha(0.4)\ndrawImage(src, 14, 12)\nfillAlpha(1.0)\n"
            "img snap = saveCanvas()\nclearCanvas()\ndrawRect(0, 0, 200, 150, ink)\n"
            "drawImage(snap, 30, 20)\n")
    ours, cairo = _both(compile_and_run, tmp_path, body)
    assert _busy(ours) > 100
    assert _diff(ours, cairo)[0] == 0


def test_an_image_drawn_onto_itself_is_the_copy_first_result(compile_and_run, tmp_path, monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    body = ("img a = src.clip(0, 0, 17, 13)\n"
            "a.drawImage(a, 5, 4)\na.drawImage(a, -3, 2)\n"
            "fillAlpha(0.5)\na.drawImage(a, 2, -2)\nfillAlpha(1.0)\n"
            "drawImage(a, 20, 20)\n")
    ours, cairo = _both(compile_and_run, tmp_path, body)
    assert _busy(ours) > 100
    assert _diff(ours, cairo)[0] == 0


# ---- resampling ----------------------------------------------------------

#: Interpolated pixels agree with Cairo's to this many levels of 255 (see
#: the module docstring): measured worst, over these scenes, 4.
BOUND = 4

#: name -> (body, worst level the scene may differ by). {T} is "t." on an
#: img and nothing on the canvas. A scale of exactly 2 or 4 is exact in
#: pixman's 16.16 arithmetic too, so those are demanded exact.
_RESAMPLED = {
    "up2x":  ("{T}drawImage(src, 10, 10, 34, 26)\n", 0),
    "up4x":  ("{T}drawImage(src, 10, 10, 68, 52)\n", 0),
    "up3x":  ("{T}drawImage(src, 10, 10, 51, 39)\n", BOUND),
    "up1.5": ("{T}drawImage(src, 10, 10, 26, 20)\n", BOUND),
    "up_odd": ("{T}drawImage(src, 10, 10, 41, 29)\n", BOUND),
    "alpha_up": ("fillAlpha(0.6)\n{T}drawImage(src, 10, 10, 41, 29)\nfillAlpha(1.0)\n", BOUND),
    # above 0.75 is still bilinear; at or below it is a box average
    "mild_down": ("{T}drawImage(src, 10, 10, 14, 11)\n", BOUND),
    "down_half": ("{T}drawImage(src, 10, 10, 8, 6)\n", BOUND),
    "down_0.6": ("{T}drawImage(src, 10, 10, 10, 8)\n", BOUND),
    "down_quarter": ("{T}drawImage(src, 10, 10, 4, 3)\n", BOUND),
    "down_to_a_pixel": ("{T}drawImage(src, 10, 10, 1, 1)\n{T}drawImage(src, 20, 10, 2, 1)\n", BOUND),
    # each axis chooses for itself
    "x_up_y_down": ("{T}drawImage(src, 10, 10, 40, 6)\n", BOUND),
    "x_down_y_up": ("{T}drawImage(src, 10, 10, 8, 40)\n", BOUND),
    # a scale-only matrix whose two factors multiply to 1 is sampled nearest
    "nearest_2_by_half": ("{T}scale(2.0, 0.5)\n{T}drawImage(src, 5, 20)\n{T}resetTransform()\n", BOUND),
    "nearest_half_by_2": ("{T}scale(0.5, 2.0)\n{T}drawImage(src, 20, 5)\n{T}resetTransform()\n", BOUND),
    "canvas_scale_2.5": ("{T}scale(2.5, 2.5)\n{T}drawImage(src, 3, 3)\n{T}resetTransform()\n", BOUND),
    "mirrored": ("{T}translate(60, 10)\n{T}scale(-1.0, 1.0)\n{T}drawImage(src, 0, 0)\n{T}resetTransform()\n", BOUND),
    "rotate_30": ("{T}translate(60, 30)\n{T}rotate(30.0)\n{T}drawImage(src, 0, 0)\n{T}resetTransform()\n", BOUND),
    "rotate_90": ("{T}translate(60, 30)\n{T}rotate(90.0)\n{T}drawImage(src, 0, 0)\n{T}resetTransform()\n", BOUND),
    "rotate_scale_up": ("{T}translate(80, 20)\n{T}rotate(45.0)\n{T}scale(1.7, 1.7)\n{T}drawImage(src, 0, 0)\n{T}resetTransform()\n", BOUND),
    "rotate_scale_down": ("{T}translate(80, 40)\n{T}rotate(20.0)\n{T}scale(0.6, 0.6)\n{T}drawImage(src, 0, 0)\n{T}resetTransform()\n", BOUND),
    "partly_off_canvas": ("{T}drawImage(src, -20, -15, 51, 39)\n{T}drawImage(src, 170, 130, 51, 39)\n", BOUND),
    "entirely_off_canvas": ("{T}drawImage(src, -200, -150, 51, 39)\n{T}drawImage(src, 900, 10, 20, 20)\n"
                            "{T}drawImage(src, 10, 10, 0, 5)\n{T}drawImage(src, 10, 10, 5, 0)\n", 0),
}


@pytest.mark.parametrize("on_img", [False, True], ids=["canvas", "img"])
@pytest.mark.parametrize("name", list(_RESAMPLED))
def test_resampling_agrees_with_cairo(compile_and_run, tmp_path, monkeypatch, name, on_img):
    monkeypatch.delenv("DISPLAY", raising=False)
    template, bound = _RESAMPLED[name]
    ours, cairo = _both(compile_and_run, tmp_path, template.format(T="t." if on_img else ""), on_img)
    worst, count, total = _diff(ours, cairo)
    assert worst <= bound, (name, worst, count)
    if count:
        assert total / count <= 2.0, (name, total / count)   # mostly one or two levels, not a few big ones
    if name != "entirely_off_canvas":
        assert _busy(ours) > (3 if name == "down_to_a_pixel" else 10)


_REGIONS = """{T}drawImage(src, 3, 2, 8, 6, 20, 20, 40, 30)
{T}drawImage(src, 0, 0, 17, 13, 80, 20, 8, 6)
{T}drawImage(src, 10, 8, 14, 12, 100, 20, 42, 36)
{T}drawImage(src, -4, -3, 12, 10, 20, 80, 36, 30)
fillAlpha(0.5)
{T}drawImage(src, 2, 2, 5, 5, 80, 80, 30, 30)
fillAlpha(1.0)
{T}translate(7, 5)
{T}drawImage(src, 1, 1, 9, 7, 120, 80, 27, 21)
{T}resetTransform()
{T}scale(2.0, 2.0)
{T}drawImage(src, 3, 3, 6, 6, 60, 55, 12, 12)
{T}resetTransform()
"""


def test_drawing_a_region_agrees_with_cairo(compile_and_run, tmp_path, monkeypatch):
    """The region form clips to the destination rectangle; under a transform
    that leaves it on whole pixels that is a pixel rectangle (everything
    here). The source beyond the sub-rectangle still takes part in the
    interpolation at its edge, as it does in Cairo."""
    monkeypatch.delenv("DISPLAY", raising=False)
    ours, cairo = _both(compile_and_run, tmp_path, _REGIONS.format(T=""))
    worst, count, total = _diff(ours, cairo)
    assert worst <= BOUND, (worst, count)
    assert _busy(ours) > 800


def test_resize_agrees_with_cairo(compile_and_run, tmp_path, monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    body = ""
    for i, (w, h) in enumerate(((34, 26), (51, 39), (13, 10), (8, 6), (5, 20), (17, 13), (1, 1), (60, 7))):
        body += (f"img r{i} = src.clip(0, 0, 17, 13)\nr{i}.resize({w}, {h})\n"
                 f"drawImage(r{i}, {5 + 24 * (i % 7)}, {5 + 40 * (i // 7)})\n"
                 f"log(r{i}.width)\nlog(r{i}.height)\n")
    program = (_SOURCE + "clearCanvas()\ndrawRect(0, 0, 200, 150, white)\n" + body
               + "log(saveCanvas('o.png'))\n")
    results = {}
    for tag, env in (("ours", None), ("cairo", {"FESTINA_CAIRO_DRAW": "1"})):
        r = compile_and_run(program, env=env)
        assert r.returncode == 0, r.stderr
        results[tag] = (r.stdout.split(), _premul(str(tmp_path / "o.png")))
    assert results["ours"][0] == results["cairo"][0]
    worst, count, _ = _diff(results["ours"][1], results["cairo"][1])
    assert worst <= BOUND, (worst, count)
    assert _busy(results["ours"][1]) > 400


def test_a_source_with_no_alpha_channel_draws_opaque(compile_and_run, tmp_path, monkeypatch):
    """A decoded JPEG is an RGB24 surface: its unused byte is not alpha.
    Whole-pixel, scaled and copied, it must match Cairo."""
    monkeypatch.delenv("DISPLAY", raising=False)
    fixtures = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "jpeg")
    jpg = sorted(f for f in os.listdir(fixtures) if f.endswith(".jpg"))[0]
    import shutil
    shutil.copy(os.path.join(fixtures, jpg), tmp_path / "photo.jpg")
    body = ("img photo = 'photo.jpg'\n"
            "drawImage(photo, 5, 5)\nfillAlpha(0.5)\ndrawImage(photo, 60, 60)\nfillAlpha(1.0)\n"
            "drawImage(photo, 90, 10, 50, 40)\ndrawImage(photo, 10, 90, 20, 15)\n"
            "img piece = photo.clip(4, 4, 30, 30)\ndrawImage(piece, 150, 100)\n")
    program = (_SOURCE + "clearCanvas()\ndrawRect(0, 0, 200, 150, white)\n" + body
               + "log(saveCanvas('o.png'))\n")
    pics = {}
    for tag, env in (("ours", None), ("cairo", {"FESTINA_CAIRO_DRAW": "1"})):
        r = compile_and_run(program, env=env)
        assert r.returncode == 0, r.stderr
        pics[tag] = _premul(str(tmp_path / "o.png"))
    worst, count, _ = _diff(pics["ours"], pics["cairo"])
    assert worst <= BOUND, (worst, count)
    assert _busy(pics["ours"]) > 400
