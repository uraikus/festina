"""Escape analysis, ported to Festina, and the oracle it is checked
against (decisions.md #299).

The fifth harness, and the one the codegen port was blocked on: escape
analysis decides whether every container and struct local lives in the
frame or behind a heap refcount header, so `bootstrap/codegen.f` cannot
emit a single such declaration without agreeing on it first.

Two things this oracle needed that the other four did not:

1. **The ORDER is part of the answer.** claude.md #74 stage 2 exempts a
   call argument only once the callee's own body has been walked, so
   two implementations analyzing the same bodies in a different order
   produce the same records and disagree about every exemption. Each
   record carries its index.
2. **The traversal order is measured, not guessed.** `escdump.py` hooks
   the real compiler and records what a genuine `generate_ir` asks for.
   Reimplementing codegen's walk in the oracle would have been
   reimplementing the thing most likely to be wrong.

Only the differential test itself needs a compiled Festina binary, so
only it is Linux-only (decisions.md #287); everything else here is pure
Python.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bootstrap import difftest, escdiff, escdump   # noqa: E402


class TestTheOracleSaysSomething:

    def test_a_plain_program_produces_one_record_per_body(self):
        dump = escdump.dump_file("examples/hello.f")
        kinds = [line.split("|")[2] for line in dump]
        assert kinds == ["FUNC", "FUNC", "TOPLEVEL"], dump

    def test_the_member_base_exemption_is_visible_in_the_oracle(self):
        """The whole analysis rests on one exemption -- a name used as
        the base of a field or element access does not escape -- so an
        oracle that never exercised it would be measuring nothing.

        `benchmarks/array_sum.f` reads `nums[j]` in a loop and never
        uses `nums` any other way, so `nums` must be absent from its
        function's record. If this ever starts failing because the file
        changed, point it at another file rather than deleting it.
        """
        dump = escdump.dump_file("benchmarks/array_sum.f")
        first = dump[0]
        assert first.startswith("SEQ|0|FUNC|run|"), first
        names = first.split("|")[4].split(",")
        assert "nums" not in names, (
            "nums is only ever indexed, so it must not escape -- if it "
            "does, the member-base exemption is gone and this harness "
            "is measuring a much weaker claim")

    def test_a_rejected_program_answers_semerr_alone(self):
        dump = escdump.dump_file("bootstrap/cases/err_stray_char.f")
        assert len(dump) == 1 and dump[0].startswith("SEMERR"), dump


class TestTheCorpusStaysMachineIndependent:

    def test_no_corpus_file_uses_an_auto_sized_pool(self):
        """`thread pool[] { }` is `cpu_count()` wide, and codegen emits
        one copy of every body per instance -- so a corpus file using
        one would give this dump a different length on a different
        machine. `escdumpf.f` refuses such a file rather than answering;
        this asserts the refusal is currently unreachable, so that if a
        corpus file ever grows one, the reason the harness starts
        skipping it is already written down.
        """
        offenders = []
        for path in difftest.corpus():
            with open(path, encoding="utf-8") as fh:
                if "pool[]" in fh.read():
                    offenders.append(os.path.relpath(path, difftest.REPO_ROOT))
        assert offenders == [], (
            f"{offenders} use an auto-sized pool, so their record count "
            f"depends on cpu_count(); escdumpf.f reports them unported")


class TestBootstrapEscapeMatchesPython:
    """The differential test itself."""

    @pytest.fixture(scope="module")
    def escape_binary(self, tmp_path_factory):
        from tests.conftest import _require_bootstrap_platform, _require_c_compiler
        _require_bootstrap_platform()
        _require_c_compiler()
        out = tmp_path_factory.mktemp("bootstrap") / "fesc"
        return escdiff.build_escape(str(out))

    @pytest.mark.parametrize("rel", [
        os.path.relpath(p, difftest.REPO_ROOT) for p in difftest.corpus()
    ])
    def test_escaping_names_match_the_python_analysis(self, escape_binary, rel):
        path = os.path.join(difftest.REPO_ROOT, rel)
        status, detail = escdiff.compare(escape_binary, path)
        if status == "unported":
            pytest.skip(f"not ported yet: {detail}")
        assert status in ("match", "rejected"), (
            f"{rel}: record {detail[0]}\n"
            f"  python:  {detail[1]}\n"
            f"  festina: {detail[2]}")

    def test_coverage_does_not_go_backwards(self, escape_binary):
        """A ratchet, not a target. Raise it as the port grows; never
        lower it to make a run green."""
        reproduced, total = escdiff.records_reproduced(escape_binary)
        assert reproduced >= 1477, (
            f"records reproduced fell to {reproduced} of {total}; the "
            f"port previously reproduced at least 1477")
