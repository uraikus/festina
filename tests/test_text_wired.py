"""runtime.md phase 5, slice 6: drawText, img.drawText and the two
measures, reaching font.f through text.f.

What is claimed, and how each claim is held to account:

- The default face -- sans-serif, regular, whole-pixel size, no scale
  or rotation -- is drawn from text.f's coverage EXACTLY: the alpha a
  drawText leaves on a transparent image is byte for byte the mask
  text.f computes, at the right offset. (Slices 3 and 4 already
  measured that coverage against FreeType, Cairo and the true outline;
  this is the claim that the wiring adds nothing and loses nothing.)
- Everything else still goes to Cairo UNCHANGED: bold, italic, another
  family, a scale or a rotation draw byte-identically whether or not
  FESTINA_CAIRO_TEXT=1 sends everything to Cairo.
- Colour, fillAlpha and text's indifference to gradients are the C
  side's, as they were.
- The font is linked only into programs that use text, and a
  component's insides cannot collide with a program's own names.
- Two threads drawing text at once get the same pixels as one.
"""
import os
import re
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from festina import imports as imports_mod   # noqa: E402

FONT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "runtime", "fonts", "DejaVuSans.ttf")


def _compile(tmp_path, cli_mod, source, name="main", with_components=()):
    from tests.conftest import compile_file_or_skip, _require_c_compiler
    for comp in with_components:
        src = os.path.join(imports_mod.RUNTIME_COMPONENT_DIR, comp)
        (tmp_path / comp).write_text(open(src, encoding="utf-8").read(), encoding="utf-8")
    (tmp_path / f"{name}.f").write_text(source, encoding="utf-8")
    out = tmp_path / name
    compile_file_or_skip(cli_mod, str(tmp_path / f"{name}.f"), str(out), cc=_require_c_compiler())
    return str(out)


