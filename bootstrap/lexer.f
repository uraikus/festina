// Festina's lexer, written in Festina -- the first step of bootstrapping
// the compiler in its own language (claude.md #271).
//
// This is a PORT of festina/lexer.py, not a redesign: it reproduces that
// file's token stream exactly, and `bootstrap/difftest.py` proves it by
// running both over every .f file in the repository and diffing. Where
// the Python lexer leans on `re` (one master pattern with named groups
// and `lastgroup`), this is a hand-written character scanner -- Festina's
// own regex is POSIX ERE with no named groups, and a scanner is what the
// `ascii` type (claude.md #256) was added for in the first place.
//
// The alternation order of festina/lexer.py's TOKEN_SPEC is load-bearing:
// Python's `re` alternation is leftmost-FIRST, not longest-match, so
// `scanOne` below tries the same kinds in the same order. Getting that
// order wrong is how `x++` becomes `+` `+` and `12.5` becomes `12` `.` `5`.
//
// Output is one token per line, in a canonical form difftest.py emits
// from the Python side too:
//     line:col|KIND|value            (value escaped by esc() below)
//     line:col|REGEX|pattern|flags   (regex literals carry two fields)
// A lexing error prints a single LEXERR line instead, so the differential
// test covers rejection as well as acceptance.

struct Tok {
    kind:text
    val:text
    extra:text
    line:int
    col:int
}

struct Seg {
    isExpr:bool
    txt:text
}

// ---------------------------------------------------------------------
// Character classes. The Python lexer gets these from `re`'s own \d and
// [A-Za-z_]; here they are explicit code-point tests against the byte
// `ascii.charCodeAt` hands back.

bool func isDigit(c:int) {
    return c >= 48 && c <= 57
}

bool func isAlpha(c:int) {
    return (c >= 65 && c <= 90) || (c >= 97 && c <= 122)
}

bool func isIdentStart(c:int) {
    return isAlpha(c) || c == 95
}

bool func isIdentPart(c:int) {
    return isIdentStart(c) || isDigit(c)
}

bool func isWs(c:int) {
    return c == 32 || c == 9 || c == 13 || c == 10
}

// ---------------------------------------------------------------------
// Keyword tables, mirroring festina/lexer.py's KEYWORDS (SPEC_KEYWORDS
// plus _EXTRA_KEYWORDS) and its _EXPR_ENDING_TOKEN_TYPES.

map[int] KEYWORDS = {}
map[int] EXPR_ENDING = {}

text KW_SRC = 'int float bool text blob arr struct table img aud null true false void func const import if else on fail log sqlite for while map amor break continue http socket try catch enum typeof thread match ascii return var let throw free delete'
text EE_SRC = 'IDENT NUMBER STRING TSTRING_END RPAREN RBRACK true false null log fail sqlite'

// ---------------------------------------------------------------------
// Escape handling. _ESCAPES in the Python lexer; an unknown escape
// resolves to the escaped character itself, which is what makes \q a
// literal 'q' rather than an error.

text func unescape(a:ascii) {
    text out = ''
    int i = 0
    int n = a.length
    while i < n {
        int c = a.charCodeAt(i)
        if c == 92 && i + 1 < n {
            int nx = a.charCodeAt(i + 1)
            if nx == 110 { out = out + 10.toChar() }
            else if nx == 116 { out = out + 9.toChar() }
            else if nx == 114 { out = out + 13.toChar() }
            else if nx == 48 { out = out + 0.toChar() }
            else { out = out + nx.toChar() }
            i = i + 2
            continue
        }
        out = out + c.toChar()
        i++
    }
    return out
}

// Python's str.strip(), which `text` has no equivalent of -- the import
// path is stripped for the token's value even though `pos` still
// advances past the whitespace that was removed.
text func trim(s:text) {
    ascii a = s.toAscii()
    if a == null { return s }
    int start = 0
    int end = a.length
    while start < end && isWs(a.charCodeAt(start)) { start++ }
    while end > start && isWs(a.charCodeAt(end - 1)) { end = end - 1 }
    return a.slice(start, end).toText()
}

