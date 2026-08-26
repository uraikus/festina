// claude.md #173: a ternary used to be treated as "aliasing" -- needs
// a copy/retain from whoever binds it -- no matter what its own two
// branches actually were, which was silently wrong the moment either
// branch was itself a genuinely fresh, owning value (a template
// literal, a `+` concatenation, a function call, an array/map
// literal, ...): the binding's own copy/retain landed on top of an
// already-correct +1 with nothing left to ever balance it, an
// unconditional leak on every evaluation that took the fresh branch.
// Found via tests/stress/json_parse_churn.f's own leak run, not by
// inspection -- this file isolates the bug on its own, independent of
// JSON parsing, across every shape that own _own_ternary_branch fix
// has to get right: text AND refcounted results, either branch fresh,
// both branches fresh, a nested ternary as one branch, and a chosen-
// vs-not-chosen asymmetry (the SAME branch has to stay leak-free
// whether or not it's the one that actually ran that pass).

struct S { n:int }
S func makeS(v:int) { S s  s.n = v  return s }

int total = 0
int i = 0
while i < 200 {
    bool cond = i % 2 == 0

    // text: one fresh branch (a template literal), one aliasing
    // branch (a bare identifier reading an existing global).
    text label = 'shared label'
    text a = cond ? `built ${i}` : label
    if a != '' { total = total + 1 }

    // text: BOTH branches fresh (a template and a `+` concatenation)
    // -- neither is ever the "unproblematic aliasing" case, so this
    // exercises _own_ternary_branch's own owning-check on both sides
    // every single pass, not just whichever one happened to run.
    text b = cond ? `tmpl ${i}` : ('concat' + `${i}`)
    if b != '' { total = total + 1 }

    // refcounted (struct): one fresh branch (a function call), one
    // aliasing branch (an existing binding) -- the exact shape
    // tests/stress/json_parse_churn.f's own self-referencing-struct
    // case first surfaced this under.
    S shared = makeS(0)
    S c = cond ? makeS(i) : shared
    total = total + c.n
    free shared
    free c

    // refcounted: BOTH branches fresh.
    S d = cond ? makeS(i) : makeS(i + 1)
    total = total + d.n
    free d

    // A nested ternary as one branch of an outer one -- the outer
    // ternary's own _own_ternary_branch call must recognize the INNER
    // ternary's already-normalized result as owning (via the same
    // Ternary case just added to _is_owning_text_source/
    // _is_owning_refcounted_source) rather than copying/retaining it
    // AGAIN on top of the inner one's own already-correct ownership.
    text e = cond ? (i % 4 == 0 ? `nested ${i}` : label) : `outer ${i}`
    if e != '' { total = total + 1 }

    i = i + 1
}
log(total)
