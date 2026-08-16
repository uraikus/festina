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
#include <stdlib.h>     /* strtol -- claude.md #89's #rrggbb colour parsing */
#include <ctype.h>      /* isdigit/tolower -- claude.md #89's colour/font parsing */
#include <strings.h>    /* strcasecmp -- claude.md #89's case-insensitive colour names */
#include <sys/select.h> /* select() -- multiplexes X11 events with timers */
#include <time.h>       /* nanosleep -- festina_graphics_init's connect retry */
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

/* claude.md #89: the canvas's current drawing style. All of it is plain
 * process-global state set by fillStyle()/borderColor()/lineWidth()/
 * font() and read by every later draw call -- the same "set it, then
 * draw" model the HTML canvas 2D context uses, rather than passing a
 * style argument to every draw function (which claude.md #37/#39's own
 * worked examples explicitly don't do: drawRect(0, 0, 100, 100) takes
 * geometry only). Defaults reproduce exactly what these functions drew
 * before this section existed: solid black fill, no border, 16px
 * sans-serif -- so adding this section changes no existing program's
 * output. */
static double g_fill_r = 0.0, g_fill_g = 0.0, g_fill_b = 0.0;
static int g_fill_none = 0;
static double g_border_r = 0.0, g_border_g = 0.0, g_border_b = 0.0;
/* Unset, not merely "black": a border is drawn only once borderColor()
 * has actually been called with a real colour, so a program that never
 * mentions it keeps the plain filled shapes it always had. */
static int g_border_set = 0;
static double g_line_width = 1.0;
static char g_font_family[64] = "sans-serif";
static double g_font_size = 16.0;
static cairo_font_slant_t g_font_slant = CAIRO_FONT_SLANT_NORMAL;
static cairo_font_weight_t g_font_weight = CAIRO_FONT_WEIGHT_NORMAL;

static void festina_graphics_require_init(void) {
    if (!g_display) {
        festina_fail("a graphics function was called but the canvas window "
                      "was never created (internal compiler error)");
    }
}

/* claude.md #89: the named colours a fillStyle()/borderColor() string may
 * use, on top of #rgb/#rrggbb. Deliberately a small, fixed table rather
 * than the full ~148-entry CSS list: these are the ones a program is
 * actually likely to reach for, and every additional name is a name a
 * typo could silently resolve to. Anything unrecognised fails loudly
 * (see festina_parse_color) rather than defaulting to black, matching
 * claude.md #59's "fail clearly the moment something is actually
 * wrong" bias. */
typedef struct {
    const char *name;
    double r, g, b;
} FestinaNamedColor;

static const FestinaNamedColor FESTINA_NAMED_COLORS[] = {
    {"black",   0.0,  0.0,  0.0},
    {"white",   1.0,  1.0,  1.0},
    {"red",     1.0,  0.0,  0.0},
    {"green",   0.0,  0.5,  0.0},
    {"lime",    0.0,  1.0,  0.0},
    {"blue",    0.0,  0.0,  1.0},
    {"yellow",  1.0,  1.0,  0.0},
    {"cyan",    0.0,  1.0,  1.0},
    {"aqua",    0.0,  1.0,  1.0},
    {"magenta", 1.0,  0.0,  1.0},
    {"fuchsia", 1.0,  0.0,  1.0},
    {"silver",  0.75, 0.75, 0.75},
    {"gray",    0.5,  0.5,  0.5},
    {"grey",    0.5,  0.5,  0.5},
    {"maroon",  0.5,  0.0,  0.0},
    {"olive",   0.5,  0.5,  0.0},
    {"purple",  0.5,  0.0,  0.5},
    {"teal",    0.0,  0.5,  0.5},
    {"navy",    0.0,  0.0,  0.5},
    {"orange",  1.0,  0.65, 0.0},
    {"pink",    1.0,  0.75, 0.8},
    {"brown",   0.65, 0.16, 0.16},
};

static int festina_hex_value(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    c = (char)tolower((unsigned char)c);
    if (c >= 'a' && c <= 'f') return 10 + (c - 'a');
    return -1;
}

/* Parses a colour string into r/g/b in 0..1. Sets *is_none for the two
 * spellings that mean "draw nothing at all" rather than a colour.
 * Returns 0 on a string it doesn't understand, so the caller can fail
 * with a message naming the offending value. */
