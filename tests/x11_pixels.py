"""Read the pixels of a live X11 window through libX11 itself.

The older window tests capture with `xwd`, a separate package that is not
always there; the presentation seam (runtime.md, phase 7, slice 7) needs
the pixels of EVERY window it shows, so this reads them directly:
XGetImage, and the colour masks of the XImage it returns say where each
channel sits.
"""
import ctypes
import ctypes.util


class _XImage(ctypes.Structure):
    _fields_ = [
        ("width", ctypes.c_int), ("height", ctypes.c_int), ("xoffset", ctypes.c_int),
        ("format", ctypes.c_int), ("data", ctypes.POINTER(ctypes.c_char)),
        ("byte_order", ctypes.c_int), ("bitmap_unit", ctypes.c_int),
        ("bitmap_bit_order", ctypes.c_int), ("bitmap_pad", ctypes.c_int),
        ("depth", ctypes.c_int), ("bytes_per_line", ctypes.c_int),
        ("bits_per_pixel", ctypes.c_int),
        ("red_mask", ctypes.c_ulong), ("green_mask", ctypes.c_ulong), ("blue_mask", ctypes.c_ulong),
    ]


def _channel(value, mask):
    if not mask:
        return 0
    shift = (mask & -mask).bit_length() - 1
    width = bin(mask >> shift).count("1")
    c = (value & mask) >> shift
    return (c << (8 - width)) | (c >> (2 * width - 8)) if width < 8 else c


def window_pixels(display, window_id):
    """(width, height, rows) of the window's current contents, each pixel
    an (r, g, b) triple of 0..255."""
    x11 = ctypes.CDLL(ctypes.util.find_library("X11"))
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XGetImage.restype = ctypes.POINTER(_XImage)
    x11.XGetImage.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
                              ctypes.c_uint, ctypes.c_uint, ctypes.c_ulong, ctypes.c_int]
    x11.XGetGeometry.argtypes = [ctypes.c_void_p, ctypes.c_ulong] + [ctypes.c_void_p] * 7
    x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    dpy = x11.XOpenDisplay(display.encode())
    if not dpy:
        raise RuntimeError("cannot open display " + display)
    try:
        root = ctypes.c_ulong()
        x, y, w, h, bw, depth = (ctypes.c_int(), ctypes.c_int(), ctypes.c_uint(),
                                 ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint())
        x11.XGetGeometry(dpy, int(window_id), ctypes.byref(root), ctypes.byref(x), ctypes.byref(y),
                         ctypes.byref(w), ctypes.byref(h), ctypes.byref(bw), ctypes.byref(depth))
        x11.XSync(dpy, 0)
        img = x11.XGetImage(dpy, int(window_id), 0, 0, w.value, h.value, 0xFFFFFFFF, 2)
        if not img:
            raise RuntimeError("XGetImage failed")
        im = img.contents
        raw = ctypes.string_at(im.data, im.bytes_per_line * im.height)
        nbytes = im.bits_per_pixel // 8
        rows = []
        for yy in range(im.height):
            row = []
            base = yy * im.bytes_per_line
            for xx in range(im.width):
                o = base + xx * nbytes
                v = int.from_bytes(raw[o:o + nbytes], "little" if im.byte_order == 0 else "big")
                row.append((_channel(v, im.red_mask), _channel(v, im.green_mask),
                            _channel(v, im.blue_mask)))
            rows.append(row)
        return im.width, im.height, rows
    finally:
        x11.XCloseDisplay(dpy)
