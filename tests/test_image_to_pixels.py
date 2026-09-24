"""runtime.md phase 4: `img.toPixels():arr[int]`.

The read half of the pixel handoff, and the exact inverse of
`imageFromPixels(px, w, h)` (claude.md #346). A rasteriser written in
Festina has to see what it is drawing onto, and `getPixelColor()` is
one pixel per call across the language boundary.

**The round trip is the specification.** `imageFromPixels(a.toPixels(),
a.width, a.height)` must reproduce `a`, and most of what is asserted
here is some form of that. The format is the one `imageFromPixels`
already takes: four `int` per pixel -- R, G, B, A -- STRAIGHT alpha,
row-major from the top-left.

Straight alpha is the whole subtlety. Cairo stores ARGB32
premultiplied, so a half-transparent red is (128, 0, 0, 128) in memory
and (255, 0, 0, 128) in the buffer these two calls agree on. Getting
it backwards looks correct on every fully opaque pixel, which is most
of a test image -- so there is a test here that uses nothing but
half-transparent ones.
"""
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _run(tmp_path, cli_mod, source):
    from tests.conftest import compile_file_or_skip, _require_c_compiler
    src = tmp_path / "main.f"
    src.write_text(source, encoding="utf-8")
    out = tmp_path / "program"
    compile_file_or_skip(cli_mod, str(src), str(out), cc=_require_c_compiler())
    result = subprocess.run([str(out)], cwd=tmp_path, capture_output=True,
                             text=True, timeout=60,
                             env=dict(os.environ, DISPLAY=""))
    assert result.returncode == 0, result.stdout + result.stderr
    return result


class TestTheBuffer:
    def test_it_is_four_ints_per_pixel(self, tmp_path, cli_mod):
        r = _run(tmp_path, cli_mod,
                 "img a = blankImage(4, 3)\n"
                 "arr[int] px = a.toPixels()\n"
                 "log(px.length)\n")
        assert r.stdout.strip() == "48"

    def test_a_blank_image_is_fully_transparent(self, tmp_path, cli_mod):
        """blankImage is documented as fully transparent, and a
        transparent pixel reads back as four zeros rather than as
        getPixelColor's -1 'none' sentinel: this answers channels, not
        a `color`, and premultiplied storage has already multiplied any
        colour away."""
        r = _run(tmp_path, cli_mod,
                 "img a = blankImage(2, 2)\n"
                 "arr[int] px = a.toPixels()\n"
                 "int i = 0\n"
                 "int nonzero = 0\n"
                 "while i < px.length {\n"
                 "  if px[i] != 0 { nonzero = nonzero + 1 }\n"
                 "  i = i + 1\n"
                 "}\n"
                 "log(nonzero)\n")
        assert r.stdout.strip() == "0"

    def test_it_reads_row_major_from_the_top_left(self, tmp_path, cli_mod):
        """One red pixel at (1, 0) on a 3x2 image lands at index 4, not
        at index 12 -- which is what a bottom-up or column-major read
        would give."""
        r = _run(tmp_path, cli_mod,
                 "color red = '#ff0000'\n"
                 "img a = blankImage(3, 2)\n"
                 "a.drawPixel(1, 0, red)\n"
                 "arr[int] px = a.toPixels()\n"
                 "log(`${px[4]} ${px[5]} ${px[6]} ${px[7]}`)\n")
        assert r.stdout.strip() == "255 0 0 255"

    def test_the_channels_are_in_rgba_order(self, tmp_path, cli_mod):
        r = _run(tmp_path, cli_mod,
                 "color c = '#20a060'\n"
                 "img a = blankImage(1, 1)\n"
                 "a.drawPixel(0, 0, c)\n"
                 "arr[int] px = a.toPixels()\n"
                 "log(`${px[0]} ${px[1]} ${px[2]} ${px[3]}`)\n")
        assert r.stdout.strip() == "32 160 96 255"


