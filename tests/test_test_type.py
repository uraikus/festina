"""specification.md 11.7: the `test` type and `festina test`.

A named group of assertions, declared `test NAME = 'description'` and
asserted by calling the binding. Written against the clause before any
of it existed, which is the order claude.md #278 requires for a
language addition.

The parts worth pinning, and why each is here rather than assumed:

- **`test` is contextual, not reserved.** `bootstrap/semantic.f`
  declares a parameter called `test` and `regex.test(s)` is an existing
  method (16.3). Reserving the word would break both, so it is
  recognised only as the first token of a `test NAME =` statement --
  the same treatment `weak` gets (claude.md #332). A test that only
  checked the new syntax would not notice the day that stopped being
  true.
- **An assertion's argument types are restricted, deliberately.** A
  struct compares by IDENTITY (8.9.1), so an assertion over two
  separately-built structs would fail while plainly meaning to pass.
  11.7.1 rejects the call instead of quietly giving `test` a second
  meaning of equality.
- **The report's exact shape.** Truncated percentages, an omitted fail
  count for a clean group, the assertion's own source text, and the
  exit code. Each is the kind of detail a reimplementation gets subtly
  wrong, and the bootstrap port is a reimplementation.
"""
import os
import re

import pytest


class TestTheDeclarationParses:
    def test_a_test_declaration_is_accepted(self, parser, semantic):
        program = parser.parse("test basicMath = 'basic math test'")
        semantic.analyze(program)

    def test_the_description_must_be_text(self, parser, semantic, errors):
        program = parser.parse("test basicMath = 42")
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)

    def test_the_description_may_be_any_text_expression(self, parser, semantic):
        source = "text what = 'math'\ntest basicMath = `basic ${what}`"
        program = parser.parse(source)
        semantic.analyze(program)


class TestTestIsContextualNotReserved:
    """The rule that keeps this addition from breaking the repository
    it lands in."""

    def test_a_variable_may_still_be_called_test(self, parser, semantic):
        program = parser.parse("int test = 5\nlog(`${test}`)")
        semantic.analyze(program)

    def test_a_parameter_may_still_be_called_test(self, parser, semantic):
        # bootstrap/semantic.f's own checkCondition(s, stmt, test) --
        # reserving the word would stop the bootstrap compiler
        # compiling, which the differential harness would report as the
        # whole corpus breaking.
        source = """
        int func f(test:int) {
            return test + 1
        }
        log(`${f(1)}`)
        """
        program = parser.parse(source)
        semantic.analyze(program)

    def test_a_regex_test_method_still_parses(self, parser, semantic):
        # 16.3's own `.test()`. Members are exempt from reservation
        # anyway (Parser.eat_name), but this is the specific call the
        # examples and the corpus use.
        program = parser.parse("regex d = /[0-9]+/\nlog(`${d.test('a1')}`)")
        semantic.analyze(program)

    def test_a_struct_field_may_still_be_called_test(self, parser, semantic):
        source = """
        struct Holder {
            test:int
        }
        Holder h
        h.test = 1
        log(`${h.test}`)
        """
        program = parser.parse(source)
        semantic.analyze(program)


class TestTheBindingIsAnOrdinaryValueName:
    def test_it_collides_with_another_value_name(self, parser, semantic, errors):
        program = parser.parse("int basicMath = 1\ntest basicMath = 'x'")
        with pytest.raises(errors.CompileError, match="already declared"):
            semantic.analyze(program)

    def test_it_may_not_take_a_builtin_name(self, parser, semantic, errors):
        # 6.7, the rule claude.md #339 finished enforcing.
        program = parser.parse("test Math = 'x'")
        with pytest.raises(errors.CompileError, match="built-in math namespace"):
            semantic.analyze(program)

    def test_an_assertion_before_the_declaration_is_an_error(self, parser, semantic, errors):
        # Not a special rule -- a global must precede its first use, and
        # nothing about `test` changes that.
        source = "basicMath(1, 1)\ntest basicMath = 'x'"
        program = parser.parse(source)
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)


class TestAssertionArguments:
    def test_two_ints_are_accepted(self, parser, semantic):
        program = parser.parse("test t = 'x'\nt(2 + 2, 4)")
        semantic.analyze(program)

    def test_two_texts_are_accepted(self, parser, semantic):
        program = parser.parse("test t = 'x'\nt('a', 'a')")
        semantic.analyze(program)

    def test_two_bools_are_accepted(self, parser, semantic):
        program = parser.parse("test t = 'x'\nt(true, true)")
        semantic.analyze(program)

    def test_null_is_accepted_against_a_value_type(self, parser, semantic):
        program = parser.parse("test t = 'x'\nt(1, null)")
        semantic.analyze(program)

    def test_mismatched_types_are_a_compile_error(self, parser, semantic, errors):
        program = parser.parse("test t = 'x'\nt(1, 'one')")
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)

    def test_a_struct_argument_is_a_compile_error(self, parser, semantic, errors):
        """11.7.1: a struct compares by identity, so this would be a
        failing assertion about two distinct values rather than the
        passing one it means. Rejected at the call instead."""
        source = """
        struct Point {
            x:int
        }
        test t = 'x'
        Point a
        Point b
        t(a, b)
        """
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="identity"):
            semantic.analyze(program)

    def test_an_array_argument_is_a_compile_error(self, parser, semantic, errors):
        source = """
        test t = 'x'
        arr[int] a = [1]
        arr[int] b = [1]
        t(a, b)
        """
        program = parser.parse(source)
        with pytest.raises(errors.CompileError, match="identity"):
            semantic.analyze(program)

    def test_the_wrong_argument_count_is_a_compile_error(self, parser, semantic, errors):
        program = parser.parse("test t = 'x'\nt(1)")
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)

    def test_an_assertion_is_a_bool_expression(self, parser, semantic):
        """11.7.1: the call answers a bool, so a program may branch on
        it. Costs nothing and allows more."""
        program = parser.parse("test t = 'x'\nbool ok = t(1, 1)\nlog(`${ok}`)")
        semantic.analyze(program)


