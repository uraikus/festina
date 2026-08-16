// claude.md #103: the canvas benchmark, browser half.
//
// The same frame as draw_shapes.f -- identical shape count, coordinates
// and colours -- drawn into an HTML <canvas> inside headless Chromium
// (see run_canvas_benchmark.py, which evaluates this file's `frame`
// function in the page). A real browser's Skia-backed 2D context, not a
// Node canvas shim.
//
// The canvas is NOT attached to the document and nothing is ever
// presented, matching the Festina side's offscreen surface: both are
// timed drawing into a buffer.
//
// The getImageData at the end is load-bearing rather than incidental.
// Skia batches and defers, so timing the loop alone can return before
// any pixel exists -- measured at 50.5 ms without this readback against
// 70.2 ms with it. It reads one pixel purely to force the flush, and it
// is inside the timed region on purpose.
function frame(ctx, shapes) {
    const t0 = performance.now();
    for (let i = 0; i < shapes; i++) {
        const x = (i * 37) % 780;
        const y = (i * 53) % 580;
        ctx.fillStyle = `rgb(${i % 255},${(i * 7) % 255},${(i * 13) % 255})`;
        ctx.fillRect(x, y, 12, 9);
        ctx.beginPath();
        ctx.arc(x + 6, y + 4, 4, 0, Math.PI * 2);
        ctx.fill();
    }
    ctx.getImageData(0, 0, 1, 1);
    return performance.now() - t0;
}
