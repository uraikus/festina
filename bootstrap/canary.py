"""Canaries: the breakages the differential harness is supposed to catch.

A green differential test proves the two implementations AGREE. It says
nothing about whether the corpus could tell them apart if they stopped
agreeing -- and over six slices of the codegen port, the honest answer
was usually "no". Four separate slices measured zero of four or zero of
five mechanisms visible to the whole corpus before a `cases/` file was
written for them. The canary is what turns "this matches" into "this
matches AND a wrong answer would have shown".

Until now those canaries were hand-written patch scripts, run once, and
recorded only as prose in a commit message. Nothing re-ran them. So a
`cases/` file could drift away from the shape it was written for, the
content assertions in tests/test_bootstrap_codegen.py could go on
passing, and the mechanism behind it would quietly stop being measured
by anything at all. This module makes each one a named, re-runnable
fact.

    python bootstrap/canary.py                 # every canary
    python bootstrap/canary.py map-header      # just these
    python bootstrap/canary.py --list

Each canary is one or more exact text substitutions into
`bootstrap/codegen.f`, chosen to produce a specific WRONG compiler
rather than a broken one -- a patch that fails to compile proves
nothing, and neither does one that makes the port refuse a construct it
used to emit. `run_one` reports which of those actually happened.

**How a canary is judged.** The harness classifies a file four ways, and
only two of them mean anything here:

  differ      some matching file now emits different IR. The strongest
              signal: a real disagreement, in a real program.
  ratchet     no file differs, but the coverage NUMBER fell -- because
              a file the port used to emit went "unported" instead.
              Weaker: "unported" is how this harness spells "not
              implemented yet", so a regression of this shape is caught
              by the ratchet in tests/test_bootstrap_codegen.py and not
              by the per-file comparison. Real detection; worth naming
              separately, because it is not the same claim.
  undetected  the corpus cannot see this mechanism at all. A canary
              that reports this is not a failing test of the compiler;
              it is a failing test of the CORPUS, and the fix is a case
              file, not a code change.

**Only currently-matching files are compared**, which is exact rather
than an optimisation: coverage counts only matching files, so a
canary's entire effect is determined by that set. A file that was
already unported cannot become more so.

**"Caught" is not the whole answer; WHICH file caught it matters too.**
decisions.md #312 is why: seven canaries fired, all seven fired on
`bootstrap/codegen.f` and on nothing else, and the honest reading of
that pass is a failure -- a mechanism whose only witness is the largest
file in the corpus goes unmeasured the day that file stops matching for
some unrelated reason, and the whole set would have gone silent
together. So a canary looks for two independent witnesses
(WITNESSES_WANTED), scans the corpus cheapest-first, and reports a lone
witness in its own output rather than letting it read as an ordinary
pass.
"""
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bootstrap import irdiff, irdump                         # noqa: E402
from bootstrap.difftest import REPO_ROOT, corpus             # noqa: E402

CODEGEN_F = os.path.join(REPO_ROOT, "bootstrap", "codegen.f")

# How many differing files a canary looks for before it stops scanning.
#
# One would be enough to call the mechanism "caught", and for most of
# this registry's life that is what the count was. It is not enough, and
# decisions.md #312 is why: seven canaries fired, all seven fired on
# `bootstrap/codegen.f` and on nothing else, and the honest reading of
# that pass is a failure. A mechanism whose only witness is the largest
# file in the corpus is measured in name only -- the whole set goes
# silent together the day that file stops matching for some unrelated
# reason. So the bar is TWO independent programs, and a canary that
# finds only one after scanning everything says so in its own output.
#
# Two rather than three because the cost is real: every extra file is
# another compilation of the patched compiler's output, and the corpus
# is now dominated by files of tens of thousands of IR lines.
WITNESSES_WANTED = 2


class Canary:
    """One deliberate breakage, and what it is meant to prove.

    Each anchor must appear EXACTLY ONCE in the target file. That is
    not pedantry: a substitution that silently matched a second site
    would break something other than the mechanism named here, and the
    canary would go on passing while measuring something else. A stale
    anchor -- the code moved and nothing matches any more -- is reported
    as its own kind of failure rather than as a passing canary, for the
    same reason.
    """

    def __init__(self, name, slice_, mechanism, old, new=None, path=CODEGEN_F):
        self.name = name
        self.slice = slice_
        self.mechanism = mechanism
        # One (old, new) pair, or a list of them. Some breakages
        # genuinely need two coordinated edits -- moving a declaration
        # means deleting it in one place and adding it in another, and
        # either half alone is a compile error rather than a wrong
        # compiler. Found the hard way: the first hand-written version
        # of `array-header-order` moved the allocation from before the
        # entry LIST to before the entry LOOP, which are both before the
        # entries, so it changed nothing and "passed" -- indistinguishable
        # from a corpus that cannot see the mechanism.
        self.edits = [(old, new)] if new is not None else list(old)
        self.path = path

    def patched(self):
        with open(self.path, encoding="utf-8") as fh:
            source = fh.read()
        for old, new in self.edits:
            hits = source.count(old)
            if hits == 0:
                raise LookupError(
                    f"canary {self.name!r} is stale: an anchor no longer "
                    f"appears in {os.path.relpath(self.path, REPO_ROOT)}. "
                    f"The code it breaks has moved or gone; re-aim it at "
                    f"the mechanism rather than deleting it.\n"
                    f"  anchor: {old.strip().splitlines()[0][:70]}")
            if hits > 1:
                raise LookupError(
                    f"canary {self.name!r} matches {hits} sites in "
                    f"{os.path.relpath(self.path, REPO_ROOT)}; it must "
                    f"break exactly one, or it is measuring something "
                    f"other than {self.mechanism}.")
            source = source.replace(old, new, 1)
        return source


# ---------------------------------------------------------------------
# The registry.
#
# Every entry here was run by hand when its slice shipped, and its
# result recorded in decisions.md. The `undetected` answers are the
# reason the `cases/` files exist; the ones that fire are the reason
# those files may not drift.

