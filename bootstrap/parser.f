// Festina's parser, written in Festina (claude.md #273) -- the second
// step of bootstrapping the compiler in its own language, after the
// lexer (claude.md #271, #272).
//
// Like the lexer, this is a PORT of festina/parser.py rather than a
// redesign, validated the same way: bootstrap/astdiff.py dumps both
// parsers' ASTs in one canonical form and diffs them over the corpus.
//
// COVERAGE. All 89 .f files in the repository parse to a byte-identical
// AST (claude.md #275). The one construct still unimplemented is the
// `http {...}` anonymous send, which no corpus file uses.
//
// The UNPORTED machinery that carried this file while it was partial is
// kept rather than deleted: a construct with no implementation produces
// an `(UNPORTED :what="...")` node, and astdiff.py counts a file
// containing one separately -- never as a match, never as a difference.
// That is what the grammar wants the next time it grows, and it is what
// let coverage be reported honestly at 64/89 rather than guessed at.
//
// AST REPRESENTATION. Python has ~45 node classes; rather than 45
// Festina structs plus a 45-member enum, a node here is a kind string
// and a list of named fields. That is what lets the dump be generic on
// both sides -- otherwise the dumper becomes a second parser to keep in
// sync. Fields are sorted by name at dump time, so neither side depends
// on the other's declaration order.

import lexer.f

struct Field {
    name:text
    tag:text          // 'raw' | 'node' | 'list'
    raw:text          // a pre-rendered scalar: null/true/false/1/"s"
    node:Node
    list:arr[Node]
}

struct Node {
    kind:text
    fields:arr[Field]
}

// ---------------------------------------------------------------------
// Node construction.

Node func mk(kind:text) {
    Node n
    n.kind = kind
    n.fields = []
    return n
}

void func addRaw(n:Node, name:text, raw:text) {
    Field f
    f.name = name
    f.tag = 'raw'
    f.raw = raw
    n.fields.push(f)
}

void func addNode(n:Node, name:text, v:Node) {
    Field f
    f.name = name
    f.tag = 'node'
    f.node = v
    n.fields.push(f)
}

void func addList(n:Node, name:text, v:arr[Node]) {
    Field f
    f.name = name
    f.tag = 'list'
    f.list = v
    n.fields.push(f)
}

// A `null` field. Python's dump_value renders None as `null`, and
// several nodes carry an optional child (an `else` branch, an
// initializer), so this is not rare.
void func addNull(n:Node, name:text) {
    addRaw(n, name, 'null')
}

void func addInt(n:Node, name:text, v:int) {
    addRaw(n, name, `${v}`)
}

void func addBool(n:Node, name:text, v:bool) {
    if v { addRaw(n, name, 'true') }
    else { addRaw(n, name, 'false') }
}

// A string field, escaped the way bootstrap/astdump.py's own _esc does.
void func addStr(n:Node, name:text, v:text) {
    text out = ''
    int i = 0
    while i < v.length {
        int c = v.charCodeAt(i)
        if c == 92 { out = out + '\\\\' }
        else if c == 10 { out = out + '\\n' }
        else if c == 9 { out = out + '\\t' }
        else if c == 13 { out = out + '\\r' }
        else if c == 34 { out = out + '\\q' }
        else { out = out + c.toChar() }
        i++
    }
    addRaw(n, name, '"' + out + '"')
}

// A (key, value) pair, for MapLit's own entries. Python stores those as
// tuples and dump_value renders a tuple exactly like a list, so this
// renders as `[key value]` rather than as a node of its own.
Node func mkPair(k:Node, v:Node) {
    Node p = mk('#pair')
    addNode(p, 'a', k)
    addNode(p, 'b', v)
    return p
}

// ---------------------------------------------------------------------
// Dumping.

// Festina has no text comparison operator beyond ==, so ordering is by
// code point, the same order Python's sorted() gives for the ASCII field
// names every AST node uses.
int func cmpText(a:text, b:text) {
    int i = 0
    while i < a.length && i < b.length {
        int ca = a.charCodeAt(i)
        int cb = b.charCodeAt(i)
        if ca != cb { return ca - cb }
        i++
    }
    return a.length - b.length
}

// Insertion sort: a node has at most a handful of fields, and this
// keeps the comparator explicit rather than routing a text compare
// through arr.sort's int-returning contract.
void func sortFields(fs:arr[Field]) {
    int i = 1
    while i < fs.length {
        Field cur = fs[i]
        int j = i - 1
        while j >= 0 && cmpText(fs[j].name, cur.name) > 0 {
            fs[j + 1] = fs[j]
            j = j - 1
        }
        fs[j + 1] = cur
        i++
    }
}

text func dumpPairHalf(e:Node) {
    if isStrType(e) { return e.fields[0].raw }
    return dumpNode(e)
}

text func dumpNode(n:Node) {
    if n == null { return 'null' }
    if n.kind == '#pair' {
        // Either half may be a '#str' marker rather than a real node:
        // MapLit.entries holds two nodes, but MatchStmt.arms holds a
        // plain tag STRING alongside its block.
        return '[' + dumpPairHalf(n.fields[0].node) + ' '
               + dumpPairHalf(n.fields[1].node) + ']'
    }
    sortFields(n.fields)
    if n.fields.length == 0 { return '(' + n.kind + ')' }
    text out = '(' + n.kind
    int i = 0
    while i < n.fields.length {
        Field f = n.fields[i]
        out = out + ' :' + f.name + '='
        if f.tag == 'raw' { out = out + f.raw }
        else if f.tag == 'node' { out = out + dumpNode(f.node) }
        else {
            // A list may hold '#str' markers rather than real nodes --
            // TemplateLit.parts, EnumDecl.members and
            // FuncTypeExpr.param_types are all lists of plain STRINGS in
            // Python. Unwrapping here rather than at each construction
            // site is what addType already does for a single field.
            out = out + '['
            int k = 0
            while k < f.list.length {
                if k > 0 { out = out + ' ' }
                Node e = f.list[k]
                if isStrType(e) { out = out + e.fields[0].raw }
                else { out = out + dumpNode(e) }
                k++
            }
            out = out + ']'
        }
        i++
    }
    return out + ')'
}

