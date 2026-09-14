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
    if into == '' { into = cgFreshHeader('%struct._FestinaArray') }""",
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
        """        } else if cgIsRefcounted(pftys[q]) {
            if escSet[pnames[q]] != null {""",
        """        } else if cgIsRefcounted(pftys[q]) {
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
    Canary(
        "math-yields-to-binding", "#306",
        "the Math namespace is chosen per METHOD NAME, not per receiver",
        "            if rawText(recv, 'name') == 'Math' && cgIsMathMethod(prop) {",
        "            if rawText(recv, 'name') == 'Math' && cgIsMathMethod(prop) && cgSlotOf('Math') == '' {",
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
        """        cgFreeCallArgs(args, argVals)
        return cgVal('', 'void', 'void')""",
        """        return cgVal('', 'void', 'void')""",
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
        "field-read-mints-before-release", "#311",
        "claude.md #117: a field read through an owning base is copied out first",
        """    if cgIsRefcounted(base.fty) && cgIsOwningRefcountedSource(childOf(e, 'obj')) {""",
        """    if false {""",
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
        cgOut(`  call void ${cgReleaseFnFor('struct', obj.ety)}(ptr ${old})`)""",
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
        "    if fty == 'struct' && cgStructOwnsAnything(ety) { return cgReleaseStructFn(ety) }",
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
        """    if cgIsRefcounted(r.fty) {
        if cgIsOwningRefcountedSource(v) == false {
            cgOut(`  call void @festina_retain(ptr ${val})`)
        }
    } else if r.fty == 'text' {""",
        """    if false {
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
        """        text want = ''
        if i < ptys.length { want = ptys[i] }""",
        """        text want = ''""",
    ),
    Canary(
        "split-receiver-not-freed", "#309",
        "split frees the receiver AND the separator it allocated",
        """        cgFreeTextTemp(recv, r)
        cgFreeTextTemp(args[0], sep)
        return cgArrVal(out, 'text')""",
        """        return cgArrVal(out, 'text')""",
    ),
    Canary(
        "owned-container-receiver", "#309",
        "a container temporary with no binding is released where it is read",
        """        cgOut(`  ${out} = load i64, ptr ${lenP}`)
        cgReleaseOwnedReceiver(childOf(e, 'obj'), obj)""",
        """        cgOut(`  ${out} = load i64, ptr ${lenP}`)""",
    ),
    Canary(
        "join-element-kind", "#309",
        "join carries the element KIND, which only the compiler knows",
        "ptr ${cgStringConst(r.ety)})`)",
        "ptr ${cgStringConst('int')})`)",
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
        self.paths = paths
        self.expected = expected      # {path: the Python side's own dump}
        self.lines = lines


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
        for path in base.paths:
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
            return "differ", differed
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
            for rel, d in detail[:2]:
                print(f"    {rel}: line {d[0] + 1}")
                print(f"      python:  {d[1]}")
                print(f"      festina: {d[2]}")
            if len(detail) > 2:
                print(f"    ... and {len(detail) - 2} more file(s)")
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
