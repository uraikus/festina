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
    "map",  # claude.md #72
    "break", "continue",  # claude.md #73
    "http", "socket",  # claude.md #151
})

# Extra control tokens the parser needs distinct token types for, so it
# can give a precise error, even though claude.md #51 doesn't list them
# as language keywords (they're explicitly *not* part of the language --
# claude.md #53). `return` is genuinely part of the language (#23 example
# bodies use it) but is likewise absent from the #51 list.
# claude.md #111 adds `free` and `delete` -- real statements, so the
# parser needs distinct token types to dispatch on. Both remain valid
# MEMBER names (parser.eat_name accepts keyword tokens), which is what
# keeps blob's `f.delete()` parsing.
_EXTRA_KEYWORDS = frozenset({"return", "var", "let", "throw", "free", "delete"})

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
    # claude.md #142: `=>` (arrow function) must come before the
    # single-char class too, for the identical reason -- otherwise
    # `=>` would lex as `=` then `>`, two separate OP tokens.
    ("OP", r"===|!==|==|!=|<=|>=|=>|&&|\|\||\+\+|--|[+\-*/%=<>!?:.,;]"),
    ("IDENT", r"[A-Za-z_][A-Za-z0-9_]*"),
]
MASTER_RE = re.compile("|".join(f"(?P<{n}>{p})" for n, p in TOKEN_SPEC), re.DOTALL)

# claude.md #67: /pattern/flags regex literals. The classic JS lexical
# ambiguity -- a leading '/' could be division (`a / b`) or the start of
# a regex literal (`/foo/`) -- is resolved the same way real JS lexers
# resolve it: a regex literal can only start where an *expression* is
# expected, never immediately after something that could itself be the
# tail end of one. `_EXPR_ENDING_TOKEN_TYPES`/`_EXPR_ENDING_OP_VALUES`
# are exactly those "tail end of an expression" tokens -- everything
# else (an operator, `(`/`[`/`{`, `,`, start of input, ...) means a `/`
# here is trying to open a regex literal instead. This is deliberately a
# denylist (permissive by default) rather than an exhaustive allowlist:
# getting it wrong for some keyword this grammar never actually places
# next to a bare `/` costs nothing, while missing a real "this ends an
# expression" case would misparse ordinary division.
_EXPR_ENDING_TOKEN_TYPES = frozenset({
    "IDENT", "NUMBER", "STRING", "TSTRING_END",
    "RPAREN", "RBRACK",
    "true", "false", "null",
    # log/fail/sqlite are lexer keywords but behave as plain identifiers
    # in expression position (see parser.parse_primary) -- `log / 2`
    # should lex as division, not attempt a regex literal at the `/`.
    "log", "fail", "sqlite",
})
_EXPR_ENDING_OP_VALUES = frozenset({"++", "--"})


def _regex_literal_may_start_here(prev_token):
    if prev_token is None:  # start of input, or the start of a `${...}`
        return True         # template interpolation's own sub-tokenize call
    if prev_token.type in _EXPR_ENDING_TOKEN_TYPES:
        return False
    if prev_token.type == "OP" and prev_token.value in _EXPR_ENDING_OP_VALUES:
        return False
    return True


