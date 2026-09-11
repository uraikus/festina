// Festina's semantic analysis, written in Festina (claude.md #280) --
// the third step of bootstrapping the compiler in its own language,
// after the lexer (#271, #272) and the parser (#273, #275).
//
// Like both of those, this is a PORT of festina/semantic.py rather than
// a redesign, validated the same way: bootstrap/semdiff.py dumps what
// each implementation resolves and diffs them over the corpus.
//
// WHAT IS BEING COMPARED. Semantic analysis has no tree to diff --
// analyze() is a checker, not an annotator, and writes nothing back
// onto the AST. bootstrap/semdump.py therefore defines the oracle as
// the resolved type of every name the program binds, ANYWHERE: globals,
// constants, functions, parameters, loop and catch variables, and
// locals nested arbitrarily deep inside bodies. That is what a type
// checker is for, and it is what this file must reproduce.
//
// WHY THERE IS NO EXPRESSION INFERENCE HERE. specification.md 10.2: "A
// declaration states its type; there is no var, let or inference." So
// every DECL record's type comes from a declared type expression, never
// from the initializer. Resolving declarations and walking scopes is
// therefore the whole job of reproducing the dump; checking that an
// initializer is ASSIGNABLE to its declaration is a separate concern
// which this file does not yet do, and which only shows up in the
// oracle as the SEMERR line of a program that should be rejected.
//
// UNPORTED. A statement kind with no implementation emits an
// `UNPORTED|what` record, and semdiff.py counts a file containing one
// separately -- never as a match, never as a difference. The rule
// matters more here than it did in the parser: most statements bind no
// names at all, so quietly ignoring an unrecognised one would look
// exactly like success.

import parser.f

// ---------------------------------------------------------------------
// Types.
//
// festina/types.py has 16 separate frozen dataclasses. As with the
// parser's nodes, one struct carrying a kind is what keeps the renderer
// generic; `typeName` below is a direct transcription of
// types.type_name, which is the ONLY thing the oracle compares, and
// which codegen already keys its generated release-function caches on.

struct Ty {
    kind:text      // 'prim' | 'arr' | 'map' | 'struct' | 'table' | 'enum'
                   // | 'func' | 'thread' | 'unknown'
    name:text      // 'int'/'text'/... for prim; the declared name otherwise
    elem:Ty        // arr element / map value / func return
    params:arr[Ty] // func parameter types
    managed:bool   // the trailing `?`
    amortized:bool
}

Ty func tyPrim(name:text) {
    Ty t
    t.kind = 'prim'
    t.name = name
    t.params = []
    return t
}

Ty func tyNamed(kind:text, name:text) {
    Ty t
    t.kind = kind
    t.name = name
    t.params = []
    return t
}

Ty func tyArr(elem:Ty, amortized:bool) {
    Ty t
    t.kind = 'arr'
    t.elem = elem
    t.amortized = amortized
    t.params = []
    return t
}

Ty func tyMap(value:Ty) {
    Ty t
    t.kind = 'map'
    t.elem = value
    t.params = []
    return t
}

// types.type_name, transcribed. Every branch that can carry
// manually_managed appends the trailing `?`, matching the surface
// syntax -- `table` and `color`/`font` deliberately do not, and `func`
// does not either, exactly as the original.
text func typeName(t:Ty) {
    if t == null { return 'unknown' }
    text mm = ''
    if t.managed { mm = '?' }
    if t.kind == 'prim' { return t.name + mm }
    if t.kind == 'struct' { return t.name + mm }
    if t.kind == 'enum' { return t.name + mm }
    if t.kind == 'table' { return t.name }
    if t.kind == 'arr' {
        text prefix = ''
        if t.amortized { prefix = 'amor ' }
        return prefix + 'arr[' + typeName(t.elem) + ']' + mm
    }
    if t.kind == 'map' { return 'map[' + typeName(t.elem) + ']' + mm }
    if t.kind == 'func' {
        text ps = ''
        int i = 0
        while i < t.params.length {
            if i > 0 { ps = ps + ',' }
            ps = ps + typeName(t.params[i])
            i++
        }
        text ret = 'void'
        if t.elem != null { ret = typeName(t.elem) }
        return 'func[' + ps + ']:' + ret
    }
    if t.kind == 'thread' {
        if t.name == '' { return 'thread' }
        return `thread '${t.name}'`
    }
    return 'unknown'
}

