"""LLVM IR code generation -- claude.md #47 (executable generation) and
the runtime-facing halves of #7/#8 (entry point + startup), #26 (arrays),
#29-31 (automatic SQLite schema sync), #41/#42 (log/fail), #45 (string
interpolation), #55-57 (int.toFloat(), Math.floor/ceil/round/trunc,
division/modulo by zero).

Scope: primitives (int/float/bool/text), global and local variables and
constants, functions, if/else, return, the full expression grammar
(arithmetic/comparison/logical/ternary/template strings), structs
(GEP field access; see the heap-allocation note below), arrays (arr[T]
literals, indexed get/set, nesting -- see the FESTINA_ARRAY_LLVM_TYPE
note below), and automatic table schema sync against festina.sqlite via
the festina_runtime C helpers.

NOT implemented yet (raises CodegenError with a clear message):
sqlite() queries and parameterized statements (so arr[Table] stays
unusable -- claude.md never defines a way to construct a Table-typed
value without a query), graphics (img/drawRect/...), audio
(aud/loadAudio/...), and `on eventName` event handlers. See README.md
for the up-to-date status list.

Uses LLVM's opaque-pointer IR (`ptr` everywhere) to match clang 15+'s
default, so no manual bitcasting between pointer "flavors" is needed.

Struct storage is always heap-allocated (calloc), never a stack alloca,
even for a struct declared local to a function. claude.md #43 prefers
stack allocation "when the value's lifetime permits it," which would be
true for a struct that provably never leaves its declaring function --
but this codegen doesn't do escape analysis, and a struct's address
genuinely can outlive its function (returned, stored in an array or
another struct's field, ...). Stack-allocating unconditionally silently
corrupted every one of those cases (verified: returning a local struct
by value produced garbage at the call site). calloc'ing every struct is
the simple, uniformly-correct choice per #54's ambiguity rule ("prefer
the simplest implementation" / "prefer performance" only when it doesn't
also mean "prefer incorrect") -- at the cost of leaking, same tradeoff
arrays already make (see below) and for the same underlying reason: no
GC/refcounting runtime exists yet to free either one (claude.md #43).
calloc (not malloc) so uninitialized fields read as zero, matching a
global struct's `zeroinitializer` -- local and global structs now start
identically rather than one being zeroed and the other garbage.

Array representation: claude.md #26 specifies arr[T]'s type-resolution
rules but not its runtime representation, push/pop-style operations, a
length accessor, or any loop construct to iterate one with (the spec has
no `for`/`while` at all) -- claude.md #54's ambiguity rule says to treat
undefined behavior as unresolved rather than invent it, so this codegen
only implements what #26 actually specifies: declaring an arr[T]
(elements sized and typed at compile time -- #26's own wording),
constructing one from an array literal, and reading/writing an element
by an arbitrary index expression. No `.length`, no growth, no bounds
checking (documented in README.md as a known gap, consistent with #14's
performance-first / low-runtime-overhead priority in the absence of a
spec requirement either way).

Every arr[T], regardless of T, lowers to the same fixed-size aggregate
FESTINA_ARRAY_LLVM_TYPE = `%struct._FestinaArray = type { i64, ptr }`
(length, data pointer) -- Festina's own type system (not the generated
IR) is what keeps different arr[T] values from mixing, exactly like
festina.types keeps PrimitiveType/StructType/etc. distinct without a
runtime tag (claude.md #11). Named `_FestinaArray` (leading underscore)
rather than a plainer name specifically to make an accidental collision
with a user-declared `struct _FestinaArray { ... }` less likely --
Festina's identifier grammar still technically allows a user to write
that exact name, so this lowers the odds without eliminating the
possibility; a Festina identifier can never collide with an LLVM name
containing a `.` in the middle the way `struct_llvm_name` produces
(`%struct.Name`), so a scheme that didn't reuse that "%struct." prefix
at all would close the gap completely if it's ever worth the churn. The
data pointer is malloc'd and never freed -- claude.md #43 promises
automatic memory management this compiler doesn't implement yet (no GC,
no refcounting runtime), so for now arrays leak; see README.md.

Null for int/float (claude.md #10, #25, #57): i64/double have no spare
bit pattern for "null" the way a pointer has NULL, and LLVM's `null`
literal is only valid for pointer types -- storing it into an i64/double
slot is a link error (verified). Represented with a reserved sentinel
instead (INT_NULL_CONST = i64 minimum; FLOAT_NULL_CONST = a quiet NaN),
per #57's "implementation-defined" allowance. This is what
division/modulo by zero produce (#57) and what a literal `null` lowers
to when assigned/passed/returned as int or float (see
_emit_value_for) -- before this change the bare "null" keyword was used
unconditionally, which broke exactly the same way `int x = null` did.
Using an already-null int or float as an operand in further arithmetic
is unresolved per #57 --
NaN naturally propagates through float arithmetic (for free), but
INT_NULL_CONST is just an ordinary (if extreme) i64 to int arithmetic,
so it does not propagate the same way. `bool` has the identical "null
literal for a non-pointer type" problem (verified: same link error) but
is NOT fixed here -- claude.md never asked for bool-null specifically,
and fixing it would mean widening bool from i1 to a multi-value
encoding everywhere bool is stored (fields, params, array elements),
well beyond the int/float scope this change actually needed. Tracked as
a known gap in README.md rather than silently left unmentioned.
"""
import struct

from . import ast
from . import types as types_mod
from . import semantic as semantic_mod
from .errors import CompileError

BOOL = types_mod.PrimitiveType("bool")
INT = types_mod.PrimitiveType("int")
FLOAT = types_mod.PrimitiveType("float")
TEXT = types_mod.PrimitiveType("text")

FESTINA_ARRAY_LLVM_TYPE = "%struct._FestinaArray"

