// runtime.md phase 1: DEFLATE decompression (RFC 1951) in Festina.
//
// The first real decoder, and the one every later image path needs: a
// PNG's pixels are a zlib stream, and zlib is a two-byte header around
// this. Pure arithmetic over bytes -- no OS, no allocation beyond the
// arrays -- which is exactly the class runtime.md says is expressible
// today.
//
// Structured after zlib's own `puff` reference decoder rather than its
// production one: puff decodes a symbol by walking code lengths
// shortest-first, which needs two small arrays per table instead of a
// multi-level lookup table. Slower per symbol and far easier to read
// against RFC 1951, which is the right trade for a first
// implementation whose job is to be correct.
//
// `>>` is arithmetic in Festina (specification.md 8.11: -8 >> 1 is -4),
// so every value treated as unsigned here is kept non-negative by
// construction -- the bit reader only ever shifts a masked buffer.

// ---- the bit reader -------------------------------------------------
//
// DEFLATE is least-significant-bit-first within a byte, which is the
// opposite of how a PNG's own chunk lengths are written. Getting this
// backwards produces a stream that decodes for a while and then does
// not, so it is stated rather than implied.

arr[int] INF_IN = []
int INF_POS = 0
int INF_BITBUF = 0
int INF_BITCNT = 0

void func infReset(src:arr[int]) {
    INF_IN = src
    INF_POS = 0
    INF_BITBUF = 0
    INF_BITCNT = 0
}

// Reads n bits LSB-first. Past the end of input it feeds zero bytes
// rather than failing: a truncated stream then decodes to whatever it
// held, which inflate() detects by the output being short rather than
// by this returning an error it has no way to report.
int func infBits(n:int) {
    while INF_BITCNT < n {
        int b = 0
        if INF_POS < INF_IN.length {
            b = INF_IN[INF_POS]
            INF_POS = INF_POS + 1
        }
        INF_BITBUF = INF_BITBUF | (b << INF_BITCNT)
        INF_BITCNT = INF_BITCNT + 8
    }
    int v = INF_BITBUF & ((1 << n) - 1)
    INF_BITBUF = INF_BITBUF >> n
    INF_BITCNT = INF_BITCNT - n
    return v
}

// ---- canonical Huffman ----------------------------------------------
//
// A table is two arrays: counts[len] is how many codes have that
// length, symbols is every used symbol ordered by (length, symbol).
// That pair is all a canonical code needs -- the codes themselves are
// implied by the ordering and never stored.

arr[int] INF_LCOUNT = []
arr[int] INF_LSYM = []
arr[int] INF_DCOUNT = []
arr[int] INF_DSYM = []

void func infFill(a:arr[int], n:int) {
    while a.length > 0 { a.pop() }
    int i = 0
    while i < n {
        a.push(0)
        i = i + 1
    }
}

void func infBuild(lengths:arr[int], n:int, counts:arr[int], symbols:arr[int]) {
    infFill(counts, 16)
    int i = 0
    while i < n {
        counts[lengths[i]] = counts[lengths[i]] + 1
        i = i + 1
    }
    // Length 0 means "unused", not "a zero-bit code", so it never
    // enters the symbol table.
    counts[0] = 0

    arr[int] offs = []
    infFill(offs, 16)
    int len = 1
    while len < 15 {
        offs[len + 1] = offs[len] + counts[len]
        len = len + 1
    }

    infFill(symbols, n)
    i = 0
    while i < n {
        if lengths[i] != 0 {
            symbols[offs[lengths[i]]] = i
            offs[lengths[i]] = offs[lengths[i]] + 1
        }
        i = i + 1
    }
}

// Walks lengths shortest-first, accumulating one bit at a time. `first`
// is the first code of this length and `index` the first symbol of it,
// so the comparison is against a count rather than against a code
// table. Answers -1 on a code no table entry covers, which is a
// corrupt stream.
int func infDecode(counts:arr[int], symbols:arr[int]) {
    int code = 0
    int first = 0
    int index = 0
    int len = 1
    while len < 16 {
        code = code | infBits(1)
        int count = counts[len]
        if (code - first) < count {
            return symbols[index + (code - first)]
        }
        index = index + count
        first = (first + count) << 1
        code = code << 1
        len = len + 1
    }
    return 0 - 1
}

// ---- the two fixed tables (RFC 1951 3.2.6) --------------------------

void func infFixed() {
    arr[int] lit = []
    int i = 0
    while i < 144 { lit.push(8) i = i + 1 }
    while i < 256 { lit.push(9) i = i + 1 }
    while i < 280 { lit.push(7) i = i + 1 }
    while i < 288 { lit.push(8) i = i + 1 }
    infBuild(lit, 288, INF_LCOUNT, INF_LSYM)

    arr[int] dist = []
    i = 0
    while i < 30 { dist.push(5) i = i + 1 }
    infBuild(dist, 30, INF_DCOUNT, INF_DSYM)
}

// ---- the dynamic tables (RFC 1951 3.2.7) ----------------------------
//
// The code-length alphabet is itself Huffman-coded, and its lengths
// arrive in a fixed permutation chosen so trailing zeros can be
// omitted. That permutation is data, not logic.