// A type as the DUMP spells it, which is not the same as how the
// language spells it: an absent type is `-` in a record and `unknown`
// when it appears nested inside another type. semdump.py draws the same
// distinction (its _type answers "-" for None before ever reaching
// types.type_name, whose own answer is "unknown"), and collapsing the
// two here renders `environment` wrongly on every single file.
text func dumpType(t:Ty) {
    if t == null { return '-' }
    return typeName(t)
}

// ---------------------------------------------------------------------
// Program-wide state. Global rather than threaded through every
// function: there is exactly one analysis in flight at a time, and
// Festina has no closures to capture an analyzer object with -- the
// same shape parser.f's own TOKS/POS use.

arr[text] OUT = []            // the dump records, unsorted until the end
bool SEM_FAILED = false
int SEM_LINE = 0
int SEM_COL = 0
bool HIT_UNSUPPORTED = false

map[bool] STRUCT_NAMES = {}
map[bool] TABLE_NAMES = {}
map[bool] ENUM_NAMES = {}
map[bool] THREAD_NAMES = {}

text MAIN_MSG = '-'
text BASE_DIR = ''
map[bool] IMPORTED = {}

// specification.md 6.2: an import merges the imported file's statements
// into ONE program, in dependency order, before analysis begins -- it
// is not a module system. Reproducing the dump therefore means doing
// the merge, not skipping it: without this, seven corpus files resolve
// nothing that another file declares.
//
// parseProgram drives the parser's own TOKS/POS globals, and analysis
// runs after the entry file is already parsed, so each nested parse
// saves and restores them. Missing that would leave the outer parse
// pointing into the imported file's token stream.
arr[Node] func parseImported(path:text) {
    arr[Node] none = []
    text full = BASE_DIR + path
    if IMPORTED[full] != null { return none }
    IMPORTED[full] = true
    blob b = full
    if !b.exists() { return none }
    arr[Tok] savedToks = TOKS
    int savedPos = POS
    bool savedFailed = FAILED
    arr[Tok] tk = tokenize(b, 0, b.length)
    TOKS = tk
    POS = 0
    FAILED = false
    arr[Node] body = parseProgram()
    TOKS = savedToks
    POS = savedPos
    FAILED = savedFailed
    return body
}

// An imported file's statements come BEFORE the importing file's, and
// its own imports before those again -- the dependency order
// imports.build_program produces.
arr[Node] func expandImports(stmts:arr[Node]) {
    arr[Node] out = []
    int i = 0
    while i < stmts.length {
        Node st = stmts[i]
        if st != null && st.kind == 'ImportDecl' {
            arr[Node] inner = expandImports(parseImported(rawText(st, 'path')))
            int j = 0
            while j < inner.length {
                out.push(inner[j])
                j++
            }
        } else {
            out.push(st)
        }
        i++
    }
    return out
}

// A thread's inbound message type is the second parameter of the
// `on message(worker:thread, msg:T)` handler inside its own body --
// there is nowhere else for it to be declared. The reply type comes
// from a `worker.reply(x)` call, which needs expression analysis this
// file does not do yet, so it stays `-`.
text func inboundTypeOf(body:Node) {
    arr[Node] stmts = listOf(body, 'body')
    int i = 0
    while i < stmts.length {
        Node st = stmts[i]
        if st != null && st.kind == 'EventHandler' && rawText(st, 'name') == 'message' {
            arr[Node] ps = listOf(st, 'params')
            if ps.length > 1 {
                Ty t = resolveTypeField(ps[1], 'type_expr')
                t = applyManaged(t, rawBool(ps[1], 'manually_managed'))
                return dumpType(t)
            }
        }
        i++
    }
    return '-'
}

void func semFail(line:int, col:int) {
    if SEM_FAILED { return }
    SEM_FAILED = true
    SEM_LINE = line
    SEM_COL = col
}

void func unsupported(what:text) {
    HIT_UNSUPPORTED = true
    OUT.push('UNPORTED|' + what)
}

// ---------------------------------------------------------------------
// Field access on the parser's generic nodes.

Field func fieldOf(n:Node, name:text) {
    Field none
    if n == null { return none }
    int i = 0
    while i < n.fields.length {
        if n.fields[i].name == name { return n.fields[i] }
        i++
    }
    return none
}

bool func hasField(n:Node, name:text) {
    return fieldOf(n, name).name == name
}

Node func childOf(n:Node, name:text) {
    Field f = fieldOf(n, name)
    if f.tag == 'node' { return f.node }
    return null
}

