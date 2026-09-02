// claude.md #239: the layered-canvas benchmark, Festina multi-threaded half.
//
// A complex scene built from four independent layers -- a sparse sky of
// stars, a band of hill texture, a band of ground texture, and a
// full-canvas foreground scatter of fine particles -- 40,000 draw calls
// total, the same order of magnitude as the single-threaded canvas
// benchmark's own 40,000 (draw_shapes.f), so the two are a fair
// apples-to-apples "same total work, 1 thread vs 4" comparison.
//
// Each layer is painted by its own real OS thread, into its own
// `img?` -- a manually-managed image (api.md's "T? -- manually-managed
// values") -- handed to that thread by PostMessage. Crossing a `thread`
// boundary shares an `img?`'s reference instead of cloning it (unlike
// every other postMessage argument type, which is always deep-copied):
// the worker draws directly into the SAME pixel buffer main already
// holds, so there is no encode/decode/copy of a 800x600 ARGB32 surface
// (1.92MB) on the way there or back -- only the bare pointer crosses the
// thread boundary. That is what "maximal speed" means here, and it is
// the whole reason this benchmark uses `img?` instead of plain `img`.
//
// Each thread's own drawing calls the per-call COLOUR-argument form
// (`msg.drawRect(x, y, w, h, color)`, not the two-step `fillStyle(...)`
// then `drawRect(...)`) -- api.md's own claude.md #234 fix exists
// specifically so a worker painting its own image never reads or writes
// the shared global fill-colour state, which four threads painting
// concurrently would otherwise race on.
//
// `NAME.drain()`, not `.reply()`/`.callback()`: nothing needs to come
// BACK from a worker here (the shared img? already reflects its work
// the instant drain() returns -- see the T? doc's own "no clone
// happened" proof), so this is a plain fire-and-wait, the cheapest
// correct way to say "block until this layer is painted."
//
// Times the fan-out + drain + composite only, the same "draw loop
// alone" discipline draw_shapes.f already uses -- the PNG encode
// (saveCanvas) happens after the timer stops.

thread skyThread {
    color skyA = '#0d1b2a'
    color skyB = '#1b3a5c'
    on message(worker:thread, msg:img?) {
        int n = 8000
        int i = 0
        while i < n {
            int x = (i * 29) % 800
            int y = (i * 17) % 300
            if i % 2 == 0 {
                msg.drawRect(x, y, 6, 6, skyA)
            } else {
                msg.drawRect(x, y, 6, 6, skyB)
            }
            i = i + 1
        }
    }
}

thread hillThread {
    color hillA = '#2d5a27'
    color hillB = '#3d6b35'
    on message(worker:thread, msg:img?) {
        int n = 9000
        int i = 0
        while i < n {
            int x = (i * 41) % 800
            int y = 300 + (i * 23) % 150
            if i % 2 == 0 {
                msg.drawCircle(x, y, 5, hillA)
            } else {
                msg.drawCircle(x, y, 5, hillB)
            }
            i = i + 1
        }
    }
}

thread groundThread {
    color groundA = '#5a3d1f'
    color groundB = '#6b4a28'
    on message(worker:thread, msg:img?) {
        int n = 11000
        int i = 0
        while i < n {
            int x = (i * 37) % 800
            int y = 450 + (i * 31) % 150
            if i % 2 == 0 {
                msg.drawRect(x, y, 5, 5, groundA)
            } else {
                msg.drawRect(x, y, 5, 5, groundB)
            }
            i = i + 1
        }
    }
}

thread fxThread {
    color fxA = '#ffffff'
    color fxB = '#cccccc'
    color fxC = '#eeeeee'
    on message(worker:thread, msg:img?) {
        int n = 12000
        int i = 0
        while i < n {
            int x = (i * 53) % 800
            int y = (i * 59) % 600
            int c = i % 3
            if c == 0 {
                msg.drawCircle(x, y, 2, fxA)
            } else if c == 1 {
                msg.drawCircle(x, y, 2, fxB)
            } else {
                msg.drawCircle(x, y, 2, fxC)
            }
            i = i + 1
        }
    }
}

img? sky = blankImage(800, 600)
img? hill = blankImage(800, 600)
img? ground = blankImage(800, 600)
img? fx = blankImage(800, 600)

int start = now()

skyThread.postMessage(sky)
hillThread.postMessage(hill)
groundThread.postMessage(ground)
fxThread.postMessage(fx)

skyThread.drain()
hillThread.drain()
groundThread.drain()
fxThread.drain()

// The top-level canvas drawImage() builtin takes a plain img -- img?
// is "a genuinely different type from img, not a looser version of it"
// (api.md's own T? section), so compositing needs one real, ordinary
// img per layer. .clip(0, 0, 800, 600) on the whole surface is exactly
// that: a single 800x600 ARGB32 copy (1.92MB) done ONCE per layer here
// on main, after the parallel drawing is already finished -- nothing
// like the 40,000 individual draw calls that would have cost the same
// copy-on-every-call if img? hadn't kept them all thread-local.
img skyPlain = sky.clip(0, 0, 800, 600)
img hillPlain = hill.clip(0, 0, 800, 600)
img groundPlain = ground.clip(0, 0, 800, 600)
img fxPlain = fx.clip(0, 0, 800, 600)

clearCanvas()
drawImage(skyPlain, 0, 0)
drawImage(hillPlain, 0, 0)
drawImage(groundPlain, 0, 0)
drawImage(fxPlain, 0, 0)

int elapsed = now() - start
log(elapsed)
log(saveCanvas('festina_layers_mt.png'))

free sky
free hill
free ground
free fx
free skyPlain
free hillPlain
free groundPlain
free fxPlain

close(0)
