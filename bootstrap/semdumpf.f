// Dumps bootstrap/semantic.f's result in the canonical form
// bootstrap/semdump.py emits from the Python side (claude.md #280).
//
// A lex or parse failure prints a single SEMERR line instead: a source
// rejected before analysis is still a source both implementations must
// reject in the same place, and which stage said so is not part of the
// claim.

import semantic.f

initLexer()

int ti = 0
arr[text] tks = TK_SRC.split(' ')
while ti < tks.length {
    TYPE_KEYWORDS[tks[ti]] = 1
    ti++
}

blob source = argv[1]
arr[Tok] toks = tokenize(source, 0, source.length)

int errAt = 0 - 1
int e = 0
while e < toks.length {
    if toks[e].kind == 'LEXERR' { errAt = e break }
    e++
}
if errAt >= 0 {
    log(`SEMERR|${toks[errAt].line}|${toks[errAt].col}`)
    close(0)
}

// Imports resolve against the entry file's own directory. `text` has
// no .slice() -- only ascii and blob do (specification.md 16.3) -- so
// the prefix is accumulated a character at a time.
text entry = argv[1]
int slash = 0 - 1
int c = 0
while c < entry.length {
    if entry.charCodeAt(c) == 47 { slash = c }
    c++
}
int d = 0
while d <= slash {
    BASE_DIR = BASE_DIR + entry.charCodeAt(d).toChar()
    d++
}

TOKS = toks
POS = 0
arr[Node] body = parseProgram()
if FAILED {
    log(`SEMERR|${FAIL_LINE}|${FAIL_COL}`)
    close(0)
}

arr[text] records = analyzeProgram(body)
sortLines(records)

text out = ''
int i = 0
while i < records.length {
    out = out + records[i] + 10.toChar()
    i++
}
log(out)
