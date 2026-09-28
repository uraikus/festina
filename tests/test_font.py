"""runtime.md phase 5: `font.f`, TrueType fonts.

**Slice 1 is compared exactly, and there is a reason it can be.** A
glyph's outline in font units is a list of integers, and FreeType --
the library Cairo draws text through on Linux -- reports the same
integers for the same glyph when asked not to scale or hint it. So
every glyph of every font here is compared point for point, not a
sample, and the cmap is compared mapping for mapping.

The oracle is a small C program built against FreeType at test time.
FreeType is already on every Linux machine that can build the graphics
runtime (libcairo2-dev depends on it), so this is a test-time use of a
library the runtime links anyway, not a new dependency.

The fonts are the four fonts-dejavu-core installs -- the package CI's
Linux job installs and the one `sans-serif` resolves to there. None of
them has a short loca table or only a format-4 cmap, so one more font
is BUILT here, from DejaVu's own glyphs, to reach those paths.
"""
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from festina import imports as imports_mod   # noqa: E402

DEJAVU_DIR = "/usr/share/fonts/truetype/dejavu"
CORE_FONTS = ["DejaVuSans.ttf", "DejaVuSans-Bold.ttf",
              "DejaVuSerif.ttf", "DejaVuSerif-Bold.ttf"]
SANS = os.path.join(DEJAVU_DIR, "DejaVuSans.ttf")

# FNT_E_* in font.f.
E_SHORT, E_NOT_SFNT, E_CFF, E_COLLECTION, E_TABLE, E_NO_CMAP, E_GLYPH, E_COMPONENT = range(1, 9)


def _missing(reason):
    """A missing font or FreeType is a skip on a machine without them,
    and a failure on Linux CI, where FESTINA_STRICT_DEPS says they must
    be there -- the same rule compile_file_or_skip applies."""
    if os.environ.get("FESTINA_STRICT_DEPS"):
        pytest.fail(reason)
    pytest.skip(reason)


ORACLE_C = r"""
#include <ft2build.h>
#include FT_FREETYPE_H
#include <stdio.h>
#include <stdlib.h>
/* argv: font, cmap format to force (0 = FreeType's own choice).
 * Composites are composed, as a renderer sees them. */
int main(int argc, char **argv) {
    FT_Library lib; FT_Face f;
    if (FT_Init_FreeType(&lib) || FT_New_Face(lib, argv[1], 0, &f)) { puts("OPENFAIL"); return 0; }
    int want = atoi(argv[2]);
    if (want) {
        for (int i = 0; i < f->num_charmaps; i++) {
            FT_CharMap m = f->charmaps[i];
            int unicode = m->platform_id == 0 ||
                          (m->platform_id == 3 && (m->encoding_id == 1 || m->encoding_id == 10));
            if (unicode && FT_Get_CMap_Format(m) == want) { FT_Set_Charmap(f, m); break; }
        }
    }
    printf("upem %d glyphs %ld\n", f->units_per_EM, f->num_glyphs);
    for (long g = 0; g < f->num_glyphs; g++) {
        int fl = FT_LOAD_NO_SCALE | FT_LOAD_NO_HINTING | FT_LOAD_NO_BITMAP;
        if (FT_Load_Glyph(f, g, fl)) { printf("g %ld LOADFAIL\n", g); continue; }
        FT_GlyphSlot s = f->glyph;
        FT_Outline *o = &s->outline;
        printf("g %ld adv %ld c %d:", g, s->metrics.horiAdvance, o->n_contours);
        for (int i = 0; i < o->n_contours; i++) printf(" %d", o->contours[i]);
        printf(" p");
        for (int i = 0; i < o->n_points; i++)
            printf(" %ld,%ld,%d", o->points[i].x, o->points[i].y, o->tags[i] & 1);
        printf("\n");
    }
    FT_UInt gi; FT_ULong cp = FT_Get_First_Char(f, &gi);
    while (gi) { printf("m %lu %u\n", cp, gi); cp = FT_Get_Next_Char(f, cp, &gi); }
    return 0;
}
"""


def _dump_program(cmap_format=0, composites=False):
    """The Festina side of the comparison, printing exactly the lines
    the oracle prints. A glyph font.f refuses prints its error code, so
    a refusal can never be mistaken for an agreement."""
    select = ""
    if cmap_format:
        select = (f"if !fntSelectCmap({cmap_format}) {{ log('no cmap {cmap_format}') }}\n")
    return (
        "import font.f\n"
        "blob raw = 'font.ttf'\n"
        "arr[int] bytes = []\n"
        "int i = 0\n"
        "while i < raw.length {\n"
        "    bytes.push(raw.byteAt(i))\n"
        "    i = i + 1\n"
        "}\n"
        "int e = fntOpen(bytes)\n"
        "if e != 0 { log(`OPENFAIL ${e}`) }\n"
        + select +
        "log(`upem ${FNT_UPEM} glyphs ${FNT_NGLYPHS}`)\n"
        "int g = 0\n"
        "while g < FNT_NGLYPHS && e == 0 {\n"
        "    arr[int] xs = []\n"
        "    arr[int] ys = []\n"
        "    arr[int] oc = []\n"
        "    arr[int] ends = []\n"
        "    int n = fntGlyph(g, xs, ys, oc, ends)\n"
        "    if n < 0 {\n"
        "        log(`g ${g} err ${FNT_ERR}`)\n"
        "    } else {\n"
        "        text line = `g ${g} adv ${fntAdvance(g)} c ${n}:`\n"
        "        int k = 0\n"
        "        while k < ends.length {\n"
        "            line = `${line} ${ends[k]}`\n"
        "            k = k + 1\n"
        "        }\n"
        "        line = `${line} p`\n"
        "        k = 0\n"
        "        while k < xs.length {\n"
        "            line = `${line} ${xs[k]},${ys[k]},${oc[k]}`\n"
        "            k = k + 1\n"
        "        }\n"
        "        log(line)\n"
        "    }\n"
        "    g = g + 1\n"
        "}\n"
        "int cp = 0\n"
        "while cp < 1114112 && e == 0 {\n"
        "    int gi = fntGlyphIndex(cp)\n"
        "    if gi != 0 { log(`m ${cp} ${gi}`) }\n"
        "    cp = cp + 1\n"
        "}\n"
    )


class _Built:
    """Compile once per module: the oracle, and a Festina program per
    configuration. Each run gets its own directory with the font copied
    in as font.ttf."""

    def __init__(self, root):
        self.root = root
        self.programs = {}
        self.oracle = None

    def build_oracle(self):
        if self.oracle:
            return self.oracle
        cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
        if not cc:
            _missing("no C compiler to build the FreeType oracle")
        try:
            flags = subprocess.run(["pkg-config", "--cflags", "--libs", "freetype2"],
                                   capture_output=True, text=True, check=True).stdout.split()
        except (OSError, subprocess.CalledProcessError):
            _missing("FreeType (pkg-config freetype2) is not installed")
        src = self.root / "oracle.c"
        src.write_text(ORACLE_C)
        out = self.root / "oracle"
        subprocess.run([cc, str(src), "-o", str(out)] + flags, check=True)
        self.oracle = str(out)
        return self.oracle

    def build_program(self, source):
        if source in self.programs:
            return self.programs[source]
        from festina import cli
        from tests.conftest import compile_file_or_skip, _require_c_compiler
        d = Path(tempfile.mkdtemp(prefix="prog", dir=self.root))
        src = os.path.join(imports_mod.RUNTIME_COMPONENT_DIR, "font.f")
        (d / "font.f").write_text(open(src, encoding="utf-8").read(), encoding="utf-8")
        (d / "main.f").write_text(source, encoding="utf-8")
        compile_file_or_skip(cli, str(d / "main.f"), str(d / "program"),
                             cc=_require_c_compiler())
        self.programs[source] = str(d / "program")
        return self.programs[source]


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    return _Built(tmp_path_factory.mktemp("font"))


