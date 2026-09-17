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
        # claude.md #74 applied per PARAMETER, for a refcounted type.
        # Measured: with the retain deleted, the whole corpus saw no
        # difference -- nothing else that matches has an escaping
        # non-scalar parameter. These two assertions are the evidence.
        assert "@festina_retain(ptr %arg." in body, (
            "no escaping refcounted parameter takes its own reference, "
            "so the half of the per-parameter decision that retains is "
            "unmeasured")
        assert "@festina_release_map(ptr" in body, (
            "no released map, so an array's release and a map's -- "
            "which are not interchangeable -- are not distinguished")
        assert any(line.startswith("  store ptr %arg.") for line in dump), (
            "every text parameter is copied, so nothing here shows a "
            "borrowed one")

    def test_the_array_literal_case_really_has_all_three_mechanisms(self):
        """`cases/array_literals.f` exists because the corpus could not
        see three of the four ways an array literal can be got wrong.

        Measured, not assumed: with the port broken on purpose four
        separate ways -- header allocated before the elements, an empty
        literal computing a size it has no elements for, a
        with-initializer local stack-allocated when it must be
        refcounted, and a retain skipped on an aliasing initializer --
        only the third showed up anywhere in the corpus. The other
        three were invisible, because every array literal in every
        matching file was a non-empty list of constants bound to a name
        that never aliased anything.

        Each of the three is asserted here rather than assumed, the
        same way `cases/float_bits.f` (decisions.md #290) had to learn:
        a case file that drifts away from the shape it was written for
        keeps passing while measuring nothing.
        """
        dump = irdump.dump_file("bootstrap/cases/array_literals.f")
        assert not dump[0].startswith("SEMERR"), (
            "cases/array_literals.f no longer compiles, so it measures "
            "nothing at all: " + dump[0])
        body = "\n".join(dump)
        # Mechanism 1: an element that emits instructions of its own,
        # so "elements first, then the header" is observable at all.
        assert "@bump(" in body, (
            "no call inside an array literal, so nothing here pins the "
            "order of element evaluation against header allocation")
        # Mechanism 2: an empty literal's malloc(0), with no size
        # computation in front of it.
        assert "@malloc(i64 0)" in body, (
            "no empty array literal, so the one literal shape that "
            "computes no element size is unmeasured")
        # Mechanism 3: both allocation strategies for a with-init local.
        assert ".storage." in body, (
            "no frame-allocated container local, so the stack half of "
            "claude.md #81 is unmeasured")
        assert "@festina_retain(" in body, (
            "no retaining initializer, so the half of the rule that "
            "says a non-literal initializer aliases rather than owns "
            "is unmeasured")
        assert "@festina_release_array(" in body, (
            "no refcounted container local, so the heap half of "
            "claude.md #81 is unmeasured")

    def test_the_map_case_really_has_all_four_mechanisms(self):
        """`cases/maps.f` is the ONLY thing standing behind any of the
        map work, and that is measured rather than feared.

        With the port broken on purpose four separate ways -- the
        literal's header allocated after its entries rather than before,
        the `bool` missing-sentinel changed, a rendered key never freed,
        and `delete` handed capacity by pointer instead of by value --
        the whole corpus reported no difference at all. Not one file
        that currently matches uses a map for anything. With this file
        present all four canaries fire.

        So each of the four is asserted here. A drift in this file is
        not a cosmetic loss: it takes the entire map mechanism back to
        unmeasured without a single test turning red.
        """
        dump = irdump.dump_file("bootstrap/cases/maps.f")
        assert not dump[0].startswith("SEMERR"), (
            "cases/maps.f no longer compiles, so it measures nothing at "
            "all: " + dump[0])
        body = "\n".join(dump)
        # 1: a literal entry that emits instructions of its own, so
        # "header first" is orderable against something.
        assert "@bump(" in body, (
            "no call inside a map literal, so nothing here pins the "
            "header allocation against entry evaluation -- and a map "
            "literal builds in the OPPOSITE order to an array one")
        # 2: all three missing-value sentinels, which are unrelated
        # constants chosen per value type at compile time.
        for sentinel, why in (
                ("i64 -9223372036854775808)", "int"),
                ("i64 9221120237041090560)", "float"),
                ("i64 2)", "bool")):
            assert sentinel in body, (
                f"no festina_map_get with the {why} missing-key "
                f"sentinel; that value type is unmeasured")
        # 3: a rendered key, which is freed, next to a constant one,
        # which must not be.
        assert "@festina_str_from_int(" in body, (
            "no non-text map key, so claude.md #302's rendering -- an "
            "expression that reads as borrowed producing an owned "
            "pointer -- is unmeasured")
        # 4: delete, whose capacity argument is by value where a set's
        # is by pointer.
        assert "@festina_map_delete(" in body, (
            "no delete, so the one map call that takes capacity by "
            "value rather than by pointer is unmeasured")
        # The container half of claude.md #81, for a map specifically.
        assert "@festina_map_free_entries(" in body, (
            "no frame-allocated map local, so the stack half of the "
            "decision is unmeasured for maps")
        assert "@festina_release_map(" in body, (
            "no refcounted map local, so the heap half is unmeasured")

    @pytest.mark.parametrize("rel,floor", [
        ("bootstrap/lexer.f", 4000),
        ("bootstrap/parser.f", 12000),
        ("bootstrap/semantic.f", 19000),
        ("bootstrap/escape.f", 20000),
        ("bootstrap/codegen.f", 88000),
        ("bootstrap/lexdump.f", 4000),
        ("bootstrap/astdumpf.f", 12000),
        ("bootstrap/semdumpf.f", 19000),
        ("bootstrap/escdumpf.f", 21000),
        ("bootstrap/irdumpf.f", 88000),
    ])
    def test_a_bootstrap_file_self_hosts(self, rel, floor):
        """The port compiling real pieces of itself -- now all ten.

        Pinned separately from the ratchet because it is a different
        claim: the line count could stay where it is while one of these
        stopped matching and something else made up the difference.
        The floor guards against the other direction -- a file that
        shrank to a stub would "self-host" trivially.
        """
        dump = irdump.dump_file(rel)
        assert not dump[0].startswith("SEMERR"), dump[0]
        assert len(dump) > floor, (
            f"{rel} now emits {len(dump)} lines, below the {floor} this "
            f"milestone was measured on -- it is no longer the same file")

    def test_the_blob_case_really_has_all_five_mechanisms(self):
        """`cases/blobs_and_scopes.f` carries the ordering rules that
        only a 4,552-line file found.

        Three of its five were invisible to the whole corpus until this
        file existed, and one of those needed a struct reached ONLY
        through an array -- anything else generates that struct's
        cascade first and hides the order being measured.
        """
        dump = irdump.dump_file("bootstrap/cases/blobs_and_scopes.f")
        assert not dump[0].startswith("SEMERR"), (
            "cases/blobs_and_scopes.f no longer compiles, so it "
            "measures nothing at all: " + dump[0])
        body = "\n".join(dump)
        assert "@festina_blob_length(" in body, (
            "no blob .length, which is a CALL where an array's is a "
            "header field -- the one blob shape worth saying")
        assert "@festina_blob_slice(" in body, "no blob .slice()"
        assert "@festina_blob_release(" in body, (
            "no blob release, so nothing distinguishes the runtime's "
            "own destructor from the generic one")
        # The struct reached only through an array: exactly one cascade
        # for it, generated by the array's.
        assert "@__festina_release_struct_OnlyViaArray" in body, (
            "the struct reached only through an array is gone, so the "
            "GENERATION ORDER of an array cascade and its element's is "
            "unmeasured -- any struct with a binding of its own "
            "generates its cascade first and hides it")
        # Both loop exits, with a scope to unwind on the way.
        assert "for.update" in body and "while.cond" in body, (
            "a for-loop's continue goes to the update and a while's to "
            "the condition; the file needs both")

    def test_the_null_case_really_has_every_spelling(self):
        """`cases/nulls.f` exists because `null` is the one expression
        with no type of its own, and each type spells its null
        differently -- so a file that only ever nulls an `int` would
        measure one quarter of the mechanism.

        The canaries behind it live in `bootstrap/canary.py`; this is
        the cheap content check that runs everywhere.
        """
        dump = irdump.dump_file("bootstrap/cases/nulls.f")
        assert not dump[0].startswith("SEMERR"), (
            "cases/nulls.f no longer compiles, so it measures nothing "
            "at all: " + dump[0])
        body = "\n".join(dump)
        for frag, why in (("i64 -9223372036854775808", "the int null"),
                          ("double 0x7FF8000000000000", "the float null (a NaN)"),
                          ("i8 2", "the bool null"),
                          ("ptr null", "the pointer null")):
            assert frag in body, (
                f"{why} never appears, so that quarter of the mechanism "
                f"is unmeasured")
        # The signature-driven argument, which is the only position
        # whose type comes from somewhere other than the expression.
        assert "@takesScalars(i64 -9223372036854775808" in body, (
            "no call passing null to a typed parameter, so the "
            "parameter-type table the port carries for exactly this is "
            "unmeasured")

    def test_the_owning_element_case_really_has_all_four_mechanisms(self):
        """`cases/owning_elements.f` is the only evidence that an
        `arr[text]` is not an `arr[int]` with a different element size.

        Four deliberate breakages -- the generated cascade replaced by
        the generic release, the element loop skipped for a
        frame-allocated array, a literal's elements aliased instead of
        copied, and an element write storing without reclaiming -- were
        all invisible to the corpus before this file, because not one
        file that matches holds a container of anything but a scalar. A
        fifth, appending generated functions at the end rather than
        lazily, is invisible without two functions needing the same
        cascade and one not.
        """
        dump = irdump.dump_file("bootstrap/cases/owning_elements.f")
        assert not dump[0].startswith("SEMERR"), (
            "cases/owning_elements.f no longer compiles, so it measures "
            "nothing at all: " + dump[0])
        body = "\n".join(dump)
        # 1: a generated cascade exists and is actually called.
        generated = [line for line in dump
                     if line.startswith("define void @__festina_release_array_")]
        assert len(generated) == 1, (
            f"{len(generated)} generated array cascades; the file needs "
            f"exactly one, shared by every arr[text] in it -- more means "
            f"the per-element-type cache stopped caching")
        assert "@festina_release_array(ptr" in body, (
            "no generic release left, so nothing distinguishes the "
            "cascade from the release every container would get anyway")
        # 2: it lands BEFORE the function that asked for it, which only
        # means something if some function is emitted before it.
        names = [line.split("(")[0].split()[-1] for line in dump
                 if line.startswith("define ")]
        gen_at = next(i for i, n in enumerate(names)
                      if n.startswith("@__festina_release_array_"))
        assert gen_at > 0, (
            "the generated cascade is the first definition in the "
            "module, so its placement relative to the function that "
            "triggered it is unmeasured -- the file needs a function "
            "that does NOT need it emitted first")
        assert gen_at < len(names) - 2, (
            "the generated cascade is last, so nothing here shows it "
            "landing before the body that asked for it")
        # 3: both storage answers, for a container whose elements own.
        assert ".storage." in body, "no frame-allocated container local"
        # 4: the literal's copy and the write's reclaim-then-copy.
        assert "@festina_text_own(" in body, (
            "no copied element, so a literal aliasing its source would "
            "go unnoticed")
        assert "@free(ptr" in body, (
            "nothing reclaimed, so an element write that leaked the "
            "value it replaced would go unnoticed")

    def test_the_json_case_really_has_all_six_mechanisms(self):
        """`cases/json_and_choices.f`: three mechanisms that only look
        unrelated, each a case where something OUTSIDE the expression
        decides its ownership.

        A JSON builder half-fills a value nothing else owns yet; a
        ternary hands back whichever arm ran; a call site holds
        temporaries across a call that may unwind past it.
        """
        dump = irdump.dump_file("bootstrap/cases/json_and_choices.f")
        assert not dump[0].startswith("SEMERR"), (
            "cases/json_and_choices.f no longer compiles, so it "
            "measures nothing at all: " + dump[0])
        body = "\n".join(dump)
        # 1: lenient parsing, and the duplicate-key overwrite.
        assert "@festina_json_skip_field_value(" in body, (
            "no unknown-key skip, so a parse that refused an extra "
            "field would go unnoticed")
        assert "@festina_json_key_matches(" in body
        # 2: the unwind registrations inside the builders.
        assert "@festina_cleanup_push(" in body, (
            "no unwind registration, so a throw mid-parse would leak "
            "the half-built value")
        # 3: all three builder families.
        assert any(l.startswith("define ptr @__festina_from_json_struct_")
                   for l in dump), "no struct builder"
        assert any(l.startswith("define ptr @__festina_from_json_arr_")
                   for l in dump), "no array builder"
        assert any(l.startswith("define ptr @__festina_from_json_map_")
                   for l in dump), (
            "no map builder, so the arbitrary-key loop that "
            "distinguishes a map target from a struct one is unmeasured")
        # 4: the render, its depth cap and its tombstone skip.
        assert any(l.startswith("define void @__festina_json_")
                   for l in dump), "no JSON walker"
        assert "icmp sgt i64 %depth, 32" in body, (
            "no depth cap, so a cyclic value would overflow the stack")
        assert "inttoptr (i64 1 to ptr)" in body, (
            "no tombstone check, so a deleted map entry would render")
        # 5: the ternary arms, normalized.
        assert "tern.then" in body and "tern.else" in body
        # 6: the call-site guard around a throwing callee.
        assert "@festina_cleanup_pop_n(" in body

    def test_the_handles_case_really_has_all_six_mechanisms(self):
        """`cases/handles_and_nesting.f`: four unrelated-looking
        mechanisms that share one property -- each is a case where the
        TYPE decides something the value cannot say for itself.

        A nested container's slots hold whole references while an
        `arr[int]`'s hold nothing. A regex literal is immortal while a
        `regex(p, f)` result is not. A scheduled callback needs a loop
        that a cleared one does not. And a handle's no-argument
        `save()` means "the path you already have", which only a null
        can spell.
        """
        dump = irdump.dump_file("bootstrap/cases/handles_and_nesting.f")
        assert not dump[0].startswith("SEMERR"), (
            "cases/handles_and_nesting.f no longer compiles, so it "
            "measures nothing at all: " + dump[0])
        body = "\n".join(dump)
        # 1: a nested element is released as a container, not freed.
        assert "@festina_release_array(ptr" in body, (
            "no nested container element released, so an arr[arr[T]] "
            "whose slots were freed as buffers would go unnoticed")
        assert any(line.startswith("define void @__festina_release_array_")
                   for line in dump), (
            "no generated cascade, so nothing shows a nested element "
            "needing one")
        # 2 and 3: a literal is cached and marked; regex() is memoized.
        assert "@.regex.cache." in body, "no cached regex literal"
        assert "@festina_regex_mark_cached(" in body, (
            "the cached compilation is not marked, so `free` on a "
            "binding aliasing it would free what every later execution "
            "of that line shares")
        assert "@.regex.memo." in body, (
            "no dynamic regex(), so nothing distinguishes memoizing "
            "from caching")
        assert "@festina_regex_compile_memo(" in body
        # 4: the two splits, and their opposite argument orders.
        assert "@festina_regex_split(" in body, "no split by regex"
        assert "@festina_text_split(" in body, (
            "no split by text, so the argument order that differs "
            "between them is unmeasured")
        # 5: scheduling, clearing, and the loop.
        assert "@festina_set_timeout(" in body and "@festina_set_interval(" in body
        assert "@festina_clear_timeout(" in body and "@festina_clear_interval(" in body
        assert "@festina_run_timer_loop()" in body, (
            "no timer loop, so the scheduled callbacks would never "
            "fire and nothing here would notice")
        # 6: both save spellings.
        assert "@festina_blob_save_copy(ptr" in body, "no saveCopy"

    def test_the_escape_hatch_case_really_has_all_seven_mechanisms(self):
        """`cases/escape_hatch.f` carries the three things a program
        does when it wants to decide something for itself: reclaim by
        hand, pass a function around, and recover from an error.

        They share a mechanism, which is why they share a file. `free`
        marks its target as ESCAPING -- calling a refcounted release on
        a frame address underflows into the stack frame -- and `try`
        makes every tracked binding in the WHOLE program register
        itself for unwinding, because a throw crosses frames that know
        nothing about it.
        """
        dump = irdump.dump_file("bootstrap/cases/escape_hatch.f")
        assert not dump[0].startswith("SEMERR"), (
            "cases/escape_hatch.f no longer compiles, so it measures "
            "nothing at all: " + dump[0])
        body = "\n".join(dump)
        # 1 and 2: free nulls, and clear goes through the flag.
        assert "store ptr null, ptr %gone" in body, (
            "no null store after a free, so a second free would be a "
            "double free and nothing here would notice")
        assert "@festina_begin_clearing()" in body, (
            "no clear, so nothing distinguishes it from a plain free")
        assert "@festina_clear_text(" in body, "no cleared text local"
        # 3: a freed binding is on the heap, not in the frame.
        assert "%owned.storage." not in body, (
            "the freed container local is frame-allocated, so releasing "
            "it would underflow into the frame -- the escape rule that "
            "makes `free` safe is not being applied")
        # 4: a function name as a value.
        assert "store ptr @byAsc" in body, (
            "no function value stored, so the global-symbol-is-the-value "
            "rule is unmeasured")
        # 5: both trampoline families.
        assert any(line.startswith("define i32 @__festina_sortcmp_")
                   for line in dump), "no comparator trampoline"
        assert any(line.startswith("define void @__festina_maptrampoline_")
                   for line in dump), "no forEach trampoline"
        # 6: the cleanup stack, and the catch frame.
        assert "@festina_try_push(" in body, "no try"
        assert "@festina_try_pop()" in body, (
            "no catch-frame pop, so a later unrelated throw could land "
            "back in a stale catch block")
        assert "@festina_cleanup_push(" in body, (
            "no unwind registration, so a throw would walk past every "
            "live local without releasing it")
        assert "@festina_cleanup_pop_n(" in body, (
            "nothing popped, so the cleanup stack would grow without "
            "bound on every ordinary exit")
        assert any(line.startswith("define void @__festina_unwind_")
                   for line in dump), "no unwind function generated"
        # 7: the thrown message is copied off its alias.
        # The CALL, not the declaration at the top of the module --
        # which is what this found first on the way in.
        throw_at = next(i for i, line in enumerate(dump)
                        if "call void @festina_throw(" in line)
        assert any("@festina_text_own(" in line
                   for line in dump[max(0, throw_at - 4):throw_at]), (
            "the thrown message is handed over without an owning copy, "
            "so the unwinding would free it out from under the catch")

    def test_the_driver_case_really_has_all_six_mechanisms(self):
        """`cases/drivers.f` carries what a program needs to be a
        COMMAND, and the two scope bugs only a command reaches.

        The bootstrap's five entry points looked like a list of ten
        missing constructs for five slices. Two were real -- `argv` and
        `close` -- and the other eight were consequences of one
        shadowing bug. Measured rather than described: the shadowing
        canary's only witness was `bootstrap/lexdump.f` before this
        file existed, which is exactly the "one witness, and it is not
        a case file" arrangement the canary harness now reports on.
        """
        dump = irdump.dump_file("bootstrap/cases/drivers.f")
        assert not dump[0].startswith("SEMERR"), (
            "cases/drivers.f no longer compiles, so it measures nothing "
            "at all: " + dump[0])
        body = "\n".join(dump)
        # 1: argv read, indexed and copied.
        assert "load ptr, ptr @argv" in body, (
            "no argv read, so a compiler that could not see the name at "
            "all would go unnoticed")
        # 2: close, through the runtime rather than libc.
        assert "@festina_program_exit(" in body, (
            "no close(), so nothing distinguishes it from a plain exit")
        # 3 and 4: the two halves of the shadowing rule. A local named
        # `rows` must get its own slot even though `@rows` exists, and
        # a top-level `tally` must be a global even though some
        # function has a local by that name.
        assert "@rows = global" in body, "no shadowed global container"
        assert any(line.strip().startswith("%rows.") and "alloca" in line
                   for line in dump), (
            "the local that shadows @rows got no storage, so this file "
            "is no longer measuring the bug it was written for")
        assert "@tally = global" in body, (
            "the top-level `tally` is not a global, so main is still "
            "inheriting some function's locals -- which is the other "
            "half of the same bug")
        # 5: both map projections, with values' three constants.
        assert "@festina_map_keys(" in body, "no .keys()"
        assert "@festina_map_values(" in body, "no .values()"
        # 6: delete, with and without a trampoline.
        deletes = [line for line in dump if "@festina_map_delete(" in line]
        assert any("ptr null)" in line for line in deletes), (
            "no delete on a map of a scalar, so the null trampoline is "
            "unmeasured")
        assert any("ptr @__festina_maprelease_" in line for line in deletes), (
            "no delete on a map whose values own something, so a delete "
            "that leaked the value it removed would go unnoticed")

    def test_the_owning_container_case_really_has_all_six_mechanisms(self):
        """`cases/owning_containers.f` exists because the mechanisms it
        holds were, for one slice, measured by nothing but
        `bootstrap/codegen.f` itself.

        Every canary for them fired -- and every one of them fired on
        that single 58,000-line file, so the whole set would have gone
        silent together the moment it stopped matching for any
        unrelated reason. Two further breakages were invisible even to
        it: a field read off a minted computed index, and a
        frame-allocated map's values. Both were found by writing this
        file, not by reading the code.
        """
        dump = irdump.dump_file("bootstrap/cases/owning_containers.f")
        assert not dump[0].startswith("SEMERR"), (
            "cases/owning_containers.f no longer compiles, so it "
            "measures nothing at all: " + dump[0])
        body = "\n".join(dump)
        # 1: an element minted out of a container the expression owns --
        # a copy for text, a retain for a refcounted one -- and the
        # container released after.
        assert "@festina_text_own(" in body, (
            "no text element minted, so an index off an owning "
            "container handing back a borrowed pointer into freed "
            "storage would go unnoticed")
        assert "@festina_retain(" in body, (
            "no refcounted element minted, so only half of claude.md "
            "#119 is measured")
        # 2 and 3: a map[text] write, which copies or not depending on
        # the value's own source, and frees what the key held before.
        assert "@festina_map_set(" in body, "no map write at all"
        assert "@festina_str_concat(" in body, (
            "no concatenation stored into a map, so the text owning "
            "predicate is never asked a question the refcounted one "
            "answers differently")
        # 4: the release trampoline, for both value shapes, and the
        # inline for_each a frame-allocated map gets.
        trampolines = [line for line in dump
                       if line.startswith("define void @__festina_maprelease_")]
        assert len(trampolines) >= 2, (
            f"{len(trampolines)} map release trampolines; the file "
            f"needs one per value type -- text and a struct -- or the "
            f"difference between freeing and releasing is unmeasured")
        assert "@festina_map_for_each(" in body, (
            "no map value walk, so a map whose values leaked would go "
            "unnoticed")
        assert "@festina_map_free_entries(" in body, (
            "no frame-allocated map, so the inline walk that runs "
            "before the entries buffer is freed is unmeasured")
        # 5: floorDiv's adjustment branch, which only differs from sdiv
        # on a negative operand.
        assert "srem i64" in body, (
            "no floorDiv remainder, so nothing distinguishes flooring "
            "from truncation")
        # 6: the exactly-representable literal that only parses once
        # trailing fraction zeros are stripped.
        assert "0x4330000000000000" in body, (
            "no 2^52 float literal, so a parse whose window is decided "
            "by SPELLING rather than value would go unnoticed")

    def test_the_conversion_case_really_has_all_four_mechanisms(self):
        """`cases/conversions.f` is the only evidence for the method-call
        slice, and that is measured rather than feared.

        With the port broken on purpose four ways -- the `'42'.toInt()`
        constant fold removed, an owning text receiver never freed, the
        float-to-int guard replaced by a bare `fptosi`, and the `Math`
        namespace made conditional on `Math` being unbound -- the whole
        corpus reported no difference at all. Not one file that
        currently matches calls a method.

        One of those four fires through the coverage RATCHET rather than
        as a diff: the port reports the file unported instead of
        emitting different IR, and "unported" is how this harness spells
        "not implemented yet". The line count still falls, which is what
        catches it.

        claude.md #339 changed what the fourth one can be. It used to
        need a binding called `Math` in this file, and that is a compile
        error now; the canary is `math-dispatches-on-the-name` instead,
        breaking the name-based dispatch itself. What this test asserts
        is unchanged in substance -- both shapes of method call are
        present -- but the second one is an ordinary float binding
        rather than one that shadows the namespace.
        """
        dump = irdump.dump_file("bootstrap/cases/conversions.f")
        assert not dump[0].startswith("SEMERR"), (
            "cases/conversions.f no longer compiles, so it measures "
            "nothing at all: " + dump[0])
        body = "\n".join(dump)
        # 1: the fold. A folded receiver leaves NO call behind, so the
        # evidence is that some toInt calls exist and some do not --
        # asserted as a count, since either alone proves nothing.
        runtime_parses = body.count("@festina_text_to_int(")
        assert runtime_parses >= 5, (
            "too few dynamic .toInt() receivers; the file must pair "
            "each folded literal with a binding, or the fold and the "
            "runtime are never compared against each other")
        assert "i64 9223372036854775807" in body, (
            "no folded overflow, so the half of strtoll's rule that "
            "CLAMPS rather than wraps is unmeasured")
        # 2: an owning receiver freed after the call reads it.
        assert "@festina_text_trim(" in body and "@free(ptr" in body, (
            "no text-consuming method on an owning receiver, so the "
            "receiver free is unmeasured")
        # 3: the float-to-int guard, in full.
        for frag, why in (("fcmp uno double", "the NaN test"),
                          ("@llvm.fptosi.sat.i64.f64(", "the saturating conversion"),
                          ("select i1", "the null answer")):
            assert frag in body, (
                f"{why} is missing, so claude.md #102's guard against "
                f"fptosi's undefined behaviour is unmeasured")
        # 4: both shapes of method call -- one dispatched on the
        # receiver's NAME (the namespace, straight to an intrinsic) and
        # one on its TYPE (an ordinary runtime call).
        assert "@llvm.sqrt.f64(" in body, "no Math namespace call"
        assert "@festina_str_from_float(" in body, (
            "no .toText() on a float binding, so only the name-based "
            "half of method dispatch is measured")

    @pytest.mark.parametrize("case", ["indexing.f", "array_literals.f", "maps.f",
                                      "conversions.f",
                                      "owning_elements.f",
                                      "nulls.f",
                                      "blobs_and_scopes.f",
                                      "owning_containers.f",
                                      "drivers.f",
                                      "escape_hatch.f",
                                      "handles_and_nesting.f",
                                      "json_and_choices.f"])
    def test_the_container_cases_really_run(self, compile_and_run, case):
        """The two container case files are PROGRAMS, not only sources
        of IR, and this runs them to prove it.

        The first version of `cases/indexing.f` declared its arrays
        empty and then wrote through them. Festina does not bounds-check
        an index, so it segfaulted -- and the differential test was
        perfectly happy with it, because a file that crashes still
        produces IR to compare. Nothing in the harness noticed; the
        program's own exit status did.

        So these two are executed as well as dumped. It is the only
        check in this file that can tell "the port emits the same IR"
        apart from "the IR either side emits actually works."
        """
        source = os.path.join(difftest.REPO_ROOT, "bootstrap", "cases", case)
        with open(source, encoding="utf-8") as handle:
            text = handle.read()
        result = compile_and_run(text, filename=case)
        assert result.returncode == 0, (
            f"cases/{case} exits {result.returncode}: {result.stderr}")

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

    @pytest.mark.parametrize("literal,why", [
        ("caf\u00e9", "a two-byte code point"),
        ("\u2713 ok", "a three-byte code point"),
        ("\U0001F389", "a four-byte code point"),
        ("a\x01b\x0bc\x7fz", "control bytes with no escape spelling"),
        ("tab\there\nand\\a quote \" too", "the escapes that do have one"),
    ])
    def test_a_string_constant_is_encoded_byte_for_byte(
            self, codegen_binary, tmp_path, literal, why):
        """A string constant is an array of BYTES with a declared
        length, and `text.length` counts CODE POINTS -- so anything but
        plain ASCII needs the two kept apart.

        This is a test rather than a corpus file because the interesting
        inputs are control bytes, which have no escape spelling in
        Festina and would be genuinely awkward to keep in a checked-in
        source file. It is here because the port got this wrong and no
        corpus file could see it: the old encoder escaped only
        backslash, quote and the three whitespace escapes, and passed
        every other byte through untouched -- so `a\x01b` emitted
        `c"abz\00"`, silently DROPPING the control characters rather
        than failing. Found by needing a multi-byte literal for
        `cases/conversions.f`, not by reading the code.
        """
        # Spelled as a Festina single-quoted literal, not via repr():
        # Python's own escaping would emit \xNN and \uNNNN forms the
        # Festina lexer has no spelling for, so the two sides would
        # disagree about the SOURCE rather than about its encoding.
        body = (literal.replace("\\", "\\\\").replace("'", "\\'")
                       .replace("\n", "\\n").replace("\t", "\\t"))
        source = tmp_path / "lit.f"
        source.write_text(f"log('{body}')\n", encoding="utf-8")
        status, detail = irdiff.compare(codegen_binary, str(source))
        assert status == "match", (
            f"{why} is encoded differently by the two implementations:\n"
            f"  python:  {detail[1] if detail else ''}\n"
            f"  festina: {detail[2] if detail else ''}")

    def test_coverage_does_not_go_backwards(self, codegen_binary):
        """A ratchet, not a target.

        The port is early, so this asserts the floor rather than a
        finished number -- what it prevents is a change that quietly
        stops emitting something that already worked. Raise it as the
        port grows; never lower it to make a run green.
        """
        reproduced = irdiff.lines_reproduced(codegen_binary)
        assert reproduced >= 358118, (
            f"file-specific IR lines reproduced fell to {reproduced}; "
            f"the port previously emitted at least 358118")