// ---------------------------------------------------------------------
// Token stream. Global rather than threaded through every function:
// there is exactly one parse in flight at a time, and Festina has no
// closures to capture a parser object with.

arr[Tok] TOKS = []
int POS = 0
bool FAILED = false
text FAIL_MSG = ''
int FAIL_LINE = 0
int FAIL_COL = 0

Tok func peekAt(k:int) {
    int idx = POS + k
    if idx > TOKS.length - 1 { idx = TOKS.length - 1 }
    return TOKS[idx]
}

Tok func peek() {
    return peekAt(0)
}

bool func at(kind:text) {
    return peek().kind == kind
}

bool func atOp(v:text) {
    Tok t = peek()
    return t.kind == 'OP' && t.val == v
}

// Records the first failure and stops. festina/parser.py raises; with no
// exceptions to unwind through here, every loop that could spin on a
// non-advancing position checks FAILED instead.
void func parseFail(msg:text) {
    if FAILED { return }
    FAILED = true
    FAIL_MSG = msg
    Tok t = peek()
    FAIL_LINE = t.line
    FAIL_COL = t.col
}

Tok func advance() {
    Tok t = peek()
    if POS < TOKS.length - 1 { POS++ }
    return t
}

Tok func eat(kind:text) {
    Tok t = peek()
    if t.kind != kind {
        parseFail(`expected ${kind}, found ${t.kind}`)
        return t
    }
    return advance()
}

Tok func eatOp(v:text) {
    Tok t = peek()
    if t.kind != 'OP' || t.val != v {
        parseFail(`expected '${v}', found ${t.kind}`)
        return t
    }
    return advance()
}

// Like eat('IDENT'), but a keyword is a valid member name too -- the
// `log` in `console.log`, or blob's own `.delete()`.
Tok func eatName() {
    Tok t = peek()
    if t.kind != 'IDENT' && KEYWORDS[t.kind] == null {
        parseFail(`expected a name, found ${t.kind}`)
        return t
    }
    return advance()
}

void func eatSemi() {
    if atOp(';') { advance() }
}

// A construct with no implementation yet. Sets HIT_UNPORTED as well as
// returning the marker node, because nothing here CONSUMES the construct
// -- without the flag, parseProgram's loop would spin forever on a token
// it neither parses nor advances past. (It did, exactly once, and the
// symptom was the harness reporting exit -9 on benchmarks/http/server.f
// rather than anything resembling a parse error.)
bool HIT_UNPORTED = false

Node func unported(what:text) {
    HIT_UNPORTED = true
    Node n = mk('UNPORTED')
    addStr(n, 'what', what)
    return n
}

// ---------------------------------------------------------------------
// Types.
//
// festina/parser.py's parse_type() returns EITHER a plain string (a type
// keyword or a bare struct name) or a node (arr[T]/map[T]/func[...]:R).
// Festina has no such union, so a plain name comes back as a '#str'
// marker node and addType() unwraps it into a raw field -- which is what
// makes `:type_expr="int"` and `:type_expr=(ArrayTypeExpr ...)` both
// come out right.

map[int] TYPE_KEYWORDS = {}
// Exactly festina/parser.py's TYPE_KEYWORDS -- lexer PRIMITIVE_TYPE_
// KEYWORDS plus img/aud/http/socket/thread. `void` is deliberately NOT
// in it: it is a valid RETURN type but never an ordinary variable/field/
// element type, so parseFuncDecl and parseArrowFunction special-case it
// and looksLikeDeclaration must not treat it as starting a declaration.
// `thread` IS in it -- `on message(w:thread, msg:int)` is the shape that
// caught the first version of this list out, on 15 corpus files.
text TK_SRC = 'int float bool text blob ascii img aud http socket thread'

Node func mkStr(v:text) {
    Node n = mk('#str')
    addStr(n, 'v', v)
    return n
}

bool func isStrType(t:Node) {
    return t != null && t.kind == '#str'
}

void func addType(n:Node, name:text, t:Node) {
    if isStrType(t) { addRaw(n, name, t.fields[0].raw) }
    else { addNode(n, name, t) }
}

Node func parseType() {
    if FAILED { return mkStr('') }
    Tok t = peek()
    if t.kind == 'amor' {
        advance()
        Node inner = parseType()
        // `amor arr[T]` sets the flag on the arr node itself. The field
        // was already added as false, so this rewrites it rather than
        // appending a second copy.
        if inner != null && inner.kind == 'ArrayTypeExpr' {
            int k = 0
            while k < inner.fields.length {
                if inner.fields[k].name == 'amortized' { inner.fields[k].raw = 'true' }
                k++
            }
        }
        return inner
    }
    if t.kind == 'arr' {
        advance()
        eat('LBRACK')
        Node inner = parseType()
        eat('RBRACK')
        Node n = mk('ArrayTypeExpr')
        addType(n, 'element', inner)
        addBool(n, 'amortized', false)
        return n
    }
    if t.kind == 'map' {
        advance()
        eat('LBRACK')
        Node inner = parseType()
        eat('RBRACK')
        Node n = mk('MapTypeExpr')
        addType(n, 'value', inner)
        return n
    }
    if t.kind == 'func' {
        advance()
        eat('LBRACK')
        arr[Node] ptypes = []
        while at('RBRACK') == false && FAILED == false {
            Node pt = parseType()
            ptypes.push(pt)
            if atOp(',') { advance() }
        }
        eat('RBRACK')
        eatOp(':')
        Node ret = parseType()
        Node n = mk('FuncTypeExpr')
        // param_types is a LIST whose elements may each be a plain
        // string, so the '#str' markers have to be unwrapped one by one
        // rather than by addType (which only handles a single field).
        Field f
        f.name = 'param_types'
        f.tag = 'list'
        f.list = ptypes
        n.fields.push(f)
        addType(n, 'return_type', ret)
        return n
    }
    if TYPE_KEYWORDS[t.kind] != null {
        advance()
        return mkStr(t.kind)
    }
    if t.kind == 'IDENT' {
        advance()
        return mkStr(t.val)
    }
    parseFail(`expected a type, found ${t.kind}`)
    return mkStr('')
}

