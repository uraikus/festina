"""Semantic analysis -- claude.md #48 (error categories), #49 (symbol
table), #50 (type checking), plus the type-resolution/truthiness/equality
rules from #12-20, the struct/table distinction from #27, #28, #35, and
#55/#56 (int/float never mix directly; Math.floor/ceil/round/trunc and
int.toFloat() are the only conversions). #58 (struct/table namespace):
struct/table names live in `structs`/`tables`, never cross-checked
against `Scope` (variables/functions) -- separate namespaces by design.

`analyze(program)` walks the AST top to bottom. None of this repo's
fixtures need forward references (structs/tables/functions are always
declared before use), so a single left-to-right pass is enough.
"""
from . import ast
from . import types as types_mod
from .errors import CompileError

# claude.md #39, #41, #42, #32: builtin globals that don't need a
# programmer declaration.
BUILTIN_FUNCTIONS = {
    "log", "fail", "sqlite",
    "drawRect", "drawCircle", "drawText", "drawImage",
    "loadImage", "loadAudio",
}

_BUILTIN_RETURN_TYPES = {
    "loadImage": types_mod.ImageType(),
    "loadAudio": types_mod.AudioType(),
}

# claude.md #55: int and float never mix directly in a binary operator.
_INT = types_mod.PrimitiveType("int")
_FLOAT = types_mod.PrimitiveType("float")
_NUMERIC_TYPES = (_INT, _FLOAT)

# claude.md #56: float -> int, with an explicit rounding decision.
MATH_FUNCTIONS = {"floor", "ceil", "round", "trunc"}


class _NullType:
    def __repr__(self):
        return "null"


NULL = _NullType()  # claude.md #10, #25: null is valid for every type.


class Symbol:
    def __init__(self, name, type, kind, node=None):
        self.name = name
        self.type = type
        self.kind = kind  # variable | constant | function | parameter
        self.node = node

    def __repr__(self):
        return f"Symbol({self.name!r}, {self.type!r}, kind={self.kind!r})"


class Scope:
    def __init__(self, parent=None):
        self.vars = {}
        self.parent = parent

    def define(self, name, symbol, err_node, filename):
        if name in self.vars:
            raise CompileError(
                f"'{name}' is already declared",
                file=filename, line=getattr(err_node, "line", 0),
                column=getattr(err_node, "column", 0),
                category="duplicate declaration",
            )
        self.vars[name] = symbol

    def lookup(self, name):
        scope = self
        while scope is not None:
            if name in scope.vars:
                return scope.vars[name]
            scope = scope.parent
        return None


class AnalyzedProgram:
    def __init__(self, symbols, structs, tables, imports):
        self.symbols = symbols
        self.structs = structs
        self.tables = tables
        self.imports = imports


def resolve_type_name(type_expr, structs, tables, filename="<string>", node=None):
    if isinstance(type_expr, ast.ArrayTypeExpr):
        return types_mod.ArrayType(resolve_type_name(type_expr.element, structs, tables, filename, node))
    name = type_expr
    if name in types_mod.PRIMITIVE_NAMES:
        return types_mod.PrimitiveType(name)
    if name == "img":
        return types_mod.ImageType()
    if name == "aud":
        return types_mod.AudioType()
    if name in structs:
        return types_mod.StructType(name)
    if name in tables:
        return types_mod.TableType(name)
    raise CompileError(
        f"unknown type '{name}'",
        file=filename, line=getattr(node, "line", 0), column=getattr(node, "column", 0),
        category="unknown type",
    )


