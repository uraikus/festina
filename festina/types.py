"""Type representations -- claude.md #11 (type categories), #12 (type
resolution), #13 (unknown types).

Each category gets its own class so the compiler never has to infer a
category from a name -- callers construct the specific type they mean.
"""
from dataclasses import dataclass

PRIMITIVE_NAMES = frozenset({"int", "float", "bool", "text", "blob"})


@dataclass(frozen=True)
class PrimitiveType:
    """claude.md #202: `manually_managed` (default False) is only ever
    actually SET True for `name == "blob"` -- the one primitive that
    carries a refcount header (see codegen._is_refcounted) and so is
    the one primitive `T?` means anything real for. For
    `int`/`float`/`bool`/`text`, semantic.py's own resolution never
    sets this field at all regardless of whether the source wrote `?`
    -- `int?`/`int` both resolve to the identical `PrimitiveType("int")`
    (this field stays its own default, False), fully interchangeable,
    so `?` on a scalar is accepted grammar with zero type-level effect,
    matching claude.md #202's own worked example (`int? count = 1`)."""
    name: str
    manually_managed: bool = False

    def __post_init__(self):
        if self.name not in PRIMITIVE_NAMES:
            raise ValueError(f"not a primitive type name: {self.name!r}")

    def __repr__(self):
        suffix = "?" if self.manually_managed else ""
        return f"PrimitiveType({self.name}{suffix})"


@dataclass(frozen=True)
class StructType:
    """claude.md #202: `manually_managed` (default False) -- see
    ArrayType's own doc comment for the full "genuinely different,
    non-interchangeable type, mirroring `amor`'s own precedent"
    reasoning, shared identically by every dataclass this field is
    added to."""
    name: str
    manually_managed: bool = False

    def __repr__(self):
        suffix = "?" if self.manually_managed else ""
        return f"StructType({self.name}{suffix})"


@dataclass(frozen=True)
class TableType:
    name: str

    def __repr__(self):
        return f"TableType({self.name})"


@dataclass(frozen=True)
class EnumType:
    """claude.md #176: `enum Name = Member1, Member2, ...` -- a tagged
    union "pseudo type" over any type. Name-only, exactly like
    StructType/TableType above -- the real member list (and whether
    every member is a struct, which decides the runtime representation:
    a zero-overhead self-tagged struct pointer, or a heap-boxed {tag,
    value} pair for anything else) lives in a separate `enums` dict
    (semantic.py's AnalyzedProgram, mirrored in codegen.py's self.enums),
    the same "StructType is a name-handle, structs holds the real field
    data" split StructType itself already uses.

    typeof on an EnumType-typed value never returns the enum's OWN
    name -- it always returns the concrete runtime member's name (the
    whole reason a runtime tag exists at all). "Shape" itself is never
    a typeof result; "Circle"/"Square" are.

    claude.md #202: `manually_managed` (default False) -- see
    ArrayType's own doc comment for the shared reasoning."""
    name: str
    manually_managed: bool = False

    def __repr__(self):
        suffix = "?" if self.manually_managed else ""
        return f"EnumType({self.name}{suffix})"


@dataclass(frozen=True)
class ThreadType:
    """claude.md #195/#208: `thread NAME { ... }` -- name-only, exactly
    like StructType/TableType/EnumType above. `NAME` itself becomes a
    global value of this type, supporting `.postMessage(x)`, `.kill()`,
    `.live(callback)`, `.isAlive()`. The real inbound message type
    (DECLARED, on this thread's own `on message(worker:thread, msg:T)`
    handler -- see semantic.py's own thread-analysis comments) lives in
    a separate `threads` dict (semantic.py's AnalyzedProgram, mirrored
    in codegen.py's self.threads), the same split every other name-only
    type here already uses.

    claude.md #208: `name` is `None` for the GENERIC variant -- the
    type spelled by the bare `thread` keyword in a parameter position
    (`on message(worker:thread, msg:T)`), resolved once, in
    resolve_type_name, alongside every other builtin type keyword.
    This is deliberately a DIFFERENT value from any specific declared
    thread's own `ThreadType("someName")` (dataclass equality means the
    two never compare equal) -- there is no way for ordinary Festina
    code to construct a `thread`-typed value itself; `worker`'s own
    value only ever arrives via message delivery (the sender's own
    handle, boxed by the runtime at every postMessage/bare-postMessage
    send site), so no widening/narrowing conversion between the named
    and generic forms is needed anywhere in this compiler.

    claude.md #216: `worker` is never `null` any more -- when main is
    the sender, it's a real singleton handle (`is_main` set at the
    runtime level), exposed to Festina code as the one field on
    `thread`, `.main:bool` (see semantic.py's ThreadType field-access
    branch)."""
    name: str | None

    def __repr__(self):
        return f"ThreadType({self.name})"


