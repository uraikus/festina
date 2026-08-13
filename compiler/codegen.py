import struct
from ast_nodes import *

ARRAY_METHODS = {'push', 'pop', 'map', 'filter', 'forEach', 'join', 'indexOf', 'slice', 'includes'}
STRING_METHODS = {'slice', 'indexOf', 'split', 'toUpperCase', 'toLowerCase', 'includes', 'charAt', 'trim'}
NUMBER_METHODS = {'toFixed', 'toString'}
DB_METHODS = {'exec', 'prepare', 'close'}
STMT_METHODS = {'run', 'get', 'all'}
CTX_METHODS = {
    'fillRect', 'strokeRect', 'clearRect', 'beginPath', 'moveTo', 'lineTo',
    'arc', 'fill', 'stroke', 'closePath', 'fillText'
}


def dhex(v):
    """Encode a python float as an LLVM double hex literal (exact round-trip)."""
    b = struct.pack('>d', float(v))
    return '0x' + b.hex().upper()


RUNTIME_DECLS = """
declare i8* @js_new_number(double)
declare i8* @js_new_string(i8*)
declare i8* @js_new_bool(i32)
declare i8* @js_undefined()
declare i8* @js_null()
declare i8* @js_new_array()
declare i8* @js_array_push(i8*, i8*)
declare i8* @js_array_pop(i8*)
declare i8* @js_array_get(i8*, i8*)
declare void @js_array_set(i8*, i8*, i8*)
declare i8* @js_array_length(i8*)
declare i8* @js_array_map(i8*, i8*)
declare i8* @js_array_filter(i8*, i8*)
declare i8* @js_array_forEach(i8*, i8*)
declare i8* @js_array_join(i8*, i8*)
declare i8* @js_array_indexOf(i8*, i8*)
declare i8* @js_array_includes(i8*, i8*)
declare i8* @js_array_slice(i8*, i8*, i8*)
declare i8* @js_generic_indexOf(i8*, i8*)
declare i8* @js_generic_includes(i8*, i8*)
declare i8* @js_generic_slice(i8*, i8*, i8*)

declare i8* @js_object_new()
declare void @js_object_set(i8*, i8*, i8*)
declare i8* @js_object_get(i8*, i8*)
declare i8* @js_object_keys(i8*)
declare i8* @js_object_values(i8*)

declare i8* @js_string_length(i8*)
declare i8* @js_string_slice(i8*, i8*, i8*)
declare i8* @js_string_indexOf(i8*, i8*)
declare i8* @js_string_split(i8*, i8*)
declare i8* @js_string_toUpperCase(i8*)
declare i8* @js_string_toLowerCase(i8*)
declare i8* @js_string_includes(i8*, i8*)
declare i8* @js_string_charAt(i8*, i8*)
declare i8* @js_string_trim(i8*)

declare i8* @js_number_toFixed(i8*, i8*)
declare i8* @js_number_toString(i8*)

declare i8* @js_add(i8*, i8*)
declare i8* @js_sub(i8*, i8*)
declare i8* @js_mul(i8*, i8*)
declare i8* @js_div(i8*, i8*)
declare i8* @js_mod(i8*, i8*)
declare i8* @js_lt(i8*, i8*)
declare i8* @js_gt(i8*, i8*)
declare i8* @js_le(i8*, i8*)
declare i8* @js_ge(i8*, i8*)
declare i8* @js_eq(i8*, i8*)
declare i8* @js_neq(i8*, i8*)
declare i8* @js_not(i8*)
declare i8* @js_neg(i8*)
declare i32 @js_truthy(i8*)

declare void @js_console_log(i8**, i32)
declare i8* @js_new_function(i8*)
declare i8* @js_call(i8*, i8**, i32)
declare i8* @js_arg_or_undef(i8**, i32, i32)

declare i8* @js_db_open(i8*)
declare i8* @js_db_exec(i8*, i8*)
declare i8* @js_db_prepare(i8*, i8*)
declare i8* @js_stmt_run(i8*, i8**, i32)
declare i8* @js_stmt_get(i8*, i8**, i32)
declare i8* @js_stmt_all(i8*, i8**, i32)
declare void @js_db_close(i8*)

declare i8* @js_canvas_create(i8*, i8*)
declare i8* @js_canvas_get_context(i8*)
declare void @js_ctx_set_fill_style(i8*, i8*)
declare void @js_ctx_set_stroke_style(i8*, i8*)
declare void @js_ctx_set_line_width(i8*, i8*)
declare void @js_ctx_fill_rect(i8*, i8*, i8*, i8*, i8*)
declare void @js_ctx_stroke_rect(i8*, i8*, i8*, i8*, i8*)
declare void @js_ctx_clear_rect(i8*, i8*, i8*, i8*, i8*)
declare void @js_ctx_begin_path(i8*)
declare void @js_ctx_move_to(i8*, i8*, i8*)
declare void @js_ctx_line_to(i8*, i8*, i8*)
declare void @js_ctx_arc(i8*, i8*, i8*, i8*, i8*, i8*)
declare void @js_ctx_fill(i8*)
declare void @js_ctx_stroke(i8*)
declare void @js_ctx_close_path(i8*)
declare void @js_ctx_fill_text(i8*, i8*, i8*, i8*)
declare void @js_canvas_save_png(i8*, i8*)
declare void @js_runtime_init()
"""