arr[int] INF_CLORDER = [16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15]

void func infDynamic() {
    int hlit = infBits(5) + 257
    int hdist = infBits(5) + 1
    int hclen = infBits(4) + 4

    arr[int] cl = []
    infFill(cl, 19)
    int i = 0
    while i < hclen {
        cl[INF_CLORDER[i]] = infBits(3)
        i = i + 1
    }

    arr[int] clcount = []
    arr[int] clsym = []
    infBuild(cl, 19, clcount, clsym)

    // 16/17/18 repeat rather than name a length, which is what keeps a
    // long run of equal lengths from costing one code each.
    arr[int] lengths = []
    while lengths.length < (hlit + hdist) {
        int sym = infDecode(clcount, clsym)
        if sym < 16 {
            lengths.push(sym)
        } else {
            int rep = 0
            int val = 0
            if sym == 16 {
                val = lengths[lengths.length - 1]
                rep = infBits(2) + 3
            }
            if sym == 17 { rep = infBits(3) + 3 }
            if sym == 18 { rep = infBits(7) + 11 }
            int k = 0
            while k < rep {
                lengths.push(val)
                k = k + 1
            }
        }
    }

    arr[int] lit = []
    int j = 0
    while j < hlit {
        lit.push(lengths[j])
        j = j + 1
    }
    arr[int] dist = []
    j = 0
    while j < hdist {
        dist.push(lengths[hlit + j])
        j = j + 1
    }
    infBuild(lit, hlit, INF_LCOUNT, INF_LSYM)
    infBuild(dist, hdist, INF_DCOUNT, INF_DSYM)
}

// ---- length and distance codes (RFC 1951 3.2.5) ---------------------

arr[int] INF_LENBASE = [3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31, 35, 43, 51, 59, 67, 83, 99, 115, 131, 163, 195, 227, 258]
arr[int] INF_LENEXTRA = [0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 0]
arr[int] INF_DISTBASE = [1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193, 257, 385, 513, 769, 1025, 1537, 2049, 3073, 4097, 6145, 8193, 12289, 16385, 24577]
arr[int] INF_DISTEXTRA = [0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6, 7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12, 12, 13, 13]

// One Huffman-coded block, appending to `out`. A back-reference may
// overlap the bytes it is still producing -- a run of 200 zeros is a
// distance of 1 and a length of 200 -- so this copies one byte at a
// time from the output built so far rather than slicing a range.
bool func infBlock(out:arr[int]) {
    bool going = true
    while going {
        int sym = infDecode(INF_LCOUNT, INF_LSYM)
        if sym < 0 { return false }
        if sym < 256 {
            out.push(sym)
        } else {
            if sym == 256 {
                going = false
            } else {
                int li = sym - 257
                if li >= 29 { return false }
                int length = INF_LENBASE[li] + infBits(INF_LENEXTRA[li])
                int dsym = infDecode(INF_DCOUNT, INF_DSYM)
                if dsym < 0 { return false }
                if dsym >= 30 { return false }
                int distance = INF_DISTBASE[dsym] + infBits(INF_DISTEXTRA[dsym])
                if distance > out.length { return false }
                int from = out.length - distance
                int k = 0
                while k < length {
                    out.push(out[from + k])
                    k = k + 1
                }
            }
        }
    }
    return true
}

// ---- the entry point -------------------------------------------------

arr[int] func inflateRaw(src:arr[int]) {
    infReset(src)
    arr[int] out = []
    bool last = false
    while last == false {
        last = infBits(1) == 1
        int btype = infBits(2)
        if btype == 0 {
            // A stored block restarts on a byte boundary, so whatever
            // is left in the bit buffer is padding and is discarded
            // rather than rewound.
            INF_BITBUF = 0
            INF_BITCNT = 0
            int lo = 0
            int hi = 0
            if INF_POS < INF_IN.length { lo = INF_IN[INF_POS] INF_POS = INF_POS + 1 }
            if INF_POS < INF_IN.length { hi = INF_IN[INF_POS] INF_POS = INF_POS + 1 }
            int stored = lo | (hi << 8)
            // NLEN is the one's complement of LEN and adds nothing a
            // length check does not, so it is skipped rather than
            // verified.
            INF_POS = INF_POS + 2
            int k = 0
            while k < stored {
                if INF_POS < INF_IN.length {
                    out.push(INF_IN[INF_POS])
                    INF_POS = INF_POS + 1
                }
                k = k + 1
            }
        } else {
            if btype == 1 { infFixed() }
            if btype == 2 { infDynamic() }
            if btype == 3 { return out }
            if infBlock(out) == false { return out }
        }
    }
    return out
}

// zlib's own wrapper (RFC 1950): a two-byte header, the DEFLATE stream,
// then an Adler-32 of the ORIGINAL data. This is what a PNG's IDAT
// holds, which is the only reason the wrapper is here at all.
arr[int] func inflateZlib(src:arr[int]) {
    arr[int] body = []
    int i = 2
    while i < src.length {
        body.push(src[i])
        i = i + 1
    }
    return inflateRaw(body)
}
