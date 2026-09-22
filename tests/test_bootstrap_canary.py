"""The canaries: proof that the differential harness could tell.

`tests/test_bootstrap_codegen.py` asserts the two implementations
AGREE. That is a real claim and it is not the one that keeps this port
honest, because it stays green whether the corpus can distinguish the
two implementations or not. Over six slices of the port, the honest
answer was usually that it could not: four consecutive slices measured
ZERO of four or five mechanisms visible to the whole corpus until a
`cases/` file was written for them.

So each of those mechanisms is broken on purpose here, and the corpus
is asked whether it notices. A failure in this module is not "the
compiler is wrong" -- the compiler is fine, that is what the other
module checks. It is **"the corpus has stopped being able to see this,"**
and the fix is a case file, not a code change.

Until this existed, the canaries were hand-written patch scripts run
once per slice and recorded only as prose in a commit message. Nothing
re-ran them, so a `cases/` file could drift away from the shape it was
written for and take a whole mechanism back to unmeasured without a
single test turning red. `bootstrap/canary.py` holds the registry; this
drives it.

Each case builds a compiler from a deliberately broken copy of
`bootstrap/` (never the repository itself -- see `build_patched`, and
`repository_is_not_written_to` below, which checks it per canary
rather than trusting that sentence). It is not marked slow and not
deselected: a canary suite nobody runs is exactly the prose in a
commit message this replaced. Linux-only, like every other bootstrap
harness and for the same reason (decisions.md #287).

It is also, by a wide margin, the most expensive thing in this
repository: 161 whole-compiler builds, measured at 6,116s of a 6,593s
serial suite -- 92.8%. That is what `scripts/run_tests.sh` runs in
parallel (decisions.md #344), and these builds sharing no state is the
only reason it is allowed. The docstring above used to say "about
ninety seconds"; it was written when there were fifty-five canaries
and `bootstrap/codegen.f` was not yet in the corpus.
"""
import hashlib
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bootstrap import canary as canary_mod   # noqa: E402
from bootstrap import irdiff                 # noqa: E402


def _bootstrap_tree_digest():
    """A digest of every Festina source under `bootstrap/`.

    The canaries patch `bootstrap/codegen.f`, `bootstrap/lexer.f` and
    `bootstrap/parser.f`, so those are what a bug would disturb -- but
    digesting the whole directory costs the same and does not have to
    be revisited when a canary is aimed somewhere new.
    """
    src_dir = os.path.join(irdiff.REPO_ROOT, "bootstrap")
    h = hashlib.sha256()
    for name in sorted(os.listdir(src_dir)):
        if not name.endswith(".f"):
            continue
        h.update(name.encode("utf-8"))
        with open(os.path.join(src_dir, name), "rb") as fh:
            h.update(fh.read())
    return h.hexdigest()


class TestTheRegistryIsWellFormed:
    """Pure-Python checks, so they run everywhere and cost nothing."""

    def test_every_canary_has_a_distinct_name(self):
        names = [c.name for c in canary_mod.CANARIES]
        assert len(names) == len(set(names)), (
            "two canaries share a name, so one of them cannot be run "
            "on its own and the summary counts it twice")

    def test_every_canary_names_the_decision_it_came_from(self):
        for c in canary_mod.CANARIES:
            assert c.slice.startswith("#"), (
                f"canary {c.name!r} does not name a decisions.md entry; "
                f"without it the reasoning behind the breakage is lost")
            assert len(c.mechanism) > 20, (
                f"canary {c.name!r} describes what it breaks as "
                f"{c.mechanism!r}, which will not mean anything to "
                f"whoever reads a failure")

    def test_every_anchor_still_matches_exactly_once(self):
        """The most valuable check in this file, and the cheapest.

        A canary whose anchor has drifted is worse than a deleted one:
        it reads as a mechanism that is still guarded while guarding
        nothing. `patched()` raises rather than silently applying zero
        substitutions, so this just asks every canary to build its own
        patched source.
        """
        for c in canary_mod.CANARIES:
            c.patched()      # raises LookupError if stale or ambiguous


