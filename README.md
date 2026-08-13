# jsc — a JavaScript-subset compiler targeting LLVM

Compiles a restricted subset of JavaScript to real LLVM IR, backed by a C
runtime that implements the dynamic value system, plus bindings to
**libsqlite3** (a `better-sqlite3`-style API) and **cairo** (a Canvas 2D
`getContext('2d')`-style API).

```
JS source --[lexer.py]--> tokens --[parser.py]--> AST --[codegen.py]--> LLVM IR (.ll)
                                                                            |
                                              clang/llc  <-------  or  libLLVM JIT
                                                  |
                                         native binary, linked against
                                         runtime.c + libsqlite3 + libcairo
```

## Layout

```
compiler/
  lexer.py      tokenizer
  parser.py     recursive-descent parser -> AST
  ast_nodes.py  AST node classes
  codegen.py    AST -> LLVM IR text
  jsc.py        CLI: jsc.py input.js -o output.ll
runtime/
  runtime.h/.c       value system, operators, array/object/string/number
                     methods, console.log, sqlite + canvas bindings
  sqlite3_shim.h     hand-written sqlite3 C ABI declarations (no -dev
                     package needed, since the ABI has been stable for
                     20 years and only the shared lib is required)
examples/
  basics.js          for-loops, if/else, arrays, objects, strings, numbers
  sqlite_canvas.js   better-sqlite3-style + canvas-style demo
build.sh             build a native binary with a real clang/LLVM toolchain
run_jit.sh           run via ctypes+libLLVM JIT (no clang needed)
jit_run.py           the ctypes JIT runner used by run_jit.sh
```

## Quick start

**With a full clang/LLVM toolchain** (e.g. `apt install clang libsqlite3-dev libcairo2-dev`):
```
./build.sh examples/basics.js out
./out
```

**Without clang** (this sandbox only has `libLLVM-*.so`, no `clang`/`llc`
binaries and no network access to install them) — the same IR is instead
parsed, verified, and JIT-executed directly through LLVM's C API via
ctypes, which is what was used to test everything below:
```
gcc -c -fPIC -O2 -o runtime/runtime.o runtime/runtime.c
gcc -shared -fPIC -O2 -o runtime/libjsruntime.so runtime/runtime.o \
    /lib/x86_64-linux-gnu/libsqlite3.so.0 -lcairo -lm
./run_jit.sh examples/basics.js
```
Both paths compile the *same* `.ll` IR — `build.sh` just also does the final
IR→object→link steps that `clang` normally does for you.

## What's supported

- **Types**: numbers, strings, booleans, `null`/`undefined`, arrays, objects
- **Control flow**: `if`/`else`, `for(;;)`, `while`
- **Functions**: top-level `function` declarations, arrow functions (used
  chiefly as callbacks for `map`/`filter`/`forEach`)
- **console**: `console.log(...)`
- **Array methods**: `push`, `pop`, `length`, `map`, `filter`, `forEach`,
  `join`, `indexOf`, `includes`, `slice`
- **Object**: literals, `obj.prop` / `obj['prop']` get/set, `Object.keys`,
  `Object.values`
- **String methods**: `length`, `slice`, `indexOf`, `includes`, `split`,
  `toUpperCase`, `toLowerCase`, `charAt`, `trim`
- **Number methods**: `toFixed`, `toString`
- **`better-sqlite3`-style API**: `new Database(path)`, `db.exec(sql)`,
  `db.prepare(sql)` → `stmt.run(...)`, `stmt.get(...)`, `stmt.all(...)`,
  `db.close()` — implemented directly against `libsqlite3`'s C ABI
- **Canvas 2D-style API**: `createCanvas(w,h)`, `canvas.getContext('2d')`,
  `fillStyle`/`strokeStyle`/`lineWidth`, `fillRect`/`strokeRect`/`clearRect`,
  `beginPath`/`moveTo`/`lineTo`/`arc`/`closePath`, `fill`/`stroke`,
  `fillText`, `canvas.toBuffer(path)` (writes a PNG) — implemented directly
  against `cairo`'s C API

Both example programs in `examples/` run successfully through the full
pipeline, including real SQL queries against an in-memory SQLite database
and a real PNG rendered by cairo (blue rectangle + red circle + black line).

## Design simplifications (read before extending)

This is a real, working compiler, but it takes shortcuts a production
implementation wouldn't, in the interest of covering the requested feature
set concretely rather than partially covering a much larger one:

- **Every JS value is boxed** as a heap-allocated tagged `JSValue*` (like
  `T_NUMBER`/`T_STRING`/`T_ARRAY`/...). This makes codegen uniform (every
  expression is just an `i8*`) at the cost of performance — there's no
  unboxed fast path for numeric loops. A real implementation would use
  LLVM's type system properly (e.g. NaN-boxing or unboxed doubles with
  guards).
- **No garbage collection.** Allocations are simply leaked. Fine for
  short-lived scripts and demos; not for long-running programs.
- **No true block scoping.** All `let`/`const`/`var`/parameters within a
  function are hoisted to allocas in the entry block (the classic
  "Kaleidoscope tutorial" approach), so shadowing between nested blocks
  isn't handled the way real JS scoping works.
- **No closures.** Arrow functions compile to ordinary top-level LLVM
  functions; they can't capture outer variables by reference. They work
  fine as `map`/`filter`/`forEach` callbacks that only use their own
  parameters.
- **Method dispatch is mostly syntactic**, not type-checked: `.push()`
  always means "array push", `.toUpperCase()` always means "string
  method", etc. `indexOf`/`slice`/`includes` (which exist on both arrays
  and strings) are resolved by *runtime* type tag instead, since those
  are genuinely ambiguous — everything else assumes the obvious type
  for that method name.
- **`require('better-sqlite3')` / `require('canvas')` are no-ops.** The
  compiler recognizes `Database`, `createCanvas`, and `Object` as builtins
  by identifier name regardless of what (if anything) they were bound to,
  rather than implementing a real module system.
- **Canvas fill/stroke color is a single shared cairo "source color"**,
  matching the common case (set a style, immediately fill/stroke) but not
  simultaneous independent fill/stroke colors mid-path.
- **No exceptions/try-catch, no `switch`, no template literals, no
  destructuring beyond the one `require()` pattern**, no classes, no
  `async`/`await`, no regex.

Extending any of these is mechanical (the codegen and runtime are both
straightforward, uniform, and small enough to read end to end) but was out
of scope for the requested feature set.
