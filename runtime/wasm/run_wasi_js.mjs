// claude.md #237: the browser WASI host (festina_wasi_browser.js), run
// under Node -- the same host a browser tab uses, with the real
// directory's files loaded into its in-memory filesystem first and
// written back afterwards. This is what proves the host itself,
// independently of any browser: tests/test_wasm_browser.py runs every
// program through it, and through Chromium via browser.html.
//
// Usage: node run_wasi_js.mjs <program.wasm> <preopen-dir>
//
// Mirrors run_wasi.mjs's contract exactly (see its own top comment):
// <preopen-dir> is the program's "/", the exit code is the program's
// own, stdout/stderr are the process's own.
import { readFileSync, readdirSync, statSync, writeFileSync, mkdirSync } from "node:fs";
import { join, relative, dirname } from "node:path";
import { FestinaWasi } from "./festina_wasi_browser.js";

const [, , wasmPath, preopenDir] = process.argv;

function loadTree(dir, root, files) {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    const st = statSync(full);
    if (st.isDirectory()) loadTree(full, root, files);
    else if (st.isFile()) files["/" + relative(root, full).split("\\").join("/")] = readFileSync(full);
  }
  return files;
}

const host = new FestinaWasi({
  args: [wasmPath],
  env: process.env,
  files: loadTree(preopenDir, preopenDir, {}),
  stdout: (line) => process.stdout.write(line + "\n"),
  stderr: (line) => process.stderr.write(line + "\n"),
});

const code = await host.run(readFileSync(wasmPath));

for (const [path, bytes] of host.files()) {
  const target = join(preopenDir, path.slice(1));
  mkdirSync(dirname(target), { recursive: true });
  writeFileSync(target, bytes);
}
process.exitCode = code;
