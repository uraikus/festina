// runtime.md phase 2, and phase 7 slice 6: JPEG decoding (ITU T.81) in
// Festina.
//
// What libjpeg does for us today. A JPEG is a marker stream carrying
// quantisation and Huffman tables, then entropy-coded scans of 8x8
// blocks: Huffman-decode the coefficients, scale them by the
// quantisation table, inverse-DCT each block, then undo chroma
// subsampling and convert YCbCr to RGB.
//
// Supported: baseline and extended sequential (SOF0, SOF1) and
// PROGRESSIVE (SOF2, with successive approximation and end-of-band
// runs), 8-bit, 1 or 3 components, any h/v sampling factors, restart
// intervals, files of several scans (interleaved or one component at a
// time), and the JFIF/Adobe rules for whether three components are
// YCbCr or RGB. REFUSED, with JPG_ERR set and an empty result:
// arithmetic coding, lossless and differential frames, 12-bit, and
// CMYK (which the C loader could not convert either). Same stance as
// png.f -- a decoder that returns plausible wrong pixels is worse than
// one that declines.
//
// The picture is decoded in two stages so progressive files work: the
// scans deposit quantised coefficients into one big array, and only when
// the last scan is read is every block dequantised and transformed.
//
// The IDCT here is the separable O(n^3) form with a cosine table, not
// AAN or any of the integer approximations. Those exist because this
// one is the slow part of a decoder, and they are worth adding when
// something measures that it matters; correctness first, and this
// version can be read directly against T.81 A.3.3.

int JPG_W = 0
int JPG_H = 0
int JPG_ERR = 0

// ---- input ----------------------------------------------------------

arr[int] JPG_IN = []
int JPG_POS = 0

// Entropy-coded data is read bit by bit, MSB first, and a literal 0xFF
// inside it is written as FF 00 so it cannot be mistaken for a marker.
// Undoing that stuffing is the bit reader's job, not the caller's.
int JPG_BITBUF = 0
int JPG_BITCNT = 0
bool JPG_HITMARKER = false

void func jpgBitReset() {
    JPG_BITBUF = 0
    JPG_BITCNT = 0
    JPG_HITMARKER = false
}

int func jpgBit() {
    if JPG_BITCNT == 0 {
        if JPG_POS >= JPG_IN.length { JPG_HITMARKER = true return 0 }
        int b = JPG_IN[JPG_POS]
        JPG_POS = JPG_POS + 1
        if b == 255 {
            int nxt = 0
            if JPG_POS < JPG_IN.length { nxt = JPG_IN[JPG_POS] }
            if nxt == 0 {
                JPG_POS = JPG_POS + 1
            } else {
                // A real marker: the scan is over (or a restart, which
                // the MCU loop handles by resetting). Feed zeros rather
                // than consuming it.
                JPG_HITMARKER = true
                JPG_POS = JPG_POS - 1
                return 0
            }
        }
        JPG_BITBUF = b
        JPG_BITCNT = 8
    }
    JPG_BITCNT = JPG_BITCNT - 1
    return (JPG_BITBUF >> JPG_BITCNT) & 1
}

int func jpgReceive(n:int) {
    int v = 0
    int i = 0
    while i < n {
        v = (v << 1) | jpgBit()
        i = i + 1
    }
    return v
}

// T.81 F.2.2.1: a coefficient of size s is stored without its sign, so
// values below the midpoint are the negative half of the range.
int func jpgExtend(v:int, s:int) {
    if s == 0 { return 0 }
    if v < (1 << (s - 1)) { return v - (1 << s) + 1 }
    return v
}

// ---- tables ---------------------------------------------------------

arr[int] JPG_QUANT = []          // 4 tables x 64

// Eight Huffman tables: DC 0..3 then AC 0..3, each with the canonical
// min/max/valptr triple per code length (T.81 F.2.2.3) and its symbols
// in one shared array.
arr[int] JPG_HMIN = []           // 8 x 17
arr[int] JPG_HMAX = []
arr[int] JPG_HPTR = []
arr[int] JPG_HVAL = []
arr[int] JPG_HBASE = []          // 8: where each table's symbols start

arr[int] JPG_ZIGZAG = [0, 1, 8, 16, 9, 2, 3, 10, 17, 24, 32, 25, 18, 11, 4, 5, 12, 19, 26, 33, 40, 48, 41, 34, 27, 20, 13, 6, 7, 14, 21, 28, 35, 42, 49, 56, 57, 50, 43, 36, 29, 22, 15, 23, 30, 37, 44, 51, 58, 59, 52, 45, 38, 31, 39, 46, 53, 60, 61, 54, 47, 55, 62, 63]

void func jpgFill(a:arr[int], n:int) {
    while a.length > 0 { a.pop() }
    int i = 0
    while i < n {
        a.push(0)
        i = i + 1
    }
}

