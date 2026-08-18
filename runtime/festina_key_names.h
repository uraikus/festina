/* The cross-platform key-name vocabulary -- macos.md / windows.md
 * Phase 2's "key-name parity" contract, pinned as an artifact rather
 * than prose so every windowing backend compiles against the same
 * list and tests/test_platform.py can verify it mechanically.
 *
 * The rule `on keyDown(key:text)` / `on keyUp(key:text)` follow, on
 * every platform, is two-tiered (established by the X11 backend's
 * festina_key_name -- see festina_runtime_graphics.c):
 *
 *   1. A key that types a printable character arrives as exactly that
 *      character, modifier-aware ("a", "A", "7", "?", " " for the
 *      space bar). These are unbounded and deliberately NOT listed
 *      here.
 *   2. Every other key arrives as a NAME from the list below. The
 *      names are X11's keysym strings, verbatim -- not because they
 *      are pretty, but because they are what existing Festina
 *      programs already match against, so the other platforms map to
 *      THEM, warts included: note "Prior"/"Next" for Page Up/Page
 *      Down (measured from XKeysymToString, not assumed) and the
 *      _L/_R modifier suffixes.
 *
 * A future macOS (NSEvent) or Windows (virtual-key code) windowing
 * layer implements its key mapping as a table from the platform's
 * codes into these strings, and MUST NOT invent a name that is absent
 * here -- adding a key every platform can report is done by adding it
 * here first. The X macro shape lets a backend expand the list into
 * whatever table form it needs. */
#ifndef FESTINA_KEY_NAMES_H
#define FESTINA_KEY_NAMES_H

#define FESTINA_NAMED_KEYS(X) \
    X("Return") \
    X("Escape") \
    X("BackSpace") \
    X("Tab") \
    X("Delete") \
    X("Insert") \
    X("Left") \
    X("Right") \
    X("Up") \
    X("Down") \
    X("Home") \
    X("End") \
    X("Prior")   /* Page Up -- X11's historical name, kept for compatibility */ \
    X("Next")    /* Page Down -- same */ \
    X("F1") \
    X("F2") \
    X("F3") \
    X("F4") \
    X("F5") \
    X("F6") \
    X("F7") \
    X("F8") \
    X("F9") \
    X("F10") \
    X("F11") \
    X("F12") \
    X("Shift_L") \
    X("Shift_R") \
    X("Control_L") \
    X("Control_R") \
    X("Alt_L") \
    X("Alt_R") \
    X("Super_L") \
    X("Super_R") \
    X("Caps_Lock") \
    X("Num_Lock") \
    X("Menu") \
    X("Pause") \
    X("Print")

#endif /* FESTINA_KEY_NAMES_H */
