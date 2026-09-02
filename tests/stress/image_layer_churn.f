// claude.md #234 (uraikus/festina#93): an img as a self-contained
// drawing target -- its own transform + saveState()/restoreState()
// stack, clearing part of it to transparent, and drawing one image
// onto another -- at volume, headless (blankImage() needs no display).
//
// What could leak or corrupt here, and so what this deliberately
// churns: the per-image state stack is a malloc'd, growing array
// (freed with the image, or not at all); img.drawImage onto ITSELF
// snapshots the source first (a temporary surface freed right after,
// or not); an owning clip() result passed straight in as the source
// is released once painted (or leaks); and every clear/draw goes
// through a short-lived Cairo context on the image's own surface.
// Images are created and released inside the loop (function locals),
// so a leak scales with the iteration count and cannot hide.

color red = 'red'
color blue = 'blue'

int stamps = 0

void func paintLayer(n:int) {
    img layer = blankImage(48, 48)
    img brush = blankImage(8, 8)
    brush.drawRect(0, 0, 8, 8, red)

    // A rotated stroke, painted straight into the layer -- the exact
    // shape the issue's own oil-brush example uses.
    layer.saveState()
    layer.translate(24, 24)
    layer.rotate(n * 7.0)
    layer.drawRect(-6, -2, 12, 4, blue)
    layer.restoreState()

    // Nested saves -- the stack grows past its first allocation.
    int d = 0
    while d < 6 {
        layer.saveState()
        layer.translate(1, 1)
        d = d + 1
    }
    while d > 0 {
        layer.restoreState()
        d = d - 1
    }

    // Composite: a plain blit, a scaled one, one through a transform,
    // one from an OWNING clip() temporary, and one onto itself.
    layer.drawImage(brush, 2, 2)
    layer.drawImage(brush, 30, 30, 16, 16)
    layer.saveState()
    layer.translate(20, 4)
    layer.rotate(45.0)
    layer.drawImage(brush, 0, 0)
    layer.restoreState()
    layer.drawImage(brush.clip(0, 0, 4, 4), 40, 2)
    layer.drawImage(layer, 24, 0)

    // The eraser: every clear shape, through the transform and not.
    layer.clearCircle(24, 24, 3)
    layer.clearRect(0, 40, 48, 8)
    layer.clearPixel(2, 2)
    layer.translate(10, 10)
    layer.clearRect(0, 0, 4, 4)
    layer.resetTransform()
    if n % 50 == 0 {
        layer.clear()
    }
    layer.scale(2.0, 2.0)
    layer.drawPixel(1, 1, red)

    if layer.getPixelColor(2, 2) != null {
        stamps = stamps + 1
    }
}

int i = 0
while i < 400 {
    paintLayer(i)
    i = i + 1
}
log(stamps)
