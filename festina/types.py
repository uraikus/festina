"""Type representations -- claude.md #11 (type categories), #12 (type
resolution), #13 (unknown types).

Each category gets its own class so the compiler never has to infer a
category from a name -- callers construct the specific type they mean.
"""
from dataclasses import dataclass

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
    element: object  # another Type instance

    def __repr__(self):
        return f"ArrayType({self.element!r})"


@dataclass(frozen=True)
class ImageType:
    def __repr__(self):
        return "ImageType()"


@dataclass(frozen=True)
class AudioType:
    def __repr__(self):
        return "AudioType()"


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
class MapType:
    """claude.md #72: map[T] -- keys are always text (never part of the
    type itself, the same way an array's index isn't), so only the
    value type distinguishes one map[T] from another. `value` may be
    any Type except ArrayType/MapType itself -- resolve_type_name
    rejects those at the point a map[T] type is resolved, since a map's
    runtime representation (festina_runtime.c's FestinaMapEntry) stores
    each value in one fixed 8-byte slot, the same convention sqlite
    query rows already use, and neither an array value (16 bytes: a
    length plus a data pointer) nor another map value fits in that."""
    value: object  # another Type instance

    def __repr__(self):
        return f"MapType({self.value!r})"


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
        return f"arr[{type_name(t.element)}]"
    if isinstance(t, ImageType):
        return "img"
    if isinstance(t, AudioType):
        return "aud"
    if isinstance(t, RegexType):
        return "regex"
    if isinstance(t, MapType):
        return f"map[{type_name(t.value)}]"
    return str(t)
