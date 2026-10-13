"""runtime.md phase 7, slice 8: text in any family, bold and italic.

Two layers, checked separately.

* **The synthesis** (text.f): bold thickens an outline by px/24 and keeps its
  left edge and baseline; italic shears it by 0.2126. Both are FreeType's
  FT_GlyphSlot_Embolden and FT_GlyphSlot_Oblique, so FreeType is the oracle,
  glyph by glyph, with measured bounds. Cairo never was one: its toy API drew
  italic upright (runtime.md, phase 5) and bold was whatever the machine had.
* **The plumbing** (the C runtime): which face a family, a list or a style
  chooses, that what is found is what is drawn, and that what is not found
  or cannot be read is the bundled face. Nothing here calls Cairo.
"""
import os
import shutil
import struct
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.test_fontscan import _font as _renamed, _put   # noqa: E402
from tests.test_font import _build_ttc, _font as _system_font, _tables   # noqa: E402
from tests.test_text_wired import (_compile, _pixels_program, _rgba, FONT,   # noqa: E402
                                   )

_MASKS = r"""
import text.f
blob raw = 'font.ttf'
arr[int] bytes = []
int i = 0
while i < raw.length {
    bytes.push(raw.byteAt(i))
    i = i + 1
}
// argv: then groups of five: text px face index style
int a = 1
while a + 4 < argv.length {
    text s = argv[a]
    int px = argv[a + 1].toInt()
    int face = argv[a + 2].toInt()
    int index = argv[a + 3].toInt()
    int style = argv[a + 4].toInt()
    arr[int] m = festinaTextMask(s, px, bytes, face, index, style)
    int w = festinaTextWidth(s, px, bytes, face, index, style)
    int h = festinaTextHeight(s, px, bytes, face, index, style)
    text out = `M ${w} ${h}`
    int k = 0
    while k < m.length {
        out = `${out} ${m[k]}`
        k = k + 1
    }
    log(out)
    a = a + 5
}
"""


@pytest.fixture(scope="module")
def mask_program(tmp_path_factory):
    from festina import cli
    d = tmp_path_factory.mktemp("maskprog")
    from festina import imports as imports_mod
    for comp in ("text.f", "font.f", "raster.f"):
        shutil.copy(os.path.join(imports_mod.RUNTIME_COMPONENT_DIR, comp), d / comp)
    (d / "main.f").write_text(_MASKS, encoding="utf-8")
    from tests.conftest import compile_file_or_skip, _require_c_compiler
    compile_file_or_skip(cli, str(d / "main.f"), str(d / "prog"), cc=_require_c_compiler())
    return str(d / "prog")


