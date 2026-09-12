"""Codegen, ported to Festina, and the oracle it is checked against
(decisions.md #289).

The fourth and last stage. Unlike the other three, its oracle needed no
design: codegen's output is LLVM IR text, so the canonical form is the
IR itself and the comparison is line for line.

What the oracle DID need was two corrections, both found by measuring
rather than by reasoning, and both pinned here because each would
otherwise have produced a green run that meant nothing:

1. **`CodeGen._uid` is a class attribute.** Generating IR twice in one
   process yields two different texts -- same structure, every
   generated name shifted by a constant. An in-process oracle would
   hand the Festina side a moving target and the failure would look
   like a port bug in every file after the first.
2. **A file count reads an order of magnitude too high here.** Eleven
   corpus files are `cases/*.f`, which exist to be lexed rather than to
   be valid programs, so both sides answer a bare `SEMERR`. Counted as
   matches, the first real run reported "12 match" for a port that
   could emit exactly one module. `irdiff.compare` reports those as
   "rejected", and coverage is measured in file-specific IR lines.

Only the differential test itself needs a compiled Festina binary, so
only it is Linux-only (decisions.md #287); everything else here is pure
Python and runs everywhere in about a second.
"""
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bootstrap import difftest, irdiff, irdump   # noqa: E402


class TestTheOracleIsReproducible:
    """`CodeGen._uid` is a class attribute, so this is not theoretical.

    Without `irdump._reset_uid`, a second dump of the same file differs
    from the first in every generated name. These tests exist because
    that would not have looked like a harness bug.

    **The file matters.** These were first written against
    `benchmarks/hello.f`, which is `log('hello')` and calls `_unique()`
    exactly zero times -- so every one of them passed, and would have
    passed with the reset deleted. `examples/hello.f` uses the counter
    ten times. A reproducibility test on an input that cannot vary
    tests nothing, and the control below is what surfaced it.
    """

    SENSITIVE = "examples/hello.f"      # 10 _unique() calls
    OTHER = "examples/basic.f"          # 1, enough to move the counter

    def test_a_second_dump_of_one_file_is_identical(self):
        first = irdump.dump_file(self.SENSITIVE)
        second = irdump.dump_file(self.SENSITIVE)
        assert first == second

    def test_an_intervening_dump_does_not_shift_the_names(self):
        """The actual failure mode: dumping the corpus in a loop, where
        every file after the first sees a counter another file moved."""
        first = irdump.dump_file(self.SENSITIVE)
        irdump.dump_file(self.OTHER)
        again = irdump.dump_file(self.SENSITIVE)
        assert first == again

    def test_the_reset_agrees_with_a_genuinely_fresh_process(self):
        """The reset could be wrong in the same direction twice and
        these tests would still pass, so the claim is checked against
        the thing it is standing in for: a process that never generated
        anything else."""
        irdump.dump_file(self.OTHER)                  # dirty the counter
        in_process = irdump.dump_file(self.SENSITIVE)
        result = subprocess.run(
            [sys.executable, "bootstrap/irdump.py", self.SENSITIVE],
            capture_output=True, text=True, cwd=difftest.REPO_ROOT, timeout=300)
        assert result.returncode == 0, result.stderr
        fresh = result.stdout.split("\n")
        if fresh and fresh[-1] == "":
            fresh.pop()
        assert in_process == fresh

    def test_without_the_reset_two_dumps_really_do_diverge(self):
        """The control, and the test that found the flaw above.

        If this passes while the three tests above are pointed at an
        input the counter cannot reach, they are all vacuous. Here the
        reset is disabled deliberately and the divergence it prevents is
        demonstrated rather than described.
        """
        original = irdump._reset_uid
        irdump._reset_uid = lambda: None
        try:
            first = irdump.dump_file(self.SENSITIVE)
            second = irdump.dump_file(self.SENSITIVE)
        finally:
            irdump._reset_uid = original
        assert first != second, (
            "two dumps of the same file agreed with the reset disabled, "
            "so CodeGen._uid is no longer a shared counter and "
            "irdump._reset_uid is dead code -- remove it rather than "
            "leave it implying a hazard that no longer exists")

    def test_the_counter_advances_on_a_real_module(self):
        from festina import codegen as codegen_mod
        irdump.dump_file(self.SENSITIVE)
        assert codegen_mod.CodeGen._uid > 0


