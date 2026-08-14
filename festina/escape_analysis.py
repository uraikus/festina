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
escaping, unconditionally. This is deliberately conservative: this
module never tries to prove a name is *not* in one of those positions
via anything more sophisticated than "does it appear there at all" --
e.g. a value passed as a call argument is always treated as escaping,
even if the called function provably doesn't retain it (claude.md #74's
own stated limitation; that's interprocedural analysis, a later stage).

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


def find_escaping_names(block):
    """Every name that appears anywhere in `block` (an ast.Block -- the
    top-level body of a function or event handler) in a position other
    than the immediate `.obj` of a Member access. Returns a set[str]."""
    escaping = set()
    _walk_stmts(block.body, escaping)
    return escaping


def _walk_stmts(stmts, escaping):
    for stmt in stmts:
        _walk_stmt(stmt, escaping)


def _walk_stmt(stmt, escaping):
    if isinstance(stmt, ast.VarDecl):
        if stmt.init is not None:
            _walk_expr(stmt.init, escaping)
    elif isinstance(stmt, ast.ExprStmt):
        _walk_expr(stmt.expr, escaping)
    elif isinstance(stmt, ast.Return):
        if stmt.value is not None:
            _walk_expr(stmt.value, escaping)
    elif isinstance(stmt, ast.IfStmt):
        _walk_expr(stmt.test, escaping)
        _walk_stmts(stmt.then.body, escaping)
        if stmt.orelse is not None:
            if isinstance(stmt.orelse, ast.IfStmt):
                _walk_stmt(stmt.orelse, escaping)
            else:
                _walk_stmts(stmt.orelse.body, escaping)
    elif isinstance(stmt, ast.WhileStmt):
        _walk_expr(stmt.test, escaping)
        _walk_stmts(stmt.body.body, escaping)
    elif isinstance(stmt, ast.ForStmt):
        _walk_stmt(stmt.init, escaping)
        _walk_expr(stmt.test, escaping)
        _walk_expr(stmt.update, escaping)
        _walk_stmts(stmt.body.body, escaping)
    elif isinstance(stmt, ast.Block):
        _walk_stmts(stmt.body, escaping)
    # BreakStmt/ContinueStmt: no expressions to walk. Everything else
    # (see the module docstring's last paragraph): silent no-op.


def _walk_expr(expr, escaping):
    if expr is None:
        return
    if isinstance(expr, ast.Identifier):
        escaping.add(expr.name)
        return
    if isinstance(expr, (ast.NumberLit, ast.StringLit, ast.BoolLit, ast.NullLit, ast.RegexLit)):
        return
    if isinstance(expr, ast.TemplateLit):
        for e in expr.exprs:
            _walk_expr(e, escaping)
        return
    if isinstance(expr, ast.ArrayLit):
        for e in expr.elements:
            _walk_expr(e, escaping)
        return
    if isinstance(expr, ast.MapLit):
        for key_expr, val_expr in expr.entries:
            _walk_expr(key_expr, escaping)
            _walk_expr(val_expr, escaping)
        return
    if isinstance(expr, ast.Assign):
        _walk_assign_target(expr.target, escaping)
        _walk_expr(expr.value, escaping)
        return
    if isinstance(expr, ast.Ternary):
        _walk_expr(expr.test, escaping)
        _walk_expr(expr.cons, escaping)
        _walk_expr(expr.alt, escaping)
        return
    if isinstance(expr, ast.LogicalOp):
        _walk_expr(expr.left, escaping)
        _walk_expr(expr.right, escaping)
        return
    if isinstance(expr, ast.BinOp):
        _walk_expr(expr.left, escaping)
        _walk_expr(expr.right, escaping)
        return
    if isinstance(expr, ast.UnaryOp):
        _walk_expr(expr.operand, escaping)
        return
    if isinstance(expr, ast.PostfixOp):
        _walk_expr(expr.operand, escaping)
        return
    if isinstance(expr, ast.Member):
        _walk_member_obj(expr, escaping)
        if expr.computed:
            _walk_expr(expr.prop, escaping)
        return
    if isinstance(expr, ast.Call):
        _walk_expr(expr.callee, escaping)
        for a in expr.args:
            _walk_expr(a, escaping)
        return
    raise AssertionError(
        f"escape_analysis: unhandled expression node {type(expr).__name__} -- "
        f"every ast.py expression type must be taught to this module (see its "
        f"own module docstring for why this raises instead of silently treating "
        f"an unrecognized node as non-escaping)"
    )


def _walk_member_obj(member, escaping):
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
    _walk_expr(obj, escaping)


def _walk_assign_target(target, escaping):
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
        _walk_member_obj(target, escaping)
        if target.computed:
            _walk_expr(target.prop, escaping)
        return
    raise AssertionError(
        f"escape_analysis: unhandled assignment target {type(target).__name__}"
    )
