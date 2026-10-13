"""runtime.md phase 7, slice 8: which font file a family name means.

runtime/festina_fontscan.h walks the platform's font directories and reads
each TrueType face's family, weight and slant from its own tables; this file
checks it two ways.

* against FreeType, over the fonts this machine really has: the same
  family, the same bold and italic, face for face;
* against fonts BUILT here, because the real ones do not exercise the
  cases that matter -- name ID 16 over ID 1, a Macintosh-only name, a name
  outside the BMP, a collection, a CFF file that must not be listed, a
  family with only some of its styles -- and because choosing between a
  family's faces (and what has to be synthesized) is a rule this project
  decided and nothing else defines.

The header is static C, so the tests include it into a small program.
"""
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.test_font import _build_ttc, BUNDLED_SANS   # noqa: E402

_ROOT = Path(__file__).resolve().parent.parent
_HEADER_DIR = _ROOT / "runtime"

_HARNESS = r"""
#include "festina_fontscan.h"
int main(int argc, char **argv) {
    if (argc >= 2 && !strcmp(argv[1], "scan")) {
        festina_fontscan_ensure();
        for (size_t i = 0; i < g_fontscan_count; i++) {
            FestinaFontFace *f = &g_fontscan_faces[i];
            printf("%s|%d|%d|%d|%s\n", f->family, f->weight, f->italic, f->face, f->path);
        }
        return 0;
    }
    if (argc >= 5 && !strcmp(argv[1], "resolve")) {
        FestinaFontChoice c = festina_font_resolve(argv[2], atoi(argv[3]), atoi(argv[4]));
        if (c.bundled) printf("bundled|%d|%d\n", c.synth_bold, c.synth_italic);
        else printf("%s|%d|%d|%d|%d|%d|%s\n", c.face->family, c.face->weight, c.face->italic,
                    c.face->face, c.synth_bold, c.synth_italic, c.face->path);
        return 0;
    }
    if (argc >= 3 && !strcmp(argv[1], "read")) {
        size_t n = 0;
        unsigned char *b = festina_font_read_file(argv[2], &n);
        printf("%s %zu\n", b ? "ok" : "none", n);
        free(b);
        return 0;
    }
    return 2;
}
"""


@pytest.fixture(scope="module")
def harness(tmp_path_factory):
    cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if not cc:
        pytest.skip("no C compiler")
    if sys.platform == "win32":
        pytest.skip("the harness is built with a POSIX shell layout")
    d = tmp_path_factory.mktemp("fontscan")
    (d / "h.c").write_text(_HARNESS)
    out = d / "h"
    subprocess.run([cc, "-O1", "-Wall", "-Werror", "-I", str(_HEADER_DIR), str(d / "h.c"), "-o", str(out)],
                   check=True)
    return str(out)


