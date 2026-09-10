// Festina's lexer, written in Festina -- the first step of bootstrapping
// the compiler in its own language (claude.md #271, #272, #273).
//
// This file is the LIBRARY: token structures, the keyword tables and
// tokenize() itself, with no top-level entry point of its own, so that
// both `bootstrap/lexdump.f` (which dumps a token stream for the
// differential test) and `bootstrap/parser.f` can `import` it. Festina's
// import model is a single translation unit (claude.md #5/#6), so any
// top-level statement here would run in every importer -- which is
// exactly why the entry point moved out (claude.md #273).
//
// initLexer() has to be called once before tokenize(): the keyword
// tables are built at runtime from a space-separated string rather than
// written out as map literals, and an importer's own top-level code is
// the only place that call can live.
//

struct Tok {
    kind:text
    val:text
    extra:text
    line:int
    col:int
}

// A template segment, as a HALF-OPEN BYTE RANGE into the source blob
// rather than extracted text -- so an interpolation's own tokens can be
// produced by re-entering tokenize() on that range in place, with no
// copy and no second buffer to index into.
struct Seg {
    isExpr:bool
    start:int
    end:int
}

// ---------------------------------------------------------------------
// Character classes. The Python lexer gets these from `re`'s own \d and
// [A-Za-z_]; here they are explicit code-point tests against the byte
// blob.byteAt hands back.

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
// Escape handling, over a raw byte range. _ESCAPES in the Python lexer;
// an unknown escape resolves to the escaped character itself, which is
// what makes \q a literal 'q' rather than an error.
//
// Bytes that are not part of an escape are copied through by SLICING,
// never by rebuilding from a code point -- that is what carries a
// multi-byte UTF-8 sequence across untouched.

text func unescape(src:blob, from:int, to:int) {
    text out = ''
    int i = from
    int runStart = from
    while i < to {
        int c = src.byteAt(i)
        if c == 92 && i + 1 < to {
            if i > runStart { out = out + src.slice(runStart, i) }
            int nx = src.byteAt(i + 1)
            if nx == 110 { out = out + 10.toChar() }
            else if nx == 116 { out = out + 9.toChar() }
            else if nx == 114 { out = out + 13.toChar() }
            else { out = out + src.slice(i + 1, i + 2) }
            i = i + 2
            runStart = i
            continue
        }
        i++
    }
    if i > runStart { out = out + src.slice(runStart, i) }
    return out
}

// claude.md #272: `\0` is no longer an accepted escape -- a `text` is
// NUL-terminated and cannot hold one. festina/lexer.py rejects it with a
// CompileError; this reports the same rejection as a LEXERR at the
// string token's own start, which is the position Python reports too.
bool func hasNulEscape(src:blob, from:int, to:int) {
    int i = from
    while i < to {
        if src.byteAt(i) == 92 && i + 1 < to {
            if src.byteAt(i + 1) == 48 { return true }
            i = i + 2
            continue
        }
        i++
    }
    return false
}

// Canonical escaping for the dump format: backslash, the three control
// characters that would break the one-token-per-line shape, and the '|'
// field separator itself. Works in CODE POINTS (text's own unit), and
// re-encodes anything non-special with toChar(), which round-trips a
// valid UTF-8 token value exactly.
text func esc(s:text) {
    text out = ''
    int i = 0
    int n = s.length
    while i < n {
        int c = s.charCodeAt(i)
        if c == 92 { out = out + '\\\\' }
        else if c == 10 { out = out + '\\n' }
        else if c == 9 { out = out + '\\t' }
        else if c == 13 { out = out + '\\r' }
        else if c == 124 { out = out + '\\p' }
        else { out = out + c.toChar() }
        i++
    }
    return out
}

// ---------------------------------------------------------------------
// Template splitting -- festina/lexer.py's _split_template, as byte
// ranges. Alternating literal/expression segments, with ${...} nesting
// tracked by brace depth.
//
// Like the Python original this counts braces without knowing about
// string literals, so a '}' inside a string closes the interpolation
// early. That is a shared limitation, pinned by
// bootstrap/cases/err_brace_in_interpolation.f so it stays shared.

arr[Seg] func splitTemplate(src:blob, from:int, to:int) {
    arr[Seg] segs = []
    int i = from
    int litStart = from
    while i < to {
        int c = src.byteAt(i)
        if c == 92 && i + 1 < to {
            i = i + 2
            continue
        }
        if c == 36 && i + 1 < to && src.byteAt(i + 1) == 123 {
            Seg lit
            lit.isExpr = false
            lit.start = litStart
            lit.end = i
            segs.push(lit)
            i = i + 2
            int depth = 1
            int exprStart = i
            while i < to && depth > 0 {
                int d = src.byteAt(i)
                if d == 123 { depth++ }
                else if d == 125 {
                    depth--
                    if depth == 0 { break }
                }
                i++
            }
            Seg ex
            ex.isExpr = true
            ex.start = exprStart
            ex.end = i
            segs.push(ex)
            i++
            litStart = i
            continue
        }
        i++
    }
    Seg tail
    tail.isExpr = false
    tail.start = litStart
    tail.end = to
    segs.push(tail)
    return segs
}