class TestTheRoundTrip:
    """imageFromPixels(a.toPixels(), a.width, a.height) == a."""

    def test_a_drawn_image_survives_the_round_trip(self, tmp_path, cli_mod):
        r = _run(tmp_path, cli_mod,
                 "color red = '#ff0000'\n"
                 "color blue = '#0000ff'\n"
                 "img a = blankImage(8, 6)\n"
                 "a.drawRect(0, 0, 8, 6, red)\n"
                 "a.drawRect(2, 2, 3, 2, blue)\n"
                 "img b = imageFromPixels(a.toPixels(), a.width, a.height)\n"
                 "log(`${b.width} ${b.height}`)\n"
                 "int x = 0\n"
                 "int same = 0\n"
                 "while x < 8 {\n"
                 "  int y = 0\n"
                 "  while y < 6 {\n"
                 "    if a.getPixelColor(x, y) == b.getPixelColor(x, y) {\n"
                 "      same = same + 1\n"
                 "    }\n"
                 "    y = y + 1\n"
                 "  }\n"
                 "  x = x + 1\n"
                 "}\n"
                 "log(same)\n")
        assert r.stdout.splitlines() == ["8 6", "48"]

    def test_half_transparent_pixels_survive_it_too(self, tmp_path, cli_mod):
        """The premultiplication test, and it uses NOTHING opaque on
        purpose. Storing premultiplied values in the buffer instead of
        straight ones is invisible at alpha 255, which is what most of
        a test image is; at alpha 128 a red reads back as 128 instead
        of 255 and the round trip darkens the image every lap.

        White at alpha 128 because that value survives 8-bit
        premultiplied storage exactly -- the same reason claude.md
        #346's own premultiply test picks it. (200, 100, 50) comes back
        as (199, 100, 50), which is the format's precision rather than a
        defect, and `color` has no == with a tolerance to say so.
        """
        r = _run(tmp_path, cli_mod,
                 "color white = '#ffffff'\n"
                 "img a = blankImage(4, 4)\n"
                 "fillAlpha(0.5)\n"
                 "a.drawRect(0, 0, 4, 4, white)\n"
                 "fillAlpha(1.0)\n"
                 "arr[int] px = a.toPixels()\n"
                 "log(`${px[0]} ${px[1]} ${px[2]} ${px[3]}`)\n"
                 "img b = imageFromPixels(px, 4, 4)\n"
                 "arr[int] qx = b.toPixels()\n"
                 "log(`${qx[0]} ${qx[1]} ${qx[2]} ${qx[3]}`)\n")
        first, second = r.stdout.splitlines()
        assert first == "255 255 255 128", (
            "straight alpha: a half-transparent white is 255 in the "
            "buffer and 128 in Cairo's premultiplied storage")
        assert second == first, "the round trip must not darken it"

    def test_a_loaded_jpeg_round_trips_opaque(self, tmp_path, cli_mod):
        """claude.md #192: a JPEG loads onto a CAIRO_FORMAT_RGB24
        surface, whose top byte is unused and stored as 0 -- not an
        alpha channel. Reading it as alpha would make every pixel of
        every JPEG come back fully transparent, and the round trip
        would produce a blank image rather than the photograph."""
        fixtures = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "fixtures")
        (tmp_path / "gradient.jpg").write_bytes(
            open(os.path.join(fixtures, "gradient.jpg"), "rb").read())
        r = _run(tmp_path, cli_mod,
                 "img a = 'gradient.jpg'\n"
                 "arr[int] px = a.toPixels()\n"
                 "int i = 3\n"
                 "int opaque = 0\n"
                 "while i < px.length {\n"
                 "  if px[i] == 255 { opaque = opaque + 1 }\n"
                 "  i = i + 4\n"
                 "}\n"
                 "log(`${opaque} ${Math.floorDiv(px.length, 4)}`)\n"
                 "img b = imageFromPixels(px, a.width, a.height)\n"
                 "log(a.getPixelColor(0, 0) == b.getPixelColor(0, 0))\n")
        counts, same = r.stdout.splitlines()
        opaque, total = counts.split()
        assert opaque == total == "256", (
            "every pixel of an RGB24 surface is opaque; reading the "
            "unused top byte as alpha makes them all transparent")
        assert same == "true"


class TestItIsRejectedWhereItShouldBe:
    def test_it_takes_no_arguments(self, tmp_path, cli_mod, parser, semantic, errors):
        from festina.errors import CompileError
        program = parser.parse("img a = blankImage(1, 1)\nlog(a.toPixels(3).length)\n")
        with pytest.raises(CompileError) as caught:
            semantic.analyze(program, filename="main.f")
        assert "toPixels() expects no arguments" in str(caught.value)

    def test_it_is_not_a_field(self, tmp_path, cli_mod, parser, semantic, errors):
        """`a.toPixels` without the call parentheses names a method,
        and img's field access is strict rather than permissive -- see
        semantic.py's own note on the `return None` this replaced."""
        from festina.errors import CompileError
        program = parser.parse("img a = blankImage(1, 1)\nlog(a.toPixels)\n")
        with pytest.raises(CompileError) as caught:
            semantic.analyze(program, filename="main.f")
        assert "is a method on img" in str(caught.value)


class TestTheDeclareBlockStayedInStep:
    """claude.md #346 the hard way: `_runtime_declares()` is
    unconditional, so one added line changes the IR of every program
    this compiler emits, and `bootstrap/codegen.f` has to emit the same
    line or the differential goes red on all 124 compiling corpus
    files. It did, last time, on the prediction that nothing would
    break because no `.f` file used the new builtin."""

    def test_both_compilers_declare_it(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        declare = "declare ptr @festina_image_to_pixels(ptr)"
        with open(os.path.join(root, "festina", "codegen.py"),
                  encoding="utf-8") as fh:
            assert declare in fh.read()
        with open(os.path.join(root, "bootstrap", "codegen.f"),
                  encoding="utf-8") as fh:
            assert declare in fh.read()