BINOP_MAP = {
    '+': 'js_add', '-': 'js_sub', '*': 'js_mul', '/': 'js_div', '%': 'js_mod',
    '<': 'js_lt', '>': 'js_gt', '<=': 'js_le', '>=': 'js_ge',
    '==': 'js_eq', '===': 'js_eq', '!=': 'js_neq', '!==': 'js_neq',
}


class FnGen:
    """Holds per-function codegen state: basic blocks, temp/var counters."""

    def __init__(self, name, params):
        self.name = name
        self.params = params
        self.tmp = 0
        self.blk = 0
        self.blocks = [['entry', []]]
        self.cur = self.blocks[0]
        self.vars = {}  # js var name -> llvm alloca register e.g. %v_x

    def new_tmp(self):
        self.tmp += 1
        return f'%t{self.tmp}'

    def new_block(self, base):
        self.blk += 1
        label = f'{base}.{self.blk}'
        self.blocks.append([label, []])
        self.cur = self.blocks[-1]
        return label

    def set_block(self, label):
        for b in self.blocks:
            if b[0] == label:
                self.cur = b
                return
        raise RuntimeError('no such block ' + label)

    def emit(self, line):
        self.cur[1].append(line)

    def terminated(self):
        if not self.cur[1]:
            return False
        return self.cur[1][-1].split()[0] in ('br', 'ret')

    def render(self):
        out = []
        for label, lines in self.blocks:
            out.append(f'{label}:')
            for l in lines:
                out.append(f'  {l}')
        return '\n'.join(out)