def _run(tmp_path, binary, cairo_only=False):
    env = dict(os.environ, DISPLAY="")
    env.pop("FESTINA_CAIRO_TEXT", None)
    if cairo_only:
        env["FESTINA_CAIRO_TEXT"] = "1"
    r = subprocess.run([binary], cwd=tmp_path, capture_output=True, text=True, timeout=300, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout.splitlines()


def _pixels_program(setup, draw, w=200, h=60):
    """Draw onto a transparent image and print its pixels as one line
    of straight RGBA -- toPixels is the bulk read phase 4 built."""
    return (
        f"img a = blankImage({w}, {h})\n" + setup + draw +
        "arr[int] px = a.toPixels()\n"
        "text out = ''\n"
        "int i = 0\n"
        "while i < px.length {\n"
        "    out = `${out} ${px[i]}`\n"
        "    i = i + 1\n"
        "}\n"
        "log(out)\n")


def _rgba(line, w):
    v = [int(t) for t in line.split()]
    return [[tuple(v[(y * w + x) * 4:(y * w + x) * 4 + 4]) for x in range(w)]
            for y in range(len(v) // (4 * w))]


def _mask(tmp_path, cli_mod, text, px):
    """text.f's own answer, from a program that imports it directly and
    draws nothing -- so it links no text component of its own, and
    this is font.f, raster.f and text.f with no C in between."""
    d = tmp_path / "mask"
    d.mkdir(exist_ok=True)
    shutil.copy(FONT, d / "font.ttf")
    prog = _compile(d, cli_mod,
                    "import text.f\n"
                    "blob raw = 'font.ttf'\n"
                    "arr[int] bytes = []\n"
                    "int i = 0\n"
                    "while i < raw.length {\n"
                    "    bytes.push(raw.byteAt(i))\n"
                    "    i = i + 1\n"
                    "}\n"
                    f"arr[int] m = festinaTextMask('{text}', {px}, bytes)\n"
                    "text out = ''\n"
                    "int k = 0\n"
                    "while k < m.length {\n"
                    "    out = `${out} ${m[k]}`\n"
                    "    k = k + 1\n"
                    "}\n"
                    "log(out)\n",
                    with_components=("text.f", "font.f", "raster.f"))
    v = [int(t) for t in _run(d, prog)[0].split()]
    x0, y0, w, h = v[:4]
    return x0, y0, w, h, [v[4 + r * w:4 + (r + 1) * w] for r in range(h)]


class TestTheDefaultFaceIsFontF:

    @pytest.mark.parametrize("px", [9, 16, 31])
    def test_drawn_alpha_is_text_f_coverage_exactly(self, tmp_path, cli_mod, px):
        """Opaque black onto a transparent image leaves alpha equal to
        the mask, so the image IS the mask, shifted to the pen."""
        text = "Handgloves Été 42"
        x0, y0, w, h, mask = _mask(tmp_path, cli_mod, text, px)
        prog = _compile(tmp_path, cli_mod, _pixels_program(
            "fillStyle(0, 0, 0)\n", f"changeFont({px}, null, null)\na.drawText('{text}', 7, 40)\n",
            w=260, h=60))
        img = _rgba(_run(tmp_path, prog)[0], 260)
        seen = 0
        for y in range(60):
            for x in range(260):
                r, c = y - 40 - y0, x - 7 - x0
                want = mask[r][c] if 0 <= r < h and 0 <= c < w else 0
                assert img[y][x][3] == want, (x, y, img[y][x], want)
                seen += want > 0
        assert seen > 100, "the text has to be in the picture"

    def test_the_canvas_draws_what_an_image_draws(self, tmp_path, cli_mod, monkeypatch):
        """Same path, two targets: drawText onto the canvas and onto an
        img, saved and compared."""
        from tests.test_raster import _decode_png
        prog = _compile(tmp_path, cli_mod,
                        "clearCanvas()\n"
                        "fillStyle(20, 40, 60)\n"
                        "drawText('Canvas and image', 5, 30)\n"
                        "log(saveCanvas('canvas.png'))\n"
                        "img b = blankImage(800, 600)\n"
                        "b.drawText('Canvas and image', 5, 30)\n"
                        "log(b.save('img.png'))\n")
        assert _run(tmp_path, prog) == ["true", "true"]
        _, _, ca, na = _decode_png(str(tmp_path / "canvas.png"))
        _, _, ib, nb = _decode_png(str(tmp_path / "img.png"))
        for y in range(10, 40):
            for x in range(0, 200):
                a = tuple(ca[y][x * na:x * na + na])
                b = tuple(ib[y][x * nb:x * nb + nb])
                assert a == b, (x, y, a, b)

    def test_measures_do_not_depend_on_the_first_size_used(self, tmp_path, cli_mod):
        """decisions.md #351: under Cairo's toy API the first size a
        program used decided whether all of its text was hinted. Here
        the measure is font.f's, and the history does not matter."""
        a = _compile(tmp_path, cli_mod,
                     "changeFont(6, null, null)\nint x = measureTextHeight('Hello')\n"
                     "changeFont(16, null, null)\nlog(measureTextHeight('Hello'))\n"
                     "log(measureTextWidth('Hello'))\n", name="small_first")
        b = _compile(tmp_path, cli_mod,
                     "changeFont(16, null, null)\nlog(measureTextHeight('Hello'))\n"
                     "log(measureTextWidth('Hello'))\n", name="big_first")
        assert _run(tmp_path, a) == _run(tmp_path, b) == ["14", "40"]

    def test_empty_and_blank_text(self, tmp_path, cli_mod):
        prog = _compile(tmp_path, cli_mod, _pixels_program(
            "fillStyle(0, 0, 0)\n",
            "a.drawText('', 5, 30)\na.drawText('    ', 5, 30)\n"
            "log(measureTextWidth(''))\nlog(measureTextHeight('   '))\n", w=40, h=40))
        out = _run(tmp_path, prog)
        assert out[:2] == ["0", "0"]
        assert set(out[2].split()) == {"0"}, "nothing is inked"

    def test_the_switch_sends_text_back_to_cairo(self, tmp_path, cli_mod):
        """FESTINA_CAIRO_TEXT=1 is the comparison every other test here
        draws against, so it must actually change the path."""
        prog = _compile(tmp_path, cli_mod, _pixels_program(
            "fillStyle(0, 0, 0)\n", "a.drawText('Hello', 5, 40)\n"))
        assert _run(tmp_path, prog) != _run(tmp_path, prog, cairo_only=True)


class TestEverythingElseStaysWithCairo:
    """Each of these draws the same bytes whether or not the text path
    is allowed -- which is what "not taken" means, observably."""

    @pytest.mark.parametrize("name,setup", [
        ("bold", "font f = 'bold 20px'\nchangeFont(f)\n"),
        ("italic", "font f = 'italic 20px'\nchangeFont(f)\n"),
        ("another family", "font f = '20px serif'\nchangeFont(f)\n"),
        ("rotated", "changeFont(20, null, null)\na.rotate(10.0)\n"),
        ("scaled", "changeFont(20, null, null)\na.scale(2.0, 2.0)\n"),
    ])
    def test_it_draws_exactly_what_cairo_draws(self, tmp_path, cli_mod, name, setup):
        """Two claims, checked separately because CI's Windows job
        separated them. First the PATH: text.f cannot draw bold, italic,
        another family, a scale or a rotation, so had it drawn one of
        these it would have drawn the plain line -- regular, unscaled,
        unrotated -- and that is exactly what this must not equal. That
        needs nothing from Cairo. Then the PIXELS: byte for byte what
        Cairo alone draws. On Windows the italic case matched the path
        claim (only 6 channels from Cairo -- text.f's plain line differs
        in hundreds) and failed the pixel one, with Cairo agreeing with
        itself; the runs now alternate, Cairo first, so a difference
        that follows whichever process ran first shows as that and not
        as "ours"."""
        prog = _compile(tmp_path, cli_mod, _pixels_program(
            "fillStyle(0, 0, 0)\n" + setup, "a.drawText('Hello', 5, 40)\n"), name="fallback")
        plain = _compile(tmp_path, cli_mod, _pixels_program(
            "fillStyle(0, 0, 0)\nchangeFont(20, null, null)\n", "a.drawText('Hello', 5, 40)\n"),
            name="plain")
        # A throwaway Cairo run first. On CI's Windows job the FIRST
        # process to draw italic differed from every later one -- Cairo
        # against Cairo, 6 channels -- while the four runs after it all
        # agreed, ours included (run 180). Why is not established; a
        # font cache warming on a fresh runner would explain it and has
        # not been checked. The claim tested is that the fallback draws
        # what Cairo draws, and that needs Cairo in a steady state.
        _run(tmp_path, prog, cairo_only=True)
        cairo1 = _run(tmp_path, prog, cairo_only=True)
        ours1 = _run(tmp_path, prog)
        cairo2 = _run(tmp_path, prog, cairo_only=True)
        ours2 = _run(tmp_path, prog)
        text_f_plain = _run(tmp_path, plain)
        assert any(t != "0" for t in ours1[0].split()), "something has to be drawn"

        differ = lambda a, b: sum(x != y for x, y in zip(a[0].split(), b[0].split()))
        assert differ(ours1, text_f_plain) > 100, (
            f"{name}: drew text.f's plain line ({differ(ours1, text_f_plain)} channels "
            f"from it) -- the path took what it cannot draw")
        if not (ours1 == cairo1 == cairo2 == ours2):
            pytest.fail(f"{name}: in run order cairo1, ours1, cairo2, ours2 -- "
                        f"ours1/cairo1 {differ(ours1, cairo1)}, ours2/cairo2 {differ(ours2, cairo2)}, "
                        f"cairo1/cairo2 {differ(cairo1, cairo2)}, ours1/ours2 {differ(ours1, ours2)}, "
                        f"cairo1/ours2 {differ(cairo1, ours2)} channels differ")

    def test_a_whole_pixel_translation_is_ours_and_moves_it_exactly(self, tmp_path, cli_mod):
        prog = _compile(tmp_path, cli_mod, _pixels_program(
            "fillStyle(0, 0, 0)\nchangeFont(18, null, null)\n",
            "a.drawText('Moved', 10, 30)\n"
            "img b = blankImage(200, 60)\nb.translate(5, 7)\nb.drawText('Moved', 5, 23)\n"
            "arr[int] q = b.toPixels()\n"
            "text o2 = ''\n"
            "int j = 0\n"
            "while j < q.length {\n"
            "    o2 = `${o2} ${q[j]}`\n"
            "    j = j + 1\n"
            "}\n"
            "log(o2)\n"))
        out = _run(tmp_path, prog)
        assert out[0] == out[1], "(5, 23) moved by (5, 7) is (10, 30)"
        assert out != _run(tmp_path, prog, cairo_only=True), "and it is text.f that drew it"


class TestTheFillIsTheCSides:

    def test_colour_and_fill_alpha(self, tmp_path, cli_mod):
        text = "Alpha"
        x0, y0, w, h, mask = _mask(tmp_path, cli_mod, text, 24)
        prog = _compile(tmp_path, cli_mod, _pixels_program(
            "fillStyle(255, 0, 0)\nfillAlpha(0.5)\nchangeFont(24, null, null)\n",
            f"a.drawText('{text}', 10, 40)\n"))
        img = _rgba(_run(tmp_path, prog)[0], 200)
        for r in range(h):
            for c in range(w):
                px = img[40 + y0 + r][10 + x0 + c]
                want = mask[r][c] / 2
                assert abs(px[3] - want) <= 1, (c, r, px, mask[r][c])
                if px[3] >= 8:
                    assert px[0] >= 250 and px[1] <= 5 and px[2] <= 5, px

    def test_text_still_ignores_a_gradient(self, tmp_path, cli_mod):
        """runtime.md phase 5 records this as an open behaviour
        question -- api.md says a gradient replaces the flat fill -- and
        until it is decided, the port keeps what the runtime did."""
        prog = _compile(tmp_path, cli_mod, _pixels_program(
            "color red = '#ff0000'\ncolor blue = '#0000ff'\n"
            "fillStyle(0, 160, 0)\nfillLinearGradient(0, 0, red, 200, 0, blue)\n"
            "changeFont(30, null, null)\n",
            "a.drawText('WWWW', 5, 40)\n"))
        img = _rgba(_run(tmp_path, prog)[0], 200)
        solid = [p for row in img for p in row if p[3] == 255]
        assert len(solid) > 50
        assert all(p[:3] == (0, 160, 0) for p in solid)


class TestLinking:

    def _symbols(self, tmp_path, cli_mod, source, name):
        binary = _compile(tmp_path, cli_mod, source, name=name)
        listing = subprocess.run(["nm", binary], capture_output=True, text=True, timeout=120)
        if listing.returncode != 0:
            pytest.skip("nm cannot read this platform's binaries")
        return binary, listing.stdout

    def test_the_font_is_linked_only_where_text_is_used(self, tmp_path, cli_mod):
        plain, syms_plain = self._symbols(tmp_path, cli_mod,
                                          "img a = blankImage(4, 4)\na.drawRect(0, 0, 2, 2)\n",
                                          "shapes")
        measured, syms_text = self._symbols(tmp_path, cli_mod,
                                            "log(measureTextWidth('x'))\n", "measures")
        assert "festinaTextMask" not in syms_plain
        assert "festinaTextMask" in syms_text
        assert os.path.getsize(measured) - os.path.getsize(plain) > 700_000, \
            "the font is in the program that measures text"

    def test_a_program_may_use_the_components_names(self, tmp_path, cli_mod):
        """font.f, raster.f and text.f are internal to the component
        (festina/cli.py's _COMPONENT_EXPORTS), so a program with its own
        fntHas, rasClamp and FNT_IN links and draws."""
        prog = _compile(tmp_path, cli_mod, _pixels_program(
            "int FNT_IN = 3\n"
            "int func fntHas(a:int) { return a + 1 }\n"
            "int func rasClamp(a:int) { return a * 2 }\n"
            "fillStyle(0, 0, 0)\n",
            "a.drawText('Names', 5, 40)\nlog(`${fntHas(FNT_IN)} ${rasClamp(4)}`)\n"))
        out = _run(tmp_path, prog)
        assert out[0] == "4 8"
        assert any(t != "0" for t in out[1].split())


class TestThreads:

    def test_two_workers_drawing_text_get_what_one_thread_gets(self, tmp_path, cli_mod):
        """text.f's state is global, so calls are serialised in C. Two
        workers each draw forty lines into their own image at the same
        time; each image must equal the one the main thread draws."""
        body = ("    on message(worker:thread, msg:img?) {\n"
                "        int k = 0\n"
                "        while k < 40 {\n"
                "            msg.drawText(`line ${k} of the worker`, 2 + (k % 7), 12 + (k * 9))\n"
                "            k = k + 1\n"
                "        }\n"
                "    }\n")
        prog = _compile(tmp_path, cli_mod,
                        "thread A {\n" + body + "}\n"
                        "thread B {\n" + body + "}\n"
                        "fillStyle(0, 0, 0)\n"
                        "changeFont(11, null, null)\n"
                        "img? one = blankImage(220, 380)\n"
                        "img? two = blankImage(220, 380)\n"
                        "A.postMessage(one)\n"
                        "B.postMessage(two)\n"
                        "A.drain()\n"
                        "B.drain()\n"
                        "img ref = blankImage(220, 380)\n"
                        "int k = 0\n"
                        "while k < 40 {\n"
                        "    ref.drawText(`line ${k} of the worker`, 2 + (k % 7), 12 + (k * 9))\n"
                        "    k = k + 1\n"
                        "}\n"
                        "log(one.save('one.png'))\n"
                        "log(two.save('two.png'))\n"
                        "log(ref.save('ref.png'))\n"
                        "free one\n"
                        "free two\n"
                        "close(0)\n")
        assert _run(tmp_path, prog) == ["true", "true", "true"]
        ref = (tmp_path / "ref.png").read_bytes()
        assert (tmp_path / "one.png").read_bytes() == ref
        assert (tmp_path / "two.png").read_bytes() == ref
