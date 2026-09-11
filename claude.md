# Festina — working instructions for agents

This file tells an AI agent, or any contributor, how to work on the
Festina compiler and runtime. It contains no language rules of its own.
The language is defined in [specification.md](specification.md); this
file points at it.

## Where things live

| Document | What it is |
|---|---|
| [specification.md](specification.md) | **The normative language specification**: scope and conformance, lexical grammar, types, expressions, statements, declarations, execution and memory models, errors, the built-in database, the standard library, graphics, audio, HTTP, threads, compilation targets, and annexes (grammar, reserved words, removed features, non-goals). Organized by topic in the manner of the ECMAScript standard. This is the source of truth for what Festina is. |
| [api.md](api.md) | The standard library and language reference **as implemented**, with worked examples, caveats and performance notes. Normative for library details the specification defers to it. |
| [decisions.md](decisions.md) | The numbered, chronological **decision log**. Formerly named `claude.md`: a citation of the form `claude.md #N` anywhere in the repository (source comments, tests, CHANGELOG.md, tests/CONTRACT.md, api.md) means entry N of decisions.md. Entries 1–73 are the original specification text, kept as history and superseded by specification.md. Informative: rationale, alternatives, verification. |
| [tests/CONTRACT.md](tests/CONTRACT.md) | What is verified, and how. |
| [CHANGELOG.md](CHANGELOG.md), [todo.md](todo.md) | Version history; open work. |
| [setup.md](setup.md), [macos.md](macos.md), [windows.md](windows.md), [wasm.md](wasm.md), [security.md](security.md), [benchmark.md](benchmark.md) | Toolchain, platforms, security posture, benchmarks. |

## Rules for implementing Festina

1. **Implement specification.md.** Do not invent language behavior it
   does not specify. Where it is silent, apply its ambiguity rules
   (specification.md §2.4) and record the gap as an open design
   question rather than guessing.
2. **Festina is not JavaScript.** Where a Festina rule differs from
   JavaScript, the Festina rule wins (§2.5): no truthiness, no implicit
   coercion beyond numeric promotion (§8.3), no `var`/`let`, no
   `===`/`!==`. Annex D lists the non-goals; Annex C lists features
   that were removed and must not come back.
3. **Resolve everything at compile time.** Every name and type is
   resolved during semantic analysis, before LLVM IR generation
   (§21.1). Prefer compile-time validation, native representations and
   the lowest runtime overhead; avoid runtime reflection, dynamic
   dispatch, boxing and unnecessary heap allocation.
4. **Keep dependencies minimal** (§21.3). Never add a project
   dependency without explicit permission.
5. **The database needs no setup.** Every program opens `festina.sqlite`
   (or its `DatabaseURL`) and synchronizes every declared `table`
   before application code runs (§15); the Festina declaration is
   authoritative for the schema.
6. **Follow the "test, don't fail" convention** (§2.3): environmental
   failures answer a testable value; only program-authoring mistakes
   fail the program.

## Making a change

For every change to the language, compiler or runtime:

1. Update **specification.md** (the normative rule; add an Annex C row
   when something is removed) and **api.md** (the reference and
   examples). Keep the two consistent with each other and with the
   code.
2. Append a **new numbered entry to decisions.md** — the next number
   after the last — in the existing style: what was asked, what was
   decided and why, the alternatives considered, and how it was
   verified. Never renumber, reorder or remove earlier entries.
3. Add or update **tests**, update **tests/CONTRACT.md**, add a
   **CHANGELOG.md** entry, and keep the README's test count current.
4. Cite decision entries in code comments and tests as `decisions.md
   #N`; the older `claude.md #N` spelling remains valid and means the
   same entry.
5. Run the checks before pushing: `python3 -m pytest` for the whole
   suite, and `scripts/leak_stress.sh` for anything that touches
   ownership, reference counting or the runtime.

## Repository map

| Path | Contents |
|---|---|
| `festina/` | The compiler (Python): `lexer.py`, `parser.py`, `ast.py`, `imports.py`, `semantic.py`, `escape_analysis.py`, `types.py`, `sqlite_schema.py`, `codegen.py` (LLVM IR), `llvm_backend.py`, `cli.py`, `compiler.py`, `errors.py`, `colors.py` |
| `runtime/` | The native C runtime: `festina_runtime.c/.h` plus `_graphics`, `_audio`, `_http`, `_https`, `_thread`, `_async`, the window backends, and `wasm/` (the WASI host) |
| `tests/` | The pytest suite, `stress/` and `valgrind_stress/` programs, `fixtures/`, and `CONTRACT.md` |
| `examples/`, `benchmarks/` | Runnable programs (every example is compiled by the test suite); benchmark suites |
| `bin/festina`, `install.sh`, `packaging/`, `scripts/` | The CLI entry point, installer, packaged-binary build, and stress/CI scripts |
| `docs/`, `editors/` | The documentation site and editor support |

## Conventions

- The compiler executable is `festina`; source files end in `.f`.
- Compile errors are `file:line:column: error: message`.
- Tests and diagnostics name the decision entry that motivated them.
- No model identifiers in commits, code or documentation.
