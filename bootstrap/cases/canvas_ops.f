// Drawing that opens no window.
//
// One mechanism, applied about twenty-five times: a canvas operation
// is a NAME, a runtime function and a fixed argument list, and nothing
// else. What makes it worth a case file of its own is the line the
// whole family is drawn along -- painting is not the same as
// presenting, and only the second needs a display.
//
// Measured, not assumed. The breakages live in `bootstrap/canary.py`
// and are re-run by tests/test_bootstrap_canary.py, which FAILS if
// this file stops making them visible.
//
//   1. **Drawing paints the OFFSCREEN canvas, so it opens no window.**
//      Every call below runs with no X server present -- which is what
//      lets a program draw a whole frame and save it as a PNG on a
//      machine that has no display at all. `render()` is the single
//      call that presents, and it is deliberately NOT here: it changes
//      main's own shape, and a port that emitted the call without that
//      change would be silently wrong rather than merely incomplete.
//
//   2. **A style setter records state and draws nothing**, so it opens
//      no window either -- for a reason one step further along than
//      the drawing calls' own. Setting a fill colour a program never
//      draws with should not open anything, and measuring text depends
//      only on the font.
//
//   3. **The image DECODER is registered in main's prologue** for any
//      program that emitted graphics code at all, drawing or not.
//      Before anything could decode an img column, and before any
//      thread could be spawned to race the store.
//
//   4. **A path argument is the caller's to free.** Cairo reads a PNG
//      path inline and copies the glyphs it draws; neither keeps the
//      pointer it was handed. A COMPUTED path is what measures this --
//      a literal is never freed anyway, so it would measure nothing.
//
//   5. **A float-typed operation takes a double, not an i64.** Rotate,
//      scale and alpha are the three, and they sit in the same table
//      as the twenty-odd integer ones: the argument types travel with
//      the function name rather than being assumed.
//
//      The two GRADIENT operations are in that same table and
//      deliberately absent from this file: their colour arguments are
//      `color`-typed, which this port has no type for yet, so writing
//      one here would make the file unported and take every mechanism
//      above down with it.

int frames = 0
text dir = '.'

// Mechanisms 1, 2 and 5. Every argument shape the table holds: none,
// one, two, three, four, six, and the float-typed ones.
int func paint() {
    clearCanvas()
    fillStyle(10, 20, 30)
    borderColor(40, 50, 60)
    lineWidth(3)

    drawRect(0, 0, 12, 9)
    drawCircle(6, 4, 4)
    drawPixel(2, 2)

    beginPath()
    moveTo(0, 0)
    lineTo(10, 10)
    curveTo(1, 2, 3, 4, 5, 6)
    closePath()
    fillPath()
    strokePath()

    saveState()
    translate(5, 5)
    rotate(1.5)
    scale(2.0, 2.0)
    fillAlpha(0.5)
    resetTransform()
    restoreState()

    clearRect(0, 0, 4, 4)
    clearCircle(2, 2, 1)
    clearPixel(1, 1)
    return 0
}

// Mechanism 2 again, with a value coming back. Measuring needs no
// canvas at all, which is why it is here rather than among the
// drawing calls.
int func measures() {
    drawText('hello', 1, 1)
    return measureTextWidth('hello') + measureTextHeight('hello')
}

// Mechanism 4. A COMPUTED path, because a literal one is never freed
// and so would measure nothing at all.
int func saves() {
    bool ok = saveCanvas(`${dir}/festina-case-canvas.png`)
    if ok { return 1 }
    return 0
}

log(paint())
log(measures())
log(saves())
log(frames)