int func jpgDecodeHuff(t:int) {
    int code = jpgBit()
    int l = 1
    while l < 17 {
        int mx = JPG_HMAX[(t * 17) + l]
        if mx >= 0 && code <= mx {
            int idx = JPG_HPTR[(t * 17) + l] + code - JPG_HMIN[(t * 17) + l]
            return JPG_HVAL[JPG_HBASE[t] + idx]
        }
        code = (code << 1) | jpgBit()
        l = l + 1
    }
    return 0
}

// ---- the inverse DCT -------------------------------------------------

arr[float] JPG_COS = []

void func jpgInitCos() {
    if JPG_COS.length > 0 { return }
    int u = 0
    while u < 8 {
        int x = 0
        while x < 8 {
            float ang = ((2.0 * x.toFloat()) + 1.0) * u.toFloat() * Math.PI / 16.0
            JPG_COS.push(Math.cos(ang))
            x = x + 1
        }
        u = u + 1
    }
}

int func jpgClamp(v:int) {
    if v < 0 { return 0 }
    if v > 255 { return 255 }
    return v
}

// Separable: the 1D transform along rows, then along columns. C(0) is
// 1/sqrt(2) and every other C(u) is 1, which is the only asymmetry in
// an otherwise plain sum.
void func jpgIdct(blk:arr[float], out:arr[int], base:int, stride:int) {
    arr[float] tmp = []
    int i = 0
    while i < 64 {
        tmp.push(0.0)
        i = i + 1
    }

    int y = 0
    while y < 8 {
        int x = 0
        while x < 8 {
            float s = 0.0
            int u = 0
            while u < 8 {
                float cu = 1.0
                if u == 0 { cu = 0.70710678118654752 }
                s = s + (cu * blk[(y * 8) + u] * JPG_COS[(u * 8) + x])
                u = u + 1
            }
            tmp[(y * 8) + x] = s / 2.0
            x = x + 1
        }
        y = y + 1
    }

    int x2 = 0
    while x2 < 8 {
        int y2 = 0
        while y2 < 8 {
            float s = 0.0
            int v = 0
            while v < 8 {
                float cv = 1.0
                if v == 0 { cv = 0.70710678118654752 }
                s = s + (cv * tmp[(v * 8) + x2] * JPG_COS[(v * 8) + y2])
                v = v + 1
            }
            // +128 undoes the level shift the encoder applied (T.81
            // A.3.1); the result is a sample, so it is clamped rather
            // than allowed to wrap.
            out[base + (y2 * stride) + x2] = jpgClamp(Math.round((s / 2.0) + 128.0))
            y2 = y2 + 1
        }
        x2 = x2 + 1
    }
}

// ---- component state -------------------------------------------------

arr[int] JPG_CID = []
arr[int] JPG_CH = []
arr[int] JPG_CV = []
arr[int] JPG_CQ = []
arr[int] JPG_CTD = []
arr[int] JPG_CTA = []
arr[int] JPG_CPRED = []
int JPG_NCOMP = 0
int JPG_RESTART = 0

// One plane per component, at that component's own resolution.
arr[int] JPG_P0 = []
arr[int] JPG_P1 = []
arr[int] JPG_P2 = []
arr[int] JPG_PW = []
arr[int] JPG_PH = []

// The picture's quantised coefficients, kept until every scan has been
// read. A baseline file arrives whole in one scan, but a progressive one
// (and a baseline one written as several scans) delivers each block's
// coefficients in pieces across many, so nothing can be dequantised and
// transformed until the last piece is in. Per component a run of blocks
// in raster order over the padded block grid, 64 coefficients each, in
// NATURAL order (row by row of the 8x8 block, not the zigzag order they
// travel in); JPG_COFF[c] is where component c's run starts, JPG_BW and
// JPG_BH its padded size in blocks, JPG_NBW and JPG_NBH the blocks that
// lie inside the picture -- which is what a scan of ONE component walks.
arr[int] JPG_COEF = []
arr[int] JPG_COFF = []
arr[int] JPG_BW = []
arr[int] JPG_BH = []
arr[int] JPG_NBW = []
arr[int] JPG_NBH = []
arr[int] JPG_CW = []              // each component's real width and height in samples
arr[int] JPG_CHT = []
arr[int] JPG_UNZIG = []
arr[int] JPG_CQT = []            // each component's quantiser, latched at its first scan
arr[bool] JPG_CQSET = []

bool JPG_PROGRESSIVE = false
int JPG_EOBRUN = 0
// The colour transform: JFIF and Adobe markers decide it, as in libjpeg.
bool JPG_JFIF = false
bool JPG_ADOBE = false
int JPG_ADOBE_TRANSFORM = 0

// The components in the current scan (indices into the frame's).
arr[int] JPG_SC = []
int JPG_NS = 0

