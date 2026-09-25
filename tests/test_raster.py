"""runtime.md phase 4, slice 1: `raster.f`'s opaque rectangle fill.

**Byte-identity is demanded here and will not be demanded later**, so
it is worth saying why this slice gets it. An opaque, axis-aligned
rectangle involves no antialiasing and no blending: every pixel it
covers is the source colour exactly, and every pixel it does not is
untouched. There is only one right answer and Cairo produces it too,
so "identical bytes" is the correct assertion rather than a bound.

Anything with a soft edge -- a circle, a rotated rect, a gradient stop,
a glyph -- does NOT get this treatment, because a different rasteriser
making different sampling choices is not a bug. Those get properties
plus a measured bound, and the difference is deliberate. See
runtime.md's "Phase 4, specified".

The comparison runs through the real handoff rather than around it:
raster.f fills an `arr[int]`, `imageFromPixels` turns it into an `img`,
and the PNG that writes is compared against the PNG Cairo's own
drawRect writes for the same scene.
"""
import math
import os
import struct
import subprocess
import sys
import zlib

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from festina import imports as imports_mod   # noqa: E402


def _with_raster(tmp_path):
    """raster.f beside the test program, imported by name -- the same
    shape test_runtime_components.py uses, and for the same reason:
    nothing triggers this component yet, and what is being tested is
    its arithmetic."""
    src = os.path.join(imports_mod.RUNTIME_COMPONENT_DIR, "raster.f")
    (tmp_path / "raster.f").write_text(
        open(src, encoding="utf-8").read(), encoding="utf-8")


def _run(tmp_path, cli_mod, source):
    from tests.conftest import compile_file_or_skip, _require_c_compiler
    src = tmp_path / "main.f"
    src.write_text(source, encoding="utf-8")
    out = tmp_path / "program"
    compile_file_or_skip(cli_mod, str(src), str(out), cc=_require_c_compiler())
    result = subprocess.run([str(out)], cwd=tmp_path, capture_output=True,
                             text=True, timeout=120,
                             env=dict(os.environ, DISPLAY=""))
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.strip()


def _decode_png(path):
    """Width, height and a pixel accessor, straight from the file."""
    with open(path, "rb") as fh:
        blob = fh.read()
    assert blob[:8] == b"\x89PNG\r\n\x1a\n", path
    at = 8
    idat = b""
    w = h = depth = colour = None
    while at < len(blob):
        length = struct.unpack(">I", blob[at:at + 4])[0]
        tag = blob[at + 4:at + 8]
        body = blob[at + 8:at + 8 + length]
        if tag == b"IHDR":
            w, h, depth, colour = struct.unpack(">IIBB", body[:10])
        elif tag == b"IDAT":
            idat += body
        at += 12 + length
    assert depth == 8 and colour in (2, 6), (depth, colour)
    n = 4 if colour == 6 else 3
    raw = zlib.decompress(idat)
    rows = []
    prev = bytearray(w * n)
    at = 0
    for _ in range(h):
        f = raw[at]
        line = bytearray(raw[at + 1:at + 1 + w * n])
        at += 1 + w * n
        for i in range(len(line)):
            a = line[i - n] if i >= n else 0
            b = prev[i]
            c = prev[i - n] if i >= n else 0
            if f == 1:
                line[i] = (line[i] + a) & 0xFF
            elif f == 2:
                line[i] = (line[i] + b) & 0xFF
            elif f == 3:
                line[i] = (line[i] + ((a + b) >> 1)) & 0xFF
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
        rows.append(bytes(line))
        prev = line
    return w, h, rows, n


def _png_diff(a, b):
    wa, ha, ra, na = _decode_png(a)
    wb, hb, rb, nb = _decode_png(b)
    if (wa, ha) != (wb, hb):
        return [("size", (wa, ha), (wb, hb))]
    diffs = []
    for y in range(ha):
        for x in range(wa):
            pa = ra[y][x * na:x * na + 3]
            pb = rb[y][x * nb:x * nb + 3]
            if pa != pb:
                diffs.append((x, y, tuple(pa), tuple(pb)))
    return diffs


#: The same scene, described once. `{T}` is the call prefix: empty for
#: the Cairo path's canvas-free img methods, unused by the raster path,
#: which builds the buffer itself.
_RECTS = [
    (0, 0, 40, 30, 200, 30, 30),
    (10, 5, 12, 12, 30, 200, 30),
    (30, 20, 20, 20, 30, 30, 200),      # runs off the right/bottom edge
    (-5, -5, 10, 10, 250, 250, 0),      # runs off the top/left edge
]


