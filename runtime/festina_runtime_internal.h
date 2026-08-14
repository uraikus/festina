#ifndef FESTINA_RUNTIME_INTERNAL_H
#define FESTINA_RUNTIME_INTERNAL_H

/*
 * Shared between festina_runtime.c (core) and festina_runtime_graphics.c
 * ONLY -- unlike festina_runtime.h, nothing declared here is part of the
 * compiler-facing runtime API (festina/codegen.py's _runtime_declares
 * never emits an LLVM `declare` for any of these), so this header is
 * never included by generated code's expectations, only by the two .c
 * files that need to cooperate on timer bookkeeping across the
 * core/graphics split (see festina_runtime.h's top-of-file note on why
 * that split exists at all).
 *
 * Timer state (the FestinaTimer array, g_timers/g_timer_count/...) lives
 * entirely in festina_runtime.c, private to that file (`static`) -- it
 * has no X11 dependency, so it belongs in core regardless of whether a
 * given program uses graphics. festina_run_event_loop
 * (festina_runtime_graphics.c) still needs to know "is a timer due, and
 * when" so its select() call can wake up in time, and needs to actually
 * fire due callbacks on each pass -- these three functions are exactly
 * that seam, non-static in festina_runtime.c and declared here for
 * festina_runtime_graphics.c to call.
 */

/* Wall-clock-monotonic "now", in seconds -- CLOCK_MONOTONIC, not affected
 * by the system clock being adjusted mid-run. */
double festina_now_seconds(void);

/* The earliest next_fire_time among all active timers, in the same
 * CLOCK_MONOTONIC seconds festina_now_seconds() returns -- or -1.0 if no
 * timer is currently active (nothing to wait for). */
double festina_next_timer_deadline(void);

/* Fires (and reschedules or deactivates, as appropriate) every timer
 * whose deadline has passed. Safe to call from a loop that has no timers
 * at all (a no-op in that case) -- festina_run_event_loop calls this
 * unconditionally on every pass regardless of whether the program
 * actually uses setTimeout/setInterval. */
void festina_fire_expired_timers(void);

#endif
