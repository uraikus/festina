"""claude.md #74: escape analysis for automatic memory reclamation,
stage 1 (non-escaping locals). See festina/codegen.py's module docstring
and CodeGen._emit_func_body/_emit_free_active_locals for how the result
of this module gets used -- this module only answers "does this name
ever appear anywhere in this function/handler body except as the base
of its own field/element access", a purely syntactic question that
needs no Festina type information at all. The caller in codegen.py is
responsible for filtering the resulting "never escapes" name set down
to the ones that are actually struct/arr[T]/map[T]-typed local
declarations -- a name that never escapes but turns out to be, say, an
int is simply never a free candidate to begin with, since only struct/
array/map values are heap-backed at all.

The core rule (claude.md #74): a name is "safe" everywhere it appears
as the immediate `.obj` of a Member access (`v.field`, `v.field = x`,
`v[i]`, `v[i] = x`, `v.someMethod(...)`) -- reading or writing through a
value's own fields/elements never exposes its address to anything else.
Every other position a name can appear in -- a bare Return value, a Call
argument, the value or target of a plain Assign, an element of an
array/map literal, an operand of any operator, ... -- is treated as
escaping, unconditionally, UNLESS the optional `escaping_params`
argument below says otherwise for that one specific Call-argument
position (claude.md #74 stage 2, interprocedural). This module never
tries to prove a name is *not* in one of those positions via anything
more sophisticated than "does it appear there at all," except for that
one case, which is proved -- not guessed -- from the callee's own body
by this exact same module, one call site at a time.

`escaping_params`, when passed, is a `{func_name: set[int]}` map: for a
Call whose callee is a plain Identifier found in this map, only the
argument positions listed in that function's own set are still treated
as escaping; every other position is exempted from the default
call-argument rule for that specific call (the argument may still end
up escaping some other way, through some other use elsewhere in the
same function -- this only stops that one call site from being the
*reason* it escapes). A callee not present in the map (a builtin, a
method call whose callee isn't a plain Identifier at all, or -- see
festina/codegen.py's own note on why this is always safe -- a
not-yet-fully-analyzed function, which can only mean the callee
currently being walked calling itself: semantic.py already rejects any
other forward reference to a function before its own declaration, so
every other possible callee is necessarily already fully analyzed by
the time it's this function's turn) falls back to the original
unconditional rule, exactly as if `escaping_params` were never passed
at all. Building the
map itself -- walking every function's body once, in the source order
Festina already requires, so each function's own callees are always
already fully resolved by the time it's this function's turn -- is
festina/codegen.py's job (`_emit_analyzed_func_body`), not this
module's; this module only ever consumes it, one Call node at a time,
exactly the same way it already consumes nothing at all when the
caller omits it.

This is also deliberately name-based, not a real scope-resolving
analysis: if an inner if/while/for block happens to declare its OWN,
unrelated local with the same name (shadowing the outer candidate), a
genuinely-escaping use of that INNER variable is (incorrectly, but
harmlessly) counted as if it were a use of the outer candidate too --
this can only ever make a name look MORE escaping than it really is,
never less, so it can only cost a missed optimization opportunity, never
mark something safe that isn't. claude.md #74 is explicit that this must
never free a variable whose safety wasn't proven; erring toward "more
conservative" on a naming collision is exactly that stance, not a bug to
fix later at the cost of correctness.

_walk_expr raises on any expression node type it doesn't recognize,
deliberately -- every expression node ast.py currently defines is
handled below; if a future one is added and this module isn't updated
to match, silently treating it as "no escaping use" here would be
exactly the kind of soundness gap claude.md #74 exists to rule out. Loud
failure here is a compile-time Python exception on the next commit that
adds a new expression kind, not a maybe-unsafe free reaching a running
program. _walk_stmt does NOT raise the same way for a statement kind it
doesn't handle -- a handful of declaration-shaped statements
(ImportDecl/FuncDecl/StructDecl/TableDecl/EventHandler) can appear
nested inside a function/handler body syntactically (the parser doesn't
forbid it) even though they're not actually valid there and
codegen.py's own _emit_stmt already rejects them with a CodegenError of
its own -- this module runs as an earlier, independent pass, so it
silently no-ops on those (nothing useful to walk in them for this
analysis anyway) and leaves that rejection to fire normally afterward,
the same "unrecognized statement kinds are ignored" convention
semantic.py's own analyze_statement already uses.
"""
from . import ast


