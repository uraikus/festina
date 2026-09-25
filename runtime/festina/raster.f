// runtime.md phase 4: the rasteriser, slice 1.
//
// A surface here is the same buffer `img.toPixels()` hands out and
// `imageFromPixels(px, w, h)` takes back: four int per pixel -- R, G,
// B, A -- STRAIGHT alpha, row-major from the top-left. Nothing in this
// file knows about Cairo, about premultiplication, or about how a
// buffer reaches a screen. It fills pixels in an array.
//
// Straight alpha rather than premultiplied is a deliberate choice and
// it is the handoff format's, not this file's. Premultiplied is what
// the compositing arithmetic actually wants; converting at the edges
// costs two multiplies per pixel touched. The alternative is a
// rasteriser whose buffers cannot be handed to imageFromPixels without
// a conversion pass anyway, and a second in-memory format for everyone
// to get wrong. Slice 1 has no blending in it at all, so the cost is
// zero here and the decision is recorded for where it starts to bite.
//
// Slice 1 is opaque axis-aligned rectangles and nothing else. That is
// small on purpose: it is the narrowest thing that exercises the whole
// handoff end to end, and -- because an opaque axis-aligned fill
// involves no antialiasing and no blending -- it is one of the few
// places where BYTE-IDENTITY with Cairo is the right thing to demand
// rather than a bound. Everything with a soft edge comes later and is
// tested differently. See runtime.md's "Phase 4, specified".

// Clamp a value into [lo, hi]. Rectangles are clipped rather than
// rejected: a rect reaching past the edge fills the part that overlaps,
// which is what every canvas does and is ordinary at a sprite's margin.
int func rasClamp(v:int, lo:int, hi:int) {
    if v < lo { return lo }
    if v > hi { return hi }
    return v
}

// Fill an axis-aligned rectangle with an opaque colour.
//
// `px` is modified in place -- arr[int] is mutable, and a rasteriser
// that returned a fresh buffer per operation would copy the whole
// surface once per draw call.
//
// Coordinates are the same half-open convention the rest of the
// language uses: the rect covers x .. x+w-1 and y .. y+h-1. A
// non-positive width or height draws nothing rather than being an
// error, matching how clearRect and drawRect already treat one.
void func rasFillRect(px:arr[int], sw:int, sh:int, x:int, y:int, w:int, h:int,
                      r:int, g:int, b:int, a:int) {
    if w <= 0 || h <= 0 { return }
    if sw <= 0 || sh <= 0 { return }
    int x0 = rasClamp(x, 0, sw)
    int y0 = rasClamp(y, 0, sh)
    int x1 = rasClamp(x + w, 0, sw)
    int y1 = rasClamp(y + h, 0, sh)
    if x1 <= x0 || y1 <= y0 { return }

    int cr = rasClamp(r, 0, 255)
    int cg = rasClamp(g, 0, 255)
    int cb = rasClamp(b, 0, 255)
    int ca = rasClamp(a, 0, 255)

    int row = y0
    while row < y1 {
        // The row's base index, computed once rather than per pixel:
        // the inner loop is the one that runs sw times.
        int at = ((row * sw) + x0) * 4
        int col = x0
        while col < x1 {
            px[at] = cr
            px[at + 1] = cg
            px[at + 2] = cb
            px[at + 3] = ca
            at = at + 4
            col = col + 1
        }
        row = row + 1
    }
}

// A fresh surface: sw * sh pixels, every channel zero, which in
// straight alpha is fully transparent -- the same thing blankImage()
// produces, and the same thing imageFromPixels() will read back.
arr[int] func rasNewSurface(sw:int, sh:int) {
    arr[int] px = []
    if sw <= 0 || sh <= 0 { return px }
    int n = sw * sh * 4
    int i = 0
    while i < n {
        px.push(0)
        i = i + 1
    }
    return px
}

