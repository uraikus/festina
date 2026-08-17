"""claude.md #67 (regular expressions), #68 (string match and replace).

Lexer/parser/semantic-level tests only -- see tests/test_codegen.py for
the real compile-and-run end-to-end coverage of these same sections.
"""
import pytest


class TestRegexConstruction:
    """claude.md #67: a regex value can be built either way -- the
    JS-style /pattern/flags literal (TestRegexLiteral below) for a
    pattern known at compile time, or the regex() function (here) for
    one that isn't (built from a variable, a template, ...), mirroring
    JS's own split between a /pattern/ literal and `new RegExp(...)`."""

    def test_regex_call_parses(self, parser):
        parser.parse("regex pattern = regex('^[a-z]+$')")

    def test_regex_call_with_flags_parses(self, parser):
        parser.parse("regex pattern = regex('^[a-z]+$', 'i')")

    def test_regex_is_a_valid_type_name(self, parser, semantic):
        program = parser.parse("regex pattern = regex('a')")
        semantic.analyze(program)

    def test_regex_used_as_a_function_argument_type(self, parser, semantic):
        source = "void func check(p:regex) {\n    log(p.test('x'))\n}\ncheck(regex('a'))"
        program = parser.parse(source)
        semantic.analyze(program)

    def test_regex_call_still_works_for_a_dynamic_pattern(self, parser, semantic):
        # The one thing a literal genuinely can't express: a pattern
        # that isn't known until runtime.
        source = "text userPattern = '[0-9]+'\nregex p = regex(userPattern)"
        program = parser.parse(source)
        semantic.analyze(program)


class TestRegexLiteral:
    """claude.md #67: /pattern/flags -- a dedicated grammar construct
    (unlike the pre-existing regex() function), resolved from ordinary
    division via the same lexical rule real JS lexers use: a regex
    literal can only start where an expression is expected, never right
    after something that could itself end one (an identifier, a
    literal, `)`/`]`, postfix ++/--). See festina/lexer.py's
    _regex_literal_may_start_here for the exact rule and
    tests/test_lexer.py for lower-level tokenization coverage of the
    disambiguation itself."""

    def test_regex_literal_parses(self, parser):
        parser.parse("regex pattern = /^[a-z]+$/")

    def test_regex_literal_with_i_flag_parses(self, parser):
        parser.parse("regex pattern = /^[a-z]+$/i")

    def test_regex_literal_with_g_flag_parses(self, parser):
        # 'g' is accepted (real JS habit) even though it has no
        # additional effect here -- see the parser's own comment on
        # _SUPPORTED_REGEX_FLAGS.
        parser.parse("regex pattern = /[a-z]+/g")

    def test_regex_literal_with_combined_flags_parses(self, parser):
        parser.parse(r"regex pattern = /\w+/gi")

    def test_regex_literal_infers_regex_type(self, parser, semantic):
        program = parser.parse("regex pattern = /a/")
        semantic.analyze(program)

    def test_regex_literal_used_directly_as_a_call_argument(self, parser, semantic):
        source = "void func check(p:regex) {\n    log(p.test('x'))\n}\ncheck(/a/)"
        program = parser.parse(source)
        semantic.analyze(program)

    def test_regex_literal_test_method_works(self, parser, semantic):
        source = "bool matched = /[0-9]+/.test('room 42')"
        program = parser.parse(source)
        semantic.analyze(program)

    def test_regex_literal_as_replace_search_argument(self, parser, semantic):
        source = "text result = 'a1b2'.replace(/[0-9]/g, '-')"
        program = parser.parse(source)
        semantic.analyze(program)

    def test_unsupported_flag_is_a_compile_error(self, parser, errors):
        # 'x' isn't one of JS's own regex flags either -- picked simply
        # as an unambiguous "not g or i" letter.
        with pytest.raises(errors.CompileError, match="unsupported regex flag"):
            parser.parse("regex pattern = /a/x")

    def test_duplicate_flag_is_a_compile_error(self, parser, errors):
        with pytest.raises(errors.CompileError, match="duplicate regex flag"):
            parser.parse("regex pattern = /a/ii")

    def test_a_slash_after_an_identifier_is_still_division(self, parser):
        # The core disambiguation claim, exercised through the full
        # parser (not just the lexer -- see test_lexer.py for that):
        # this must produce a BinOp('/', ...), not attempt to parse a
        # regex literal starting at the second '/' and fail confusingly.
        program = parser.parse("int result = a / b / c")
        # Reaching here at all (no exception) is the assertion -- a
        # regex-literal misparse would raise well before this returns.
        assert program is not None

    def test_a_slash_after_a_call_result_is_still_division(self, parser):
        program = parser.parse("int result = f() / 2")
        assert program is not None