arr[Node] func parseTypedParams() {
    arr[Node] params = []
    while at('RPAREN') == false && FAILED == false {
        if at('IDENT') == false {
            parseFail(`expected a parameter name, found ${peek().kind}`)
            return params
        }
        Tok nameTok = eat('IDENT')
        if atOp(':') == false {
            parseFail(`parameter '${nameTok.val}' requires a type`)
            return params
        }
        eatOp(':')
        Node ty = parseType()
        bool mm = false
        if atOp('?') { advance() mm = true }
        Node p = mk('Param')
        addStr(p, 'name', nameTok.val)
        addType(p, 'type_expr', ty)
        addBool(p, 'manually_managed', mm)
        params.push(p)
        if atOp(',') { advance() }
    }
    return params
}

arr[Node] func parseFields() {
    arr[Node] fields = []
    while at('RBRACE') == false && FAILED == false {
        Tok nameTok = eat('IDENT')
        if FAILED { return fields }
        if atOp(':') == false {
            parseFail(`field '${nameTok.val}' requires a type`)
            return fields
        }
        eatOp(':')
        Node ty = parseType()
        Node fd = mk('FieldDecl')
        addStr(fd, 'name', nameTok.val)
        addType(fd, 'type_expr', ty)
        fields.push(fd)
        if atOp(',') { advance() }
    }
    return fields
}

// ---------------------------------------------------------------------
// Expressions. Same precedence ladder as festina/parser.py, in the same
// order: assignment, ternary, ||, &&, equality, relational, additive,
// multiplicative, unary, call/member, primary.

Node func parseExpression() {
    return parseAssign()
}

Node func parseAssign() {
    Node left = parseTernary()
    if atOp('=') {
        Tok t = advance()
        Node right = parseAssign()
        Node n = mk('Assign')
        addNode(n, 'target', left)
        addStr(n, 'op', '=')
        addNode(n, 'value', right)
        addInt(n, 'line', t.line)
        addInt(n, 'column', t.col)
        return n
    }
    return left
}

Node func parseTernary() {
    Node test = parseLogicalOr()
    if atOp('?') {
        Tok t = advance()
        Node cons = parseAssign()
        eatOp(':')
        Node alt = parseAssign()
        Node n = mk('Ternary')
        addNode(n, 'test', test)
        addNode(n, 'cons', cons)
        addNode(n, 'alt', alt)
        addInt(n, 'line', t.line)
        addInt(n, 'column', t.col)
        return n
    }
    return test
}

Node func mkLogical(op:text, l:Node, r:Node) {
    Node n = mk('LogicalOp')
    addStr(n, 'op', op)
    addNode(n, 'left', l)
    addNode(n, 'right', r)
    return n
}

Node func mkBin(op:text, l:Node, r:Node, t:Tok) {
    Node n = mk('BinOp')
    addStr(n, 'op', op)
    addNode(n, 'left', l)
    addNode(n, 'right', r)
    addInt(n, 'line', t.line)
    addInt(n, 'column', t.col)
    return n
}

Node func parseLogicalOr() {
    Node left = parseLogicalAnd()
    while atOp('||') && FAILED == false {
        advance()
        Node right = parseLogicalAnd()
        left = mkLogical('||', left, right)
    }
    return left
}

Node func parseLogicalAnd() {
    Node left = parseEquality()
    while atOp('&&') && FAILED == false {
        advance()
        Node right = parseEquality()
        left = mkLogical('&&', left, right)
    }
    return left
}

Node func parseEquality() {
    Node left = parseRelational()
    while (atOp('==') || atOp('!=') || atOp('===') || atOp('!==')) && FAILED == false {
        Tok op = advance()
        if op.val == '===' || op.val == '!==' {
            parseFail(`'${op.val}' is not supported`)
            return left
        }
        Node right = parseRelational()
        left = mkBin(op.val, left, right, op)
    }
    return left
}

Node func parseRelational() {
    Node left = parseAdditive()
    while (atOp('<') || atOp('>') || atOp('<=') || atOp('>=')) && FAILED == false {
        Tok op = advance()
        Node right = parseAdditive()
        left = mkBin(op.val, left, right, op)
    }
    return left
}

Node func parseAdditive() {
    Node left = parseMultiplicative()
    while (atOp('+') || atOp('-')) && FAILED == false {
        Tok op = advance()
        Node right = parseMultiplicative()
        left = mkBin(op.val, left, right, op)
    }
    return left
}

Node func parseMultiplicative() {
    Node left = parseUnary()
    while (atOp('*') || atOp('/') || atOp('%')) && FAILED == false {
        Tok op = advance()
        Node right = parseUnary()
        left = mkBin(op.val, left, right, op)
    }
    return left
}