CANARIES = [
    # --- decisions.md #303: array literals ---------------------------
    Canary(
        "array-header-order", "#303",
        "an array literal evaluates every element before allocating its header",
        [
            # Allocate before the elements instead of after -- which is
            # the MAP literal's order, and the whole reason the two
            # cannot share one builder.
            ("""    arr[text] vals = []
    arr[int] owned = []""",
             """    text early = header
    if early == '' { early = cgFreshHeader('%struct._FestinaArray') }
    arr[text] vals = []
    arr[int] owned = []"""),
            ("""    text into = header
    if into == '' { into = cgFreshHeader(hdrTy) }""",
             """    text into = early"""),
        ],
    ),
    Canary(
        "array-empty-size", "#303",
        "an empty array literal computes no element size",
        "    if elems.length > 0 {",
        "    if elems.length >= 0 {",
    ),
    Canary(
        "array-init-always-stack", "#303",
        "claude.md #81: a with-initializer container stack-allocates only from a literal",
        """            if linit != null {
                // claude.md #81 covers both container literals, and
                // only a literal: the entry count of a `{ ... }` is as
                // knowable at the declaration as an array's length.
                if managed == 'arr' {
                    if linit.kind != 'ArrayLit' { stackable = false }
                } else if managed == 'map' {
                    if linit.kind != 'MapLit' { stackable = false }
                } else {
                    stackable = false
                }
            }""",
        """            if linit != null { stackable = false }""",
    ),
    Canary(
        "array-init-no-retain", "#303",
        "an aliasing container initializer retains rather than owning",
        """                bool lOwning = cgIsOwningRefcountedSource(linit)
                if lv.fresh { lOwning = true }
                // claude.md #202: no retain at the declaration and no
                // tracking afterwards -- see the blob branch above.
                if manual { lOwning = true }
                if lOwning == false {
                    cgOut(`  call void @festina_retain(ptr ${lv.v})`)
                }""",
        "",
    ),

    # --- decisions.md #304: maps -------------------------------------
    Canary(
        "map-bool-sentinel", "#304",
        "the per-value-type 'key not present' constant",
        "    if vlty == 'i8' { return '2' }",
        "    if vlty == 'i8' { return '0' }",
    ),
    Canary(
        "map-key-not-freed", "#304",
        "festina_map_set strdups the key, so a rendered one is freed at the call",
        "    if keyOwned { cgOut(`  call void @free(ptr ${keyV})`) }",
        "",
    ),
    Canary(
        "map-delete-capacity", "#304",
        "delete takes capacity by VALUE where a set takes it by pointer",
        """    text cap = cgTmp()
    cgOut(`  ${cap} = load i64, ptr ${capP}`)
    text tombP = cgTmp()""",
        """    text cap = capP
    text tombP = cgTmp()""",
    ),

    # --- decisions.md #305: non-scalar parameters --------------------
    Canary(
        "param-no-retain", "#305",
        "an escaping refcounted parameter takes its own reference",
        """                cgOut(`  call void @festina_retain(ptr ${arg})`)
                CG_PARAM_LIVE.push(`${pftys[q]}|${slot}|${petys[q]}${psnames[q]}`)""",
        """                CG_PARAM_LIVE.push(`${pftys[q]}|${slot}|${petys[q]}${psnames[q]}`)""",
    ),
    Canary(
        "param-always-retain", "#305",
        "a parameter the body only READS takes nothing at all",
        """        } else if cgIsRefcounted(pftys[q]) && pmanual[q] == false {
            if escSet[pnames[q]] != null {""",
        """        } else if cgIsRefcounted(pftys[q]) && pmanual[q] == false {
            if true {""",
    ),
    Canary(
        "param-generic-release", "#305",
        "an array's release and a map's are not interchangeable",
        "                CG_PARAM_LIVE.push(`${pftys[q]}|${slot}|${petys[q]}${psnames[q]}`)",
        "                CG_PARAM_LIVE.push(`struct|${slot}|${petys[q]}${psnames[q]}`)",
    ),

    # --- decisions.md #306: method calls -----------------------------
    Canary(
        "toint-no-fold", "#306",
        "claude.md #150: a literal .toInt() receiver is parsed at compile time",
        """    if m == 'toInt' && args.length == 0 && recv.kind == 'StringLit' {
        return cgVal(`${rawText(recv, 'value').toInt()}`, 'i64', 'int')
    }""",
        "",
    ),
    Canary(
        "method-receiver-not-freed", "#306",
        "a text receiver the expression allocated is freed by the method that reads it",
        """        cgOut(`  ${out} = call ptr @festina_text_trim(ptr ${r.v})`)
        cgFreeTextTemp(recv, r)""",
        """        cgOut(`  ${out} = call ptr @festina_text_trim(ptr ${r.v})`)""",
    ),
    Canary(
        "float-to-int-ub", "#306",
        "claude.md #102: fptosi is undefined for NaN, infinity and out-of-range",
        """    text isNan = cgTmp()
    cgOut(`  ${isNan} = fcmp uno double ${v}, ${v}`)""",
        """    text out0 = cgTmp()
    cgOut(`  ${out0} = fptosi double ${v} to i64`)
    return out0
    text isNan = cgTmp()
    cgOut(`  ${isNan} = fcmp uno double ${v}, ${v}`)""",
    ),
    # claude.md #339 retired this canary's predecessor,
    # "math-yields-to-binding": it broke the Math branch by making it
    # yield to a variable called `Math`, and `cases/conversions.f`
    # caught it because the file declared one. A binding of that name is
    # a compile error now, so nothing could declare one and the break
    # became undetectable -- an anchor that still applies cleanly while
    # pinning nothing, which is the worst state a canary can be in. The
    # invariant that replaced it is the one the rule rests on: a Math
    # method is dispatched on the receiver's NAME, so it reaches the
    # intrinsic rather than the ordinary method path.
    Canary(
        "math-dispatches-on-the-name", "#339",
        "a float-returning Math method reaches the namespace, not cgMethodCall",
        "    if cgMathFloatFn(m) != '' { return true }",
        "",
    ),
    Canary(
        "string-const-byte-rule", "#306",
        "a string constant is escaped per BYTE, not per recognised escape",
        "    if b >= 32 && b < 127 && b != 34 && b != 92 { return b.toChar() }",
        "    if b != 34 && b != 92 && b != 10 && b != 9 && b != 13 { return b.toChar() }",
    ),

    # --- decisions.md #307: containers whose elements own ------------
    Canary(
        "cascade-generic-release", "#307",
        "a container whose elements own something needs a generated cascade",
        "    if fty == 'arr' && cgElemOwnsSomething(ety) { return cgReleaseArrayFn(ety) }",
        "",
    ),
    Canary(
        "cascade-stack-skips-elements", "#307",
        "a frame-allocated array still has its elements' claims to give back",
        """        if cgElemOwnsSomething(parts[2]) {
            cgReleaseArrayElements(dataV, lenV, cgElemReleaseFn(parts[2]), cgElemLty(parts[2]))
        }""",
        "",
    ),
    Canary(
        "literal-element-aliased", "#307",
        "a literal COPIES a text element rather than aliasing its source",
        "        } else if ety == 'text' && owned[k] == 0 {",
        "        } else if false {",
    ),
    Canary(
        "element-write-no-reclaim", "#307",
        "an element write gives back what the slot already held",
        """        cgOut(`  call void @free(ptr ${old})`)
    }
    cgOut(`  store ${elemLty} ${stored}, ptr ${slot}`)""",
        """    }
    cgOut(`  store ${elemLty} ${stored}, ptr ${slot}`)""",
    ),
    # --- decisions.md #311: cycles, minting, and parser.f -------------
    Canary(
        "cycle-trial", "#311",
        "claude.md #120: a cyclic type's release runs a trial deletion",
        """    bool cyclic = cgIsCyclic(sname)
    text aliveL = doneL""",
        """    bool cyclic = false
    text aliveL = doneL""",
    ),
    # --- decisions.md #340: the deferred-root buffer -----------------
    Canary(
        "cycle-release-buffers-the-root", "#340",
        "a still-referenced release BUFFERS the value, it does not walk it",
        "    cgOut(`  call void @festina_cycle_add_root(ptr %payload, ptr ${g}, ptr ${sc}, ptr ${w})`)",
        "    cgOut(`  call void ${g}(ptr %payload)`)\n"
        "    cgOut(`  call void ${sc}(ptr %payload)`)\n"
        "    cgOut(`  call void ${w}(ptr %payload)`)",
    ),
    Canary(
        "cycle-white-defers-its-free", "#340",
        "a white sweep hands its node to the pending-free list rather than freeing it",
        "        cgOut(`  call void @festina_cycle_defer_free(ptr ${hdr})`)",
        "        cgOut(`  call void @free(ptr ${hdr})`)",
    ),
    Canary(
        "cycle-buffer-is-flushed-at-exit", "#340",
        "main flushes the deferred-root buffer before returning",
        "    cgOut('  call void @festina_cycle_flush()')",
        "",
    ),
    Canary(
        "cycle-white-disposes-acyclic-fields", "#311",
        "a white sweep disposes exactly the fields the trial did not traverse",
        """            if cyclic == false {
                if cgIsRefcounted(f) {""",
        """            if true {
                if cgIsRefcounted(f) {""",
    ),
    Canary(
        "field-release-resolved-first", "#311",
        "a field's release is resolved before the field's own GEP temp",
        [
            ("""            text fn = '@festina_free_z'
            if f != 'text' { fn = cgReleaseFnFor(f, cgFieldEty(key)) }
            text fp = cgTmp()""",
             """            text fp = cgTmp()"""),
            ("""            cgOut(`  ${fv} = load ptr, ptr ${fp}`)
            cgOut(`  call void ${fn}(ptr ${fv})`)""",
             """            cgOut(`  ${fv} = load ptr, ptr ${fp}`)
            text fn = '@festina_free_z'
            if f != 'text' { fn = cgReleaseFnFor(f, cgFieldEty(key)) }
            cgOut(`  call void ${fn}(ptr ${fv})`)"""),
        ],
    ),
    Canary(
        "call-frees-owning-arguments", "#311",
        "a call borrows its arguments, so an owning one is the caller's to reclaim",
        # Aimed at the shared helper rather than at one call site:
        # claude.md #141 gave calls a SECOND form (indirect, through a
        # function value), and a canary pinned to the direct site alone
        # went ambiguous the moment the second one appeared. The
        # mechanism was always the helper.
        """void func cgFreeCallArgs(args:arr[Node], vals:arr[Val]) {
    int i = 0""",
        """void func cgFreeCallArgs(args:arr[Node], vals:arr[Val]) {
    int i = vals.length""",
    ),
    Canary(
        "discarded-result-released", "#311",
        "a discarded call result is provably this statement's only reference",
        """        if cgIsRefcounted(r.fty) {
            if cgIsOwningRefcountedSource(ex) || r.fresh {
                cgOut(`  call void ${cgReleaseFnFor(r.fty, cgRelKeyVal(r))}(ptr ${r.v})`)
            }
        } else {
            cgFreeTextTemp(ex, r)
        }""",
        "",
    ),
    Canary(
        "field-read-drains-its-chain", "#311",
        "claude.md #117/#262: a field read off an owned base mints, then "
        "releases that base",
        """    if es.length == 0 { return out }
    if cgIsRefcounted(out.fty) {""",
        """    if true { return out }
    if cgIsRefcounted(out.fty) {""",
    ),
    # The mint and the release are one mechanism but two claims, and
    # breaking only the mint went UNDETECTED until
    # `cases/owning_field_reads.f` existed: every corpus file that read
    # a field off an owning base did it through `.length`, which drains
    # the chain and mints nothing, because an i64 owes the base
    # nothing. Two canaries rather than one, so a corpus that can see
    # the release but not the mint says so.
    Canary(
        "field-read-mint-retains-a-refcounted-field", "#311",
        "claude.md #117: a refcounted field read through an owning base "
        "takes its own reference before the base is released",
        """    if cgIsRefcounted(out.fty) {
        cgOut(`  call void @festina_retain(ptr ${out.v})`)
        out.fresh = true
    } else if out.fty == 'text' {
        text owned = cgTmp()
        cgOut(`  ${owned} = call ptr @festina_text_own(ptr ${out.v})`)
        out.v = owned
        out.fresh = true
    }
    int j = 0""",
        """    if false {
        cgOut(`  call void @festina_retain(ptr ${out.v})`)
        out.fresh = true
    } else if out.fty == 'text' {
        text owned = cgTmp()
        cgOut(`  ${owned} = call ptr @festina_text_own(ptr ${out.v})`)
        out.v = owned
        out.fresh = true
    }
    int j = 0""",
    ),
    Canary(
        "field-read-mint-copies-a-text-field", "#311",
        "claude.md #83: a text field read through an owning base is "
        "COPIED, not retained -- it has no count to take",
        """    } else if out.fty == 'text' {
        text owned = cgTmp()
        cgOut(`  ${owned} = call ptr @festina_text_own(ptr ${out.v})`)
        out.v = owned
        out.fresh = true
    }
    int j = 0""",
        """    } else if false {
        text owned = cgTmp()
        cgOut(`  ${owned} = call ptr @festina_text_own(ptr ${out.v})`)
        out.v = owned
        out.fresh = true
    }
    int j = 0""",
    ),
    # No canary for "two struct values compare as i64". The
    # construct cannot appear in a working program: the shipped
    # compiler emits `icmp eq i64 %ptr, %ptr` for it, which LLVM
    # rejects outright, so `struct == struct` fails to build. Both
    # implementations agree on that invalid IR -- it is a compiler
    # bug rather than a port gap (todo.md) -- and a canary for
    # something no compiling program can contain could never be
    # caught by any corpus.
    Canary(
        "null-comparison-after-text", "#311",
        "the text branch claims `text == null` before the pointer branch sees it",
        """    if l.fty == 'text' || r.fty == 'text' {""",
        """    if op == '==' || op == '!=' {
        if rn.kind == 'NullLit' || ln.kind == 'NullLit' {
            text other2 = r.lty
            text value2 = r.v
            if rn.kind == 'NullLit' { other2 = l.lty  value2 = l.v }
            if other2 == 'ptr' {
                text cmp2 = cgTmp()
                text pred2 = 'eq'
                if op == '!=' { pred2 = 'ne' }
                cgOut(`  ${cmp2} = icmp ${pred2} ptr ${value2}, null`)
                text out2 = cgTmp()
                cgOut(`  ${out2} = zext i1 ${cmp2} to i8`)
                return cgVal(out2, 'i8', 'bool')
            }
        }
    }
    if l.fty == 'text' || r.fty == 'text' {""",
    ),
    Canary(
        "refcounted-element-write-retains", "#311",
        "a refcounted array element write retains, stores, then releases the old",
        """        cgOut(`  store ${elemLty} ${stored}, ptr ${slot}`)
        cgOut(`  call void ${cgElemReleaseFn(obj.ety)}(ptr ${old})`)""",
        """        cgOut(`  store ${elemLty} ${stored}, ptr ${slot}`)""",
    ),
    Canary(
        "refcounted-field-write-defers-release", "#311",
        "claude.md #120: a refcounted field write stores BEFORE releasing the old",
        [
            ("""            cgOut(`  store ptr ${rv.v}, ptr ${fp.v}`)
            cgOut(`  call void ${cgReleaseFnFor(fp.fty, cgRelKeyVal(fp))}(ptr ${old})`)""",
             """            cgOut(`  call void ${cgReleaseFnFor(fp.fty, cgRelKeyVal(fp))}(ptr ${old})`)
            cgOut(`  store ptr ${rv.v}, ptr ${fp.v}`)"""),
        ],
    ),

    # --- decisions.md #310: blob, break/continue, struct cascades -----
    Canary(
        "blob-generic-release", "#310",
        "a blob's destructor is the runtime's own, not the generic release",
        "    if fty == 'blob' { return '@festina_blob_release' }",
        "",
    ),
    Canary(
        "blob-length-is-a-call", "#310",
        "a blob carries no length field, unlike an array header",
        """        cgOut(`  ${out} = call i64 @festina_blob_length(ptr ${obj.v})`)""",
        """        cgOut(`  ${out} = call i64 @festina_blob_length(ptr ${obj.v})`)
        cgOut(`  ; canary`)""",
    ),
    Canary(
        "slice-emits-receiver-twice", "#310",
        "the original emits a .slice() receiver twice and discards the first",
        """        if m == 'slice' {
            Val first = cgExpr(recv)""",
        """        if false {
            Val first = cgExpr(recv)""",
    ),
    Canary(
        "break-frees-its-scope", "#310",
        "break and continue free what the iteration declared before leaving",
        "        cgFreeFrom(target[2].toInt())",
        "",
    ),
    Canary(
        "continue-target-is-the-update", "#310",
        "a for-loop's continue goes to the update, so the step still runs",
        "    CG_LOOPS.push(`${updateL}|${endL}|${CG_LIVE.length}`)",
        "    CG_LOOPS.push(`${condL}|${endL}|${CG_LIVE.length}`)",
    ),
    Canary(
        "struct-cascade", "#310",
        "a struct with an owning field needs a generated cascade",
        """    if fty == 'struct' {
        if cgStructOwnsAnything(ety) || CG_TAGGED[ety] != null
                || SF_WEAK_TARGET[ety] != null {
            return cgReleaseStructFn(ety)
        }
    }""",
        "",
    ),
    Canary(
        "stack-struct-keeps-its-fields", "#310",
        "a frame-allocated struct still owns its fields' buffers",
        """                if managed == 'struct' {
                    if cgStructOwnsAnything(declEty) {
                        cgTrackLive('struct.stack', slot, declEty)
                    }
                }""",
        "",
    ),
    Canary(
        # The first of two ordering rules this slice got wrong, each
        # found only by diffing a 4,552-line file line for line.
        "field-write-value-before-old", "#310",
        "a text field write emits the VALUE before reading what the slot held",
        [
            ("""            Node fvalue = childOf(e, 'value')
            Val fv = cgExprExpecting(fvalue, 'text', '')
            if CG_STUCK { return }
            text old = cgTmp()
            cgOut(`  ${old} = load ptr, ptr ${fp.v}`)""",
             """            text old = cgTmp()
            cgOut(`  ${old} = load ptr, ptr ${fp.v}`)
            Node fvalue = childOf(e, 'value')
            Val fv = cgExprExpecting(fvalue, 'text', '')
            if CG_STUCK { return }"""),
        ],
    ),
    Canary(
        # The second. Within one frame the frees run in DECLARATION
        # order; across frames the INNERMOST runs first. The two point
        # opposite ways, which is exactly why guessing gets it wrong.
        "frees-innermost-frame-first", "#310",
        "scope exit frees the innermost frame first, outer frames last",
        [
            ("""    arr[int] bounds = []
    int f = 0
    while f < CG_FRAME.length {
        if CG_FRAME[f] > downTo { bounds.push(CG_FRAME[f]) }
        f++
    }""",
             """    arr[int] bounds = []
    int f = 0
    while f < 0 {
        if CG_FRAME[f] > downTo { bounds.push(CG_FRAME[f]) }
        f++
    }"""),
        ],
    ),
    Canary(
        "return-retains-before-freeing", "#310",
        "a returned refcounted value takes its reference before the scope frees",
        """        if cgIsOwningRefcountedSource(v) == false && r.fresh == false {
            cgOut(`  call void @festina_retain(ptr ${val})`)
        }
    } else if r.fty == 'text' {""",
        """        if false {
            cgOut(`  call void @festina_retain(ptr ${val})`)
        }
    } else if r.fty == 'text' {""",
    ),
    Canary(
        "elem-release-resolved-first", "#310",
        "an element's own cascade is generated before the array body's temps",
        [
            ("""    text elemFn = cgElemReleaseFn(ety)

    arr[text] saved = CUR""", """
    arr[text] saved = CUR"""),
            ("cgReleaseArrayElements(dataV, lenV, elemFn, cgElemLty(ety))",
             "cgReleaseArrayElements(dataV, lenV, cgElemReleaseFn(ety), cgElemLty(ety))"),
        ],
    ),

    # --- decisions.md #309: null, split and join ---------------------
    Canary(
        "null-int-sentinel", "#309",
        "each Festina type spells its own null, and they are unrelated",
        "    if fty == 'int' { return '-9223372036854775808' }",
        "    if fty == 'int' { return '0' }",
    ),
    Canary(
        # Necessarily a ratchet canary rather than a diff one, and the
        # reason is worth stating: the ORDER here is a consequence of
        # the type resolution rather than an independent choice. A null
        # cannot be emitted before the side it takes its type from,
        # because there is nothing to emit yet -- so there is no "wrong
        # order" that still compiles, only a missing mechanism. The
        # coverage number is what catches it.
        "null-comparison-typed-from-the-other-side", "#309",
        "a comparison against null resolves it from the other operand's type",
        """    if rn.kind == 'NullLit' && ln.kind != 'NullLit' {""",
        """    if false {""",
    ),
    Canary(
        "null-argument-signature", "#309",
        "a null ARGUMENT takes the parameter's type, not the call site's",
        # BOTH tables the call site consults, not one: claude.md #141
        # added the encoded signature beside FN_PARAMS, and breaking
        # either alone leaves the other still supplying a type -- so
        # the canary would pass while measuring nothing. Found by
        # re-aiming it and noticing it stopped firing.
        """        text want = ''
        text wantKey = ''
        if i < ptys.length { want = ptys[i] }
        if want == '' {
            if i < sigPtys.length {
                want = cgSigFty(sigPtys[i])
                wantKey = cgSigKey(sigPtys[i])
            }
        }""",
        """        text want = ''
        text wantKey = ''""",
    ),
    Canary(
        "split-receiver-not-freed", "#309",
        "split frees the receiver AND the separator it allocated",
        """        cgFreeTextTemp(recv, r)
        cgFreeTextTemp(args[0], sep)
        cgFreeRegexTemp(args[0], sep)
        return cgArrVal(out, 'text')""",
        """        return cgArrVal(out, 'text')""",
    ),
    Canary(
        "owned-container-receiver", "#309",
        "a container temporary with no binding is released where it is read",
        # Anchored on the ARRAY branch's own preceding GEP, because
        # decisions.md #320's ascii .length is a header load with the
        # identical two lines after it -- the shorter anchor named two
        # sites and so measured neither.
        """        if havePend {
            Val noOut
            cgReleaseMemberChain(pendE, pendV, childOf(e, 'obj'), obj, noOut)
        }
        return cgVal(out, 'i64', 'int')""",
        """        return cgVal(out, 'i64', 'int')""",
    ),
    Canary(
        "join-element-kind", "#309",
        "join carries the element KIND, which only the compiler knows",
        "ptr ${cgStringConst(r.ety)})`)",
        "ptr ${cgStringConst('int')})`)",
    ),

    # --- decisions.md #312: the port compiles itself ------------------
    Canary(
        "computed-index-mints-before-release", "#312",
        "an element read through an OWNING container is copied before "
        "the container is released",
        """    } else if out.fty == 'text' {
        text owned = cgTmp()
        cgOut(`  ${owned} = call ptr @festina_text_own(ptr ${out.v})`)
        out.v = owned
        out.fresh = true
    }
    cgOut(`  call void ${cgReleaseFnFor(obj.fty, cgRelKeyVal(obj))}(ptr ${obj.v})`)""",
        """    }
    cgOut(`  call void ${cgReleaseFnFor(obj.fty, cgRelKeyVal(obj))}(ptr ${obj.v})`)""",
    ),
    Canary(
        "computed-index-releases-the-container", "#312",
        "a computed index off an owning receiver releases the container "
        "it indexed",
        """    if cgIsRefcounted(obj.fty) == false { return out }
    if cgIsOwningRefcountedSource(childOf(e, 'obj')) == false { return out }""",
        """    if cgIsRefcounted(obj.fty) == false { return out }
    if true { return out }""",
    ),
    Canary(
        "map-text-value-asks-the-text-question", "#312",
        "a TEXT map value uses the text owning predicate, not the "
        "refcounted one",
        """bool func cgMapValOwns(vty:text, e:Node, v:Val) {
    if vty == 'text' { return cgOwnsText(e, v) }""",
        """bool func cgMapValOwns(vty:text, e:Node, v:Val) {""",
    ),
    Canary(
        "map-text-frees-the-old-buffer", "#312",
        "overwriting a text map entry frees what the key mapped to before",
        """            cgOut(`  call void @free(ptr ${old})`)
        } else {""",
        """        } else {""",
    ),
    Canary(
        "map-release-trampoline-element-fn", "#312",
        "a map's release trampoline frees a text value rather than "
        "releasing it as though it had a header",
        """    text releaseFn = cgElemReleaseFn(vty)""",
        """    text releaseFn = cgReleaseFnFor('struct', vty)""",
    ),
    Canary(
        "minted-base-field-release", "#312",
        "a field read off a MINTED computed index releases the base it "
        "was handed",
        """    if v.fresh { return true }
    if e == null { return false }
    return e.kind == 'Call'""",
        """    if e == null { return false }
    return e.kind == 'Call'""",
    ),
    Canary(
        "map-stack-values-released", "#312",
        "a FRAME-allocated map still releases its values before freeing "
        "the entries buffer",
        """        if cgElemOwnsSomething(parts[2]) {
            text tramp = cgMapReleaseTrampoline(parts[2])
            cgOut(`  call void @festina_map_for_each(ptr ${entV}, i64 ${nV}, ptr ${tramp})`)
        }""",
        "",
    ),
    Canary(
        "floordiv-floors", "#312",
        "Math.floorDiv rounds toward negative infinity, not toward zero",
        """        text adjust = cgTmp()
        cgOut(`  ${adjust} = and i1 ${rNonzero}, ${differ}`)""",
        """        text adjust = cgTmp()
        cgOut(`  ${adjust} = and i1 ${rNonzero}, false`)""",
    ),
    Canary(
        "float-literal-trailing-zeros", "#312",
        "a float literal's window is decided by its VALUE, not its "
        "spelling",
        """    while frac.length > 0 {
        if frac.charCodeAt(frac.length - 1) != 48 { break }
        frac = cgDropLast(frac)
    }""",
        """    while false {
        frac = cgDropLast(frac)
    }""",
    ),

    # --- decisions.md #313: the drivers, and the fixed point ----------
    Canary(
        "argv-is-an-ordinary-global", "#313",
        "argv is registered like any other arr[text] global, so reads "
        "of it resolve",
        """    G_SLOT['argv'] = '@argv'
    G_FTY['argv'] = 'arr'
    G_ETY['argv'] = 'text'""",
        """    G_FTY['argv'] = 'arr'
    G_ETY['argv'] = 'text'""",
    ),
    Canary(
        "close-exits-through-the-runtime", "#313",
        "close(code) goes through festina_program_exit, which runs a "
        "declared exit handler first",
        """        cgOut(`  call void @festina_program_exit(i64 ${cv.v})`)""",
        """        cgOut(`  call void @exit(i64 ${cv.v})`)""",
    ),
    Canary(
        "a-local-shadows-a-global-of-the-same-name", "#313",
        "a managed declaration INSIDE a function is local even when a "
        "global shares its name",
        """            bool isGlobalDecl = false
            if CG_IN_FUNC == false && CG_AT_TOPLEVEL {
                if G_SLOT[gname] != null { isGlobalDecl = true }
            }""",
        """            bool isGlobalDecl = false
            if G_SLOT[gname] != null { isGlobalDecl = true }""",
    ),
    # Three edits, because the property is now over-determined. The
    # port keeps its scope in GLOBAL maps, where Python keeps it in an
    # `Env` chained per body and passed down -- so Python cannot leak a
    # body's names by construction and the port has to unbind them, and
    # it now does so in three independent places:
    #
    #   1. `cgScopeRestore` unbinds everything a block bound, and a
    #      function body IS a block (block scope, #325).
    #   2. `cgFunc` saves and restores L_SLOT/L_FTY around cgFuncBody,
    #      for the nested-function early returns (#142).
    #   3. main resets them outright, which is what #313 actually fixed.
    #
    # Removing any ONE of the three -- or any two -- emits a
    # byte-identical compiler, so a single-edit canary here reports NOT
    # CAUGHT and reads like a corpus gap when it is nothing of the kind.
    # It was a single edit until block scope landed and quietly made it
    # vacuous; measured, not assumed. What is worth asserting is the
    # PROPERTY rather than any one of the three spellings of it, so the
    # canary removes all three and the corpus is asked whether a
    # compiler with no scope discipline at all would show.
    Canary(
        "main-gets-a-fresh-local-scope", "#313",
        "__festina_main does not inherit the locals of whichever "
        "function was emitted last",
        [
            ("""    map[text] mainSlot = {}
    map[text] mainFty = {}
    L_SLOT = mainSlot
    L_FTY = mainFty
""", ""),
            ("""    L_SLOT = wLSlot
    L_FTY = wLFty
""", """    wLSlot = L_SLOT
    wLFty = L_FTY
"""),
            ("""void func cgScopeRestore(mark:int) {
    while CG_SCOPE_NAMES.length > mark {""",
             """void func cgScopeRestore(mark:int) {
    while false {"""),
        ],
    ),
    Canary(
        "map-keys-is-not-map-values", "#313",
        "keys and values are different runtime calls with different "
        "element answers",
        """        if m == 'keys' {
            cgOut(`  call void @festina_map_keys(ptr ${kEnt}, i64 ${kCap}, ptr ${dst})`)
            return cgArrVal(dst, 'text')
        }""",
        """        if m == 'keys' {
            cgOut(`  call void @festina_map_keys(ptr ${kEnt}, i64 ${kCap}, ptr ${dst})`)
            return cgArrVal(dst, mv.ety)
        }""",
    ),
    Canary(
        "map-delete-releases-the-value", "#313",
        "deleting an entry whose VALUE owns something hands the runtime "
        "a release trampoline",
        """    if cgElemOwnsSomething(obj.ety) { delFn = cgMapReleaseTrampoline(obj.ety) }""",
        """    if false { delFn = cgMapReleaseTrampoline(obj.ety) }""",
    ),

    # --- decisions.md #314: the escape hatch, function values, try ----
    Canary(
        "free-nulls-the-binding", "#314",
        "`free` nulls the binding, which is what makes it composable "
        "with automatic reclamation",
        """        cgOut(`  store ptr null, ptr ${slot}`)
        return
    }
    cgOut(`  store ${lty} ${cgNullValue(fty)}, ptr ${slot}`)""",
        """        return
    }
    cgOut(`  store ${lty} ${cgNullValue(fty)}, ptr ${slot}`)""",
    ),
    Canary(
        "clear-is-not-free", "#314",
        "`clear` zeroes through the runtime's clearing flag; `free` "
        "does not",
        """            if zeroing { cgOut('  call void @festina_begin_clearing()') }""",
        """            if false { cgOut('  call void @festina_begin_clearing()') }""",
    ),
    Canary(
        "main-gets-escape-analysis", "#314",
        "a local declared in a nested block at the TOP level gets the "
        "same storage answer a function's does",
        """    CG_ESC = findEscapingNames(body)""",
        """    map[int] noEsc = {}
    CG_ESC = noEsc""",
    ),
    Canary(
        "function-name-is-a-value", "#314",
        "a bare reference to a function's name is its own global symbol",
        """                    Val fv = cgVal(`@${name}`, 'ptr', 'func')""",
        """                    Val fv = cgVal('null', 'ptr', 'func')""",
    ),
    Canary(
        "sort-trampoline-decodes-the-element", "#314",
        "the comparator trampoline decodes both raw slots as THIS "
        "element type",
        """    text elemLty = cgElemLty(ety)

    arr[text] saved = CUR""",
        """    text elemLty = 'i64'

    arr[text] saved = CUR""",
    ),
    Canary(
        "foreach-trampoline-reinterprets", "#314",
        "a map forEach trampoline reinterprets the raw i64 into the "
        "map's own value type",
        """    text v = cgMapFromI64('%raw', vlty)
    cgOut(`  call void ${cbName}(${vlty} ${v}, ptr %key)`)""",
        """    text v = '%raw'
    cgOut(`  call void ${cbName}(${vlty} ${v}, ptr %key)`)""",
    ),
    Canary(
        "try-pushes-the-catch-frame", "#314",
        "a try body registers its setjmp buffer as the top catch frame",
        """    cgOut(`  call void @festina_try_push(ptr ${bufp})`)""",
        """    cgOut(`  ; no try push for ${bufp}`)""",
    ),
    Canary(
        "try-frame-popped-on-every-exit", "#314",
        "every exit from a try body pops the runtime's catch frame",
        """        if CG_SKIP_TRY_POP == false { cgOut('  call void @festina_try_pop()') }""",
        """        if false { cgOut('  call void @festina_try_pop()') }""",
    ),
    Canary(
        "cleanup-stack-tracks-every-binding", "#314",
        "with a try in the program, every tracked binding is registered "
        "for unwinding as it is bound",
        """    if CG_HAS_TRY {
        cgOut(`  call void @festina_cleanup_push(ptr ${slot}, ptr ${cgUnwindFn(kind, ety)})`)
    }""",
        "",
    ),
    Canary(
        "throw-owns-an-aliased-message", "#314",
        "a thrown text that aliases a local is copied before the "
        "unwinding releases it",
        """        if cgOwnsText(ex, v) == false {
            text owned = cgTmp()
            cgOut(`  ${owned} = call ptr @festina_text_own(ptr ${val})`)
            val = owned
        }""",
        "",
    ),

    # --- decisions.md #315: nesting, timers, handles, regex ----------
    Canary(
        "nested-element-owns-a-reference", "#315",
        "a container element that is itself a container owns a whole "
        "reference, whatever it holds",
        # Anchored on the comment that follows rather than on the line
        # that used to, because decisions.md #318 put a handle-element
        # case between this check and the struct one -- and the bare
        # check now appears in cgElemIsRefcounted too, so it no longer
        # names one site on its own.
        """    if cgIsNestedElem(ety) { return true }
    // A refcounted HANDLE element""",
        """    // A refcounted HANDLE element""",
    ),
    Canary(
        "nested-element-releases-through-its-own-type", "#315",
        "a nested container element is released as the container it "
        "is, not freed as a buffer",
        """    if cgIsNestedElem(ety) { return cgReleaseFnFor(cgKeyFty(ety), cgKeyEty(ety)) }""",
        """    if false { return cgReleaseFnFor(cgKeyFty(ety), cgKeyEty(ety)) }""",
    ),
    Canary(
        "timers-need-a-loop-to-fire-in", "#315",
        "a program that schedules a callback gets a blocking loop for "
        "it to fire in",
        """    else if CG_USES_TIMERS || CG_USES_ASYNC_IO || CG_USES_THREADS { cgOut('  call void @festina_run_timer_loop()') }""",
        """    else if CG_USES_ASYNC_IO || CG_USES_THREADS { cgOut('  call void @festina_run_timer_loop()') }""",
    ),
    Canary(
        "clearing-alone-schedules-nothing", "#315",
        "only setTimeout/setInterval make a program use timers",
        """        CG_USES_TIMERS = true
        text tfn = 'festina_set_timeout'""",
        """        text tfn = 'festina_set_timeout'""",
    ),
    Canary(
        "save-with-no-path-uses-the-handles-own", "#315",
        "a no-argument save passes a NULL path, which is how the "
        "runtime is told to use the handle's own",
        """        text pathV = 'null'""",
        """        text pathV = cgStringConst('')""",
    ),
    Canary(
        "regex-literal-is-compiled-once", "#315",
        "a /pattern/ literal's compilation is cached per call site",
        """    cgOut(`  br i1 ${isNull}, label %${compileL}, label %${doneL}`)""",
        """    cgOut(`  br i1 true, label %${compileL}, label %${doneL}`)""",
    ),
    Canary(
        "cached-regex-is-marked-immortal", "#315",
        "a cached compilation is marked, so `free` on a binding "
        "aliasing it cannot free what every later execution shares",
        """    cgOut(`  call void @festina_regex_mark_cached(ptr ${compiled})`)""",
        "",
    ),
    Canary(
        "regex-literal-is-fresh-for-a-store", "#315",
        "an immortal compilation needs no retain when it is stored",
        """    Val rv = cgVal(out, 'ptr', 'regex')
    rv.fresh = true""",
        """    Val rv = cgVal(out, 'ptr', 'regex')""",
    ),
    Canary(
        "regex-split-takes-its-arguments-the-other-way", "#315",
        "a regex split passes the pattern first and the subject "
        "second, unlike a text split",
        """            cgOut(`  ${out} = call ptr @festina_regex_split(ptr ${sep.v}, ptr ${r.v})`)""",
        """            cgOut(`  ${out} = call ptr @festina_regex_split(ptr ${r.v}, ptr ${sep.v})`)""",
    ),
    Canary(
        "dynamic-regex-is-memoized-not-cached", "#315",
        "regex(p, f) is memoized per call site by the runtime, which "
        "recompiles when the pattern actually changes",
        """        cgOut(`  ${rout} = call ptr @festina_regex_compile_memo(ptr ${pv.v}, ptr ${flagsV}, ptr ${memo})`)""",
        """        cgOut(`  ${rout} = call ptr @festina_regex_compile(ptr ${pv.v}, ptr ${flagsV})`)""",
    ),

    # --- decisions.md #316: JSON, ternaries, and unwind guards -------
    Canary(
        "json-struct-skips-unknown-keys", "#316",
        "an unrecognized JSON key's value is SKIPPED, not refused",
        """    cgOut('  call void @festina_json_skip_field_value(ptr %cursor)')""",
        "",
    ),
    Canary(
        "json-duplicate-key-frees-the-old", "#316",
        "a duplicate JSON key overwrites, and gives back what the "
        "earlier one stored",
        """        if oldVal != '' {
            if ffty == 'text' {
                cgOut(`  call void @free(ptr ${oldVal})`)
            } else {
                cgOut(`  call void ${cgReleaseFnFor(ffty, fkey)}(ptr ${oldVal})`)
            }
        }""",
        "",
    ),
    Canary(
        "json-builder-registers-what-it-holds", "#316",
        "a half-built JSON value is released if a deeper parse throws",
        """    cgOut(`  call void @festina_cleanup_push(ptr ${out}, ptr ${cgReleaseFnFor('struct', sname)})`)""",
        "",
    ),
    Canary(
        "json-map-takes-every-key", "#316",
        "a map target takes arbitrary keys, where a struct target "
        "matches a fixed set",
        """    if cgKeyFty(key) == 'map' { return cgFromJsonMapFn(cgKeyEty(key)) }""",
        "",
    ),
    Canary(
        "json-render-caps-its-depth", "#316",
        "a cyclic value renders as null at the cap rather than "
        "overflowing the stack",
        """    cgOut(`  ${toodeep} = icmp sgt i64 %depth, 32`)""",
        """    cgOut(`  ${toodeep} = icmp sgt i64 %depth, -1`)""",
    ),
    Canary(
        "json-render-skips-tombstones", "#316",
        "a map renders its live entries only, walking buckets by "
        "capacity",
        """        cgOut(`  ${skip} = or i1 ${isNull2}, ${isTomb}`)""",
        """        cgOut(`  ${skip} = and i1 ${isNull2}, ${isTomb}`)""",
    ),
    Canary(
        "ternary-arm-is-owned-before-the-phi", "#316",
        "claude.md #173: a ternary ARM is normalized to something "
        "genuinely owned, rather than the whole ternary read as aliasing",
        """        a = cgOwnTernaryBranch(a, consN)""",
        """        a = a""",
    ),
    Canary(
        "ternary-is-an-owning-source", "#316",
        "and the normalized result is then owning, so the caller does "
        "not claim it a second time",
        """    if e.kind == 'Ternary' { return true }
    return e.kind == 'Call'
}""",
        """    return e.kind == 'Call'
}""",
    ),
    Canary(
        "null-ternary-arm-takes-the-other-side", "#316",
        "a null arm has no type of its own, so the other arm is "
        "emitted FIRST when the consequent is the null one",
        """    if consNull && altNull == false {""",
        """    if false {""",
    ),
    Canary(
        "call-arguments-survive-a-throwing-callee", "#316",
        "claude.md #236: a call site's own fresh argument temporaries "
        "are released by the unwinding when the callee never returns",
        """    int guarded = cgGuardCallArgs(args, argVals)
    if retF == 'void' {""",
        """    int guarded = 0
    if retF == 'void' {""",
    ),
    Canary(
        "parameter-uid-order-is-interleaved", "#316",
        "an escaping parameter's binding may generate a function, and "
        "it is numbered between this parameter's slot and the next",
        """        text slot = `%${pnames[q]}.${cgUid()}`
        cgOut(`  ${slot} = alloca ${pltys[q]}`)""",
        """        text slot = `%${pnames[q]}.${cgUid()}${cgUid()}`
        cgOut(`  ${slot} = alloca ${pltys[q]}`)""",
    ),
    Canary(
        "time-and-file-builtins-free-their-paths", "#316",
        "formatTime/mkdir/ls keep no pointer past the call, so a "
        "temporary path is the caller's to free",
        """        int fk = 0
        while fk < fvals.length {
            cgFreeTextTemp(fargs[fk], fvals[fk])
            fk++
        }""",
        "",
    ),

    Canary(
        "table-decl-syncs-its-schema", "#317",
        "a declared table becomes a festina_sync_table call in main's "
        "own prologue, before __festina_main runs anything",
        """    cgSyncTables()
""",
        "",
    ),
    Canary(
        "table-columns-before-the-table-name", "#317",
        "a sync call interns its column names and types BEFORE the "
        "table's own name, because constants are numbered",
        [
            # The name asked for FIRST, so it takes the lower number and
            # every column shifts up by one. Nothing about the call
            # itself changes -- only which constant each `ptr` names.
            #
            # The first spelling of this canary moved where the name was
            # interned but left it after the columns either way, so it
            # broke nothing and reported NOT CAUGHT -- which read as a
            # corpus failure and was a canary failure. Interning is
            # memoized, so what decides the number is the FIRST ask.
            ("""        cgTableArrays(tn)
        text tnConst = cgStringConst(tn)""",
             """        text tnConst = cgStringConst(tn)
        cgTableArrays(tn)"""),
        ],
    ),
    Canary(
        "declaration-binds-after-its-initializer", "#317",
        "a scalar declaration's name becomes visible only after its own "
        "initializer is emitted",
        [
            ("""        if isLocalDecl { cgBindLocalDecl(s, name, fty, localSlot) }
        if fty == 'text' {""",
             """        if fty == 'text' {"""),
            ("""            freshLocal = true
        }""",
             """            freshLocal = true
            cgBindLocalDecl(s, name, fty, localSlot)
        }"""),
        ],
    ),

    Canary(
        "handle-element-owns-a-reference", "#318",
        "a blob/regex ELEMENT holds a whole reference, so its container "
        "needs a generated cascade rather than the plain release",
        """    if cgIsRefcounted(ety) { return true }
    // A ROW element owns whatever its own text/blob columns hold""",
        """    // A ROW element owns whatever its own text/blob columns hold""",
    ),
    Canary(
        "handle-element-gets-its-own-destructor", "#318",
        "a handle element is released through its own type's destructor, "
        "never plain free",
        """    if cgIsRefcounted(ety) { return cgReleaseFn(ety) }
    return '@free'""",
        """    return '@free'""",
    ),
    Canary(
        "row-release-frees-its-text-columns", "#318",
        "a sqlite row frees each of its own text columns before the "
        "allocation they hang off",
        """        if ctypes[i] == 'text' { colFree = '@free' }""",
        """        if ctypes[i] == 'text' { colFree = '' }""",
    ),
    Canary(
        "row-is-freed-from-its-base", "#318",
        "a row's allocation starts one i64 before the payload every "
        "column offset is measured from",
        """    cgOut(`  ${base} = getelementptr i8, ptr %row, i64 -8`)
    cgOut(`  call void @free(ptr ${base})`)
    cgOut(`  br label %${nullL}`)""",
        """    cgOut(`  ${base} = getelementptr i8, ptr %row, i64 0`)
    cgOut(`  call void @free(ptr ${base})`)
    cgOut(`  br label %${nullL}`)""",
    ),
    Canary(
        "collected-rows-need-no-repacking", "#318",
        "the runtime's own row-pointer buffer IS an arr[T] data pointer, "
        "so a fresh header is built around it as it stands",
        [
            # Copy the buffer into a second allocation instead of
            # adopting it. Both halves are needed: the header still has
            # to be filled in, or the result is not an array at all.
            ("""    cgTableArrays(tname)
    int n = TBL_NCOLS[tname]""",
             """    cgTableArrays(tname)
    int n = 0"""),
        ],
    ),
    Canary(
        "map-set-releases-the-old-value-by-its-own-type", "#318",
        "the value a map key used to hold is released through the "
        "element dispatch, not the struct one",
        """            oldFn = cgElemReleaseFn(vty)""",
        """            oldFn = cgReleaseFnFor('struct', vty)""",
    ),
    Canary(
        "database-url-runs-before-the-open", "#318",
        "the DatabaseURL directive is evaluated in main's prologue, "
        "ahead of festina_db_open, not where it was written",
        """    text url = ''
    if CG_DB_URL != null {""",
        """    text url = ''
    if false {""",
    ),

    Canary(
        "canvas-op-argument-types-travel-with-the-name", "#319",
        "a canvas operation's argument LLVM types come from its own "
        "table entry, not from an assumption that everything is i64",
        [
            # rotate, scale and fillAlpha take doubles. Assuming i64 for
            # every argument has to leave the call EMITTED and merely
            # mistyped, or it proves nothing about the corpus -- so the
            # port's own type check is dropped in the same breath.
            # Reading the types off the wrong half of the table entry
            # was the first spelling and made the port REFUSE instead,
            # which is a ratchet detection and a weaker claim.
            ("""            if cv.lty != argLtys[cq] {""",
             """            if false {"""),
            ("""            cjoined = cjoined + `${argLtys[cq]} ${cv.v}`""",
             """            cjoined = cjoined + `i64 ${cv.v}`"""),
        ],
    ),
    Canary(
        "drawing-opens-no-window", "#319",
        "painting the offscreen canvas registers the image decoder but "
        "never opens a window",
        """    if CG_USES_GRAPHICS_CODE {
        cgOut('  call void @festina_set_image_decoder(ptr @festina_image_from_bytes)')
    }""",
        "",
    ),
    Canary(
        "canvas-path-arguments-are-freed", "#319",
        "Cairo reads a PNG path inline and keeps no pointer, so a "
        "computed one is the caller's to free",
        """        cgFreeTextTemp(vargs[0], pv)
        return cgVal(sout, 'i8', 'bool')""",
        """        return cgVal(sout, 'i8', 'bool')""",
    ),

    Canary(
        "spliced-in-elements-take-their-own-reference", "#320",
        "a splice-insert's newly written range retains (or copies) "
        "every element, because the source array keeps managing its own",
        """    if cgElemOwnsSomething(ety) == false { return }""",
        """    if true { return }""",
    ),
    Canary(
        "splice-reloads-the-data-pointer", "#320",
        "splice-insert may realloc, so the array's data pointer is "
        "read again AFTER the call rather than reused from before it",
        [
            # Reuse the pointer read for the INSERT argument instead.
            # Same value in the shrinking case, stale in the growing
            # one -- which is exactly the bug this ordering prevents.
            ("""        text nowP = cgTmp()
        cgOut(`  ${nowP} = getelementptr %struct._FestinaArray, ptr ${obj.v}, i32 0, i32 1`)
        text nowV = cgTmp()
        cgOut(`  ${nowV} = load ptr, ptr ${nowP}`)
        cgSpliceOwnRange(nowV, spElemLty, obj.ety, spStart.v, insLen)""",
             """        cgSpliceOwnRange(insData, spElemLty, obj.ety, spStart.v, insLen)"""),
        ],
    ),
    Canary(
        "ascii-length-is-a-header-load", "#320",
        "an ascii carries its length in its own header, so .length is "
        "a load at payload-16 and not a call",
        """        cgOut(`  ${lenP} = getelementptr i8, ptr ${obj.v}, i64 -16`)
        text out = cgTmp()
        cgOut(`  ${out} = load i64, ptr ${lenP}`)""",
        """        text out = cgTmp()
        cgOut(`  ${out} = call i64 @festina_ascii_length(ptr ${obj.v})`)""",
    ),
    Canary(
        "ascii-char-code-is-inlined", "#320",
        "charCodeAt on an ascii is emitted inline and branchless, with "
        "no call at all",
        """    cgOut(`  ${out} = select i1 ${oor}, i64 ${cgNullValue('int')}, i64 ${code}`)""",
        """    cgOut(`  ${out} = add i64 ${code}, 0`)""",
    ),
    Canary(
        "ascii-literal-is-immortal", "#320",
        "an ascii literal carries the immortal refcount sentinel, so "
        "retain, release and free on one are all no-ops",
        r"""    CG_EXTRA.push(`${name} = private unnamed_addr constant ${ty} {i64 ${bytes - 1}, i64 -1, [${bytes} x i8] c"${cgCEscape(v)}\\00"}`)""",
        r"""    CG_EXTRA.push(`${name} = private unnamed_addr constant ${ty} {i64 ${bytes - 1}, i64 1, [${bytes} x i8] c"${cgCEscape(v)}\\00"}`)""",
    ),
    Canary(
        "ascii-alias-takes-its-own-reference", "#326",
        "an ascii local aliasing another binding claims a reference of its own",
        """                bool bOwning = cgIsOwningRefcountedSource(binit)
                if bv.fresh { bOwning = true }""",
        """                bool bOwning = true""",
    ),

    Canary(
        "generated-fn-placement", "#307",
        "a generated cascade lands BEFORE the function whose body asked for it",
        [
            # Stream straight into the shared buffer, so anything
            # generated mid-body lands in the middle of the definition
            # that triggered it. Both halves are needed: aliasing the
            # buffer WITHOUT dropping the copy-back loop makes the loop
            # push the buffer onto itself and the compiler is killed by
            # the OOM reaper rather than emitting anything wrong.
            ("""    arr[text] body = []
    CUR = body""",
             """    arr[text] body = CG_FUNCS
    CUR = body"""),
            ("""    int b = 0
    while b < body.length {
        CG_FUNCS.push(body[b])
        b++
    }
""", ""),
        ],
    ),

    # --- decisions.md #323: threads ----------------------------------
    Canary(
        "if-both-arms-terminate", "#323",
        "an `if` whose arms both end in a terminator emits no end block",
        """    if thenTerm && elseTerm {
        CG_TERM = true
        return
    }
""",
        "",
    ),
    Canary(
        "thread-state-initializers-run", "#323",
        "a thread's state initializers run inside its own on_load",
        """                cgOut(`  store ${cgLtyOf(fty)} ${v.v}, ptr ${ref}`)""",
        "",
    ),
    Canary(
        "thread-state-shadows-a-global", "#323",
        "a thread's private state is a scope of its own, not more globals",
        """            if G_SLOT[sym] != null {
                T_SLOT[vn] = G_SLOT[sym]""",
        """            if G_SLOT[sym] == null {
                T_SLOT[vn] = G_SLOT[sym]""",
    ),
    Canary(
        "thread-send-names-the-calling-thread", "#323",
        "a send from inside a thread passes THAT thread's handle as the sender",
        """    if CG_THREAD_HANDLE != '' {
        cgOut(`  ${sender} = load ptr, ptr ${CG_THREAD_HANDLE}`)
    } else {
        cgOut(`  ${sender} = call ptr @festina_thread_get_main_handle()`)
    }""",
        """    cgOut(`  ${sender} = call ptr @festina_thread_get_main_handle()`)""",
    ),
    Canary(
        "thread-always-gets-three-adapters", "#323",
        "an undeclared handler still gets a real, never-called adapter",
        """        cgThreadStubAdapter(`@__festina_thread_${tname}_on_exit`, 'i64 %arg.code')""",
        "",
    ),
    Canary(
        "thread-handle-stored-before-spawn", "#323",
        "a thread's handle global is stored before the thread is spawned",
        [
            ("""            cgOut(`  store ptr %__thread_${tn}, ptr @__festina_thread_${tn}_handle`)""",
             """            cgOut(`  store ptr null, ptr @__festina_thread_${tn}_handle`)"""),
        ],
    ),
    Canary(
        "thread-text-payload-is-cloned", "#323",
        "a text payload crosses the boundary as its own buffer, never shared",
        """    if fty == 'text' {
        text owned = cgTmp()
        cgOut(`  ${owned} = call ptr @festina_text_own(ptr ${val})`)
        return owned
    }""",
        """    if fty == 'text' {
        return val
    }""",
    ),
    Canary(
        "bare-postmessage-loads-its-handle-first", "#323",
        "a bare postMessage loads its own handle before boxing the payload",
        [
            ("""    text handle = cgTmp()
    cgOut(`  ${handle} = load ptr, ptr ${CG_THREAD_HANDLE}`)
    text box = cgThreadBox(v.v, CG_MAIN_MSG_IN)""",
             """    text box = cgThreadBox(v.v, CG_MAIN_MSG_IN)
    text handle = cgTmp()
    cgOut(`  ${handle} = load ptr, ptr ${CG_THREAD_HANDLE}`)"""),
        ],
    ),
    Canary(
        "exec-releases-an-owning-argument", "#323",
        "exec() releases the argument array when the expression owned it",
        """        if cgIsRefcounted(ev.fty) && cgIsOwningRefcountedSource(eargs[0]) {""",
        """        if cgIsRefcounted(ev.fty) && false {""",
    ),

    # --- decisions.md #324: reply and callback ------------------------
    Canary(
        "reply-carries-its-own-release", "#324",
        "a reply records how to free its payload, for when there is nothing to dispatch to",
        """    cgOut(`  call void @festina_thread_reply(ptr ${selfH}, ptr ${dest.v}, ptr ${box}, ptr ${cgThreadReleaseFn(desc)})`)""",
        """    cgOut(`  call void @festina_thread_reply(ptr ${selfH}, ptr ${dest.v}, ptr ${box}, ptr @festina_noop_release)`)""",
    ),
    Canary(
        "reply-names-where-it-came-from", "#324",
        "a reply made inside a thread names THAT thread as its sender, not main",
        """    text selfH = cgTmp()
    if CG_THREAD_HANDLE != '' {
        cgOut(`  ${selfH} = load ptr, ptr ${CG_THREAD_HANDLE}`)
    } else {
        cgOut(`  ${selfH} = call ptr @festina_thread_get_main_handle()`)
    }
    // claude.md #218: the release function travels WITH the reply,""",
        """    text selfH = cgTmp()
    cgOut(`  ${selfH} = call ptr @festina_thread_get_main_handle()`)
    // claude.md #218: the release function travels WITH the reply,""",
    ),
    Canary(
        "callback-is-registered-before-the-send", "#324",
        "a pending callback is registered before the message that will answer it is posted",
        [
            ("""    cgOut(`  call void @festina_thread_register_callback(ptr ${selfH}, i64 ${txn}, ptr ${tramp}, ptr ${fn.v}, i8 ${onMain})`)
    if bare {
        CG_USES_ASYNC_IO = true
        cgBarePostMessageTxn(pmArgs, txn)
    } else {
        cgNamedPostMessageTxn(sendDesc, handle, pmArgs, txn)
        if CG_STUCK { return }
        cgThreadCloseBounds(endL)
    }""",
             """    if bare {
        CG_USES_ASYNC_IO = true
        cgBarePostMessageTxn(pmArgs, txn)
    } else {
        cgNamedPostMessageTxn(sendDesc, handle, pmArgs, txn)
        if CG_STUCK { return }
        cgThreadCloseBounds(endL)
    }
    cgOut(`  call void @festina_thread_register_callback(ptr ${selfH}, i64 ${txn}, ptr ${tramp}, ptr ${fn.v}, i8 ${onMain})`)"""),
        ],
    ),
    Canary(
        "callback-on-a-bare-send-fires-on-main", "#324",
        "only the bare send asks for its reply to be dispatched on main's own thread",
        """    text onMain = '0'
    if bare { onMain = '1' }""",
        """    text onMain = '0'""",
    ),
    Canary(
        "reply-trampoline-frees-the-payload", "#324",
        "a reply payload is freed by the trampoline, since nothing else ever frees one",
        """    cgOut(`  call void ${cgThreadReleaseFn(desc)}(ptr %payload)`)""",
        "",
    ),
    Canary(
        "a-bare-send-needs-the-async-io-hooks", "#324",
        "a program using the bare callback form registers the async-io hooks",
        """    if bare {
        CG_USES_ASYNC_IO = true""",
        """    if bare {""",
    ),
    Canary(
        "the-send-carries-its-transaction-id", "#324",
        "a callback's transaction id travels with the message it will answer",
        """    cgOut(`  call void @festina_thread_post_outbound(ptr ${handle}, ptr ${box}, i64 ${txn})`)""",
        """    cgOut(`  call void @festina_thread_post_outbound(ptr ${handle}, ptr ${box}, i64 0)`)""",
    ),
    Canary(
        "colour-equality-is-one-integer-compare", "#324",
        "two colours compare as the packed integers they are",
        """        if l.fty == 'color' { lOk = true }
        if r.fty == 'color' { rOk = true }""",
        "",
    ),

    # --- decisions.md #325: thread pools ------------------------------
    Canary(
        "pool-instances-are-independent", "#325",
        "a pool is N independent threads, each with its own state and queues",
        """    int i = 0
    while i < n {
        cgThreadDeclNamed(d, `${base}$${i}`)
        if CG_STUCK { return }
        i++
    }""",
        """    int i = 0
    while i < n {
        cgThreadDeclNamed(d, `${base}$0`)
        if CG_STUCK { return }
        i = n
    }""",
    ),
    Canary(
        "pool-index-is-bounds-checked", "#325",
        "an out-of-range pool index is a silent no-op rather than a wild load",
        """        CG_TGT_OOB = CG_BLOCK
        text okL = cgLabel('pool.inrange')
        text endL = cgLabel('pool.end')
        cgOut(`  br i1 ${inRange}, label %${okL}, label %${endL}`)
        cgBlockLabel(okL)""",
        """        CG_TGT_OOB = CG_BLOCK
        text okL = cgLabel('pool.inrange')
        text endL = cgLabel('pool.end')
        cgOut(`  br label %${okL}`)
        cgBlockLabel(okL)""",
    ),
    Canary(
        "pool-index-takes-two-loads", "#325",
        "a pool slot names a handle global, which then names the handle itself",
        """        text hg = cgTmp()
        cgOut(`  ${hg} = load ptr, ptr ${slot}`)
        text h2 = cgTmp()
        cgOut(`  ${h2} = load ptr, ptr ${hg}`)""",
        """        text hg = cgTmp()
        cgOut(`  ${hg} = load ptr, ptr ${slot}`)
        text h2 = hg""",
    ),
    Canary(
        "bare-pool-send-round-robins", "#325",
        "a bare pool send moves its starting point so an idle pool spreads load",
        """        text rrOld = cgTmp()
        cgOut(`  ${rrOld} = atomicrmw add ptr @__festina_thread_pool_${rname}_rr, i64 1 monotonic`)
        text start = cgTmp()
        cgOut(`  ${start} = urem i64 ${rrOld}, ${n}`)""",
        """        text start = '0'""",
    ),
    Canary(
        "kill-and-drain-are-different-calls", "#325",
        "kill stops a worker where drain waits for its queue to empty",
        """        cgOut(`  call void @festina_thread_wait_drained(ptr ${handle})`)""",
        """        cgOut(`  call void @festina_thread_kill(ptr ${handle})`)""",
    ),
    Canary(
        "a-missing-pool-instance-is-not-alive", "#325",
        "isAlive answers false for an index that names no instance",
        """    cgOut(`  ${phi} = phi i8 [ ${out}, %${pred} ], [ 0, %${oob} ]`)""",
        """    cgOut(`  ${phi} = phi i8 [ ${out}, %${pred} ], [ 1, %${oob} ]`)""",
    ),

    # --- decisions.md #327: bitwise operators and hex literals --------
    Canary(
        "bitwise-ops-are-distinct-instructions", "#327",
        "each binary bitwise operator emits its own instruction",
        """        if op == '|' { bins = 'or' }
        if op == '^' { bins = 'xor' }""",
        """        if op == '|' { bins = 'and' }
        if op == '^' { bins = 'and' }""",
    ),
    Canary(
        "bitwise-not-is-xor-against-all-ones", "#327",
        "the unary complement is xor -1, which is what ~x means for two's complement",
        """        cgOut(`  ${nout} = xor i64 ${v.v}, -1`)""",
        """        cgOut(`  ${nout} = sub i64 0, ${v.v}`)""",
    ),
    Canary(
        "right-shift-is-arithmetic", "#327",
        "`>>` preserves the sign bit, because int is signed",
        """    text ins = 'shl'
    if op == '>>' { ins = 'ashr' }""",
        """    text ins = 'shl'
    if op == '>>' { ins = 'lshr' }""",
    ),
    Canary(
        "a-literal-shift-count-skips-the-bounds-check", "#327",
        "a shift by a literal count in range is one instruction and no branch",
        """    if cgIsSmallIntLiteral(rn) {""",
        """    if false {""",
    ),
    Canary(
        "an-out-of-range-shift-count-answers-null", "#327",
        "a shift count outside 0 to 63 yields null rather than whatever the instruction does",
        """    cgOut(`  ${inRange} = icmp ult i64 ${rv}, 64`)""",
        """    cgOut(`  ${inRange} = icmp ult i64 ${rv}, 1024`)""",
    ),
    Canary(
        "a-hex-literal-is-base-sixteen", "#327",
        "a hexadecimal literal is read in base sixteen, and as one token",
        """        acc = acc * 16 + d""",
        """        acc = acc * 10 + d""",
        path=os.path.join(REPO_ROOT, "bootstrap", "lexer.f"),
    ),
    Canary(
        "bitwise-binds-tighter-than-comparison", "#327",
        "the binary bitwise operators group inside the comparisons, unlike C",
        [
            ("""Node func parseRelational() {
    Node left = parseBitOr()""",
             """Node func parseRelational() {
    Node left = parseAdditive()"""),
            ("""        Node right = parseBitOr()
        left = mkBin(op.val, left, right, op)
    }
    return left
}

// claude.md #327: the three binary bitwise levels""",
             """        Node right = parseAdditive()
        left = mkBin(op.val, left, right, op)
    }
    return left
}

// claude.md #327: the three binary bitwise levels"""),
            ("""Node func parseShift() {
    Node left = parseAdditive()""",
             """Node func parseShift() {
    Node left = parseRelational()"""),
        ],
        path=os.path.join(REPO_ROOT, "bootstrap", "parser.f"),
    ),

    # --- decisions.md #328: the text methods ---------------------------
    Canary(
        "a-text-method-frees-nothing-before-its-own-call", "#328",
        "a text method's receiver is still live when the call reads it",
        # The order of the two frees AFTER the call is not meaningful --
        # both happen once the call has returned, so swapping them
        # changes nothing and a canary that swapped them would be
        # measuring nothing. What matters is that neither free is
        # emitted BEFORE the call, which is what this moves.
        """        text mout = cgTmp()
        cgOut(`  ${mout} = call ${spec[1]} @${spec[0]}(ptr ${r.v}${joined})`)""",
        """        cgFreeTextTemp(recv, r)
        text mout = cgTmp()
        cgOut(`  ${mout} = call ${spec[1]} @${spec[0]}(ptr ${r.v}${joined})`)""",
    ),
    Canary(
        "index-of-takes-its-optional-start", "#328",
        "indexOf's second argument is passed when written and defaulted when not",
        """                joined = joined + `, ${kind} 0`""",
        """                joined = joined + `, ${kind} 1`""",
    ),
    Canary(
        "each-text-method-calls-its-own-runtime-function", "#328",
        "each text method is a distinct runtime call, not one standing in for another",
        """    'toLowerCase': 'festina_text_to_lower|ptr||text',
    'toUpperCase': 'festina_text_to_upper|ptr||text',""",
        """    'toLowerCase': 'festina_text_to_lower|ptr||text',
    'toUpperCase': 'festina_text_to_lower|ptr||text',""",
    ),
    # claude.md #332: `weak` fields. Six mechanisms, and the first two
    # are the ones worth separating -- skipping a weak edge in the TYPE
    # walk and skipping it in the generated TRAVERSAL are different code
    # paths, and an implementation can have either one alone. Getting
    # only the first one produced a compiler that measured exactly as
    # slow as before.
    Canary(
        "weak-edge-is-not-a-cycle-type-edge", "#332",
        "a weak field does not count when deciding whether a type can "
        "form a cycle, so a weak-only back edge generates no detector",
        """        if SF_WEAK[fk] == null {
            if f == 'struct' || f == 'arr' || f == 'map' {
                out.push(cgTypeKey(f, cgFieldEty(fk)))
            }
        }""",
        """        if true {
            if f == 'struct' || f == 'arr' || f == 'map' {
                out.push(cgTypeKey(f, cgFieldEty(fk)))
            }
        }""",
    ),
    Canary(
        "weak-edge-is-not-walked-by-a-trial", "#332",
        "a trial deletion does not traverse a weak field, so a release "
        "walks its own subtree rather than the whole document",
        """        if SF_WEAK[fk] == null {
            if f == 'struct' || f == 'arr' || f == 'map' {
                text ck = cgTypeKey(f, cgFieldEty(fk))
                if cgIsCyclic(ck) { out.push(`${SF_IDX[fk]}|${ck}`) }
            }
        }""",
        """        if true {
            if f == 'struct' || f == 'arr' || f == 'map' {
                text ck = cgTypeKey(f, cgFieldEty(fk))
                if cgIsCyclic(ck) { out.push(`${SF_IDX[fk]}|${ck}`) }
            }
        }""",
    ),
    Canary(
        "weak-read-is-checked", "#332",
        "reading a weak field upgrades through its block rather than "
        "loading the slot, which is what lets it answer null",
        """        cgOut(`  ${wout} = call ptr @festina_weak_get(ptr ${wblk})`)""",
        """        cgOut(`  ${wout} = load ptr, ptr ${wblk}`)""",
    ),
    Canary(
        "weak-store-takes-no-reference", "#332",
        "storing to a weak field stores the block and retains nothing",
        """            cgOut(`  ${wblk} = call ptr @festina_weak_ref(ptr ${wv.v})`)""",
        """            cgOut(`  call void @festina_retain(ptr ${wv.v})`)
            text wblk2 = wblk
            cgOut(`  ${wblk} = call ptr @festina_weak_ref(ptr ${wv.v})`)""",
    ),
    Canary(
        "weak-field-release-drops-only-its-block", "#332",
        "freeing a struct drops its weak field's block and leaves the "
        "target's own lifetime alone",
        """            cgOut(`  call void @festina_weak_drop(ptr ${wfv})`)""",
        """            cgOut(`  call void @festina_release(ptr ${wfv})`)""",
    ),
    Canary(
        "a-weak-target-tells-its-blocks-it-died", "#332",
        "a struct some weak field points at notifies its blocks on free, "
        "which is the whole of the no-dangling-read guarantee",
        """    if SF_WEAK_TARGET[sname] != null {
        cgOut('  call void @festina_weak_died(ptr %payload)')
    }""",
        """    if false {
        cgOut('  call void @festina_weak_died(ptr %payload)')
    }""",
    ),
    # claude.md #333: the terminal-read rule. Three canaries because the
    # rule has three independent halves and breaking any one of them
    # produces a different wrong compiler: creating on a terminal read
    # (the original bug), NOT creating on a receiver read (which would
    # fault reaching through an untouched field), and applying it to
    # containers (which would hand a program a null array).
    Canary(
        "a-terminal-field-read-does-not-create", "#333",
        "reading a struct field without reaching through it answers "
        "what the field holds rather than creating a value",
        """    if CG_RECV_CTX == false {
        if fp.fty == 'struct' {""",
        """    if false {
        if fp.fty == 'struct' {""",
    ),
    Canary(
        "a-receiver-field-read-still-creates", "#333",
        "a field reached THROUGH is still created, which is the whole "
        "reason auto-vivification exists",
        """    bool savedRecv = CG_RECV_CTX
    CG_RECV_CTX = true""",
        """    bool savedRecv = CG_RECV_CTX
    CG_RECV_CTX = false""",
    ),
    Canary(
        "container-fields-are-not-covered-by-the-null-rule", "#333",
        "an arr[T]/map[T] field's zero value is an empty container, not "
        "an absent one, so it is still created on a terminal read",
        """        if fp.fty == 'struct' {
            text sread = cgTmp()""",
        """        if fp.fty == 'struct' || fp.fty == 'arr' || fp.fty == 'map' {
            text sread = cgTmp()""",
    ),
]
BY_NAME = {c.name: c for c in CANARIES}


