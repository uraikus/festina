// claude.md #239: the layered-canvas benchmark's single-threaded Festina
// baseline -- the SAME four layers, SAME coordinates, SAME colors,
// SAME 40,000 total draw calls as layers_threaded.f, just painted one
// after another on the one thread the program already has, instead of
// fanned out across four. Threading is the only variable this isolates:
// every draw-call formula below is copy-identical to its threaded
// counterpart (thread bodies can't call an ordinary top-level func, so
// the two files can't share these as one definition either way -- see
// api.md's Isolation section), which is also what lets the runner
// compare the two PNGs byte-for-byte rather than merely approximately:
// Cairo is deterministic, so "the only difference is which thread(s)
// drew it" should mean pixel-identical output.
//
// Each function draws onto a plain `img` (no `?` -- nothing here ever
// crosses a thread boundary, so there is no manual-management story to
// opt into).

void func drawSky(target:img) {
    color skyA = '#0d1b2a'
    color skyB = '#1b3a5c'
    int n = 8000
    int i = 0
    while i < n {
        int x = (i * 29) % 800
        int y = (i * 17) % 300
        if i % 2 == 0 {
            target.drawRect(x, y, 6, 6, skyA)
        } else {
            target.drawRect(x, y, 6, 6, skyB)
        }
        i = i + 1
    }
}

void func drawHill(target:img) {
    color hillA = '#2d5a27'
    color hillB = '#3d6b35'
    int n = 9000
    int i = 0
    while i < n {
        int x = (i * 41) % 800
        int y = 300 + (i * 23) % 150
        if i % 2 == 0 {
            target.drawCircle(x, y, 5, hillA)
        } else {
            target.drawCircle(x, y, 5, hillB)
        }
        i = i + 1
    }
}

void func drawGround(target:img) {
    color groundA = '#5a3d1f'
    color groundB = '#6b4a28'
    int n = 11000
    int i = 0
    while i < n {
        int x = (i * 37) % 800
        int y = 450 + (i * 31) % 150
        if i % 2 == 0 {
            target.drawRect(x, y, 5, 5, groundA)
        } else {
            target.drawRect(x, y, 5, 5, groundB)
        }
        i = i + 1
    }
}

void func drawFx(target:img) {
    color fxA = '#ffffff'
    color fxB = '#cccccc'
    color fxC = '#eeeeee'
    int n = 12000
    int i = 0
    while i < n {
        int x = (i * 53) % 800
        int y = (i * 59) % 600
        int c = i % 3
        if c == 0 {
            target.drawCircle(x, y, 2, fxA)
        } else if c == 1 {
            target.drawCircle(x, y, 2, fxB)
        } else {
            target.drawCircle(x, y, 2, fxC)
        }
        i = i + 1
    }
}

img sky = blankImage(800, 600)
img hill = blankImage(800, 600)
img ground = blankImage(800, 600)
img fx = blankImage(800, 600)

int start = now()

drawSky(sky)
drawHill(hill)
drawGround(ground)
drawFx(fx)

clearCanvas()
drawImage(sky, 0, 0)
drawImage(hill, 0, 0)
drawImage(ground, 0, 0)
drawImage(fx, 0, 0)

int elapsed = now() - start
log(elapsed)
log(saveCanvas('festina_layers_single.png'))