// ---- Slice 2: paths, scanline fill, winding rules, antialiasing ----
//
// A path is two arrays. `pts` is x0,y0,x1,y1,... in pixel coordinates,
// and `ends` holds one index PER SUBPATH: the point index one past
// that subpath's last point. So a single triangle is ends = [3], and a
// square with a square hole in it is ends = [4, 8]. Every subpath is
// implicitly closed; there is no "open" fill.
//
// Two arrays rather than a struct because this is the hot data: a
// glyph is a few hundred points and slice 5 will run this per
// character. Flat arrays of primitives are what this language makes
// cheap.
//
// **The algorithm: sub-scanlines in y, exact coverage in x.** For each
// pixel row, RAS_SUB evenly spaced horizontal lines are intersected
// with every edge; the crossings are sorted, the fill rule turns them
// into spans, and each span adds its exact horizontal coverage to a
// per-pixel accumulator. Coverage is therefore EXACT in x and
// quantised to 1/RAS_SUB in y.
//
// That asymmetry is deliberate and is the quality/complexity trade
// this slice makes. Exact analytic area (what Cairo does) needs a
// signed-area cell structure and is a substantially harder thing to
// get right; 16 sub-scanlines is within 1/16 of it on the worst case,
// a near-horizontal edge, and exact everywhere x dominates. The tests
// assert properties plus a measured bound rather than byte-identity,
// which runtime.md's "Phase 4, specified" commits to in advance for
// exactly this reason.

int RAS_SUB = 16
int RAS_NONZERO = 0
int RAS_EVENODD = 1

// Scratch, reused across rows and calls rather than reallocated. A
// fill touches these and nothing else keeps a reference.
arr[float] RAS_COV = []
arr[float] RAS_XS = []
arr[int] RAS_WS = []

// Grow a float scratch array to at least n entries.
void func rasEnsureCov(n:int) {
    while RAS_COV.length < n {
        RAS_COV.push(0.0)
    }
}

float func rasMinF(a:float, b:float) {
    if a < b { return a }
    return b
}

float func rasMaxF(a:float, b:float) {
    if a > b { return a }
    return b
}

// Fill a path.
//
// `rule` is RAS_NONZERO or RAS_EVENODD. Colour is straight alpha, and
// `a` is the fill's own alpha before coverage multiplies it -- so a
// half-transparent fill over an antialiased edge composites twice, as
// it should.
void func rasFillPath(px:arr[int], sw:int, sh:int,
                      pts:arr[float], ends:arr[int], rule:int,
                      r:int, g:int, b:int, a:int) {
    if sw <= 0 || sh <= 0 { return }
    if ends.length == 0 || pts.length < 6 { return }
    int ca = rasClamp(a, 0, 255)
    if ca == 0 { return }
    int cr = rasClamp(r, 0, 255)
    int cg = rasClamp(g, 0, 255)
    int cb = rasClamp(b, 0, 255)

    rasEnsureCov(sw)

    // Vertical extent, so rows the path cannot touch cost nothing.
    float minY = pts[1]
    float maxY = pts[1]
    int i = 1
    while i < pts.length {
        minY = rasMinF(minY, pts[i])
        maxY = rasMaxF(maxY, pts[i])
        i = i + 2
    }
    int y0 = rasClamp(Math.floor(minY), 0, sh)
    int y1 = rasClamp(Math.floor(maxY) + 1, 0, sh)
    if y1 <= y0 { return }

    float invSub = 1.0 / RAS_SUB.toFloat()

    int row = y0
    while row < y1 {
        int c = 0
        while c < sw {
            RAS_COV[c] = 0.0
            c = c + 1
        }

        int s = 0
        while s < RAS_SUB {
            float sy = row.toFloat() + ((s.toFloat() + 0.5) * invSub)

            // Crossings of this sub-scanline with every edge, kept
            // sorted by x as they are inserted: a scanline meets a
            // handful of edges even in a complex path, and an
            // insertion sort over parallel arrays avoids needing a
            // comparator over a struct.
            int nx = 0
            int sub = 0
            int from = 0
            while sub < ends.length {
                int to = ends[sub]
                int p = from
                while p < to {
                    int q = p + 1
                    if q == to { q = from }
                    float ax = pts[p * 2]
                    float ay = pts[(p * 2) + 1]
                    float bx = pts[q * 2]
                    float by = pts[(q * 2) + 1]
                    bool down = ay <= sy && by > sy
                    bool up = by <= sy && ay > sy
                    if down || up {
                        float t = (sy - ay) / (by - ay)
                        float xx = ax + (t * (bx - ax))
                        int w = 1
                        if up { w = -1 }
                        // insert, keeping RAS_XS[0..nx) ascending
                        while RAS_XS.length <= nx { RAS_XS.push(0.0) }
                        while RAS_WS.length <= nx { RAS_WS.push(0) }
                        int j = nx
                        while j > 0 && RAS_XS[j - 1] > xx {
                            RAS_XS[j] = RAS_XS[j - 1]
                            RAS_WS[j] = RAS_WS[j - 1]
                            j = j - 1
                        }
                        RAS_XS[j] = xx
                        RAS_WS[j] = w
                        nx = nx + 1
                    }
                    p = p + 1
                }
                from = to
                sub = sub + 1
            }

            // Crossings to spans, by the fill rule.
            int k = 0
            int wind = 0
            float spanStart = 0.0
            bool inside = false
            while k < nx {
                if rule == RAS_EVENODD {
                    wind = wind + 1
                } else {
                    wind = wind + RAS_WS[k]
                }
                bool wasIn = inside
                if rule == RAS_EVENODD {
                    inside = (wind % 2) != 0
                } else {
                    inside = wind != 0
                }
                if !wasIn && inside {
                    spanStart = RAS_XS[k]
                }
                if wasIn && !inside {
                    rasAddSpan(sw, spanStart, RAS_XS[k], invSub)
                }
                k = k + 1
            }
            s = s + 1
        }

        rasBlendRow(px, sw, row, cr, cg, cb, ca)
        row = row + 1
    }
}