@dataclass(frozen=True)
class ArrayType:
    """claude.md #156: `amortized` (default False) is set by the `amor`
    prefix -- `amor arr[T]` -- originally tracked for parsing/type-
    checking symmetry with `amor map[T]`, which claude.md #175 later
    removed outright once plain map[T] itself became a real hash table
    with intrinsic geometric growth (see MapType's own docstring).
    claude.md #174 gave `amortized` a real runtime effect for arrays,
    which #175 didn't touch: `festina_array_resize` grows an
    `amor arr[T]`'s backing buffer geometrically (doubling), tracked in
    a THIRD header field (`FESTINA_AMOR_ARRAY_LLVM_TYPE`, a byte-
    compatible prefix extension of the plain `{length, data}` shape) a
    plain `arr[T]`'s header doesn't have -- so this is part of the
    type's real identity, not a comparison-transparent modifier: an
    `amor arr[T]` and a plain `arr[T]` of the same element type are
    genuinely different, non-interchangeable representations, and
    assignment between them is a compile error.

    claude.md #202: `manually_managed` (default False) is a SECOND,
    independent field of this exact same shape -- set by a trailing
    `?` (`arr[int]?`), meaning this array's own header/backing buffer
    is never automatically retained/released, only ever reclaimed by
    an explicit `free`/`delete`. Composes freely with `amortized` (an
    `amor arr[T]?` is both amortized-growth AND manually-managed at
    once) -- the two are orthogonal representation questions, unlike
    `amortized` vs. plain, which are mutually exclusive variants of the
    SAME question."""
    element: object  # another Type instance
    amortized: bool = False
    manually_managed: bool = False

    def __repr__(self):
        prefix = "amor " if self.amortized else ""
        suffix = "?" if self.manually_managed else ""
        return f"{prefix}ArrayType({self.element!r}){suffix}"


@dataclass(frozen=True)
class ImageType:
    """claude.md #202: `manually_managed` (default False) -- see
    ArrayType's own doc comment for the shared reasoning."""
    manually_managed: bool = False

    def __repr__(self):
        suffix = "?" if self.manually_managed else ""
        return f"ImageType(){suffix}"


@dataclass(frozen=True)
class AudioType:
    """claude.md #202: `manually_managed` (default False) -- see
    ArrayType's own doc comment for the shared reasoning."""
    manually_managed: bool = False

    def __repr__(self):
        suffix = "?" if self.manually_managed else ""
        return f"AudioType(){suffix}"


@dataclass(frozen=True)
class HttpType:
    """claude.md #151: an incoming HTTP request, handed to `on
    request(req:http)`. Like RegexType/AudioType/ImageType there is
    only one shape of this type -- the request's own method/headers/
    body all live in the runtime value (a small refcounted handle
    wrapping a connection id, see festina_runtime_http.c), never the
    static type.

    claude.md #202: `manually_managed` (default False) -- see
    ArrayType's own doc comment for the shared reasoning."""
    manually_managed: bool = False

    def __repr__(self):
        suffix = "?" if self.manually_managed else ""
        return f"HttpType(){suffix}"


@dataclass(frozen=True)
class UrlType:
    """claude.md #162: parseURL(text) -- a parsed URL's own
    hash/hostname/password/pathname/port/protocol/searchParams/
    username. Same one-shape-only reasoning as HttpType/RegexType:
    every field lives in the runtime value (a small refcounted struct,
    see festina_runtime_url.c), never the static type -- there's
    nothing here to distinguish one url from another at the type
    level, unlike StructType/TableType.

    claude.md #202: `manually_managed` (default False) -- see
    ArrayType's own doc comment for the shared reasoning."""
    manually_managed: bool = False

    def __repr__(self):
        suffix = "?" if self.manually_managed else ""
        return f"UrlType(){suffix}"


@dataclass(frozen=True)
class SocketType:
    """claude.md #151: an upgraded WebSocket connection, handed to `on
    upgrade(s:socket)`/`on message(s:socket, msg:blob)`/`on
    socketClose(s:socket)`. Same one-shape-only reasoning as HttpType
    -- the connection this wraps is looked up by id at every runtime
    call, never held as a live pointer past a single call (see
    festina_runtime_http.c's own doc comment on why).

    claude.md #202: `manually_managed` (default False) -- see
    ArrayType's own doc comment for the shared reasoning."""
    manually_managed: bool = False

    def __repr__(self):
        suffix = "?" if self.manually_managed else ""
        return f"SocketType(){suffix}"


