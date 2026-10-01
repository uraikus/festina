"""runtime.md phase 7, slice 4: the exact-area coverage (`rasRowCoverageArea`).

draw.f draws its own simple outlines -- a circle, a ring, a parallelogram
-- with the area each edge cuts out of each pixel, summed along the row,
instead of sixteen sampled lines through it. That is only worth having if
it is right, and "looks like the sampled one" is a weak oracle (it is off
by up to a sixteenth itself), so it is checked three ways that do not
lean on the sampler:

  * axis-aligned rectangles, whose coverage of every pixel is the product
    of two overlaps -- exact to rounding, with the rectangle running off
    each side of the surface, either winding, and at whole and half pixels;
  * the area identity: for any shape inside the surface the coverage
    summed over every pixel IS the polygon's area (shoelace), circles and
    rings (outer minus the hole) and rotated rectangles included;
  * the sampled rasteriser, as a bound only: never further than a
    sixteenth-and-a-bit away anywhere.

The numbers come out of the program as text and are compared here.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.test_raster import _run, _with_raster   # noqa: E402

_PRELUDE = r"""
import raster.f
int SEED = 4242
int func rnd(n:int) {
    SEED = ((SEED * 1103515245) + 12345) % 2147483648
    return Math.floorDiv(SEED, 65536) % n
}
float func rf(lo:float, hi:float) {
    return lo + ((rnd(10000).toFloat() / 10000.0) * (hi - lo))
}
float func overlap(a0:float, a1:float, b0:float, b1:float) {
    float lo = rasMaxF(a0, b0)
    float hi = rasMinF(a1, b1)
    if hi > lo { return hi - lo }
    return 0.0
}
float func shoelace(pts:arr[float], ends:arr[int]) {
    float total = 0.0
    int from = 0
    int s = 0
    while s < ends.length {
        int to = ends[s]
        int p = from
        while p < to {
            int q = p + 1
            if q == to { q = from }
            total = total + ((pts[p * 2] * pts[(q * 2) + 1]) - (pts[q * 2] * pts[(p * 2) + 1]))
            p = p + 1
        }
        from = to
        s = s + 1
    }
    return Math.abs(total) / 2.0
}
int W = 37
int H = 29
rasEnsureCov(W + 4)
"""


def _numbers(tmp_path, cli_mod, body):
    _with_raster(tmp_path)
    out = _run(tmp_path, cli_mod, _PRELUDE + body).split()
    return [float(v) for v in out]


def test_rectangles_are_the_product_of_their_overlaps(tmp_path, cli_mod):
    """Every pixel of 400 rectangles -- fractional corners, whole and
    half pixels among them, running off any side or all of them, wound
    either way -- is exactly the area the rectangle shares with it."""
    worst, = _numbers(tmp_path, cli_mod, r"""
