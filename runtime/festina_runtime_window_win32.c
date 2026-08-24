/*
 * Festina native runtime -- Windows windowing backend: claude.md #128 /
 * windows.md Phase 2. Implements the five functions
 * festina_runtime_window.h declares (festina_window_open/close/
 * present/events_wait/events_drain) with plain Win32 (RegisterClassEx/
 * CreateWindowEx/GDI), the counterpart to festina_runtime_graphics.c's
 * X11 implementation (guarded `#if !defined(__APPLE__) && !defined(_WIN32)`
 * there) and festina_runtime_window_mac.m's Cocoa one. Unlike Cocoa,
 * Win32 is plain C -- no separate translation unit or `.m` extension
 * needed, just an ordinary `.c` file wired in by festina/cli.py's
 * per-platform graphics config.
 *
 * The event model mirrors the Cocoa backend, not the X11 one: Win32
 * delivers input via a WNDPROC callback DispatchMessage invokes
 * synchronously, not a flat stream festina_window_events_drain could
 * translate directly the way Xlib's XNextEvent loop can (X11 is the
 * outlier here, not Win32 or Cocoa). So WndProc pushes normalized
 * events into the same small ring-buffer shape the Cocoa backend uses,
 * and festina_window_events_drain pumps PeekMessage/TranslateMessage/
 * DispatchMessage (which is what actually calls WndProc) before
 * draining whatever that pump produced.
 *
 * NOTE (windows.md Phase 2): compiled and type-checked by every windows
 * CI run (see .github/workflows/ci.yml), exactly like Phase 1's waveOut
 * backend was before its own hardware verification -- but not yet
 * confirmed against a real window, mouse and keyboard on real hardware.
 * festina/cli.py gates windowed graphics behind
 * FESTINA_ENABLE_WINDOWS_GRAPHICS=1 for exactly that reason, mirroring
 * the darwin graphics gate precedent (claude.md #123) precisely. Two
 * pieces are best-effort pending that pass, called out at their own
 * sites below: the virtual-key -> runtime/festina_key_names.h mapping
 * table, and the left/right Shift/Control/Alt disambiguation.
 */
#include <windows.h>
#include <stdio.h>
#include <string.h>
/* Bare <cairo.h>, not <cairo/cairo.h> -- same fix as
 * festina_runtime_window.h and festina_runtime_window_mac.m needed,
 * for the same reason: pkg-config's Cflags for MSYS2's `cairo` package
 * point -I directly at the cairo headers directory itself, so this is
 * the spelling that resolves via the explicit -I flag rather than an
 * implicit default search path (claude.md #126). */
#include <cairo.h>
#include "festina_runtime.h"          /* festina_fail */
#include "festina_runtime_window.h"

/* ---- the small event queue WndProc's push-based callback feeds -- see
 * this file's own top comment for why a queue, not a flat translated
 * stream, is the right shape here (same reasoning as the Cocoa
 * backend's identical queue). */
#define FESTINA_WIN32_EVENT_QUEUE_CAP 256
static FestinaWindowEvent g_pending[FESTINA_WIN32_EVENT_QUEUE_CAP];
static char g_pending_key_name[FESTINA_WIN32_EVENT_QUEUE_CAP][32];
static int g_pending_head = 0;
static int g_pending_count = 0;

static void festina_win32_push(FestinaWindowEvent ev, const char *key_name) {
    if (g_pending_count >= FESTINA_WIN32_EVENT_QUEUE_CAP) {
        /* Same overrun handling as the Cocoa backend: drop the oldest
         * queued event rather than grow unbounded or crash. A real
         * Festina program calls festina_run_event_loop() in a tight
         * cycle specifically so this never matters in practice. */
        g_pending_head = (g_pending_head + 1) % FESTINA_WIN32_EVENT_QUEUE_CAP;
        g_pending_count--;
    }
    int slot = (g_pending_head + g_pending_count) % FESTINA_WIN32_EVENT_QUEUE_CAP;
    if (key_name) {
        snprintf(g_pending_key_name[slot], sizeof(g_pending_key_name[slot]), "%s", key_name);
        ev.key_name = g_pending_key_name[slot];
    }
    g_pending[slot] = ev;
    g_pending_count++;
}

static int festina_win32_pop(FestinaWindowEvent *out) {
    if (g_pending_count == 0) return 0;
    *out = g_pending[g_pending_head];
    g_pending_head = (g_pending_head + 1) % FESTINA_WIN32_EVENT_QUEUE_CAP;
    g_pending_count--;
    return 1;
}

