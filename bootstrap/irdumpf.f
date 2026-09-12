// Dumps bootstrap/codegen.f's LLVM IR in the form bootstrap/irdump.py
// emits from the Python side (decisions.md #289).
//
// The same entry-point shape as semdumpf.f: lex, check for a lex error,
// resolve the import base directory, parse, then run the stage. A
// source rejected before codegen is reported as SEMERR with a position
// and nothing else -- both implementations must reject it in the same
// place, and which stage said so is not part of the claim.

import codegen.f

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

// The analyzer runs for its rejections: a program festina/codegen.py
// never sees is one this must not emit IR for either. Its binding
// records are discarded here -- what codegen needs off the analyzer
// (structs, tables, enums, threads, the message types) it reads from
// semantic.f's own globals, exactly as CodeGen reads them off
// AnalyzedProgram.
arr[text] records = analyzeProgram(body)
if SEM_FAILED {
    log(`SEMERR|${SEM_LINE}|${SEM_COL}`)
    close(0)
}

cgProgram(body, argv[1])

if CG_UNPORTED {
    // One line per distinct reason: irdiff.py already de-duplicates and
    // joins every UNPORTED record it sees, so reporting them all turns
    // its blocker table from a first-blocker histogram into something
    // that can say how many files a construct is the ONLY thing
    // holding back.
    int w = 0
    while w < CG_WHYS.length {
        log(`UNPORTED|${CG_WHYS[w]}`)
        w++
    }
    close(0)
}

// Joined BETWEEN lines, never after the last one: log() supplies the
// final newline itself, so terminating every line here would put two
// at the end and leave the dump one phantom blank line longer than the
// module actually is. Blank lines inside the IR are meaningful, so the
// harness cannot just drop empties the way semdiff.py does.
text out = ''
int i = 0
while i < CG_IR.length {
    if i > 0 { out = out + 10.toChar() }
    out = out + CG_IR[i]
    i++
}
log(out)