int func jpgBE16(at:int) {
    return (JPG_IN[at] << 8) | JPG_IN[at + 1]
}

// ---- one block, five ways ---------------------------------------------
//
// T.81 F.2 (sequential) and G.1.2 (progressive). `at` is the block's
// first coefficient in JPG_COEF.

// Sequential: the DC difference, then the AC run/size pairs.
void func jpgSeqBlock(c:int, at:int) {
    int t = jpgDecodeHuff(JPG_CTD[c])
    int diff = 0
    if t > 0 { diff = jpgExtend(jpgReceive(t), t) }
    JPG_CPRED[c] = JPG_CPRED[c] + diff
    JPG_COEF[at] = JPG_CPRED[c]

    int k = 1
    while k < 64 {
        int rs = jpgDecodeHuff(4 + JPG_CTA[c])
        int s = rs & 15
        int r = rs >> 4
        if s == 0 {
            // 0x00 ends the block; 0xF0 is a run of sixteen zeros.
            if r != 15 { k = 64 } else { k = k + 16 }
        } else {
            k = k + r
            if k > 63 { k = 64 } else {
                JPG_COEF[at + JPG_ZIGZAG[k]] = jpgExtend(jpgReceive(s), s)
                k = k + 1
            }
        }
    }
}

// Progressive DC, first scan (Ah = 0): the difference of the DC value
// after its point transform, which is stored shifted back up by Al.
void func jpgDCFirst(c:int, at:int, al:int) {
    int t = jpgDecodeHuff(JPG_CTD[c])
    int diff = 0
    if t > 0 { diff = jpgExtend(jpgReceive(t), t) }
    JPG_CPRED[c] = JPG_CPRED[c] + diff
    JPG_COEF[at] = JPG_CPRED[c] << al
}

// Progressive DC, refinement: one more bit of every DC value, at bit Al.
void func jpgDCRefine(at:int, al:int) {
    if jpgBit() == 1 { JPG_COEF[at] = JPG_COEF[at] | (1 << al) }
}

// Progressive AC, first pass over a band Ss..Se: run/size pairs as in a
// sequential block, plus the end-of-band RUN -- a symbol whose run field
// r < 15 and size 0 means "this block and the next 2^r - 1 + (r more
// bits) blocks have no more coefficients in this band".
void func jpgACFirst(c:int, at:int, ss:int, se:int, al:int) {
    if JPG_EOBRUN > 0 {
        JPG_EOBRUN = JPG_EOBRUN - 1
        return
    }
    int k = ss
    while k <= se {
        int rs = jpgDecodeHuff(4 + JPG_CTA[c])
        int s = rs & 15
        int r = rs >> 4
        if s == 0 {
            if r == 15 {
                k = k + 16
            } else {
                JPG_EOBRUN = (1 << r) - 1
                if r > 0 { JPG_EOBRUN = JPG_EOBRUN + jpgReceive(r) }
                return
            }
        } else {
            k = k + r
            if k <= 63 {
                JPG_COEF[at + JPG_ZIGZAG[k]] = jpgExtend(jpgReceive(s), s) * (1 << al)
            }
            k = k + 1
        }
    }
}

// One correction bit for a coefficient that is already non-zero: if it
// is set and this bit of the magnitude is not yet, add it (away from
// zero, whichever sign the coefficient has).
void func jpgRefineNonzero(idx:int, p1:int) {
    if jpgBit() == 1 {
        int v = JPG_COEF[idx]
        if (v & p1) == 0 {
            if v >= 0 { JPG_COEF[idx] = v + p1 } else { JPG_COEF[idx] = v - p1 }
        }
    }
}

// Progressive AC, refinement (Ah > 0), T.81 G.1.2.3 -- the awkward one.
// Every coefficient in the band that is already non-zero gets a
// correction bit as the scan passes over it; a new non-zero coefficient
// (size 1, always +-1 at this bit) appears after `r` zero coefficients;
// and an end-of-band run ends the block, with the rest of the band's
// non-zero coefficients still owed their correction bits.
void func jpgACRefine(c:int, at:int, ss:int, se:int, al:int) {
    int p1 = 1 << al
    int k = ss
    if JPG_EOBRUN <= 0 {
        bool eob = false
        while k <= se && eob == false {
            int rs = jpgDecodeHuff(4 + JPG_CTA[c])
            int s = rs & 15
            int r = rs >> 4
            int newval = 0
            if s == 0 {
                if r != 15 {
                    JPG_EOBRUN = 1 << r
                    if r > 0 { JPG_EOBRUN = JPG_EOBRUN + jpgReceive(r) }
                    eob = true
                }
            } else {
                // s is 1 in a valid file: the sign bit of the new +-1.
                if jpgBit() == 1 { newval = p1 } else { newval = 0 - p1 }
            }
            if eob == false {
                // Skip r zero coefficients (16 for a ZRL), correcting
                // every non-zero one passed on the way.
                bool placed = false
                while k <= se && placed == false {
                    int idx = at + JPG_ZIGZAG[k]
                    if JPG_COEF[idx] != 0 {
                        jpgRefineNonzero(idx, p1)
                    } else {
                        if r == 0 {
                            placed = true
                        } else {
                            r = r - 1
                        }
                    }
                    if placed == false { k = k + 1 }
                }
                if newval != 0 && k <= 63 {
                    JPG_COEF[at + JPG_ZIGZAG[k]] = newval
                }
                k = k + 1
            }
        }
    }
    if JPG_EOBRUN > 0 {
        // The rest of the band: only correction bits.
        while k <= se {
            int idx = at + JPG_ZIGZAG[k]
            if JPG_COEF[idx] != 0 { jpgRefineNonzero(idx, p1) }
            k = k + 1
        }
        JPG_EOBRUN = JPG_EOBRUN - 1
    }
}

