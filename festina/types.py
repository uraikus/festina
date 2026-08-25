"""Type representations -- claude.md #11 (type categories), #12 (type
resolution), #13 (unknown types).

Each category gets its own class so the compiler never has to infer a
category from a name -- callers construct the specific type they mean.
"""
from dataclasses import dataclass, field

PRIMITIVE_NAMES = frozenset({"int", "float", "bool", "text", "blob"})


@dataclass(frozen=True)
class PrimitiveType:
    name: str

    def __post_init__(self):
        if self.name not in PRIMITIVE_NAMES:
            raise ValueError(f"not a primitive type name: {self.name!r}")

    def __repr__(self):
        return f"PrimitiveType({self.name})"


@dataclass(frozen=True)
class StructType:
    name: str

    def __repr__(self):
        return f"StructType({self.name})"


@dataclass(frozen=True)
class TableType:
    name: str

    def __repr__(self):
        return f"TableType({self.name})"


@dataclass(frozen=True)
class ArrayType:
    """claude.md #156: `amortized` (default False) is set by the `amor`
    prefix -- `amor arr[T]` -- tracked for parsing/type-checking
    symmetry with MapType's own identical field (`amor` was asked for
    on both containers together), but with NO runtime effect yet:
    array growth (festina_array_resize) isn't amortized the way
    festina_amap_set makes map growth amortized -- `amor arr[T]`
    currently compiles and behaves exactly like plain arr[T]. Left as
    a real, honest scope boundary (see claude.md #156's own writeup)
    rather than silently accepting the syntax and doing nothing with
    it, or blocking it outright. `compare=False`: since there's no
    representation difference yet, an `amor arr[T]` and a plain
    arr[T] of the same element type are treated as the SAME type for
    assignment/equality purposes (unlike MapType's own `amortized`,
    which IS part of that type's identity, since amor map[T] genuinely
    has a different runtime header) -- revisit this the moment array
    amortization is actually implemented, since at that point the two
    genuinely stop being interchangeable."""
    element: object  # another Type instance
    amortized: bool = field(default=False, compare=False)

    def __repr__(self):
        prefix = "amor " if self.amortized else ""
        return f"{prefix}ArrayType({self.element!r})"


@dataclass(frozen=True)
class ImageType:
    def __repr__(self):
        return "ImageType()"


@dataclass(frozen=True)
class AudioType:
    def __repr__(self):
        return "AudioType()"


@dataclass(frozen=True)
class HttpType:
    """claude.md #151: an incoming HTTP request, handed to `on
    request(req:http)`. Like RegexType/AudioType/ImageType there is
    only one shape of this type -- the request's own method/headers/
    body all live in the runtime value (a small refcounted handle
    wrapping a connection id, see festina_runtime_http.c), never the
    static type."""

    def __repr__(self):
        return "HttpType()"


@dataclass(frozen=True)
class SocketType:
    """claude.md #151: an upgraded WebSocket connection, handed to `on
    upgrade(s:socket)`/`on message(s:socket, msg:blob)`/`on
    socketClose(s:socket)`. Same one-shape-only reasoning as HttpType
    -- the connection this wraps is looked up by id at every runtime
    call, never held as a live pointer past a single call (see
    festina_runtime_http.c's own doc comment on why)."""

    def __repr__(self):
        return "SocketType()"


@dataclass(frozen=True)
class RegexType:
    """claude.md #67 -- a regex value's pattern/flags live in the
    runtime pointer value (see festina_regex_compile), never the static
    type, whether created via a /pattern/flags literal or the regex()
    builtin -- so (unlike StructType/TableType) there's only ever one
    shape of this type; no fields to distinguish."""

    def __repr__(self):
        return "RegexType()"


@dataclass(frozen=True)
class ColorType:
    """claude.md #91: a colour, resolved to its channels at compile time.

    Like RegexType there is only one shape of this type -- the actual
    channels live in the value, not the type. The value is a packed
    0xRRGGBB integer (see codegen's `pack_color`), so passing a colour
    costs one register and comparing two is one integer compare; a
    negative value means "no colour at all" (Festina's 'none').

    Nothing here is reference-counted or freed: a colour is a plain
    integer, so it has no more lifetime than an `int` does."""

    def __repr__(self):
        return "ColorType()"