// Canonical escaping for the dump format: backslash, the three control
// characters that would break the one-token-per-line shape, and the '|'
// field separator itself.
text func esc(s:text) {
    ascii a = s.toAscii()
    if a == null { return '<<NON-ASCII>>' }
    text out = ''
    int i = 0
    int n = a.length
    while i < n {
        int c = a.charCodeAt(i)
        if c == 92 { out = out + '\\\\' }
        else if c == 10 { out = out + '\\n' }
        else if c == 9 { out = out + '\\t' }
        else if c == 13 { out = out + '\\r' }
        else if c == 124 { out = out + '\\p' }
        // A NUL can't survive being appended to a `text` at all (it is
        // NUL-terminated), so it is escaped here rather than silently
        // truncating the dump line -- see claude.md #271's note on the
        // one recorded divergence, which is exactly this.
        else if c == 0 { out = out + '\\z' }
        else { out = out + c.toChar() }
        i++
    }
    return out
}

// A float literal is compared against Python's repr(), so 1.50 and 1.5
// have to agree. Trailing zeros go, but never the last digit: 127.0
// stays 127.0, matching repr(127.0) rather than becoming "127.".
text func normalizeFloat(lex:text) {
    ascii a = lex.toAscii()
    int end = a.length
    while end > 0 {
        int c = a.charCodeAt(end - 1)
        if c == 48 { end = end - 1 }
        else { break }
    }
    // Never strip past "N." -- keep one digit after the point.
    if end > 0 && a.charCodeAt(end - 1) == 46 { end = end + 1 }
    return a.slice(0, end).toText()
}

// ---------------------------------------------------------------------
// Template splitting -- festina/lexer.py's _split_template. Alternating
// literal/expression segments, with ${...} nesting tracked by brace
// depth so `${ m['}'] }` doesn't end the interpolation early.

arr[Seg] func splitTemplate(raw:ascii) {
    arr[Seg] segs = []
    text buf = ''
    int i = 0
    int n = raw.length
    while i < n {
        int c = raw.charCodeAt(i)
        if c == 92 && i + 1 < n {
            buf = buf + raw.slice(i, i + 2).toText()
            i = i + 2
            continue
        }
        if c == 36 && i + 1 < n && raw.charCodeAt(i + 1) == 123 {
            Seg lit
            lit.isExpr = false
            lit.txt = buf
            segs.push(lit)
            buf = ''
            i = i + 2
            int depth = 1
            int start = i
            while i < n && depth > 0 {
                int d = raw.charCodeAt(i)
                if d == 123 { depth++ }
                else if d == 125 {
                    depth--
                    if depth == 0 { break }
                }
                i++
            }
            Seg ex
            ex.isExpr = true
            ex.txt = raw.slice(start, i).toText()
            segs.push(ex)
            i++
            continue
        }
        buf = buf + c.toChar()
        i++
    }
    Seg tail
    tail.isExpr = false
    tail.txt = buf
    segs.push(tail)
    return segs
}

// ---------------------------------------------------------------------
// Regex-literal disambiguation -- _regex_literal_may_start_here. An
// empty prevKind stands for Python's None: the start of input, and the
// start of every ${...} sub-tokenize, which is a fresh expression
// context for exactly the same reason.

bool func regexMayStart(prevKind:text, prevVal:text) {
    if prevKind == '' { return true }
    if EXPR_ENDING[prevKind] != null { return false }
    if prevKind == 'OP' && (prevVal == '++' || prevVal == '--') { return false }
    return true
}

// ---------------------------------------------------------------------
// The scanner proper.

