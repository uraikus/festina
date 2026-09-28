// runtime.md phase 5, slice 6: text, drawn by font.f and raster.f.
//
// This is what drawText, img.drawText and the two measure calls reach
// when the text is in the default face -- sans-serif, regular, the
// bundled DejaVu Sans -- and whatever else the runtime says is not
// this component's (bold, italic, another family, a rotating or
// scaling transform) stays with Cairo, unchanged.
//
// It answers COVERAGE, not pixels. The C side owns the surface and the
// fill state -- colour, fillAlpha, and the rule that text ignores a
// gradient (runtime.md, phase 5) -- so it composites this mask with
// exactly the source it always used. What changes is only which
// rasteriser decided how much of each pixel a glyph covers.
//
// The font arrives as an argument, once per call, from the bytes the
// compiler links into programs that use text. It is opened on the first
// call and kept: every call after that passes the same array.

import font.f
import raster.f

bool TXT_OPENED = false
int TXT_ERR = 0

bool func txtOpen(font:arr[int]) {
    if !TXT_OPENED {
        TXT_ERR = fntOpen(font)
        TXT_OPENED = true
    }
    return TXT_ERR == 0
}

// measureTextWidth / measureTextHeight, or -1 if the font would not
// open -- which the caller answers by asking Cairo instead.
int func festinaTextWidth(s:text, px:int, font:arr[int]) {
    if !txtOpen(font) { return -1 }
    return fntTextWidth(s, px)
}

int func festinaTextHeight(s:text, px:int, font:arr[int]) {
    if !txtOpen(font) { return -1 }
    return fntTextHeight(s, px)
}

// ---- the glyph cache ----
//
// A glyph's coverage depends on the glyph and the size and on nothing
// else: every pen position is a whole pixel (advances are rounded), so
// the mask a glyph makes at one position is the same mask at any
// other, moved. Rasterising it again for every occurrence of every
// letter of every line was 693 us per 25-character line -- 77 times
// Cairo's 9 us, which caches glyph bitmaps -- and a program drawing
// text every frame could afford about twenty lines. Each (glyph, size)
// is rasterised once, at pen 0, and blitted after that.
//
// One flat float pool, because a map cannot hold arrays: an entry is
// [x0, y0, w, h, then w*h coverages 0..1], its offset found through a
// map from a key to that offset. A glyph with no ink is a w = 0 entry,
// so it is not rasterised again either. The pool is emptied at the
// START of a call once it is over TXT_POOL_LIMIT floats (32 MB) --
// never mid-line, where an offset already taken would go stale.

map[int] TXT_INDEX = {}
// A plain array, not `amor`: it only grows on a cache miss, and the
// bootstrap compiler (bootstrap/codegen.f) compiles a GLOBAL `amor arr`
// as a plain array, which the differential harness caught -- so until
// that port learns it, a global amor array cannot be in a corpus file.
arr[float] TXT_POOL = []
int TXT_POOL_LIMIT = 4000000

// Offset of the glyph's entry in TXT_POOL, rasterising it if it is not
// there yet.
int func txtGlyph(g:int, px:int, size:float) {
    int key = (g * 8192) + px
    int have = TXT_INDEX[key]
    if have != null { return have }

    int at = TXT_POOL.length
    arr[float] pts = []
    arr[int] ends = []
    int n = fntGlyphPath(g, size, 0.0, 0.0, pts, ends)
    if n <= 0 || pts.length < 6 {
        TXT_POOL.push(0.0)
        TXT_POOL.push(0.0)
        TXT_POOL.push(0.0)
        TXT_POOL.push(0.0)
        TXT_INDEX[key] = at
        return at
    }
    float lx = pts[0]
    float hx = lx
    float ly = pts[1]
    float hy = ly
    int k = 0
    while k < pts.length {
        if pts[k] < lx { lx = pts[k] }
        if pts[k] > hx { hx = pts[k] }
        if pts[k + 1] < ly { ly = pts[k + 1] }
        if pts[k + 1] > hy { hy = pts[k + 1] }
        k = k + 2
    }
    int bx0 = Math.floor(lx)
    int by0 = Math.floor(ly)
    int gw = (Math.floor(hx) + 1) - bx0
    int gh = (Math.floor(hy) + 1) - by0

    // The glyph's points, moved into its own box.
    arr[float] local = []
    k = 0
    while k < pts.length {
        local.push(pts[k] - bx0.toFloat())
        local.push(pts[k + 1] - by0.toFloat())
        k = k + 2
    }
    TXT_POOL.push(bx0.toFloat())
    TXT_POOL.push(by0.toFloat())
    TXT_POOL.push(gw.toFloat())
    TXT_POOL.push(gh.toFloat())
    int filled = TXT_POOL.length
    int total = gw * gh
    int z = 0
    while z < total {
        TXT_POOL.push(0.0)
        z = z + 1
    }
    rasEnsureCov(gw)
    if rasPathExtent(local, gh) {
        int row = RAS_Y0
        while row < RAS_Y1 {
            rasRowCoverage(row, gw, local, ends, RAS_NONZERO)
            int c = 0
            while c < gw {
                float v = RAS_COV[c]
                if v > 1.0 { v = 1.0 }
                if v > 0.0 { TXT_POOL[filled + (row * gw) + c] = v }
                c = c + 1
            }
            row = row + 1
        }
    }
    TXT_INDEX[key] = at
    return at
}