@dataclass(frozen=True)
class FontType:
    """claude.md #91: a font, resolved to its parts at compile time.

    The value is a pointer to a static constant record
    (`%struct.FestinaFont` -- size, slant, weight, family) that codegen
    emits into the binary's own read-only data from the declaration's
    literal. Nothing allocates it, nothing frees it, and copying a font
    value copies one pointer to storage that lives as long as the
    process -- so, like ColorType, this type never interacts with the
    reference-counting or text-ownership machinery at all."""

    def __repr__(self):
        return "FontType()"


@dataclass(frozen=True)
class MapType:
    """claude.md #72: map[T] -- keys are always text (never part of the
    type itself, the same way an array's index isn't), so only the
    value type distinguishes one map[T] from another. `value` may be
    any Type except ArrayType/MapType itself -- resolve_type_name
    rejects those at the point a map[T] type is resolved, since a map's
    runtime representation (festina_runtime.c's FestinaMapEntry) stores
    each value in one fixed 8-byte slot, the same convention sqlite
    query rows already use, and neither an array value (16 bytes: a
    length plus a data pointer) nor another map value fits in that.

    claude.md #156: `amortized` (default False) is set by the `amor`
    prefix -- `amor map[T]` -- and is part of this type's own identity
    for the identical reason ArrayType's own `amortized` field is: it
    changes the runtime header layout (an extra tracked-capacity field)
    and which growth function codegen calls, not just an internal
    detail invisible at the type level."""
    value: object  # another Type instance
    amortized: bool = False

    def __repr__(self):
        prefix = "amor " if self.amortized else ""
        return f"{prefix}MapType({self.value!r})"


@dataclass(frozen=True)
class FuncType:
    """claude.md #141: func[T, T, ...]:R -- a first-class reference to a
    FUNCTION, not a call to one. `param_types` is a tuple of Type
    instances (empty for a zero-argument function); `return_type` is
    another Type instance, or None for a void-returning function
    (mirroring how a plain FuncDecl's own `return_type` is represented
    as None internally everywhere else in this compiler, e.g.
    semantic.py's Symbol / codegen's Env entries for a function name).

    Two FuncType instances are equal (via the generated dataclass
    __eq__, since `param_types`/`return_type` are themselves ordinary
    hashable/comparable Type values or None) exactly when their whole
    signatures match -- the same "structural, not nominal" equality
    every other parametrized type (ArrayType, MapType) already has, so
    `check_assignable`'s generic `declared != actual` fallback needs no
    FuncType-specific branch at all.

    The runtime VALUE behind a FuncType is a bare function pointer --
    an LLVM `ptr` holding a real `@name` global-function address, never
    allocated, never freed, immortal for the life of the process (a
    declared function itself never goes away) -- so, exactly like
    ColorType/FontType, this never interacts with the reference-
    counting or text-ownership machinery: _is_refcounted (codegen.py)
    deliberately does not list it."""
    param_types: tuple
    return_type: object  # another Type instance, or None (void)

    def __repr__(self):
        return f"FuncType({self.param_types!r}, {self.return_type!r})"


def type_name(t):
    """Readable name for error messages, e.g. `arr[int]`, `User`, `int`."""
    if t is None:
        return "unknown"
    if isinstance(t, PrimitiveType):
        return t.name
    if isinstance(t, StructType):
        return t.name
    if isinstance(t, TableType):
        return t.name
    if isinstance(t, ArrayType):
        prefix = "amor " if t.amortized else ""
        return f"{prefix}arr[{type_name(t.element)}]"
    if isinstance(t, ImageType):
        return "img"
    if isinstance(t, AudioType):
        return "aud"
    if isinstance(t, RegexType):
        return "regex"
    if isinstance(t, HttpType):
        return "http"
    if isinstance(t, SocketType):
        return "socket"
    if isinstance(t, ColorType):
        return "color"
    if isinstance(t, FontType):
        return "font"
    if isinstance(t, MapType):
        prefix = "amor " if t.amortized else ""
        return f"{prefix}map[{type_name(t.value)}]"
    if isinstance(t, FuncType):
        params = ",".join(type_name(p) for p in t.param_types)
        ret = "void" if t.return_type is None else type_name(t.return_type)
        return f"func[{params}]:{ret}"
    return str(t)
