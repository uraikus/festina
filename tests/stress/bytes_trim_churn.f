// claude.md #272: text.trim(), blob.byteAt(i) and blob.slice(a, b).
//
// All three came out of bootstrap/lexer.f (claude.md #271). Two of them
// hand back a FRESH OWNED text -- trim() and slice() both malloc a copy
// rather than pointing into their receiver -- so both are exactly the
// shape that leaks if the caller's temporary is not released, and
// exactly the shape that double-frees if it is released twice because
// something mistook the copy for a borrow into the blob's own buffer.
//
// byteAt() answers a scalar and so cannot leak on its own, but it shares
// the receiver-releasing path with the other two, and a blob built
// inline purely to read one byte off is the case that path exists for.
//
// The loop deliberately mixes:
//   - a named blob read many times (the receiver must NOT be released)
//   - a call-result blob receiver (the receiver MUST be released)
//   - results used, discarded, concatenated, and stored in a container
//   - a non-ASCII source, so slice() is really copying multi-byte
//     sequences rather than a convenient all-ASCII special case

blob src = 'bytes_trim_churn_input.txt'
src.write('  café naïve  straße  ')

// A fresh blob handle per call -- this is the OWNING receiver case. A
// string literal is `text`, not a blob, so there is no inline-blob
// expression to write; a call returning one is the shape the
// receiver-release path actually has to handle.
blob func openSrc() {
    blob b = 'bytes_trim_churn_input.txt'
    return b
}

int iterations = 2000
int i = 0
int checksum = 0
arr[text] kept = []

while i < iterations {
    // A named receiver, read repeatedly. Releasing this one would be a
    // use-after-free on the next iteration.
    int b = src.byteAt(i % src.length)
    if b != null { checksum = checksum + b }

    // Owned copies, discarded immediately -- the leak case.
    text piece = src.slice(2, 8)
    text trimmed = src.toText().trim()

    // Owned copies, consumed by concatenation -- the temporary has to
    // be released after the concat reads it, not before.
    text joined = piece + '|' + trimmed
    checksum = checksum + joined.length

    // A call-result receiver: constructed, read, and dropped in one
    // expression, with nothing else holding a reference to it. This one
    // MUST be released; the named `src` above must not.
    int first = openSrc().byteAt(0)
    if first != null { checksum = checksum + first }
    text fresh = openSrc().slice(0, 4)
    checksum = checksum + fresh.length

    // Stored rather than dropped, every so often, so the container's
    // own release path sees these values too. Bounded so the array
    // itself is not what grows.
    if i % 250 == 0 {
        kept.push(src.slice(0, 4).trim())
    }

    // Clamped and out-of-range answers still allocate (an empty text)
    // and still have to be freed.
    text empty = src.slice(9999, 10000)
    checksum = checksum + empty.length
    if src.byteAt(0 - 1) == null { checksum++ }

    i++
}

log(`checksum=${checksum} kept=${kept.length}`)
log(`still readable: [${src.slice(2, 8)}]`)
src.delete()