def _try_lex_regex_literal(source, start):
    """Attempt to lex a /pattern/flags literal starting at `source[start]`
    ('/', already confirmed by the caller not to be opening a `//`/`/*`
    comment). Returns (pattern, flags, end_pos), or None if this isn't a
    validly terminated regex literal -- unterminated before end of line
    or end of input, which almost certainly means the '/' actually was
    division and something after it just happens to contain more '/'
    characters (or a genuine syntax error the parser will catch its own
    way). Regex literals can't span multiple lines, matching JS.

    Flag characters are collected but not validated here (the lexer has
    no CompileError-with-location machinery the way the parser does) --
    see Parser.parse_primary's REGEX handling for the actual "is this a
    supported flag" check, now possible at compile time (unlike
    regex()'s flags *argument*, whose value is an arbitrary runtime
    text expression the compiler can't inspect)."""
    n = len(source)
    i = start + 1
    pattern_chars = []
    while i < n and source[i] != "/":
        if source[i] == "\n":
            return None
        if source[i] == "\\" and i + 1 < n:
            if source[i + 1] == "/":
                # \/ is JS's own delimiter-escape syntax -- POSIX
                # regcomp() never requires (or expects) '/' to be
                # escaped at all, so this unescapes straight to a
                # literal '/' in the pattern text passed to the runtime,
                # rather than being passed through as "\/" (which glibc's
                # regcomp would otherwise choke on -- backslash followed
                # by an ordinary character with no defined escape meaning).
                pattern_chars.append("/")
            else:
                # Every other backslash sequence (\w, \d, \s, \., \\, ...)
                # is passed straight through untouched -- glibc's regcomp
                # accepts \w/\d/\s/\b etc. as GNU extensions even in
                # REG_EXTENDED mode (verified directly against this
                # runtime's own libc), so the familiar JS shorthand
                # classes work in practice, not just POSIX ERE's own
                # narrower official escape set.
                pattern_chars.append(source[i])
                pattern_chars.append(source[i + 1])
            i += 2
            continue
        pattern_chars.append(source[i])
        i += 1
    if i >= n or source[i] != "/":
        return None
    i += 1  # consume the closing '/'
    flags_start = i
    while i < n and source[i].isalpha():
        i += 1
    return "".join(pattern_chars), source[flags_start:i], i


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

    # The last token actually emitted (WS/COMMENT never count, since
    # they're never emitted at all) -- None at the very start, and
    # implicitly reset to None on every recursive tokenize() call this
    # function makes for a `${...}` template interpolation, which is
    # exactly right: a fresh expression context starts there too. Read
    # by _regex_literal_may_start_here to disambiguate a leading '/'.
    prev_significant = None

    while pos < n:
        if (source[pos] == "/" and source[pos:pos + 2] not in ("//", "/*")
                and _regex_literal_may_start_here(prev_significant)):
            regex_result = _try_lex_regex_literal(source, pos)
            if regex_result is not None:
                pattern, flags, end = regex_result
                line, col = loc(pos)
                tok = Token("REGEX", (pattern, flags), line, col)
                tokens.append(tok)
                prev_significant = tok
                pos = end
                continue
            # Not a validly terminated regex literal after all (e.g. no
            # closing '/' before a newline) -- fall through to ordinary
            # tokenization below, which lexes the '/' as plain division.

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
            prev_significant = tokens[-1]
            continue

        if kind == "IDENT" and text in KEYWORDS:
            tokens.append(Token(text, text, line, col))
            prev_significant = tokens[-1]
            continue
        if kind == "IDENT":
            tokens.append(Token("IDENT", text, line, col))
            prev_significant = tokens[-1]
            continue

        if kind == "STRING":
            tokens.append(Token("STRING", _unescape(text[1:-1]), line, col))
            prev_significant = tokens[-1]
            continue

        if kind == "TEMPLATE":
            segments = _split_template(text[1:-1])
            expr_parts = [t for is_expr, t in segments if is_expr]
            str_parts = [t for is_expr, t in segments if not is_expr]
            if not expr_parts:
                tokens.append(Token("STRING", _unescape(str_parts[0]), line, col))
                prev_significant = tokens[-1]
                continue
            tokens.append(Token("TSTRING_START", _unescape(str_parts[0]), line, col))
            for k, expr_text in enumerate(expr_parts):
                sub_tokens = tokenize(expr_text, filename)
                tokens.extend(sub_tokens[:-1])  # drop sub-EOF
                is_last = k == len(expr_parts) - 1
                part_value = _unescape(str_parts[k + 1])
                tokens.append(Token("TSTRING_END" if is_last else "TSTRING_MID", part_value, line, col))
            prev_significant = tokens[-1]
            continue

        if kind == "NUMBER":
            value = float(text) if "." in text else int(text)
            tokens.append(Token("NUMBER", value, line, col))
            prev_significant = tokens[-1]
            continue

        # LPAREN/RPAREN/LBRACE/RBRACE/LBRACK/RBRACK/OP
        tokens.append(Token(kind, text, line, col))
        prev_significant = tokens[-1]

    line, col = loc(n)
    tokens.append(Token("EOF", None, line, col))
    return tokens
