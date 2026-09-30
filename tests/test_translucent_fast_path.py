"""runtime.md phase 7, decision 5: the solid-fill fast path takes a
translucent colour too.

claude.md #240's direct-pixel path drew an opaque flat colour at an
integer position straight into the surface, and every draw under
`fillAlpha(x)` with x below 1 went through Cairo -- 1.3 us for a
translucent rectangle and 30 us for a translucent circle, against 0.1
and 2.6 for the opaque ones. The path is extended to a translucent solid
colour: the source is reduced the way Cairo reduces it (alpha, and each
channel times alpha, in doubles, to a 16-bit short and its high byte)
and goes OVER the pixels with pixman's arithmetic, and a circle's
cached coverage scales it first.

The oracle is the one #240 used: the same scene drawn with the fast path
on and with FESTINA_NO_DIRECT_FILL=1 FESTINA_CAIRO_DRAW=1, which send every call to Cairo,
and the two saved images must be byte-identical. Alphas are ones that do
not round trivially (0.4 and 0.7 are not a whole number of 255ths), so a
premultiply from a rounded byte would show.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.test_codegen import _SOLID_FILL_HEAD, _png_diff   # noqa: E402

ALPHAS = (0.05, 0.3, 0.4, 0.5, 0.7, 0.85, 0.995)

_SCENE = """fillStyle(30, 60, 90)
{T}drawRect(0, 0, 400, 300)
fillStyle(200, 200, 40)
{T}drawRect(0, 120, 400, 60)
"""

_ONE_ALPHA = """fillAlpha({a})
fillStyle({r}, {g}, {b})
{T}drawRect({x}, {y}, 40, 30)
{T}drawCircle({x} + 20, {y} + 60, {rad})
{T}drawPixel({x} + 3, {y} + 3)
{T}drawRect({x} + 50, {y}, 20, 20, red)
{T}drawCircle({x} + 60, {y} + 60, {rad}, green)
{T}drawRect({x} + 50, {y} + 30, 10, 10, blue, none)
{T}drawPixel({x} + 5, {y} + 5, white)
{T}drawRect({x} - 7, {y} - 5, 60, 12)
"""


def _program(png_canvas, png_layer):
    body = ""
    for i, a in enumerate(ALPHAS):
        r, g, b = (37 * i + 20) % 256, (91 * i + 5) % 256, (149 * i + 60) % 256
        body += _ONE_ALPHA.format(a=a, r=r, g=g, b=b, x=8 + 55 * i, y=10 + 9 * (i % 3),
                                  rad=4 + i * 2, T="{T}")
    body += ("fillAlpha(0.4)\nfillStyle(250, 20, 20)\n{T}drawRect(-20, -20, 60, 60)\n"
             "{T}drawRect(380, 280, 50, 50)\n{T}drawRect(50, 50, -30, -30)\n"
             "{T}drawCircle(-3, -3, 12)\n{T}drawCircle(398, 298, 30)\n"
             # overlapping translucent shapes stack
             "{T}drawRect(100, 200, 80, 50)\n{T}drawRect(140, 220, 80, 50)\n"
             "{T}drawCircle(180, 240, 30)\n{T}drawCircle(200, 250, 30)\n")
    scene = _SCENE + body
    return (_SOLID_FILL_HEAD + "clearCanvas()\n" + scene.format(T="")
            + "img layer = blankImage(400, 300)\n" + scene.format(T="layer.")
            + f"log(layer.save('{png_layer}'))\nlog(saveCanvas('{png_canvas}'))\n")


def _draw(compile_and_run, monkeypatch, canvas, layer, direct):
    monkeypatch.delenv("DISPLAY", raising=False)
    env = {"FESTINA_CAIRO_DRAW": "1"}
    if not direct:
        env["FESTINA_NO_DIRECT_FILL"] = "1"
    result = compile_and_run(_program(canvas, layer), env=env)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["true", "true"], result.stdout


def test_a_translucent_scene_is_byte_identical_to_cairos(compile_and_run, tmp_path, monkeypatch):
    _draw(compile_and_run, monkeypatch, "fast_c.png", "fast_l.png", direct=True)
    _draw(compile_and_run, monkeypatch, "slow_c.png", "slow_l.png", direct=False)
    for kind in ("c", "l"):
        diffs = _png_diff(str(tmp_path / f"fast_{kind}.png"), str(tmp_path / f"slow_{kind}.png"))
        assert diffs == [], (kind, len(diffs), diffs[:8])


def test_the_scene_really_draws_translucent_colours(compile_and_run, tmp_path, monkeypatch):
    """A comparison of two blank images passes: count the distinct
    colours, which translucent stacking and soft circle edges make
    many."""
    _draw(compile_and_run, monkeypatch, "fast_c.png", "fast_l.png", direct=True)
    from tests.test_codegen import _png_pixels
    w, h, px = _png_pixels(str(tmp_path / "fast_c.png"))
    colours = {px[i:i + 4] for i in range(0, len(px), 4)}
    assert len(colours) > 400, len(colours)


def test_an_alpha_cairo_calls_opaque_is_drawn_as_opaque(compile_and_run, tmp_path, monkeypatch):
    """Cairo treats an alpha whose 16-bit value reaches 0xff00 (0.9961 and
    up) as opaque and composites the colour unscaled. 0.999 must draw
    what 1.0 draws, not the colour times 0.999 -- a channel one lower.
    (In bytes it does, with no special case: alpha is 255 from 0.9961 and
    a colour times such an alpha does not lose a byte. This holds that.)"""
    monkeypatch.delenv("DISPLAY", raising=False)
    shape = ("color c = '#f227ba'\nfillAlpha({a})\nimg a = blankImage(40, 40)\n"
             "a.drawRect(2, 2, 20, 20, c)\na.drawCircle(25, 25, 9, c)\n"
             "log(a.save('{n}.png'))\n")
    for a, n in ((1.0, "one"), (0.999, "almost"), (0.9965, "edge"), (0.995, "below")):
        result = compile_and_run(shape.format(a=a, n=n))
        assert result.returncode == 0 and result.stdout.split() == ["true"], result.stderr
    assert _png_diff(str(tmp_path / "one.png"), str(tmp_path / "almost.png")) == []
    assert _png_diff(str(tmp_path / "one.png"), str(tmp_path / "edge.png")) == []
    # ... and just under the threshold it is translucent, and differs.
    assert _png_diff(str(tmp_path / "one.png"), str(tmp_path / "below.png")) != []
