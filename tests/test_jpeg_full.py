"""runtime.md phase 7, slice 6 (decision 3: implement): jpeg.f decodes
progressive JPEG.

The reference is libjpeg's own output, stored. `tests/fixtures/jpeg/`
holds twenty small pictures that libjpeg compressed with different
options -- baseline and progressive, 4:4:4, 4:2:0, 4:2:2, 4:4:0, grey,
RGB-coded, restart intervals, baseline in three scans, odd sizes down to
1x1 -- each beside the `.rgb` libjpeg itself decodes it to with the
settings the runtime's C loader used (make_fixtures.c regenerates both).
jpeg.f is held to those bytes, so the oracle is libjpeg and is not
re-derived from anything in this repository.

(The oracle the suite had before this was not one. The phase 2 test named
"agrees with libjpeg" loaded the fixture with `img = 'x.jpg'`, and since
the decoders were wired in (#346) that call goes through jpeg.f first --
the test compared jpeg.f with itself.)

Two more checks need no libjpeg at all. A progressive file made from a
baseline one by recoding the SAME coefficients must decode to the same
pixels as the baseline: the pairs below are made that way (same synthetic
picture, same quality and sampling), and they hold jpeg.f to itself across
the two scan structures.
"""
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from festina import imports as imports_mod   # noqa: E402

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "jpeg")
NAMES = sorted(n[:-4] for n in os.listdir(_DIR) if n.endswith(".jpg"))

_PROGRAM = """import jpeg.f

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
    arr[int] px = jpgDecode(load(names[n]))
    log(`${{JPG_W}} ${{JPG_H}} ${{JPG_ERR}} ${{px.length}}`)
    text o = ''
    int k = 0
    while k < px.length {{
        o = `${{o}} ${{px[k]}} ${{px[k + 1]}} ${{px[k + 2]}}`
        k = k + 4
    }}
    log(o)
    n = n + 1
}}
"""


@pytest.fixture(scope="module")
def decoded(tmp_path_factory, cli_mod=None):
    from tests.conftest import compile_file_or_skip, _require_c_compiler, import_spec_module
    cli = import_spec_module("cli")
    tmp = tmp_path_factory.mktemp("jpegfull")
    src = imports_mod.RUNTIME_COMPONENT_DIR
    (tmp / "jpeg.f").write_text(open(os.path.join(src, "jpeg.f"), encoding="utf-8").read(),
                                encoding="utf-8")
    for n in NAMES:
        (tmp / f"{n}.jpg").write_bytes(open(os.path.join(_DIR, f"{n}.jpg"), "rb").read())
    main = tmp / "main.f"
    main.write_text(_PROGRAM.format(names=", ".join(f"'{n}.jpg'" for n in NAMES)), encoding="utf-8")
    out = tmp / "program"
    compile_file_or_skip(cli, str(main), str(out), cc=_require_c_compiler())
    r = subprocess.run([str(out)], cwd=tmp, capture_output=True, text=True, timeout=600,
                       env=dict(os.environ, DISPLAY=""))
    assert r.returncode == 0, r.stdout + r.stderr
    lines = r.stdout.splitlines()
    assert len(lines) == 2 * len(NAMES)
    return {n: (lines[2 * i], [int(v) for v in lines[2 * i + 1].split()])
            for i, n in enumerate(NAMES)}


def _reference(name):
    return list(open(os.path.join(_DIR, f"{name}.rgb"), "rb").read())


def _dims(name):
    # Picked out of the .jpg's SOF marker, so the test does not trust jpeg.f for the size.
    d = open(os.path.join(_DIR, f"{name}.jpg"), "rb").read()
    i = 2
    while i < len(d):
        m = d[i + 1]
        ln = (d[i + 2] << 8) | d[i + 3]
        if m in (0xC0, 0xC1, 0xC2):
            return (d[i + 7] << 8) | d[i + 8], (d[i + 5] << 8) | d[i + 6]
        i += 2 + ln
    raise AssertionError(name)


def test_there_are_baseline_and_progressive_fixtures():
    assert len([n for n in NAMES if n.startswith("prog_")]) >= 10
    assert len([n for n in NAMES if n.startswith("base_")]) >= 6


@pytest.mark.parametrize("name", NAMES)
def test_it_decodes_to_what_libjpeg_decodes(decoded, name):
    head, px = decoded[name]
    w, h = _dims(name)
    assert head == f"{w} {h} 0 {w * h * 4}", head
    ref = _reference(name)
    assert len(px) == len(ref)
    diffs = [abs(a - b) for a, b in zip(px, ref)]
    # Not byte-identical, and three rounding decisions account for all of
    # it: libjpeg's DCT is an integer approximation and this one is float,
    # its YCbCr conversion is fixed-point tables, and its chroma
    # upsampling is an integer triangle filter where this rounds a float
    # bilinear one. Each is off by at most one in a sample; together they
    # reach 3 at worst, on under 2% of samples. The DCT, the Huffman
    # decode and the progressive machinery must otherwise agree exactly,
    # and a fault in any of them is a large error, not a small one.
    assert max(diffs) <= 3, (name, max(diffs))
    off = sum(1 for d in diffs if d >= 2) / len(diffs)
    assert off < 0.02, (name, off)
    assert sum(1 for d in diffs if d == 0) / len(diffs) > 0.45, name


PAIRS = [(n, "prog_" + n[5:]) for n in NAMES
         if n.startswith("base_") and ("prog_" + n[5:]) in NAMES]


@pytest.mark.parametrize("base,prog", PAIRS)
def test_a_progressive_file_decodes_like_its_baseline_twin(decoded, base, prog):
    """The pairs are the same picture at the same quality and sampling, so
    their coefficients are the same; only the scan structure differs."""
    assert decoded[base][1] == decoded[prog][1]
