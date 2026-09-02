// claude.md #237: runs a compiled Festina .wasm inside a Web Worker so
// the page's own thread never blocks -- a Festina program is a plain
// synchronous main() (plus its own timer loop, which sleeps via
// poll_oneoff), and WebAssembly has no way to yield back to the page
// from inside it. Spawned by browser.html; usable directly too:
//
//   const worker = new Worker("festina_wasi_worker.js", { type: "module" });
//   worker.onmessage = ({ data }) => { /* {kind: "stdout"|"stderr", line} | {kind: "exit", code, stdout, stderr, files} | {kind: "error", message} */ };
//   worker.postMessage({ wasm: arrayBuffer, args: ["program.wasm"], files: { "/notes.txt": "hello" } });
//
// `files` seeds the program's in-memory filesystem (see
// festina_wasi_browser.js); the exit message carries every file the
// program left behind, as a plain object of path -> Uint8Array.
import { FestinaWasi } from "./festina_wasi_browser.js";

self.onmessage = async ({ data }) => {
  const host = new FestinaWasi({
    args: data.args || ["program.wasm"],
    env: data.env || {},
    files: data.files || {},
    stdout: (line) => self.postMessage({ kind: "stdout", line }),
    stderr: (line) => self.postMessage({ kind: "stderr", line }),
  });
  try {
    const code = await host.run(data.wasm);
    const files = {};
    for (const [path, bytes] of host.files()) files[path] = bytes;
    self.postMessage({ kind: "exit", code, stdout: host.stdout, stderr: host.stderr, files });
  } catch (err) {
    self.postMessage({ kind: "error", message: String(err && err.stack ? err.stack : err) });
  }
};