void func jpgScanBlock(c:int, bx:int, by:int, ss:int, se:int, ah:int, al:int) {
    int at = JPG_COFF[c] + (((by * JPG_BW[c]) + bx) * 64)
    if JPG_PROGRESSIVE == false {
        jpgSeqBlock(c, at)
        return
    }
    if ss == 0 {
        if ah == 0 { jpgDCFirst(c, at, al) } else { jpgDCRefine(at, al) }
        return
    }
    if ah == 0 { jpgACFirst(c, at, ss, se, al) } else { jpgACRefine(c, at, ss, se, al) }
}

// ---- one scan ---------------------------------------------------------

// Skips a restart marker (RSTn, FF D0..D7) if that is what the input is
// at, and starts the next interval: bit buffer, DC predictors and the
// end-of-band run all reset -- the point of restarts is that damage
// cannot travel past one.
void func jpgRestart() {
    jpgBitReset()
    // Look forward for the RSTn, past any entropy bytes the bit reader
    // has not needed, and take exactly one: what follows it is entropy
    // data again, which may well begin with a stuffed 0xFF 0x00.
    int p = JPG_POS
    bool found = false
    bool stop = false
    while found == false && stop == false && (p + 1) < JPG_IN.length {
        if JPG_IN[p] == 255 {
            int nx = JPG_IN[p + 1]
            if nx >= 208 && nx <= 215 {
                JPG_POS = p + 2
                found = true
            } else {
                if nx == 0 { p = p + 2 } else {
                    if nx == 255 { p = p + 1 } else { stop = true }
                }
            }
        } else {
            p = p + 1
        }
    }
    int ci = 0
    while ci < JPG_NCOMP {
        JPG_CPRED[ci] = 0
        ci = ci + 1
    }
    JPG_EOBRUN = 0
}

// A scan of the components in JPG_SC. Several components are
// INTERLEAVED, MCU by MCU over the whole padded grid; one component is
// walked block by block over the blocks inside the picture only. Either
// way the restart interval counts those units.
void func jpgRunScan(ss:int, se:int, ah:int, al:int, mcux:int, mcuy:int) {
    jpgBitReset()
    JPG_EOBRUN = 0
    int ci = 0
    while ci < JPG_NCOMP {
        JPG_CPRED[ci] = 0
        ci = ci + 1
    }
    int unit = 0
    if JPG_NS > 1 {
        int total = mcux * mcuy
        while unit < total {
            if JPG_RESTART > 0 && unit > 0 && (unit % JPG_RESTART) == 0 { jpgRestart() }
            int my = Math.floorDiv(unit, mcux)
            int mx = unit % mcux
            int s = 0
            while s < JPG_NS {
                int c = JPG_SC[s]
                int by = 0
                while by < JPG_CV[c] {
                    int bx = 0
                    while bx < JPG_CH[c] {
                        jpgScanBlock(c, (mx * JPG_CH[c]) + bx, (my * JPG_CV[c]) + by, ss, se, ah, al)
                        bx = bx + 1
                    }
                    by = by + 1
                }
                s = s + 1
            }
            unit = unit + 1
        }
    } else {
        int c = JPG_SC[0]
        int nbw = JPG_NBW[c]
        int total = nbw * JPG_NBH[c]
        while unit < total {
            if JPG_RESTART > 0 && unit > 0 && (unit % JPG_RESTART) == 0 { jpgRestart() }
            jpgScanBlock(c, unit % nbw, Math.floorDiv(unit, nbw), ss, se, ah, al)
            unit = unit + 1
        }
    }
}

