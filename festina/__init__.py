"""Festina: a statically typed, JavaScript-inspired language compiled
through LLVM to native executables.

This package implements the language described in claude.md. See
tests/CONTRACT.md for the module layout this implementation follows.

Only the front end (lexing, parsing, type resolution, semantic analysis)
and the SQLite schema-synchronization logic are implemented here. LLVM IR
generation and native linking (claude.md #47) are out of scope for this
package and remain unimplemented.
"""

__version__ = "0.1.0"
