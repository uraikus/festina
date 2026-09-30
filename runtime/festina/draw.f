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

// drawRect: the rectangle (x, y, w, h) in USER space under the matrix m
// (Cairo's order: xx, yx, xy, yy, x0, y0), filled and then stroked, each
// in its own colour and both at `alpha`. A negative w or h extends the
// other way, as cairo_rectangle's does.
void func festinaDrawRect(target:img, x:float, y:float, w:float, h:float,
                          fillOn:bool, fillSrc:arr[float],
                          borderOn:bool, br:int, bg:int, bb:int, width:float,
                          alpha:float, m:arr[float]) {
    arr[float] pts = [x, y, x + w, y, x + w, y + h, x, y + h]
    arr[int] ends = [4]
    arr[int] closed = [1]
    if fillOn { drawFill(target, pts, ends, fillSrc, alpha, m) }
    if borderOn {
        rasStrokePathTImg(target, pts, ends, closed, width, br, bg, bb, alpha, m)
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
void func festinaDrawCircle(target:img, cx:float, cy:float, radius:float,
                            fillOn:bool, fillSrc:arr[float],
                            borderOn:bool, br:int, bg:int, bb:int, width:float,
                            alpha:float, m:arr[float]) {
    arr[float] pts = []
    arr[int] ends = []
    arr[int] closed = [1]
    if radius <= 0.0 { return }
    drawCirclePoints(pts, ends, cx, cy, radius, m)
    if ends.length == 0 { return }
    if fillOn { drawFill(target, pts, ends, fillSrc, alpha, m) }
    if borderOn {
        rasStrokePathTImg(target, pts, ends, closed, width, br, bg, bb, alpha, m)
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
    rasClearImg(target, rasTransformPoints(m, pts), ends, RAS_NONZERO, kind != 2)
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
// other circle, so a circle stamped at an integer centre and one drawn
// through festinaDrawCircle agree.
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
        rasRowCoverage(row, size, pts, ends, RAS_NONZERO)
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