// After a scan: on to the next marker, past whatever entropy-coded bytes
// were not consumed (the bit reader stops at the last byte it needed),
// restart markers, and 0xFF fill.
void func jpgSeekMarker() {
    bool found = false
    while found == false && (JPG_POS + 1) < JPG_IN.length {
        if JPG_IN[JPG_POS] == 255 {
            int nx = JPG_IN[JPG_POS + 1]
            if nx == 255 {
                JPG_POS = JPG_POS + 1
            } else {
                if nx != 0 && (nx < 208 || nx > 215) { found = true } else { JPG_POS = JPG_POS + 2 }
            }
        } else {
            JPG_POS = JPG_POS + 1
        }
    }
}

// Bilinear sample of a subsampled plane at full-resolution (x, y).
//
// sx/sy are how many output pixels one source sample covers. The
// half-sample offsets place a source sample at the centre of its box,
// which is what makes this agree with libjpeg's triangular filter
// rather than being shifted half a pixel from it. Edges replicate:
// clamping the index is the standard choice and the one libjpeg makes --
// to the component's REAL size (pw x ph samples), not to the block-padded
// plane it is stored in: the padding past the edge is whatever the encoder
// filled it with, and libjpeg never looks at it.
float func jpgSample(plane:arr[int], stride:int, pw:int, ph:int, x:int, y:int,
                      ch:int, cv:int, hmax:int, vmax:int) {
    float sx = hmax.toFloat() / ch.toFloat()
    float sy = vmax.toFloat() / cv.toFloat()
    float u = ((x.toFloat() + 0.5) / sx) - 0.5
    float v = ((y.toFloat() + 0.5) / sy) - 0.5

    int u0 = Math.floor(u)
    int v0 = Math.floor(v)
    float fu = u - u0.toFloat()
    float fv = v - v0.toFloat()
    int u1 = u0 + 1
    int v1 = v0 + 1
    if u0 < 0 { u0 = 0 }
    if v0 < 0 { v0 = 0 }
    if u1 < 0 { u1 = 0 }
    if v1 < 0 { v1 = 0 }
    if u0 > (pw - 1) { u0 = pw - 1 }
    if u1 > (pw - 1) { u1 = pw - 1 }
    if v0 > (ph - 1) { v0 = ph - 1 }
    if v1 > (ph - 1) { v1 = ph - 1 }

    float a = plane[(v0 * stride) + u0].toFloat()
    float b = plane[(v0 * stride) + u1].toFloat()
    float c = plane[(v1 * stride) + u0].toFloat()
    float d = plane[(v1 * stride) + u1].toFloat()
    float top = a + ((b - a) * fu)
    float bot = c + ((d - c) * fu)
    return top + ((bot - top) * fv)
}

// The picture from its coefficients: dequantise each block, transform it,
// and put the samples in the component's plane.
void func jpgTransformAll() {
    int c = 0
    while c < JPG_NCOMP {
        int bw = JPG_BW[c]
        int bh = JPG_BH[c]
        int qbase = c * 64
        int by = 0
        while by < bh {
            int bx = 0
            while bx < bw {
                int at = JPG_COFF[c] + (((by * bw) + bx) * 64)
                arr[float] blk = []
                int n = 0
                while n < 64 {
                    blk.push((JPG_COEF[at + n] * JPG_CQT[qbase + JPG_UNZIG[n]]).toFloat())
                    n = n + 1
                }
                int base = (by * 8 * bw * 8) + (bx * 8)
                if c == 0 { jpgIdct(blk, JPG_P0, base, bw * 8) }
                if c == 1 { jpgIdct(blk, JPG_P1, base, bw * 8) }
                if c == 2 { jpgIdct(blk, JPG_P2, base, bw * 8) }
                bx = bx + 1
            }
            by = by + 1
        }
        c = c + 1
    }
}

