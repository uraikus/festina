"""The oracle semantic analysis will be ported against (decisions.md #280).

`bootstrap/semdump.py` defines what two implementations of semantic
analysis must agree on. The Festina port does not exist yet; these tests
pin the oracle itself, because an oracle that cannot fail would let a
wrong port pass, and that is a more expensive mistake to make late than
to prevent now.

Three things are checked:

1. **It covers the corpus** -- most files analyze, and the ones that do
   not are only the deliberate lexer/parser edge cases.
2. **Imports are merged before analysis**, the specific harness bug that
   made seven corpus files look rejected when nothing was wrong with
   them.
3. **It can actually fail**, shown by perturbing the Python analyzer and
   confirming the dump changes -- the same discipline
   `test_bootstrap_lexer.py::TestTheDifferentialTestCanFail` and
   `test_leak_stress.py::test_the_harness_can_actually_fail` apply.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bootstrap import difftest, semdump   # noqa: E402
from festina import semantic as py_semantic  # noqa: E402


@pytest.fixture(scope="module")
def corpus_dumps():
    """Every corpus file dumped once -- the baseline every canary
    perturbs. Module-scoped because analysis of 89 files is not free."""
    out = {}
    for path in difftest.corpus():
        out[path] = semdump.dump_file(path)
    return out


def _rel(path):
    return os.path.relpath(path, difftest.REPO_ROOT).replace(os.sep, "/")


class TestTheOracleCoversTheCorpus:

    def test_most_of_the_corpus_analyzes(self, corpus_dumps):
        analyzed = [p for p, d in corpus_dumps.items() if not _rejected(d)]
        assert len(analyzed) > 70, (
            "semantic analysis should accept the great majority of the "
            "repository's own .f files; a sharp drop here means the "
            "harness broke, not that the corpus did")

    def test_nothing_crashes(self, corpus_dumps):
        # A crash would surface as an exception from the fixture, so
        # reaching here at all is the assertion; this states it.
        assert len(corpus_dumps) == len(difftest.corpus())

    def test_only_the_lexer_edge_cases_are_rejected(self, corpus_dumps):
        rejected = sorted(_rel(p) for p, d in corpus_dumps.items() if _rejected(d))
        # bootstrap/cases/ holds sources written to exercise LEXING --
        # stray characters, unterminated strings, ambiguous slashes.
        # They are not valid programs and are correctly rejected. A file
        # from anywhere else appearing here is a real finding.
        stray = [r for r in rejected if not r.startswith("bootstrap/cases/")]
        assert not stray, (
            f"these are not lexer edge cases but were rejected: {stray}")

    def test_the_dump_records_locals_not_just_globals(self, corpus_dumps):
        """The whole reason the oracle wraps Scope.define.

        An AnalyzedProgram-only dump says nothing about the inside of a
        function body: two analyzers could agree on every global and
        disagree about every local. Binding records must therefore
        outnumber the per-program summary lines by a wide margin."""
        decls = summaries = 0
        for dump in corpus_dumps.values():
            for line in dump:
                if line.startswith("DECL|"):
                    decls += 1
                elif line.startswith(("MAIN|", "STRUCT|", "TABLE|", "ENUM|", "THREAD|")):
                    summaries += 1
        assert decls > 2000, f"only {decls} binding records over the whole corpus"
        assert decls > summaries * 5


class TestImportsAreMergedBeforeAnalysis:
    """decisions.md #280. The dump goes through imports.build_program,
    not parser.parse on one file, because an `import` merges every
    file's statements into ONE program before analyze() sees them
    (specification.md §6.2) and DatabaseURL is resolved there too.

    Parsing files in isolation instead made five corpus files fail on
    names their imports define and two more on DatabaseURL. Every one of
    those seven would have become a requirement for the port to
    reproduce an error that does not exist."""

    @pytest.mark.parametrize("rel", [
        "bootstrap/parser.f",      # imports lexer.f
        "bootstrap/lexdump.f",     # imports lexer.f
        "bootstrap/astdumpf.f",    # imports parser.f
        "examples/multifile.f",    # imports a struct definition
    ])
    def test_a_file_using_imports_analyzes(self, rel):
        dump = semdump.dump_file(os.path.join(difftest.REPO_ROOT, rel))
        assert not _rejected(dump), (
            f"{rel} was rejected: {dump[0]}. Its imports are not being "
            f"merged before analysis.")

    @pytest.mark.parametrize("rel", [
        "examples/config.f",
        "tests/stress/thread_db_churn.f",
    ])
    def test_a_file_declaring_a_database_url_analyzes(self, rel):
        dump = semdump.dump_file(os.path.join(difftest.REPO_ROOT, rel))
        assert not _rejected(dump), (
            f"{rel} was rejected: {dump[0]}. DatabaseURL is resolved by "
            f"the import pass, so the dump must go through it.")


class TestTheOracleCanActuallyFail:
    """Perturb the Python analyzer; the dump must notice.

    Each canary is a specific, plausible way a port could be wrong --
    not a synthetic mutation -- so that what the oracle detects is
    stated rather than assumed.
    """

    def _sweep(self, baseline, patch, restore):
        patch()
        try:
            differing = []
            for path, want in baseline.items():
                try:
                    got = semdump.dump_file(path)
                except Exception:
                    got = ["CRASHED"]
                if got != want:
                    differing.append(_rel(path))
        finally:
            restore()
        return differing

    def test_a_lost_manually_managed_marker_is_detected(self, corpus_dumps):
        """A port that resolves `blob?` to plain `blob` -- forgetting
        that §8.18 makes them distinct types -- must not pass."""
        original = py_semantic.apply_manually_managed
        differing = self._sweep(
            corpus_dumps,
            lambda: setattr(py_semantic, "apply_manually_managed",
                            lambda t, m: t),
            lambda: setattr(py_semantic, "apply_manually_managed", original))
        assert differing, (
            "dropping `?` from every resolved type changed no file's "
            "dump, so the oracle is not comparing types at all")

    def test_a_lost_binding_kind_is_detected(self, corpus_dumps):
        """A port that cannot tell a constant from a variable from a
        parameter must not pass."""
        original = py_semantic.Symbol

        class KindBlind(original):
            def __init__(self, name, type_, kind, decl, *a, **k):
                super().__init__(name, type_, "variable", decl, *a, **k)

        differing = self._sweep(
            corpus_dumps,
            lambda: setattr(py_semantic, "Symbol", KindBlind),
            lambda: setattr(py_semantic, "Symbol", original))
        assert len(differing) > 50, (
            f"reporting every binding as a plain variable changed only "
            f"{len(differing)} files; the oracle should see this nearly "
            f"everywhere")

    def test_the_recorder_does_not_leak_between_files(self):
        """The wrapper around Scope.define must be removed again.

        A leaked patch would make every later dump in the process
        accumulate the previous file's bindings -- which produces
        confident agreement between two implementations that are both
        being measured wrongly, the hardest kind of harness bug to
        notice."""
        before = py_semantic.Scope.define
        semdump.dump_file(os.path.join(difftest.REPO_ROOT, "examples", "hello.f"))
        assert py_semantic.Scope.define is before

        first = semdump.dump_file(
            os.path.join(difftest.REPO_ROOT, "examples", "hello.f"))
        semdump.dump_file(
            os.path.join(difftest.REPO_ROOT, "bootstrap", "lexer.f"))
        again = semdump.dump_file(
            os.path.join(difftest.REPO_ROOT, "examples", "hello.f"))
        assert first == again, (
            "dumping another file in between changed hello.f's dump")


class TestTheTypeColumnDiscriminates:
    """The canaries above are not enough, and that was found by
    breaking the oracle rather than by reasoning about it.

    Replacing `_type` with a constant -- every type in every record
    rendering as one string -- left all of the other tests in this file
    passing. The `?` canary looks like it covers this and does not: a
    port that drops the manually-managed marker also *rejects different
    programs*, so that test fires on the SEMERR lines and never on the
    type column at all. Nothing was asserting that two different types
    produce two different records, which is the one thing the whole
    oracle rests on.
    """

    def test_two_different_types_render_differently(self):
        from festina import types as types_mod
        from bootstrap.semdump import _type
        int_t = types_mod.PrimitiveType("int")
        float_t = types_mod.PrimitiveType("float")
        arr_int = types_mod.ArrayType(int_t)
        arr_text = types_mod.ArrayType(types_mod.PrimitiveType("text"))
        rendered = {_type(int_t), _type(float_t), _type(arr_int), _type(arr_text)}
        assert len(rendered) == 4, (
            f"four distinct types rendered as {rendered}; the type "
            f"renderer is collapsing types the language tells apart")

    def test_the_corpus_dump_carries_many_distinct_types(self, corpus_dumps):
        """The structural version of the same check, over real data: a
        renderer that collapses everything cannot produce a wide
        spread of type strings."""
        seen = set()
        for dump in corpus_dumps.values():
            for line in dump:
                if line.startswith("DECL|"):
                    seen.add(line.rsplit("|", 1)[1])
        assert len(seen) > 20, (
            f"only {len(seen)} distinct types across the whole corpus "
            f"({sorted(seen)}); the type column is not discriminating")

    def test_a_manually_managed_type_is_distinct_from_its_base(self):
        """§8.18: `T?` and `T` are different types, neither assignable
        to the other. The dump must say so."""
        import dataclasses
        from festina import types as types_mod
        from bootstrap.semdump import _type
        base = types_mod.ArrayType(types_mod.PrimitiveType("int"))
        managed = dataclasses.replace(base, manually_managed=True)
        assert _type(base) != _type(managed), (
            f"{_type(base)} and {_type(managed)} render identically")


class TestTheDumpIsCanonical:

    def test_the_dump_is_sorted(self, corpus_dumps):
        """Dict iteration order and the order of analysis passes are
        implementation choices, not language facts. A port is not
        obliged to reproduce them, so the dump is sorted and neither
        side's internal ordering can manufacture a difference."""
        for path, dump in corpus_dumps.items():
            if not _rejected(dump):
                assert dump == sorted(dump), f"{_rel(path)} is not sorted"

    def test_a_rejection_is_one_line_carrying_only_a_position(self, corpus_dumps):
        """The message text is deliberately not compared -- only that
        both implementations reject the same program in the same
        place."""
        for path, dump in corpus_dumps.items():
            if _rejected(dump):
                assert len(dump) == 1, _rel(path)
                parts = dump[0].split("|")
                assert len(parts) == 3 and parts[0] == "SEMERR"
                assert parts[1].isdigit() and parts[2].isdigit()


def _rejected(dump):
    return len(dump) == 1 and dump[0].startswith("SEMERR|")