# ---------------------------------------------------------------------
# Running one.

class Baseline:
    """What the unbroken port reproduces, computed once.

    Holds the EXPECTED dump of every matching file alongside the file
    list. Without that, each canary would re-run the Python code
    generator over every matching file -- twenty canaries times
    twenty-odd files is four hundred compilations of programs whose
    answer cannot have changed, since nothing here touches
    festina/codegen.py. Caching them turned the suite from two and a
    half minutes into well under one.
    """

    def __init__(self, paths, expected, lines):
        # Cheapest FIRST, because a canary stops as soon as it has
        # enough witnesses (see WITNESSES_WANTED). The corpus is no
        # longer a flat set of small programs: `bootstrap/codegen.f`
        # alone is 58,000 IR lines, and re-dumping it for every one of
        # fifty-five canaries turned the suite from about a minute into
        # well over an hour. Sorting by expected size means the usual
        # case -- a mechanism a `cases/` file was written for -- is
        # answered in a few hundred milliseconds, and the whole corpus
        # is still scanned whenever the answer is not found early.
        self.paths = sorted(paths, key=lambda p: len(expected[p]))
        self.expected = expected      # {path: the Python side's own dump}
        self.lines = lines
        self.total_lines = {p: max(0, len(expected[p]) - irdiff.SHARED_PREAMBLE_LINES)
                            for p in paths}