def _masks(mask_program, tmp_path, font_bytes, jobs):
    """jobs: (text, px, face, index, style) -> [(width, height, x0, y0, w, h, rows)]"""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "font.ttf").write_bytes(font_bytes)
    args = [str(x) for j in jobs for x in j]
    r = subprocess.run([mask_program, *args], cwd=tmp_path, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    out = []
    for line in r.stdout.splitlines():
        t = line.split()
        width, height = int(t[1]), int(t[2])
        v = list(map(int, t[3:]))
        x0, y0, w, h = v[:4]
        out.append((width, height, x0, y0, w, h, [v[4 + r_ * w:4 + (r_ + 1) * w] for r_ in range(h)]))
    return out


# ---- the synthesis against FreeType ----

_FT = r"""
#include <ft2build.h>
#include FT_FREETYPE_H
#include FT_OUTLINE_H
#include FT_SYNTHESIS_H
#include <stdio.h>
#include <stdlib.h>
/* argv: font px style codepoint... ; per glyph: cp advance26.6 left top w h values */
int main(int argc, char **argv) {
    FT_Library lib; FT_Face f;
    FT_Init_FreeType(&lib);
    if (FT_New_Face(lib, argv[1], 0, &f)) return 1;
    int px = atoi(argv[2]), style = atoi(argv[3]);
    FT_Set_Pixel_Sizes(f, 0, px);
    for (int a = 4; a < argc; a++) {
        long cp = strtol(argv[a], 0, 0);
        FT_UInt gi = FT_Get_Char_Index(f, cp);
        if (FT_Load_Glyph(f, gi, FT_LOAD_NO_HINTING | FT_LOAD_NO_BITMAP)) { printf("LOADFAIL\n"); continue; }
        if (style & 1) FT_GlyphSlot_Embolden(f->glyph);
        if (style & 2) FT_GlyphSlot_Oblique(f->glyph);
        long adv = f->glyph->advance.x;
        FT_Render_Glyph(f->glyph, FT_RENDER_MODE_NORMAL);
        FT_Bitmap *b = &f->glyph->bitmap;
        printf("%ld %ld %d %d %u %u", cp, adv, f->glyph->bitmap_left, f->glyph->bitmap_top, b->width, b->rows);
        for (unsigned y = 0; y < b->rows; y++)
            for (unsigned x = 0; x < b->width; x++) printf(" %d", b->buffer[y * b->pitch + x]);
        printf("\n");
    }
    return 0;
}
"""


@pytest.fixture(scope="module")
def freetype(tmp_path_factory):
    cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    try:
        flags = subprocess.run(["pkg-config", "--cflags", "--libs", "freetype2"],
                               capture_output=True, text=True, check=True).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("FreeType (pkg-config freetype2) is not installed")
    d = tmp_path_factory.mktemp("ftsyn")
    (d / "ft.c").write_text(_FT)
    subprocess.run([cc, str(d / "ft.c"), "-o", str(d / "ft")] + flags, check=True)

    def run(font, px, style, chars):
        out = subprocess.run([str(d / "ft"), font, str(px), str(style)] + [hex(ord(c)) for c in chars],
                             capture_output=True, text=True, check=True).stdout.splitlines()
        res = []
        for line in out:
            t = list(map(int, line.split()))
            adv, left, top, w, h = t[1:6]
            res.append((adv, left, -top, w, h, t[6:]))
        return res
    return run


def _canvas(x0, y0, w, h, vals):
    return {(x0 + c, y0 + r): vals[r * w + c] for r in range(h) for c in range(w)}


CHARS = "lHagW@i"
SIZES = [12, 20, 33]


@pytest.mark.parametrize("style,max_diff,mean_diff,ink", [
    (2, 20, 2.5, 0.015),          # italic: a shear, the same arithmetic
    (1, 80, 5.5, 0.035),          # bold: a stroke's miters against FreeType's offsetting (measured: 3.0% worst, "@" at 12px)
    (3, 80, 5.5, 0.035),
])
def test_synthesized_glyphs_match_freetype(mask_program, freetype, tmp_path, style, max_diff, mean_diff, ink):
    """Every glyph of a set, at three sizes: the coverage agrees with what
    FreeType renders for the same synthetic outline -- per pixel within a
    bound, in total ink within a few percent, and (the part that cannot be
    wrong by a little) the baseline and left edge of bold stay where the
    plain glyph's are."""
    font = open(FONT, "rb").read()
    jobs = [(c, px, 0, 0, style) for px in SIZES for c in CHARS]
    ours = _masks(mask_program, tmp_path, font, jobs)
    k = 0
    for px in SIZES:
        theirs = freetype(FONT, px, style, CHARS)
        for ci, c in enumerate(CHARS):
            _, _, x0, y0, w, h, rows = ours[k]
            k += 1
            adv, left, top, fw, fh, fvals = theirs[ci]
            a = _canvas(x0, y0, w, h, [v for r in rows for v in r])
            b = _canvas(left, top, fw, fh, fvals)
            keys = set(a) | set(b)
            diffs = [abs(a.get(p, 0) - b.get(p, 0)) for p in keys]
            where = (c, px, style)
            assert max(diffs) <= max_diff, (where, max(diffs))
            assert sum(diffs) / len(diffs) <= mean_diff, (where, sum(diffs) / len(diffs))
            sa, sb = sum(a.values()), sum(b.values())
            assert abs(sa - sb) <= ink * sb, (where, sa / 255, sb / 255)


def test_bold_keeps_the_baseline_and_the_left_edge(mask_program, tmp_path):
    font = open(FONT, "rb").read()
    for c in "Hlxm":
        plain, bold = _masks(mask_program, tmp_path, font, [(c, 24, 0, 0, 0), (c, 24, 0, 0, 1)])

        def ink_box(m):
            _, _, x0, y0, w, h, rows = m
            pts = [(x0 + cx, y0 + cy) for cy in range(h) for cx in range(w) if rows[cy][cx] > 127]
            return min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)
        px0, py0, px1, py1 = ink_box(plain)
        bx0, by0, bx1, by1 = ink_box(bold)
        assert bx0 == px0, (c, "left edge")
        assert by1 == py1, (c, "baseline")
        assert bx1 >= px1 and by0 <= py0, (c, "grows right and up")
        # 24px thickens by one pixel; a diagonal stroke's end, mitred, reaches a little further
        assert 0 <= bx1 - px1 <= 3 and 0 <= py0 - by0 <= 3, (c, "by about a pixel at 24px")


