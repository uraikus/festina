"""Compiler error type -- claude.md #48.

Errors carry file, line, column, a category, and a human-readable message,
and format as:

    main.f:12:5: error: condition must be bool, found text
"""


class CompileError(Exception):
    """A Festina compile-time error.

    All keyword arguments are optional so internal code can raise with
    just a message and fill in location info incrementally, but callers
    that care about diagnostics should always pass file/line/column.
    """

    def __init__(self, message="", *, file="<string>", line=0, column=0, category="error"):
        self.file = file
        self.line = line
        self.column = column
        self.category = category
        self.message = message
        super().__init__(str(self))

    def __str__(self):
        return f"{self.file}:{self.line}:{self.column}: error: {self.message}"

    def __repr__(self):
        return (
            f"CompileError(file={self.file!r}, line={self.line}, "
            f"column={self.column}, category={self.category!r}, "
            f"message={self.message!r})"
        )


class CircularImportError(CompileError):
    """claude.md #6: circular imports must be detected deterministically."""

    def __init__(self, message="", *, file="<string>", line=0, column=0,
                 category="circular import"):
        super().__init__(message, file=file, line=line, column=column, category=category)
