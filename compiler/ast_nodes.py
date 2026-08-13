"""AST node definitions for the JS-subset compiler."""


class Node:
    pass


class Program(Node):
    def __init__(self, body):
        self.body = body


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


class UndefinedLit(Node):
    pass


class ArrayLit(Node):
    def __init__(self, elements):
        self.elements = elements


class ObjectLit(Node):
    def __init__(self, pairs):
        self.pairs = pairs  # list of (key:str, value:Node)


class Identifier(Node):
    def __init__(self, name):
        self.name = name


class VarDecl(Node):
    def __init__(self, kind, name, init):
        self.kind = kind  # 'var'|'let'|'const'
        self.name = name
        self.init = init


class DestructureDecl(Node):
    """`const { a, b } = require(...)` — names are recognized as builtins by
    identifier at call sites, so this node is a compile-time no-op."""
    def __init__(self, names, init):
        self.names = names
        self.init = init


class Assign(Node):
    def __init__(self, target, op, value):
        self.target = target  # Identifier | Member
        self.op = op  # '=', '+=', '-=', '*=', '/='
        self.value = value


class BinOp(Node):
    def __init__(self, op, left, right):
        self.op = op
        self.left = left
        self.right = right


class LogicalOp(Node):
    def __init__(self, op, left, right):
        self.op = op  # && ||
        self.left = left
        self.right = right


class UnaryOp(Node):
    def __init__(self, op, operand, prefix=True):
        self.op = op
        self.operand = operand
        self.prefix = prefix


class Member(Node):
    def __init__(self, obj, prop, computed):
        self.obj = obj
        self.prop = prop  # Node if computed else str
        self.computed = computed


class Call(Node):
    def __init__(self, callee, args):
        self.callee = callee
        self.args = args


class New(Node):
    def __init__(self, callee, args):
        self.callee = callee
        self.args = args


class FuncDecl(Node):
    def __init__(self, name, params, body):
        self.name = name
        self.params = params
        self.body = body


class Return(Node):
    def __init__(self, value):
        self.value = value


class If(Node):
    def __init__(self, test, cons, alt):
        self.test = test
        self.cons = cons
        self.alt = alt


class For(Node):
    def __init__(self, init, test, update, body):
        self.init = init
        self.test = test
        self.update = update
        self.body = body


class While(Node):
    def __init__(self, test, body):
        self.test = test
        self.body = body


class Block(Node):
    def __init__(self, body):
        self.body = body


class ExprStmt(Node):
    def __init__(self, expr):
        self.expr = expr


class ArrowFunc(Node):
    def __init__(self, params, body, is_expr_body):
        self.params = params
        self.body = body
        self.is_expr_body = is_expr_body