Node func parseUnary() {
    if atOp('!') || atOp('-') || atOp('+') {
        Tok op = advance()
        Node operand = parseUnary()
        Node n = mk('UnaryOp')
        addStr(n, 'op', op.val)
        addNode(n, 'operand', operand)
        return n
    }
    if at('typeof') {
        Tok op = advance()
        Node operand = parseUnary()
        Node n = mk('TypeofExpr')
        addNode(n, 'operand', operand)
        addInt(n, 'line', op.line)
        addInt(n, 'column', op.col)
        return n
    }
    return parseCallMember()
}

arr[Node] func parseArgs() {
    eat('LPAREN')
    arr[Node] args = []
    while at('RPAREN') == false && FAILED == false {
        args.push(parseAssign())
        if atOp(',') { advance() }
    }
    eat('RPAREN')
    return args
}

// .toStruct(T)/.toArr(T) -- the one place a call's argument is a TYPE.
Node func parseTypeArg() {
    eat('LPAREN')
    Node ty = parseType()
    eat('RPAREN')
    Node n = mk('TypeArg')
    addType(n, 'type_expr', ty)
    addInt(n, 'line', 0)
    addInt(n, 'column', 0)
    return n
}

// The prop of a non-computed Member, needed to spot toStruct/toArr.
text func memberProp(n:Node) {
    if n == null || n.kind != 'Member' { return '' }
    int i = 0
    bool computed = true
    text prop = ''
    while i < n.fields.length {
        Field f = n.fields[i]
        if f.name == 'computed' && f.tag == 'raw' && f.raw == 'false' { computed = false }
        if f.name == 'prop' && f.tag == 'raw' { prop = f.raw }
        i++
    }
    if computed { return '' }
    return prop
}

Node func parseCallMember() {
    Node node = parsePrimary()
    while FAILED == false {
        if atOp('.') {
            advance()
            Tok propTok = eatName()
            if FAILED { return node }
            Node m = mk('Member')
            addNode(m, 'obj', node)
            addStr(m, 'prop', propTok.val)
            addBool(m, 'computed', false)
            addInt(m, 'line', propTok.line)
            addInt(m, 'column', propTok.col)
            node = m
        } else if at('LBRACK') {
            eat('LBRACK')
            Node idx = parseExpression()
            eat('RBRACK')
            Node m = mk('Member')
            addNode(m, 'obj', node)
            addNode(m, 'prop', idx)
            addBool(m, 'computed', true)
            addInt(m, 'line', 0)
            addInt(m, 'column', 0)
            node = m
        } else if at('LPAREN') {
            arr[Node] args = []
            text prop = memberProp(node)
            if prop == '"toStruct"' || prop == '"toArr"' {
                args.push(parseTypeArg())
            } else {
                args = parseArgs()
            }
            Node c = mk('Call')
            addNode(c, 'callee', node)
            addList(c, 'args', args)
            addInt(c, 'line', 0)
            addInt(c, 'column', 0)
            node = c
        } else {
            break
        }
    }
    if atOp('++') || atOp('--') {
        Tok op = advance()
        Node n = mk('PostfixOp')
        addStr(n, 'op', op.val)
        addNode(n, 'operand', node)
        addInt(n, 'line', op.line)
        addInt(n, 'column', op.col)
        return n
    }
    return node
}

Node func parseTemplate() {
    Tok start = eat('TSTRING_START')
    arr[Node] parts = []
    arr[Node] exprs = []
    parts.push(mkStr(start.val))
    while FAILED == false {
        exprs.push(parseAssign())
        if at('TSTRING_MID') {
            Tok tok = advance()
            parts.push(mkStr(tok.val))
            continue
        }
        Tok tok = eat('TSTRING_END')
        parts.push(mkStr(tok.val))
        break
    }
    Node n = mk('TemplateLit')
    // parts is a list of plain STRINGS in Python, so the '#str' markers
    // are unwrapped into raw entries at dump time -- same shape as
    // FuncTypeExpr's own param_types.
    Field pf
    pf.name = 'parts'
    pf.tag = 'list'
    pf.list = parts
    n.fields.push(pf)
    addList(n, 'exprs', exprs)
    return n
}