arr[int] func jpgDecode(src:arr[int]) {
    JPG_W = 0
    JPG_H = 0
    JPG_ERR = 0
    JPG_IN = src
    JPG_POS = 2
    JPG_RESTART = 0
    JPG_PROGRESSIVE = false
    JPG_JFIF = false
    JPG_ADOBE = false
    JPG_ADOBE_TRANSFORM = 0
    arr[int] empty = []
    jpgInitCos()

    if src.length < 4 { JPG_ERR = 1 return empty }
    if src[0] != 255 || src[1] != 216 { JPG_ERR = 1 return empty }

    jpgFill(JPG_QUANT, 4 * 64)
    jpgFill(JPG_HMIN, 8 * 17)
    jpgFill(JPG_HMAX, 8 * 17)
    jpgFill(JPG_HPTR, 8 * 17)
    jpgFill(JPG_HBASE, 8)
    while JPG_HVAL.length > 0 { JPG_HVAL.pop() }
    while JPG_CID.length > 0 { JPG_CID.pop() }
    while JPG_CH.length > 0 { JPG_CH.pop() }
    while JPG_CV.length > 0 { JPG_CV.pop() }
    while JPG_CQ.length > 0 { JPG_CQ.pop() }
    jpgFill(JPG_UNZIG, 64)
    int zi = 0
    while zi < 64 {
        JPG_UNZIG[JPG_ZIGZAG[zi]] = zi
        zi = zi + 1
    }

    bool haveFrame = false
    bool decodedScan = false
    bool eoi = false
    int hmax = 1
    int vmax = 1
    int mcux = 0
    int mcuy = 0
    while eoi == false {
        // Find the next marker: 0xFF fill is legal before one.
        while (JPG_POS + 1) < JPG_IN.length && JPG_IN[JPG_POS] == 255 && JPG_IN[JPG_POS + 1] == 255 {
            JPG_POS = JPG_POS + 1
        }
        if (JPG_POS + 1) >= JPG_IN.length { JPG_ERR = 2 return empty }
        if JPG_IN[JPG_POS] != 255 { JPG_ERR = 2 return empty }
        int m = JPG_IN[JPG_POS + 1]
        if m == 217 {
            eoi = true
        } else {
            if (JPG_POS + 3) >= JPG_IN.length { JPG_ERR = 2 return empty }
            int seglen = jpgBE16(JPG_POS + 2)
            int body = JPG_POS + 4
            if (JPG_POS + 2 + seglen) > JPG_IN.length { JPG_ERR = 2 return empty }

            if m == 192 || m == 193 || m == 194 {
                // SOF0 baseline, SOF1 extended sequential (8-bit
                // sequential Huffman with up to four tables of each kind
                // -- which this decoder has room for anyway), SOF2
                // progressive.
                if haveFrame { JPG_ERR = 2 return empty }
                JPG_PROGRESSIVE = m == 194
                JPG_H = jpgBE16(body + 1)
                JPG_W = jpgBE16(body + 3)
                JPG_NCOMP = JPG_IN[body + 5]
                if JPG_IN[body] != 8 { JPG_ERR = 3 return empty }
                if JPG_NCOMP != 1 && JPG_NCOMP != 3 { JPG_ERR = 4 return empty }
                if JPG_W <= 0 || JPG_H <= 0 { JPG_ERR = 7 return empty }
                int c = 0
                while c < JPG_NCOMP {
                    int at = body + 6 + (c * 3)
                    JPG_CID.push(JPG_IN[at])
                    JPG_CH.push(JPG_IN[at + 1] >> 4)
                    JPG_CV.push(JPG_IN[at + 1] & 15)
                    JPG_CQ.push(JPG_IN[at + 2])
                    if JPG_CH[c] < 1 || JPG_CH[c] > 4 || JPG_CV[c] < 1 || JPG_CV[c] > 4 { JPG_ERR = 4 return empty }
                    c = c + 1
                }
                // Planes and coefficient storage, one run per component.
                c = 0
                while c < JPG_NCOMP {
                    if JPG_CH[c] > hmax { hmax = JPG_CH[c] }
                    if JPG_CV[c] > vmax { vmax = JPG_CV[c] }
                    c = c + 1
                }
                mcux = Math.floorDiv(JPG_W + (8 * hmax) - 1, 8 * hmax)
                mcuy = Math.floorDiv(JPG_H + (8 * vmax) - 1, 8 * vmax)
                jpgFill(JPG_PW, JPG_NCOMP)
                jpgFill(JPG_PH, JPG_NCOMP)
                jpgFill(JPG_CPRED, JPG_NCOMP)
                jpgFill(JPG_CTD, JPG_NCOMP)
                jpgFill(JPG_CTA, JPG_NCOMP)
                jpgFill(JPG_COFF, JPG_NCOMP)
                jpgFill(JPG_BW, JPG_NCOMP)
                jpgFill(JPG_BH, JPG_NCOMP)
                jpgFill(JPG_NBW, JPG_NCOMP)
                jpgFill(JPG_NBH, JPG_NCOMP)
                jpgFill(JPG_CW, JPG_NCOMP)
                jpgFill(JPG_CHT, JPG_NCOMP)
                jpgFill(JPG_CQT, JPG_NCOMP * 64)
                while JPG_CQSET.length > 0 { JPG_CQSET.pop() }
                int total = 0
                c = 0
                while c < JPG_NCOMP {
                    JPG_BW[c] = mcux * JPG_CH[c]
                    JPG_BH[c] = mcuy * JPG_CV[c]
                    JPG_PW[c] = JPG_BW[c] * 8
                    JPG_PH[c] = JPG_BH[c] * 8
                    JPG_COFF[c] = total
                    total = total + (JPG_BW[c] * JPG_BH[c] * 64)
                    // The blocks inside the picture: the component's own
                    // width in samples is ceil(W * h / hmax).
                    int cw = Math.floorDiv((JPG_W * JPG_CH[c]) + hmax - 1, hmax)
                    int chh = Math.floorDiv((JPG_H * JPG_CV[c]) + vmax - 1, vmax)
                    JPG_CW[c] = cw
                    JPG_CHT[c] = chh
                    JPG_NBW[c] = Math.floorDiv(cw + 7, 8)
                    JPG_NBH[c] = Math.floorDiv(chh + 7, 8)
                    JPG_CQSET.push(false)
                    c = c + 1
                }
                jpgFill(JPG_COEF, total)
                jpgFill(JPG_P0, JPG_PW[0] * JPG_PH[0])
                if JPG_NCOMP == 3 {
                    jpgFill(JPG_P1, JPG_PW[1] * JPG_PH[1])
                    jpgFill(JPG_P2, JPG_PW[2] * JPG_PH[2])
                }
                haveFrame = true
            }
            // SOF3 (lossless), the differential frames, and arithmetic
            // coding are not decoded.
            if m == 195 || (m >= 197 && m <= 199) || (m >= 201 && m <= 203) || (m >= 205 && m <= 207) {
                JPG_ERR = 5
                return empty
            }

            if m == 224 {
                // APP0 -- "JFIF\\0" says the colours are YCbCr.
                if seglen >= 7 && JPG_IN[body] == 74 && JPG_IN[body + 1] == 70 && JPG_IN[body + 2] == 73 && JPG_IN[body + 3] == 70 {
                    JPG_JFIF = true
                }
            }
            if m == 238 {
                // APP14 -- "Adobe", and after 11 more bytes its colour
                // transform: 0 means the three components are RGB as
                // they stand, 1 that they are YCbCr.
                if seglen >= 14 && JPG_IN[body] == 65 && JPG_IN[body + 1] == 100 && JPG_IN[body + 2] == 111 && JPG_IN[body + 3] == 98 && JPG_IN[body + 4] == 101 {
                    JPG_ADOBE = true
                    JPG_ADOBE_TRANSFORM = JPG_IN[body + 11]
                }
            }

            if m == 219 {
                // DQT -- one segment may carry several tables.
                int at = body
                while at < (JPG_POS + 2 + seglen) {
                    int pq = JPG_IN[at] >> 4
                    int tq = JPG_IN[at] & 15
                    if pq != 0 { JPG_ERR = 6 return empty }
                    if tq > 3 { JPG_ERR = 6 return empty }
                    int k = 0
                    while k < 64 {
                        JPG_QUANT[(tq * 64) + k] = JPG_IN[at + 1 + k]
                        k = k + 1
                    }
                    at = at + 65
                }
            }

            if m == 196 {
                // DHT -- also several per segment.
                int at = body
                while at < (JPG_POS + 2 + seglen) {
                    int tc = JPG_IN[at] >> 4
                    int th = JPG_IN[at] & 15
                    if tc > 1 || th > 3 { JPG_ERR = 6 return empty }
                    int t = (tc * 4) + th
                    int total = 0
                    arr[int] bits = []
                    int l = 0
                    while l < 16 {
                        int n = JPG_IN[at + 1 + l]
                        bits.push(n)
                        total = total + n
                        l = l + 1
                    }
                    // A table redefined between scans (progressive files
                    // do this) replaces its symbols; the symbol array is
                    // append-only, so the new ones go on the end and the
                    // table's base moves.
                    JPG_HBASE[t] = JPG_HVAL.length
                    int k = 0
                    while k < total {
                        JPG_HVAL.push(JPG_IN[at + 17 + k])
                        k = k + 1
                    }
                    // Canonical codes, T.81 F.2.2.3.
                    int code = 0
                    int idx = 0
                    l = 1
                    while l < 17 {
                        int n = bits[l - 1]
                        JPG_HPTR[(t * 17) + l] = idx
                        JPG_HMIN[(t * 17) + l] = code
                        code = code + n
                        idx = idx + n
                        JPG_HMAX[(t * 17) + l] = code - 1
                        if n == 0 { JPG_HMAX[(t * 17) + l] = 0 - 1 }
                        code = code << 1
                        l = l + 1
                    }
                    at = at + 17 + total
                }
            }

            if m == 221 { JPG_RESTART = jpgBE16(body) }

            if m == 218 {
                // SOS -- this scan's components, their table selectors,
                // and (Ss, Se, Ah, Al): the spectral band it carries and
                // the successive-approximation bits.
                if haveFrame == false { JPG_ERR = 2 return empty }
                int ns = JPG_IN[body]
                if ns < 1 || ns > JPG_NCOMP { JPG_ERR = 2 return empty }
                while JPG_SC.length > 0 { JPG_SC.pop() }
                int s = 0
                while s < ns {
                    int cs = JPG_IN[body + 1 + (s * 2)]
                    int tt = JPG_IN[body + 2 + (s * 2)]
                    int c = 0
                    bool matched = false
                    while c < JPG_NCOMP {
                        if JPG_CID[c] == cs && matched == false {
                            matched = true
                            JPG_SC.push(c)
                            JPG_CTD[c] = tt >> 4
                            JPG_CTA[c] = tt & 15
                            // The quantiser is latched the first time the
                            // component is scanned.
                            if JPG_CQSET[c] == false {
                                int k = 0
                                while k < 64 {
                                    JPG_CQT[(c * 64) + k] = JPG_QUANT[(JPG_CQ[c] * 64) + k]
                                    k = k + 1
                                }
                                JPG_CQSET[c] = true
                            }
                        }
                        c = c + 1
                    }
                    if matched == false { JPG_ERR = 2 return empty }
                    s = s + 1
                }
                JPG_NS = ns
                int ss = JPG_IN[body + 1 + (ns * 2)]
                int se = JPG_IN[body + 2 + (ns * 2)]
                int ah = JPG_IN[body + 3 + (ns * 2)] >> 4
                int al = JPG_IN[body + 3 + (ns * 2)] & 15
                if JPG_PROGRESSIVE == false {
                    ss = 0
                    se = 63
                    ah = 0
                    al = 0
                } else {
                    if ss > se || se > 63 || al > 13 { JPG_ERR = 2 return empty }
                    // An AC band is one component's alone (T.81 G.1.1.1.1).
                    if ss > 0 && ns != 1 { JPG_ERR = 2 return empty }
                }
                JPG_POS = JPG_POS + 2 + seglen
                jpgRunScan(ss, se, ah, al, mcux, mcuy)
                decodedScan = true
                jpgSeekMarker()
            } else {
                JPG_POS = JPG_POS + 2 + seglen
            }
        }
    }

    if haveFrame == false || decodedScan == false { JPG_ERR = 7 return empty }

    jpgTransformAll()

    // ---- upsample and convert -------------------------------------
    //
    // Triangular (bilinear) chroma upsampling, which is what libjpeg
    // does by default and calls "fancy". Nearest-neighbour was the
    // first version here and decoded the fixture's first pixel exactly
    // right and its neighbours 4-5 units off -- the give-away that the
    // block decode was correct and only the resampling was not.
    //
    // A chroma sample sits at the CENTRE of the box it covers, so the
    // source coordinate is (x + 0.5)/sx - 0.5 rather than x/sx. Getting
    // that half-sample offset wrong shifts the whole chroma plane by
    // half a pixel, which looks like colour fringing on one side of
    // every edge.
    //
    // Three components are YCbCr unless the file says otherwise, the way
    // libjpeg decides: a JFIF marker says YCbCr; an Adobe marker says
    // what its transform byte says; with neither, component ids of
    // 'R', 'G', 'B' mean RGB and anything else YCbCr.
    bool ycc = true
    if JPG_NCOMP == 3 {
        if JPG_JFIF == false {
            if JPG_ADOBE {
                ycc = JPG_ADOBE_TRANSFORM != 0
            } else {
                if JPG_CID[0] == 82 && JPG_CID[1] == 71 && JPG_CID[2] == 66 { ycc = false }
            }
        }
    }
    arr[int] out = []
    int y = 0
    while y < JPG_H {
        int x = 0
        while x < JPG_W {
            int yy = JPG_P0[(y * JPG_PW[0]) + x]
            int r = yy
            int g = yy
            int b = yy
            if JPG_NCOMP == 3 {
                if ycc {
                    float cb = jpgSample(JPG_P1, JPG_PW[1], JPG_CW[1], JPG_CHT[1],
                                          x, y, JPG_CH[1], JPG_CV[1], hmax, vmax) - 128.0
                    float cr = jpgSample(JPG_P2, JPG_PW[2], JPG_CW[2], JPG_CHT[2],
                                          x, y, JPG_CH[2], JPG_CV[2], hmax, vmax) - 128.0
                    float fy = yy.toFloat()
                    r = jpgClamp(Math.round(fy + (1.402 * cr)))
                    g = jpgClamp(Math.round(fy - (0.344136 * cb) - (0.714136 * cr)))
                    b = jpgClamp(Math.round(fy + (1.772 * cb)))
                } else {
                    g = jpgClamp(Math.round(jpgSample(JPG_P1, JPG_PW[1], JPG_CW[1], JPG_CHT[1],
                                                       x, y, JPG_CH[1], JPG_CV[1], hmax, vmax)))
                    b = jpgClamp(Math.round(jpgSample(JPG_P2, JPG_PW[2], JPG_CW[2], JPG_CHT[2],
                                                       x, y, JPG_CH[2], JPG_CV[2], hmax, vmax)))
                }
            }
            out.push(r)
            out.push(g)
            out.push(b)
            out.push(255)
            x = x + 1
        }
        y = y + 1
    }
    return out
}
