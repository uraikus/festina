/*
 * Festina native runtime -- macOS windowing backend: claude.md #123 /
 * macos.md Phase 2. Implements the five functions
 * festina_runtime_window.h declares (festina_window_open/close/
 * present/events_wait/events_drain) with Cocoa, the counterpart to
 * festina_runtime_graphics.c's X11 implementation (guarded
 * `#ifndef __APPLE__` there; this file exists only for `__APPLE__`,
 * wired in by festina/cli.py's per-platform graphics config). A
 * separate Objective-C translation unit because Cocoa cannot be
 * compiled as part of a plain .c file.
 *
 * The manual event pump (festina_window_events_wait/_drain) never
 * calls [NSApp run] -- AppKit normally wants to own the whole process,
 * but Festina's model is the opposite: top-level code runs first, and
 * only THEN does festina_run_event_loop() (festina_runtime_graphics.c,
 * portable) block, alternating festina_window_events_wait (peek with a
 * timeout carrying the next timer deadline, the exact analog of the
 * X11 backend's select() on ConnectionNumber) with
 * festina_window_events_drain (pump AppKit's own queue via
 * nextEventMatchingMask/sendEvent so it drives our view's and
 * delegate's normal callbacks, then hand back whatever those callbacks
 * queued). Mouse/key input is read directly from NSView method
 * overrides rather than parsed out of raw NSEvents in
 * events_drain, because AppKit delivers window lifecycle notifications
 * (resize, close) as delegate callbacks with no NSEvent of their own
 * at all -- a single push-based path (view + delegate both feed the
 * same small ring buffer) covers every event kind uniformly instead of
 * needing two different translation strategies.
 *
 * NOTE (macos.md Phase 2): compiled and type-checked by every macOS CI
 * run (see .github/workflows/ci.yml), exactly like #121's AudioQueue
 * backend was before its hardware verification -- but not yet
 * confirmed against a real window, mouse and keyboard on real
 * hardware. festina/cli.py gates windowed graphics behind
 * FESTINA_ENABLE_MACOS_GRAPHICS=1 for exactly that reason, mirroring
 * the audio gate precedent (claude.md #121) precisely. What is a
 * best-effort mapping pending that pass, called out at its own site
 * below: the keyCode -> runtime/festina_key_names.h vocabulary table
 * (Apple's HIToolbox virtual keycodes are public and stable across
 * macOS versions, but were not checked against physical hardware here).
 */
#import <Cocoa/Cocoa.h>
#include <stdio.h>
#include <string.h>
/* Bare <cairo.h>, not <cairo/cairo.h> -- same fix as
 * festina_runtime_window.h, and the same real bug: pkg-config's
 * Cflags for darwin's `cairo` package point -I directly at the cairo
 * headers directory, and Homebrew's non-default prefix has no
 * implicit default search path to fall back on (claude.md #126). This
 * file has its own separate #include line, so it needed the same fix
 * applied twice -- missed the first time because nothing compiles
 * this translation unit but real macOS CI. */
#include <cairo.h>
#include "festina_runtime.h"          /* festina_fail */
#include "festina_runtime_window.h"

/* ---- the small event queue Cocoa's push-based callbacks feed -- see
 * this file's own top comment for why a single queue, not raw NSEvent
 * parsing, is the uniform path for every event kind. */
#define FESTINA_MAC_EVENT_QUEUE_CAP 256
static FestinaWindowEvent g_pending[FESTINA_MAC_EVENT_QUEUE_CAP];
static char g_pending_key_name[FESTINA_MAC_EVENT_QUEUE_CAP][32];
static int g_pending_head = 0;
static int g_pending_count = 0;

static void festina_mac_push(FestinaWindowEvent ev, const char *key_name) {
    if (g_pending_count >= FESTINA_MAC_EVENT_QUEUE_CAP) {
        /* Queue overrun means events_drain hasn't been called in a
         * while (the caller isn't pumping) -- dropping the oldest
         * event is a far smaller correctness hazard than growing
         * unbounded or crashing, and a real Festina program calls
         * festina_run_event_loop() in a tight cycle specifically to
         * avoid this ever mattering. */
        g_pending_head = (g_pending_head + 1) % FESTINA_MAC_EVENT_QUEUE_CAP;
        g_pending_count--;
    }
    int slot = (g_pending_head + g_pending_count) % FESTINA_MAC_EVENT_QUEUE_CAP;
    if (key_name) {
        snprintf(g_pending_key_name[slot], sizeof(g_pending_key_name[slot]), "%s", key_name);
        ev.key_name = g_pending_key_name[slot];
    }
    g_pending[slot] = ev;
    g_pending_count++;
}