@dataclass(frozen=True)
class RegexType:
    """claude.md #67 -- a regex value's pattern/flags live in the
    runtime pointer value (see festina_regex_compile), never the static
    type, whether created via a /pattern/flags literal or the regex()
    builtin -- so (unlike StructType/TableType) there's only ever one
    shape of this type; no fields to distinguish.

    claude.md #202: `manually_managed` (default False) -- see
    ArrayType's own doc comment for the shared reasoning."""
    manually_managed: bool = False

    def __repr__(self):
        suffix = "?" if self.manually_managed else ""
        return f"RegexType(){suffix}"


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
    """claude.md #72, rebuilt into a real hash table by #175: map[T] --
    keys are always text (never part of the type itself, the same way
    an array's index isn't), so only the value type distinguishes one
    map[T] from another. `value` may be any Type except ArrayType/
    MapType itself -- resolve_type_name rejects those at the point a
    map[T] type is resolved, since a map's runtime representation
    (festina_runtime.c's FestinaMapEntry) stores each value in one
    fixed 8-byte slot, the same convention sqlite query rows already
    use, and neither an array value (16 bytes: a length plus a data
    pointer) nor another map value fits in that.

    Backed by a real open-addressing hash table (linear probing,
    FNV-1a, tombstone deletion, doubling at 75% load factor -- see
    festina_map_set's own comment in runtime/festina_runtime.c), not a
    linear scan. Growth is intrinsic to being a hash table -- every
    map[T] tracks its own bucket capacity and grows geometrically, the
    same "amortized" growth claude.md #156's now-removed `amor map[T]`
    variant used to bolt on separately; there is no distinct amortized
    map type left to give this a second field for (unlike ArrayType,
    which still has one -- `amor arr[T]` is unaffected by this
    change).

    claude.md #202: `manually_managed` (default False) -- see
    ArrayType's own doc comment for the shared reasoning."""
    value: object  # another Type instance
    manually_managed: bool = False

    def __repr__(self):
        suffix = "?" if self.manually_managed else ""
        return f"MapType({self.value!r}){suffix}"


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
    """Readable name for error messages, e.g. `arr[int]`, `User`, `int`.

    claude.md #202: every branch below that can carry `manually_managed`
    appends a trailing `?` when it's set, matching the surface syntax
    exactly (`Circle?`, `arr[int]?`, ...) -- this is NOT just cosmetic:
    codegen.py's own generated-function caches for struct/array/map/enum
    release cascades are keyed by THIS string, not by the raw Type
    instance's own `__eq__`/`__hash__` (`amor`'s own `"amor "` prefix
    just below is the existing precedent this mirrors exactly) -- so a
    manually-managed type colliding with its ordinary counterpart here
    would be a silent cache-collision bug, two genuinely different
    types sharing one generated release function."""
    if t is None:
        return "unknown"
    mm = "?" if getattr(t, "manually_managed", False) else ""
    if isinstance(t, PrimitiveType):
        return f"{t.name}{mm}"
    if isinstance(t, StructType):
        return f"{t.name}{mm}"
    if isinstance(t, TableType):
        return t.name
    if isinstance(t, EnumType):
        return f"{t.name}{mm}"
    if isinstance(t, ArrayType):
        prefix = "amor " if t.amortized else ""
        return f"{prefix}arr[{type_name(t.element)}]{mm}"
    if isinstance(t, ImageType):
        return f"img{mm}"
    if isinstance(t, AudioType):
        return f"aud{mm}"
    if isinstance(t, RegexType):
        return f"regex{mm}"
    if isinstance(t, HttpType):
        return f"http{mm}"
    if isinstance(t, UrlType):
        return f"url{mm}"
    if isinstance(t, SocketType):
        return f"socket{mm}"
    if isinstance(t, ColorType):
        return "color"
    if isinstance(t, FontType):
        return "font"
    if isinstance(t, MapType):
        return f"map[{type_name(t.value)}]{mm}"
    if isinstance(t, FuncType):
        params = ",".join(type_name(p) for p in t.param_types)
        ret = "void" if t.return_type is None else type_name(t.return_type)
        return f"func[{params}]:{ret}"
    if isinstance(t, ThreadType):
        # claude.md #218: without this, every user-facing message about
        # a thread value (`cannot assign value of type X to int`, an
        # argument-type mismatch, ...) fell through to `str(t)` below
        # and printed this class's own Python repr -- `ThreadType(None)`
        # -- rather than the type as the language actually spells it.
        # The generic variant IS spelled `thread`; a specific declared
        # thread's own type is only ever reachable through its name, so
        # naming it that way is what a reader can act on.
        return "thread" if t.name is None else f"thread '{t.name}'"
    return str(t)