float worst = 0.0
int t = 0
while t < 400 {
    float x = rf(-12.0, 45.0)
    float y = rf(-10.0, 36.0)
    float w = rf(0.2, 40.0)
    float h = rf(0.2, 30.0)
    if t % 5 == 0 { x = (rnd(50) - 8).toFloat() + (rnd(2).toFloat() * 0.5) }
    if t % 5 == 0 { y = (rnd(40) - 6).toFloat() + (rnd(2).toFloat() * 0.5) }
    if t % 7 == 0 { w = (1 + rnd(30)).toFloat() }
    arr[float] pts = [x, y, x + w, y, x + w, y + h, x, y + h]
    if t % 2 == 1 { pts = [x, y, x, y + h, x + w, y + h, x + w, y] }
    arr[int] ends = [4]
    int row = 0
    while row < H {
        rasRowCoverageArea(row, W, pts, ends)
        int c = 0
        while c < W {
            float want = overlap(x, x + w, c.toFloat(), c.toFloat() + 1.0) * overlap(y, y + h, row.toFloat(), row.toFloat() + 1.0)
            float d = Math.abs(want - RAS_COV[c])
            if d > worst { worst = d }
            c = c + 1
        }
        row = row + 1
    }
    t = t + 1
}
log(worst)
""")
    assert worst < 1e-9, worst


def test_the_coverage_summed_is_the_shapes_area(tmp_path, cli_mod):
    """Circles, rings and rotated rectangles that sit inside the surface:
    all the coverage there is, added up, is the outline's area."""
    worst, count = _numbers(tmp_path, cli_mod, r"""
float worst = 0.0
int count = 0
int t = 0
while t < 240 {
    arr[float] pts = []
    arr[int] ends = []
    int kind = t % 3
    float cx = rf(12.0, 25.0)
    float cy = rf(10.0, 19.0)
    if kind == 0 {
        rasCircle(pts, ends, cx, cy, rf(0.6, 9.0))
    }
    if kind == 1 {
        float ro = rf(3.0, 9.0)
        rasCircle(pts, ends, cx, cy, ro)
        arr[float] ip = []
        arr[int] ie = []
        rasCircle(ip, ie, cx, cy, ro - rf(0.4, 2.5))
        int k = ie[0] - 1
        int outerN = ends[0]
        while k >= 0 {
            pts.push(ip[k * 2])
            pts.push(ip[(k * 2) + 1])
            k = k - 1
        }
        ends.push(outerN + ie[0])
    }
    if kind == 2 {
        arr[float] mt = rasIdentity()
        rasTranslate(mt, cx, cy)
        rasRotate(mt, rf(0.0, 360.0))
        float w = rf(1.0, 12.0)
        float h = rf(1.0, 9.0)
        arr[float] box = [-w * 0.5, -h * 0.5, w * 0.5, -h * 0.5, w * 0.5, h * 0.5, -w * 0.5, h * 0.5]
        pts = rasTransformPoints(mt, box)
        ends = [4]
    }
    float total = 0.0
    int row = 0
    while row < H {
        rasRowCoverageArea(row, W, pts, ends)
        int c = RAS_LO
        while c < RAS_HI {
            total = total + RAS_COV[c]
            c = c + 1
        }
        row = row + 1
    }
    float want = shoelace(pts, ends)
    if kind == 1 {
        // the hole is wound the other way: outer minus inner
        arr[float] outer = []
        arr[int] oe = [ends[0]]
        int q = 0
        while q < ends[0] * 2 {
            outer.push(pts[q])
            q = q + 1
        }
        arr[float] inner = []
        arr[int] ie2 = [ends[1] - ends[0]]
        while q < pts.length {
            inner.push(pts[q])
            q = q + 1
        }
        want = shoelace(outer, oe) - shoelace(inner, ie2)
    }
    float d = Math.abs(total - want)
    if d > worst { worst = d }
    count = count + 1
    t = t + 1
}
log(worst)
log(count)
""")
    assert count == 240
    assert worst < 1e-6, worst


def test_never_far_from_the_sampled_rasteriser(tmp_path, cli_mod):
    """The sampled coverage takes sixteen lines a row, so it is within
    about a sixteenth of the truth; the exact one must stay within that
    of it -- including for shapes cut by every side of the surface."""
    worst, = _numbers(tmp_path, cli_mod, r"""
float worst = 0.0
int t = 0
while t < 200 {
    arr[float] pts = []
    arr[int] ends = []
    if t % 2 == 0 {
        rasCircle(pts, ends, rf(-8.0, 45.0), rf(-8.0, 37.0), rf(0.6, 22.0))
    } else {
        arr[float] mt = rasIdentity()
        rasTranslate(mt, rf(-5.0, 42.0), rf(-5.0, 34.0))
        rasRotate(mt, rf(0.0, 360.0))
        float w = rf(1.0, 30.0)
        float h = rf(1.0, 20.0)
        pts = rasTransformPoints(mt, [0.0, 0.0, w, 0.0, w, h, 0.0, h])
        ends = [4]
    }
    int row = 0
    while row < H {
        rasRowCoverage(row, W, pts, ends, RAS_NONZERO)
        arr[float] sampled = []
        int c = 0
        while c < W {
            sampled.push(RAS_COV[c])
            c = c + 1
        }
        rasRowCoverageArea(row, W, pts, ends)
        c = 0
        while c < W {
            float d = Math.abs(sampled[c] - RAS_COV[c])
            if d > worst { worst = d }
            c = c + 1
        }
        row = row + 1
    }
    t = t + 1
}
log(worst)
""")
    assert worst < 0.08, worst


def test_a_shape_that_runs_off_the_right_is_covered_to_the_last_column(tmp_path, cli_mod):
    """The edge that would close the shape is beyond the surface and is
    dropped; the interior must still run to the last column, and the next
    row (a different shape's) must start from nothing."""
    out = _numbers(tmp_path, cli_mod, r"""
arr[float] big = [5.0, 2.0, 80.0, 2.0, 80.0, 9.0, 5.0, 9.0]
arr[int] be = [4]
rasRowCoverageArea(4, W, big, be)
log(RAS_COV[4])
log(RAS_COV[5])
log(RAS_COV[W - 1])
arr[float] small = [1.0, 12.0, 3.0, 12.0, 3.0, 14.0, 1.0, 14.0]
arr[int] se = [4]
rasRowCoverageArea(4, W, small, se)
log(RAS_COV[1])
log(RAS_COV[10])
log(RAS_COV[W - 1])
rasRowCoverageArea(12, W, small, se)
log(RAS_COV[0])
log(RAS_COV[1])
log(RAS_COV[2])
log(RAS_COV[3])
""")
    assert out == [0.0, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 0.0]