def baseline(binary=None, paths=None):
    """A Baseline for the port as it stands.

    Only a file that matches today can stop matching, so this set is
    the entire surface a canary can act on.
    """
    paths = paths or corpus()
    if binary is None:
        raise ValueError("baseline needs a built compiler")
    matching = []
    expected = {}
    lines = 0
    for path in paths:
        want = irdump.dump_file(irdiff.relative(path))
        got = irdiff.festina_dump(binary, path)
        if got != want:
            continue
        if len(want) == 1 and want[0].startswith("SEMERR"):
            continue                  # rejected by both, not a match
        matching.append(path)
        expected[path] = want
        lines += max(0, len(want) - irdiff.SHARED_PREAMBLE_LINES)
    return Baseline(matching, expected, lines)


def build_patched(canary, out_dir):
    """Build a compiler from the broken source.

    **The repository is never written to.** The whole of `bootstrap/`
    is copied into a scratch directory and the patch applied to the
    COPY -- `bootstrap/codegen.f` imports its siblings by relative
    path, so a copy of the directory resolves exactly as the original
    does (verified: a build from a copied tree emits byte-identical IR).

    Patching in place and restoring in a `finally` would work until the
    first interrupted run, which would leave a deliberately broken
    compiler checked out and every later test failing for a reason that
    has nothing to do with what it was testing. Not worth the risk for
    a directory copy.
    """
    patched = canary.patched()
    tree = os.path.join(out_dir, "bootstrap")
    os.makedirs(tree, exist_ok=True)
    src_dir = os.path.dirname(canary.path)
    for name in os.listdir(src_dir):
        if name.endswith(".f"):
            shutil.copyfile(os.path.join(src_dir, name),
                            os.path.join(tree, name))
    with open(os.path.join(tree, os.path.basename(canary.path)),
              "w", encoding="utf-8") as fh:
        fh.write(patched)
    entry = os.path.join(tree, os.path.basename(irdiff.CODEGEN_SOURCE))
    out = os.path.join(out_dir, "fir")
    result = subprocess.run(
        [sys.executable, "-m", "festina.cli", "compile", entry, "-o", out],
        capture_output=True, text=True, timeout=1800, cwd=REPO_ROOT)
    if result.returncode != 0:
        return None, (result.stdout + result.stderr).strip()
    return out, None


