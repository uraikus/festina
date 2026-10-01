// runtime.md phase 7, slice 4: the shapes the graphics runtime draws,
// drawn by raster.f.
//
// This is what the C runtime calls -- through the hook table in
// runtime/festina_draw_hooks.h, registered by a constructor, exactly as
// text.f is reached -- where it used to build a Cairo context, a path and
// a fill. The C side owns the state (the current colours, alpha, line
// width, gradient and transform) and hands each call what it needs; what
// is computed here is the shape's outline, its coverage and its stroke,
// and the blending goes back through img.__blendRow[Words] into the
// surface's own bytes.
//
// The target is an img. The canvas is one too as far as this goes: the
// runtime wraps its backing surface in a box for the length of the call.

import raster.f

// Fill the USER-space outline with the fill source `src` (see
// runtime/festina_draw_hooks.h) under the matrix m.
void func drawFill(target:img, pts:arr[float], ends:arr[int], src:arr[float],
                   alpha:float, m:arr[float]) {
    if src[0] == 0.0 {
        rasFillPathTImg(target, pts, ends, RAS_NONZERO, Math.round(src[1]), Math.round(src[2]),
                        Math.round(src[3]), alpha, m)
    } else {
        rasFillGradientImg(target, rasTransformPoints(m, pts), ends, RAS_NONZERO, src, alpha,
                           rasInvertMatrix(m), true)
    }
}

// The same without antialiasing, for a single pixel: a device pixel is in
// when its centre is inside the (transformed) shape.
void func drawFillNoAA(target:img, pts:arr[float], ends:arr[int], src:arr[float],
                       alpha:float, m:arr[float]) {
    arr[float] dev = rasTransformPoints(m, pts)
    if src[0] == 0.0 {
        rasFillNoAAImg(target, dev, ends, RAS_NONZERO, Math.round(src[1]), Math.round(src[2]),
                       Math.round(src[3]), alpha)
    } else {
        rasFillGradientImg(target, dev, ends, RAS_NONZERO, src, alpha, rasInvertMatrix(m), false)
    }
}

// drawFill for an outline that is one simple loop -- or two wound opposite
// ways -- so its coverage can be the exact area (raster.f, rasRowCoverageArea)
// and not the sampled one. Only the shapes drawn here may ask for it.
void func drawFillSimple(target:img, pts:arr[float], ends:arr[int], src:arr[float],
                         alpha:float, m:arr[float]) {
    RAS_SIMPLE = true
    drawFill(target, pts, ends, src, alpha, m)
    RAS_SIMPLE = false
}

// drawRect: the rectangle (x, y, w, h) in USER space under the matrix m
// (Cairo's order: xx, yx, xy, yy, x0, y0), filled and then stroked, each
// in its own colour and both at `alpha`. A negative w or h extends the
// other way, as cairo_rectangle's does.
void func festinaDrawRect(target:img, x:float, y:float, w:float, h:float,
                          fillOn:bool, fillSrc:arr[float],
                          borderOn:bool, br:int, bg:int, bb:int, width:float,
                          alpha:float, m:arr[float]) {
    // Only scaled and moved: a box on the surface, drawn analytically
    // (rasBoxImg) -- a solid fill and the border, which is the difference
    // of two boxes. A gradient fill, a rotation or a skew take the general
    // path below.
    if m[1] == 0.0 && m[2] == 0.0 && m[0] > 0.0 && m[3] > 0.0 && (fillSrc[0] == 0.0 || !fillOn) {
        float ax = (m[0] * x) + m[4]
        float bx = (m[0] * (x + w)) + m[4]
        float ay = (m[3] * y) + m[5]
        float by = (m[3] * (y + h)) + m[5]
        float x0 = rasMinF(ax, bx)
        float x1 = rasMaxF(ax, bx)
        float y0 = rasMinF(ay, by)
        float y1 = rasMaxF(ay, by)
        if fillOn {
            rasBoxImg(target, x0, y0, x1, y1, 0.0, 0.0, 0.0, 0.0, false,
                      Math.round(fillSrc[1]), Math.round(fillSrc[2]), Math.round(fillSrc[3]), alpha)
        }
        if borderOn {
            // The pen is width wide in USER space, so it is width * scale
            // on the surface, half of it either side of the edge.
            float hx = m[0] * width * 0.5
            float hy = m[3] * width * 0.5
            bool inner = (x0 + hx) < (x1 - hx) && (y0 + hy) < (y1 - hy)
            rasBoxImg(target, x0 - hx, y0 - hy, x1 + hx, y1 + hy,
                      x0 + hx, y0 + hy, x1 - hx, y1 - hy, inner, br, bg, bb, alpha)
        }
        return
    }
    arr[float] pts = [x, y, x + w, y, x + w, y + h, x, y + h]
    arr[int] ends = [4]
    arr[int] closed = [1]
    if fillOn { drawFillSimple(target, pts, ends, fillSrc, alpha, m) }
    if borderOn {
        // Stroked in USER space, so under any matrix the border is the
        // ring between the rectangle grown by half the pen and shrunk by
        // it (mitred corners make the offsets rectangles again), as long
        // as the pen leaves a hole. A degenerate or too-thin rectangle
        // goes to the general stroker.
        float hw = width * 0.5
        float nx = rasMinF(x, x + w)
        float ny = rasMinF(y, y + h)
        float aw = Math.abs(w)
        float ah = Math.abs(h)
        if aw > width && ah > width {
            arr[float] ring = [nx - hw, ny - hw, nx + aw + hw, ny - hw,
                               nx + aw + hw, ny + ah + hw, nx - hw, ny + ah + hw,
                               nx + hw, ny + hw, nx + hw, ny + ah - hw,
                               nx + aw - hw, ny + ah - hw, nx + aw - hw, ny + hw]
            arr[int] ringEnds = [4, 8]
            RAS_SIMPLE = true
            rasFillPathTImg(target, ring, ringEnds, RAS_NONZERO, br, bg, bb, alpha, m)
            RAS_SIMPLE = false
        } else {
            rasStrokePathTImg(target, pts, ends, closed, width, br, bg, bb, alpha, m)
        }
    }
}

