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
#include <stdatomic.h>  /* claude.md #240: the lock-free circle-coverage cache */
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
/* The same gradient as plain numbers, for draw.f (runtime.md phase 7
 * slice 4): the Cairo pattern above cannot be read without Cairo. kind is
 * 0 for none, 1 linear, 2 radial; geom holds x0 y0 x1 y1, or cx cy
 * radius; c0 and c1 are the two stop colours, 0..255. */
typedef struct { int kind; double geom[4]; double c0[3], c1[3]; } FestinaGradientParams;
static FestinaGradientParams g_grad;

/* The transform's own arithmetic, in the layout of cairo_matrix_t (xx, yx,
 * xy, yy, x0, y0) and to the formulas cairo-matrix.c uses, so a program's
 * transform is computed without calling Cairo: each operation applies
 * FIRST, then the matrix already there (new = op * old). The type stays
 * cairo_matrix_t while Cairo still draws the fallbacks from it;
 * tests/test_transform_matrix.py holds these equal to cairo_matrix_* bit
 * for bit. */
static void festina_matrix_identity(cairo_matrix_t *m) {
    m->xx = 1.0; m->yx = 0.0; m->xy = 0.0; m->yy = 1.0; m->x0 = 0.0; m->y0 = 0.0;
}

/* result = a * b: a applies first, then b (cairo_matrix_multiply). */
static void festina_matrix_multiply(cairo_matrix_t *result, const cairo_matrix_t *a, const cairo_matrix_t *b) {
    cairo_matrix_t r;
    r.xx = a->xx * b->xx + a->yx * b->xy;
    r.yx = a->xx * b->yx + a->yx * b->yy;
    r.xy = a->xy * b->xx + a->yy * b->xy;
    r.yy = a->xy * b->yx + a->yy * b->yy;
    r.x0 = a->x0 * b->xx + a->y0 * b->xy + b->x0;
    r.y0 = a->x0 * b->yx + a->y0 * b->yy + b->y0;
    *result = r;
}

static void festina_matrix_translate(cairo_matrix_t *m, double tx, double ty) {
    cairo_matrix_t t;
    festina_matrix_identity(&t);
    t.x0 = tx; t.y0 = ty;
    festina_matrix_multiply(m, &t, m);
}

static void festina_matrix_rotate(cairo_matrix_t *m, double radians) {
    cairo_matrix_t r;
    double s = sin(radians), c = cos(radians);
    r.xx = c; r.yx = s; r.xy = -s; r.yy = c; r.x0 = 0.0; r.y0 = 0.0;
    festina_matrix_multiply(m, &r, m);
}

static void festina_matrix_scale(cairo_matrix_t *m, double sx, double sy) {
    cairo_matrix_t k;
    festina_matrix_identity(&k);
    k.xx = sx; k.yy = sy;
    festina_matrix_multiply(m, &k, m);
}

/* saveState()/restoreState() save the whole drawing state, not just the
 * transform -- that is what the canvas save()/restore() this mirrors
 * does, and restoring a transform while leaving a colour changed is
 * exactly the kind of half-measure that produces baffling bugs. */
