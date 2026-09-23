"""runtime.md phase 0: Festina-implemented runtime components, and the
mechanism that puts one into a program only when the program needs it.

[runtime.md](../runtime.md) plans to replace Cairo's drawing, libjpeg
and mpg123 with Festina source. The property that makes that worth
doing rather than merely possible is the one security.md already
promises for the C runtime: a program carries what it uses and nothing
else. A JPEG decoder compiled into a program with no `img` in it would
be a regression dressed as a feature.

The mechanism is deliberately not new machinery. `festina/imports.py`
already merges an imported file into one `ast.Program`, so a component
is injected exactly the way a written `import` would be -- which is why
this file tests the INJECTION and the component's own arithmetic, and
does not test the import system underneath it.

`RUNTIME_TRIGGERS` is empty until phase 1, so these tests supply their
own predicates. That is the whole reason `build_program` takes them as
a parameter: a mechanism with no consumer would otherwise be scaffolding
nobody could watch work.
"""
import os
import subprocess
import sys
import zlib

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from festina import imports as imports_mod   # noqa: E402


def _names(program):
    return {getattr(stmt, "name", None) for stmt in program.body}


class TestTheRegistry:
    """Pure-Python checks, so they cost nothing and run everywhere."""

    def test_every_registered_component_has_a_source_file(self):
        """A registered name with no file is a compile error waiting for
        whichever program first triggers it, which is the worst time to
        find out."""
        for name in imports_mod.RUNTIME_TRIGGERS:
            path = os.path.join(imports_mod.RUNTIME_COMPONENT_DIR, f"{name}.f")
            assert os.path.exists(path), (
                f"component {name!r} is registered but {path} does not exist")

    def test_the_registry_is_empty_until_something_triggers_one(self):
        """Phase 0 ships the mechanism, phase 1 ships the first consumer.

        This is not a placeholder: a component in the table with no
        predicate that can fire would be compiled into programs for no
        reason, and one with a predicate that always fires would be
        compiled into ALL of them. Either is the opposite of the point.
        Delete this test when phase 1 adds the PNG decoder -- and
        replace it with one asserting that decoder's trigger does not
        fire on a program with no `img`.
        """
        assert imports_mod.RUNTIME_TRIGGERS == {}, (
            "a component is registered now, so this test has outlived "
            "its purpose -- assert the new trigger's own both-directions "
            "behaviour instead of asserting the table is empty")


class TestTheInjection:
    """Both directions, because only one of them is the feature."""

    def _entry(self, tmp_path, source="log('hi')\n"):
        path = tmp_path / "main.f"
        path.write_text(source, encoding="utf-8")
        return str(path)

    def test_a_program_gets_nothing_it_did_not_trigger(self, tmp_path):
        program = imports_mod.build_program(
            self._entry(tmp_path), triggers={"checksums": lambda p: False})
        assert "adler32" not in _names(program)
        assert "crc32" not in _names(program)

    def test_a_triggered_component_is_merged_in(self, tmp_path):
        program = imports_mod.build_program(
            self._entry(tmp_path), triggers={"checksums": lambda p: True})
        assert "adler32" in _names(program)
        assert "crc32" in _names(program)

    def test_the_default_registry_injects_nothing(self, tmp_path):
        """The state every program compiles in today."""
        program = imports_mod.build_program(self._entry(tmp_path))
        assert _names(program) == {None}, (
            "something is being injected into every program by default")

    def test_a_component_goes_in_front_of_the_program(self, tmp_path):
        """specification.md 7.2: a global precedes its first use. A
        component that declares one and lands after the program reading
        it would not compile, so the order is part of the mechanism
        rather than an accident of how the lists were concatenated."""
        program = imports_mod.build_program(
            self._entry(tmp_path, "log('last')\n"),
            triggers={"checksums": lambda p: True})
        component_file = os.path.join(imports_mod.RUNTIME_COMPONENT_DIR,
                                       "checksums.f")
        assert program.body[0].file == component_file
        assert program.body[-1].file.endswith("main.f")

    def test_a_predicate_sees_the_users_parsed_program(self, tmp_path):
        """Triggers run before semantic analysis, so they can ask what
        the SOURCE says and not what codegen concluded. This pins that
        they are handed the user's program rather than an empty one."""
        seen = []
        imports_mod.build_program(
            self._entry(tmp_path, "log('marker')\n"),
            triggers={"checksums": lambda p: seen.append(len(p.body)) or False})
        assert seen == [1]

    def test_a_registered_component_with_no_file_is_a_clear_error(self, tmp_path):
        from festina.errors import CompileError
        with pytest.raises(CompileError, match="registered but"):
            imports_mod.build_program(
                self._entry(tmp_path), triggers={"nonexistent": lambda p: True})


