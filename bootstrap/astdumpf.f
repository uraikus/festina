// Dumps bootstrap/parser.f's AST in the canonical form
// bootstrap/astdump.py emits from the Python side, one top-level
// statement per line (claude.md #273).
//
// A parse failure prints a single PARSEERR line instead, so the
// differential test covers rejection as well as acceptance -- the same
// shape bootstrap/lexdump.f uses for LEXERR.

import parser.f

initLexer()

int ti = 0
arr[text] tks = TK_SRC.split(' ')
while ti < tks.length {
    TYPE_KEYWORDS[tks[ti]] = 1
    ti++
}

blob source = argv[1]
arr[Tok] toks = tokenize(source, 0, source.length)

// A source the LEXER rejects never reaches the parser in Python either
// (parse() tokenizes first), so this reports the same thing.
int errAt = 0 - 1
int e = 0
while e < toks.length {
    if toks[e].kind == 'LEXERR' { errAt = e break }
    e++
}
if errAt >= 0 {
    log(`LEXERR|${toks[errAt].line}|${toks[errAt].col}`)
    close(0)
}

TOKS = toks
POS = 0
arr[Node] body = parseProgram()
if FAILED {
    log(`PARSEERR|${FAIL_LINE}|${FAIL_COL}`)
    close(0)
}

text out = ''
int i = 0
while i < body.length {
    out = out + dumpNode(body[i]) + 10.toChar()
    i++
}
log(out)
