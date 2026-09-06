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
`sqlite()` support lives in the core runtime translation unit, which
is compiled against sqlite3's symbols (claude.md #242: the LINK then
drops every SQLite function from a program that never declares a
`table` or calls `sqlite()` -- see wasm.md's "Binary size" -- but the
symbols still have to resolve), so WASM export cannot work at all
without a real, working sqlite3 compiled for `wasm32-wasi` -- it compiles cleanly against `wasi-libc`
with zero changes needed (see `wasm.md`'s own design section for the
exact flags this project builds it with).

Only linked into the `wasm32-wasi` build (`festina/cli.py`'s own
target-specific runtime-object selection) -- every native target is
completely unaffected and keeps using the system's `libsqlite3`.

# The WASI hosts

The rest of this directory is how a compiled `.wasm` gets *run*
(claude.md #148, #237 -- see `wasm.md`):

- `run_wasi.mjs` -- runs a program under Node's built-in `node:wasi`
  host; what `festina run --target=wasm32-wasi` uses.
- `festina_wasi_browser.js` -- this project's own WASI Preview 1 host,
  a dependency-free ES module with an in-memory filesystem, for the
  browser (and anywhere else with `WebAssembly`).
- `festina_wasi_worker.js` -- runs a program on that host inside a Web
  Worker; `browser.html` is the page that uses it
  (`browser.html?wasm=program.wasm`, served over HTTP).
- `run_wasi_js.mjs` -- the browser host under Node, the preopened
  directory loaded into its filesystem and written back afterwards; how
  the host is tested without a browser.
- `package.json` -- `{"type": "module"}`, so Node imports the `.js`
  host as an ES module exactly as a browser does.