class TestTheComponentItself:
    """checksums.f is real code with a real caller coming, so it is
    tested as code rather than as a fixture."""

    @pytest.mark.parametrize("payload", [
        b"", b"a", b"Hello", b"The quick brown fox jumps over the lazy dog",
        bytes(range(256)),
    ])
    def test_it_agrees_with_zlib(self, payload, tmp_path, cli_mod):
        """The differential shape runtime.md plans for every decoder,
        at the smallest scale it applies to: two implementations, one
        corpus, exact agreement demanded. zlib is the oracle because it
        is the implementation a PNG was written against."""
        component = tmp_path / "checksums.f"
        component.write_text(
            open(os.path.join(imports_mod.RUNTIME_COMPONENT_DIR,
                              "checksums.f"), encoding="utf-8").read(),
            encoding="utf-8")
        literal = ", ".join(str(b) for b in payload) if payload else ""
        src = tmp_path / "main.f"
        src.write_text(
            "import checksums.f\n\n"
            f"arr[int] d = [{literal}]\n"
            "log(adler32(d))\n"
            "log(crc32(d))\n",
            encoding="utf-8")
        out = tmp_path / "program"
        from tests.conftest import compile_file_or_skip, _require_c_compiler
        compile_file_or_skip(cli_mod, str(src), str(out),
                             cc=_require_c_compiler())
        result = subprocess.run([str(out)], cwd=tmp_path, capture_output=True,
                                 text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        got_adler, got_crc = result.stdout.split()
        assert int(got_adler) == zlib.adler32(payload)
        assert int(got_crc) == zlib.crc32(payload)


def _with_components(tmp_path, *names):
    """Copy runtime components into tmp_path so a test program can
    `import` them by name.

    Explicit imports rather than RUNTIME_TRIGGERS, because the registry
    is empty until a decoder has a builtin to hang off (phase 1 of
    runtime.md ships the decoder; wiring `img` to it is its own step).
    What these tests check is the component's arithmetic, which is the
    half that has to be right either way.
    """
    for name in names:
        src = os.path.join(imports_mod.RUNTIME_COMPONENT_DIR, f"{name}.f")
        (tmp_path / f"{name}.f").write_text(
            open(src, encoding="utf-8").read(), encoding="utf-8")


def _run_festina(tmp_path, cli_mod, source):
    from tests.conftest import compile_file_or_skip, _require_c_compiler
    src = tmp_path / "main.f"
    src.write_text(source, encoding="utf-8")
    out = tmp_path / "program"
    compile_file_or_skip(cli_mod, str(src), str(out), cc=_require_c_compiler())
    result = subprocess.run([str(out)], cwd=tmp_path, capture_output=True,
                             text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.strip()


#: A position-weighted sum, not a length. Two buffers of equal length
#: with two bytes transposed have the same sum under a plain total and
#: different ones under this, which is the failure a decoder actually
#: makes.
_CHECK = ("int i = 0\nint sum = 0\n"
          "while i < px.length { sum = sum + (px[i] * (i + 1)) i = i + 1 }\n")


def _weighted(data):
    return sum(b * (i + 1) for i, b in enumerate(data))


class TestInflate:
    """runtime.md phase 1: DEFLATE against zlib, which is the
    implementation every PNG in the world was compressed by."""

    @pytest.mark.parametrize("level", [0, 6, 9])
    @pytest.mark.parametrize("payload,label", [
        (b"", "empty"),
        (b"\x00" * 300, "a 300-zero run -- distance 1, overlapping copy"),
        (bytes(range(256)), "every byte value"),
        (b"The quick brown fox. " * 60, "repetitive text -- dynamic Huffman"),
    ])
    def test_it_agrees_with_zlib(self, payload, label, level, tmp_path, cli_mod):
        _with_components(tmp_path, "inflate")
        comp = zlib.compress(payload, level)
        literal = ", ".join(str(b) for b in comp)
        got = _run_festina(tmp_path, cli_mod,
                           "import inflate.f\n\n"
                           f"arr[int] z = [{literal}]\n"
                           "arr[int] px = inflateZlib(z)\n"
                           + _CHECK +
                           "log(`${px.length} ${sum}`)\n")
        assert got == f"{len(payload)} {_weighted(payload)}", label


class TestPngDecode:
    """The five colour types and the five filters, separately, because
    they fail independently -- a Paeth bug shows on one file in ten and
    a palette bug shows on every indexed file."""

    def _png(self, w, h, colour, pixels, palette=None, trns=None, filt=0):
        import struct

        def chunk(tag, data):
            return (struct.pack(">I", len(data)) + tag + data
                    + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))

        bpp = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[colour]
        raw = b""
        for y in range(h):
            raw += bytes([filt]) + pixels[y * w * bpp:(y + 1) * w * bpp]
        out = b"\x89PNG\r\n\x1a\n"
        out += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, colour, 0, 0, 0))
        if palette:
            out += chunk(b"PLTE", palette)
        if trns:
            out += chunk(b"tRNS", trns)
        out += chunk(b"IDAT", zlib.compress(raw, 6))
        out += chunk(b"IEND", b"")
        return out

    def _expected(self, w, h, colour, pixels, palette=None, trns=None):
        bpp = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[colour]
        res = []
        for y in range(h):
            for x in range(w):
                p = pixels[y * w * bpp + x * bpp:][:bpp]
                if colour == 0:
                    res += [p[0], p[0], p[0], 255]
                elif colour == 2:
                    res += [p[0], p[1], p[2], 255]
                elif colour == 3:
                    i = p[0]
                    res += list(palette[i * 3:i * 3 + 3])
                    res += [trns[i] if trns and i < len(trns) else 255]
                elif colour == 4:
                    res += [p[0], p[0], p[0], p[1]]
                else:
                    res += [p[0], p[1], p[2], p[3]]
        return res

    def _check(self, tmp_path, cli_mod, png, w, h, want):
        _with_components(tmp_path, "inflate", "png")
        literal = ", ".join(str(b) for b in png)
        got = _run_festina(tmp_path, cli_mod,
                           "import png.f\n\n"
                           f"arr[int] d = [{literal}]\n"
                           "arr[int] px = pngDecode(d)\n"
                           + _CHECK +
                           "log(`${PNG_W} ${PNG_H} ${sum} ${PNG_ERR}`)\n")
        assert got == f"{w} {h} {_weighted(want)} 0"

    @pytest.mark.parametrize("colour", [0, 2, 3, 4, 6])
    def test_every_colour_type(self, colour, tmp_path, cli_mod):
        import random
        random.seed(11)
        w, h = 7, 5
        bpp = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[colour]
        pix = bytes(random.getrandbits(8) for _ in range(w * h * bpp))
        pal = bytes(random.getrandbits(8) for _ in range(256 * 3)) if colour == 3 else None
        trns = bytes(random.getrandbits(8) for _ in range(120)) if colour == 3 else None
        self._check(tmp_path, cli_mod, self._png(w, h, colour, pix, pal, trns),
                    w, h, self._expected(w, h, colour, pix, pal, trns))

    @pytest.mark.parametrize("filt", [0, 1, 2, 3, 4])
    def test_every_filter_type(self, filt, tmp_path, cli_mod):
        """Each row encoded with the SAME filter, so undoing it is the
        only way the pixels come back. Adaptive encoders pick per row
        and would leave whichever filter is buggy untested on most
        files."""
        import random
        random.seed(23)
        w, h, bpp = 9, 6, 3
        pix = bytes(random.getrandbits(8) for _ in range(w * h * bpp))

        def paeth(a, b, c):
            p = a + b - c
            pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
            return a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)

        import struct

        def chunk(tag, data):
            return (struct.pack(">I", len(data)) + tag + data
                    + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))

        raw = b""
        prev = bytes(w * bpp)
        for y in range(h):
            row = pix[y * w * bpp:(y + 1) * w * bpp]
            enc = bytearray()
            for x in range(len(row)):
                a = row[x - bpp] if x >= bpp else 0
                b = prev[x]
                c = prev[x - bpp] if x >= bpp else 0
                if filt == 1:
                    enc.append((row[x] - a) & 255)
                elif filt == 2:
                    enc.append((row[x] - b) & 255)
                elif filt == 3:
                    enc.append((row[x] - ((a + b) // 2)) & 255)
                elif filt == 4:
                    enc.append((row[x] - paeth(a, b, c)) & 255)
                else:
                    enc.append(row[x])
            raw += bytes([filt]) + bytes(enc)
            prev = row
        png = (b"\x89PNG\r\n\x1a\n"
               + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
               + chunk(b"IDAT", zlib.compress(raw, 6))
               + chunk(b"IEND", b""))
        self._check(tmp_path, cli_mod, png, w, h,
                    self._expected(w, h, 2, pix))

    def test_an_unsupported_file_is_refused_rather_than_half_decoded(
            self, tmp_path, cli_mod):
        """16-bit and interlaced PNGs are not supported, and the
        decoder says so instead of producing plausible-looking wrong
        pixels. A caller can fall back; it cannot un-see a bad image."""
        import random
        import struct
        random.seed(31)

        def chunk(tag, data):
            return (struct.pack(">I", len(data)) + tag + data
                    + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))

        w, h = 4, 4
        raw = b"".join(bytes([0]) + bytes(w * 3) for _ in range(h))
        png = (b"\x89PNG\r\n\x1a\n"
               # interlace=1 (Adam7)
               + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 1))
               + chunk(b"IDAT", zlib.compress(raw, 6))
               + chunk(b"IEND", b""))
        _with_components(tmp_path, "inflate", "png")
        literal = ", ".join(str(b) for b in png)
        got = _run_festina(tmp_path, cli_mod,
                           "import png.f\n\n"
                           f"arr[int] d = [{literal}]\n"
                           "arr[int] px = pngDecode(d)\n"
                           "log(`${px.length} ${PNG_W} ${PNG_ERR}`)\n")
        assert got == "0 0 5", (
            "an interlaced PNG must be refused with a reason, not "
            "decoded as if it were progressive")