static int festina_parse_color(const char *s, double *r, double *g, double *b, int *is_none) {
    *is_none = 0;
    if (!s) return 0;
    while (*s == ' ' || *s == '\t') s++;

    if (strcasecmp(s, "none") == 0 || strcasecmp(s, "transparent") == 0) {
        *is_none = 1;
        *r = *g = *b = 0.0;
        return 1;
    }

    if (*s == '#') {
        const char *h = s + 1;
        size_t len = strlen(h);
        int v[6];
        if (len != 3 && len != 6) return 0;
        for (size_t i = 0; i < len; i++) {
            v[i] = festina_hex_value(h[i]);
            if (v[i] < 0) return 0;
        }
        if (len == 3) {
            /* #abc is #aabbcc -- each digit doubled, same as CSS. */
            *r = (v[0] * 17) / 255.0;
            *g = (v[1] * 17) / 255.0;
            *b = (v[2] * 17) / 255.0;
        } else {
            *r = (v[0] * 16 + v[1]) / 255.0;
            *g = (v[2] * 16 + v[3]) / 255.0;
            *b = (v[4] * 16 + v[5]) / 255.0;
        }
        return 1;
    }

    for (size_t i = 0; i < sizeof(FESTINA_NAMED_COLORS) / sizeof(FESTINA_NAMED_COLORS[0]); i++) {
        if (strcasecmp(s, FESTINA_NAMED_COLORS[i].name) == 0) {
            *r = FESTINA_NAMED_COLORS[i].r;
            *g = FESTINA_NAMED_COLORS[i].g;
            *b = FESTINA_NAMED_COLORS[i].b;
            return 1;
        }
    }
    return 0;
}

static void festina_fail_bad_color(const char *fn, const char *value) {
    char msg[512];
    snprintf(msg, sizeof(msg),
             "%s(): '%s' is not a colour Festina understands -- use a name "
             "(red, blue, black, ...), a #rgb or #rrggbb hex value, or "
             "'none' for no colour at all",
             fn, value ? value : "null");
    festina_fail(msg);
}

void festina_set_fill_style(const char *color) {
    double r, g, b;
    int none;
    if (!festina_parse_color(color, &r, &g, &b, &none)) {
        festina_fail_bad_color("fillStyle", color);
    }
    g_fill_r = r; g_fill_g = g; g_fill_b = b;
    g_fill_none = none;
}

void festina_set_border_color(const char *color) {
    double r, g, b;
    int none;
    if (!festina_parse_color(color, &r, &g, &b, &none)) {
        festina_fail_bad_color("borderColor", color);
    }
    /* 'none' turns the border back off rather than setting a colour, so
     * a program can switch borders off again after enabling them. */
    g_border_set = !none;
    g_border_r = r; g_border_g = g; g_border_b = b;
}

void festina_set_line_width(int64_t width) {
    /* A negative width is meaningless to Cairo (and would silently draw
     * nothing); clamping to 0 keeps "no border" expressible both ways. */
    g_line_width = width < 0 ? 0.0 : (double)width;
}

/* claude.md #89: a tolerant subset of the CSS/canvas `font` shorthand --
 * whitespace-separated words, in any order, where `italic`/`oblique` set
 * the slant, `bold` sets the weight, a bare number or `<n>px` sets the
 * size, and the first thing that is none of those becomes the family.
 * Order-independence is deliberate: the strict CSS grammar requires
 * size and family last and in that order, which is exactly the kind of
 * rule that turns a reasonable-looking string into a silent no-op, and
 * nothing here needs the ambiguity that grammar exists to resolve. */
