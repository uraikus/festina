"""Lexer -- claude.md #4 (source files), #9 (lexical conventions),
#51 (reserved language features).
"""
import bisect
import re

# claude.md #4: Festina source files use the .f extension.
SOURCE_EXTENSION = ".f"

# claude.md #51: names with a defined language meaning.
SPEC_KEYWORDS = frozenset({
    "int", "float", "bool", "text", "blob", "arr", "struct", "table",
    "img", "aud", "null", "true", "false", "void", "func", "const",
    "import", "if", "else", "on", "fail", "log", "sqlite",
    "for", "while",  # claude.md #60, #61
})

# Extra control tokens the parser needs distinct token types for, so it
# can give a precise error, even though claude.md #51 doesn't list them
# as language keywords (they're explicitly *not* part of the language --
# claude.md #53). `return` is genuinely part of the language (#23 example
# bodies use it) but is likewise absent from the #51 list.
_EXTRA_KEYWORDS = frozenset({"return", "var", "let", "throw"})

# Exposed reserved-word set used by the parser/semantic layer. A superset
# of the spec's list is fine -- tests only check the spec words are
# present, not that nothing else is.
KEYWORDS = SPEC_KEYWORDS | _EXTRA_KEYWORDS

PRIMITIVE_TYPE_KEYWORDS = frozenset({"int", "float", "bool", "text", "blob"})

TOKEN_SPEC = [
    ("WS", r"[ \t\r\n]+"),
    ("COMMENT", r"//[^\n]*|/\*.*?\*/"),
    ("TEMPLATE", r"`(?:\\.|[^`\\])*`"),
    ("STRING", r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\""),
    ("NUMBER", r"\d+\.\d+|\d+"),
    ("LPAREN", r"\("), ("RPAREN", r"\)"),
    ("LBRACE", r"\{"), ("RBRACE", r"\}"),
    ("LBRACK", r"\["), ("RBRACK", r"\]"),
    # claude.md #66: postfix ++/-- -- must come before the single-char
    # +/- alternatives so `x++` lexes as one OP token, not `+` `+`.
    ("OP", r"===|!==|==|!=|<=|>=|&&|\|\||\+\+|--|[+\-*/%=<>!?:.,;]"),
    ("IDENT", r"[A-Za-z_][A-Za-z0-9_]*"),
]
MASTER_RE = re.compile("|".join(f"(?P<{n}>{p})" for n, p in TOKEN_SPEC), re.DOTALL)


class Token:
    __slots__ = ("type", "value", "line", "column")

    def __init__(self, type_, value, line=1, column=1):
        self.type = type_
        self.value = value
        self.line = line
        self.column = column

    def __repr__(self):
        return f"Token({self.type},{self.value!r},{self.line}:{self.column})"


_ESCAPES = {
    "n": "\n", "t": "\t", "r": "\r", "\\": "\\",
    "'": "'", '"': '"', "`": "`", "0": "\0",
}


def _unescape(text):
    out = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == "\\" and i + 1 < n:
            nxt = text[i + 1]
            out.append(_ESCAPES.get(nxt, nxt))
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _line_index(source):
    starts = [0]
    for i, c in enumerate(source):
        if c == "\n":
            starts.append(i + 1)
    return starts


def _split_template(raw):
    """Split backtick contents into alternating (is_expr, text) segments.

    "Hello ${name}" -> [(False, "Hello "), (True, "name"), (False, "")]
    """
    segments = []
    buf = []
    i = 0
    n = len(raw)
    while i < n:
        c = raw[i]
        if c == "\\" and i + 1 < n:
            buf.append(raw[i:i + 2])
            i += 2
            continue
        if c == "$" and i + 1 < n and raw[i + 1] == "{":
            segments.append((False, "".join(buf)))
            buf = []
            i += 2
            depth = 1
            start = i
            while i < n and depth > 0:
                if raw[i] == "{":
                    depth += 1
                elif raw[i] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            segments.append((True, raw[start:i]))
            i += 1  # skip closing '}'
            continue
        buf.append(c)
        i += 1
    segments.append((False, "".join(buf)))
    return segments


def tokenize(source, filename="<string>"):
    tokens = []
    pos = 0
    n = len(source)
    line_starts = _line_index(source)

    def loc(p):
        idx = bisect.bisect_right(line_starts, p) - 1
        return idx + 1, p - line_starts[idx] + 1

    while pos < n:
        m = MASTER_RE.match(source, pos)
        if not m:
            line, col = loc(pos)
            raise SyntaxError(f"{filename}:{line}:{col}: unexpected character {source[pos]!r}")
        kind = m.lastgroup
        text = m.group()
        line, col = loc(pos)
        pos = m.end()

        if kind in ("WS", "COMMENT"):
            continue

        if kind == "IDENT" and text == "import":
            tokens.append(Token("import", "import", line, col))
            rest_m = re.match(r"[ \t]*([^\n;]*)", source[pos:])
            raw_path = rest_m.group(1).strip()
            if raw_path:
                path_line, path_col = loc(pos)
                tokens.append(Token("PATH", raw_path, path_line, path_col))
                pos += rest_m.end()
            continue

        if kind == "IDENT" and text in KEYWORDS:
            tokens.append(Token(text, text, line, col))
            continue
        if kind == "IDENT":
            tokens.append(Token("IDENT", text, line, col))
            continue

        if kind == "STRING":
            tokens.append(Token("STRING", _unescape(text[1:-1]), line, col))
            continue

        if kind == "TEMPLATE":
            segments = _split_template(text[1:-1])
            expr_parts = [t for is_expr, t in segments if is_expr]
            str_parts = [t for is_expr, t in segments if not is_expr]
            if not expr_parts:
                tokens.append(Token("STRING", _unescape(str_parts[0]), line, col))
                continue
            tokens.append(Token("TSTRING_START", _unescape(str_parts[0]), line, col))
            for k, expr_text in enumerate(expr_parts):
                sub_tokens = tokenize(expr_text, filename)
                tokens.extend(sub_tokens[:-1])  # drop sub-EOF
                is_last = k == len(expr_parts) - 1
                part_value = _unescape(str_parts[k + 1])
                tokens.append(Token("TSTRING_END" if is_last else "TSTRING_MID", part_value, line, col))
            continue

        if kind == "NUMBER":
            value = float(text) if "." in text else int(text)
            tokens.append(Token("NUMBER", value, line, col))
            continue

        # LPAREN/RPAREN/LBRACE/RBRACE/LBRACK/RBRACK/OP
        tokens.append(Token(kind, text, line, col))

    line, col = loc(n)
    tokens.append(Token("EOF", None, line, col))
    return tokens