// ---------------------------------------------------------------------
// Regex-literal disambiguation -- _regex_literal_may_start_here. An
// empty prevKind stands for Python's None: the start of input, and the
// start of every ${...} sub-tokenize, which is a fresh expression
// context for exactly the same reason.

// A column is a CHARACTER offset, not a byte offset. Python's lexer
// indexes str, whose unit is the code point, so `pos - line_start + 1`
// counts characters there for free; scanning bytes, it has to be counted.
//
// This is not cosmetic. The column lands in every compile error a user
// reads, and getting it wrong misplaces the caret on any line with a
// non-ASCII character before the token -- which is exactly what the two
// files that used to be unreadable here turned out to contain.
//
// UTF-8 continuation bytes are 10xxxxxx: not < 0x80, not >= 0xC0. Those
// are the bytes that do NOT start a new character.
int func colAt(src:blob, from:int, lineStart:int, pos:int) {
    int chars = 0
    int i = lineStart
    while i < pos {
        int b = src.byteAt(from + i)
        if b < 128 || b >= 192 { chars++ }
        i++
    }
    return chars + 1
}

bool func regexMayStart(prevKind:text, prevVal:text) {
    if prevKind == '' { return true }
    if EXPR_ENDING[prevKind] != null { return false }
    if prevKind == 'OP' && (prevVal == '++' || prevVal == '--') { return false }
    return true
}

// ---------------------------------------------------------------------
// The scanner proper.
//
// Tokenizes the half-open byte range [from, to) of `src`. Line and
// column are relative to `from`, not to the file: a ${...} fragment is
// re-entered here as its own range, and festina/lexer.py's own recursive
// tokenize() call sees a fresh string starting at line 1, column 1. The
// coordinates it produces for those sub-tokens are relative in exactly
// the same way, so this reproduces them rather than "fixing" them.

