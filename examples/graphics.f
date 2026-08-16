// claude.md #37, #39, #40: a real on-screen canvas -- drawRect/
// drawCircle/drawText/drawImage, clientWidth/clientHeight, and the five
// event handlers (click/mouse/key/resize/close). Build and run with:
//
//   ./bin/festina examples/graphics.f -o graphics_demo
//   ./graphics_demo
//
// Needs a real (or virtual, e.g. Xvfb) X server -- $DISPLAY must be set.
// See api.md's Graphics section: the canvas opens automatically the
// first time something below actually needs it (the drawRect call) and
// starts at 800x600. There's no "clear" function, so this program only
// ever adds to the canvas, never erases (see examples/tic_tac_toe.f for
// a small game built entirely around that same constraint).
//
// claude.md #89: fillStyle/borderColor/lineWidth/font are set once and
// apply to every later draw, and measureTextWidth/measureTextHeight
// report metrics for the current font without needing a window at all.

log('opening the canvas -- close the window to exit')

// A filled shape with a border: fill colour, border colour, thickness.
fillStyle('#4a90d9')
borderColor('navy')
lineWidth(4)
drawRect(50, 50, 200, 100)

// fillStyle('none') leaves the interior untouched, so borderColor alone
// draws an outline-only shape.
fillStyle('none')
borderColor('orange')
lineWidth(6)
drawCircle(400, 100, 50)

// Text is drawn in the current fill colour and font.
fillStyle('black')
font('bold 24px sans-serif')
text title = 'Festina graphics demo'
drawText(title, 50, 220)

// Metrics let a program lay out relative to what it just drew -- here,
// a rule underlining the title at exactly its width.
borderColor('gray')
lineWidth(2)
fillStyle('none')
drawRect(50, 228, measureTextWidth(title), 2)
log(`title is ${measureTextWidth(title)}x${measureTextHeight(title)} px`)

log(`canvas started at ${clientWidth}x${clientHeight}`)

on click(x:int, y:int) {
    log(`clicked at ${x}, ${y}`)
    drawCircle(x, y, 5)   // a small dot marking every click
}

on mouse(x:int, y:int) {
    // Fires continuously while the mouse moves over the canvas --
    // logging every single move would be noisy, so this only reacts
    // near the drawn rectangle's corner as a lightweight example of
    // reading the position at all.
    if x > 45 && x < 55 && y > 45 && y < 55 {
        log('near the rectangle\'s top-left corner')
    }
}

on key(key:text) {
    log(`key pressed: ${key}`)
}

on resize() {
    log(`resized to ${clientWidth}x${clientHeight}`)
}

on close() {
    log('window closing, goodbye')
}