/* claude.md #40's key NAME, shared by keyDown and keyUp exactly like
 * the X11 backend's festina_x11_key_name and the Cocoa backend's
 * festina_mac_key_name -- see either's comment for the two-tier rule
 * this mirrors a third time.
 *
 * Unlike X11 (XLookupString) and Cocoa (charactersIgnoringModifiers),
 * Win32 does not hand the printable character to WM_KEYDOWN/WM_KEYUP
 * directly -- that normally arrives later as a separate WM_CHAR
 * message, generated only for the down half, which would make keyUp
 * unable to report the same text a matching keyDown did. ToUnicode
 * sidesteps that: given the virtual-key code, its scancode, and the
 * current keyboard state, it computes the same shift-aware character
 * WM_CHAR would have delivered, synchronously, for both halves of a
 * press -- so this file needs no separate WM_CHAR handler at all. Its
 * documented side effect on dead-key composition state is an accepted
 * simplification here, the same "no non-ASCII keyboard layout" scope
 * limit the Cocoa backend's own comment already states for itself. */
static void festina_win32_key_name(WPARAM vk, LPARAM lParam, char *out, size_t out_size) {
    BYTE state[256];
    GetKeyboardState(state);
    WCHAR buf[8];
    UINT scancode = (UINT)((lParam >> 16) & 0xFF);
    int len = ToUnicode((UINT)vk, scancode, state, buf, 8, 0);
    if (len == 1 && buf[0] >= 0x20 && buf[0] != 0x7F && buf[0] < 0x80) {
        out[0] = (char)buf[0];
        out[1] = '\0';
        return;
    }
    const char *name = "";
    switch (vk) {
        case VK_RETURN:    name = "Return"; break;
        case VK_ESCAPE:    name = "Escape"; break;
        case VK_BACK:      name = "BackSpace"; break;
        case VK_TAB:       name = "Tab"; break;
        case VK_DELETE:    name = "Delete"; break;
        case VK_INSERT:    name = "Insert"; break;
        case VK_LEFT:      name = "Left"; break;
        case VK_RIGHT:     name = "Right"; break;
        case VK_UP:        name = "Up"; break;
        case VK_DOWN:      name = "Down"; break;
        case VK_HOME:      name = "Home"; break;
        case VK_END:       name = "End"; break;
        case VK_PRIOR:     name = "Prior"; break;    /* Page Up */
        case VK_NEXT:      name = "Next"; break;     /* Page Down */
        case VK_F1:        name = "F1"; break;
        case VK_F2:        name = "F2"; break;
        case VK_F3:        name = "F3"; break;
        case VK_F4:        name = "F4"; break;
        case VK_F5:        name = "F5"; break;
        case VK_F6:        name = "F6"; break;
        case VK_F7:        name = "F7"; break;
        case VK_F8:        name = "F8"; break;
        case VK_F9:        name = "F9"; break;
        case VK_F10:       name = "F10"; break;
        case VK_F11:       name = "F11"; break;
        case VK_F12:       name = "F12"; break;
        case VK_LSHIFT:    name = "Shift_L"; break;
        case VK_RSHIFT:    name = "Shift_R"; break;
        case VK_LCONTROL:  name = "Control_L"; break;
        case VK_RCONTROL:  name = "Control_R"; break;
        case VK_LMENU:     name = "Alt_L"; break;
        case VK_RMENU:     name = "Alt_R"; break;
        case VK_LWIN:      name = "Super_L"; break;
        case VK_RWIN:      name = "Super_R"; break;
        case VK_CAPITAL:   name = "Caps_Lock"; break;
        case VK_NUMLOCK:   name = "Num_Lock"; break;
        case VK_APPS:      name = "Menu"; break;
        case VK_PAUSE:     name = "Pause"; break;
        case VK_SNAPSHOT:  name = "Print"; break;
        default: break;
    }
    snprintf(out, out_size, "%s", name);
}

/* WM_KEYDOWN/WM_KEYUP report the GENERIC VK_SHIFT/VK_CONTROL/VK_MENU
 * for either the left or right key -- distinguishing them needs the
 * lParam scancode (extended-key bit 24 tells left from right Control
 * and Alt directly; Shift has no extended bit at all and needs the
 * scancode remapped through MapVirtualKey instead). This is the
 * standard, well-documented Win32 technique for this exact problem,
 * not a Festina-specific guess -- but see this file's own top comment:
 * it is one of the two pieces still awaiting real-hardware
 * verification, since only real keyboard hardware can confirm the
 * scancodes it exercises. */