static int festina_mac_pop(FestinaWindowEvent *out) {
    if (g_pending_count == 0) return 0;
    *out = g_pending[g_pending_head];
    g_pending_head = (g_pending_head + 1) % FESTINA_MAC_EVENT_QUEUE_CAP;
    g_pending_count--;
    return 1;
}

/* claude.md #40's key NAME, shared by keyDown and keyUp exactly like
 * the X11 backend's festina_x11_key_name -- see its own comment for
 * the two-tier rule this mirrors. A printable character comes back via
 * charactersIgnoringModifiers (which DOES honor Shift, unlike its
 * name's own -Ignoring- might suggest -- it ignores every OTHER
 * modifier, which is what makes a shifted "a" arrive as "A" here too,
 * the identical behavior XLookupString gives the X11 side). Anything
 * else falls back to a keyCode table into runtime/festina_key_names.h's
 * shared vocabulary -- Apple's own HIToolbox virtual keycodes, stable
 * across macOS versions, reproduced here as bare integers (pulling in
 * the whole Carbon framework for a handful of well-known constants
 * would be a strange trade). This table is the one piece of this file
 * pending the real-hardware verification pass macos.md Phase 2 calls
 * for -- it has not been checked against physical hardware. */
static void festina_mac_key_name(NSEvent *event, char *out, size_t out_size) {
    NSString *chars = [event charactersIgnoringModifiers];
    const char *utf8 = chars ? [chars UTF8String] : NULL;
    if (utf8 && utf8[0] != '\0' && (unsigned char)utf8[0] >= 0x20
            && (unsigned char)utf8[0] != 0x7F
            /* A single-byte printable ASCII char only -- anything
             * needing a second UTF-8 byte isn't one of the "ordinary
             * printable character" keys the X11 side's own tier 1
             * covers either, and this file makes no claim about
             * non-ASCII keyboard layouts. */
            && utf8[1] == '\0') {
        out[0] = utf8[0];
        out[1] = '\0';
        return;
    }
    unsigned short code = [event keyCode];
    const char *name = "";
    switch (code) {
        case 36: name = "Return"; break;
        case 48: name = "Tab"; break;
        case 51: name = "BackSpace"; break;
        case 53: name = "Escape"; break;
        case 117: name = "Delete"; break;
        case 123: name = "Left"; break;
        case 124: name = "Right"; break;
        case 125: name = "Down"; break;
        case 126: name = "Up"; break;
        case 115: name = "Home"; break;
        case 119: name = "End"; break;
        case 116: name = "Prior"; break;   /* Page Up */
        case 121: name = "Next"; break;    /* Page Down */
        case 122: name = "F1"; break;
        case 120: name = "F2"; break;
        case 99:  name = "F3"; break;
        case 118: name = "F4"; break;
        case 96:  name = "F5"; break;
        case 97:  name = "F6"; break;
        case 98:  name = "F7"; break;
        case 100: name = "F8"; break;
        case 101: name = "F9"; break;
        case 109: name = "F10"; break;
        case 103: name = "F11"; break;
        case 111: name = "F12"; break;
        case 56:  name = "Shift_L"; break;
        case 60:  name = "Shift_R"; break;
        case 59:  name = "Control_L"; break;
        case 62:  name = "Control_R"; break;
        case 58:  name = "Alt_L"; break;
        case 61:  name = "Alt_R"; break;
        case 55:  name = "Super_L"; break;   /* left Command */
        case 54:  name = "Super_R"; break;   /* right Command */
        case 57:  name = "Caps_Lock"; break;
        default: break;
    }
    snprintf(out, out_size, "%s", name);
}

@interface FestinaView : NSView
@end

@interface FestinaWindowDelegate : NSObject <NSWindowDelegate>
@end

static NSWindow *g_window = NULL;
static FestinaView *g_view = NULL;
static FestinaWindowDelegate *g_delegate = NULL;
static cairo_surface_t *g_last_backing = NULL;