// Add a horizontal span's coverage, exact at both ends.
void func rasAddSpan(sw:int, xa:float, xb:float, weight:float) {
    float lo = rasMaxF(xa, 0.0)
    float hi = rasMinF(xb, sw.toFloat())
    if hi <= lo { return }
    int ia = Math.floor(lo)
    int ib = Math.floor(hi)
    if ib >= sw { ib = sw - 1 }
    if ia == ib {
        RAS_COV[ia] = RAS_COV[ia] + ((hi - lo) * weight)
        return
    }
    RAS_COV[ia] = RAS_COV[ia] + (((ia + 1).toFloat() - lo) * weight)
    int i = ia + 1
    while i < ib {
        RAS_COV[i] = RAS_COV[i] + weight
        i = i + 1
    }
    RAS_COV[ib] = RAS_COV[ib] + ((hi - ib.toFloat()) * weight)
}

// Composite one row of accumulated coverage onto the surface.
//
// src-over in STRAIGHT alpha, which is where that format choice starts
// costing something: the general case needs a divide per channel to
// un-premultiply the result. The two cases that actually dominate --
// an opaque destination and an empty one -- are exact without it and
// are taken first.
void func rasBlendRow(px:arr[int], sw:int, row:int,
                      cr:int, cg:int, cb:int, ca:int) {
    int base = row * sw * 4
    int i = 0
    while i < sw {
        float cov = RAS_COV[i]
        if cov > 0.0 {
            if cov > 1.0 { cov = 1.0 }
            int sa = Math.round(ca.toFloat() * cov)
            if sa > 0 {
                int at = base + (i * 4)
                int da = px[at + 3]
                if da == 0 {
                    px[at] = cr
                    px[at + 1] = cg
                    px[at + 2] = cb
                    px[at + 3] = sa
                } else {
                    if sa == 255 {
                        px[at] = cr
                        px[at + 1] = cg
                        px[at + 2] = cb
                        px[at + 3] = 255
                    } else {
                        int inv = 255 - sa
                        if da == 255 {
                            px[at] = Math.floorDiv((cr * sa) + (px[at] * inv) + 127, 255)
                            px[at + 1] = Math.floorDiv((cg * sa) + (px[at + 1] * inv) + 127, 255)
                            px[at + 2] = Math.floorDiv((cb * sa) + (px[at + 2] * inv) + 127, 255)
                            px[at + 3] = 255
                        } else {
                            // General src-over on straight alpha.
                            int oa = sa + Math.floorDiv(da * inv + 127, 255)
                            if oa > 255 { oa = 255 }
                            if oa > 0 {
                                int dw = Math.floorDiv(da * inv + 127, 255)
                                px[at] = Math.floorDiv((cr * sa) + (px[at] * dw) + Math.floorDiv(oa, 2), oa)
                                px[at + 1] = Math.floorDiv((cg * sa) + (px[at + 1] * dw) + Math.floorDiv(oa, 2), oa)
                                px[at + 2] = Math.floorDiv((cb * sa) + (px[at + 2] * dw) + Math.floorDiv(oa, 2), oa)
                                px[at + 3] = oa
                            }
                        }
                    }
                }
            }
        }
        i = i + 1
    }
}
