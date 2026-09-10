"""claude.md #272: text.trim(), blob.byteAt()/blob.slice(), and the
rejected \\0 escape.

All three came out of bootstrap/lexer.f (claude.md #271) hitting real
limits of the language while being written in it -- a port of
festina/lexer.py that could not read 3 of this repository's own .f files,
could not hold a value its own lexer produced, and had to carry a
hand-written trim().
"""
import pytest


class TestTextTrim:
    """text.trim() -- leading and trailing ASCII whitespace removed."""

    def test_trims_both_ends(self, compile_and_run):
        result = compile_and_run("log('[' + '  hello  '.trim() + ']')")
        assert result.stdout == "[hello]\n"

    def test_trims_every_ascii_whitespace_character(self, compile_and_run):
        # space, tab, newline, carriage return -- the set C's isspace()
        # answers in the "C" locale, which is what the runtime uses.
        result = compile_and_run("log('[' + '\\t\\n\\r mixed \\r\\n\\t'.trim() + ']')")
        assert result.stdout == "[mixed]\n"

    def test_leaves_a_clean_string_alone(self, compile_and_run):
        result = compile_and_run("log('[' + 'none'.trim() + ']')")
        assert result.stdout == "[none]\n"

    def test_all_whitespace_becomes_empty(self, compile_and_run):
        result = compile_and_run("log('[' + '   '.trim() + ']')\nlog('[' + ''.trim() + ']')")
        assert result.stdout == "[]\n[]\n"

    def test_interior_whitespace_is_untouched(self, compile_and_run):
        result = compile_and_run("log('[' + '  a  b  '.trim() + ']')")
        assert result.stdout == "[a  b]\n"

    def test_multibyte_characters_survive(self, compile_and_run):
        # Byte-wise trimming is only safe on UTF-8 because every byte of
        # a multi-byte sequence has its high bit set and so can never be
        # mistaken for one of the seven ASCII whitespace bytes. This is
        # that guarantee, pinned.
        result = compile_and_run("log('[' + '  café ☃  '.trim() + ']')")
        assert result.stdout == "[café ☃]\n"

    def test_does_not_mutate_the_receiver(self, compile_and_run):
        result = compile_and_run(
            "text t = '  var  '\n"
            "log('[' + t.trim() + ']')\n"
            "log(t.length)")
        assert result.stdout == "[var]\n7\n"

    def test_trim_takes_no_arguments(self, parser, semantic, errors):
        program = parser.parse("log('x'.trim(1))", filename="main.f")
        with pytest.raises(errors.CompileError) as excinfo:
            semantic.analyze(program, filename="main.f")
        assert "trim() takes no arguments" in str(excinfo.value)


class TestBlobByteAt:
    """blob.byteAt(i) -- an O(1) raw byte, null out of range."""

    def _with_file(self, tmp_path, content):
        path = tmp_path / "data.bin"
        path.write_bytes(content)
        return str(path)

    def test_reads_ascii_bytes(self, compile_and_run, tmp_path):
        self._with_file(tmp_path, b"abc")
        result = compile_and_run(
            "blob f = 'data.bin'\n"
            "log(`${f.byteAt(0)} ${f.byteAt(1)} ${f.byteAt(2)}`)")
        assert result.stdout == "97 98 99\n"

    def test_reads_high_bytes_as_unsigned(self, compile_and_run, tmp_path):
        # plain char is signed on x86, so without the cast through
        # unsigned char 0xC3 would come back as -61 instead of 195. This
        # is the whole point of a BYTE read.
        self._with_file(tmp_path, "café".encode("utf-8"))
        result = compile_and_run(
            "blob f = 'data.bin'\n"
            "log(`${f.byteAt(3)} ${f.byteAt(4)}`)")
        assert result.stdout == "195 169\n"

    def test_out_of_range_answers_null(self, compile_and_run, tmp_path):
        self._with_file(tmp_path, b"ab")
        result = compile_and_run(
            "blob f = 'data.bin'\n"
            "if f.byteAt(-1) == null { log('negative -> null') }\n"
            "if f.byteAt(2) == null { log('past end -> null') }\n"
            "if f.byteAt(9999) == null { log('far past end -> null') }")
        assert result.stdout == (
            "negative -> null\npast end -> null\nfar past end -> null\n")

    def test_a_missing_file_answers_null_rather_than_failing(self, compile_and_run):
        # The same "nothing here fails the program" rule the rest of
        # blob follows -- an unreadable path is an empty blob.
        result = compile_and_run(
            "blob f = 'no_such_file_at_all.bin'\n"
            "if f.byteAt(0) == null { log('empty blob -> null') }")
        assert result.stdout == "empty blob -> null\n"

    def test_byte_at_wants_exactly_one_int(self, parser, semantic, errors):
        program = parser.parse(
            "blob f = 'x'\nlog(f.byteAt('nope'))", filename="main.f")
        with pytest.raises(errors.CompileError):
            semantic.analyze(program, filename="main.f")