@implementation FestinaView

- (BOOL)acceptsFirstResponder {
    return YES;
}

/* claude.md #182: AppKit's own buttonNumber is 0-indexed and ordered
 * left/right/middle (0/1/2) -- a genuinely different numbering AND
 * ordering than X11's 1/2/3 left/middle/right, which
 * FestinaWindowEvent's own `button` field standardizes on (see its doc
 * comment in festina_runtime_window.h). mouseDown:/mouseUp: only ever
 * fire for buttonNumber 0 and rightMouseDown:/rightMouseUp: only for
 * buttonNumber 1, so those two call sites hardcode 1/3 directly with
 * no lookup needed; this translation is only for otherMouseDown:/
 * otherMouseUp:, which fire for every buttonNumber 2 and up. Buttons 3
 * and 4 are, empirically, "back"/"forward" on most multi-button mice
 * (there's no OS-level guarantee of this -- Apple documents no fixed
 * meaning past 0/1/2), mapped to X11's own 8/9 for exactly those same
 * two physical buttons; anything past that is genuinely unstandardized
 * anywhere, reported as buttonNumber + 1 on a best-effort basis only. */
static int64_t festina_mac_other_button_number(NSInteger buttonNumber) {
    if (buttonNumber == 2) return 2;   /* middle */
    if (buttonNumber == 3) return 8;   /* back, empirically */
    if (buttonNumber == 4) return 9;   /* forward, empirically */
    return (int64_t)buttonNumber + 1;
}

- (void)festinaMouseButtonEvent:(NSEvent *)event kind:(FestinaWindowEventKind)kind button:(int64_t)button {
    NSPoint p = [self convertPoint:[event locationInWindow] fromView:nil];
    FestinaWindowEvent ev = {0};
    ev.kind = kind;
    ev.x = (int64_t)p.x;
    /* claude.md #37: Festina's own coordinates put y=0 at the TOP, the
     * X11/canvas convention every draw call already uses -- AppKit's
     * view coordinates put y=0 at the BOTTOM, so this is the one place
     * that difference has to be corrected, not scattered across every
     * event kind's own math. */
    ev.y = (int64_t)([self bounds].size.height - p.y);
    ev.button = button;
    festina_mac_push(ev, NULL);
}

- (void)mouseDown:(NSEvent *)event {
    [self festinaMouseButtonEvent:event kind:FESTINA_WEVENT_MOUSE_DOWN button:1];
}

- (void)mouseUp:(NSEvent *)event {
    [self festinaMouseButtonEvent:event kind:FESTINA_WEVENT_MOUSE_UP button:1];
}

- (void)rightMouseDown:(NSEvent *)event {
    [self festinaMouseButtonEvent:event kind:FESTINA_WEVENT_MOUSE_DOWN button:3];
}

- (void)rightMouseUp:(NSEvent *)event {
    [self festinaMouseButtonEvent:event kind:FESTINA_WEVENT_MOUSE_UP button:3];
}

- (void)otherMouseDown:(NSEvent *)event {
    [self festinaMouseButtonEvent:event kind:FESTINA_WEVENT_MOUSE_DOWN
                            button:festina_mac_other_button_number([event buttonNumber])];
}

- (void)otherMouseUp:(NSEvent *)event {
    [self festinaMouseButtonEvent:event kind:FESTINA_WEVENT_MOUSE_UP
                            button:festina_mac_other_button_number([event buttonNumber])];
}

- (void)festinaMouseMoved:(NSEvent *)event {
    NSPoint p = [self convertPoint:[event locationInWindow] fromView:nil];
    FestinaWindowEvent ev = {0};
    ev.kind = FESTINA_WEVENT_MOUSE_MOVE;
    ev.x = (int64_t)p.x;
    ev.y = (int64_t)([self bounds].size.height - p.y);
    festina_mac_push(ev, NULL);
}

- (void)mouseMoved:(NSEvent *)event {
    [self festinaMouseMoved:event];
}

- (void)mouseDragged:(NSEvent *)event {
    /* AppKit delivers mouseDragged: instead of mouseMoved: while a
     * button is held -- both map to the same MOUSE_MOVE event here so
     * a program dragging with the button down still sees continuous
     * `on mouse` firing, matching X11's MotionNotify (which the X11
     * backend reports identically whether or not a button is down). */
    [self festinaMouseMoved:event];
}

