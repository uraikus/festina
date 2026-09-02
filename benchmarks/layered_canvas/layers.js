// claude.md #239: the layered-canvas benchmark, browser half.
//
// The SAME four layers as layers_threaded.f/layers_single.f -- identical
// coordinates, identical colors, identical 40,000 total draw calls --
// so this can never quietly drift out of step with the Festina side the
// way an independently-hand-copied second implementation could
// (draw_shapes.js's own precedent). Used two ways by
// run_layered_canvas_benchmark.py: once per function inside a Worker
// (the multi-threaded run) and all four back to back on one context
// (the single-threaded baseline) -- see that file's own two harness
// strings for how each is driven.

const W = 800, H = 600;

function drawSky(ctx) {
    const n = 8000;
    for (let i = 0; i < n; i++) {
        const x = (i * 29) % W;
        const y = (i * 17) % 300;
        ctx.fillStyle = (i % 2 === 0) ? '#0d1b2a' : '#1b3a5c';
        ctx.fillRect(x, y, 6, 6);
    }
}

function drawHill(ctx) {
    const n = 9000;
    for (let i = 0; i < n; i++) {
        const x = (i * 41) % W;
        const y = 300 + (i * 23) % 150;
        ctx.fillStyle = (i % 2 === 0) ? '#2d5a27' : '#3d6b35';
        ctx.beginPath();
        ctx.arc(x, y, 5, 0, Math.PI * 2);
        ctx.fill();
    }
}

function drawGround(ctx) {
    const n = 11000;
    for (let i = 0; i < n; i++) {
        const x = (i * 37) % W;
        const y = 450 + (i * 31) % 150;
        ctx.fillStyle = (i % 2 === 0) ? '#5a3d1f' : '#6b4a28';
        ctx.fillRect(x, y, 5, 5);
    }
}

function drawFx(ctx) {
    const n = 12000;
    const colors = ['#ffffff', '#cccccc', '#eeeeee'];
    for (let i = 0; i < n; i++) {
        const x = (i * 53) % W;
        const y = (i * 59) % H;
        ctx.fillStyle = colors[i % 3];
        ctx.beginPath();
        ctx.arc(x, y, 2, 0, Math.PI * 2);
        ctx.fill();
    }
}
