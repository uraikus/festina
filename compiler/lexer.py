import re

KEYWORDS = {
    'var', 'let', 'const', 'function', 'return', 'if', 'else', 'for', 'while',
    'true', 'false', 'null', 'undefined', 'new', 'console'
}

TOKEN_SPEC = [
    ('WS',        r'[ \t\r\n]+'),
    ('COMMENT',   r'//[^\n]*|/\*.*?\*/'),
    ('NUMBER',    r'\d+\.\d+|\d+'),
    ('STRING',    r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\""),
    ('ARROW',     r'=>'),
    ('OP',        r'===|!==|==|!=|<=|>=|&&|\|\||\+=|-=|\*=|/=|\+\+|--|=>|=|<|>|\+|-|\*|/|%|!|\.|,|;|:|\?'),
    ('LPAREN',    r'\('), ('RPAREN', r'\)'),
    ('LBRACE',    r'\{'), ('RBRACE', r'\}'),
    ('LBRACK',    r'\['), ('RBRACK', r'\]'),
    ('IDENT',     r'[A-Za-z_$][A-Za-z0-9_$]*'),
]

MASTER_RE = re.compile('|'.join(f'(?P<{n}>{p})' for n, p in TOKEN_SPEC), re.DOTALL)


class Token:
    def __init__(self, type_, value, pos):
        self.type = type_
        self.value = value
        self.pos = pos

    def __repr__(self):
        return f'Token({self.type},{self.value!r})'


def tokenize(src):
    tokens = []
    pos = 0
    while pos < len(src):
        m = MASTER_RE.match(src, pos)
        if not m:
            raise SyntaxError(f'Unexpected character {src[pos]!r} at {pos}')
        kind = m.lastgroup
        text = m.group()
        pos = m.end()
        if kind in ('WS', 'COMMENT'):
            continue
        if kind == 'IDENT' and text in KEYWORDS:
            tokens.append(Token(text, text, pos))
        elif kind == 'STRING':
            raw = text[1:-1]
            raw = raw.encode().decode('unicode_escape')
            tokens.append(Token('STRING', raw, pos))
        elif kind == 'NUMBER':
            tokens.append(Token('NUMBER', float(text), pos))
        else:
            tokens.append(Token(kind, text, pos))
    tokens.append(Token('EOF', None, pos))
    return tokens