- (void)scrollWheel:(NSEvent *)event {
    /* claude.md #181: one call handles both a real scroll wheel and a
     * trackpad's two-finger scroll -- AppKit reports both through the
     * same scrollWheel: method and the same deltaY sign convention
     * (already normalized for the user's own "natural scrolling"
     * preference, so this needs no special-casing for it). Positive
     * deltaY is scroll-up, negative is scroll-down -- exactly
     * mouseWheelUp/mouseWheelDown's own split (see semantic.py's
     * _EVENT_SIGNATURES). A near-zero deltaY (a pure horizontal swipe,
     * or a trackpad's own momentum/inertia phase reporting a residual
     * near-0 value) fires neither -- there's no horizontal wheel event
     * to report it as. */
    CGFloat deltaY = [event deltaY];
    if (deltaY == 0.0) return;
    NSPoint p = [self convertPoint:[event locationInWindow] fromView:nil];
    FestinaWindowEvent ev = {0};
    ev.kind = deltaY > 0 ? FESTINA_WEVENT_MOUSE_WHEEL_UP : FESTINA_WEVENT_MOUSE_WHEEL_DOWN;
    ev.x = (int64_t)p.x;
    ev.y = (int64_t)([self bounds].size.height - p.y);
    festina_mac_push(ev, NULL);
}

- (void)keyDown:(NSEvent *)event {
    /* claude.md #98: a held key auto-repeats through keyDown -- that
     * IS how text entry works, the same allowance the X11 side's own
     * comment states -- and AppKit's own key-repeat mechanism already
     * produces exactly that shape with no filtering needed here. */
    char name[32];
    festina_mac_key_name(event, name, sizeof(name));
    FestinaWindowEvent ev = {0};
    ev.kind = FESTINA_WEVENT_KEY_DOWN;
    festina_mac_push(ev, name);
}

- (void)keyUp:(NSEvent *)event {
    char name[32];
    festina_mac_key_name(event, name, sizeof(name));
    FestinaWindowEvent ev = {0};
    ev.kind = FESTINA_WEVENT_KEY_UP;
    festina_mac_push(ev, name);
}

- (void)drawRect:(NSRect)dirtyRect {
    (void)dirtyRect;
    /* claude.md #123: redraw-on-demand is entirely this backend's own
     * concern -- see festina_runtime_window.h's own note. Repaints
     * from whatever surface festina_window_present last remembered,
     * with no round trip back into shared code at all. */
    if (!g_last_backing) {
        [[NSColor whiteColor] set];
        NSRectFill([self bounds]);
        return;
    }
    /* cairo's CAIRO_FORMAT_ARGB32 is one 32-bit int per pixel, native
     * endian, alpha in the high byte, premultiplied -- on every Mac
     * (little-endian, always) that is byte order B,G,R,A in memory,
     * which is exactly kCGBitmapByteOrder32Little combined with
     * kCGImageAlphaPremultipliedFirst: the standard, well-known
     * cairo/CoreGraphics interop recipe (also how cairo's own Quartz
     * backend reads a CGImage back the other way).
     *
     * cairo_image_surface_get_data() below is a RAW memory read,
     * entirely outside cairo's own drawing API -- unlike the X11
     * backend's own festina_window_present (which reads
     * g_backing_surface only through cairo_set_source_surface+
     * cairo_paint, cairo mediating the read the same way it mediates
     * the write), this reaches straight into the surface's pixel
     * buffer. Cairo's own documented contract for
     * cairo_image_surface_get_data() requires a cairo_surface_flush()
     * first "to ensure that all pending drawing operations are
     * finished" -- drawRect: can fire asynchronously, an arbitrary
     * amount of program logic (and cairo drawing) after whatever last
     * touched this exact surface, so the flush belongs HERE, at the
     * point of the raw read, not back in festina_window_present at
     * present() time. Missing this is exactly the shape of bug that
     * reads back correct dimensions (the header fields cairo always
     * keeps current) but stale or blank pixel content until some
     * unrelated later draw call happens to trigger a flush of its
     * own. */
    cairo_surface_flush(g_last_backing);
    int width = cairo_image_surface_get_width(g_last_backing);
    int height = cairo_image_surface_get_height(g_last_backing);
    int stride = cairo_image_surface_get_stride(g_last_backing);
    unsigned char *data = cairo_image_surface_get_data(g_last_backing);

    CGColorSpaceRef space = CGColorSpaceCreateDeviceRGB();
    CGDataProviderRef provider = CGDataProviderCreateWithData(
        NULL, data, (unsigned long)(stride * height), NULL);
    CGImageRef image = CGImageCreate(
        (unsigned long)width, (unsigned long)height, 8, 32, (unsigned long)stride,
        space, kCGBitmapByteOrder32Little | kCGImageAlphaPremultipliedFirst,
        provider, NULL, 0, kCGRenderingIntentDefault);

    CGContextRef cg = [[NSGraphicsContext currentContext] CGContext];
    NSRect target;
    target.origin.x = 0;
    target.origin.y = 0;
    target.size.width = width;
    target.size.height = height;
    CGContextDrawImage(cg, target, image);

    CGImageRelease(image);
    CGDataProviderRelease(provider);
    CGColorSpaceRelease(space);
}