class TestTheTestBuildMatchesPython(TestBootstrapCodegenMatchesPython):
    """claude.md #341: the same differential, over a `festina test`
    build.

    The port of the test type covered what an ordinary build does --
    remove every assertion -- because that was the only thing
    `bootstrap/irdumpf.f` could produce. Its `--tests` argument is what
    makes the EMITTING half comparable at all: group registration, the
    comparison per type, the rendered source line, `.near`, and the
    report and exit code in main.

    Deliberately a separate comparison from `irdiff.compare` rather
    than a widening of it. Every canary in the registry calls that
    function, and the sweep is already the longest-running thing here;
    doubling its work to cover one mechanism would be a bad trade. This
    runs the corpus once more instead, which is bounded and explicit.

    Over the WHOLE corpus, not just the file that declares a group:
    with assertions enabled every file's `main` grows the report call
    and the exit-code select, so the part of this that is not about
    `test` bindings gets 131 witnesses rather than one.
    """

    @pytest.mark.parametrize("rel", [
        os.path.relpath(p, difftest.REPO_ROOT) for p in difftest.corpus()
    ])
    def test_test_build_matches_the_python_codegen(self, codegen_binary, rel):
        path = os.path.join(difftest.REPO_ROOT, rel)
        status, detail = irdiff.compare_tests(codegen_binary, path)
        if status == "unported":
            pytest.skip(f"not ported yet: {detail}")
        assert status in ("match", "rejected"), (
            f"{rel}: line {detail[0] + 1}\n"
            f"  python:  {detail[1]}\n"
            f"  festina: {detail[2]}")
