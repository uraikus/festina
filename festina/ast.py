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