arr[Tok] func tokenize(src:blob, from:int, to:int) {
    arr[Tok] toks = []
    int n = to - from

    // Line starts, walked with a cursor that only ever moves forward --
    // the Python lexer binary-searches this with bisect, but every
    // position this loop asks about is non-decreasing, and Festina's `/`
    // promotes int operands to float (so there is no integer midpoint to
    // bisect with without a round trip through float).
    arr[int] lineStarts = []
    lineStarts.push(0)
    int k = 0
    while k < n {
        if src.byteAt(from + k) == 10 { lineStarts.push(k + 1) }
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
        int col = colAt(src, from, lineStarts[lineIdx], pos)

        int c0 = src.byteAt(from + pos)

        // --- regex literal, before anything else can claim the '/' ---
        if c0 == 47 && regexMayStart(prevKind, prevVal) {
            bool isComment = false
            if pos + 1 < n {
                int c1 = src.byteAt(from + pos + 1)
                if c1 == 47 || c1 == 42 { isComment = true }
            }
            if isComment == false {
                int i = pos + 1
                text pat = ''
                int runStart = i
                bool ok = true
                while i < n {
                    int c = src.byteAt(from + i)
                    if c == 47 { break }
                    if c == 10 { ok = false break }
                    if c == 92 && i + 1 < n {
                        int nx = src.byteAt(from + i + 1)
                        if i > runStart { pat = pat + src.slice(from + runStart, from + i) }
                        // \/ is JS's delimiter escape; POSIX regcomp never
                        // wants it, so it unescapes to a bare '/'.
                        if nx == 47 { pat = pat + '/' }
                        else { pat = pat + src.slice(from + i, from + i + 2) }
                        i = i + 2
                        runStart = i
                        continue
                    }
                    i++
                }
                if ok && i < n && src.byteAt(from + i) == 47 {
                    if i > runStart { pat = pat + src.slice(from + runStart, from + i) }
                    i++
                    int flagStart = i
                    while i < n && isAlpha(src.byteAt(from + i)) { i++ }
                    Tok t
                    t.kind = 'REGEX'
                    t.val = pat
                    t.extra = src.slice(from + flagStart, from + i)
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
            while pos < n && isWs(src.byteAt(from + pos)) { pos++ }
            continue
        }

        // --- comments ---
        if c0 == 47 && pos + 1 < n && src.byteAt(from + pos + 1) == 47 {
            while pos < n && src.byteAt(from + pos) != 10 { pos++ }
            continue
        }
        if c0 == 47 && pos + 1 < n && src.byteAt(from + pos + 1) == 42 {
            int i = pos + 2
            bool closed = false
            while i + 1 < n {
                if src.byteAt(from + i) == 42 && src.byteAt(from + i + 1) == 47 {
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
                int c = src.byteAt(from + i)
                if c == 92 && i + 1 < n { i = i + 2 continue }
                if c == 96 { closed = true break }
                i++
            }
            if closed {
                if hasNulEscape(src, from + pos + 1, from + i) {
                    Tok bad
                    bad.kind = 'LEXERR'
                    bad.val = ''
                    bad.extra = ''
                    bad.line = line
                    bad.col = col
                    toks.push(bad)
                    return toks
                }
                arr[Seg] segs = splitTemplate(src, from + pos + 1, from + i)
                arr[Seg] exprs = []
                arr[Seg] strs = []
                int si = 0
                while si < segs.length {
                    Seg s = segs[si]
                    if s.isExpr { exprs.push(s) }
                    else { strs.push(s) }
                    si++
                }
                if exprs.length == 0 {
                    Tok t
                    t.kind = 'STRING'
                    t.val = unescape(src, strs[0].start, strs[0].end)
                    t.extra = ''
                    t.line = line
                    t.col = col
                    toks.push(t)
                    prevKind = 'STRING'
                    prevVal = t.val
                } else {
                    Tok st
                    st.kind = 'TSTRING_START'
                    st.val = unescape(src, strs[0].start, strs[0].end)
                    st.extra = ''
                    st.line = line
                    st.col = col
                    toks.push(st)
                    int e = 0
                    while e < exprs.length {
                        arr[Tok] sub = tokenize(src, exprs[e].start, exprs[e].end)
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
                        mid.val = unescape(src, strs[e + 1].start, strs[e + 1].end)
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
                int c = src.byteAt(from + i)
                if c == 92 && i + 1 < n { i = i + 2 continue }
                if c == c0 { closed = true break }
                i++
            }
            if closed {
                if hasNulEscape(src, from + pos + 1, from + i) {
                    Tok bad
                    bad.kind = 'LEXERR'
                    bad.val = ''
                    bad.extra = ''
                    bad.line = line
                    bad.col = col
                    toks.push(bad)
                    return toks
                }
                Tok t
                t.kind = 'STRING'
                t.val = unescape(src, from + pos + 1, from + i)
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
            while i < n && isDigit(src.byteAt(from + i)) { i++ }
            bool isFloat = false
            if i + 1 < n && src.byteAt(from + i) == 46 && isDigit(src.byteAt(from + i + 1)) {
                isFloat = true
                i++
                while i < n && isDigit(src.byteAt(from + i)) { i++ }
            }
            Tok t
            t.kind = 'NUMBER'
            if isFloat {
                // A float is compared against Python's repr(), so 1.50
                // and 1.5 have to agree. Trailing zeros go, but never
                // the last digit: 127.0 stays 127.0, matching
                // repr(127.0) rather than becoming "127.".
                int end = i
                while end > pos && src.byteAt(from + end - 1) == 48 { end = end - 1 }
                if end > pos && src.byteAt(from + end - 1) == 46 { end = end + 1 }
                t.val = 'float ' + src.slice(from + pos, from + end)
            } else {
                t.val = 'int ' + `${src.slice(from + pos, from + i).toInt()}`
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
            text three = src.slice(from + pos, from + pos + 3)
            if three == '===' || three == '!==' { op = three }
        }
        if op == '' && pos + 1 < n {
            text two = src.slice(from + pos, from + pos + 2)
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
            while i < n && isIdentPart(src.byteAt(from + i)) { i++ }
            text word = src.slice(from + pos, from + i)
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
                while p < n && (src.byteAt(from + p) == 32 || src.byteAt(from + p) == 9) { p++ }
                int ps = p
                while p < n && src.byteAt(from + p) != 10 && src.byteAt(from + p) != 59 { p++ }
                // claude.md #272: text.trim() -- this used to be a
                // hand-written helper here, for want of one.
                text trimmed = src.slice(from + ps, from + p).trim()
                if trimmed != '' {
                    while lineIdx + 1 < lineStarts.length && lineStarts[lineIdx + 1] <= pos {
                        lineIdx++
                    }
                    Tok pt
                    pt.kind = 'PATH'
                    pt.val = trimmed
                    pt.extra = ''
                    pt.line = lineIdx + 1
                    pt.col = colAt(src, from, lineStarts[lineIdx], pos)
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
    eof.col = colAt(src, from, lineStarts[lineIdx], n)
    toks.push(eof)
    return toks
}

// ---------------------------------------------------------------------
// One-time setup. Called by whichever program imports this file.

void func initLexer() {
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
}