def _cairo_program(out_name, w=40, h=30):
    body = "".join(
        f"color c{i} = '#{r:02x}{g:02x}{b:02x}'\n"
        f"a.drawRect({x}, {y}, {rw}, {rh}, c{i})\n"
        for i, (x, y, rw, rh, r, g, b) in enumerate(_RECTS))
    return (f"img a = blankImage({w}, {h})\n" + body
            + f"log(a.save('{out_name}'))\n")


def _raster_program(out_name, w=40, h=30):
    body = "".join(
        f"rasFillRect(px, {w}, {h}, {x}, {y}, {rw}, {rh}, {r}, {g}, {b}, 255)\n"
        for (x, y, rw, rh, r, g, b) in _RECTS)
    return ("import raster.f\n"
            f"arr[int] px = rasNewSurface({w}, {h})\n" + body
            + f"img a = imageFromPixels(px, {w}, {h})\n"
            f"log(a.save('{out_name}'))\n")


class TestItMatchesCairoExactly:
    def test_a_scene_of_opaque_rects_is_byte_identical(
            self, tmp_path, cli_mod):
        _with_raster(tmp_path)
        assert _run(tmp_path, cli_mod, _cairo_program("cairo.png")) == "true"
        assert _run(tmp_path, cli_mod, _raster_program("raster.png")) == "true"
        diffs = _png_diff(str(tmp_path / "cairo.png"),
                          str(tmp_path / "raster.png"))
        assert diffs == [], diffs[:10]

    def test_the_comparison_can_fail(self, tmp_path, cli_mod):
        """The harness itself, checked. A byte-identity assertion that
        cannot fail proves nothing, and this one runs against two
        images that differ in a single pixel."""
        _with_raster(tmp_path)
        self_check = ("import raster.f\n"
                      "arr[int] px = rasNewSurface(4, 4)\n"
                      "rasFillRect(px, 4, 4, 0, 0, 4, 4, 10, 20, 30, 255)\n"
                      "img a = imageFromPixels(px, 4, 4)\n"
                      "log(a.save('one.png'))\n"
                      "rasFillRect(px, 4, 4, 2, 2, 1, 1, 90, 20, 30, 255)\n"
                      "img b = imageFromPixels(px, 4, 4)\n"
                      "log(b.save('two.png'))\n")
        assert _run(tmp_path, cli_mod, self_check) == "true\ntrue"
        diffs = _png_diff(str(tmp_path / "one.png"), str(tmp_path / "two.png"))
        assert diffs == [(2, 2, (10, 20, 30), (90, 20, 30))], diffs


