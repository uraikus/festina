// claude.md #37, #39, #40: a real on-screen canvas -- drawRect/
// drawCircle/drawText/drawImage, clientWidth/clientHeight, and the five
// event handlers (click/mouse/key/resize/close). Build and run with:
//
//   ./bin/festina examples/graphics.f -o graphics_demo
//   ./graphics_demo
//
// Needs a real (or virtual, e.g. Xvfb) X server -- $DISPLAY must be set.
// See api.md's Graphics section: the canvas opens automatically the
// first time something below actually needs it (the drawRect call),
// starts at 800x600, and everything draws in solid black -- there's no
// "clear" function, so this program only ever adds to the canvas, never
// erases (see examples/tic_tac_toe.f for a small game built entirely
// around that same constraint).

log('opening the canvas -- close the window to exit')

drawRect(50, 50, 200, 100)
drawCircle(400, 100, 50)
drawText('Festina graphics demo', 50, 220)

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
