// claude.md #148: WASM export's own runner -- executes a compiled
// wasm32-wasi binary via Node.js's built-in `node:wasi` module, the
// WASI host this whole feature was verified against (no wasmtime/
// wasmer install assumed; Node is already a listed dependency for
// running the compiler frontend itself from a checkout -- see
// setup.md -- so this adds no NEW dependency for that path, though a
// packaged `festina` binary user still needs Node specifically
// installed to actually RUN a .wasm it produces, same as any WASI
// host would require).
//
// Usage: node run_wasi.mjs <program.wasm> <preopen-dir>
//
// <preopen-dir> is mapped to the wasm program's own "/" -- Festina's
// filesystem builtins (blob, mkdir, ls) and its own always-on sqlite
// support both do real file I/O, and WASI's sandboxing model requires
// the host to explicitly grant a directory rather than exposing the
// whole real filesystem the way a native binary can see. festina/
// cli.py's own run_program passes the invoking process's cwd here,
// matching how a native compiled program already resolves relative
// paths against its own cwd.
import { readFileSync } from "node:fs";
import { WASI } from "node:wasi";

const [, , wasmPath, preopenDir] = process.argv;

const wasi = new WASI({
  version: "preview1",
  args: [wasmPath],
  env: process.env,
  preopens: { "/": preopenDir },
});

const wasmBuffer = readFileSync(wasmPath);
const { instance } = await WebAssembly.instantiate(wasmBuffer, {
  wasi_snapshot_preview1: wasi.wasiImport,
});

// wasi.start() returns the program's own exit code (proc_exit, or 0 on
// a plain return from main) -- propagated as this process's own exit
// code rather than always exiting 0, so a compiled Festina program's
// fail()/close(code) is visible to whatever invoked this script the
// same way a native binary's own exit code already is.
process.exitCode = wasi.start(instance);