@pytest.mark.parametrize("px", [10, 11, 13, 14, 20, 25, 32])
def test_bold_advances_are_freetypes_where_it_and_this_agree(mask_program, freetype, tmp_path, px):
    """Advance grows by floor(px * 64 / 24) in 26.6 units. FreeType 2.13
    adds one unit less when that division is exact (px a multiple of 3), so
    the sizes here are the others, where a whole string measures the same."""
    font = open(FONT, "rb").read()
    text = "Handgloves 42 @W"
    w = _masks(mask_program, tmp_path, font, [(text, px, 0, 0, 1)])[0][0]
    adv = freetype(FONT, px, 1, text)
    pen = 0
    for a, *_ in adv:
        pen += (a + 32) >> 6
    assert w == pen, (px, w, pen)


@pytest.mark.parametrize("px", [9, 12, 15, 18, 24, 36])
def test_bold_advances_differ_by_at_most_a_pixel_a_glyph_where_freetype_is_one_unit_short(
        mask_program, freetype, tmp_path, px):
    font = open(FONT, "rb").read()
    text = "Handgloves 42 @W"
    w = _masks(mask_program, tmp_path, font, [(text, px, 0, 0, 1)])[0][0]
    pen = sum((a + 32) >> 6 for a, *_ in freetype(FONT, px, 1, text))
    assert 0 <= w - pen <= len(text), (px, w, pen)


def test_italic_changes_no_advance_and_no_height(mask_program, tmp_path):
    font = open(FONT, "rb").read()
    plain, italic = _masks(mask_program, tmp_path, font, [("Hamburgefonts", 24, 0, 0, 0),
                                                           ("Hamburgefonts", 24, 0, 0, 2)])
    assert plain[0] == italic[0] and plain[1] == italic[1]
    assert italic[4] > plain[4], "the slant makes the ink wider"


def test_style_zero_is_the_plain_face_exactly(mask_program, tmp_path):
    font = open(FONT, "rb").read()
    a = _masks(mask_program, tmp_path / "a", font, [("Hello", 20, 0, 0, 0)])
    b = _masks(mask_program, tmp_path / "b", font, [("Hello", 20, 7, 0, 0)])
    assert a == b, "the face id only names the cache"


def test_two_faces_in_one_run_do_not_share_a_cache(mask_program, tmp_path):
    """Face 0 is Sans; face 1, asked for the same glyphs, is a different
    font's bytes: its masks must be its own, and Sans's must come back
    unchanged after."""
    sans = open(FONT, "rb").read()
    serif = open(_system_font("DejaVuSerif.ttf"), "rb").read()
    tmp_path.mkdir(parents=True, exist_ok=True)
    first = _masks(mask_program, tmp_path / "a", sans, [("Rag", 20, 0, 0, 0)])[0]
    other = _masks(mask_program, tmp_path / "b", serif, [("Rag", 20, 0, 0, 0)])[0]
    assert first != other
    # one process, two faces: the font file is fixed per process, so the
    # two faces are told apart by id alone -- the SAME bytes under two ids
    # must give the same masks (a cache miss, not a mix-up) and a repeat hits
    both = _masks(mask_program, tmp_path / "c", sans, [("Rag", 20, 0, 0, 0), ("Rag", 20, 1, 0, 0),
                                                       ("Rag", 20, 0, 0, 0)])
    assert both[0] == both[1] == both[2] == first


# ---- the plumbing: which face the runtime chooses ----

PEN_X, PEN_Y, W, H = 7, 40, 300, 70
TEXT = "Rag Handgloves 42"


