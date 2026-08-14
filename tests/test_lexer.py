"""Lexical convention tests -- claude.md #9 (lexical conventions) and
#51 (reserved language features)."""
import pytest


# claude.md #51 -- names with defined language meanings.
RESERVED_WORDS = [
    "int", "float", "bool", "text", "blob", "arr", "struct", "table",
    "img", "aud", "null", "true", "false", "void", "func", "const",
    "import", "if", "else", "on", "fail", "log", "sqlite",
    "for", "while",  # claude.md #60, #61
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


class TestPostfixOperators:
    """claude.md #66: `++`/`--` must lex as one token each, not two
    single-char `+`/`-` OP tokens."""

    @pytest.mark.parametrize("op", ["++", "--"])
    def test_postfix_operator_is_one_token(self, lexer, op):
        tokens = [t for t in lexer.tokenize(f"i{op}") if t.type != "EOF"]
        assert [t.type for t in tokens] == ["IDENT", "OP"]
        assert tokens[1].value == op

    def test_adjacent_plus_operators_still_lex_separately(self, lexer):
        # `i + +1` (unary plus) must not be swallowed into a single `++`.
        tokens = [t for t in lexer.tokenize("i + +1") if t.type != "EOF"]
        assert [t.value for t in tokens] == ["i", "+", "+", 1]


class TestRegexLiterals:
    """claude.md #67: /pattern/flags -- the classic JS lexical ambiguity
    with the division operator, resolved by treating a leading '/' as a
    regex literal everywhere EXCEPT immediately after something that
    could itself end an expression (an identifier, a literal, `)`/`]`,
    postfix ++/--) -- see lexer.py's _regex_literal_may_start_here."""

    def test_regex_literal_is_a_single_token(self, lexer):
        tokens = [t for t in lexer.tokenize("/foo/") if t.type != "EOF"]
        assert [t.type for t in tokens] == ["REGEX"]
        assert tokens[0].value == ("foo", "")

    def test_regex_literal_captures_its_flags(self, lexer):
        tokens = lexer.tokenize(r"/\w+/gi")
        regex_tok = next(t for t in tokens if t.type == "REGEX")
        assert regex_tok.value == (r"\w+", "gi")

    def test_escaped_slash_in_pattern_is_unescaped(self, lexer):
        # \/ is JS's own delimiter-escape -- POSIX regcomp() never wants
        # '/' escaped at all, so this becomes a literal '/' in the
        # pattern text, not the two characters "\/".
        tokens = lexer.tokenize(r"/a\/b/")
        regex_tok = next(t for t in tokens if t.type == "REGEX")
        assert regex_tok.value == ("a/b", "")

    def test_other_backslash_sequences_pass_through_untouched(self, lexer):
        tokens = lexer.tokenize(r"/\d\s\./")
        regex_tok = next(t for t in tokens if t.type == "REGEX")
        assert regex_tok.value == (r"\d\s\.", "")

    @pytest.mark.parametrize("prefix, expected_types", [
        ("(", ["LPAREN", "REGEX"]),
        ("[", ["LBRACK", "REGEX"]),
        ("=", ["OP", "REGEX"]),
        ("==", ["OP", "REGEX"]),
        ("&&", ["OP", "REGEX"]),
        (",", ["OP", "REGEX"]),
        ("return ", ["return", "REGEX"]),
    ])
    def test_regex_literal_starts_after_an_expression_boundary(self, lexer, prefix, expected_types):
        tokens = [t for t in lexer.tokenize(f"{prefix}/x/") if t.type != "EOF"]
        assert [t.type for t in tokens] == expected_types

    @pytest.mark.parametrize("source, expected_types", [
        ("a / b", ["IDENT", "OP", "IDENT"]),
        ("5 / b", ["NUMBER", "OP", "IDENT"]),
        ("'x' / b", ["STRING", "OP", "IDENT"]),
        ("(a) / b", ["LPAREN", "IDENT", "RPAREN", "OP", "IDENT"]),
        ("a[0] / b", ["IDENT", "LBRACK", "NUMBER", "RBRACK", "OP", "IDENT"]),
        ("a++ / b", ["IDENT", "OP", "OP", "IDENT"]),
        ("true / b", ["true", "OP", "IDENT"]),
        ("a / b / c", ["IDENT", "OP", "IDENT", "OP", "IDENT"]),
    ])
    def test_a_slash_after_an_expression_boundary_is_division(self, lexer, source, expected_types):
        tokens = [t for t in lexer.tokenize(source) if t.type != "EOF"]
        assert [t.type for t in tokens] == expected_types

    def test_line_comment_still_wins_over_a_regex_literal(self, lexer):
        # '//' must never be mistaken for an (impossible, empty-pattern)
        # regex literal -- it's always a comment, same as real JS.
        tokens = [t for t in lexer.tokenize("x = // not a regex\n1") if t.type != "EOF"]
        assert [t.type for t in tokens] == ["IDENT", "OP", "NUMBER"]

    def test_block_comment_still_wins_over_a_regex_literal(self, lexer):
        tokens = [t for t in lexer.tokenize("x = /* not a regex */ 1") if t.type != "EOF"]
        assert [t.type for t in tokens] == ["IDENT", "OP", "NUMBER"]

    def test_unterminated_regex_falls_back_to_division(self, lexer):
        # '/' right after '=' is exactly where a regex literal is
        # normally allowed to start -- but with no closing '/' before
        # the newline, this isn't a valid one, so it must fall back to
        # lexing '/' as plain division instead of raising or misparsing.
        tokens = [t for t in lexer.tokenize("x = /foo\nbar") if t.type != "EOF"]
        assert [t.type for t in tokens] == ["IDENT", "OP", "OP", "IDENT", "IDENT"]

    def test_regex_literal_inside_template_interpolation(self, lexer):
        tokens = lexer.tokenize("`${/a+/.test(x)}`")
        assert any(t.type == "REGEX" and t.value == ("a+", "") for t in tokens)


class TestSourceFileConvention:
    """claude.md #4: Festina source files use the .f extension."""

    def test_source_extension_constant_if_exposed(self, lexer):
        # Not all implementations need to expose this, but if the module
        # documents a canonical extension, it must match the spec.
        ext = getattr(lexer, "SOURCE_EXTENSION", ".f")
        assert ext == ".f"
