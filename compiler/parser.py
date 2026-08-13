from lexer import tokenize
from ast_nodes import *


class Parser:
    def __init__(self, tokens):
        self.toks = tokens
        self.i = 0

    def peek(self, k=0):
        return self.toks[self.i + k]

    def at(self, *types):
        return self.peek().type in types

    def eat(self, type_=None):
        t = self.toks[self.i]
        if type_ and t.type != type_:
            raise SyntaxError(f'Expected {type_} got {t.type}({t.value!r}) at token {self.i}')
        self.i += 1
        return t

    def opt(self, type_):
        if self.at(type_):
            return self.eat()
        return None

    # ---- program ----
    def parse_program(self):
        body = []
        while not self.at('EOF'):
            body.append(self.parse_statement())
        return Program(body)

    def parse_block(self):
        self.eat('LBRACE')
        body = []
        while not self.at('RBRACE'):
            body.append(self.parse_statement())
        self.eat('RBRACE')
        return Block(body)

    def parse_statement(self):
        t = self.peek()
        if t.type in ('var', 'let', 'const'):
            s = self.parse_var_decl()
            self.opt('OP') if self.at('OP') and self.peek().value == ';' else None
            return s
        if t.type == 'function':
            return self.parse_func_decl()
        if t.type == 'if':
            return self.parse_if()
        if t.type == 'for':
            return self.parse_for()
        if t.type == 'while':
            return self.parse_while()
        if t.type == 'return':
            self.eat('return')
            val = None
            if not (self.at('OP') and self.peek().value == ';') and not self.at('RBRACE'):
                val = self.parse_expression()
            self._semi()
            return Return(val)
        if t.type == 'LBRACE':
            return self.parse_block()
        # expression statement
        expr = self.parse_expression()
        self._semi()
        return ExprStmt(expr)

    def _semi(self):
        if self.at('OP') and self.peek().value == ';':
            self.eat()

    def parse_var_decl(self):
        kind = self.eat().type  # var/let/const
        if self.at('LBRACE'):
            # destructuring: const { a, b } = expr  -- only used for `require(...)` module
            # bindings in this subset; each name becomes its own no-op declaration since
            # builtins (createCanvas, Database, Object) are recognized by name at call sites.
            self.eat('LBRACE')
            names = []
            while not self.at('RBRACE'):
                names.append(self.eat('IDENT').value)
                if self.at('OP') and self.peek().value == ',':
                    self.eat()
            self.eat('RBRACE')
            init = None
            if self.at('OP') and self.peek().value == '=':
                self.eat()
                init = self.parse_assign_expr()
            self._semi()
            return DestructureDecl(names, init)
        name = self.eat('IDENT').value
        init = None
        if self.at('OP') and self.peek().value == '=':
            self.eat()
            init = self.parse_assign_expr()
        self._semi()
        return VarDecl(kind, name, init)

    def parse_func_decl(self):
        self.eat('function')
        name = self.eat('IDENT').value
        self.eat('LPAREN')
        params = []
        while not self.at('RPAREN'):
            params.append(self.eat('IDENT').value)
            if self.at('OP') and self.peek().value == ',':
                self.eat()
        self.eat('RPAREN')
        body = self.parse_block()
        return FuncDecl(name, params, body)

    def parse_if(self):
        self.eat('if')
        self.eat('LPAREN')
        test = self.parse_expression()
        self.eat('RPAREN')
        cons = self.parse_statement()
        alt = None
        if self.at('else'):
            self.eat('else')
            alt = self.parse_statement()
        return If(test, cons, alt)

    def parse_for(self):
        self.eat('for')
        self.eat('LPAREN')
        init = None
        if not (self.at('OP') and self.peek().value == ';'):
            if self.at('var', 'let', 'const'):
                init = self.parse_var_decl_noSemi()
            else:
                init = ExprStmt(self.parse_expression())
        self.eat('OP')  # ;
        test = None
        if not (self.at('OP') and self.peek().value == ';'):
            test = self.parse_expression()
        self.eat('OP')  # ;
        update = None
        if not self.at('RPAREN'):
            update = self.parse_expression()
        self.eat('RPAREN')
        body = self.parse_statement()
        return For(init, test, update, body)

    def parse_var_decl_noSemi(self):
        kind = self.eat().type
        name = self.eat('IDENT').value
        init = None
        if self.at('OP') and self.peek().value == '=':
            self.eat()
            init = self.parse_assign_expr()
        return VarDecl(kind, name, init)

    def parse_while(self):
        self.eat('while')
        self.eat('LPAREN')
        test = self.parse_expression()
        self.eat('RPAREN')
        body = self.parse_statement()
        return While(test, body)

    # ---- expressions (precedence climbing) ----
    def parse_expression(self):
        return self.parse_assign_expr()

    def parse_assign_expr(self):
        left = self.parse_logical_or()
        if self.at('OP') and self.peek().value in ('=', '+=', '-=', '*=', '/='):
            op = self.eat().value
            value = self.parse_assign_expr()
            return Assign(left, op, value)
        return left

    def parse_logical_or(self):
        left = self.parse_logical_and()
        while self.at('OP') and self.peek().value == '||':
            self.eat()
            right = self.parse_logical_and()
            left = LogicalOp('||', left, right)
        return left

    def parse_logical_and(self):
        left = self.parse_equality()
        while self.at('OP') and self.peek().value == '&&':
            self.eat()
            right = self.parse_equality()
            left = LogicalOp('&&', left, right)
        return left

    def parse_equality(self):
        left = self.parse_relational()
        while self.at('OP') and self.peek().value in ('==', '!=', '===', '!=='):
            op = self.eat().value
            right = self.parse_relational()
            left = BinOp(op, left, right)
        return left

    def parse_relational(self):
        left = self.parse_additive()
        while self.at('OP') and self.peek().value in ('<', '>', '<=', '>='):
            op = self.eat().value
            right = self.parse_additive()
            left = BinOp(op, left, right)
        return left

    def parse_additive(self):
        left = self.parse_multiplicative()
        while self.at('OP') and self.peek().value in ('+', '-'):
            op = self.eat().value
            right = self.parse_multiplicative()
            left = BinOp(op, left, right)
        return left

    def parse_multiplicative(self):
        left = self.parse_unary()
        while self.at('OP') and self.peek().value in ('*', '/', '%'):
            op = self.eat().value
            right = self.parse_unary()
            left = BinOp(op, left, right)
        return left

    def parse_unary(self):
        if self.at('OP') and self.peek().value in ('!', '-', '+'):
            op = self.eat().value
            operand = self.parse_unary()
            return UnaryOp(op, operand, prefix=True)
        if self.at('OP') and self.peek().value in ('++', '--'):
            op = self.eat().value
            operand = self.parse_unary()
            return UnaryOp(op, operand, prefix=True)
        return self.parse_postfix()

    def parse_postfix(self):
        node = self.parse_call_member()
        if self.at('OP') and self.peek().value in ('++', '--'):
            op = self.eat().value
            node = UnaryOp(op, node, prefix=False)
        return node

    def parse_call_member(self):
        if self.at('new'):
            self.eat('new')
            callee = self.parse_primary()
            args = []
            if self.at('LPAREN'):
                args = self.parse_args()
            node = New(callee, args)
        else:
            node = self.parse_primary()
        while True:
            if self.at('OP') and self.peek().value == '.':
                self.eat()
                prop = self.eat('IDENT').value
                node = Member(node, prop, False)
            elif self.at('LBRACK'):
                self.eat('LBRACK')
                idx = self.parse_expression()
                self.eat('RBRACK')
                node = Member(node, idx, True)
            elif self.at('LPAREN'):
                args = self.parse_args()
                node = Call(node, args)
            else:
                break
        return node

    def parse_args(self):
        self.eat('LPAREN')
        args = []
        while not self.at('RPAREN'):
            args.append(self.parse_assign_expr())
            if self.at('OP') and self.peek().value == ',':
                self.eat()
        self.eat('RPAREN')
        return args

    def parse_primary(self):
        t = self.peek()
        if t.type == 'NUMBER':
            self.eat()
            return NumberLit(t.value)
        if t.type == 'STRING':
            self.eat()
            return StringLit(t.value)
        if t.type == 'true':
            self.eat()
            return BoolLit(True)
        if t.type == 'false':
            self.eat()
            return BoolLit(False)
        if t.type == 'null':
            self.eat()
            return NullLit()
        if t.type == 'undefined':
            self.eat()
            return UndefinedLit()
        if t.type == 'console':
            self.eat()
            return Identifier('console')
        if t.type == 'IDENT':
            # lookahead for arrow function: IDENT =>
            if self.peek(1).type == 'ARROW':
                param = self.eat('IDENT').value
                self.eat('ARROW')
                return self._finish_arrow([param])
            self.eat()
            return Identifier(t.value)
        if t.type == 'LPAREN':
            # could be (a,b) => ... arrow, or parenthesized expr
            save = self.i
            try:
                self.eat('LPAREN')
                params = []
                while not self.at('RPAREN'):
                    params.append(self.eat('IDENT').value)
                    if self.at('OP') and self.peek().value == ',':
                        self.eat()
                self.eat('RPAREN')
                if self.at('ARROW'):
                    self.eat('ARROW')
                    return self._finish_arrow(params)
                raise SyntaxError('not arrow')
            except SyntaxError:
                self.i = save
            self.eat('LPAREN')
            e = self.parse_expression()
            self.eat('RPAREN')
            return e
        if t.type == 'LBRACK':
            self.eat('LBRACK')
            elems = []
            while not self.at('RBRACK'):
                elems.append(self.parse_assign_expr())
                if self.at('OP') and self.peek().value == ',':
                    self.eat()
            self.eat('RBRACK')
            return ArrayLit(elems)
        if t.type == 'LBRACE':
            self.eat('LBRACE')
            pairs = []
            while not self.at('RBRACE'):
                if self.at('STRING'):
                    key = self.eat().value
                else:
                    key = self.eat('IDENT').value
                self.eat('OP')  # :
                val = self.parse_assign_expr()
                pairs.append((key, val))
                if self.at('OP') and self.peek().value == ',':
                    self.eat()
            self.eat('RBRACE')
            return ObjectLit(pairs)
        raise SyntaxError(f'Unexpected token {t.type}({t.value!r})')

    def _finish_arrow(self, params):
        if self.at('LBRACE'):
            body = self.parse_block()
            return ArrowFunc(params, body, False)
        expr = self.parse_assign_expr()
        return ArrowFunc(params, expr, True)


def parse(src):
    tokens = tokenize(src)
    p = Parser(tokens)
    return p.parse_program()