def _drawn(tmp_path, cli_mod, setup, name="p", env=None, extra=""):
    """Draw TEXT in black on a transparent image, print its alpha plane
    and the two measures. Returns (alpha rows, width, height)."""
    src = (f"img a = blankImage({W}, {H})\n" + "fillStyle(0, 0, 0)\n" + setup +
           f"a.drawText('{TEXT}', {PEN_X}, {PEN_Y})\n" + extra +
           "arr[int] px = a.toPixels()\n"
           "text out = `${measureTextWidth('" + TEXT + "')} ${measureTextHeight('" + TEXT + "')}`\n"
           "int i = 3\n"
           "while i < px.length {\n"
           "    out = `${out} ${px[i]}`\n"
           "    i = i + 4\n"
           "}\n"
           "log(out)\n")
    prog = _compile(tmp_path, cli_mod, src, name=name)
    e = dict(os.environ, DISPLAY="")
    e.pop("FESTINA_CAIRO_TEXT", None)
    e.update(env or {})
    r = subprocess.run([prog], cwd=tmp_path, capture_output=True, text=True, timeout=120, env=e)
    assert r.returncode == 0, r.stdout + r.stderr
    t = list(map(int, r.stdout.split()))
    return [t[2 + y * W:2 + (y + 1) * W] for y in range(H)], t[0], t[1]


def _want(mask):
    """The alpha plane a mask leaves when drawn with its pen at (PEN_X, PEN_Y)."""
    width, height, x0, y0, w, h, rows = mask
    grid = [[0] * W for _ in range(H)]
    for r in range(h):
        for c in range(w):
            y, x = PEN_Y + y0 + r, PEN_X + x0 + c
            if 0 <= y < H and 0 <= x < W:
                grid[y][x] = rows[r][c]
    return grid, width, height


def _ink(grid):
    return sum(1 for row in grid for v in row if v)


@pytest.fixture
def fonts(tmp_path):
    """Directory helpers: a font directory built per test."""
    d = tmp_path / "fonts"
    d.mkdir()
    return d


def _serif():
    return _system_font("DejaVuSerif.ttf")


def _serif_bold():
    return _system_font("DejaVuSerif-Bold.ttf")


def test_a_family_that_is_not_found_draws_exactly_the_default_face(tmp_path, cli_mod, fonts, mask_program):
    plain = _drawn(tmp_path, cli_mod, "changeFont(20, null, null)\n", name="plain",
                   env={"FESTINA_FONT_DIRS": str(fonts)})
    missing = _drawn(tmp_path, cli_mod, "changeFont(20, null, 'Nonexistent Family')\n", name="missing",
                     env={"FESTINA_FONT_DIRS": str(fonts)})
    assert missing == plain
    assert _ink(plain[0]) > 300
    want = _want(_masks(mask_program, tmp_path / "m", open(FONT, "rb").read(), [(TEXT, 20, 0, 0, 0)])[0])
    assert plain == want


def test_bold_and_italic_of_the_default_face_are_synthesized(tmp_path, cli_mod, fonts, mask_program):
    sans = open(FONT, "rb").read()
    for style, bits in (("bold", 1), ("italic", 2), ("bold italic", 3)):
        got = _drawn(tmp_path, cli_mod, f"changeFont(20, '{style}', null)\n", name="s" + str(bits),
                     env={"FESTINA_FONT_DIRS": str(fonts)})
        want = _want(_masks(mask_program, tmp_path / f"m{bits}", sans, [(TEXT, 20, 0, 0, bits)])[0])
        assert got == want, style
    plain = _drawn(tmp_path, cli_mod, "changeFont(20, null, null)\n", name="pl", env={"FESTINA_FONT_DIRS": str(fonts)})
    italic = _drawn(tmp_path, cli_mod, "changeFont(20, 'italic', null)\n", name="it", env={"FESTINA_FONT_DIRS": str(fonts)})
    assert italic[0] != plain[0] and italic[1] == plain[1], "italic is visible now, and changes no advance"


def test_a_family_that_is_found_is_the_face_drawn(tmp_path, cli_mod, fonts, mask_program):
    _put(fonts, "fixture.ttf", _renamed("Fixture Serif", base=_serif()))
    got = _drawn(tmp_path, cli_mod, "changeFont(20, null, 'Fixture Serif')\n", env={"FESTINA_FONT_DIRS": str(fonts)})
    want = _want(_masks(mask_program, tmp_path / "m", open(_serif(), "rb").read(), [(TEXT, 20, 0, 0, 0)])[0])
    assert got == want
    default = _want(_masks(mask_program, tmp_path / "d", open(FONT, "rb").read(), [(TEXT, 20, 0, 0, 0)])[0])
    assert got != default, "a serif face is not the sans one"