@end

@implementation FestinaWindowDelegate

- (BOOL)windowShouldClose:(NSWindow *)sender {
    (void)sender;
    /* claude.md #123: matches X11's own WM_DELETE_WINDOW flow exactly
     * -- push CLOSE and let shared code (festina_run_event_loop,
     * festina_runtime_graphics.c) decide, via `on close` and then
     * festina_window_close(), rather than letting AppKit tear the
     * window down on its own first. */
    FestinaWindowEvent ev = {0};
    ev.kind = FESTINA_WEVENT_CLOSE;
    festina_mac_push(ev, NULL);
    return NO;
}

- (void)windowDidResize:(NSNotification *)notification {
    (void)notification;
    NSRect frame = [g_view frame];
    FestinaWindowEvent ev = {0};
    ev.kind = FESTINA_WEVENT_RESIZE;
    ev.width = (int64_t)frame.size.width;
    ev.height = (int64_t)frame.size.height;
    festina_mac_push(ev, NULL);
}

@end

void festina_window_open(int64_t width, int64_t height, const char *title) {
    @autoreleasepool {
        [NSApplication sharedApplication];
        [NSApp setActivationPolicy:NSApplicationActivationPolicyRegular];

        NSRect rect;
        rect.origin.x = 0;
        rect.origin.y = 0;
        rect.size.width = (CGFloat)width;
        rect.size.height = (CGFloat)height;
        /* claude.md #180: a real, decorated window -- title bar, and
         * the standard close/miniaturize/zoom buttons -- rather than
         * the borderless "canvas, nothing else" look
         * NSWindowStyleMaskBorderless used to request. Resizable too,
         * since a normal window's own zoom button and drag-to-resize
         * both need it -- initWithContentRect: computes the outer frame
         * FROM this content rect regardless of styleMask, so `rect`
         * here still means exactly what it always did: the canvas's
         * own content size, no adjustment needed the way the Win32
         * backend's own CreateWindowExW/SetWindowPos calls now need
         * (see that file's own comment). */
        g_window = [[NSWindow alloc] initWithContentRect:rect
                                                styleMask:NSWindowStyleMaskTitled
                                                    | NSWindowStyleMaskClosable
                                                    | NSWindowStyleMaskMiniaturizable
                                                    | NSWindowStyleMaskResizable
                                                  backing:NSBackingStoreBuffered
                                                    defer:NO];
        if (!g_window) {
            festina_fail("could not create a macOS window -- claude.md #39's "
                          "graphics functions need a running window server");
        }
        /* claude.md #180: opts this window into AppKit's own native
         * fullscreen (the green zoom button's Option-click / menu-bar
         * "Enter Full Screen" affordance, and what festina_window_
         * set_fullscreen's [g_window toggleFullScreen:nil] below relies
         * on) -- a window has to explicitly claim this behavior, it is
         * not implied by NSWindowStyleMaskResizable alone. */
        [g_window setCollectionBehavior:NSWindowCollectionBehaviorFullScreenPrimary];
        g_view = [[FestinaView alloc] initWithFrame:rect];
        /* claude.md #180: the view now needs to actually track the
         * window's content size as it changes -- previously true only
         * via festina_window_resize's own explicit setContentSize:
         * call, but a titled+resizable window can ALSO change size
         * from the user dragging an edge or clicking zoom/fullscreen,
         * none of which go through that function at all. Without this,
         * windowDidResize: below (which reports whatever size [g_view
         * frame] happens to be) would report a stale size after any
         * such user-driven resize -- the view's frame does not follow
         * the window's content size on its own by default. */
        [g_view setAutoresizingMask:NSViewWidthSizable | NSViewHeightSizable];
        NSTrackingArea *tracking = [[NSTrackingArea alloc]
            initWithRect:rect
                 options:NSTrackingMouseMoved | NSTrackingActiveAlways | NSTrackingInVisibleRect
                   owner:g_view
                userInfo:NULL];
        [g_view addTrackingArea:tracking];

        g_delegate = [[FestinaWindowDelegate alloc] init];
        [g_window setDelegate:g_delegate];
        [g_window setContentView:g_view];
        [g_window setTitle:[NSString stringWithUTF8String:title]];
        [g_window makeFirstResponder:g_view];
        [g_window makeKeyAndOrderFront:nil];
        [NSApp activateIgnoringOtherApps:YES];

        g_last_backing = NULL;
        g_pending_head = 0;
        g_pending_count = 0;
    }
}

