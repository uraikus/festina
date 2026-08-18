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
#include <sys/select.h> /* select() -- the X11 window backend's events_wait */
#include <time.h>       /* nanosleep -- the X11 backend's connect retry */
#include "festina_runtime.h"
#include "festina_runtime_internal.h"
#include "festina_runtime_window.h" /* claude.md #123: the windowing device seam --
                                     * see its own doc comment for the full design.
                                     * Everything in THIS file is now portable: the
                                     * X11 implementation of the seam lives at the
                                     * bottom of this file, guarded `#ifndef __APPLE__`;
                                     * the macOS implementation is a separate
                                     * Objective-C translation unit
                                     * (festina_runtime_window_mac.m), since Cocoa
                                     * cannot be compiled as part of a plain .c file. */

static int g_window_open = 0;      /* claude.md #123: portable stand-in for "is
                                     * there a live platform window" -- the shared
                                     * code's own guard, since g_display/g_window
                                     * no longer exist here at all. */
static cairo_surface_t *g_backing_surface = NULL;
/* claude.md #106: `on click` split into `on mouseDown` and `on mouseUp`,
 * exactly as claude.md #98 split `on key`. A click is a press and a
 * release, and a program that needs to tell them apart -- dragging,
 * charging a shot, holding to aim -- could not, because the two were
 * collapsed into one event that fired on press. */
static void (*g_mouse_down_handler)(int64_t, int64_t) = NULL;
static void (*g_mouse_up_handler)(int64_t, int64_t) = NULL;
static void (*g_mouse_handler)(int64_t, int64_t) = NULL;
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
    cairo_t *cr = cairo_create(g_backing_surface);
    cairo_set_source_rgb(cr, 1, 1, 1); /* white canvas background */
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

/* claude.md #123: opens the platform window through the seam, exactly
 * once. Portable now -- every platform-specific concern (connect
 * retries, decorations, input focus, ...) lives in that platform's own
 * festina_window_open implementation; see festina_runtime_window.h. */