def test_a_real_bold_is_used_and_nothing_is_synthesized_on_it(tmp_path, cli_mod, fonts, mask_program):
    _put(fonts, "r.ttf", _renamed("Fixture Serif", 400, base=_serif()))
    _put(fonts, "b.ttf", _renamed("Fixture Serif", 700, base=_serif_bold()))
    got = _drawn(tmp_path, cli_mod, "changeFont(20, 'bold', 'Fixture Serif')\n", env={"FESTINA_FONT_DIRS": str(fonts)})
    want = _want(_masks(mask_program, tmp_path / "m", open(_serif_bold(), "rb").read(), [(TEXT, 20, 0, 0, 0)])[0])
    assert got == want


def test_what_the_found_family_lacks_is_synthesized_on_the_face_that_is_there(tmp_path, cli_mod, fonts, mask_program):
    _put(fonts, "r.ttf", _renamed("Fixture Serif", 400, base=_serif()))
    serif = open(_serif(), "rb").read()
    for style, bits in (("bold", 1), ("italic", 2), ("bold italic", 3)):
        got = _drawn(tmp_path, cli_mod, f"changeFont(20, '{style}', 'Fixture Serif')\n", name="y" + str(bits),
                     env={"FESTINA_FONT_DIRS": str(fonts)})
        want = _want(_masks(mask_program, tmp_path / f"m{bits}", serif, [(TEXT, 20, 0, 0, bits)])[0])
        assert got == want, style


def test_a_real_italic_is_chosen_over_the_regular_and_not_slanted_again(tmp_path, cli_mod, fonts, mask_program):
    """The 'italic' face here has upright outlines (it is the regular
    renamed); if the runtime synthesized on top of it the picture would
    be the slanted one."""
    _put(fonts, "r.ttf", _renamed("Fixture Serif", 400, base=_serif()))
    _put(fonts, "i.ttf", _renamed("Fixture Serif", 400, True, base=_serif()))
    got = _drawn(tmp_path, cli_mod, "changeFont(20, 'italic', 'Fixture Serif')\n", env={"FESTINA_FONT_DIRS": str(fonts)})
    want = _want(_masks(mask_program, tmp_path / "m", open(_serif(), "rb").read(), [(TEXT, 20, 0, 0, 0)])[0])
    assert got == want


def test_a_list_takes_the_first_family_that_exists(tmp_path, cli_mod, fonts, mask_program):
    _put(fonts, "f.ttf", _renamed("Fixture Serif", base=_serif()))
    got = _drawn(tmp_path, cli_mod, "changeFont(20, null, '\"Nope One\", Nope Two, Fixture Serif, sans-serif')\n",
                 env={"FESTINA_FONT_DIRS": str(fonts)})
    want = _want(_masks(mask_program, tmp_path / "m", open(_serif(), "rb").read(), [(TEXT, 20, 0, 0, 0)])[0])
    assert got == want


def test_serif_and_monospace_find_their_listed_families(tmp_path, cli_mod, fonts, mask_program):
    _put(fonts, "t.ttf", _renamed("Times New Roman", base=_serif()))
    _put(fonts, "c.ttf", _renamed("Courier New", base=_system_font("DejaVuSerif-Bold.ttf")))
    serif = _drawn(tmp_path, cli_mod, "changeFont(20, null, 'serif')\n", name="a", env={"FESTINA_FONT_DIRS": str(fonts)})
    mono = _drawn(tmp_path, cli_mod, "changeFont(20, null, 'monospace')\n", name="b", env={"FESTINA_FONT_DIRS": str(fonts)})
    assert serif == _want(_masks(mask_program, tmp_path / "m1", open(_serif(), "rb").read(), [(TEXT, 20, 0, 0, 0)])[0])
    assert mono == _want(_masks(mask_program, tmp_path / "m2", open(_serif_bold(), "rb").read(), [(TEXT, 20, 0, 0, 0)])[0])


