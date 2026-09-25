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


class TestStroking:
    """runtime.md phase 4, slice 4: a stroke is the FILL of its outline.

    Segments become quads, joins become small polygons on the outside of
    each turn, and the set is filled once with the nonzero rule -- so an
    overlap is covered once, not blended twice. Styles are the ones the
    runtime has always had by never changing Cairo's defaults: miter
    joins, miter limit 10, butt caps."""

    def _alpha_grid(self, tmp_path, cli_mod, w, h, pts, ends, closed, width):
        _with_raster(tmp_path)
        out = _run(tmp_path, cli_mod,
                   "import raster.f\n"
                   f"arr[int] px = rasNewSurface({w}, {h})\n"
                   f"arr[float] pts = [{pts}]\n"
                   f"arr[int] ends = [{ends}]\n"
                   f"arr[int] closed = [{closed}]\n"
                   f"rasStrokePath(px, {w}, {h}, pts, ends, closed, {width}, 9, 9, 9, 255)\n"
                   "int i = 3\ntext out = ''\n"
                   "while i < px.length { out = `${out} ${px[i]}` i = i + 4 }\n"
                   "log(out)\n")
        vals = [int(v) for v in out.split()]
        return [vals[y * w:(y + 1) * w] for y in range(h)]

    def test_a_line_has_butt_caps_and_exact_width(self, tmp_path, cli_mod):
        g = self._alpha_grid(tmp_path, cli_mod, 12, 10,
                             "2.0, 5.0, 10.0, 5.0", "2", "0", "4.0")
        for y in range(10):
            for x in range(12):
                want = 255 if (2 <= x < 10 and 3 <= y < 7) else 0
                assert g[y][x] == want, f"({x}, {y})"

    def test_a_right_angle_gets_a_square_miter(self, tmp_path, cli_mod):
        """The outer corner square is filled. A bevel would cut it on
        the diagonal, and a missing join would leave it empty."""
        g = self._alpha_grid(tmp_path, cli_mod, 18, 16,
                             "3.0, 3.0, 13.0, 3.0, 13.0, 13.0", "3", "0", "4.0")
        for y in (1, 2):
            for x in (13, 14):
                assert g[y][x] == 255, f"outer corner ({x}, {y})"
        assert g[1][15] == 0 and g[0][14] == 0, "and nothing beyond it"

    def test_a_closed_path_is_joined_where_it_closes(self, tmp_path, cli_mod):
        """The vertex a closed path returns to needs a join too -- the
        classic off-by-one is to join every vertex but the first. The
        top-left outer corner exists only if vertex 0 was joined."""
        g = self._alpha_grid(tmp_path, cli_mod, 16, 16,
                             "3.0, 3.0, 13.0, 3.0, 13.0, 13.0, 3.0, 13.0",
                             "4", "1", "4.0")
        for corner in ((1, 1), (14, 1), (14, 14), (1, 14)):
            x, y = corner
            assert g[y][x] == 255, f"corner at {corner}"
        assert g[8][8] == 0, "a stroke, not a fill: the middle is empty"

    def test_zero_width_draws_nothing(self, tmp_path, cli_mod):
        g = self._alpha_grid(tmp_path, cli_mod, 8, 8,
                             "1.0, 1.0, 6.0, 6.0", "2", "0", "0.0")
        assert all(v == 0 for row in g for v in row)

    def test_a_self_crossing_stroke_is_not_blended_twice(self, tmp_path, cli_mod):
        """The reason for stroking by filling a union. Half-transparent
        black over white is 127 everywhere on the stroke -- including
        where two segments cross. Painting the pieces one by one would
        leave about 64 there.

        This does NOT test winding consistency, and its first docstring
        claimed it did. Segment quads are wound consistently by
        construction -- each is built from its own direction and left
        normal -- so removing rasEmitPoly's orientation fix still
        passes here. See the next test for where that fix matters."""
        _with_raster(tmp_path)
        out = _run(tmp_path, cli_mod,
                   "import raster.f\n"
                   "arr[int] px = rasNewSurface(40, 40)\n"
                   "rasFillRect(px, 40, 40, 0, 0, 40, 40, 255, 255, 255, 255)\n"
                   "arr[float] pts = [5.0, 20.0, 35.0, 20.0, 35.0, 5.0, 20.0, 5.0, 20.0, 35.0]\n"
                   "arr[int] ends = [5]\narr[int] closed = [0]\n"
                   "rasStrokePath(px, 40, 40, pts, ends, closed, 4.0, 0, 0, 0, 128)\n"
                   "log(px[((20 * 40) + 20) * 4])\n"
                   "log(px[((20 * 40) + 10) * 4])\n"
                   "log(px[((5 * 40) + 27) * 4])\n")
        crossing, plain, corner = (int(v) for v in out.split())
        assert crossing == plain == corner, (crossing, plain, corner)
        assert 126 <= plain <= 129


    def test_a_segment_crossing_a_join_is_not_a_hole(self, tmp_path, cli_mod):
        """Where rasEmitPoly's orientation fix is load-bearing. Join
        polygons flip winding with the direction of the turn; quads do
        not. A join fills the wedge OUTSIDE its own two quads, so it
        never overlaps them -- but another part of the path can cross
        it. Here a later segment runs straight through a right angle's
        miter corner. With every piece normalized the crossing is
        inside once, 127; with the fix removed the opposite-wound join
        cancels the crossing segment under nonzero and leaves a HOLE,
        255 -- checked, by putting the bug back."""
        _with_raster(tmp_path)
        out = _run(tmp_path, cli_mod,
                   "import raster.f\n"
                   "arr[int] px = rasNewSurface(50, 40)\n"
                   "rasFillRect(px, 50, 40, 0, 0, 50, 40, 255, 255, 255, 255)\n"
                   "arr[float] pts = [10.0, 10.0, 30.0, 10.0, 30.0, 30.0, 20.0, 0.0, 45.0, 20.0]\n"
                   "arr[int] ends = [3, 5]\narr[int] closed = [0, 0]\n"
                   "rasStrokePath(px, 50, 40, pts, ends, closed, 6.0, 0, 0, 0, 128)\n"
                   "log(px[((8 * 50) + 32) * 4])\n"
                   "log(px[((10 * 50) + 15) * 4])\n")
        through_join, plain = (int(v) for v in out.split())
        assert through_join != 255, "the crossing cancelled to a hole"
        assert through_join == plain, (through_join, plain)


