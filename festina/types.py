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
    """claude.md #67 -- created only via the regex() builtin, never a
    dedicated literal syntax, so (unlike StructType/TableType) there's
    only ever one shape of this type; no fields to distinguish."""

    def __repr__(self):
        return "RegexType()"


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
    return str(t)