class TestTheReport:
    """End-to-end through `festina test`, which is the only thing that
    compiles assertions at all."""

    SOURCE = """
test basicMath = 'basic math test'
basicMath(2 + 2, 4)
basicMath(3 - 1, 2)
basicMath(2 - 2, 4)

test stringInterpolation = 'string interpolation'
text name = 'Patrick'
text greeting = `Hello, ${name}!`
stringInterpolation(greeting, 'Hello, Patrick!')
"""

    def test_the_report_matches_the_specified_shape(self, run_festina_test):
        out, code = run_festina_test(self.SOURCE)
        assert "basic math test: 2 pass, 1 fail. 66%" in out
        assert "string interpolation: 1 pass. 100%" in out
        assert "Overall: 3 pass, 1 fail. 75%" in out

    def test_percentages_are_truncated_not_rounded(self, run_festina_test):
        """Two of three is 66%. A rounding implementation says 67% and
        every other line in the report still looks right."""
        out, _ = run_festina_test(self.SOURCE)
        assert "66%" in out and "67%" not in out

    def test_a_clean_group_omits_the_fail_count(self, run_festina_test):
        out, _ = run_festina_test(self.SOURCE)
        assert "1 pass. 100%" in out
        assert "1 pass, 0 fail" not in out

    def test_a_failure_quotes_its_own_source_and_the_actual_value(self, run_festina_test):
        out, _ = run_festina_test(self.SOURCE)
        assert "fail: basicMath(2 - 2, 4) // 0" in out

    def test_the_exit_code_is_non_zero_when_something_failed(self, run_festina_test):
        """What makes the command usable in a pipeline."""
        _, code = run_festina_test(self.SOURCE)
        assert code != 0

    def test_the_exit_code_is_zero_when_everything_passed(self, run_festina_test):
        out, code = run_festina_test(
            "test t = 'all good'\nt(1, 1)\nt('a', 'a')\n")
        assert code == 0, out
        assert "all good: 2 pass. 100%" in out

    def test_a_declared_but_never_called_group_reports_nothing_failed(self, run_festina_test):
        out, code = run_festina_test("test t = 'unused'\nlog('ran')\n")
        assert "unused: 0 pass. 100%" in out
        assert code == 0

    def test_ordinary_top_level_code_still_runs(self, run_festina_test):
        """11.7.3: the declarations between assertions are ordinary
        declarations, and the program is a program."""
        out, _ = run_festina_test("log('hello')\ntest t = 'x'\nt(1, 1)\n")
        assert "hello" in out

    def test_the_report_comes_after_the_programs_own_output(self, run_festina_test):
        out, _ = run_festina_test("test t = 'x'\nt(1, 1)\nlog('during')\n")
        assert out.index("during") < out.index("x: 1 pass")


class TestNear:
    def test_near_passes_within_the_tolerance(self, run_festina_test):
        out, code = run_festina_test(
            "test t = 'floats'\nt.near(0.1 + 0.2, 0.3, 0.0001)\n")
        assert code == 0, out
        assert "floats: 1 pass. 100%" in out

    def test_near_fails_outside_the_tolerance(self, run_festina_test):
        out, code = run_festina_test(
            "test t = 'floats'\nt.near(1.0, 2.0, 0.5)\n")
        assert code != 0
        assert "1 fail" in out

    def test_exact_float_equality_is_still_the_trap_it_is(self, run_festina_test):
        """The reason .near exists at all, pinned so the claim in the
        specification is measured rather than asserted."""
        out, code = run_festina_test(
            "test t = 'floats'\nt(0.1 + 0.2, 0.3)\n")
        assert code != 0, out


class TestAssertionsAreCompiledOutOfAnOrdinaryBuild:
    def test_a_normal_compile_emits_no_assertion_code(self, ir_of):
        """11.7.3: `festina compile` removes the declarations and the
        calls entirely, so a shipped binary carries neither the code nor
        the report."""
        ir = ir_of("test t = 'x'\nt(1, 1)\nlog('hi')\n")
        assert "festina_test_" not in ir

    def test_the_program_itself_is_unaffected(self, compile_and_run):
        result = compile_and_run("test t = 'x'\nt(1, 1)\nlog('hi')\n")
        assert result.stdout.strip() == "hi"