static WPARAM festina_win32_left_right_vk(WPARAM vk, LPARAM lParam) {
    UINT scancode = (UINT)((lParam >> 16) & 0xFF);
    int extended = (lParam & 0x01000000) != 0;
    switch (vk) {
        case VK_SHIFT:
            return MapVirtualKey(scancode, MAPVK_VSC_TO_VK_EX);
        case VK_CONTROL:
            return extended ? VK_RCONTROL : VK_LCONTROL;
        case VK_MENU:
            return extended ? VK_RMENU : VK_LMENU;
        default:
            return vk;
    }
}

static const wchar_t *FESTINA_WIN32_CLASS_NAME = L"FestinaWindowClass";

static HWND g_hwnd = NULL;
static cairo_surface_t *g_last_backing = NULL;

/* claude.md #123: redraw-on-demand is entirely this backend's own
 * concern -- see festina_runtime_window.h's own note. Repaints from
 * whatever surface festina_window_present last remembered, with no
 * round trip back into shared code at all -- same as the X11 Expose
 * handler and the Cocoa drawRect: override.
 *
 * windows.md Phase 2's own design note: cairo's CAIRO_FORMAT_ARGB32 is
 * one 32-bit int per pixel, native-endian, alpha in the high byte,
 * premultiplied -- on little-endian Windows that is byte order
 * B,G,R,A in memory, which is exactly a 32bpp top-down Windows DIB
 * (BI_RGB, biBitCount 32, biHeight negative). StretchDIBits needs no
 * cairo-win32 backend at all, just this one well-known interop
 * recipe -- the same blit shape the Mac backend's CGImage path uses on
 * purpose, per festina_runtime_window.h's own doc comment. A 32bpp
 * DIB's scanline stride is always exactly width*4 bytes (already
 * 4-byte aligned, the one alignment DIBs require), which is also
 * cairo's own ARGB32 stride for every width -- so no separate stride
 * parameter is needed here the way the CGImage path's DataProvider
 * needed one. */
static void festina_win32_paint(HWND hwnd) {
    PAINTSTRUCT ps;
    HDC hdc = BeginPaint(hwnd, &ps);
    if (!g_last_backing) {
        RECT r;
        GetClientRect(hwnd, &r);
        FillRect(hdc, &r, (HBRUSH)(COLOR_WINDOW + 1));
        EndPaint(hwnd, &ps);
        return;
    }
    int width = cairo_image_surface_get_width(g_last_backing);
    int height = cairo_image_surface_get_height(g_last_backing);
    unsigned char *data = cairo_image_surface_get_data(g_last_backing);

    BITMAPINFO bmi;
    memset(&bmi, 0, sizeof(bmi));
    bmi.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
    bmi.bmiHeader.biWidth = width;
    bmi.bmiHeader.biHeight = -height;   /* negative: top-down, matching cairo's own row order */
    bmi.bmiHeader.biPlanes = 1;
    bmi.bmiHeader.biBitCount = 32;
    bmi.bmiHeader.biCompression = BI_RGB;

    StretchDIBits(hdc, 0, 0, width, height, 0, 0, width, height,
                  data, &bmi, DIB_RGB_COLORS, SRCCOPY);
    EndPaint(hwnd, &ps);
}

