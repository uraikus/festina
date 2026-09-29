import raster.f

int W = 97
int H = 71
int SEED = 12345

int func rnd(n:int) {
    SEED = ((SEED * 1103515245) + 12345) % 2147483648
    return Math.floorDiv(SEED, 65536) % n
}

float func rf(lo:float, hi:float) {
    return lo + ((rnd(10000).toFloat() / 10000.0) * (hi - lo))
}

int func hashOf(px:arr[int]) {
    int h = 7
    int i = 0
    while i < px.length {
        h = ((h * 31) + px[i]) % 1000000007
        i = i + 1
    }
    return h
}

arr[int] px = rasNewSurface(W, H)
img a = blankImage(W, H)
rasFillRect(px, W, H, 0, 0, W, H, 40, 70, 100, 255)
rasFillRectImg(a, 0.0, 0.0, 97.0, 71.0, 40, 70, 100, 1.0)

arr[float] m = rasIdentity()
int n = 0
while n < 260 {
    int kind = n % 10
    int r = rnd(256)
    int g = rnd(256)
    int b = rnd(256)
    int al = 40 + rnd(216)
    float alf = al.toFloat() / 255.0
    arr[float] pts = []
    arr[int] ends = []
    arr[int] closed = [0]
    int count = 3 + rnd(5)
    if kind == 0 || kind == 1 || kind == 3 || kind == 5 || kind == 6 || kind == 7 || kind == 8 || kind == 9 {
        int k = 0
        while k < count {
            pts.push(rf(-10.0, 107.0))
            pts.push(rf(-10.0, 81.0))
            k = k + 1
        }
        ends.push(count)
    }
    if kind == 0 {
        rasFillPath(px, W, H, pts, ends, RAS_NONZERO, r, g, b, al)
        rasFillPathImg(a, pts, ends, RAS_NONZERO, r, g, b, alf)
    }
    if kind == 1 {
        rasFillPath(px, W, H, pts, ends, RAS_EVENODD, r, g, b, al)
        rasFillPathImg(a, pts, ends, RAS_EVENODD, r, g, b, alf)
    }
    if kind == 2 {
        arr[float] cp = []
        arr[int] ce = []
        float rad = rf(0.5, 30.0)
        float cx = rf(-5.0, 102.0)
        float cy = rf(-5.0, 76.0)
        rasCircle(cp, ce, cx, cy, rad)
        rasFillPath(px, W, H, cp, ce, RAS_NONZERO, r, g, b, al)
        rasFillPathImg(a, cp, ce, RAS_NONZERO, r, g, b, alf)
    }
    if kind == 3 {
        float wd = rf(0.5, 9.0)
        rasStrokePath(px, W, H, pts, ends, closed, wd, r, g, b, al)
        rasStrokePathImg(a, pts, ends, closed, wd, r, g, b, alf)
    }
    if kind == 4 {
        float x = rf(-10.0, 90.0)
        float y = rf(-10.0, 65.0)
        float w = rf(0.3, 40.0)
        float h = rf(0.3, 30.0)
        arr[float] rp = [x, y, x + w, y, x + w, y + h, x, y + h]
        arr[int] re = [4]
        rasFillPath(px, W, H, rp, re, RAS_NONZERO, r, g, b, al)
        rasFillRectImg(a, x, y, w, h, r, g, b, alf)
    }
    if kind == 5 {
        arr[float] lin = rasLinear(rf(0.0, 97.0), rf(0.0, 71.0), rf(0.0, 97.0), rf(0.0, 71.0), r, g, b, 255 - r, 255 - g, 255 - b, al)
        rasFillPathWith(px, W, H, pts, ends, RAS_NONZERO, lin)
    }
    if kind == 6 {
        arr[float] rad2 = rasRadial(rf(0.0, 97.0), rf(0.0, 71.0), rf(1.0, 50.0), r, g, b, 255 - r, 255 - g, 255 - b, al)
        rasFillPathWith(px, W, H, pts, ends, RAS_EVENODD, rad2)
    }
    if kind == 7 {
        arr[float] cp2 = []
        arr[int] ce2 = []
        rasCircle(cp2, ce2, rf(10.0, 87.0), rf(10.0, 61.0), rf(5.0, 30.0))
        arr[float] mask = rasClipMask(W, H, cp2, ce2, RAS_NONZERO)
        rasFillPathClip(px, W, H, pts, ends, RAS_NONZERO, r, g, b, al, mask)
        rasFillPathImgClip(a, pts, ends, RAS_NONZERO, r, g, b, alf, mask)
        rasStrokePathClip(px, W, H, pts, ends, closed, 3.0, g, b, r, al, mask)
        rasStrokePathImgClip(a, pts, ends, closed, 3.0, g, b, r, alf, mask)
    }
    if kind == 8 {
        arr[float] mt = rasIdentity()
        rasTranslate(mt, rf(0.0, 50.0), rf(0.0, 40.0))
        rasRotate(mt, rf(0.0, 360.0))
        rasScale(mt, rf(0.3, 1.5), rf(0.3, 1.5))
        rasFillPathT(px, W, H, pts, ends, RAS_NONZERO, rasSolid(r, g, b, al), mt)
        rasFillPathTImg(a, pts, ends, RAS_NONZERO, r, g, b, alf, mt)
        rasStrokePathT(px, W, H, pts, ends, closed, 2.0, rasSolid(b, r, g, al), mt)
        rasStrokePathTImg(a, pts, ends, closed, 2.0, b, r, g, alf, mt)
    }
    if kind == 9 {
        rasClearPath(px, W, H, pts, ends, RAS_NONZERO)
        rasFillRectImg(a, 0.0, 0.0, 1.0, 1.0, 0, 0, 0, 0.0)
    }
    n = n + 1
}

log(hashOf(px))
arr[int] ip = a.toPixels()
log(hashOf(ip))
img b = imageFromPixels(px, W, H)
log(b.save('golden_arr.png'))
log(a.save('golden_img.png'))