class TestTheReportIsMemoryClean:
    """claude.md #341: the report path under AddressSanitizer.

    `scripts/leak_stress.sh` cannot cover this and it is worth saying
    why: every program it builds goes through `festina compile`, where
    specification.md 11.7.3 removes the assertions entirely, so the
    harness would be measuring a program with none in it. The group
    registry's own strdup/free pairs -- a description per group, a
    source and a rendered value per failure, all freed by the report --
    need a build with assertions ON, which is what this does.

    Written after the same check was run by hand and came back clean,
    because a check that ran once and was not kept is not coverage.
    """

    CHURN = """
test t = 'churn'
int i = 0
while i < 200 {
    text a = `value ${i}`
    text b = `value ${i}`
    t(a, b)
    t(i * 2, i + i)
    t.near(i.toFloat() * 0.5, i.toFloat() / 2.0, 0.0001)
    i = i + 1
}
text bad = 'x'
t(bad, 'y')
t(1, 2)
"""

    def test_an_assertion_build_is_sanitizer_clean(self, tmp_path, cli_mod):
        import shutil
        import subprocess
        # Probed rather than assumed, and in this order, for the
        # reason scripts/leak_stress.sh's own probe_san_cc gives: the
        # ASan runtime ships separately from the compiler and is
        # routinely absent. This container has clang with no
        # libclang_rt.asan at all, so picking clang because it exists
        # skips a check that gcc can run perfectly well -- which is
        # exactly what the first version of this test did.
        probe = tmp_path / "probe.c"
        probe.write_text("int main(void){return 0;}\n")
        cc = None
        for candidate in ("clang", "gcc", "cc"):
            found = shutil.which(candidate)
            if found is None:
                continue
            if subprocess.run([found, "-fsanitize=address", str(probe),
                               "-o", str(tmp_path / "probe.bin")],
                              capture_output=True).returncode == 0:
                cc = found
                break
        if cc is None:
            pytest.skip("no compiler on PATH can link with -fsanitize=address")
        if subprocess.run([str(tmp_path / "probe.bin")],
                          env={**os.environ, "ASAN_OPTIONS": "detect_leaks=1"},
                          capture_output=True).returncode != 0:
            pytest.skip("LeakSanitizer unsupported on this platform")

        src = tmp_path / "churn.f"
        src.write_text(self.CHURN, encoding="utf-8")
        ir = cli_mod.compile_file(str(src), emit_llvm=True, tests_enabled=True)
        # The same per-define `sanitize_address` stamping
        # scripts/leak_stress.sh does, and for the reason its own header
        # comment gives: clang does NOT instrument raw .ll text, because
        # the attribute is added by its C frontend.
        ll = tmp_path / "churn.ll"
        ll.write_text(re.sub(r"^(define [^{]+) \{", r"\1 sanitize_address {",
                             ir, flags=re.M))
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        objs = []
        for name in ("festina_runtime.c", "festina_runtime_test.c"):
            obj = tmp_path / (name + ".o")
            flags = []
            if name == "festina_runtime.c":
                flags = subprocess.run(["pkg-config", "--cflags", "sqlite3"],
                                       capture_output=True, text=True
                                       ).stdout.split()
            built = subprocess.run(
                [cc, "-fsanitize=address", "-g", "-O1", "-c",
                 os.path.join(root, "runtime", name), *flags, "-o", str(obj)],
                capture_output=True, text=True)
            if built.returncode != 0:
                pytest.skip(f"cannot build the runtime here: {built.stderr[-300:]}")
            objs.append(str(obj))
        ir_cc = shutil.which("clang")
        if ir_cc is None:
            pytest.skip("no clang -- only clang compiles LLVM IR text")
        prog_obj = tmp_path / "churn.o"
        built = subprocess.run(
            [ir_cc, "-fsanitize=address", "-g", "-O1", "-c", str(ll),
             "-o", str(prog_obj)], capture_output=True, text=True)
        assert built.returncode == 0, built.stderr[-800:]
        binary = tmp_path / "churn.bin"
        sqlite_libs = subprocess.run(["pkg-config", "--libs", "sqlite3"],
                                     capture_output=True, text=True).stdout.split()
        linked = subprocess.run(
            [cc, "-fsanitize=address", "-g", str(prog_obj), *objs,
             "-o", str(binary), *sqlite_libs, "-lm", "-lpthread", "-ldl"],
            capture_output=True, text=True)
        assert linked.returncode == 0, linked.stderr[-800:]
        result = subprocess.run(
            [str(binary)], capture_output=True, text=True, timeout=120,
            env={**os.environ, "ASAN_OPTIONS": "detect_leaks=1"})
        combined = result.stdout + result.stderr
        assert "AddressSanitizer" not in combined, combined[-2000:]
        assert "LeakSanitizer" not in combined, combined[-2000:]
        # And the assertions really ran, so a build that silently
        # stripped them would fail here rather than pass quietly.
        assert "600 pass" in combined, combined[-500:]