def find_escaping_names(block, escaping_params=None):
    """Every name that appears anywhere in `block` (an ast.Block -- the
    top-level body of a function or event handler) in a position other
    than the immediate `.obj` of a Member access -- or, when a Call
    argument position is proven safe by `escaping_params` (see the
    module docstring), other than that one call site either. Returns a
    set[str]."""
    escaping = set()
    _walk_stmts(block.body, escaping, escaping_params)
    return escaping


def _walk_stmts(stmts, escaping, escaping_params):
    for stmt in stmts:
        _walk_stmt(stmt, escaping, escaping_params)


def _walk_stmt(stmt, escaping, escaping_params):
    if isinstance(stmt, ast.VarDecl):
        if stmt.init is not None:
            _walk_expr(stmt.init, escaping, escaping_params)
    elif isinstance(stmt, ast.ExprStmt):
        _walk_expr(stmt.expr, escaping, escaping_params)
    elif isinstance(stmt, ast.Return):
        if stmt.value is not None:
            _walk_expr(stmt.value, escaping, escaping_params)
    elif isinstance(stmt, ast.IfStmt):
        _walk_expr(stmt.test, escaping, escaping_params)
        _walk_stmts(stmt.then.body, escaping, escaping_params)
        if stmt.orelse is not None:
            if isinstance(stmt.orelse, ast.IfStmt):
                _walk_stmt(stmt.orelse, escaping, escaping_params)
            else:
                _walk_stmts(stmt.orelse.body, escaping, escaping_params)
    elif isinstance(stmt, ast.WhileStmt):
        _walk_expr(stmt.test, escaping, escaping_params)
        _walk_stmts(stmt.body.body, escaping, escaping_params)
    elif isinstance(stmt, ast.ForStmt):
        _walk_stmt(stmt.init, escaping, escaping_params)
        _walk_expr(stmt.test, escaping, escaping_params)
        _walk_expr(stmt.update, escaping, escaping_params)
        _walk_stmts(stmt.body.body, escaping, escaping_params)
    elif isinstance(stmt, ast.Block):
        _walk_stmts(stmt.body, escaping, escaping_params)
    # BreakStmt/ContinueStmt: no expressions to walk. Everything else
    # (see the module docstring's last paragraph): silent no-op.