def _run_in(tmp_path, program, font_bytes):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "font.ttf").write_bytes(font_bytes)
    r = subprocess.run([program], cwd=tmp_path, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout.splitlines()


def _oracle(built, font_path, cmap_format=0):
    """FreeType's view: composites composed, as a renderer sees them."""
    args = [built.build_oracle(), font_path, str(cmap_format)]
    r = subprocess.run(args, capture_output=True, text=True, timeout=300, check=True)
    return r.stdout.splitlines()


def _font(name):
    path = os.path.join(DEJAVU_DIR, name)
    if not os.path.exists(path):
        _missing(f"{path} is not installed (fonts-dejavu-core)")
    return path


def _by_glyph(lines):
    return {l.split()[1]: l for l in lines if l.startswith("g ")}


def _mappings(lines):
    return {l for l in lines if l.startswith("m ")}


def _compare(ours, theirs):
    """(glyphs identical, disagreements)."""
    mine, ref = _by_glyph(ours), _by_glyph(theirs)
    assert mine.keys() == ref.keys()
    same, bad = 0, []
    for g, line in ref.items():
        if line == mine[g]:
            same += 1
        else:
            bad.append((line[:120], mine[g][:120]))
    return same, bad


def _composite_ids(data):
    """Which glyphs of a font are composites, from its own bytes."""
    t = _tables(data)
    maxp = t[b"maxp"][0]
    n = struct.unpack(">H", data[maxp + 4:maxp + 6])[0]
    out = set()
    for g in range(n):
        body = _glyph_bytes(data, t, g)
        if len(body) >= 2 and struct.unpack(">h", body[:2])[0] < 0:
            out.add(g)
    return out


# ---- the tables, and the file they come from ----

def _tables(data):
    n = struct.unpack(">H", data[4:6])[0]
    out = {}
    for i in range(n):
        tag, _, off, ln = struct.unpack(">4sIII", data[12 + 16 * i:28 + 16 * i])
        out[tag] = (off, ln, 12 + 16 * i)
    return out


def _glyph_bytes(data, tables, g):
    head = tables[b"head"][0]
    long_loca = struct.unpack(">h", data[head + 50:head + 52])[0] == 1
    loca = tables[b"loca"][0]
    if long_loca:
        a, b = struct.unpack(">II", data[loca + 4 * g:loca + 4 * g + 8])
    else:
        a, b = (2 * v for v in struct.unpack(">HH", data[loca + 2 * g:loca + 2 * g + 4]))
    glyf = tables[b"glyf"][0]
    return data[glyf + a:glyf + b]


def _metric(data, tables, g):
    hhea, hmtx = tables[b"hhea"][0], tables[b"hmtx"][0]
    nh = struct.unpack(">H", data[hhea + 34:hhea + 36])[0]
    if g < nh:
        return struct.unpack(">Hh", data[hmtx + 4 * g:hmtx + 4 * g + 4])
    adv = struct.unpack(">H", data[hmtx + 4 * (nh - 1):hmtx + 4 * (nh - 1) + 2])[0]
    at = hmtx + 4 * nh + 2 * (g - nh)
    return adv, struct.unpack(">h", data[at:at + 2])[0]


# A glyph whose top is an OFF-curve point: one quadratic from (0, 0)
# through (500, 1500) to (1000, 0). The curve peaks at 750, its control
# box at 1500 -- the two measures of "how tall" this glyph is disagree
# by a factor of two, where in any real font's glyphs, which put an
# on-curve point at every extreme, they agree.
HUMP_BODY = (struct.pack(">hhhhh", 1, 0, 0, 1000, 750) + struct.pack(">H", 2) +
             struct.pack(">H", 0) + bytes([1, 0, 1]) +
             struct.pack(">hhh", 0, 500, 500) + struct.pack(">hhh", 0, 1500, -1500))


def _build_small_font(source_path, text, upem=None, hump=None):
    """A real TrueType font holding .notdef and the SIMPLE glyphs for
    `text`, with a short loca, a format-4 cmap only, and fewer hmtx
    entries than glyphs -- the three paths DejaVu itself never takes.
    The last few glyphs share one advance, which is what makes the
    shorter hmtx legal: it stores that advance once.

    `upem` replaces DejaVu's 2048 units per em -- a power of two, which
    makes every size's scale an exact division and hides the rounding
    in FT_DivFix -- and `hump`, a code point, maps to HUMP_BODY."""
    data = open(source_path, "rb").read()
    t = _tables(data)
    cmap_src = {}
    # Map the characters through DejaVu's own format-12 cmap.
    co = t[b"cmap"][0]
    for i in range(struct.unpack(">H", data[co + 2:co + 4])[0]):
        p, e, off = struct.unpack(">HHI", data[co + 4 + 8 * i:co + 12 + 8 * i])
        sub = co + off
        if (p, e) == (3, 10) and struct.unpack(">H", data[sub:sub + 2])[0] == 12:
            ngroups = struct.unpack(">I", data[sub + 12:sub + 16])[0]
            for k in range(ngroups):
                s, e2, g0 = struct.unpack(">III", data[sub + 16 + 12 * k:sub + 28 + 12 * k])
                for c in range(s, e2 + 1):
                    cmap_src[c] = g0 + c - s
    chars, glyphs = [], [0]
    for ch in text:
        g = cmap_src[ord(ch)]
        body = _glyph_bytes(data, t, g)
        if body and struct.unpack(">h", body[:2])[0] < 0:
            continue                       # composites are slice 2's
        if ord(ch) not in [c for c, _ in chars]:
            chars.append((ord(ch), len(glyphs)))
            glyphs.append(g)
    if hump is not None:
        glyphs.insert(1, "hump")
        chars = [(c, g + 1) for c, g in chars] + [(hump, 1)]
    # The hump's advance is 2291 on purpose: with a 1000-unit em, at
    # 12 px, it is one of the few advances whose pixel width changes if
    # FT_DivFix's rounding is dropped -- a one-in-65536 effect on the
    # scale that only shows where an advance sits on a rounding edge.
    # Found by searching every advance, not by guessing.
    metric = lambda g: (2291, 0) if g == "hump" else _metric(data, t, g)
    body_of = lambda g: HUMP_BODY if g == "hump" else _glyph_bytes(data, t, g)
    # Monospace the tail so hmtx can stop early: the last three glyphs
    # take the advance of the one before them.
    advances = [metric(g)[0] for g in glyphs]
    lsbs = [metric(g)[1] for g in glyphs]
    nh = len(glyphs) - 3
    for k in range(nh, len(glyphs)):
        advances[k] = advances[nh - 1]
    glyf, loca = b"", []
    for g in glyphs:
        body = body_of(g)
        if len(body) % 2:
            body += b"\0"
        loca.append(len(glyf) // 2)
        glyf += body
    loca.append(len(glyf) // 2)
    assert loca[-1] < 65536, "too big for a short loca"
    head = bytearray(data[t[b"head"][0]:t[b"head"][0] + t[b"head"][1]])
    head[50:52] = struct.pack(">h", 0)
    if upem is not None:
        head[18:20] = struct.pack(">H", upem)
    maxp = bytearray(data[t[b"maxp"][0]:t[b"maxp"][0] + t[b"maxp"][1]])
    maxp[4:6] = struct.pack(">H", len(glyphs))
    hhea = bytearray(data[t[b"hhea"][0]:t[b"hhea"][0] + t[b"hhea"][1]])
    hhea[34:36] = struct.pack(">H", nh)
    hmtx = b"".join(struct.pack(">Hh", advances[k], lsbs[k]) for k in range(nh))
    hmtx += b"".join(struct.pack(">h", lsbs[k]) for k in range(nh, len(glyphs)))
    # Format 4. Letters get a segment each, mapped by idDelta alone.
    # The digits share ONE segment, '0' to '9', mapped through the
    # glyph array: the digits this font holds get their glyph, the
    # rest a 0 -- and the segment also has a nonzero idDelta, because
    # the rule under test is that a 0 from the array means "missing"
    # WITHOUT the delta being added. (DejaVu's own format-4 table never
    # takes that path, so without this nothing would.) Then 0xFFFF.
    held = dict(chars)
    digit_delta = 7
    singles = [(c, g) for c, g in sorted(chars) if not 0x30 <= c <= 0x39]
    segs = [(c, c, (g - c) & 0xFFFF, None) for c, g in singles]
    segs.append((0x30, 0x39, digit_delta,
                 [((held[c] - digit_delta) & 0xFFFF) if c in held else 0
                  for c in range(0x30, 0x3A)]))
    segs.sort()
    segs.append((0xFFFF, 0xFFFF, 1, None))
    n = len(segs)
    array = []
    ranges = []
    for i, (_, _, _, glyph_ids) in enumerate(segs):
        if glyph_ids is None:
            ranges.append(0)
        else:
            # From this segment's own idRangeOffset slot to its first
            # entry in the glyph array, which starts right after the
            # idRangeOffset array.
            ranges.append(2 * (n - i) + 2 * len(array))
            array += glyph_ids
    sub = struct.pack(">HHHHHHH", 4, 0, 0, 2 * n, 0, 0, 0)
    sub += struct.pack(f">{n}H", *[e for _, e, _, _ in segs]) + b"\0\0"
    sub += struct.pack(f">{n}H", *[s for s, _, _, _ in segs])
    sub += struct.pack(f">{n}H", *[d for _, _, d, _ in segs])
    sub += struct.pack(f">{n}H", *ranges) + struct.pack(f">{len(array)}H", *array)
    sub = sub[:2] + struct.pack(">H", len(sub)) + sub[4:]
    cmap = struct.pack(">HHHHI", 0, 1, 3, 1, 12) + sub
    loca_bytes = struct.pack(f">{len(loca)}H", *loca)
    return _pack_sfnt({b"cmap": cmap, b"glyf": glyf, b"head": bytes(head), b"hhea": bytes(hhea),
                       b"hmtx": hmtx, b"loca": loca_bytes, b"maxp": bytes(maxp)})


def _pack_sfnt(tables):
    """Tables into a TrueType file: the directory, sorted by tag, then
    each table padded to four bytes. Checksums are left zero; neither
    reader checks them."""
    tags = sorted(tables)
    out = struct.pack(">IHHHH", 0x00010000, len(tags), 0, 0, 0)
    offset = 12 + 16 * len(tags)
    body = b""
    for tag in tags:
        blob = tables[tag]
        out += struct.pack(">4sIII", tag, 0, offset + len(body), len(blob))
        body += blob + b"\0" * (-len(blob) % 4)
    return out + body


# ---- slice 1 ----

class TestOutlinesMatchFreeType:

    @pytest.mark.parametrize("name", CORE_FONTS)
    def test_every_glyph_matches_freetype(self, built, tmp_path, name):
        """Every glyph, point for point, and every advance -- simple
        glyphs (slice 1) and composites composed (slice 2), which in
        these fonts nest up to four deep."""
        path = _font(name)
        data = open(path, "rb").read()
        ours = _run_in(tmp_path, built.build_program(_dump_program()), data)
        theirs = _oracle(built, path)
        same, bad = _compare(ours, theirs)
        assert not bad, f"{len(bad)} glyphs differ, first: {bad[0]}"
        composites = _composite_ids(data)
        assert len(composites) > 1000 and same > len(composites) + 1000, \
            "the comparison has to include real outlines of both kinds"

    def test_the_format_12_cmap_matches_freetype_everywhere(self, built, tmp_path):
        """Every code point from 0 to U+10FFFF, both ways: nothing
        FreeType maps is missing, and nothing is mapped it does not
        map -- including the supplementary planes, which only a format
        12 table reaches."""
        ours = _run_in(tmp_path, built.build_program(_dump_program()), open(_font("DejaVuSans.ttf"), "rb").read())
        theirs = _oracle(built, SANS)
        mine, ref = _mappings(ours), _mappings(theirs)
        assert mine == ref, (sorted(ref - mine)[:5], sorted(mine - ref)[:5])
        assert any(int(m.split()[1]) > 0xFFFF for m in ref), "the plane beyond the BMP must be in it"

    def test_the_format_4_cmap_matches_freetype(self, built, tmp_path):
        ours = _run_in(tmp_path, built.build_program(_dump_program(cmap_format=4)),
                       open(_font("DejaVuSans.ttf"), "rb").read())
        theirs = _oracle(built, SANS, cmap_format=4)
        mine, ref = _mappings(ours), _mappings(theirs)
        assert mine == ref, (sorted(ref - mine)[:5], sorted(mine - ref)[:5])
        assert len(ref) > 3000

    def test_a_short_loca_font_with_a_short_hmtx_matches_freetype(self, built, tmp_path):
        """The font built above, from DejaVu's own glyphs: short loca,
        format 4 only, and three glyphs past the end of hmtx's full
        entries. FreeType reads it too, so it is compared exactly like
        the rest."""
        small = _build_small_font(_font("DejaVuSans.ttf"), "Handgloves 0123 xyz")
        path = tmp_path / "small.ttf"
        path.write_bytes(small)
        ours = _run_in(tmp_path / "run", built.build_program(_dump_program()), small)
        theirs = _oracle(built, str(path))
        assert theirs[0] != "OPENFAIL", "FreeType must accept the built font"
        same, bad = _compare(ours, theirs)
        assert not bad, bad[:2]
        assert _mappings(ours) == _mappings(theirs)
        assert len(_mappings(theirs)) >= 10


# ---- what font.f refuses, and how ----

def _open_result(built, tmp_path, font_bytes):
    """Just FNT_ERR from fntOpen, or 0."""
    lines = _run_in(tmp_path, built.build_program(_dump_program()), font_bytes)
    for line in lines:
        if line.startswith("OPENFAIL"):
            return int(line.split()[1])
    return 0


class TestRefusals:
    """Each refusal is a code the caller can fall through on, and none
    of them may crash or read outside the file."""

    def test_cff_outlines_are_refused(self, built, tmp_path):
        data = bytearray(open(_font("DejaVuSans.ttf"), "rb").read())
        data[0:4] = b"OTTO"
        assert _open_result(built, tmp_path, bytes(data)) == E_CFF

    def test_a_collection_is_refused(self, built, tmp_path):
        data = bytearray(open(_font("DejaVuSans.ttf"), "rb").read())
        data[0:4] = b"ttcf"
        assert _open_result(built, tmp_path, bytes(data)) == E_COLLECTION

    def test_something_that_is_not_a_font_is_refused(self, built, tmp_path):
        assert _open_result(built, tmp_path, b"\x89PNG\r\n\x1a\n" + b"\0" * 64) == E_NOT_SFNT

    def test_a_missing_table_is_refused(self, built, tmp_path):
        data = bytearray(open(_font("DejaVuSans.ttf"), "rb").read())
        at = _tables(bytes(data))[b"loca"][2]
        data[at:at + 4] = b"locx"
        assert _open_result(built, tmp_path, bytes(data)) == E_TABLE

    def test_a_font_with_no_unicode_cmap_is_refused(self, built, tmp_path):
        """Every subtable relabelled as Macintosh Roman: a font, but not
        one whose characters can be found by code point."""
        data = bytearray(open(_font("DejaVuSans.ttf"), "rb").read())
        co = _tables(bytes(data))[b"cmap"][0]
        for i in range(struct.unpack(">H", data[co + 2:co + 4])[0]):
            data[co + 4 + 8 * i:co + 8 + 8 * i] = struct.pack(">HH", 1, 0)
        assert _open_result(built, tmp_path, bytes(data)) == E_NO_CMAP

    @pytest.mark.parametrize("keep", [0, 11, 12, 100, 300, 5000, 400000])
    def test_a_truncated_file_is_refused_not_read_past(self, built, tmp_path, keep):
        """Cut short anywhere from the header to the middle of glyf.
        Past the directory, the file still LISTS every table, so this
        is the case where fntOpen's range check on each table is what
        stands between a glyph read and the end of the buffer -- and a
        table that is listed but cut off is damage, not absence, so it
        must say E_SHORT rather than E_TABLE."""
        data = open(_font("DejaVuSans.ttf"), "rb").read()[:keep]
        assert _open_result(built, tmp_path, data) == E_SHORT

    def test_a_glyph_pointing_outside_glyf_is_refused(self, built, tmp_path):
        """loca says where each glyph is. One entry pointing past the
        end of glyf is an error for that glyph alone -- the rest of
        the font still reads."""
        data = bytearray(open(_font("DejaVuSans.ttf"), "rb").read())
        t = _tables(bytes(data))
        loca, glyf_len = t[b"loca"][0], t[b"glyf"][1]
        g = 36                                          # 'A' in DejaVu Sans
        data[loca + 4 * (g + 1):loca + 4 * (g + 2)] = struct.pack(">I", glyf_len + 1000)
        lines = _run_in(tmp_path, built.build_program(_dump_program()), bytes(data))
        by = _by_glyph(lines)
        assert by[str(g)] == f"g {g} err {E_GLYPH}"
        assert " c " in by["38"], "the glyphs around it still read"


# ---- slice 2: composites the CI fonts never build ----

def _dejavu_simple(data, t, ch):
    """DejaVu's glyph for `ch`, which must be a simple one."""
    co = t[b"cmap"][0]
    for i in range(struct.unpack(">H", data[co + 2:co + 4])[0]):
        p, e, off = struct.unpack(">HHI", data[co + 4 + 8 * i:co + 12 + 8 * i])
        sub = co + off
        if (p, e) == (3, 10):
            for k in range(struct.unpack(">I", data[sub + 12:sub + 16])[0]):
                s0, s1, g0 = struct.unpack(">III", data[sub + 16 + 12 * k:sub + 28 + 12 * k])
                if s0 <= ord(ch) <= s1:
                    body = _glyph_bytes(data, t, g0 + ord(ch) - s0)
                    assert struct.unpack(">h", body[:2])[0] >= 0, ch
                    return body
    raise KeyError(ch)


# Component flags (glyf spec).
WORDS, XY, SCALE, MORE, XYSCALE, TWO_BY_TWO = 1, 2, 8, 32, 64, 128
SCALED_OFFSET, UNSCALED_OFFSET = 2048, 4096


def _component(flags, glyph, a1, a2, transform=()):
    """One component record. Byte arguments are packed signed when they
    are x/y offsets and unsigned when they are point numbers, as the
    spec says they are read."""
    out = struct.pack(">HH", flags, glyph)
    if flags & WORDS:
        out += struct.pack(">hh" if flags & XY else ">HH", a1, a2)
    else:
        out += struct.pack(">bb" if flags & XY else ">BB", a1, a2)
    for v in transform:
        out += struct.pack(">h", round(v * 16384))
    return out


def _composite(components, xmin=0):
    """A composite glyph: numberOfContours -1, a bounding box (only xMin
    is read, by the lsb adjustment), then the components, each but the
    last flagged MORE."""
    body = struct.pack(">hhhhh", -1, xmin, -500, 2000, 2000)
    for i, (flags, glyph, a1, a2, transform) in enumerate(components):
        if i < len(components) - 1:
            flags |= MORE
        body += _component(flags, glyph, a1, a2, transform)
    return body


def _build_composite_font(source_path):
    """Composites using everything DejaVu does not: one scale, separate
    x and y scales, a 2x2 matrix, scaled composites nested inside
    composites, and a composite whose lsb disagrees with its xMin. Then
    three that font.f must refuse -- point matching, a scaled offset,
    and a composite that contains itself -- which FreeType handles
    (or rejects) in its own way.

    Returns (font bytes, {glyph: what font.f must say about it}),
    where the value is None for "the same as FreeType"."""
    data = open(source_path, "rb").read()
    t = _tables(data)
    o, l, H = (_dejavu_simple(data, t, c) for c in "olH")
    notdef = _glyph_bytes(data, t, 0)
    s = 0.7071
    glyphs = [
        (notdef, None),
        (o, None),                                                      # 1
        (l, None),                                                      # 2
        (H, None),                                                      # 3
        (_composite([(XY | SCALE, 1, -20, 30, (0.5,))]), None),         # 4: one scale, byte offsets
        (_composite([(XY | WORDS | XYSCALE, 2, 300, -200, (1.5, -0.75)),
                     (XY, 1, -5, 7, ())]), None),                       # 5: x/y scale, then plain
        (_composite([(XY | WORDS | TWO_BY_TWO, 3, 1000, 50,
                      (s, 0.25, -0.3, 0.9))]), None),                   # 6: a 2x2
        (_composite([(XY | SCALE | UNSCALED_OFFSET, 5, 10, 10, (0.75,)),
                     (XY | WORDS, 6, -100, 0, ())]), None),             # 7: nested, scaled
        (_composite([(XY, 1, 0, 0, ())], xmin=40), None),               # 8: lsb 45, xMin 40
        (_composite([(XY, 1, 0, 0, ()), (0, 2, 0, 2, ())]), E_COMPONENT),  # 9: point matching
        (_composite([(XY | SCALE | SCALED_OFFSET, 1, 30, 30, (0.5,))]), E_COMPONENT),  # 10
        (_composite([(XY, 11, 0, 0, ())]), E_GLYPH),                    # 11: contains itself
    ]
    advances = [1000 + 10 * i for i in range(len(glyphs))]
    # Simple glyphs get their own xMin as lsb, as a well-formed font
    # would -- except 'l', three units off, so that a component whose
    # own lsb and xMin disagree sits inside composites 5 and 7, and
    # FreeType says whether that disagreement travels with it.
    lsbs = [0] * len(glyphs)
    for g in (1, 2, 3):
        lsbs[g] = struct.unpack(">h", glyphs[g][0][2:4])[0]
    lsbs[2] += 3
    lsbs[8] = 45
    glyf, loca = b"", []
    for body, _ in glyphs:
        body += b"\0" * (len(body) % 2)
        loca.append(len(glyf))
        glyf += body
    loca.append(len(glyf))
    head = bytearray(data[t[b"head"][0]:t[b"head"][0] + t[b"head"][1]])
    head[50:52] = struct.pack(">h", 1)
    maxp = bytearray(data[t[b"maxp"][0]:t[b"maxp"][0] + t[b"maxp"][1]])
    maxp[4:6] = struct.pack(">H", len(glyphs))
    hhea = bytearray(data[t[b"hhea"][0]:t[b"hhea"][0] + t[b"hhea"][1]])
    hhea[34:36] = struct.pack(">H", len(glyphs))
    hmtx = b"".join(struct.pack(">Hh", a, b) for a, b in zip(advances, lsbs))
    # 'A' onward maps to glyph 1 onward; one segment, by idDelta.
    n = len(glyphs) - 1
    segs = [(0x41, 0x41 + n - 1, (1 - 0x41) & 0xFFFF), (0xFFFF, 0xFFFF, 1)]
    sub = struct.pack(">HHHHHHH", 4, 0, 0, 4, 0, 0, 0)
    sub += struct.pack(">HH", segs[0][1], 0xFFFF) + b"\0\0"
    sub += struct.pack(">HH", segs[0][0], 0xFFFF)
    sub += struct.pack(">HH", segs[0][2], 1) + struct.pack(">HH", 0, 0)
    sub = sub[:2] + struct.pack(">H", len(sub)) + sub[4:]
    cmap = struct.pack(">HHHHI", 0, 1, 3, 1, 12) + sub
    font = _pack_sfnt({b"cmap": cmap, b"glyf": glyf, b"head": bytes(head),
                       b"hhea": bytes(hhea), b"hmtx": hmtx,
                       b"loca": struct.pack(f">{len(loca)}I", *loca), b"maxp": bytes(maxp)})
    return font, {g: want for g, (_, want) in enumerate(glyphs)}


class TestComposites:
    """Slice 2. The DejaVu comparison above already covers composites
    as CI's fonts build them -- nested four deep, byte and word offsets,
    USE_MY_METRICS. This covers what they never do."""

    def test_transformed_and_nested_components_match_freetype(self, built, tmp_path):
        """Scales and matrices go through FreeType's own 16.16 rounding
        (FT_MulFix), so they land on the same integers, and are
        compared exactly. The composites font.f refuses are checked for
        the right code instead."""
        font, expect = _build_composite_font(_font("DejaVuSans.ttf"))
        path = tmp_path / "comp.ttf"
        path.write_bytes(font)
        theirs = _by_glyph(_oracle(built, str(path)))
        ours = _by_glyph(_run_in(tmp_path / "run", built.build_program(_dump_program()), font))
        compared = 0
        for g, want in expect.items():
            if want is None:
                assert ours[str(g)] == theirs[str(g)], (g, theirs[str(g)][:160], ours[str(g)][:160])
                compared += 1
            else:
                assert ours[str(g)] == f"g {g} err {want}", (g, ours[str(g)])
        assert compared == 9

    def test_the_shift_applies_to_the_glyph_asked_for_only(self, built, tmp_path):
        """Glyph 8 is glyph 1 unmoved, but its own lsb is five units
        right of its xMin, so it comes out five units right of glyph 1.
        And glyph 2 is three units right of where its points say when
        drawn alone -- but as a component of glyph 5 it is placed by
        its offset, not by its own lsb. (That second half is FreeType's
        behaviour; the comparison above is what establishes it.)"""
        font, _ = _build_composite_font(_font("DejaVuSans.ttf"))
        ours = _by_glyph(_run_in(tmp_path, built.build_program(_dump_program()), font))
        pts = lambda g: [tuple(map(int, p.split(",")[:2])) for p in ours[str(g)].split(" p ")[1].split()]
        assert pts(8) == [(x + 5, y) for x, y in pts(1)]
        raw_l = pts(2)
        in_5 = pts(5)[:len(raw_l)]
        # Component 1 of glyph 5 is 'l' scaled (1.5, -0.75) and moved
        # by (300, -200): its x is 1.5 * (raw - 3) + 300 only if the
        # standalone shift stayed behind.
        def mulfix_1_5(v):          # FT_MulFix by exactly 1.5: halves away from zero
            m = (abs(v) * 3 + 1) // 2
            return m if v >= 0 else -m
        assert [x for x, _ in in_5] == [mulfix_1_5(x - 3) + 300 for x, _ in raw_l]
        assert any((x - 3) % 2 for x, _ in raw_l), "a half has to be in it for the rounding to count"


# ---- slice 3: glyphs drawn through raster.f ----

CAIRO_GLYPHS_C = r"""
/* argv: font size W H out.png, then gid x y triples. The options are
 * runtime.md's phase 5 reference -- greyscale, unhinted, hint metrics
 * on -- and the face is the FILE, not whatever fontconfig picks, so
 * this compares rasterisation and nothing else. Each glyph is shown on
 * its own, the way drawText composites them. */
#include <cairo.h>
#include <cairo-ft.h>
#include <stdlib.h>
#include <stdio.h>
int main(int argc, char **argv) {
    FT_Library lib; FT_Face face;
    FT_Init_FreeType(&lib);
    if (FT_New_Face(lib, argv[1], 0, &face)) { puts("OPENFAIL"); return 1; }
    int W = atoi(argv[3]), H = atoi(argv[4]);
    cairo_surface_t *s = cairo_image_surface_create(CAIRO_FORMAT_ARGB32, W, H);
    cairo_t *cr = cairo_create(s);
    cairo_set_source_rgb(cr, 1, 1, 1); cairo_paint(cr); cairo_set_source_rgb(cr, 0, 0, 0);
    cairo_set_font_face(cr, cairo_ft_font_face_create_for_ft_face(face, 0));
    cairo_set_font_size(cr, atof(argv[2]));
    cairo_font_options_t *fo = cairo_font_options_create();
    cairo_font_options_set_antialias(fo, CAIRO_ANTIALIAS_GRAY);
    cairo_font_options_set_hint_style(fo, CAIRO_HINT_STYLE_NONE);
    cairo_font_options_set_hint_metrics(fo, CAIRO_HINT_METRICS_ON);
    cairo_set_font_options(cr, fo);
    for (int i = 6; i + 2 < argc; i += 3) {
        cairo_glyph_t g = { (unsigned long)atoi(argv[i]), atof(argv[i + 1]), atof(argv[i + 2]) };
        cairo_show_glyphs(cr, &g, 1);
    }
    cairo_surface_write_to_png(s, argv[5]);
    return 0;
}
"""

GLYPH_TEXT = "HamburgefonstivQRS&@%g\u00c9\u00c5"     # composites last: E-acute, A-ring


def _build_c(built, name, source, packages):
    cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if not cc:
        _missing("no C compiler to build the oracle")
    try:
        flags = subprocess.run(["pkg-config", "--cflags", "--libs"] + packages,
                               capture_output=True, text=True, check=True).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        _missing(f"{' '.join(packages)} not found by pkg-config")
    src = built.root / f"{name}.c"
    src.write_text(source)
    out = built.root / name
    if not out.exists():
        subprocess.run([cc, str(src), "-o", str(out)] + flags, check=True)
    return str(out)


def _render_program(jobs):
    """A Festina program drawing each job's glyphs onto its own white
    surface through raster.f, one glyph at a time, and saving it."""
    lines = ["import font.f", "import raster.f", "blob raw = 'font.ttf'", "arr[int] bytes = []",
             "int i = 0", "while i < raw.length {", "    bytes.push(raw.byteAt(i))", "    i = i + 1",
             "}", "int e = fntOpen(bytes)"]
    for name, size, W, H, placed in jobs:
        lines.append(f"arr[int] px_{name} = rasNewSurface({W}, {H})")
        lines.append(f"rasFillRect(px_{name}, {W}, {H}, 0, 0, {W}, {H}, 255, 255, 255, 255)")
        for g, x, y in placed:
            lines += ["if true {", "    arr[float] pts = []", "    arr[int] ends = []",
                      f"    int n = fntGlyphPath({g}, {float(size)!r}, {float(x)!r}, {float(y)!r}, pts, ends)",
                      f"    rasFillPath(px_{name}, {W}, {H}, pts, ends, RAS_NONZERO, 0, 0, 0, 255)",
                      "}"]
        lines.append(f"img a_{name} = imageFromPixels(px_{name}, {W}, {H})")
        lines.append(f"log(a_{name}.save('{name}.png'))")
    return "\n".join(lines) + "\n"


def _build_render_program(built, source):
    """Like build_program, with raster.f beside font.f."""
    key = ("render", source)
    if key in built.programs:
        return built.programs[key]
    from festina import cli
    from tests.conftest import compile_file_or_skip, _require_c_compiler
    d = Path(tempfile.mkdtemp(prefix="render", dir=built.root))
    for comp in ("font.f", "raster.f"):
        src = os.path.join(imports_mod.RUNTIME_COMPONENT_DIR, comp)
        (d / comp).write_text(open(src, encoding="utf-8").read(), encoding="utf-8")
    (d / "main.f").write_text(source, encoding="utf-8")
    compile_file_or_skip(cli, str(d / "main.f"), str(d / "program"), cc=_require_c_compiler())
    built.programs[key] = str(d / "program")
    return built.programs[key]


def _grey(path):
    """The green channel of a saved PNG, as rows -- black on white, so
    every channel is the same."""
    from tests.test_raster import _decode_png
    w, h, rows, n = _decode_png(str(path))
    return [[row[x * n + 1] for x in range(w)] for row in rows]


def _layout(gids, size, frac=False):
    """Glyphs in a row with room between them, so none overlaps the
    next. With `frac`, every origin gets a different fraction of a
    pixel in x and a quarter in y."""
    step = math.ceil(size * 1.4)
    base = math.ceil(size * 1.4)
    placed = []
    for k, g in enumerate(gids):
        x, y = 10 + k * step, base
        if frac:
            x, y = x + (k * 0.37) % 1.0, y + 0.25
        placed.append((g, x, y))
    return step * len(gids) + 20, math.ceil(size * 2) + 10, placed


def _gids(built, text):
    """Code points to glyphs through FreeType, which slice 1 showed
    agrees with font.f's cmap everywhere."""
    cmap = {}
    for m in _mappings(_oracle(built, SANS)):
        _, cp, g = m.split()
        cmap[int(cp)] = int(g)
    return [cmap[ord(c)] for c in text]


def _stats(a, b):
    """(max |a-b|, mean |a-b|, mean a-b, pixels off by > 60), over the
    pixels either image inked."""
    diffs = [pa - pb for ra, rb in zip(a, b) for pa, pb in zip(ra, rb) if pa < 255 or pb < 255]
    assert len(diffs) > 500, "the comparison has to include real glyphs"
    return (max(abs(d) for d in diffs), sum(abs(d) for d in diffs) / len(diffs),
            sum(diffs) / len(diffs), sum(1 for d in diffs if abs(d) > 60))


def _true_grey(outlines, placed, size, W, H, sub=32, steps=64):
    """The exact picture: each glyph's quadratics flattened finely
    enough not to matter (64 steps each), filled by nonzero winding with
    coverage exact in x and `sub` rows per pixel -- which converges:
    16, 64 and 256 rows agree to 0.3 grey levels on these glyphs."""
    s = size / 2048
    edges = []
    for g, ox, oy in placed:
        ends, pts = outlines[g]
        first = 0
        for e in ends:
            c = [(ox + x * s, oy - y * s, on) for x, y, on in pts[first:e + 1]]
            first = e + 1
            seq = []
            for i in range(len(c)):
                a, b = c[i], c[(i + 1) % len(c)]
                seq.append(a)
                if not a[2] and not b[2]:
                    seq.append(((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, 1))
            k = next(i for i, p in enumerate(seq) if p[2])
            seq = seq[k:] + seq[:k]
            poly, i = [], 0
            while i < len(seq):
                a, b = seq[i], seq[(i + 1) % len(seq)]
                if b[2]:
                    poly.append(a[:2])
                    i += 1
                else:
                    c2 = seq[(i + 2) % len(seq)]
                    for t in range(steps):
                        u = t / steps
                        poly.append(((1 - u) ** 2 * a[0] + 2 * (1 - u) * u * b[0] + u * u * c2[0],
                                     (1 - u) ** 2 * a[1] + 2 * (1 - u) * u * b[1] + u * u * c2[1]))
                    i += 2
            for i in range(len(poly)):
                (x0, y0), (x1, y1) = poly[i], poly[(i + 1) % len(poly)]
                if y0 != y1:
                    edges.append((x0, y0, x1, y1))
    cov = [[0.0] * W for _ in range(H)]
    for row in range(H):
        for k in range(sub):
            y = row + (k + 0.5) / sub
            xs = sorted((x0 + (y - y0) * (x1 - x0) / (y1 - y0), 1 if y1 > y0 else -1)
                        for x0, y0, x1, y1 in edges if (y0 <= y < y1) or (y1 <= y < y0))
            w = 0
            for j in range(len(xs) - 1):
                w += xs[j][1]
                if w:
                    xa, b = max(xs[j][0], 0), min(xs[j + 1][0], W)
                    while xa < b:
                        px = int(xa)
                        nxt = min(b, px + 1)
                        cov[row][px] += (nxt - xa) / sub
                        xa = nxt
    return [[255 - 255 * min(v, 1.0) for v in r] for r in cov]


def _outlines(built):
    """FreeType's composed outlines of DejaVu Sans, as integers --
    identical to font.f's, per slice 1 and 2."""
    out = {}
    for line in _oracle(built, SANS):
        if line.startswith("g ") and " c " in line:
            head, pts = line.split(" p")
            ends = [int(v) for v in head.split(":")[1].split()]
            out[int(head.split()[1])] = (ends, [tuple(map(int, p.split(","))) for p in pts.split()])
    return out


class TestGlyphsDrawn:
    """Slice 3. Two references, because neither alone is enough: Cairo
    is what text looks like today, and the true outline is what it
    should look like -- and they are not the same. Measured on these
    glyphs, Cairo's own greyscale rendering is up to 19.7 / 23.2 grey
    levels from the truth at 16 / 32 px, raster.f's 8.0 / 8.5."""

    # Measured maxima against Cairo, per size: 12, 16, 20, 18, 20.
    CAIRO_BOUND = {12: 16, 16: 20, 32: 24, 64: 24, 128: 24}

    @pytest.mark.parametrize("size", sorted(CAIRO_BOUND))
    def test_glyphs_match_cairo_within_a_measured_bound(self, built, tmp_path, size):
        font_path = _font("DejaVuSans.ttf")
        W, H, placed = _layout(_gids(built, GLYPH_TEXT), size)
        prog = _build_render_program(built, _render_program([("r", size, W, H, placed)]))
        tmp_path.mkdir(exist_ok=True)
        (tmp_path / "font.ttf").write_bytes(open(font_path, "rb").read())
        subprocess.run([prog], cwd=tmp_path, check=True, capture_output=True, timeout=300)
        oracle = _build_c(built, "cairo_glyphs", CAIRO_GLYPHS_C, ["cairo", "freetype2"])
        args = [oracle, font_path, str(size), str(W), str(H), str(tmp_path / "c.png")]
        for g, x, y in placed:
            args += [str(g), str(x), str(y)]
        subprocess.run(args, check=True, timeout=300)
        worst, mean, bias, wrong = _stats(_grey(tmp_path / "r.png"), _grey(tmp_path / "c.png"))
        assert wrong == 0, f"{wrong} pixels off by more than 60 -- a misplaced or missing edge"
        assert worst <= self.CAIRO_BOUND[size], f"worst {worst}"
        assert mean <= 3.0 and abs(bias) <= 1.0, (mean, bias)

    @pytest.mark.parametrize("size", [16, 32])
    def test_glyphs_match_the_true_outline(self, built, tmp_path, size):
        """At fractional origins, which Cairo positions its own way and
        the truth does not care about. Measured here at 16 / 32 px:
        worst 8.4 / 8.4, mean 1.84 / 1.25, bias +0.16 / +0.17 (and
        8.0 / 8.5, 1.87 / 1.29 at whole-pixel origins)."""
        font_path = _font("DejaVuSans.ttf")
        W, H, placed = _layout(_gids(built, GLYPH_TEXT), size, frac=True)
        prog = _build_render_program(built, _render_program([("r", size, W, H, placed)]))
        (tmp_path / "font.ttf").write_bytes(open(font_path, "rb").read())
        subprocess.run([prog], cwd=tmp_path, check=True, capture_output=True, timeout=300)
        truth = _true_grey(_outlines(built), placed, size, W, H)
        worst, mean, bias, wrong = _stats(_grey(tmp_path / "r.png"), truth)
        assert wrong == 0
        assert worst <= 10, f"worst {worst:.1f}"
        assert mean <= 2.5, f"mean {mean:.2f}"
        assert abs(bias) <= 0.5, f"bias {bias:+.2f}"

    def test_flattening_meets_its_tolerance(self, built, tmp_path):
        """The segment count is derived, so the tolerance is a
        guarantee: every chord within 0.03 px of its true quadratic,
        checked against the curve sampled densely -- and some chord
        close to it, so the count is not just generously high."""
        curves = [(0, 0, 50, 80, 100, 0), (0, 0, 1, 30, 2, 0), (5, 5, 400, 5, 400, 300),
                  (0, 0, 3, 3, 6, 0), (10, 10, 10.5, 200, 11, 10), (0, 0, 0.1, 0.1, 0.2, 0)]
        prog = ["import font.f"]
        for k, (x0, y0, x1, y1, x2, y2) in enumerate(curves):
            prog += [f"arr[float] p{k} = [{float(x0)!r}, {float(y0)!r}]",
                     f"fntQuadTo(p{k}, {float(x0)!r}, {float(y0)!r}, {float(x1)!r}, {float(y1)!r}, "
                     f"{float(x2)!r}, {float(y2)!r})",
                     f"text t{k} = ''", f"int i{k} = 0",
                     f"while i{k} < p{k}.length {{ t{k} = `${{t{k}}} ${{p{k}[i{k}]}}` i{k} = i{k} + 1 }}",
                     f"log(t{k})"]
        out = _run_in(tmp_path, built.build_program("\n".join(prog) + "\n"), b"")
        worst_seen = 0.0
        for (x0, y0, x1, y1, x2, y2), line in zip(curves, out):
            v = [float(t) for t in line.split()]
            poly = list(zip(v[0::2], v[1::2]))
            assert poly[0] == (x0, y0) and poly[-1] == (x2, y2)
            for j in range(2001):
                t = j / 2000
                px = (1 - t) ** 2 * x0 + 2 * (1 - t) * t * x1 + t * t * x2
                py = (1 - t) ** 2 * y0 + 2 * (1 - t) * t * y1 + t * t * y2
                d = min(_seg_dist(px, py, a, b) for a, b in zip(poly, poly[1:]))
                worst_seen = max(worst_seen, d)
                assert d <= 0.03 + 1e-6, f"{d:.4f} px from the curve {(x0, y0, x1, y1, x2, y2)}"
        assert worst_seen > 0.015, f"worst chord only {worst_seen:.4f} px -- over-flattened"

    def test_where_a_contour_starts_does_not_change_it(self, built, tmp_path):
        """TrueType contours may start on an off-curve point, and the
        walk handles three cases: start on the first point; start on the
        last, when the first is off and the last on; start halfway
        between them, when both are off. The same contour rotated into
        each case must draw the same pixels."""
        outl = _outlines(built)
        g = _gids(built, "S")[0]
        ends, pts = outl[g]
        contour = pts[:ends[0] + 1]
        n = len(contour)
        on = [p[2] for p in contour]
        starts = {
            "on": next(k for k in range(n) if on[k]),
            "off, last on": next(k for k in range(n) if not on[k] and on[k - 1]),
            "off, last off": next(k for k in range(n) if not on[k] and not on[k - 1]),
        }
        prog = ["import font.f", "import raster.f"]
        for name, k in enumerate(starts.values()):
            rot = contour[k:] + contour[:k]
            xs = ", ".join(str(p[0]) for p in rot)
            ys = ", ".join(str(p[1]) for p in rot)
            oc = ", ".join(str(p[2]) for p in rot)
            prog += [f"arr[int] xs{name} = [{xs}]", f"arr[int] ys{name} = [{ys}]",
                     f"arr[int] oc{name} = [{oc}]", f"arr[float] pts{name} = []",
                     f"fntContourPath(xs{name}, ys{name}, oc{name}, 0, {n - 1}, 0.05, 10.0, 110.0, pts{name})",
                     f"arr[int] ends{name} = [Math.floorDiv(pts{name}.length, 2)]",
                     f"arr[int] px{name} = rasNewSurface(120, 120)",
                     f"rasFillPath(px{name}, 120, 120, pts{name}, ends{name}, RAS_NONZERO, 0, 0, 0, 255)",
                     f"img a{name} = imageFromPixels(px{name}, 120, 120)",
                     f"log(a{name}.save('s{name}.png'))"]
        prog_path = _build_render_program(built, "\n".join(prog) + "\n")
        subprocess.run([prog_path], cwd=tmp_path, check=True, capture_output=True, timeout=300)
        from tests.test_raster import _decode_png
        imgs = []
        for name in range(3):
            _, _, rows, nch = _decode_png(str(tmp_path / f"s{name}.png"))
            imgs.append([[row[x * nch + 3] for x in range(120)] for row in rows])
        assert sum(v for r in imgs[0] for v in r) > 100000, "the S has to be drawn"
        for other in imgs[1:]:
            assert max(abs(a - b) for ra, rb in zip(imgs[0], other) for a, b in zip(ra, rb)) <= 1


def _seg_dist(px, py, a, b):
    (ax, ay), (bx, by) = a, b
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    t = 0.0 if L == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


# ---- slice 4: laying out a line ----

CAIRO_TEXT_C = r"""
/* argv: font px, then strings. Per string, under the spec's reference
 * options and the font FILE: the advance, the inked height, and each
 * glyph Cairo's own text_to_glyphs lays out, as gid:x. */
#include <cairo.h>
#include <cairo-ft.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
int main(int argc, char **argv) {
    FT_Library lib; FT_Face face;
    FT_Init_FreeType(&lib);
    if (FT_New_Face(lib, argv[1], 0, &face)) { puts("OPENFAIL"); return 1; }
    cairo_surface_t *s = cairo_image_surface_create(CAIRO_FORMAT_ARGB32, 1, 1);
    cairo_t *cr = cairo_create(s);
    cairo_set_font_face(cr, cairo_ft_font_face_create_for_ft_face(face, 0));
    cairo_set_font_size(cr, atof(argv[2]));
    cairo_font_options_t *fo = cairo_font_options_create();
    cairo_font_options_set_antialias(fo, CAIRO_ANTIALIAS_GRAY);
    cairo_font_options_set_hint_style(fo, CAIRO_HINT_STYLE_NONE);
    cairo_font_options_set_hint_metrics(fo, CAIRO_HINT_METRICS_ON);
    cairo_set_font_options(cr, fo);
    cairo_scaled_font_t *sf = cairo_get_scaled_font(cr);
    for (int i = 3; i < argc; i++) {
        cairo_text_extents_t e;
        cairo_text_extents(cr, argv[i], &e);
        printf("%d %d |", (int)(e.x_advance + 0.5), (int)(e.height + 0.5));
        cairo_glyph_t *glyphs = NULL; int n = 0;
        cairo_scaled_font_text_to_glyphs(sf, 0, 0, argv[i], -1, &glyphs, &n, NULL, NULL, NULL);
        for (int k = 0; k < n; k++) printf(" %lu:%g", glyphs[k].index, glyphs[k].x);
        printf("\n");
        cairo_glyph_free(glyphs);
    }
    return 0;
}
"""

# Everything printable in ASCII that a Festina literal can hold as it
# is, words, composites, a character past U+FFFF that DejaVu does have
# (U+1F600, four bytes of UTF-8), and three it does not -- CJK, one
# from the supplementary CJK plane (also four bytes) and a private-use
# code point -- which must come out as glyph 0 with glyph 0's advance.
LAYOUT_WORDS = ([chr(c) for c in range(33, 127) if chr(c) not in "'`$\\{}"] +
                ["Hello", "xg", " ", "Handgloves", "The quick brown fox", "iiiiiiii", "WAVE",
                 "fi fl", "0123456789", "\u00e9", "\u00c5ngstr\u00f6m", "\u00c9\u00c7\u00d1",
                 "\u4e2d", "a\U0001F600b", "a\U00020000b", "\ue000", "  two  spaces  ", ""])
LAYOUT_SIZES = list(range(6, 73, 3))


def _layout_program(words, sizes, runtime_too=False):
    lit = ", ".join(f"'{w}'" for w in words)
    body = [
        "import font.f", "blob raw = 'font.ttf'", "arr[int] bytes = []", "int i = 0",
        "while i < raw.length {", "    bytes.push(raw.byteAt(i))", "    i = i + 1", "}",
        "int e = fntOpen(bytes)", f"arr[text] words = [{lit}]",
        f"arr[int] sizes = [{', '.join(map(str, sizes))}]", "int s = 0",
        "while s < sizes.length {", "    int px = sizes[s]",
    ]
    if runtime_too:
        body.append("    changeFont(px, null, null)")
    body += [
        "    int k = 0", "    while k < words.length {",
        "        arr[int] gids = []", "        arr[float] pens = []",
        "        int w = fntLayout(words[k], px, 0.0, gids, pens)",
        "        text line = `${px} ${k} ${w} ${fntTextHeight(words[k], px)} |`",
        "        int j = 0",
        "        while j < gids.length {",
        "            line = `${line} ${gids[j]}:${Math.round(pens[j])}`",
        "            j = j + 1",
        "        }",
    ]
    if runtime_too:
        body.append("        line = `${line} | ${measureTextWidth(words[k])} ${measureTextHeight(words[k])}`")
    body += ["        log(line)", "        k = k + 1", "    }", "    s = s + 1", "}"]
    return "\n".join(body) + "\n"


def _sans_is_dejavu():
    try:
        out = subprocess.run(["fc-match", "-f", "%{file}", "sans-serif"],
                             capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        _missing("fc-match is not available to say which file sans-serif is")
    if os.path.realpath(out) != os.path.realpath(SANS):
        _missing(f"sans-serif resolves to {out}, not {SANS}: the runtime would be "
                 f"measuring a different font")


class TestLayout:
    """Slice 4."""

    def _ours(self, built, tmp_path, runtime_too=False):
        prog = built.build_program(_layout_program(LAYOUT_WORDS, LAYOUT_SIZES, runtime_too))
        lines = _run_in(tmp_path, prog, open(_font("DejaVuSans.ttf"), "rb").read())
        out = {}
        for line in lines:
            # "px k width height | gid:x gid:x ... | runtimeWidth runtimeHeight",
            # split on the bar alone: the empty text has no glyphs, so its
            # middle field is empty and " | " would not find both bars.
            parts = line.split("|")
            px, k, w, h = map(int, parts[0].split()[:4])
            out[(px, k)] = (w, h, parts[1].split() if len(parts) > 1 else [],
                            tuple(map(int, parts[2].split())) if len(parts) > 2 else None)
        assert len(out) == len(LAYOUT_WORDS) * len(LAYOUT_SIZES)
        return out

    def test_width_matches_measure_text_width_exactly(self, built, tmp_path):
        """The runtime's own measureTextWidth, called in the same
        program -- the thing slice 6 will replace -- against font.f,
        over every string and size. Exact, because hinting never moves
        an advance here: measured 7,437 of 7,437 in a wider sweep."""
        _sans_is_dejavu()
        ours = self._ours(built, tmp_path, runtime_too=True)
        wrong = [(LAYOUT_WORDS[k], px, w, rt[0]) for (px, k), (w, _, _, rt) in ours.items() if w != rt[0]]
        assert not wrong, wrong[:5]

    def test_layout_and_height_match_unhinted_cairo_exactly(self, built, tmp_path):
        """Against Cairo under the spec's reference options: the
        advance, the inked height, and every glyph's id and pen position
        from Cairo's own text_to_glyphs."""
        ours = self._ours(built, tmp_path)
        oracle = _build_c(built, "cairo_text", CAIRO_TEXT_C, ["cairo", "freetype2"])
        wrong = []
        for px in LAYOUT_SIZES:
            out = subprocess.run([oracle, _font("DejaVuSans.ttf"), str(px)] + LAYOUT_WORDS,
                                 capture_output=True, text=True, check=True).stdout.splitlines()
            for k, line in enumerate(out):
                head, glyphs = line.split(" |")
                w, h = map(int, head.split())
                mine = ours[(px, k)]
                if (w, h, glyphs.split()) != mine[:3]:
                    wrong.append((LAYOUT_WORDS[k], px, (w, h, glyphs.split()[:4]), mine[:2], mine[2][:4]))
        assert not wrong, wrong[:3]

    def test_characters_are_code_points_and_missing_ones_are_glyph_zero(self, built, tmp_path):
        """A four-byte character is ONE glyph -- decoding it as bytes
        would put four here -- whether the font has it (U+1F600 is in
        DejaVu Sans) or not (U+20000 is not, and is glyph 0). CJK and a
        private-use character are glyph 0 too."""
        ours = self._ours(built, tmp_path)
        gids = lambda word: [g.split(":")[0] for g in ours[(12, LAYOUT_WORDS.index(word))][2]]
        assert gids("\u4e2d") == ["0"]
        assert gids("\ue000") == ["0"]
        have = gids("a\U0001F600b")
        assert len(have) == 3 and "0" not in have, have
        lack = gids("a\U00020000b")
        assert len(lack) == 3 and lack[1] == "0" and "0" not in (lack[0], lack[2]), lack

    def test_layout_matches_cairo_where_dejavu_cannot_tell(self, built, tmp_path):
        """Two things DejaVu Sans cannot show, in the built font. Its em
        is 1000 units, not a power of two, so FT_DivFix's rounding
        decides the scale at some sizes. And '^' is the hump: its top is
        an off-curve point twice as high as the curve reaches, so the
        height tells whether the box is the control box (every point) or
        only the on-curve points -- Cairo's answer, not an assumption,
        decides which."""
        text = "Handgloves 0123 xyz"
        font = _build_small_font(_font("DejaVuSans.ttf"), text, upem=1000, hump=0x5E)
        path = tmp_path / "built.ttf"
        path.write_bytes(font)
        words = ["^", "x^x", "Handgloves", "0123", "xyz", "gloves 0"]
        sizes = list(range(6, 73))
        lines = _run_in(tmp_path / "run", built.build_program(_layout_program(words, sizes)), font)
        ours = {}
        for line in lines:
            head, glyphs = line.split("|")[:2]
            px, k, w, h = map(int, head.split())
            ours[(px, k)] = (w, h, glyphs.split())
        oracle = _build_c(built, "cairo_text", CAIRO_TEXT_C, ["cairo", "freetype2"])
        wrong = []
        for px in sizes:
            out = subprocess.run([oracle, str(path), str(px)] + words,
                                 capture_output=True, text=True, check=True).stdout.splitlines()
            for k, line in enumerate(out):
                head, glyphs = line.split(" |")
                w, h = map(int, head.split())
                if (w, h, glyphs.split()) != ours[(px, k)]:
                    wrong.append((words[k], px, (w, h), ours[(px, k)][:2]))
        assert not wrong, wrong[:4]
        # The hump's height is the control box's: 1500 units, not 750.
        assert ours[(40, 0)][1] >= 58, ours[(40, 0)]
