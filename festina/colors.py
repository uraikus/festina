"""claude.md #90: compile-time resolution of colour and font literals.

`fillStyle('red')` and `font('arial 14px bold')` are written as text
because that is what reads well in source, but neither needs to *stay*
text: both are fully knowable at compile time whenever they're written
as a literal, which is essentially always. This module does that
resolution, so codegen can emit a call taking plain numbers instead of a
pointer to a string the runtime would have to parse on every single
draw -- "compile-time work over runtime work", from the project's own
design principles, applied to the one part of the graphics API that was
still doing string work at runtime.

Keeping the table here rather than in C also means there is exactly one
copy of it. The C runtime has no colour names and no font grammar at
all now; it takes the numbers this module produces.
"""

# The full CSS Color Module Level 4 named-colour set: the 147 extended
# colour keywords inherited from X11, plus `rebeccapurple`. Stored as
# hex strings because that is the form every reference table publishes,
# so this list can be checked against one by eye without arithmetic.
CSS_COLORS = {
    "aliceblue": "f0f8ff", "antiquewhite": "faebd7", "aqua": "00ffff",
    "aquamarine": "7fffd4", "azure": "f0ffff", "beige": "f5f5dc",
    "bisque": "ffe4c4", "black": "000000", "blanchedalmond": "ffebcd",
    "blue": "0000ff", "blueviolet": "8a2be2", "brown": "a52a2a",
    "burlywood": "deb887", "cadetblue": "5f9ea0", "chartreuse": "7fff00",
    "chocolate": "d2691e", "coral": "ff7f50", "cornflowerblue": "6495ed",
    "cornsilk": "fff8dc", "crimson": "dc143c", "cyan": "00ffff",
    "darkblue": "00008b", "darkcyan": "008b8b", "darkgoldenrod": "b8860b",
    "darkgray": "a9a9a9", "darkgreen": "006400", "darkgrey": "a9a9a9",
    "darkkhaki": "bdb76b", "darkmagenta": "8b008b", "darkolivegreen": "556b2f",
    "darkorange": "ff8c00", "darkorchid": "9932cc", "darkred": "8b0000",
    "darksalmon": "e9967a", "darkseagreen": "8fbc8f", "darkslateblue": "483d8b",
    "darkslategray": "2f4f4f", "darkslategrey": "2f4f4f",
    "darkturquoise": "00ced1", "darkviolet": "9400d3", "deeppink": "ff1493",
    "deepskyblue": "00bfff", "dimgray": "696969", "dimgrey": "696969",
    "dodgerblue": "1e90ff", "firebrick": "b22222", "floralwhite": "fffaf0",
    "forestgreen": "228b22", "fuchsia": "ff00ff", "gainsboro": "dcdcdc",
    "ghostwhite": "f8f8ff", "gold": "ffd700", "goldenrod": "daa520",
    "gray": "808080", "green": "008000", "greenyellow": "adff2f",
    "grey": "808080", "honeydew": "f0fff0", "hotpink": "ff69b4",
    "indianred": "cd5c5c", "indigo": "4b0082", "ivory": "fffff0",
    "khaki": "f0e68c", "lavender": "e6e6fa", "lavenderblush": "fff0f5",
    "lawngreen": "7cfc00", "lemonchiffon": "fffacd", "lightblue": "add8e6",
    "lightcoral": "f08080", "lightcyan": "e0ffff",
    "lightgoldenrodyellow": "fafad2", "lightgray": "d3d3d3",
    "lightgreen": "90ee90", "lightgrey": "d3d3d3", "lightpink": "ffb6c1",
    "lightsalmon": "ffa07a", "lightseagreen": "20b2aa",
    "lightskyblue": "87cefa", "lightslategray": "778899",
    "lightslategrey": "778899", "lightsteelblue": "b0c4de",
    "lightyellow": "ffffe0", "lime": "00ff00", "limegreen": "32cd32",
    "linen": "faf0e6", "magenta": "ff00ff", "maroon": "800000",
    "mediumaquamarine": "66cdaa", "mediumblue": "0000cd",
    "mediumorchid": "ba55d3", "mediumpurple": "9370db",
    "mediumseagreen": "3cb371", "mediumslateblue": "7b68ee",
    "mediumspringgreen": "00fa9a", "mediumturquoise": "48d1cc",
    "mediumvioletred": "c71585", "midnightblue": "191970",
    "mintcream": "f5fffa", "mistyrose": "ffe4e1", "moccasin": "ffe4b5",
    "navajowhite": "ffdead", "navy": "000080", "oldlace": "fdf5e6",
    "olive": "808000", "olivedrab": "6b8e23", "orange": "ffa500",
    "orangered": "ff4500", "orchid": "da70d6", "palegoldenrod": "eee8aa",
    "palegreen": "98fb98", "paleturquoise": "afeeee",
    "palevioletred": "db7093", "papayawhip": "ffefd5", "peachpuff": "ffdab9",
    "peru": "cd853f", "pink": "ffc0cb", "plum": "dda0dd",
    "powderblue": "b0e0e6", "purple": "800080", "rebeccapurple": "663399",
    "red": "ff0000", "rosybrown": "bc8f8f", "royalblue": "4169e1",
    "saddlebrown": "8b4513", "salmon": "fa8072", "sandybrown": "f4a460",
    "seagreen": "2e8b57", "seashell": "fff5ee", "sienna": "a0522d",
    "silver": "c0c0c0", "skyblue": "87ceeb", "slateblue": "6a5acd",
    "slategray": "708090", "slategrey": "708090", "snow": "fffafa",
    "springgreen": "00ff7f", "steelblue": "4682b4", "tan": "d2b48c",
    "teal": "008080", "thistle": "d8bfd8", "tomato": "ff6347",
    "turquoise": "40e0d0", "violet": "ee82ee", "wheat": "f5deb3",
    "white": "ffffff", "whitesmoke": "f5f5f5", "yellow": "ffff00",
    "yellowgreen": "9acd32",
}