void festina_window_close(void) {
    @autoreleasepool {
        [g_window orderOut:nil];
        [g_window close];
        g_window = NULL;
        g_view = NULL;
        g_delegate = NULL;
        g_last_backing = NULL;
    }
}

void festina_window_present(cairo_surface_t *backing) {
    @autoreleasepool {
        g_last_backing = backing;
        [g_view setNeedsDisplay:YES];
        /* Synchronous, matching X11's own XFlush-after-paint semantics
         * -- present() takes effect essentially immediately rather
         * than waiting for AppKit's next natural display pass. */
        [g_view displayIfNeeded];
    }
}

void festina_window_events_wait(double timeout_seconds) {
    @autoreleasepool {
        NSDate *until = timeout_seconds < 0.0
            ? [NSDate distantFuture]
            : [NSDate dateWithTimeIntervalSinceNow:timeout_seconds];
        /* dequeue:NO -- this only blocks until something is available
         * (or the deadline passes); festina_window_events_drain does
         * the actual dequeue+dispatch, exactly the wait/drain split
         * festina_runtime_window.h's own doc comment describes. */
        [NSApp nextEventMatchingMask:NSEventMaskAny
                            untilDate:until
                               inMode:NSDefaultRunLoopMode
                              dequeue:NO];
    }
}

void festina_window_events_drain(void (*handler)(const FestinaWindowEvent *event)) {
    @autoreleasepool {
        NSEvent *event;
        /* Stage 1: pump AppKit's OWN queue fully. This is what
         * actually drives mouseDown:/keyDown:/windowDidResize:/
         * windowShouldClose: on our view/delegate -- Cocoa event
         * handling is push-based, not a flat stream this function can
         * translate directly the way Xlib's XNextEvent loop can. */
        while ((event = [NSApp nextEventMatchingMask:NSEventMaskAny
                                             untilDate:[NSDate distantPast]
                                                inMode:NSDefaultRunLoopMode
                                               dequeue:YES])) {
            [NSApp sendEvent:event];
        }
        [NSApp updateWindows];

        /* Stage 2: drain whatever those callbacks queued. */
        FestinaWindowEvent ev;
        while (festina_mac_pop(&ev)) {
            handler(&ev);
        }
    }
}

/* claude.md #139: screenWidth/screenHeight and setClientWidth/
 * setClientHeight's macOS half of the seam -- see
 * festina_runtime_window.h's own doc comment for what each function
 * is responsible for. Built the same way the rest of this file's
 * Cocoa backend was: real X11 code ported to the equivalent Cocoa
 * call, unverified on real macOS hardware yet (see macos.md's own
 * "verify later on real hardware" note, which this inherits). */
