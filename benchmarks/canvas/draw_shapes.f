// claude.md #103: the canvas benchmark, Festina half.
//
// A frame of mixed 2D drawing -- filled rectangles, circles and text,
// with the fill colour changed between shapes so the runtime cannot
// batch them into one paint. This is what a 2D game's frame actually
// looks like, rather than one huge shape or one repeated call.
//
// Everything here paints an OFFSCREEN surface (claude.md #95); no
// display is involved and no window opens. That is deliberate and it is
// what makes the comparison against a browser canvas fair in the one
// direction that matters: both sides are measured drawing into a
// buffer, not presenting it to a screen.

int shapes = 20000

void func frame(n:int) {
    for int i = 0, i < n, i++ {
        int x = (i * 37) % 780
        int y = (i * 53) % 580
        fillStyle(i % 255, (i * 7) % 255, (i * 13) % 255)
        drawRect(x, y, 12, 9)
        drawCircle(x + 6, y + 4, 4)
    }
}

// Timed with now() around the draw loop alone, so the number is the
// same thing the browser side measures with performance.now(): the
// drawing, not the process around it. Subtracting a blank-frame run
// instead would fold in the PNG encode, and a busy image encodes far
// slower than an empty one -- which inflated Festina's figure by
// roughly a third the first time this was measured.
clearCanvas()
int start = now()
frame(shapes)
int elapsed = now() - start
log(elapsed)
log(saveCanvas('festina_canvas.png'))