# The sentinel codegen emits for 'none'/'transparent'. A negative
# component can never be a real channel value, so it needs no extra
# argument or second runtime function to distinguish it -- see
# festina_set_fill_rgb's own comment in the runtime.
NO_COLOR = (-1, -1, -1)

# The two spellings that mean "draw nothing here", as opposed to a
# colour. Both are CSS spellings; `transparent` is a real CSS keyword
# and `none` is what the equivalent SVG/CSS paint properties use.
NO_COLOR_NAMES = ("none", "transparent")


def resolve_color(text):
    """A colour literal -> (r, g, b) with each component 0..255, or
    NO_COLOR for 'none'/'transparent'. Returns None if `text` isn't a
    colour this language recognises, so the caller can raise an error
    naming the offending value and its source location.

    Accepts a CSS name (case-insensitively), `#rgb`, or `#rrggbb`.
    `#abc` expands to `#aabbcc`, the same doubling CSS does.
    """
    if text is None:
        return None
    s = text.strip().lower()
    if not s:
        return None
    if s in NO_COLOR_NAMES:
        return NO_COLOR
    if s.startswith("#"):
        h = s[1:]
        if len(h) not in (3, 6) or any(c not in "0123456789abcdef" for c in h):
            return None
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    hexed = CSS_COLORS.get(s)
    if hexed is None:
        return None
    return (int(hexed[0:2], 16), int(hexed[2:4], 16), int(hexed[4:6], 16))


# Everything the font shorthand recognises that isn't a size or a
# family name. `normal` is accepted and carries no information, exactly
# as in CSS -- it names the default rather than changing anything.
_FONT_SLANTS = ("italic", "oblique")
_FONT_WEIGHTS = ("bold", "bolder")
_FONT_IGNORED = ("normal", "regular")


def parse_font(text):
    """A font shorthand literal -> (px, style, family), each of which is
    None when the literal didn't mention it.

    Words may appear in any order, and any of the three may be omitted:
    `'arial 14px bold'`, `'bold 14px arial'` and `'14px'` are all
    accepted, the last one yielding (14, None, None). Order-independence
    is deliberate -- CSS's own grammar requires size and family last and
    in that order, which is exactly the kind of rule that turns a
    reasonable-looking string into a silent no-op, and none of the
    ambiguity that grammar exists to resolve can arise here.

    `style` is normalised to one of None/'bold'/'italic'/'italic bold',
    so the runtime never has to cope with orderings or spellings.
    Returns None (rather than a tuple) only if `text` is None.
    """
    if text is None:
        return None
    px = None
    slant = False
    weight = False
    family = None
    for word in text.split():
        low = word.lower()
        if low in _FONT_SLANTS:
            slant = True
        elif low in _FONT_WEIGHTS:
            weight = True
        elif low in _FONT_IGNORED:
            continue
        elif low.endswith("px") and low[:-2].isdigit():
            px = int(low[:-2])
        elif low.isdigit():
            px = int(low)
        elif family is None:
            # First word that is none of the above. Kept in the source's
            # own case: font family names are matched case-insensitively
            # by fontconfig, but preserving what was written keeps the
            # generated IR readable.
            family = word
    style = None
    if slant and weight:
        style = "italic bold"
    elif slant:
        style = "italic"
    elif weight:
        style = "bold"
    return (px, style, family)
