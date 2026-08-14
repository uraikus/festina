"""claude.md #67 (regular expressions), #68 (string match and replace).

Lexer/parser/semantic-level tests only -- see tests/test_codegen.py for
the real compile-and-run end-to-end coverage of these same sections.
"""
import pytest


class TestRegexConstruction:
    """claude.md #67: regex() is a global function (like sqlite(),
    loadImage()), not a dedicated /pattern/ literal syntax."""

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
    """claude.md #68: value.replace(search, replacement:text) -> text,
    value.replaceAll(search, replacement:text) -> text -- search may be
    text (literal) or regex."""

    @pytest.mark.parametrize("method", ["replace", "replaceAll"])
    def test_replace_with_text_search_parses(self, parser, semantic, method):
        program = parser.parse(f"text result = 'a-b'.{method}('-', '_')")
        semantic.analyze(program)

    @pytest.mark.parametrize("method", ["replace", "replaceAll"])
    def test_replace_with_regex_search_parses(self, parser, semantic, method):
        source = f"regex p = regex('[0-9]')\ntext result = 'a1b2'.{method}(p, '-')"
        program = parser.parse(source)
        semantic.analyze(program)

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