arr[Tok] func tokenize(src:ascii) {
    arr[Tok] toks = []
    int n = src.length

    // Line starts, walked with a cursor that only ever moves forward --
    // the Python lexer binary-searches this with bisect, but every
    // position this loop asks about is non-decreasing, and Festina's `/`
    // promotes int operands to float (so there is no integer midpoint to
    // bisect with without a round trip through float).
    arr[int] lineStarts = []
    lineStarts.push(0)
    int k = 0
    while k < n {
        if src.charCodeAt(k) == 10 { lineStarts.push(k + 1) }
        k++
    }
    int lineIdx = 0

    int pos = 0
    text prevKind = ''
    text prevVal = ''

    while pos < n {
        // Locate `pos` first: every branch below reports the token's
        // start, and the cursor is shared across all of them.
        while lineIdx + 1 < lineStarts.length && lineStarts[lineIdx + 1] <= pos {
            lineIdx++
        }
        int line = lineIdx + 1
        int col = pos - lineStarts[lineIdx] + 1

        int c0 = src.charCodeAt(pos)

        // --- regex literal, before anything else can claim the '/' ---
        if c0 == 47 && regexMayStart(prevKind, prevVal) {
            bool isComment = false
            if pos + 1 < n {
                int c1 = src.charCodeAt(pos + 1)
                if c1 == 47 || c1 == 42 { isComment = true }
            }
            if isComment == false {
                int i = pos + 1
                text pat = ''
                bool ok = true
                while i < n {
                    int c = src.charCodeAt(i)
                    if c == 47 { break }
                    if c == 10 { ok = false break }
                    if c == 92 && i + 1 < n {
                        int nx = src.charCodeAt(i + 1)
                        // \/ is JS's delimiter escape; POSIX regcomp never
                        // wants it, so it unescapes to a bare '/'.
                        if nx == 47 { pat = pat + '/' }
                        else { pat = pat + 92.toChar() + nx.toChar() }
                        i = i + 2
                        continue
                    }
                    pat = pat + c.toChar()
                    i++
                }
                if ok && i < n && src.charCodeAt(i) == 47 {
                    i++
                    int flagStart = i
                    while i < n && isAlpha(src.charCodeAt(i)) { i++ }
                    Tok t
                    t.kind = 'REGEX'
                    t.val = pat
                    t.extra = src.slice(flagStart, i).toText()
                    t.line = line
                    t.col = col
                    toks.push(t)
                    prevKind = 'REGEX'
                    prevVal = ''
                    pos = i
                    continue
                }
                // Not a validly terminated literal -- fall through and
                // let it lex as ordinary division.
            }
        }

        // --- whitespace ---
        if isWs(c0) {
            while pos < n && isWs(src.charCodeAt(pos)) { pos++ }
            continue
        }

        // --- comments ---
        if c0 == 47 && pos + 1 < n && src.charCodeAt(pos + 1) == 47 {
            while pos < n && src.charCodeAt(pos) != 10 { pos++ }
            continue
        }
        if c0 == 47 && pos + 1 < n && src.charCodeAt(pos + 1) == 42 {
            int i = pos + 2
            bool closed = false
            while i + 1 < n {
                if src.charCodeAt(i) == 42 && src.charCodeAt(i + 1) == 47 {
                    closed = true
                    break
                }
                i++
            }
            if closed {
                pos = i + 2
                continue
            }
            // Unterminated /* -- the Python COMMENT alternative fails
            // outright, so the '/' falls through to the OP class.
        }

        // --- template ---
        if c0 == 96 {
            int i = pos + 1
            bool closed = false
            while i < n {
                int c = src.charCodeAt(i)
                if c == 92 && i + 1 < n { i = i + 2 continue }
                if c == 96 { closed = true break }
                i++
            }
            if closed {
                ascii raw = src.slice(pos + 1, i)
                arr[Seg] segs = splitTemplate(raw)
                arr[text] exprs = []
                arr[text] strs = []
                int si = 0
                while si < segs.length {
                    Seg s = segs[si]
                    if s.isExpr { exprs.push(s.txt) }
                    else { strs.push(s.txt) }
                    si++
                }
                if exprs.length == 0 {
                    Tok t
                    t.kind = 'STRING'
                    t.val = unescape(strs[0].toAscii())
                    t.extra = ''
                    t.line = line
                    t.col = col
                    toks.push(t)
                    prevKind = 'STRING'
                    prevVal = t.val
                } else {
                    Tok st
                    st.kind = 'TSTRING_START'
                    st.val = unescape(strs[0].toAscii())
                    st.extra = ''
                    st.line = line
                    st.col = col
                    toks.push(st)
                    int e = 0
                    while e < exprs.length {
                        // A fresh expression context: sub-token line and
                        // column are relative to the interpolation text,
                        // exactly as the Python lexer's own recursive
                        // tokenize() call leaves them.
                        arr[Tok] sub = tokenize(exprs[e].toAscii())
                        // An error inside the interpolation has to
                        // propagate, not be swallowed: the Python lexer's
                        // recursive tokenize() RAISES, so the whole lex
                        // fails. Dropping sub's last token (the EOF) below
                        // would silently discard a LEXERR sitting in that
                        // same position.
                        if sub.length > 0 && sub[sub.length - 1].kind == 'LEXERR' {
                            arr[Tok] errOut = []
                            errOut.push(sub[sub.length - 1])
                            return errOut
                        }
                        int q = 0
                        while q < sub.length - 1 {
                            toks.push(sub[q])
                            q++
                        }
                        Tok mid
                        if e == exprs.length - 1 { mid.kind = 'TSTRING_END' }
                        else { mid.kind = 'TSTRING_MID' }
                        mid.val = unescape(strs[e + 1].toAscii())
                        mid.extra = ''
                        mid.line = line
                        mid.col = col
                        toks.push(mid)
                        e++
                    }
                    prevKind = toks[toks.length - 1].kind
                    prevVal = toks[toks.length - 1].val
                }
                pos = i + 1
                continue
            }
        }

        // --- string ---
        if c0 == 39 || c0 == 34 {
            int i = pos + 1
            bool closed = false
            while i < n {
                int c = src.charCodeAt(i)
                if c == 92 && i + 1 < n { i = i + 2 continue }
                if c == c0 { closed = true break }
                i++
            }
            if closed {
                Tok t
                t.kind = 'STRING'
                t.val = unescape(src.slice(pos + 1, i))
                t.extra = ''
                t.line = line
                t.col = col
                toks.push(t)
                prevKind = 'STRING'
                prevVal = t.val
                pos = i + 1
                continue
            }
            // An unterminated string never matches in Python either, and
            // lands in the error path below.
        }

        // --- number ---
        if isDigit(c0) {
            int i = pos
            while i < n && isDigit(src.charCodeAt(i)) { i++ }
            bool isFloat = false
            if i + 1 < n && src.charCodeAt(i) == 46 && isDigit(src.charCodeAt(i + 1)) {
                isFloat = true
                i++
                while i < n && isDigit(src.charCodeAt(i)) { i++ }
            }
            text lex = src.slice(pos, i).toText()
            Tok t
            t.kind = 'NUMBER'
            if isFloat {
                t.val = 'float ' + normalizeFloat(lex)
            } else {
                t.val = 'int ' + `${lex.toInt()}`
            }
            t.extra = ''
            t.line = line
            t.col = col
            toks.push(t)
            prevKind = 'NUMBER'
            prevVal = t.val
            pos = i
            continue
        }

        // --- brackets ---
        text bkind = ''
        if c0 == 40 { bkind = 'LPAREN' }
        else if c0 == 41 { bkind = 'RPAREN' }
        else if c0 == 123 { bkind = 'LBRACE' }
        else if c0 == 125 { bkind = 'RBRACE' }
        else if c0 == 91 { bkind = 'LBRACK' }
        else if c0 == 93 { bkind = 'RBRACK' }
        if bkind != '' {
            Tok t
            t.kind = bkind
            t.val = c0.toChar()
            t.extra = ''
            t.line = line
            t.col = col
            toks.push(t)
            prevKind = bkind
            prevVal = t.val
            pos++
            continue
        }

        // --- operators, longest alternative first ---
        text op = ''
        if pos + 2 < n {
            text three = src.slice(pos, pos + 3).toText()
            if three == '===' || three == '!==' { op = three }
        }
        if op == '' && pos + 1 < n {
            text two = src.slice(pos, pos + 2).toText()
            if two == '==' || two == '!=' || two == '<=' || two == '>='
                    || two == '=>' || two == '&&' || two == '||'
                    || two == '++' || two == '--' {
                op = two
            }
        }
        if op == '' {
            if c0 == 43 || c0 == 45 || c0 == 42 || c0 == 47 || c0 == 37
                    || c0 == 61 || c0 == 60 || c0 == 62 || c0 == 33
                    || c0 == 63 || c0 == 58 || c0 == 46 || c0 == 44
                    || c0 == 59 {
                op = c0.toChar()
            }
        }
        if op != '' {
            Tok t
            t.kind = 'OP'
            t.val = op
            t.extra = ''
            t.line = line
            t.col = col
            toks.push(t)
            prevKind = 'OP'
            prevVal = op
            pos = pos + op.length
            continue
        }

        // --- identifiers and keywords ---
        if isIdentStart(c0) {
            int i = pos
            while i < n && isIdentPart(src.charCodeAt(i)) { i++ }
            text word = src.slice(pos, i).toText()
            pos = i

            if word == 'import' {
                Tok t
                t.kind = 'import'
                t.val = 'import'
                t.extra = ''
                t.line = line
                t.col = col
                toks.push(t)
                prevKind = 'import'
                prevVal = 'import'
                // [ \t]*([^\n;]*) -- the path is the rest of the line,
                // stripped for the token's value but NOT for how far pos
                // advances, which is the whole match including the spaces
                // the strip removed.
                int p = pos
                while p < n && (src.charCodeAt(p) == 32 || src.charCodeAt(p) == 9) { p++ }
                int ps = p
                while p < n && src.charCodeAt(p) != 10 && src.charCodeAt(p) != 59 { p++ }
                text rawPath = src.slice(ps, p).toText()
                text trimmed = trim(rawPath)
                if trimmed != '' {
                    while lineIdx + 1 < lineStarts.length && lineStarts[lineIdx + 1] <= pos {
                        lineIdx++
                    }
                    Tok pt
                    pt.kind = 'PATH'
                    pt.val = trimmed
                    pt.extra = ''
                    pt.line = lineIdx + 1
                    pt.col = pos - lineStarts[lineIdx] + 1
                    toks.push(pt)
                    prevKind = 'PATH'
                    prevVal = trimmed
                    pos = p
                }
                continue
            }

            Tok t
            if KEYWORDS[word] != null { t.kind = word }
            else { t.kind = 'IDENT' }
            t.val = word
            t.extra = ''
            t.line = line
            t.col = col
            toks.push(t)
            prevKind = t.kind
            prevVal = word
            continue
        }

        // --- nothing matched: the Python lexer raises CompileError here ---
        Tok bad
        bad.kind = 'LEXERR'
        bad.val = c0.toChar()
        bad.extra = ''
        bad.line = line
        bad.col = col
        toks.push(bad)
        return toks
    }

    while lineIdx + 1 < lineStarts.length && lineStarts[lineIdx + 1] <= n {
        lineIdx++
    }
    Tok eof
    eof.kind = 'EOF'
    eof.val = ''
    eof.extra = ''
    eof.line = lineIdx + 1
    eof.col = n - lineStarts[lineIdx] + 1
    toks.push(eof)
    return toks
}

// ---------------------------------------------------------------------
// Entry point.

int ki = 0
arr[text] kws = KW_SRC.split(' ')
while ki < kws.length {
    KEYWORDS[kws[ki]] = 1
    ki++
}
int ei = 0
arr[text] ees = EE_SRC.split(' ')
while ei < ees.length {
    EXPR_ENDING[ees[ei]] = 1
    ei++
}

blob srcFile = argv[1]
text rawText = srcFile.toText()
ascii source = rawText.toAscii()
if source == null {
    log('NONASCII')
} else {
    arr[Tok] toks = tokenize(source)
    text out = ''
    // A failed lex reports ONLY where it failed. The Python lexer raises
    // a CompileError and produces no token list at all, so emitting the
    // tokens that happened to precede the bad character would be a
    // difference in the harness rather than in the lexers.
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
}