def run_one(canary, base):
    """(verdict, detail) for one canary.

    verdict is "differ", "ratchet", "undetected" or "broken".

    "broken" means the patched source did not compile at all, which
    proves nothing about the corpus -- a canary has to produce a WRONG
    compiler, not an absent one.
    """
    with tempfile.TemporaryDirectory() as tmp:
        binary, error = build_patched(canary, tmp)
        if binary is None:
            return "broken", error
        differed = []
        lines = 0
        scanned_all = True
        for n, path in enumerate(base.paths):
            # Enough witnesses: this mechanism is visible, and visible
            # in more than one program, which is the part that matters
            # (see WITNESSES_WANTED). Everything after this point in
            # the corpus is strictly more expensive to dump than
            # everything before it, so stopping here is where the
            # saving is.
            if len(differed) >= WITNESSES_WANTED:
                scanned_all = False
                break
            try:
                got = irdiff.festina_dump(binary, path)
            except RuntimeError as exc:
                # The patched compiler built but does not RUN -- it
                # crashed, hung until the OOM reaper took it, or exited
                # non-zero on a file it used to handle. That is a broken
                # canary, not a detection: it proves the corpus can tell
                # a working compiler from a dead one, which was never in
                # question.
                return "broken", f"the patched compiler died: {exc}"
            rel = os.path.relpath(path, REPO_ROOT)
            want = base.expected[path]
            if got == want:
                lines += max(0, len(want) - irdiff.SHARED_PREAMBLE_LINES)
                continue
            if any(line.startswith("UNPORTED|") for line in got):
                continue              # refused now: the ratchet's business
            for i in range(max(len(got), len(want))):
                a = want[i] if i < len(want) else "<missing>"
                b = got[i] if i < len(got) else "<missing>"
                if a != b:
                    differed.append((rel, (i, a, b)))
                    break
        if differed:
            return "differ", (differed, scanned_all)
        # Only reachable on a full scan: the loop cannot exit early
        # without having found a difference, so `lines` is a real total
        # here rather than a partial one.
        if lines < base.lines:
            return "ratchet", f"{base.lines} -> {lines} lines reproduced"
        return "undetected", None