def analyze(program, filename="<string>"):
    global_scope = Scope()
    structs = {}
    tables = {}
    imports = []

    def resolve(type_expr, node=None):
        return resolve_type_name(type_expr, structs, tables, filename, node)

    def check_assignable(declared, actual, node, what="value"):
        if actual is None or actual is NULL or declared is None:
            return
        if declared != actual:
            raise CompileError(
                f"cannot assign {what} of type {types_mod.type_name(actual)} "
                f"to {types_mod.type_name(declared)}",
                file=filename, line=getattr(node, "line", 0), column=getattr(node, "column", 0),
                category="invalid assignment",
            )

    def check_condition_bool(cond_type, node):
        if cond_type is None:
            return
        if cond_type != types_mod.PrimitiveType("bool"):
            raise CompileError(
                f"condition must be bool, found {types_mod.type_name(cond_type)}",
                file=filename, line=getattr(node, "line", 0), column=getattr(node, "column", 0),
                category="invalid condition type",
            )

    def infer(expr, scope):
        if isinstance(expr, ast.NumberLit):
            return types_mod.PrimitiveType("float" if isinstance(expr.value, float) else "int")
        if isinstance(expr, ast.StringLit):
            return types_mod.PrimitiveType("text")
        if isinstance(expr, ast.BoolLit):
            return types_mod.PrimitiveType("bool")
        if isinstance(expr, ast.NullLit):
            return NULL
        if isinstance(expr, ast.TemplateLit):
            for e in expr.exprs:
                infer(e, scope)
            return types_mod.PrimitiveType("text")
        if isinstance(expr, ast.ArrayLit):
            elem_type = None
            for e in expr.elements:
                elem_type = infer(e, scope)
            return types_mod.ArrayType(elem_type) if elem_type is not None else None
        if isinstance(expr, ast.Identifier):
            sym = scope.lookup(expr.name)
            if sym is None:
                raise CompileError(
                    f"unknown variable '{expr.name}'",
                    file=filename, line=expr.line, column=expr.column,
                    category="unknown variable",
                )
            return sym.type
        if isinstance(expr, ast.Member):
            return _infer_member(expr, scope)
        if isinstance(expr, ast.Call):
            return _infer_call(expr, scope)
        if isinstance(expr, ast.Assign):
            target_type = infer(expr.target, scope)
            value_type = infer(expr.value, scope)
            check_assignable(target_type, value_type, expr)
            return target_type
        if isinstance(expr, ast.Ternary):
            cond_type = infer(expr.test, scope)
            check_condition_bool(cond_type, expr)
            cons_type = infer(expr.cons, scope)
            infer(expr.alt, scope)
            return cons_type
        if isinstance(expr, ast.LogicalOp):
            infer(expr.left, scope)
            infer(expr.right, scope)
            return types_mod.PrimitiveType("bool")
        if isinstance(expr, ast.BinOp):
            left = infer(expr.left, scope)
            right = infer(expr.right, scope)
            # claude.md #55: int and float never mix directly, in any
            # binary operator -- arithmetic, comparison, or equality.
            if left in _NUMERIC_TYPES and right in _NUMERIC_TYPES and left != right:
                raise CompileError(
                    f"cannot use {expr.op} directly between int and float; "
                    "convert one side first (int.toFloat(), or Math.floor/ceil/round/trunc for float -> int)",
                    file=filename, line=getattr(expr, "line", 0), column=getattr(expr, "column", 0),
                    category="invalid operand type",
                )
            if expr.op in ("==", "!=", "<", ">", "<=", ">="):
                return types_mod.PrimitiveType("bool")
            if left == types_mod.PrimitiveType("float") or right == types_mod.PrimitiveType("float"):
                return types_mod.PrimitiveType("float")
            return left if left is not None else right
        if isinstance(expr, ast.UnaryOp):
            operand = infer(expr.operand, scope)
            if expr.op == "!":
                return types_mod.PrimitiveType("bool")
            return operand
        return None

    def _infer_member(expr, scope):
        obj_type = infer(expr.obj, scope)
        if expr.computed:
            idx_type = infer(expr.prop, scope) if isinstance(expr.prop, ast.Node) else None
            if not isinstance(obj_type, types_mod.ArrayType):
                raise CompileError(
                    f"cannot index into {types_mod.type_name(obj_type)}",
                    file=filename, line=getattr(expr, "line", 0), column=getattr(expr, "column", 0),
                    category="invalid field access",
                )
            return obj_type.element
        if isinstance(obj_type, types_mod.StructType):
            fields = structs.get(obj_type.name, {})
            if expr.prop not in fields:
                raise CompileError(
                    f"struct '{obj_type.name}' has no field '{expr.prop}'",
                    file=filename, line=expr.line, column=expr.column,
                    category="invalid field access",
                )
            return fields[expr.prop]
        if isinstance(obj_type, types_mod.TableType):
            # claude.md #34: a query against a declared table produces
            # arr[TableType(name)] -- field access on a row (e.g.
            # `people[0].name`) resolves against that table's declared
            # columns, same as a struct field except `tables` stores raw
            # type-expr strings rather than already-resolved Type objects
            # (see analyze_table above), so each lookup resolves on demand.
            columns = tables.get(obj_type.name, {})
            if expr.prop not in columns:
                raise CompileError(
                    f"table '{obj_type.name}' has no field '{expr.prop}'",
                    file=filename, line=expr.line, column=expr.column,
                    category="invalid field access",
                )
            return resolve(columns[expr.prop], expr)
        if isinstance(obj_type, (types_mod.ImageType, types_mod.AudioType)):
            return None  # permissive: methods like .play()/.isPlaying() aren't modeled
        raise CompileError(
            f"cannot access field '{expr.prop}' on {types_mod.type_name(obj_type)}",
            file=filename, line=expr.line, column=expr.column,
            category="invalid field access",
        )

    def _infer_call(expr, scope):
        callee = expr.callee
        if isinstance(callee, ast.Identifier):
            name = callee.name
            if name in BUILTIN_FUNCTIONS:
                for a in expr.args:
                    infer(a, scope)
                return _BUILTIN_RETURN_TYPES.get(name)
            sym = scope.lookup(name)
            if sym is None or sym.kind != "function":
                raise CompileError(
                    f"unknown function '{name}'",
                    file=filename, line=callee.line, column=callee.column,
                    category="unknown function",
                )
            func_decl = sym.node
            if len(expr.args) != len(func_decl.params):
                raise CompileError(
                    f"function '{name}' expects {len(func_decl.params)} argument(s), "
                    f"got {len(expr.args)}",
                    file=filename, line=callee.line, column=callee.column,
                    category="invalid function argument type",
                )
            for arg_expr, param in zip(expr.args, func_decl.params):
                arg_type = infer(arg_expr, scope)
                param_type = resolve(param.type_expr, callee)
                if arg_type is not None and arg_type is not NULL and arg_type != param_type:
                    raise CompileError(
                        f"argument '{param.name}' of '{name}' expects "
                        f"{types_mod.type_name(param_type)}, found {types_mod.type_name(arg_type)}",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
            return sym.type
        if isinstance(callee, ast.Member) and not callee.computed:
            # claude.md #56: Math.floor/ceil/round/trunc(x:float) -> int
            if (isinstance(callee.obj, ast.Identifier) and callee.obj.name == "Math"
                    and callee.prop in MATH_FUNCTIONS):
                if len(expr.args) != 1:
                    raise CompileError(
                        f"Math.{callee.prop}() expects exactly 1 argument, got {len(expr.args)}",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                arg_type = infer(expr.args[0], scope)
                if arg_type is not None and arg_type is not NULL and arg_type != _FLOAT:
                    raise CompileError(
                        f"Math.{callee.prop}() expects a float argument, found {types_mod.type_name(arg_type)}",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                return _INT
            # claude.md #55: int.toFloat() -> float
            if callee.prop == "toFloat" and not expr.args and infer(callee.obj, scope) == _INT:
                return _FLOAT
        # Member call, e.g. music.play() -- validates the member access itself.
        infer(callee, scope)
        for a in expr.args:
            infer(a, scope)
        return None

    def analyze_struct(decl):
        if decl.name in structs or decl.name in tables:
            raise CompileError(
                f"'{decl.name}' is already declared",
                file=filename, line=decl.line, column=decl.column, category="duplicate declaration",
            )
        field_types = {}
        for f in decl.fields:
            field_types[f.name] = resolve(f.type_expr, decl)
        structs[decl.name] = field_types

    def analyze_table(decl):
        if decl.name in structs or decl.name in tables:
            raise CompileError(
                f"'{decl.name}' is already declared",
                file=filename, line=decl.line, column=decl.column, category="duplicate declaration",
            )
        columns = {}
        for f in decl.fields:
            resolve(f.type_expr, decl)  # validates the type is known
            columns[f.name] = f.type_expr
        tables[decl.name] = columns

    def analyze_var_decl(decl, scope, is_global):
        declared_type = resolve(decl.type_expr, decl)
        if decl.init is not None:
            actual_type = infer(decl.init, scope)
            check_assignable(declared_type, actual_type, decl)
        kind = "constant" if decl.is_const else "variable"
        scope.define(decl.name, Symbol(decl.name, declared_type, kind, decl), decl, filename)

    def analyze_func(decl):
        return_type = resolve(decl.return_type, decl) if decl.return_type != "void" else None
        global_scope.define(decl.name, Symbol(decl.name, return_type, "function", decl), decl, filename)
        func_scope = Scope(global_scope)
        for p in decl.params:
            func_scope.define(p.name, Symbol(p.name, resolve(p.type_expr, decl), "parameter"), decl, filename)
        analyze_block(decl.body, func_scope, return_type=return_type)

    def analyze_event_handler(decl):
        handler_scope = Scope(global_scope)
        for p in decl.params:
            handler_scope.define(p.name, Symbol(p.name, resolve(p.type_expr, decl), "parameter"), decl, filename)
        analyze_block(decl.body, handler_scope, return_type=None)

    def analyze_statement(stmt, scope, return_type):
        if isinstance(stmt, ast.ImportDecl):
            imports.append(stmt.path)
        elif isinstance(stmt, ast.StructDecl):
            analyze_struct(stmt)
        elif isinstance(stmt, ast.TableDecl):
            analyze_table(stmt)
        elif isinstance(stmt, ast.FuncDecl):
            analyze_func(stmt)
        elif isinstance(stmt, ast.EventHandler):
            analyze_event_handler(stmt)
        elif isinstance(stmt, ast.VarDecl):
            analyze_var_decl(stmt, scope, scope is global_scope)
        elif isinstance(stmt, ast.IfStmt):
            cond_type = infer(stmt.test, scope)
            check_condition_bool(cond_type, stmt)
            analyze_block(stmt.then, scope, return_type)
            if stmt.orelse is not None:
                if isinstance(stmt.orelse, ast.IfStmt):
                    analyze_statement(stmt.orelse, scope, return_type)
                else:
                    analyze_block(stmt.orelse, scope, return_type)
        elif isinstance(stmt, ast.Return):
            if stmt.value is not None:
                actual = infer(stmt.value, scope)
                if return_type is not None:
                    check_assignable(return_type, actual, stmt, what="return value")
        elif isinstance(stmt, ast.Block):
            analyze_block(stmt, scope, return_type)
        elif isinstance(stmt, ast.ExprStmt):
            infer(stmt.expr, scope)
        # unrecognized statement kinds are ignored (no-op)

    def analyze_block(block, parent_scope, return_type):
        scope = Scope(parent_scope)
        for stmt in block.body:
            analyze_statement(stmt, scope, return_type)

    for stmt in program.body:
        analyze_statement(stmt, global_scope, None)

    return AnalyzedProgram(global_scope.vars, structs, tables, imports)