// A circle's outline in USER space, cut into as many chords as its size
// ON THE SURFACE needs: the matrix is only consulted for how much it
// enlarges things, so a circle drawn under scale(4, 4) is not a coarse
// polygon. The radius is the one that puts the chords' area equal to the
// circle's, as rasCircle does.
void func drawCirclePoints(pts:arr[float], ends:arr[int], cx:float, cy:float, r:float,
                           m:arr[float]) {
    float stretch = rasMaxStretch(m)
    int n = rasArcSegments(r * stretch, RAS_TAU)
    float full = RAS_TAU / n.toFloat()
    float rb = r * Math.sqrt(RAS_TAU / (n.toFloat() * Math.sin(full)))
    int k = 0
    while k <= n {
        float a = (RAS_TAU * k.toFloat()) / n.toFloat()
        pts.push(cx + (rb * Math.cos(a)))
        pts.push(cy + (rb * Math.sin(a)))
        k = k + 1
    }
    ends.push(Math.floorDiv(pts.length, 2))
}

// drawCircle: centre (cx, cy), radius, the same way.
// Whether the matrix is a similarity -- a rotation and/or a UNIFORM scale,
// reflection allowed -- under which the stroke of a circle is again a ring
// of circles; any other matrix (a non-uniform scale, a skew) makes the
// stroke an ellipse's offset curve and needs the general stroker.
bool func drawIsSimilarity(m:arr[float]) {
    float a = (m[0] * m[0]) + (m[1] * m[1])
    float b = (m[2] * m[2]) + (m[3] * m[3])
    float dot = (m[0] * m[2]) + (m[1] * m[3])
    float scale = a + b
    if scale <= 0.0 { return false }
    return Math.abs(a - b) <= (scale * 0.000000001) && Math.abs(dot) <= (scale * 0.000000001)
}

void func festinaDrawCircle(target:img, cx:float, cy:float, radius:float,
                            fillOn:bool, fillSrc:arr[float],
                            borderOn:bool, br:int, bg:int, bb:int, width:float,
                            alpha:float, m:arr[float]) {
    if radius <= 0.0 { return }
    arr[float] pts = []
    arr[int] ends = []
    drawCirclePoints(pts, ends, cx, cy, radius, m)
    if ends.length == 0 { return }
    if fillOn { drawFillSimple(target, pts, ends, fillSrc, alpha, m) }
    if borderOn {
        float half = width * 0.5
        if drawIsSimilarity(m) {
            // The stroke is the ring between two circles: the outer one
            // counter-clockwise and the inner one reversed, filled
            // non-zero -- two polygons of n chords where the general
            // stroker makes a quad and a join for each of n segments.
            arr[float] ring = []
            arr[int] ringEnds = []
            drawCirclePoints(ring, ringEnds, cx, cy, radius + half, m)
            if radius > half {
                arr[float] inner = []
                arr[int] innerEnds = []
                drawCirclePoints(inner, innerEnds, cx, cy, radius - half, m)
                int k = Math.floorDiv(inner.length, 2) - 1
                while k >= 0 {
                    ring.push(inner[k * 2])
                    ring.push(inner[(k * 2) + 1])
                    k = k - 1
                }
                ringEnds.push(Math.floorDiv(ring.length, 2))
            }
            RAS_SIMPLE = true
            rasFillPathTImg(target, ring, ringEnds, RAS_NONZERO, br, bg, bb, alpha, m)
            RAS_SIMPLE = false
        } else {
            arr[int] closed = [1]
            rasStrokePathTImg(target, pts, ends, closed, width, br, bg, bb, alpha, m)
        }
    }
}

// drawPixel: one pixel, the unit square at (x, y), point-sampled.
void func festinaDrawPixel(target:img, x:float, y:float, fillOn:bool, fillSrc:arr[float],
                           alpha:float, m:arr[float]) {
    if !fillOn { return }
    arr[float] pts = [x, y, x + 1.0, y, x + 1.0, y + 1.0, x, y + 1.0]
    arr[int] ends = [4]
    drawFillNoAA(target, pts, ends, fillSrc, alpha, m)
}