arr[Node] func listOf(n:Node, name:text) {
    Field f = fieldOf(n, name)
    arr[Node] empty = []
    if f.tag == 'list' { return f.list }
    return empty
}

// A raw field as parser.f stored it: addStr wraps in double quotes and
// escapes \\ \n \t \r and " (as \q). Reading one back has to undo
// exactly that, or a name containing a quote silently truncates.
text func rawText(n:Node, name:text) {
    Field f = fieldOf(n, name)
    if f.tag != 'raw' { return '' }
    text r = f.raw
    if r.length < 2 { return r }
    if r.charCodeAt(0) != 34 { return r }
    text out = ''
    int i = 1
    int last = r.length - 1
    while i < last {
        int c = r.charCodeAt(i)
        if c == 92 && i + 1 < last {
            int d = r.charCodeAt(i + 1)
            if d == 110 { out = out + '\n'  i = i + 2  continue }
            if d == 116 { out = out + '\t'  i = i + 2  continue }
            if d == 114 { out = out + '\r'  i = i + 2  continue }
            if d == 113 { out = out + '"'   i = i + 2  continue }
            if d == 92  { out = out + '\\'  i = i + 2  continue }
        }
        out = out + c.toChar()
        i++
    }
    return out
}

int func rawInt(n:Node, name:text) {
    Field f = fieldOf(n, name)
    if f.tag != 'raw' { return 0 }
    int v = f.raw.toInt()
    if v == null { return 0 }
    return v
}

bool func rawBool(n:Node, name:text) {
    Field f = fieldOf(n, name)
    return f.tag == 'raw' && f.raw == 'true'
}

// ---------------------------------------------------------------------
// Type resolution: a parsed type expression becomes a Ty.
//
// parser.f stores a simple type as a RAW field (a quoted name, unwrapped
// by addType) and a compound one as a NODE. Both spellings arrive here
// through the same field name, so which one it is has to be asked
// rather than assumed.

map[bool] PRIMS = {}
text PRIM_SRC = 'int float bool text blob ascii color font void regex url'

void func initSemantic() {
    int i = 0
    arr[text] names = PRIM_SRC.split(' ')
    while i < names.length {
        PRIMS[names[i]] = true
        i++
    }
}

Ty func resolveNamed(name:text) {
    if name == '' { return null }
    if STRUCT_NAMES[name] != null { return tyNamed('struct', name) }
    if TABLE_NAMES[name] != null { return tyNamed('table', name) }
    if ENUM_NAMES[name] != null { return tyNamed('enum', name) }
    if name == 'img' { return tyPrim('img') }
    if name == 'aud' { return tyPrim('aud') }
    if name == 'http' { return tyPrim('http') }
    if name == 'socket' { return tyPrim('socket') }
    if name == 'thread' { return tyNamed('thread', '') }
    if PRIMS[name] != null { return tyPrim(name) }
    return null
}

Ty func resolveTypeField(owner:Node, fieldName:text) {
    Field f = fieldOf(owner, fieldName)
    if f.tag == 'raw' { return resolveNamed(rawText(owner, fieldName)) }
    if f.tag == 'node' { return resolveTypeNode(f.node) }
    return null
}

Ty func resolveTypeNode(t:Node) {
    if t == null { return null }
    if t.kind == '#str' { return resolveNamed(rawText(t, 'v')) }
    if t.kind == 'ArrayTypeExpr' {
        Ty elem = resolveTypeField(t, 'element')
        if elem == null { return null }
        return tyArr(elem, rawBool(t, 'amortized'))
    }
    if t.kind == 'MapTypeExpr' {
        Ty value = resolveTypeField(t, 'value')
        if value == null { return null }
        return tyMap(value)
    }
    if t.kind == 'FuncTypeExpr' {
        Ty ft
        ft.kind = 'func'
        ft.name = ''
        ft.params = []
        arr[Node] ps = listOf(t, 'param_types')
        int i = 0
        while i < ps.length {
            Ty p = resolveTypeNode(ps[i])
            if p == null { return null }
            ft.params.push(p)
            i++
        }
        Field r = fieldOf(t, 'return_type')
        if r.tag == 'raw' {
            text rn = rawText(t, 'return_type')
            if rn != 'void' && rn != '' { ft.elem = resolveNamed(rn) }
        } else if r.tag == 'node' {
            ft.elem = resolveTypeNode(r.node)
        }
        return ft
    }
    return null
}

