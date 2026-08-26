"""Parser -- builds an ast.Program from Festina source.

Covers claude.md #5-9, #17-28, #36-42, #51, #53.
"""
import re

from . import ast
from . import lexer as lexer_mod
from .errors import CompileError

TYPE_KEYWORDS = lexer_mod.PRIMITIVE_TYPE_KEYWORDS | {"img", "aud", "http", "socket"}

_IMPORT_PATH_RE = re.compile(r"^[A-Za-z0-9_./-]+\.f$")

# claude.md #67: /pattern/flags literal flags. Unlike regex()'s flags
# *argument* (an arbitrary runtime text expression the compiler can't
# inspect), a literal's flags are plain text known at parse time, so
# they can -- and should -- be validated right here, the same way any
# other syntax error is. 'i' is meaningful (case-insensitive matching,
# same as regex()'s own 'i' flag); claude.md #107 made 'g' meaningful
# too, where it used to be accepted-but-inert. It now means for
# .replace() exactly what it means in JS -- replace every match rather
# than just the first -- and .replaceAll(), which used to be how that
# was said, is gone. Note that neither flag does anything HERE beyond
# being spelled correctly: 'g' is recorded on the compiled pattern and
# read back by the runtime, because `regex(p, f)` builds its flags from
# a runtime expression and both spellings have to obey the same rule.
# Anything else (m/s/u/y/d, JS's own further flags) isn't
# supported by the POSIX-regex-backed runtime this compiles down to, so
# it's a clear compile error rather than a silently-ignored letter.
_SUPPORTED_REGEX_FLAGS = frozenset("gi")


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
        if self.at("amor"):
            # claude.md #156: amor map[T] / amor arr[T] -- amortized
            # growth, a modifier on the container type itself rather
            # than a separate type name (composes with `const` the
            # same way: `const amor map[text] m`, parsed in
            # parse_const_decl below). Only map[T]/arr[T] have a growth
            # strategy to modify at all -- anything else after `amor`
            # is a clear, direct error rather than a confusing
            # downstream one.
            amor_tok = self.eat("amor")
            if not (self.at("arr") or self.at("map")):
                t = self.peek()
                raise self.err(amor_tok, "invalid syntax",
                                f"'amor' must be followed by arr[T] or map[T], found {t.type}({t.value!r})")
            inner_type = self.parse_type()
            inner_type.amortized = True
            return inner_type
        if self.at("arr"):
            self.eat("arr")
            self.eat("LBRACK")
            inner = self.parse_type()
            self.eat("RBRACK")
            return ast.ArrayTypeExpr(inner)
        if self.at("map"):
            self.eat("map")
            self.eat("LBRACK")
            inner = self.parse_type()
            self.eat("RBRACK")
            return ast.MapTypeExpr(inner)
        if self.at("func"):
            return self.parse_func_type()
        if self.peek().type in TYPE_KEYWORDS:
            return self.eat().type
        if self.at("IDENT"):
            return self.eat().value
        t = self.peek()
        raise self.err(t, "invalid syntax", f"expected a type, found {t.type}({t.value!r})")

    def parse_func_type(self):
        """claude.md #141: `func[T, T, ...]:R` as a TYPE (not a
        declaration -- see parse_statement's own `func` handling for how
        the two are told apart: this form's `func` is always
        immediately followed by `[`, a declaration's is always followed
        by a name). `func[]:void` is a zero-argument, void-returning
        function type -- the empty-brackets case is handled the same
        way arr[T]/map[T]'s own bracket pair is, just with zero or more
        comma-separated types instead of exactly one."""
        self.eat("func")
        self.eat("LBRACK")
        param_types = []
        while not self.at("RBRACK"):
            param_types.append(self.parse_type())
            if self.at_op(","):
                self.eat()
        self.eat("RBRACK")
        self.eat_op(":")
        if self.at("void"):
            self.eat("void")
            return_type = "void"
        else:
            return_type = self.parse_type()
        return ast.FuncTypeExpr(param_types, return_type)

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
            return self.parse_throw()
        if t.type == "try":
            return self.parse_try()
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
        if t.type == "while":
            return self.parse_while()
        if t.type == "for":
            return self.parse_for()
        if t.type == "return":
            return self.parse_return()
        if t.type == "free":
            # claude.md #111: `free name`. The target is a bare variable
            # -- freeing THROUGH an expression (a field, an element)
            # would be `delete`'s territory, and a computed target has
            # no binding to null afterwards.
            free_tok = self.eat()
            name_tok = self.eat("IDENT")
            return ast.FreeStmt(name_tok.value, free_tok.line, free_tok.column)
        if t.type == "delete":
            # claude.md #111: `delete m.key` / `delete m['key']` /
            # `delete s.field`. Parsed as a full postfix expression and
            # then required to be a Member, so the error for `delete x`
            # can say what delete is FOR instead of being a parse error.
            del_tok = self.eat()
            target = self.parse_call_member()
            if not isinstance(target, ast.Member):
                raise self.err(del_tok, "invalid statement",
                                "delete removes a map key or nulls a struct/row "
                                "field (delete m['key'], delete s.field) -- to "
                                "release a whole variable, use `free name`")
            return ast.DeleteStmt(target, del_tok.line, del_tok.column)
        if t.type == "break":
            return self.parse_break()
        if t.type == "continue":
            return self.parse_continue()
        if t.type == "func" and self.peek(1).type != "LBRACK":
            # claude.md #141: `func[...]:...` (a first-class function
            # TYPE, always immediately followed by `[`) falls through to
            # _looks_like_declaration below instead of landing here --
            # only a bare `func name(...)` (missing its return type, the
            # one pre-existing case this guards) is still unconditionally
            # rejected.
            raise self.err(t, "invalid function declaration",
                            "functions require an explicit return type, e.g. 'text func name() { }'")
        if self._starts_func_decl():
            return self.parse_func_decl()
        # claude.md #164: `http {...}` -- an anonymous, fire-and-forget
        # request: no variable name to call .send() on, so the send is
        # implied. Checked BEFORE _looks_like_declaration (which would
        # otherwise route this to parse_var_decl, whose own
        # `self.eat("IDENT")` right after the type expects a variable
        # name -- exactly what this form deliberately has none of) and
        # before the plain `t.type == "LBRACE"` block check above (this
        # branch only ever matches when `http` comes FIRST, so a bare
        # `{` at statement-start is still an ordinary block,
        # unambiguously). Desugars to `{...}.send()` at parse time --
        # semantic.py/codegen.py need no awareness this shorthand
        # exists at all, since `{...}.send()` (claude.md #164's OTHER
        # new shorthand) already handles the resulting AST shape.
        if t.type == "http" and self.peek(1).type == "LBRACE":
            return self.parse_http_anon_send()
        # claude.md #165 (extended to img/aud by #171): `blob
        # 'path'.callback(fn)` (also `img 'path'.callback(fn)`/`aud
        # 'path'.callback(fn)` -- see semantic.py's own comment) --
        # the anonymous,
        # fire-and-forget counterpart to `http {...}` just above, same
        # "checked before _looks_like_declaration would otherwise
        # misroute it" reasoning. Unlike `http {...}`, no AST
        # rewriting is needed here at all -- the type keyword is
        # discarded outright (it's redundant: `.callback()`'s own type
        # is always read off its argument func's OWN signature, see
        # semantic.py's `_infer_call`) and whatever expression follows
        # (expected to be a `.callback(...)` call, though nothing here
        # enforces that shape specifically -- semantic.py's own type
        # check on the discarded type keyword's absence means any
        # OTHER expression here is simply evaluated and its result
        # discarded, same as any other bare expression-statement) is
        # parsed and wrapped as an ordinary ExprStmt.
        if t.type in ("blob", "img", "aud") and self.peek(1).type != "IDENT":
            self.eat()  # the (redundant, purely readability) type keyword
            expr = self.parse_expression()
            self._semi()
            return ast.ExprStmt(expr)
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
        # claude.md #141: `func[...]:...` starts a declaration too (a
        # func-typed variable/constant) -- reached only once
        # parse_statement's own dedicated `func` check has already ruled
        # out the bare-`func`-with-no-`[`-following case (the "missing
        # return type" mistake, still always an error), so any `func`
        # reaching here is unambiguously the type-expression form.
        if t0.type in TYPE_KEYWORDS or t0.type in ("arr", "map", "amor", "func"):
            return True
        if t0.type == "IDENT" and t1.type == "IDENT":
            return True
        return False

    def _starts_func_decl(self):
        """True if the statement at the current position is
        `<return-type> func name(...)`. A plain `self.peek(1).type ==
        'func'` isn't enough once the return type is `arr[T]` -- that's
        multiple tokens (arr, [, T, ]), possibly nested (arr[arr[int]]),
        so this walks past a full type expression first."""
        end = self._type_expr_end(self.i)
        return self.toks[end].type == "func"

    def _type_expr_end(self, i):
        """Index of the token just past the type expression starting at
        token i, without consuming anything. Doesn't validate the type is
        well-formed -- parse_type() does that once we actually parse it;
        this only needs to skip past it far enough to check for whatever
        comes next (a declaration's own `func`, claude.md #142's own
        arrow-function `=>`, ...)."""
        if i >= len(self.toks):
            return i
        if self.toks[i].type == "amor":
            # claude.md #156: amor map[T] / amor arr[T] -- just a
            # one-token prefix ahead of whatever map[T]/arr[T] itself
            # spans, not a container needing its own [T] skip.
            return self._type_expr_end(i + 1)
        if self.toks[i].type in ("arr", "map"):
            i += 1
            if i < len(self.toks) and self.toks[i].type == "LBRACK":
                i += 1
                i = self._type_expr_end(i)
                if i < len(self.toks) and self.toks[i].type == "RBRACK":
                    i += 1
            return i
        if self.toks[i].type == "func" and i + 1 < len(self.toks) and self.toks[i + 1].type == "LBRACK":
            # claude.md #141: func[T, T, ...]:R -- skips the WHOLE
            # construct (param types, the `:`, and the return type,
            # itself possibly another nested func[...]:...), not just
            # the bare `func` keyword -- needed so a declaration whose
            # own return type IS a func[...]:... (`func[int]:int func
            # makeAdder() {...}`) is still recognized as a function
            # DECLARATION by _starts_func_decl, and so claude.md #142's
            # arrow-function lookahead can tell a `func[...]:...`-typed
            # arrow function's own return type apart from its params.
            i += 2  # past `func` and `[`
            while i < len(self.toks) and self.toks[i].type != "RBRACK":
                i = self._type_expr_end(i)
                if i < len(self.toks) and self.toks[i].type == "OP" and self.toks[i].value == ",":
                    i += 1
            if i < len(self.toks) and self.toks[i].type == "RBRACK":
                i += 1
            if i < len(self.toks) and self.toks[i].type == "OP" and self.toks[i].value == ":":
                i += 1
                i = self._type_expr_end(i)
            return i
        return i + 1

    def _starts_arrow_function(self):
        """claude.md #142: True if the CURRENT position starts
        `<returnType> (<params>) =>`. For an unambiguous return type --
        a TYPE_KEYWORD, `void`, arr[T], map[T], or func[...]:... -- none
        of which can ever start an ordinary EXPRESSION on their own
        (every one is a reserved word with no other meaning at an
        expression position, or -- arr[T]/map[T]/func[...]:... -- a
        keyword immediately followed by a bracket that has no
        expression-level meaning either), this only needs to confirm
        the type is immediately followed by `(`: nothing else that
        shape could possibly be, so anything after `(` that doesn't
        ALSO shape up as a param list ending in `) =>` is a genuine
        syntax error the eager parse in parse_arrow_function reports
        directly, not something this check needs to pre-verify.

        For a bare IDENT return type (a struct/table name), by
        contrast, `Point(x)` is also a perfectly ordinary function
        CALL -- the common case, by far -- so this scans the full
        candidate parameter list all the way to a matching `) =>`
        before ever committing, via _arrow_params_end, to avoid
        misparsing every such call as a broken arrow function.

        The FIRST token must itself look like the start of a type --
        TYPE_KEYWORDS, `void`, `arr`, `map`, `func`, or IDENT -- before
        _type_expr_end is even consulted. Skipping this gate was a real
        bug caught directly, not a hypothetical: `_type_expr_end`'s own
        fallback treats ANY unrecognized token as "a valid one-token
        type" (correct for its ORIGINAL callers, _starts_func_decl and
        _looks_like_declaration, both of which already gate on this
        exact same token set before ever calling it) -- so calling it
        unconditionally here misidentified `log(x)` itself as an arrow
        function (`log`'s own token type is the literal keyword `log`,
        not `IDENT`, so the bare "must confirm _type_expr_end result is
        followed by LPAREN" check alone let it through), breaking every
        `log(...)` call in the language until this gate was added."""
        t = self.peek()
        if t.type not in TYPE_KEYWORDS and t.type not in ("void", "arr", "map", "amor", "func", "IDENT"):
            return False
        end = self._type_expr_end(self.i)
        if end >= len(self.toks) or self.toks[end].type != "LPAREN":
            return False
        if t.type != "IDENT":
            return True
        return self._arrow_params_end(end) is not None

    def _arrow_params_end(self, lparen_index):
        """Returns the token index right after a `) =>` closing a
        well-formed `(name:type, name:type, ...)` parameter list
        starting at lparen_index (which must be an LPAREN); None if the
        tokens starting there don't shape up that way at all. Used only
        for the ambiguous bare-IDENT-return-type case in
        _starts_arrow_function above -- the unambiguous cases never
        call this, since parse_arrow_function's own eager parse (via
        the ordinary parse_typed_params) is what reports a malformed
        parameter list for those."""
        i = lparen_index + 1
        if i < len(self.toks) and self.toks[i].type == "RPAREN":
            i += 1
        else:
            while True:
                if i >= len(self.toks) or self.toks[i].type != "IDENT":
                    return None
                i += 1
                if not (i < len(self.toks) and self.toks[i].type == "OP" and self.toks[i].value == ":"):
                    return None
                i += 1
                i = self._type_expr_end(i)
                if i < len(self.toks) and self.toks[i].type == "OP" and self.toks[i].value == ",":
                    i += 1
                    continue
                if i < len(self.toks) and self.toks[i].type == "RPAREN":
                    i += 1
                    break
                return None
        if i < len(self.toks) and self.toks[i].type == "OP" and self.toks[i].value == "=>":
            return i + 1
        return None

    def parse_arrow_function(self):
        """claude.md #142: `<returnType> (params) => expr`, parsed once
        _starts_arrow_function has already confirmed the shape.
        `void`'s own special-cased (not handled by parse_type at all --
        it's a valid return type but never a valid ordinary variable/
        field/element type, the same asymmetry parse_func_decl/parse_
        func_type both already carry) rather than routed through
        parse_type, matching both of those."""
        t = self.peek()
        if self.at("void"):
            self.eat("void")
            return_type = "void"
        else:
            return_type = self.parse_type()
        self.eat("LPAREN")
        params = self.parse_typed_params()
        self.eat("RPAREN")
        self.eat_op("=>")
        body = self.parse_assign_expr()
        return ast.ArrowFuncExpr(params, return_type, body, t.line, t.column)

    def parse_import(self):
        self.eat("import")
        path_tok = self.eat("PATH")
        if not path_tok.value or not _IMPORT_PATH_RE.match(path_tok.value):
            raise self.err(path_tok, "invalid import", f"invalid import path {path_tok.value!r}")
        return ast.ImportDecl(path_tok.value, path_tok.line, path_tok.column)

    def parse_http_anon_send(self):
        """claude.md #164: `http {...}` -- desugars to `{...}.send()` at
        parse time (an ExprStmt wrapping a Call whose callee is a
        Member on the freshly-parsed MapLit) so semantic.py/codegen.py
        need no separate awareness of this spelling at all -- see
        parse_statement's own comment on why this is checked before
        _looks_like_declaration would otherwise misroute it."""
        t = self.eat("http")
        maplit = self.parse_primary()  # positioned at LBRACE -- produces an ast.MapLit
        callee = ast.Member(maplit, "send", computed=False, line=t.line, column=t.column)
        call = ast.Call(callee, [], line=t.line, column=t.column)
        self._semi()
        return ast.ExprStmt(call)

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

    def parse_while(self):
        # claude.md #61: `while condition { }` -- condition parens are
        # optional, same convention as parse_if.
        t = self.eat("while")
        if self.at("LPAREN"):
            self.eat("LPAREN")
            test = self.parse_expression()
            self.eat("RPAREN")
        else:
            test = self.parse_expression()
        body = self.parse_block()
        return ast.WhileStmt(test, body, t.line, t.column)

    def parse_for(self):
        # claude.md #60: `for initialization, condition, update { }` --
        # comma-separated, no parens. `init` is always a fresh
        # declaration (claude.md's own examples and "the initialization
        # variable is scoped to the loop body" wording both assume this);
        # parse_var_decl's optional trailing `_semi()` is a harmless
        # no-op here since the next token is a comma, not `;`.
        t = self.eat("for")
        init = self.parse_var_decl()
        self.eat_op(",")
        test = self.parse_expression()
        self.eat_op(",")
        update = self.parse_expression()
        body = self.parse_block()
        return ast.ForStmt(init, test, update, body, t.line, t.column)

    def parse_try(self):
        # claude.md #157: try { ... } catch (name:text) { ... } -- catch
        # is required (no bare `try` with no handler -- there would be
        # nothing distinguishing it from just writing the body directly),
        # and its variable's type annotation must be exactly `text`
        # (throw only ever raises text -- see parse_throw), spelled out
        # so a typo'd `catch(error:int)` is a clear error here rather
        # than a confusing one from semantic.py's own type checking.
        t = self.eat("try")
        try_body = self.parse_block()
        self.eat("catch")
        self.eat("LPAREN")
        name_tok = self.eat("IDENT")
        self.eat_op(":")
        type_tok = self.peek()
        if type_tok.type != "text":
            raise self.err(type_tok, "invalid syntax",
                            f"catch's variable is always text (a thrown value is always "
                            f"text), found {type_tok.type}({type_tok.value!r})")
        self.eat("text")
        self.eat("RPAREN")
        catch_body = self.parse_block()
        return ast.TryStmt(try_body, name_tok.value, catch_body, t.line, t.column)

    def parse_throw(self):
        # claude.md #157: throw <expr> -- unlike fail() (a call
        # expression), this is its own statement, the same shape
        # return/free/delete already are.
        t = self.eat("throw")
        value = self.parse_expression()
        self._semi()
        return ast.ThrowStmt(value, t.line, t.column)

    def parse_return(self):
        t = self.eat("return")
        value = None
        if not self.at("RBRACE") and not self.at_op(";") and not self.at("EOF"):
            value = self.parse_expression()
        self._semi()
        return ast.Return(value, t.line, t.column)

    def parse_break(self):
        # claude.md #73: a bare keyword, no value -- validated as
        # actually being inside a loop by semantic.py, not here (the
        # parser has no notion of "inside a loop", same division of
        # labor as every other semantic-level check in this compiler).
        t = self.eat("break")
        self._semi()
        return ast.BreakStmt(t.line, t.column)

    def parse_continue(self):
        t = self.eat("continue")
        self._semi()
        return ast.ContinueStmt(t.line, t.column)

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
                # claude.md #159: .toStruct(StructName)/.toArr(ElementType)
                # -- the one place in the language a call's own
                # "argument" is a TYPE, not a value expression. Special-
                # cased on the method name here (right before parsing
                # the parens, so a same-named user function/method
                # elsewhere is completely unaffected) rather than
                # generalizing "types as call arguments" anywhere else.
                if (isinstance(node, ast.Member) and not node.computed
                        and node.prop in ("toStruct", "toArr")):
                    args = [self._parse_type_arg()]
                else:
                    args = self.parse_args()
                node = ast.Call(node, args)
            else:
                break
        # claude.md #66: postfix ++/-- -- highest precedence, binds
        # tighter than unary, same as call/member access.
        if self.at_op("++", "--"):
            op_tok = self.eat()
            node = ast.PostfixOp(op_tok.value, node, op_tok.line, op_tok.column)
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

    def _parse_type_arg(self):
        # claude.md #159: .toStruct(T)/.toArr(T) -- exactly one type,
        # parsed with the same parse_type() every other type position
        # in the grammar already uses (a var decl, a param, a struct
        # field, ...), wrapped as an ast.TypeArg so semantic.py/
        # codegen.py can tell it apart from an ordinary expression
        # argument at a glance.
        self.eat("LPAREN")
        type_expr = self.parse_type()
        self.eat("RPAREN")
        return ast.TypeArg(type_expr)

    def parse_primary(self):
        t = self.peek()
        if self._starts_arrow_function():
            return self.parse_arrow_function()
        if t.type == "NUMBER":
            self.eat()
            return ast.NumberLit(t.value)
        if t.type == "STRING":
            self.eat()
            return ast.StringLit(t.value)
        if t.type == "TSTRING_START":
            return self.parse_template()
        if t.type == "REGEX":
            self.eat()
            pattern, flags = t.value
            seen = set()
            for f in flags:
                if f not in _SUPPORTED_REGEX_FLAGS:
                    raise self.err(
                        t, "invalid syntax",
                        f"unsupported regex flag '{f}' -- only 'i' "
                        f"(case-insensitive) and 'g' (replace every match, "
                        f"not just the first) are supported")
                if f in seen:
                    raise self.err(t, "invalid syntax", f"duplicate regex flag '{f}'")
                seen.add(f)
            return ast.RegexLit(pattern, flags, t.line, t.column)
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
        if t.type == "LBRACE":
            # claude.md #72: { key: value, ... } -- a map literal. Only
            # ever reached from expression position (parse_primary is
            # never called while parsing a statement, where '{' means a
            # block instead -- see parse_statement's own LBRACE check,
            # which runs first and never falls through to here), so
            # there's no ambiguity with block syntax to resolve.
            #
            # claude.md #162: shorthand entries -- a bare identifier
            # with no `: value` (JS's own object-literal shorthand,
            # `{headers}` for `{'headers': headers}`) is recognized
            # right here at parse time, not deferred to semantic
            # analysis: after parsing `key`, the NEXT token decides it
            # (`,`/`}` means shorthand, `:` means an ordinary explicit
            # entry) -- no lookahead beyond the one token parse_assign_expr
            # already consumed. Only a bare IDENT can be shorthand
            # (`key` must already be exactly ast.Identifier -- an
            # arbitrary computed key expression like `{a.b}` or
            # `{f()}` has no single name to reuse as both the literal
            # string key and the value reference, so it still requires
            # an explicit `: value`, caught below by the ordinary
            # eat_op(":") failing with its own clear error). This
            # produces the exact same (key_expr, value_expr) shape an
            # explicit entry does -- key becomes a TextLit of the
            # identifier's own name, value an Identifier reference to
            # it -- so nothing downstream (MapLit's own semantic/codegen
            # handling, or the new http-literal handling claude.md #162
            # also adds) needs to know shorthand was ever used at all.
            self.eat("LBRACE")
            entries = []
            while not self.at("RBRACE"):
                key = self.parse_assign_expr()
                if (isinstance(key, ast.Identifier)
                        and not self.at_op(":")):
                    value = ast.Identifier(key.name, key.line, key.column)
                    key = ast.StringLit(key.name)
                else:
                    self.eat_op(":")
                    value = self.parse_assign_expr()
                entries.append((key, value))
                if self.at_op(","):
                    self.eat()
            self.eat("RBRACE")
            return ast.MapLit(entries, t.line, t.column)
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
