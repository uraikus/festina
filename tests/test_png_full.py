"""runtime.md phase 7, slice 6 (decision 3: implement): png.f decodes every
PNG -- all five colour types at every depth the format allows, Adam7
interlaced or not, and tRNS in all three of its meanings.

Until now png.f refused what it did not cover (1, 2, 4 and 16 bits,
interlacing) and the C loader picked the file up. Once libpng and Cairo
leave there is nothing to pick it up, and on a machine with cairo 1.17.2
or later the C loader already returned garbage for 16-bit files (a float
surface read as ARGB32), so there was never a right answer to fall back
to for those.

The oracle is written here, in Python, from the PNG specification and
from what Cairo's reader does where the spec leaves a choice -- and that
second half is itself checked against Cairo (through ctypes, where the
library can be loaded), so "the same as Cairo" is a measurement and not
a claim:

* the encoder builds each file from a grid of samples at the file's own
  depth, filtering rows with a cycle of all five filter types, packing
  sub-byte samples, and cutting the grid into Adam7 passes;
* the expected RGBA is computed from those samples: grey of 1, 2, 4 bits
  scaled to 8 (x * 255 / (2^d - 1)), 16-bit kept to its high byte, a
  tRNS colour key matched at the file's own depth, a palette alpha per
  entry, no gamma;
* every case Cairo can read is also decoded by Cairo, and its
  premultiplied result must equal the expectation premultiplied the way
  Cairo does it.
"""
import ctypes
import ctypes.util
import os
import random
import struct
import subprocess
import sys
import zlib

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from festina import imports as imports_mod   # noqa: E402

CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
DEPTHS = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
SIZES = ((1, 1), (3, 2), (9, 7), (16, 16), (5, 1), (1, 5), (8, 8), (17, 3))

ADAM7 = ((0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4),
         (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2))


def _chunk(tag, data):
    return (struct.pack(">I", len(data)) + tag + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))


def _pack_row(samples, depth):
    """A row of samples at `depth` bits, leftmost in the high bits."""
    if depth == 16:
        return b"".join(struct.pack(">H", v) for v in samples)
    if depth == 8:
        return bytes(samples)
    out = bytearray()
    bits = 0
    nbits = 0
    for v in samples:
        bits = (bits << depth) | v
        nbits += depth
        if nbits == 8:
            out.append(bits)
            bits = nbits = 0
    if nbits:
        out.append(bits << (8 - nbits))
    return bytes(out)


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    return a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)