// specification.md 8.18: the trailing `?` makes a DISTINCT type. Only
// the types listed as manually manageable carry it; on int/float/bool/
// color/font/func/table it is accepted and has no effect, so setting
// the flag unconditionally would render `int?` where the original
// renders `int`.
Ty func applyManaged(t:Ty, managed:bool) {
    if t == null { return null }
    if !managed { return t }
    if t.kind == 'prim' {
        if t.name == 'int' || t.name == 'float' || t.name == 'bool' { return t }
        if t.name == 'color' || t.name == 'font' { return t }
    }
    if t.kind == 'func' || t.kind == 'table' { return t }
    Ty c = t
    c.managed = true
    return c
}

// ---------------------------------------------------------------------
// Scopes and the symbol table.
//
// Every binding in the program passes through define(), which is what
// makes the dump cover locals inside bodies rather than only globals --
// exactly the chokepoint bootstrap/semdump.py wraps on the Python side.

// `depth` is not bookkeeping for its own sake. A struct-typed field
// AUTO-VIVIFIES when read (specification.md 10.11), so `s.parent ==
// null` is never true -- reading the field manufactures a Scope. That
// silently broke the top-level test for main's own `on message`, and a
// parent-chain walk written as `while cur != null` would not terminate
// at all. An int depth is the one thing about a scope that can be
// compared without reaching through a reference.
struct Scope {
    parent:Scope
    depth:int
    names:map[bool]
}

Scope func newScope(parent:Scope) {
    Scope s
    s.parent = parent
    s.depth = 0
    s.names = {}
    return s
}

Scope func childScope(parent:Scope) {
    Scope s = newScope(parent)
    s.depth = parent.depth + 1
    return s
}

bool func known(s:Scope, name:text) {
    Scope cur = s
    int guard = cur.depth
    while guard >= 0 {
        if cur.names[name] != null { return true }
        if guard == 0 { return false }
        cur = cur.parent
        guard--
    }
    return false
}

void func define(s:Scope, name:text, t:Ty, kind:text, line:int, col:int) {
    if s.names[name] != null {
        semFail(line, col)
        return
    }
    s.names[name] = true
    OUT.push(`DECL|${line.toText()}:${col.toText()}|${name}|${kind}|${dumpType(t)}`)
}

// The builtins every program gets without declaring them. Defined with
// no declaring node, so they land at 0:0 -- which is correct and stable:
// both implementations must register the same set in the same shape.
void func defineBuiltins(g:Scope) {
    define(g, 'argv', tyArr(tyPrim('text'), false), 'variable', 0, 0)
    define(g, 'clientHeight', tyPrim('int'), 'constant', 0, 0)
    define(g, 'clientWidth', tyPrim('int'), 'constant', 0, 0)
    define(g, 'devicePixelRatio', tyPrim('float'), 'constant', 0, 0)
    define(g, 'environment', null, 'constant', 0, 0)
    define(g, 'screenHeight', tyPrim('int'), 'constant', 0, 0)
    define(g, 'screenWidth', tyPrim('int'), 'constant', 0, 0)
}

// ---------------------------------------------------------------------
// Declarations.

text func declKind(n:Node) {
    if rawBool(n, 'is_const') { return 'constant' }
    return 'variable'
}

void func analyzeVarDecl(s:Scope, n:Node) {
    Ty t = resolveTypeField(n, 'type_expr')
    t = applyManaged(t, rawBool(n, 'manually_managed'))
    define(s, rawText(n, 'name'), t, declKind(n),
           rawInt(n, 'line'), rawInt(n, 'column'))
}

// A parameter list, as FuncDecl/EventHandler/ArrowFuncExpr all carry it.
// A Param node carries no position of its own, and Python does not
// give it one either: festina/semantic.py defines a parameter with the
// DECLARATION as its err_node, so every parameter reports the line and
// column of the `func` (or handler) it belongs to.
void func defineParams(inner:Scope, ps:arr[Node], line:int, col:int) {
    int i = 0
    while i < ps.length {
        Node p = ps[i]
        Ty t = resolveTypeField(p, 'type_expr')
        t = applyManaged(t, rawBool(p, 'manually_managed'))
        define(inner, rawText(p, 'name'), t, 'parameter', line, col)
        i++
    }
}