static LRESULT CALLBACK festina_win32_wndproc(HWND hwnd, UINT msg, WPARAM wparam, LPARAM lparam) {
    FestinaWindowEvent ev;
    switch (msg) {
    case WM_PAINT:
        festina_win32_paint(hwnd);
        return 0;
    case WM_LBUTTONDOWN:
        memset(&ev, 0, sizeof(ev));
        ev.kind = FESTINA_WEVENT_MOUSE_DOWN;
        /* Client-area coordinates, top-left origin -- Win32's own
         * convention already matches Festina's (claude.md #37), unlike
         * Cocoa's bottom-left AppKit view coordinates, so no flip is
         * needed here the way the Cocoa backend's mouseDown: needs one. */
        ev.x = (short)LOWORD(lparam);
        ev.y = (short)HIWORD(lparam);
        festina_win32_push(ev, NULL);
        return 0;
    case WM_LBUTTONUP:
        memset(&ev, 0, sizeof(ev));
        ev.kind = FESTINA_WEVENT_MOUSE_UP;
        ev.x = (short)LOWORD(lparam);
        ev.y = (short)HIWORD(lparam);
        festina_win32_push(ev, NULL);
        return 0;
    case WM_MOUSEMOVE:
        memset(&ev, 0, sizeof(ev));
        ev.kind = FESTINA_WEVENT_MOUSE_MOVE;
        ev.x = (short)LOWORD(lparam);
        ev.y = (short)HIWORD(lparam);
        festina_win32_push(ev, NULL);
        return 0;
    case WM_KEYDOWN:
    case WM_SYSKEYDOWN: {
        /* claude.md #98: WM_KEYDOWN repeats natively while a key is
         * held (bit 30 of lParam distinguishes a repeat from the
         * initial press, unused here since a program wants exactly
         * this repeating shape for text entry -- the same allowance
         * the X11 and Cocoa backends' own comments state). */
        WPARAM vk = festina_win32_left_right_vk(wparam, lparam);
        char name[32];
        festina_win32_key_name(vk, lparam, name, sizeof(name));
        memset(&ev, 0, sizeof(ev));
        ev.kind = FESTINA_WEVENT_KEY_DOWN;
        festina_win32_push(ev, name);
        return 0;
    }
    case WM_KEYUP:
    case WM_SYSKEYUP: {
        WPARAM vk = festina_win32_left_right_vk(wparam, lparam);
        char name[32];
        festina_win32_key_name(vk, lparam, name, sizeof(name));
        memset(&ev, 0, sizeof(ev));
        ev.kind = FESTINA_WEVENT_KEY_UP;
        festina_win32_push(ev, name);
        return 0;
    }
    case WM_SIZE: {
        int64_t new_w = LOWORD(lparam);
        int64_t new_h = HIWORD(lparam);
        memset(&ev, 0, sizeof(ev));
        ev.kind = FESTINA_WEVENT_RESIZE;
        ev.width = new_w;
        ev.height = new_h;
        festina_win32_push(ev, NULL);
        return 0;
    }
    case WM_CLOSE:
        /* claude.md #123: matches X11's WM_DELETE_WINDOW and Cocoa's
         * windowShouldClose: exactly -- push CLOSE and let shared code
         * (festina_run_event_loop, festina_runtime_graphics.c) decide,
         * via `on close` and then festina_window_close(), rather than
         * letting DefWindowProc destroy the window on its own first.
         * Returning 0 without calling DefWindowProc is what suppresses
         * that default destroy. */
        memset(&ev, 0, sizeof(ev));
        ev.kind = FESTINA_WEVENT_CLOSE;
        festina_win32_push(ev, NULL);
        return 0;
    default:
        return DefWindowProcW(hwnd, msg, wparam, lparam);
    }
}

void festina_window_open(int64_t width, int64_t height, const char *title) {
    HINSTANCE instance = GetModuleHandleW(NULL);

    WNDCLASSEXW wc;
    memset(&wc, 0, sizeof(wc));
    wc.cbSize = sizeof(wc);
    wc.lpfnWndProc = festina_win32_wndproc;
    wc.hInstance = instance;
    wc.hCursor = LoadCursorW(NULL, (LPCWSTR)IDC_ARROW);
    wc.lpszClassName = FESTINA_WIN32_CLASS_NAME;
    /* RegisterClassExW is idempotent to call more than once in the
     * same process (a program that opens, closes, then reopens a
     * window) only in the sense that the second call fails with
     * ERROR_CLASS_ALREADY_EXISTS -- harmless, so its return value is
     * deliberately ignored rather than treated as fatal. */
    RegisterClassExW(&wc);

    /* WS_POPUP: a plain borderless window, no title bar/border/system
     * menu -- the same "canvas, nothing else" look the X11 backend
     * requests via the Motif no-decorations hint and the Cocoa backend
     * requests via NSWindowStyleMaskBorderless. With no border, the
     * requested width/height IS the client size, matching both other
     * backends' own convention of passing content size directly. */
    int wtitle_len = MultiByteToWideChar(CP_UTF8, 0, title, -1, NULL, 0);
    wchar_t *wtitle = malloc((size_t)wtitle_len * sizeof(wchar_t));
    if (wtitle) MultiByteToWideChar(CP_UTF8, 0, title, -1, wtitle, wtitle_len);

    g_hwnd = CreateWindowExW(0, FESTINA_WIN32_CLASS_NAME, wtitle ? wtitle : L"Festina",
                              WS_POPUP, CW_USEDEFAULT, CW_USEDEFAULT,
                              (int)width, (int)height, NULL, NULL, instance, NULL);
    free(wtitle);
    if (!g_hwnd) {
        festina_fail("could not create a Windows window -- claude.md #39's "
                      "graphics functions need a running window session");
    }

    g_last_backing = NULL;
    g_pending_head = 0;
    g_pending_count = 0;

    ShowWindow(g_hwnd, SW_SHOW);
    UpdateWindow(g_hwnd);
    SetForegroundWindow(g_hwnd);
    SetFocus(g_hwnd);
}