def test_serif_with_nothing_on_its_list_is_the_default_face(tmp_path, cli_mod, fonts):
    _put(fonts, "u.ttf", _renamed("Unrelated", base=_serif()))
    a = _drawn(tmp_path, cli_mod, "changeFont(20, null, 'serif')\n", name="a", env={"FESTINA_FONT_DIRS": str(fonts)})
    b = _drawn(tmp_path, cli_mod, "changeFont(20, null, null)\n", name="b", env={"FESTINA_FONT_DIRS": str(fonts)})
    assert a == b


def test_a_face_in_a_collection_is_the_one_asked_for(tmp_path, cli_mod, fonts, mask_program):
    _put(fonts, "set.ttc", _build_ttc([_renamed("Alpha Face", base=FONT), _renamed("Beta Face", base=_serif())]))
    beta = _drawn(tmp_path, cli_mod, "changeFont(20, null, 'Beta Face')\n", name="b", env={"FESTINA_FONT_DIRS": str(fonts)})
    alpha = _drawn(tmp_path, cli_mod, "changeFont(20, null, 'Alpha Face')\n", name="a", env={"FESTINA_FONT_DIRS": str(fonts)})
    assert beta == _want(_masks(mask_program, tmp_path / "m1", open(_serif(), "rb").read(), [(TEXT, 20, 0, 0, 0)])[0])
    assert alpha == _want(_masks(mask_program, tmp_path / "m2", open(FONT, "rb").read(), [(TEXT, 20, 0, 0, 0)])[0])
    assert alpha != beta


def test_a_listed_face_text_f_cannot_open_is_the_default_face(tmp_path, cli_mod, fonts, mask_program):
    """The scanner lists it (it has a name, glyf and loca) but there is no
    Unicode cmap, so font.f refuses it when the text is laid out: the
    runtime must fall back to the bundled face, for the measures and for
    the drawing, and keep doing so."""
    broken = bytearray(_renamed("Broken Face", base=_serif()))
    co = _tables(bytes(broken))[b"cmap"][0]
    for i in range(struct.unpack(">H", broken[co + 2:co + 4])[0]):
        broken[co + 4 + 8 * i:co + 8 + 8 * i] = struct.pack(">HH", 1, 0)
    _put(fonts, "broken.ttf", bytes(broken))
    got = _drawn(tmp_path, cli_mod, "changeFont(20, null, 'Broken Face')\n", env={"FESTINA_FONT_DIRS": str(fonts)},
                 extra="a.drawText('again', 5, 20)\n")
    want = _drawn(tmp_path, cli_mod, "changeFont(20, null, null)\n", name="q", env={"FESTINA_FONT_DIRS": str(fonts)},
                  extra="a.drawText('again', 5, 20)\n")
    assert got == want


def test_the_face_can_change_between_draws_in_one_program(tmp_path, cli_mod, fonts, mask_program):
    """Sans, a found serif, sans again, bold sans, a second found family,
    the first found one again: each word is what that face alone draws (text.f reopens the font when the
    face changes, and its glyph cache never mixes them)."""
    _put(fonts, "f.ttf", _renamed("Fixture Serif", base=_serif()))
    _put(fonts, "g.ttf", _renamed("Fixture Heavy", 700, base=_serif_bold()))
    src = ("img a = blankImage(700, 60)\nfillStyle(0, 0, 0)\n"
           "changeFont(20, '', 'sans-serif')\na.drawText('Rag', 5, 40)\n"
           "changeFont(20, '', 'Fixture Serif')\na.drawText('Rag', 105, 40)\n"
           "changeFont(20, '', 'sans-serif')\na.drawText('Rag', 205, 40)\n"
           "changeFont(20, 'bold', 'sans-serif')\na.drawText('Rag', 305, 40)\n"
           "changeFont(20, '', 'Fixture Heavy')\na.drawText('Rag', 405, 40)\n"
           "changeFont(20, '', 'Fixture Serif')\na.drawText('Rag', 505, 40)\n"
           "arr[int] px = a.toPixels()\ntext out = ''\nint i = 3\n"
           "while i < px.length {\n    out = `${out} ${px[i]}`\n    i = i + 4\n}\nlog(out)\n")
    prog = _compile(tmp_path, cli_mod, src, name="sw")
    r = subprocess.run([prog], cwd=tmp_path, capture_output=True, text=True, timeout=120,
                       env=dict(os.environ, DISPLAY="", FESTINA_FONT_DIRS=str(fonts)))
    assert r.returncode == 0, r.stderr
    v = list(map(int, r.stdout.split()))
    sans, serif = open(FONT, "rb").read(), open(_serif(), "rb").read()
    refs = [_masks(mask_program, tmp_path / "ma", sans, [("Rag", 20, 0, 0, 0)])[0],
            _masks(mask_program, tmp_path / "mb", serif, [("Rag", 20, 0, 0, 0)])[0],
            _masks(mask_program, tmp_path / "mc", sans, [("Rag", 20, 0, 0, 0)])[0],
            _masks(mask_program, tmp_path / "md", sans, [("Rag", 20, 0, 0, 1)])[0],
            _masks(mask_program, tmp_path / "me", open(_serif_bold(), "rb").read(), [("Rag", 20, 0, 0, 0)])[0],
            _masks(mask_program, tmp_path / "mf", serif, [("Rag", 20, 0, 0, 0)])[0]]
    for n, m in enumerate(refs):
        _, _, x0, y0, w, h, rows = m
        for r_ in range(h):
            for c in range(w):
                assert v[(40 + y0 + r_) * 700 + 5 + 100 * n + x0 + c] == rows[r_][c], (n, r_, c)