Ty func returnTypeOf(n:Node) {
    Field r = fieldOf(n, 'return_type')
    if r.tag == 'raw' {
        text rn = rawText(n, 'return_type')
        if rn == 'void' || rn == '' { return null }
        return resolveNamed(rn)
    }
    if r.tag == 'node' { return resolveTypeNode(r.node) }
    return null
}

void func analyzeFuncDecl(s:Scope, n:Node) {
    // The function's own name is registered in the ENCLOSING scope with
    // its RETURN type, not a func[...] type -- festina/semantic.py's
    // Symbol(decl.name, return_type, "function", decl). Parameters and
    // body locals go in a fresh child scope.
    int line = rawInt(n, 'line')
    int col = rawInt(n, 'column')
    define(s, rawText(n, 'name'), returnTypeOf(n), 'function', line, col)
    Scope inner = childScope(s)
    defineParams(inner, listOf(n, 'params'), line, col)
    analyzeBlock(inner, childOf(n, 'body'))
}

void func analyzeRecordDecl(label:text, n:Node) {
    text name = rawText(n, 'name')
    arr[Node] fs = listOf(n, 'fields')
    text out = label + '|' + name + '|'
    int i = 0
    while i < fs.length {
        Node f = fs[i]
        Ty t = resolveTypeField(f, 'type_expr')
        t = applyManaged(t, rawBool(f, 'manually_managed'))
        if i > 0 { out = out + '|' }
        out = out + rawText(f, 'name') + ':' + dumpType(t)
        i++
    }
    OUT.push(out)
}

void func analyzeEnumDecl(n:Node) {
    OUT.push('ENUM|' + rawText(n, 'name') + '|' + enumMembers(n))
}

text func enumMembers(n:Node) {
    arr[Node] ms = listOf(n, 'members')
    text out = ''
    int i = 0
    while i < ms.length {
        if i > 0 { out = out + '|' }
        out = out + rawText(ms[i], 'v') + ':-'
        i++
    }
    return out
}

// ---------------------------------------------------------------------
// Statement walking.
//
// Only statements that BIND a name, or that contain a block which
// might, need handling. Anything unrecognised emits UNPORTED rather
// than being skipped: a statement that binds nothing and a statement
// this file does not know about look identical from the outside, and
// conflating them is how a port reports coverage it does not have.

void func analyzeBlock(s:Scope, b:Node) {
    if b == null { return }
    analyzeStmts(childScope(s), listOf(b, 'body'))
}

void func analyzeStmts(s:Scope, stmts:arr[Node]) {
    int i = 0
    while i < stmts.length {
        analyzeStmt(s, stmts[i])
        if SEM_FAILED { return }
        i++
    }
}