class TestTheFillItself:
    def _pixels(self, tmp_path, cli_mod, calls, w=6, h=4, read="px"):
        source = ("import raster.f\n"
                  f"arr[int] px = rasNewSurface({w}, {h})\n"
                  + calls
                  + f"log({read}.length)\n"
                  "int i = 0\n"
                  "text out = ''\n"
                  f"while i < {read}.length {{ out = `${{out}} ${{{read}[i]}}` i = i + 1 }}\n"
                  "log(out)\n")
        lines = _run(tmp_path, cli_mod, source).splitlines()
        return [int(v) for v in lines[1].split()]

    def test_a_fresh_surface_is_transparent(self, tmp_path, cli_mod):
        _with_raster(tmp_path)
        px = self._pixels(tmp_path, cli_mod, "", w=2, h=2)
        assert px == [0] * 16

    def test_it_fills_exactly_the_requested_box(self, tmp_path, cli_mod):
        """Half-open, like the rest of the language: (1,1,2,2) covers
        columns 1-2 and rows 1-2, and column 3 is untouched."""
        _with_raster(tmp_path)
        px = self._pixels(
            tmp_path, cli_mod,
            "rasFillRect(px, 4, 3, 1, 1, 2, 2, 7, 8, 9, 255)\n", w=4, h=3)
        filled = {(i // 4) % 4 for i in range(0, len(px), 4)
                  if px[i:i + 4] != [0, 0, 0, 0]}
        rows = {(i // 4) // 4 for i in range(0, len(px), 4)
                if px[i:i + 4] != [0, 0, 0, 0]}
        assert filled == {1, 2}, "columns"
        assert rows == {1, 2}, "rows"

    def test_it_clips_rather_than_overflowing(self, tmp_path, cli_mod):
        """A rect reaching past every edge fills the overlap and writes
        nothing outside the buffer -- the length is unchanged, and an
        out-of-range write in Festina is not bounds-checked, so this is
        the test that says the clipping arithmetic is right rather than
        merely not crashing today."""
        _with_raster(tmp_path)
        px = self._pixels(
            tmp_path, cli_mod,
            "rasFillRect(px, 3, 2, -4, -4, 20, 20, 1, 2, 3, 255)\n", w=3, h=2)
        assert len(px) == 3 * 2 * 4
        assert px == [1, 2, 3, 255] * 6

    def test_a_rect_entirely_outside_touches_nothing(self, tmp_path, cli_mod):
        _with_raster(tmp_path)
        px = self._pixels(
            tmp_path, cli_mod,
            "rasFillRect(px, 3, 2, 50, 50, 4, 4, 1, 2, 3, 255)\n"
            "rasFillRect(px, 3, 2, -50, 0, 4, 4, 1, 2, 3, 255)\n", w=3, h=2)
        assert px == [0] * 24

    def test_an_empty_rect_draws_nothing(self, tmp_path, cli_mod):
        _with_raster(tmp_path)
        px = self._pixels(
            tmp_path, cli_mod,
            "rasFillRect(px, 3, 2, 0, 0, 0, 5, 1, 2, 3, 255)\n"
            "rasFillRect(px, 3, 2, 0, 0, 5, -1, 1, 2, 3, 255)\n", w=3, h=2)
        assert px == [0] * 24

    def test_channels_are_clamped_not_wrapped(self, tmp_path, cli_mod):
        """A rasteriser fed an out-of-range channel clamps it, the same
        way fillStyle(r, g, b) already does -- 300 is white, not 44."""
        _with_raster(tmp_path)
        px = self._pixels(
            tmp_path, cli_mod,
            "rasFillRect(px, 1, 1, 0, 0, 1, 1, 300, -20, 128, 999)\n",
            w=1, h=1)
        assert px == [255, 0, 128, 255]

    def test_it_writes_row_major_from_the_top_left(self, tmp_path, cli_mod):
        """One pixel at (1, 0) lands at index 4. Bottom-up or
        column-major would put it at 12 or 8, and every one of those
        conventions round-trips through imageFromPixels looking
        plausible on a symmetric test image."""
        _with_raster(tmp_path)
        px = self._pixels(
            tmp_path, cli_mod,
            "rasFillRect(px, 3, 2, 1, 0, 1, 1, 4, 5, 6, 255)\n", w=3, h=2)
        assert px[4:8] == [4, 5, 6, 255]
        assert px[0:4] == [0, 0, 0, 0]
        assert px[8:12] == [0, 0, 0, 0]


class TestItRoundTripsThroughTheHandoff:
    def test_what_raster_writes_is_what_toPixels_reads_back(
            self, tmp_path, cli_mod):
        """raster.f -> imageFromPixels -> img.toPixels() is the whole
        handoff, both directions, and it must be the identity for
        opaque pixels."""
        _with_raster(tmp_path)
        out = _run(tmp_path, cli_mod,
                   "import raster.f\n"
                   "arr[int] px = rasNewSurface(5, 4)\n"
                   "rasFillRect(px, 5, 4, 1, 1, 3, 2, 12, 34, 56, 255)\n"
                   "img a = imageFromPixels(px, 5, 4)\n"
                   "arr[int] back = a.toPixels()\n"
                   "int i = 0\n"
                   "int same = 0\n"
                   "while i < px.length {\n"
                   "  if px[i] == back[i] { same = same + 1 }\n"
                   "  i = i + 1\n"
                   "}\n"
                   "log(`${same} ${px.length}`)\n")
        same, total = out.split()
        assert same == total == "80"


class TestPathFill:
    """runtime.md phase 4, slice 2: the scanline core.

    Coverage is exact in x and sampled at RAS_SUB positions in y, so
    these tests split in two. Pixel-aligned geometry has exactly one
    right answer and is asserted exactly -- including against slice 1's
    own rectangle fill, which is a cross-check between two independent
    code paths that must agree. Antialiased edges get properties plus a
    measured bound, per runtime.md's "Phase 4, specified".
    """

    def _pixels(self, tmp_path, cli_mod, body, w, h):
        source = ("import raster.f\n"
                  f"arr[int] px = rasNewSurface({w}, {h})\n"
                  + body
                  + "int i = 0\ntext out = ''\n"
                  "while i < px.length { out = `${out} ${px[i]}` i = i + 1 }\n"
                  "log(out)\n")
        return [int(v) for v in _run(tmp_path, cli_mod, source).split()]

    def test_a_pixel_aligned_square_has_no_soft_edge(self, tmp_path, cli_mod):
        _with_raster(tmp_path)
        px = self._pixels(
            tmp_path, cli_mod,
            "arr[float] pts = [1.0, 1.0, 4.0, 1.0, 4.0, 3.0, 1.0, 3.0]\n"
            "arr[int] ends = [4]\n"
            "rasFillPath(px, 6, 4, pts, ends, RAS_NONZERO, 10, 20, 30, 255)\n",
            6, 4)
        for i in range(0, len(px), 4):
            x, y = (i // 4) % 6, (i // 4) // 6
            inside = 1 <= x < 4 and 1 <= y < 3
            want = [10, 20, 30, 255] if inside else [0, 0, 0, 0]
            assert px[i:i + 4] == want, f"at ({x}, {y})"

    def test_it_agrees_with_the_slice_one_rectangle_fill(self, tmp_path, cli_mod):
        """Two independent code paths for the same shape. If the
        scanline filler and the rectangle filler ever disagree on a
        pixel-aligned box, one of them is wrong."""
        _with_raster(tmp_path)
        body_path = ("arr[float] pts = [2.0, 1.0, 7.0, 1.0, 7.0, 5.0, 2.0, 5.0]\n"
                     "arr[int] ends = [4]\n"
                     "rasFillPath(px, 9, 6, pts, ends, RAS_NONZERO, 77, 88, 99, 255)\n")
        body_rect = "rasFillRect(px, 9, 6, 2, 1, 5, 4, 77, 88, 99, 255)\n"
        assert (self._pixels(tmp_path, cli_mod, body_path, 9, 6)
                == self._pixels(tmp_path, cli_mod, body_rect, 9, 6))

    def test_a_half_pixel_edge_is_half_covered(self, tmp_path, cli_mod):
        """Exact in x, so a left edge at 1.5 leaves column 1 at exactly
        half coverage -- round(255 * 0.5) = 128 -- and column 2 fully
        covered. This is the assertion that would catch an off-by-half
        in the span arithmetic, which every other test here tolerates."""
        _with_raster(tmp_path)
        px = self._pixels(
            tmp_path, cli_mod,
            "arr[float] pts = [1.5, 0.0, 4.0, 0.0, 4.0, 2.0, 1.5, 2.0]\n"
            "arr[int] ends = [4]\n"
            "rasFillPath(px, 5, 2, pts, ends, RAS_NONZERO, 200, 100, 50, 255)\n",
            5, 2)
        assert px[4:8] == [200, 100, 50, 128], "column 1 is half covered"
        assert px[8:12] == [200, 100, 50, 255], "column 2 is whole"
        assert px[0:4] == [0, 0, 0, 0], "column 0 is untouched"

    def test_nonzero_and_evenodd_differ_on_nested_subpaths(self, tmp_path, cli_mod):
        """Two concentric squares wound the SAME way. Nonzero counts 2
        inside the inner one and fills it; even-odd counts two
        crossings and leaves a hole. Same geometry, same call, one
        argument apart -- which is the only way to test that the rule
        is actually consulted rather than hardcoded."""
        _with_raster(tmp_path)
        geometry = (
            "arr[float] pts = [0.0, 0.0, 6.0, 0.0, 6.0, 6.0, 0.0, 6.0,\n"
            "                  2.0, 2.0, 4.0, 2.0, 4.0, 4.0, 2.0, 4.0]\n"
            "arr[int] ends = [4, 8]\n")
        centre = 4 * ((3 * 6) + 3)
        nz = self._pixels(
            tmp_path, cli_mod,
            geometry + "rasFillPath(px, 6, 6, pts, ends, RAS_NONZERO, 9, 9, 9, 255)\n",
            6, 6)
        eo = self._pixels(
            tmp_path, cli_mod,
            geometry + "rasFillPath(px, 6, 6, pts, ends, RAS_EVENODD, 9, 9, 9, 255)\n",
            6, 6)
        assert nz[centre:centre + 4] == [9, 9, 9, 255], "nonzero fills the middle"
        assert eo[centre:centre + 4] == [0, 0, 0, 0], "even-odd leaves a hole"
        corner = 0
        assert nz[corner:corner + 4] == eo[corner:corner + 4] == [9, 9, 9, 255], (
            "both rules agree outside the inner square")

    def test_a_path_is_clipped_to_the_surface(self, tmp_path, cli_mod):
        _with_raster(tmp_path)
        px = self._pixels(
            tmp_path, cli_mod,
            "arr[float] pts = [-10.0, -10.0, 20.0, -10.0, 20.0, 20.0, -10.0, 20.0]\n"
            "arr[int] ends = [4]\n"
            "rasFillPath(px, 3, 2, pts, ends, RAS_NONZERO, 1, 2, 3, 255)\n",
            3, 2)
        assert px == [1, 2, 3, 255] * 6

    def test_a_path_entirely_outside_touches_nothing(self, tmp_path, cli_mod):
        _with_raster(tmp_path)
        px = self._pixels(
            tmp_path, cli_mod,
            "arr[float] pts = [40.0, 40.0, 50.0, 40.0, 50.0, 50.0]\n"
            "arr[int] ends = [3]\n"
            "rasFillPath(px, 3, 2, pts, ends, RAS_NONZERO, 1, 2, 3, 255)\n",
            3, 2)
        assert px == [0] * 24

    def test_alpha_composites_over_what_is_already_there(self, tmp_path, cli_mod):
        """Half-transparent black over opaque white is mid grey, and
        the destination stays opaque. This is the first test that
        exercises the src-over path at all -- slice 1 only ever
        overwrote."""
        _with_raster(tmp_path)
        px = self._pixels(
            tmp_path, cli_mod,
            "rasFillRect(px, 2, 1, 0, 0, 2, 1, 255, 255, 255, 255)\n"
            "arr[float] pts = [0.0, 0.0, 2.0, 0.0, 2.0, 1.0, 0.0, 1.0]\n"
            "arr[int] ends = [4]\n"
            "rasFillPath(px, 2, 1, pts, ends, RAS_NONZERO, 0, 0, 0, 128)\n",
            2, 1)
        for at in (0, 4):
            r, g, b, a = px[at:at + 4]
            assert a == 255, "an opaque destination stays opaque"
            assert r == g == b, "grey"
            assert 126 <= r <= 129, f"half of white is mid grey, got {r}"


class TestAgainstCairosOwnRasteriser:
    """The same triangle, filled by Cairo's fillPath and by
    rasFillPath, over an opaque background so the comparison means
    something.

    **Over an opaque background specifically.** The first version of
    this measurement compared the two on a transparent surface and
    reported a max deviation of 255 -- which was the measurement's
    fault, not the rasteriser's: a pixel with alpha 1/255 un-premultiplies
    to a saturated colour, so comparing RGB while ignoring alpha
    compares noise. Over white, every pixel is opaque and the numbers
    are what a viewer would actually see.

    **The bound is measured, and its cause was checked rather than
    assumed.** The obvious explanation for the residual was RAS_SUB's
    1/16 quantisation in y. That is wrong: varying RAS_SUB over 8, 16,
    32 and 64 moves the maximum by one unit (13, 12, 12, 12), so the
    difference is in how the two rasterisers compute coverage and not
    in how finely this one samples. Sixteen is kept because thirty-two
    buys nothing.
    """

    _TRIANGLE = [(40.0, 30.0), (180.0, 55.0), (95.0, 150.0)]

    def _render(self, tmp_path, cli_mod):
        _with_raster(tmp_path)
        pts = ", ".join(f"{x}, {y}" for x, y in self._TRIANGLE)
        moves = "\n".join(
            [f"moveTo({int(self._TRIANGLE[0][0])}, {int(self._TRIANGLE[0][1])})"]
            + [f"lineTo({int(x)}, {int(y)})" for x, y in self._TRIANGLE[1:]])
        assert _run(tmp_path, cli_mod,
                    "color red = '#c81e1e'\n"
                    "color white = '#ffffff'\n"
                    "clearCanvas()\n"
                    "fillStyle(white)\ndrawRect(0, 0, 800, 600)\n"
                    "fillStyle(red)\nbeginPath()\n" + moves + "\n"
                    "closePath()\nfillPath()\n"
                    "log(saveCanvas('cairo.png'))\n") == "true"
        assert _run(tmp_path, cli_mod,
                    "import raster.f\n"
                    "arr[int] px = rasNewSurface(800, 600)\n"
                    "rasFillRect(px, 800, 600, 0, 0, 800, 600, 255, 255, 255, 255)\n"
                    f"arr[float] pts = [{pts}]\n"
                    "arr[int] ends = [3]\n"
                    "rasFillPath(px, 800, 600, pts, ends, RAS_NONZERO, 200, 30, 30, 255)\n"
                    "img a = imageFromPixels(px, 800, 600)\n"
                    "log(a.save('raster.png'))\n") == "true"
        return (_decode_png(str(tmp_path / "cairo.png")),
                _decode_png(str(tmp_path / "raster.png")))

    def test_the_deviation_stays_within_its_measured_bound(
            self, tmp_path, cli_mod):
        (wa, ha, ra, na), (wb, hb, rb, nb) = self._render(tmp_path, cli_mod)
        assert (wa, ha) == (wb, hb)
        worst = 0
        total = 0
        differing = 0
        for y in range(ha):
            for x in range(wa):
                pa = ra[y][x * na:x * na + 3]
                pb = rb[y][x * nb:x * nb + 3]
                d = max(abs(pa[i] - pb[i]) for i in range(3))
                if d:
                    differing += 1
                total += d
                worst = max(worst, d)
        # Measured on this Cairo: max 12, 331 of 480,000 pixels
        # differing, mean 0.0028. 16 is that with room for another
        # Cairo's own choices, and is 6% of full scale -- a regression
        # that mattered would blow straight through it.
        assert worst <= 16, f"max deviation {worst}"
        assert differing <= 1200, f"{differing} pixels differ"
        assert total / (wa * ha) <= 0.01, "mean deviation"

    def test_the_interior_and_exterior_are_exact(self, tmp_path, cli_mod):
        """Whatever a rasteriser does at an edge, a pixel well inside
        the triangle is the fill colour and a pixel well outside is the
        background. No bound applies to either."""
        (wa, ha, ra, na), (wb, hb, rb, nb) = self._render(tmp_path, cli_mod)
        inside = (100, 70)
        outside = (400, 300)
        for name, (x, y) in (("inside", inside), ("outside", outside)):
            pa = tuple(ra[y][x * na:x * na + 3])
            pb = tuple(rb[y][x * nb:x * nb + 3])
            assert pa == pb, f"{name} at ({x}, {y}): cairo {pa}, raster {pb}"
        assert tuple(rb[inside[1]][inside[0] * nb:inside[0] * nb + 3]) == (200, 30, 30)
        assert tuple(rb[outside[1]][outside[0] * nb:outside[0] * nb + 3]) == (255, 255, 255)


def _flatten_output(tmp_path, cli_mod, body):
    """Run a program that builds `pts` and print it, returning the
    points as (x, y) pairs."""
    _with_raster(tmp_path)
    out = _run(tmp_path, cli_mod,
               "import raster.f\n" + body +
               "int i = 0\ntext out = ''\n"
               "while i < pts.length { out = `${out} ${pts[i]}` i = i + 1 }\n"
               "log(out)\n")
    vals = [float(v) for v in out.split()]
    return [(vals[i], vals[i + 1]) for i in range(0, len(vals), 2)]


def _dist_to_polyline(p, poly):
    best = float("inf")
    px, py = p
    for (ax, ay), (bx, by) in zip(poly, poly[1:]):
        dx, dy = bx - ax, by - ay
        L = dx * dx + dy * dy
        t = 0.0 if L == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L))
        best = min(best, math.hypot(px - (ax + t * dx), py - (ay + t * dy)))
    return best


class TestFlattening:
    """runtime.md phase 4, slice 3: curves and arcs become polygons.

    The segment counts are derived from error bounds, so the tolerance
    is a GUARANTEE, and it is checked as one: against the true curve,
    sampled densely, rather than against a picture of it."""

    TOL = 0.1

    def test_a_cubic_stays_within_tolerance_of_the_true_curve(self, tmp_path, cli_mod):
        P = [(10.0, 80.0), (40.0, -30.0), (160.0, 190.0), (190.0, 20.0)]
        poly = _flatten_output(
            tmp_path, cli_mod,
            "arr[float] pts = [10.0, 80.0]\n"
            "rasCubicTo(pts, 10.0, 80.0, 40.0, -30.0, 160.0, 190.0, 190.0, 20.0)\n")

        def B(t):
            u = 1 - t
            return tuple(u ** 3 * P[0][i] + 3 * u * u * t * P[1][i]
                         + 3 * u * t * t * P[2][i] + t ** 3 * P[3][i] for i in (0, 1))

        worst = max(_dist_to_polyline(B(k / 4000), poly) for k in range(4001))
        assert worst <= self.TOL, f"{worst:.4f} px from the true curve"

    def test_a_cubic_ends_exactly_where_it_was_asked_to(self, tmp_path, cli_mod):
        """Written, not evaluated at t = 1: the next segment of a path
        starts from this point, and a rounding error here opens a gap."""
        poly = _flatten_output(
            tmp_path, cli_mod,
            "arr[float] pts = [1.5, 2.5]\n"
            "rasCubicTo(pts, 1.5, 2.5, 30.0, 90.0, 70.0, -40.0, 123.25, 45.75)\n")
        assert poly[0] == (1.5, 2.5)
        assert poly[-1] == (123.25, 45.75)

    def test_a_straight_cubic_is_one_segment(self, tmp_path, cli_mod):
        """Collinear, evenly spaced controls have zero second
        difference, so the bound asks for a single chord -- and a
        flattener that subdivided anyway would be wasting every glyph
        stem slice 5 feeds it."""
        poly = _flatten_output(
            tmp_path, cli_mod,
            "arr[float] pts = [0.0, 0.0]\n"
            "rasCubicTo(pts, 0.0, 0.0, 10.0, 10.0, 20.0, 20.0, 30.0, 30.0)\n")
        assert poly == [(0.0, 0.0), (30.0, 30.0)]

    def test_an_arc_stays_within_tolerance_of_the_true_circle(self, tmp_path, cli_mod):
        poly = _flatten_output(
            tmp_path, cli_mod,
            "arr[float] pts = []\n"
            "rasArc(pts, 100.0, 100.0, 37.5, 0.3, 4.1)\n")
        worst = max(
            _dist_to_polyline((100 + 37.5 * math.cos(a), 100 + 37.5 * math.sin(a)), poly)
            for a in (0.3 + (4.1 - 0.3) * k / 4000 for k in range(4001)))
        assert worst <= self.TOL, f"{worst:.4f} px"

    def test_an_arc_begins_and_ends_on_the_true_radius(self, tmp_path, cli_mod):
        """Paths join at an arc's end points, so those two must be ON
        the circle -- which is also why only rasCircle, and never
        rasArc, gets the area-balanced radius.

        Checked inside the program rather than on its printed output:
        a float in a template literal prints to six significant
        figures, and the first version of this test compared 37.5
        against 37.49988 and blamed the arc for the harness's rounding."""
        _with_raster(tmp_path)
        out = _run(tmp_path, cli_mod,
                   "import raster.f\n"
                   "arr[float] pts = []\n"
                   "rasArc(pts, 100.0, 100.0, 37.5, 0.3, 4.1)\n"
                   "int last = pts.length - 2\n"
                   "float d0 = Math.sqrt(((pts[0] - 100.0) * (pts[0] - 100.0))"
                   " + ((pts[1] - 100.0) * (pts[1] - 100.0)))\n"
                   "float d1 = Math.sqrt(((pts[last] - 100.0) * (pts[last] - 100.0))"
                   " + ((pts[last + 1] - 100.0) * (pts[last + 1] - 100.0)))\n"
                   "log(Math.abs(d0 - 37.5) < 0.000000001)\n"
                   "log(Math.abs(d1 - 37.5) < 0.000000001)\n")
        assert out.splitlines() == ["true", "true"]

    def test_segment_count_follows_size_not_a_constant(self, tmp_path, cli_mod):
        """A radius-3 circle and a radius-300 one are not the same
        polygon. Fixed subdivision is either faceted when large or
        wasteful when small; the bound is neither."""
        small = _flatten_output(tmp_path, cli_mod,
                                "arr[float] pts = []\narr[int] e = []\n"
                                "rasCircle(pts, e, 0.0, 0.0, 3.0)\n")
        big = _flatten_output(tmp_path, cli_mod,
                              "arr[float] pts = []\narr[int] e = []\n"
                              "rasCircle(pts, e, 0.0, 0.0, 300.0)\n")
        assert len(small) < 15 < 100 < len(big)

    def test_a_sub_tolerance_circle_does_not_vanish(self, tmp_path, cli_mod):
        """Below half the tolerance the arc bound degenerates -- every
        point of the circle is within tolerance of its centre. A dot
        still has to cover the pixel it sits in."""
        _with_raster(tmp_path)
        out = _run(tmp_path, cli_mod,
                   "import raster.f\n"
                   "arr[int] px = rasNewSurface(3, 3)\n"
                   "arr[float] pts = []\narr[int] ends = []\n"
                   "rasCircle(pts, ends, 1.5, 1.5, 0.04)\n"
                   "rasFillPath(px, 3, 3, pts, ends, RAS_NONZERO, 255, 0, 0, 255)\n"
                   "log(px[(4 * 4) + 3])\n")
        assert int(out) > 0


class TestFilledCirclesAgainstTheTruth:
    """A filled circle compared against the TRUE circle's coverage
    rather than against Cairo -- because comparing against Cairo turned
    out to measure two approximations disagreeing with each other.

    The reference is computed here: 32x32 point samples per edge pixel,
    over white, so every expected value is exact to about 1/1000.

    Measured, and recorded because the ordering is the point:

        vs the TRUE circle     max|err|  mean|err|  mean signed
        Cairo                    22.3      3.35       +1.63
        raster.f                 13.5      4.45       +0.06

    **The signed column is the regression test.** An inscribed polygon
    lies wholly inside its circle, so every filled circle came out
    small: +12.12. Balancing the extreme deviations left +3.07.
    Balancing the AREA took it to +0.06, as the second-order expansion
    predicted it would. Both earlier versions were put back and run
    against the assertion below, and both fail it. (This docstring
    first said the inscribed figure was "about +6" -- written before it
    was measured, and wrong by a factor of two.) mean|err| remains above Cairo's and is not
    explained yet -- it is recorded rather than bounded away.
    """

    CIRCLES = [(100.0, 100.0, 37.0), (300.0, 120.0, 90.0), (520.0, 80.0, 5.0)]

    def _errors(self, tmp_path, cli_mod):
        _with_raster(tmp_path)
        adds = "".join(f"rasCircle(pts, ends, {cx}, {cy}, {r})\n"
                       for cx, cy, r in self.CIRCLES)
        assert _run(tmp_path, cli_mod,
                    "import raster.f\n"
                    "arr[int] px = rasNewSurface(640, 240)\n"
                    "rasFillRect(px, 640, 240, 0, 0, 640, 240, 255, 255, 255, 255)\n"
                    "arr[float] pts = []\narr[int] ends = []\n" + adds +
                    "rasFillPath(px, 640, 240, pts, ends, RAS_NONZERO, 200, 30, 30, 255)\n"
                    "img a = imageFromPixels(px, 640, 240)\n"
                    "log(a.save('c.png'))\n") == "true"
        w, h, rows, n = _decode_png(str(tmp_path / "c.png"))
        N = 32
        errs = []
        for cx, cy, r in self.CIRCLES:
            for y in range(int(cy - r - 2), int(cy + r + 3)):
                for x in range(int(cx - r - 2), int(cx + r + 3)):
                    if abs(math.hypot(x + 0.5 - cx, y + 0.5 - cy) - r) > 1.5:
                        continue
                    inside = sum(
                        1 for sy in range(N) for sx in range(N)
                        if (x + (sx + 0.5) / N - cx) ** 2 + (y + (sy + 0.5) / N - cy) ** 2 <= r * r)
                    t = inside / (N * N)
                    if 0 < t < 1:
                        errs.append(rows[y][x * n + 1] - (255 * (1 - t) + 30 * t))
        return errs

    def test_filled_circles_are_not_biased(self, tmp_path, cli_mod):
        errs = self._errors(tmp_path, cli_mod)
        mean_signed = sum(errs) / len(errs)
        assert abs(mean_signed) <= 1.0, (
            f"mean signed error {mean_signed:+.2f} -- an inscribed polygon "
            f"measures +12.12 here and extremes-balanced +3.07")

    def test_filled_circles_stay_within_their_measured_bound(self, tmp_path, cli_mod):
        errs = self._errors(tmp_path, cli_mod)
        worst = max(abs(e) for e in errs)
        mean_abs = sum(abs(e) for e in errs) / len(errs)
        # Measured 13.5 and 4.45. Cairo's own figures against the same
        # truth are 22.3 and 3.35, so the max is held to below Cairo's
        # and the mean to what is measured, with a unit's room.
        assert worst <= 18.0, f"max |error| {worst:.1f}"
        assert mean_abs <= 5.5, f"mean |error| {mean_abs:.2f}"
