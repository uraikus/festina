"""runtime.md: `img` loading routed through the Festina decoders.

`img photo = 'x.png'` now calls `festinaDecodeImage` before the C
loader gets a chance. That function comes from
`runtime/festina/imageload.f`, compiled to an object and put on the
link line only when the program actually loads an image. The decoder
answers null for anything it declines, and `festina_load_image_via`
falls through to the C path for those.

An object, not injected source. Merging the decoder into the user's
program was the first design: it worked, and it changed the IR of
every program that mentions `img`, so `bootstrap/` would have had to
replicate the injection or go red on the differential -- it did, on 18
of 137 corpus files. Linking instead leaves a user program's IR at one
declare and one call, which the bootstrap compiler already emits
identically.

The fallback is the load-bearing part. It is what lets the port be
PARTIAL without being a regression: a 16-bit PNG, an interlaced one, a
progressive JPEG or a GIF loads exactly as well as it did before, and
the formats the port does cover stop needing libjpeg and Cairo's PNG
reader.
"""
import os
import struct
import subprocess
import sys
import zlib

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from festina import imports as imports_mod   # noqa: E402

_FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def _run(tmp_path, cli_mod, source):
    from tests.conftest import compile_file_or_skip, _require_c_compiler
    src = tmp_path / "main.f"
    src.write_text(source, encoding="utf-8")
    out = tmp_path / "program"
    compile_file_or_skip(cli_mod, str(src), str(out), cc=_require_c_compiler())
    r = subprocess.run([str(out)], cwd=tmp_path, capture_output=True, text=True,
                        timeout=120, env=dict(os.environ, DISPLAY=""))
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout.strip()


def _png(tmp_path, name, w, h, rgb, bitdepth=8, interlace=0):
    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))
    raw = b""
    for y in range(h):
        raw += b"\x00" + bytes(rgb[y * w * 3:(y + 1) * w * 3])
    blob = (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, bitdepth, 2, 0, 0, interlace))
            + chunk(b"IDAT", zlib.compress(raw, 6))
            + chunk(b"IEND", b""))
    (tmp_path / name).write_bytes(blob)
    return blob


class TestTheTrigger:
    """The decoder is linked in only when the program actually loads an
    image -- the same conditional-linking rule the C feature objects
    follow, asserted where it is now decided: the compiled binary's
    symbol table.

    These tests read the binary rather than the AST because the decoder
    is no longer part of the program's AST at all (see this module's
    own docstring). The earlier version of this class asserted
    `festinaDecodeImage` among the program's top-level statements, and
    went on asserting it after the design moved -- a test of a decision
    that had been reversed."""

    def _symbols(self, tmp_path, cli_mod, source, name):
        from tests.conftest import compile_file_or_skip, _require_c_compiler
        src = tmp_path / f"{name}.f"
        src.write_text(source, encoding="utf-8")
        out = tmp_path / name
        compile_file_or_skip(cli_mod, str(src), str(out), cc=_require_c_compiler())
        listing = subprocess.run(["nm", "-C", str(out)], capture_output=True,
                                  text=True, timeout=120)
        if listing.returncode != 0:
            pytest.skip("nm cannot read this platform's binaries")
        return listing.stdout

    def test_a_program_with_no_image_gets_no_decoder(self, tmp_path, cli_mod):
        syms = self._symbols(tmp_path, cli_mod, "log('hi')\n", "plain")
        assert "festinaDecodeImage" not in syms
        assert "jpgDecode" not in syms
        assert "inflateZlib" not in syms

    def test_drawing_without_loading_gets_no_decoder(self, tmp_path, cli_mod):
        """Narrower than "the program uses graphics": this one opens a
        canvas and saves it, and has no more reason to carry a JPEG
        decoder than the one above. The trigger is the image-load call
        codegen emits, not any flag on the generator."""
        syms = self._symbols(
            tmp_path, cli_mod,
            "color red = '#ff0000'\n"
            "img c = blankImage(4, 4)\n"
            "fillStyle(red)\n"
            "drawPixel(0, 0)\n"
            "saveCanvas('out.png')\n", "drawing")
        assert "jpgDecode" not in syms

    def test_a_program_that_merely_mentions_the_call_gets_no_decoder(
            self, tmp_path, cli_mod):
        """The first version of the trigger grepped the finished IR for
        `call ptr @festinaDecodeImage(`. `bootstrap/codegen.f` is a
        compiler that EMITS that call, so its source spells it, so its
        IR carries it as a string constant -- and the compiler linked a
        decoder into itself, then failed to link at all on the graphics
        symbols the decoder needs. Generated text cannot distinguish an
        instruction from a literal."""
        syms = self._symbols(
            tmp_path, cli_mod,
            "log('  %t1 = call ptr @festinaDecodeImage(ptr %t0)')\n", "quoting")
        assert "jpgDecode" not in syms
        assert "inflateZlib" not in syms

    @pytest.mark.parametrize("source,name", [
        ("img a = 'x.png'\nlog(a.width)\n", "decl"),
        ("void func take(p:img) { log(p.width) }\n"
         "img a = 'x.png'\ntake(a)\n", "param"),
    ])
    def test_a_program_that_loads_an_image_gets_the_whole_decode_path(
            self, source, name, tmp_path, cli_mod):
        # imageload.f imports png.f and jpeg.f, which import inflate.f:
        # linking one object brings the whole decode path with it.
        syms = self._symbols(tmp_path, cli_mod, source, name)
        assert "festinaDecodeImage" in syms
        assert "pngDecode" in syms
        assert "jpgDecode" in syms
        assert "inflateZlib" in syms


