"""AST node definitions for Festina.

Plain, lightweight nodes -- no behavior. `type_expr` fields hold either a
plain type-name string (e.g. "int", "User") or an ArrayTypeExpr, as
produced by Parser.parse_type().
"""


class Node:
    pass


class Program(Node):
    def __init__(self, body):
        self.body = body


class ImportDecl(Node):
    def __init__(self, path, line=0, column=0):
        self.path = path
        self.line = line
        self.column = column


class ArrayTypeExpr(Node):
    """`arr[T]` as it appears in a type position."""

    def __init__(self, element):
        self.element = element


class MapTypeExpr(Node):
    """`map[T]` as it appears in a type position -- claude.md #72. Keys
    are always text, so (mirroring ArrayTypeExpr) only the value type
    needs spelling out."""

    def __init__(self, value):
        self.value = value


class Param(Node):
    def __init__(self, name, type_expr):
        self.name = name
        self.type_expr = type_expr


class FieldDecl(Node):
    def __init__(self, name, type_expr):
        self.name = name
        self.type_expr = type_expr


class VarDecl(Node):
    def __init__(self, type_expr, name, init, is_const=False, line=0, column=0):
        self.type_expr = type_expr
        self.name = name
        self.init = init
        self.is_const = is_const
        self.line = line
        self.column = column


class FuncDecl(Node):
    def __init__(self, name, return_type, params, body, line=0, column=0):
        self.name = name
        self.return_type = return_type
        self.params = params
        self.body = body
        self.line = line
        self.column = column


class StructDecl(Node):
    def __init__(self, name, fields, line=0, column=0):
        self.name = name
        self.fields = fields
        self.line = line
        self.column = column


class TableDecl(Node):
    def __init__(self, name, fields, line=0, column=0):
        self.name = name
        self.fields = fields
        self.line = line
        self.column = column


class EventHandler(Node):
    def __init__(self, name, params, body, line=0, column=0):
        self.name = name
        self.params = params
        self.body = body
        self.line = line
        self.column = column


class Block(Node):
    def __init__(self, body):
        self.body = body


class IfStmt(Node):
    def __init__(self, test, then, orelse, line=0, column=0):
        self.test = test
        self.then = then
        self.orelse = orelse
        self.line = line
        self.column = column


class Return(Node):
    def __init__(self, value, line=0, column=0):
        self.value = value
        self.line = line
        self.column = column


class ExprStmt(Node):
    def __init__(self, expr):
        self.expr = expr


class WhileStmt(Node):
    """claude.md #61."""

    def __init__(self, test, body, line=0, column=0):
        self.test = test
        self.body = body
        self.line = line
        self.column = column


class ForStmt(Node):
    """claude.md #60. `init` is a VarDecl (the loop variable, scoped to
    this statement); `update` is an arbitrary expression, evaluated for
    its side effect at the end of each iteration (typically a
    PostfixOp -- claude.md #60's own list of valid update expressions)."""

    def __init__(self, init, test, update, body, line=0, column=0):
        self.init = init
        self.test = test
        self.update = update
        self.body = body
        self.line = line
        self.column = column


class BreakStmt(Node):
    """claude.md #73: exits the nearest enclosing for/while loop
    immediately. No value, no target label -- Festina has no labeled
    break the way some JS-inspired languages do (unspecified, so not
    invented -- claude.md #54)."""

    def __init__(self, line=0, column=0):
        self.line = line
        self.column = column


class ContinueStmt(Node):
    """claude.md #73: skips directly to the next iteration of the
    nearest enclosing for/while loop -- for a `for` loop this still runs
    the update expression first (claude.md #60's own step order), same
    as continue's JS/C meaning."""

    def __init__(self, line=0, column=0):
        self.line = line
        self.column = column


# ---- expressions ----

class Identifier(Node):
    def __init__(self, name, line=0, column=0):
        self.name = name
        self.line = line
        self.column = column


class NumberLit(Node):
    def __init__(self, value):
        self.value = value


class StringLit(Node):
    def __init__(self, value):
        self.value = value


class BoolLit(Node):
    def __init__(self, value):
        self.value = value


class NullLit(Node):
    pass


class TemplateLit(Node):
    """`parts` has one more element than `exprs`: parts[0] expr[0]
    parts[1] expr[1] ... parts[-1]."""

    def __init__(self, parts, exprs):
        self.parts = parts
        self.exprs = exprs


class ArrayLit(Node):
    def __init__(self, elements):
        self.elements = elements


class MapLit(Node):
    """claude.md #72: { key: value, ... } -- `entries` is a list of
    (key_expr, value_expr) pairs, in source order (so codegen can build
    the map by emitting one festina_map_set per entry in that same
    order, giving "last write wins" for a repeated key for free, with
    no separate dedup pass needed). Every key_expr must be text
    (checked in semantic.py, same as array indexing's int check) --
    there is no bareword-as-string-literal shorthand the way a plain JS
    object literal has: an unquoted identifier key is a reference to
    that variable, not its own name."""

    def __init__(self, entries, line=0, column=0):
        self.entries = entries
        self.line = line
        self.column = column


class RegexLit(Node):
    """claude.md #67: a JS-style /pattern/flags literal -- `pattern` and
    `flags` are both plain, already-unescaped text (never a runtime
    expression, unlike the regex() function's arguments below), fixed
    at compile time. Semantically and at codegen time this produces the
    exact same regex value the equivalent regex(pattern, flags) call
    would (see semantic.py's infer() and codegen.py's _emit_expr) --
    regex() itself is kept as the escape hatch for a pattern/flags value
    that genuinely isn't known until runtime (built from a variable, a
    template, ...), the same split JS itself has between a /pattern/
    literal and `new RegExp(str, flags)`."""

    def __init__(self, pattern, flags, line=0, column=0):
        self.pattern = pattern
        self.flags = flags
        self.line = line
        self.column = column


class Assign(Node):
    def __init__(self, target, op, value, line=0, column=0):
        self.target = target
        self.op = op
        self.value = value
        self.line = line
        self.column = column


class Ternary(Node):
    def __init__(self, test, cons, alt, line=0, column=0):
        self.test = test
        self.cons = cons
        self.alt = alt
        self.line = line
        self.column = column


class LogicalOp(Node):
    def __init__(self, op, left, right):
        self.op = op
        self.left = left
        self.right = right


class BinOp(Node):
    def __init__(self, op, left, right, line=0, column=0):
        self.op = op
        self.left = left
        self.right = right
        self.line = line
        self.column = column


class UnaryOp(Node):
    def __init__(self, op, operand):
        self.op = op
        self.operand = operand


class PostfixOp(Node):
    """claude.md #66: postfix ++/--. Only ever valid on a mutable int
    variable (an Identifier) -- enforced in semantic.py, not the parser,
    same pattern as every other type restriction in this codebase."""

    def __init__(self, op, operand, line=0, column=0):
        self.op = op
        self.operand = operand
        self.line = line
        self.column = column


class Member(Node):
    def __init__(self, obj, prop, computed, line=0, column=0):
        self.obj = obj
        self.prop = prop
        self.computed = computed
        self.line = line
        self.column = column


class Call(Node):
    def __init__(self, callee, args, line=0, column=0):
        self.callee = callee
        self.args = args
        self.line = line
        self.column = column
