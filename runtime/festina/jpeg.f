// runtime.md phase 2: baseline JPEG decoding (ITU T.81) in Festina.
//
// What libjpeg does for us today. A baseline JPEG is a marker stream
// carrying quantisation and Huffman tables, then one entropy-coded
// scan of 8x8 blocks: Huffman-decode the coefficients, scale them by
// the quantisation table, inverse-DCT each block, then undo chroma
// subsampling and convert YCbCr to RGB.
//
// Supported: baseline sequential (SOF0), 8-bit, 1 or 3 components,
// any h/v sampling factors, restart intervals. REFUSED, with
// JPG_ERR set and an empty result: progressive (SOF2), arithmetic
// coding, 12-bit, and CMYK. Same stance as png.f -- a decoder that
// returns plausible wrong pixels is worse than one that declines.
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

int func jpgBE16(at:int) {
    return (JPG_IN[at] << 8) | JPG_IN[at + 1]
}

// Decodes one 8x8 block into the given plane at (bx, by) in blocks.
void func jpgBlock(c:int, plane:arr[int], pw:int, bx:int, by:int) {
    arr[float] blk = []
    int i = 0
    while i < 64 {
        blk.push(0.0)
        i = i + 1
    }

    int qbase = JPG_CQ[c] * 64
    int t = jpgDecodeHuff(JPG_CTD[c])
    int diff = 0
    if t > 0 { diff = jpgExtend(jpgReceive(t), t) }
    JPG_CPRED[c] = JPG_CPRED[c] + diff
    blk[0] = (JPG_CPRED[c] * JPG_QUANT[qbase]).toFloat()

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
                int v = jpgExtend(jpgReceive(s), s)
                blk[JPG_ZIGZAG[k]] = (v * JPG_QUANT[qbase + k]).toFloat()
                k = k + 1
            }
        }
    }

    jpgIdct(blk, plane, (by * 8 * pw) + (bx * 8), pw)
}


// Bilinear sample of a subsampled plane at full-resolution (x, y).
//
// sx/sy are how many output pixels one source sample covers. The
// half-sample offsets place a source sample at the centre of its box,
// which is what makes this agree with libjpeg's triangular filter
// rather than being shifted half a pixel from it. Edges replicate:
// clamping the index is the standard choice and the one libjpeg makes.
float func jpgSample(plane:arr[int], pw:int, ph:int, x:int, y:int,
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

    float a = plane[(v0 * pw) + u0].toFloat()
    float b = plane[(v0 * pw) + u1].toFloat()
    float c = plane[(v1 * pw) + u0].toFloat()
    float d = plane[(v1 * pw) + u1].toFloat()
    float top = a + ((b - a) * fu)
    float bot = c + ((d - c) * fu)
    return top + ((bot - top) * fv)
}