Node func parsePrimary() {
    Tok t = peek()
    // festina/parser.py checks _starts_arrow_function first, and so must
    // this: an arrow function is reachable from ANY expression position
    // (`cbk.fn = int (x:int) => x + 1`), not just from statement start.
    // Checking only at statement start left this as a parse failure
    // rather than an honest "not ported yet".
    if startsArrowFunctionHere() { return parseArrowFunction() }
    if t.kind == 'NUMBER' {
        advance()
        Node n = mk('NumberLit')
        // The lexer hands NUMBER over as 'int 42' or 'float 1.5' -- the
        // kind matters because `1` and `1.0` are different literals.
        // split(' ') rather than a slice: `text` has no .slice() (ascii
        // and blob do, text does not -- claude.md #273).
        arr[text] numParts = t.val.split(' ')
        addRaw(n, 'value', numParts[1])
        return n
    }
    if t.kind == 'STRING' {
        advance()
        Node n = mk('StringLit')
        addStr(n, 'value', t.val)
        return n
    }
    if t.kind == 'TSTRING_START' { return parseTemplate() }
    if t.kind == 'REGEX' {
        advance()
        Node n = mk('RegexLit')
        addStr(n, 'pattern', t.val)
        addStr(n, 'flags', t.extra)
        addInt(n, 'line', t.line)
        addInt(n, 'column', t.col)
        return n
    }
    if t.kind == 'true' {
        advance()
        Node n = mk('BoolLit')
        addBool(n, 'value', true)
        return n
    }
    if t.kind == 'false' {
        advance()
        Node n = mk('BoolLit')
        addBool(n, 'value', false)
        return n
    }
    if t.kind == 'null' {
        advance()
        return mk('NullLit')
    }
    if t.kind == 'log' || t.kind == 'fail' || t.kind == 'sqlite' {
        advance()
        Node n = mk('Identifier')
        addStr(n, 'name', t.kind)
        addInt(n, 'line', t.line)
        addInt(n, 'column', t.col)
        return n
    }
    if t.kind == 'IDENT' {
        advance()
        Node n = mk('Identifier')
        addStr(n, 'name', t.val)
        addInt(n, 'line', t.line)
        addInt(n, 'column', t.col)
        return n
    }
    if t.kind == 'LPAREN' {
        advance()
        Node e = parseExpression()
        eat('RPAREN')
        return e
    }
    if t.kind == 'LBRACK' {
        advance()
        arr[Node] elems = []
        while at('RBRACK') == false && FAILED == false {
            elems.push(parseAssign())
            if atOp(',') { advance() }
        }
        eat('RBRACK')
        Node n = mk('ArrayLit')
        addList(n, 'elements', elems)
        return n
    }
    if t.kind == 'LBRACE' {
        advance()
        arr[Node] entries = []
        while at('RBRACE') == false && FAILED == false {
            Node key = parseAssign()
            Node value
            if key != null && key.kind == 'Identifier' && atOp(':') == false {
                // Shorthand: `{headers}` is `{'headers': headers}`.
                text nm = ''
                int line = 0
                int col = 0
                int q = 0
                while q < key.fields.length {
                    Field f = key.fields[q]
                    if f.name == 'name' { nm = f.raw }
                    q++
                }
                value = key
                Node k2 = mk('StringLit')
                addRaw(k2, 'value', nm)
                key = k2
            } else {
                eatOp(':')
                value = parseAssign()
            }
            entries.push(mkPair(key, value))
            if atOp(',') { advance() }
        }
        eat('RBRACE')
        Node n = mk('MapLit')
        addList(n, 'entries', entries)
        addInt(n, 'line', t.line)
        addInt(n, 'column', t.col)
        return n
    }
    parseFail(`unexpected token ${t.kind}`)
    return mk('NullLit')
}

// ---------------------------------------------------------------------
// Declaration lookahead. festina/parser.py decides between a
// declaration, a function declaration and a bare expression statement by
// scanning ahead without consuming, and these mirror that exactly.

int func typeExprEnd(i:int) {
    if i >= TOKS.length { return i }
    text k = TOKS[i].kind
    if k == 'amor' { return typeExprEnd(i + 1) }
    if k == 'arr' || k == 'map' {
        i++
        if i < TOKS.length && TOKS[i].kind == 'LBRACK' {
            i++
            i = typeExprEnd(i)
            if i < TOKS.length && TOKS[i].kind == 'RBRACK' { i++ }
        }
        return i
    }
    if k == 'func' && i + 1 < TOKS.length && TOKS[i + 1].kind == 'LBRACK' {
        i = i + 2
        while i < TOKS.length && TOKS[i].kind != 'RBRACK' {
            i = typeExprEnd(i)
            if i < TOKS.length && TOKS[i].kind == 'OP' && TOKS[i].val == ',' { i++ }
        }
        if i < TOKS.length && TOKS[i].kind == 'RBRACK' { i++ }
        if i < TOKS.length && TOKS[i].kind == 'OP' && TOKS[i].val == ':' {
            i++
            i = typeExprEnd(i)
        }
        return i
    }
    return i + 1
}

bool func startsFuncDecl() {
    int end = typeExprEnd(POS)
    if end >= TOKS.length { return false }
    return TOKS[end].kind == 'func'
}

// `Circle? c` (a manually-managed declaration) against `Circle ? c : x`
// (an ordinary ternary used as a statement). Both are IDENT OP(?) IDENT,
// so the decision is the first depth-0 `=` (declaration) or `:`
// (ternary) that follows.
bool func confirmsManuallyManaged(start:int) {
    int depth = 0
    int i = start
    int limit = TOKS.length
    if start + 2000 < limit { limit = start + 2000 }
    while i < limit {
        Tok tok = TOKS[i]
        if tok.kind == 'EOF' { return true }
        if depth == 0 && tok.kind == 'OP' && tok.val == '=' { return true }
        if depth == 0 && tok.kind == 'OP' && tok.val == ':' { return false }
        if tok.kind == 'LPAREN' || tok.kind == 'LBRACK' || tok.kind == 'LBRACE' {
            depth++
        } else if tok.kind == 'RPAREN' || tok.kind == 'RBRACK' || tok.kind == 'RBRACE' {
            depth--
            if depth < 0 { return true }
        }
        i++
    }
    return true
}

bool func looksLikeDeclaration() {
    Tok t0 = peekAt(0)
    Tok t1 = peekAt(1)
    if TYPE_KEYWORDS[t0.kind] != null { return true }
    if t0.kind == 'arr' || t0.kind == 'map' || t0.kind == 'amor' || t0.kind == 'func' {
        return true
    }
    if t0.kind == 'IDENT' && t1.kind == 'IDENT' { return true }
    if t0.kind == 'IDENT' && t1.kind == 'OP' && t1.val == '?' && peekAt(2).kind == 'IDENT' {
        return confirmsManuallyManaged(POS + 3)
    }
    return false
}

// ---------------------------------------------------------------------
// Statements.

Node func parseBlock() {
    eat('LBRACE')
    arr[Node] body = []
    while at('RBRACE') == false && FAILED == false && HIT_UNPORTED == false {
        body.push(parseStatement())
    }
    if HIT_UNPORTED == false { eat('RBRACE') }
    Node n = mk('Block')
    addList(n, 'body', body)
    return n
}