void festina_set_font(const char *spec) {
    if (!spec) return;
    cairo_font_slant_t slant = CAIRO_FONT_SLANT_NORMAL;
    cairo_font_weight_t weight = CAIRO_FONT_WEIGHT_NORMAL;
    double size = g_font_size;
    char family[64];
    family[0] = '\0';

    const char *p = spec;
    while (*p) {
        while (*p == ' ' || *p == '\t') p++;
        if (!*p) break;
        const char *start = p;
        while (*p && *p != ' ' && *p != '\t') p++;
        size_t len = (size_t)(p - start);
        char word[64];
        if (len >= sizeof(word)) len = sizeof(word) - 1;
        memcpy(word, start, len);
        word[len] = '\0';

        if (strcasecmp(word, "italic") == 0 || strcasecmp(word, "oblique") == 0) {
            slant = CAIRO_FONT_SLANT_ITALIC;
        } else if (strcasecmp(word, "bold") == 0) {
            weight = CAIRO_FONT_WEIGHT_BOLD;
        } else if (strcasecmp(word, "normal") == 0) {
            /* explicit default -- accepted and ignored, as in CSS */
        } else if (isdigit((unsigned char)word[0])) {
            char *end = NULL;
            double parsed = strtod(word, &end);
            if (parsed > 0.0) size = parsed;
        } else if (family[0] == '\0') {
            snprintf(family, sizeof(family), "%s", word);
        }
    }

    g_font_slant = slant;
    g_font_weight = weight;
    g_font_size = size;
    if (family[0] != '\0') {
        snprintf(g_font_family, sizeof(g_font_family), "%s", family);
    }
}

/* Applies the current font to a context -- shared by drawing and by the
 * measure functions, so a measurement can never disagree with what a
 * later draw of the same string actually produces. */
static void festina_apply_font(cairo_t *cr) {
    cairo_select_font_face(cr, g_font_family, g_font_slant, g_font_weight);
    cairo_set_font_size(cr, g_font_size);
}

/* claude.md #89: measuring deliberately does NOT require the canvas
 * window. Text metrics depend only on the font, so these run against a
 * tiny scratch image surface and work in a program that never draws
 * anything at all (the same reasoning that keeps loadImage() from
 * forcing a window open -- see festina_load_image's own note). */
static cairo_t *festina_measure_context(void) {
    static cairo_surface_t *scratch = NULL;
    if (!scratch) {
        scratch = cairo_image_surface_create(CAIRO_FORMAT_ARGB32, 1, 1);
    }
    cairo_t *cr = cairo_create(scratch);
    festina_apply_font(cr);
    return cr;
}

int64_t festina_measure_text_width(const char *text) {
    if (!text) text = "";
    cairo_t *cr = festina_measure_context();
    cairo_text_extents_t ext;
    cairo_text_extents(cr, text, &ext);
    cairo_destroy(cr);
    /* x_advance, not the inked width: this is how far the pen moves, so
     * laying out one string after another actually lines up. Matches
     * the canvas 2D measureText().width. */
    return (int64_t)(ext.x_advance + 0.5);
}

int64_t festina_measure_text_height(const char *text) {
    if (!text) text = "";
    cairo_t *cr = festina_measure_context();
    cairo_text_extents_t ext;
    cairo_text_extents(cr, text, &ext);
    cairo_destroy(cr);
    /* The inked height of THIS string, which is why it takes the text
     * rather than reading the font alone -- 'x' is shorter than 'Xg'.
     * See api.md for when font-wide line height is the better tool. */
    return (int64_t)(ext.height + 0.5);
}

/* Fills the path already built on `cr` with the current fill colour and,
 * when borderColor() has set one and lineWidth() is non-zero, strokes
 * the same path on top. Shared by every filled shape so they can never
 * drift apart. Preserves the path across the fill (cairo_fill_preserve)
 * only when a border is actually going to use it. */
static void festina_fill_and_border(cairo_t *cr) {
    int border = g_border_set && g_line_width > 0.0;
    if (!g_fill_none) {
        cairo_set_source_rgb(cr, g_fill_r, g_fill_g, g_fill_b);
        if (border) {
            cairo_fill_preserve(cr);
        } else {
            cairo_fill(cr);
        }
    } else if (!border) {
        /* nothing to fill and nothing to stroke -- clear the path so it
         * doesn't leak into whatever this context draws next */
        cairo_new_path(cr);
        return;
    }
    if (border) {
        cairo_set_source_rgb(cr, g_border_r, g_border_g, g_border_b);
        cairo_set_line_width(cr, g_line_width);
        cairo_stroke(cr);
    }
}

