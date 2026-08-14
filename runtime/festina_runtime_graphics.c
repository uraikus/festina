/*
 * Festina native runtime -- graphics translation unit: claude.md #37,
 * #39, #40 (img, graphics functions, click/mouse/key/resize/close
 * events). See festina_runtime.h's doc comment (the "claude.md #37,
 * #39, #40" block) for the full design rationale -- this file is pure
 * implementation, split out of the single original festina_runtime.c so
 * that a compiled program which never uses graphics never needs Cairo/
 * X11 linked in at all (see festina_runtime.h's top-of-file note, and
 * cli.py's per-feature object file selection driven by
 * CodeGen.uses_graphics in festina/codegen.py).
 *
 * Also home to festina_run_event_loop -- the graphics-aware blocking
 * loop main() enters after __festina_main() returns whenever a program
 * uses graphics (with or without also using timers; see
 * festina_runtime.c's festina_run_timer_loop for the no-graphics
 * equivalent). It shares timer bookkeeping with festina_runtime.c
 * through festina_runtime_internal.h rather than owning any of its own.
 */
#include <string.h>     /* memset -- Motif WM hints */
#include <stdio.h>      /* snprintf -- festina_load_image's error message */
#include <sys/select.h> /* select() -- multiplexes X11 events with timers */
#include <X11/Xlib.h>
#include <X11/Xutil.h> /* XLookupString/XKeysymToString -- `on key` */
#include <cairo/cairo-xlib.h>
#include "festina_runtime.h"
#include "festina_runtime_internal.h"

/* Motif WM hints -- the widely-honored (if not core-protocol) X11
 * convention for requesting a window with no title bar/border/menu. */
typedef struct {
    unsigned long flags, functions, decorations;
    long input_mode;
    unsigned long status;
} FestinaMotifWmHints;

static Display *g_display = NULL;
static Window g_window;
static Atom g_wm_delete_atom;
static cairo_surface_t *g_window_surface = NULL;
static cairo_surface_t *g_backing_surface = NULL;
static void (*g_click_handler)(int64_t, int64_t) = NULL;
static void (*g_mouse_handler)(int64_t, int64_t) = NULL;
static void (*g_key_handler)(const char *) = NULL;
static void (*g_resize_handler)(void) = NULL;
static void (*g_close_handler)(void) = NULL;
/* The canvas's *current* size -- starts at FESTINA_CANVAS_WIDTH/HEIGHT
 * but tracks the window's real size after an `on resize`-triggering
 * ConfigureNotify (see festina_handle_graphics_event);
 * festina_client_width/_height read these, not the compile-time
 * constants. */
static int64_t g_canvas_width = FESTINA_CANVAS_WIDTH;
static int64_t g_canvas_height = FESTINA_CANVAS_HEIGHT;

static void festina_graphics_require_init(void) {
    if (!g_display) {
        festina_fail("a graphics function was called but the canvas window "
                      "was never created (internal compiler error)");
    }
}

void festina_graphics_init(void) {
    g_display = XOpenDisplay(NULL);
    if (!g_display) {
        festina_fail("could not open the X display -- claude.md #39's graphics "
                      "functions need a running X server (is $DISPLAY set?)");
    }

    int screen = DefaultScreen(g_display);
    g_window = XCreateSimpleWindow(g_display, RootWindow(g_display, screen), 0, 0,
                                    FESTINA_CANVAS_WIDTH, FESTINA_CANVAS_HEIGHT, 0,
                                    BlackPixel(g_display, screen), WhitePixel(g_display, screen));

    Atom mwm_hints_atom = XInternAtom(g_display, "_MOTIF_WM_HINTS", False);
    FestinaMotifWmHints hints;
    memset(&hints, 0, sizeof(hints));
    hints.flags = 2; /* MWM_HINTS_DECORATIONS */
    hints.decorations = 0;
    XChangeProperty(g_display, g_window, mwm_hints_atom, mwm_hints_atom, 32,
                     PropModeReplace, (unsigned char *)&hints,
                     sizeof(hints) / sizeof(long));

    XStoreName(g_display, g_window, "Festina");
    XSelectInput(g_display, g_window,
                 ExposureMask | ButtonPressMask | PointerMotionMask |
                 KeyPressMask | StructureNotifyMask);
    g_wm_delete_atom = XInternAtom(g_display, "WM_DELETE_WINDOW", False);
    XSetWMProtocols(g_display, g_window, &g_wm_delete_atom, 1);

    XMapWindow(g_display, g_window);
    XSync(g_display, False); /* the map must reach the server before ... */
    /* ... this: with no window manager to hand focus over (as under a
     * bare Xvfb instance -- see tests/test_codegen.py's TestGraphics),
     * nothing else would ever give this window keyboard focus, and `on
     * key` would never fire. A real desktop's WM normally does this on
     * click/map; asking directly is harmless either way. */
    XSetInputFocus(g_display, g_window, RevertToParent, CurrentTime);
    XFlush(g_display);

    g_canvas_width = FESTINA_CANVAS_WIDTH;
    g_canvas_height = FESTINA_CANVAS_HEIGHT;
    g_window_surface = cairo_xlib_surface_create(g_display, g_window, DefaultVisual(g_display, screen),
                                                  FESTINA_CANVAS_WIDTH, FESTINA_CANVAS_HEIGHT);
    g_backing_surface = cairo_image_surface_create(CAIRO_FORMAT_ARGB32,
                                                     FESTINA_CANVAS_WIDTH, FESTINA_CANVAS_HEIGHT);
    cairo_t *cr = cairo_create(g_backing_surface);
    cairo_set_source_rgb(cr, 1, 1, 1); /* white canvas background */
    cairo_paint(cr);
    cairo_destroy(cr);
}

