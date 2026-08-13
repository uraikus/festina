"""Lexical convention tests -- claude.md #9 (lexical conventions) and
#51 (reserved language features)."""
import pytest


# claude.md #51 -- names with defined language meanings.
RESERVED_WORDS = [
    "int", "float", "bool", "text", "blob", "arr", "struct", "table",
    "img", "aud", "null", "true", "false", "void", "func", "const",
    "import", "if", "else", "on", "fail", "log", "sqlite",
]


def token_types(lexer, source):
    return [t.type for t in lexer.tokenize(source)]


def token_values(lexer, source):
    return [t.value for t in lexer.tokenize(source)]


class TestStringLiterals:
    """claude.md #9: both single and double quoted strings are supported."""

    def test_single_quoted_string(self, lexer):
        tokens = lexer.tokenize("'hello'")
        assert any(t.value == "hello" for t in tokens)

    def test_double_quoted_string(self, lexer):
        tokens = lexer.tokenize('"hello"')
        assert any(t.value == "hello" for t in tokens)


class TestTemplateStrings:
    """claude.md #9: template strings support ${...} interpolation."""

    def test_template_string_is_tokenized(self, lexer):
        # Should not raise, and should produce more than just an opaque
        # string blob -- the interpolated expression `name` must be
        # independently lexed so the parser can build an expression from it.
        tokens = lexer.tokenize("`Hello ${name}`")
        values = [t.value for t in tokens if t.type == "IDENT"]
        assert "name" in values


class TestSemicolons:
    """claude.md #9: semicolons are optional."""

    def test_program_without_semicolons_tokenizes(self, lexer):
        source = "text name = 'Festina'\nlog(name)\n"
        # Must not raise.
        lexer.tokenize(source)

    def test_program_with_semicolons_tokenizes(self, lexer):
        source = "text name = 'Festina';\nlog(name);\n"
        lexer.tokenize(source)


class TestReservedWords:
    """claude.md #51: reserved words must lex as their own keyword-like
    token, not as a generic identifier a program could reassign."""

    @pytest.mark.parametrize("word", RESERVED_WORDS)
    def test_reserved_word_is_not_a_plain_identifier(self, lexer, word):
        tokens = lexer.tokenize(word)
        real = [t for t in tokens if t.type != "EOF"]
        assert len(real) == 1
        assert real[0].type != "IDENT", (
            f"{word!r} must be a reserved keyword token, not IDENT"
        )

    def test_reserved_words_are_exposed_for_reuse(self, lexer):
        # Other layers (e.g. "cannot use a reserved word as an identifier")
        # need to know the full reserved set.
        assert hasattr(lexer, "KEYWORDS")
        for word in RESERVED_WORDS:
            assert word in lexer.KEYWORDS


class TestSourceFileConvention:
    """claude.md #4: Festina source files use the .f extension."""

    def test_source_extension_constant_if_exposed(self, lexer):
        # Not all implementations need to expose this, but if the module
        # documents a canonical extension, it must match the spec.
        ext = getattr(lexer, "SOURCE_EXTENSION", ".f")
        assert ext == ".f"