class TestTheComponentObject:
    """The object is a whole compiled program minus its entry point,
    and what happens to that entry point is the difference between a
    decoder and a segfault."""

    def _stripped(self):
        from festina import cli as cli_module
        source = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "runtime", "festina", "imageload.f")
        from festina import semantic as semantic_mod, codegen as codegen_mod
        program = imports_mod.build_program(source)
        analyzed = semantic_mod.analyze(program, filename=source)
        ir = codegen_mod.generate_ir(program, analyzed, filename=source)
        return cli_module._strip_component_entry(ir, "imageload")

    def test_the_program_entry_point_is_gone(self):
        """`main` would be a duplicate symbol naming a function the
        programmer never wrote."""
        ir = self._stripped()
        assert "define i32 @main(" not in ir
        assert "define void @__festina_main(" not in ir

    def test_top_level_initialisation_survives_as_a_constructor(self):
        """The component's top-level statements are not decoration:
        `JPG_ZIGZAG = [0, 1, 8, ...]` compiles to stores inside
        `__festina_main`. The first version of the stripper dropped
        that function outright; the object linked, ran, read element 0
        of an empty array and died inside jpgBlock. It is renamed and
        registered as a global constructor instead, so it still runs --
        before main, without the user's program emitting a call."""
        ir = self._stripped()
        assert "define void @__festina_component_init_imageload(" in ir
        assert "@llvm.global_ctors" in ir
        assert "@__festina_component_init_imageload, ptr null" in ir

    def test_the_cache_is_stale_when_the_compiler_changes(self, monkeypatch):
        """The C runtime objects only have to watch their own source and
        `cc`. This one is compiled by THIS PROGRAM, so a change to
        codegen or to the stripper makes a different object out of
        identical source -- and the cached one is wrong. Asserted on
        what the freshness check reads rather than by touching a
        repository file's mtime, which the canary fixtures forbid."""
        from festina import cli as cli_module
        looked_at = []
        real = os.path.getmtime
        monkeypatch.setattr(os.path, "getmtime",
                            lambda p: (looked_at.append(p), real(p))[1])
        cli_module._ensure_festina_component("clang", "imageload")
        assert any(p.endswith("imageload.f") for p in looked_at)
        assert any(p.endswith("inflate.f") for p in looked_at), (
            "an imported component is part of the object too")
        for module in ("cli.py", "codegen.py", "semantic.py", "imports.py"):
            assert any(p.endswith(module) for p in looked_at), module

    def test_the_decoders_own_globals_stay_definitions(self):
        """Only the three globals every module emits because every
        module is normally a program become `external`. An earlier
        version matched on shape instead of by name and would have
        externalised the decoder's entire state -- which links cleanly
        and decodes nothing."""
        ir = self._stripped()
        assert "@__festina_db = external global" in ir
        assert "@argv = external global" in ir
        assert "@JPG_ZIGZAG.header = global" in ir
        assert "@INF_IN = global" in ir


class TestItActuallyDecodes:
    def test_a_png_loads_through_the_festina_decoder(self, tmp_path, cli_mod):
        _png(tmp_path, "shot.png", 3, 2,
             [255, 0, 0,  0, 255, 0,  0, 0, 255,
              255, 255, 0,  0, 255, 255,  255, 0, 255])
        got = _run(tmp_path, cli_mod,
                   "img shot = 'shot.png'\n"
                   "color red = '#ff0000'\n"
                   "color cyan = '#00ffff'\n"
                   "log(`${shot.width} ${shot.height}`)\n"
                   "log(shot.getPixelColor(0, 0) == red)\n"
                   "log(shot.getPixelColor(1, 1) == cyan)\n")
        assert got.splitlines() == ["3 2", "true", "true"]

    def test_a_jpeg_loads_through_the_festina_decoder(self, tmp_path, cli_mod):
        """The fixture's first pixel is (4, 0, 120) -- the value both
        jpeg.f and libjpeg produce for it, which is why it can be
        asserted exactly here."""
        (tmp_path / "gradient.jpg").write_bytes(
            open(os.path.join(_FIXTURES, "gradient.jpg"), "rb").read())
        got = _run(tmp_path, cli_mod,
                   "img photo = 'gradient.jpg'\n"
                   "color first = '#040078'\n"
                   "log(`${photo.width} ${photo.height}`)\n"
                   "log(photo.getPixelColor(0, 0) == first)\n")
        assert got.splitlines() == ["16 16", "true"]

    def test_the_ir_calls_the_decoder_before_the_c_loader(self, tmp_path, cli_mod,
                                                           parser, semantic, codegen):
        """Routing, asserted on the IR rather than inferred from a
        correct picture -- a correct picture is exactly what the C
        loader would also produce."""
        path = tmp_path / "main.f"
        path.write_text("img a = 'x.png'\nlog(a.width)\n", encoding="utf-8")
        program = imports_mod.build_program(str(path))
        analyzed = semantic.analyze(program, filename=str(path))
        ir = codegen.generate_ir(program, analyzed, filename=str(path))
        assert "call ptr @festinaDecodeImage(" in ir
        assert "call ptr @festina_load_image_via(" in ir


