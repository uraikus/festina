// claude.md #256: the `ascii` type under churn -- a refcounted string
// whose length lives in its own header. Everything here is shaped to
// catch the two failure modes that matter: a missing release (the
// header is 16 bytes back, so a free at the wrong offset corrupts
// rather than leaks) and a release of one of the 128 immortal
// single-character singletons `s[i]` hands back without allocating.
ascii base = 'the quick brown fox jumps over the lazy dog'
int total = 0
int hits = 0

for int i = 0, i < 2000, i++ {
    // s[i] -- an immortal singleton every time, never freed, never
    // allocated. The loop drops each one on the next iteration.
    ascii ch = base[i % base.length]
    if ch == 'o' { hits = hits + 1 }
    total = total + base.charCodeAt(i % base.length)

    // Fresh, genuinely heap-allocated ascii values: a slice and a
    // concatenation, both dropped at the end of the iteration.
    ascii part = base.slice(4, 9)
    ascii joined = part + '-' + ch
    total = total + joined.length

    // Round-tripping through text exercises both conversion
    // directions, each of which allocates its own buffer.
    text asText = part.toText()
    ascii back = asText.toAscii()
    if back == part { total = total + 1 }
}

log(total)
log(hits)
log(base.length)