/* Blits the backing store (source of truth for what's been drawn) onto
 * the visible window -- called after every draw call for immediate
 * feedback, and on every Expose event to repaint correctly. */
static void festina_graphics_present(void) {
    cairo_t *cr = cairo_create(g_window_surface);
    cairo_set_source_surface(cr, g_backing_surface, 0, 0);
    cairo_paint(cr);
    cairo_destroy(cr);
    cairo_surface_flush(g_window_surface);
    XFlush(g_display);
}

void festina_draw_rect(int64_t x, int64_t y, int64_t w, int64_t h) {
    festina_graphics_require_init();
    cairo_t *cr = cairo_create(g_backing_surface);
    cairo_set_source_rgb(cr, 0, 0, 0);
    cairo_rectangle(cr, (double)x, (double)y, (double)w, (double)h);
    cairo_fill(cr);
    cairo_destroy(cr);
    festina_graphics_present();
}

void festina_draw_circle(int64_t x, int64_t y, int64_t r) {
    festina_graphics_require_init();
    cairo_t *cr = cairo_create(g_backing_surface);
    cairo_set_source_rgb(cr, 0, 0, 0);
    cairo_arc(cr, (double)x, (double)y, (double)r, 0.0, 2.0 * 3.14159265358979323846);
    cairo_fill(cr);
    cairo_destroy(cr);
    festina_graphics_present();
}

void festina_draw_text(const char *text, int64_t x, int64_t y) {
    festina_graphics_require_init();
    if (!text) text = "";
    cairo_t *cr = cairo_create(g_backing_surface);
    cairo_set_source_rgb(cr, 0, 0, 0);
    cairo_select_font_face(cr, "sans-serif", CAIRO_FONT_SLANT_NORMAL, CAIRO_FONT_WEIGHT_NORMAL);
    cairo_set_font_size(cr, 16);
    cairo_move_to(cr, (double)x, (double)y);
    cairo_show_text(cr, text);
    cairo_destroy(cr);
    festina_graphics_present();
}

void *festina_load_image(const char *path) {
    if (!path) path = "";
    /* claude.md #37: "Supported image formats are determined by the
     * runtime" -- PNG only, via Cairo's own built-in decoder. */
    cairo_surface_t *img = cairo_image_surface_create_from_png(path);
    cairo_status_t status = cairo_surface_status(img);
    if (status != CAIRO_STATUS_SUCCESS) {
        char msg[512];
        snprintf(msg, sizeof(msg), "could not load image '%s': %s (only PNG images are supported)",
                 path, cairo_status_to_string(status));
        cairo_surface_destroy(img);
        festina_fail(msg);
    }
    return img;
}

void festina_draw_image(void *img, int64_t x, int64_t y) {
    festina_graphics_require_init();
    if (!img) return;
    cairo_t *cr = cairo_create(g_backing_surface);
    cairo_set_source_surface(cr, (cairo_surface_t *)img, (double)x, (double)y);
    cairo_paint(cr);
    cairo_destroy(cr);
    festina_graphics_present();
}

void festina_register_click_handler(void (*handler)(int64_t, int64_t)) {
    g_click_handler = handler;
}

void festina_register_mouse_handler(void (*handler)(int64_t, int64_t)) {
    g_mouse_handler = handler;
}

void festina_register_key_handler(void (*handler)(const char *)) {
    g_key_handler = handler;
}

void festina_register_resize_handler(void (*handler)(void)) {
    g_resize_handler = handler;
}

void festina_register_close_handler(void (*handler)(void)) {
    g_close_handler = handler;
}

int64_t festina_client_width(void) {
    festina_graphics_require_init();
    return g_canvas_width;
}

int64_t festina_client_height(void) {
    festina_graphics_require_init();
    return g_canvas_height;
}

/* Handles one already-read X11 event. Returns 0 if this was the
 * window-close request (the caller should stop looping and tear down),
 * 1 otherwise. Factored out of what used to be festina_graphics_run's
 * own while(1) loop body so festina_run_event_loop (below) can drive
 * it either as-is or interleaved with timer processing. */