void festina_window_close(void) {
    if (g_hwnd) {
        DestroyWindow(g_hwnd);
        g_hwnd = NULL;
    }
    g_last_backing = NULL;
}

void festina_window_present(cairo_surface_t *backing) {
    g_last_backing = backing;
    /* InvalidateRect + UpdateWindow rather than waiting for the next
     * natural WM_PAINT: present() must take effect essentially
     * immediately, matching the X11 backend's XFlush-after-paint and
     * the Cocoa backend's displayIfNeeded, both synchronous for the
     * same reason. */
    InvalidateRect(g_hwnd, NULL, FALSE);
    UpdateWindow(g_hwnd);
}

void festina_window_events_wait(double timeout_seconds) {
    /* MsgWaitForMultipleObjects with zero handles is the documented
     * way to wait on the calling thread's own message queue alone --
     * the precise Win32 analog of the X11 backend's select() on
     * ConnectionNumber(g_display) and the Cocoa backend's
     * nextEventMatchingMask:...dequeue:NO, all three a peek-with-
     * timeout that leaves the actual dequeue to events_drain. */
    DWORD timeout_ms = timeout_seconds < 0.0 ? INFINITE : (DWORD)(timeout_seconds * 1000.0);
    MsgWaitForMultipleObjects(0, NULL, FALSE, timeout_ms, QS_ALLINPUT);
}

void festina_window_events_drain(void (*handler)(const FestinaWindowEvent *event)) {
    /* Stage 1: pump every pending message. DispatchMessage is what
     * actually calls festina_win32_wndproc -- Win32 input handling is
     * push-based via WndProc, not a flat stream this function can
     * translate directly the way Xlib's XNextEvent loop can (see this
     * file's own top comment). */
    MSG msg;
    while (PeekMessageW(&msg, NULL, 0, 0, PM_REMOVE)) {
        TranslateMessage(&msg);
        DispatchMessageW(&msg);
    }

    /* Stage 2: drain whatever that pump produced. */
    FestinaWindowEvent ev;
    while (festina_win32_pop(&ev)) {
        handler(&ev);
    }
}

/* claude.md #139: screenWidth/screenHeight and setClientWidth/
 * setClientHeight's Win32 half of the seam -- see
 * festina_runtime_window.h's own doc comment for what each function is
 * responsible for. Built the same way the rest of this file's Win32
 * backend was: real X11 code ported to the equivalent Win32 call,
 * unverified on real Windows hardware yet (see windows.md's own
 * "verify later on real hardware" note, which this inherits). */
void festina_window_screen_size(int64_t *out_width, int64_t *out_height) {
    /* SM_CXSCREEN/SM_CYSCREEN -- the primary display's resolution,
     * needing no window or display connection to query (unlike every
     * other seam function in this file, all of which require g_hwnd),
     * matching festina_window_screen_size's own contract that it must
     * answer even before any window has ever been opened. */
    *out_width = GetSystemMetrics(SM_CXSCREEN);
    *out_height = GetSystemMetrics(SM_CYSCREEN);
}

void festina_window_resize(int64_t width, int64_t height) {
    if (!g_hwnd) return;
    /* SWP_NOMOVE | SWP_NOZORDER: change only the size, leaving the
     * window's position and stacking order untouched -- x/y are
     * ignored by Win32 when SWP_NOMOVE is set, so 0/0 below is a
     * don't-care placeholder, not an actual move to the origin. With
     * WS_POPUP (no border, see festina_window_open's own comment on
     * why), the window's outer size IS its client size, so `width`/
     * `height` here need no adjustment the way a bordered window's
     * AdjustWindowRect would require. */
    SetWindowPos(g_hwnd, NULL, 0, 0, (int)width, (int)height,
                 SWP_NOMOVE | SWP_NOZORDER);
}