class TestTheOracleIsMachineIndependent:
    """The IR carries its own source path in a comment on line 2, so a
    dump taken with an absolute path bakes this checkout's location into
    the expected output. Relative paths are not cosmetic here."""

    def test_a_relative_path_leaves_no_absolute_path_in_the_ir(self):
        dump = irdump.dump_file("benchmarks/hello.f")
        assert not any(difftest.REPO_ROOT in line for line in dump)

    def test_an_absolute_path_does_leak_one(self):
        """The control for the test above: if absolute paths did NOT
        leak, `irdiff.relative` would be pointless ceremony and the
        test above would be proving nothing."""
        dump = irdump.dump_file(os.path.join(difftest.REPO_ROOT,
                                             "benchmarks/hello.f"))
        assert any(difftest.REPO_ROOT in line for line in dump)


class TestTheCoverageNumberIsHonest:

    def test_rejected_files_are_not_counted_as_matches(self):
        """The `cases/*.f` files are rejected by the front end, so both
        implementations answer a bare SEMERR and "agree" trivially.
        Counting those as matches reported 12 for a port that could emit
        one module."""
        rejected = [p for p in difftest.corpus()
                    if len(irdump.dump_file(irdiff.relative(p))) == 1
                    and irdump.dump_file(irdiff.relative(p))[0].startswith("SEMERR")]
        assert len(rejected) >= 10, (
            "the corpus should still contain the deliberate "
            "lexer/parser edge cases that never reach codegen")

    def test_the_struct_field_case_really_reaches_the_lazy_path(self):
        """`cases/struct_fields.f` exists to measure the auto-vivify
        path claude.md #97 describes, and the differential test can only
        measure what the corpus actually contains.

        So the property is asserted rather than assumed: more than one
        `field.make` block, because a single reach through a
        struct-typed field cannot distinguish a phi that names the
        block its value was computed in from one that names the label
        it branched to -- the two agree on the first access and diverge
        on every one after it. This is the `cases/float_bits.f` lesson:
        a case file whose literal was quietly outside the range it was
        written for measured nothing and looked fine.
        """
        dump = irdump.dump_file("bootstrap/cases/struct_fields.f")
        assert not dump[0].startswith("SEMERR"), (
            "cases/struct_fields.f no longer compiles, so it measures "
            "nothing at all: " + dump[0])
        makes = [line for line in dump if line.startswith("field.make")]
        assert len(makes) >= 3, (
            f"only {len(makes)} lazy-field blocks in the expected IR; "
            f"the file needs several reaches through a struct-typed "
            f"field or it cannot tell a correct phi predecessor from a "
            f"hardcoded label")

    def test_the_text_case_really_reaches_every_text_path(self):
        """`cases/text_building.f` is the only corpus file that
        distinguishes four separate text-building decisions, and each is
        asserted here rather than assumed.

        Measured, not guessed: with string-constant interning removed
        from the port, `cases/text_building.f` is the ONE file in the
        whole corpus that differs. Three of the other four canaries are
        invisible to `benchmarks/string_concat.f` too -- the only
        pre-existing file that builds strings at all. A file that stops
        containing a repeated literal, or an interpolation with no
        surrounding text, silently stops measuring the thing it exists
        for.
        """
        dump = irdump.dump_file("bootstrap/cases/text_building.f")
        assert not dump[0].startswith("SEMERR"), (
            "cases/text_building.f no longer compiles, so it measures "
            "nothing at all: " + dump[0])
        body = "\n".join(dump)
        assert body.count("@festina_text_append(") > 5, (
            "the in-place append path (decisions.md #243) is barely "
            "reached; the file needs the `s = s + x` and "
            "`s = `${s}x`` shapes, in a loop as well as straight-line")
        assert body.count("@festina_str_concat(") > 5, (
            "the ordinary concat path is barely reached, so the file no "
            "longer distinguishes appending from concatenating")
        assert "@festina_text_own(" in body, (
            "no festina_text_own call, so nothing here is a bare "
            "`${x}` -- the one template shape that must copy on the "
            "way out rather than alias its only interpolation")
        assert "@festina_str_eq(ptr" in body, (
            "no text equality, which is its own operand-freeing rule")
        # One constant per distinct literal, not per use. `'-'` appears
        # four times in the source; if this finds two definitions of it,
        # interning has regressed on the PYTHON side.
        dashes = [line for line in dump
                  if line.startswith("@.str.") and line.endswith('c"-\\00"')]
        assert len(dashes) == 1, (
            f"{len(dashes)} globals for the literal '-'; the file must "
            f"keep repeating one literal, and codegen.py must keep "
            f"interning it, or the port's own interning is unmeasured")

    def test_the_escape_case_really_has_both_allocation_strategies(self):
        """`cases/escape_locals.f` exists to measure claude.md #74's one
        decision -- frame storage versus a refcount header for the same
        source construct -- so it has to contain both answers.

        A file that drifted to all-stack or all-heap would keep passing
        while measuring nothing, which is the `cases/float_bits.f`
        lesson (decisions.md #290) in a new place.
        """
        dump = irdump.dump_file("bootstrap/cases/escape_locals.f")
        assert not dump[0].startswith("SEMERR"), dump[0]
        body = "\n".join(dump)
        assert ".storage." in body, (
            "no frame-allocated struct local, so the stack half of the "
            "decision is unmeasured")
        assert "@festina_release(ptr" in body, (
            "no released struct local, so the heap half is unmeasured")
        assert "@festina_text_own(ptr %arg." in body, (
            "no escaping text parameter takes its owning copy, so the "
            "per-parameter half of the decision is unmeasured")
        # The borrowed parameter is the other half: at least one text
        # parameter must be stored straight from its own %arg register.
        assert "@festina_release_array(ptr" in body, (
            "no released array local, so the container half of the "
            "decision is unmeasured")
        assert "@festina_map_free_entries(" in body, (
            "no frame-allocated map local -- a stack container still "
            "owns a heap buffer, which is the one way its stack answer "
            "differs from a struct's")
        assert any(line.startswith("  store ptr %arg.") for line in dump), (
            "every text parameter is copied, so nothing here shows a "
            "borrowed one")

    def test_the_line_budget_excludes_the_shared_preamble(self):
        total, specific = irdiff.line_budget()
        assert specific < total, (
            "every module emits the same runtime declaration block, so "
            "the file-specific budget must be strictly smaller than the "
            "total")
        assert total - specific > 20000, (
            "the shared preamble is 382 lines across 80-odd compilable "
            "files; if the gap has collapsed, the preamble is no longer "
            "shared and the budget is measuring the wrong thing")