// clearRect / clearCircle / clearPixel: kind 0 is the rectangle
// (a, b, c, d) = (x, y, w, h), 1 the circle (a, b, c) = (cx, cy, r), 2 the
// pixel (a, b) = (x, y) -- which, like drawPixel, is point-sampled.
void func festinaClear(target:img, kind:int, a:float, b:float, c:float, d:float, m:arr[float]) {
    arr[float] pts = []
    arr[int] ends = []
    if kind == 0 {
        pts = [a, b, a + c, b, a + c, b + d, a, b + d]
        ends = [4]
    }
    if kind == 1 && c > 0.0 { drawCirclePoints(pts, ends, a, b, c, m) }
    if kind == 2 {
        pts = [a, b, a + 1.0, b, a + 1.0, b + 1.0, a, b + 1.0]
        ends = [4]
    }
    if ends.length == 0 { return }
    RAS_SIMPLE = kind != 2
    rasClearImg(target, rasTransformPoints(m, pts), ends, RAS_NONZERO, kind != 2)
    RAS_SIMPLE = false
}

// A path the program built with beginPath / moveTo / lineTo / curveTo /
// closePath: `ops` is one code per segment -- 0 move, 1 line, 2 cubic
// curve, 3 close -- and `coords` the numbers they consume, in order (two
// for a move or a line, six for a curve, none for a close), all in USER
// space. Filled and/or stroked like a shape.
void func festinaDrawPath(target:img, ops:arr[int], coords:arr[float],
                          fillOn:bool, fillSrc:arr[float],
                          borderOn:bool, br:int, bg:int, bb:int, width:float,
                          alpha:float, m:arr[float]) {
    arr[float] pts = []
    arr[int] ends = []
    arr[int] closed = []
    int at = 0
    int start = 0            // where the current subpath begins, in pts
    float sx = 0.0
    float sy = 0.0
    float cx = 0.0
    float cy = 0.0
    bool open = false
    int i = 0
    while i < ops.length {
        int op = ops[i]
        if op == 0 {
            if open && Math.floorDiv(pts.length, 2) > start {
                ends.push(Math.floorDiv(pts.length, 2))
                closed.push(0)
            }
            start = Math.floorDiv(pts.length, 2)
            cx = coords[at]
            cy = coords[at + 1]
            sx = cx
            sy = cy
            pts.push(cx)
            pts.push(cy)
            open = true
            at = at + 2
        }
        if op == 1 {
            if !open {
                start = Math.floorDiv(pts.length, 2)
                pts.push(cx)
                pts.push(cy)
                open = true
            }
            cx = coords[at]
            cy = coords[at + 1]
            pts.push(cx)
            pts.push(cy)
            at = at + 2
        }
        if op == 2 {
            if !open {
                start = Math.floorDiv(pts.length, 2)
                pts.push(cx)
                pts.push(cy)
                open = true
            }
            rasCubicTo(pts, cx, cy, coords[at], coords[at + 1], coords[at + 2], coords[at + 3],
                       coords[at + 4], coords[at + 5])
            cx = coords[at + 4]
            cy = coords[at + 5]
            at = at + 6
        }
        if op == 3 {
            if open && Math.floorDiv(pts.length, 2) > start {
                ends.push(Math.floorDiv(pts.length, 2))
                closed.push(1)
            }
            open = false
            cx = sx
            cy = sy
        }
        i = i + 1
    }
    if open && Math.floorDiv(pts.length, 2) > start {
        ends.push(Math.floorDiv(pts.length, 2))
        closed.push(0)
    }
    if ends.length == 0 { return }
    if fillOn { drawFill(target, pts, ends, fillSrc, alpha, m) }
    if borderOn {
        rasStrokePathTImg(target, pts, ends, closed, width, br, bg, bb, alpha, m)
    }
}

// The coverage of a circle of integer radius r centred on the middle of a
// (2r + 2) x (2r + 2) square, one byte a pixel, row by row -- what the
// runtime's direct circle stamp (claude.md #104, #240) caches per radius
// and blends by hand. Made here, by the same rasteriser that draws every
// other circle -- exact-area coverage included -- so a circle stamped at an
// integer centre and one drawn through festinaDrawCircle agree.
arr[int] func festinaCircleMask(r:int) {
    int size = (r * 2) + 2
    arr[int] out = []
    arr[float] pts = []
    arr[int] ends = []
    arr[float] ident = rasIdentity()
    float mid = size.toFloat() / 2.0
    drawCirclePoints(pts, ends, mid, mid, r.toFloat(), ident)
    rasEnsureCov(size)
    int row = 0
    while row < size {
        rasRowCoverageArea(row, size, pts, ends)
        int c = 0
        while c < size {
            float v = RAS_COV[c]
            if v > 1.0 { v = 1.0 }
            out.push(Math.round(v * 255.0))
            c = c + 1
        }
        row = row + 1
    }
    return out
}
