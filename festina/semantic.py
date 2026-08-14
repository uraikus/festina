"""Semantic analysis -- claude.md #48 (error categories), #49 (symbol
table), #50 (type checking), plus the type-resolution/truthiness/equality
rules from #12-20, the struct/table distinction from #27, #28, #35, and
#55/#56 (int/float never mix directly; Math.floor/ceil/round/trunc and
int.toFloat() are the only conversions). #58 (struct/table namespace):
struct/table names live in `structs`/`tables`, never cross-checked
against `Scope` (variables/functions) -- separate namespaces by design.
#67/#68 (regex(), .test(), .match(), .replace()/.replaceAll()) follow
the same "recognized Call-on-Member pattern" approach Math.floor/
int.toFloat() already established -- Festina has no general concept of
methods on primitive types, so each one is checked by name against the
receiver's inferred type in _infer_call, not looked up in some method
table.

`analyze(program)` walks the AST top to bottom. None of this repo's
fixtures need forward references (structs/tables/functions are always
declared before use), so a single left-to-right pass is enough. This
also makes multi-file compilation (claude.md #5-6) work with no extra
machinery here: festina.imports.build_program already merged every
file's statements into one `program.body`, in dependency order, before
analyze() ever sees it -- the only thing this module does specially for
that is re-read each top-level statement's originating file (`.file`,
set by build_program) into the `filename` closure variable right before
analyzing it, so errors still name the right file (see the loop at the
bottom of analyze()).
"""
from . import ast
from . import types as types_mod
from .errors import CompileError

# claude.md #39, #41, #42, #32, #67, #69: builtin globals that don't
# need a programmer declaration. setTimeout/setInterval/clearTimeout/
# clearInterval are listed here for documentation/completeness, but
# _infer_call actually dispatches them through their own branch before
# ever reaching the BUILTIN_FUNCTIONS check below, since setTimeout/
# setInterval's first argument (a callback) needs structural handling
# no other builtin needs -- see the comment there.
BUILTIN_FUNCTIONS = {
    "log", "fail", "sqlite",
    "drawRect", "drawCircle", "drawText", "drawImage",
    "loadImage", "loadAudio",
    "regex",
    "setTimeout", "setInterval", "clearTimeout", "clearInterval",
}

_BUILTIN_RETURN_TYPES = {
    "loadImage": types_mod.ImageType(),
    "loadAudio": types_mod.AudioType(),
    "regex": types_mod.RegexType(),
}

# claude.md #55: int and float never mix directly in a binary operator.
_INT = types_mod.PrimitiveType("int")
_FLOAT = types_mod.PrimitiveType("float")
_NUMERIC_TYPES = (_INT, _FLOAT)
_TEXT = types_mod.PrimitiveType("text")
_BLOB = types_mod.PrimitiveType("blob")

# claude.md #37, #39: signatures for the builtins with real implementations
# (drawCircle/drawText/drawImage/loadImage) -- matches each function's own
# worked example in the spec exactly (e.g. drawRect(0, 0, 100, 100)).
# Builtins with no entry here (log, fail, sqlite) stay permissive:
# their args are inferred but not checked against a fixed signature,
# since claude.md leaves their shape open.
_BUILTIN_SIGNATURES = {
    "drawRect": (_INT, _INT, _INT, _INT),
    "drawCircle": (_INT, _INT, _INT),
    "drawText": (_TEXT, _INT, _INT),
    "drawImage": (types_mod.ImageType(), _INT, _INT),
    "loadImage": (_TEXT,),
    "loadAudio": (_TEXT,),  # claude.md #38
}
_REGEX = types_mod.RegexType()
_AUDIO = types_mod.AudioType()

# claude.md #56: float -> int, with an explicit rounding decision.
MATH_FUNCTIONS = {"floor", "ceil", "round", "trunc"}

# claude.md #40: these five event names are the only ones with a real
# runtime source (an X11 event of some kind -- see festina_runtime.h's
# doc comment on festina_run_event_loop), so only they get a fixed-
# signature check; codegen registers each compiled handler with the
# runtime through a fixed C function-pointer type per event, so a
# mismatched signature would be a silent ABI mismatch, not just an
# unusual choice. Any other event name (analyze_event_handler leaves it
# unconstrained) still compiles but is simply dead code -- nothing ever
# fires it. Each entry is (required-arg-types, human-readable signature
# for the error message) -- resize/close take no arguments, so their
# tuple is empty and `param_types != sig` alone (no separate length
# check needed) catches both "too many" and "wrong type" mistakes.
_EVENT_SIGNATURES = {
    "click": ((_INT, _INT), "(x:int, y:int)"),
    "mouse": ((_INT, _INT), "(x:int, y:int)"),
    "key": ((_TEXT,), "(key:text)"),
    "resize": ((), "no parameters"),
    "close": ((), "no parameters"),
}

# claude.md #39: clientWidth/clientHeight report the canvas window's
# current size as read-only global ints (borrowing the name from the
# DOM's Element.clientWidth/clientHeight, which they're the closest
# analogue to) -- pre-registered directly into global_scope below so a
# plain identifier reference just works through the same Scope.lookup
# every real global variable uses, and so Scope.define's own
# "already declared" check rejects a user var/function/struct/table
# with either name for free, with no extra machinery here.
_CLIENT_SIZE_GLOBALS = ("clientWidth", "clientHeight")