def test_the_font_directories_are_not_read_until_another_family_is_asked_for(tmp_path, cli_mod, fonts):
    """A FIFO named like a font blocks whoever opens it. A program that
    draws the default face must never open it; one that names another
    family would (so that one is not run)."""
    if not hasattr(os, "mkfifo"):
        pytest.skip("no FIFOs here")
    os.mkfifo(str(fonts / "trap.ttf"))
    src = ("img a = blankImage(100, 50)\nfillStyle(0, 0, 0)\nchangeFont(18, 'bold', 'sans-serif')\n"
           "a.drawText('Hello', 5, 30)\nlog(measureTextWidth('Hello'))\n")
    prog = _compile(tmp_path, cli_mod, src, name="lazy")
    r = subprocess.run([prog], cwd=tmp_path, capture_output=True, text=True, timeout=20,
                       env=dict(os.environ, DISPLAY="", FESTINA_FONT_DIRS=str(fonts)))
    assert r.returncode == 0 and int(r.stdout) > 20


def test_the_scan_is_done_once_not_per_call(tmp_path, cli_mod, fonts):
    """Two thousand measures of a family that does not exist: if each
    walked the directories this would take a very long time. (Timing
    bound, generous: the walk itself is milliseconds.)"""
    for i in range(60):
        _put(fonts, f"f{i}.ttf", _renamed(f"Family {i}", base=_serif()))
    src = ("int n = 0\nint total = 0\nchangeFont(16, null, 'Nonexistent')\n"
           "while n < 2000 {\n    total = total + measureTextWidth('Hello')\n    n = n + 1\n}\nlog(total)\n")
    prog = _compile(tmp_path, cli_mod, src, name="once")
    r = subprocess.run([prog], cwd=tmp_path, capture_output=True, text=True, timeout=60,
                       env=dict(os.environ, DISPLAY="", FESTINA_FONT_DIRS=str(fonts)))
    assert r.returncode == 0 and int(r.stdout) > 0


def test_a_broken_face_measures_as_the_default_face_even_before_anything_is_drawn(tmp_path, cli_mod, fonts):
    broken = bytearray(_renamed("Broken Face", base=_serif()))
    co = _tables(bytes(broken))[b"cmap"][0]
    for i in range(struct.unpack(">H", broken[co + 2:co + 4])[0]):
        broken[co + 4 + 8 * i:co + 8 + 8 * i] = struct.pack(">HH", 1, 0)
    _put(fonts, "broken.ttf", bytes(broken))
    results = []
    for tag, family in (("b", "Broken Face"), ("s", "sans-serif")):
        src = (f"changeFont(20, 'bold', '{family}')\n"
               "log(`${measureTextWidth('Handgloves 42')} ${measureTextHeight('Handgloves 42')}`)\n")
        prog = _compile(tmp_path, cli_mod, src, name="m" + tag)
        r = subprocess.run([prog], cwd=tmp_path, capture_output=True, text=True, timeout=60,
                           env=dict(os.environ, DISPLAY="", FESTINA_FONT_DIRS=str(fonts)))
        assert r.returncode == 0, r.stderr
        results.append(r.stdout.split())
    assert results[0] == results[1]


