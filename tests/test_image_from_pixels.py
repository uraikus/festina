"""claude.md #346: `imageFromPixels(px, w, h):img`.

specification.md §17.3. An image built from a pixel buffer in one
call, for [runtime.md](../runtime.md)'s decoders, which produce
exactly that buffer.

It adds no capability, and the tests say so directly: every assertion
here could be satisfied by `blankImage` plus a `fillStyle`/`drawPixel`
per pixel, and one test builds the same image both ways and demands
they agree. What the builtin adds is that the loop costs about 18
million pixels a second -- roughly 115ms for a 1920x1080 image --
against one copy.
"""
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _run(tmp_path, cli_mod, source, expect_fail=False):
    from tests.conftest import compile_file_or_skip, _require_c_compiler
    src = tmp_path / "main.f"
    src.write_text(source, encoding="utf-8")
    out = tmp_path / "program"
    compile_file_or_skip(cli_mod, str(src), str(out), cc=_require_c_compiler())
    result = subprocess.run([str(out)], cwd=tmp_path, capture_output=True,
                             text=True, timeout=60,
                             env=dict(os.environ, DISPLAY=""))
    if not expect_fail:
        assert result.returncode == 0, result.stdout + result.stderr
    return result


class TestImageFromPixels:
    def test_it_builds_an_image_of_the_given_size(self, tmp_path, cli_mod):
        r = _run(tmp_path, cli_mod,
                 "arr[int] px = []\n"
                 "int i = 0\n"
                 "while i < (4 * 3 * 4) { px.push(0) i = i + 1 }\n"
                 "img out = imageFromPixels(px, 4, 3)\n"
                 "log(`${out.width} ${out.height}`)\n")
        assert r.stdout.strip() == "4 3"

    def test_the_pixels_are_the_ones_given(self, tmp_path, cli_mod):
        """Read back through getPixelColor, which is the only way a
        program can inspect one pixel -- and the same route the
        drawPixel path is checked by."""
        r = _run(tmp_path, cli_mod,
                 "arr[int] px = [255,0,0,255,  0,255,0,255,\n"
                 "               0,0,255,255,  255,255,0,255]\n"
                 "img out = imageFromPixels(px, 2, 2)\n"
                 "color red = '#ff0000'\n"
                 "color green = '#00ff00'\n"
                 "color blue = '#0000ff'\n"
                 "color yellow = '#ffff00'\n"
                 "log(out.getPixelColor(0, 0) == red)\n"
                 "log(out.getPixelColor(1, 0) == green)\n"
                 "log(out.getPixelColor(0, 1) == blue)\n"
                 "log(out.getPixelColor(1, 1) == yellow)\n")
        assert r.stdout.split() == ["true", "true", "true", "true"]

    def test_it_agrees_with_the_drawPixel_loop_it_replaces(self, tmp_path, cli_mod):
        """The claim that this adds no capability, asserted rather than
        stated. Both images are built from the same buffer -- one in a
        single call, one the long way -- and every pixel must match."""
        r = _run(tmp_path, cli_mod,
                 "int W = 7\n"
                 "int H = 5\n"
                 "arr[int] px = []\n"
                 "int i = 0\n"
                 "while i < (W * H) {\n"
                 "    px.push((i * 7) % 256)\n"
                 "    px.push((i * 13) % 256)\n"
                 "    px.push((i * 29) % 256)\n"
                 "    px.push(255)\n"
                 "    i = i + 1\n"
                 "}\n"
                 "img bulk = imageFromPixels(px, W, H)\n"
                 "img loop = blankImage(W, H)\n"
                 "int j = 0\n"
                 "while j < (W * H) {\n"
                 "    fillStyle(px[j*4], px[j*4+1], px[j*4+2])\n"
                 "    loop.drawPixel(j % W, Math.floorDiv(j, W))\n"
                 "    j = j + 1\n"
                 "}\n"
                 "int mismatches = 0\n"
                 "int y = 0\n"
                 "while y < H {\n"
                 "    int x = 0\n"
                 "    while x < W {\n"
                 "        if (bulk.getPixelColor(x, y) == loop.getPixelColor(x, y)) == false {\n"
                 "            mismatches = mismatches + 1\n"
                 "        }\n"
                 "        x = x + 1\n"
                 "    }\n"
                 "    y = y + 1\n"
                 "}\n"
                 "log(mismatches)\n")
        assert r.stdout.strip() == "0"

    def test_alpha_is_carried(self, tmp_path, cli_mod):
        """Four components per pixel, not three. A fully transparent
        pixel reads back as null, which is what getPixelColor answers
        for anything unpainted."""
        r = _run(tmp_path, cli_mod,
                 "arr[int] px = [255,0,0,255,  255,0,0,0]\n"
                 "img out = imageFromPixels(px, 2, 1)\n"
                 "color red = '#ff0000'\n"
                 "log(out.getPixelColor(0, 0) == red)\n"
                 "log(out.getPixelColor(1, 0) == null)\n")
        assert r.stdout.split() == ["true", "true"]

    def test_partial_alpha_is_premultiplied(self, tmp_path, cli_mod):
        """The subtlest line in the runtime, and the one a fully opaque
        test image cannot check.

        Cairo's ARGB32 is PREMULTIPLIED: white at half alpha is stored
        (128,128,128,128), not (255,255,255,128). getPixelColor
        un-premultiplies on the way out, so a correct round trip reads
        back white -- and storing straight alpha instead would divide
        255 by 128 on the way out and answer something else entirely.

        White at alpha 128 is chosen because it is one of the values
        that survives 8-bit premultiplied storage EXACTLY. Most do not:
        (200,100,50) comes back (199,100,50), which is the format's own
        precision and not a defect, and is why this asserts on a colour
        where the arithmetic is exact rather than on a tolerance the
        `color` type cannot express.
        """
        r = _run(tmp_path, cli_mod,
                 "arr[int] px = [255, 255, 255, 128]\n"
                 "img out = imageFromPixels(px, 1, 1)\n"
                 "color white = '#ffffff'\n"
                 "log(out.getPixelColor(0, 0) == white)\n")
        assert r.stdout.strip() == "true"

    @pytest.mark.parametrize("w,h,n,label", [
        (4, 3, 4 * 3 * 4 - 4, "one pixel short"),
        (4, 3, 4 * 3 * 4 + 4, "one pixel long"),
        (0, 3, 0, "zero width"),
        (4, 0, 0, "zero height"),
    ])
    def test_a_buffer_that_does_not_match_is_a_runtime_failure(
            self, w, h, n, label, tmp_path, cli_mod):
        """specification.md: not a silently wrong image. A length that
        disagrees with the dimensions means the caller computed one of
        them wrongly, and every pixel after the mistake would be
        shifted -- an image that looks almost right is the worst
        outcome available."""
        r = _run(tmp_path, cli_mod,
                 "arr[int] px = []\n"
                 "int i = 0\n"
                 f"while i < {n} {{ px.push(0) i = i + 1 }}\n"
                 f"img out = imageFromPixels(px, {w}, {h})\n"
                 "log('should not get here')\n",
                 expect_fail=True)
        assert r.returncode != 0, label
        assert "should not get here" not in r.stdout

    def test_components_are_clamped_like_fillStyle(self, tmp_path, cli_mod):
        """fillStyle(r, g, b) clamps out-of-range components rather
        than failing, and this takes the same values from the same
        kind of buffer, so it clamps too."""
        r = _run(tmp_path, cli_mod,
                 "arr[int] px = [300,0,0,255,  0,0,0 - 5,255]\n"
                 "img out = imageFromPixels(px, 2, 1)\n"
                 "color red = '#ff0000'\n"
                 "color black = '#000000'\n"
                 "log(out.getPixelColor(0, 0) == red)\n"
                 "log(out.getPixelColor(1, 0) == black)\n")
        assert r.stdout.split() == ["true", "true"]
