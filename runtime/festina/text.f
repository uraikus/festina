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
// Each glyph is rasterised in a box just its own size, so the cost is
// the area of its ink, not the width of the whole line for every glyph.
arr[int] func festinaTextMask(s:text, px:int, font:arr[int]) {
    arr[int] out = []
    if !txtOpen(font) {
        out.push(-1)
        return out
    }
    arr[int] gids = []
    arr[float] pens = []
    int width = fntLayout(s, px, 0.0, gids, pens)

    // Pass one: every glyph's path at its pen position, one after
    // another in the same arrays, with each glyph's own box -- and the
    // union of the boxes, which is the mask.
    arr[float] pts = []
    arr[int] ends = []
    arr[int] glyphEnd = []       // per glyph: its last entry in `ends`, +1
    arr[int] boxes = []          // per glyph: x0, y0, x1, y1 in whole pixels
    float size = px.toFloat()
    bool any = false
    int mx0 = 0
    int my0 = 0
    int mx1 = 0
    int my1 = 0
    int i = 0
    while i < gids.length {
        int firstPt = pts.length
        int n = fntGlyphPath(gids[i], size, pens[i], 0.0, pts, ends)
        glyphEnd.push(ends.length)
        if n > 0 && pts.length > firstPt {
            float lx = pts[firstPt]
            float hx = lx
            float ly = pts[firstPt + 1]
            float hy = ly
            int k = firstPt
            while k < pts.length {
                if pts[k] < lx { lx = pts[k] }
                if pts[k] > hx { hx = pts[k] }
                if pts[k + 1] < ly { ly = pts[k + 1] }
                if pts[k + 1] > hy { hy = pts[k + 1] }
                k = k + 2
            }
            int bx0 = Math.floor(lx)
            int by0 = Math.floor(ly)
            int bx1 = Math.floor(hx) + 1
            int by1 = Math.floor(hy) + 1
            boxes.push(bx0)
            boxes.push(by0)
            boxes.push(bx1)
            boxes.push(by1)
            if !any || bx0 < mx0 { mx0 = bx0 }
            if !any || by0 < my0 { my0 = by0 }
            if !any || bx1 > mx1 { mx1 = bx1 }
            if !any || by1 > my1 { my1 = by1 }
            any = true
        } else {
            boxes.push(0)
            boxes.push(0)
            boxes.push(0)
            boxes.push(0)
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

    // Pass two: each glyph in its own box, added into the mask.
    int w = mx1 - mx0
    int h = my1 - my0
    arr[float] acc = []
    int a = 0
    while a < w * h {
        acc.push(0.0)
        a = a + 1
    }
    int firstEnd = 0
    int firstPoint = 0
    i = 0
    while i < gids.length {
        int lastEnd = glyphEnd[i]
        int gx0 = boxes[i * 4]
        int gy0 = boxes[(i * 4) + 1]
        int gw = boxes[(i * 4) + 2] - gx0
        int gh = boxes[(i * 4) + 3] - gy0
        if lastEnd > firstEnd && gw > 0 && gh > 0 {
            // This glyph's points, moved into its own box.
            int lastPoint = ends[lastEnd - 1]
            arr[float] local = []
            arr[int] localEnds = []
            int p = firstPoint
            while p < lastPoint {
                local.push(pts[p * 2] - gx0.toFloat())
                local.push(pts[(p * 2) + 1] - gy0.toFloat())
                p = p + 1
            }
            int e = firstEnd
            while e < lastEnd {
                localEnds.push(ends[e] - firstPoint)
                e = e + 1
            }
            rasEnsureCov(gw)
            if rasPathExtent(local, gh) {
                int row = RAS_Y0
                while row < RAS_Y1 {
                    rasRowCoverage(row, gw, local, localEnds, RAS_NONZERO)
                    int base = ((row + gy0 - my0) * w) + (gx0 - mx0)
                    int c = 0
                    while c < gw {
                        float v = RAS_COV[c]
                        if v > 0.0 {
                            if v > 1.0 { v = 1.0 }
                            acc[base + c] = acc[base + c] + v
                        }
                        c = c + 1
                    }
                    row = row + 1
                }
            }
            firstPoint = lastPoint
        }
        firstEnd = lastEnd
        i = i + 1
    }

    out.push(mx0)
    out.push(my0)
    out.push(w)
    out.push(h)
    a = 0
    while a < w * h {
        float v = acc[a]
        if v > 1.0 { v = 1.0 }
        out.push(Math.round(v * 255.0))
        a = a + 1
    }
    return out
}