class TestBootstrapCodegenMatchesPython:
    """The differential test itself.

    Linux-only, like the other three harnesses and for the same reason:
    it compiles a Festina binary and runs it across the whole corpus,
    and nothing in it is platform-specific.
    """

    @pytest.fixture(scope="module")
    def codegen_binary(self, tmp_path_factory):
        from tests.conftest import _require_bootstrap_platform, _require_c_compiler
        _require_bootstrap_platform()
        _require_c_compiler()
        out = tmp_path_factory.mktemp("bootstrap") / "fir"
        return irdiff.build_codegen(str(out))

    @pytest.mark.parametrize("rel", [
        os.path.relpath(p, difftest.REPO_ROOT) for p in difftest.corpus()
    ])
    def test_ir_matches_the_python_codegen(self, codegen_binary, rel):
        path = os.path.join(difftest.REPO_ROOT, rel)
        status, detail = irdiff.compare(codegen_binary, path)
        if status == "unported":
            pytest.skip(f"not ported yet: {detail}")
        assert status in ("match", "rejected"), (
            f"{rel}: line {detail[0] + 1}\n"
            f"  python:  {detail[1]}\n"
            f"  festina: {detail[2]}")

    def test_coverage_does_not_go_backwards(self, codegen_binary):
        """A ratchet, not a target.

        The port is early, so this asserts the floor rather than a
        finished number -- what it prevents is a change that quietly
        stops emitting something that already worked. Raise it as the
        port grows; never lower it to make a run green.
        """
        reproduced = irdiff.lines_reproduced(codegen_binary)
        assert reproduced >= 1896, (
            f"file-specific IR lines reproduced fell to {reproduced}; "
            f"the port previously emitted at least 1896")