class TestRegexTest:
    """claude.md #67: pattern.test(value:text) -> bool."""

    def test_test_call_parses(self, parser):
        parser.parse("regex p = regex('a')\nlog(p.test('abc'))")

    def test_test_returns_bool(self, parser, semantic):
        source = "regex p = regex('a')\nbool matched = p.test('abc')"
        program = parser.parse(source)
        semantic.analyze(program)

    def test_test_wrong_argument_count_is_a_compile_error(self, parser, semantic, errors):
        source = "regex p = regex('a')\nlog(p.test())"
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="test"):
            semantic.analyze(program)

    def test_test_non_text_argument_is_a_compile_error(self, parser, semantic, errors):
        source = "regex p = regex('a')\nlog(p.test(5))"
        program = parser.parse(source)
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)


class TestStringMatch:
    """claude.md #68: value.match(pattern:regex) -> text (or null)."""

    def test_match_call_parses(self, parser):
        parser.parse("regex p = regex('[0-9]+')\ntext found = 'room 42'.match(p)")

    def test_match_returns_text(self, parser, semantic):
        source = "regex p = regex('[0-9]+')\ntext found = 'room 42'.match(p)"
        program = parser.parse(source)
        semantic.analyze(program)

    def test_match_wrong_argument_count_is_a_compile_error(self, parser, semantic, errors):
        source = "log('x'.match())"
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="match"):
            semantic.analyze(program)

    def test_match_argument_must_be_regex(self, parser, semantic, errors):
        source = "log('x'.match('not a regex'))"
        program = parser.parse(source)
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)

    def test_match_on_non_text_is_a_compile_error(self, parser, semantic, errors):
        source = "int x = 5\nlog(x.match(regex('a')))"
        program = parser.parse(source)
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)


class TestStringReplace:
    """claude.md #68 (#107): value.replace(search, replacement:text) ->
    text -- search may be text (literal) or regex. claude.md #107
    removed .replaceAll(); how many matches are replaced is the
    pattern's own 'g' flag now."""

    def test_replace_with_text_search_parses(self, parser, semantic):
        program = parser.parse("text result = 'a-b'.replace('-', '_')")
        semantic.analyze(program)

    def test_replace_with_regex_search_parses(self, parser, semantic):
        source = "regex p = regex('[0-9]')\ntext result = 'a1b2'.replace(p, '-')"
        program = parser.parse(source)
        semantic.analyze(program)

    def test_replace_all_is_a_compile_error_naming_the_g_flag(
            self, parser, semantic, errors):
        # claude.md #107: removed rather than aliased, and the error
        # names the replacement -- this is a breaking change and the
        # message is the only place a reader will find out why.
        program = parser.parse("text result = 'a-b'.replaceAll('-', '_')")
        with pytest.raises(errors.CompileError, match="replaceAll"):
            semantic.analyze(program)

    def test_the_replace_all_error_points_at_the_g_flag(
            self, parser, semantic, errors):
        program = parser.parse("text result = 'a-b'.replaceAll('-', '_')")
        with pytest.raises(errors.CompileError) as excinfo:
            semantic.analyze(program)
        assert "/search/g" in str(excinfo.value)

    def test_replace_wrong_argument_count_is_a_compile_error(self, parser, semantic, errors):
        source = "log('x'.replace('x'))"
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="replace"):
            semantic.analyze(program)

    def test_replace_search_must_be_text_or_regex(self, parser, semantic, errors):
        source = "log('x'.replace(5, 'y'))"
        program = parser.parse(source)
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)

    def test_replace_replacement_must_be_text(self, parser, semantic, errors):
        source = "log('x'.replace('x', 5))"
        program = parser.parse(source)
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)

    def test_replace_on_non_text_is_a_compile_error(self, parser, semantic, errors):
        source = "int x = 5\nlog(x.replace('a', 'b'))"
        program = parser.parse(source)
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)