class TestStrokingAgainstCairo:
    """Cairo's own strokePath over the same geometry. Stroke edges are
    straight lines, so this bound is tighter than the filled triangle's.

    **No pixel off by more than 60** is asserted separately from the
    maximum, because it is the join test. A wrong miter-versus-bevel
    decision does not produce a small error; it leaves a whole wedge
    wrong by around 225. Measured: max 6 on the polyline, max 14 on the
    spikes, and zero such pixels on either."""

    def _compare(self, tmp_path, cli_mod, cairo_body, pts, ends, closed, width):
        _with_raster(tmp_path)
        assert _run(tmp_path, cli_mod,
                    "color red = '#c81e1e'\ncolor white = '#ffffff'\n"
                    "clearCanvas()\nfillStyle(white)\ndrawRect(0, 0, 800, 600)\n"
                    f"borderColor(red)\nlineWidth({int(width)})\n" + cairo_body +
                    "log(saveCanvas('cairo.png'))\n") == "true"
        assert _run(tmp_path, cli_mod,
                    "import raster.f\n"
                    "arr[int] px = rasNewSurface(800, 600)\n"
                    "rasFillRect(px, 800, 600, 0, 0, 800, 600, 255, 255, 255, 255)\n"
                    f"arr[float] pts = [{pts}]\narr[int] ends = [{ends}]\n"
                    f"arr[int] closed = [{closed}]\n"
                    f"rasStrokePath(px, 800, 600, pts, ends, closed, {width}, 200, 30, 30, 255)\n"
                    "img a = imageFromPixels(px, 800, 600)\n"
                    "log(a.save('raster.png'))\n") == "true"
        w, h, ra, na = _decode_png(str(tmp_path / "cairo.png"))
        _, _, rb, nb = _decode_png(str(tmp_path / "raster.png"))
        worst = 0
        wedge = 0
        for y in range(h):
            for x in range(w):
                d = max(abs(ra[y][x * na + i] - rb[y][x * nb + i]) for i in range(3))
                worst = max(worst, d)
                if d > 60:
                    wedge += 1
        return worst, wedge

    def test_a_polyline_and_a_closed_square(self, tmp_path, cli_mod):
        worst, wedge = self._compare(
            tmp_path, cli_mod,
            "beginPath()\nmoveTo(40, 60)\nlineTo(200, 60)\nlineTo(260, 180)\n"
            "lineTo(330, 70)\nlineTo(420, 190)\nlineTo(440, 80)\nstrokePath()\n"
            "beginPath()\nmoveTo(100, 300)\nlineTo(300, 300)\nlineTo(300, 450)\n"
            "lineTo(100, 450)\nclosePath()\nstrokePath()\n",
            "40.0, 60.0, 200.0, 60.0, 260.0, 180.0, 330.0, 70.0, 420.0, 190.0, 440.0, 80.0,"
            " 100.0, 300.0, 300.0, 300.0, 300.0, 450.0, 100.0, 450.0",
            "6, 10", "0, 1", 6.0)
        assert wedge == 0, f"{wedge} pixels off by more than 60 -- a join disagrees"
        assert worst <= 10, f"max deviation {worst}"

    def test_the_miter_limit_bevels_where_cairo_does(self, tmp_path, cli_mod):
        """Two spikes either side of the limit: 6.54 degrees has a miter
        ratio of 17.5 and must bevel; 16.26 degrees has 7.07 and must
        keep a miter seventy pixels long."""
        worst, wedge = self._compare(
            tmp_path, cli_mod,
            "beginPath()\nmoveTo(50, 300)\nlineTo(400, 280)\nlineTo(50, 260)\nstrokePath()\n"
            "beginPath()\nmoveTo(50, 450)\nlineTo(400, 400)\nlineTo(50, 350)\nstrokePath()\n",
            "50.0, 300.0, 400.0, 280.0, 50.0, 260.0, 50.0, 450.0, 400.0, 400.0, 50.0, 350.0",
            "3, 6", "0, 0", 10.0)
        assert wedge == 0, f"{wedge} pixels off by more than 60 -- a bevel/miter call disagrees"
        assert worst <= 18, f"max deviation {worst}"


