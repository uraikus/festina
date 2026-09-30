/* runtime.md phase 7, slice 4: how the graphics runtime reaches the
 * drawing component written in Festina (runtime/festina/draw.f).
 *
 * The component is linked into programs that use graphics, and a
 * constructor in the object generated for it (festina/cli.py's
 * _ensure_draw_glue_object) registers this table -- so a program that
 * does not link it simply has no hooks and draws through Cairo as it
 * always did, with nothing to stub. The functions are compiled from
 * Festina: `void *` is a pointer to an `img` (the runtime's own
 * FestinaImageBox, which for the canvas is a stack box around the
 * backing surface) or to an `arr[float]`.
 *
 * `fill_src` is the fill's source, an arr[float]: [0, r, g, b] for a
 * flat colour (0..255), [1, x0, y0, x1, y1, r0, g0, b0, r1, g1, b1] for
 * a linear gradient, [2, cx, cy, radius, r0, g0, b0, r1, g1, b1] for a
 * radial one -- gradient coordinates in USER space, which is where
 * Cairo interprets them. */
#ifndef FESTINA_DRAW_HOOKS_H
#define FESTINA_DRAW_HOOKS_H

#include <stdint.h>

typedef struct {
    void (*rect)(void *target, double x, double y, double w, double h,
                 int8_t fill_on, void *fill_src,
                 int8_t border_on, int64_t br, int64_t bg, int64_t bb, double width,
                 double alpha, void *matrix);
    void (*circle)(void *target, double cx, double cy, double radius,
                   int8_t fill_on, void *fill_src,
                   int8_t border_on, int64_t br, int64_t bg, int64_t bb, double width,
                   double alpha, void *matrix);
    /* drawPixel: the unit square at (x, y), point-sampled. */
    void (*pixel)(void *target, double x, double y, int8_t fill_on, void *fill_src,
                  double alpha, void *matrix);
    /* clearRect/clearCircle/clearPixel: kind 0 rect (x, y, w, h), 1 circle
     * (cx, cy, r), 2 pixel (x, y). */
    void (*clear)(void *target, int64_t kind, double a, double b, double c, double d,
                  void *matrix);
    /* A path built with beginPath...: `ops` an arr[int] (0 move, 1 line, 2
     * cubic, 3 close) and `coords` the arr[float] they consume. */
    void (*path)(void *target, void *ops, void *coords,
                 int8_t fill_on, void *fill_src,
                 int8_t border_on, int64_t br, int64_t bg, int64_t bb, double width,
                 double alpha, void *matrix);
    /* The coverage of a circle of integer radius r in a (2r+2)-square, one
     * int (0..255) per pixel, row by row: an arr[int], to be released. */
    void *(*circle_mask)(int64_t r);
} FestinaDrawHooks;

void festina_draw_register(const FestinaDrawHooks *hooks);

#endif
