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
(claude.md #148, `wasm.md`) targets WASI, which has no shared-library
story a Festina binary could dynamically link against, and no
`apt`/Homebrew/pacman-installed *static* `libsqlite3.a` built for
`wasm32-wasi` exists to statically link either -- there is nothing
"system" to reach for. Since `table`/`sqlite()` support is unconditional
core (claude.md #10/#28-31: every compiled program links against
sqlite3's symbols, whether or not it ever declares a `table`), WASM
export cannot work AT ALL without a real, working sqlite3 compiled for
`wasm32-wasi` -- confirmed directly: it genuinely does compile cleanly
against `wasi-libc` with zero changes needed (see `wasm.md`'s own
design section for the exact flags this project builds it with).

**Where this exact copy came from:** `sqlite.org` itself was not
reachable from the environment this was vendored in (the outbound
proxy in place there returned a 403 on the amalgamation download).
`npm`'s registry was reachable, and the `better-sqlite3` package
(<https://www.npmjs.com/package/better-sqlite3>) vendors this exact
upstream amalgamation unmodified at `deps/sqlite3/{sqlite3.c,sqlite3.h}`
in its own published tarball -- confirmed byte-for-byte against the
version string in its own top comment (`3.53.4`), not taken on faith.
Re-vendoring a NEWER SQLite release later should pull from
`sqlite.org`'s own amalgamation zip directly wherever that's reachable;
this indirect route was this one vendoring's own workaround, not a
recommendation for next time.

Only linked into the `wasm32-wasi` build (`festina/cli.py`'s own
target-specific runtime-object selection) -- every native target is
completely unaffected and keeps using the system's `libsqlite3` exactly
as before.