void func analyzeStmt(s:Scope, n:Node) {
    if n == null { return }
    text k = n.kind
    if k == 'VarDecl' { analyzeVarDecl(s, n)  return }
    if k == 'Block' { analyzeBlock(s, n)  return }
    if k == 'IfStmt' {
        analyzeBlock(s, childOf(n, 'then'))
        // `else if` chains hang the next IfStmt off `orelse` directly
        // rather than wrapping it in a Block, so this dispatches as a
        // statement instead of assuming a block.
        analyzeStmt(s, childOf(n, 'orelse'))
        return
    }
    if k == 'WhileStmt' { analyzeBlock(s, childOf(n, 'body'))  return }
    if k == 'ForStmt' {
        // The loop variable belongs to a scope wrapping the body, not
        // to the body's own scope: `for int i = 0, ...` must not
        // collide with an `int i` declared inside.
        Scope loop = childScope(s)
        analyzeStmt(loop, childOf(n, 'init'))
        analyzeBlock(loop, childOf(n, 'body'))
        return
    }
    if k == 'TryStmt' {
        analyzeBlock(s, childOf(n, 'try_body'))
        Scope c = childScope(s)
        text cn = rawText(n, 'catch_var')
        if cn != '' {
            define(c, cn, tyPrim('text'), 'variable',
                   rawInt(n, 'line'), rawInt(n, 'column'))
        }
        analyzeBlock(c, childOf(n, 'catch_body'))
        return
    }
    if k == 'ExprStmt' || k == 'Return' || k == 'ThrowStmt' { return }
    if k == 'FreeStmt' || k == 'DeleteStmt' { return }
    if k == 'ImportDecl' { return }
    if k == 'FuncDecl' { analyzeFuncDecl(s, n)  return }
    if k == 'StructDecl' { analyzeRecordDecl('STRUCT', n)  return }
    if k == 'TableDecl' { analyzeRecordDecl('TABLE', n)  return }
    if k == 'EventHandler' {
        if rawText(n, 'name') == 'message' && s.depth == 0 {
            arr[Node] mps = listOf(n, 'params')
            if mps.length > 1 {
                Ty mt = resolveTypeField(mps[1], 'type_expr')
                mt = applyManaged(mt, rawBool(mps[1], 'manually_managed'))
                MAIN_MSG = dumpType(mt)
            }
        }
        // A handler's parameters are its own bindings, in a scope that
        // is NOT the global one -- the same shape a function body has.
        int hl = rawInt(n, 'line')
        int hc = rawInt(n, 'column')
        Scope hs = childScope(s)
        defineParams(hs, listOf(n, 'params'), hl, hc)
        analyzeBlock(hs, childOf(n, 'body'))
        return
    }
    if k == 'BreakStmt' || k == 'ContinueStmt' { return }
    if k == 'ThreadDecl' {
        // specification.md 20.3: a thread body is ISOLATED. Its
        // handlers parent on a scope holding only function names, never
        // on the global one, so a global variable is invisible inside
        // and a local of the same name is not a redeclaration. Parenting
        // on the enclosing scope here would silently make every such
        // local collide.
        // The thread's own NAME is bound in the enclosing scope, with
        // its own specific thread type -- `thread 'pool'`, not the
        // generic `thread` a parameter gets.
        define(s, rawText(n, 'name'), tyNamed('thread', rawText(n, 'name')),
               'thread', rawInt(n, 'line'), rawInt(n, 'column'))
        Node tbody = childOf(n, 'body')
        OUT.push('THREAD|' + rawText(n, 'name') + '|in=' + inboundTypeOf(tbody)
                 + '|reply=-')
        Scope ts = newScope(null)
        analyzeStmts(ts, listOf(tbody, 'body'))
        return
    }
    if k == 'MatchStmt' {
        arr[Node] arms = listOf(n, 'arms')
        int a = 0
        while a < arms.length {
            analyzeBlock(s, arms[a].fields[1].node)
            a++
        }
        analyzeStmt(s, childOf(n, 'default'))
        return
    }
    if k == 'EnumDecl' { analyzeEnumDecl(n)  return }
    unsupported(k)
}

// ---------------------------------------------------------------------
// Entry point.

void func registerNames(stmts:arr[Node]) {
    // Struct, table, enum and thread NAMES are pre-registered in their
    // own pass before the real walk, so declaring one below its first
    // use is never an ordering error -- festina/semantic.py does the
    // same, and it is what makes a field of a type declared later
    // resolve at all.
    int i = 0
    while i < stmts.length {
        Node n = stmts[i]
        if n != null {
            if n.kind == 'StructDecl' { STRUCT_NAMES[rawText(n, 'name')] = true }
            if n.kind == 'TableDecl' { TABLE_NAMES[rawText(n, 'name')] = true }
            if n.kind == 'EnumDecl' { ENUM_NAMES[rawText(n, 'name')] = true }
            if n.kind == 'ThreadDecl' { THREAD_NAMES[rawText(n, 'name')] = true }
        }
        i++
    }
}

arr[text] func analyzeProgram(stmts:arr[Node]) {
    initSemantic()
    OUT = []
    SEM_FAILED = false
    HIT_UNSUPPORTED = false
    MAIN_MSG = '-'
    IMPORTED = {}
    arr[Node] merged = expandImports(stmts)
    registerNames(merged)
    Scope g = newScope(null)
    defineBuiltins(g)
    analyzeStmts(g, merged)
    if SEM_FAILED {
        arr[text] only = []
        only.push(`SEMERR|${SEM_LINE.toText()}|${SEM_COL.toText()}`)
        return only
    }
    OUT.push('MAIN|msg=' + MAIN_MSG + '|reply=-')
    return OUT
}

// ---------------------------------------------------------------------
// The dump has to be sorted: neither side's walk order is a language
// fact, so leaving it in walk order would make a port reproduce this
// file's pass structure rather than its conclusions. Insertion sort,
// matching parser.f's own sortFields -- a few hundred records per file.

void func sortLines(xs:arr[text]) {
    int i = 1
    while i < xs.length {
        text cur = xs[i]
        int j = i - 1
        while j >= 0 && cmpText(xs[j], cur) > 0 {
            xs[j + 1] = xs[j]
            j--
        }
        xs[j + 1] = cur
        i++
    }
}
