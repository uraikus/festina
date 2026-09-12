// Dumps escape analysis in the canonical form bootstrap/escdump.py
// emits from the Python side (decisions.md #299).
//
// The same entry-point shape as semdumpf.f and irdumpf.f: lex, check
// for a lex error, resolve the import base directory, parse, merge
// imports once, run the analyzer for its rejections, then produce the
// records.
//
// **The ORDER of the records is part of the claim.** claude.md #74
// stage 2 exempts a call argument only when the callee has already been
// analysed, so a body walked too early sees fewer exemptions and
// reports more escaping names. Measured against the real compiler
// rather than assumed: every FuncDecl and EventHandler in source order,
// then the top-level statement list.

import escape.f

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

arr[Node] merged = expandImports(body)
if SEM_FAILED {
    log(`SEMERR|${SEM_LINE}|${SEM_COL}`)
    close(0)
}

arr[text] records = analyzeProgram(merged)
if SEM_FAILED {
    log(`SEMERR|${SEM_LINE}|${SEM_COL}`)
    close(0)
}

// ---------------------------------------------------------------------

// `EOUT`, not `OUT`: semantic.f already exports one, and this file
// imports it. Two globals of the same name is a hard error rather than
// a silent shadow, which is the good case -- see decisions.md #298 for
// what the silent one cost.
arr[text] EOUT = []
int SEQ = 0
bool UNPORTED = false
text WHYNOT = ''

// A set of names as one sorted, comma-joined field. sortLines is
// semantic.f's own code-point sort, which is what Python's sorted()
// gives for the ASCII identifiers a Festina program can contain.
text func joinNames(names:map[int]) {
    arr[text] ks = names.keys()
    sortLines(ks)
    text out = ''
    int i = 0
    while i < ks.length {
        if i > 0 { out = out + ',' }
        out = out + ks[i]
        i++
    }
    return out
}

void func addRecord(kind:text, name:text, names:map[int]) {
    EOUT.push(`SEQ|${SEQ}|${kind}|${name}|${joinNames(names)}`)
    SEQ++
}

// An arrow function is emitted where its expression is reached, so its
// own analysis lands AFTER the enclosing body's rather than in source
// order -- measured: a top-level `func[int]:void p = void (v:int) =>
// ...` produces its record after the TOPLEVEL one, not before. That
// interleaving, and the synthesized `__festina_arrow_N` name it is
// keyed by, are both real work rather than a detail, so a program
// containing one is reported unported instead of guessed at.
bool func hasArrow(n:Node) {
    if n == null { return false }
    if n.kind == 'ArrowFuncExpr' { return true }
    int i = 0
    while i < n.fields.length {
        Field f = n.fields[i]
        if f.tag == 'node' {
            if hasArrow(f.node) { return true }
        }
        if f.tag == 'list' {
            int k = 0
            while k < f.list.length {
                if hasArrow(f.list[k]) { return true }
                k++
            }
        }
        i++
    }
    return false
}

// `match` is pure sugar that festina/semantic.py DESUGARS AWAY, in
// place, into a right-nested IfStmt/TypeofExpr chain before codegen --
// and therefore before escape analysis -- ever runs. bootstrap/
// semantic.f is a checker rather than an annotator and mutates nothing,
// so a MatchStmt survives to this walk, which then skips it and misses
// every name its arms touch. Porting the desugaring is its own piece of
// work; reporting it is what keeps the difference from reading as an
// escape-analysis disagreement.
bool func hasKind(n:Node, want:text) {
    if n == null { return false }
    if n.kind == want { return true }
    int i = 0
    while i < n.fields.length {
        Field f = n.fields[i]
        if f.tag == 'node' {
            if hasKind(f.node, want) { return true }
        }
        if f.tag == 'list' {
            int k = 0
            while k < f.list.length {
                if hasKind(f.list[k], want) { return true }
                k++
            }
        }
        i++
    }
    return false
}

int ai = 0
while ai < merged.length {
    if hasArrow(merged[ai]) {
        UNPORTED = true
        WHYNOT = 'arrow function'
    }
    if hasKind(merged[ai], 'MatchStmt') {
        UNPORTED = true
        WHYNOT = 'match statement'
    }
    ai++
}

void func analyzeFunc(s:Node) {
    map[int] escSet = findEscapingNames(escBodyStmts(s))
    addRecord('FUNC', rawText(s, 'name'), escSet)
    escRegisterParams(rawText(s, 'name'), listOf(s, 'params'), escSet)
}

void func analyzeHandler(s:Node) {
    map[int] escSet = findEscapingNames(escBodyStmts(s))
    // No registration: nothing ever calls a handler by name, so it
    // never needs an entry for a later analysis to consult.
    addRecord('HANDLER', rawText(s, 'name'), escSet)
}

// The first `on NAME` in a thread body, or null.
Node func handlerNamed(stmts:arr[Node], want:text) {
    int i = 0
    while i < stmts.length {
        if stmts[i].kind == 'EventHandler' {
            if rawText(stmts[i], 'name') == want { return stmts[i] }
        }
        i++
    }
    return null
}