def test_a_broken_face_asked_bold_is_the_default_face_bold(tmp_path, cli_mod, fonts):
    broken = bytearray(_renamed("Broken Face", base=_serif()))
    co = _tables(bytes(broken))[b"cmap"][0]
    for i in range(struct.unpack(">H", broken[co + 2:co + 4])[0]):
        broken[co + 4 + 8 * i:co + 8 + 8 * i] = struct.pack(">HH", 1, 0)
    _put(fonts, "broken.ttf", bytes(broken))
    env = {"FESTINA_FONT_DIRS": str(fonts)}
    got = _drawn(tmp_path, cli_mod, "changeFont(20, 'bold italic', 'Broken Face')\n", name="g", env=env)
    want = _drawn(tmp_path, cli_mod, "changeFont(20, 'bold italic', 'sans-serif')\n", name="w", env=env)
    assert got == want


@pytest.mark.parametrize("bits", [1, 3])
def test_a_synthesized_line_is_its_glyphs_laid_out_by_the_synthesized_advance(mask_program, tmp_path, bits):
    """The mask of 'Hl' is the mask of 'H' plus the mask of 'l' moved by the
    width of 'H' -- bold's advance included. (The widths themselves are
    FreeType's, above; this ties the drawing to them.)"""
    font = open(FONT, "rb").read()
    h, l, hl = _masks(mask_program, tmp_path, font, [("H", 24, 0, 0, bits), ("l", 24, 0, 0, bits),
                                                      ("Hl", 24, 0, 0, bits)])
    shift = h[0]
    plain_h = _masks(mask_program, tmp_path / "p", font, [("H", 24, 0, 0, bits & 2)])[0]
    assert shift > plain_h[0] or not bits & 1, "bold H is wider than plain H"
    want = {}
    for (m, dx) in ((h, 0), (l, shift)):
        _, _, x0, y0, w, hh, rows = m
        for r in range(hh):
            for c in range(w):
                key = (x0 + dx + c, y0 + r)
                want[key] = min(255, want.get(key, 0) + rows[r][c])
    _, _, x0, y0, w, hh, rows = hl
    got = {(x0 + c, y0 + r): rows[r][c] for r in range(hh) for c in range(w) if rows[r][c]}
    assert got == {k: v for k, v in want.items() if v}


_WIND = r"""
import text.f
// a counter-clockwise square with a clockwise hole, and its mirror image
arr[float] a = [0.0, 0.0, 10.0, 0.0, 10.0, 10.0, 0.0, 10.0,   3.0, 3.0, 3.0, 7.0, 7.0, 7.0, 7.0, 3.0]
arr[int] ea = [4, 8]
arr[float] b = [0.0, 0.0, 0.0, 10.0, 10.0, 10.0, 10.0, 0.0,   3.0, 3.0, 7.0, 3.0, 7.0, 7.0, 3.0, 7.0]
arr[int] eb = [4, 8]
txtWindPositive(a, ea)
txtWindPositive(b, eb)
log(a)
log(b)
"""


def test_a_glyph_wound_the_wrong_way_is_turned_round(tmp_path, cli_mod):
    """The outer contour wound against rasEmitPoly's pieces is reversed
    (with its hole); one already wound with them is left alone."""
    from tests.test_text_wired import _run
    prog = _compile(tmp_path, cli_mod, _WIND, name="wind", with_components=("text.f", "font.f", "raster.f"))
    lines = _run(tmp_path, prog)
    first = lines[0].replace("[", "").replace("]", "").split(",")
    second = lines[1].replace("[", "").replace("]", "").split(",")
    pa = [float(x) for x in first]
    pb = [float(x) for x in second]

    def area(p, lo, hi):
        pts = [(p[2 * i], p[2 * i + 1]) for i in range(lo, hi)]
        return sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1]
                   for i in range(len(pts)))
    # whichever way they started, afterwards the outer contour is positive and the hole negative
    assert area(pa, 0, 4) > 0 > area(pa, 4, 8), (pa, area(pa, 0, 4), area(pa, 4, 8))
    assert area(pb, 0, 4) > 0 > area(pb, 4, 8), (pb, area(pb, 0, 4), area(pb, 4, 8))
