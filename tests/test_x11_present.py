"""runtime.md phase 7, slice 7: presenting the canvas on an X11 window.

The canvas is a plain surface; the window shows it. This file pins what
the window shows, from a live program under Xvfb, read back with XGetImage.
"""
import os
import shutil
import subprocess
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.x11_pixels import window_pixels   # noqa: E402

_HEAD = """color red = '#c83232'
color blu = '#3232c8'
color ink = '#203040'
color white = '#ffffff'
color grn = '#32c832'
"""


def _canvas_over_white(png_path):
    """The saved canvas (straight RGBA) as the window should show it:
    composited over the window's white ground."""
    from tests.test_codegen import _png_raw
    w, h, stride, bpp, out = _png_raw(png_path)
    rows = []
    for y in range(h):
        row = []
        for x in range(w):
            o = y * stride + x * bpp
            r, g, b = out[o:o + 3]
            a = out[o + 3] if bpp == 4 else 255
            row.append(tuple(round(c * a / 255) + 255 - a for c in (r, g, b)))
        rows.append(row)
    return w, h, rows


def _worst(a, b):
    (w, h, pa), (_, _, pb) = a, b
    worst, differing = 0, 0
    for y in range(h):
        for x in range(w):
            d = max(abs(p - q) for p, q in zip(pa[y][x], pb[y][x]))
            if d:
                differing += 1
                worst = max(worst, d)
    return worst, differing


