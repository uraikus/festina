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
