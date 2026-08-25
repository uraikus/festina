// claude.md #37/#39/#40/#57: an `arr[img]` used as a layer stack --
// each layer is its own `img`, modified by calling the *same* drawing
// methods `graphics.f` calls on the canvas directly (drawRect/
// drawPixel/drawCircle/drawText -- api.md's "Drawing onto an image"),
// and one `renderFrame()` function composites every layer onto the
// canvas, in order, each frame. Build and run with:
//
//   ./bin/festina examples/layers.f -o layers_demo
//   ./layers_demo
//
// Needs a real (or virtual, e.g. Xvfb) X server -- $DISPLAY must be set.
//
// Four layers, each modified a different way, which is the point:
//
//   0  background  drawn once, never touched again -- a layer doesn't
//                   have to change every frame just because it's composited
//                   every frame.
//   1  stars        drawn once, then occasionally gains ONE more star
//                   (layers[LAYER_STARS].drawPixel(...)) -- a layer can be
//                   modified sparsely, not on every tick.
//   2  trail        gains one more dot every single frame
//                   (layers[LAYER_TRAIL].drawCircle(...)) at the bouncing
//                   ball's new position. An `img` has no "clear" the way
//                   the canvas does (api.md's Images section -- only
//                   drawRect/drawPixel/drawCircle/drawText), so drawing
//                   onto it repeatedly naturally accumulates into a trail,
//                   the same "can only add" constraint tic_tac_toe.f
//                   already relies on for the canvas itself.
//   3  hud          the one layer that DOES need to look like it's
//                   changing text every frame (a frame counter) -- since
//                   there's no way to erase old text from an img either,
//                   this layer is REPLACED wholesale each frame instead of
//                   drawn onto: `blankTemplate.clip(...)` makes a fresh,
//                   fully transparent img the same size as the canvas
//                   (clip() always returns a new img, api.md's own
//                   clip() row), which the fresh text is drawn onto before
//                   it takes layers[LAYER_HUD]'s place.

log('opening the canvas -- close the window to exit')

color skyColor = '#1a2b4a'
color groundColor = '#2f5c3a'
color starColor = '#f5f0d0'
color trailColor = 'orange'
color hudColor = 'white'
font hudFont = 'bold 16px sans-serif'

int LAYER_BACKGROUND = 0
int LAYER_STARS = 1
int LAYER_TRAIL = 2
int LAYER_HUD = 3

// A reusable "cookie cutter": grabbed while the canvas is still blank,
// so it's a fully transparent snapshot at the canvas's own size --
// clip()-ing a fresh copy of it later is how the HUD layer below gets
// replaced with a genuinely blank img each frame, with no external
// image asset needed at all (api.md's Images section: `img`'s own
// surface exists in full the moment the image does).
img blankTemplate = saveCanvas()

// `/` always returns float (api.md's Numbers section), so halfHeight
// is computed once, via the explicit float->int rounding Math.floor
// gives, rather than re-deriving it with a stray `/` at every use
// below.
int halfHeight = Math.floor(clientHeight / 2)

// Layer 0: background. Built once by drawing straight to the canvas,
// then captured into its own img and never touched again.
fillStyle(skyColor)
drawRect(0, 0, clientWidth, halfHeight)
fillStyle(groundColor)
drawRect(0, halfHeight, clientWidth, halfHeight)
img backgroundLayer = saveCanvas()
clearCanvas()

// Layer 1: stars. Also built by drawing to the canvas first, then
// captured -- this one gets ONE more star added later (see
// renderFrame below), so it changes, just not every frame.
fillStyle(starColor)
for int i = 0, i < 40, i++ {
    int sx = (i * 137) % clientWidth
    int sy = (i * 71) % halfHeight
    drawPixel(sx, sy)
}
img starsLayer = saveCanvas()
clearCanvas()

// Layers 2 and 3 start blank -- clip() always returns a NEW img, so
// clipping the whole canvas out of blankTemplate twice gives two
// independent transparent images, not two names for the same one
// (contrast with `img b = a`, which api.md's own Images section shows
// DOES alias).
arr[img] layers = [
    backgroundLayer,
    starsLayer,
    blankTemplate.clip(0, 0, clientWidth, clientHeight),
    blankTemplate.clip(0, 0, clientWidth, clientHeight),
]

int ballX = 100
int ballY = 100
int velX = 5
int velY = 4
int ballRadius = 8
int frameCount = 0
int totalFrames = 200
int intervalId = 0

void func renderFrame() {
    frameCount = frameCount + 1

    // Bounce the ball off all four edges.
    ballX = ballX + velX
    ballY = ballY + velY
    if ballX - ballRadius < 0 || ballX + ballRadius > clientWidth {
        velX = -velX
    }
    if ballY - ballRadius < 0 || ballY + ballRadius > clientHeight {
        velY = -velY
    }

    // Layer 2, modified every frame: one more dot at the ball's new
    // position, left behind permanently -- there's no way to erase it,
    // so the accumulation itself IS the trail.
    fillStyle(trailColor)
    layers[LAYER_TRAIL].drawCircle(ballX, ballY, 3)

    // Layer 1, modified sparsely: one new star every 15 frames, called
    // on the SAME img still sitting in the layer array -- proof that
    // layers[i].someMethod() reaches through to the real image, not a
    // copy of it.
    if frameCount % 15 == 0 {
        fillStyle(starColor)
        int extraX = (frameCount * 211) % clientWidth
        int extraY = (frameCount * 97) % halfHeight
        layers[LAYER_STARS].drawPixel(extraX, extraY)
    }

    // Layer 3, REPLACED every frame rather than drawn onto -- see this
    // file's own top comment for why. The old img this overwrites is
    // released the same way any other rebinding already is.
    layers[LAYER_HUD] = blankTemplate.clip(0, 0, clientWidth, clientHeight)
    fillStyle(hudColor)
    changeFont(hudFont)
    layers[LAYER_HUD].drawText(`Frame ${frameCount}/${totalFrames}`, 10, 20)

    // The overall Render function: composite every layer onto the
    // canvas, in array order (so later layers draw over earlier ones --
    // background, then stars, then the trail, then the HUD on top),
    // then present the frame.
    clearCanvas()
    for int i = 0, i < layers.length, i++ {
        drawImage(layers[i], 0, 0)
    }
    render()

    if frameCount >= totalFrames {
        log(`rendered ${frameCount} frames -- stopping (close the window to exit)`)
        clearInterval(intervalId)
    }
}

renderFrame()   // paint the first frame immediately, don't wait a full tick
intervalId = setInterval(renderFrame, 33)   // ~30 fps

on close() {
    log('window closing, goodbye')
}
