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
import os
import shutil
import struct
import subprocess
import sys

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
        d = self.root / f"prog{len(self.programs)}"
        d.mkdir()
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


def _build_small_font(source_path, text):
    """A real TrueType font holding .notdef and the SIMPLE glyphs for
    `text`, with a short loca, a format-4 cmap only, and fewer hmtx
    entries than glyphs -- the three paths DejaVu itself never takes.
    The last few glyphs share one advance, which is what makes the
    shorter hmtx legal: it stores that advance once."""
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
    # Monospace the tail so hmtx can stop early: the last three glyphs
    # take the advance of the one before them.
    advances = [_metric(data, t, g)[0] for g in glyphs]
    lsbs = [_metric(data, t, g)[1] for g in glyphs]
    nh = len(glyphs) - 3
    for k in range(nh, len(glyphs)):
        advances[k] = advances[nh - 1]
    glyf, loca = b"", []
    for g in glyphs:
        body = _glyph_bytes(data, t, g)
        if len(body) % 2:
            body += b"\0"
        loca.append(len(glyf) // 2)
        glyf += body
    loca.append(len(glyf) // 2)
    assert loca[-1] < 65536, "too big for a short loca"
    head = bytearray(data[t[b"head"][0]:t[b"head"][0] + t[b"head"][1]])
    head[50:52] = struct.pack(">h", 0)
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