def main(argv):
    names = [a for a in argv[1:] if not a.startswith("-")]
    if "--list" in argv:
        for c in CANARIES:
            print(f"{c.name:30s} {c.slice:6s} {c.mechanism}")
        return 0
    unknown = [n for n in names if n not in BY_NAME]
    if unknown:
        print(f"no canary named {', '.join(repr(n) for n in unknown)}; "
              f"--list to see them", file=sys.stderr)
        return 2
    chosen = [BY_NAME[n] for n in names] if names else CANARIES

    print("building the unbroken compiler ...", flush=True)
    with tempfile.TemporaryDirectory() as tmp:
        clean = irdiff.build_codegen(os.path.join(tmp, "fir"))
        base = baseline(clean)
    print(f"{len(base.paths)} files match, {base.lines} file-specific lines\n")

    counts = {"differ": 0, "ratchet": 0, "undetected": 0, "broken": 0}
    for c in chosen:
        try:
            verdict, detail = run_one(c, base)
        except LookupError as exc:
            verdict, detail = "broken", str(exc)
        counts[verdict] += 1
        mark = {"differ": "caught", "ratchet": "caught (ratchet only)",
                "undetected": "NOT CAUGHT", "broken": "BROKEN"}[verdict]
        print(f"{c.name:30s} {c.slice:6s} {mark}")
        if verdict == "differ":
            files, scanned_all = detail
            for rel, d in files[:2]:
                print(f"    {rel}: line {d[0] + 1}")
                print(f"      python:  {d[1]}")
                print(f"      festina: {d[2]}")
            if scanned_all and len(files) == 1:
                # The distinction this whole ordering exists to keep
                # visible. One witness after a FULL scan is a real
                # finding: the mechanism hangs on a single file and
                # goes unmeasured the day that file stops matching for
                # any unrelated reason. WHICH file decides how much it
                # matters -- a purpose-written `cases/` file is the
                # intended arrangement, and anything else is an
                # accident waiting to be noticed.
                only = files[0][0]
                if only.startswith("bootstrap/cases/"):
                    print(f"    one witness, and it is the case file "
                          f"written for it -- by design")
                else:
                    print(f"    ONE WITNESS, and it is not a case file: "
                          f"{only}. {c.mechanism} goes unmeasured the day "
                          f"that file stops matching for any reason at "
                          f"all -- write a `cases/` file for it")
            elif len(files) > 2:
                print(f"    ... and {len(files) - 2} more file(s)")
        elif verdict == "ratchet":
            print(f"    {detail}")
        elif verdict == "broken":
            print(f"    {detail.splitlines()[0] if detail else ''}")
        elif verdict == "undetected":
            print(f"    the corpus cannot see: {c.mechanism}")

    print(f"\n{counts['differ']} caught, {counts['ratchet']} caught via the "
          f"ratchet, {counts['undetected']} NOT caught, {counts['broken']} broken")
    return 1 if (counts["undetected"] or counts["broken"]) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