# claude.md #71: `environment` -- unlike clientWidth/clientHeight above,
# this is never a valid *value* on its own (only environment.NAME or
# environment[keyExpr] mean anything, and NAME is arbitrary -- there's
# no fixed set of members to register the way clientWidth/clientHeight
# are their own two complete globals). So this name is pre-registered
# into global_scope purely for Scope.define's "already declared"
# collision protection (same free "a user can't redeclare this name"
# guarantee clientWidth/clientHeight get) -- its Symbol's `type` is
# never actually read anywhere; _infer_member and the bare-Identifier
# check below both special-case the AST shape (an Identifier literally
# named "environment") directly, before any real type-based dispatch,
# precisely so environment.NAME still resolves correctly even though a
# bare `environment` reference is deliberately rejected (see the
# Identifier branch in infer()).
_ENVIRONMENT_NAME = "environment"


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
            if name == _ENVIRONMENT_NAME:
                # claude.md #71: environment is pre-registered into
                # global_scope purely so this collision is caught at all
                # (see analyze()) -- give it the same specific, named
                # treatment as a builtin-function-name collision
                # (analyze_func below) instead of the generic message,
                # since "already declared" alone doesn't explain *why*
                # (there's no earlier `environment` declaration in this
                # program to point a user back to).
                raise CompileError(
                    f"'{_ENVIRONMENT_NAME}' is reserved for reading environment "
                    f"variables ({_ENVIRONMENT_NAME}.NAME) and cannot be "
                    f"declared as a variable, constant, function, struct, or table",
                    file=filename, line=getattr(err_node, "line", 0),
                    column=getattr(err_node, "column", 0),
                    category="duplicate declaration",
                )
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
    if isinstance(type_expr, ast.MapTypeExpr):
        value_type = resolve_type_name(type_expr.value, structs, tables, filename, node)
        # claude.md #72: a map value is stored in one fixed 8-byte slot
        # (see types.MapType's own doc comment) -- an ArrayType (16
        # bytes: length + data pointer) or another MapType simply
        # doesn't fit, so this is rejected here at the point a map[T]
        # type is resolved, the same "unknown type"-shaped error a
        # genuinely undefined type name gets below.
        if isinstance(value_type, (types_mod.ArrayType, types_mod.MapType)):
            raise CompileError(
                f"map values cannot be {types_mod.type_name(value_type)} -- a map value is "
                f"stored in a single fixed-size slot, which an array or another map doesn't fit in",
                file=filename, line=getattr(node, "line", 0), column=getattr(node, "column", 0),
                category="unknown type",
            )
        return types_mod.MapType(value_type)
    name = type_expr
    if name in types_mod.PRIMITIVE_NAMES:
        return types_mod.PrimitiveType(name)
    if name == "img":
        return types_mod.ImageType()
    if name == "aud":
        return types_mod.AudioType()
    if name == "regex":
        return types_mod.RegexType()
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
    entry_filename = filename  # see the DatabaseURL check at the bottom

    # claude.md #39: clientWidth/clientHeight, see _CLIENT_SIZE_GLOBALS above.
    for _name in _CLIENT_SIZE_GLOBALS:
        global_scope.define(_name, Symbol(_name, _INT, "constant", None), None, filename)
    # claude.md #71: environment, see _ENVIRONMENT_NAME above -- type is
    # irrelevant (never consulted), only here so redeclaring it is a
    # duplicate-declaration error like any other reserved global.
    global_scope.define(_ENVIRONMENT_NAME, Symbol(_ENVIRONMENT_NAME, None, "constant", None), None, filename)

    def resolve(type_expr, node=None):
        return resolve_type_name(type_expr, structs, tables, filename, node)

    def check_assignable(declared, actual, node, what="value"):
        if actual is None or actual is NULL or declared is None:
            return
        # claude.md #36: blob's only worked example ("blob data =
        # 'path/to/file'") assigns a plain string literal -- which
        # infers as `text` (there's no separate blob-literal syntax
        # anywhere in claude.md) -- directly to a blob-declared
        # variable. Without this, that example wouldn't compile at all,
        # and blob would be completely unconstructible (no builtin
        # returns one either), so text -> blob is allowed here
        # one-directionally, matching the only direction claude.md ever
        # shows; codegen needs no special coercion for it either, since
        # blob and text already share the identical `ptr` runtime
        # representation (see _llvm_type).
        if declared == _BLOB and actual == _TEXT:
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
        if isinstance(expr, ast.RegexLit):
            # claude.md #67: /pattern/flags -- same RegexType() a
            # regex(...) call infers (see BUILTIN_FUNCTIONS/
            # _BUILTIN_RETURN_TYPES above); flags were already validated
            # for real (unsupported/duplicate letters) by the parser,
            # since a literal's flags are compile-time-known text, unlike
            # regex()'s flags argument.
            return types_mod.RegexType()
        if isinstance(expr, ast.TemplateLit):
            for e in expr.exprs:
                infer(e, scope)
            return types_mod.PrimitiveType("text")
        if isinstance(expr, ast.ArrayLit):
            elem_type = None
            # concrete_type tracks the first non-null element's type, purely
            # to catch a genuine mismatch between two concrete element types
            # (e.g. [1, 'x', true]) -- before this check existed, a mixed
            # literal like that reached codegen and failed as a raw,
            # confusing "LLVM object emission failed" error instead of a
            # normal CompileError, the same class of gap the security.md
            # audit fixed for comparisons/array indices/etc. but missed
            # here. elem_type itself is left tracking the *last* element's
            # type, same as before this check -- that's what feeds the
            # returned ArrayType below, and several existing null-element
            # corner cases (e.g. `[5, null]` failing against a declared
            # arr[int]) already depend on that exact "last wins" value;
            # this fix only adds a mismatch check, it doesn't change what
            # gets returned.
            concrete_type = None
            for e in expr.elements:
                elem_type = infer(e, scope)
                if elem_type is not None and elem_type is not NULL:
                    if concrete_type is None:
                        concrete_type = elem_type
                    elif elem_type != concrete_type:
                        raise CompileError(
                            f"array literal elements must all be the same type, "
                            f"found {types_mod.type_name(concrete_type)} and "
                            f"{types_mod.type_name(elem_type)}",
                            file=filename, line=getattr(e, "line", 0), column=getattr(e, "column", 0),
                            category="invalid operand type",
                        )
            return types_mod.ArrayType(elem_type) if elem_type is not None else None
        if isinstance(expr, ast.MapLit):
            # claude.md #72: { key: value, ... } -- every key must be
            # text (checked per-entry, unlike ArrayLit's value type
            # above which only ever keeps the *last* element's type --
            # a key's type genuinely needs checking every time, since
            # nothing else ever constrains it the way a declared
            # map[T]'s value slot constrains values). value_type follows
            # ArrayLit's own convention exactly: None (needs a declared
            # type for context, e.g. an empty `{}`) if there's nothing
            # to infer it from.
            value_type = None
            # Duplicate-literal-key detection: "last value wins" for a
            # repeated key (claude.md #72) is fine as a runtime rule when
            # the key is a general text expression -- there's no way to
            # know at compile time whether two different expressions
            # produce the same string. But when BOTH keys in a pair are
            # plain string literals (ast.StringLit, not a variable/
            # template/other expression), the duplicate is knowable right
            # now, at zero runtime cost, and it's essentially always a
            # typo rather than something intentional -- the same
            # "catch it before it runs" instinct as the const-reassignment
            # and void-return checks (security.md's audit). Tracked by
            # literal string value, first-occurrence node only (so the
            # error always points at the *second*, redundant one).
            seen_literal_keys = {}
            for key_expr, val_expr in expr.entries:
                key_type = infer(key_expr, scope)
                if key_type is not None and key_type is not NULL and key_type != _TEXT:
                    raise CompileError(
                        f"map key must be text, found {types_mod.type_name(key_type)}",
                        file=filename, line=getattr(key_expr, "line", 0), column=getattr(key_expr, "column", 0),
                        category="invalid operand type",
                    )
                if isinstance(key_expr, ast.StringLit):
                    if key_expr.value in seen_literal_keys:
                        raise CompileError(
                            f"duplicate map key '{key_expr.value}' in this literal "
                            f"(the earlier entry for this key would never be seen)",
                            file=filename, line=getattr(key_expr, "line", 0), column=getattr(key_expr, "column", 0),
                            category="invalid operand type",
                        )
                    seen_literal_keys[key_expr.value] = key_expr
                value_type = infer(val_expr, scope)
            return types_mod.MapType(value_type) if value_type is not None else None
        if isinstance(expr, ast.Identifier):
            # claude.md #71: `environment` alone means nothing -- only
            # environment.NAME/environment[keyExpr] do (see
            # _ENVIRONMENT_NAME above). Checked by name, before the
            # generic scope.lookup below, since environment IS present
            # in global_scope (for collision protection only) and would
            # otherwise silently return its placeholder `type=None`
            # here instead of a clear, specific error.
            if expr.name == _ENVIRONMENT_NAME:
                raise CompileError(
                    f"'{_ENVIRONMENT_NAME}' must be accessed as {_ENVIRONMENT_NAME}.NAME "
                    f"(e.g. {_ENVIRONMENT_NAME}.DATABASE_URL), not used by itself",
                    file=filename, line=expr.line, column=expr.column,
                    category="invalid field access",
                )
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
            # claude.md #63: ".length" is read-only -- caught here, before
            # the generic infer(expr.target) below, which would otherwise
            # happily type-check `arr.length = n` as a plain int
            # assignment (the ArrayType branch in _infer_member has no
            # way to tell a read apart from a write target).
            if (isinstance(expr.target, ast.Member) and not expr.target.computed
                    and expr.target.prop == "length"
                    and isinstance(infer(expr.target.obj, scope), types_mod.ArrayType)):
                raise CompileError(
                    "'.length' is read-only and cannot be assigned to",
                    file=filename, line=getattr(expr, "line", 0), column=getattr(expr, "column", 0),
                    category="invalid assignment",
                )
            # claude.md #39: clientWidth/clientHeight are read-only too --
            # same reasoning and same "catch it before the generic
            # target_type/value_type check below" placement as .length
            # above, since that check alone has no way to tell a read
            # from a write target.
            if isinstance(expr.target, ast.Identifier) and expr.target.name in _CLIENT_SIZE_GLOBALS:
                raise CompileError(
                    f"'{expr.target.name}' is read-only and cannot be assigned to",
                    file=filename, line=getattr(expr, "line", 0), column=getattr(expr, "column", 0),
                    category="invalid assignment",
                )
            # claude.md #71: environment.NAME/environment[keyExpr] are
            # read-only too -- same placement/reasoning as .length and
            # clientWidth/clientHeight just above.
            if (isinstance(expr.target, ast.Member)
                    and isinstance(expr.target.obj, ast.Identifier)
                    and expr.target.obj.name == _ENVIRONMENT_NAME):
                raise CompileError(
                    f"'{_ENVIRONMENT_NAME}' is read-only and cannot be assigned to",
                    file=filename, line=getattr(expr, "line", 0), column=getattr(expr, "column", 0),
                    category="invalid assignment",
                )
            # claude.md #22: a `const`-declared variable cannot be
            # reassigned -- the whole point of "constant," and needed
            # for "Constants should be available for compiler
            # optimization" to actually hold (an optimization assuming
            # a const never changes would be unsafe if reassignment
            # were silently allowed). Checked here for the same reason
            # .length/clientWidth's read-only checks are: the generic
            # target_type/value_type check below only verifies type
            # compatibility, it can't tell a legal write target from an
            # illegal one. Postfix ++/-- already separately rejects a
            # constant operand (see PostfixOp below) -- that's a
            # different AST node, so it needs its own check rather than
            # sharing this one.
            if isinstance(expr.target, ast.Identifier):
                target_sym = scope.lookup(expr.target.name)
                if target_sym is not None and target_sym.kind == "constant":
                    raise CompileError(
                        f"cannot assign to constant '{expr.target.name}'",
                        file=filename, line=getattr(expr, "line", 0), column=getattr(expr, "column", 0),
                        category="invalid assignment",
                    )
            target_type = infer(expr.target, scope)
            value_type = infer(expr.value, scope)
            check_assignable(target_type, value_type, expr)
            return target_type
        if isinstance(expr, ast.PostfixOp):
            # claude.md #66: postfix ++/-- -- valid only on a mutable int
            # variable.
            if not isinstance(expr.operand, ast.Identifier):
                raise CompileError(
                    f"'{expr.op}' can only be used on a variable, not an arbitrary expression",
                    file=filename, line=getattr(expr, "line", 0), column=getattr(expr, "column", 0),
                    category="invalid operand type",
                )
            sym = scope.lookup(expr.operand.name)
            if sym is None:
                raise CompileError(
                    f"unknown variable '{expr.operand.name}'",
                    file=filename, line=expr.operand.line, column=expr.operand.column,
                    category="unknown variable",
                )
            if sym.type != _INT:
                raise CompileError(
                    f"'{expr.op}' requires an int operand, found {types_mod.type_name(sym.type)}",
                    file=filename, line=getattr(expr, "line", 0), column=getattr(expr, "column", 0),
                    category="invalid operand type",
                )
            if sym.kind == "constant":
                raise CompileError(
                    f"cannot use '{expr.op}' on constant '{expr.operand.name}'",
                    file=filename, line=getattr(expr, "line", 0), column=getattr(expr, "column", 0),
                    category="invalid operand type",
                )
            return _INT
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
            if expr.op in ("==", "!="):
                # claude.md #18 shows == / != between two values of the
                # same type; it never shows (and #2's "no implicit
                # coercion" principle rules out) comparing genuinely
                # different types like int and text. Without this check
                # something like `5 == 'x'` passed straight through to
                # codegen, which has no general fallback for a type
                # mismatch here (only the text-specific branch of
                # _emit_binop even looks at the operand types) and
                # produced invalid LLVM IR instead of a clear compile
                # error. NULL is valid against everything (#25); blob
                # and text are mutually comparable since check_assignable
                # already allows text -> blob assignment and they share
                # the identical runtime representation (both `ptr`,
                # compared via the same festina_str_eq either way).
                compatible = (
                    left is None or right is None
                    or left is NULL or right is NULL
                    or left == right
                    or {left, right} == {_TEXT, _BLOB}
                )
                if not compatible:
                    raise CompileError(
                        f"cannot compare {types_mod.type_name(left)} and "
                        f"{types_mod.type_name(right)} with {expr.op}",
                        file=filename, line=getattr(expr, "line", 0), column=getattr(expr, "column", 0),
                        category="invalid operand type",
                    )
                return types_mod.PrimitiveType("bool")
            if expr.op in ("<", ">", "<=", ">="):
                # claude.md never defines ordering for anything but
                # numbers, and codegen only ever implements int/float
                # comparisons for these operators (text raises its own
                # clear CodegenError already, but nothing stopped e.g.
                # two `bool` operands from silently reaching codegen's
                # numeric icmp path and being "ordered" in a way
                # claude.md never sanctions).
                numeric_or_null = (_INT, _FLOAT, NULL)
                left_ok = left is None or left in numeric_or_null
                right_ok = right is None or right in numeric_or_null
                if not (left_ok and right_ok):
                    raise CompileError(
                        f"'{expr.op}' requires int or float operands, found "
                        f"{types_mod.type_name(left)} and {types_mod.type_name(right)}",
                        file=filename, line=getattr(expr, "line", 0), column=getattr(expr, "column", 0),
                        category="invalid operand type",
                    )
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
        # claude.md #71: environment.NAME / environment[keyExpr] --
        # checked structurally (an Identifier literally named
        # "environment"), before the generic infer(expr.obj, scope)
        # below, since environment isn't a real value to infer the type
        # of (see _ENVIRONMENT_NAME's own comment) -- it's a namespace,
        # not a variable, so its "object" is never actually evaluated.
        if isinstance(expr.obj, ast.Identifier) and expr.obj.name == _ENVIRONMENT_NAME:
            if expr.computed:
                key_type = infer(expr.prop, scope) if isinstance(expr.prop, ast.Node) else None
                if key_type is not None and key_type is not NULL and key_type != _TEXT:
                    raise CompileError(
                        f"{_ENVIRONMENT_NAME}[...] key must be text, found {types_mod.type_name(key_type)}",
                        file=filename, line=getattr(expr, "line", 0), column=getattr(expr, "column", 0),
                        category="invalid operand type",
                    )
            return _TEXT
        obj_type = infer(expr.obj, scope)
        if expr.computed:
            idx_type = infer(expr.prop, scope) if isinstance(expr.prop, ast.Node) else None
            if isinstance(obj_type, types_mod.MapType):
                # claude.md #72: npcHealths['npc1'] / npcHealths[key] --
                # keys are always text (never int, unlike array
                # indexing just below), and a missing key is not a
                # compile-time concern at all (it's claude.md #72's own
                # "returns null" runtime behavior -- see codegen.py's
                # _emit_map_get), so there's nothing else to check here
                # beyond the key's own type.
                if idx_type is not None and idx_type is not NULL and idx_type != _TEXT:
                    raise CompileError(
                        f"map key must be text, found {types_mod.type_name(idx_type)}",
                        file=filename, line=getattr(expr, "line", 0), column=getattr(expr, "column", 0),
                        category="invalid operand type",
                    )
                return obj_type.value
            if not isinstance(obj_type, types_mod.ArrayType):
                raise CompileError(
                    f"cannot index into {types_mod.type_name(obj_type)}",
                    file=filename, line=getattr(expr, "line", 0), column=getattr(expr, "column", 0),
                    category="invalid field access",
                )
            # claude.md #65: "The index expression must resolve to
            # int" -- idx_type used to be inferred and then simply
            # discarded, never actually checked against anything. A
            # float or text index (e.g. `a[1.5]`, `a['x']`) passed
            # semantic analysis and reached codegen, which emits a
            # `getelementptr` using the index value's own LLVM
            # representation regardless of its Festina type --
            # producing invalid LLVM IR (a raw double or a `ptr` used
            # where an `i64` GEP index is required) and a confusing
            # internal "LLVM object emission failed" error instead of a
            # clear compile-time one. Covers both a read (`a[i]`) and a
            # write target (`a[i] = v`), since Assign's target_type
            # also goes through this same function.
            if idx_type is not None and idx_type is not NULL and idx_type != _INT:
                raise CompileError(
                    f"array index must be int, found {types_mod.type_name(idx_type)}",
                    file=filename, line=getattr(expr, "line", 0), column=getattr(expr, "column", 0),
                    category="invalid operand type",
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
        if isinstance(obj_type, types_mod.ArrayType):
            # claude.md #63: the only field an array has is the built-in
            # read-only `.length` (int) -- anything else is an error, not
            # a permissive fallthrough, matching how struct/table field
            # access is handled just above.
            if expr.prop != "length":
                raise CompileError(
                    f"array has no field '{expr.prop}' (did you mean '.length'?)",
                    file=filename, line=expr.line, column=expr.column,
                    category="invalid field access",
                )
            return types_mod.PrimitiveType("int")
        if isinstance(obj_type, types_mod.ImageType):
            return None  # permissive: claude.md #37 defines no methods on img at all
        # claude.md #38: aud's play()/stop()/isPlaying() are only
        # recognized as Call-on-Member patterns (see _infer_call) --
        # this branch is for a bare `music.play` reference with no
        # call, which was never a valid thing to write, so it's a hard
        # error like everything else below, not a silent fallthrough
        # (AudioType used to share ImageType's permissive branch above,
        # back when neither had any real methods modeled).
        raise CompileError(
            f"cannot access field '{expr.prop}' on {types_mod.type_name(obj_type)}",
            file=filename, line=expr.line, column=expr.column,
            category="invalid field access",
        )

    def _infer_call(expr, scope):
        callee = expr.callee
        if isinstance(callee, ast.Identifier):
            name = callee.name
            if name in ("setTimeout", "setInterval"):
                # claude.md #69 -- see BUILTIN_FUNCTIONS's comment
                # above and festina_runtime.h's doc comment on
                # festina_set_timeout/_interval. The callback has to be
                # the bare name of an already-declared function (Festina
                # has no first-class functions/closures -- see
                # codegen.py's "functions are not first-class values yet"
                # CodegenError), checked structurally here rather than
                # through infer(), which would otherwise just return that
                # function's own return type like any other identifier
                # reference -- it's not being *used* as a value here, its
                # *declaration* is what's being validated.
                if len(expr.args) != 2:
                    raise CompileError(
                        f"{name}() expects 2 arguments (a callback function and a "
                        f"delay in milliseconds), got {len(expr.args)}",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                callback_expr = expr.args[0]
                if not isinstance(callback_expr, ast.Identifier):
                    raise CompileError(
                        f"{name}()'s first argument must be the name of a declared "
                        f"function, not an arbitrary expression",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                callback_sym = scope.lookup(callback_expr.name)
                if callback_sym is None or callback_sym.kind != "function":
                    raise CompileError(
                        f"'{callback_expr.name}' is not a declared function",
                        file=filename, line=callback_expr.line, column=callback_expr.column,
                        category="unknown function",
                    )
                if callback_sym.type is not None or callback_sym.node.params:
                    raise CompileError(
                        f"the callback passed to {name}() must take no parameters and "
                        f"return nothing -- declare it as "
                        f"'void func {callback_expr.name}() {{ ... }}'",
                        file=filename, line=callback_expr.line, column=callback_expr.column,
                        category="invalid function argument type",
                    )
                delay_type = infer(expr.args[1], scope)
                if delay_type is not None and delay_type is not NULL and delay_type != _INT:
                    raise CompileError(
                        f"{name}()'s delay argument must be an int (milliseconds), "
                        f"found {types_mod.type_name(delay_type)}",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                return _INT  # a timer id, usable with clearTimeout()/clearInterval()
            if name in ("clearTimeout", "clearInterval"):
                if len(expr.args) != 1:
                    raise CompileError(
                        f"{name}() expects exactly 1 argument (a timer id), got {len(expr.args)}",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                id_type = infer(expr.args[0], scope)
                if id_type is not None and id_type is not NULL and id_type != _INT:
                    raise CompileError(
                        f"{name}()'s argument must be an int (a timer id), "
                        f"found {types_mod.type_name(id_type)}",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                return None
            if name in BUILTIN_FUNCTIONS:
                sig = _BUILTIN_SIGNATURES.get(name)
                if sig is None:
                    for a in expr.args:
                        if name == "sqlite" and isinstance(a, ast.ArrayLit):
                            # claude.md #33: sqlite()'s parameter list is
                            # passed as an array literal, but -- unlike
                            # every real arr[T] value in the language --
                            # it's explicitly allowed (and, per #33's own
                            # worked example, [1, 'Patrick'], expected) to
                            # mix types: one bound value per '?'
                            # placeholder, each independently int/float/
                            # text/bool/null. Infer each element on its
                            # own (still catching a genuinely broken
                            # sub-expression) instead of delegating to the
                            # ArrayLit branch above, whose same-element-
                            # type check exists for real arr[T] values and
                            # would otherwise wrongly reject this one,
                            # spec-mandated exception.
                            for e in a.elements:
                                infer(e, scope)
                        else:
                            infer(a, scope)
                else:
                    if len(expr.args) != len(sig):
                        raise CompileError(
                            f"{name}() expects {len(sig)} argument(s), got {len(expr.args)}",
                            file=filename, line=callee.line, column=callee.column,
                            category="invalid function argument type",
                        )
                    for i, (arg_expr, expected) in enumerate(zip(expr.args, sig)):
                        arg_type = infer(arg_expr, scope)
                        if arg_type is not None and arg_type is not NULL and arg_type != expected:
                            raise CompileError(
                                f"{name}()'s argument {i + 1} expects "
                                f"{types_mod.type_name(expected)}, found {types_mod.type_name(arg_type)}",
                                file=filename, line=callee.line, column=callee.column,
                                category="invalid function argument type",
                            )
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
            # int/float/bool.toText() -> text -- an explicit spelling of
            # the same stringification template interpolation already
            # does implicitly for these three types (see codegen.py's
            # _to_text); the receiver check is against the SAME three
            # types _to_text itself handles, kept in sync deliberately.
            if (callee.prop == "toText" and not expr.args
                    and infer(callee.obj, scope) in (_INT, _FLOAT, types_mod.PrimitiveType("bool"))):
                return _TEXT
            # claude.md #67: pattern.test(value:text) -> bool
            if callee.prop == "test" and infer(callee.obj, scope) == _REGEX:
                if len(expr.args) != 1:
                    raise CompileError(
                        f"test() expects exactly 1 argument, got {len(expr.args)}",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                arg_type = infer(expr.args[0], scope)
                if arg_type is not None and arg_type is not NULL and arg_type != _TEXT:
                    raise CompileError(
                        f"test() expects a text argument, found {types_mod.type_name(arg_type)}",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                return types_mod.PrimitiveType("bool")
            # claude.md #68: value.match(pattern:regex) -> text (or null,
            # if there's no match -- claude.md #25: null is valid for
            # every type).
            if callee.prop == "match" and infer(callee.obj, scope) == _TEXT:
                if len(expr.args) != 1:
                    raise CompileError(
                        f"match() expects exactly 1 argument, got {len(expr.args)}",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                arg_type = infer(expr.args[0], scope)
                if arg_type is not None and arg_type is not NULL and arg_type != _REGEX:
                    raise CompileError(
                        f"match() expects a regex argument, found {types_mod.type_name(arg_type)}",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                return _TEXT
            # claude.md #68: value.replace(search, replacement:text) -> text
            #                value.replaceAll(search, replacement:text) -> text
            # search may be text (a literal substring match) or regex.
            if callee.prop in ("replace", "replaceAll") and infer(callee.obj, scope) == _TEXT:
                if len(expr.args) != 2:
                    raise CompileError(
                        f"{callee.prop}() expects exactly 2 arguments, got {len(expr.args)}",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                search_type = infer(expr.args[0], scope)
                if search_type is not None and search_type is not NULL and search_type not in (_TEXT, _REGEX):
                    raise CompileError(
                        f"{callee.prop}()'s first argument must be text or regex, "
                        f"found {types_mod.type_name(search_type)}",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                replacement_type = infer(expr.args[1], scope)
                if replacement_type is not None and replacement_type is not NULL and replacement_type != _TEXT:
                    raise CompileError(
                        f"{callee.prop}()'s replacement argument must be text, "
                        f"found {types_mod.type_name(replacement_type)}",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                return _TEXT
            # claude.md #38: music.play() / music.stop() / music.isPlaying()
            # -- the only three methods claude.md defines for aud, so
            # (unlike log/fail/sqlite's deliberately open shape) any
            # other method call on an aud value falls through to the
            # generic "Member call" fallback below and fails there via
            # _infer_member's now-strict AudioType handling, the same
            # way an unknown struct field does.
            if callee.prop in ("play", "stop") and infer(callee.obj, scope) == _AUDIO:
                if expr.args:
                    raise CompileError(
                        f"{callee.prop}() takes no arguments, got {len(expr.args)}",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                return None
            if callee.prop == "isPlaying" and infer(callee.obj, scope) == _AUDIO:
                if expr.args:
                    raise CompileError(
                        f"isPlaying() takes no arguments, got {len(expr.args)}",
                        file=filename, line=callee.line, column=callee.column,
                        category="invalid function argument type",
                    )
                return types_mod.PrimitiveType("bool")
            # claude.md #72: npcHealths.forEach(callback) -- the callback
            # is checked structurally, the same way setTimeout/
            # setInterval's callback is above: it has to be the bare
            # name of an already-declared function (Festina has no
            # first-class functions/closures), not an arbitrary
            # expression, since its *declaration* (parameter types) is
            # what's being validated here, not its value.
            if callee.prop == "forEach":
                obj_type = infer(callee.obj, scope)
                if isinstance(obj_type, types_mod.MapType):
                    if len(expr.args) != 1:
                        raise CompileError(
                            f"forEach() expects exactly 1 argument (a callback function), "
                            f"got {len(expr.args)}",
                            file=filename, line=callee.line, column=callee.column,
                            category="invalid function argument type",
                        )
                    callback_expr = expr.args[0]
                    if not isinstance(callback_expr, ast.Identifier):
                        raise CompileError(
                            "forEach()'s argument must be the name of a declared function, "
                            "not an arbitrary expression",
                            file=filename, line=callee.line, column=callee.column,
                            category="invalid function argument type",
                        )
                    callback_sym = scope.lookup(callback_expr.name)
                    if callback_sym is None or callback_sym.kind != "function":
                        raise CompileError(
                            f"'{callback_expr.name}' is not a declared function",
                            file=filename, line=callback_expr.line, column=callback_expr.column,
                            category="unknown function",
                        )
                    params = callback_sym.node.params
                    value_type_name = types_mod.type_name(obj_type.value)
                    expected_sig = f"({value_type_name} value, text key)"
                    if callback_sym.type is not None or len(params) != 2:
                        raise CompileError(
                            f"the callback passed to forEach() must take exactly 2 parameters "
                            f"(the map's value, then its key) and return nothing -- declare it as "
                            f"'void func {callback_expr.name}(v:{value_type_name}, key:text) {{ ... }}'",
                            file=filename, line=callback_expr.line, column=callback_expr.column,
                            category="invalid function argument type",
                        )
                    value_param_type = resolve(params[0].type_expr, callee)
                    key_param_type = resolve(params[1].type_expr, callee)
                    if value_param_type != obj_type.value:
                        raise CompileError(
                            f"forEach()'s callback first parameter must be {value_type_name} "
                            f"(this map's value type), found {types_mod.type_name(value_param_type)}",
                            file=filename, line=callback_expr.line, column=callback_expr.column,
                            category="invalid function argument type",
                        )
                    if key_param_type != _TEXT:
                        raise CompileError(
                            f"forEach()'s callback second parameter must be text (the map's key), "
                            f"found {types_mod.type_name(key_param_type)}",
                            file=filename, line=callback_expr.line, column=callback_expr.column,
                            category="invalid function argument type",
                        )
                    return None
        # Member call, e.g. someUnknownMethod() on a struct-shaped
        # receiver -- validates the member access itself (so an unknown
        # method on a real type still fails with a specific message
        # rather than silently passing through as untyped).
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
        # `log`/`fail`/`sqlite` are already lexer keywords, so a
        # function can never be declared with those names in the first
        # place -- but drawRect/drawCircle/drawText/drawImage/
        # loadImage/loadAudio/regex/setTimeout/setInterval/
        # clearTimeout/clearInterval are ordinary identifiers, only
        # recognized as builtins by name inside _infer_call's Call
        # dispatch (which always checks the builtin name *before* ever
        # looking the name up in scope). A user function declared with
        # one of those names would therefore compile fine but be
        # permanently unreachable -- every call to it resolves to the
        # builtin instead, silently. This used to be accepted (see
        # security.md's audit writeup) on the reasoning that none of
        # graphics/audio/timers were implemented yet, so the collision
        # couldn't actually bite; now that they are, it can.
        if decl.name in BUILTIN_FUNCTIONS:
            raise CompileError(
                f"'{decl.name}' is a builtin function name and cannot be used to "
                f"declare a function -- a function declared with this name would be "
                f"permanently unreachable, since every call to '{decl.name}(...)' "
                f"resolves to the builtin instead",
                file=filename, line=decl.line, column=decl.column,
                category="duplicate declaration",
            )
        return_type = resolve(decl.return_type, decl) if decl.return_type != "void" else None
        global_scope.define(decl.name, Symbol(decl.name, return_type, "function", decl), decl, filename)
        func_scope = Scope(global_scope)
        for p in decl.params:
            func_scope.define(p.name, Symbol(p.name, resolve(p.type_expr, decl), "parameter"), decl, filename)
        analyze_block(decl.body, func_scope, return_type=return_type)

    def analyze_event_handler(decl):
        # claude.md #40: see _EVENT_SIGNATURES above -- click/mouse/key/
        # resize/close get a fixed-signature check; any other event name
        # is unconstrained (and simply never fires -- there's no event
        # source claude.md defines for it).
        entry = _EVENT_SIGNATURES.get(decl.name)
        if entry is not None:
            sig, help_text = entry
            param_types = tuple(resolve(p.type_expr, decl) for p in decl.params)
            if param_types != sig:
                raise CompileError(
                    f"on {decl.name}(...) must declare exactly {help_text}, "
                    f"matching claude.md #40's own example",
                    file=filename, line=decl.line, column=decl.column,
                    category="invalid function argument type",
                )
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
        elif isinstance(stmt, ast.WhileStmt):
            # claude.md #61: condition must be bool, no truthy/falsy
            # conversion -- same rule check_condition_bool already
            # enforces for if/ternary.
            cond_type = infer(stmt.test, scope)
            check_condition_bool(cond_type, stmt)
            analyze_block(stmt.body, scope, return_type)
        elif isinstance(stmt, ast.ForStmt):
            # claude.md #60: "the initialization variable is scoped to
            # the loop body" -- a fresh scope holds just the loop
            # variable, and analyze_block below nests the body under
            # *that* (not the outer scope), so the variable is visible
            # in the condition/update/body but nowhere after the loop.
            loop_scope = Scope(scope)
            analyze_var_decl(stmt.init, loop_scope, is_global=False)
            cond_type = infer(stmt.test, loop_scope)
            check_condition_bool(cond_type, stmt)
            infer(stmt.update, loop_scope)
            analyze_block(stmt.body, loop_scope, return_type)
        elif isinstance(stmt, ast.Return):
            # claude.md #23: "A function that does not return a value
            # uses void" implies the converse too -- a void function
            # doesn't return one, and a non-void function does. Neither
            # direction used to be checked: `void func f() { return 5 }`
            # silently discarded the 5 (LLVM `ret void` regardless of
            # what expression was given -- the expression itself was
            # still evaluated, so a side-effecting return value's
            # effects still happened, just its result vanished), and
            # `int func f() { return }` (no value) fell through with
            # nothing checked at all in either branch below.
            if stmt.value is not None:
                actual = infer(stmt.value, scope)
                if return_type is None:
                    raise CompileError(
                        "cannot return a value from a void function",
                        file=filename, line=getattr(stmt, "line", 0), column=getattr(stmt, "column", 0),
                        category="invalid return type",
                    )
                check_assignable(return_type, actual, stmt, what="return value")
            elif return_type is not None:
                raise CompileError(
                    f"function must return a value of type {types_mod.type_name(return_type)}, "
                    f"not a bare 'return'",
                    file=filename, line=getattr(stmt, "line", 0), column=getattr(stmt, "column", 0),
                    category="invalid return type",
                )
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
        # claude.md #6: a multi-file program (festina.imports.build_program)
        # is one merged ast.Program, but errors should still point at
        # whichever source file a statement actually came from. Every
        # nested function above closes over `filename` as a free
        # variable, resolved fresh on each call (Python's late-binding
        # closures) rather than captured once -- so reassigning it here,
        # right before analyzing each top-level statement, is enough to
        # correctly thread the right filename through everything that
        # statement's analysis touches, however deeply nested, with no
        # changes needed to any individual `raise CompileError(...)` site.
        # A single-file program tags every statement with that one file
        # (see build_program), so this is a no-op change of behavior for
        # today's single-file callers.
        filename = getattr(stmt, "file", filename)
        analyze_statement(stmt, global_scope, None)

    # claude.md #70: DatabaseURL = <expr> -- festina.imports.build_program
    # already validated *position* (first statement of the entry file,
    # before any other code or import) and pulled the value expression
    # out to program.database_url; this is the one thing left to check,
    # the same as any other value: it must actually be text. Uses
    # entry_filename (captured before the loop above reassigned
    # `filename` statement by statement) since this expression always
    # comes from the entry file specifically -- build_program never
    # recognizes it in an imported file.
    if getattr(program, "database_url", None) is not None:
        database_url_type = infer(program.database_url, global_scope)
        if database_url_type is not None and database_url_type is not NULL and database_url_type != _TEXT:
            raise CompileError(
                f"DatabaseURL must be text, found {types_mod.type_name(database_url_type)}",
                file=entry_filename, line=getattr(program.database_url, "line", 0),
                column=getattr(program.database_url, "column", 0),
                category="invalid assignment",
            )

    return AnalyzedProgram(global_scope.vars, structs, tables, imports)