Node func parseVarDecl() {
    Tok t = peek()
    Node ty = parseType()
    bool mm = false
    if atOp('?') { advance() mm = true }
    Tok nameTok = eat('IDENT')
    Node n = mk('VarDecl')
    addType(n, 'type_expr', ty)
    addStr(n, 'name', nameTok.val)
    if atOp('=') {
        advance()
        addNode(n, 'init', parseAssign())
    } else {
        addNull(n, 'init')
    }
    addBool(n, 'is_const', false)
    addBool(n, 'manually_managed', mm)
    addInt(n, 'line', t.line)
    addInt(n, 'column', t.col)
    eatSemi()
    return n
}

Node func parseConstDecl() {
    Tok t = eat('const')
    Node ty = parseType()
    if at('IDENT') == false {
        parseFail('expected a constant name after the type')
        return mk('NullLit')
    }
    Tok nameTok = eat('IDENT')
    Node n = mk('VarDecl')
    addType(n, 'type_expr', ty)
    addStr(n, 'name', nameTok.val)
    if atOp('=') {
        advance()
        addNode(n, 'init', parseAssign())
    } else {
        addNull(n, 'init')
    }
    addBool(n, 'is_const', true)
    addBool(n, 'manually_managed', false)
    addInt(n, 'line', t.line)
    addInt(n, 'column', t.col)
    eatSemi()
    return n
}

Node func parseFuncDecl() {
    Tok t = peek()
    Node ret
    if at('void') { advance() ret = mkStr('void') }
    else { ret = parseType() }
    eat('func')
    Tok nameTok = eat('IDENT')
    eat('LPAREN')
    arr[Node] params = parseTypedParams()
    eat('RPAREN')
    Node body = parseBlock()
    Node n = mk('FuncDecl')
    addStr(n, 'name', nameTok.val)
    addType(n, 'return_type', ret)
    addList(n, 'params', params)
    addNode(n, 'body', body)
    addInt(n, 'line', t.line)
    addInt(n, 'column', t.col)
    return n
}

Node func parseRecordDecl(kw:text, kind:text) {
    Tok t = eat(kw)
    Tok nameTok = eat('IDENT')
    eat('LBRACE')
    arr[Node] fields = parseFields()
    eat('RBRACE')
    Node n = mk(kind)
    addStr(n, 'name', nameTok.val)
    addList(n, 'fields', fields)
    addInt(n, 'line', t.line)
    addInt(n, 'column', t.col)
    return n
}

Node func parseEnumDecl() {
    Tok t = eat('enum')
    Tok nameTok = eat('IDENT')
    eatOp('=')
    arr[Node] members = []
    members.push(parseType())
    while atOp(',') && FAILED == false {
        advance()
        members.push(parseType())
    }
    eatSemi()
    Node n = mk('EnumDecl')
    addStr(n, 'name', nameTok.val)
    // members is a list of types, so '#str' markers are unwrapped at
    // dump time exactly like FuncTypeExpr's param_types.
    Field mf
    mf.name = 'members'
    mf.tag = 'list'
    mf.list = members
    n.fields.push(mf)
    addInt(n, 'line', t.line)
    addInt(n, 'column', t.col)
    return n
}

Node func parseImport() {
    eat('import')
    Tok pathTok = eat('PATH')
    Node n = mk('ImportDecl')
    addStr(n, 'path', pathTok.val)
    addInt(n, 'line', pathTok.line)
    addInt(n, 'column', pathTok.col)
    return n
}

Node func parseIf() {
    Tok t = eat('if')
    // claude.md #274: no leading-LPAREN special case -- `(a || b) && c`
    // is an ordinary grouped expression, and eating the paren here is
    // exactly the bug that entry fixed in festina/parser.py.
    Node test = parseExpression()
    Node then = parseBlock()
    Node n = mk('IfStmt')
    addNode(n, 'test', test)
    addNode(n, 'then', then)
    if at('else') {
        advance()
        if at('if') { addNode(n, 'orelse', parseIf()) }
        else { addNode(n, 'orelse', parseBlock()) }
    } else {
        addNull(n, 'orelse')
    }
    addInt(n, 'line', t.line)
    addInt(n, 'column', t.col)
    return n
}

Node func parseWhile() {
    Tok t = eat('while')
    Node test = parseExpression()
    Node body = parseBlock()
    Node n = mk('WhileStmt')
    addNode(n, 'test', test)
    addNode(n, 'body', body)
    addInt(n, 'line', t.line)
    addInt(n, 'column', t.col)
    return n
}

Node func parseFor() {
    Tok t = eat('for')
    Node init = parseVarDecl()
    eatOp(',')
    Node test = parseExpression()
    eatOp(',')
    Node update = parseExpression()
    Node body = parseBlock()
    Node n = mk('ForStmt')
    addNode(n, 'init', init)
    addNode(n, 'test', test)
    addNode(n, 'update', update)
    addNode(n, 'body', body)
    addInt(n, 'line', t.line)
    addInt(n, 'column', t.col)
    return n
}

Node func parseTry() {
    Tok t = eat('try')
    Node tryBody = parseBlock()
    eat('catch')
    eat('LPAREN')
    Tok nameTok = eat('IDENT')
    eatOp(':')
    if at('text') == false {
        parseFail(`catch's variable is always text, found ${peek().kind}`)
        return mk('NullLit')
    }
    eat('text')
    eat('RPAREN')
    Node catchBody = parseBlock()
    Node n = mk('TryStmt')
    addNode(n, 'try_body', tryBody)
    addStr(n, 'catch_var', nameTok.val)
    addNode(n, 'catch_body', catchBody)
    addInt(n, 'line', t.line)
    addInt(n, 'column', t.col)
    return n
}

