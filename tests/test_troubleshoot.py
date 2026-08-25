"""claude.md #158: troubleshoot(event, fields) / fail(message[, fields]).

Structured logging: one JSON line to stdout for troubleshoot(), and an
optional structured form of fail() (a JSON line to stderr instead of
the plain "fail: <message>" line, still followed by exit(1) either
way). Parser-level coverage isn't needed -- both are ordinary call
expressions, no new grammar -- so this file is semantic-error coverage
plus real compile-and-run coverage together.
"""
import pytest


class TestSemanticErrors:
    def test_troubleshoot_requires_exactly_two_arguments(self, parser, semantic, errors):
        program = parser.parse("troubleshoot('x')")
        with pytest.raises(errors.CompileError, match="expects 2 argument"):
            semantic.analyze(program)

    def test_fail_still_accepts_one_argument(self, parser, semantic):
        # claude.md #42's original 1-argument form is unchanged.
        program = parser.parse("fail('message')")
        semantic.analyze(program)

    def test_fail_rejects_three_arguments(self, parser, semantic, errors):
        program = parser.parse("fail('x', {'a': 'b'}, {'c': 'd'})")
        with pytest.raises(errors.CompileError, match="expects 1 or 2 argument"):
            semantic.analyze(program)

    def test_fields_must_be_map_text_not_some_other_map(self, parser, semantic, errors):
        program = parser.parse("map[int] m = {'a': 1}\ntroubleshoot('x', m)")
        with pytest.raises(errors.CompileError, match=r"map\[text\]"):
            semantic.analyze(program)

    def test_fields_literal_with_a_non_text_value_is_rejected(self, parser, semantic, errors):
        # claude.md #156's own MapLit bypass, mirrored here -- a literal
        # goes through its own entry-by-entry check, not generic infer().
        program = parser.parse("troubleshoot('x', {'a': 5})")
        with pytest.raises(errors.CompileError, match="expects text"):
            semantic.analyze(program)

    def test_fields_literal_with_a_non_text_key_is_rejected(self, parser, semantic):
        # A computed non-text key: claude.md #72's existing key-must-be-
        # text rule, exercised through this bypass path specifically.
        program = parser.parse("int k = 1\ntroubleshoot('x', {k: 'v'})")
        with pytest.raises(Exception):
            semantic.analyze(program)


class TestRuntimeBehavior:
    def test_troubleshoot_prints_one_structured_json_line(self, compile_and_run):
        source = """
        troubleshoot('user_login_failed', {'user_id': '7', 'reason': 'bad_password'})
        """
        result = compile_and_run(source)
        line = result.stdout.strip()
        assert line.startswith("{") and line.endswith("}")
        assert '"level":"info"' in line
        assert '"event":"user_login_failed"' in line
        assert '"user_id":"7"' in line
        assert '"reason":"bad_password"' in line
        assert '"timestamp":"' in line

    def test_troubleshoot_with_an_empty_fields_literal(self, compile_and_run):
        # claude.md #158: {} has no entries to infer a value type from
        # -- exercises the same expected-type threading claude.md #156's
        # amor map[T] literal needed, in a brand new position (a call
        # argument, not a var declaration).
        result = compile_and_run("troubleshoot('startup', {})")
        assert '"fields":{}' in result.stdout

    def test_troubleshoot_accepts_a_map_text_variable(self, compile_and_run):
        source = """
        map[text] tags = {'service': 'api', 'region': 'us-east'}
        troubleshoot('deploy_started', tags)
        log(tags['service'])
        """
        result = compile_and_run(source)
        lines = result.stdout.splitlines()
        assert '"event":"deploy_started"' in lines[0]
        assert '"service":"api"' in lines[0]
        # claude.md #158: troubleshoot() only READS the fields map --
        # tags must still be fully usable afterward, not consumed.
        assert lines[1] == "api"

    def test_fail_one_argument_form_is_unchanged(self, compile_and_run):
        result = compile_and_run("fail('old style still works')")
        assert result.returncode == 1
        assert result.stderr.strip() == "fail: old style still works"

    def test_fail_two_argument_form_is_structured_json(self, compile_and_run):
        result = compile_and_run(
            "fail('db connection lost', {'host': 'db1', 'retry': 'no'})")
        assert result.returncode == 1
        line = result.stderr.strip()
        assert line.startswith("{") and line.endswith("}")
        assert '"level":"error"' in line
        assert '"message":"db connection lost"' in line
        assert '"host":"db1"' in line
        assert '"retry":"no"' in line

    def test_troubleshoot_event_accepts_any_type_coerced_to_text(self, compile_and_run):
        # Matches log()/fail()'s own implicit-toText convention -- the
        # event key is always a JSON STRING, even for a non-text
        # argument (42 renders as "42", not the bare number 42), since
        # event is semantically a text label, not a numeric field.
        result = compile_and_run("troubleshoot(42, {})")
        assert '"event":"42"' in result.stdout

    def test_troubleshoot_in_a_loop_leaks_nothing_observable(self, compile_and_run):
        # Not a Valgrind run (this suite doesn't do that here -- see
        # claude.md #158's own account of the real use-after-free this
        # exact shape caught during development, and scripts/
        # leak_stress.sh for the project's actual Valgrind coverage),
        # but pins that a repeated, referenced (never consumed) map[text]
        # value stays fully correct and readable after many calls.
        source = """
        map[text] tags = {'n': '0'}
        int i = 0
        while (i < 50) {
            i = i + 1
            tags['n'] = `${i}`
            troubleshoot('tick', tags)
        }
        log(tags['n'])
        """
        result = compile_and_run(source)
        lines = result.stdout.splitlines()
        assert len(lines) == 51
        assert lines[-1] == "50"
        assert '"n":"1"' in lines[0]
        assert '"n":"50"' in lines[49]