/* Swallows exactly the failure mode festina_graphics_init's own
 * best-effort XSetInputFocus call can trigger: under a *real* window
 * manager (unlike the bare Xvfb instance tests/test_codegen.py's
 * TestGraphics runs against, which has no WM to race with at all), the
 * WM can still be reparenting/managing the just-mapped window at the
 * moment this call reaches the server, so the window is transiently not
 * yet "viewable" -- a real, reproduced BadMatch (X_SetInputFocus, opcode
 * 42), confirmed directly by running a compiled Festina graphics program
 * under `twm`, not a hypothetical race. Xlib's *default* error handler
 * prints this and then calls exit(), which would otherwise take the
 * whole program down over a focus request that was already documented
 * as harmless-if-it-fails. Installed only around that one call (see its
 * own call site) -- every other X11 error the program might hit still
 * goes through Xlib's default handler and is treated as fatal, exactly
 * as before this existed. */
static int festina_ignore_focus_error(Display *display, XErrorEvent *error) {
    (void)display;
    (void)error;
    return 0;
}

void festina_graphics_init(void) {
    /* claude.md #87: retried, not a single attempt. XOpenDisplay does no
     * retrying of its own, so ONE transient failure to connect -- a full
     * listen backlog on the X server's socket under load, or a server
     * that is accepting connections but momentarily not completing them
     * -- used to kill the whole program with a fatal "is $DISPLAY set?"
     * error that named entirely the wrong cause.
     *
     * Confirmed as a real, reproducible transient rather than a
     * misdiagnosed dead server: instrumenting the failure showed the
     * Xvfb process still alive, /tmp/.X11-unix/X<n> and /tmp/.X<n>-lock
     * both present with the lock file naming that same live server's own
     * pid (so not a display-number collision either), and `xdotool`
     * connecting to that exact display successfully both immediately
     * before and immediately after the failed attempt. The connection
     * was simply refused once, under load.
     *
     * This was also the entire cause of tests/test_codegen.py's
     * TestGraphics being intermittently flaky -- roughly a third of
     * full-suite runs, essentially never when run in isolation, which
     * had previously been attributed to slow window startup and
     * "fixed" by raising the test-side polling timeout to 20s. That
     * diagnosis was wrong: the window appears in ~0.2s consistently,
     * and no timeout could ever have helped, because the program had
     * already exited by then.
     *
     * Ten attempts, 100ms apart, so a genuinely absent X server still
     * fails with the same clear message in about a second. */
    for (int attempt = 0; attempt < 10; attempt++) {
        g_display = XOpenDisplay(NULL);
        if (g_display) break;
        struct timespec pause = {0, 100L * 1000L * 1000L}; /* 100ms */
        nanosleep(&pause, NULL);
    }
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
     * click/map; asking directly is harmless either way -- and, under a
     * real WM, can genuinely fail (BadMatch, if the WM is still
     * reparenting the window at this exact moment), so this one call is
     * wrapped in a lenient handler that tolerates it rather than letting
     * Xlib's own default handler exit() the whole program over it; see
     * festina_ignore_focus_error's own comment. */
    int (*prev_error_handler)(Display *, XErrorEvent *) = XSetErrorHandler(festina_ignore_focus_error);
    XSetInputFocus(g_display, g_window, RevertToParent, CurrentTime);
    XSync(g_display, False); /* force any BadMatch to arrive before the handler is restored */
    XSetErrorHandler(prev_error_handler);
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
    cairo_rectangle(cr, (double)x, (double)y, (double)w, (double)h);
    festina_fill_and_border(cr); /* claude.md #89 */
    cairo_destroy(cr);
    festina_graphics_present();
}

void festina_draw_circle(int64_t x, int64_t y, int64_t r) {
    festina_graphics_require_init();
    cairo_t *cr = cairo_create(g_backing_surface);
    cairo_arc(cr, (double)x, (double)y, (double)r, 0.0, 2.0 * 3.14159265358979323846);
    festina_fill_and_border(cr); /* claude.md #89 */
    cairo_destroy(cr);
    festina_graphics_present();
}

void festina_draw_text(const char *text, int64_t x, int64_t y) {
    festina_graphics_require_init();
    if (!text) text = "";
    cairo_t *cr = cairo_create(g_backing_surface);
    /* claude.md #89: drawn in the current fill colour and font. Text is
     * filled only -- borderColor outlines shapes, not glyphs. */
    if (g_fill_none) { cairo_destroy(cr); return; }
    cairo_set_source_rgb(cr, g_fill_r, g_fill_g, g_fill_b);
    festina_apply_font(cr);
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