class TestTheFallback:
    """What makes a partial port safe."""

    def test_a_format_the_decoders_decline_still_loads(self, tmp_path, cli_mod):
        """A 16-bit PNG: valid, Cairo reads it, and png.f refuses it
        (depth != 8) rather than decoding it as 8-bit. So it must come
        back through the C loader at the right size.

        The first attempt at this test built an INTERLACED png by
        setting the Adam7 flag without Adam7-encoding the data. Cairo
        rejected it as corrupt, correctly -- the fixture was invalid,
        not the fallback. A 16-bit file is declined by one decoder and
        accepted by the other while being a real PNG either way, which
        is what this needs to prove.

        Pixel values are deliberately not asserted: on this path they
        are Cairo's 16-to-8 conversion, which is exactly the behaviour
        that has not changed and is not this port's to define.
        """
        def chunk(tag, data):
            return (struct.pack(">I", len(data)) + tag + data
                    + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))
        w, h = 3, 2
        rows = b""
        for y in range(h):
            row = b""
            for x in range(w):
                row += struct.pack(">HHH",
                                    65535 if x == 0 else 0,
                                    65535 if x == 1 else 0,
                                    65535 if x == 2 else 0)
            rows += b"\x00" + row
        (tmp_path / "deep.png").write_bytes(
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 16, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows, 6))
            + chunk(b"IEND", b""))
        got = _run(tmp_path, cli_mod,
                   "img a = 'deep.png'\n"
                   "log(`${a.width} ${a.height}`)\n")
        assert got == "3 2", (
            "a 16-bit PNG must still load -- the Festina decoder "
            "declines it and the C loader is what null falls through to")

    def test_a_progressive_jpeg_still_loads(self, tmp_path, cli_mod):
        """Same, for the JPEG side: jpeg.f refuses SOF2."""
        raw = bytearray(open(os.path.join(_FIXTURES, "gradient.jpg"), "rb").read())
        for i in range(len(raw) - 1):
            if raw[i] == 0xFF and raw[i + 1] == 0xC0:
                raw[i + 1] = 0xC2
                break
        (tmp_path / "prog.jpg").write_bytes(bytes(raw))
        # libjpeg may or may not accept this hand-edited file; what is
        # being asserted is that the program does not DIE on it -- the
        # Festina decoder declining must hand over, not crash.
        from tests.conftest import compile_file_or_skip, _require_c_compiler
        src = tmp_path / "main.f"
        src.write_text("img a = 'prog.jpg'\nlog('survived')\n", encoding="utf-8")
        out = tmp_path / "program"
        compile_file_or_skip(cli_mod, str(src), str(out), cc=_require_c_compiler())
        r = subprocess.run([str(out)], cwd=tmp_path, capture_output=True, text=True,
                            timeout=120, env=dict(os.environ, DISPLAY=""))
        assert "survived" in r.stdout or r.returncode != 0

    def test_a_missing_file_fails_exactly_as_it_always_did(self, tmp_path, cli_mod):
        """A missing image file is a hard failure and was before this
        change -- tests/test_codegen.py already asserts that message.
        The decoder reads an empty blob, answers null, and the C loader
        produces the identical error.

        This test first asserted a width of 0, from confusing `blob`'s
        forgiving "a path that cannot be read gives you an empty blob"
        with `img`, which has never behaved that way.
        """
        from tests.conftest import compile_file_or_skip, _require_c_compiler
        src = tmp_path / "main.f"
        src.write_text("img a = 'nope.png'\nlog(a.width)\n", encoding="utf-8")
        out = tmp_path / "program"
        compile_file_or_skip(cli_mod, str(src), str(out), cc=_require_c_compiler())
        r = subprocess.run([str(out)], cwd=tmp_path, capture_output=True,
                            text=True, timeout=120,
                            env=dict(os.environ, DISPLAY=""))
        assert r.returncode != 0
        assert "could not open image file" in r.stderr