class CodeGen:
    def __init__(self):
        self.strings = {}  # text -> global name
        self.str_globals = []
        self.functions = []  # rendered function text blocks
        self.declared_funcs = {}  # name -> arity (top-level function decls)
        self.pending_arrows = []  # (name, params, body_node) still to compile
        self.arrow_count = 0

    # ---------- string constant pool ----------
    def cstr(self, s):
        if s in self.strings:
            return self.strings[s]
        name = f'@.str.{len(self.strings)}'
        data = s.encode('utf-8') + b'\x00'
        esc = ''.join('\\%02X' % b for b in data)
        self.str_globals.append(f'{name} = private unnamed_addr constant [{len(data)} x i8] c"{esc}"')
        self.strings[s] = (name, len(data))
        return self.strings[s]

    def cstr_ptr(self, s):
        name, length = self.cstr(s)
        return f'getelementptr inbounds ([{length} x i8], [{length} x i8]* {name}, i32 0, i32 0)'

    # ---------- top level ----------
    def compile(self, program):
        # pass 1: register top-level function names for direct calls
        for stmt in program.body:
            if isinstance(stmt, FuncDecl):
                self.declared_funcs[stmt.name] = len(stmt.params)

        main_fg = FnGen('main', [])
        main_fg.emit('call void @js_runtime_init()')
        self.hoist_vars(program.body, main_fg)

        for stmt in program.body:
            if isinstance(stmt, FuncDecl):
                continue
            self.gen_stmt(stmt, main_fg)

        if not main_fg.terminated():
            main_fg.emit('ret i32 0')

        # now compile actual function decls (may reference each other; simple top-level scope)
        for stmt in program.body:
            if isinstance(stmt, FuncDecl):
                self.compile_function(stmt.name, stmt.params, stmt.body)

        while self.pending_arrows:
            name, params, body, is_expr = self.pending_arrows.pop()
            self.compile_function(name, params, body, expr_body=is_expr)

        out = []
        out.append('; ModuleID = \'jsc\'')
        out.append('target triple = "x86_64-unknown-linux-gnu"')
        out.append('')
        out.extend(self.str_globals)
        out.append('')
        out.append(RUNTIME_DECLS)
        out.append('')
        out.extend(self.functions)
        out.append('')
        out.append('define i32 @main() {')
        out.append(main_fg.render())
        out.append('}')
        return '\n'.join(out)

    # ---------- variable hoisting ----------
    def hoist_vars(self, body, fg):
        for stmt in body:
            self._hoist_stmt(stmt, fg)

    def _hoist_stmt(self, node, fg):
        if isinstance(node, VarDecl):
            self._declare(fg, node.name)
        elif isinstance(node, Block):
            for s in node.body:
                self._hoist_stmt(s, fg)
        elif isinstance(node, If):
            self._hoist_stmt(node.cons, fg)
            if node.alt:
                self._hoist_stmt(node.alt, fg)
        elif isinstance(node, For):
            if isinstance(node.init, VarDecl):
                self._declare(fg, node.init.name)
            self._hoist_stmt(node.body, fg)
        elif isinstance(node, While):
            self._hoist_stmt(node.body, fg)

    def _declare(self, fg, name):
        if name in fg.vars:
            return fg.vars[name]
        reg = f'%v_{name}_{len(fg.vars)}'
        fg.vars[name] = reg
        fg.blocks[0][1].insert(0, f'{reg} = alloca i8*')
        return reg

    # ---------- function compilation ----------
    def compile_function(self, name, params, body, expr_body=False):
        fg = FnGen(name, params)
        for p in params:
            self._declare(fg, p)
        if isinstance(body, Block):
            self.hoist_vars(body.body, fg)
        for i, p in enumerate(params):
            v = fg.new_tmp()
            fg.emit(f'{v} = call i8* @js_arg_or_undef(i8** %args, i32 %argc, i32 {i})')
            fg.emit(f'store i8* {v}, i8** {fg.vars[p]}')

        if expr_body:
            val = self.gen_expr(body, fg)
            fg.emit(f'ret i8* {val}')
        else:
            for stmt in body.body:
                self.gen_stmt(stmt, fg)
            if not fg.terminated():
                u = fg.new_tmp()
                fg.emit(f'{u} = call i8* @js_undefined()')
                fg.emit(f'ret i8* {u}')

        text = f'define i8* @{name}(i8** %args, i32 %argc) {{\n{fg.render()}\n}}'
        self.functions.append(text)

    def register_arrow(self, node):
        self.arrow_count += 1
        name = f'__arrow_{self.arrow_count}'
        self.pending_arrows.append((name, node.params, node.body, node.is_expr_body))
        return name

    # ---------- statements ----------
    def gen_stmt(self, node, fg):
        if isinstance(node, VarDecl):
            reg = fg.vars.get(node.name) or self._declare(fg, node.name)
            if node.init is not None:
                val = self.gen_expr(node.init, fg)
            else:
                val = fg.new_tmp()
                fg.emit(f'{val} = call i8* @js_undefined()')
            fg.emit(f'store i8* {val}, i8** {reg}')
        elif isinstance(node, ExprStmt):
            self.gen_expr(node.expr, fg)
        elif isinstance(node, Block):
            for s in node.body:
                self.gen_stmt(s, fg)
        elif isinstance(node, Return):
            if node.value is not None:
                val = self.gen_expr(node.value, fg)
            else:
                val = fg.new_tmp()
                fg.emit(f'{val} = call i8* @js_undefined()')
            fg.emit(f'ret i8* {val}')
        elif isinstance(node, If):
            self.gen_if(node, fg)
        elif isinstance(node, For):
            self.gen_for(node, fg)
        elif isinstance(node, While):
            self.gen_while(node, fg)
        elif isinstance(node, FuncDecl):
            pass  # compiled separately at top level
        elif isinstance(node, DestructureDecl):
            pass  # no-op: builtin names (createCanvas/Database/Object) resolved by identifier
        else:
            raise NotImplementedError(f'stmt {type(node)}')

    def gen_if(self, node, fg):
        cond = self.gen_expr(node.test, fg)
        truthy = fg.new_tmp()
        fg.emit(f'{truthy} = call i32 @js_truthy(i8* {cond})')
        cmp = fg.new_tmp()
        fg.emit(f'{cmp} = icmp ne i32 {truthy}, 0')
        then_l = f'if.then.{fg.blk + 1}'
        else_l = f'if.else.{fg.blk + 1}'
        end_l = f'if.end.{fg.blk + 1}'
        if node.alt:
            fg.emit(f'br i1 {cmp}, label %{then_l}, label %{else_l}')
        else:
            fg.emit(f'br i1 {cmp}, label %{then_l}, label %{end_l}')
        fg.new_block('if.then')
        fg.blocks[-1][0] = then_l
        self.gen_stmt(node.cons, fg)
        if not fg.terminated():
            fg.emit(f'br label %{end_l}')
        if node.alt:
            fg.new_block('if.else')
            fg.blocks[-1][0] = else_l
            self.gen_stmt(node.alt, fg)
            if not fg.terminated():
                fg.emit(f'br label %{end_l}')
        fg.new_block('if.end')
        fg.blocks[-1][0] = end_l

    def gen_for(self, node, fg):
        if node.init is not None:
            self.gen_stmt(node.init, fg)
        cond_l = f'for.cond.{fg.blk + 1}'
        body_l = f'for.body.{fg.blk + 1}'
        upd_l = f'for.upd.{fg.blk + 1}'
        end_l = f'for.end.{fg.blk + 1}'
        fg.emit(f'br label %{cond_l}')
        fg.new_block('for.cond')
        fg.blocks[-1][0] = cond_l
        if node.test is not None:
            cond = self.gen_expr(node.test, fg)
            truthy = fg.new_tmp()
            fg.emit(f'{truthy} = call i32 @js_truthy(i8* {cond})')
            cmp = fg.new_tmp()
            fg.emit(f'{cmp} = icmp ne i32 {truthy}, 0')
            fg.emit(f'br i1 {cmp}, label %{body_l}, label %{end_l}')
        else:
            fg.emit(f'br label %{body_l}')
        fg.new_block('for.body')
        fg.blocks[-1][0] = body_l
        self.gen_stmt(node.body, fg)
        if not fg.terminated():
            fg.emit(f'br label %{upd_l}')
        fg.new_block('for.upd')
        fg.blocks[-1][0] = upd_l
        if node.update is not None:
            self.gen_expr(node.update, fg)
        fg.emit(f'br label %{cond_l}')
        fg.new_block('for.end')
        fg.blocks[-1][0] = end_l

    def gen_while(self, node, fg):
        cond_l = f'while.cond.{fg.blk + 1}'
        body_l = f'while.body.{fg.blk + 1}'
        end_l = f'while.end.{fg.blk + 1}'
        fg.emit(f'br label %{cond_l}')
        fg.new_block('while.cond')
        fg.blocks[-1][0] = cond_l
        cond = self.gen_expr(node.test, fg)
        truthy = fg.new_tmp()
        fg.emit(f'{truthy} = call i32 @js_truthy(i8* {cond})')
        cmp = fg.new_tmp()
        fg.emit(f'{cmp} = icmp ne i32 {truthy}, 0')
        fg.emit(f'br i1 {cmp}, label %{body_l}, label %{end_l}')
        fg.new_block('while.body')
        fg.blocks[-1][0] = body_l
        self.gen_stmt(node.body, fg)
        if not fg.terminated():
            fg.emit(f'br label %{cond_l}')
        fg.new_block('while.end')
        fg.blocks[-1][0] = end_l

    # ---------- expressions ----------
    def gen_expr(self, node, fg):
        if isinstance(node, NumberLit):
            v = fg.new_tmp()
            fg.emit(f'{v} = call i8* @js_new_number(double {dhex(node.value)})')
            return v
        if isinstance(node, StringLit):
            v = fg.new_tmp()
            fg.emit(f'{v} = call i8* @js_new_string(i8* {self.cstr_ptr(node.value)})')
            return v
        if isinstance(node, BoolLit):
            v = fg.new_tmp()
            fg.emit(f'{v} = call i8* @js_new_bool(i32 {1 if node.value else 0})')
            return v
        if isinstance(node, NullLit):
            v = fg.new_tmp()
            fg.emit(f'{v} = call i8* @js_null()')
            return v
        if isinstance(node, UndefinedLit):
            v = fg.new_tmp()
            fg.emit(f'{v} = call i8* @js_undefined()')
            return v
        if isinstance(node, ArrayLit):
            arr = fg.new_tmp()
            fg.emit(f'{arr} = call i8* @js_new_array()')
            for el in node.elements:
                ev = self.gen_expr(el, fg)
                t = fg.new_tmp()
                fg.emit(f'{t} = call i8* @js_array_push(i8* {arr}, i8* {ev})')
            return arr
        if isinstance(node, ObjectLit):
            obj = fg.new_tmp()
            fg.emit(f'{obj} = call i8* @js_object_new()')
            for key, val_node in node.pairs:
                val = self.gen_expr(val_node, fg)
                fg.emit(f'call void @js_object_set(i8* {obj}, i8* {self.cstr_ptr(key)}, i8* {val})')
            return obj
        if isinstance(node, Identifier):
            if node.name == 'console':
                return None  # handled specially at Call site
            if node.name not in fg.vars:
                raise NameError(f'Undeclared identifier {node.name}')
            v = fg.new_tmp()
            fg.emit(f'{v} = load i8*, i8** {fg.vars[node.name]}')
            return v
        if isinstance(node, ArrowFunc):
            fname = self.register_arrow(node)
            v = fg.new_tmp()
            fg.emit(f'{v} = call i8* @js_new_function(i8* bitcast (i8*(i8**, i32)* @{fname} to i8*))')
            return v
        if isinstance(node, VarDecl):
            self.gen_stmt(node, fg)
            v = fg.new_tmp()
            fg.emit(f'{v} = call i8* @js_undefined()')
            return v
        if isinstance(node, UnaryOp):
            return self.gen_unary(node, fg)
        if isinstance(node, BinOp):
            left = self.gen_expr(node.left, fg)
            right = self.gen_expr(node.right, fg)
            fn = BINOP_MAP[node.op]
            v = fg.new_tmp()
            fg.emit(f'{v} = call i8* @{fn}(i8* {left}, i8* {right})')
            if node.op in ('!=', '!=='):
                pass
            return v
        if isinstance(node, LogicalOp):
            left = self.gen_expr(node.left, fg)
            truthy = fg.new_tmp()
            fg.emit(f'{truthy} = call i32 @js_truthy(i8* {left})')
            cmp = fg.new_tmp()
            fg.emit(f'{cmp} = icmp ne i32 {truthy}, 0')
            rhs_l = f'log.rhs.{fg.blk + 1}'
            end_l = f'log.end.{fg.blk + 1}'
            result_slot = fg.new_tmp().replace('%t', '%logres')
            fg.emit(f'{result_slot} = alloca i8*')
            if node.op == '&&':
                fg.emit(f'br i1 {cmp}, label %{rhs_l}, label %{end_l}')
            else:
                fg.emit(f'br i1 {cmp}, label %{end_l}, label %{rhs_l}')
            entry_val = left
            fg.emit(f'store i8* {entry_val}, i8** {result_slot}')
            fg.new_block('log.rhs')
            fg.blocks[-1][0] = rhs_l
            right = self.gen_expr(node.right, fg)
            fg.emit(f'store i8* {right}, i8** {result_slot}')
            fg.emit(f'br label %{end_l}')
            fg.new_block('log.end')
            fg.blocks[-1][0] = end_l
            v = fg.new_tmp()
            fg.emit(f'{v} = load i8*, i8** {result_slot}')
            return v
        if isinstance(node, Assign):
            return self.gen_assign(node, fg)
        if isinstance(node, Member):
            return self.gen_member_load(node, fg)
        if isinstance(node, New):
            return self.gen_new(node, fg)
        if isinstance(node, Call):
            return self.gen_call(node, fg)
        raise NotImplementedError(f'expr {type(node)}')

    def gen_unary(self, node, fg):
        if node.op in ('++', '--'):
            cur = self.gen_expr(node.operand, fg)
            one = fg.new_tmp()
            fg.emit(f'{one} = call i8* @js_new_number(double {dhex(1.0)})')
            fn = 'js_add' if node.op == '++' else 'js_sub'
            newv = fg.new_tmp()
            fg.emit(f'{newv} = call i8* @{fn}(i8* {cur}, i8* {one})')
            self._store_to_target(node.operand, newv, fg)
            return newv if node.prefix else cur
        operand = self.gen_expr(node.operand, fg)
        if node.op == '!':
            v = fg.new_tmp()
            fg.emit(f'{v} = call i8* @js_not(i8* {operand})')
            return v
        if node.op == '-':
            v = fg.new_tmp()
            fg.emit(f'{v} = call i8* @js_neg(i8* {operand})')
            return v
        if node.op == '+':
            return operand
        raise NotImplementedError(node.op)

    def _store_to_target(self, target, val, fg):
        if isinstance(target, Identifier):
            fg.emit(f'store i8* {val}, i8** {fg.vars[target.name]}')
        elif isinstance(target, Member):
            obj = self.gen_expr(target.obj, fg)
            if target.computed:
                idx = self.gen_expr(target.prop, fg)
                fg.emit(f'call void @js_array_set(i8* {obj}, i8* {idx}, i8* {val})')
            elif target.prop == 'fillStyle':
                fg.emit(f'call void @js_ctx_set_fill_style(i8* {obj}, i8* {val})')
            elif target.prop == 'strokeStyle':
                fg.emit(f'call void @js_ctx_set_stroke_style(i8* {obj}, i8* {val})')
            elif target.prop == 'lineWidth':
                fg.emit(f'call void @js_ctx_set_line_width(i8* {obj}, i8* {val})')
            else:
                fg.emit(f'call void @js_object_set(i8* {obj}, i8* {self.cstr_ptr(target.prop)}, i8* {val})')
        else:
            raise NotImplementedError('assign target')

    def gen_assign(self, node, fg):
        if node.op == '=':
            val = self.gen_expr(node.value, fg)
        else:
            cur = self.gen_expr(node.target, fg)
            rhs = self.gen_expr(node.value, fg)
            fn = BINOP_MAP[node.op[0]]
            val = fg.new_tmp()
            fg.emit(f'{val} = call i8* @{fn}(i8* {cur}, i8* {rhs})')
        self._store_to_target(node.target, val, fg)
        return val

    def gen_member_load(self, node, fg):
        # obj.length special-cased for arrays/strings by runtime dispatch (type-checked at runtime)
        obj = self.gen_expr(node.obj, fg)
        if obj is None and isinstance(node.obj, Identifier) and node.obj.name == 'console':
            return None
        if node.computed:
            idx = self.gen_expr(node.prop, fg)
            v = fg.new_tmp()
            fg.emit(f'{v} = call i8* @js_array_get(i8* {obj}, i8* {idx})')
            return v
        if node.prop == 'length':
            v = fg.new_tmp()
            fg.emit(f'{v} = call i8* @js_array_length(i8* {obj})')
            return v
        v = fg.new_tmp()
        fg.emit(f'{v} = call i8* @js_object_get(i8* {obj}, i8* {self.cstr_ptr(node.prop)})')
        return v

    def _build_args_array(self, args, fg):
        n = len(args)
        arr_reg = f'%argsarr{fg.new_tmp()[2:]}'
        fg.emit(f'{arr_reg} = alloca [{max(n,1)} x i8*]')
        vals = [self.gen_expr(a, fg) for a in args]
        for i, v in enumerate(vals):
            gep = fg.new_tmp()
            fg.emit(f'{gep} = getelementptr inbounds [{max(n,1)} x i8*], [{max(n,1)} x i8*]* {arr_reg}, i32 0, i32 {i}')
            fg.emit(f'store i8* {v}, i8** {gep}')
        ptr = fg.new_tmp()
        fg.emit(f'{ptr} = getelementptr inbounds [{max(n,1)} x i8*], [{max(n,1)} x i8*]* {arr_reg}, i32 0, i32 0')
        return ptr, n

    def gen_new(self, node, fg):
        if isinstance(node.callee, Identifier) and node.callee.name == 'Database':
            path = self.gen_expr(node.args[0], fg) if node.args else self.cstr_ptr(':memory:')
            v = fg.new_tmp()
            if node.args:
                fg.emit(f'{v} = call i8* @js_db_open(i8* {path})')
            else:
                v2 = fg.new_tmp()
                fg.emit(f'{v2} = call i8* @js_new_string(i8* {self.cstr_ptr(":memory:")})')
                fg.emit(f'{v} = call i8* @js_db_open(i8* {v2})')
            return v
        raise NotImplementedError('new ' + getattr(node.callee, "name", "?"))

    def gen_call(self, node, fg):
        callee = node.callee
        # console.log(...)
        if isinstance(callee, Member) and isinstance(callee.obj, Identifier) and callee.obj.name == 'console' and callee.prop == 'log':
            ptr, n = self._build_args_array(node.args, fg)
            fg.emit(f'call void @js_console_log(i8** {ptr}, i32 {n})')
            v = fg.new_tmp()
            fg.emit(f'{v} = call i8* @js_undefined()')
            return v
        # Object.keys / Object.values
        if isinstance(callee, Member) and isinstance(callee.obj, Identifier) and callee.obj.name == 'Object':
            arg = self.gen_expr(node.args[0], fg)
            v = fg.new_tmp()
            fn = 'js_object_keys' if callee.prop == 'keys' else 'js_object_values'
            fg.emit(f'{v} = call i8* @{fn}(i8* {arg})')
            return v
        # createCanvas(w, h)
        if isinstance(callee, Identifier) and callee.name == 'createCanvas':
            w = self.gen_expr(node.args[0], fg)
            h = self.gen_expr(node.args[1], fg)
            v = fg.new_tmp()
            fg.emit(f'{v} = call i8* @js_canvas_create(i8* {w}, i8* {h})')
            return v
        # require(...) => no-op
        if isinstance(callee, Identifier) and callee.name == 'require':
            v = fg.new_tmp()
            fg.emit(f'{v} = call i8* @js_undefined()')
            return v
        # method calls on member expressions: dispatch by method name
        if isinstance(callee, Member) and not callee.computed:
            method = callee.prop
            obj = self.gen_expr(callee.obj, fg)
            if method in ARRAY_METHODS and method in ('push',):
                a0 = self.gen_expr(node.args[0], fg)
                v = fg.new_tmp()
                fg.emit(f'{v} = call i8* @js_array_push(i8* {obj}, i8* {a0})')
                return v
            if method == 'pop':
                v = fg.new_tmp()
                fg.emit(f'{v} = call i8* @js_array_pop(i8* {obj})')
                return v
            if method in ('map', 'filter'):
                cb = self.gen_expr(node.args[0], fg)
                v = fg.new_tmp()
                fg.emit(f'{v} = call i8* @js_array_{method}(i8* {obj}, i8* {cb})')
                return v
            if method == 'forEach':
                cb = self.gen_expr(node.args[0], fg)
                v = fg.new_tmp()
                fg.emit(f'{v} = call i8* @js_array_forEach(i8* {obj}, i8* {cb})')
                return v
            if method == 'join':
                sep = self.gen_expr(node.args[0], fg) if node.args else self.gen_expr(StringLit(','), fg)
                v = fg.new_tmp()
                fg.emit(f'{v} = call i8* @js_array_join(i8* {obj}, i8* {sep})')
                return v
            if method == 'indexOf':
                # ambiguous between array/string -> resolved at runtime by type tag
                a0 = self.gen_expr(node.args[0], fg)
                v = fg.new_tmp()
                fg.emit(f'{v} = call i8* @js_generic_indexOf(i8* {obj}, i8* {a0})')
                return v
            if method == 'slice':
                a0 = self.gen_expr(node.args[0], fg)
                a1 = self.gen_expr(node.args[1], fg) if len(node.args) > 1 else self._undef(fg)
                v = fg.new_tmp()
                fg.emit(f'{v} = call i8* @js_generic_slice(i8* {obj}, i8* {a0}, i8* {a1})')
                return v
            if method == 'includes':
                a0 = self.gen_expr(node.args[0], fg)
                v = fg.new_tmp()
                fg.emit(f'{v} = call i8* @js_generic_includes(i8* {obj}, i8* {a0})')
                return v
            if method == 'split':
                a0 = self.gen_expr(node.args[0], fg)
                v = fg.new_tmp()
                fg.emit(f'{v} = call i8* @js_string_split(i8* {obj}, i8* {a0})')
                return v
            if method in ('toUpperCase', 'toLowerCase', 'trim'):
                v = fg.new_tmp()
                fg.emit(f'{v} = call i8* @js_string_{method}(i8* {obj})')
                return v
            if method == 'charAt':
                a0 = self.gen_expr(node.args[0], fg)
                v = fg.new_tmp()
                fg.emit(f'{v} = call i8* @js_string_charAt(i8* {obj}, i8* {a0})')
                return v
            if method == 'toFixed':
                a0 = self.gen_expr(node.args[0], fg) if node.args else self.gen_expr(NumberLit(0), fg)
                v = fg.new_tmp()
                fg.emit(f'{v} = call i8* @js_number_toFixed(i8* {obj}, i8* {a0})')
                return v
            if method == 'toString':
                v = fg.new_tmp()
                fg.emit(f'{v} = call i8* @js_number_toString(i8* {obj})')
                return v
            if method == 'exec':
                a0 = self.gen_expr(node.args[0], fg)
                v = fg.new_tmp()
                fg.emit(f'{v} = call i8* @js_db_exec(i8* {obj}, i8* {a0})')
                return v
            if method == 'prepare':
                a0 = self.gen_expr(node.args[0], fg)
                v = fg.new_tmp()
                fg.emit(f'{v} = call i8* @js_db_prepare(i8* {obj}, i8* {a0})')
                return v
            if method == 'close':
                fg.emit(f'call void @js_db_close(i8* {obj})')
                return self._undef(fg)
            if method in ('run', 'get', 'all'):
                ptr, n = self._build_args_array(node.args, fg)
                v = fg.new_tmp()
                fg.emit(f'{v} = call i8* @js_stmt_{method}(i8* {obj}, i8** {ptr}, i32 {n})')
                return v
            if method == 'getContext':
                v = fg.new_tmp()
                fg.emit(f'{v} = call i8* @js_canvas_get_context(i8* {obj})')
                return v
            if method in CTX_METHODS:
                return self._gen_ctx_call(method, obj, node.args, fg)
            if method == 'toBuffer' or method == 'savePng' or method == 'toPNG':
                a0 = self.gen_expr(node.args[0], fg)
                fg.emit(f'call void @js_canvas_save_png(i8* {obj}, i8* {a0})')
                return self._undef(fg)
            # generic: property is a function value -> call via js_call
            fnval = fg.new_tmp()
            fg.emit(f'{fnval} = call i8* @js_object_get(i8* {obj}, i8* {self.cstr_ptr(method)})')
            ptr, n = self._build_args_array(node.args, fg)
            v = fg.new_tmp()
            fg.emit(f'{v} = call i8* @js_call(i8* {fnval}, i8** {ptr}, i32 {n})')
            return v
        # direct call to top-level named function
        if isinstance(callee, Identifier) and callee.name in self.declared_funcs:
            ptr, n = self._build_args_array(node.args, fg)
            v = fg.new_tmp()
            fg.emit(f'{v} = call i8* @{callee.name}(i8** {ptr}, i32 {n})')
            return v
        # calling a function value held in a variable
        if isinstance(callee, Identifier):
            fnval = self.gen_expr(callee, fg)
            ptr, n = self._build_args_array(node.args, fg)
            v = fg.new_tmp()
            fg.emit(f'{v} = call i8* @js_call(i8* {fnval}, i8** {ptr}, i32 {n})')
            return v
        raise NotImplementedError('call target')

    def _undef(self, fg):
        v = fg.new_tmp()
        fg.emit(f'{v} = call i8* @js_undefined()')
        return v

    def _gen_ctx_call(self, method, obj, args, fg):
        vals = [self.gen_expr(a, fg) for a in args]
        m = {
            'fillRect': ('js_ctx_fill_rect', 4), 'strokeRect': ('js_ctx_stroke_rect', 4),
            'clearRect': ('js_ctx_clear_rect', 4), 'moveTo': ('js_ctx_move_to', 2),
            'lineTo': ('js_ctx_line_to', 2), 'arc': ('js_ctx_arc', 5),
            'fillText': ('js_ctx_fill_text', 3),
        }
        if method in m:
            fn, arity = m[method]
            argstr = ', '.join(f'i8* {v}' for v in vals)
            fg.emit(f'call void @{fn}(i8* {obj}{", " if vals else ""}{argstr})')
            return self._undef(fg)
        zero = {'beginPath': 'js_ctx_begin_path', 'fill': 'js_ctx_fill',
                'stroke': 'js_ctx_stroke', 'closePath': 'js_ctx_close_path'}
        if method in zero:
            fg.emit(f'call void @{zero[method]}(i8* {obj})')
            return self._undef(fg)
        raise NotImplementedError(method)


def compile_to_ir(source):
    from parser import parse
    prog = parse(source)
    cg = CodeGen()
    ir = cg.compile(prog)
    return ir