class TestClipping:
    """runtime.md phase 4, slice 5: a clip is a coverage mask, and a
    clipped draw multiplies its coverage by it.

    The language exposes no path clip -- the runtime's one cairo_clip is
    internal to drawImageRegion -- so there is no Cairo program to
    compare against. The oracles are exact instead: cases where the
    right answer is fixed by the definition, and cross-checks against
    unclipped fills that must come out byte-identical."""

    def _alpha(self, tmp_path, cli_mod, w, h, setup):
        _with_raster(tmp_path)
        out = _run(tmp_path, cli_mod,
                   "import raster.f\n"
                   f"arr[int] px = rasNewSurface({w}, {h})\n" + setup +
                   "int i = 3\ntext out = ''\n"
                   "while i < px.length { out = `${out} ${px[i]}` i = i + 4 }\n"
                   "log(out)\n")
        vals = [int(v) for v in out.split()]
        return [vals[y * w:(y + 1) * w] for y in range(h)]

    FULL = ("arr[float] all = [0.0, 0.0, 40.0, 0.0, 40.0, 40.0, 0.0, 40.0]\n"
            "arr[int] allEnds = [4]\n")

    def test_a_rectangular_clip_is_exact(self, tmp_path, cli_mod):
        g = self._alpha(tmp_path, cli_mod, 10, 6,
                        "arr[float] cp = [3.0, 1.0, 7.0, 1.0, 7.0, 4.0, 3.0, 4.0]\n"
                        "arr[int] ce = [4]\n"
                        "arr[float] mask = rasClipMask(10, 6, cp, ce, RAS_NONZERO)\n"
                        + self.FULL +
                        "rasFillPathClip(px, 10, 6, all, allEnds, RAS_NONZERO, 9, 9, 9, 255, mask)\n")
        for y in range(6):
            for x in range(10):
                assert g[y][x] == (255 if 3 <= x < 7 and 1 <= y < 4 else 0), f"({x}, {y})"

    def test_a_full_shape_through_a_soft_clip_is_the_clip(self, tmp_path, cli_mod):
        """Where the shape covers every pixel completely, its coverage
        is exactly 1 and the product IS the clip's coverage. So a
        full-surface fill through a circular clip must be byte-for-byte
        a plain fill of that circle -- soft edges and all. This is the
        cross-check that the mask path and the fill path compute the
        same coverage."""
        clipped = self._alpha(tmp_path, cli_mod, 40, 40,
                              "arr[float] cp = []\narr[int] ce = []\n"
                              "rasCircle(cp, ce, 20.0, 19.3, 13.7)\n"
                              "arr[float] mask = rasClipMask(40, 40, cp, ce, RAS_NONZERO)\n"
                              + self.FULL +
                              "rasFillPathClip(px, 40, 40, all, allEnds, RAS_NONZERO, 9, 9, 9, 255, mask)\n")
        plain = self._alpha(tmp_path, cli_mod, 40, 40,
                            "arr[float] cp = []\narr[int] ce = []\n"
                            "rasCircle(cp, ce, 20.0, 19.3, 13.7)\n"
                            "rasFillPath(px, 40, 40, cp, ce, RAS_NONZERO, 9, 9, 9, 255)\n")
        assert clipped == plain
        assert any(0 < v < 255 for row in plain for v in row), (
            "the comparison has to include soft edges to mean anything")

    def test_an_open_clip_changes_nothing(self, tmp_path, cli_mod):
        clipped = self._alpha(tmp_path, cli_mod, 30, 30,
                              "arr[float] mask = rasClipAll(30, 30)\n"
                              "arr[float] t = [3.0, 2.5, 27.2, 9.0, 11.0, 28.4]\narr[int] te = [3]\n"
                              "rasFillPathClip(px, 30, 30, t, te, RAS_NONZERO, 9, 9, 9, 180, mask)\n")
        plain = self._alpha(tmp_path, cli_mod, 30, 30,
                            "arr[float] t = [3.0, 2.5, 27.2, 9.0, 11.0, 28.4]\narr[int] te = [3]\n"
                            "rasFillPath(px, 30, 30, t, te, RAS_NONZERO, 9, 9, 9, 180)\n")
        assert clipped == plain

    def test_clips_intersect(self, tmp_path, cli_mod):
        g = self._alpha(tmp_path, cli_mod, 12, 12,
                        "arr[float] a = [1.0, 1.0, 8.0, 1.0, 8.0, 8.0, 1.0, 8.0]\narr[int] ae = [4]\n"
                        "arr[float] b = [4.0, 4.0, 11.0, 4.0, 11.0, 11.0, 4.0, 11.0]\narr[int] be = [4]\n"
                        "arr[float] mask = rasClipMask(12, 12, a, ae, RAS_NONZERO)\n"
                        "rasClipIntersect(mask, 12, 12, b, be, RAS_NONZERO)\n"
                        "arr[float] all = [0.0, 0.0, 12.0, 0.0, 12.0, 12.0, 0.0, 12.0]\n"
                        "arr[int] allEnds = [4]\n"
                        "rasFillPathClip(px, 12, 12, all, allEnds, RAS_NONZERO, 9, 9, 9, 255, mask)\n")
        for y in range(12):
            for x in range(12):
                assert g[y][x] == (255 if 4 <= x < 8 and 4 <= y < 8 else 0), f"({x}, {y})"

    def test_intersecting_closes_rows_the_new_path_cannot_reach(self, tmp_path, cli_mod):
        """rasClipIntersect visits EVERY row. The obvious optimisation --
        only the new path's own rows -- would leave the others as open
        as before: an open clip narrowed to the top half would still let
        the bottom half through. Put back, that bug fails this test."""
        g = self._alpha(tmp_path, cli_mod, 8, 8,
                        "arr[float] mask = rasClipAll(8, 8)\n"
                        "arr[float] top = [0.0, 0.0, 8.0, 0.0, 8.0, 3.0, 0.0, 3.0]\narr[int] te = [4]\n"
                        "rasClipIntersect(mask, 8, 8, top, te, RAS_NONZERO)\n"
                        "arr[float] all = [0.0, 0.0, 8.0, 0.0, 8.0, 8.0, 0.0, 8.0]\n"
                        "arr[int] allEnds = [4]\n"
                        "rasFillPathClip(px, 8, 8, all, allEnds, RAS_NONZERO, 9, 9, 9, 255, mask)\n")
        for y in range(8):
            assert all(v == (255 if y < 3 else 0) for v in g[y]), f"row {y}: {g[y]}"

    def test_soft_edges_multiply_they_do_not_intersect(self, tmp_path, cli_mod):
        """The shape covers the LEFT half of column 0; the clip covers
        the RIGHT half. Geometrically they do not overlap at all, so a
        true intersection would draw nothing there. The mask product
        draws 0.5 x 0.5 = 0.25, alpha 64.

        That is asserted deliberately. It is the conflation that every
        mask-based compositor has, Cairo's clip included, and slice 5's
        docstring in raster.f says why it is the definition rather than
        an approximation of one. Pinning it stops a later change from
        "fixing" it into disagreement with the Cairo it replaces.

        The first version of this test put the two soft edges on
        PERPENDICULAR sides of the pixel, where the halves really do
        overlap in a quarter -- so it could not tell a product from an
        intersection, whatever its name said."""
        g = self._alpha(tmp_path, cli_mod, 3, 2,
                        "arr[float] cp = [0.5, 0.0, 3.0, 0.0, 3.0, 2.0, 0.5, 2.0]\narr[int] ce = [4]\n"
                        "arr[float] mask = rasClipMask(3, 2, cp, ce, RAS_NONZERO)\n"
                        "arr[float] s = [0.0, 0.0, 0.5, 0.0, 0.5, 2.0, 0.0, 2.0]\narr[int] se = [4]\n"
                        "rasFillPathClip(px, 3, 2, s, se, RAS_NONZERO, 9, 9, 9, 255, mask)\n")
        assert g[0][0] == 64, "0.5 x 0.5, though the halves do not overlap"
        assert g[0][1] == 0, "the clip is open here but the shape is absent"

    def test_a_clip_outside_the_surface_lets_nothing_through(self, tmp_path, cli_mod):
        g = self._alpha(tmp_path, cli_mod, 6, 6,
                        "arr[float] cp = [50.0, 50.0, 60.0, 50.0, 60.0, 60.0]\narr[int] ce = [3]\n"
                        "arr[float] mask = rasClipMask(6, 6, cp, ce, RAS_NONZERO)\n"
                        "arr[float] all = [0.0, 0.0, 6.0, 0.0, 6.0, 6.0, 0.0, 6.0]\n"
                        "arr[int] allEnds = [4]\n"
                        "rasFillPathClip(px, 6, 6, all, allEnds, RAS_NONZERO, 9, 9, 9, 255, mask)\n")
        assert all(v == 0 for row in g for v in row)

    def test_a_stroke_is_clipped_like_a_fill(self, tmp_path, cli_mod):
        """A stroke is a fill of its outline, so clipping it needs
        nothing new -- but that is a claim, and this checks it: a
        horizontal line clipped to its own left half."""
        g = self._alpha(tmp_path, cli_mod, 12, 8,
                        "arr[float] cp = [0.0, 0.0, 6.0, 0.0, 6.0, 8.0, 0.0, 8.0]\narr[int] ce = [4]\n"
                        "arr[float] mask = rasClipMask(12, 8, cp, ce, RAS_NONZERO)\n"
                        "arr[float] s = [1.0, 4.0, 11.0, 4.0]\narr[int] se = [2]\narr[int] sc = [0]\n"
                        "rasStrokePathClip(px, 12, 8, s, se, sc, 4.0, 9, 9, 9, 255, mask)\n")
        for y in range(8):
            for x in range(12):
                assert g[y][x] == (255 if 1 <= x < 6 and 2 <= y < 6 else 0), f"({x}, {y})"