def _run(harness, dirs, *args):
    env = dict(os.environ, FESTINA_FONT_DIRS=os.pathsep.join(str(d) for d in dirs))
    r = subprocess.run([harness, *args], capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == 0, r.stderr
    return r.stdout.splitlines()


def _scan(harness, dirs):
    return [tuple(l.split("|")) for l in _run(harness, dirs, "scan")]


def _resolve(harness, dirs, family, bold=0, italic=0):
    return _run(harness, dirs, "resolve", family, str(bold), str(italic))[0].split("|")


# ---- building fonts ----

def _sfnt_tables(data):
    n = struct.unpack(">H", data[4:6])[0]
    out = {}
    for i in range(n):
        tag, _, off, ln = struct.unpack(">4sIII", data[12 + 16 * i:28 + 16 * i])
        out[tag] = data[off:off + ln]
    return out


def _pack(tables, version=b"\x00\x01\x00\x00"):
    tags = sorted(tables)
    n = len(tags)
    head = bytearray(version + struct.pack(">HHHH", n, 0, 0, 0))
    body = bytearray()
    base = 12 + 16 * n
    recs = bytearray()
    for tag in tags:
        t = tables[tag]
        recs += struct.pack(">4sIII", tag, 0, base + len(body), len(t))
        body += t
        body += b"\0" * (-len(body) % 4)
    return bytes(head + recs + body)


def _name_table(records):
    """records: (platform, encoding, language, nameID, str) -> a name table."""
    strings = bytearray()
    recs = bytearray()
    for plat, enc, lang, nid, s in records:
        raw = s.encode("utf-16-be") if plat in (0, 3) else s.encode("latin-1")
        recs += struct.pack(">HHHHHH", plat, enc, lang, nid, len(raw), len(strings))
        strings += raw
    return struct.pack(">HHH", 0, len(records), 6 + 12 * len(records)) + bytes(recs) + bytes(strings)


_BASES = {}


def _font(family, weight=400, italic=False, names=None, mac_style=None, os2_len=96, base=None):
    """A font with its identity replaced -- name table, OS/2 weight and
    slant, head.macStyle -- and the outlines of `base` (a path; the bundled
    DejaVu Sans by default)."""
    base = base or BUNDLED_SANS
    if base not in _BASES:
        _BASES[base] = _sfnt_tables(open(base, "rb").read())
    t = dict(_BASES[base])
    style = ("Bold " if weight >= 600 else "") + ("Italic" if italic else "")
    t[b"name"] = _name_table(names or [(3, 1, 0x409, 1, family), (3, 1, 0x409, 2, style.strip() or "Regular")])
    os2 = bytearray(t[b"OS/2"][:os2_len].ljust(os2_len, b"\0"))
    struct.pack_into(">H", os2, 4, weight)
    if os2_len >= 64:
        struct.pack_into(">H", os2, 62, (1 if italic else 0) | (0x20 if weight >= 600 else 0))
    t[b"OS/2"] = bytes(os2)
    head = bytearray(t[b"head"])
    ms = ((1 if weight >= 600 else 0) | (2 if italic else 0)) if mac_style is None else mac_style
    struct.pack_into(">H", head, 44, ms)
    t[b"head"] = bytes(head)
    return _pack(t)


def _put(d, name, data):
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_bytes(data)
    return d / name


# ---- against FreeType, over the fonts that are really here ----

_FT_C = r"""
#include <ft2build.h>
#include FT_FREETYPE_H
#include FT_TRUETYPE_TABLES_H
#include <stdio.h>
#include <ctype.h>
int main(int argc, char **argv) {
    FT_Library lib; FT_Init_FreeType(&lib);
    for (int a = 1; a < argc; a++) {
        FT_Face f;
        if (FT_New_Face(lib, argv[a], 0, &f)) continue;
        long n = f->num_faces; FT_Done_Face(f);
        for (long i = 0; i < n; i++) {
            if (FT_New_Face(lib, argv[a], i, &f)) continue;
            TT_OS2 *os2 = FT_Get_Sfnt_Table(f, ft_sfnt_os2);
            int italic = (f->style_flags & FT_STYLE_FLAG_ITALIC) ? 1 : 0;
            printf("%ld|%d|%d|%s|", i, os2 ? os2->usWeightClass : 0, italic, argv[a]);
            for (const char *p = f->family_name; p && *p; p++) putchar(tolower((unsigned char)*p));
            printf("|%d\n", FT_IS_SFNT(f) && FT_HAS_MULTIPLE_MASTERS(f) ? 1 : 0);
            FT_Done_Face(f);
        }
    }
    return 0;
}
"""


def test_the_system_fonts_read_as_freetype_reads_them(harness, tmp_path):
    """Every TrueType face this machine has: the same family, weight class
    and slant as FreeType reports. (FreeType prefers name ID 1 when a font
    gives both IDs 1 and 16, this prefers 16, so a face whose two differ is
    compared on weight and slant only -- and counted, so the exemption
    cannot swallow the test.)"""
    cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    try:
        flags = subprocess.run(["pkg-config", "--cflags", "--libs", "freetype2"], capture_output=True,
                               text=True, check=True).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("FreeType (pkg-config freetype2) is not installed")
    roots = [p for p in ("/usr/share/fonts", "/usr/local/share/fonts") if os.path.isdir(p)]
    files = sorted(str(p) for r in roots for p in Path(r).rglob("*") if p.suffix.lower() in (".ttf", ".ttc"))
    if len(files) < 3:
        pytest.skip("fewer than three system TrueType fonts to compare")
    (tmp_path / "ft.c").write_text(_FT_C)
    subprocess.run([cc, str(tmp_path / "ft.c"), "-o", str(tmp_path / "ft")] + flags, check=True)
    theirs = {}
    for line in subprocess.run([str(tmp_path / "ft"), *files], capture_output=True, text=True,
                               check=True).stdout.splitlines():
        face, weight, italic, path, family, _ = line.split("|")
        theirs[(path, int(face))] = (family, int(weight), int(italic))
    ours = {(path, int(face)): (family, int(weight), int(italic))
            for family, weight, italic, face, path in _scan(harness, roots)}
    assert ours, "nothing was listed"
    compared = exempt = 0
    for key, (family, weight, italic) in ours.items():
        assert key in theirs, f"{key} is listed here and FreeType cannot open it"
        tf, tw, ti = theirs[key]
        assert (weight, italic) == (tw, ti), (key, (weight, italic), (tw, ti))
        if family == tf:
            compared += 1
        else:
            exempt += 1
    assert compared >= 3 and compared > 4 * exempt, (compared, exempt)
    # nothing FreeType opens as TrueType is missing, except what is declared
    missing = [k for k in theirs if k not in ours and k[0].lower().endswith(".ttc") is False]
    assert len(missing) <= len(theirs) // 10 + 2, missing[:5]


# ---- choosing between a family's faces, on fonts built here ----

def _family_dir(tmp_path, family, styles, sub="f"):
    """styles: list of (weight, italic) -> a directory with one font file
    per style."""
    d = tmp_path / sub
    for i, (w, it) in enumerate(styles):
        _put(d, f"{family.replace(' ', '')}-{w}{'i' if it else ''}.ttf", _font(family, w, it))
    return d


ALL4 = [(400, False), (700, False), (400, True), (700, True)]


@pytest.mark.parametrize("bold,italic,want", [
    (0, 0, (400, 0)), (1, 0, (700, 0)), (0, 1, (400, 1)), (1, 1, (700, 1))])
def test_a_family_with_every_style_gives_the_exact_face_and_synthesizes_nothing(
        harness, tmp_path, bold, italic, want):
    d = _family_dir(tmp_path, "Fixture Serif", ALL4)
    got = _resolve(harness, [d], "Fixture Serif", bold, italic)
    assert got[0] == "fixture serif"
    assert (int(got[1]), int(got[2])) == want
    assert got[4:6] == ["0", "0"], got


@pytest.mark.parametrize("styles,bold,italic,synth", [
    ([(400, False)], 0, 0, (0, 0)),
    ([(400, False)], 1, 0, (1, 0)),
    ([(400, False)], 0, 1, (0, 1)),
    ([(400, False)], 1, 1, (1, 1)),
    ([(400, False), (700, False)], 0, 1, (0, 1)),            # italic missing, bold present
    ([(400, False), (700, False)], 1, 1, (0, 1)),            # the bold face, slanted
    ([(400, False), (400, True)], 1, 0, (1, 0)),             # bold missing: the upright regular
    ([(400, False), (400, True)], 1, 1, (1, 0)),             # the real italic, thickened
    ([(700, False)], 0, 0, (0, 0)),                          # only bold exists: nothing to un-bold
])
def test_what_the_chosen_face_lacks_is_what_gets_synthesized(harness, tmp_path, styles, bold, italic, synth):
    d = _family_dir(tmp_path, "Fixture Serif", styles)
    got = _resolve(harness, [d], "Fixture Serif", bold, italic)
    assert got[0] == "fixture serif"
    assert (int(got[4]), int(got[5])) == synth, got


@pytest.mark.parametrize("weights,bold,want_weight,want_synth_bold", [
    ([300, 400, 500, 700, 900], 0, 400, 0),
    ([300, 400, 500, 700, 900], 1, 700, 0),
    ([300, 900], 0, 300, 0),          # 100 away beats 500
    ([300, 900], 1, 900, 0),
    ([300], 1, 300, 1),               # nothing bold: thicken the light one
    ([500, 600], 1, 600, 0),          # semibold counts as bold
    ([500], 1, 500, 1),
    ([100, 200], 0, 200, 0),
])
def test_the_face_nearest_the_wanted_weight_is_chosen(harness, tmp_path, weights, bold,
                                                       want_weight, want_synth_bold):
    d = _family_dir(tmp_path, "Weighty", [(w, False) for w in weights])
    got = _resolve(harness, [d], "weighty", bold, 0)
    assert int(got[1]) == want_weight and int(got[4]) == want_synth_bold, got


def test_italic_outweighs_weight(harness, tmp_path):
    d = _family_dir(tmp_path, "Slanty", [(400, False), (900, True)])
    got = _resolve(harness, [d], "Slanty", 0, 1)
    assert (int(got[1]), int(got[2])) == (900, 1), "the italic face, though far from 400"
    assert got[4:6] == ["0", "0"]


# ---- names ----

def test_the_typographic_family_is_preferred_to_the_legacy_one(harness, tmp_path):
    d = tmp_path / "f"
    _put(d, "a.ttf", _font("x", 300, names=[(3, 1, 0x409, 1, "Foo Light"), (3, 1, 0x409, 2, "Regular"),
                                          (3, 1, 0x409, 16, "Foo"), (3, 1, 0x409, 17, "Light")]))
    assert [r[0] for r in _scan(harness, [d])] == ["foo"]


def test_english_is_preferred_among_languages(harness, tmp_path):
    d = tmp_path / "f"
    _put(d, "a.ttf", _font("x", names=[(3, 1, 0x407, 1, "Schrift"), (3, 1, 0x409, 1, "Typeface")]))
    assert [r[0] for r in _scan(harness, [d])] == ["typeface"]


def test_a_macintosh_only_name_is_read(harness, tmp_path):
    d = tmp_path / "f"
    _put(d, "a.ttf", _font("x", names=[(1, 0, 0, 1, "Maclike"), (1, 0, 0, 2, "Regular")]))
    assert [r[0] for r in _scan(harness, [d])] == ["maclike"]


def test_the_unicode_platform_name_is_read(harness, tmp_path):
    d = tmp_path / "f"
    _put(d, "a.ttf", _font("x", names=[(0, 3, 0, 1, "Uni Platform")]))
    assert [r[0] for r in _scan(harness, [d])] == ["uni platform"]


def test_a_name_outside_ascii_and_the_bmp_keeps_its_bytes(harness, tmp_path):
    d = tmp_path / "f"
    _put(d, "a.ttf", _font("x", names=[(3, 10, 0x409, 1, "Fönt \U0001F600 Ж")]))
    got = _scan(harness, [d])
    assert [r[0] for r in got] == ["fönt \U0001F600 Ж"]
    # ASCII folds, the rest compares byte for byte: a different case of 'ö' is not found
    assert _resolve(harness, [d], "FÖNT \U0001F600 Ж")[0] == "bundled"
    assert _resolve(harness, [d], "Fönt \U0001F600 Ж")[0] == "fönt \U0001F600 Ж"


def test_a_face_with_no_family_name_is_not_listed(harness, tmp_path):
    d = tmp_path / "f"
    _put(d, "a.ttf", _font("x", names=[(3, 1, 0x409, 4, "Full Name Only")]))
    assert _scan(harness, [d]) == []


def test_old_weight_scales_and_missing_os2_data_fall_back(harness, tmp_path):
    d = tmp_path / "f"
    _put(d, "old.ttf", _font("Oldscale", 700 // 100))                       # usWeightClass 7: the 1-9 scale
    _put(d, "short.ttf", _font("Shortos", 700, True, os2_len=20))            # no fsSelection: head.macStyle decides
    rows = {r[0]: r for r in _scan(harness, [d])}
    assert int(rows["oldscale"][1]) == 700
    assert (int(rows["shortos"][1]), int(rows["shortos"][2])) == (700, 1)


def test_matching_ignores_ascii_case_quotes_and_spaces(harness, tmp_path):
    d = _family_dir(tmp_path, "Fixture Serif", [(400, False)])
    for form in ("fixture serif", "FIXTURE SERIF", "  'Fixture Serif'  ", '"Fixture Serif"'):
        assert _resolve(harness, [d], form)[0] == "fixture serif", form


# ---- lists, generics and the bundled face ----

def test_a_family_list_takes_the_first_one_found(harness, tmp_path):
    d = _family_dir(tmp_path, "Fixture Serif", [(400, False)])
    assert _resolve(harness, [d], "Nope, Also Nope, Fixture Serif, sans-serif")[0] == "fixture serif"


def test_sans_serif_ends_the_search_and_is_the_bundled_face(harness, tmp_path):
    d = _family_dir(tmp_path, "Fixture Serif", [(400, False)])
    assert _resolve(harness, [d], "sans-serif, Fixture Serif") == ["bundled", "0", "0"]
    assert _resolve(harness, [d], "SANS-SERIF", 1, 1) == ["bundled", "1", "1"]
    assert _resolve(harness, [d], "", 0, 0) == ["bundled", "0", "0"]


def test_a_family_that_is_not_found_is_the_bundled_face_with_the_style_synthesized(harness, tmp_path):
    d = _family_dir(tmp_path, "Fixture Serif", [(400, False)])
    assert _resolve(harness, [d], "Missing Family") == ["bundled", "0", "0"]
    assert _resolve(harness, [d], "Missing Family", 1, 0) == ["bundled", "1", "0"]
    assert _resolve(harness, [d], "Missing Family", 1, 1) == ["bundled", "1", "1"]
    assert _resolve(harness, [tmp_path / "no_such_dir"], "Fixture Serif") == ["bundled", "0", "0"]


@pytest.mark.parametrize("generic,present,expect", [
    ("serif", ["Liberation Serif", "DejaVu Serif"], "liberation serif"),
    ("serif", ["DejaVu Serif", "Times New Roman", "Noto Serif"], "times new roman"),
    ("serif", ["Noto Serif"], "noto serif"),
    ("monospace", ["DejaVu Sans Mono", "Courier New", "Liberation Mono"], "courier new"),
    ("monospace", ["Noto Sans Mono", "DejaVu Sans Mono"], "dejavu sans mono"),
    ("MONOSPACE", ["Consolas"], "consolas"),
])
def test_serif_and_monospace_try_a_fixed_list_in_order(harness, tmp_path, generic, present, expect):
    d = tmp_path / "f"
    for fam in present:
        _put(d, fam.replace(" ", "") + ".ttf", _font(fam))
    assert _resolve(harness, [d], generic)[0] == expect


def test_a_generic_with_nothing_on_its_list_is_the_bundled_face(harness, tmp_path):
    d = _family_dir(tmp_path, "Unrelated", [(400, False)])
    assert _resolve(harness, [d], "serif", 1, 0) == ["bundled", "1", "0"]
    assert _resolve(harness, [d], "monospace") == ["bundled", "0", "0"]


# ---- files and directories ----

def test_collections_list_every_face_with_its_index(harness, tmp_path):
    d = tmp_path / "f"
    _put(d, "pair.ttc", _build_ttc([_font("First Face", 400), _font("Second Face", 700, True)]))
    rows = sorted(_scan(harness, [d]))
    assert [(r[0], r[3]) for r in rows] == [("first face", "0"), ("second face", "1")]
    got = _resolve(harness, [d], "Second Face", 1, 1)
    assert got[0] == "second face" and got[3] == "1" and got[4:6] == ["0", "0"]


def test_cff_collections_damage_and_other_files_are_not_listed(harness, tmp_path):
    d = tmp_path / "f"
    good = _font("Good One")
    cff = bytearray(good)
    cff[0:4] = b"OTTO"
    _put(d, "good.ttf", good)
    _put(d, "cff_in_ttf.ttf", bytes(cff))
    _put(d, "real.otf", good)                    # the right bytes, an extension that is never read
    _put(d, "notes.txt", good)
    _put(d, "garbage.ttf", b"\x00\x01\x02" * 100)
    _put(d, "short.ttf", good[:5])
    _put(d, "empty.ttf", b"")
    _put(d, "cut.ttf", good[:2000])              # the directory is there, the name table is not
    _put(d, "badttc.ttc", b"ttcf" + b"\x00\x01\x00\x00" + struct.pack(">I", 3) + b"\0" * 4)
    assert [r[0] for r in _scan(harness, [d])] == ["good one"]


def test_subdirectories_are_walked_and_several_directories_are_searched(harness, tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    _put(a / "x" / "y" / "z", "deep.ttf", _font("Deep One"))
    _put(b, "top.ttf", _font("Top One"))
    got = sorted(r[0] for r in _scan(harness, [a, tmp_path / "nowhere", b]))
    assert got == ["deep one", "top one"]


def test_the_first_face_by_file_name_wins_a_tie(harness, tmp_path):
    d = tmp_path / "f"
    _put(d, "b.ttf", _font("Twin"))
    _put(d, "a.ttf", _font("Twin"))
    got = _resolve(harness, [d], "Twin")
    assert got[-1].endswith("a.ttf"), got


def test_a_file_is_read_whole_unless_it_is_too_big(harness, tmp_path):
    d = tmp_path / "f"
    p = _put(d, "g.ttf", _font("Good One"))
    out = _run(harness, [d], "read", str(p))[0].split()
    assert out == ["ok", str(p.stat().st_size)]
    assert _run(harness, [d], "read", str(d / "missing.ttf"))[0].split()[0] == "none"
    big = d / "big.ttf"
    with open(big, "wb") as f:
        f.truncate(17 * 1024 * 1024)
    assert _run(harness, [d], "read", str(big))[0].split()[0] == "none"
    empty = _put(d, "empty.ttf", b"")
    assert _run(harness, [d], "read", str(empty))[0].split()[0] == "none"