typedef struct {
    cairo_matrix_t transform;
    double fill_r, fill_g, fill_b, alpha;
    int fill_none;
    /* runtime.md phase 4 slice 7 / decisions.md #350: the gradient is
     * part of the fill, so it is part of the state. It was not, and
     * festina_set_fill_source reads the gradient FIRST -- so a gradient
     * set between saveState() and restoreState() outlived the restore,
     * and the next fill used it instead of the colour that was saved.
     * The comment above this struct warns about exactly that half-
     * measure. A reference is held while saved and handed back on
     * restore, so the saved pattern survives whatever fillStyle() or a
     * later gradient does to the live one in between. */
    cairo_pattern_t *gradient;
    FestinaGradientParams grad;
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
/* claude.md #240: fault a fresh image surface's pixel memory in NOW,
 * in one batch, instead of one page at a time on first touch.
 *
 * A new 800x600 ARGB32 surface is 1.92MB that Cairo gets from calloc,
 * which for a block that size is fresh, untouched mmap'd memory: every
 * one of its 469 pages is materialized by a page fault the first time
 * something writes to it. Drawing into the surface later pays those
 * faults scattered through the draw calls, a couple of microseconds
 * each -- invisible on one thread (the layered-canvas benchmark's
 * single-threaded run loses ~2ms per layer to them), but the reason
 * four threads painting four fresh `img?` layers at once ran no faster
 * than one: concurrent first-touch faults in one process serialize on
 * the kernel's per-process memory lock, so four threads' worth of them
 * take four threads' worth of time, and every layer finished in the
 * 11-12ms a single thread would have taken for all four together.
 * Measured (this machine, 4 threads, 40,000 stamps into 4 layers):
 * 12.2ms wall with lazily-faulted surfaces, 3.4ms with pre-faulted
 * ones, and the pre-faulting itself costs 0.5ms per surface on main.
 *
 * MADV_POPULATE_WRITE (Linux 5.14+) does it in one syscall without a
 * trap per page -- half the cost of touching them from user space,
 * which is the fallback everywhere else (a volatile read-back write of
 * one byte per page, so the pixels are never changed, only
 * materialized). Applied at every ARGB32 surface creation whose whole
 * area is about to be used anyway (a blank image, a clip, a canvas
 * snapshot, a resize, the backing store), so the faults move rather
 * than multiply: for a single-threaded program this is at worst a
 * wash and usually a small win; for one drawing from several threads
 * it is the difference between parallel and serial. */
#if defined(__linux__)
#include <sys/mman.h>
#ifndef MADV_POPULATE_WRITE
#define MADV_POPULATE_WRITE 23
#endif
#endif
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

/* runtime.md phase 7, slice 1: the one place a colour surface's pixels
 * are allocated.
 *
 * The buffer is OURS -- calloc'd here, freed when the last reference to
 * the surface goes (the destroy callback below) -- and Cairo is lent a
 * view of it (cairo_image_surface_create_for_data: same pointer, same
 * premultiplied ARGB32, no copy; measured 258 ns for a wrapper and a
 * context). That is the whole change, and it is deliberately not
 * visible: the same sizes, the same stride, zeroed the way Cairo
 * zeroes its own, so every caller behaves as it did. What it does is
 * settle who owns the pixels, which is the question phase 7 has to have
 * answered before anything else can stop being Cairo's -- the direct
 * paths from #104 and #240 already write these bytes without asking it.
 *
 * Anything Cairo would refuse (a non-positive or oversized dimension) or
 * that cannot be allocated goes to cairo_image_surface_create itself, so
 * the error surface, and the status callers check, are Cairo's own. Not
 * routed through here: the A8 coverage masks (small, and never
 * presented), and the surface cairo_image_surface_create_from_png_stream
 * decodes -- the fallback for PNGs png.f refuses, which stays Cairo's
 * until phase 7's decoder slice. */
static cairo_user_data_key_t g_surface_buffer_key;

static void festina_surface_buffer_free(void *buffer) {
    free(buffer);
}

static cairo_surface_t *festina_surface_create(cairo_format_t format, int width, int height) {
    if (width <= 0 || height <= 0) return cairo_image_surface_create(format, width, height);
    int stride = cairo_format_stride_for_width(format, width);
    if (stride <= 0) return cairo_image_surface_create(format, width, height);
    unsigned char *buffer = calloc((size_t)stride, (size_t)height);
    if (!buffer) return cairo_image_surface_create(format, width, height);
    cairo_surface_t *surface = cairo_image_surface_create_for_data(buffer, format, width,
                                                                    height, stride);
    if (cairo_surface_status(surface) != CAIRO_STATUS_SUCCESS) {
        /* Cairo's own verdict on the size; the buffer was never adopted. */
        free(buffer);
        return surface;
    }
    if (cairo_surface_set_user_data(surface, &g_surface_buffer_key, buffer,
                                    festina_surface_buffer_free) != CAIRO_STATUS_SUCCESS) {
        cairo_surface_destroy(surface);
        free(buffer);
        return cairo_image_surface_create(format, width, height);
    }
    return surface;
}

static void festina_surface_prefault(cairo_surface_t *s) {
    if (!s || cairo_surface_status(s) != CAIRO_STATUS_SUCCESS) return;
    unsigned char *data = cairo_image_surface_get_data(s);
    if (!data) return;
    size_t bytes = (size_t)cairo_image_surface_get_stride(s)
                 * (size_t)cairo_image_surface_get_height(s);
    if (bytes == 0) return;
#if defined(__linux__)
    {
        uintptr_t page = 4096;
        uintptr_t lo = (uintptr_t)data & ~(page - 1);
        uintptr_t hi = ((uintptr_t)data + bytes + page - 1) & ~(page - 1);
        if (madvise((void *)lo, hi - lo, MADV_POPULATE_WRITE) == 0) return;
        /* an older kernel (EINVAL) or a locked-down one: fall through */
    }
#endif
    volatile unsigned char *p = data;
    for (size_t i = 0; i < bytes; i += 4096) p[i] = p[i];
    p[bytes - 1] = p[bytes - 1];
}

static void festina_backing_require(void) {
    if (g_backing_surface) return;
    g_backing_surface = festina_surface_create(CAIRO_FORMAT_ARGB32, (int)g_canvas_width, (int)g_canvas_height);
    festina_surface_prefault(g_backing_surface);
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

/* ---- runtime.md phase 5, slice 6: text through font.f ----
 *
 * The default face -- sans-serif, regular, at a whole-pixel size -- is
 * drawn and measured by runtime/festina/text.f from the bundled DejaVu
 * Sans, unhinted and in greyscale (decisions.md #352), instead of by
 * Cairo and whatever font this machine's fontconfig picks. Everything
 * else stays with Cairo, unchanged: bold (the bold face is not
 * bundled), italic (which today draws upright on a default Linux
 * install -- runtime.md phase 5 -- and changing that is a separate
 * decision), any other family, and a transform that scales or rotates.
 *
 * text.f answers coverage; this side composites it with the same
 * source it always set, so colour, fillAlpha, and text's standing
 * indifference to gradients are unchanged. FESTINA_CAIRO_TEXT=1 sends
 * everything back to Cairo -- the comparison the tests draw against. */
#include "festina_text_hooks.h"
#include "festina_draw_hooks.h"   /* runtime.md phase 7 slice 4: draw.f */

static const FestinaTextHooks *g_text_hooks = NULL;

void festina_text_register(const FestinaTextHooks *hooks) {
    g_text_hooks = hooks;
}

/* text.f, font.f and raster.f keep their working state in globals --
 * the open font, raster.f's scanline scratch -- and a worker thread
 * may draw text into its own `img` (claude.md #234) while another
 * does the same. Cairo's text path was safe for that, so this one is
 * serialised: one call into the component at a time. A spinlock
 * rather than a mutex, the same plain-C11 stance #240's lock-free
 * circle cache takes; a call holds it for the time one line of text
 * takes to rasterise. */
static atomic_flag g_text_lock = ATOMIC_FLAG_INIT;

static void festina_text_lock(void) {
    while (atomic_flag_test_and_set_explicit(&g_text_lock, memory_order_acquire)) {
        /* spin */
    }
}

static void festina_text_unlock(void) {
    atomic_flag_clear_explicit(&g_text_lock, memory_order_release);
}

static int festina_text_ci_equal(const char *a, const char *b) {
    while (*a && *b) {
        if (tolower((unsigned char)*a) != tolower((unsigned char)*b)) return 0;
        a++;
        b++;
    }
    return *a == *b;
}

/* Is the current font text.f's to draw? */
static int festina_text_ours(void) {
    static atomic_int cairo_only = -1;       /* -1: environment not read yet */
    if (!g_text_hooks) return 0;
    int only = atomic_load_explicit(&cairo_only, memory_order_relaxed);
    if (only < 0) {
        const char *e = getenv("FESTINA_CAIRO_TEXT");
        only = e && *e && strcmp(e, "0") != 0;
        atomic_store_explicit(&cairo_only, only, memory_order_relaxed);
    }
    if (only) return 0;
    if (g_font_slant != CAIRO_FONT_SLANT_NORMAL) return 0;
    if (g_font_weight != CAIRO_FONT_WEIGHT_NORMAL) return 0;
    if (!festina_text_ci_equal(g_font_family, "sans-serif")) return 0;
    if (g_font_size < 1.0 || g_font_size > 4096.0 || g_font_size != floor(g_font_size)) return 0;
    return 1;
}

/* The font's bytes as the arr[int] text.f reads, built once and never
 * freed: its refcount is the negative immortal sentinel, so no
 * Festina code that holds it -- font.f keeps it in a global -- ever
 * releases it. Called only with the text lock held. */
static void *festina_text_font(void) {
    static void *payload = NULL;
    if (payload) return payload;
    int64_t n = g_text_hooks->font_len;
    char *raw = calloc(1, sizeof(int64_t) + 2 * sizeof(int64_t));
    int64_t *data = malloc((size_t)(n ? n : 1) * sizeof(int64_t));
    if (!raw || !data) festina_fail("out of memory loading the bundled font");
    *(int64_t *)raw = -1;
    int64_t *header = (int64_t *)(raw + sizeof(int64_t));
    for (int64_t i = 0; i < n; i++) data[i] = g_text_hooks->font[i];
    header[0] = n;
    memcpy(&header[1], &data, sizeof(int64_t *));
    payload = header;
    return payload;
}

/* Draw `text` with its pen at (x, y) on `cr`, whose source is already
 * set, if text.f can: returns 1 when it did (including a line with no
 * ink), 0 to fall back to Cairo. Only under an identity transform or a
 * whole-pixel translation, where the mask lands on device pixels as it
 * was computed; a scale or rotation would resample it. */
static int festina_text_draw(cairo_t *cr, const cairo_matrix_t *m, const char *text,
                             int64_t x, int64_t y) {
    if (!festina_text_ours()) return 0;
    if (m && (m->xx != 1.0 || m->yy != 1.0 || m->xy != 0.0 || m->yx != 0.0 ||
              m->x0 != floor(m->x0) || m->y0 != floor(m->y0))) return 0;
    festina_text_lock();
    int64_t *arr = g_text_hooks->mask(text, (int64_t)g_font_size, festina_text_font());
    festina_text_unlock();
    if (!arr) return 0;
    int64_t len = arr[0];
    int64_t *d;
    memcpy(&d, &arr[1], sizeof(d));
    int handled = 0;
    if (len >= 4) {
        int64_t x0 = d[0], y0 = d[1], w = d[2], h = d[3];
        if (w <= 0 || h <= 0) {
            handled = 1;                    /* nothing inked: nothing to draw */
        } else if (len == 4 + w * h && w < 65536 && h < 65536) {
            cairo_surface_t *mask = cairo_image_surface_create(CAIRO_FORMAT_A8, (int)w, (int)h);
            if (cairo_surface_status(mask) == CAIRO_STATUS_SUCCESS) {
                unsigned char *px = cairo_image_surface_get_data(mask);
                int stride = cairo_image_surface_get_stride(mask);
                for (int64_t row = 0; row < h; row++) {
                    for (int64_t col = 0; col < w; col++) {
                        px[row * stride + col] = (unsigned char)d[4 + row * w + col];
                    }
                }
                cairo_surface_mark_dirty(mask);
                cairo_mask_surface(cr, mask, (double)(x + x0), (double)(y + y0));
                handled = 1;
            }
            cairo_surface_destroy(mask);
        }
    }
    festina_release_array(arr);
    return handled;
}

/* claude.md #89: measuring deliberately does NOT require the canvas
 * window. Text metrics depend only on the font, so these run against a
 * tiny scratch image surface and work in a program that never draws
 * anything at all (the same reasoning that keeps loadImage() from
 * forcing a window open -- see festina_load_image's own note). */
static cairo_t *festina_measure_context(void) {
    static cairo_surface_t *scratch = NULL;
    if (!scratch) {
        scratch = festina_surface_create(CAIRO_FORMAT_ARGB32, 1, 1);
    }
    cairo_t *cr = cairo_create(scratch);
    festina_apply_font(cr);
    return cr;
}

int64_t festina_measure_text_width(const char *text) {
    if (!text) text = "";
    if (festina_text_ours()) {
        festina_text_lock();
        int64_t w = g_text_hooks->width(text, (int64_t)g_font_size, festina_text_font());
        festina_text_unlock();
        if (w >= 0) return w;
    }
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
    if (festina_text_ours()) {
        festina_text_lock();
        int64_t h = g_text_hooks->height(text, (int64_t)g_font_size, festina_text_font());
        festina_text_unlock();
        if (h >= 0) return h;
    }
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
        festina_matrix_identity(&g_transform);
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
/* runtime.md phase 4 slice 6: the gradient, with fillAlpha carried in
 * its own stops.
 *
 * This replaces a cairo_paint_with_alpha() call that was a real bug,
 * found by comparing raster.f's gradients against Cairo's. paint()
 * ignores the current path and covers the whole clip region -- which
 * for the canvas is the whole canvas -- so a gradient drawn with
 * fillAlpha(0.5) did two wrong things at once: it washed a
 * half-transparent gradient over EVERYTHING outside the shape, and
 * then the caller's cairo_fill() drew the shape itself at full
 * opacity, so fillAlpha was ignored exactly where it was asked for.
 * api.md documents fillAlpha as applying to every fill; measured, a
 * pixel inside a 50%-alpha gradient rect was opaque red and one 400px
 * away was blue-tinted.
 *
 * Scaling the stops' alpha instead makes the SOURCE translucent, which
 * is right for every caller -- fill, fill_preserve and show_text alike
 * -- because none of them paints outside what it draws. Cairo
 * interpolates gradients premultiplied, so two stops sharing an alpha
 * give exactly the opaque gradient times that alpha. */
static cairo_pattern_t *festina_gradient_with_alpha(cairo_pattern_t *src, double alpha) {
    cairo_pattern_t *out;
    double x0, y0, x1, y1, r0, r1;
    if (cairo_pattern_get_type(src) == CAIRO_PATTERN_TYPE_LINEAR) {
        cairo_pattern_get_linear_points(src, &x0, &y0, &x1, &y1);
        out = cairo_pattern_create_linear(x0, y0, x1, y1);
    } else {
        cairo_pattern_get_radial_circles(src, &x0, &y0, &r0, &x1, &y1, &r1);
        out = cairo_pattern_create_radial(x0, y0, r0, x1, y1, r1);
    }
    int n = 0;
    cairo_pattern_get_color_stop_count(src, &n);
    for (int i = 0; i < n; i++) {
        double off, r, g, b, a;
        cairo_pattern_get_color_stop_rgba(src, i, &off, &r, &g, &b, &a);
        cairo_pattern_add_color_stop_rgba(out, off, r, g, b, a * alpha);
    }
    return out;
}

static void festina_set_fill_source(cairo_t *cr) {
    if (g_fill_gradient) {
        if (g_fill_alpha >= 1.0) {
            cairo_set_source(cr, g_fill_gradient);
            return;
        }
        /* cairo_set_source takes its own reference, so this one can be
         * dropped at once and the pattern lives exactly as long as it is
         * the source. */
        cairo_pattern_t *p = festina_gradient_with_alpha(g_fill_gradient, g_fill_alpha);
        cairo_set_source(cr, p);
        cairo_pattern_destroy(p);
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
    g_grad.kind = 0;
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
    g_grad.kind = 1;
    g_grad.geom[0] = (double)x0; g_grad.geom[1] = (double)y0;
    g_grad.geom[2] = (double)x1; g_grad.geom[3] = (double)y1;
    g_grad.c0[0] = r0 * 255.0; g_grad.c0[1] = g0 * 255.0; g_grad.c0[2] = b0 * 255.0;
    g_grad.c1[0] = r1 * 255.0; g_grad.c1[1] = g1 * 255.0; g_grad.c1[2] = b1 * 255.0;
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
    g_grad.kind = 2;
    g_grad.geom[0] = (double)x; g_grad.geom[1] = (double)y; g_grad.geom[2] = (double)radius;
    g_grad.c0[0] = ri * 255.0; g_grad.c0[1] = gi * 255.0; g_grad.c0[2] = bi * 255.0;
    g_grad.c1[0] = ro * 255.0; g_grad.c1[1] = go * 255.0; g_grad.c1[2] = bo * 255.0;
    g_fill_none = 0;
}

/* ---- claude.md #94: transforms ---- */

void festina_translate(int64_t x, int64_t y) {
    if (!g_transform_ready) { festina_matrix_identity(&g_transform); g_transform_ready = 1; }
    festina_matrix_translate(&g_transform, (double)x, (double)y);
}

void festina_rotate(double degrees) {
    if (!g_transform_ready) { festina_matrix_identity(&g_transform); g_transform_ready = 1; }
    /* Degrees, not radians: this language has no angle type to make the
     * unit self-documenting, and degrees are what a program author
     * reaches for. Math.PI is available for anyone who wants radians. */
    festina_matrix_rotate(&g_transform, degrees * 3.14159265358979323846 / 180.0);
}

void festina_scale(double sx, double sy) {
    if (!g_transform_ready) { festina_matrix_identity(&g_transform); g_transform_ready = 1; }
    /* A zero scale collapses the matrix to something non-invertible,
     * which makes every later Cairo call on it fail silently. */
    if (sx == 0.0 || sy == 0.0) return;
    festina_matrix_scale(&g_transform, sx, sy);
}

void festina_reset_transform(void) {
    festina_matrix_identity(&g_transform);
    g_transform_ready = 1;
}

void festina_save_state(void) {
    if (g_state_depth >= FESTINA_STATE_STACK_MAX) {
        festina_fail("saveState(): nested too deeply (limit 64) -- is a "
                      "restoreState() missing?");
    }
    if (!g_transform_ready) { festina_matrix_identity(&g_transform); g_transform_ready = 1; }
    FestinaCanvasState *st = &g_state_stack[g_state_depth++];
    st->transform = g_transform;
    st->fill_r = g_fill_r; st->fill_g = g_fill_g; st->fill_b = g_fill_b;
    st->alpha = g_fill_alpha; st->fill_none = g_fill_none;
    st->gradient = g_fill_gradient ? cairo_pattern_reference(g_fill_gradient) : NULL;
    st->grad = g_grad;
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
    festina_clear_gradient();
    g_fill_gradient = st->gradient;     /* the saved reference, handed back */
    g_grad = st->grad;
    st->gradient = NULL;
    g_border_r = st->border_r; g_border_g = st->border_g; g_border_b = st->border_b;
    g_line_width = st->line_width; g_border_set = st->border_set;
    g_font_size = st->font_size; g_font_slant = st->font_slant;
    g_font_weight = st->font_weight;
    snprintf(g_font_family, sizeof(g_font_family), "%s", st->font_family);
}

/* ---- claude.md #94: paths ---- */

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

/* ---- claude.md #240: the solid-fill fast path -------------------------
 *
 * The overwhelmingly common draw call -- a flat colour (opaque, and since
 * runtime.md phase 7 translucent too, over what is there),
 * no border, no gradient, an integer position on an untransformed (or
 * whole-pixel-translated) surface -- does not need a rasterizer at
 * all. A rectangle at integer coordinates covers whole pixels, so its
 * result is simply the source colour written into each of them; a
 * circle's coverage per pixel is the same for every circle of that
 * radius, so it can be rasterized ONCE and blended by hand thereafter.
 * Going through Cairo for these costs a context, a path, a gstate
 * lookup, a compositor dispatch and a pixman call per shape -- about
 * 0.5 us for a 6x6 rectangle and 2.5 us for a radius-2 circle on the
 * layered-canvas benchmark's machine, against ~30 ns for the direct
 * write. Measured before this existed (single thread, per layer of that
 * benchmark): 12,000 radius-2 circles 32 ms, 9,000 radius-5 circles
 * 40 ms, 8,000 6x6 rectangles 4 ms, 11,000 5x5 rectangles 6 ms.
 *
 * Exactness is the whole contract here, and it is what makes this safe
 * to switch on silently: the coverage bytes come from Cairo's own
 * rasterizer (the same A8 mask claude.md #104's canvas circle cache
 * already stamps), and the blend is pixman's own 8-bit premultiplied
 * OVER, reproduced operation for operation (MUL_UN8's rounding, the
 * saturating add) so the pixels are BYTE-IDENTICAL to what
 * cairo_mask_surface produced -- verified by drawing the same scene both
 * ways and comparing the PNGs (tests/test_codegen.py's
 * TestSolidFillFastPath), not by eyeballing. Anything outside the
 * contract -- an active gradient, a border, a
 * rotation/scale/fractional translation, a colour of `none`, a radius
 * over FESTINA_COVERAGE_MAX_RADIUS -- takes the Cairo path exactly as
 * before, so nothing observable changes except the time.
 *
 * The coverage cache is one immutable record per radius, published
 * with a compare-and-swap into a fixed table and never freed or
 * replaced: any thread can look one up with a single acquire load, and
 * two threads racing to create the same radius simply agree on
 * whichever won (the loser frees its copy). That lock-free shape is
 * what lets four `thread`s stamp circles into four `img?` layers at
 * once (the layered-canvas benchmark) without a mutex on the hot path
 * and without ThreadSanitizer having anything to report. Bounded by
 * construction: at most FESTINA_COVERAGE_MAX_RADIUS + 1 records, ~2.9MB
 * if a program genuinely used every radius, reachable from a global so
 * LeakSanitizer never counts it.
 *
 * FESTINA_NO_DIRECT_FILL=1 in the environment switches the fast path
 * off (read once). That is the test hook the byte-identity check uses
 * -- same program, same scene, two PNGs -- and the escape hatch if a
 * platform's pixman ever disagreed with the arithmetic here. */
/* ---- runtime.md phase 7, slice 4: shapes drawn by draw.f ----------------
 *
 * The hooks draw.f registers (runtime/festina_draw_hooks.h). Where a draw
 * call used to build a Cairo context, a path and a fill, it asks here
 * first: the state it owns -- colours, alpha, line width, the transform
 * -- is read into arguments, draw.f works out the outline, coverage and
 * stroke in Festina, and the blending goes back through
 * festina_image_blend_row into the surface's own bytes. A program that
 * does not link draw.f has no hooks, and FESTINA_CAIRO_DRAW=1 in the
 * environment turns them off; both draw through Cairo exactly as before.
 *
 * Calls are serialised by a
 * spinlock: draw.f keeps one coverage row in a global, as text.f does,
 * and the four threads of the layered-canvas benchmark draw into their own
 * layers at once. (The direct-pixel fast paths above take no lock.) */
static const FestinaDrawHooks *g_draw_hooks = NULL;
static atomic_flag g_draw_lock = ATOMIC_FLAG_INIT;
static atomic_int g_draw_enabled = -1;

void festina_draw_register(const FestinaDrawHooks *hooks) {
    g_draw_hooks = hooks;
}

static int festina_draw_ours(void) {
    if (!g_draw_hooks) return 0;
    int v = atomic_load_explicit(&g_draw_enabled, memory_order_relaxed);
    if (v < 0) {
        const char *e = getenv("FESTINA_CAIRO_DRAW");
        v = (e && *e && *e != '0') ? 0 : 1;
        atomic_store_explicit(&g_draw_enabled, v, memory_order_relaxed);
    }
    return v;
}

/* An arr[float] the way Festina lays one out -- a block holding the
 * refcount and the {length, data} payload, the doubles in a second
 * allocation -- with a refcount of one, for festina_release_array. */
static void *festina_farray(const double *v, int64_t n) {
    char *raw = calloc(1, 3 * sizeof(int64_t));
    double *data = malloc((size_t)(n ? n : 1) * sizeof(double));
    if (!raw || !data) festina_fail("out of memory drawing");
    *(int64_t *)raw = 1;
    int64_t *payload = (int64_t *)(raw + sizeof(int64_t));
    payload[0] = n;
    memcpy(&payload[1], &data, sizeof(data));
    for (int64_t i = 0; i < n; i++) data[i] = v[i];
    return payload;
}

static int festina_draw_byte(double unit) {
    int v = (int)(unit * 255.0 + 0.5);
    return v < 0 ? 0 : (v > 255 ? 255 : v);
}

/* One rectangle (a, b, c, d = x, y, w, h) or circle (a, b, c = cx, cy, r)
 * in user space under `m`, filled and/or stroked the way
 * festina_fill_and_border_override decides, through draw.f. Returns 1 if
 * drawn (or if there was nothing to draw) and 0 if the caller has to draw
 * it the old way. */
static int festina_raster_shape(FestinaImageBox *box, const cairo_matrix_t *m, int circle,
                                double a, double b, double c, double d,
                                int fill_overridden, int64_t fill_color,
                                int border_overridden, int64_t border_color) {
    if (!festina_draw_ours()) return 0;
    if (!box || !box->surface || cairo_surface_get_type(box->surface) != CAIRO_SURFACE_TYPE_IMAGE) return 0;
    cairo_format_t fmt = cairo_image_surface_get_format(box->surface);
    if (fmt != CAIRO_FORMAT_ARGB32 && fmt != CAIRO_FORMAT_RGB24) return 0;
    int fill_none = fill_overridden ? (fill_color < 0) : g_fill_none;
    int border_set = border_overridden ? (border_color >= 0) : g_border_set;
    int border = border_set && g_line_width > 0.0;
    if (fill_none && !border) return 1;
    int br, bg, bb;
    /* The fill's source: a flat colour, or the gradient (an explicit
     * colour argument bypasses it, as it always did). */
    double src[11];
    int64_t nsrc = 4;
    if (fill_overridden) {
        src[0] = 0; src[1] = (double)((fill_color >> 16) & 0xFF);
        src[2] = (double)((fill_color >> 8) & 0xFF); src[3] = (double)(fill_color & 0xFF);
    } else if (g_fill_gradient && g_grad.kind != 0) {
        int k = 0;
        src[k++] = (double)g_grad.kind;
        int ng = g_grad.kind == 1 ? 4 : 3;
        for (int i = 0; i < ng; i++) src[k++] = g_grad.geom[i];
        for (int i = 0; i < 3; i++) src[k++] = g_grad.c0[i];
        for (int i = 0; i < 3; i++) src[k++] = g_grad.c1[i];
        nsrc = k;
    } else {
        src[0] = 0; src[1] = (double)festina_draw_byte(g_fill_r);
        src[2] = (double)festina_draw_byte(g_fill_g); src[3] = (double)festina_draw_byte(g_fill_b);
    }
    if (border_overridden) {
        br = (int)((border_color >> 16) & 0xFF); bg = (int)((border_color >> 8) & 0xFF); bb = (int)(border_color & 0xFF);
    } else {
        br = festina_draw_byte(g_border_r); bg = festina_draw_byte(g_border_g); bb = festina_draw_byte(g_border_b);
    }
    void *fsrc = festina_farray(src, nsrc);
    double mv[6] = { m->xx, m->yx, m->xy, m->yy, m->x0, m->y0 };
    void *matrix = festina_farray(mv, 6);
    while (atomic_flag_test_and_set_explicit(&g_draw_lock, memory_order_acquire)) { /* spin */ }
    if (circle) {
        g_draw_hooks->circle(box, a, b, c, !fill_none, fsrc, border, br, bg, bb,
                             g_line_width, g_fill_alpha, matrix);
    } else {
        g_draw_hooks->rect(box, a, b, c, d, !fill_none, fsrc, border, br, bg, bb,
                           g_line_width, g_fill_alpha, matrix);
    }
    atomic_flag_clear_explicit(&g_draw_lock, memory_order_release);
    festina_release_array(matrix);
    festina_release_array(fsrc);
    return 1;
}

/* The canvas as a target: its backing surface in a box that lives for
 * the call, and the transform it is drawn under (the identity until one
 * is set). */
static int festina_canvas_raster_shape(int circle, double a, double b, double c, double d,
                                       int fill_overridden, int64_t fill_color,
                                       int border_overridden, int64_t border_color) {
    FestinaImageBox box;
    memset(&box, 0, sizeof(box));
    box.surface = g_backing_surface;
    cairo_matrix_t ident;
    festina_matrix_identity(&ident);
    return festina_raster_shape(&box, g_transform_ready ? &g_transform : &ident, circle,
                                a, b, c, d, fill_overridden, fill_color,
                                border_overridden, border_color);
}

/* drawPixel through draw.f: point-sampled, fill only. */
static int festina_raster_pixel(FestinaImageBox *box, const cairo_matrix_t *m, int64_t x, int64_t y,
                                int fill_overridden, int64_t fill_color) {
    if (!festina_draw_ours()) return 0;
    if (!box || !box->surface || cairo_surface_get_type(box->surface) != CAIRO_SURFACE_TYPE_IMAGE) return 0;
    int fill_none = fill_overridden ? (fill_color < 0) : g_fill_none;
    if (fill_none) return 1;
    double src[11];
    int64_t nsrc = 4;
    if (fill_overridden) {
        src[0] = 0; src[1] = (double)((fill_color >> 16) & 0xFF);
        src[2] = (double)((fill_color >> 8) & 0xFF); src[3] = (double)(fill_color & 0xFF);
    } else if (g_fill_gradient && g_grad.kind != 0) {
        int k = 0;
        src[k++] = (double)g_grad.kind;
        int ng = g_grad.kind == 1 ? 4 : 3;
        for (int i = 0; i < ng; i++) src[k++] = g_grad.geom[i];
        for (int i = 0; i < 3; i++) src[k++] = g_grad.c0[i];
        for (int i = 0; i < 3; i++) src[k++] = g_grad.c1[i];
        nsrc = k;
    } else {
        src[0] = 0; src[1] = (double)festina_draw_byte(g_fill_r);
        src[2] = (double)festina_draw_byte(g_fill_g); src[3] = (double)festina_draw_byte(g_fill_b);
    }
    double mv[6] = { m->xx, m->yx, m->xy, m->yy, m->x0, m->y0 };
    void *matrix = festina_farray(mv, 6);
    void *fsrc = festina_farray(src, nsrc);
    while (atomic_flag_test_and_set_explicit(&g_draw_lock, memory_order_acquire)) { /* spin */ }
    g_draw_hooks->pixel(box, (double)x, (double)y, 1, fsrc, g_fill_alpha, matrix);
    atomic_flag_clear_explicit(&g_draw_lock, memory_order_release);
    festina_release_array(matrix);
    festina_release_array(fsrc);
    return 1;
}

/* clearRect / clearCircle / clearPixel through draw.f (kind 0, 1, 2). */
static int festina_raster_clear(FestinaImageBox *box, const cairo_matrix_t *m, int64_t kind,
                                double a, double b, double c, double d) {
    if (!festina_draw_ours()) return 0;
    if (!box || !box->surface || cairo_surface_get_type(box->surface) != CAIRO_SURFACE_TYPE_IMAGE) return 0;
    double mv[6] = { m->xx, m->yx, m->xy, m->yy, m->x0, m->y0 };
    void *matrix = festina_farray(mv, 6);
    while (atomic_flag_test_and_set_explicit(&g_draw_lock, memory_order_acquire)) { /* spin */ }
    g_draw_hooks->clear(box, kind, a, b, c, d, matrix);
    atomic_flag_clear_explicit(&g_draw_lock, memory_order_release);
    festina_release_array(matrix);
    return 1;
}

/* Clearing a whole surface: every byte to zero, which is what SOURCE with
 * a transparent source leaves in both colour formats. */
static void festina_surface_zero(cairo_surface_t *s) {
    if (!s || cairo_surface_get_type(s) != CAIRO_SURFACE_TYPE_IMAGE) return;
    cairo_surface_flush(s);
    unsigned char *data = cairo_image_surface_get_data(s);
    if (!data) return;
    memset(data, 0, (size_t)cairo_image_surface_get_stride(s) * (size_t)cairo_image_surface_get_height(s));
    cairo_surface_mark_dirty(s);
}

static void festina_canvas_box(FestinaImageBox *box) {
    memset(box, 0, sizeof(*box));
    box->surface = g_backing_surface;
}

static const cairo_matrix_t *festina_canvas_matrix(cairo_matrix_t *ident) {
    festina_matrix_identity(ident);
    return g_transform_ready ? &g_transform : ident;
}

/* runtime.md phase 7 slice 4: when draw.f is drawing, the path is kept as
 * plain numbers -- one code per segment and the coordinates they consume
 * -- together with the transform in force at beginPath(), which is where
 * a Cairo context would have fixed it too. fillPath()/strokePath() hand it
 * to draw.f in one call. With Cairo drawing (no draw.f, or
 * FESTINA_CAIRO_DRAW=1) the Cairo context below is kept as it always was. */
static int g_path_ours = 0;
static int64_t *g_path_ops = NULL;
static int64_t g_path_nops = 0, g_path_ops_cap = 0;
static double *g_path_coords = NULL;
static int64_t g_path_ncoords = 0, g_path_coords_cap = 0;
static cairo_matrix_t g_path_matrix;

static void festina_path_reset_ours(void) {
    g_path_nops = 0;
    g_path_ncoords = 0;
}

static void festina_path_push(int64_t op, const double *c, int n) {
    if (g_path_nops == g_path_ops_cap) {
        g_path_ops_cap = g_path_ops_cap ? g_path_ops_cap * 2 : 16;
        g_path_ops = realloc(g_path_ops, (size_t)g_path_ops_cap * sizeof(int64_t));
        if (!g_path_ops) festina_fail("out of memory building a path");
    }
    if (g_path_ncoords + n > g_path_coords_cap) {
        while (g_path_ncoords + n > g_path_coords_cap) g_path_coords_cap = g_path_coords_cap ? g_path_coords_cap * 2 : 64;
        g_path_coords = realloc(g_path_coords, (size_t)g_path_coords_cap * sizeof(double));
        if (!g_path_coords) festina_fail("out of memory building a path");
    }
    g_path_ops[g_path_nops++] = op;
    for (int i = 0; i < n; i++) g_path_coords[g_path_ncoords++] = c[i];
}

/* An arr[int], the way festina_farray makes an arr[float]. */
static void *festina_iarray(const int64_t *v, int64_t n) {
    char *raw = calloc(1, 3 * sizeof(int64_t));
    int64_t *data = malloc((size_t)(n ? n : 1) * sizeof(int64_t));
    if (!raw || !data) festina_fail("out of memory drawing");
    *(int64_t *)raw = 1;
    int64_t *payload = (int64_t *)(raw + sizeof(int64_t));
    payload[0] = n;
    memcpy(&payload[1], &data, sizeof(data));
    for (int64_t i = 0; i < n; i++) data[i] = v[i];
    return payload;
}

void festina_begin_path(void) {
    festina_backing_require();
    if (g_path_cr) { cairo_destroy(g_path_cr); g_path_cr = NULL; }
    if (festina_draw_ours()) {
        g_path_ours = 1;
        festina_path_reset_ours();
        festina_matrix_identity(&g_path_matrix);
        if (g_transform_ready) g_path_matrix = g_transform;
        return;
    }
    g_path_ours = 0;
    g_path_cr = festina_canvas_context();
}

static int festina_path_open(const char *fn) {
    if (g_path_cr || g_path_ours) return 1;
    char msg[256];
    snprintf(msg, sizeof(msg),
             "%s(): no path is open -- call beginPath() first", fn);
    festina_fail(msg);
    return 0;
}

void festina_move_to(int64_t x, int64_t y) {
    if (!festina_path_open("moveTo")) return;
    if (g_path_ours) { double c[2] = { (double)x, (double)y }; festina_path_push(0, c, 2); return; }
    cairo_move_to(g_path_cr, (double)x, (double)y);
}

void festina_line_to(int64_t x, int64_t y) {
    if (!festina_path_open("lineTo")) return;
    if (g_path_ours) { double c[2] = { (double)x, (double)y }; festina_path_push(1, c, 2); return; }
    cairo_line_to(g_path_cr, (double)x, (double)y);
}

void festina_curve_to(int64_t cx1, int64_t cy1, int64_t cx2, int64_t cy2,
                       int64_t x, int64_t y) {
    if (!festina_path_open("curveTo")) return;
    if (g_path_ours) {
        double c[6] = { (double)cx1, (double)cy1, (double)cx2, (double)cy2, (double)x, (double)y };
        festina_path_push(2, c, 6);
        return;
    }
    cairo_curve_to(g_path_cr, (double)cx1, (double)cy1, (double)cx2, (double)cy2,
                    (double)x, (double)y);
}

void festina_close_path(void) {
    if (!festina_path_open("closePath")) return;
    if (g_path_ours) { festina_path_push(3, NULL, 0); return; }
    cairo_close_path(g_path_cr);
}

/* The open path through draw.f: filled with the fill source, or stroked in
 * the border colour, then consumed. */
static void festina_path_through_draw(int fill, int border) {
    FestinaImageBox box;
    cairo_matrix_t ident;
    (void)ident;
    festina_canvas_box(&box);
    double src[11];
    int64_t nsrc = 4;
    if (g_fill_gradient && g_grad.kind != 0) {
        int k = 0;
        src[k++] = (double)g_grad.kind;
        int ng = g_grad.kind == 1 ? 4 : 3;
        for (int i = 0; i < ng; i++) src[k++] = g_grad.geom[i];
        for (int i = 0; i < 3; i++) src[k++] = g_grad.c0[i];
        for (int i = 0; i < 3; i++) src[k++] = g_grad.c1[i];
        nsrc = k;
    } else {
        src[0] = 0; src[1] = (double)festina_draw_byte(g_fill_r);
        src[2] = (double)festina_draw_byte(g_fill_g); src[3] = (double)festina_draw_byte(g_fill_b);
    }
    double mv[6] = { g_path_matrix.xx, g_path_matrix.yx, g_path_matrix.xy, g_path_matrix.yy,
                     g_path_matrix.x0, g_path_matrix.y0 };
    void *matrix = festina_farray(mv, 6);
    void *fsrc = festina_farray(src, nsrc);
    void *ops = festina_iarray(g_path_ops, g_path_nops);
    void *coords = festina_farray(g_path_coords, g_path_ncoords);
    while (atomic_flag_test_and_set_explicit(&g_draw_lock, memory_order_acquire)) { /* spin */ }
    g_draw_hooks->path(&box, ops, coords, fill, fsrc, border,
                       festina_draw_byte(g_border_r), festina_draw_byte(g_border_g), festina_draw_byte(g_border_b),
                       g_line_width, g_fill_alpha, matrix);
    atomic_flag_clear_explicit(&g_draw_lock, memory_order_release);
    festina_release_array(matrix);
    festina_release_array(fsrc);
    festina_release_array(ops);
    festina_release_array(coords);
}

/* Both of these consume the path, matching the canvas model where
 * fill()/stroke() end the current path -- keeping it would make a
 * second fill silently paint the same shape twice. */
void festina_fill_path(void) {
    if (!festina_path_open("fillPath")) return;
    if (g_path_ours) {
        if (!g_fill_none) festina_path_through_draw(1, 0);
        g_path_ours = 0;
        festina_path_reset_ours();
        return;
    }
    if (!g_fill_none) {
        festina_set_fill_source(g_path_cr);
        cairo_fill(g_path_cr);
    }
    cairo_destroy(g_path_cr);
    g_path_cr = NULL;
}

void festina_stroke_path(void) {
    if (!festina_path_open("strokePath")) return;
    if (g_path_ours) {
        if (g_border_set && g_line_width > 0.0) festina_path_through_draw(0, 1);
        g_path_ours = 0;
        festina_path_reset_ours();
        return;
    }
    if (g_border_set && g_line_width > 0.0) {
        cairo_set_source_rgba(g_path_cr, g_border_r, g_border_g, g_border_b, g_fill_alpha);
        cairo_set_line_width(g_path_cr, g_line_width);
        cairo_stroke(g_path_cr);
    }
    cairo_destroy(g_path_cr);
    g_path_cr = NULL;
}

#define FESTINA_COVERAGE_MAX_RADIUS 128

typedef struct {
    int size;               /* the mask is size x size: 2r + 2, like #104's */
    unsigned char cov[];    /* row-major coverage, 0..255 */
} FestinaCircleCoverage;

static _Atomic(FestinaCircleCoverage *) g_circle_coverage[FESTINA_COVERAGE_MAX_RADIUS + 1];
static atomic_int g_direct_fill_enabled = -1;   /* -1: environment not read yet */

static int festina_direct_fill_enabled(void) {
    int v = atomic_load_explicit(&g_direct_fill_enabled, memory_order_relaxed);
    if (v < 0) {
        const char *env = getenv("FESTINA_NO_DIRECT_FILL");
        v = !(env && *env);
        atomic_store_explicit(&g_direct_fill_enabled, v, memory_order_relaxed);
    }
    return v;
}

static const FestinaCircleCoverage *festina_circle_coverage(int64_t r) {
    if (r <= 0 || r > FESTINA_COVERAGE_MAX_RADIUS) return NULL;
    FestinaCircleCoverage *have =
        atomic_load_explicit(&g_circle_coverage[r], memory_order_acquire);
    if (have) return have;

    int size = (int)(r * 2) + 2;
    if (festina_draw_ours()) {
        /* Phase 7 slice 4: the coverage comes from draw.f, the rasteriser
         * every other circle is drawn by. */
        while (atomic_flag_test_and_set_explicit(&g_draw_lock, memory_order_acquire)) { /* spin */ }
        int64_t *arr = g_draw_hooks->circle_mask(r);
        atomic_flag_clear_explicit(&g_draw_lock, memory_order_release);
        if (!arr) return NULL;
        if (arr[0] != (int64_t)size * size) { festina_release_array(arr); return NULL; }
        const int64_t *d;
        memcpy(&d, &arr[1], sizeof(d));
        FestinaCircleCoverage *made = malloc(sizeof(*made) + (size_t)size * (size_t)size);
        if (!made) { festina_release_array(arr); return NULL; }
        made->size = size;
        for (int64_t i = 0; i < (int64_t)size * size; i++) {
            int64_t v = d[i];
            made->cov[i] = (unsigned char)(v < 0 ? 0 : (v > 255 ? 255 : v));
        }
        festina_release_array(arr);
        FestinaCircleCoverage *expected = NULL;
        if (!atomic_compare_exchange_strong_explicit(&g_circle_coverage[r], &expected, made,
                                                     memory_order_acq_rel, memory_order_acquire)) {
            free(made);
            return expected;
        }
        return made;
    }

    /* Rasterize exactly the way festina_circle_mask does, so the shape
     * is Cairo's own: an A8 surface of 2r+2 pixels, the arc centred on
     * it, filled with the default antialiasing. */
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
    cairo_surface_flush(mask);

    FestinaCircleCoverage *made = malloc(sizeof(*made) + (size_t)size * (size_t)size);
    if (!made) {
        cairo_surface_destroy(mask);
        return NULL;
    }
    made->size = size;
    const unsigned char *data = cairo_image_surface_get_data(mask);
    int stride = cairo_image_surface_get_stride(mask);
    for (int row = 0; row < size; row++) {
        memcpy(made->cov + (size_t)row * size, data + (size_t)row * stride, (size_t)size);
    }
    cairo_surface_destroy(mask);

    FestinaCircleCoverage *expected = NULL;
    if (!atomic_compare_exchange_strong_explicit(&g_circle_coverage[r], &expected, made,
                                                 memory_order_acq_rel, memory_order_acquire)) {
        free(made);         /* another thread published this radius first */
        return expected;
    }
    return made;
}

/* pixman's UN8 multiply: a*b/255, rounded, in the exact integer form
 * pixman-combine32.h uses (ONE_HALF then the add-the-high-byte trick),
 * which its SSE2/NEON paths are bit-exact with. */
static inline uint32_t festina_mul_un8(uint32_t a, uint32_t b) {
    uint32_t t = a * b + 0x80;
    return ((t >> 8) + t) >> 8;
}

/* `s` (premultiplied, already scaled by coverage) OVER `d`:
 * d = MUL_UN8(d, 255 - s.a) + s per channel, saturating -- pixman's
 * combine_over_u, written the way pixman writes it: two channels to a
 * 32-bit lane (red and blue, then alpha and green), so the four
 * multiplies are two and the four saturating adds are two.
 * tests/test_blend_row.py holds it equal, over millions of random pairs
 * and every edge value, to the channel-at-a-time definition it replaced. */
static inline uint32_t festina_over_un8x4(uint32_t s, uint32_t d) {
    uint32_t sa = s >> 24;
    if (sa == 0xFF) return s;
    if (s == 0) return d;
    uint32_t ia = 0xFF - sa;
    uint32_t rb = (d & 0x00FF00FFu) * ia + 0x00800080u;
    rb = ((rb + ((rb >> 8) & 0x00FF00FFu)) >> 8) & 0x00FF00FFu;
    uint32_t ag = ((d >> 8) & 0x00FF00FFu) * ia + 0x00800080u;
    ag = ((ag + ((ag >> 8) & 0x00FF00FFu)) >> 8) & 0x00FF00FFu;
    uint32_t t = rb + (s & 0x00FF00FFu);
    t |= 0x10000100u - ((t >> 8) & 0x00FF00FFu);
    rb = t & 0x00FF00FFu;
    t = ag + ((s >> 8) & 0x00FF00FFu);
    t |= 0x10000100u - ((t >> 8) & 0x00FF00FFu);
    ag = t & 0x00FF00FFu;
    return rb | (ag << 8);
}

/* One 8-bit channel exactly as Cairo derives it from a double: to a
 * 16-bit short (d * 65535 + 0.5), then pixman's high byte. Every
 * fillStyle()/colour literal in the language is an integer over 255.0,
 * for which this is the identity, but the conversion is spelled out so
 * it can never drift from Cairo's own. */
static inline uint32_t festina_channel_byte(double d) {
    if (d < 0.0) d = 0.0;
    if (d > 1.0) d = 1.0;
    return (uint32_t)(uint16_t)(d * 65535.0 + 0.5) >> 8;
}

/* The solid source as Cairo reduces it, premultiplied: alpha and each
 * channel times alpha, in doubles, each to a 16-bit short and then its
 * high byte -- the same route festina_image_blend_row takes, so the
 * bytes that go OVER the destination are the ones cairo_fill's solid
 * source would have made. At alpha 1.0 it is the opaque pixel the fast
 * path always wrote (channel_byte of r/255 is the identity on a byte),
 * and so is it at 0.999: an alpha of 0.9961 or more is byte 255, and a
 * channel times such an alpha does not drop a byte -- which is also
 * where Cairo itself starts calling a colour opaque. */
static inline uint32_t festina_solid_pixel_premul(double r, double g, double b, double alpha) {
    if (alpha > 1.0) alpha = 1.0;
    if (alpha < 0.0) alpha = 0.0;
    return (festina_channel_byte(alpha) << 24) | (festina_channel_byte(r * alpha) << 16)
         | (festina_channel_byte(g * alpha) << 8) | festina_channel_byte(b * alpha);
}

static inline uint32_t festina_solid_pixel_from_style(void) {
    return festina_solid_pixel_premul(g_fill_r, g_fill_g, g_fill_b, g_fill_alpha);
}

static inline uint32_t festina_solid_pixel_from_color(int64_t color) {
    return festina_solid_pixel_premul(((color >> 16) & 0xFF) / 255.0, ((color >> 8) & 0xFF) / 255.0,
                                      (color & 0xFF) / 255.0, g_fill_alpha);
}

/* The style-state half of the contract for the plain (fillStyle-driven)
 * forms: an opaque flat colour and nothing to stroke. The colour
 * override forms check `color >= 0` in place of g_fill_none and ignore
 * any gradient (they always did -- see festina_fill_and_border_override). */
static int festina_solid_style_ok(void) {
    if (g_fill_none || g_fill_gradient) return 0;
    if (g_border_set && g_line_width > 0.0) return 0;
    return 1;
}

static int festina_solid_override_ok(int64_t color, int border_effective) {
    if (color < 0 || border_effective) return 0;
    return 1;
}

/* A matrix the fast path can honour: identity, or a whole-pixel
 * translation. Same test claude.md #104's festina_circle_fast_path_ok
 * makes, returning the offset instead of just yes/no. */
static int festina_matrix_integer_offset(const cairo_matrix_t *m, int ready,
                                         int64_t *tx, int64_t *ty) {
    if (!ready) { *tx = 0; *ty = 0; return 1; }
    if (m->xx != 1.0 || m->yy != 1.0 || m->xy != 0.0 || m->yx != 0.0) return 0;
    if (m->x0 != floor(m->x0) || m->y0 != floor(m->y0)) return 0;
    if (fabs(m->x0) > 1073741824.0 || fabs(m->y0) > 1073741824.0) return 0;
    *tx = (int64_t)m->x0;
    *ty = (int64_t)m->y0;
    return 1;
}

static int festina_canvas_integer_offset(int64_t *tx, int64_t *ty) {
    return festina_matrix_integer_offset(&g_transform, g_transform_ready, tx, ty);
}

/* Coordinates the arithmetic below can add and clip without overflow.
 * Anything wilder is not a shape anyone meant to draw, and Cairo's
 * fixed-point clamping is the behaviour it already had. */
#define FESTINA_DIRECT_COORD_LIMIT ((int64_t)1 << 30)
static inline int festina_direct_coord_ok(int64_t v) {
    return v > -FESTINA_DIRECT_COORD_LIMIT && v < FESTINA_DIRECT_COORD_LIMIT;
}

/* Pins down the writable pixels of an ARGB32 image surface. Every
 * surface this runtime draws on (the canvas backing store, every img)
 * is one, but the check costs nothing and keeps the assumption honest. */
static uint32_t *festina_direct_target(cairo_surface_t *s, int *stride_px, int *w, int *h) {
    if (cairo_surface_get_type(s) != CAIRO_SURFACE_TYPE_IMAGE) return NULL;
    if (cairo_image_surface_get_format(s) != CAIRO_FORMAT_ARGB32) return NULL;
    cairo_surface_flush(s);
    unsigned char *data = cairo_image_surface_get_data(s);
    if (!data) return NULL;
    *stride_px = cairo_image_surface_get_stride(s) / 4;
    *w = cairo_image_surface_get_width(s);
    *h = cairo_image_surface_get_height(s);
    return (uint32_t *)data;
}

/* Fills the integer rectangle (x+tx, y+ty, w, h) with a solid pixel
 * (premultiplied; translucent goes OVER what is there).
 * Returns 0 -- having touched nothing -- when the fast path does not
 * apply, so the caller falls through to Cairo. A negative width or
 * height extends the other way, as cairo_rectangle's does. */
static int festina_direct_rect(cairo_surface_t *s, int64_t x, int64_t y, int64_t tx, int64_t ty,
                               int64_t w, int64_t h, uint32_t pixel) {
    if (!festina_direct_coord_ok(x) || !festina_direct_coord_ok(y) ||
        !festina_direct_coord_ok(w) || !festina_direct_coord_ok(h)) return 0;
    if (w < 0) { x += w; w = -w; }
    if (h < 0) { y += h; h = -h; }
    x += tx;
    y += ty;
    int stride_px, sw, sh;
    uint32_t *data = festina_direct_target(s, &stride_px, &sw, &sh);
    if (!data) return 0;
    int64_t x0 = x < 0 ? 0 : x, y0 = y < 0 ? 0 : y;
    int64_t x1 = x + w > sw ? sw : x + w, y1 = y + h > sh ? sh : y + h;
    if (x0 >= x1 || y0 >= y1) return 1;    /* nothing inside the surface: drawn, trivially */
    if ((pixel >> 24) == 0xFF) {
        for (int64_t row = y0; row < y1; row++) {
            uint32_t *p = data + row * stride_px + x0;
            for (int64_t i = 0, n = x1 - x0; i < n; i++) p[i] = pixel;
        }
    } else if (pixel != 0) {
        /* A translucent colour: the same OVER on every pixel. clang
         * vectorises this at -O2 (about 12 instructions a pixel); a version
         * with the source's half of the arithmetic hoisted out of the loop
         * was measured and was no faster. */
        for (int64_t row = y0; row < y1; row++) {
            uint32_t *p = data + row * stride_px + x0;
            for (int64_t i = 0, n = x1 - x0; i < n; i++) p[i] = festina_over_un8x4(pixel, p[i]);
        }
    }
    cairo_surface_mark_dirty_rectangle(s, (int)x0, (int)y0, (int)(x1 - x0), (int)(y1 - y0));
    return 1;
}

/* Stamps the cached coverage for radius r, centred on (cx+tx, cy+ty),
 * blending a solid pixel by coverage. Same fallthrough contract as
 * festina_direct_rect. */
static int festina_direct_circle(cairo_surface_t *s, int64_t cx, int64_t cy, int64_t tx, int64_t ty,
                                 int64_t r, uint32_t pixel) {
    if (!festina_direct_coord_ok(cx) || !festina_direct_coord_ok(cy)) return 0;
    const FestinaCircleCoverage *cov = festina_circle_coverage(r);
    if (!cov) return 0;
    int stride_px, sw, sh;
    uint32_t *data = festina_direct_target(s, &stride_px, &sw, &sh);
    if (!data) return 0;
    int size = cov->size;
    /* size is even, so the mask origin is a whole pixel: exactly the
     * `x - size / 2.0` festina_draw_circle's stamp used. */
    int64_t ox = cx + tx - size / 2, oy = cy + ty - size / 2;
    int64_t x0 = ox < 0 ? 0 : ox, y0 = oy < 0 ? 0 : oy;
    int64_t x1 = ox + size > sw ? sw : ox + size, y1 = oy + size > sh ? sh : oy + size;
    if (x0 >= x1 || y0 >= y1) return 1;
    for (int64_t row = y0; row < y1; row++) {
        uint32_t *p = data + row * stride_px;
        const unsigned char *m = cov->cov + (row - oy) * size;
        for (int64_t col = x0; col < x1; col++) {
            uint32_t a = m[col - ox];
            if (a == 0) continue;
            if (a == 0xFF) { p[col] = festina_over_un8x4(pixel, p[col]); continue; }
            uint32_t in = (festina_mul_un8(pixel >> 24, a) << 24)
                        | (festina_mul_un8((pixel >> 16) & 0xFF, a) << 16)
                        | (festina_mul_un8((pixel >> 8) & 0xFF, a) << 8)
                        | festina_mul_un8(pixel & 0xFF, a);
            p[col] = festina_over_un8x4(in, p[col]);
        }
    }
    cairo_surface_mark_dirty_rectangle(s, (int)x0, (int)y0, (int)(x1 - x0), (int)(y1 - y0));
    return 1;
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
    if (festina_draw_ours()) { festina_surface_zero(g_backing_surface); return; }
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
    {
        FestinaImageBox box; cairo_matrix_t ident;
        festina_canvas_box(&box);
        if (festina_raster_clear(&box, festina_canvas_matrix(&ident), 0, (double)x, (double)y, (double)w, (double)h)) return;
    }
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
    if (r < 0) r = 0;
    {
        FestinaImageBox box; cairo_matrix_t ident;
        festina_canvas_box(&box);
        if (festina_raster_clear(&box, festina_canvas_matrix(&ident), 1, (double)x, (double)y, (double)r, 0)) return;
    }
    cairo_t *cr = festina_canvas_context();
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
    {
        FestinaImageBox box; cairo_matrix_t ident;
        festina_canvas_box(&box);
        if (festina_raster_clear(&box, festina_canvas_matrix(&ident), 2, (double)x, (double)y, 0, 0)) return;
    }
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

/* claude.md #240: every filled rectangle/pixel/circle below tries the
 * solid-fill fast path first (see its own section above) and falls
 * through to the Cairo path it always had when the contract does not
 * hold. The two helpers here are just the canvas's own "is the style
 * state solid and is the transform an integer offset" check, spelled
 * once. */
static int festina_canvas_direct_ok(int64_t *tx, int64_t *ty) {
    return festina_direct_fill_enabled() && festina_solid_style_ok()
        && festina_canvas_integer_offset(tx, ty);
}

static int festina_canvas_direct_override_ok(int64_t color, int border_effective,
                                             int64_t *tx, int64_t *ty) {
    return festina_direct_fill_enabled() && festina_solid_override_ok(color, border_effective)
        && festina_canvas_integer_offset(tx, ty);
}

void festina_draw_rect(int64_t x, int64_t y, int64_t w, int64_t h) {
    festina_backing_require();
    int64_t tx, ty;
    if (festina_canvas_direct_ok(&tx, &ty) &&
        festina_direct_rect(g_backing_surface, x, y, tx, ty, w, h,
                            festina_solid_pixel_from_style())) return;
    if (festina_canvas_raster_shape(0, (double)x, (double)y, (double)w, (double)h, 0, 0, 0, 0)) return;
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
    int64_t tx, ty;
    if (festina_canvas_direct_override_ok(color, g_border_set && g_line_width > 0.0, &tx, &ty) &&
        festina_direct_rect(g_backing_surface, x, y, tx, ty, w, h,
                            festina_solid_pixel_from_color(color))) return;
    if (festina_canvas_raster_shape(0, (double)x, (double)y, (double)w, (double)h, 1, color, 0, 0)) return;
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
    int64_t tx, ty;
    if (festina_canvas_direct_override_ok(fill_color, border_color >= 0 && g_line_width > 0.0, &tx, &ty) &&
        festina_direct_rect(g_backing_surface, x, y, tx, ty, w, h,
                            festina_solid_pixel_from_color(fill_color))) return;
    if (festina_canvas_raster_shape(0, (double)x, (double)y, (double)w, (double)h, 1, fill_color, 1, border_color)) return;
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
    int64_t tx, ty;
    /* claude.md #240: a pixel never strokes, so only the fill half of
     * the solid-fill contract applies (not festina_solid_style_ok's
     * border check). */
    if (festina_direct_fill_enabled() && !g_fill_none && !g_fill_gradient &&
        festina_canvas_integer_offset(&tx, &ty) &&
        festina_direct_rect(g_backing_surface, x, y, tx, ty, 1, 1,
                            festina_solid_pixel_from_style())) return;
    {
        FestinaImageBox box; cairo_matrix_t ident;
        festina_canvas_box(&box);
        if (festina_raster_pixel(&box, festina_canvas_matrix(&ident), x, y, 0, 0)) return;
    }
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
    int64_t tx, ty;
    if (festina_canvas_direct_override_ok(color, 0, &tx, &ty) &&
        festina_direct_rect(g_backing_surface, x, y, tx, ty, 1, 1,
                            festina_solid_pixel_from_color(color))) return;
    {
        FestinaImageBox box; cairo_matrix_t ident;
        festina_canvas_box(&box);
        if (festina_raster_pixel(&box, festina_canvas_matrix(&ident), x, y, 1, color)) return;
    }
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
    int64_t tx, ty;
    /* claude.md #240: the opaque flat-colour case skips Cairo entirely;
     * a gradient still gets #104's mask stamp just below, and everything
     * else the tessellating fallback. (Phase 7: a translucent flat colour
     * takes the direct path too -- see festina_solid_pixel_premul.) */
    if (festina_canvas_direct_ok(&tx, &ty) &&
        festina_direct_circle(g_backing_surface, x, y, tx, ty, r,
                              festina_solid_pixel_from_style())) return;
    if (festina_canvas_raster_shape(1, (double)x, (double)y, (double)(r < 0 ? 0 : r), 0, 0, 0, 0, 0)) return;
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
 * per-call override as drawRect's own color/colors forms. claude.md
 * #240: the opaque case takes the same direct stamp as plain
 * festina_draw_circle now (this used to say a colour override was
 * "not the hot path" -- the layered-canvas benchmark's threads made
 * it exactly that), so the two forms produce identical pixels; the
 * Cairo fallback is the tessellating one it always was. */
void festina_draw_circle_color(int64_t x, int64_t y, int64_t r, int64_t color) {
    festina_backing_require();
    int64_t tx, ty;
    if (festina_canvas_direct_override_ok(color, g_border_set && g_line_width > 0.0, &tx, &ty) &&
        festina_direct_circle(g_backing_surface, x, y, tx, ty, r,
                              festina_solid_pixel_from_color(color))) return;
    if (festina_canvas_raster_shape(1, (double)x, (double)y, (double)(r < 0 ? 0 : r), 0, 1, color, 0, 0)) return;
    cairo_t *cr = festina_canvas_context();
    cairo_arc(cr, (double)x, (double)y, (double)(r < 0 ? 0 : r), 0.0, 2.0 * 3.14159265358979323846);
    festina_fill_and_border_with_color(cr, color);
    cairo_destroy(cr);
}

void festina_draw_circle_colors(int64_t x, int64_t y, int64_t r,
                                 int64_t fill_color, int64_t border_color) {
    festina_backing_require();
    int64_t tx, ty;
    if (festina_canvas_direct_override_ok(fill_color, border_color >= 0 && g_line_width > 0.0, &tx, &ty) &&
        festina_direct_circle(g_backing_surface, x, y, tx, ty, r,
                              festina_solid_pixel_from_color(fill_color))) return;
    if (festina_canvas_raster_shape(1, (double)x, (double)y, (double)(r < 0 ? 0 : r), 0, 1, fill_color, 1, border_color)) return;
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
    if (festina_text_draw(cr, g_transform_ready ? &g_transform : NULL, text, x, y)) {
        cairo_destroy(cr);
        return;
    }
    festina_apply_font(cr);
    cairo_move_to(cr, (double)x, (double)y);
    cairo_show_text(cr, text);
    cairo_destroy(cr);
}


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

    surface = festina_surface_create(CAIRO_FORMAT_RGB24,
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
    cairo_surface_t *placeholder = festina_surface_create(CAIRO_FORMAT_ARGB32, 1, 1);
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

/* ---- runtime.md phase 7, slice 5: images as sources ---------------------
 *
 * Cairo did three things with an image as a source: copy a piece of it
 * (clip, a canvas snapshot, a self-draw's copy-first), put it on a
 * surface at a whole-pixel offset, and resample it under a matrix --
 * drawImage's scaled and region forms, resize, and any image drawn under
 * a rotated, scaled or fractionally moved transform. All three are done
 * here now, on the surfaces' own bytes, with pixman's arithmetic:
 *
 *   * a copy is a row copy (CAIRO_OPERATOR_SOURCE with an ARGB32 source
 *     was one too); a source with no alpha channel gets alpha 255;
 *   * a whole-pixel translation is a blit: nothing is interpolated, and
 *     the pixel is the source's, OVER the destination under fillAlpha;
 *   * everything else is looked up through the inverse matrix. What
 *     Cairo's CAIRO_FILTER_GOOD does -- measured against libcairo 1.18,
 *     not read from its source -- is, per axis of the SOURCE: a scale
 *     above 0.75 is bilinear, interpolating the four nearest texels with
 *     pixman's 7-bit weights; a scale of 0.75 or less is a box (the
 *     average of the source area the pixel covers). Outside the image is
 *     transparent, so the edge pixels fade over the half pixel beyond it.
 *     The one exception found: a matrix that is only a scale whose two
 *     factors multiply to 1 (0.5 by 2, say) is sampled nearest.
 *
 * The positions are computed once per pixel from the inverse matrix in
 * doubles, where pixman starts from the matrix rounded to 16.16 and
 * a translation that depends on the paint's extents; the two agree to
 * within a hundred-and-twenty-eighth of a pixel, which moves an
 * interpolated channel by at most two levels (tests/test_image_sources.py
 * measures it). Whole-pixel and exact-power-of-two cases agree exactly. */

static atomic_int g_image_ours = -1;

/* FESTINA_CAIRO_DRAW=1 keeps the Cairo code of every image source as it
 * was -- the oracle the tests compare with until Cairo goes. */
static int festina_image_ours(void) {
    int v = atomic_load_explicit(&g_image_ours, memory_order_relaxed);
    if (v < 0) {
        const char *e = getenv("FESTINA_CAIRO_DRAW");
        v = (e && *e && *e != '0') ? 0 : 1;
        atomic_store_explicit(&g_image_ours, v, memory_order_relaxed);
    }
    return v;
}

static int festina_matrix_invert_to(cairo_matrix_t *out, const cairo_matrix_t *m) {
    double det = m->xx * m->yy - m->yx * m->xy;
    if (!(fabs(det) > 1e-300) || !isfinite(det)) return 0;
    out->xx = m->yy / det;
    out->yx = -m->yx / det;
    out->xy = -m->xy / det;
    out->yy = m->xx / det;
    out->x0 = (m->xy * m->y0 - m->yy * m->x0) / det;
    out->y0 = (m->yx * m->x0 - m->xx * m->y0) / det;
    return 1;
}

typedef struct {
    const uint32_t *px;
    int w, h;
    size_t stride;       /* in 32-bit words */
    uint32_t force_alpha;/* 0xFF000000 for RGB24, else 0 */
} FestinaSrcView;

static int festina_src_view(FestinaSrcView *v, cairo_surface_t *s) {
    if (!s || cairo_surface_get_type(s) != CAIRO_SURFACE_TYPE_IMAGE) return 0;
    cairo_format_t f = cairo_image_surface_get_format(s);
    if (f != CAIRO_FORMAT_ARGB32 && f != CAIRO_FORMAT_RGB24) return 0;
    cairo_surface_flush(s);
    unsigned char *d = cairo_image_surface_get_data(s);
    if (!d) return 0;
    v->px = (const uint32_t *)d;
    v->w = cairo_image_surface_get_width(s);
    v->h = cairo_image_surface_get_height(s);
    v->stride = (size_t)cairo_image_surface_get_stride(s) / 4;
    v->force_alpha = f == CAIRO_FORMAT_RGB24 ? 0xFF000000u : 0u;
    return v->w > 0 && v->h > 0;
}

static inline uint32_t festina_src_px(const FestinaSrcView *v, int x, int y) {
    if ((unsigned)x >= (unsigned)v->w || (unsigned)y >= (unsigned)v->h) return 0;
    return v->px[(size_t)y * v->stride + (size_t)x] | v->force_alpha;
}

/* pixman's bilinear interpolation of four premultiplied texels with 7-bit
 * weights: each channel the weighted sum, shifted down by 14. Two channels
 * to a 32-bit lane of a 64-bit word, so four products add up in two
 * multiplies a texel. */
static inline uint64_t festina_spread(uint32_t x) {
    uint64_t a = x & 0x00FF00FFu;
    return (a | (a << 16)) & 0x000000FF000000FFull;
}

static inline uint32_t festina_bilinear(uint32_t tl, uint32_t tr, uint32_t bl, uint32_t br,
                                        int dx, int dy) {
    uint64_t w0 = (uint64_t)((128 - dx) * (128 - dy));
    uint64_t w1 = (uint64_t)(dx * (128 - dy));
    uint64_t w2 = (uint64_t)((128 - dx) * dy);
    uint64_t w3 = (uint64_t)(dx * dy);
    uint64_t rb = festina_spread(tl) * w0 + festina_spread(tr) * w1
                + festina_spread(bl) * w2 + festina_spread(br) * w3;
    uint64_t ag = festina_spread(tl >> 8) * w0 + festina_spread(tr >> 8) * w1
                + festina_spread(bl >> 8) * w2 + festina_spread(br >> 8) * w3;
    uint32_t b = (uint32_t)((rb >> 14) & 0xFF), r = (uint32_t)((rb >> 46) & 0xFF);
    uint32_t g = (uint32_t)((ag >> 14) & 0xFF), a = (uint32_t)((ag >> 46) & 0xFF);
    return b | (g << 8) | (r << 16) | (a << 24);
}

/* A pixel times a byte, pixman's UN8x4_MUL_UN8: every channel
 * festina_mul_un8(channel, m). */
static inline uint32_t festina_scale_un8x4(uint32_t s, uint32_t m) {
    uint32_t rb = (s & 0x00FF00FFu) * m + 0x00800080u;
    rb = ((rb + ((rb >> 8) & 0x00FF00FFu)) >> 8) & 0x00FF00FFu;
    uint32_t ag = ((s >> 8) & 0x00FF00FFu) * m + 0x00800080u;
    ag = ((ag + ((ag >> 8) & 0x00FF00FFu)) >> 8) & 0x00FF00FFu;
    return rb | (ag << 8);
}

/* festina_over_un8x4 with no early returns, so a row of them vectorises:
 * the same arithmetic -- an opaque source multiplies the destination by
 * zero, a transparent one by 255 -- and so the same result. */
static inline uint32_t festina_over_flat(uint32_t s, uint32_t d) {
    uint32_t ia = 0xFFu - (s >> 24);
    uint32_t rb = (d & 0x00FF00FFu) * ia + 0x00800080u;
    rb = ((rb + ((rb >> 8) & 0x00FF00FFu)) >> 8) & 0x00FF00FFu;
    uint32_t ag = ((d >> 8) & 0x00FF00FFu) * ia + 0x00800080u;
    ag = ((ag + ((ag >> 8) & 0x00FF00FFu)) >> 8) & 0x00FF00FFu;
    uint32_t t = rb + (s & 0x00FF00FFu);
    t |= 0x10000100u - ((t >> 8) & 0x00FF00FFu);
    rb = t & 0x00FF00FFu;
    t = ag + ((s >> 8) & 0x00FF00FFu);
    t |= 0x10000100u - ((t >> 8) & 0x00FF00FFu);
    ag = t & 0x00FF00FFu;
    return rb | (ag << 8);
}

/* A source pixel under fillAlpha's mask byte (255 = none), OVER the
 * destination pixel -- festina_image_blend_row's arithmetic. */
static inline void festina_put_over(uint32_t *dp, uint32_t s, uint32_t m, int opaque_dest) {
    if (m != 255u) s = festina_scale_un8x4(s, m);
    if (s == 0) return;
    uint32_t d = *dp;
    if (opaque_dest) d |= 0xFF000000u;
    d = festina_over_un8x4(s, d);
    *dp = opaque_dest ? (d | 0xFF000000u) : d;
}

/* The overlap of [a, b] with [c, c + 1]. */
static inline double festina_overlap(double a, double b, int64_t c) {
    double lo = a > (double)c ? a : (double)c;
    double hi = b < (double)c + 1.0 ? b : (double)c + 1.0;
    return hi > lo ? hi - lo : 0.0;
}

/* One axis of an axis-aligned resample: for each destination index, the
 * run of source indices that make it and the weights of each, summing to
 * 65536 (less where the run leaves the image, which is transparent). */
typedef struct {
    int64_t *first;     /* per destination index: first source index kept */
    int32_t *count;     /* ... and how many; 0 = nothing of the image */
    uint32_t *w;        /* count[k] weights at w[k * maxn] */
    int maxn;
} FestinaTaps;

typedef enum { FESTINA_TAPS_BILINEAR, FESTINA_TAPS_BOX, FESTINA_TAPS_NEAREST } FestinaTapKind;

static void festina_taps_free(FestinaTaps *t) {
    free(t->first); free(t->count); free(t->w);
    memset(t, 0, sizeof(*t));
}

/* Destination indices [d0, d1) of an axis that maps to source position
 * u = scale * (d + 0.5) + shift (scale and shift are the inverse
 * matrix's), against a source of `size` pixels. */
static int festina_taps_build(FestinaTaps *t, FestinaTapKind kind, int64_t d0, int64_t d1,
                              double scale, double shift, double window, int64_t size) {
    int64_t n = d1 - d0;
    memset(t, 0, sizeof(*t));
    int maxn = 2;
    if (kind == FESTINA_TAPS_NEAREST) maxn = 1;
    if (kind == FESTINA_TAPS_BOX) {
        double w = window < 0 ? -window : window;
        if (w > 4096.0) w = 4096.0;
        maxn = (int)ceil(w) + 2;
    }
    t->maxn = maxn;
    t->first = calloc((size_t)n, sizeof(int64_t));
    t->count = calloc((size_t)n, sizeof(int32_t));
    t->w = calloc((size_t)n * (size_t)maxn, sizeof(uint32_t));
    if (!t->first || !t->count || !t->w) { festina_taps_free(t); return 0; }
    for (int64_t k = 0; k < n; k++) {
        double u = scale * ((double)(d0 + k) + 0.5) + shift;
        uint32_t *w = t->w + (size_t)k * (size_t)maxn;
        int64_t i0;
        int cnt;
        uint32_t tmp[2];
        if (kind == FESTINA_TAPS_NEAREST) {
            int64_t fu = (int64_t)floor(u * 65536.0 + 0.5);
            i0 = (fu - 1) >> 16;
            cnt = 1;
            tmp[0] = 65536u;
        } else if (kind == FESTINA_TAPS_BILINEAR) {
            int64_t fu = (int64_t)floor(u * 65536.0 - 32768.0);
            i0 = fu >> 16;
            int f = (int)((fu >> 9) & 127);
            cnt = 2;
            tmp[0] = (uint32_t)(128 - f) << 9;
            tmp[1] = (uint32_t)f << 9;
        } else {
            double half = 0.5 * (window < 0 ? -window : window);
            if (half > 2048.0) half = 2048.0;
            double lo = u - half, hi = u + half;
            i0 = (int64_t)floor(lo);
            cnt = (int)ceil(hi) - (int)i0;
            if (cnt > maxn) cnt = maxn;
            double full = hi - lo;
            double cum = 0.0;
            uint32_t prev = 0;
            for (int j = 0; j < cnt; j++) {
                cum += festina_overlap(lo, hi, i0 + j);
                uint32_t at = (uint32_t)floor(cum / full * 65536.0 + 0.5);
                w[j] = at - prev;
                prev = at;
            }
        }
        /* Keep only the part inside the image. */
        int64_t a = 0, b = cnt;
        while (a < b && (i0 + a < 0 || i0 + a >= size)) a++;
        while (b > a && (i0 + b - 1 < 0 || i0 + b - 1 >= size)) b--;
        if (kind == FESTINA_TAPS_BOX) {
            if (a > 0) memmove(w, w + a, (size_t)(b - a) * sizeof(uint32_t));
        } else {
            for (int64_t j = a; j < b; j++) w[j - a] = tmp[j];
        }
        t->first[k] = i0 + a;
        t->count[k] = (int32_t)(b - a);
    }
    return 1;
}

/* n source pixels OVER n destination pixels, under mask byte m. The two
 * rows never overlap (a self-draw is copied first), which `restrict` says,
 * and the loop has no branches: the compiler turns it into vector code. */
static void festina_over_run(uint32_t *restrict dp, const uint32_t *restrict sp, int64_t n,
                             uint32_t fa, uint32_t dmask, uint32_t m) {
    if (m == 255u) {
        for (int64_t i = 0; i < n; i++)
            dp[i] = festina_over_flat(sp[i] | fa, dp[i] | dmask) | dmask;
    } else {
        for (int64_t i = 0; i < n; i++)
            dp[i] = festina_over_flat(festina_scale_un8x4(sp[i] | fa, m), dp[i] | dmask) | dmask;
    }
}

/* A row of finished source pixels OVER the destination, each at the mask
 * byte m, scaled by that pixel's clip coverage when there is a clip. */
static void festina_over_line(uint32_t *restrict dp, const uint32_t *restrict sp, int64_t n,
                              uint32_t fa, uint32_t dmask, uint32_t m, const uint8_t *cov) {
    if (!cov) { festina_over_run(dp, sp, n, fa, dmask, m); return; }
    for (int64_t i = 0; i < n; i++) {
        uint32_t mm = cov[i] == 255u ? m : festina_mul_un8(m, cov[i]);
        if (mm) festina_put_over(&dp[i], sp[i] | fa, mm, dmask != 0);
    }
}

/* A whole-pixel translation: the pixel is the source's. */
static void festina_blit_rows(uint32_t *dd, size_t dstride_words, const FestinaSrcView *sv,
                              int64_t ox, int64_t oy, int64_t x0, int64_t y0, int64_t x1, int64_t y1,
                              uint32_t m, int opaque_dest, const uint8_t *mask, int64_t mx0, int64_t my0, int64_t mw) {
    int64_t xa = x0 > ox ? x0 : ox;
    int64_t xb = x1 < ox + sv->w ? x1 : ox + sv->w;
    if (xa >= xb) return;
    uint32_t fa = sv->force_alpha;
    for (int64_t y = y0; y < y1; y++) {
        int64_t sy = y - oy;
        if (sy < 0 || sy >= sv->h) continue;
        uint32_t *dp = dd + (size_t)y * dstride_words;
        const uint32_t *sp = sv->px + (size_t)sy * sv->stride - ox;
        festina_over_line(dp + xa, sp + xa, xb - xa, fa, opaque_dest ? 0xFF000000u : 0u, m,
                          mask ? mask + (size_t)(y - my0) * (size_t)mw + (size_t)(xa - mx0) : NULL);
    }
}

/* Axis-aligned, and bilinear in both directions (every scale above 0.75,
 * any flip, any whole or fractional move): pixman's own arithmetic, bit
 * for bit. The sum  tl*(128-dx)*(128-dy) + tr*dx*(128-dy) + bl*(128-dx)*dy
 * + br*dx*dy  is  (128-dx)*V[i] + dx*V[i+1]  with V[i] = top*(128-dy) +
 * bottom*dy, an integer identity -- so each destination row blends its two
 * source rows once into V (two channels to a lane pair, unrounded), and
 * each pixel is then two multiplies a lane pair. V is padded by a
 * transparent column each side, so the interior has no bounds to test. */
static void festina_resample_bilinear(uint32_t *dd, size_t dstride_words, const FestinaSrcView *sv,
                                      const cairo_matrix_t *inv, int64_t x0, int64_t y0,
                                      int64_t x1, int64_t y1, uint32_t m, int opaque_dest,
                                      const uint8_t *mask, int64_t mx0, int64_t my0, int64_t mw) {
    int64_t n = x1 - x0;
    int64_t *ixs = malloc((size_t)n * sizeof(int64_t));
    uint8_t *dxs = malloc((size_t)n);
    size_t vw = (size_t)sv->w + 2;
    uint64_t *vrb = malloc(2 * vw * sizeof(uint64_t));
    uint32_t *r0 = malloc(2 * vw * sizeof(uint32_t));
    uint32_t *line = malloc((size_t)n * sizeof(uint32_t));
    if (!ixs || !dxs || !vrb || !r0 || !line) { free(ixs); free(dxs); free(vrb); free(r0); free(line); return; }
    uint64_t *vag = vrb + vw;
    uint32_t *r1 = r0 + vw;
    for (int64_t k = 0; k < n; k++) {
        double u = inv->xx * ((double)(x0 + k) + 0.5) + inv->x0;
        int64_t fu = (int64_t)floor(u * 65536.0 - 32768.0);
        ixs[k] = fu >> 16;
        dxs[k] = (uint8_t)((fu >> 9) & 127);
    }
    int64_t have_iy = INT64_MIN;
    int have_dy = -1;
    for (int64_t y = y0; y < y1; y++) {
        double v = inv->yy * ((double)y + 0.5) + inv->y0;
        int64_t fv = (int64_t)floor(v * 65536.0 - 32768.0);
        int64_t iy = fv >> 16;
        int dy = (int)((fv >> 9) & 127);
        if (iy < -1 || iy >= sv->h) continue;
        if (iy != have_iy || dy != have_dy) {
            for (int pass = 0; pass < 2; pass++) {
                int64_t want = iy + pass;
                uint32_t *buf = pass ? r1 : r0;
                buf[0] = 0; buf[vw - 1] = 0;
                if (want < 0 || want >= sv->h) memset(buf + 1, 0, (size_t)sv->w * sizeof(uint32_t));
                else {
                    const uint32_t *sp = sv->px + (size_t)want * sv->stride;
                    if (!sv->force_alpha) memcpy(buf + 1, sp, (size_t)sv->w * sizeof(uint32_t));
                    else for (int i = 0; i < sv->w; i++) buf[1 + i] = sp[i] | sv->force_alpha;
                }
            }
            uint64_t wa = (uint64_t)(128 - dy), wb = (uint64_t)dy;
            for (size_t i = 0; i < vw; i++) {
                vrb[i] = festina_spread(r0[i]) * wa + festina_spread(r1[i]) * wb;
                vag[i] = festina_spread(r0[i] >> 8) * wa + festina_spread(r1[i] >> 8) * wb;
            }
            have_iy = iy;
            have_dy = dy;
        }
        for (int64_t k = 0; k < n; k++) {
            int64_t ix = ixs[k];
            if (ix < -1 || ix >= sv->w) { line[k] = 0; continue; }
            uint64_t wl = (uint64_t)(128 - dxs[k]), wr = (uint64_t)dxs[k];
            uint64_t rb = vrb[ix + 1] * wl + vrb[ix + 2] * wr;
            uint64_t ag = vag[ix + 1] * wl + vag[ix + 2] * wr;
            line[k] = (uint32_t)((rb >> 14) & 0xFF) | (uint32_t)(((ag >> 14) & 0xFF) << 8)
                    | (uint32_t)(((rb >> 46) & 0xFF) << 16) | (uint32_t)(((ag >> 46) & 0xFF) << 24);
        }
        festina_over_line(dd + (size_t)y * dstride_words + x0, line, n, 0u,
                          opaque_dest ? 0xFF000000u : 0u, m,
                          mask ? mask + (size_t)(y - my0) * (size_t)mw + (size_t)(x0 - mx0) : NULL);
    }
    free(ixs); free(dxs); free(vrb); free(r0); free(line);
}

/* Axis-aligned, with a box or nearest axis in it: each axis reduced by its
 * own weights, rows first. Weights are 16-bit fractions that sum to
 * 65536. Two channels share a 64-bit word, a lane of 32 bits each: a row
 * reduced horizontally is kept to 8 fractional bits (at most 65280), and
 * the vertical sum of those under weights summing to 65536 stays under
 * 2^32 -- no lane spills into its neighbour -- and rounds once at the end. */
typedef struct { uint64_t rb, ag; } FestinaHRow;

static void festina_resample_separable(uint32_t *dd, size_t dstride_words, const FestinaSrcView *sv,
                                       const FestinaTaps *tx, const FestinaTaps *ty,
                                       int64_t x0, int64_t y0, int64_t x1, int64_t y1,
                                       uint32_t m, int opaque_dest,
                                       const uint8_t *mask, int64_t mx0, int64_t my0, int64_t mw) {
    int64_t n = x1 - x0;
    int slots = ty->maxn + 1;
    FestinaHRow *rows = malloc((size_t)slots * (size_t)n * sizeof(FestinaHRow));
    int64_t *tag = malloc((size_t)slots * sizeof(int64_t));
    const FestinaHRow **hr = malloc((size_t)slots * sizeof(*hr));
    uint32_t *line = malloc((size_t)n * sizeof(uint32_t));
    if (!rows || !tag || !hr || !line) { free(rows); free(tag); free(hr); free(line); return; }
    for (int i = 0; i < slots; i++) tag[i] = INT64_MIN;
    const uint32_t fa = sv->force_alpha;
    for (int64_t y = y0; y < y1; y++) {
        int64_t k = y - y0;
        int cy = ty->count[k];
        if (cy <= 0) continue;
        int64_t sy0 = ty->first[k];
        const uint32_t *wy = ty->w + (size_t)k * (size_t)ty->maxn;
        /* The horizontally reduced rows this destination row needs. */
        for (int j = 0; j < cy; j++) {
            int64_t sr = sy0 + j;
            int slot = (int)(((sr % slots) + slots) % slots);
            if (tag[slot] == sr) continue;
            tag[slot] = sr;
            const uint32_t *sp = sv->px + (size_t)sr * sv->stride;
            FestinaHRow *out = rows + (size_t)slot * (size_t)n;
            for (int64_t c = 0; c < n; c++) {
                int cx = tx->count[c];
                uint64_t rb = 0, ag = 0;
                const uint32_t *wx = tx->w + (size_t)c * (size_t)tx->maxn;
                const uint32_t *p = sp + tx->first[c];
                for (int i = 0; i < cx; i++) {
                    uint32_t px = p[i] | fa;
                    uint64_t w = wx[i];
                    rb += festina_spread(px) * w;
                    ag += festina_spread(px >> 8) * w;
                }
                out[c].rb = ((rb + 0x0000008000000080ull) >> 8) & 0x0000FFFF0000FFFFull;
                out[c].ag = ((ag + 0x0000008000000080ull) >> 8) & 0x0000FFFF0000FFFFull;
            }
        }
        for (int j = 0; j < cy; j++) {
            int slot = (int)((((sy0 + j) % slots) + slots) % slots);
            hr[j] = rows + (size_t)slot * (size_t)n;
        }
        for (int64_t c = 0; c < n; c++) {
            if (tx->count[c] <= 0) { line[c] = 0; continue; }
            uint64_t rb = 0x0000000000800000ull + 0x0080000000000000ull, ag = rb;
            for (int j = 0; j < cy; j++) {
                uint64_t w = wy[j];
                rb += hr[j][c].rb * w;
                ag += hr[j][c].ag * w;
            }
            line[c] = (uint32_t)((rb >> 24) & 0xFF) | (uint32_t)(((ag >> 24) & 0xFF) << 8)
                    | (uint32_t)(((rb >> 56) & 0xFF) << 16) | (uint32_t)(((ag >> 56) & 0xFF) << 24);
        }
        festina_over_line(dd + (size_t)y * dstride_words + x0, line, n, 0u,
                          opaque_dest ? 0xFF000000u : 0u, m,
                          mask ? mask + (size_t)(y - my0) * (size_t)mw + (size_t)(x0 - mx0) : NULL);
    }
    free(rows); free(tag); free(hr); free(line);
}

/* Anything else -- a rotation or a skew: a point at a time through the
 * inverse matrix, bilinear (pixman's arithmetic) or, where a source axis
 * shrinks to 0.75 or less, a box of that axis' size around the point. */
static void festina_resample_general(uint32_t *dd, size_t dstride_words, const FestinaSrcView *sv,
                                     const cairo_matrix_t *inv, double fsx, double fsy,
                                     int64_t x0, int64_t y0, int64_t x1, int64_t y1,
                                     uint32_t m, int opaque_dest,
                                     const uint8_t *mask, int64_t mx0, int64_t my0, int64_t mw) {
    int box_x = fsx <= 0.75, box_y = fsy <= 0.75;
    double wu = box_x ? 1.0 / fsx : 0.0;   /* the box's width in source pixels */
    double wv = box_y ? 1.0 / fsy : 0.0;
    for (int64_t y = y0; y < y1; y++) {
        uint32_t *dp = dd + (size_t)y * dstride_words;
        double py = (double)y + 0.5;
        double u = inv->xx * ((double)x0 + 0.5) + inv->xy * py + inv->x0;
        double v = inv->yx * ((double)x0 + 0.5) + inv->yy * py + inv->y0;
        for (int64_t x = x0; x < x1; x++, u += inv->xx, v += inv->yx) {
            uint32_t s;
            if (!box_x && !box_y) {
                int64_t fu = (int64_t)floor(u * 65536.0 - 32768.0);
                int64_t fv = (int64_t)floor(v * 65536.0 - 32768.0);
                int64_t ix = fu >> 16, iy = fv >> 16;
                if (ix < -1 || ix >= sv->w || iy < -1 || iy >= sv->h) continue;
                int dx = (int)((fu >> 9) & 127), dy = (int)((fv >> 9) & 127);
                if (ix >= 0 && iy >= 0 && ix + 1 < sv->w && iy + 1 < sv->h) {
                    const uint32_t *r0 = sv->px + (size_t)iy * sv->stride + (size_t)ix;
                    const uint32_t *r1 = r0 + sv->stride;
                    uint32_t fa = sv->force_alpha;
                    s = festina_bilinear(r0[0] | fa, r0[1] | fa, r1[0] | fa, r1[1] | fa, dx, dy);
                } else {
                    s = festina_bilinear(festina_src_px(sv, (int)ix, (int)iy), festina_src_px(sv, (int)ix + 1, (int)iy),
                                         festina_src_px(sv, (int)ix, (int)iy + 1), festina_src_px(sv, (int)ix + 1, (int)iy + 1),
                                         dx, dy);
                }
            } else {
                /* Weights per axis in doubles: a box on the axes that
                 * shrink, bilinear on the others. */
                double alo = 0, ahi = 0, blo = 0, bhi = 0;
                int64_t ia, ib;
                int na, nb, bil_u = 0, bil_v = 0;
                if (box_x) { alo = u - 0.5 * wu; ahi = u + 0.5 * wu; ia = (int64_t)floor(alo); na = (int)ceil(ahi) - (int)ia; }
                else { int64_t fu = (int64_t)floor(u * 65536.0 - 32768.0); ia = fu >> 16; bil_u = (int)((fu >> 9) & 127); na = 2; }
                if (box_y) { blo = v - 0.5 * wv; bhi = v + 0.5 * wv; ib = (int64_t)floor(blo); nb = (int)ceil(bhi) - (int)ib; }
                else { int64_t fv = (int64_t)floor(v * 65536.0 - 32768.0); ib = fv >> 16; bil_v = (int)((fv >> 9) & 127); nb = 2; }
                if (ia + na <= 0 || ia >= sv->w || ib + nb <= 0 || ib >= sv->h) continue;
                double acc[4] = {0, 0, 0, 0};
                for (int j = 0; j < nb; j++) {
                    double wy = box_y ? festina_overlap(blo, bhi, ib + j) / wv : (j ? bil_v : 128 - bil_v) / 128.0;
                    if (wy == 0.0) continue;
                    for (int i = 0; i < na; i++) {
                        double wx = box_x ? festina_overlap(alo, ahi, ia + i) / wu : (i ? bil_u : 128 - bil_u) / 128.0;
                        if (wx == 0.0) continue;
                        uint32_t p = festina_src_px(sv, (int)(ia + i), (int)(ib + j));
                        if (!p) continue;
                        double w = wx * wy;
                        acc[0] += (double)(p & 0xFF) * w;
                        acc[1] += (double)((p >> 8) & 0xFF) * w;
                        acc[2] += (double)((p >> 16) & 0xFF) * w;
                        acc[3] += (double)(p >> 24) * w;
                    }
                }
                uint32_t c[4];
                for (int k = 0; k < 4; k++) { double r = floor(acc[k] + 0.5); c[k] = r > 255.0 ? 255u : (uint32_t)r; }
                s = c[0] | (c[1] << 8) | (c[2] << 16) | (c[3] << 24);
            }
            if (!s) continue;
            uint32_t mm = m;
            if (mask) { uint8_t c = mask[(size_t)(y - my0) * (size_t)mw + (size_t)(x - mx0)]; if (c != 255u) mm = festina_mul_un8(m, c); }
            if (mm) festina_put_over(&dp[x], s, mm, opaque_dest);
        }
    }
}

/* ---- a clip that is a parallelogram on the device -----------------------
 *
 * drawImage's region form clips the draw to its destination rectangle. Under
 * a transform that leaves that rectangle's edges between pixels the clip
 * is a pixel rectangle; under any other (a turn, a fractional move) it is a
 * parallelogram whose edge pixels are partly covered. The coverage of a
 * pixel is the exact area the parallelogram shares with it (a convex
 * polygon clipped to the pixel's square), as a byte -- Cairo's clip is a
 * mask of the same kind, from its scan converter, so the two agree to a
 * level or two on the edge pixels and exactly inside and out. */
typedef struct { double x, y; } FestinaPt;

/* Clip the polygon `in` (n points) to one half plane of the pixel square:
 * axis 0 keeps x >= bound (keep_max = 0) or x <= bound (1); axis 1 y. */
static int festina_clip_side(const FestinaPt *in, int n, FestinaPt *out, int axis, double bound, int keep_max) {
    int m = 0;
    for (int i = 0; i < n; i++) {
        FestinaPt a = in[i], b = in[(i + 1) % n];
        double da = (axis ? a.y : a.x) - bound, db = (axis ? b.y : b.x) - bound;
        if (keep_max) { da = -da; db = -db; }
        int ina = da >= 0.0, inb = db >= 0.0;
        if (ina) out[m++] = a;
        if (ina != inb) {
            double t = da / (da - db);
            FestinaPt c = { a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t };
            out[m++] = c;
        }
    }
    return m;
}

static double festina_quad_coverage(const double *q, int64_t px, int64_t py) {
    FestinaPt a[16], b[16];
    for (int i = 0; i < 4; i++) { a[i].x = q[i * 2]; a[i].y = q[i * 2 + 1]; }
    int n = 4;
    n = festina_clip_side(a, n, b, 0, (double)px, 0);       if (n < 3) return 0.0;
    n = festina_clip_side(b, n, a, 0, (double)px + 1.0, 1);  if (n < 3) return 0.0;
    n = festina_clip_side(a, n, b, 1, (double)py, 0);       if (n < 3) return 0.0;
    n = festina_clip_side(b, n, a, 1, (double)py + 1.0, 1);  if (n < 3) return 0.0;
    double area = 0.0;
    for (int i = 0; i < n; i++) {
        FestinaPt p = a[i], r = a[(i + 1) % n];
        area += p.x * r.y - r.x * p.y;
    }
    area = fabs(area) * 0.5;
    return area > 1.0 ? 1.0 : area;
}

/* Coverage bytes over the device rectangle [x0, x1) x [y0, y1) of the
 * parallelogram q (four corners, in order). NULL if memory runs out. */
static uint8_t *festina_quad_mask(const double *q, int64_t x0, int64_t y0, int64_t x1, int64_t y1) {
    int64_t w = x1 - x0, h = y1 - y0;
    uint8_t *mask = malloc((size_t)w * (size_t)h);
    if (!mask) return NULL;
    double ex[4], ey[4];
    double area2 = 0.0;
    for (int i = 0; i < 4; i++) {
        int j = (i + 1) % 4;
        ex[i] = q[j * 2] - q[i * 2];
        ey[i] = q[j * 2 + 1] - q[i * 2 + 1];
        area2 += q[i * 2] * q[j * 2 + 1] - q[j * 2] * q[i * 2 + 1];
    }
    double sign = area2 >= 0.0 ? 1.0 : -1.0;
    for (int64_t y = y0; y < y1; y++) {
        uint8_t *row = mask + (size_t)(y - y0) * (size_t)w;
        for (int64_t x = x0; x < x1; x++) {
            /* inside = all four corners of the pixel on the left of every edge */
            int inside = 1, outside = 0;
            for (int e = 0; e < 4 && !outside; e++) {
                int corners_in = 0;
                for (int c = 0; c < 4; c++) {
                    double cx = (double)(x + (c & 1)), cy = (double)(y + (c >> 1));
                    double cross = sign * (ex[e] * (cy - q[e * 2 + 1]) - ey[e] * (cx - q[e * 2]));
                    if (cross >= 0.0) corners_in++;
                }
                if (corners_in == 0) outside = 1;
                if (corners_in < 4) inside = 0;
            }
            if (outside) row[x - x0] = 0;
            else if (inside) row[x - x0] = 255;
            else {
                double cov = festina_quad_coverage(q, x, y);
                row[x - x0] = (uint8_t)(cov * 255.0 + 0.5);
            }
        }
    }
    return mask;
}

/* Composite `src` onto `dst` through `fwd` (source pixels -> device
 * pixels) at `alpha`, within the device rectangle [cx0, cx1) x [cy0, cy1).
 * Both must be image surfaces of ARGB32 or RGB24. */
static void festina_composite_image(cairo_surface_t *dst, cairo_surface_t *srcs,
                                    const cairo_matrix_t *fwd, double alpha,
                                    int64_t cx0, int64_t cy0, int64_t cx1, int64_t cy1,
                                    const uint8_t *mask) {
    if (!(alpha > 0.0)) return;
    FestinaSrcView sv;
    if (!festina_src_view(&sv, srcs)) return;
    if (!dst || cairo_surface_get_type(dst) != CAIRO_SURFACE_TYPE_IMAGE) return;
    cairo_format_t df = cairo_image_surface_get_format(dst);
    if (df != CAIRO_FORMAT_ARGB32 && df != CAIRO_FORMAT_RGB24) return;
    int dw = cairo_image_surface_get_width(dst);
    int dh = cairo_image_surface_get_height(dst);
    /* With a clip mask the rectangle is the mask's own, and must lie on
     * the surface: its bytes are laid out for exactly that rectangle. */
    int64_t mx0 = cx0, my0 = cy0, mw = cx1 - cx0;
    if (cx0 < 0) cx0 = 0;
    if (cy0 < 0) cy0 = 0;
    if (cx1 > dw) cx1 = dw;
    if (cy1 > dh) cy1 = dh;
    if (cx0 >= cx1 || cy0 >= cy1) return;
    if (mask && (cx0 != mx0 || cy0 != my0 || cx1 - cx0 != mw)) return;
    uint32_t m = alpha >= 1.0 ? 255u : festina_channel_byte(alpha);
    if (m == 0) return;
    int opaque_dest = df == CAIRO_FORMAT_RGB24;
    cairo_matrix_t inv;
    if (!festina_matrix_invert_to(&inv, fwd)) return;

    /* The pixels the image can reach: its transformed corners, a half
     * source pixel wider (the filters spread that far past the edge). */
    double xs[4] = {0, (double)sv.w, 0, (double)sv.w};
    double ys[4] = {0, 0, (double)sv.h, (double)sv.h};
    double bx0 = 1e300, by0 = 1e300, bx1 = -1e300, by1 = -1e300;
    for (int i = 0; i < 4; i++) {
        double x = fwd->xx * xs[i] + fwd->xy * ys[i] + fwd->x0;
        double y = fwd->yx * xs[i] + fwd->yy * ys[i] + fwd->y0;
        if (x < bx0) bx0 = x;
        if (x > bx1) bx1 = x;
        if (y < by0) by0 = y;
        if (y > by1) by1 = y;
    }
    if (!isfinite(bx0) || !isfinite(by0) || !isfinite(bx1) || !isfinite(by1)) return;
    if (bx1 < (double)cx0 - 2.0 || bx0 > (double)cx1 + 2.0
        || by1 < (double)cy0 - 2.0 || by0 > (double)cy1 + 2.0) return;
    /* In destination pixels that is half the scale, and never under one. */
    double fsx = hypot(fwd->xx, fwd->yx);
    double fsy = hypot(fwd->xy, fwd->yy);
    double grow = ceil(0.5 * (fsx > fsy ? fsx : fsy)) + 1.0;
    if (grow > 1e6) grow = 1e6;
    int64_t x0 = (int64_t)floor(bx0 - grow), x1 = (int64_t)ceil(bx1 + grow);
    int64_t y0 = (int64_t)floor(by0 - grow), y1 = (int64_t)ceil(by1 + grow);
    if (x0 < cx0) x0 = cx0;
    if (y0 < cy0) y0 = cy0;
    if (x1 > cx1) x1 = cx1;
    if (y1 > cy1) y1 = cy1;
    if (x0 >= x1 || y0 >= y1) return;

    cairo_surface_flush(dst);
    unsigned char *dbytes = cairo_image_surface_get_data(dst);
    if (!dbytes) return;
    uint32_t *dd = (uint32_t *)dbytes;
    size_t dstride_words = (size_t)cairo_image_surface_get_stride(dst) / 4;

    if (fwd->xx == 1.0 && fwd->yy == 1.0 && fwd->xy == 0.0 && fwd->yx == 0.0
        && fwd->x0 == floor(fwd->x0) && fwd->y0 == floor(fwd->y0)
        && fabs(fwd->x0) < 1e9 && fabs(fwd->y0) < 1e9) {
        festina_blit_rows(dd, dstride_words, &sv, (int64_t)fwd->x0, (int64_t)fwd->y0,
                          x0, y0, x1, y1, m, opaque_dest, mask, mx0, my0, mw);
    } else if (fwd->xy == 0.0 && fwd->yx == 0.0) {
        /* Per axis of the source: its size under the matrix picks the
         * filter. A scale-only matrix whose two factors multiply to 1
         * (0.5 by 2, say) -- to within 1/512 -- is sampled nearest, which is what
         * Cairo 1.18 does. */
        int box_x = fsx <= 0.75, box_y = fsy <= 0.75;
        int nearest = (box_x || box_y) && fabs(fsx * fsy - 1.0) < 1.0 / 512.0;
        if (!box_x && !box_y && !nearest) {
            festina_resample_bilinear(dd, dstride_words, &sv, &inv, x0, y0, x1, y1, m, opaque_dest, mask, mx0, my0, mw);
        } else {
            FestinaTaps tx, ty;
            FestinaTapKind kx = nearest ? FESTINA_TAPS_NEAREST : box_x ? FESTINA_TAPS_BOX : FESTINA_TAPS_BILINEAR;
            FestinaTapKind ky = nearest ? FESTINA_TAPS_NEAREST : box_y ? FESTINA_TAPS_BOX : FESTINA_TAPS_BILINEAR;
            if (festina_taps_build(&tx, kx, x0, x1, inv.xx, inv.x0, box_x ? 1.0 / fsx : 0.0, sv.w)) {
                if (festina_taps_build(&ty, ky, y0, y1, inv.yy, inv.y0, box_y ? 1.0 / fsy : 0.0, sv.h))
                    festina_resample_separable(dd, dstride_words, &sv, &tx, &ty, x0, y0, x1, y1, m, opaque_dest, mask, mx0, my0, mw);
                festina_taps_free(&ty);
            }
            festina_taps_free(&tx);
        }
    } else {
        festina_resample_general(dd, dstride_words, &sv, &inv, fsx, fsy, x0, y0, x1, y1, m, opaque_dest, mask, mx0, my0, mw);
    }
    cairo_surface_mark_dirty_rectangle(dst, (int)x0, (int)y0, (int)(x1 - x0), (int)(y1 - y0));
}

/* src copied into the new surface `dst`, the source's pixel (sx + i,
 * sy + j) landing on the destination's (i, j); what falls outside the
 * source stays as it is (transparent, in a fresh surface). */
static void festina_surface_copy_region(cairo_surface_t *dst, cairo_surface_t *srcs,
                                        int64_t sx, int64_t sy) {
    FestinaSrcView sv;
    if (!festina_src_view(&sv, srcs)) return;
    cairo_surface_flush(dst);
    unsigned char *dd = cairo_image_surface_get_data(dst);
    if (!dd) return;
    int dw = cairo_image_surface_get_width(dst);
    int dh = cairo_image_surface_get_height(dst);
    size_t dstride = (size_t)cairo_image_surface_get_stride(dst);
    int64_t xa = sx < 0 ? -sx : 0;
    int64_t xb = (int64_t)sv.w - sx < dw ? (int64_t)sv.w - sx : dw;
    if (xa >= xb) return;
    for (int64_t j = 0; j < dh; j++) {
        int64_t srow = sy + j;
        if (srow < 0 || srow >= sv.h) continue;
        const uint32_t *sp = sv.px + (size_t)srow * sv.stride + (size_t)(sx + xa);
        uint32_t *dp = (uint32_t *)(dd + (size_t)j * dstride) + xa;
        if (!sv.force_alpha) memcpy(dp, sp, (size_t)(xb - xa) * 4);
        else for (int64_t i = 0; i < xb - xa; i++) dp[i] = sp[i] | sv.force_alpha;
    }
    cairo_surface_mark_dirty(dst);
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
    cairo_surface_t *out = festina_surface_create(CAIRO_FORMAT_ARGB32, (int)w, (int)h);
    festina_surface_prefault(out);
    if (festina_image_ours()) {
        festina_surface_copy_region(out, src, x, y);
        return festina_image_box(out);
    }
    cairo_t *cr = cairo_create(out);
    /* Offsetting the source by -x/-y puts the requested region at the
     * new surface's origin. */
    cairo_set_source_surface(cr, src, -(double)x, -(double)y);
    /* claude.md #240: SOURCE, not the default OVER. The destination
     * was created transparent a moment ago, and OVER onto transparent
     * black is the source pixel exactly -- so this is the same result
     * as a straight copy, which is what SOURCE lets pixman do
     * (a per-row memcpy instead of a per-pixel blend). Measured on the
     * layered-canvas benchmark's four 800x600 clips: 3.1 ms -> under
     * 1 ms, pixel-identical. */
    cairo_set_operator(cr, CAIRO_OPERATOR_SOURCE);
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
    cairo_surface_t *out = festina_surface_create(CAIRO_FORMAT_ARGB32, (int)w, (int)h);
    festina_surface_prefault(out);   /* claude.md #240 */
    return festina_image_box(out);
}

/* runtime.md: the decoder-first load path's fallback.
 *
 * Codegen emits a call to the Festina decoder and then this, rather
 * than a branch, so an img load stays two straight calls in the IR:
 *   %d = call ptr @festinaDecodeImage(ptr %path)
 *   %i = call ptr @festina_load_image_via(ptr %d, ptr %path)
 *
 * A null %d means the Festina decoders declined the file -- a format
 * they do not implement, or one they refuse rather than half-decode --
 * and the C loader handles it exactly as it always did. The file is
 * read twice on that path, which is the price of a fallback and is
 * paid only by formats the port has not reached.
 *
 * A NON-null %d still has to be finished here, and forgetting that was
 * a real regression: claude.md #110 keeps the bytes an image was
 * loaded from so that save()/saveCopy() reproduce the file rather than
 * re-encoding it. festina_load_image attaches them; the Festina
 * decoder hands back pixels and knows nothing about the file. Without
 * this block a saved JPEG came back a PNG, which four tests in
 * test_codegen.py said out loud.
 *
 * So the source file IS read on this path too. It is not the decode
 * -- that is the expensive half and it has already been skipped -- and
 * it is what makes the decoder swap invisible to everything that
 * looks at an image's origin. A file that decoded a moment ago and
 * cannot be read now is not worth failing over: the image is already
 * correct, and it simply loses the byte-exact save, the same position
 * an image from a pixel buffer is in. */
void *festina_load_image_via(void *decoded, const char *path) {
    if (!decoded) return festina_load_image(path);

    FestinaImageBox *box = (FestinaImageBox *)decoded;
    int64_t len = 0;
    unsigned char *bytes = festina_read_image_file_noflail(path, &len);
    if (bytes) {
        free(box->bytes);
        box->bytes = bytes;          /* adopted, not copied */
        box->byte_count = (size_t)len;
    }
    free(box->path);
    box->path = path ? strdup(path) : NULL;
    if (path && !box->path) festina_fail("out of memory loading an image");
    return decoded;
}

/* claude.md #346: an image from a pixel buffer, in one call.
 *
 * `arr` is a Festina arr[int]: header[0] is the length and header[1]
 * the data pointer, exactly as festina_arr_join reads one. Four
 * elements per pixel -- R, G, B, A -- row-major from the top-left.
 *
 * This adds no capability. The same image can be built with
 * blankImage() and a fillStyle/drawPixel pair per pixel, which is what
 * runtime.md's decoders would otherwise have to do; that loop runs at
 * about 18 million pixels a second, so a 1920x1080 image spends ~115ms
 * being handed over one pixel at a time. This is the copy instead.
 *
 * Cairo's ARGB32 is PREMULTIPLIED and native-endian, which is the only
 * subtle part: a half-transparent red is (128, 0, 0, 128) here and
 * (255, 0, 0, 128) in the straight-alpha buffer a decoder produces.
 * Getting that backwards makes every partially transparent pixel too
 * bright, and looks correct on every fully opaque one -- which is most
 * test images. */
void *festina_image_from_pixels(void *arr, int64_t w, int64_t h) {
    festina_check_image_size("imageFromPixels", w, h);
    int64_t want = w * h * 4;
    int64_t n = 0;
    int64_t *data = NULL;
    if (arr) {
        int64_t *header = (int64_t *)arr;
        n = header[0];
        memcpy(&data, &header[1], sizeof(int64_t *));
    }
    if (n != want) {
        char msg[160];
        snprintf(msg, sizeof(msg),
                 "imageFromPixels: %lld pixels' worth of data given for a "
                 "%lldx%lld image, which needs %lld",
                 (long long)(n / 4), (long long)w, (long long)h,
                 (long long)want);
        festina_fail(msg);
        return NULL;
    }

    cairo_surface_t *out = festina_surface_create(CAIRO_FORMAT_ARGB32,
                                                      (int)w, (int)h);
    festina_surface_prefault(out);
    unsigned char *dst = cairo_image_surface_get_data(out);
    int stride = cairo_image_surface_get_stride(out);
    for (int64_t y = 0; y < h; y++) {
        uint32_t *row = (uint32_t *)(dst + (y * stride));
        for (int64_t x = 0; x < w; x++) {
            int64_t at = ((y * w) + x) * 4;
            int64_t r = data[at];
            int64_t g = data[at + 1];
            int64_t b = data[at + 2];
            int64_t a = data[at + 3];
            /* Clamped exactly as fillStyle(r, g, b) clamps. */
            if (r < 0) r = 0; if (r > 255) r = 255;
            if (g < 0) g = 0; if (g > 255) g = 255;
            if (b < 0) b = 0; if (b > 255) b = 255;
            if (a < 0) a = 0; if (a > 255) a = 255;
            /* Premultiply, rounding rather than truncating so that
             * alpha 255 is exactly the original component. */
            uint32_t pr = (uint32_t)(((r * a) + 127) / 255);
            uint32_t pg = (uint32_t)(((g * a) + 127) / 255);
            uint32_t pb = (uint32_t)(((b * a) + 127) / 255);
            row[x] = ((uint32_t)a << 24) | (pr << 16) | (pg << 8) | pb;
        }
    }
    cairo_surface_mark_dirty(out);
    return festina_image_box(out);
}

/* runtime.md phase 4: img.toPixels() -- the exact inverse of
 * imageFromPixels above, and the read half of the pixel handoff.
 *
 * A rasteriser written in Festina has to see what it is drawing onto,
 * and getPixelColor() is one pixel per call across the language
 * boundary. This hands the whole surface over in one call, in the
 * same format imageFromPixels takes: four `int` per pixel -- R, G, B,
 * A, straight (NOT premultiplied) alpha -- row-major from the
 * top-left. So `imageFromPixels(a.toPixels(), a.width, a.height)`
 * reproduces `a`, and that round trip is the test.
 *
 * Un-premultiplying is the whole subtlety, and it is exactly what
 * festina_pixel_color_from_surface already does for one pixel
 * (claude.md #189/#192), including the RGB24 case: a JPEG-loaded
 * surface stores 0 in the top byte because it has no alpha channel,
 * not because it is transparent, and reading that as alpha would make
 * every pixel of every JPEG come back fully transparent.
 *
 * Alpha 0 answers (0, 0, 0, 0) rather than getPixelColor's -1 'none'
 * sentinel: this returns four channel values, not a `color`, and a
 * fully transparent pixel has no colour to recover -- premultiplied
 * storage has already multiplied it away. Round-tripping it gives
 * back a transparent pixel, which is what it was. */
void *festina_image_to_pixels(void *img) {
    if (!img) return NULL;
    cairo_surface_t *surface = ((FestinaImageBox *)img)->surface;
    if (!surface) return NULL;
    cairo_surface_flush(surface);
    int w = cairo_image_surface_get_width(surface);
    int h = cairo_image_surface_get_height(surface);
    int stride = cairo_image_surface_get_stride(surface);
    const unsigned char *src = cairo_image_surface_get_data(surface);
    int opaque = cairo_image_surface_get_format(surface) == CAIRO_FORMAT_RGB24;
    int64_t n = (int64_t)w * (int64_t)h * 4;
    if (!src) n = 0;

    /* The array Festina itself emits: one block holding the refcount
     * and the {length, data} payload, with the elements in a second
     * allocation -- see festina_release_array, which frees exactly
     * these two. A refcount of 1 because this is a fresh value the
     * caller owns, not a literal (those carry the negative immortal
     * sentinel). */
    char *raw = calloc(1, sizeof(int64_t) + 2 * sizeof(int64_t));
    if (!raw) festina_fail("out of memory reading an image's pixels");
    *(int64_t *)raw = 1;
    int64_t *payload = (int64_t *)(raw + sizeof(int64_t));
    int64_t *data = malloc((size_t)(n ? n : 1) * sizeof(int64_t));
    if (!data) festina_fail("out of memory reading an image's pixels");
    payload[0] = n;
    memcpy(&payload[1], &data, sizeof(int64_t *));

    int64_t at = 0;
    for (int y = 0; y < h && n; y++) {
        for (int x = 0; x < w; x++) {
            uint32_t px;
            memcpy(&px, src + (int64_t)y * stride + (int64_t)x * 4, sizeof(px));
            uint32_t a = opaque ? 255u : ((px >> 24) & 0xff);
            uint32_t r = (px >> 16) & 0xff;
            uint32_t g = (px >> 8) & 0xff;
            uint32_t b = px & 0xff;
            if (a == 0) {
                r = g = b = 0;
            } else if (a < 255) {
                r = (r * 255 + a / 2) / a;
                g = (g * 255 + a / 2) / a;
                b = (b * 255 + a / 2) / a;
            }
            data[at++] = (int64_t)r;
            data[at++] = (int64_t)g;
            data[at++] = (int64_t)b;
            data[at++] = (int64_t)a;
        }
    }
    return payload;
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
    cairo_surface_t *out = festina_surface_create(CAIRO_FORMAT_ARGB32, w, h);
    festina_surface_prefault(out);
    if (festina_image_ours()) {
        festina_surface_copy_region(out, g_backing_surface, 0, 0);
        return festina_image_box(out);
    }
    cairo_t *cr = cairo_create(out);
    cairo_set_source_surface(cr, g_backing_surface, 0, 0);
    cairo_set_operator(cr, CAIRO_OPERATOR_SOURCE); /* claude.md #240: see festina_image_clip */
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
    cairo_surface_t *out = festina_surface_create(CAIRO_FORMAT_ARGB32, (int)w, (int)h);
    festina_surface_prefault(out);
    if (festina_image_ours()) {
        cairo_matrix_t fwd;
        festina_matrix_identity(&fwd);
        festina_matrix_scale(&fwd, (double)w / src_w, (double)h / src_h);
        festina_composite_image(out, box->surface, &fwd, 1.0, 0, 0, w, h, NULL);
        cairo_surface_destroy(box->surface);
        box->surface = out;
        free(box->bytes);
        box->bytes = NULL;
        box->byte_count = 0;
        return;
    }
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
    /* claude.md #240: read before writing. This runs on every draw
     * call, and the four boxes of four `img?` layers painted by four
     * threads at once are small consecutive allocations -- writing
     * NULL over an already-NULL field on each call had those threads
     * ping-ponging the same cache lines between cores on every single
     * shape (false sharing). A read of an unchanged line is free. */
    if (!box->bytes) return;
    free(box->bytes);
    box->bytes = NULL;
    box->byte_count = 0;
}

/* An img as a target: its own transform (the identity until it has one). */
static int festina_image_raster_shape(FestinaImageBox *box, int circle,
                                      double a, double b, double c, double d,
                                      int fill_overridden, int64_t fill_color,
                                      int border_overridden, int64_t border_color) {
    cairo_matrix_t ident;
    festina_matrix_identity(&ident);
    return festina_raster_shape(box, box->transform_ready ? &box->transform : &ident, circle,
                                a, b, c, d, fill_overridden, fill_color,
                                border_overridden, border_color);
}

static const cairo_matrix_t *festina_image_matrix(FestinaImageBox *box, cairo_matrix_t *ident) {
    festina_matrix_identity(ident);
    return box->transform_ready ? &box->transform : ident;
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

/* runtime.md phase 7, slice 2: the row-compositing primitive -- the one
 * thing raster.f needs from C to draw onto an img.
 *
 * raster.f (Festina) works out how much of each pixel of a row a shape
 * covers, as floats 0..1 in `cov`; this blends the colour (r, g, b, a),
 * with `alpha` the fill's own 0..1, into the surface's own bytes across [x0, x1) of
 * row `row`, weighted by that coverage. The surface is a plain buffer
 * (slice 1), so nothing is copied and no Cairo call is made -- this is
 * text.f's architecture turned round: there Festina answered coverage
 * and C composited it, and it is the same split here (runtime.md,
 * "The architecture proposed", option A).
 *
 * The arithmetic is pixman's own, the same routines the solid-fill fast
 * path (claude.md #240) and the text mask use: the coverage becomes a
 * byte, the premultiplied colour is scaled by it with MUL_UN8, and that
 * goes OVER the destination. So where raster.f and Cairo agree on how
 * much of a pixel is covered, they agree on the pixel, byte for byte;
 * what remains is the coverage itself, which is the rasteriser's
 * business and is measured where it is drawn.
 *
 * Drawing invalidates the image's cached encoded bytes, as every other
 * drawing call does (claude.md #101): an image loaded from a file keeps
 * the file's own bytes for save() and `file:img` columns, and without
 * this a picture drawn on with raster.f would be saved as it was loaded.
 *
 * A coverage of 0 touches nothing; `cov` shorter than [x0, x1), a row
 * or column outside the surface, or a surface that is not an image
 * surface are clipped away rather than read past. Returns nothing: a
 * rasteriser drawing into a surface has no error to act on. */
void festina_image_blend_row(void *img, int64_t row, int64_t x0, int64_t x1, void *cov,
                             int64_t cr, int64_t cg, int64_t cb, double alpha) {
    if (!img || !cov) return;
    cairo_surface_t *s = ((FestinaImageBox *)img)->surface;
    if (!s || cairo_surface_get_type(s) != CAIRO_SURFACE_TYPE_IMAGE) return;
    cairo_format_t format = cairo_image_surface_get_format(s);
    if (format != CAIRO_FORMAT_ARGB32 && format != CAIRO_FORMAT_RGB24) return;
    int sw = cairo_image_surface_get_width(s);
    int sh = cairo_image_surface_get_height(s);
    if (row < 0 || row >= sh) return;
    int64_t *payload = (int64_t *)cov;
    int64_t n = payload[0];
    const double *c;
    memcpy(&c, &payload[1], sizeof(c));
    if (!c) return;
    if (x0 < 0) x0 = 0;
    if (x1 > sw) x1 = sw;
    if (x1 > n) x1 = n;
    if (x0 >= x1) return;
    if (!(alpha > 0.0)) return;
    if (alpha > 1.0) alpha = 1.0;
    if (cr < 0) cr = 0; else if (cr > 255) cr = 255;
    if (cg < 0) cg = 0; else if (cg > 255) cg = 255;
    if (cb < 0) cb = 0; else if (cb > 255) cb = 255;

    cairo_surface_flush(s);
    unsigned char *data = cairo_image_surface_get_data(s);
    if (!data) return;
    uint32_t *p = (uint32_t *)(data + row * cairo_image_surface_get_stride(s));
    /* The source the way Cairo reduces every solid colour before it
     * composites: alpha and each channel times alpha, in doubles, each
     * to a 16-bit short and then its high byte (festina_channel_byte,
     * which is the same route fillStyle's own colours take). Premultiplying
     * from a rounded 8-bit alpha instead would be one grey level off at
     * alphas like 0.7, which is why alpha arrives as the double the
     * program set and not as a byte. Once per call, not per pixel. */
    uint32_t sa = festina_channel_byte(alpha);
    uint32_t pr = festina_channel_byte(((double)cr / 255.0) * alpha);
    uint32_t pg = festina_channel_byte(((double)cg / 255.0) * alpha);
    uint32_t pb = festina_channel_byte(((double)cb / 255.0) * alpha);
    int opaque_dest = format == CAIRO_FORMAT_RGB24;
    uint32_t full = (sa << 24) | (pr << 16) | (pg << 8) | pb;
    for (int64_t i = x0; i < x1; i++) {
        double v = c[i];
        if (!(v > 0.0)) continue;
        /* Opaque colour, full coverage: the pixel is the colour. OVER of
         * an opaque source returns the source (festina_over_un8x4's first
         * line), so this skips the coverage byte and the loads. */
        if (v >= 1.0 && sa == 255u) { p[i] = full; continue; }
        uint32_t m = v >= 1.0 ? 255u : (uint32_t)(v * 255.0 + 0.5);
        if (m == 0) continue;
        /* Full coverage, the interior of nearly every shape: MUL_UN8 by
         * 255 is the identity, so the source is the colour as reduced. */
        uint32_t in = m == 255u ? full
                    : (festina_mul_un8(sa, m) << 24) | (festina_mul_un8(pr, m) << 16)
                    | (festina_mul_un8(pg, m) << 8) | festina_mul_un8(pb, m);
        uint32_t d = p[i];
        if (opaque_dest) d |= 0xFF000000u;
        d = festina_over_un8x4(in, d);
        p[i] = opaque_dest ? (d | 0xFF000000u) : d;
    }
    cairo_surface_mark_dirty_rectangle(s, (int)x0, (int)row, (int)(x1 - x0), 1);
    festina_image_bytes_now_stale(img);
}

/* The same row blend for a colour that changes along the row -- a
 * gradient: `words[i]` is pixel i's source, premultiplied ARGB as a 32-bit
 * word (alpha already in it), and `cov` its coverage. Each is scaled by its
 * coverage byte and goes OVER the destination with pixman's arithmetic,
 * exactly as festina_image_blend_row does for one colour; clipped the same
 * way, to the image and to both arrays. */
void festina_image_blend_row_words(void *img, int64_t row, int64_t x0, int64_t x1, void *cov, void *words) {
    if (!img || !cov || !words) return;
    cairo_surface_t *s = ((FestinaImageBox *)img)->surface;
    if (!s || cairo_surface_get_type(s) != CAIRO_SURFACE_TYPE_IMAGE) return;
    cairo_format_t format = cairo_image_surface_get_format(s);
    if (format != CAIRO_FORMAT_ARGB32 && format != CAIRO_FORMAT_RGB24) return;
    int sw = cairo_image_surface_get_width(s);
    int sh = cairo_image_surface_get_height(s);
    if (row < 0 || row >= sh) return;
    int64_t *cp = (int64_t *)cov, *wp = (int64_t *)words;
    int64_t n = cp[0] < wp[0] ? cp[0] : wp[0];
    const double *c;
    const int64_t *w;
    memcpy(&c, &cp[1], sizeof(c));
    memcpy(&w, &wp[1], sizeof(w));
    if (!c || !w) return;
    if (x0 < 0) x0 = 0;
    if (x1 > sw) x1 = sw;
    if (x1 > n) x1 = n;
    if (x0 >= x1) return;
    cairo_surface_flush(s);
    unsigned char *data = cairo_image_surface_get_data(s);
    if (!data) return;
    uint32_t *p = (uint32_t *)(data + row * cairo_image_surface_get_stride(s));
    int opaque_dest = format == CAIRO_FORMAT_RGB24;
    for (int64_t i = x0; i < x1; i++) {
        double v = c[i];
        if (!(v > 0.0)) continue;
        uint32_t m = v >= 1.0 ? 255u : (uint32_t)(v * 255.0 + 0.5);
        if (m == 0) continue;
        uint32_t sp = (uint32_t)w[i];
        uint32_t in = m == 255u ? sp
                    : (festina_mul_un8(sp >> 24, m) << 24) | (festina_mul_un8((sp >> 16) & 0xFF, m) << 16)
                    | (festina_mul_un8((sp >> 8) & 0xFF, m) << 8) | festina_mul_un8(sp & 0xFF, m);
        uint32_t d = p[i];
        if (opaque_dest) d |= 0xFF000000u;
        d = festina_over_un8x4(in, d);
        p[i] = opaque_dest ? (d | 0xFF000000u) : d;
    }
    cairo_surface_mark_dirty_rectangle(s, (int)x0, (int)row, (int)(x1 - x0), 1);
    festina_image_bytes_now_stale(img);
}

/* Clearing through a coverage row: Cairo's SOURCE operator with a
 * transparent source, which leaves each pixel scaled by one minus its
 * coverage -- all four premultiplied channels, so the colour fades with
 * the alpha -- and a pixel fully covered becomes 0. */
void festina_image_clear_row(void *img, int64_t row, int64_t x0, int64_t x1, void *cov) {
    if (!img || !cov) return;
    cairo_surface_t *s = ((FestinaImageBox *)img)->surface;
    if (!s || cairo_surface_get_type(s) != CAIRO_SURFACE_TYPE_IMAGE) return;
    cairo_format_t format = cairo_image_surface_get_format(s);
    if (format != CAIRO_FORMAT_ARGB32 && format != CAIRO_FORMAT_RGB24) return;
    int sw = cairo_image_surface_get_width(s);
    int sh = cairo_image_surface_get_height(s);
    if (row < 0 || row >= sh) return;
    int64_t *cp = (int64_t *)cov;
    int64_t n = cp[0];
    const double *c;
    memcpy(&c, &cp[1], sizeof(c));
    if (!c) return;
    if (x0 < 0) x0 = 0;
    if (x1 > sw) x1 = sw;
    if (x1 > n) x1 = n;
    if (x0 >= x1) return;
    cairo_surface_flush(s);
    unsigned char *data = cairo_image_surface_get_data(s);
    if (!data) return;
    uint32_t *p = (uint32_t *)(data + row * cairo_image_surface_get_stride(s));
    for (int64_t i = x0; i < x1; i++) {
        double v = c[i];
        if (!(v > 0.0)) continue;
        uint32_t m = v >= 1.0 ? 255u : (uint32_t)(v * 255.0 + 0.5);
        if (m == 0) continue;
        if (m == 255u) { p[i] = 0; continue; }
        uint32_t d = p[i], ia = 255u - m;
        p[i] = (festina_mul_un8(d >> 24, ia) << 24) | (festina_mul_un8((d >> 16) & 0xFF, ia) << 16)
             | (festina_mul_un8((d >> 8) & 0xFF, ia) << 8) | festina_mul_un8(d & 0xFF, ia);
    }
    cairo_surface_mark_dirty_rectangle(s, (int)x0, (int)row, (int)(x1 - x0), 1);
    festina_image_bytes_now_stale(img);
}

/* claude.md #240: the img half of the solid-fill fast path -- the same
 * contract as the canvas's festina_canvas_direct_ok, against THIS
 * image's own transform. This is the path that made the layered-canvas
 * benchmark's four worker threads fast: each stamps into its own
 * `img?` with no Cairo context, no shared cache lock and no global
 * state written (the style globals are only read, exactly as #234
 * already required of the colour-override forms). */
static int festina_image_direct_ok(FestinaImageBox *box, int64_t *tx, int64_t *ty) {
    return festina_direct_fill_enabled() && festina_solid_style_ok()
        && festina_matrix_integer_offset(&box->transform, box->transform_ready, tx, ty);
}

static int festina_image_direct_override_ok(FestinaImageBox *box, int64_t color, int border_effective,
                                            int64_t *tx, int64_t *ty) {
    return festina_direct_fill_enabled() && festina_solid_override_ok(color, border_effective)
        && festina_matrix_integer_offset(&box->transform, box->transform_ready, tx, ty);
}

void festina_image_draw_rect(void *img, int64_t x, int64_t y, int64_t w, int64_t h) {
    if (!img) return;
    FestinaImageBox *box = (FestinaImageBox *)img;
    int64_t tx, ty;
    if (festina_image_direct_ok(box, &tx, &ty) &&
        festina_direct_rect(box->surface, x, y, tx, ty, w, h, festina_solid_pixel_from_style())) {
        festina_image_bytes_now_stale(img);
        return;
    }
    if (festina_image_raster_shape(box, 0, (double)x, (double)y, (double)w, (double)h, 0, 0, 0, 0)) {
        festina_image_bytes_now_stale(img);
        return;
    }
    cairo_t *cr = festina_image_context(box);
    cairo_rectangle(cr, (double)x, (double)y, (double)w, (double)h);
    festina_fill_and_border(cr);
    cairo_destroy(cr);
    festina_image_bytes_now_stale(img);
}

void festina_image_draw_rect_color(void *img, int64_t x, int64_t y, int64_t w, int64_t h, int64_t color) {
    if (!img) return;
    FestinaImageBox *box = (FestinaImageBox *)img;
    int64_t tx, ty;
    if (festina_image_direct_override_ok(box, color, g_border_set && g_line_width > 0.0, &tx, &ty) &&
        festina_direct_rect(box->surface, x, y, tx, ty, w, h, festina_solid_pixel_from_color(color))) {
        festina_image_bytes_now_stale(img);
        return;
    }
    if (festina_image_raster_shape(box, 0, (double)x, (double)y, (double)w, (double)h, 1, color, 0, 0)) {
        festina_image_bytes_now_stale(img);
        return;
    }
    cairo_t *cr = festina_image_context(box);
    cairo_rectangle(cr, (double)x, (double)y, (double)w, (double)h);
    festina_fill_and_border_with_color(cr, color);
    cairo_destroy(cr);
    festina_image_bytes_now_stale(img);
}

/* claude.md #188 (uraikus/festina#76 item 8) */
void festina_image_draw_rect_colors(void *img, int64_t x, int64_t y, int64_t w, int64_t h,
                                     int64_t fill_color, int64_t border_color) {
    if (!img) return;
    FestinaImageBox *box = (FestinaImageBox *)img;
    int64_t tx, ty;
    if (festina_image_direct_override_ok(box, fill_color, border_color >= 0 && g_line_width > 0.0, &tx, &ty) &&
        festina_direct_rect(box->surface, x, y, tx, ty, w, h, festina_solid_pixel_from_color(fill_color))) {
        festina_image_bytes_now_stale(img);
        return;
    }
    if (festina_image_raster_shape(box, 0, (double)x, (double)y, (double)w, (double)h, 1, fill_color, 1, border_color)) {
        festina_image_bytes_now_stale(img);
        return;
    }
    cairo_t *cr = festina_image_context(box);
    cairo_rectangle(cr, (double)x, (double)y, (double)w, (double)h);
    festina_fill_and_border_with_colors(cr, fill_color, border_color);
    cairo_destroy(cr);
    festina_image_bytes_now_stale(img);
}

/* See festina_draw_pixel's own comment (just above festina_draw_circle
 * in this file) for why antialiasing is disabled around the fill. */
void festina_image_draw_pixel(void *img, int64_t x, int64_t y) {
    if (!img) return;
    FestinaImageBox *box = (FestinaImageBox *)img;
    int64_t tx, ty;
    if (festina_direct_fill_enabled() && !g_fill_none && !g_fill_gradient &&
        festina_matrix_integer_offset(&box->transform, box->transform_ready, &tx, &ty) &&
        festina_direct_rect(box->surface, x, y, tx, ty, 1, 1, festina_solid_pixel_from_style())) {
        festina_image_bytes_now_stale(img);
        return;
    }
    {
        cairo_matrix_t ident;
        if (festina_raster_pixel(box, festina_image_matrix(box, &ident), x, y, 0, 0)) {
            festina_image_bytes_now_stale(img);
            return;
        }
    }
    cairo_t *cr = festina_image_context(box);
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
    FestinaImageBox *box = (FestinaImageBox *)img;
    int64_t tx, ty;
    if (festina_image_direct_override_ok(box, color, 0, &tx, &ty) &&
        festina_direct_rect(box->surface, x, y, tx, ty, 1, 1, festina_solid_pixel_from_color(color))) {
        festina_image_bytes_now_stale(img);
        return;
    }
    {
        cairo_matrix_t ident;
        if (festina_raster_pixel(box, festina_image_matrix(box, &ident), x, y, 1, color)) {
            festina_image_bytes_now_stale(img);
            return;
        }
    }
    cairo_t *cr = festina_image_context(box);
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

/* claude.md #240: img circles take the solid-fill fast path too (the
 * stamped coverage is Cairo's own, so the pixels match). This used to
 * say drawing onto an image was "a far rarer, less hot-path call" than
 * the canvas's -- true until `img?` layers painted by worker threads
 * (claude.md #239) made it THE hot path: 21,000 of the layered
 * benchmark's 40,000 calls are img circles, and they were 72 of its
 * 84 single-threaded milliseconds. The Cairo fallback is the
 * tessellating one it always was (no #104-style mask stamp: that
 * cache is the canvas's, keyed on its transform state). */
void festina_image_draw_circle(void *img, int64_t x, int64_t y, int64_t r) {
    if (!img) return;
    FestinaImageBox *box = (FestinaImageBox *)img;
    int64_t tx, ty;
    if (festina_image_direct_ok(box, &tx, &ty) &&
        festina_direct_circle(box->surface, x, y, tx, ty, r, festina_solid_pixel_from_style())) {
        festina_image_bytes_now_stale(img);
        return;
    }
    if (festina_image_raster_shape(box, 1, (double)x, (double)y, (double)(r < 0 ? 0 : r), 0, 0, 0, 0, 0)) {
        festina_image_bytes_now_stale(img);
        return;
    }
    cairo_t *cr = festina_image_context(box);
    cairo_arc(cr, (double)x, (double)y, (double)(r < 0 ? 0 : r), 0.0, 2.0 * 3.14159265358979323846);
    festina_fill_and_border(cr);
    cairo_destroy(cr);
    festina_image_bytes_now_stale(img);
}

/* claude.md #188 (uraikus/festina#76 item 8) */
void festina_image_draw_circle_color(void *img, int64_t x, int64_t y, int64_t r, int64_t color) {
    if (!img) return;
    FestinaImageBox *box = (FestinaImageBox *)img;
    int64_t tx, ty;
    if (festina_image_direct_override_ok(box, color, g_border_set && g_line_width > 0.0, &tx, &ty) &&
        festina_direct_circle(box->surface, x, y, tx, ty, r, festina_solid_pixel_from_color(color))) {
        festina_image_bytes_now_stale(img);
        return;
    }
    if (festina_image_raster_shape(box, 1, (double)x, (double)y, (double)(r < 0 ? 0 : r), 0, 1, color, 0, 0)) {
        festina_image_bytes_now_stale(img);
        return;
    }
    cairo_t *cr = festina_image_context(box);
    cairo_arc(cr, (double)x, (double)y, (double)(r < 0 ? 0 : r), 0.0, 2.0 * 3.14159265358979323846);
    festina_fill_and_border_with_color(cr, color);
    cairo_destroy(cr);
    festina_image_bytes_now_stale(img);
}

void festina_image_draw_circle_colors(void *img, int64_t x, int64_t y, int64_t r,
                                       int64_t fill_color, int64_t border_color) {
    if (!img) return;
    FestinaImageBox *box = (FestinaImageBox *)img;
    int64_t tx, ty;
    if (festina_image_direct_override_ok(box, fill_color, border_color >= 0 && g_line_width > 0.0, &tx, &ty) &&
        festina_direct_circle(box->surface, x, y, tx, ty, r, festina_solid_pixel_from_color(fill_color))) {
        festina_image_bytes_now_stale(img);
        return;
    }
    if (festina_image_raster_shape(box, 1, (double)x, (double)y, (double)(r < 0 ? 0 : r), 0, 1, fill_color, 1, border_color)) {
        festina_image_bytes_now_stale(img);
        return;
    }
    cairo_t *cr = festina_image_context(box);
    cairo_arc(cr, (double)x, (double)y, (double)(r < 0 ? 0 : r), 0.0, 2.0 * 3.14159265358979323846);
    festina_fill_and_border_with_colors(cr, fill_color, border_color);
    cairo_destroy(cr);
    festina_image_bytes_now_stale(img);
}

void festina_image_draw_text(void *img, const char *text, int64_t x, int64_t y) {
    if (!img || !text) return;
    FestinaImageBox *box = (FestinaImageBox *)img;
    cairo_t *cr = festina_image_context(box);
    if (g_fill_none) { cairo_destroy(cr); return; }
    cairo_set_source_rgba(cr, g_fill_r, g_fill_g, g_fill_b, g_fill_alpha);
    if (!festina_text_draw(cr, box->transform_ready ? &box->transform : NULL, text, x, y)) {
        festina_apply_font(cr);
        cairo_move_to(cr, (double)x, (double)y);
        cairo_show_text(cr, text);
    }
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
        festina_matrix_identity(&box->transform);
        box->transform_ready = 1;
    }
}

void festina_image_translate(void *img, int64_t x, int64_t y) {
    if (!img) return;
    FestinaImageBox *box = (FestinaImageBox *)img;
    festina_image_transform_require(box);
    festina_matrix_translate(&box->transform, (double)x, (double)y);
}

void festina_image_rotate(void *img, double degrees) {
    if (!img) return;
    FestinaImageBox *box = (FestinaImageBox *)img;
    festina_image_transform_require(box);
    /* Degrees, exactly like the canvas's rotate() -- see its comment. */
    festina_matrix_rotate(&box->transform, degrees * 3.14159265358979323846 / 180.0);
}

void festina_image_scale(void *img, double sx, double sy) {
    if (!img) return;
    FestinaImageBox *box = (FestinaImageBox *)img;
    festina_image_transform_require(box);
    /* Same guard as festina_scale: a zero scale would leave a
     * non-invertible matrix every later call silently fails on. */
    if (sx == 0.0 || sy == 0.0) return;
    festina_matrix_scale(&box->transform, sx, sy);
}

void festina_image_reset_transform(void *img) {
    if (!img) return;
    FestinaImageBox *box = (FestinaImageBox *)img;
    festina_matrix_identity(&box->transform);
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
    if (festina_draw_ours()) {
        festina_surface_zero(((FestinaImageBox *)img)->surface);
        festina_image_bytes_now_stale(img);
        return;
    }
    cairo_t *cr = cairo_create(((FestinaImageBox *)img)->surface);
    cairo_set_operator(cr, CAIRO_OPERATOR_SOURCE);
    cairo_set_source_rgba(cr, 0, 0, 0, 0);
    cairo_paint(cr);
    cairo_destroy(cr);
    festina_image_bytes_now_stale(img);
}

void festina_image_clear_rect(void *img, int64_t x, int64_t y, int64_t w, int64_t h) {
    if (!img) return;
    {
        cairo_matrix_t ident;
        FestinaImageBox *box = (FestinaImageBox *)img;
        if (festina_raster_clear(box, festina_image_matrix(box, &ident), 0, (double)x, (double)y, (double)w, (double)h)) {
            festina_image_bytes_now_stale(img);
            return;
        }
    }
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
    {
        cairo_matrix_t ident;
        FestinaImageBox *box = (FestinaImageBox *)img;
        if (festina_raster_clear(box, festina_image_matrix(box, &ident), 1, (double)x, (double)y, (double)(r < 0 ? 0 : r), 0)) {
            festina_image_bytes_now_stale(img);
            return;
        }
    }
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
    {
        cairo_matrix_t ident;
        FestinaImageBox *box = (FestinaImageBox *)img;
        if (festina_raster_clear(box, festina_image_matrix(box, &ident), 2, (double)x, (double)y, 0, 0)) {
            festina_image_bytes_now_stale(img);
            return;
        }
    }
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
    cairo_surface_t *out = festina_surface_create(CAIRO_FORMAT_ARGB32, w, h);
    festina_surface_prefault(out);
    if (festina_image_ours()) {
        festina_surface_copy_region(out, src, 0, 0);
        return out;
    }
    cairo_t *cr = cairo_create(out);
    cairo_set_source_surface(cr, src, 0, 0);
    cairo_set_operator(cr, CAIRO_OPERATOR_SOURCE); /* claude.md #240: see festina_image_clip */
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
    if (festina_image_ours()) {
        cairo_matrix_t ident, fwd = *festina_image_matrix(d, &ident);
        festina_matrix_translate(&fwd, (double)x, (double)y);
        festina_composite_image(d->surface, source, &fwd, g_fill_alpha, 0, 0, INT32_MAX, INT32_MAX, NULL);
        if (copy) cairo_surface_destroy(copy);
        festina_image_bytes_now_stale(dst);
        return;
    }
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
    if (festina_image_ours()) {
        cairo_matrix_t ident, fwd = *festina_image_matrix(d, &ident);
        festina_matrix_translate(&fwd, (double)x, (double)y);
        festina_matrix_scale(&fwd, (double)w / (double)src_w, (double)h / (double)src_h);
        festina_composite_image(d->surface, source, &fwd, g_fill_alpha, 0, 0, INT32_MAX, INT32_MAX, NULL);
        if (copy) cairo_surface_destroy(copy);
        festina_image_bytes_now_stale(dst);
        return;
    }
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
 * alpha)`; it once also used `cairo_paint_with_alpha` for a gradient,
 * which was wrong there because paint ignores the path -- decisions.md
 * #349 -- and is right HERE because drawing an image is a paint of its
 * rectangle): cairo_set_source_surface has no alpha channel of
 * its own to carry it the way cairo_set_source_rgba does, so applying
 * the alpha has to happen at PAINT time instead of source-setup time.
 * `cairo_paint_with_alpha(cr, 1.0)` is defined to behave identically to
 * plain `cairo_paint`, so this is a strict extension, not a behavior
 * change for the (overwhelmingly common) case where fillAlpha was
 * never touched. */
void festina_draw_image(void *img, int64_t x, int64_t y) {
    festina_backing_require();
    if (!img) return;
    if (festina_image_ours()) {
        cairo_matrix_t ident, fwd = *festina_canvas_matrix(&ident);
        festina_matrix_translate(&fwd, (double)x, (double)y);
        festina_composite_image(g_backing_surface, ((FestinaImageBox *)img)->surface, &fwd,
                                g_fill_alpha, 0, 0, INT32_MAX, INT32_MAX, NULL);
        return;
    }
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
    if (festina_image_ours()) {
        cairo_matrix_t ident, fwd = *festina_canvas_matrix(&ident);
        festina_matrix_translate(&fwd, (double)x, (double)y);
        festina_matrix_scale(&fwd, (double)w / (double)src_w, (double)h / (double)src_h);
        festina_composite_image(g_backing_surface, surface, &fwd, g_fill_alpha, 0, 0, INT32_MAX, INT32_MAX, NULL);
        return;
    }
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
    if (festina_image_ours()) {
        /* The destination rectangle clips the draw. Under a transform that
         * leaves its edges between pixels (the identity, an integer move,
         * a scale by whole numbers) the clip is a pixel rectangle; under
         * any other it is a parallelogram with partly covered edge pixels,
         * and the draw is scaled by that coverage. */
        cairo_matrix_t ident, ctm = *festina_canvas_matrix(&ident), fwd = ctm;
        double q[8] = { (double)dx, (double)dy, (double)(dx + dw), (double)dy,
                        (double)(dx + dw), (double)(dy + dh), (double)dx, (double)(dy + dh) };
        double bx0 = 1e300, by0 = 1e300, bx1 = -1e300, by1 = -1e300;
        for (int i = 0; i < 4; i++) {
            double ux = q[i * 2], uy = q[i * 2 + 1];
            q[i * 2] = ctm.xx * ux + ctm.xy * uy + ctm.x0;
            q[i * 2 + 1] = ctm.yx * ux + ctm.yy * uy + ctm.y0;
            if (q[i * 2] < bx0) bx0 = q[i * 2];
            if (q[i * 2] > bx1) bx1 = q[i * 2];
            if (q[i * 2 + 1] < by0) by0 = q[i * 2 + 1];
            if (q[i * 2 + 1] > by1) by1 = q[i * 2 + 1];
        }
        festina_matrix_translate(&fwd, (double)dx, (double)dy);
        festina_matrix_scale(&fwd, (double)dw / (double)sw, (double)dh / (double)sh);
        festina_matrix_translate(&fwd, -(double)sx, -(double)sy);
        if (!isfinite(bx0) || !isfinite(by0) || !isfinite(bx1) || !isfinite(by1)) return;
        int cw = cairo_image_surface_get_width(g_backing_surface);
        int ch = cairo_image_surface_get_height(g_backing_surface);
        int aligned = ctm.xy == 0.0 && ctm.yx == 0.0
            && bx0 == floor(bx0) && bx1 == floor(bx1) && by0 == floor(by0) && by1 == floor(by1);
        double lo_x = floor(bx0) < 0.0 ? 0.0 : floor(bx0), lo_y = floor(by0) < 0.0 ? 0.0 : floor(by0);
        double hi_x = ceil(bx1) > (double)cw ? (double)cw : ceil(bx1), hi_y = ceil(by1) > (double)ch ? (double)ch : ceil(by1);
        if (lo_x >= hi_x || lo_y >= hi_y) return;
        uint8_t *mask = NULL;
        if (!aligned) {
            mask = festina_quad_mask(q, (int64_t)lo_x, (int64_t)lo_y, (int64_t)hi_x, (int64_t)hi_y);
            if (!mask) festina_fail("out of memory drawing an image");
        }
        festina_composite_image(g_backing_surface, surface, &fwd, g_fill_alpha,
                                (int64_t)lo_x, (int64_t)lo_y, (int64_t)hi_x, (int64_t)hi_y, mask);
        free(mask);
        return;
    }
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
        g_backing_surface = festina_surface_create(CAIRO_FORMAT_ARGB32, (int)width, (int)height);
        festina_surface_prefault(g_backing_surface);   /* claude.md #240 */
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
        g_backing_surface = festina_surface_create(CAIRO_FORMAT_ARGB32, (int)ev->width, (int)ev->height);
        festina_surface_prefault(g_backing_surface);   /* claude.md #240 */
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
