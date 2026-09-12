// festina/escape_analysis.py, ported to Festina.
//
// The FIFTH module the codegen port depends on, and one that was not in
// the original estimate (decisions.md #295). It answers one purely
// syntactic question about a function body:
//
//     does this name ever appear anywhere except as the immediate
//     `.obj` of a field or element access?
//
// A name that never does is "safe": reading or writing through a
// value's own fields never exposes its address to anything else, so a
// struct/arr[T]/map[T] local of that name can live in the frame rather
// than behind a heap refcount header, and its storage can be reclaimed
// at scope exit. claude.md #74.
//
// **It is wrong in one direction only, deliberately.** Every position
// other than a member base counts as escaping, unconditionally, with
// one proven exception (see EP_KNOWN below). It never tries to show a
// name is NOT in such a position by anything cleverer than "does it
// appear there at all". The analysis is also name-based rather than
// scope-resolving: an inner block declaring its own unrelated local of
// the same name makes the outer candidate look MORE escaping than it
// is, which costs a missed optimization and can never mark something
// safe that is not.
//
// The port mirrors that stance rather than improving on it. A
// difference in either direction changes the IR, so "better" here would
// be a bug.

// semantic.f rather than parser.f: the node accessors this walk is
// written against (fieldOf/childOf/listOf/rawText) live there, and it
// imports parser.f itself.
import semantic.f

// ---------------------------------------------------------------------
// State.
//
// The result is a set of names, which Festina has no type for, so it is
// a map[int] with 1 for every member. `ESC` is rebound to a fresh map
// by findEscapingNames rather than cleared, because an earlier result
// is still held by whoever asked for it -- rebinding the global leaves
// that map untouched, which is what makes a nested analysis safe while
// an outer one's answer is still in use.
map[int] ESC = {}

// An expression kind this walk does not recognize. Python raises rather
// than treating an unknown node as non-escaping, because silence there
// would be exactly the soundness gap claude.md #74 exists to rule out.
// A void Festina function has nothing to raise, so the loud failure is
// a flag the caller must check: an unrecognized node means the answer
// cannot be trusted, and the file is reported unported rather than
// emitted from a guess.
bool ESC_UNKNOWN = false
text ESC_UNKNOWN_KIND = ''

// claude.md #74 stage 2, interprocedural. For a Call whose callee is a
// plain name this module has ALREADY analysed, only the argument
// positions that callee's own body proves escaping still count; every
// other position is exempted at that one call site.
//
// Python carries this as {func_name: set[int]}; Festina has no nested
// generic map, so it is two flat maps: EP_KNOWN says the function has
// been analysed at all (Python's `name in escaping_params`), and
// EP_POS, keyed '<func>#<index>', says that one position escapes.
//
// The distinction is load-bearing. A callee absent from EP_KNOWN -- a
// builtin, a method call whose callee is not a plain name,
// self-recursion, or a genuine forward reference to a function
// declared later (claude.md #140's hoisting makes that ordinary) --
// falls back to "every argument escapes". Always safe, never a
// soundness gap: "not proven safe yet" and "escaping" get the identical
// treatment here.
map[int] EP_KNOWN = {}
map[int] EP_POS = {}

// Builtins none of whose arguments ever escape (claude.md #92). A
// builtin has no Festina body to analyse, so without this list it fell
// to the conservative default -- and `drawImage(tile, x, y)` alone was
// enough to keep `tile` alive forever, defeating the reclamation it
// exists for. Every entry was checked against the runtime rather than
// assumed: each reads its argument during the call and keeps no pointer
// afterwards.
text NON_RETAINING = 'log fail drawRect drawCircle drawText drawImage loadImage loadAudio fillStyle borderColor lineWidth changeFont measureTextWidth measureTextHeight sqlite regex'
map[int] NR_SET = {}
bool NR_READY = false

void func escInit() {
    if NR_READY { return }
    arr[text] names = NON_RETAINING.split(' ')
    int i = 0
    while i < names.length {
        NR_SET[names[i]] = 1
        i++
    }
    NR_READY = true
}