def _find_window(display, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        out = subprocess.run(["xdotool", "search", "--name", "Festina"],
                             env=dict(os.environ, DISPLAY=display),
                             capture_output=True, text=True).stdout.split()
        if out:
            return out[-1]
        time.sleep(0.1)
    raise AssertionError("no window appeared")


@pytest.fixture
def shown(tmp_path, cli_mod, x_display):
    """Run a Festina program that ends in render() and hand back the
    pixels of its window once it is up; the process is stopped after."""
    from tests.conftest import _require_c_compiler
    if not shutil.which("xdotool"):
        pytest.skip("xdotool isn't installed -- needed to find the window")
    procs = []

    def _show(source, settle=0.6):
        src = tmp_path / "main.f"
        src.write_text(_HEAD + source, encoding="utf-8")
        exe = tmp_path / "program"
        cli_mod.compile_file(str(src), str(exe), cc=_require_c_compiler())
        env = dict(os.environ, DISPLAY=x_display)
        proc = subprocess.Popen([str(exe)], cwd=tmp_path, env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        procs.append(proc)
        wid = _find_window(x_display)
        time.sleep(settle)
        return window_pixels(x_display, wid)

    yield _show
    for p in procs:
        p.terminate()
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()


_SCENE = """clearCanvas()
fillStyle(white)
drawRect(0, 0, 800, 600)
fillStyle(red)
borderColor(ink)
lineWidth(3)
drawRect(10, 10, 80, 50)
fillAlpha(0.5)
fillStyle(blu)
drawCircle(150, 60, 30)
fillAlpha(1.0)
fillStyle(grn)
borderColor(ink)
drawCircle(250, 60, 25)
render()
"""


def test_a_window_shows_what_was_drawn(shown):
    w, h, px = shown(_SCENE)
    assert (w, h) == (800, 600)
    assert px[35][50] == (200, 50, 50)          # inside the red rectangle
    assert px[300][400] == (255, 255, 255)      # the white ground
    assert px[60][150][2] > px[60][150][0]      # the translucent blue circle, over white
    # a busy picture
    assert len({px[y][x] for y in range(0, 120) for x in range(0, 300)}) > 20


# A canvas of every kind of pixel: opaque, translucent over nothing,
# transparent, and a hole cleared out of an opaque shape.
_KINDS = """fillStyle(red)
drawRect(10, 10, 120, 80)
fillAlpha(0.5)
fillStyle(blu)
drawCircle(200, 60, 40)
fillAlpha(0.2)
fillStyle(grn)
drawRect(260, 20, 100, 70)
fillAlpha(1.0)
fillStyle(ink)
drawRect(10, 120, 120, 80)
clearCircle(70, 160, 25)
fillAlpha(0.8)
drawCircle(300, 160, 30)
fillAlpha(1.0)
log(saveCanvas('canvas.png'))
render()
"""


def test_the_window_is_the_canvas_over_white(shown, tmp_path):
    """Whatever the canvas holds -- opaque, translucent or transparent --
    the window shows it composited over its white ground, to within one
    level (the canvas is saved as straight RGBA, which rounds)."""
    window = shown(_KINDS)
    expected = _canvas_over_white(str(tmp_path / "canvas.png"))
    assert (window[0], window[1]) == (expected[0], expected[1]) == (800, 600)
    worst, differing = _worst(window, expected)
    assert worst <= 1, (worst, differing)
    px = window[2]
    assert px[160][70] == (255, 255, 255)           # the hole, transparent: white
    assert px[300][700] == (255, 255, 255)          # untouched canvas: white
    assert px[50][200] != (255, 255, 255)           # translucent blue over white
    assert len({tuple(p) for row in px for p in row}) > 15


def test_a_cleared_canvas_shows_white_not_the_last_frame(shown, x_display, tmp_path):
    """The window used to be painted OVER what it already held, so a canvas
    cleared to transparent left the previous frame on screen. A click
    clears it and draws one small thing: everything else must go white."""
    source = """fillStyle(red)
drawRect(0, 0, 800, 600)
on mouseDown(x:int, y:int, button:int) {
    clearCanvas()
    fillStyle(blu)
    drawRect(100, 100, 50, 50)
    render()
}
render()
"""
    from tests.conftest import _require_c_compiler
    window = shown(source)
    assert window[2][300][400] == (200, 50, 50)     # the red frame
    wid = _find_window(x_display)
    env = dict(os.environ, DISPLAY=x_display)
    subprocess.run(["xdotool", "click", "--window", wid, "1"],
                   env=env, check=True)
    deadline = time.time() + 10
    while time.time() < deadline:
        _, _, px = window_pixels(x_display, wid)
        if px[300][400] != (200, 50, 50):
            break
        time.sleep(0.1)
    assert px[300][400] == (255, 255, 255)          # not the stale red
    assert px[120][120] == (50, 50, 200)            # the new square


def test_the_window_is_painted_again_after_it_is_uncovered(shown, x_display):
    """Unmapping and mapping a window makes the server send Expose and
    forget the contents (there is no backing store); the runtime paints it
    again from the canvas."""
    window = shown(_KINDS)
    wid = _find_window(x_display)
    env = dict(os.environ, DISPLAY=x_display)
    subprocess.run(["xdotool", "windowunmap", wid], env=env, check=True)
    time.sleep(0.3)
    subprocess.run(["xdotool", "windowmap", wid], env=env, check=True)
    deadline = time.time() + 10
    again = None
    while time.time() < deadline:
        again = window_pixels(x_display, wid)
        if _worst(window, again)[0] <= 1:
            break
        time.sleep(0.1)
    assert _worst(window, again)[0] <= 1


def test_a_resize_presents_the_new_canvas_size(shown, x_display):
    """The window is resized; the program's own handler fills the canvas
    (which has been resized with it) and the whole new window shows it."""
    source = """on resize() {
    fillStyle(grn)
    drawRect(0, 0, clientWidth, clientHeight)
    render()
}
render()
"""
    shown(source)
    wid = _find_window(x_display)
    env = dict(os.environ, DISPLAY=x_display)
    subprocess.run(["xdotool", "windowsize", wid, "500", "400"], env=env, check=True)
    deadline = time.time() + 10
    w = h = 0
    px = None
    while time.time() < deadline:
        w, h, px = window_pixels(x_display, wid)
        if (w, h) == (500, 400) and px[399][499] == (50, 200, 50):
            break
        time.sleep(0.1)
    assert (w, h) == (500, 400)
    assert px[0][0] == px[399][499] == px[200][250] == (50, 200, 50)


# ---- other visuals --------------------------------------------------------

@pytest.fixture
def xvfb_depth(tmp_path):
    """A throwaway Xvfb with a screen of the given depth; yields its
    display name. The server picks its own number (-displayfd)."""
    xvfb = shutil.which("Xvfb")
    if not xvfb:
        pytest.skip("Xvfb isn't installed")
    procs = []

    def _start(depth):
        read_fd, write_fd = os.pipe()
        proc = subprocess.Popen([xvfb, "-displayfd", str(write_fd), "-screen", "0",
                                 f"800x600x{depth}"], stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, pass_fds=(write_fd,))
        os.close(write_fd)
        procs.append(proc)
        import select
        deadline = time.time() + 10
        chunk = b""
        while time.time() < deadline and b"\n" not in chunk:
            if select.select([read_fd], [], [], 0.1)[0]:
                got = os.read(read_fd, 64)
                if not got:
                    break
                chunk += got
        os.close(read_fd)
        if b"\n" not in chunk:
            pytest.skip(f"this Xvfb cannot make a {depth}-bit screen")
        return ":" + chunk.decode().strip()

    yield _start
    for p in procs:
        p.terminate()
        p.wait(timeout=5)


def _run_on(cli_mod, tmp_path, display, source):
    from tests.conftest import _require_c_compiler
    src = tmp_path / "main.f"
    src.write_text(_HEAD + source, encoding="utf-8")
    exe = tmp_path / "program"
    cli_mod.compile_file(str(src), str(exe), cc=_require_c_compiler())
    return subprocess.Popen([str(exe)], cwd=tmp_path, env=dict(os.environ, DISPLAY=display),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def test_a_16_bit_display_is_presented_by_the_general_path(tmp_path, cli_mod, xvfb_depth):
    """Red 0xF800, green 0x07E0, blue 0x001F: not the layout the words are
    in, so each pixel is packed from its channels. Within the 5- and 6-bit
    quantisation of the canvas over white."""
    if not shutil.which("xdotool"):
        pytest.skip("xdotool isn't installed")
    display = xvfb_depth(16)
    proc = _run_on(cli_mod, tmp_path, display, _KINDS)
    try:
        wid = _find_window(display)
        time.sleep(0.6)
        window = window_pixels(display, wid)
    finally:
        proc.terminate()
        proc.wait(timeout=5)
    expected = _canvas_over_white(str(tmp_path / "canvas.png"))
    worst, differing = _worst(window, expected)
    assert (window[0], window[1]) == (800, 600)
    assert worst <= 8, (worst, differing)       # 5 bits of red and blue: steps of 8
    assert window[2][160][70] == (255, 255, 255)
    assert len({tuple(p) for row in window[2] for p in row}) > 15


def test_a_palette_display_is_refused_with_a_reason(tmp_path, cli_mod, xvfb_depth):
    """An 8-bit PseudoColor screen has no layout to present to; the
    program stops saying so rather than painting nonsense."""
    display = xvfb_depth(8)
    proc = _run_on(cli_mod, tmp_path, display, "fillStyle(red)\ndrawRect(0, 0, 10, 10)\nrender()\n")
    try:
        out, err = proc.communicate(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise
    assert proc.returncode != 0
    assert "TrueColor" in out + err, (out, err)
