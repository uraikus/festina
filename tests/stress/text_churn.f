// claude.md #83/#97/#102: every way a text value is produced, bound and
// discarded, thousands of times over. Text is copy-managed rather than
// refcounted, so one missed free here is one buffer per iteration and
// LeakSanitizer sees it immediately.

text func decorate(s:text) {
    return s + '!'
}

bool func sameish(a:text, b:text) {
    return a == b
}

text prefix = 'item'
map[text] byName = {}
arr[text] collected = []
int i = 0
while i < 2000 {
    // Concatenation chains: every `+` allocates, and the intermediates
    // have to be freed at the operator rather than at the binding.
    text a = prefix + `${i}` + '-' + 'tail'
    text b = decorate(a)
    text c = decorate(decorate('x'))

    // Discarded call results and template temporaries -- nothing binds
    // these, so only the call site can free them.
    log(sameish(decorate(`t${i}`), `${a}${b}${c}`))

    // Comparison consumes both operands and keeps neither.
    if sameish(a + '', b) {
        log('unreachable')
    }

    // Through a map (computed key AND computed value) and an array.
    byName[`k${i % 8}`] = decorate(a)
    collected.push(b + '')
    if collected.length > 4 {
        collected.shift()
    }

    // Text methods on computed receivers.
    text r = (a + 'z').replace('item', 'thing')
    text r2 = decorate(a).replace(/-/g, '_')
    if sameish(r, r2) {
        log('unreachable')
    }

    // Reassignment has to free the value being replaced.
    text reused = 'first'
    reused = decorate(a)
    reused = `${b}${c}`
    i = i + 1
}
log(`${collected.length} ${sameish(byName['k0'], byName['k1'])}`)

// claude.md #116: split allocates an array plus one owned piece per
// element, join allocates through the string builder -- both per
// iteration, so a missed release is one leak per pass.
int extra = 0
int j = 0
while j < 300 {
    arr[text] words = `piece ${j} of text`.split(' ')
    extra = extra + words.length
    text joined = words.join('|')
    if joined == '' { log('unreachable') }
    arr[text] rx = joined.split(/\|/g)
    extra = extra + rx.length
    j = j + 1
}

// claude.md #249: charCodeAt()/toChar() -- charCodeAt's receiver is a
// computed (call-result) text on every iteration, so a missed free
// there is one leak per pass; toChar()'s result is a fresh, minted
// one-character text every time, so a discarded one (never bound to
// anything) is the other half of the same question. Multi-byte
// codepoints (the accented characters in `decorate`'s own '!' suffix
// text plus a literal one here) and both the null-producing
// (out-of-range/invalid) and real-value paths are all exercised, on
// computed receivers throughout -- not just literals, which would
// constant-fold in Python and never touch the runtime path at all.
int k = 0
while k < 500 {
    // 'café{k}!' is always at least 6 codepoints (c-a-f-é plus one
    // digit plus '!'), so `k % 6` never reaches into a text.length
    // this language doesn't have (indexing/charCodeAt stay bounds-
    // checked at runtime regardless -- see festina_text_char_at's own
    // "test, don't fail" doc comment -- this is just about keeping
    // this loop on the REAL-VALUE path most iterations, with the
    // fixed 9999 below covering the null/out-of-range one instead).
    text word = decorate(`café${k}`)
    int cp = word.charCodeAt(k % 6)
    text back = cp.toChar()
    if back == null { log('unreachable') }
    log(back.charCodeAt(0) == cp)
    // Chained straight off a template literal and straight into
    // toChar(), consumed as a call ARGUMENT rather than bound to
    // anything -- nothing in this expression owns the intermediate
    // charCodeAt() int (nothing to free, it's not refcounted) or the
    // minted toChar() text except the log() call site itself.
    log((`x${k}`.charCodeAt(0)).toChar())
    log((word.charCodeAt(9999) == null))
    k = k + 1
}
log(extra)
