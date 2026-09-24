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
