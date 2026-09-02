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
#include <stdio.h>      /* snprintf/fopen -- image loading and its errors */
#include <stdlib.h>     /* malloc/free -- claude.md #92's image box */
#include <ctype.h>      /* tolower -- claude.md #90's case-insensitive style match */
#include <errno.h>      /* strerror -- festina_load_image's error message */
#include <stdint.h>     /* uint32_t -- claude.md #101's JPEG pixel conversion */
#include <math.h>       /* floor -- claude.md #104's transform check */
#include <setjmp.h>     /* libjpeg reports errors by longjmp -- claude.md #101 */
#include <jpeglib.h>    /* claude.md #101: JPEG decoding */
/* windows.md Phase 2 / claude.md #128: <sys/select.h> and the connect-
 * retry loop's <time.h> use (nanosleep) are needed only by the X11
 * backend at the bottom of this file -- neither exists on Windows, so
 * they must stay conditional on the exact same platforms that backend
 * itself compiles on, not just "not Apple" (see that guard's own note
 * below for why "not Apple" alone used to be wrong here). */
#if !defined(__APPLE__) && !defined(_WIN32)
#include <sys/select.h> /* select() -- the X11 window backend's events_wait */
#include <time.h>       /* nanosleep -- the X11 backend's connect retry */
#endif
#include "festina_runtime.h"
#include "festina_runtime_internal.h"
#include "festina_runtime_window.h" /* claude.md #123: the windowing device seam --
                                     * see its own doc comment for the full design.
                                     * Everything in THIS file is now portable: the
                                     * X11 implementation of the seam lives at the
                                     * bottom of this file, guarded
                                     * `#if !defined(__APPLE__) && !defined(_WIN32)`
                                     * -- claude.md #128: this used to read
                                     * `#ifndef __APPLE__` alone, which is also true
                                     * on Windows, so before windows.md Phase 2 had
                                     * anywhere else for Windows to go, this file
                                     * would have tried to compile the X11 backend
                                     * (<X11/Xlib.h> and friends, none of which exist
                                     * under MinGW) the moment anything ever asked it
                                     * to -- invisible until now because nothing did.
                                     * The macOS implementation is a separate
                                     * Objective-C translation unit
                                     * (festina_runtime_window_mac.m, Cocoa cannot be
                                     * compiled as part of a plain .c file); the
                                     * Windows implementation is plain C
                                     * (festina_runtime_window_win32.c), wired in by
                                     * festina/cli.py exactly like the other two. */

static int g_window_open = 0;      /* claude.md #123: portable stand-in for "is
                                     * there a live platform window" -- the shared
                                     * code's own guard, since g_display/g_window
                                     * no longer exist here at all. */
/* claude.md #180: enterFullscreen()/exitFullscreen()'s own desired/
 * current-state flag -- does double duty exactly like g_canvas_width/
 * g_canvas_height already do (claude.md #178's own comment on those):
 * "the requested state before a window exists" and "the current state
 * once one does" are the same variable, on purpose, so a program that
 * calls enterFullscreen() before ever drawing anything gets a window
 * that opens DIRECTLY in fullscreen -- no flash of a normal window
 * first -- the identical fix #178 already made for canvas size. */
static int g_is_fullscreen = 0;
/* claude.md #182: showCursor()/hideCursor()'s own desired/current-state
 * flag, the identical double-duty shape g_is_fullscreen just above
 * (and g_canvas_width/g_canvas_height before it, claude.md #178) --
 * default VISIBLE (1), so a program that never touches this at all
 * behaves exactly as before this existed. */
static int g_cursor_visible = 1;
static cairo_surface_t *g_backing_surface = NULL;
/* claude.md #106: `on click` split into `on mouseDown` and `on mouseUp`,
 * exactly as claude.md #98 split `on key`. A click is a press and a
 * release, and a program that needs to tell them apart -- dragging,
 * charging a shot, holding to aim -- could not, because the two were
 * collapsed into one event that fired on press. */
/* claude.md #182: `button` (X11's own numbering, see FestinaWindowEvent's
 * own doc comment in festina_runtime_window.h). `on mouse` stays
 * 2-argument -- a move has no button of its own to report. */
static void (*g_mouse_down_handler)(int64_t, int64_t, int64_t) = NULL;
static void (*g_mouse_up_handler)(int64_t, int64_t, int64_t) = NULL;
static void (*g_mouse_handler)(int64_t, int64_t) = NULL;
/* claude.md #181: the scroll wheel, split by direction -- see
 * semantic.py's _EVENT_SIGNATURES' own comment. */
static void (*g_mouse_wheel_up_handler)(int64_t, int64_t) = NULL;
static void (*g_mouse_wheel_down_handler)(int64_t, int64_t) = NULL;
/* claude.md #98: `on key` split into `on keyDown` and `on keyUp`. */
static void (*g_key_down_handler)(const char *) = NULL;
static void (*g_key_up_handler)(const char *) = NULL;
static void (*g_resize_handler)(void) = NULL;
static void (*g_close_handler)(void) = NULL;
/* The canvas's *current* size -- starts at FESTINA_CANVAS_WIDTH/HEIGHT
 * but tracks the window's real size after an `on resize`-triggering
 * ConfigureNotify (see festina_handle_graphics_event);
 * festina_client_width/_height read these, not the compile-time
 * constants. */
static int64_t g_canvas_width = FESTINA_CANVAS_WIDTH;
static int64_t g_canvas_height = FESTINA_CANVAS_HEIGHT;

/* claude.md #89: the canvas's current drawing style, in the form the
 * drawing code actually wants it -- channels already scaled to Cairo's
 * 0..1, slant/weight already chosen. claude.md #90 moved all the
 * turning-source-text-into-these work to compile time. Plain
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

/* claude.md #94: the current transform, plus the saved-state stack.
 *
 * Every drawing function creates its own short-lived cairo_t (see
 * festina_draw_rect and friends), which starts with an identity matrix
 * -- so a transform set by translate()/rotate()/scale() has to live
 * here, outside any one of them, and be applied to each new context.
 * That is what makes `translate(100, 0)` affect the NEXT drawRect
 * rather than nothing at all. */
static cairo_matrix_t g_transform;
static int g_transform_ready = 0;
static double g_fill_alpha = 1.0;
/* A gradient set by fillLinearGradient/fillRadialGradient, used instead
 * of the flat fill colour until the next plain fillStyle() call. */
static cairo_pattern_t *g_fill_gradient = NULL;

/* saveState()/restoreState() save the whole drawing state, not just the
 * transform -- that is what the canvas save()/restore() this mirrors
 * does, and restoring a transform while leaving a colour changed is
 * exactly the kind of half-measure that produces baffling bugs. */
typedef struct {
    cairo_matrix_t transform;
    double fill_r, fill_g, fill_b, alpha;
    int fill_none;
    double border_r, border_g, border_b, line_width;
    int border_set;
    double font_size;
    cairo_font_slant_t font_slant;
    cairo_font_weight_t font_weight;
    char font_family[64];
} FestinaCanvasState;

#define FESTINA_STATE_STACK_MAX 64
static FestinaCanvasState g_state_stack[FESTINA_STATE_STACK_MAX];
static int g_state_depth = 0;

/* The path being built by beginPath()/moveTo()/lineTo()/... A single
 * context is kept open across those calls, since a Cairo path lives on
 * its context and this language's drawing calls are each independent
 * statements. */
static cairo_t *g_path_cr = NULL;

/* Forward declarations: claude.md #94's state helpers are defined
 * alongside the transform/path code further down, but are used by the
 * style setters above it. */
static void festina_clear_gradient(void);
static void festina_graphics_present(void);

/* claude.md #95: the canvas exists WITHOUT a window.
 *
 * Drawing paints onto this image surface, which needs no X server, no
 * display and no window manager. Only render() puts it on screen. That
 * split is what lets a program draw and saveCanvas() headlessly -- on a
 * build server, in a container, over ssh -- and it is also what makes
 * "does this program need a GUI?" a question the compiler can answer by
 * looking for render(), rather than something implied by whether any
 * drawing happens at all. */
static void festina_backing_require(void) {
    if (g_backing_surface) return;
    g_backing_surface = cairo_image_surface_create(
        CAIRO_FORMAT_ARGB32, (int)g_canvas_width, (int)g_canvas_height);
    /* claude.md #136: a fresh canvas starts fully transparent, not
     * opaque white -- the same blank state every clear* function now
     * fills back to (see their own shared comment). Explicit rather
     * than relying on cairo_image_surface_create's own zero-
     * initialization to already mean this, the same "state what this
     * needs, don't assume a library default" choice this codebase
     * already makes elsewhere (e.g. windows.md's own history of
     * exactly this kind of assumption going wrong). CAIRO_OPERATOR_
     * SOURCE for the same reason every clear* function needs it: a
     * transparent source under the default OVER operator would be a
     * no-op, not a real clear. */
    cairo_t *cr = cairo_create(g_backing_surface);
    cairo_set_operator(cr, CAIRO_OPERATOR_SOURCE);
    cairo_set_source_rgba(cr, 0, 0, 0, 0);
    cairo_paint(cr);
    cairo_destroy(cr);
}

static void festina_graphics_require_init(void) {
    if (!g_window_open) {
        festina_fail("a graphics function was called but the canvas window "
                      "was never created (internal compiler error)");
    }
}

/* claude.md #90: colours and fonts arrive here already resolved.
 *
 * fillStyle('red') / font('arial 14px bold') are written as text in
 * Festina source, but the compiler resolves both at compile time
 * (festina/colors.py) -- so this file has no colour-name table, no hex
 * parsing and no font grammar at all, and none of that work happens per
 * draw call. What used to be a string compare against a colour table on
 * every fillStyle() is now three integers already in registers.
 *
 * A NEGATIVE component means "no colour at all" (Festina's
 * 'none'/'transparent'). It needs no extra argument and no second
 * function to say so, because no real channel value can be negative. */
void festina_set_fill_rgb(int64_t r, int64_t g, int64_t b) {
    festina_clear_gradient();  /* claude.md #94: a flat colour replaces any gradient */
    if (r < 0 || g < 0 || b < 0) {
        g_fill_none = 1;
        return;
    }
    g_fill_none = 0;
    g_fill_r = (r > 255 ? 255 : r) / 255.0;
    g_fill_g = (g > 255 ? 255 : g) / 255.0;
    g_fill_b = (b > 255 ? 255 : b) / 255.0;
}

void festina_set_border_rgb(int64_t r, int64_t g, int64_t b) {
    if (r < 0 || g < 0 || b < 0) {
        /* turns the border back off, rather than setting a colour */
        g_border_set = 0;
        return;
    }
    g_border_set = 1;
    g_border_r = (r > 255 ? 255 : r) / 255.0;
    g_border_g = (g > 255 ? 255 : g) / 255.0;
    g_border_b = (b > 255 ? 255 : b) / 255.0;
}

/* claude.md #91: a `color` value is a packed 0xRRGGBB integer, and a
 * negative one means 'none'. Packing is what makes a colour cost one
 * register to pass and one integer compare to test; unpacking is three
 * shift/mask pairs, done once per fillStyle() call. */
void festina_set_fill_color(int64_t packed) {
    if (packed < 0) {
        festina_set_fill_rgb(-1, -1, -1);
        return;
    }
    festina_set_fill_rgb((packed >> 16) & 0xFF, (packed >> 8) & 0xFF, packed & 0xFF);
}

void festina_set_border_color(int64_t packed) {
    if (packed < 0) {
        festina_set_border_rgb(-1, -1, -1);
        return;
    }
    festina_set_border_rgb((packed >> 16) & 0xFF, (packed >> 8) & 0xFF, packed & 0xFF);
}

/* claude.md #91: changeFont(f) hands over a pointer to the static
 * record the compiler emitted for that font's own literal -- read-only
 * data in the binary, never allocated and never freed. A NULL record
 * (a `font` binding that was never given a value) is a no-op rather
 * than a crash, matching how an unset colour simply doesn't paint. */
void festina_set_font_value(const FestinaFont *f) {
    if (!f) return;
    if (f->px > 0) {
        g_font_size = (double)f->px;
    }
    g_font_slant = f->slant ? CAIRO_FONT_SLANT_ITALIC : CAIRO_FONT_SLANT_NORMAL;
    g_font_weight = f->weight ? CAIRO_FONT_WEIGHT_BOLD : CAIRO_FONT_WEIGHT_NORMAL;
    if (f->family) {
        snprintf(g_font_family, sizeof(g_font_family), "%s", f->family);
    }
}

void festina_set_line_width(int64_t width) {
    /* A negative width is meaningless to Cairo (and would silently draw
     * nothing); clamping to 0 keeps "no border" expressible both ways. */
    g_line_width = width < 0 ? 0.0 : (double)width;
}

/* claude.md #90: the canonical three-part form every font() call is
 * compiled into -- size in px, style, family -- with each part
 * independently omittable: a non-positive `px` or a NULL string means
 * "leave that aspect of the font as it is", which is what makes
 * font('14px') change only the size.
 *
 * `style` is normalised by the compiler to NULL/'bold'/'italic'/
 * 'italic bold', so the checks below never have to cope with orderings
 * or spellings. They are substring tests rather than exact compares
 * only because this function is also reachable from the explicit
 * three-argument form (font(14, someText, ...)), where the value is an
 * arbitrary runtime string. */
/* Case-insensitive substring test. Not strcasestr(): that is a GNU
 * extension needing _GNU_SOURCE, and this runtime is deliberately
 * plain C (see festina_runtime.h's top-of-file note on dependencies). */
static int festina_contains_ci(const char *haystack, const char *needle) {
    size_t nlen = strlen(needle);
    if (!nlen) return 1;
    for (const char *h = haystack; *h; h++) {
        size_t i = 0;
        while (i < nlen && h[i] &&
               tolower((unsigned char)h[i]) == tolower((unsigned char)needle[i])) {
            i++;
        }
        if (i == nlen) return 1;
    }
    return 0;
}