// Records a function's analysed result so later analyses can use it.
// Called AFTER the body is walked, exactly where festina/codegen.py's
// _emit_analyzed_func_body registers its own entry -- so the order
// functions are emitted in is part of the answer, not an accident of
// it.
void func escRegisterParams(fname:text, params:arr[Node], escaping:map[int]) {
    EP_KNOWN[fname] = 1
    int i = 0
    while i < params.length {
        if escaping[rawText(params[i], 'name')] != null {
            EP_POS[`${fname}#${i}`] = 1
        }
        i++
    }
}

// ---------------------------------------------------------------------
// The walk.

void func escStmts(stmts:arr[Node]) {
    int i = 0
    while i < stmts.length {
        escStmt(stmts[i])
        i++
    }
}

// The statement list inside a declaration's `body` Block. parseBlock
// wraps a body in a Block node rather than storing a bare list, so a
// body is two hops away, not one.
arr[Node] func escBodyStmts(d:Node) {
    arr[Node] empty = []
    if d == null { return empty }
    Node b = childOf(d, 'body')
    if b == null { return empty }
    if b.kind != 'Block' { return empty }
    return listOf(b, 'body')
}

void func escBlockStmts(b:Node) {
    if b == null { return }
    if b.kind != 'Block' { return }
    escStmts(listOf(b, 'body'))
}

void func escStmt(s:Node) {
    if s == null { return }

    if s.kind == 'VarDecl' {
        escExpr(childOf(s, 'init'))
        return
    }
    if s.kind == 'ExprStmt' {
        escExpr(childOf(s, 'expr'))
        return
    }
    if s.kind == 'FreeStmt' {
        // claude.md #111: this one line is what makes `free` SAFE
        // rather than merely implemented. Escaping-ness is what forces
        // the binding's value onto the heap behind a real refcount
        // header -- a non-escaping local stack-allocates, and calling a
        // refcounted release on a stack address underflows into the
        // frame -- and what stops the compiler's own scope-exit
        // reclamation from also claiming a value the program said it
        // would manage by hand.
        ESC[rawText(s, 'name')] = 1
        return
    }
    if s.kind == 'DeleteStmt' {
        escExpr(childOf(s, 'target'))
        return
    }
    if s.kind == 'Return' {
        escExpr(childOf(s, 'value'))
        return
    }
    if s.kind == 'IfStmt' {
        escExpr(childOf(s, 'test'))
        escBlockStmts(childOf(s, 'then'))
        Node orelse = childOf(s, 'orelse')
        if orelse != null {
            // `else if` chains as a nested IfStmt rather than a Block.
            if orelse.kind == 'IfStmt' { escStmt(orelse) }
            else { escBlockStmts(orelse) }
        }
        return
    }
    if s.kind == 'WhileStmt' {
        escExpr(childOf(s, 'test'))
        escBlockStmts(childOf(s, 'body'))
        return
    }
    if s.kind == 'ForStmt' {
        escStmt(childOf(s, 'init'))
        escExpr(childOf(s, 'test'))
        escExpr(childOf(s, 'update'))
        escBlockStmts(childOf(s, 'body'))
        return
    }
    if s.kind == 'Block' {
        escStmts(listOf(s, 'body'))
        return
    }
    if s.kind == 'TryStmt' {
        // claude.md #192: try and catch are ordinary function-body
        // statements and codegen emits both under this same escaping
        // set, so a value escaping only inside one of them
        // (`try { g = xs }`) has to be seen here. Missing them made
        // such a value stack-allocated and freed at scope exit while
        // still reachable through the escape -- a real use-after-free.
        // The catch variable is a fresh text binding shadowing within
        // the catch body, and walking its uses can only ADD names,
        // never wrongly clear one, so the conservative direction holds.
        escBlockStmts(childOf(s, 'try_body'))
        escBlockStmts(childOf(s, 'catch_body'))
        return
    }
    if s.kind == 'ThrowStmt' {
        escExpr(childOf(s, 'expr'))
        return
    }

    // BreakStmt/ContinueStmt have no expressions. Everything else --
    // the declaration-shaped statements a parser will accept inside a
    // body even though codegen rejects them there -- is a silent no-op,
    // because this pass runs earlier and independently.
    return
}