def _walk_expr(expr, escaping, escaping_params):
    if expr is None:
        return
    if isinstance(expr, ast.Identifier):
        escaping.add(expr.name)
        return
    if isinstance(expr, (ast.NumberLit, ast.StringLit, ast.BoolLit, ast.NullLit, ast.RegexLit)):
        return
    if isinstance(expr, ast.TemplateLit):
        for e in expr.exprs:
            _walk_expr(e, escaping, escaping_params)
        return
    if isinstance(expr, ast.ArrayLit):
        for e in expr.elements:
            _walk_expr(e, escaping, escaping_params)
        return
    if isinstance(expr, ast.MapLit):
        for key_expr, val_expr in expr.entries:
            _walk_expr(key_expr, escaping, escaping_params)
            _walk_expr(val_expr, escaping, escaping_params)
        return
    if isinstance(expr, ast.Assign):
        _walk_assign_target(expr.target, escaping, escaping_params)
        _walk_expr(expr.value, escaping, escaping_params)
        return
    if isinstance(expr, ast.Ternary):
        _walk_expr(expr.test, escaping, escaping_params)
        _walk_expr(expr.cons, escaping, escaping_params)
        _walk_expr(expr.alt, escaping, escaping_params)
        return
    if isinstance(expr, ast.LogicalOp):
        _walk_expr(expr.left, escaping, escaping_params)
        _walk_expr(expr.right, escaping, escaping_params)
        return
    if isinstance(expr, ast.BinOp):
        _walk_expr(expr.left, escaping, escaping_params)
        _walk_expr(expr.right, escaping, escaping_params)
        return
    if isinstance(expr, ast.UnaryOp):
        _walk_expr(expr.operand, escaping, escaping_params)
        return
    if isinstance(expr, ast.PostfixOp):
        _walk_expr(expr.operand, escaping, escaping_params)
        return
    if isinstance(expr, ast.Member):
        _walk_member_obj(expr, escaping, escaping_params)
        if expr.computed:
            _walk_expr(expr.prop, escaping, escaping_params)
        return
    if isinstance(expr, ast.Call):
        _walk_expr(expr.callee, escaping, escaping_params)
        # claude.md #74 stage 2: a plain-Identifier callee found in
        # escaping_params has already been fully analyzed (see the
        # module docstring) -- escaping_positions is the set of
        # argument indices ITS OWN body proves escape; every other
        # index is exempted from the default "any call argument
        # escapes" rule for this one call site. Anything else (an
        # unknown/builtin/method/not-yet-analyzed callee, or
        # escaping_params not passed at all) leaves escaping_positions
        # None, which is exactly the original unconditional behavior
        # below (the `i not in escaping_positions` guard can never be
        # True when escaping_positions is None, by the `is not None`
        # check that guards it).
        escaping_positions = None
        if escaping_params is not None and isinstance(expr.callee, ast.Identifier):
            escaping_positions = escaping_params.get(expr.callee.name)
        for i, a in enumerate(expr.args):
            if (escaping_positions is not None and i not in escaping_positions
                    and isinstance(a, ast.Identifier)):
                # Proven safe at this specific call site -- deliberately
                # NOT added to `escaping` here. `a` may still end up in
                # `escaping` anyway, through some other, unrelated use
                # elsewhere in this same function; this only stops this
                # one call site from being the *reason* it does.
                continue
            _walk_expr(a, escaping, escaping_params)
        return
    raise AssertionError(
        f"escape_analysis: unhandled expression node {type(expr).__name__} -- "
        f"every ast.py expression type must be taught to this module (see its "
        f"own module docstring for why this raises instead of silently treating "
        f"an unrecognized node as non-escaping)"
    )


def _walk_member_obj(member, escaping, escaping_params):
    """The one place a name is safe: as the direct `.obj` of a Member
    access -- reading/writing through a value's own field/element never
    exposes its address. A bare Identifier here is NOT added to
    `escaping`; anything else (a nested Member chain, a Call result,
    ...) is walked normally, so a deeper chain like `x.y.z` still
    correctly treats `x` as safe (it bottoms out at this same special
    case one level down, on the inner `x.y` Member node) while something
    like `getStruct().field` doesn't even reach this special case at all
    -- `getStruct()` is a Call, not a name, so there's nothing here to
    mark safe or unsafe in the first place."""
    obj = member.obj
    if isinstance(obj, ast.Identifier):
        return
    _walk_expr(obj, escaping, escaping_params)


def _walk_assign_target(target, escaping, escaping_params):
    """Assign.target gets the same special-casing _walk_member_obj gives
    Member.obj: `v = ...` (a bare Identifier target) is a real
    reassignment of the whole variable and escapes -- v's OLD value may
    still be aliased elsewhere, and freeing whatever v holds at the end
    of the function would free the wrong thing (see claude.md #74's own
    reasoning). `v.field = ...` / `v[i] = ...` (a Member target) only
    ever writes through v's own storage and is safe, exactly like a
    Member read."""
    if isinstance(target, ast.Identifier):
        escaping.add(target.name)
        return
    if isinstance(target, ast.Member):
        _walk_member_obj(target, escaping, escaping_params)
        if target.computed:
            _walk_expr(target.prop, escaping, escaping_params)
        return
    raise AssertionError(
        f"escape_analysis: unhandled assignment target {type(target).__name__}"
    )