class TestTheCorpusCanStillSeeEachMechanism:
    """The differential test itself, run backwards.

    Deliberately NOT marked slow and deselected by default. It is the
    only thing that keeps a case file honest, and a canary suite nobody
    runs is exactly the prose-in-a-commit-message this replaced.

    Keeping that affordable is a live concern rather than a settled
    one. The corpus is now dominated by the bootstrap's own compiler
    -- `bootstrap/codegen.f` alone is 58,000 IR lines -- so re-dumping
    all of it for every canary would have taken this from about a
    minute to well over an hour. `canary.run_one` scans cheapest-first
    and stops once it has `WITNESSES_WANTED` differing files, which
    puts the usual case (a mechanism a `cases/` file was written for)
    back in the hundreds of milliseconds. A canary with only ONE
    witness anywhere still costs a full scan, which is the right way
    round: those are the ones worth knowing about.
    """

    # SESSION-scoped, not module-scoped, and the difference is worth
    # 2,750 seconds. Under `-n`, pytest-xdist hands each worker tests in
    # scheduler order, freely interleaved across modules -- so a
    # module-scoped fixture is torn down whenever a worker moves to
    # another file and REBUILT when it comes back. With 161 canaries
    # scattered among 3,716 other tests, each worker paid this
    # baseline's ~148s bootstrap rebuild over and over: the full suite
    # ran in 5,221s where the arithmetic said 2,475s, while this module
    # ALONE ran at 3.66x parallel efficiency. Running the file by itself
    # hides the whole effect, which is why it has to be said here.
    # Nothing about the baseline is per-module: it is a clean compiler
    # and the Python side's dump of every corpus file, and neither
    # depends on which test asks.
    @pytest.fixture(scope="session")
    def baseline(self, tmp_path_factory):
        from tests.conftest import _require_bootstrap_platform, _require_c_compiler
        _require_bootstrap_platform()
        _require_c_compiler()
        out = tmp_path_factory.mktemp("canary") / "fir"
        clean = irdiff.build_codegen(str(out))
        base = canary_mod.baseline(clean)
        assert base.paths, (
            "no corpus file matches at all, so every canary below would "
            "report 'undetected' for a reason that has nothing to do "
            "with the corpus")
        return base

    @pytest.fixture(autouse=True)
    def repository_is_not_written_to(self):
        """Every canary, checked for the property parallelism rests on.

        `canary.build_patched` copies the whole of `bootstrap/` into a
        scratch directory and patches the COPY, so 161 canaries can be
        built at once without colliding -- which is what pytest-xdist
        does here (decisions.md #344), and the only reason this module
        is affordable to run at all.

        Patching in place would still pass every assertion below: the
        canary would compile, the corpus would notice, and the verdict
        would be identical. It would simply leave a deliberately broken
        compiler checked out for whichever test ran next. So the
        property has to be checked directly rather than inferred from a
        green run, and checking it per canary costs a few hundred
        microseconds against a build measured in tens of seconds.
        """
        before = _bootstrap_tree_digest()
        yield
        assert _bootstrap_tree_digest() == before, (
            "a canary wrote to bootstrap/ in the repository instead of "
            "to its own copy. Every later test in this session is now "
            "running against a deliberately broken compiler, and under "
            "-n that corruption is racing whatever the other workers "
            "are building.")

    @pytest.mark.parametrize("name", [c.name for c in canary_mod.CANARIES])
    def test_breaking_it_on_purpose_is_noticed(self, baseline, name, tmp_path):
        c = canary_mod.BY_NAME[name]
        verdict, detail = canary_mod.run_one(c, baseline)

        if verdict == "broken":
            pytest.fail(
                f"canary {name!r} no longer produces a WRONG compiler, "
                f"it produces an absent one -- which proves nothing "
                f"about the corpus. Re-aim it.\n{detail}")
        assert verdict in ("differ", "ratchet"), (
            f"THE CORPUS CAN NO LONGER SEE: {c.mechanism}\n"
            f"({c.name}, added for decisions.md {c.slice})\n\n"
            f"Breaking this on purpose changed nothing any corpus file "
            f"could detect. That is not a compiler bug -- the compiler "
            f"is right, and tests/test_bootstrap_codegen.py still "
            f"passes. It means the case file this mechanism relies on "
            f"has drifted away from the shape it was written for, so "
            f"the mechanism is now unmeasured by anything.\n\n"
            f"The fix is a corpus file that exercises it, not a change "
            f"to bootstrap/codegen.f.")