// The one place a name is safe: as the direct `.obj` of a member
// access. A bare name there is NOT recorded; anything else (a nested
// chain, a call result) is walked normally -- so `x.y.z` still treats
// `x` as safe, bottoming out at this same case one level down on the
// inner `x.y`, while `getStruct().field` never reaches the special
// case at all, because a Call is not a name.
void func escMemberObj(m:Node) {
    Node obj = childOf(m, 'obj')
    if obj == null { return }
    if obj.kind == 'Identifier' { return }
    escExpr(obj)
}

// Assign.target gets the same treatment: `v = ...` is a real
// reassignment of the whole variable and escapes -- v's OLD value may
// still be aliased elsewhere, so freeing whatever v holds at the end of
// the function would free the wrong thing. `v.field = ...` and
// `v[i] = ...` only write through v's own storage and are safe, exactly
// like a member read.
void func escAssignTarget(t:Node) {
    if t == null { return }
    if t.kind == 'Identifier' {
        ESC[rawText(t, 'name')] = 1
        return
    }
    if t.kind == 'Member' {
        escMemberObj(t)
        if fieldOf(t, 'computed').raw == 'true' { escExpr(childOf(t, 'prop')) }
        return
    }
    return
}

void func escList(n:Node, field:text) {
    arr[Node] xs = listOf(n, field)
    int i = 0
    while i < xs.length {
        escExpr(xs[i])
        i++
    }
}

void func escExpr(e:Node) {
    if e == null { return }

    if e.kind == 'Identifier' {
        ESC[rawText(e, 'name')] = 1
        return
    }
    if e.kind == 'NumberLit' { return }
    if e.kind == 'StringLit' { return }
    if e.kind == 'BoolLit' { return }
    if e.kind == 'NullLit' { return }
    if e.kind == 'RegexLit' { return }
    if e.kind == 'TypeArg' {
        // claude.md #159: .toStruct(T)/.toArr(T)'s "argument" is a
        // TYPE, never a variable reference.
        return
    }
    if e.kind == 'TemplateLit' {
        escList(e, 'exprs')
        return
    }
    if e.kind == 'ArrayLit' {
        escList(e, 'elements')
        return
    }
    if e.kind == 'MapLit' {
        // entries are '#pair' markers with the key at field 0 and the
        // value at field 1 -- Python stores them as tuples.
        arr[Node] entries = listOf(e, 'entries')
        int i = 0
        while i < entries.length {
            escExpr(entries[i].fields[0].node)
            escExpr(entries[i].fields[1].node)
            i++
        }
        return
    }
    if e.kind == 'Assign' {
        escAssignTarget(childOf(e, 'target'))
        escExpr(childOf(e, 'value'))
        return
    }
    if e.kind == 'Ternary' {
        escExpr(childOf(e, 'test'))
        escExpr(childOf(e, 'cons'))
        escExpr(childOf(e, 'alt'))
        return
    }
    if e.kind == 'LogicalOp' {
        escExpr(childOf(e, 'left'))
        escExpr(childOf(e, 'right'))
        return
    }
    if e.kind == 'BinOp' {
        escExpr(childOf(e, 'left'))
        escExpr(childOf(e, 'right'))
        return
    }
    if e.kind == 'UnaryOp' {
        escExpr(childOf(e, 'operand'))
        return
    }
    if e.kind == 'PostfixOp' {
        escExpr(childOf(e, 'operand'))
        return
    }
    if e.kind == 'TypeofExpr' {
        // Walked like any other operand. typeof only reads its
        // operand's tag, never retains or stores it, but the
        // unconditional "operand of any operator escapes" default costs
        // nothing worse than a missed optimization here, so there is no
        // reason to special-case it narrower than UnaryOp already is.
        escExpr(childOf(e, 'operand'))
        return
    }
    if e.kind == 'Member' {
        escMemberObj(e)
        if fieldOf(e, 'computed').raw == 'true' { escExpr(childOf(e, 'prop')) }
        return
    }
    if e.kind == 'Call' {
        escCall(e)
        return
    }
    if e.kind == 'ArrowFuncExpr' {
        // claude.md #142: a deliberate no-op, not an oversight. Unlike
        // every other expression kind here, this one contributes
        // NOTHING to the enclosing function's escaping set: its body is
        // analysed and emitted in a completely separate scope
        // (semantic.py parents every function's scope at the global
        // one, however deeply nested -- there are no closures), so no
        // name inside it could alias one of THIS function's locals.
        // Its own body gets its own separate analysis, like any other
        // nested declaration's.
        return
    }

    // Every expression kind the parser produces is handled above, so
    // reaching here means the grammar grew one this walk was not
    // taught. See ESC_UNKNOWN's own comment: the answer is not
    // trustworthy from this point on, and saying so is the only safe
    // response.
    ESC_UNKNOWN = true
    ESC_UNKNOWN_KIND = e.kind
}