def _filter_rows(rows, bpp, first_filter=0):
    """Filter each packed row with a cycle of the five filter types."""
    out = b""
    prev = bytes(len(rows[0])) if rows else b""
    for y, row in enumerate(rows):
        ft = (y + first_filter) % 5
        enc = bytearray()
        for x in range(len(row)):
            a = row[x - bpp] if x >= bpp else 0
            b = prev[x]
            c = prev[x - bpp] if x >= bpp else 0
            pred = (0, a, b, (a + b) // 2, _paeth(a, b, c))[ft]
            enc.append((row[x] - pred) & 255)
        out += bytes([ft]) + bytes(enc)
        prev = row
    return out


def encode(w, h, colour, depth, grid, palette=None, trns=None, interlace=False):
    """`grid[y][x]` is a tuple of the pixel's samples at `depth` bits."""
    bits = CHANNELS[colour] * depth
    bpp = max(1, bits // 8)
    if not interlace:
        rows = [_pack_row([v for px in grid[y] for v in px], depth) for y in range(h)]
        raw = _filter_rows(rows, bpp)
    else:
        raw = b""
        for n, (x0, y0, dx, dy) in enumerate(ADAM7):
            xs = list(range(x0, w, dx))
            ys = list(range(y0, h, dy))
            if not xs or not ys:
                continue
            rows = [_pack_row([v for x in xs for v in grid[y][x]], depth) for y in ys]
            raw += _filter_rows(rows, bpp, first_filter=n)
    out = b"\x89PNG\r\n\x1a\n"
    out += _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, depth, colour, 0, 0, 1 if interlace else 0))
    if palette is not None:
        out += _chunk(b"PLTE", bytes(palette))
    if trns is not None:
        out += _chunk(b"tRNS", bytes(trns))
    # Split the IDAT so the chunk concatenation is exercised too.
    z = zlib.compress(raw, 6)
    third = max(1, len(z) // 3)
    for i in range(0, len(z), third):
        out += _chunk(b"IDAT", z[i:i + third])
    return out + _chunk(b"IEND", b"")


def expected(w, h, colour, depth, grid, palette=None, trns=None):
    """The straight RGBA a reader that follows libpng's conventions
    produces, flat."""
    maxv = (1 << depth) - 1

    def byte(v):
        if depth == 16:
            return v >> 8
        if depth == 8:
            return v
        return v * 255 // maxv

    key = None
    if colour == 0 and trns is not None:
        key = ((trns[0] << 8) | trns[1],)
    if colour == 2 and trns is not None:
        key = tuple((trns[i] << 8) | trns[i + 1] for i in (0, 2, 4))
    out = []
    for y in range(h):
        for x in range(w):
            s = grid[y][x]
            if colour == 0:
                g = byte(s[0])
                out += [g, g, g, 0 if key == (s[0],) else 255]
            elif colour == 2:
                out += [byte(s[0]), byte(s[1]), byte(s[2]), 0 if key == tuple(s) else 255]
            elif colour == 3:
                i = s[0]
                a = trns[i] if trns is not None and i < len(trns) else 255
                out += [palette[i * 3], palette[i * 3 + 1], palette[i * 3 + 2], a]
            elif colour == 4:
                g = byte(s[0])
                out += [g, g, g, byte(s[1])]
            else:
                out += [byte(s[0]), byte(s[1]), byte(s[2]), byte(s[3])]
    return out


def _cases(colour, seed):
    """Every depth x interlace x size of one colour type, some with tRNS."""
    rng = random.Random(seed)
    cases = []
    for depth in DEPTHS[colour]:
        maxv = (1 << depth) - 1
        for interlace in (False, True):
            for (w, h) in SIZES:
                palette = trns = None
                nch = CHANNELS[colour]
                if colour == 3:
                    n = min(1 << depth, 256)
                    palette = [rng.randrange(256) for _ in range(n * 3)]
                    if (w + h) % 2:
                        trns = [rng.randrange(256) for _ in range(max(1, n // 2))]
                    grid = [[(rng.randrange(n),) for _ in range(w)] for _ in range(h)]
                else:
                    grid = [[tuple(rng.randrange(maxv + 1) for _ in range(nch))
                             for _ in range(w)] for _ in range(h)]
                    if colour in (0, 2) and (w * h) % 2 == 0:
                        # a colour key that some pixels really carry
                        pick = grid[rng.randrange(h)][rng.randrange(w)]
                        trns = b"".join(struct.pack(">H", v) for v in pick)
                        # Pixels that miss the key by one in ONE sample each --
                        # a key compared on fewer samples than the file has, or
                        # after a 16-bit sample was cut to a byte, keys them too.
                        spots = [(x, y) for y in range(h) for x in range(w)]
                        rng.shuffle(spots)
                        for n, (x, y) in enumerate(spots[:nch * 2]):
                            near = list(pick)
                            near[n % nch] = (near[n % nch] + (1 if n < nch else 256)) & maxv
                            grid[y][x] = tuple(near)
                        grid[spots[-1][1]][spots[-1][0]] = tuple(pick)
                cases.append(dict(w=w, h=h, colour=colour, depth=depth, interlace=interlace,
                                  grid=grid, palette=palette, trns=trns))
    return cases


_PROGRAM = """import png.f

arr[int] func load(path:text) {{
    blob raw = path
    arr[int] bytes = []
    int i = 0
    while i < raw.length {{
        bytes.push(raw.byteAt(i))
        i = i + 1
    }}
    return bytes
}}

arr[text] names = [{names}]
int n = 0
while n < names.length {{
    arr[int] px = pngDecode(load(names[n]))
    log(`${{PNG_W}} ${{PNG_H}} ${{PNG_ERR}} ${{px.length}}`)
    text o = ''
    int k = 0
    while k < px.length {{
        o = `${{o}} ${{px[k]}}`
        k = k + 1
    }}
    log(o)
    n = n + 1
}}
"""


def _run_cases(tmp_path, cli_mod, cases):
    from tests.conftest import compile_file_or_skip, _require_c_compiler
    src = os.path.join(imports_mod.RUNTIME_COMPONENT_DIR)
    for name in ("png.f", "inflate.f"):
        (tmp_path / name).write_text(
            open(os.path.join(src, name), encoding="utf-8").read(), encoding="utf-8")
    names = []
    for i, c in enumerate(cases):
        name = f"c{i}.png"
        (tmp_path / name).write_bytes(encode(
            c["w"], c["h"], c["colour"], c["depth"], c["grid"], c["palette"], c["trns"],
            c["interlace"]))
        names.append(name)
    main = tmp_path / "main.f"
    main.write_text(_PROGRAM.format(names=", ".join(f"'{n}'" for n in names)), encoding="utf-8")
    out = tmp_path / "program"
    compile_file_or_skip(cli_mod, str(main), str(out), cc=_require_c_compiler())
    r = subprocess.run([str(out)], cwd=tmp_path, capture_output=True, text=True, timeout=300,
                       env=dict(os.environ, DISPLAY=""))
    assert r.returncode == 0, r.stdout + r.stderr
    lines = r.stdout.splitlines()
    assert len(lines) == 2 * len(cases), len(lines)
    return names, [(lines[2 * i], [int(v) for v in lines[2 * i + 1].split()])
                   for i in range(len(cases))]


def _label(c):
    return (f"colour {c['colour']} depth {c['depth']} {'adam7' if c['interlace'] else 'plain'} "
            f"{c['w']}x{c['h']}{' trns' if c['trns'] is not None else ''}")


@pytest.mark.parametrize("colour", [0, 2, 3, 4, 6])
def test_every_depth_and_layout_decodes_to_the_expected_pixels(colour, tmp_path, cli_mod):
    cases = _cases(colour, seed=100 + colour)
    _, results = _run_cases(tmp_path, cli_mod, cases)
    for c, (head, px) in zip(cases, results):
        want = expected(c["w"], c["h"], c["colour"], c["depth"], c["grid"], c["palette"], c["trns"])
        assert head == f"{c['w']} {c['h']} 0 {c['w'] * c['h'] * 4}", (_label(c), head)
        assert px == want, (_label(c), [(i, a, b) for i, (a, b) in enumerate(zip(px, want)) if a != b][:6])


# ---- the expectation is Cairo's, where Cairo can say ----

def _cairo():
    name = ctypes.util.find_library("cairo") or ("libcairo-2.dll" if os.name == "nt" else None)
    if not name:
        return None
    try:
        lib = ctypes.CDLL(name)
    except OSError:
        return None
    lib.cairo_image_surface_create_from_png.restype = ctypes.c_void_p
    lib.cairo_image_surface_create_from_png.argtypes = [ctypes.c_char_p]
    for f in ("cairo_surface_status", "cairo_image_surface_get_width",
              "cairo_image_surface_get_height", "cairo_image_surface_get_stride",
              "cairo_image_surface_get_format", "cairo_surface_destroy"):
        getattr(lib, f).argtypes = [ctypes.c_void_p]
    lib.cairo_image_surface_get_data.restype = ctypes.POINTER(ctypes.c_ubyte)
    lib.cairo_image_surface_get_data.argtypes = [ctypes.c_void_p]
    return lib


def _cairo_read(lib, path):
    s = lib.cairo_image_surface_create_from_png(path.encode())
    try:
        if lib.cairo_surface_status(s):
            return None
        if lib.cairo_image_surface_get_format(s) not in (0, 1):     # ARGB32, RGB24
            return None     # a float surface: cairo 1.17.2+ for 16-bit files
        w = lib.cairo_image_surface_get_width(s)
        h = lib.cairo_image_surface_get_height(s)
        stride = lib.cairo_image_surface_get_stride(s)
        d = lib.cairo_image_surface_get_data(s)
        out = []
        for y in range(h):
            for x in range(w):
                o = y * stride + x * 4
                out += [d[o + 2], d[o + 1], d[o], d[o + 3]]     # r, g, b, a (premultiplied)
        return out
    finally:
        lib.cairo_surface_destroy(s)


def _premultiply(rgba):
    def mul(c, a):
        t = c * a + 0x80
        return ((t >> 8) + t) >> 8
    out = []
    for i in range(0, len(rgba), 4):
        r, g, b, a = rgba[i:i + 4]
        out += [mul(r, a), mul(g, a), mul(b, a), a]
    return out


@pytest.mark.parametrize("colour", [0, 2, 3, 4, 6])
def test_the_expectation_is_what_cairo_reads(colour, tmp_path):
    lib = _cairo()
    if lib is None:
        pytest.skip("cairo cannot be loaded here")
    checked = 0
    for c in _cases(colour, seed=100 + colour):
        path = str(tmp_path / "c.png")
        open(path, "wb").write(encode(c["w"], c["h"], c["colour"], c["depth"], c["grid"],
                                      c["palette"], c["trns"], c["interlace"]))
        got = _cairo_read(lib, path)
        if got is None:
            assert c["depth"] == 16, ("cairo refused a file that is not 16-bit", _label(c))
            continue
        want = _premultiply(expected(c["w"], c["h"], c["colour"], c["depth"], c["grid"],
                                     c["palette"], c["trns"]))
        assert got == want, (_label(c), [(i, a, b) for i, (a, b) in enumerate(zip(got, want)) if a != b][:6])
        checked += 1
    assert checked >= 12, checked