# claude.md #57: division/modulo by zero returns null; null has no spare
# bit pattern in a plain i64/double, so it's a reserved sentinel instead
# (see the module docstring's "Null for int/float" note).
INT_NULL_CONST = "-9223372036854775808"  # i64 minimum
FLOAT_NULL_CONST = "0x7FF8000000000000"  # a quiet NaN, as a raw double bit pattern
MATH_INTRINSICS = {
    "floor": "llvm.floor.f64", "ceil": "llvm.ceil.f64",
    "round": "llvm.round.f64", "trunc": "llvm.trunc.f64",
}


class CodegenError(CompileError):
    def __init__(self, message, **kw):
        kw.setdefault("category", "not implemented")
        super().__init__(message, **kw)


def _llvm_type(t):
    if isinstance(t, types_mod.PrimitiveType):
        return {"int": "i64", "float": "double", "bool": "i1",
                "text": "ptr", "blob": "ptr"}[t.name]
    if isinstance(t, types_mod.StructType):
        return "ptr"
    if isinstance(t, types_mod.ArrayType):
        return FESTINA_ARRAY_LLVM_TYPE
    if isinstance(t, types_mod.TableType):
        raise CodegenError(
            "table-typed values are not implemented yet (only automatic "
            "table schema sync is; sqlite() queries into arr[Table] are not)"
        )
    if isinstance(t, types_mod.ImageType):
        raise CodegenError("img / graphics are not implemented yet")
    if isinstance(t, types_mod.AudioType):
        raise CodegenError("aud / audio are not implemented yet")
    raise CodegenError(f"cannot generate code for type {t!r}")


class Env:
    """Mirrors festina.semantic.Scope, but maps names to (llvm_ref, Type)
    pairs instead of Symbols."""

    def __init__(self, parent=None):
        self.vars = {}
        self.parent = parent

    def define(self, name, ref, type_):
        self.vars[name] = (ref, type_)

    def lookup(self, name):
        env = self
        while env is not None:
            if name in env.vars:
                return env.vars[name]
            env = env.parent
        return None