void func escCall(e:Node) {
    escExpr(childOf(e, 'callee'))
    arr[Node] args = listOf(e, 'args')

    // Which argument positions still escape at THIS call site. A
    // present-but-empty answer exempts every one of them; an absent
    // answer exempts none.
    bool havePositions = false
    text fname = ''
    Node callee = childOf(e, 'callee')
    if callee != null && callee.kind == 'Identifier' {
        fname = rawText(callee, 'name')
        if NR_SET[fname] != null {
            // claude.md #92: no position escapes, exactly as a fully
            // safe user function's own analysis would say. The name is
            // cleared so the per-position lookup below finds nothing.
            havePositions = true
            fname = ''
        } else {
            if EP_KNOWN[fname] != null { havePositions = true }
        }
    }

    int i = 0
    while i < args.length {
        bool exempt = false
        if havePositions {
            exempt = true
            if fname != '' {
                if EP_POS[`${fname}#${i}`] != null { exempt = false }
            }
        }
        if exempt {
            Node a = args[i]
            if a.kind == 'Identifier' {
                // Proven safe at this one call site, so deliberately
                // NOT recorded. The name may still escape through some
                // other use elsewhere in this same function; this only
                // stops this call from being the reason.
                i++
                continue
            }
            if a.kind == 'ArrayLit' {
                // claude.md #101: reach INSIDE a literal array
                // argument. This exists for sqlite(), whose bound
                // parameters must be a literal array, so
                // `sqlite('... VALUES (?, ?)', [name, track])` is the
                // ordinary shape and testing only for a bare name
                // treated every bound value as escaping. Each parameter
                // binds with SQLITE_TRANSIENT, so sqlite has copied
                // what it needs before the call returns -- the same
                // property that put it in the non-retaining list. Only
                // bare-name elements are exempted; anything else is
                // walked normally.
                arr[Node] elems = listOf(a, 'elements')
                int k = 0
                while k < elems.length {
                    if elems[k].kind != 'Identifier' { escExpr(elems[k]) }
                    k++
                }
                i++
                continue
            }
        }
        escExpr(args[i])
        i++
    }
}

// ---------------------------------------------------------------------
// Entry point.

// Every name appearing anywhere in `stmts` in a position other than the
// immediate `.obj` of a member access -- or, where EP_KNOWN/EP_POS
// prove a call-argument position safe, other than that one call site.
//
// Takes a statement LIST rather than a Block node so the same function
// serves both callers: a function or handler body, and the top-level
// statement list, which has no Block wrapper of its own.
map[int] func findEscapingNames(stmts:arr[Node]) {
    escInit()
    map[int] fresh = {}
    ESC = fresh
    escStmts(stmts)
    return fresh
}