arr[int] func jpgDecode(src:arr[int]) {
    JPG_W = 0
    JPG_H = 0
    JPG_ERR = 0
    JPG_IN = src
    JPG_POS = 2
    JPG_RESTART = 0
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
    while JPG_CTD.length > 0 { JPG_CTD.pop() }
    while JPG_CTA.length > 0 { JPG_CTA.pop() }

    bool inScan = false
    while inScan == false {
        if (JPG_POS + 3) >= JPG_IN.length { JPG_ERR = 2 return empty }
        if JPG_IN[JPG_POS] != 255 { JPG_ERR = 2 return empty }
        int m = JPG_IN[JPG_POS + 1]
        int seglen = jpgBE16(JPG_POS + 2)
        int body = JPG_POS + 4

        if m == 192 {
            // SOF0 -- baseline.
            JPG_H = jpgBE16(body + 1)
            JPG_W = jpgBE16(body + 3)
            JPG_NCOMP = JPG_IN[body + 5]
            if JPG_IN[body] != 8 { JPG_ERR = 3 return empty }
            if JPG_NCOMP != 1 && JPG_NCOMP != 3 { JPG_ERR = 4 return empty }
            int c = 0
            while c < JPG_NCOMP {
                int at = body + 6 + (c * 3)
                JPG_CID.push(JPG_IN[at])
                JPG_CH.push(JPG_IN[at + 1] >> 4)
                JPG_CV.push(JPG_IN[at + 1] & 15)
                JPG_CQ.push(JPG_IN[at + 2])
                c = c + 1
            }
        }
        // SOF2 progressive, SOF1/3/5-15, and arithmetic coding: refused.
        if m == 193 || m == 194 || m == 195 { JPG_ERR = 5 return empty }
        if m == 201 || m == 202 { JPG_ERR = 5 return empty }

        if m == 219 {
            // DQT -- one segment may carry several tables.
            int at = body
            while at < (JPG_POS + 2 + seglen) {
                int pq = JPG_IN[at] >> 4
                int tq = JPG_IN[at] & 15
                if pq != 0 { JPG_ERR = 6 return empty }
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
            // SOS -- the scan's own component-to-table mapping.
            int ns = JPG_IN[body]
            int s = 0
            while s < ns {
                int cs = JPG_IN[body + 1 + (s * 2)]
                int tt = JPG_IN[body + 2 + (s * 2)]
                int c = 0
                while c < JPG_NCOMP {
                    if JPG_CID[c] == cs {
                        while JPG_CTD.length <= c { JPG_CTD.push(0) }
                        while JPG_CTA.length <= c { JPG_CTA.push(0) }
                        JPG_CTD[c] = tt >> 4
                        JPG_CTA[c] = tt & 15
                    }
                    c = c + 1
                }
                s = s + 1
            }
            JPG_POS = JPG_POS + 2 + seglen
            inScan = true
        } else {
            JPG_POS = JPG_POS + 2 + seglen
        }
    }

    if JPG_W <= 0 || JPG_H <= 0 { JPG_ERR = 7 return empty }

    // ---- planes, one per component at its own resolution ----
    int hmax = 1
    int vmax = 1
    int c2 = 0
    while c2 < JPG_NCOMP {
        if JPG_CH[c2] > hmax { hmax = JPG_CH[c2] }
        if JPG_CV[c2] > vmax { vmax = JPG_CV[c2] }
        c2 = c2 + 1
    }
    int mcux = Math.floorDiv(JPG_W + (8 * hmax) - 1, 8 * hmax)
    int mcuy = Math.floorDiv(JPG_H + (8 * vmax) - 1, 8 * vmax)

    jpgFill(JPG_PW, JPG_NCOMP)
    jpgFill(JPG_PH, JPG_NCOMP)
    jpgFill(JPG_CPRED, JPG_NCOMP)
    c2 = 0
    while c2 < JPG_NCOMP {
        JPG_PW[c2] = mcux * JPG_CH[c2] * 8
        JPG_PH[c2] = mcuy * JPG_CV[c2] * 8
        c2 = c2 + 1
    }
    jpgFill(JPG_P0, JPG_PW[0] * JPG_PH[0])
    if JPG_NCOMP == 3 {
        jpgFill(JPG_P1, JPG_PW[1] * JPG_PH[1])
        jpgFill(JPG_P2, JPG_PW[2] * JPG_PH[2])
    }

    // ---- the scan ----
    jpgBitReset()
    int mcu = 0
    int total = mcux * mcuy
    while mcu < total {
        if JPG_RESTART > 0 && mcu > 0 && (mcu % JPG_RESTART) == 0 {
            // A restart marker resets the bit buffer and every DC
            // predictor, which is the whole point of having them: a
            // corrupt run cannot propagate past one interval.
            jpgBitReset()
            while (JPG_POS + 1) < JPG_IN.length && JPG_IN[JPG_POS] == 255 {
                int rm = JPG_IN[JPG_POS + 1]
                if rm >= 208 && rm <= 215 {
                    JPG_POS = JPG_POS + 2
                } else {
                    JPG_POS = JPG_IN.length
                }
            }
            int ci = 0
            while ci < JPG_NCOMP {
                JPG_CPRED[ci] = 0
                ci = ci + 1
            }
        }
        int my = Math.floorDiv(mcu, mcux)
        int mx = mcu % mcux
        int c = 0
        while c < JPG_NCOMP {
            int by = 0
            while by < JPG_CV[c] {
                int bx = 0
                while bx < JPG_CH[c] {
                    int px = (mx * JPG_CH[c]) + bx
                    int py = (my * JPG_CV[c]) + by
                    if c == 0 { jpgBlock(c, JPG_P0, JPG_PW[0], px, py) }
                    if c == 1 { jpgBlock(c, JPG_P1, JPG_PW[1], px, py) }
                    if c == 2 { jpgBlock(c, JPG_P2, JPG_PW[2], px, py) }
                    bx = bx + 1
                }
                by = by + 1
            }
            c = c + 1
        }
        mcu = mcu + 1
    }

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
                float cb = jpgSample(JPG_P1, JPG_PW[1], JPG_PH[1],
                                      x, y, JPG_CH[1], JPG_CV[1], hmax, vmax) - 128.0
                float cr = jpgSample(JPG_P2, JPG_PW[2], JPG_PH[2],
                                      x, y, JPG_CH[2], JPG_CV[2], hmax, vmax) - 128.0
                float fy = yy.toFloat()
                r = jpgClamp(Math.round(fy + (1.402 * cr)))
                g = jpgClamp(Math.round(fy - (0.344136 * cb) - (0.714136 * cr)))
                b = jpgClamp(Math.round(fy + (1.772 * cb)))
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
