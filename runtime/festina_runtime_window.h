/* claude.md #123 / macos.md Phase 2: the windowing DEVICE seam --
 * the graphics counterpart of #121's festina_pcm_* audio seam.
 *
 * Everything about drawing (Cairo, the backing image surface, style
 * state, the circle/text/image machinery) is fully portable and lives
 * in festina_runtime_graphics.c unconditionally. The one thing that
 * differs per platform is putting that drawing on an actual window and
 * reading input back out of it, and its whole surface is these five
 * functions plus one event shape:
 *
 *   festina_window_open(width, height, title)   -> opens the window
 *   festina_window_close()                      -> destroys it
 *   festina_window_present(backing)              -> blits `backing`
 *                                                    (the shared ARGB32
 *                                                    ImageSurface that
 *                                                    IS the canvas) onto
 *                                                    the visible window
 *   festina_window_events_wait(timeout_seconds)  -> blocks up to
 *                                                    timeout_seconds
 *                                                    for at least one
 *                                                    event to exist
 *                                                    (negative = wait
 *                                                    indefinitely)
 *   festina_window_events_drain(handler)         -> delivers every
 *                                                    currently queued
 *                                                    event, oldest
 *                                                    first, via
 *                                                    `handler`
 *
 * A backend owns REDRAW ON ITS OWN: each implementation remembers the
 * last surface handed to festina_window_present and repaints from it
 * whenever the OS says the window needs it (an X11 Expose event, an
 * NSView drawRect: call) -- entirely internal to that backend, with no
 * event or round trip back into shared code, since shared code has
 * nothing more useful to say than "the same thing I already told you".
 *
 * `key_name` in a KEY_DOWN/KEY_UP event is BORROWED -- valid only for
 * the duration of the handler call, exactly the convention every other
 * runtime callback in this codebase already uses for a `const char *`
 * it does not itself own. Every implementation must report the SAME
 * strings for the same physical keys -- see festina_key_names.h, the
 * pinned cross-platform vocabulary both backends draw from.
 *
 * The Linux implementation (X11, inside festina_runtime_graphics.c,
 * guarded `#ifndef __APPLE__`) is the original code, moved verbatim
 * behind these five functions -- verified zero-regression against the
 * full Xvfb-backed TestGraphics/TestExampleGraphics/TestExampleTicTacToe
 * suite. The macOS implementation (festina_runtime_window_mac.m, a
 * separate Objective-C translation unit -- Cocoa cannot be compiled as
 * part of a plain .c file) blits the same backing surface via CGImage.
 * See macos.md Phase 2 for the full design and its known open pieces
 * (real-hardware verification; the gate in festina/cli.py). */
#ifndef FESTINA_RUNTIME_WINDOW_H
#define FESTINA_RUNTIME_WINDOW_H

#include <stdint.h>
/* Bare <cairo.h>, not <cairo/cairo.h>: pkg-config's Cflags for both
 * cairo-xlib (Linux) and cairo (darwin) point -I directly at the
 * cairo headers directory itself (e.g. -I/usr/include/cairo,
 * -I/opt/homebrew/opt/cairo/include/cairo), so <cairo.h> is the form
 * that resolves via the explicit -I flag on every platform. The
 * <cairo/cairo-xlib.h> spelling elsewhere in the X11-only backend
 * block happens to also work on Linux, but only because /usr/include
 * is always on clang/gcc's own default search path there -- a
 * coincidence Homebrew's non-default install prefix doesn't share,
 * confirmed by real macOS CI (claude.md #126). */
#include <cairo.h>

typedef enum {
    FESTINA_WEVENT_MOUSE_DOWN,
    FESTINA_WEVENT_MOUSE_UP,
    FESTINA_WEVENT_MOUSE_MOVE,
    FESTINA_WEVENT_KEY_DOWN,
    FESTINA_WEVENT_KEY_UP,
    FESTINA_WEVENT_RESIZE,
    FESTINA_WEVENT_CLOSE,
} FestinaWindowEventKind;

typedef struct {
    FestinaWindowEventKind kind;
    int64_t x, y;            /* MOUSE_DOWN/MOUSE_UP/MOUSE_MOVE */
    const char *key_name;    /* KEY_DOWN/KEY_UP -- borrowed, see above */
    int64_t width, height;   /* RESIZE -- the window's new content size */
} FestinaWindowEvent;

void festina_window_open(int64_t width, int64_t height, const char *title);
void festina_window_close(void);
void festina_window_present(cairo_surface_t *backing);
void festina_window_events_wait(double timeout_seconds);
void festina_window_events_drain(void (*handler)(const FestinaWindowEvent *event));

/* claude.md #139: screenWidth/screenHeight and setClientWidth/
 * setClientHeight.
 *
 * festina_window_screen_size reports the PHYSICAL display's own
 * resolution, independent of whether a window is currently open --
 * unlike every other seam function above, which all require one. A
 * backend with no window open yet must still answer this (a headless
 * program asking "how big is the screen" before ever drawing anything
 * is a real, intended use), so each implementation connects/queries/
 * disconnects on its own if it has to, invisibly to the caller.
 *
 * festina_window_resize resizes the OPEN window to exactly (width,
 * height) content pixels -- a no-op if none is open, since portable
 * code (festina_set_client_size in festina_runtime_graphics.c) already
 * handles the "no window yet" case entirely on its own by updating the
 * canvas's own size for whenever one opens. This function's only job
 * is the native OS resize call. */
void festina_window_screen_size(int64_t *out_width, int64_t *out_height);
void festina_window_resize(int64_t width, int64_t height);

/* claude.md #180: enterFullscreen()/exitFullscreen(). Toggles the OPEN
 * window in and out of true OS fullscreen -- covering the whole screen,
 * no decorations -- restoring its prior geometry on exit. A no-op if no
 * window is open, mirroring festina_window_resize's own contract just
 * above: portable code (festina_enter_fullscreen/festina_exit_
 * fullscreen in festina_runtime_graphics.c) already handles the "no
 * window yet" case by recording the desired state for
 * festina_graphics_init to apply once one actually opens, the identical
 * split festina_set_client_size/festina_window_resize already use for
 * setClientWidth/setClientHeight. The resulting size change is reported
 * the same way any other native, WM-driven resize already is -- through
 * this backend's own RESIZE event, whenever the next events_drain pump
 * happens to run -- rather than synchronously the way setClientWidth/
 * setClientHeight are, since going fullscreen is a real window-manager/
 * OS negotiation (X11's async ClientMessage protocol; AppKit's own
 * animated transition), not a Festina-driven resize like those two. */
void festina_window_set_fullscreen(int8_t fullscreen);

#endif /* FESTINA_RUNTIME_WINDOW_H */