Node func parseThrow() {
    Tok t = eat('throw')
    Node value = parseExpression()
    eatSemi()
    Node n = mk('ThrowStmt')
    addNode(n, 'expr', value)
    addInt(n, 'line', t.line)
    addInt(n, 'column', t.col)
    return n
}

Node func parseReturn() {
    Tok t = eat('return')
    Node n = mk('Return')
    if at('RBRACE') == false && atOp(';') == false && at('EOF') == false {
        addNode(n, 'value', parseExpression())
    } else {
        addNull(n, 'value')
    }
    eatSemi()
    addInt(n, 'line', t.line)
    addInt(n, 'column', t.col)
    return n
}

Node func parseSimpleKeywordStmt(kw:text, kind:text) {
    Tok t = eat(kw)
    eatSemi()
    Node n = mk(kind)
    addInt(n, 'line', t.line)
    addInt(n, 'column', t.col)
    return n
}

Node func parseExprStmt() {
    Node e = parseExpression()
    eatSemi()
    Node n = mk('ExprStmt')
    addNode(n, 'expr', e)
    return n
}

// claude.md #273: `on NAME(params) { }` -- plus the one-line
// `on request use NAME` shorthand, which desugars HERE, at parse time,
// into an ordinary EventHandler whose body calls NAME.giveRequest(req).
// Everything downstream stays unaware the sugar exists, which is
// exactly why it has to be reproduced rather than skipped: the dumped
// AST of the sugared form must equal the dumped AST of the longhand.
Node func parseEventHandler() {
    Tok t = eat('on')
    Tok nameTok = eat('IDENT')

    if nameTok.val == 'request' && at('IDENT') && peek().val == 'use' {
        advance()
        Tok targetTok = eat('IDENT')

        Node param = mk('Param')
        addStr(param, 'name', 'req')
        addRaw(param, 'type_expr', '"http"')
        addBool(param, 'manually_managed', true)

        Node target = mk('Identifier')
        addStr(target, 'name', targetTok.val)
        addInt(target, 'line', targetTok.line)
        addInt(target, 'column', targetTok.col)

        Node callee = mk('Member')
        addNode(callee, 'obj', target)
        addStr(callee, 'prop', 'giveRequest')
        addBool(callee, 'computed', false)
        addInt(callee, 'line', targetTok.line)
        addInt(callee, 'column', targetTok.col)

        Node reqRef = mk('Identifier')
        addStr(reqRef, 'name', 'req')
        addInt(reqRef, 'line', targetTok.line)
        addInt(reqRef, 'column', targetTok.col)
        arr[Node] callArgs = []
        callArgs.push(reqRef)

        Node call = mk('Call')
        addNode(call, 'callee', callee)
        addList(call, 'args', callArgs)
        addInt(call, 'line', targetTok.line)
        addInt(call, 'column', targetTok.col)

        Node give = mk('ExprStmt')
        addNode(give, 'expr', call)
        arr[Node] stmts = []
        stmts.push(give)
        Node block = mk('Block')
        addList(block, 'body', stmts)

        arr[Node] params = []
        params.push(param)
        Node n = mk('EventHandler')
        addStr(n, 'name', 'request')
        addList(n, 'params', params)
        addNode(n, 'body', block)
        addInt(n, 'line', t.line)
        addInt(n, 'column', t.col)
        return n
    }

    eat('LPAREN')
    arr[Node] params = parseTypedParams()
    eat('RPAREN')
    Node body = parseBlock()
    Node n = mk('EventHandler')
    addStr(n, 'name', nameTok.val)
    addList(n, 'params', params)
    addNode(n, 'body', body)
    addInt(n, 'line', t.line)
    addInt(n, 'column', t.col)
    return n
}

// `thread NAME { }`, `thread NAME[N] { }`, `thread NAME[] { }`. No
// signature of its own -- the body is an ordinary block, and the nested
// on load/on message/on exit handlers parse for free through
// parseStatement, the same reuse parseFuncDecl's body already gets.
Node func parseThreadDecl() {
    Tok t = eat('thread')
    Tok nameTok = eat('IDENT')
    text poolSize = 'null'
    if at('LBRACK') {
        advance()
        if at('RBRACK') {
            // An explicitly empty bracket pair asks the compiler to size
            // the pool itself; "auto" is the sentinel semantic.py
            // resolves before any real analysis runs.
            poolSize = '"auto"'
        } else {
            Tok sizeTok = eat('NUMBER')
            arr[text] parts = sizeTok.val.split(' ')
            if parts[0] != 'int' {
                parseFail('a thread pool size must be a plain integer literal')
                return mk('NullLit')
            }
            if parts[1].toInt() <= 0 {
                parseFail('a thread pool size must be a positive integer')
                return mk('NullLit')
            }
            poolSize = parts[1]
        }
        eat('RBRACK')
    }
    Node body = parseBlock()
    Node n = mk('ThreadDecl')
    addStr(n, 'name', nameTok.val)
    addNode(n, 'body', body)
    addInt(n, 'line', t.line)
    addInt(n, 'column', t.col)
    addRaw(n, 'pool_size', poolSize)
    return n
}

