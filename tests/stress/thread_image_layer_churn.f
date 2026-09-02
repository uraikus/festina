// claude.md #234 (uraikus/festina#93): a worker thread painting its OWN
// layer -- rotated strokes through the image's transform, clears,
// drawImage onto itself -- while main paints layers of its own with
// the same calls, both using the per-call colour forms
// (`layer.drawRect(..., color)`), at volume.
//
// What this exists to catch: the per-call colour overrides used to
// save, overwrite and restore the GLOBAL fill/border state around every
// such draw -- a real data race (ThreadSanitizer, found the first time
// a thread and main both used the colour form at once) that made
// "each img method touches only that one private image" untrue for
// exactly the calls a layer-painting worker needs most. They compute
// the override locally now and only read the globals. Runs under
// scripts/thread_tsan_stress.sh (every thread_*.f) and, like every
// stress program, scripts/leak_stress.sh -- so a leaked per-image
// state stack or self-draw snapshot would show up here too.

int done = 0

thread painter {
    on message(t:thread, n:int) {
        color ink = 'red'
        color edge = 'blue'
        img layer = blankImage(64, 64)
        layer.saveState()
        layer.translate(32, 32)
        layer.rotate(n * 3.0)
        layer.drawRect(-8, -2, 16, 4, ink, edge)
        layer.drawCircle(0, 0, 3, ink)
        layer.restoreState()
        layer.clearCircle(32, 32, 2)
        layer.drawImage(layer, 1, 1)
        layer.drawImage(layer.clip(0, 0, 8, 8), 40, 40, 16, 16)
        layer.scale(2.0, 2.0)
        layer.drawPixel(2, 2, ink)
        layer.resetTransform()
        t.reply(layer.getPixelColor(32, 32) == null)
    }
}

void func onPainted(hole:bool) {
    done = done + 1
    if done == 600 {
        log(`painted ${done}`)
        // Replies reach main through its event loop, which nothing
        // else would ever end -- the same close(0) every reply-driven
        // stress program finishes with.
        close(0)
    }
}

color red = 'red'
color blue = 'blue'
int i = 0
while i < 600 {
    painter.postMessage(i).callback(onPainted)
    // main paints its own layer at the same time, colour forms included
    img mine = blankImage(32, 32)
    mine.translate(4, 4)
    mine.drawRect(0, 0, 8, 8, red, blue)
    mine.drawCircle(16, 16, 4, blue)
    mine.clearRect(0, 0, 2, 2)
    mine.drawImage(mine, 10, 10)
    i = i + 1
}