void festina_graphics_init(void) {
    festina_window_open(FESTINA_CANVAS_WIDTH, FESTINA_CANVAS_HEIGHT, "Festina");
    g_window_open = 1;
    g_canvas_width = FESTINA_CANVAS_WIDTH;
    g_canvas_height = FESTINA_CANVAS_HEIGHT;
    /* claude.md #95: whatever was already drawn headlessly is kept --
     * a program may well have drawn before its first render(). */
    festina_backing_require();
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

/* claude.md #95: erases the whole canvas to opaque white -- the missing
 * half of animation. Without it a canvas could only ever accumulate, so
 * nothing could move: every frame painted on top of every frame before
 * it. */
void festina_clear_canvas(void) {
    festina_backing_require();
    cairo_t *cr = cairo_create(g_backing_surface);
    /* Deliberately NOT the current transform: clearing is about the
     * canvas itself, and a rotated "clear everything" that leaves
     * wedges behind would be a trap rather than a feature. */
    cairo_set_source_rgb(cr, 1, 1, 1);
    cairo_paint(cr);
    cairo_destroy(cr);
}

/* Erases one rectangle back to white. Unlike clearCanvas this DOES
 * honour the current transform, since it names a region in the same
 * coordinates the drawing calls around it use. */
void festina_clear_rect(int64_t x, int64_t y, int64_t w, int64_t h) {
    festina_backing_require();
    cairo_t *cr = festina_canvas_context();
    cairo_set_source_rgb(cr, 1, 1, 1);
    cairo_rectangle(cr, (double)x, (double)y, (double)w, (double)h);
    cairo_fill(cr);
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
    cairo_surface_t *surface = NULL;
    unsigned char *scanline = NULL;

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

void *festina_image_from_bytes(const void *data, int64_t len, const char *label) {
    const unsigned char *bytes = (const unsigned char *)data;
    if (!label) label = "<blob>";
    if (!bytes || len <= 0) {
        char msg[512];
        snprintf(msg, sizeof(msg), "could not load image '%s': no image data", label);
        festina_fail(msg);
    }

    cairo_surface_t *img = NULL;
    if (len >= 8 && memcmp(bytes, "\x89PNG\r\n\x1a\n", 8) == 0) {
        FestinaByteReader reader = { bytes, (size_t)len, 0 };
        img = cairo_image_surface_create_from_png_stream(festina_png_read, &reader);
        if (cairo_surface_status(img) != CAIRO_STATUS_SUCCESS) {
            cairo_surface_destroy(img);
            img = NULL;
        }
    } else if (len >= 3 && bytes[0] == 0xFF && bytes[1] == 0xD8 && bytes[2] == 0xFF) {
        img = festina_decode_jpeg(bytes, (size_t)len);
    } else {
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
    free((char *)img - sizeof(int64_t));
}

void festina_draw_image(void *img, int64_t x, int64_t y) {
    festina_backing_require();
    if (!img) return;
    cairo_t *cr = festina_canvas_context();
    cairo_set_source_surface(cr, ((FestinaImageBox *)img)->surface, (double)x, (double)y);
    cairo_paint(cr);
    cairo_destroy(cr);
}

void festina_register_mouse_down_handler(void (*handler)(int64_t, int64_t)) {
    g_mouse_down_handler = handler;
}

void festina_register_mouse_up_handler(void (*handler)(int64_t, int64_t)) {
    g_mouse_up_handler = handler;
}

void festina_register_mouse_handler(void (*handler)(int64_t, int64_t)) {
    g_mouse_handler = handler;
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
        if (g_mouse_down_handler) g_mouse_down_handler(ev->x, ev->y);
        break;
    case FESTINA_WEVENT_MOUSE_UP:
        if (g_mouse_up_handler) g_mouse_up_handler(ev->x, ev->y);
        break;
    case FESTINA_WEVENT_MOUSE_MOVE:
        if (g_mouse_handler) g_mouse_handler(ev->x, ev->y);
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
         * back to white at the new size, the same behavior resizing a
         * browser's <canvas> element has, which clientWidth/
         * clientHeight are named after. The window's own on-screen
         * surface is already the new size by the time this fires --
         * each backend resizes its own before emitting RESIZE (see
         * festina_runtime_window.h) -- so only the portable backing
         * store needs rebuilding here. */
        g_canvas_width = ev->width;
        g_canvas_height = ev->height;
        cairo_surface_destroy(g_backing_surface);
        g_backing_surface = cairo_image_surface_create(
            CAIRO_FORMAT_ARGB32, (int)ev->width, (int)ev->height);
        cairo_t *cr = cairo_create(g_backing_surface);
        cairo_set_source_rgb(cr, 1, 1, 1);
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
 * of its own. */
void festina_run_event_loop(void) {
    g_should_stop_looping = 0;
    while (!g_should_stop_looping) {
        double earliest = festina_next_timer_deadline();
        double timeout = -1.0;
        if (earliest >= 0.0) {
            timeout = earliest - festina_now_seconds();
            if (timeout < 0.0) timeout = 0.0;
        }
        festina_window_events_wait(timeout);
        festina_window_events_drain(festina_handle_window_event);
        festina_fire_expired_timers();
    }
    cairo_surface_destroy(g_backing_surface);
    g_backing_surface = NULL;
    festina_window_close();
    g_window_open = 0;
}

/* ---- the X11 window backend -- claude.md #123's seam, implemented ----
 *
 * Everything in this block is the ORIGINAL X11 code, moved verbatim
 * behind festina_runtime_window.h's five functions -- verified
 * zero-regression against the full Xvfb-backed TestGraphics/
 * TestExampleGraphics/TestExampleTicTacToe suite. Compiled only on
 * non-Apple platforms; macOS gets festina_runtime_window_mac.m instead
 * (a separate Objective-C translation unit -- Cocoa cannot be part of
 * a plain .c file). */
#ifndef __APPLE__
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

    Atom mwm_hints_atom = XInternAtom(g_display, "_MOTIF_WM_HINTS", False);
    FestinaMotifWmHints hints;
    memset(&hints, 0, sizeof(hints));
    hints.flags = 2; /* MWM_HINTS_DECORATIONS */
    hints.decorations = 0;
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
            wev.kind = ev.type == ButtonPress ? FESTINA_WEVENT_MOUSE_DOWN : FESTINA_WEVENT_MOUSE_UP;
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
#endif /* !__APPLE__ */