// The coverage of a line of text whose pen starts at (0, 0) on the
// baseline, as [x0, y0, w, h, then w * h values 0..255 row by row]:
// the mask's top-left corner relative to the pen, its size, and how
// much of each pixel is covered. A line with no ink -- empty, or all
// spaces -- is [0, 0, 0, 0]. [-1] means this component cannot draw it
// and the caller should use Cairo.
//
// Each glyph's coverage is ADDED into the mask, saturating at full,
// which is how Cairo builds the mask for a run of glyphs before it
// composites: two glyphs that overlap are not unioned into one shape.
arr[int] func festinaTextMask(s:text, px:int, font:arr[int]) {
    arr[int] out = []
    if !txtOpen(font) {
        out.push(-1)
        return out
    }
    if TXT_POOL.length > TXT_POOL_LIMIT {
        TXT_POOL = []
        TXT_INDEX = {}
    }
    arr[int] gids = []
    arr[float] pens = []
    int width = fntLayout(s, px, 0.0, gids, pens)
    float size = px.toFloat()

    // Every glyph cached, then the union of their boxes -- which is
    // the mask.
    arr[int] offs = []
    bool any = false
    int mx0 = 0
    int my0 = 0
    int mx1 = 0
    int my1 = 0
    int i = 0
    while i < gids.length {
        int off = txtGlyph(gids[i], px, size)
        offs.push(off)
        int gw = Math.round(TXT_POOL[off + 2])
        int gh = Math.round(TXT_POOL[off + 3])
        if gw > 0 && gh > 0 {
            int pen = Math.round(pens[i])
            int bx0 = pen + Math.round(TXT_POOL[off])
            int by0 = Math.round(TXT_POOL[off + 1])
            if !any || bx0 < mx0 { mx0 = bx0 }
            if !any || by0 < my0 { my0 = by0 }
            if !any || bx0 + gw > mx1 { mx1 = bx0 + gw }
            if !any || by0 + gh > my1 { my1 = by0 + gh }
            any = true
        }
        i = i + 1
    }
    if !any {
        out.push(0)
        out.push(0)
        out.push(0)
        out.push(0)
        return out
    }

    int w = mx1 - mx0
    int h = my1 - my0
    // amor: geometric growth. Plain arrays reallocate on EVERY push,
    // and this loop pushes one element per pixel of the line -- with
    // the output below, that was two thirds of the time left once glyphs
    // were cached.
    amor arr[float] acc = []
    int a = 0
    while a < w * h {
        acc.push(0.0)
        a = a + 1
    }
    i = 0
    while i < gids.length {
        int off = offs[i]
        int gw = Math.round(TXT_POOL[off + 2])
        int gh = Math.round(TXT_POOL[off + 3])
        if gw > 0 && gh > 0 {
            int pen = Math.round(pens[i])
            int dx = (pen + Math.round(TXT_POOL[off])) - mx0
            int dy = Math.round(TXT_POOL[off + 1]) - my0
            int row = 0
            while row < gh {
                int src = off + 4 + (row * gw)
                int dst = ((row + dy) * w) + dx
                int c = 0
                while c < gw {
                    float v = TXT_POOL[src + c]
                    if v > 0.0 { acc[dst + c] = acc[dst + c] + v }
                    c = c + 1
                }
                row = row + 1
            }
        }
        i = i + 1
    }

    amor arr[int] grown = []
    grown.push(mx0)
    grown.push(my0)
    grown.push(w)
    grown.push(h)
    a = 0
    while a < w * h {
        float v = acc[a]
        if v > 1.0 { v = 1.0 }
        grown.push(Math.round(v * 255.0))
        a = a + 1
    }
    // The C side reads a plain arr[int]; this copies the amor one into it.
    arr[int] plain = grown.splice(0, grown.length)
    return plain
}
