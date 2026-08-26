# Vendored: the SQLite amalgamation

`sqlite3.c` and `sqlite3.h` in this directory are SQLite's own
single-file amalgamation build, version 3.53.4, straight from upstream
with zero modifications -- not Festina's own code, and not meant to be
edited here. SQLite itself is public domain
(<https://www.sqlite.org/copyright.html>); vendoring the amalgamation
is the standard, sanctioned way to embed SQLite in a project (it's
literally what the amalgamation exists for -- "This file is all you
need to compile SQLite," per its own top comment).

**Why vendor it at all, when every native target (Linux/macOS/Windows,
see `setup.md`) just links the system's own `libsqlite3`?** WASM export
targets WASI, which has no shared-library story a Festina binary could
dynamically link against, and no `apt`/Homebrew/pacman-installed
*static* `libsqlite3.a` built for `wasm32-wasi` exists to statically
link either -- there is nothing "system" to reach for. `table`/
`sqlite()` support is unconditional core (every compiled program links
against sqlite3's symbols, whether or not it ever declares a `table`),
so WASM export cannot work at all without a real, working sqlite3
compiled for `wasm32-wasi` -- it compiles cleanly against `wasi-libc`
with zero changes needed (see `wasm.md`'s own design section for the
exact flags this project builds it with).

Only linked into the `wasm32-wasi` build (`festina/cli.py`'s own
target-specific runtime-object selection) -- every native target is
completely unaffected and keeps using the system's `libsqlite3`.
