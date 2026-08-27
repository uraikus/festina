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
// claude.md #89/#90/#91: fillStyle/borderColor/lineWidth/changeFont are
// set once and apply to every later draw, and measureTextWidth/
// measureTextHeight report metrics for the current font without needing
// a window at all.
//
// Colours and fonts are their own TYPES, declared once and resolved by
// the compiler: `color brand = '#4a90d9'` becomes a packed integer and
// `font title = 'bold 24px sans-serif'` becomes a static record in the
// binary's read-only data, so nothing parses a colour name or a font
// shorthand while this is drawing. To choose either at runtime, use
// fillStyle(r, g, b) or changeFont(px, style, family) -- see the swatch
// row near the bottom of this file.

log('opening the canvas -- close the window to exit')

// Declared once, used everywhere -- each of these is resolved by the
// compiler, not parsed while the program runs.
color brand = '#4a90d9'
color navy = 'navy'
color orange = 'orange'
color black = 'black'
color gray = 'gray'
color none = 'none'
font titleFont = 'bold 24px sans-serif'

// A filled shape with a border: fill colour, border colour, thickness.
fillStyle(brand)
borderColor(navy)
lineWidth(4)
drawRect(50, 50, 200, 100)

// A 'none' fill leaves the interior untouched, so borderColor alone
// draws an outline-only shape.
fillStyle(none)
borderColor(orange)
lineWidth(6)
drawCircle(400, 100, 50)

// Text is drawn in the current fill colour and font.
fillStyle(black)
changeFont(titleFont)
text title = 'Festina graphics demo'
drawText(title, 50, 220)

// Metrics let a program lay out relative to what it just drew -- here,
// a rule underlining the title at exactly its width.
borderColor(gray)
lineWidth(2)
fillStyle(none)
drawRect(50, 228, measureTextWidth(title), 2)
log(`title is ${measureTextWidth(title)}x${measureTextHeight(title)} px`)

// The explicit rgb form, for a colour that isn't known until it runs:
// a row of swatches fading from blue to red.
borderColor(none)
for int i = 0, i < 10, i++ {
    fillStyle(i * 25, 0, 255 - i * 25)
    drawRect(50 + i * 40, 280, 36, 36)
}

// claude.md #95: drawing paints an offscreen canvas -- render() is what
// puts it on screen, and the only call here that needs a display at all.
// A program that drew this and called saveCanvas() instead would run
// with no window and no event loop.
render()

log(`canvas started at ${clientWidth}x${clientHeight}`)

// claude.md #106: a click is a press and a release, and they are two
// separate events -- the same split claude.md #98 made for the keyboard.
// Holding the button down and moving before letting go is a drag, and
// the only way to express one is to see both ends of it: mouseDown
// reports where the button went down, mouseUp where it came back up.
on mouseDown(x:int, y:int, button:int) {
    log(`pressed at ${x}, ${y} (button ${button})`)
    drawCircle(x, y, 5)   // a small dot marking where the press landed
    render()              // show it
}

on mouseUp(x:int, y:int, button:int) {
    // The coordinates differ from the press above whenever the pointer
    // moved in between, which is exactly what makes a drag visible.
    log(`released at ${x}, ${y} (button ${button})`)
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

// claude.md #98: a press and a release are separate events, so a
// program can tell "the key is being held" from "the key was tapped" --
// which is what a movement key in a game actually needs. Both report
// the same name for the same physical key, and a held key fires one
// keyUp (when it is really let go), not one per auto-repeat.
on keyDown(key:text) {
    log(`key pressed: ${key}`)
}

on keyUp(key:text) {
    log(`key released: ${key}`)
}

on resize() {
    log(`resized to ${clientWidth}x${clientHeight}`)
}

on close() {
    log('window closing, goodbye')
}