class CodeGen:
    def __init__(self, analyzed, filename="main.f"):
        self.analyzed = analyzed
        self.filename = filename
        self.structs = analyzed.structs       # name -> {field: Type}
        self.struct_order = list(analyzed.structs.keys())
        self.tables = analyzed.tables          # name -> {field: festina-type-name}
        self.string_constants = {}             # text -> global name
        self.tmp_counter = 0
        self.label_counter = 0
        self.global_env = Env()
        self.func_defs = []                    # emitted `define` blocks (text)
        self.extra_globals = []                # globals discovered while emitting main() (e.g. table column arrays)
        self.entry_stmts = []                  # top-level statements for __festina_main
        self.func_decls = {}                   # name -> ast.FuncDecl (for signatures)
        self.cur_block = None                  # label of the block currently being emitted into

    # ---- naming ----
    def tmp(self):
        self.tmp_counter += 1
        return f"%t{self.tmp_counter}"

    def label(self, prefix):
        self.label_counter += 1
        return f"{prefix}{self.label_counter}"

    def _start_block(self, label, lines):
        """Open a new basic block and record it as the current one, so
        branch-merging code (ternary/&&/||) can find the *actual* last
        block of an arm -- which may differ from the label the arm
        started in in if that arm itself contains nested control flow."""
        lines.append(f"{label}:")
        self.cur_block = label

    def string_const(self, text):
        if text in self.string_constants:
            return self.string_constants[text]
        name = f"@.str.{len(self.string_constants)}"
        self.string_constants[text] = name
        return name

    # ---- struct layout ----
    def struct_llvm_name(self, name):
        return f"%struct.{name}"

    def struct_fields(self, name):
        """Ordered [(field_name, Type)] for a declared struct."""
        return list(self.structs[name].items())

    def struct_field_index(self, struct_name, field_name):
        for i, (fname, _) in enumerate(self.struct_fields(struct_name)):
            if fname == field_name:
                return i
        raise CodegenError(f"struct '{struct_name}' has no field '{field_name}'")

    # ---- entry point ----
    def generate(self, program):
        for stmt in program.body:
            self._toplevel(stmt)
        # Emitted first so any table-column globals it discovers land in
        # self.extra_globals before that list is read below.
        entry_and_main = self._emit_main_and_entry()
        module = []
        module.append('; ModuleID = "festina"')
        module.append(f'; generated from {self.filename} -- claude.md #47')
        module.append("")
        module.extend(self._runtime_declares())
        module.append("")
        module.extend(self._struct_type_defs())
        module.append("")
        module.extend(self._global_var_defs())
        module.append("")
        module.extend(self.extra_globals)
        module.append("")
        module.extend(self.func_defs)
        module.append("")
        module.extend(entry_and_main)
        module.append("")
        module.extend(self._string_const_defs())
        return "\n".join(module) + "\n"

    def _runtime_declares(self):
        return [
            "declare void @festina_log_int(i64)",
            "declare void @festina_log_float(double)",
            "declare void @festina_log_bool(i1)",
            "declare void @festina_log_text(ptr)",
            "declare void @festina_fail(ptr)",
            "declare ptr @festina_str_from_int(i64)",
            "declare ptr @festina_str_from_float(double)",
            "declare ptr @festina_str_from_bool(i1)",
            "declare ptr @festina_str_concat(ptr, ptr)",
            "declare i1 @festina_str_eq(ptr, ptr)",
            "declare ptr @festina_db_open()",
            "declare void @festina_sync_table(ptr, ptr, ptr, ptr, i32)",
            "declare ptr @malloc(i64)",
            "declare ptr @calloc(i64, i64)",
            # claude.md #56: Math.floor/ceil/round/trunc, via LLVM's
            # built-in intrinsics rather than a runtime C function.
            "declare double @llvm.floor.f64(double)",
            "declare double @llvm.ceil.f64(double)",
            "declare double @llvm.round.f64(double)",
            "declare double @llvm.trunc.f64(double)",
        ]

    def _struct_type_defs(self):
        # claude.md #26: every arr[T] -- regardless of T -- lowers to the
        # same fixed-size {length, data} header; see the module docstring.
        lines = [f"{FESTINA_ARRAY_LLVM_TYPE} = type {{ i64, ptr }}"]
        for name in self.struct_order:
            fields = self.struct_fields(name)
            field_types = ", ".join(_llvm_type(t) for _, t in fields)
            lines.append(f"{self.struct_llvm_name(name)} = type {{ {field_types} }}")
        return lines

    def _global_var_defs(self):
        lines = []
        for name, (ref, type_) in self.global_env.vars.items():
            if name in self.func_decls:
                continue
            if isinstance(type_, types_mod.StructType):
                # `ref` (@name) holds a *pointer* to the struct's actual
                # storage, exactly like a local struct var's alloca slot
                # (see _emit_stmt) -- kept uniform so Identifier lookup
                # never needs to special-case structs.
                backing = f"{ref}.storage"
                lines.append(f"{backing} = global {self.struct_llvm_name(type_.name)} zeroinitializer")
                lines.append(f"{ref} = global ptr {backing}")
                continue
            llvm_ty = _llvm_type(type_)
            zero = self._zero_value(type_)
            lines.append(f"{ref} = global {llvm_ty} {zero}")
        return lines

    def _zero_value(self, type_):
        llvm_ty = _llvm_type(type_)
        if llvm_ty in ("i64", "i1"):
            return "0"
        if llvm_ty == "double":
            return "0.0"
        if llvm_ty.startswith("%struct."):
            # Named aggregate types (currently just FESTINA_ARRAY_LLVM_TYPE
            # reaches this branch -- struct-typed globals are handled
            # separately in _global_var_defs) can't use "null"; a plain
            # "ptr null" only works for actual pointer types.
            return "zeroinitializer"
        return "null"

    def _string_const_defs(self):
        lines = []
        for text, name in self.string_constants.items():
            encoded, length = _encode_c_string(text)
            lines.append(f'{name} = private unnamed_addr constant [{length} x i8] c"{encoded}"')
        return lines

    # ---- top-level declarations ----
    def _toplevel(self, stmt):
        if isinstance(stmt, ast.ImportDecl):
            return  # claude.md #6: import resolution happens before this stage
        if isinstance(stmt, ast.StructDecl):
            return  # already reflected in self.structs (from semantic analysis)
        if isinstance(stmt, ast.TableDecl):
            return  # already reflected in self.tables; schema sync emitted in main()
        if isinstance(stmt, ast.FuncDecl):
            self._emit_func(stmt)
            return
        if isinstance(stmt, ast.EventHandler):
            raise CodegenError("`on` event handlers are not implemented yet",
                                file=self.filename, line=stmt.line, column=stmt.column)
        if isinstance(stmt, ast.VarDecl):
            type_ = self._resolve(stmt.type_expr, stmt)
            ref = f"@{stmt.name}"
            self.global_env.define(stmt.name, ref, type_)
            self.entry_stmts.append(stmt)
            return
        # claude.md #7: any other executable top-level statement goes into
        # the generated entry function.
        self.entry_stmts.append(stmt)

    def _resolve(self, type_expr, node):
        return semantic_mod.resolve_type_name(
            type_expr, self.structs, self.tables, self.filename, node)

    # ---- functions ----
    def _emit_func(self, decl):
        return_type = None if decl.return_type == "void" else self._resolve(decl.return_type, decl)
        self.func_decls[decl.name] = decl
        self.global_env.define(decl.name, f"@{decl.name}", return_type)

        param_types = [self._resolve(p.type_expr, decl) for p in decl.params]
        llvm_ret = "void" if return_type is None else _llvm_type(return_type)
        params_ir = ", ".join(f"{_llvm_type(t)} %arg.{p.name}" for t, p in zip(param_types, decl.params))

        body_env = Env(self.global_env)
        body_lines = []
        entry_label = self.label("entry")
        self._start_block(entry_label, body_lines)
        for t, p in zip(param_types, decl.params):
            slot = f"%{p.name}"
            body_lines.append(f"  {slot} = alloca {_llvm_type(t)}")
            body_lines.append(f"  store {_llvm_type(t)} %arg.{p.name}, ptr {slot}")
            body_env.define(p.name, slot, t)

        block = self._emit_block(decl.body, body_env, return_type, body_lines)
        if not block["terminated"]:
            if return_type is None:
                block["lines"].append("  ret void")
            else:
                block["lines"].append(f"  ret {_llvm_type(return_type)} {self._zero_value(return_type)}")

        func = [f"define {llvm_ret} @{decl.name}({params_ir}) {{"]
        func.extend(block["lines"])
        func.append("}")
        self.func_defs.extend(func)
        self.func_defs.append("")

    # ---- statements ----
    def _emit_block(self, block, parent_env, return_type, lines):
        env = Env(parent_env)
        ctx = {"lines": lines, "terminated": False}
        for stmt in block.body:
            if ctx["terminated"]:
                break
            self._emit_stmt(stmt, env, return_type, ctx)
        return ctx

    def _emit_stmt(self, stmt, env, return_type, ctx):
        lines = ctx["lines"]
        if isinstance(stmt, ast.VarDecl):
            type_ = self._resolve(stmt.type_expr, stmt)
            if isinstance(type_, types_mod.StructType):
                # `slot` holds a *pointer* to the struct's own storage,
                # kept uniform with every other type so Identifier lookup
                # never needs a struct special-case. That storage is
                # calloc'd, not a stack alloca -- see the module
                # docstring's "Struct storage is always heap-allocated"
                # note for why (a stack-allocated struct's address can
                # outlive its function: returned, put in an array, stored
                # in another struct's field -- verified to silently
                # corrupt memory when it does).
                uid = self._unique()
                struct_ty = self.struct_llvm_name(type_.name)
                size_val = self._sizeof(struct_ty, lines)
                backing = f"%{stmt.name}.storage.{uid}"
                slot = f"%{stmt.name}.{uid}"
                lines.append(f"  {backing} = call ptr @calloc(i64 1, i64 {size_val})")
                lines.append(f"  {slot} = alloca ptr")
                lines.append(f"  store ptr {backing}, ptr {slot}")
                env.define(stmt.name, slot, type_)
                # No struct-literal initializer syntax exists yet, so
                # stmt.init is always None here.
                return
            llvm_ty = _llvm_type(type_)
            slot = f"%{stmt.name}.{self._unique()}"
            lines.append(f"  {slot} = alloca {llvm_ty}")
            env.define(stmt.name, slot, type_)
            if stmt.init is not None:
                val, vtype = self._emit_value_for(stmt.init, env, lines, type_)
                val = self._coerce(val, vtype, type_, lines)
                lines.append(f"  store {llvm_ty} {val}, ptr {slot}")
            return
        if isinstance(stmt, ast.ExprStmt):
            self._emit_expr(stmt.expr, env, lines)
            return
        if isinstance(stmt, ast.Return):
            if stmt.value is None or return_type is None:
                if stmt.value is not None:
                    self._emit_expr(stmt.value, env, lines)  # side effects only
                lines.append("  ret void")
            else:
                val, vtype = self._emit_value_for(stmt.value, env, lines, return_type)
                val = self._coerce(val, vtype, return_type, lines)
                lines.append(f"  ret {_llvm_type(return_type)} {val}")
            ctx["terminated"] = True
            return
        if isinstance(stmt, ast.IfStmt):
            self._emit_if(stmt, env, return_type, ctx)
            return
        if isinstance(stmt, ast.Block):
            inner = self._emit_block(stmt, env, return_type, lines)
            ctx["terminated"] = inner["terminated"]
            return
        raise CodegenError(f"cannot generate code for statement {type(stmt).__name__}",
                            file=self.filename, line=getattr(stmt, "line", 0),
                            column=getattr(stmt, "column", 0))

    def _emit_if(self, stmt, env, return_type, ctx):
        lines = ctx["lines"]
        cond_val, _ = self._emit_expr(stmt.test, env, lines)
        then_label = self.label("if.then")
        else_label = self.label("if.else")
        end_label = self.label("if.end")
        lines.append(f"  br i1 {cond_val}, label %{then_label}, label %{else_label}")

        self._start_block(then_label, lines)
        then_ctx = self._emit_block(stmt.then, env, return_type, lines)
        if not then_ctx["terminated"]:
            lines.append(f"  br label %{end_label}")

        self._start_block(else_label, lines)
        else_terminated = False
        if stmt.orelse is not None:
            if isinstance(stmt.orelse, ast.IfStmt):
                else_ctx = {"lines": lines, "terminated": False}
                self._emit_stmt(stmt.orelse, env, return_type, else_ctx)
                else_terminated = else_ctx["terminated"]
            else:
                else_ctx = self._emit_block(stmt.orelse, env, return_type, lines)
                else_terminated = else_ctx["terminated"]
        if not else_terminated:
            lines.append(f"  br label %{end_label}")

        if then_ctx["terminated"] and else_terminated:
            ctx["terminated"] = True
        else:
            self._start_block(end_label, lines)

    _uid = 0

    def _unique(self):
        CodeGen._uid += 1
        return CodeGen._uid

    # ---- expressions ----
    def _coerce(self, val, from_type, to_type, lines):
        # claude.md #55: int and float never convert implicitly, not even
        # on assignment -- semantic.py already rejects a mismatched
        # int/float assignment before codegen ever runs, so there is no
        # remaining case that needs a numeric promotion here. What's left
        # is genuinely permissive by design: a null literal (from_type is
        # None or NULL-ish) or an unconstrained builtin return (e.g.
        # sqlite()) flowing into a concretely-typed slot.
        return val

    def _emit_expr(self, expr, env, lines):
        if isinstance(expr, ast.NumberLit):
            if isinstance(expr.value, float):
                return _format_double(expr.value), FLOAT
            return str(expr.value), INT
        if isinstance(expr, ast.BoolLit):
            return ("1" if expr.value else "0"), BOOL
        if isinstance(expr, ast.StringLit):
            return self._const_string(expr.value, lines), TEXT
        if isinstance(expr, ast.NullLit):
            # No declared-type context here (see _emit_value_for for the
            # version that has one) -- "null" is only valid IR for a
            # pointer type, which covers every Festina type reachable
            # without context (text/blob/struct/array all lower to `ptr`
            # or a pointer-holding aggregate). int/float/bool can't reach
            # this path uniformly assigned/coerced (see _emit_value_for).
            return "null", None
        if isinstance(expr, ast.TemplateLit):
            return self._emit_template(expr, env, lines), TEXT
        if isinstance(expr, ast.Identifier):
            if expr.name in self.func_decls:
                raise CodegenError("functions are not first-class values yet "
                                    f"(found bare reference to '{expr.name}')",
                                    file=self.filename, line=expr.line, column=expr.column)
            found = env.lookup(expr.name)
            if found is None:
                raise CodegenError(f"unknown variable '{expr.name}'",
                                    file=self.filename, line=expr.line, column=expr.column)
            ref, type_ = found
            # Every env slot -- scalar, struct, or function -- uniformly
            # holds a value of _llvm_type(type_) at `ref`; for structs
            # that value is itself a pointer to the struct's storage
            # (see the VarDecl/global handling below), so a plain load
            # here is correct for every case, not just scalars.
            out = self.tmp()
            lines.append(f"  {out} = load {_llvm_type(type_)}, ptr {ref}")
            return out, type_
        if isinstance(expr, ast.Member):
            return self._emit_member_load(expr, env, lines)
        if isinstance(expr, ast.ArrayLit):
            # No contextual element type here -- reached only when an
            # array literal appears somewhere _emit_value_for's callers
            # don't thread a declared type through (e.g. nested inside
            # another expression). Falls back to the elements' own type.
            return self._emit_array_lit(expr, env, lines, expected_type=None)
        if isinstance(expr, ast.Assign):
            return self._emit_assign(expr, env, lines)
        if isinstance(expr, ast.Ternary):
            return self._emit_ternary(expr, env, lines)
        if isinstance(expr, ast.LogicalOp):
            return self._emit_logical(expr, env, lines)
        if isinstance(expr, ast.BinOp):
            return self._emit_binop(expr, env, lines)
        if isinstance(expr, ast.UnaryOp):
            return self._emit_unary(expr, env, lines)
        if isinstance(expr, ast.Call):
            return self._emit_call(expr, env, lines)
        raise CodegenError(f"cannot generate code for expression {type(expr).__name__}",
                            file=self.filename, line=getattr(expr, "line", 0),
                            column=getattr(expr, "column", 0))

    def _const_string(self, text, lines):
        name = self.string_const(text)
        return name

    def _emit_template(self, expr, env, lines):
        result = self._const_string(expr.parts[0], lines)
        for part_expr, next_part in zip(expr.exprs, expr.parts[1:]):
            val, vtype = self._emit_expr(part_expr, env, lines)
            piece = self._to_text(val, vtype, lines)
            out = self.tmp()
            lines.append(f"  {out} = call ptr @festina_str_concat(ptr {result}, ptr {piece})")
            result = out
            part_str = self._const_string(next_part, lines)
            out2 = self.tmp()
            lines.append(f"  {out2} = call ptr @festina_str_concat(ptr {result}, ptr {part_str})")
            result = out2
        return result

    def _to_text(self, val, type_, lines):
        if type_ == TEXT:
            return val
        out = self.tmp()
        if type_ == INT:
            lines.append(f"  {out} = call ptr @festina_str_from_int(i64 {val})")
        elif type_ == FLOAT:
            lines.append(f"  {out} = call ptr @festina_str_from_float(double {val})")
        elif type_ == BOOL:
            lines.append(f"  {out} = call ptr @festina_str_from_bool(i1 {val})")
        else:
            raise CodegenError(f"cannot interpolate a value of type {types_mod.type_name(type_)}")
        return out

    def _emit_value_for(self, node, env, lines, expected_type):
        """Like _emit_expr, but for positions where the *declared* type is
        already known (a var's declared type, a param's type, a function's
        return type) -- lets an array literal pick its element type from
        context instead of guessing from its own elements, and lets a
        bare `null` literal pick the right runtime encoding (claude.md
        #10/#25/#57): "null" the LLVM keyword for text/blob/struct/array
        (all pointer-backed), but the reserved sentinel constants for
        int/float, which have no spare bit pattern for a real null."""
        if isinstance(node, ast.ArrayLit):
            return self._emit_array_lit(node, env, lines, expected_type)
        if isinstance(node, ast.NullLit):
            if expected_type == INT:
                return INT_NULL_CONST, INT
            if expected_type == FLOAT:
                return FLOAT_NULL_CONST, FLOAT
            return "null", expected_type
        return self._emit_expr(node, env, lines)

    def _sizeof(self, llvm_ty, lines):
        """sizeof(llvm_ty) as a runtime i64, via the standard
        getelementptr-on-null trick -- avoids reimplementing LLVM's
        struct layout/alignment rules in Python."""
        ptr_val = self.tmp()
        lines.append(f"  {ptr_val} = getelementptr {llvm_ty}, ptr null, i64 1")
        size_val = self.tmp()
        lines.append(f"  {size_val} = ptrtoint ptr {ptr_val} to i64")
        return size_val

    def _emit_array_lit(self, expr, env, lines, expected_type=None):
        # claude.md #26: "Arrays may contain supported primitive types,
        # structs, tables, and other array types" -- table elements are
        # rejected by _llvm_type(TableType) below, since there's no way
        # to construct a Table-typed value without sqlite() queries yet.
        expected_elem = expected_type.element if isinstance(expected_type, types_mod.ArrayType) else None

        values = []
        elem_type = expected_elem
        for e in expr.elements:
            if isinstance(e, ast.ArrayLit) and isinstance(expected_elem, types_mod.ArrayType):
                val, vtype = self._emit_array_lit(e, env, lines, expected_elem)
            else:
                val, vtype = self._emit_value_for(e, env, lines, expected_elem)
            if expected_elem is not None:
                val = self._coerce(val, vtype, expected_elem, lines)
                vtype = expected_elem
            values.append(val)
            elem_type = elem_type or vtype

        if elem_type is None:
            raise CodegenError(
                "cannot infer the element type of an empty array literal without a declared type",
                file=self.filename, line=getattr(expr, "line", 0),
            )
        elem_llvm_ty = _llvm_type(elem_type)
        n = len(values)

        header = f"%arr.hdr.{self._unique()}"
        lines.append(f"  {header} = alloca {FESTINA_ARRAY_LLVM_TYPE}")
        len_ptr = self.tmp()
        lines.append(f"  {len_ptr} = getelementptr {FESTINA_ARRAY_LLVM_TYPE}, ptr {header}, i32 0, i32 0")
        lines.append(f"  store i64 {n}, ptr {len_ptr}")

        data_ptr = self.tmp()
        if n == 0:
            lines.append(f"  {data_ptr} = call ptr @malloc(i64 0)")
        else:
            elem_size = self._sizeof(elem_llvm_ty, lines)
            total_size = self.tmp()
            lines.append(f"  {total_size} = mul i64 {elem_size}, {n}")
            lines.append(f"  {data_ptr} = call ptr @malloc(i64 {total_size})")
            for i, val in enumerate(values):
                elem_ptr = self.tmp()
                lines.append(f"  {elem_ptr} = getelementptr {elem_llvm_ty}, ptr {data_ptr}, i64 {i}")
                lines.append(f"  store {elem_llvm_ty} {val}, ptr {elem_ptr}")

        data_field_ptr = self.tmp()
        lines.append(f"  {data_field_ptr} = getelementptr {FESTINA_ARRAY_LLVM_TYPE}, ptr {header}, i32 0, i32 1")
        lines.append(f"  store ptr {data_ptr}, ptr {data_field_ptr}")

        out = self.tmp()
        lines.append(f"  {out} = load {FESTINA_ARRAY_LLVM_TYPE}, ptr {header}")
        return out, types_mod.ArrayType(elem_type)

    def _emit_member_load(self, expr, env, lines):
        ptr, ftype = self._member_ptr(expr, env, lines)
        out = self.tmp()
        lines.append(f"  {out} = load {_llvm_type(ftype)}, ptr {ptr}")
        return out, ftype

    def _member_ptr(self, expr, env, lines):
        if expr.computed:
            # claude.md #26: arr[i] -- `expr.prop` is the index expression
            # (see parser.parse_call_member), not a field name.
            obj_val, obj_type = self._emit_expr(expr.obj, env, lines)
            if not isinstance(obj_type, types_mod.ArrayType):
                raise CodegenError(f"cannot index into {types_mod.type_name(obj_type)}",
                                    file=self.filename, line=getattr(expr, "line", 0))
            idx_val, _ = self._emit_expr(expr.prop, env, lines)
            data_ptr = self.tmp()
            lines.append(f"  {data_ptr} = extractvalue {FESTINA_ARRAY_LLVM_TYPE} {obj_val}, 1")
            elem_type = obj_type.element
            elem_llvm_ty = _llvm_type(elem_type)
            out = self.tmp()
            lines.append(f"  {out} = getelementptr {elem_llvm_ty}, ptr {data_ptr}, i64 {idx_val}")
            return out, elem_type
        obj_val, obj_type = self._emit_expr(expr.obj, env, lines)
        if not isinstance(obj_type, types_mod.StructType):
            raise CodegenError(f"cannot access field '{expr.prop}' on {types_mod.type_name(obj_type)}",
                                file=self.filename, line=expr.line, column=expr.column)
        idx = self.struct_field_index(obj_type.name, expr.prop)
        ftype = self.struct_fields(obj_type.name)[idx][1]
        out = self.tmp()
        struct_ty = self.struct_llvm_name(obj_type.name)
        lines.append(f"  {out} = getelementptr {struct_ty}, ptr {obj_val}, i32 0, i32 {idx}")
        return out, ftype

    def _emit_assign(self, expr, env, lines):
        # The target's declared type is resolved *before* the value, so an
        # array-literal RHS (e.g. `nums = [4, 5, 6]`) can pick its element
        # type from the target instead of guessing from its own elements.
        if isinstance(expr.target, ast.Identifier):
            found = env.lookup(expr.target.name)
            if found is None:
                raise CodegenError(f"unknown variable '{expr.target.name}'",
                                    file=self.filename, line=expr.target.line)
            ref, ttype = found
            val, vtype = self._emit_value_for(expr.value, env, lines, ttype)
            val = self._coerce(val, vtype, ttype, lines)
            lines.append(f"  store {_llvm_type(ttype)} {val}, ptr {ref}")
            return val, ttype
        if isinstance(expr.target, ast.Member):
            ptr, ftype = self._member_ptr(expr.target, env, lines)
            val, vtype = self._emit_value_for(expr.value, env, lines, ftype)
            val = self._coerce(val, vtype, ftype, lines)
            lines.append(f"  store {_llvm_type(ftype)} {val}, ptr {ptr}")
            return val, ftype
        raise CodegenError("unsupported assignment target", file=self.filename)

    def _emit_ternary(self, expr, env, lines):
        cond_val, _ = self._emit_expr(expr.test, env, lines)
        then_label = self.label("tern.then")
        else_label = self.label("tern.else")
        end_label = self.label("tern.end")
        lines.append(f"  br i1 {cond_val}, label %{then_label}, label %{else_label}")

        self._start_block(then_label, lines)
        cons_val, cons_type = self._emit_expr(expr.cons, env, lines)
        then_pred = self.cur_block  # may differ from then_label if expr.cons had its own branches
        lines.append(f"  br label %{end_label}")

        self._start_block(else_label, lines)
        alt_val, _ = self._emit_expr(expr.alt, env, lines)
        else_pred = self.cur_block
        lines.append(f"  br label %{end_label}")

        self._start_block(end_label, lines)
        out = self.tmp()
        llvm_ty = _llvm_type(cons_type)
        lines.append(f"  {out} = phi {llvm_ty} [ {cons_val}, %{then_pred} ], [ {alt_val}, %{else_pred} ]")
        return out, cons_type

    def _emit_logical(self, expr, env, lines):
        left_val, _ = self._emit_expr(expr.left, env, lines)
        rhs_label = self.label("logic.rhs")
        end_label = self.label("logic.end")
        start_label = self.label("logic.start")
        # `start_label` exists purely so the short-circuit edge into
        # end_label always originates from a block we control, even if
        # evaluating expr.left itself opened (and left us inside) other
        # blocks -- that only affects where left_val was *computed*, not
        # where this edge originates.
        lines.append(f"  br label %{start_label}")
        self._start_block(start_label, lines)
        if expr.op == "&&":
            lines.append(f"  br i1 {left_val}, label %{rhs_label}, label %{end_label}")
        else:
            lines.append(f"  br i1 {left_val}, label %{end_label}, label %{rhs_label}")
        self._start_block(rhs_label, lines)
        right_val, _ = self._emit_expr(expr.right, env, lines)
        rhs_pred = self.cur_block  # may differ from rhs_label if expr.right had its own branches
        lines.append(f"  br label %{end_label}")
        self._start_block(end_label, lines)
        out = self.tmp()
        lines.append(f"  {out} = phi i1 [ {left_val}, %{start_label} ], [ {right_val}, %{rhs_pred} ]")
        return out, BOOL

    def _emit_binop(self, expr, env, lines):
        left_val, left_type = self._emit_expr(expr.left, env, lines)
        right_val, right_type = self._emit_expr(expr.right, env, lines)

        if left_type == TEXT or right_type == TEXT:
            if expr.op in ("==", "!="):
                out = self.tmp()
                lines.append(f"  {out} = call i1 @festina_str_eq(ptr {left_val}, ptr {right_val})")
                if expr.op == "!=":
                    neg = self.tmp()
                    lines.append(f"  {neg} = xor i1 {out}, 1")
                    return neg, BOOL
                return out, BOOL
            if expr.op == "+":
                out = self.tmp()
                lines.append(f"  {out} = call ptr @festina_str_concat(ptr {left_val}, ptr {right_val})")
                return out, TEXT
            raise CodegenError(f"operator '{expr.op}' is not supported on text",
                                file=self.filename, line=expr.line)

        # claude.md #55: int and float never mix directly -- semantic.py
        # already rejected a genuine mismatch before codegen ever runs, so
        # reaching here with different numeric types is a compiler bug,
        # not a user error; this is a consistency check, not a promotion
        # (there's no implicit numeric conversion left in this codegen).
        if left_type in (INT, FLOAT) and right_type in (INT, FLOAT) and left_type != right_type:
            raise CodegenError(
                f"internal error: mismatched numeric operands ({left_type!r}, {right_type!r}) "
                "reached codegen -- semantic analysis should have rejected this",
                file=self.filename, line=expr.line,
            )
        use_float = left_type == FLOAT

        if expr.op in ("/", "%"):
            out = self._emit_divmod(expr.op, left_val, right_val, use_float, lines)
            return out, (FLOAT if use_float else INT)

        arith = {"+": "add", "-": "sub", "*": "mul"}
        farith = {"+": "fadd", "-": "fsub", "*": "fmul"}
        icmp = {"<": "slt", ">": "sgt", "<=": "sle", ">=": "sge", "==": "eq", "!=": "ne"}
        fcmp = {"<": "olt", ">": "ogt", "<=": "ole", ">=": "oge", "==": "oeq", "!=": "one"}

        out = self.tmp()
        if expr.op in arith:
            op = farith[expr.op] if use_float else arith[expr.op]
            ty = "double" if use_float else "i64"
            lines.append(f"  {out} = {op} {ty} {left_val}, {right_val}")
            return out, (FLOAT if use_float else INT)
        if expr.op in icmp:
            if use_float:
                lines.append(f"  {out} = fcmp {fcmp[expr.op]} double {left_val}, {right_val}")
            else:
                ty = "i64" if left_type != BOOL else "i1"
                lines.append(f"  {out} = icmp {icmp[expr.op]} {ty} {left_val}, {right_val}")
            return out, BOOL
        raise CodegenError(f"unsupported operator '{expr.op}'", file=self.filename, line=expr.line)

    def _emit_divmod(self, op, left_val, right_val, is_float, lines):
        """claude.md #57: division/modulo by zero returns null instead of
        crashing. For int specifically, `sdiv`/`srem` by zero is undefined
        behavior at the hardware level (SIGFPE) -- checking *after*
        computing would be too late, and a `select` would still evaluate
        the trapping instruction unconditionally, so this has to be real
        control flow that skips the division entirely on the zero path."""
        llvm_ty = "double" if is_float else "i64"
        zero_lit = "0.0" if is_float else "0"
        null_const = FLOAT_NULL_CONST if is_float else INT_NULL_CONST
        cmp_instr = "fcmp oeq" if is_float else "icmp eq"

        is_zero = self.tmp()
        lines.append(f"  {is_zero} = {cmp_instr} {llvm_ty} {right_val}, {zero_lit}")

        zero_label = self.label("divzero")
        nonzero_label = self.label("divnonzero")
        end_label = self.label("divend")
        lines.append(f"  br i1 {is_zero}, label %{zero_label}, label %{nonzero_label}")

        self._start_block(zero_label, lines)
        zero_pred = self.cur_block
        lines.append(f"  br label %{end_label}")

        self._start_block(nonzero_label, lines)
        instr = {"float": {"/": "fdiv", "%": "frem"}, "int": {"/": "sdiv", "%": "srem"}}["float" if is_float else "int"][op]
        result = self.tmp()
        lines.append(f"  {result} = {instr} {llvm_ty} {left_val}, {right_val}")
        nonzero_pred = self.cur_block
        lines.append(f"  br label %{end_label}")

        self._start_block(end_label, lines)
        out = self.tmp()
        lines.append(f"  {out} = phi {llvm_ty} [ {null_const}, %{zero_pred} ], [ {result}, %{nonzero_pred} ]")
        return out

    def _emit_unary(self, expr, env, lines):
        val, vtype = self._emit_expr(expr.operand, env, lines)
        out = self.tmp()
        if expr.op == "!":
            lines.append(f"  {out} = xor i1 {val}, 1")
            return out, BOOL
        if expr.op == "-":
            if vtype == FLOAT:
                lines.append(f"  {out} = fneg double {val}")
            else:
                lines.append(f"  {out} = sub i64 0, {val}")
            return out, vtype
        return val, vtype  # unary '+' is a no-op

    def _emit_call(self, expr, env, lines):
        callee = expr.callee
        if isinstance(callee, ast.Identifier):
            name = callee.name
            if name == "log":
                val, vtype = self._emit_expr(expr.args[0], env, lines)
                if not isinstance(vtype, types_mod.PrimitiveType):
                    raise CodegenError(
                        f"log() only supports primitive values right now, "
                        f"found {types_mod.type_name(vtype)}",
                        file=self.filename, line=callee.line)
                fn = {"int": "festina_log_int", "float": "festina_log_float",
                      "bool": "festina_log_bool", "text": "festina_log_text"}[vtype.name]
                ty = _llvm_type(vtype)
                lines.append(f"  call void @{fn}({ty} {val})")
                return "0", None
            if name == "fail":
                val, vtype = self._emit_expr(expr.args[0], env, lines)
                text_val = self._to_text(val, vtype, lines)
                lines.append(f"  call void @festina_fail(ptr {text_val})")
                return "0", None
            if name == "sqlite":
                raise CodegenError("sqlite() queries are not implemented yet "
                                    "(automatic table schema sync is)",
                                    file=self.filename, line=callee.line)
            if name in ("drawRect", "drawCircle", "drawText", "drawImage",
                        "loadImage", "loadAudio"):
                raise CodegenError(f"'{name}' (graphics/audio) is not implemented yet",
                                    file=self.filename, line=callee.line)
            if name in self.func_decls:
                decl = self.func_decls[name]
                arg_vals = []
                for arg_expr, param in zip(expr.args, decl.params):
                    ptype = self._resolve(param.type_expr, decl)
                    val, vtype = self._emit_value_for(arg_expr, env, lines, ptype)
                    val = self._coerce(val, vtype, ptype, lines)
                    arg_vals.append(f"{_llvm_type(ptype)} {val}")
                ret_ref, ret_type = env.lookup(name)
                args_ir = ", ".join(arg_vals)
                if ret_type is None:
                    lines.append(f"  call void @{name}({args_ir})")
                    return "0", None
                out = self.tmp()
                lines.append(f"  {out} = call {_llvm_type(ret_type)} @{name}({args_ir})")
                return out, ret_type
            raise CodegenError(f"unknown function '{name}'", file=self.filename, line=callee.line)
        if isinstance(callee, ast.Member) and not callee.computed:
            # claude.md #56: Math.floor/ceil/round/trunc(x:float) -> int
            if (isinstance(callee.obj, ast.Identifier) and callee.obj.name == "Math"
                    and callee.prop in MATH_INTRINSICS):
                val, vtype = self._emit_expr(expr.args[0], env, lines)
                if vtype != FLOAT:
                    raise CodegenError(
                        f"Math.{callee.prop}() expects a float argument, found {types_mod.type_name(vtype)}",
                        file=self.filename, line=callee.line)
                rounded = self.tmp()
                lines.append(f"  {rounded} = call double @{MATH_INTRINSICS[callee.prop]}(double {val})")
                out = self.tmp()
                lines.append(f"  {out} = fptosi double {rounded} to i64")
                return out, INT
            # claude.md #55: int.toFloat() -> float
            if callee.prop == "toFloat" and not expr.args:
                val, vtype = self._emit_expr(callee.obj, env, lines)
                if vtype == INT:
                    out = self.tmp()
                    lines.append(f"  {out} = sitofp i64 {val} to double")
                    return out, FLOAT
        raise CodegenError("only calls to named functions are implemented",
                            file=self.filename, line=getattr(expr, "line", 0))

    # ---- entry function / main ----
    def _emit_main_and_entry(self):
        lines = []
        entry_ctx = {"lines": lines, "terminated": False}
        env = self.global_env
        self.cur_block = "entry"
        for stmt in self.entry_stmts:
            self._emit_toplevel_stmt(stmt, env, entry_ctx)
        if not entry_ctx["terminated"]:
            lines.append("  ret void")

        entry_func = ["define void @__festina_main() {"]
        entry_func.append("entry:")
        entry_func.extend(lines)
        entry_func.append("}")

        main_lines = ["define i32 @main() {", "entry:"]
        if self.tables:
            main_lines.append("  %db = call ptr @festina_db_open()")
            for tname, cols in self.tables.items():
                names_global, types_global, ncols = self._table_arrays(tname, cols)
                main_lines.append(
                    f"  call void @festina_sync_table(ptr %db, ptr {self.string_const(tname)}, "
                    f"ptr {names_global}, ptr {types_global}, i32 {ncols})"
                )
        main_lines.append("  call void @__festina_main()")
        main_lines.append("  ret i32 0")
        main_lines.append("}")

        return entry_func + [""] + main_lines

    def _table_arrays(self, table_name, cols):
        names = list(cols.keys())
        types = list(cols.values())
        names_arr = f"@{table_name}.cols"
        types_arr = f"@{table_name}.types"
        name_ptrs = ", ".join(f"ptr {self.string_const(n)}" for n in names)
        type_ptrs = ", ".join(f"ptr {self.string_const(t)}" for t in types)
        self.extra_globals.append(f"{names_arr} = private constant [{len(names)} x ptr] [{name_ptrs}]")
        self.extra_globals.append(f"{types_arr} = private constant [{len(types)} x ptr] [{type_ptrs}]")
        return names_arr, types_arr, len(names)

    def _emit_toplevel_stmt(self, stmt, env, ctx):
        lines = ctx["lines"]
        if isinstance(stmt, ast.VarDecl):
            ref, type_ = env.lookup(stmt.name)
            if stmt.init is not None:
                val, vtype = self._emit_value_for(stmt.init, env, lines, type_)
                val = self._coerce(val, vtype, type_, lines)
                lines.append(f"  store {_llvm_type(type_)} {val}, ptr {ref}")
            return
        self._emit_stmt(stmt, env, None, ctx)


def generate_ir(program, analyzed, filename="main.f"):
    gen = CodeGen(analyzed, filename)
    return gen.generate(program)


def _format_double(v):
    # repr(float) used to be used here directly, on the assumption its
    # decimal form always round-trips into something LLVM's IR parser
    # accepts -- it doesn't: repr() switches to scientific notation for
    # small/large magnitudes (e.g. 1e-07), and LLVM's double-literal
    # grammar rejects that (verified: "integer constant must have integer
    # type", i.e. it doesn't parse as a float literal at all). LLVM's `0x`
    # hex-float form takes the raw IEEE-754 bit pattern directly, so it's
    # exact and unambiguous regardless of magnitude -- no formatting edge
    # cases to enumerate.
    bits = struct.unpack(">Q", struct.pack(">d", float(v)))[0]
    return f"0x{bits:016X}"


def _encode_c_string(text):
    data = text.encode("utf-8") + b"\x00"
    out = []
    for b in data:
        c = chr(b)
        if c.isprintable() and c not in ('"', "\\") and b < 128:
            out.append(c)
        else:
            out.append(f"\\{b:02X}")
    return "".join(out), len(data)