static int festina_handle_graphics_event(XEvent *ev) {
    if (ev->type == Expose) {
        festina_graphics_present();
    } else if (ev->type == ButtonPress) {
        if (g_click_handler) g_click_handler(ev->xbutton.x, ev->xbutton.y);
    } else if (ev->type == MotionNotify) {
        if (g_mouse_handler) g_mouse_handler(ev->xmotion.x, ev->xmotion.y);
    } else if (ev->type == KeyPress) {
        if (g_key_handler) {
            /* A key that types an ordinary printable character
             * (letters, digits, punctuation, space) comes back as
             * that character through the buffer XLookupString fills
             * in. Anything else -- Enter/Escape/Backspace/arrow
             * keys/... -- either comes back empty or as an
             * unprintable control character (e.g. 0x1B for Escape,
             * 0x0D for Return), neither of which is a useful `text`
             * value, so those fall back to XKeysymToString's X11 key
             * name instead (e.g. "Return", "Escape", "Left") --
             * there's no claude.md-defined naming scheme for these,
             * so this is simply X11's own. */
            char buf[32];
            KeySym keysym;
            int len = XLookupString(&ev->xkey, buf, sizeof(buf) - 1, &keysym, NULL);
            if (len > 0 && (unsigned char)buf[0] >= 0x20 && (unsigned char)buf[0] != 0x7F) {
                buf[len] = '\0';
                g_key_handler(buf);
            } else {
                const char *name = XKeysymToString(keysym);
                g_key_handler(name ? name : "");
            }
        }
    } else if (ev->type == ConfigureNotify) {
        /* ConfigureNotify fires on more than just a resize (e.g. a
         * move), so only treat it as `on resize` when the size
         * genuinely changed. */
        int64_t new_w = ev->xconfigure.width;
        int64_t new_h = ev->xconfigure.height;
        if (new_w != g_canvas_width || new_h != g_canvas_height) {
            g_canvas_width = new_w;
            g_canvas_height = new_h;
            cairo_xlib_surface_set_size(g_window_surface, new_w, new_h);
            /* claude.md #39's own examples never draw relative to a
             * canvas size (there's no syntax for one), so there's no
             * spec-defined way to preserve old content sanely across
             * a resize -- clear back to white at the new size, the
             * same behavior resizing a browser's <canvas> element
             * has, which clientWidth/clientHeight are named after. */
            cairo_surface_destroy(g_backing_surface);
            g_backing_surface = cairo_image_surface_create(CAIRO_FORMAT_ARGB32, new_w, new_h);
            cairo_t *cr = cairo_create(g_backing_surface);
            cairo_set_source_rgb(cr, 1, 1, 1);
            cairo_paint(cr);
            cairo_destroy(cr);
            festina_graphics_present();
            if (g_resize_handler) g_resize_handler();
        }
    } else if (ev->type == ClientMessage) {
        if ((Atom)ev->xclient.data.l[0] == g_wm_delete_atom) {
            if (g_close_handler) g_close_handler();
            return 0;
        }
    }
    return 1;
}

static void festina_graphics_teardown(void) {
    cairo_surface_destroy(g_backing_surface);
    cairo_surface_destroy(g_window_surface);
    XDestroyWindow(g_display, g_window);
    XCloseDisplay(g_display);
}

/* The blocking loop main() enters (via festina_run_event_loop) whenever
 * a program uses graphics -- see festina_runtime.h's doc comment.
 * Multiplexes X11 events and timer deadlines on the same select() call
 * (via ConnectionNumber(g_display)) rather than picking one or the
 * other, so `on click`/timers both stay responsive at once; exits when
 * the window closes (timers, if any, are simply abandoned -- matching a
 * browser tab unloading). Timer state itself lives in
 * festina_runtime.c, reached only through
 * festina_next_timer_deadline()/festina_fire_expired_timers()
 * (festina_runtime_internal.h) -- this file owns no timer bookkeeping
 * of its own. */
void festina_run_event_loop(void) {
    while (1) {
        double earliest = festina_next_timer_deadline();

        if (!XPending(g_display)) {
            int xfd = ConnectionNumber(g_display);
            fd_set fds;
            FD_ZERO(&fds);
            FD_SET(xfd, &fds);
            struct timeval tv;
            struct timeval *tvp = NULL;
            if (earliest >= 0.0) {
                double remaining = earliest - festina_now_seconds();
                if (remaining < 0.0) remaining = 0.0;
                tv.tv_sec = (long)remaining;
                tv.tv_usec = (long)((remaining - (double)tv.tv_sec) * 1e6);
                tvp = &tv;
            }
            select(xfd + 1, &fds, NULL, NULL, tvp);
        }
        int keep_going = 1;
        while (XPending(g_display)) {
            XEvent ev;
            XNextEvent(g_display, &ev);
            if (!festina_handle_graphics_event(&ev)) {
                keep_going = 0;
                break; /* stop on window-close, same as the old loop did */
            }
        }
        festina_fire_expired_timers();
        if (!keep_going) {
            festina_graphics_teardown();
            return;
        }
    }
}