class TestBlobSlice:
    """blob.slice(start, end) -- the half-open byte range, as text."""

    def _with_file(self, tmp_path, content):
        (tmp_path / "data.bin").write_bytes(content)

    def test_slices_a_byte_range(self, compile_and_run, tmp_path):
        self._with_file(tmp_path, b"hello world")
        result = compile_and_run(
            "blob f = 'data.bin'\nlog('[' + f.slice(0, 5) + ']')")
        assert result.stdout == "[hello]\n"

    def test_carries_multibyte_sequences_through_whole(self, compile_and_run, tmp_path):
        # This is what the lexer needs: a string literal's bytes copied
        # across untouched, without the type system insisting they be
        # ASCII first.
        self._with_file(tmp_path, "x=café".encode("utf-8"))
        result = compile_and_run(
            "blob f = 'data.bin'\nlog('[' + f.slice(2, 8) + ']')")
        assert result.stdout == "[café]\n"

    def test_range_is_clamped_not_checked(self, compile_and_run, tmp_path):
        # splice()'s rule, not arr[T] indexing's -- a blob's length is
        # not something the program chose.
        self._with_file(tmp_path, b"abc")
        result = compile_and_run(
            "blob f = 'data.bin'\n"
            "log('[' + f.slice(-10, 999) + ']')\n"
            "log('[' + f.slice(2, 1) + ']')\n"
            "log('[' + f.slice(99, 100) + ']')")
        assert result.stdout == "[abc]\n[]\n[]\n"

    def test_slice_of_a_missing_file_is_empty(self, compile_and_run):
        result = compile_and_run(
            "blob f = 'no_such_file_at_all.bin'\nlog('[' + f.slice(0, 5) + ']')")
        assert result.stdout == "[]\n"

    def test_slice_wants_two_arguments(self, parser, semantic, errors):
        program = parser.parse("blob f = 'x'\nlog(f.slice(0))", filename="main.f")
        with pytest.raises(errors.CompileError) as excinfo:
            semantic.analyze(program, filename="main.f")
        assert "slice() expects 2 arguments" in str(excinfo.value)

    def test_scanning_a_whole_file_by_byte(self, compile_and_run, tmp_path):
        """The shape bootstrap/lexer.f actually uses: byteAt in the loop
        condition, slice to extract, over a file that is NOT pure ASCII
        -- the exact case text.toAscii() refuses."""
        (tmp_path / "src.txt").write_text("aé b\tc", encoding="utf-8")
        result = compile_and_run(
            "blob f = 'src.txt'\n"
            "int i = 0\n"
            "int words = 0\n"
            "while i < f.length {\n"
            "    int c = f.byteAt(i)\n"
            "    if c == 32 || c == 9 { i++ }\n"
            "    else {\n"
            "        words++\n"
            "        while i < f.length {\n"
            "            int d = f.byteAt(i)\n"
            "            if d == 32 || d == 9 { break }\n"
            "            i++\n"
            "        }\n"
            "    }\n"
            "}\n"
            "log(`words=${words} bytes=${f.length}`)")
        # "aé b\tc" is 7 bytes (é is two) and three whitespace-separated
        # words -- a file text.toAscii() answers null for.
        assert result.stdout == "words=3 bytes=7\n"


class TestNulEscapeIsRejected:
    """claude.md #272: `\\0` used to lex to a real NUL, and text is
    NUL-terminated -- so 'a\\0b'.length answered 1 and everything past the
    NUL silently vanished. Accepting an escape whose value the language
    cannot represent is worse than rejecting it."""

    def test_a_nul_escape_in_a_string_is_a_compile_error(self, parser, errors):
        with pytest.raises(errors.CompileError) as excinfo:
            parser.parse("text bad = 'a\\0b'", filename="main.f")
        assert "\\0 escape is not supported" in str(excinfo.value)
        assert "NUL-terminated" in str(excinfo.value)

    def test_a_nul_escape_in_a_template_is_a_compile_error(self, parser, errors):
        with pytest.raises(errors.CompileError):
            parser.parse("text bad = `x\\0y`", filename="main.f")

    def test_an_escaped_backslash_before_a_zero_still_works(self, compile_and_run):
        # '\\0' is an escaped backslash followed by an ordinary '0' --
        # four characters, no NUL anywhere, and untouched by the check.
        result = compile_and_run("text ok = 'a\\\\0b'\nlog(ok)\nlog(ok.length)")
        assert result.stdout == "a\\0b\n4\n"

    def test_other_escapes_are_unaffected(self, compile_and_run):
        result = compile_and_run("log('a\\tb')\nlog('c\\nd')\nlog('e\\qf')")
        assert result.stdout == "a\tb\nc\nd\neqf\n"
