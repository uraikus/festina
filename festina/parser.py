"""Parser -- builds an ast.Program from Festina source.

Covers claude.md #5-9, #17-28, #36-42, #51, #53.
"""
import re

from . import ast
from . import lexer as lexer_mod
from .errors import CompileError

TYPE_KEYWORDS = lexer_mod.PRIMITIVE_TYPE_KEYWORDS | {"img", "aud"}

_IMPORT_PATH_RE = re.compile(r"^[A-Za-z0-9_./-]+\.f$")


class _ParseError(Exception):
    def __init__(self, tok, message):
        self.tok = tok
        self.message = message


class Parser:
    def __init__(self, tokens, filename="<string>"):
        self.toks = tokens
        self.i = 0
        self.filename = filename

    # ---- token stream helpers ----
    def peek(self, k=0):
        idx = min(self.i + k, len(self.toks) - 1)
        return self.toks[idx]

    def at(self, *types):
        return self.peek().type in types

    def at_op(self, *values):
        t = self.peek()
        return t.type == "OP" and t.value in values

    def eat(self, type_=None):
        t = self.toks[self.i]
        if type_ is not None and t.type != type_:
            raise _ParseError(t, f"expected {type_}, found {t.type}({t.value!r})")
        self.i += 1
        return t

    def eat_op(self, value):
        t = self.toks[self.i]
        if not (t.type == "OP" and t.value == value):
            raise _ParseError(t, f"expected '{value}', found {t.type}({t.value!r})")
        self.i += 1
        return t

    def eat_name(self):
        """Like eat('IDENT'), but also accepts keyword tokens as names --
        e.g. the `log` in `console.log` is a member name, not a builtin
        call, even though `log` is itself a reserved word."""
        t = self.toks[self.i]
        if t.type != "IDENT" and t.type not in lexer_mod.KEYWORDS:
            raise _ParseError(t, f"expected a name, found {t.type}({t.value!r})")
        self.i += 1
        return t

    def err(self, tok, category, message):
        return CompileError(message, file=self.filename, line=tok.line,
                             column=tok.column, category=category)

    def _semi(self):
        if self.at_op(";"):
            self.eat()

    # ---- program ----
    def parse_program(self):
        body = []
        while not self.at("EOF"):
            body.append(self.parse_statement())
        return ast.Program(body)

    def parse_block(self):
        self.eat("LBRACE")
        body = []
        while not self.at("RBRACE"):
            body.append(self.parse_statement())
        self.eat("RBRACE")
        return ast.Block(body)

    # ---- types ----
    def parse_type(self):
        if self.at("arr"):
            self.eat("arr")
            self.eat("LBRACK")
            inner = self.parse_type()
            self.eat("RBRACK")
            return ast.ArrayTypeExpr(inner)
        if self.peek().type in TYPE_KEYWORDS:
            return self.eat().type
        if self.at("IDENT"):
            return self.eat().value
        t = self.peek()
        raise self.err(t, "invalid syntax", f"expected a type, found {t.type}({t.value!r})")

    def parse_typed_params(self):
        params = []
        while not self.at("RPAREN"):
            if not self.at("IDENT"):
                t = self.peek()
                raise self.err(t, "invalid syntax", f"expected a parameter name, found {t.type}({t.value!r})")
            name_tok = self.eat("IDENT")
            if not self.at_op(":"):
                raise self.err(self.peek(), "invalid function argument type",
                                f"parameter '{name_tok.value}' requires a type, e.g. '{name_tok.value}:int'")
            self.eat_op(":")
            type_expr = self.parse_type()
            params.append(ast.Param(name_tok.value, type_expr))
            if self.at_op(","):
                self.eat()
        return params

    def parse_fields(self):
        fields = []
        while not self.at("RBRACE"):
            name_tok = self.eat("IDENT")
            if not self.at_op(":"):
                raise self.err(self.peek(), "invalid syntax",
                                f"field '{name_tok.value}' requires a type, e.g. '{name_tok.value}:int'")
            self.eat_op(":")
            type_expr = self.parse_type()
            fields.append(ast.FieldDecl(name_tok.value, type_expr))
            if self.at_op(","):
                self.eat()
        return fields

    # ---- statements ----
    def parse_statement(self):
        t = self.peek()

        if t.type in ("var", "let"):
            raise self.err(t, "invalid declaration",
                            f"'{t.type}' is not part of Festina; declare variables as 'type name = value'")
        if t.type == "throw":
            raise self.err(t, "invalid statement", "'throw' is not supported; use fail() instead")
        if t.type == "import":
            return self.parse_import()
        if t.type == "const":
            return self.parse_const_decl()
        if t.type == "struct":
            return self.parse_struct_decl()
        if t.type == "table":
            return self.parse_table_decl()
        if t.type == "on":
            return self.parse_event_handler()
        if t.type == "if":
            return self.parse_if()
        if t.type == "return":
            return self.parse_return()
        if t.type == "func":
            raise self.err(t, "invalid function declaration",
                            "functions require an explicit return type, e.g. 'text func name() { }'")
        if self.peek(1).type == "func":
            return self.parse_func_decl()
        if t.type == "LBRACE":
            return self.parse_block()
        if self._looks_like_declaration():
            return self.parse_var_decl()

        expr = self.parse_expression()
        self._semi()
        return ast.ExprStmt(expr)

    def _looks_like_declaration(self):
        t0 = self.peek(0)
        t1 = self.peek(1)
        if t0.type in TYPE_KEYWORDS or t0.type == "arr":
            return True
        if t0.type == "IDENT" and t1.type == "IDENT":
            return True
        return False

    def parse_import(self):
        self.eat("import")
        path_tok = self.eat("PATH")
        if not path_tok.value or not _IMPORT_PATH_RE.match(path_tok.value):
            raise self.err(path_tok, "invalid import", f"invalid import path {path_tok.value!r}")
        return ast.ImportDecl(path_tok.value, path_tok.line, path_tok.column)

    def parse_var_decl(self):
        t = self.peek()
        type_expr = self.parse_type()
        name_tok = self.eat("IDENT")
        init = None
        if self.at_op("="):
            self.eat()
            init = self.parse_assign_expr()
        self._semi()
        return ast.VarDecl(type_expr, name_tok.value, init, is_const=False, line=t.line, column=t.column)

    def parse_const_decl(self):
        t = self.eat("const")
        type_expr = self.parse_type()
        if not self.at("IDENT"):
            raise self.err(self.peek(), "invalid declaration", "expected a constant name after the type")
        name_tok = self.eat("IDENT")
        init = None
        if self.at_op("="):
            self.eat()
            init = self.parse_assign_expr()
        self._semi()
        return ast.VarDecl(type_expr, name_tok.value, init, is_const=True, line=t.line, column=t.column)

    def parse_func_decl(self):
        t = self.peek()
        if self.at("void"):
            self.eat("void")
            return_type = "void"
        else:
            return_type = self.parse_type()
        self.eat("func")
        name_tok = self.eat("IDENT")
        self.eat("LPAREN")
        params = self.parse_typed_params()
        self.eat("RPAREN")
        body = self.parse_block()
        return ast.FuncDecl(name_tok.value, return_type, params, body, t.line, t.column)

    def parse_struct_decl(self):
        t = self.eat("struct")
        name_tok = self.eat("IDENT")
        self.eat("LBRACE")
        fields = self.parse_fields()
        self.eat("RBRACE")
        return ast.StructDecl(name_tok.value, fields, t.line, t.column)

    def parse_table_decl(self):
        t = self.eat("table")
        name_tok = self.eat("IDENT")
        self.eat("LBRACE")
        fields = self.parse_fields()
        self.eat("RBRACE")
        return ast.TableDecl(name_tok.value, fields, t.line, t.column)

    def parse_event_handler(self):
        t = self.eat("on")
        name_tok = self.eat("IDENT")
        self.eat("LPAREN")
        params = self.parse_typed_params()
        self.eat("RPAREN")
        body = self.parse_block()
        return ast.EventHandler(name_tok.value, params, body, t.line, t.column)

    def parse_if(self):
        t = self.eat("if")
        if self.at("LPAREN"):
            self.eat("LPAREN")
            test = self.parse_expression()
            self.eat("RPAREN")
        else:
            test = self.parse_expression()
        then = self.parse_block()
        orelse = None
        if self.at("else"):
            self.eat("else")
            orelse = self.parse_if() if self.at("if") else self.parse_block()
        return ast.IfStmt(test, then, orelse, t.line, t.column)

    def parse_return(self):
        t = self.eat("return")
        value = None
        if not self.at("RBRACE") and not self.at_op(";") and not self.at("EOF"):
            value = self.parse_expression()
        self._semi()
        return ast.Return(value, t.line, t.column)

    # ---- expressions ----
    def parse_expression(self):
        return self.parse_assign_expr()

    def parse_assign_expr(self):
        left = self.parse_ternary()
        if self.at_op("="):
            t = self.eat()
            right = self.parse_assign_expr()
            return ast.Assign(left, "=", right, t.line, t.column)
        return left

    def parse_ternary(self):
        test = self.parse_logical_or()
        if self.at_op("?"):
            t = self.eat()
            cons = self.parse_assign_expr()
            self.eat_op(":")
            alt = self.parse_assign_expr()
            return ast.Ternary(test, cons, alt, t.line, t.column)
        return test

    def parse_logical_or(self):
        left = self.parse_logical_and()
        while self.at_op("||"):
            self.eat()
            right = self.parse_logical_and()
            left = ast.LogicalOp("||", left, right)
        return left

    def parse_logical_and(self):
        left = self.parse_equality()
        while self.at_op("&&"):
            self.eat()
            right = self.parse_equality()
            left = ast.LogicalOp("&&", left, right)
        return left

    def parse_equality(self):
        left = self.parse_relational()
        while self.at_op("==", "!=", "===", "!=="):
            op_tok = self.eat()
            if op_tok.value in ("===", "!=="):
                suggestion = "==" if op_tok.value == "===" else "!="
                raise self.err(op_tok, "unsupported operator",
                                f"'{op_tok.value}' is not supported; use '{suggestion}' instead")
            right = self.parse_relational()
            left = ast.BinOp(op_tok.value, left, right, op_tok.line, op_tok.column)
        return left

    def parse_relational(self):
        left = self.parse_additive()
        while self.at_op("<", ">", "<=", ">="):
            op_tok = self.eat()
            right = self.parse_additive()
            left = ast.BinOp(op_tok.value, left, right, op_tok.line, op_tok.column)
        return left

    def parse_additive(self):
        left = self.parse_multiplicative()
        while self.at_op("+", "-"):
            op_tok = self.eat()
            right = self.parse_multiplicative()
            left = ast.BinOp(op_tok.value, left, right, op_tok.line, op_tok.column)
        return left

    def parse_multiplicative(self):
        left = self.parse_unary()
        while self.at_op("*", "/", "%"):
            op_tok = self.eat()
            right = self.parse_unary()
            left = ast.BinOp(op_tok.value, left, right, op_tok.line, op_tok.column)
        return left

    def parse_unary(self):
        if self.at_op("!", "-", "+"):
            op_tok = self.eat()
            operand = self.parse_unary()
            return ast.UnaryOp(op_tok.value, operand)
        return self.parse_call_member()

    def parse_call_member(self):
        node = self.parse_primary()
        while True:
            if self.at_op("."):
                self.eat()
                prop_tok = self.eat_name()
                node = ast.Member(node, prop_tok.value, False, prop_tok.line, prop_tok.column)
            elif self.at("LBRACK"):
                self.eat("LBRACK")
                idx = self.parse_expression()
                self.eat("RBRACK")
                node = ast.Member(node, idx, True)
            elif self.at("LPAREN"):
                args = self.parse_args()
                node = ast.Call(node, args)
            else:
                break
        return node

    def parse_args(self):
        self.eat("LPAREN")
        args = []
        while not self.at("RPAREN"):
            args.append(self.parse_assign_expr())
            if self.at_op(","):
                self.eat()
        self.eat("RPAREN")
        return args

    def parse_primary(self):
        t = self.peek()
        if t.type == "NUMBER":
            self.eat()
            return ast.NumberLit(t.value)
        if t.type == "STRING":
            self.eat()
            return ast.StringLit(t.value)
        if t.type == "TSTRING_START":
            return self.parse_template()
        if t.type == "true":
            self.eat()
            return ast.BoolLit(True)
        if t.type == "false":
            self.eat()
            return ast.BoolLit(False)
        if t.type == "null":
            self.eat()
            return ast.NullLit()
        if t.type in ("log", "fail", "sqlite"):
            self.eat()
            return ast.Identifier(t.type, t.line, t.column)
        if t.type == "IDENT":
            self.eat()
            return ast.Identifier(t.value, t.line, t.column)
        if t.type == "LPAREN":
            self.eat()
            e = self.parse_expression()
            self.eat("RPAREN")
            return e
        if t.type == "LBRACK":
            self.eat()
            elems = []
            while not self.at("RBRACK"):
                elems.append(self.parse_assign_expr())
                if self.at_op(","):
                    self.eat()
            self.eat("RBRACK")
            return ast.ArrayLit(elems)
        raise self.err(t, "invalid syntax", f"unexpected token {t.type}({t.value!r})")

    def parse_template(self):
        start = self.eat("TSTRING_START")
        parts = [start.value]
        exprs = []
        while True:
            exprs.append(self.parse_assign_expr())
            if self.at("TSTRING_MID"):
                tok = self.eat("TSTRING_MID")
                parts.append(tok.value)
                continue
            tok = self.eat("TSTRING_END")
            parts.append(tok.value)
            break
        return ast.TemplateLit(parts, exprs)


def parse(source, filename="<string>"):
    tokens = lexer_mod.tokenize(source, filename)
    p = Parser(tokens, filename)
    try:
        return p.parse_program()
    except _ParseError as e:
        raise CompileError(e.message, file=filename, line=e.tok.line,
                            column=e.tok.column, category="invalid syntax") from e
    except CompileError:
        raise
    except SyntaxError as e:
        raise CompileError(str(e), file=filename, category="invalid syntax") from e
    except IndexError as e:
        raise CompileError("unexpected end of input", file=filename, category="invalid syntax") from e