// `match EXPR { 'Tag' { } ... default { } }`. Purely structural here --
// no knowledge of which tags are valid and no exhaustiveness check;
// semantic.py owns both, since they need the subject's resolved type.
// `default` is recognized by VALUE, not reserved globally.
Node func parseMatch() {
    Tok t = eat('match')
    Node subject = parseExpression()
    eat('LBRACE')
    arr[Node] arms = []
    Node dflt
    bool haveDefault = false
    while at('RBRACE') == false && FAILED == false {
        if at('IDENT') && peek().val == 'default' {
            if haveDefault {
                parseFail("match already has a 'default' case")
                return mk('NullLit')
            }
            advance()
            dflt = parseBlock()
            haveDefault = true
            continue
        }
        Tok tagTok = eat('STRING')
        if FAILED { return mk('NullLit') }
        Node body = parseBlock()
        arms.push(mkPair(mkStr(tagTok.val), body))
    }
    eat('RBRACE')
    Node n = mk('MatchStmt')
    addNode(n, 'subject', subject)
    addList(n, 'arms', arms)
    if haveDefault { addNode(n, 'default', dflt) }
    else { addNull(n, 'default') }
    addInt(n, 'line', t.line)
    addInt(n, 'column', t.col)
    return n
}

// `<returnType> (params) => expr`, once startsArrowFunction has
// confirmed the shape. `void` is special-cased rather than routed
// through parseType -- it is a valid RETURN type but never a valid
// variable/field/element type, the same asymmetry parseFuncDecl carries.
Node func parseArrowFunction() {
    Tok t = peek()
    Node ret
    if at('void') { advance() ret = mkStr('void') }
    else { ret = parseType() }
    eat('LPAREN')
    arr[Node] params = parseTypedParams()
    eat('RPAREN')
    eatOp('=>')
    Node body = parseAssign()
    Node n = mk('ArrowFuncExpr')
    addList(n, 'params', params)
    addType(n, 'return_type', ret)
    addNode(n, 'body', body)
    // `decl` is assigned None in ArrowFuncExpr.__init__'s BODY rather
    // than taken as a parameter (semantic.py fills it in later with a
    // synthesized FuncDecl), so it does not show up in the constructor
    // signature -- but it is a real field and the dump carries it.
    addNull(n, 'decl')
    addInt(n, 'line', t.line)
    addInt(n, 'column', t.col)
    return n
}

Node func parseStatement() {
    Tok t = peek()
    text k = t.kind

    if k == 'var' || k == 'let' {
        parseFail(`'${k}' is not part of Festina`)
        return mk('NullLit')
    }
    if k == 'throw' { return parseThrow() }
    if k == 'try' { return parseTry() }
    if k == 'import' { return parseImport() }
    if k == 'const' { return parseConstDecl() }
    if k == 'struct' { return parseRecordDecl('struct', 'StructDecl') }
    if k == 'table' { return parseRecordDecl('table', 'TableDecl') }
    if k == 'enum' { return parseEnumDecl() }
    if k == 'if' { return parseIf() }
    if k == 'while' { return parseWhile() }
    if k == 'for' { return parseFor() }
    if k == 'return' { return parseReturn() }
    if k == 'break' { return parseSimpleKeywordStmt('break', 'BreakStmt') }
    if k == 'continue' { return parseSimpleKeywordStmt('continue', 'ContinueStmt') }

    // decisions.md #283: `clear` is `free` with zeroing=true -- ONE
    // node on the Python side too, so the dump carries the flag and
    // both spellings have to set it.
    if k == 'free' || k == 'clear' {
        Tok freeTok = advance()
        Tok nameTok = eat('IDENT')
        Node n = mk('FreeStmt')
        addStr(n, 'name', nameTok.val)
        addInt(n, 'line', freeTok.line)
        addInt(n, 'column', freeTok.col)
        addBool(n, 'zeroing', k == 'clear')
        return n
    }
    if k == 'delete' {
        Tok delTok = advance()
        Node target = parseCallMember()
        if target == null || target.kind != 'Member' {
            parseFail('delete removes a map key or nulls a struct/row field')
            return mk('NullLit')
        }
        Node n = mk('DeleteStmt')
        addNode(n, 'target', target)
        addInt(n, 'line', delTok.line)
        addInt(n, 'column', delTok.col)
        return n
    }

    if k == 'thread' { return parseThreadDecl() }
    if k == 'on' { return parseEventHandler() }
    if k == 'match' { return parseMatch() }

    // Not ported yet -- each announces itself rather than being
    // mis-parsed. See this file's own header for the list.
    if k == 'http' && peekAt(1).kind == 'LBRACE' { return unported('http-anon') }

    if k == 'func' && peekAt(1).kind != 'LBRACK' {
        parseFail('functions require an explicit return type')
        return mk('NullLit')
    }
    if startsFuncDecl() { return parseFuncDecl() }

    bool mmDecl = peekAt(1).kind == 'OP' && peekAt(1).val == '?' && peekAt(2).kind == 'IDENT'
    if (k == 'blob' || k == 'img' || k == 'aud') && peekAt(1).kind != 'IDENT' && mmDecl == false {
        return unported('anon-callback')
    }
    if k == 'LBRACE' { return parseBlock() }
    if looksLikeDeclaration() { return parseVarDecl() }

    return parseExprStmt()
}

// A cheap, conservative version of festina/parser.py's own
// _starts_arrow_function: enough to NOTICE one so it can be reported
// unported, not enough to parse it.
bool func startsArrowFunctionHere() {
    int end = typeExprEnd(POS)
    if end >= TOKS.length { return false }
    if TOKS[end].kind != 'LPAREN' { return false }
    int depth = 0
    int i = end
    while i < TOKS.length {
        text k = TOKS[i].kind
        if k == 'LPAREN' { depth++ }
        else if k == 'RPAREN' {
            depth--
            if depth == 0 {
                if i + 1 < TOKS.length && TOKS[i + 1].kind == 'OP' && TOKS[i + 1].val == '=>' {
                    return true
                }
                return false
            }
        }
        i++
    }
    return false
}

arr[Node] func parseProgram() {
    arr[Node] body = []
    while at('EOF') == false && FAILED == false && HIT_UNPORTED == false {
        body.push(parseStatement())
    }
    return body
}