// A thread body, in the order festina/codegen.py's _emit_thread_decl
// emits it -- which is NOT source order, and had to be read off that
// function rather than guessed:
//
//   1. every private function, in source order;
//   2. the four HTTP-shaped handlers, in the FIXED order request,
//      upgrade, socketMessage, socketClose -- they are emitted before
//      on_load because its own registration prologue needs their
//      symbols;
//   3. on load, then on message, then on exit.
//
// A pool repeats the whole sequence once per instance (claude.md #128):
// each instance is separately compiled, so a pool of three contributes
// three copies of every record.
void func analyzeThread(t:Node) {
    arr[Node] stmts = escBodyStmts(t)
    text poolRaw = fieldOf(t, 'pool_size').raw
    int instances = 1
    if poolRaw != '' && poolRaw != 'null' {
        if poolRaw == '"auto"' {
            // An auto-sized pool is cpu_count(N) wide, so the number of
            // records would depend on the machine running the dump.
            // Nothing in the corpus uses one; refusing is what keeps
            // this harness's answer a property of the program.
            UNPORTED = true
            WHYNOT = 'auto-sized thread pool'
            return
        }
        instances = poolRaw.toInt()
    }

    int inst = 0
    while inst < instances {
        int i = 0
        while i < stmts.length {
            if stmts[i].kind == 'FuncDecl' { analyzeFunc(stmts[i]) }
            i++
        }
        Node h = handlerNamed(stmts, 'request')
        if h != null { analyzeHandler(h) }
        h = handlerNamed(stmts, 'upgrade')
        if h != null { analyzeHandler(h) }
        h = handlerNamed(stmts, 'socketMessage')
        if h != null { analyzeHandler(h) }
        h = handlerNamed(stmts, 'socketClose')
        if h != null { analyzeHandler(h) }
        h = handlerNamed(stmts, 'load')
        if h != null { analyzeHandler(h) }
        h = handlerNamed(stmts, 'message')
        if h != null { analyzeHandler(h) }
        h = handlerNamed(stmts, 'exit')
        if h != null { analyzeHandler(h) }
        inst++
    }
}

// Top-level declarations in source order, descending into each thread
// at its own position.
int fi = 0
while fi < merged.length {
    Node s = merged[fi]
    if s.kind == 'FuncDecl' { analyzeFunc(s) }
    if s.kind == 'EventHandler' { analyzeHandler(s) }
    if s.kind == 'ThreadDecl' { analyzeThread(s) }
    fi++
}

// Then the top-level statement list -- everything the generated entry
// function actually executes. The declaration-shaped statements are
// excluded exactly as festina/codegen.py's own _toplevel excludes them
// from entry_stmts; a top-level VarDecl IS included, because its
// initializer runs there.
arr[Node] entryStmts = []
int ei = 0
while ei < merged.length {
    Node s = merged[ei]
    bool skip = false
    if s.kind == 'ImportDecl' { skip = true }
    if s.kind == 'StructDecl' { skip = true }
    if s.kind == 'TableDecl' { skip = true }
    if s.kind == 'EnumDecl' { skip = true }
    if s.kind == 'FuncDecl' { skip = true }
    if s.kind == 'EventHandler' { skip = true }
    if s.kind == 'ThreadDecl' { skip = true }
    // claude.md #70: `DatabaseURL = <expr>` is syntactically an
    // ordinary assignment statement, but festina/imports.py lifts it
    // off the entry file's statement list onto the Program itself, so
    // codegen evaluates it in main's prologue before festina_db_open
    // rather than as a top-level statement. It is therefore not in
    // entry_stmts, and the name it assigns is not in the top-level
    // escaping set. imports.py enforces that it is the entry file's
    // FIRST statement; by the time the merged list exists the entry
    // file's own statements are last, so position cannot be re-checked
    // here -- and need not be, since an out-of-position one was already
    // rejected.
    if s.kind == 'ExprStmt' {
        Node inner = childOf(s, 'expr')
        if inner != null && inner.kind == 'Assign' {
            Node tgt = childOf(inner, 'target')
            if tgt != null && tgt.kind == 'Identifier' {
                if rawText(tgt, 'name') == 'DatabaseURL' { skip = true }
            }
        }
    }
    if skip == false { entryStmts.push(s) }
    ei++
}
map[int] topEsc = findEscapingNames(entryStmts)
addRecord('TOPLEVEL', '', topEsc)

if ESC_UNKNOWN {
    UNPORTED = true
    WHYNOT = `expression ${ESC_UNKNOWN_KIND}`
}

if UNPORTED {
    log(`UNPORTED|${WHYNOT}`)
    close(0)
}

text out = ''
int oi = 0
while oi < EOUT.length {
    if oi > 0 { out = out + 10.toChar() }
    out = out + EOUT[oi]
    oi++
}
log(out)