void festina_set_font(int64_t px, const char *style, const char *family) {
    if (px > 0) {
        g_font_size = (double)px;
    }
    if (style) {
        g_font_slant = (festina_contains_ci(style, "italic")
                        || festina_contains_ci(style, "oblique"))
                       ? CAIRO_FONT_SLANT_ITALIC : CAIRO_FONT_SLANT_NORMAL;
        g_font_weight = festina_contains_ci(style, "bold")
                        ? CAIRO_FONT_WEIGHT_BOLD : CAIRO_FONT_WEIGHT_NORMAL;
    }
    if (family) {
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
/* claude.md #94: every drawing context starts from the current
 * transform rather than the identity, which is what makes a transform
 * set once apply to everything drawn afterwards. */
static void festina_apply_transform(cairo_t *cr) {
    if (!g_transform_ready) {
        cairo_matrix_init_identity(&g_transform);
        g_transform_ready = 1;
    }
    cairo_set_matrix(cr, &g_transform);
}

static cairo_t *festina_canvas_context(void) {
    cairo_t *cr = cairo_create(g_backing_surface);
    festina_apply_transform(cr);
    return cr;
}

/* Sets the fill source: a gradient when one is active, otherwise the
 * flat colour, in both cases carrying the current alpha. */
static void festina_set_fill_source(cairo_t *cr) {
    if (g_fill_gradient) {
        cairo_set_source(cr, g_fill_gradient);
        if (g_fill_alpha < 1.0) {
            cairo_paint_with_alpha(cr, g_fill_alpha);
        }
        return;
    }
    cairo_set_source_rgba(cr, g_fill_r, g_fill_g, g_fill_b, g_fill_alpha);
}

void festina_set_alpha(double alpha) {
    if (alpha < 0.0) alpha = 0.0;
    if (alpha > 1.0) alpha = 1.0;
    g_fill_alpha = alpha;
}

static void festina_clear_gradient(void) {
    if (g_fill_gradient) {
        cairo_pattern_destroy(g_fill_gradient);
        g_fill_gradient = NULL;
    }
}

static void festina_unpack_rgb(int64_t packed, double *r, double *g, double *b) {
    *r = ((packed >> 16) & 0xFF) / 255.0;
    *g = ((packed >> 8) & 0xFF) / 255.0;
    *b = (packed & 0xFF) / 255.0;
}

/* claude.md #94: a two-stop gradient becomes the fill, replacing the
 * flat colour until the next fillStyle(). Two stops rather than an
 * arbitrary list deliberately: it covers essentially every gradient a
 * program actually draws and needs no new value type to express, where
 * an n-stop version would need a whole gradient object. */
void festina_fill_linear_gradient(int64_t x0, int64_t y0, int64_t c0,
                                   int64_t x1, int64_t y1, int64_t c1) {
    festina_clear_gradient();
    double r0, g0, b0, r1, g1, b1;
    festina_unpack_rgb(c0, &r0, &g0, &b0);
    festina_unpack_rgb(c1, &r1, &g1, &b1);
    g_fill_gradient = cairo_pattern_create_linear((double)x0, (double)y0,
                                                   (double)x1, (double)y1);
    cairo_pattern_add_color_stop_rgb(g_fill_gradient, 0.0, r0, g0, b0);
    cairo_pattern_add_color_stop_rgb(g_fill_gradient, 1.0, r1, g1, b1);
    g_fill_none = 0;
}

void festina_fill_radial_gradient(int64_t x, int64_t y, int64_t radius,
                                   int64_t inner, int64_t outer) {
    festina_clear_gradient();
    double ri, gi, bi, ro, go, bo;
    festina_unpack_rgb(inner, &ri, &gi, &bi);
    festina_unpack_rgb(outer, &ro, &go, &bo);
    if (radius < 0) radius = 0;
    g_fill_gradient = cairo_pattern_create_radial((double)x, (double)y, 0.0,
                                                   (double)x, (double)y, (double)radius);
    cairo_pattern_add_color_stop_rgb(g_fill_gradient, 0.0, ri, gi, bi);
    cairo_pattern_add_color_stop_rgb(g_fill_gradient, 1.0, ro, go, bo);
    g_fill_none = 0;
}

/* ---- claude.md #94: transforms ---- */

void festina_translate(int64_t x, int64_t y) {
    if (!g_transform_ready) { cairo_matrix_init_identity(&g_transform); g_transform_ready = 1; }
    cairo_matrix_translate(&g_transform, (double)x, (double)y);
}

void festina_rotate(double degrees) {
    if (!g_transform_ready) { cairo_matrix_init_identity(&g_transform); g_transform_ready = 1; }
    /* Degrees, not radians: this language has no angle type to make the
     * unit self-documenting, and degrees are what a program author
     * reaches for. Math.PI is available for anyone who wants radians. */
    cairo_matrix_rotate(&g_transform, degrees * 3.14159265358979323846 / 180.0);
}

void festina_scale(double sx, double sy) {
    if (!g_transform_ready) { cairo_matrix_init_identity(&g_transform); g_transform_ready = 1; }
    /* A zero scale collapses the matrix to something non-invertible,
     * which makes every later Cairo call on it fail silently. */
    if (sx == 0.0 || sy == 0.0) return;
    cairo_matrix_scale(&g_transform, sx, sy);
}

void festina_reset_transform(void) {
    cairo_matrix_init_identity(&g_transform);
    g_transform_ready = 1;
}

void festina_save_state(void) {
    if (g_state_depth >= FESTINA_STATE_STACK_MAX) {
        festina_fail("saveState(): nested too deeply (limit 64) -- is a "
                      "restoreState() missing?");
    }
    if (!g_transform_ready) { cairo_matrix_init_identity(&g_transform); g_transform_ready = 1; }
    FestinaCanvasState *st = &g_state_stack[g_state_depth++];
    st->transform = g_transform;
    st->fill_r = g_fill_r; st->fill_g = g_fill_g; st->fill_b = g_fill_b;
    st->alpha = g_fill_alpha; st->fill_none = g_fill_none;
    st->border_r = g_border_r; st->border_g = g_border_g; st->border_b = g_border_b;
    st->line_width = g_line_width; st->border_set = g_border_set;
    st->font_size = g_font_size; st->font_slant = g_font_slant;
    st->font_weight = g_font_weight;
    snprintf(st->font_family, sizeof(st->font_family), "%s", g_font_family);
}

void festina_restore_state(void) {
    if (g_state_depth <= 0) {
        festina_fail("restoreState(): nothing was saved -- every "
                      "restoreState() needs its own saveState() first");
    }
    FestinaCanvasState *st = &g_state_stack[--g_state_depth];
    g_transform = st->transform; g_transform_ready = 1;
    g_fill_r = st->fill_r; g_fill_g = st->fill_g; g_fill_b = st->fill_b;
    g_fill_alpha = st->alpha; g_fill_none = st->fill_none;
    g_border_r = st->border_r; g_border_g = st->border_g; g_border_b = st->border_b;
    g_line_width = st->line_width; g_border_set = st->border_set;
    g_font_size = st->font_size; g_font_slant = st->font_slant;
    g_font_weight = st->font_weight;
    snprintf(g_font_family, sizeof(g_font_family), "%s", st->font_family);
}

/* ---- claude.md #94: paths ---- */

void festina_begin_path(void) {
    festina_backing_require();
    if (g_path_cr) cairo_destroy(g_path_cr);
    g_path_cr = festina_canvas_context();
}

static int festina_path_open(const char *fn) {
    if (g_path_cr) return 1;
    char msg[256];
    snprintf(msg, sizeof(msg),
             "%s(): no path is open -- call beginPath() first", fn);
    festina_fail(msg);
    return 0;
}

void festina_move_to(int64_t x, int64_t y) {
    if (!festina_path_open("moveTo")) return;
    cairo_move_to(g_path_cr, (double)x, (double)y);
}

void festina_line_to(int64_t x, int64_t y) {
    if (!festina_path_open("lineTo")) return;
    cairo_line_to(g_path_cr, (double)x, (double)y);
}

void festina_curve_to(int64_t cx1, int64_t cy1, int64_t cx2, int64_t cy2,
                       int64_t x, int64_t y) {
    if (!festina_path_open("curveTo")) return;
    cairo_curve_to(g_path_cr, (double)cx1, (double)cy1, (double)cx2, (double)cy2,
                    (double)x, (double)y);
}

void festina_close_path(void) {
    if (!festina_path_open("closePath")) return;
    cairo_close_path(g_path_cr);
}

/* Both of these consume the path, matching the canvas model where
 * fill()/stroke() end the current path -- keeping it would make a
 * second fill silently paint the same shape twice. */
void festina_fill_path(void) {
    if (!festina_path_open("fillPath")) return;
    if (!g_fill_none) {
        festina_set_fill_source(g_path_cr);
        cairo_fill(g_path_cr);
    }
    cairo_destroy(g_path_cr);
    g_path_cr = NULL;
}

void festina_stroke_path(void) {
    if (!festina_path_open("strokePath")) return;
    if (g_border_set && g_line_width > 0.0) {
        cairo_set_source_rgba(g_path_cr, g_border_r, g_border_g, g_border_b, g_fill_alpha);
        cairo_set_line_width(g_path_cr, g_line_width);
        cairo_stroke(g_path_cr);
    }
    cairo_destroy(g_path_cr);
    g_path_cr = NULL;
}

static void festina_fill_and_border(cairo_t *cr) {
    int border = g_border_set && g_line_width > 0.0;
    if (!g_fill_none) {
        festina_set_fill_source(cr);
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
        cairo_set_source_rgba(cr, g_border_r, g_border_g, g_border_b, g_fill_alpha);
        cairo_set_line_width(cr, g_line_width);
        cairo_stroke(cr);
    }
}

/* claude.md #133: drawRect(x, y, w, h, color)/drawPixel(x, y, color) --
 * fills with `color` for THIS call only, then restores whatever
 * fillStyle (flat colour or active gradient) was already set, so a
 * one-off override never leaks into the next plain drawRect()/
 * drawCircle()/... call. Border/alpha are untouched either way, since
 * only the FILL colour is what these two ever override -- the same
 * split fillStyle()/borderColor() already keep separate. `color < 0`
 * is `color`'s own 'none' encoding (claude.md #91), so this call paints
 * nothing, exactly like fillStyle('none') would. */
/* claude.md #234 (uraikus/festina#93): these per-call overrides used to
 * SAVE, OVERWRITE and RESTORE the global fill/border state around a
 * call to festina_fill_and_border -- fine on one thread, and a real
 * data race the moment a worker thread paints its own layer with
 * `layer.drawRect(..., color)` while main draws anything with a colour
 * of its own (found by ThreadSanitizer the first time a thread and
 * main both used the colour form at once). The override colours are
 * passed in and the globals only READ now, exactly the shape
 * festina_image_draw_pixel_color always had. Semantics unchanged: an
 * explicit fill colour bypasses any active gradient for this one call,
 * a negative colour is `none`, and neither override touches
 * g_line_width. */
static void festina_fill_and_border_override(cairo_t *cr,
                                             int64_t fill_color, int fill_overridden,
                                             int64_t border_color, int border_overridden) {
    int fill_none = fill_overridden ? (fill_color < 0) : g_fill_none;
    int border_set = border_overridden ? (border_color >= 0) : g_border_set;
    int border = border_set && g_line_width > 0.0;
    if (!fill_none) {
        if (fill_overridden) {
            double r, g, b;
            festina_unpack_rgb(fill_color, &r, &g, &b);
            cairo_set_source_rgba(cr, r, g, b, g_fill_alpha);
        } else {
            festina_set_fill_source(cr);
        }
        if (border) {
            cairo_fill_preserve(cr);
        } else {
            cairo_fill(cr);
        }
    } else if (!border) {
        cairo_new_path(cr);
        return;
    }
    if (border) {
        double r = g_border_r, g = g_border_g, b = g_border_b;
        if (border_overridden) festina_unpack_rgb(border_color, &r, &g, &b);
        cairo_set_source_rgba(cr, r, g, b, g_fill_alpha);
        cairo_set_line_width(cr, g_line_width);
        cairo_stroke(cr);
    }
}

static void festina_fill_and_border_with_color(cairo_t *cr, int64_t color) {
    festina_fill_and_border_override(cr, color, 1, 0, 0);
}

/* claude.md #188 (uraikus/festina#76 item 8): drawRect(x, y, w, h,
 * fillColor, borderColor)/drawCircle(x, y, r, fillColor, borderColor)
 * -- the border-colour counterpart to festina_fill_and_border_with_
 * color just above, overriding BOTH colours for this call only, then
 * restoring whatever fillStyle()/borderColor() were already set to.
 * This is what closes the "global, mutable draw style silently leaks
 * between shapes" gap #76 itself reported: a border colour left over
 * from a previous, unrelated draw call no longer has to be reset by
 * hand (or via saveState()/restoreState()) before every shape that
 * needs its own.
 *
 * `border_color < 0` means no border for this one call, matching
 * borderColor('none')'s own encoding (claude.md #91) -- and, like
 * festina_fill_and_border_with_color's own fill-only override, this
 * does NOT also touch g_line_width: a border colour given while
 * lineWidth() is still 0 draws nothing, the exact same "nothing to
 * stroke" case plain borderColor() already has, not a new special
 * case to invent here. */
static void festina_fill_and_border_with_colors(cairo_t *cr, int64_t fill_color, int64_t border_color) {
    /* claude.md #234: no global state is written here any more -- see
     * festina_fill_and_border_override's own comment above. */
    festina_fill_and_border_override(cr, fill_color, 1, border_color, 1);
}

/* claude.md #123: opens the platform window through the seam, exactly
 * once -- self-guarding (returns immediately if already open) rather
 * than relying on every call site to check first, since this is a
 * public runtime entry point with more than one caller (festina_render,
 * festina_run_event_loop). Portable now -- every platform-specific
 * concern (connect retries, decorations, input focus, ...) lives in
 * that platform's own festina_window_open implementation; see
 * festina_runtime_window.h.
 *
 * Opens at g_canvas_width/g_canvas_height AS THEY ALREADY STAND, not the
 * hardcoded FESTINA_CANVAS_WIDTH/_HEIGHT default -- claude.md #178 (see
 * uraikus/festina#79): festina_set_client_size already lets
 * setClientWidth/setClientHeight update those globals before any window
 * exists (it only touches the live window/backing store inside its own
 * `if (g_window_open)` branch), so a program that calls either near the
 * top of its own boot sequence -- the documented, `on resize`-safe
 * pattern #75 recommends -- had that request silently overwritten back
 * to 800x600 right here, every time, then "corrected" a moment later by
 * whatever real resize festina_set_client_size fires once the window
 * DOES exist. Reading the current globals instead means a size chosen
 * before the window opens is simply the window's initial size, with no
 * flash of the wrong dimensions and no spurious `on resize` firing for
 * a size the program never actually asked to see. */
void festina_graphics_init(void) {
    if (g_window_open) return;
    festina_window_open(g_canvas_width, g_canvas_height, "Festina");
    g_window_open = 1;
    /* claude.md #180: apply an enterFullscreen() call that already ran
     * before the window existed -- see g_is_fullscreen's own comment. */
    if (g_is_fullscreen) festina_window_set_fullscreen(1);
    /* claude.md #182: apply a hideCursor() call that already ran before
     * the window existed -- see g_cursor_visible's own comment. Only
     * the HIDE case needs applying: a freshly opened window's own
     * native cursor already starts visible, matching g_cursor_visible's
     * own default. */
    if (!g_cursor_visible) festina_window_set_cursor_visible(0);
    /* claude.md #95: whatever was already drawn headlessly is kept --
     * a program may well have drawn before its first render(). */
    festina_backing_require();
}

/* claude.md #180: enterFullscreen()/exitFullscreen() -- see
 * g_is_fullscreen's own comment for why the same flag tracks both "what
 * was requested before a window existed" and "what's true now that one
 * does", and festina_runtime_window.h's own doc comment on
 * festina_window_set_fullscreen for why the resulting size change
 * surfaces asynchronously (a real RESIZE event, on the next
 * events_drain pump) rather than synchronously the way setClientWidth/
 * setClientHeight's own g_canvas_width/height update is. No-ops if
 * already in the requested state, the same guard festina_set_client_
 * size already uses for a same-value call -- redundantly calling the
 * platform seam twice would be at best wasted work and at worst (X11's
 * ClientMessage toggle-by-convention on some window managers) a second,
 * unwanted state flip. */
void festina_enter_fullscreen(void) {
    if (g_is_fullscreen) return;
    g_is_fullscreen = 1;
    if (g_window_open) festina_window_set_fullscreen(1);
}

void festina_exit_fullscreen(void) {
    if (!g_is_fullscreen) return;
    g_is_fullscreen = 0;
    if (g_window_open) festina_window_set_fullscreen(0);
}

/* claude.md #182: showCursor()/hideCursor() -- the identical shape
 * enterFullscreen()/exitFullscreen() just above already established:
 * g_cursor_visible tracks both the pre-window desired state and the
 * live one, festina_graphics_init applies a hidden request once a
 * window actually opens (see its own comment), and each call here
 * no-ops if already in the requested state. Unlike fullscreen, this
 * doesn't need to force a window open (see codegen.py's own
 * _CANVAS_OPS handling) -- a cursor is meaningless with no window, but
 * that's a reason to let the call be a harmless no-op, not a reason to
 * open one just to hide nothing over it. */
void festina_show_cursor(void) {
    if (g_cursor_visible) return;
    g_cursor_visible = 1;
    if (g_window_open) festina_window_set_cursor_visible(1);
}

void festina_hide_cursor(void) {
    if (!g_cursor_visible) return;
    g_cursor_visible = 0;
    if (g_window_open) festina_window_set_cursor_visible(0);
}

/* claude.md #93: writes the canvas to a PNG. Cairo's PNG *writer* has
 * been compiled into every build this language already links against
 * for loadImage's reader (CAIRO_HAS_PNG_FUNCTIONS covers both), so this
 * is one call against a dependency already present -- no new library,
 * and no encoder to write.
 *
 * Saves the BACKING surface, not the window: that is the source of
 * truth for everything drawn (see festina_graphics_present), so the
 * result is what the program drew rather than whatever happened to be
 * unobscured on screen. */
int8_t festina_save_canvas(const char *path) {
    if (!path) return 0;
    festina_backing_require();
    cairo_status_t st = cairo_surface_write_to_png(g_backing_surface, path);
    return st == CAIRO_STATUS_SUCCESS ? 1 : 0;
}

/* claude.md #95: puts the canvas on screen, opening the window the
 * first time it is called.
 *
 * Drawing alone never reaches a display -- it paints the offscreen
 * canvas. This is the one call that needs a GUI, which is exactly why
 * it is separate: a program that draws and saves a PNG never calls it,
 * so it never opens a window, never enters an event loop, and runs
 * anywhere. It also fixes the cost of drawing: every shape used to blit
 * the whole canvas and flush X, so a frame of 100 sprites was 100 full-
 * canvas round trips. Now a frame is however many draw calls it takes,
 * plus one render(). */
void festina_render(void) {
    if (!g_window_open) festina_graphics_init();
    festina_backing_require();
    festina_graphics_present();
}

/* claude.md #136: every clear* function below fills with FULLY
 * TRANSPARENT pixels, not opaque white -- matching the HTML5 canvas
 * model these calls otherwise already mirror (a fresh or cleared
 * <canvas> is transparent, not white), and carrying through to
 * saveCanvas()'s real alpha channel (claude.md #93's own PNG writer
 * already round-trips ARGB32 faithfully; nothing there needed to
 * change for this).
 *
 * Cairo's DEFAULT compositing operator (CAIRO_OPERATOR_OVER) treats a
 * fully-transparent source as a no-op: result = src*alpha + dst*(1-
 * alpha), which is just `dst` unchanged when alpha is 0 -- painting
 * "nothing" over existing content does not erase it. Genuinely
 * replacing pixels with transparent ones needs CAIRO_OPERATOR_SOURCE,
 * which replaces the destination outright regardless of source alpha.
 * Scoped to each function's own short-lived `cr` (created and
 * destroyed within it, same as every other draw/clear function here),
 * so there is nothing to restore afterward. */
void festina_clear_canvas(void) {
    festina_backing_require();
    cairo_t *cr = cairo_create(g_backing_surface);
    /* Deliberately NOT the current transform: clearing is about the
     * canvas itself, and a rotated "clear everything" that leaves
     * wedges behind would be a trap rather than a feature. */
    cairo_set_operator(cr, CAIRO_OPERATOR_SOURCE);
    cairo_set_source_rgba(cr, 0, 0, 0, 0);
    cairo_paint(cr);
    cairo_destroy(cr);
}

/* Erases one rectangle to transparent. Unlike clearCanvas this DOES
 * honour the current transform, since it names a region in the same
 * coordinates the drawing calls around it use. */
void festina_clear_rect(int64_t x, int64_t y, int64_t w, int64_t h) {
    festina_backing_require();
    cairo_t *cr = festina_canvas_context();
    cairo_set_operator(cr, CAIRO_OPERATOR_SOURCE);
    cairo_set_source_rgba(cr, 0, 0, 0, 0);
    cairo_rectangle(cr, (double)x, (double)y, (double)w, (double)h);
    cairo_fill(cr);
    cairo_destroy(cr);
}

/* claude.md #133: clearRect()'s own circle-shaped counterpart -- erases
 * to transparent, honouring the current transform exactly as clearRect
 * does. No fast-path mask cache the way drawCircle has one: clearing is
 * a far rarer call than drawing, so the extra machinery would cost more
 * to maintain than it would ever save here. */
void festina_clear_circle(int64_t x, int64_t y, int64_t r) {
    festina_backing_require();
    cairo_t *cr = festina_canvas_context();
    if (r < 0) r = 0;
    cairo_set_operator(cr, CAIRO_OPERATOR_SOURCE);
    cairo_set_source_rgba(cr, 0, 0, 0, 0);
    cairo_arc(cr, (double)x, (double)y, (double)r, 0.0, 2.0 * 3.14159265358979323846);
    cairo_fill(cr);
    cairo_destroy(cr);
}

/* claude.md #133: clearRect()'s own single-pixel counterpart -- see
 * festina_draw_pixel just below for why antialiasing is disabled
 * around the fill. */
void festina_clear_pixel(int64_t x, int64_t y) {
    festina_backing_require();
    cairo_t *cr = festina_canvas_context();
    cairo_antialias_t save_aa = cairo_get_antialias(cr);
    cairo_set_antialias(cr, CAIRO_ANTIALIAS_NONE);
    cairo_set_operator(cr, CAIRO_OPERATOR_SOURCE);
    cairo_set_source_rgba(cr, 0, 0, 0, 0);
    cairo_rectangle(cr, (double)x, (double)y, 1, 1);
    cairo_fill(cr);
    cairo_set_antialias(cr, save_aa);
    cairo_destroy(cr);
}

/* Blits the backing store (source of truth for what's been drawn) onto
 * the visible window, through the seam -- called after every draw call
 * for immediate feedback. A backend's own redraw-on-expose (an X11
 * Expose event, an NSView drawRect:) never comes back through here; it
 * repaints from the surface festina_window_present last remembered,
 * entirely inside that backend -- see festina_runtime_window.h. */
static void festina_graphics_present(void) {
    /* claude.md #95: nothing to present to until render() has opened a
     * window -- drawing headlessly is not an error, it just has no
     * screen to reach. */
    if (!g_window_open || !g_backing_surface) return;
    festina_window_present(g_backing_surface);
}

void festina_draw_rect(int64_t x, int64_t y, int64_t w, int64_t h) {
    festina_backing_require();
    cairo_t *cr = festina_canvas_context();
    cairo_rectangle(cr, (double)x, (double)y, (double)w, (double)h);
    festina_fill_and_border(cr); /* claude.md #89 */
    cairo_destroy(cr);
}

/* claude.md #133: drawRect(x, y, w, h, color) -- see
 * festina_fill_and_border_with_color's own comment for the save/
 * restore-fillStyle semantics. */
void festina_draw_rect_color(int64_t x, int64_t y, int64_t w, int64_t h, int64_t color) {
    festina_backing_require();
    cairo_t *cr = festina_canvas_context();
    cairo_rectangle(cr, (double)x, (double)y, (double)w, (double)h);
    festina_fill_and_border_with_color(cr, color);
    cairo_destroy(cr);
}

/* claude.md #188 (uraikus/festina#76 item 8): drawRect(x, y, w, h,
 * fillColor, borderColor) -- see festina_fill_and_border_with_colors'
 * own comment. */
void festina_draw_rect_colors(int64_t x, int64_t y, int64_t w, int64_t h,
                               int64_t fill_color, int64_t border_color) {
    festina_backing_require();
    cairo_t *cr = festina_canvas_context();
    cairo_rectangle(cr, (double)x, (double)y, (double)w, (double)h);
    festina_fill_and_border_with_colors(cr, fill_color, border_color);
    cairo_destroy(cr);
}

/* claude.md #133: a single pixel, filled with the current fillStyle.
 * Antialiasing is disabled around the fill so an integer-aligned 1x1
 * rectangle paints exactly one pixel deterministically -- with it left
 * on, Cairo blends a sub-pixel-positioned edge even for whole-number
 * coordinates, which would make a "pixel" a faint smudge instead of one
 * solid pixel. No border: a 1x1 shape has nothing meaningful to
 * stroke, unlike drawRect/drawCircle. */
void festina_draw_pixel(int64_t x, int64_t y) {
    festina_backing_require();
    cairo_t *cr = festina_canvas_context();
    cairo_antialias_t save_aa = cairo_get_antialias(cr);
    cairo_set_antialias(cr, CAIRO_ANTIALIAS_NONE);
    cairo_rectangle(cr, (double)x, (double)y, 1, 1);
    if (!g_fill_none) {
        festina_set_fill_source(cr);
        cairo_fill(cr);
    } else {
        cairo_new_path(cr);
    }
    cairo_set_antialias(cr, save_aa);
    cairo_destroy(cr);
}

/* claude.md #133: drawPixel(x, y, color) -- `color` for this one pixel
 * only, the same per-call override drawRect_color makes, but simpler:
 * a single pixel is never a gradient, so there is no fillStyle state to
 * save and restore around it at all. */
void festina_draw_pixel_color(int64_t x, int64_t y, int64_t color) {
    festina_backing_require();
    cairo_t *cr = festina_canvas_context();
    cairo_antialias_t save_aa = cairo_get_antialias(cr);
    cairo_set_antialias(cr, CAIRO_ANTIALIAS_NONE);
    cairo_rectangle(cr, (double)x, (double)y, 1, 1);
    if (color >= 0) {
        double r, g, b;
        festina_unpack_rgb(color, &r, &g, &b);
        cairo_set_source_rgba(cr, r, g, b, g_fill_alpha);
        cairo_fill(cr);
    } else {
        cairo_new_path(cr);
    }
    cairo_set_antialias(cr, save_aa);
    cairo_destroy(cr);
}

/* claude.md #189 (getPixelColor): reads one pixel back off an ARGB32
 * surface (the canvas's own backing store, or an img's) as a packed
 * `color` -- the exact reverse of festina_unpack_rgb, and the direct
 * runtime counterpart of `pack_color`'s own `(r<<16)|(g<<8)|b` in
 * codegen.py (claude.md #91).
 *
 * `cairo_surface_flush` first: direct pixel access needs every pending
 * drawing operation actually committed to memory first, the same
 * requirement claude.md #178's own mac/Windows present-path flush
 * fixed for reading a window surface -- here it's the offscreen
 * backing/image surface instead, but the underlying Cairo contract is
 * identical.
 *
 * Out of bounds, or a NULL surface (an img handle that was never
 * given one), reads as `color`'s own 'none' -- consistent with
 * `festina_image_clip`'s own "past the edge is simply not there"
 * rule, not a crash.
 *
 * ARGB32 stores PREMULTIPLIED alpha (Cairo's own documented format),
 * so a translucent pixel's stored R/G/B are already scaled down by its
 * own alpha -- e.g. opaque red drawn at fillAlpha(0.5) over nothing
 * stores roughly half-brightness red, not full red. Dividing back out
 * by alpha (rounding to the nearest integer, not truncating) is what
 * makes getPixelColor answer the colour that was actually PAINTED,
 * not one darkened by whatever fillAlpha happened to be in effect
 * when it landed. A fully transparent pixel (alpha 0, nothing ever
 * painted there, or painted then cleared) has no real colour to
 * recover at all -- premultiplied R/G/B are always 0/0/0 regardless of
 * what was last set, so this reads it as 'none' rather than a
 * meaningless black. */
static int64_t festina_pixel_color_from_surface(cairo_surface_t *surface, int64_t x, int64_t y) {
    if (!surface) return -1;
    int w = cairo_image_surface_get_width(surface);
    int h = cairo_image_surface_get_height(surface);
    if (x < 0 || y < 0 || x >= w || y >= h) return -1;
    cairo_surface_flush(surface);
    int stride = cairo_image_surface_get_stride(surface);
    const unsigned char *data = cairo_image_surface_get_data(surface);
    if (!data) return -1;
    uint32_t px;
    memcpy(&px, data + (int64_t)y * stride + (int64_t)x * 4, sizeof(px));
    /* claude.md #192: a JPEG-loaded image (claude.md #101) is a
     * CAIRO_FORMAT_RGB24 surface -- a 32-bit pixel whose top byte is
     * unused and stored as 0, NOT an alpha channel. Reading that top
     * byte as alpha would make every pixel of a JPEG read back as
     * fully transparent (alpha 0 -> the -1/'none' sentinel below). For
     * RGB24 the pixel is always fully opaque; only ARGB32 surfaces
     * (the canvas, PNGs, clips, resizes) carry real premultiplied
     * alpha to unpack. */
    uint32_t a;
    if (cairo_image_surface_get_format(surface) == CAIRO_FORMAT_RGB24) {
        a = 255;
    } else {
        a = (px >> 24) & 0xff;
        if (a == 0) return -1;
    }
    uint32_t r = (px >> 16) & 0xff;
    uint32_t g = (px >> 8) & 0xff;
    uint32_t b = px & 0xff;
    if (a < 255) {
        r = (r * 255 + a / 2) / a;
        g = (g * 255 + a / 2) / a;
        b = (b * 255 + a / 2) / a;
    }
    return ((int64_t)r << 16) | ((int64_t)g << 8) | (int64_t)b;
}

int64_t festina_get_pixel_color(int64_t x, int64_t y) {
    festina_backing_require();
    return festina_pixel_color_from_surface(g_backing_surface, x, y);
}

/* claude.md #104: filled circles, rasterized once per radius.
 *
 * cairo_arc + cairo_fill tessellates the curve into Beziers and
 * scan-converts a general polygon EVERY TIME. Measured on the canvas
 * benchmark, that was 90% of the whole frame: 20,000 circles cost 76 ms
 * against 10 ms for the same number of rectangles. Rasterizing the
 * circle once into an A8 alpha mask and stamping it thereafter is the
 * same trick a glyph cache uses, and it is 4.4x faster on that
 * workload.
 *
 * The cache is keyed on radius, which is an int in the language, so
 * there is nothing to quantize and no rounding to get wrong. It is
 * small and fixed: a program drawing circles draws a handful of sizes
 * over and over (particles, bullets, dots), and one that genuinely uses
 * hundreds of distinct radii gets the slow path rather than an
 * unbounded cache.
 *
 * Verified pixel-identical against tessellation for every radius from 1
 * to 20 -- zero differing pixels -- and one channel off by one at r=40.
 * That exactness is not luck: drawCircle takes an integer centre and
 * radius, so the mask always lands on whole-pixel boundaries. The
 * moment that stops being true the fast path is skipped, which is what
 * the transform check below is for. */
#define FESTINA_CIRCLE_CACHE_SIZE 16
#define FESTINA_CIRCLE_CACHE_MAX_RADIUS 128

typedef struct {
    int64_t radius;
    cairo_surface_t *mask;
} FestinaCircleMask;

static FestinaCircleMask g_circle_masks[FESTINA_CIRCLE_CACHE_SIZE];
static int g_circle_mask_next = 0;   /* round-robin eviction */

static cairo_surface_t *festina_circle_mask(int64_t r) {
    for (int i = 0; i < FESTINA_CIRCLE_CACHE_SIZE; i++) {
        if (g_circle_masks[i].mask && g_circle_masks[i].radius == r) {
            return g_circle_masks[i].mask;
        }
    }
    int size = (int)(r * 2) + 2;
    cairo_surface_t *mask = cairo_image_surface_create(CAIRO_FORMAT_A8, size, size);
    if (cairo_surface_status(mask) != CAIRO_STATUS_SUCCESS) {
        cairo_surface_destroy(mask);
        return NULL;
    }
    cairo_t *mc = cairo_create(mask);
    cairo_set_source_rgba(mc, 0.0, 0.0, 0.0, 1.0);
    cairo_arc(mc, size / 2.0, size / 2.0, (double)r, 0.0, 2.0 * 3.14159265358979323846);
    cairo_fill(mc);
    cairo_destroy(mc);

    /* Round-robin rather than least-recently-used: the working set that
     * matters is "the few sizes this program draws", which any eviction
     * policy keeps resident once the cache is warm, and LRU bookkeeping
     * would cost more per stamp than it could ever save. */
    FestinaCircleMask *slot = &g_circle_masks[g_circle_mask_next];
    g_circle_mask_next = (g_circle_mask_next + 1) % FESTINA_CIRCLE_CACHE_SIZE;
    if (slot->mask) cairo_surface_destroy(slot->mask);
    slot->radius = r;
    slot->mask = mask;
    return mask;
}

/* The fast path is only correct while the mask lands exactly where a
 * tessellated circle would. A scale or a rotation would resample a
 * pre-rasterized bitmap -- blurry, and visibly different from a curve
 * rasterized at that size -- and a fractional translation would land it
 * off the pixel grid. So: no rotation, no scale, whole-number
 * translation, and no border (a stroke needs a real path). Anything
 * else falls back, which costs one matrix read. */
static int festina_circle_fast_path_ok(int64_t r) {
    if (r <= 0 || r > FESTINA_CIRCLE_CACHE_MAX_RADIUS) return 0;
    if (g_border_set && g_line_width > 0.0) return 0;
    if (g_fill_none) return 0;
    if (!g_transform_ready) return 1;                  /* identity */
    if (g_transform.xx != 1.0 || g_transform.yy != 1.0) return 0;
    if (g_transform.xy != 0.0 || g_transform.yx != 0.0) return 0;
    return g_transform.x0 == floor(g_transform.x0) && g_transform.y0 == floor(g_transform.y0);
}

void festina_draw_circle(int64_t x, int64_t y, int64_t r) {
    festina_backing_require();
    cairo_t *cr = festina_canvas_context();
    if (festina_circle_fast_path_ok(r)) {
        cairo_surface_t *mask = festina_circle_mask(r);
        if (mask) {
            int size = cairo_image_surface_get_width(mask);
            festina_set_fill_source(cr);
            cairo_mask_surface(cr, mask, (double)x - size / 2.0, (double)y - size / 2.0);
            cairo_destroy(cr);
            return;
        }
    }
    cairo_arc(cr, (double)x, (double)y, (double)r, 0.0, 2.0 * 3.14159265358979323846);
    festina_fill_and_border(cr); /* claude.md #89 */
    cairo_destroy(cr);
}

/* claude.md #188 (uraikus/festina#76 item 8): drawCircle(x, y, r,
 * fillColor)/drawCircle(x, y, r, fillColor, borderColor) -- same
 * per-call override as drawRect's own color/colors forms; no fast-path
 * mask cache here (unlike plain festina_draw_circle just above) since
 * an occasional colour override is not the hot path that optimization
 * exists for. */
void festina_draw_circle_color(int64_t x, int64_t y, int64_t r, int64_t color) {
    festina_backing_require();
    cairo_t *cr = festina_canvas_context();
    cairo_arc(cr, (double)x, (double)y, (double)(r < 0 ? 0 : r), 0.0, 2.0 * 3.14159265358979323846);
    festina_fill_and_border_with_color(cr, color);
    cairo_destroy(cr);
}

void festina_draw_circle_colors(int64_t x, int64_t y, int64_t r,
                                 int64_t fill_color, int64_t border_color) {
    festina_backing_require();
    cairo_t *cr = festina_canvas_context();
    cairo_arc(cr, (double)x, (double)y, (double)(r < 0 ? 0 : r), 0.0, 2.0 * 3.14159265358979323846);
    festina_fill_and_border_with_colors(cr, fill_color, border_color);
    cairo_destroy(cr);
}

void festina_draw_text(const char *text, int64_t x, int64_t y) {
    festina_backing_require();
    if (!text) text = "";
    cairo_t *cr = festina_canvas_context();
    /* claude.md #89: drawn in the current fill colour and font. Text is
     * filled only -- borderColor outlines shapes, not glyphs. */
    if (g_fill_none) { cairo_destroy(cr); return; }
    cairo_set_source_rgba(cr, g_fill_r, g_fill_g, g_fill_b, g_fill_alpha);
    festina_apply_font(cr);
    cairo_move_to(cr, (double)x, (double)y);
    cairo_show_text(cr, text);
    cairo_destroy(cr);
}

/* claude.md #92: an `img` value is a pointer to one of these, not the
 * Cairo surface directly. The indirection is what makes resize() work
 * the way it reads -- `grass.resize(32, 32)` is a statement, so it has
 * to change `grass` itself, and a Cairo surface cannot be resized in
 * place. Boxing the surface means every binding that shares an image
 * sees the new one, exactly as they shared the old. */
typedef struct {
    cairo_surface_t *surface;
    /* claude.md #101: the bytes this image was LOADED from, kept so a
     * `file:img` table column round-trips byte for byte rather than
     * being re-encoded. NULL for an image that never came from a file
     * (a clip() or a resize() result, or one decoded from a blob that
     * has since been resized) -- festina_image_bytes encodes PNG on
     * demand in that case, and caches it here. Usually SMALLER than
     * the decoded surface it sits next to: a 128x64 PNG is a couple of
     * kilobytes against 32KB of ARGB32, so keeping it is a modest
     * overhead rather than a doubling. */
    unsigned char *bytes;
    size_t byte_count;
    /* claude.md #110: the path this image was loaded from, so save()
     * with no argument has somewhere to write. Empty (never NULL, so
     * the shared festina_save_bytes need not special-case it) for an
     * image that never came from a file -- a clip() or resize() result,
     * or one decoded out of a database column. That is precisely the
     * case save(path) exists for, and the case save() refuses. */
    char *path;
    /* claude.md #234 (uraikus/festina#93): this image's OWN transform
     * (identity until the first img.translate()/rotate()/scale();
     * `transform_ready` is the same lazy-init flag the canvas's
     * g_transform_ready is) and its own saveState()/restoreState()
     * stack of transforms. Completely independent of the canvas's
     * g_transform -- an image is a portable asset with its own local
     * coordinates -- and private to this one image, so a worker thread
     * drawing into its own layer never touches shared state. The stack
     * is allocated on the first img.saveState() and grows as needed
     * (most images never save at all; a fixed 64-slot array of
     * matrices would cost every 16x16 sprite 3KB it never uses). */
    cairo_matrix_t transform;
    int transform_ready;
    cairo_matrix_t *state_stack;
    int state_depth;
    int state_cap;
} FestinaImageBox;

/* claude.md #118: the box is REFERENCE COUNTED now, behind the same
 * i64 header immediately before the payload that structs/arrays/maps/
 * blobs carry (festina_retain/festina_release_check in the core
 * runtime). That is what turned `free` on an aliased img from the
 * documented dangling-alias hazard into an ordinary decrement, and
 * what lets an escaping handle be released by every binding that held
 * it instead of leaking. */
static FestinaImageBox *festina_image_box(cairo_surface_t *surface) {
    char *raw = calloc(1, sizeof(int64_t) + sizeof(FestinaImageBox));
    if (!raw) festina_fail("out of memory creating an image");
    *(int64_t *)raw = 1;
    FestinaImageBox *box = (FestinaImageBox *)(raw + sizeof(int64_t));
    box->surface = surface;
    box->path = strdup("");   /* claude.md #110: no path until one is given */
    if (!box->path) festina_fail("out of memory creating an image");
    return box;
}

/* Both dimensions must be positive: Cairo would accept 0 and hand back
 * a surface nothing can ever draw, which is a silent no-op rather than
 * the mistake it almost certainly is. */
static void festina_check_image_size(const char *fn, int64_t w, int64_t h) {
    if (w > 0 && h > 0) return;
    char msg[256];
    snprintf(msg, sizeof(msg),
             "%s(): width and height must both be positive, got %lldx%lld",
             fn, (long long)w, (long long)h);
    festina_fail(msg);
}

/* claude.md #101: decoding from MEMORY is the primitive now, and
 * loading a path is "read the file, then decode the bytes". That is
 * what lets an `img` come out of a sqlite BLOB column as easily as out
 * of a file -- the two paths differ only in where the bytes came from.
 *
 * PNG goes through Cairo's own decoder (via a stream callback, since
 * Cairo has no decode-this-buffer entry point) and JPEG through
 * libjpeg. Sniffing is by magic bytes rather than by file extension:
 * a blob out of a database has no extension, and an extension was
 * never evidence of anything anyway. */

typedef struct {
    const unsigned char *data;
    size_t len;
    size_t pos;
} FestinaByteReader;

static cairo_status_t festina_png_read(void *closure, unsigned char *out, unsigned int len) {
    FestinaByteReader *r = (FestinaByteReader *)closure;
    if (r->pos + len > r->len) return CAIRO_STATUS_READ_ERROR;
    memcpy(out, r->data + r->pos, len);
    r->pos += len;
    return CAIRO_STATUS_SUCCESS;
}

/* libjpeg's error handler exits the process by default, which would
 * turn a corrupt image into a silent death with no Festina-level
 * message. This one longjmps back into the decoder below so the
 * failure can be reported the same way every other load failure is. */
struct festina_jpeg_error {
    struct jpeg_error_mgr base;
    jmp_buf escape;
};

static void festina_jpeg_fail(j_common_ptr info) {
    longjmp(((struct festina_jpeg_error *)info->err)->escape, 1);
}

static cairo_surface_t *festina_decode_jpeg(const unsigned char *data, size_t len) {
    struct jpeg_decompress_struct info;
    struct festina_jpeg_error err;
    /* claude.md #192: both locals are modified between setjmp and the
     * longjmps below, and read again in the setjmp-return cleanup path,
     * so they MUST be volatile -- C11 7.13.2.1 leaves a non-volatile
     * local's value indeterminate after longjmp, and at -O2 clang keeps
     * them in registers that the longjmp restores to their pre-decode
     * NULLs, silently leaking the decoded surface and scanline whenever
     * a truncated/corrupt JPEG errors mid-decode (the graceful
     * corrupt-image .callback() path, claude.md #172). */
    cairo_surface_t * volatile surface = NULL;
    unsigned char * volatile scanline = NULL;

    info.err = jpeg_std_error(&err.base);
    err.base.error_exit = festina_jpeg_fail;
    if (setjmp(err.escape)) {
        jpeg_destroy_decompress(&info);
        free(scanline);
        if (surface) cairo_surface_destroy(surface);
        return NULL;
    }

    jpeg_create_decompress(&info);
    jpeg_mem_src(&info, data, (unsigned long)len);
    if (jpeg_read_header(&info, TRUE) != JPEG_HEADER_OK) longjmp(err.escape, 1);
    /* Ask for plain RGB regardless of what the file actually is
     * (greyscale, CMYK, YCbCr) -- libjpeg converts, and one output
     * shape means one conversion loop below instead of four. */
    info.out_color_space = JCS_RGB;
    jpeg_start_decompress(&info);

    surface = cairo_image_surface_create(CAIRO_FORMAT_RGB24,
                                          (int)info.output_width, (int)info.output_height);
    if (cairo_surface_status(surface) != CAIRO_STATUS_SUCCESS) longjmp(err.escape, 1);
    unsigned char *pixels = cairo_image_surface_get_data(surface);
    int stride = cairo_image_surface_get_stride(surface);
    scanline = malloc((size_t)info.output_width * 3);
    if (!scanline) longjmp(err.escape, 1);

    while (info.output_scanline < info.output_height) {
        unsigned char *rows[1] = { scanline };
        int y = (int)info.output_scanline;
        jpeg_read_scanlines(&info, rows, 1);
        /* CAIRO_FORMAT_RGB24 is a 32-bit pixel with the top byte
         * unused, laid out natively -- so on a little-endian target
         * (the only kind this runtime targets, same assumption the WAV
         * loader already makes) the bytes go B, G, R, x. */
        uint32_t *out = (uint32_t *)(pixels + (size_t)y * (size_t)stride);
        for (unsigned int x = 0; x < info.output_width; x++) {
            out[x] = ((uint32_t)scanline[x * 3] << 16) |
                     ((uint32_t)scanline[x * 3 + 1] << 8) |
                     (uint32_t)scanline[x * 3 + 2];
        }
    }

    jpeg_finish_decompress(&info);
    jpeg_destroy_decompress(&info);
    free(scanline);
    cairo_surface_mark_dirty(surface);
    return surface;
}

/* claude.md #171: festina_image_from_bytes's own decode step, pulled
 * out so a background worker thread (see festina_image_load_worker
 * below) can share it -- unlike festina_image_from_bytes itself, this
 * NEVER calls festina_fail, on any input: empty, a format it doesn't
 * recognize, or genuinely corrupt PNG/JPEG data all just come back as
 * NULL, with `*out_recognized_format` telling the two "no image" cases
 * apart for the caller's own error message (festina_image_from_bytes
 * below is unchanged, just now this plus the fail() calls its own
 * synchronous-path contract has always made). Cairo/libjpeg decoding
 * into a fresh, private surface and scratch buffers here touches no
 * shared mutable state (no font/text subsystem, no shared cairo_t,
 * nothing this runtime's own g_backing_surface or any window touches)
 * -- confirmed safe to call from several threads at once by a real
 * concurrent ThreadSanitizer run (see test_async_io.py's own img/aud
 * coverage), not just by inspection. */
static cairo_surface_t *festina_decode_image_surface(const unsigned char *bytes, int64_t len,
                                                     int *out_recognized_format) {
    if (out_recognized_format) *out_recognized_format = 0;
    if (!bytes || len <= 0) return NULL;
    if (len >= 8 && memcmp(bytes, "\x89PNG\r\n\x1a\n", 8) == 0) {
        if (out_recognized_format) *out_recognized_format = 1;
        FestinaByteReader reader = { bytes, (size_t)len, 0 };
        cairo_surface_t *img = cairo_image_surface_create_from_png_stream(festina_png_read, &reader);
        if (cairo_surface_status(img) != CAIRO_STATUS_SUCCESS) {
            cairo_surface_destroy(img);
            return NULL;
        }
        return img;
    }
    if (len >= 3 && bytes[0] == 0xFF && bytes[1] == 0xD8 && bytes[2] == 0xFF) {
        if (out_recognized_format) *out_recognized_format = 1;
        return festina_decode_jpeg(bytes, (size_t)len);
    }
    return NULL;
}

void *festina_image_from_bytes(const void *data, int64_t len, const char *label) {
    const unsigned char *bytes = (const unsigned char *)data;
    if (!label) label = "<blob>";
    if (!bytes || len <= 0) {
        char msg[512];
        snprintf(msg, sizeof(msg), "could not load image '%s': no image data", label);
        festina_fail(msg);
    }

    int recognized = 0;
    cairo_surface_t *img = festina_decode_image_surface(bytes, len, &recognized);
    if (!recognized) {
        char msg[512];
        snprintf(msg, sizeof(msg),
                 "could not load image '%s': not a PNG or JPEG "
                 "(those are the two formats this runtime decodes)", label);
        festina_fail(msg);
    }

    if (!img) {
        char msg[512];
        snprintf(msg, sizeof(msg), "could not load image '%s': the image data is corrupt", label);
        festina_fail(msg);
    }

    FestinaImageBox *box = festina_image_box(img);
    box->bytes = malloc((size_t)len);
    if (!box->bytes) festina_fail("out of memory loading an image");
    memcpy(box->bytes, bytes, (size_t)len);
    box->byte_count = (size_t)len;
    return box;
}

void *festina_load_image(const char *path) {
    if (!path) path = "";
    FILE *f = fopen(path, "rb");
    if (!f) {
        char msg[512];
        snprintf(msg, sizeof(msg), "could not open image file '%s': %s", path, strerror(errno));
        festina_fail(msg);
    }
    if (fseek(f, 0, SEEK_END) != 0) { fclose(f); festina_fail("could not read image file"); }
    long size = ftell(f);
    if (size < 0) { fclose(f); festina_fail("could not read image file"); }
    rewind(f);
    unsigned char *data = malloc((size_t)size ? (size_t)size : 1);
    if (!data) { fclose(f); festina_fail("out of memory loading an image"); }
    size_t got = fread(data, 1, (size_t)size, f);
    fclose(f);
    if (got != (size_t)size) {
        free(data);
        char msg[512];
        snprintf(msg, sizeof(msg), "could not read image file '%s'", path);
        festina_fail(msg);
    }
    void *box = festina_image_from_bytes(data, (int64_t)size, path);
    free(data);
    /* claude.md #110: remember where it came from, so save() works and
     * saveCopy() into a directory has a filename to reuse. Set here
     * rather than inside festina_image_from_bytes, because THAT entry
     * point is also how a database column becomes an image -- and one
     * of those genuinely has no path. */
    FestinaImageBox *loaded = (FestinaImageBox *)box;
    free(loaded->path);
    loaded->path = strdup(path);
    if (!loaded->path) festina_fail("out of memory loading an image");
    return box;
}

/* Reads a whole file with no festina_fail() on any recoverable failure
 * -- a local, non-throwing counterpart of festina_load_image's own
 * fopen/fseek/fread block, used only by festina_image_load_worker
 * below (see festina_runtime_async.c's own top comment: nothing an
 * async-io work_fn calls is allowed to call festina_fail, except on
 * genuine out-of-memory -- the one case this still treats as fatal,
 * matching festina_blob_load_worker's own precedent exactly). */
static unsigned char *festina_read_image_file_noflail(const char *path, int64_t *out_len) {
    *out_len = 0;
    if (!path || !*path) return NULL;
    FILE *f = fopen(path, "rb");
    if (!f) return NULL;
    if (fseek(f, 0, SEEK_END) != 0) { fclose(f); return NULL; }
    long size = ftell(f);
    if (size < 0) { fclose(f); return NULL; }
    rewind(f);
    unsigned char *data = malloc((size_t)size ? (size_t)size : 1);
    if (!data) { fclose(f); festina_fail("out of memory loading an image"); }
    size_t got = fread(data, 1, (size_t)size, f);
    fclose(f);
    if (got != (size_t)size) { free(data); return NULL; }
    *out_len = (int64_t)size;
    return data;
}

/* claude.md #171: runs on a background worker thread -- reads and
 * decodes `box->path` (already set, at construction time, by
 * festina_image_load_dispatch below) and, only on a genuinely
 * successful decode, replaces box->surface/bytes/byte_count IN PLACE
 * on the SAME box the caller is already holding, exactly the pattern
 * festina_blob_load_worker established. A missing file, an
 * unrecognized format, or corrupt image data all leave the box exactly
 * as it started: the 1x1 transparent placeholder, empty bytes -- as
 * "unpopulated" as a background blob load's own empty bytes/length
 * leaves it, never a crash the caller has no chance to catch. */
static void festina_image_load_worker(void *payload) {
    FestinaImageBox *box = (FestinaImageBox *)payload;
    int64_t len = 0;
    unsigned char *bytes = festina_read_image_file_noflail(box->path, &len);
    if (!bytes) return;
    cairo_surface_t *decoded = festina_decode_image_surface(bytes, len, NULL);
    if (!decoded) { free(bytes); return; }
    cairo_surface_destroy(box->surface);
    box->surface = decoded;
    free(box->bytes);
    box->bytes = bytes;
    box->byte_count = (size_t)len;
}

/* claude.md #171: codegen's own entry point for a `.callback()`-carrying
 * img construction, mirroring festina_blob_load_dispatch exactly --
 * NULL callback is the unchanged, fully synchronous festina_load_image
 * path; non-NULL builds the 1x1 placeholder above, sets its path, and
 * returns it immediately while the real decode runs in the background. */
void *festina_image_load_dispatch(const char *path, void (*callback)(void *)) {
    if (!callback) return festina_load_image(path);
    if (!path) path = "";
    cairo_surface_t *placeholder = cairo_image_surface_create(CAIRO_FORMAT_ARGB32, 1, 1);
    FestinaImageBox *box = festina_image_box(placeholder);
    free(box->path);
    box->path = strdup(path);
    if (!box->path) festina_fail("out of memory allocating an image");
    festina_retain(box);
    festina_async_io_dispatch(box, festina_image_load_worker, callback, festina_image_free);
    return box;
}

/* claude.md #101: the bytes to store when an `img` is bound as a sqlite
 * BLOB. For an image loaded from a file or a blob these are exactly the
 * bytes it came from, so a round trip through a table is byte-identical
 * and a JPEG stays a JPEG. An image with no source bytes -- a clip() or
 * resize() result -- is encoded to PNG on demand and the result cached,
 * since PNG is lossless and Cairo can already write it. */
static cairo_status_t festina_png_write(void *closure, const unsigned char *data,
                                         unsigned int len) {
    FestinaImageBox *box = (FestinaImageBox *)closure;
    unsigned char *grown = realloc(box->bytes, box->byte_count + len);
    if (!grown) return CAIRO_STATUS_WRITE_ERROR;
    memcpy(grown + box->byte_count, data, len);
    box->bytes = grown;
    box->byte_count += len;
    return CAIRO_STATUS_SUCCESS;
}

const void *festina_image_bytes(void *img, int64_t *out_len) {
    FestinaImageBox *box = (FestinaImageBox *)img;
    if (out_len) *out_len = 0;
    if (!box) return NULL;
    if (!box->bytes) {
        box->byte_count = 0;
        if (cairo_surface_write_to_png_stream(box->surface, festina_png_write, box)
                != CAIRO_STATUS_SUCCESS) {
            free(box->bytes);
            box->bytes = NULL;
            box->byte_count = 0;
            festina_fail("could not encode an image for storage");
        }
    }
    if (out_len) *out_len = (int64_t)box->byte_count;
    return box->bytes;
}

/* claude.md #110: writes the image's encoded bytes to a path. Uses
 * festina_image_bytes, so a clip()/resize() result is PNG-encoded on
 * demand exactly as it would be for a database column -- which is why
 * saving a clip works at all, and why it lands as a PNG whatever the
 * sheet it came from was. */
int8_t festina_image_save(void *img, const char *target) {
    if (!img) return 0;
    FestinaImageBox *box = (FestinaImageBox *)img;
    int64_t len = 0;
    const void *data = festina_image_bytes(img, &len);
    return festina_save_bytes(target, &box->path, data, len, "img", 1);
}

int8_t festina_image_save_copy(void *img, const char *target) {
    if (!img) return 0;
    FestinaImageBox *box = (FestinaImageBox *)img;
    int64_t len = 0;
    const void *data = festina_image_bytes(img, &len);
    return festina_save_bytes(target, &box->path, data, len, "img", 0);
}

int64_t festina_image_width(void *img) {
    if (!img) return 0;
    return (int64_t)cairo_image_surface_get_width(((FestinaImageBox *)img)->surface);
}

int64_t festina_image_height(void *img) {
    if (!img) return 0;
    return (int64_t)cairo_image_surface_get_height(((FestinaImageBox *)img)->surface);
}

/* claude.md #189: img.getPixelColor(x, y) -- the img-method
 * counterpart of the canvas-level getPixelColor(x, y); shares
 * festina_pixel_color_from_surface's own premultiplied-alpha unpacking
 * and out-of-bounds/no-colour-here 'none' handling. */
int64_t festina_image_get_pixel_color(void *img, int64_t x, int64_t y) {
    if (!img) return -1;
    return festina_pixel_color_from_surface(((FestinaImageBox *)img)->surface, x, y);
}

/* claude.md #92: a rectangle lifted out of a larger image -- the
 * spritesheet operation. Returns a NEW image; the source is untouched,
 * so one sheet can be clipped as many times as a program likes.
 *
 * A region reaching past the source's edge is deliberately not an
 * error: the overlapping part is copied and the rest stays transparent,
 * which is what every canvas drawImage-with-source-rect does, and is
 * ordinary at a sheet's right/bottom margin. */
void *festina_image_clip(void *img, int64_t x, int64_t y, int64_t w, int64_t h) {
    if (!img) return NULL;
    festina_check_image_size("clip", w, h);
    cairo_surface_t *src = ((FestinaImageBox *)img)->surface;
    cairo_surface_t *out = cairo_image_surface_create(CAIRO_FORMAT_ARGB32, (int)w, (int)h);
    cairo_t *cr = cairo_create(out);
    /* Offsetting the source by -x/-y puts the requested region at the
     * new surface's origin. */
    cairo_set_source_surface(cr, src, -(double)x, -(double)y);
    cairo_paint(cr);
    cairo_destroy(cr);
    return festina_image_box(out);
}

/* claude.md #188 (uraikus/festina#76 item 4): blankImage(w, h) -- a
 * fresh, fully-transparent img at a given size, with no existing image
 * or canvas to derive it from. Cairo's own cairo_image_surface_create
 * already zero-initializes every byte (documented guarantee), which
 * for ARGB32 IS fully transparent, so there's nothing else to paint
 * here -- unlike clip()/resize()/saveCanvas() just below, every one of
 * which copies FROM something that already exists. Closes the gap
 * those three leave: getting an independently-resizable, genuinely
 * blank image used to mean bouncing through the canvas by hand
 * (clearCanvas(); saveCanvas()), even when nothing needed to be drawn
 * yet -- and unlike that workaround, this never touches the real
 * on-screen canvas at all, so it costs nothing when a program is
 * midway through a frame. */
void *festina_blank_image(int64_t w, int64_t h) {
    festina_check_image_size("blankImage", w, h);
    cairo_surface_t *out = cairo_image_surface_create(CAIRO_FORMAT_ARGB32, (int)w, (int)h);
    return festina_image_box(out);
}

/* claude.md #135: saveCanvas() with no path -> img, a SNAPSHOT of the
 * canvas at this instant rather than a live view of it -- built the
 * exact same way festina_image_clip just above builds any other fresh
 * img from existing pixels (a new ARGB32 surface, the source painted
 * onto it, boxed). A snapshot rather than an alias is the only choice
 * that keeps `img` semantics honest: every OTHER img is its own
 * independent value once created (clip/resize never retroactively
 * change an unrelated image), and the canvas keeps being drawn into
 * and cleared long after this call returns -- an alias would make the
 * returned image silently change out from under whatever the program
 * does with it next. */
void *festina_canvas_to_image(void) {
    festina_backing_require();
    int w = cairo_image_surface_get_width(g_backing_surface);
    int h = cairo_image_surface_get_height(g_backing_surface);
    cairo_surface_t *out = cairo_image_surface_create(CAIRO_FORMAT_ARGB32, w, h);
    cairo_t *cr = cairo_create(out);
    cairo_set_source_surface(cr, g_backing_surface, 0, 0);
    cairo_paint(cr);
    cairo_destroy(cr);
    return festina_image_box(out);
}

/* claude.md #92: scales this image to w x h IN PLACE, so every binding
 * holding it sees the new size. The old surface is destroyed here --
 * safe precisely because the box, not the surface, is what any Festina
 * binding ever holds. */
void festina_image_resize(void *img, int64_t w, int64_t h) {
    if (!img) return;
    festina_check_image_size("resize", w, h);
    FestinaImageBox *box = (FestinaImageBox *)img;
    int src_w = cairo_image_surface_get_width(box->surface);
    int src_h = cairo_image_surface_get_height(box->surface);
    if (src_w <= 0 || src_h <= 0) return;
    cairo_surface_t *out = cairo_image_surface_create(CAIRO_FORMAT_ARGB32, (int)w, (int)h);
    cairo_t *cr = cairo_create(out);
    cairo_scale(cr, (double)w / src_w, (double)h / src_h);
    cairo_set_source_surface(cr, box->surface, 0, 0);
    /* GOOD filtering matters here: the default is fine for a 1:1 blit
     * but visibly blocky once a sprite is scaled. */
    cairo_pattern_set_filter(cairo_get_source(cr), CAIRO_FILTER_GOOD);
    cairo_paint(cr);
    cairo_destroy(cr);
    cairo_surface_destroy(box->surface);
    box->surface = out;
    /* claude.md #101: the source bytes describe the OLD size, so they
     * are no longer this image's bytes. Dropped rather than re-encoded
     * eagerly -- festina_image_bytes will encode a PNG if and only if
     * something actually asks for them. */
    free(box->bytes);
    box->bytes = NULL;
    box->byte_count = 0;
}

/* claude.md #134: drawRect/drawPixel/drawCircle/drawText as methods on
 * img -- the same four canvas-level drawing functions above, retargeted
 * at an image's OWN surface instead of the canvas backing store. No
 * window or festina_backing_require() needed at all: an img's surface
 * already exists in full the moment the image itself does (loaded,
 * clipped, resized, or decoded from bytes), unlike the canvas's own
 * lazily-created backing store. Deliberately does NOT apply the
 * canvas's global transform (translate/rotate/scale, claude.md #94) --
 * an image is a portable asset with its own local pixel coordinates,
 * independent of whatever the canvas's own transform happens to be set
 * to when a program draws onto one. What DOES apply (claude.md #234) is
 * the image's OWN transform -- img.translate()/rotate()/scale(), kept
 * on the box and reached through festina_image_context below. Still
 * reads the SAME global fillStyle/borderColor/lineWidth/font state
 * every canvas draw call does, since claude.md #133's own "otherwise
 * uses fillColor" default makes the most sense as one shared style,
 * not a second one to configure separately per image.
 *
 * `festina_check_image_bytes_stale`-equivalent bookkeeping (claude.md
 * #101's cached-PNG-bytes invalidation, see festina_image_resize just
 * above) applies here too: any of these mutates the surface's actual
 * pixels, so the cached encoded bytes (if any) are stale the moment
 * this returns. */
static void festina_image_bytes_now_stale(void *img) {
    FestinaImageBox *box = (FestinaImageBox *)img;
    free(box->bytes);
    box->bytes = NULL;
    box->byte_count = 0;
}

/* claude.md #234: the img counterpart of festina_canvas_context -- a
 * fresh context on this image's own surface carrying this IMAGE's own
 * transform (identity, and no cairo_set_matrix call at all, until the
 * image has ever been translated/rotated/scaled -- the common case for
 * a plain sprite). Every image-drawing/clearing/compositing call below
 * goes through this, so an image's transform applies to all of them
 * uniformly, exactly as the canvas's applies to the canvas versions. */
static cairo_t *festina_image_context(FestinaImageBox *box) {
    cairo_t *cr = cairo_create(box->surface);
    if (box->transform_ready) cairo_set_matrix(cr, &box->transform);
    return cr;
}

void festina_image_draw_rect(void *img, int64_t x, int64_t y, int64_t w, int64_t h) {
    if (!img) return;
    cairo_t *cr = festina_image_context((FestinaImageBox *)img);
    cairo_rectangle(cr, (double)x, (double)y, (double)w, (double)h);
    festina_fill_and_border(cr);
    cairo_destroy(cr);
    festina_image_bytes_now_stale(img);
}

void festina_image_draw_rect_color(void *img, int64_t x, int64_t y, int64_t w, int64_t h, int64_t color) {
    if (!img) return;
    cairo_t *cr = festina_image_context((FestinaImageBox *)img);
    cairo_rectangle(cr, (double)x, (double)y, (double)w, (double)h);
    festina_fill_and_border_with_color(cr, color);
    cairo_destroy(cr);
    festina_image_bytes_now_stale(img);
}

/* claude.md #188 (uraikus/festina#76 item 8) */
void festina_image_draw_rect_colors(void *img, int64_t x, int64_t y, int64_t w, int64_t h,
                                     int64_t fill_color, int64_t border_color) {
    if (!img) return;
    cairo_t *cr = festina_image_context((FestinaImageBox *)img);
    cairo_rectangle(cr, (double)x, (double)y, (double)w, (double)h);
    festina_fill_and_border_with_colors(cr, fill_color, border_color);
    cairo_destroy(cr);
    festina_image_bytes_now_stale(img);
}

/* See festina_draw_pixel's own comment (just above festina_draw_circle
 * in this file) for why antialiasing is disabled around the fill. */
void festina_image_draw_pixel(void *img, int64_t x, int64_t y) {
    if (!img) return;
    cairo_t *cr = festina_image_context((FestinaImageBox *)img);
    cairo_antialias_t save_aa = cairo_get_antialias(cr);
    cairo_set_antialias(cr, CAIRO_ANTIALIAS_NONE);
    cairo_rectangle(cr, (double)x, (double)y, 1, 1);
    if (!g_fill_none) {
        festina_set_fill_source(cr);
        cairo_fill(cr);
    } else {
        cairo_new_path(cr);
    }
    cairo_set_antialias(cr, save_aa);
    cairo_destroy(cr);
    festina_image_bytes_now_stale(img);
}

void festina_image_draw_pixel_color(void *img, int64_t x, int64_t y, int64_t color) {
    if (!img) return;
    cairo_t *cr = festina_image_context((FestinaImageBox *)img);
    cairo_antialias_t save_aa = cairo_get_antialias(cr);
    cairo_set_antialias(cr, CAIRO_ANTIALIAS_NONE);
    cairo_rectangle(cr, (double)x, (double)y, 1, 1);
    if (color >= 0) {
        double r, g, b;
        festina_unpack_rgb(color, &r, &g, &b);
        cairo_set_source_rgba(cr, r, g, b, g_fill_alpha);
        cairo_fill(cr);
    } else {
        cairo_new_path(cr);
    }
    cairo_set_antialias(cr, save_aa);
    cairo_destroy(cr);
    festina_image_bytes_now_stale(img);
}

/* No circle-mask fast path here (unlike the canvas's own drawCircle,
 * claude.md #104) -- that cache is keyed on the CANVAS's own transform
 * state, which images deliberately do not use (see this section's own
 * comment above), and drawing onto an image is a far rarer, less
 * hot-path call than drawing a frame's worth of shapes onto the canvas
 * every tick. */
void festina_image_draw_circle(void *img, int64_t x, int64_t y, int64_t r) {
    if (!img) return;
    cairo_t *cr = festina_image_context((FestinaImageBox *)img);
    cairo_arc(cr, (double)x, (double)y, (double)(r < 0 ? 0 : r), 0.0, 2.0 * 3.14159265358979323846);
    festina_fill_and_border(cr);
    cairo_destroy(cr);
    festina_image_bytes_now_stale(img);
}

/* claude.md #188 (uraikus/festina#76 item 8) */
void festina_image_draw_circle_color(void *img, int64_t x, int64_t y, int64_t r, int64_t color) {
    if (!img) return;
    cairo_t *cr = festina_image_context((FestinaImageBox *)img);
    cairo_arc(cr, (double)x, (double)y, (double)(r < 0 ? 0 : r), 0.0, 2.0 * 3.14159265358979323846);
    festina_fill_and_border_with_color(cr, color);
    cairo_destroy(cr);
    festina_image_bytes_now_stale(img);
}

void festina_image_draw_circle_colors(void *img, int64_t x, int64_t y, int64_t r,
                                       int64_t fill_color, int64_t border_color) {
    if (!img) return;
    cairo_t *cr = festina_image_context((FestinaImageBox *)img);
    cairo_arc(cr, (double)x, (double)y, (double)(r < 0 ? 0 : r), 0.0, 2.0 * 3.14159265358979323846);
    festina_fill_and_border_with_colors(cr, fill_color, border_color);
    cairo_destroy(cr);
    festina_image_bytes_now_stale(img);
}

void festina_image_draw_text(void *img, const char *text, int64_t x, int64_t y) {
    if (!img || !text) return;
    cairo_t *cr = festina_image_context((FestinaImageBox *)img);
    if (g_fill_none) { cairo_destroy(cr); return; }
    cairo_set_source_rgba(cr, g_fill_r, g_fill_g, g_fill_b, g_fill_alpha);
    festina_apply_font(cr);
    cairo_move_to(cr, (double)x, (double)y);
    cairo_show_text(cr, text);
    cairo_destroy(cr);
    festina_image_bytes_now_stale(img);
}

/* ---- claude.md #234 (uraikus/festina#93): an img as a self-contained
 * drawing target -- its own transform and state stack, clearing part
 * of it to transparent, and drawing one image onto another. Method
 * forms mirroring the canvas calls name-for-name (festina_translate/
 * festina_rotate/.../festina_clear_rect/festina_draw_image above), each
 * touching ONLY the receiver image: the transform lives on the box, the
 * state stack holds that image's own transforms (style state stays
 * global -- the canvas's saveState() keeps owning it), and every call
 * here goes through festina_image_context so the image's transform
 * applies to drawing, clearing and compositing alike. What this
 * replaces: bouncing a layer through the canvas (clearCanvas, drawImage
 * in, draw, saveCanvas out, clip) just to get one rotated rect or one
 * clearCircle onto it -- two window-sized copies per stamp, and every
 * layer edit forced onto main's canvas, the one thing a worker thread
 * can't touch. ---- */

static void festina_image_transform_require(FestinaImageBox *box) {
    if (!box->transform_ready) {
        cairo_matrix_init_identity(&box->transform);
        box->transform_ready = 1;
    }
}

void festina_image_translate(void *img, int64_t x, int64_t y) {
    if (!img) return;
    FestinaImageBox *box = (FestinaImageBox *)img;
    festina_image_transform_require(box);
    cairo_matrix_translate(&box->transform, (double)x, (double)y);
}

void festina_image_rotate(void *img, double degrees) {
    if (!img) return;
    FestinaImageBox *box = (FestinaImageBox *)img;
    festina_image_transform_require(box);
    /* Degrees, exactly like the canvas's rotate() -- see its comment. */
    cairo_matrix_rotate(&box->transform, degrees * 3.14159265358979323846 / 180.0);
}

void festina_image_scale(void *img, double sx, double sy) {
    if (!img) return;
    FestinaImageBox *box = (FestinaImageBox *)img;
    festina_image_transform_require(box);
    /* Same guard as festina_scale: a zero scale would leave a
     * non-invertible matrix every later call silently fails on. */
    if (sx == 0.0 || sy == 0.0) return;
    cairo_matrix_scale(&box->transform, sx, sy);
}

void festina_image_reset_transform(void *img) {
    if (!img) return;
    FestinaImageBox *box = (FestinaImageBox *)img;
    cairo_matrix_init_identity(&box->transform);
    box->transform_ready = 1;
}

/* The same 64-deep limit and the same two loud failures the canvas's
 * own saveState()/restoreState() have (see festina_save_state above),
 * named as the img forms so the message points at the right call. */
void festina_image_save_state(void *img) {
    if (!img) return;
    FestinaImageBox *box = (FestinaImageBox *)img;
    if (box->state_depth >= FESTINA_STATE_STACK_MAX) {
        festina_fail("img.saveState(): nested too deeply (limit 64) -- is an "
                      "img.restoreState() missing?");
    }
    if (box->state_depth == box->state_cap) {
        int cap = box->state_cap ? box->state_cap * 2 : 4;
        cairo_matrix_t *grown = realloc(box->state_stack, (size_t)cap * sizeof(cairo_matrix_t));
        if (!grown) festina_fail("out of memory in img.saveState()");
        box->state_stack = grown;
        box->state_cap = cap;
    }
    festina_image_transform_require(box);
    box->state_stack[box->state_depth++] = box->transform;
}

void festina_image_restore_state(void *img) {
    if (!img) return;
    FestinaImageBox *box = (FestinaImageBox *)img;
    if (box->state_depth <= 0) {
        festina_fail("img.restoreState(): nothing was saved -- every "
                      "img.restoreState() needs its own img.saveState() first");
    }
    box->transform = box->state_stack[--box->state_depth];
    box->transform_ready = 1;
}

/* Clearing: CAIRO_OPERATOR_SOURCE with a transparent source, for the
 * same reason festina_clear_canvas gives -- the default OVER operator
 * would paint "nothing" and leave the pixels untouched; genuinely
 * replacing them with transparent ones is what lets a later draw
 * underneath show through. img.clear() ignores the image's transform
 * exactly as clearCanvas() ignores the canvas's (a rotated "clear
 * everything" leaving wedges behind would be a trap); the three
 * region-shaped clears honour it exactly as clearRect/clearCircle/
 * clearPixel honour the canvas's. */
void festina_image_clear(void *img) {
    if (!img) return;
    cairo_t *cr = cairo_create(((FestinaImageBox *)img)->surface);
    cairo_set_operator(cr, CAIRO_OPERATOR_SOURCE);
    cairo_set_source_rgba(cr, 0, 0, 0, 0);
    cairo_paint(cr);
    cairo_destroy(cr);
    festina_image_bytes_now_stale(img);
}

void festina_image_clear_rect(void *img, int64_t x, int64_t y, int64_t w, int64_t h) {
    if (!img) return;
    cairo_t *cr = festina_image_context((FestinaImageBox *)img);
    cairo_set_operator(cr, CAIRO_OPERATOR_SOURCE);
    cairo_set_source_rgba(cr, 0, 0, 0, 0);
    cairo_rectangle(cr, (double)x, (double)y, (double)w, (double)h);
    cairo_fill(cr);
    cairo_destroy(cr);
    festina_image_bytes_now_stale(img);
}

void festina_image_clear_circle(void *img, int64_t x, int64_t y, int64_t r) {
    if (!img) return;
    cairo_t *cr = festina_image_context((FestinaImageBox *)img);
    if (r < 0) r = 0;
    cairo_set_operator(cr, CAIRO_OPERATOR_SOURCE);
    cairo_set_source_rgba(cr, 0, 0, 0, 0);
    cairo_arc(cr, (double)x, (double)y, (double)r, 0.0, 2.0 * 3.14159265358979323846);
    cairo_fill(cr);
    cairo_destroy(cr);
    festina_image_bytes_now_stale(img);
}

void festina_image_clear_pixel(void *img, int64_t x, int64_t y) {
    if (!img) return;
    cairo_t *cr = festina_image_context((FestinaImageBox *)img);
    cairo_antialias_t save_aa = cairo_get_antialias(cr);
    cairo_set_antialias(cr, CAIRO_ANTIALIAS_NONE);
    cairo_set_operator(cr, CAIRO_OPERATOR_SOURCE);
    cairo_set_source_rgba(cr, 0, 0, 0, 0);
    cairo_rectangle(cr, (double)x, (double)y, 1, 1);
    cairo_fill(cr);
    cairo_set_antialias(cr, save_aa);
    cairo_destroy(cr);
    festina_image_bytes_now_stale(img);
}

/* An independent copy of a surface's pixels -- used only for the one
 * case Cairo itself cannot do: an image drawn onto ITSELF. A source
 * pattern reading the very surface being painted is undefined in Cairo,
 * so the source is snapshotted first ("copy-first", the friendlier of
 * the two options uraikus/festina#93 allowed; tiling an image with
 * shifted copies of itself just works). Everything else pays nothing
 * for this: the copy is made only when the two boxes share a surface. */
static cairo_surface_t *festina_surface_snapshot(cairo_surface_t *src) {
    int w = cairo_image_surface_get_width(src);
    int h = cairo_image_surface_get_height(src);
    cairo_surface_t *out = cairo_image_surface_create(CAIRO_FORMAT_ARGB32, w, h);
    cairo_t *cr = cairo_create(out);
    cairo_set_source_surface(cr, src, 0, 0);
    cairo_paint(cr);
    cairo_destroy(cr);
    return out;
}

/* img.drawImage(src, x, y): src onto THIS image at (x, y) in this
 * image's own coordinates, through this image's transform, honouring
 * fillAlpha the same way the canvas drawImage does (claude.md #183 --
 * cairo_paint_with_alpha, since a surface source carries no alpha of
 * its own). */
void festina_image_draw_image(void *dst, void *src, int64_t x, int64_t y) {
    if (!dst || !src) return;
    FestinaImageBox *d = (FestinaImageBox *)dst;
    cairo_surface_t *source = ((FestinaImageBox *)src)->surface;
    cairo_surface_t *copy = NULL;
    if (source == d->surface) { copy = festina_surface_snapshot(source); source = copy; }
    cairo_t *cr = festina_image_context(d);
    cairo_set_source_surface(cr, source, (double)x, (double)y);
    cairo_paint_with_alpha(cr, g_fill_alpha);
    cairo_destroy(cr);
    if (copy) cairo_surface_destroy(copy);
    festina_image_bytes_now_stale(dst);
}

/* img.drawImage(src, x, y, w, h): the whole source scaled to fit a
 * w x h box -- the img counterpart of festina_draw_image_scaled above,
 * same scale-then-paint, same GOOD filtering from Cairo's own image
 * pattern default. */
void festina_image_draw_image_scaled(void *dst, void *src, int64_t x, int64_t y,
                                     int64_t w, int64_t h) {
    if (!dst || !src || w <= 0 || h <= 0) return;
    FestinaImageBox *d = (FestinaImageBox *)dst;
    cairo_surface_t *source = ((FestinaImageBox *)src)->surface;
    int src_w = cairo_image_surface_get_width(source);
    int src_h = cairo_image_surface_get_height(source);
    if (src_w <= 0 || src_h <= 0) return;
    cairo_surface_t *copy = NULL;
    if (source == d->surface) { copy = festina_surface_snapshot(source); source = copy; }
    cairo_t *cr = festina_image_context(d);
    cairo_translate(cr, (double)x, (double)y);
    cairo_scale(cr, (double)w / (double)src_w, (double)h / (double)src_h);
    cairo_set_source_surface(cr, source, 0, 0);
    cairo_paint_with_alpha(cr, g_fill_alpha);
    cairo_destroy(cr);
    if (copy) cairo_surface_destroy(copy);
    festina_image_bytes_now_stale(dst);
}

/* claude.md #92/#118: the img counterpart of festina_blob_release --
 * decrement, and only on the last reference destroy the surface and
 * free everything hanging off the box before the storage itself.
 * Reached from every place codegen releases an img value: scope exit,
 * reassignment, `free`/`delete`, a struct's field cascade, a query
 * result array's row release. */
void festina_image_free(void *img) {
    if (!img) return;
    if (!festina_release_check(img)) return;
    FestinaImageBox *box = (FestinaImageBox *)img;
    if (box->surface) cairo_surface_destroy(box->surface);
    free(box->bytes);   /* claude.md #101 */
    free(box->path);    /* claude.md #110 */
    free(box->state_stack);  /* claude.md #234 */
    free((char *)img - sizeof(int64_t));
}

/* claude.md #198 Phase 4: `thread`'s own deep-clone of an img message/
 * field -- deliberately NOT a Cairo surface copy (cairo_surface_t has
 * no portable "duplicate this" call, and hand-rolling one per surface
 * TYPE would be real new Cairo-API risk this project has no reason to
 * take on). Instead round-trips through the SAME encode/decode pair
 * `.save()` and a database `img` column already use and this project
 * already has real coverage of -- festina_image_bytes (PNG-encodes on
 * demand, cached) and festina_image_from_bytes (the decoder every
 * loadImage()-equivalent entry point already shares) -- lossless
 * (PNG is lossless regardless of the source format) and, since the
 * clone never touches the source surface at all, safe to call from a
 * thread OTHER than whichever one owns the source image (Cairo's own
 * documented thread-safety model permits concurrent use of DIFFERENT
 * surfaces on different threads; this never even reaches that surface
 * concurrently -- festina_image_bytes only WRITES the source's own
 * lazily-cached PNG bytes if they aren't already cached, which the
 * codegen-side clone dispatch never races against anything else that
 * could also be encoding the SAME image at the SAME time). `path` is
 * copied across afterward, same as festina_load_image's own post-decode
 * step -- festina_image_from_bytes itself never sets it. */
void *festina_image_clone(void *img) {
    if (!img) return NULL;
    FestinaImageBox *src = (FestinaImageBox *)img;
    int64_t len = 0;
    const void *data = festina_image_bytes(img, &len);
    void *clone = festina_image_from_bytes(data, len, "<thread clone>");
    FestinaImageBox *dst = (FestinaImageBox *)clone;
    free(dst->path);
    dst->path = strdup(src->path ? src->path : "");
    if (!dst->path) festina_fail("out of memory cloning an image");
    /* claude.md #234: the image's own current transform travels with
     * it (a layer handed to a worker keeps drawing where the sender
     * left off); the saveState() stack does not -- a clone starts with
     * nothing to restore, the same as any freshly created image. */
    dst->transform = src->transform;
    dst->transform_ready = src->transform_ready;
    return clone;
}

/* claude.md #183 (see uraikus/festina#78): drawImage used to always
 * `cairo_paint`, unconditionally full opacity, completely ignoring
 * `g_fill_alpha` -- every OTHER draw path already carries it (see
 * festina_set_fill_source's own `cairo_set_source_rgba(..., g_fill_
 * alpha)`, and its own `cairo_paint_with_alpha` call for a gradient
 * fill just above, the direct precedent this now follows for the
 * identical reason: cairo_set_source_surface has no alpha channel of
 * its own to carry it the way cairo_set_source_rgba does, so applying
 * the alpha has to happen at PAINT time instead of source-setup time).
 * `cairo_paint_with_alpha(cr, 1.0)` is defined to behave identically to
 * plain `cairo_paint`, so this is a strict extension, not a behavior
 * change for the (overwhelmingly common) case where fillAlpha was
 * never touched. */
void festina_draw_image(void *img, int64_t x, int64_t y) {
    festina_backing_require();
    if (!img) return;
    cairo_t *cr = festina_canvas_context();
    cairo_set_source_surface(cr, ((FestinaImageBox *)img)->surface, (double)x, (double)y);
    cairo_paint_with_alpha(cr, g_fill_alpha);
    cairo_destroy(cr);
}

/* claude.md #185 (uraikus/festina#76 item 3): drawImage(img, x, y, w,
 * h) -- draws the WHOLE source image scaled to fit a w x h box at
 * (x, y). The gap this closes: previously the only way to change an
 * image's displayed size at all was img.resize(), which mutates in
 * place -- so drawing one stored sprite at two different sizes (a
 * small palette icon and a full-size stamp) meant keeping two separate
 * copies around, generated or resized by hand.
 *
 * A plain scale-then-paint, not a resample into a fresh surface --
 * Cairo's own source-pattern filtering (CAIRO_FILTER_GOOD, the default
 * for an image pattern) does the interpolation, so this needs no image
 * processing of its own, and costs nothing extra when w/h happen to
 * match the source size exactly. */
void festina_draw_image_scaled(void *img, int64_t x, int64_t y, int64_t w, int64_t h) {
    festina_backing_require();
    if (!img || w <= 0 || h <= 0) return;
    cairo_surface_t *surface = ((FestinaImageBox *)img)->surface;
    int src_w = cairo_image_surface_get_width(surface);
    int src_h = cairo_image_surface_get_height(surface);
    if (src_w <= 0 || src_h <= 0) return;
    cairo_t *cr = festina_canvas_context();
    cairo_save(cr);
    cairo_translate(cr, (double)x, (double)y);
    cairo_scale(cr, (double)w / (double)src_w, (double)h / (double)src_h);
    cairo_set_source_surface(cr, surface, 0, 0);
    cairo_paint_with_alpha(cr, g_fill_alpha);
    cairo_restore(cr);
    cairo_destroy(cr);
}

/* claude.md #185: the full 8-argument canvas-style form -- a SOURCE
 * rect (sx, sy, sw, sh) cut out of the image and scaled to fit a
 * DESTINATION rect (dx, dy, dw, dh), the variable-size paint-brush-
 * from-one-fixed-size-source case #76 itself named.
 *
 * A source rect reaching past the image's own edge behaves exactly
 * like festina_image_clip's own "the overlap is copied, the rest stays
 * transparent" rule -- clipping to the DESTINATION rect (not the
 * source) is what keeps that transparent overflow from spilling past
 * the intended box instead of just fading out inside it. */
void festina_draw_image_region(void *img, int64_t sx, int64_t sy, int64_t sw, int64_t sh,
                                int64_t dx, int64_t dy, int64_t dw, int64_t dh) {
    festina_backing_require();
    if (!img || sw <= 0 || sh <= 0 || dw <= 0 || dh <= 0) return;
    cairo_surface_t *surface = ((FestinaImageBox *)img)->surface;
    cairo_t *cr = festina_canvas_context();
    cairo_save(cr);
    cairo_rectangle(cr, (double)dx, (double)dy, (double)dw, (double)dh);
    cairo_clip(cr);
    cairo_translate(cr, (double)dx, (double)dy);
    cairo_scale(cr, (double)dw / (double)sw, (double)dh / (double)sh);
    cairo_set_source_surface(cr, surface, -(double)sx, -(double)sy);
    cairo_paint_with_alpha(cr, g_fill_alpha);
    cairo_restore(cr);
    cairo_destroy(cr);
}

void festina_register_mouse_down_handler(void (*handler)(int64_t, int64_t, int64_t)) {
    g_mouse_down_handler = handler;
}

void festina_register_mouse_up_handler(void (*handler)(int64_t, int64_t, int64_t)) {
    g_mouse_up_handler = handler;
}

void festina_register_mouse_handler(void (*handler)(int64_t, int64_t)) {
    g_mouse_handler = handler;
}

void festina_register_mouse_wheel_up_handler(void (*handler)(int64_t, int64_t)) {
    g_mouse_wheel_up_handler = handler;
}

void festina_register_mouse_wheel_down_handler(void (*handler)(int64_t, int64_t)) {
    g_mouse_wheel_down_handler = handler;
}

void festina_register_key_down_handler(void (*handler)(const char *)) {
    g_key_down_handler = handler;
}

void festina_register_key_up_handler(void (*handler)(const char *)) {
    g_key_up_handler = handler;
}

void festina_register_resize_handler(void (*handler)(void)) {
    g_resize_handler = handler;
}

void festina_register_close_handler(void (*handler)(void)) {
    g_close_handler = handler;
}

/* claude.md #95: readable with no window open. The canvas has a size
 * (800x600 until an `on resize` changes it) whether or not it is on
 * screen, and requiring a window here would defeat headless rendering
 * for the very common case of asking how big the canvas is before
 * drawing into it. */
int64_t festina_client_width(void) {
    return g_canvas_width;
}

int64_t festina_client_height(void) {
    return g_canvas_height;
}

/* claude.md #139: screenWidth/screenHeight -- the physical display's
 * own resolution, through the seam (festina_window_screen_size), since
 * only a platform backend knows how to ask its own OS that. Two thin
 * wrappers rather than one two-out-param function reaching all the way
 * up to codegen, matching festina_client_width/_height's own shape
 * immediately above -- each is one global property, one call. */
int64_t festina_screen_width(void) {
    int64_t w = 0, h = 0;
    festina_window_screen_size(&w, &h);
    return w;
}

int64_t festina_screen_height(void) {
    int64_t w = 0, h = 0;
    festina_window_screen_size(&w, &h);
    return h;
}

/* claude.md #181: devicePixelRatio -- through the seam
 * (festina_window_device_pixel_ratio), the identical one-property-one-
 * call thin-wrapper shape as festina_screen_width/_height just above. */
double festina_device_pixel_ratio(void) {
    return festina_window_device_pixel_ratio();
}

/* claude.md #139: setClientWidth/setClientHeight's shared portable
 * core -- everything about "what changing the canvas size MEANS" lives
 * here, once, regardless of which of the two axes changed and whether
 * a window is even open yet. A non-positive size is silently ignored
 * (matching festina_check_image_size's own "no image nothing could
 * ever draw to" reasoning, applied to the canvas itself) rather than
 * failing the program.
 *
 * Deliberately synchronous and self-contained, not "resize the OS
 * window and wait for its own resize event to come back around": every
 * Festina-visible piece of state (clientWidth/clientHeight, the
 * backing surface) changes immediately, in this call, so
 * `setClientWidth(400) log(clientWidth)` reads 400 right away rather
 * than whatever stale value was true before the native window manager
 * gets around to confirming it asynchronously. festina_window_resize
 * (the seam call at the end) still asks the OS window to match, for
 * when one is open -- but the real ConfigureNotify/native resize event
 * that eventually arrives from THAT call is a trailing echo of a
 * change already applied here, not a second one: see
 * festina_handle_window_event's own RESIZE case, which skips its
 * rebuild-and-fire entirely when the event's size already matches what
 * this function already set, so one logical resize never fires `on
 * resize` twice. */
/* claude.md #139: counts native resize echoes still owed back to us
 * from calls to festina_window_resize below, one per call -- NOT a
 * size comparison. X11 (and presumably every other backend) does not
 * coalesce ConfigureNotify-equivalents across back-to-back resize
 * calls: two setClientWidth/setClientHeight calls in a row produce two
 * separate native resize requests and, later, two separate echoes,
 * each carrying whatever geometry was current AT THE TIME the OS
 * finally got around to it -- which can be a stale intermediate size,
 * not the final one. A "does this echo's size match current state"
 * guard (the first approach tried here) is fooled by exactly that: the
 * first stale echo still passes because current state hasn't been
 * touched yet, and processing it clobbers g_canvas_width/height away
 * from the size festina_set_client_size already committed, so the
 * SECOND echo then also looks "new" and fires `on resize` again too --
 * confirmed by a real back-to-back setClientWidth/setClientHeight
 * Xvfb repro that produced 4 firings instead of 2. Counting owed
 * echoes sidesteps geometry entirely: every echo genuinely caused by
 * this function is swallowed regardless of what stale size it reports,
 * while a real window-manager-driven resize (dragging an edge) never
 * increments this counter at all, so it always falls through and
 * fires normally. */
static int g_pending_self_resizes = 0;

static void festina_set_client_size(int64_t width, int64_t height) {
    if (width <= 0 || height <= 0) return;
    if (width == g_canvas_width && height == g_canvas_height) return;
    g_canvas_width = width;
    g_canvas_height = height;
    if (g_backing_surface) {
        cairo_surface_destroy(g_backing_surface);
        g_backing_surface = cairo_image_surface_create(
            CAIRO_FORMAT_ARGB32, (int)width, (int)height);
        /* claude.md #136: fresh canvas state is transparent, not white
         * -- see festina_backing_require's own identical block. */
        cairo_t *cr = cairo_create(g_backing_surface);
        cairo_set_operator(cr, CAIRO_OPERATOR_SOURCE);
        cairo_set_source_rgba(cr, 0, 0, 0, 0);
        cairo_paint(cr);
        cairo_destroy(cr);
    }
    if (g_window_open) {
        festina_graphics_present();
        if (g_resize_handler) g_resize_handler();
        g_pending_self_resizes++;
        festina_window_resize(width, height);
    }
}

void festina_set_client_width(int64_t width) {
    festina_set_client_size(width, g_canvas_height);
}

void festina_set_client_height(int64_t height) {
    festina_set_client_size(g_canvas_width, height);
}

/* claude.md #123: handles one already-NORMALIZED window event -- the
 * portable dispatch every backend's festina_window_events_drain calls
 * into, unchanged regardless of which platform produced the event.
 * Returns 0 if this was the window-close request (the caller should
 * stop looping and tear down), 1 otherwise. */
static int g_should_stop_looping = 0;

static void festina_handle_window_event(const FestinaWindowEvent *ev) {
    switch (ev->kind) {
    case FESTINA_WEVENT_MOUSE_DOWN:
        /* claude.md #106: both carry the pointer position at the
         * moment they happened, which is what makes a drag
         * expressible -- press and release report different
         * coordinates when the pointer moved in between. */
        if (g_mouse_down_handler) g_mouse_down_handler(ev->x, ev->y, ev->button);
        break;
    case FESTINA_WEVENT_MOUSE_UP:
        if (g_mouse_up_handler) g_mouse_up_handler(ev->x, ev->y, ev->button);
        break;
    case FESTINA_WEVENT_MOUSE_MOVE:
        if (g_mouse_handler) g_mouse_handler(ev->x, ev->y);
        break;
    case FESTINA_WEVENT_MOUSE_WHEEL_UP:
        if (g_mouse_wheel_up_handler) g_mouse_wheel_up_handler(ev->x, ev->y);
        break;
    case FESTINA_WEVENT_MOUSE_WHEEL_DOWN:
        if (g_mouse_wheel_down_handler) g_mouse_wheel_down_handler(ev->x, ev->y);
        break;
    case FESTINA_WEVENT_KEY_DOWN:
        if (g_key_down_handler) g_key_down_handler(ev->key_name);
        break;
    case FESTINA_WEVENT_KEY_UP:
        if (g_key_up_handler) g_key_up_handler(ev->key_name);
        break;
    case FESTINA_WEVENT_RESIZE:
        /* claude.md #39's own examples never draw relative to a canvas
         * size (there's no syntax for one), so there's no spec-defined
         * way to preserve old content sanely across a resize -- clear
         * to transparent at the new size (claude.md #136), the same
         * behavior resizing a browser's <canvas> element actually has
         * (a resized/recreated canvas is transparent, not white),
         * which clientWidth/clientHeight are named after. The window's
         * own on-screen surface is already the new size by the time
         * this fires -- each backend resizes its own before emitting
         * RESIZE (see festina_runtime_window.h) -- so only the
         * portable backing store needs rebuilding here.
         *
         * claude.md #139: skipped entirely while g_pending_self_resizes
         * is nonzero -- setClientWidth/setClientHeight (festina_set_
         * client_size) apply the identical rebuild SYNCHRONOUSLY, then
         * ask the OS window to match via festina_window_resize, which
         * increments that counter once per call. That native resize
         * still generates its own RESIZE event later (a
         * ConfigureNotify/equivalent), arriving here as the trailing
         * echo of a change already applied, not a new one -- without
         * this guard, one logical resize would rebuild the backing
         * store and fire `on resize` again. This is deliberately a
         * COUNT, not a size comparison: back-to-back calls (e.g.
         * setClientWidth then setClientHeight) produce two separate,
         * non-coalesced echoes, and the first echo can carry a stale
         * intermediate geometry that doesn't match either the size
         * before or after -- a size-comparison guard is fooled by that
         * (confirmed by a real Xvfb repro that mis-fired twice), while
         * counting owed echoes swallows both regardless of what
         * geometry each one happens to report. A genuine window-
         * manager-driven resize (dragging an edge) never increments
         * this counter, so it always falls through and fires. */
        if (g_pending_self_resizes > 0) {
            g_pending_self_resizes--;
            break;
        }
        g_canvas_width = ev->width;
        g_canvas_height = ev->height;
        cairo_surface_destroy(g_backing_surface);
        g_backing_surface = cairo_image_surface_create(
            CAIRO_FORMAT_ARGB32, (int)ev->width, (int)ev->height);
        cairo_t *cr = cairo_create(g_backing_surface);
        cairo_set_operator(cr, CAIRO_OPERATOR_SOURCE);
        cairo_set_source_rgba(cr, 0, 0, 0, 0);
        cairo_paint(cr);
        cairo_destroy(cr);
        festina_graphics_present();
        if (g_resize_handler) g_resize_handler();
        break;
    case FESTINA_WEVENT_CLOSE:
        if (g_close_handler) g_close_handler();
        g_should_stop_looping = 1;
        break;
    }
}

/* The blocking loop main() enters (via festina_run_event_loop) whenever
 * a program uses graphics -- see festina_runtime.h's doc comment.
 * claude.md #123: portable now -- festina_window_events_wait is each
 * backend's own analog of the X11 original's select() on
 * ConnectionNumber(g_display), so `on mouseDown`/timers both stay
 * responsive at once exactly as before; exits when the window closes
 * (timers, if any, are simply abandoned -- matching a browser tab
 * unloading). Timer state itself lives in festina_runtime.c, reached
 * only through festina_next_timer_deadline()/festina_fire_expired_timers()
 * (festina_runtime_internal.h) -- this file owns no timer bookkeeping
 * of its own. claude.md #166: also the loop a combined graphics+http
 * program blocks in -- since main() only ever enters ONE blocking loop
 * (see festina/codegen.py's _emit_main_and_entry), openPort() being
 * used at all doesn't change which loop that is once graphics is also
 * in play; it just adds http servicing to this one, through the hook
 * seam declared in festina_runtime.h.
 *
 * claude.md #178 (see uraikus/festina#79): main() no longer opens the
 * window eagerly before __festina_main() runs -- codegen.py's own
 * prologue only registers the event handlers there, letting any
 * setClientWidth/setClientHeight call the program's top-level code
 * makes run first, against no window at all. This lazy fallback (the
 * same guard festina_render() already used) is what still guarantees a
 * window exists by the time this loop needs one: a program that
 * declares a handler or sets its client size but never itself calls
 * render()/draws anything still gets a real window here, at whatever
 * size was last requested (or the 800x600 default, if none was). */
void festina_run_event_loop(void) {
    if (!g_window_open) festina_graphics_init();
    g_should_stop_looping = 0;
    /* claude.md #161: checked once per iteration alongside
     * g_should_stop_looping -- the identical shape a real window
     * close (FESTINA_WEVENT_CLOSE) already uses to end this same
     * loop, just from a different trigger (a signal, not a window
     * event). A short `festina_window_events_wait` timeout (this loop
     * already recomputes one every pass for timers) means a Ctrl-C/
     * SIGTERM is noticed within one timer tick even with no timer at
     * all active (timeout defaults to -1/block-forever only when
     * nothing else is pending, which festina_window_events_wait's own
     * backend still wakes from on the interrupting signal itself,
     * same as festina_run_http_loop's poll() does). */
    while (!g_should_stop_looping && !festina_shutdown_requested()) {
        double earliest = festina_next_timer_deadline();
        double timeout = -1.0;
        if (earliest >= 0.0) {
            timeout = earliest - festina_now_seconds();
            if (timeout < 0.0) timeout = 0.0;
        }
        /* claude.md #165: this loop's own lifetime is governed by the
         * window (it only ever exits on a real close or a shutdown
         * signal), so an outstanding blob/img/aud background load
         * never needs to keep it ALIVE the way it does for the other
         * two loops -- it only needs a bounded wait so a completed
         * load's callback fires promptly rather than waiting for the
         * next real window/timer event. */
        if (festina_async_io_outstanding() > 0 && (timeout < 0.0 || timeout > 0.02)) {
            timeout = 0.02;
        }
        /* claude.md #195 Phase 2: same bounded-wait treatment, for the
         * same reason -- a completed thread outbound message should
         * fire its onMessage() callback promptly rather than waiting
         * for the next real window/timer event. Unlike
         * festina_run_timer_loop this loop's own lifetime is still
         * governed purely by the window (see this loop's own doc
         * comment on festina_async_io_outstanding just above), so a
         * live idling thread does not, on its own, keep a GRAPHICS
         * program's window-driven loop running -- closing the window
         * still ends it, and festina_program_exit's own
         * festina_thread_kill_all() cleans up any thread still alive
         * at that point regardless. */
        if (festina_thread_outstanding() > 0 && (timeout < 0.0 || timeout > 0.02)) {
            timeout = 0.02;
        }
        /* claude.md #166: an open openPort()/openSecurePort() listener
         * (or a live connection, or a pending background client
         * request) gets exactly the same bounded-wait treatment --
         * this is what makes combining http with graphics possible at
         * all: this loop stays the ONE thing main() blocks in, and http
         * work just gets serviced from inside it, at the cost of the
         * same up-to-20ms latency already accepted for background
         * blob/img/aud loads. A no-op call (both hooks default to
         * "nothing registered") for a program that never uses http. */
        if (festina_http_service_outstanding() > 0 && (timeout < 0.0 || timeout > 0.02)) {
            timeout = 0.02;
        }
        festina_window_events_wait(timeout);
        festina_window_events_drain(festina_handle_window_event);
        festina_fire_expired_timers();
        festina_async_io_drain();
        festina_http_service_ready();
        festina_thread_drain();
    }
    cairo_surface_destroy(g_backing_surface);
    g_backing_surface = NULL;
    festina_window_close();
    g_window_open = 0;
    /* claude.md #161: a real window close (g_should_stop_looping) just
     * falls through to whatever main() does next (db_close, ret 0) --
     * unchanged, exactly as before this entry. A SHUTDOWN signal
     * instead runs the same clean-exit path close(code) already uses,
     * so a declared `on exit(code:int)` handler still fires. */
    if (festina_shutdown_requested()) {
        festina_program_exit(festina_shutdown_exit_code());
    }
}

/* ---- the X11 window backend -- claude.md #123's seam, implemented ----
 *
 * Everything in this block is the ORIGINAL X11 code, moved verbatim
 * behind festina_runtime_window.h's five functions -- verified
 * zero-regression against the full Xvfb-backed TestGraphics/
 * TestExampleGraphics/TestExampleTicTacToe suite. Compiled only on
 * Linux (and any other non-Apple, non-Windows platform); macOS gets
 * festina_runtime_window_mac.m instead (a separate Objective-C
 * translation unit -- Cocoa cannot be part of a plain .c file) and
 * Windows gets festina_runtime_window_win32.c (windows.md Phase 2 /
 * claude.md #128) -- plain C, but still its own file, since none of
 * this block's X11 headers exist under MinGW. See this file's own
 * top-of-file comment on festina_runtime_window.h's #include for why
 * the guard below is no longer simply `#ifndef __APPLE__`. */
#if !defined(__APPLE__) && !defined(_WIN32)
#include <X11/Xlib.h>
#include <X11/Xutil.h> /* XLookupString/XKeysymToString -- `on keyDown`/`on keyUp` */
#include <X11/XKBlib.h> /* XkbSetDetectableAutoRepeat -- claude.md #98 */
#include <cairo/cairo-xlib.h>

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
/* Whether the X server could turn on detectable auto-repeat (XKB). When
 * it can, a held key produces a single KeyPress and one KeyRelease when
 * it is finally let go. When it cannot, X synthesizes a
 * KeyRelease/KeyPress PAIR per repeat, and this backend filters them
 * out by hand -- see festina_x11_key_is_autorepeat. Without either, a
 * held key would fire keyUp/keyDown dozens of times a second, which is
 * exactly the bug splitting the event apart is meant to let a program
 * avoid. */
static int g_detectable_autorepeat = 0;

/* Swallows exactly the failure mode festina_window_open's own
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

void festina_window_open(int64_t width, int64_t height, const char *title) {
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
                                    (unsigned int)width, (unsigned int)height, 0,
                                    BlackPixel(g_display, screen), WhitePixel(g_display, screen));

    /* claude.md #180: request full decorations -- title bar, and the
     * WM's own minimize/maximize/close buttons -- rather than the
     * borderless "canvas, nothing else" look this used to request via
     * decorations=0 (MWM_DECOR_ALL, decorations=1, is the explicit ask
     * for the WM's normal chrome, not just the absence of the old
     * override). Still set explicitly rather than left unset, matching
     * this window's own previous convention of stating what it wants
     * rather than assuming a WM's default -- some WMs default borderless
     * windows to fully undecorated too, so simply omitting this property
     * wouldn't reliably produce a decorated window on every WM the way
     * asking for one explicitly does. */
    Atom mwm_hints_atom = XInternAtom(g_display, "_MOTIF_WM_HINTS", False);
    FestinaMotifWmHints hints;
    memset(&hints, 0, sizeof(hints));
    hints.flags = 2; /* MWM_HINTS_DECORATIONS */
    hints.decorations = 1; /* MWM_DECOR_ALL */
    XChangeProperty(g_display, g_window, mwm_hints_atom, mwm_hints_atom, 32,
                     PropModeReplace, (unsigned char *)&hints,
                     sizeof(hints) / sizeof(long));

    XStoreName(g_display, g_window, title);
    XSelectInput(g_display, g_window,
                 ExposureMask | ButtonPressMask | ButtonReleaseMask | PointerMotionMask |
                 KeyPressMask | KeyReleaseMask | StructureNotifyMask);
    /* claude.md #98: ask the server to stop synthesizing a KeyRelease
     * before every auto-repeated KeyPress, so a held key produces one
     * keyDown and one keyUp when it is actually let go. Part of libX11
     * itself (XKB), not a separate dependency. Not every server
     * supports it, so the result is recorded and a hand-rolled filter
     * covers the ones that do not -- see festina_x11_key_is_autorepeat. */
    Bool detectable = False;
    XkbSetDetectableAutoRepeat(g_display, True, &detectable);
    g_detectable_autorepeat = detectable ? 1 : 0;
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

    g_window_surface = cairo_xlib_surface_create(g_display, g_window, DefaultVisual(g_display, screen),
                                                  (int)width, (int)height);
}

void festina_window_close(void) {
    cairo_surface_destroy(g_window_surface);
    g_window_surface = NULL;
    XDestroyWindow(g_display, g_window);
    XCloseDisplay(g_display);
    g_display = NULL;
}

/* claude.md #139: reuses the already-open connection if a window is
 * open; otherwise opens a throwaway one just long enough to ask, and
 * closes it again -- no retry loop the way festina_window_open's own
 * XOpenDisplay has one, since this is a read-only property query a
 * program can call as often as it likes, not a one-time hard
 * requirement worth stalling up to a second for. Fails clearly (the
 * identical message render() itself uses) rather than silently
 * answering 0x0, which would look like a real, if degenerate, screen
 * size instead of "no display at all". */
void festina_window_screen_size(int64_t *out_width, int64_t *out_height) {
    if (g_display) {
        int screen = DefaultScreen(g_display);
        *out_width = DisplayWidth(g_display, screen);
        *out_height = DisplayHeight(g_display, screen);
        return;
    }
    Display *tmp = XOpenDisplay(NULL);
    if (!tmp) {
        festina_fail("could not open the X display -- claude.md #39's graphics "
                      "functions need a running X server (is $DISPLAY set?)");
        return;
    }
    int screen = DefaultScreen(tmp);
    *out_width = DisplayWidth(tmp, screen);
    *out_height = DisplayHeight(tmp, screen);
    XCloseDisplay(tmp);
}

/* claude.md #181: unlike screenWidth/screenHeight's unambiguous
 * DisplayWidth/DisplayHeight, X11 has no single canonical "what's the
 * pixel ratio" call -- the obvious-looking alternative (deriving a DPI
 * from DisplayWidth/DisplayWidthMM's physical millimeter size) is a
 * well-known unreliable heuristic in practice (many real monitors
 * report inaccurate EDID physical dimensions), which is why GTK/Qt/
 * every serious X11 toolkit instead reads the `Xft.dpi` X resource --
 * the actual standard mechanism a desktop environment's own display
 * settings write when a user picks a scale factor. Falls back to 1.0
 * (no scaling) if unset, which is also the CORRECT answer for the
 * common case: a plain X11 setup with no HiDPi configuration at all.
 * Same reuse-the-open-connection-or-open-a-throwaway-one shape as
 * festina_window_screen_size just above. */
double festina_window_device_pixel_ratio(void) {
    Display *display = g_display;
    Display *tmp = NULL;
    if (!display) {
        tmp = XOpenDisplay(NULL);
        display = tmp;
    }
    double ratio = 1.0;
    if (display) {
        char *dpi_str = XGetDefault(display, "Xft", "dpi");
        if (dpi_str) {
            double dpi = atof(dpi_str);
            if (dpi > 0) ratio = dpi / 96.0;
        }
    }
    if (tmp) XCloseDisplay(tmp);
    return ratio;
}

/* claude.md #139: a no-op with no window open -- festina_set_client_
 * size (festina_runtime_graphics.c's own portable half) already
 * updates the canvas's own size for whenever one does; this function's
 * only job is telling the ALREADY-open native window to match.
 * cairo_xlib_surface_set_size keeps the Cairo-side surface's own
 * cached dimensions in step with the just-resized drawable -- without
 * it, festina_window_present would keep blitting at the OLD size into
 * a window that has already changed underneath it. */
void festina_window_resize(int64_t width, int64_t height) {
    if (!g_display) return;
    XResizeWindow(g_display, g_window, (unsigned int)width, (unsigned int)height);
    cairo_xlib_surface_set_size(g_window_surface, (int)width, (int)height);
    XFlush(g_display);
}

/* claude.md #180: the standard EWMH way to ask the window manager for
 * real fullscreen -- a _NET_WM_STATE ClientMessage sent to the ROOT
 * window (not the client window itself; that is what tells the WM to
 * treat this as a state-change REQUEST rather than a property it should
 * just record), asking it to add or remove _NET_WM_STATE_FULLSCREEN.
 * Honored by every EWMH-compliant window manager (which is effectively
 * all of them; see https://specifications.freedesktop.org/wm-spec/) --
 * this backend never draws the fullscreen chrome/geometry itself the
 * way the Windows backend has to (see that file's own comment), since
 * X11's own WM already owns that job for every other window too.
 * data.l[0]: 1 = _NET_WM_STATE_ADD, 0 = _NET_WM_STATE_REMOVE. data.l[3]:
 * source indication -- 1 means "a normal application", the value the
 * spec asks a well-behaved client to send. A no-op with no window open,
 * matching festina_window_resize's own guard just above -- the portable
 * caller (festina_enter_fullscreen/festina_exit_fullscreen) already
 * only reaches this when g_window_open is true, but the same defensive
 * check costs nothing and keeps this function safe to call on its own. */
void festina_window_set_fullscreen(int8_t fullscreen) {
    if (!g_display) return;
    Atom net_wm_state = XInternAtom(g_display, "_NET_WM_STATE", False);
    Atom net_wm_state_fullscreen = XInternAtom(g_display, "_NET_WM_STATE_FULLSCREEN", False);
    XEvent xev;
    memset(&xev, 0, sizeof(xev));
    xev.type = ClientMessage;
    xev.xclient.window = g_window;
    xev.xclient.message_type = net_wm_state;
    xev.xclient.format = 32;
    xev.xclient.data.l[0] = fullscreen ? 1 : 0;
    xev.xclient.data.l[1] = (long)net_wm_state_fullscreen;
    xev.xclient.data.l[2] = 0;
    xev.xclient.data.l[3] = 1;
    XSendEvent(g_display, DefaultRootWindow(g_display), False,
               SubstructureRedirectMask | SubstructureNotifyMask, &xev);
    XFlush(g_display);
}

/* claude.md #182: X11's core protocol has no direct "hide the cursor"
 * call (XFixesHideCursor exists, but pulling in libXfixes as a whole
 * new link dependency for one call isn't worth it -- claude.md #59's
 * own "smallest dependency that does the job" reasoning) -- the
 * standard, dependency-free workaround (used by SDL's own X11 backend,
 * among others) is defining a real cursor that's simply fully
 * transparent: a 1x1 bitmap with every pixel masked out, so nothing of
 * it is ever actually drawn. XUndefineCursor removes the per-window
 * override entirely, falling back to whatever cursor the window's
 * parent (ultimately the root window's own default) already shows --
 * the correct way to "restore" it, since this never had a cursor of
 * its own before this call existed. */
void festina_window_set_cursor_visible(int8_t visible) {
    if (!g_display) return;
    if (visible) {
        XUndefineCursor(g_display, g_window);
        XFlush(g_display);
        return;
    }
    char data[1] = {0};
    Pixmap blank = XCreateBitmapFromData(g_display, g_window, data, 1, 1);
    XColor dummy;
    memset(&dummy, 0, sizeof(dummy));
    Cursor invisible = XCreatePixmapCursor(g_display, blank, blank, &dummy, &dummy, 0, 0);
    XDefineCursor(g_display, g_window, invisible);
    /* Safe to free both immediately: the server keeps its own copy for
     * as long as the cursor stays defined on the window, exactly like
     * every other server-side X11 resource (windows, GCs, ...) already
     * works in this file. */
    XFreeCursor(g_display, invisible);
    XFreePixmap(g_display, blank);
    XFlush(g_display);
}

void festina_window_present(cairo_surface_t *backing) {
    cairo_t *cr = cairo_create(g_window_surface);
    cairo_set_source_surface(cr, backing, 0, 0);
    cairo_paint(cr);
    cairo_destroy(cr);
    cairo_surface_flush(g_window_surface);
    XFlush(g_display);
}

void festina_window_events_wait(double timeout_seconds) {
    if (XPending(g_display)) return;
    int xfd = ConnectionNumber(g_display);
    fd_set fds;
    FD_ZERO(&fds);
    FD_SET(xfd, &fds);
    struct timeval tv;
    struct timeval *tvp = NULL;
    if (timeout_seconds >= 0.0) {
        tv.tv_sec = (long)timeout_seconds;
        tv.tv_usec = (long)((timeout_seconds - (double)tv.tv_sec) * 1e6);
        tvp = &tv;
    }
    select(xfd + 1, &fds, NULL, NULL, tvp);
}

/* claude.md #40's key NAME, shared by keyDown and keyUp so the two
 * events can never disagree about what to call the same physical key.
 *
 * A key that types an ordinary printable character (letters, digits,
 * punctuation, space) comes back as that character through the buffer
 * XLookupString fills in. Anything else -- Enter/Escape/Backspace/
 * arrow keys/... -- either comes back empty or as an unprintable
 * control character (e.g. 0x1B for Escape, 0x0D for Return), neither
 * of which is a useful `text` value, so those fall back to
 * XKeysymToString's X11 key name instead (e.g. "Return", "Escape",
 * "Left") -- exactly runtime/festina_key_names.h's own vocabulary,
 * since that header's names were measured from this call.
 *
 * XLookupString is given the event by address rather than a copy: it
 * takes an XKeyEvent* and reads the modifier state out of it, which is
 * what makes a shifted "a" arrive as "A". */
static void festina_x11_key_name(XKeyEvent *ev, char *out, size_t out_size) {
    char buf[32];
    KeySym keysym;
    int len = XLookupString(ev, buf, sizeof(buf) - 1, &keysym, NULL);
    if (len > 0 && (unsigned char)buf[0] >= 0x20 && (unsigned char)buf[0] != 0x7F) {
        if ((size_t)len >= out_size) len = (int)out_size - 1;
        memcpy(out, buf, (size_t)len);
        out[len] = '\0';
        return;
    }
    const char *name = XKeysymToString(keysym);
    if (!name) name = "";
    snprintf(out, out_size, "%s", name);
}

/* claude.md #98: true for the KeyRelease half of an auto-repeat pair.
 * See g_detectable_autorepeat's own comment for the full reasoning --
 * peeking at the next queued event and dropping the release when its
 * partner is already sitting behind it turns the synthesized
 * KeyRelease/KeyPress stream back into the one-down-many-repeats shape
 * a program expects. */
static int festina_x11_key_is_autorepeat(XEvent *ev) {
    if (g_detectable_autorepeat || ev->type != KeyRelease) return 0;
    if (!XPending(g_display)) return 0;
    XEvent next;
    XPeekEvent(g_display, &next);
    return next.type == KeyPress &&
           next.xkey.time == ev->xkey.time &&
           next.xkey.keycode == ev->xkey.keycode;
}

void festina_window_events_drain(void (*handler)(const FestinaWindowEvent *event)) {
    while (XPending(g_display)) {
        XEvent ev;
        XNextEvent(g_display, &ev);
        FestinaWindowEvent wev;
        memset(&wev, 0, sizeof(wev));

        if (ev.type == Expose) {
            /* claude.md #123: redraw-on-expose is entirely this
             * backend's own concern now -- see
             * festina_runtime_window.h's own note. The X11 window
             * surface still holds whatever was last painted onto it
             * (Cairo/X11 own the pixels), so simply flushing it again
             * is enough; there is no "last backing surface" to
             * re-fetch here since nothing about it changed. */
            cairo_surface_flush(g_window_surface);
            XFlush(g_display);
            continue;
        } else if (ev.type == ButtonPress || ev.type == ButtonRelease) {
            /* claude.md #181: X11's core protocol has no dedicated
             * scroll-wheel event -- by long-standing, universal
             * convention (predating XInput2's real smooth-scroll
             * events, but still what every application and toolkit
             * still honors for a simple wheel), the wheel is reported
             * as buttons 4 (up) and 5 (down), delivered as an ordinary
             * ButtonPress immediately followed by its own ButtonRelease
             * -- there's no separate "hold the wheel down" gesture the
             * way there is for a real mouse button. Firing on the
             * PRESS half only (and swallowing the paired release
             * entirely, rather than letting it fall through to
             * mouseUp) is what makes this "one wheel event per notch",
             * not two, and is also the fix for a real pre-existing
             * quirk this uncovered: every button's press/release used
             * to reach mouseDown/mouseUp completely unfiltered, so
             * scrolling over the canvas already silently fired a
             * mouseDown+mouseUp pair at the wheel's own position --
             * harmless-looking but wrong, now corrected as part of
             * giving the wheel its own real event instead. */
            if (ev.xbutton.button == 4 || ev.xbutton.button == 5) {
                if (ev.type == ButtonRelease) continue;
                wev.kind = ev.xbutton.button == 4
                    ? FESTINA_WEVENT_MOUSE_WHEEL_UP : FESTINA_WEVENT_MOUSE_WHEEL_DOWN;
            } else {
                /* claude.md #182: X11's own button numbering (1=left,
                 * 2=middle, 3=right, 8=back, 9=forward on any mouse
                 * that reports them) is reported directly here with no
                 * translation needed at all -- it's the very numbering
                 * FestinaWindowEvent's own `button` field standardizes
                 * on (see its doc comment), specifically because X11
                 * already produces it natively; Cocoa/Win32 each
                 * translate their own different numbering into this
                 * one instead. */
                wev.kind = ev.type == ButtonPress ? FESTINA_WEVENT_MOUSE_DOWN : FESTINA_WEVENT_MOUSE_UP;
                wev.button = ev.xbutton.button;
            }
            wev.x = ev.xbutton.x;
            wev.y = ev.xbutton.y;
            handler(&wev);
        } else if (ev.type == MotionNotify) {
            wev.kind = FESTINA_WEVENT_MOUSE_MOVE;
            wev.x = ev.xmotion.x;
            wev.y = ev.xmotion.y;
            handler(&wev);
        } else if (ev.type == KeyPress || ev.type == KeyRelease) {
            if (festina_x11_key_is_autorepeat(&ev)) continue;
            char name[32];
            festina_x11_key_name(&ev.xkey, name, sizeof(name));
            wev.kind = ev.type == KeyPress ? FESTINA_WEVENT_KEY_DOWN : FESTINA_WEVENT_KEY_UP;
            wev.key_name = name;
            handler(&wev);
        } else if (ev.type == ConfigureNotify) {
            /* ConfigureNotify fires on more than just a resize (e.g. a
             * move); the shared dispatcher only needs to hear about a
             * GENUINE size change, so only that case is translated. */
            int64_t new_w = ev.xconfigure.width;
            int64_t new_h = ev.xconfigure.height;
            if (new_w != g_canvas_width || new_h != g_canvas_height) {
                /* claude.md #123: resize THIS backend's own on-screen
                 * surface before handing the event to shared code --
                 * see festina_runtime_window.h's own note on why. */
                cairo_xlib_surface_set_size(g_window_surface, new_w, new_h);
                wev.kind = FESTINA_WEVENT_RESIZE;
                wev.width = new_w;
                wev.height = new_h;
                handler(&wev);
            }
        } else if (ev.type == ClientMessage) {
            if ((Atom)ev.xclient.data.l[0] == g_wm_delete_atom) {
                wev.kind = FESTINA_WEVENT_CLOSE;
                handler(&wev);
                return; /* the window is going away -- nothing queued after this matters */
            }
        }
    }
}
#endif /* !__APPLE__ && !_WIN32 */
