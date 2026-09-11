// Dumps bootstrap/lexer.f's token stream in the canonical form
// bootstrap/difftest.py compares against festina/lexer.py's own
// (claude.md #271, #273).
//
// This is the entry point that used to live at the bottom of lexer.f.
// It moved out so lexer.f could become an importable library, which is
// what bootstrap/parser.f needs (claude.md #273) -- Festina's import
// model is a single translation unit, so a top-level statement in an
// imported file runs in every program that imports it.
//
//     line:col|KIND|value            (value escaped by esc())
//     line:col|REGEX|pattern|flags   (regex literals carry two fields)
//     line:col|LEXERR|char           (a rejected source reports only this)

import lexer.f

initLexer()

blob source = argv[1]
arr[Tok] toks = tokenize(source, 0, source.length)
text out = ''

// A failed lex reports ONLY where it failed. The Python lexer raises a
// CompileError and produces no token list at all, so emitting the tokens
// that happened to precede the bad character would be a difference in the
// harness rather than in the lexers.
int errAt = 0 - 1
int e = 0
while e < toks.length {
    if toks[e].kind == 'LEXERR' { errAt = e break }
    e++
}
if errAt >= 0 {
    Tok bad = toks[errAt]
    log(`${bad.line}:${bad.col}|LEXERR|${esc(bad.val)}`)
    close(0)
}
int i = 0
while i < toks.length {
    Tok t = toks[i]
    if t.kind == 'REGEX' {
        out = out + `${t.line}:${t.col}|REGEX|${esc(t.val)}|${esc(t.extra)}`
    } else {
        out = out + `${t.line}:${t.col}|${t.kind}|${esc(t.val)}`
    }
    out = out + 10.toChar()
    i++
}
log(out)