void festina_window_screen_size(int64_t *out_width, int64_t *out_height) {
    @autoreleasepool {
        /* mainScreen -- the screen actually holding the active window,
         * or the system's designated primary screen if nothing is key
         * yet, matching the "just tell me the display's resolution"
         * intent this call exists for. Reported in POINTS, not backing
         * pixels, consistent with every other size this backend already
         * hands to/from Festina code (festina_window_open's own width/
         * height are points too, and this backend does no actual
         * backing-pixel-resolution rendering anywhere -- claude.md
         * #181's devicePixelRatio below is purely informational), so a
         * Retina display's doubled pixel density doesn't leak through
         * as a mismatched unit here. */
        NSScreen *screen = [NSScreen mainScreen];
        if (!screen) {
            *out_width = 0;
            *out_height = 0;
            return;
        }
        NSRect frame = [screen frame];
        *out_width = (int64_t)frame.size.width;
        *out_height = (int64_t)frame.size.height;
    }
}

/* claude.md #181: devicePixelRatio -- backingScaleFactor is exactly
 * this ratio already (2.0 on Retina, 1.0 otherwise), the one place in
 * this file that reads it -- everything else stays in POINTS (see
 * festina_window_screen_size's own comment just above), this call
 * included; it just also reports the multiplier a program would need
 * to convert those points to actual device pixels, same "mainScreen,
 * or nothing key yet" fallback shape. */
double festina_window_device_pixel_ratio(void) {
    @autoreleasepool {
        NSScreen *screen = [NSScreen mainScreen];
        if (!screen) return 1.0;
        return (double)[screen backingScaleFactor];
    }
}

void festina_window_resize(int64_t width, int64_t height) {
    @autoreleasepool {
        if (!g_window) return;
        NSSize size;
        size.width = (CGFloat)width;
        size.height = (CGFloat)height;
        /* display:YES -- forces the resize (and the windowDidResize:
         * delegate callback it triggers, which is what actually
         * produces the RESIZE event -- see the delegate method above)
         * to take effect synchronously here, rather than coalescing
         * into AppKit's next natural display pass, matching the
         * synchronous XFlush-after-resize behavior the X11
         * implementation of this same function already has. */
        [g_window setContentSize:size];
        [g_window displayIfNeeded];
    }
}

/* claude.md #180: AppKit's own native fullscreen -- the same feature
 * the green zoom button's Option-click, or the Full Screen menu item,
 * already offer any titled window opted into it (see
 * NSWindowCollectionBehaviorFullScreenPrimary in festina_window_open
 * above). toggleFullScreen: is AppKit's ENTIRE API surface for this --
 * there is no separate enterFullScreen/exitFullScreen selector pair to
 * call directly, so this checks the window's OWN current styleMask
 * (rather than trusting a separately-tracked flag that could drift out
 * of sync with it, e.g. if the user exits fullscreen via Escape or the
 * menu rather than through Festina's own exitFullscreen()) and only
 * toggles when that disagrees with what's being asked for -- the same
 * "no-op if already in the requested state" contract every other
 * backend's own festina_window_set_fullscreen already has. The resize
 * this produces reaches windowDidResize: (see the delegate above) the
 * same way any other AppKit-driven resize does, once the (animated)
 * transition completes. */
void festina_window_set_fullscreen(int8_t fullscreen) {
    @autoreleasepool {
        if (!g_window) return;
        BOOL is_full = ([g_window styleMask] & NSWindowStyleMaskFullScreen) != 0;
        BOOL want_full = fullscreen != 0;
        if (is_full != want_full) {
            [g_window toggleFullScreen:nil];
        }
    }
}

/* claude.md #182: [NSCursor hide]/[NSCursor unhide] are AppKit's own
 * direct API for this -- unlike X11 (which needs the manual invisible-
 * cursor workaround, see that backend's own comment) or Win32 (whose
 * ShowCursor is itself counter-based). NSCursor's hide/unhide ARE also
 * counter-based internally, but that's harmless here specifically
 * because the only caller, festina_hide_cursor/festina_show_cursor in
 * festina_runtime_graphics.c, already guards against a redundant call
 * with the identical state (see g_cursor_visible's own comment) -- so
 * this is only ever reached on a genuine state transition, one hide
 * per one unhide, never imbalanced. */
void festina_window_set_cursor_visible(int8_t visible) {
    @autoreleasepool {
        if (visible) {
            [NSCursor unhide];
        } else {
            [NSCursor hide];
        }
    }
}
