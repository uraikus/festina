# Festina — intent, working instructions and index for AI agents

This file is for an AI agent (or a new contributor) starting work on
Festina. It holds three things that belong in neither the language
specification nor the decision log: what this project is trying to be,
how the owner wants work on it done, and an index of where every kind
of information lives. It contains no language rules and no history.

- The language is defined in [specification.md](specification.md).
- The reasons behind it are in [decisions.md](decisions.md), the
  numbered decision log (a citation `claude.md #N` anywhere in the
  repository means entry N of that file; it was renamed from claude.md).
- The library as implemented, with examples, is [api.md](api.md).

## 1 Intent

- **Festina is a fast, simple, statically typed language that compiles
  straight to native code**, for games, tools and small servers. A
  program is one `.f` file (plus imports) with everything it needs
  built in: SQLite, files, graphics, audio, timers, HTTP/WebSocket,
  threads. No package manager, no configuration, no `main()`.
- **Performance over flexibility.** Then simplicity, predictability,
  static typing, low runtime overhead and familiar syntax. When two
  designs both satisfy the specification, the one with the lower
  runtime cost wins. A feature that would slow compiled programs down
  is not worth its convenience.
- **Familiar, not compatible.** The syntax reads like JavaScript so it
  gets out of the way; the semantics are Festina's own. Nothing dynamic
  is inherited: no truthiness, no coercion, no closures, no runtime
  reflection.
- **Batteries are part of the language, not libraries.** A `table`
  creates and migrates its own SQLite table; `img x = 'a.png'` loads an
  image; `on request` serves HTTP; `thread` runs on a real OS thread.
  The programmer never initializes any of it.
- **Minimal dependencies, clear failures.** Compiling and running a
  program should need as few installed tools as possible, and a missing
  one must produce an error that says what is missing and how to get
  it. A compiled program links only what it uses.
- **Correct means verified.** "The IR looks right" and "the program
  behaves right" are different claims; only the second counts. Every
  documented behavior has a test, every example is compiled by the test
  suite, and memory claims are checked under sanitizers, not assumed.
- **The long-term direction is self-hosting.** Features that make it
  easier to write Festina in Festina (`match`, `ascii`, the parse
  cache) are welcome, but never at the cost of the compiled program's
  performance.

## 2 Working instructions

These are the owner's standing instructions. They apply in addition to
the specification's own conformance rules (specification.md §2).

**Before changing anything**

- Read the relevant specification clause, the api.md section and the
  decision entries the index below points at. Most questions have
  already been decided; find the decision before re-deciding it.
- When asked a question, answer it without making changes.
- Do not invent behavior the specification does not define. Apply its
  ambiguity rules (§2.4); if the answer is genuinely undetermined, or a
  request would change existing semantics at scale, ask the owner and
  confirm the scope before writing code, as decisions.md #143 did.
- Never add a project dependency (a Python package, a C library, a
  build tool) without explicit permission.

**The order of work for a language change** (decisions.md #278)

Anything that adds to or changes the language surface — a type, a
method, an operator, a statement form, a literal, a diagnostic — is
written in this order, and the order is not negotiable:

1. **specification.md first.** Write the normative clause before any
   other file. Deciding the rule in prose, where it has to sit beside
   the clauses it interacts with, is what catches a design that does
   not fit; discovering that halfway through codegen is expensive.
   Add the Annex C row if something is being removed or superseded.
2. **Tests second.** Write them against the clause, and watch them
   fail for the right reason before implementing. A test written after
   the implementation tests what the code does; one written after the
   specification tests what the language promises.
3. **The implementation last**, until those tests pass.

This does not apply to a bug fix, where the specification already says
what should happen and the code disagrees — there, the clause is the
thing being restored, so confirm it says what you think, add the
failing test, then fix. It does apply the moment a "fix" turns out to
need a rule that is not written down yet.

**While changing**

- Keep compiled programs fast: prefer compile-time work, native
  representations and the lowest-overhead implementation; measure
  before optimizing and record the measurement.
- A feature that cannot be verified on real hardware stays behind an
  explicit opt-in environment variable until it has been (macOS and
  Windows audio today); say so in the platform document rather than
  claiming support.
- Limitations are documented, never silent: a scope cut goes into
  api.md and todo.md with the open design question written down, so the
  next session starts from a real fork instead of re-deriving it.

**Verifying**

- Run the real program, not only the unit tests: compile and execute a
  probe for every new behavior before writing its test.
- Run the full suite (`python3 -m pytest`) before pushing. For anything
  that touches ownership, reference counting or the runtime, run
  `scripts/leak_stress.sh`; for threading, `scripts/thread_tsan_stress.sh`;
  for JSON parsing, `scripts/valgrind_stress.sh`.
- A flaky test is measured and fixed at its root, never rerun until
  green, skipped or quarantined (decisions.md #261).

**Documenting every change** (all of these, every time)

1. specification.md — the normative rule; add an Annex C row for
   anything removed. For a language change this was already written
   first, before the tests and the code; check it still says what the
   implementation ended up doing, and correct whichever one is wrong.
2. api.md — the reference and worked examples, kept consistent with the
   specification and the code.
3. decisions.md — append the next numbered entry, in the existing
   style: what was asked, what was decided and why, alternatives
   considered, how it was verified. Never renumber, reorder or remove
   an earlier entry.
4. tests and tests/CONTRACT.md — what is now verified, and how.
5. CHANGELOG.md — under the current unreleased version.
6. README.md — keep the test count current.

Cite decision entries as `decisions.md #N` in new comments, tests and
documents; the older `claude.md #N` spelling remains valid and means
the same entry.

**Documentation describes the present, and only the present.** Every
document states what the project *is* right now — not what it used to
be, and not what it is going to be. No "as of version X", no "this
used to work differently", no "this will be added later", no
`claude.md #N`/`decisions.md #N` citation used to explain what changed
instead of what is true. This covers specification.md, api.md,
benchmark.md, setup.md, macos.md, windows.md, wasm.md, security.md,
README.md, every `README.md` under a subdirectory, tests/CONTRACT.md
and the documentation site.

Exactly three documents are exempt, because recording time is their
whole purpose:

| | |
|---|---|
| CHANGELOG.md | the past, by version |
| decisions.md | the past, by decision — what was asked, decided, and why |
| todo.md | the future — open work and deliberate non-work |

One sanctioned exception outside those three: Annex C of
specification.md, a table of removed and superseded features naming
the entry that removed each, kept because a reader needs to know
something is gone.

When a change makes an existing sentence read as history or as a
promise, rewrite the sentence; do not leave the old and new framing
side by side, and do not annotate the stale one. A number that has
moved (a file count, a test count, a measurement) is the same problem
in miniature: correct it rather than letting it date the document.

**Delivering**

- Work on the designated branch, commit with clear messages, push, and
  open a pull request at the end of every task.
- Report outcomes faithfully: what passed, what failed with its output,
  what was left out and why.
- Tell the owner about better ways of doing things and about
  improvements to these instructions.
- Never put a model identifier in a commit, a comment, a document or
  any other repository artifact.

## 3 Index — where to find things

Use this table to go straight to the right place. "Spec" is
[specification.md](specification.md); "api" is a section of
[api.md](api.md); "Decisions" are entry numbers in
[decisions.md](decisions.md).

| Topic | Spec | api.md section | Decisions | Code | Tests |
|---|---|---|---|---|---|
| Design priorities, conformance, ambiguity rules, non-goals | §2, §4, Annex D | — | #1, #2, #53, #54, #59 | — | `test_non_goals.py` |
| Lexical grammar, keywords, literals, precedence | §7, §9.13, Annex A, Annex B | — | #9, #51, #66, #67, #142, #252, #266 | `festina/lexer.py`, `parser.py`, `ast.py` | `test_lexer.py`, `test_syntax_declarations.py` |
| Imports, entry file, startup order, hoisting, namespaces | §6 | Imports; CLI | #4–#8, #58, #140, #178 | `festina/imports.py`, `compiler.py` | `test_imports.py`, `test_entry_point_and_example.py` |
| Types, `null`, zero values, assignability | §8.1, §8.2, §8.19, §8.20 | Types | #10–#16, #25, #50, #97 | `festina/types.py`, `semantic.py` | `test_types.py` |
| Numbers, promotion, division by zero, `Math` | §8.3, §16.2 | Types | #55–#57, #93, #102, #143, #188 | `festina/codegen.py` | `test_numeric_conversion.py` |
| `text`, `ascii`, string methods | §8.4, §8.5, §16.3 | Strings; `ascii` | #83, #116, #150, #243, #249, #251, #256, #258, #272 | `runtime/festina_runtime.c` | `test_codegen.py` |
| Arrays, `amor`, maps | §8.7, §8.8 | Arrays; Maps | #26, #62–#65, #72, #96, #130, #156, #174, #175, #184, #186 | `codegen.py`, runtime | `test_maps.py`, `test_loops.py` |
| Structs, enums, `typeof`, `match` | §8.9, §8.11, §9.6, §10.9 | Structs; Enums | #27, #106, #176, #252, #267 | `semantic.py`, `codegen.py` | `test_enums.py` |
| Functions, first-class values, arrows | §8.12, §11.1 | Variables, constants, functions | #23, #24, #140–#142, #187 | `semantic.py` | `test_codegen.py` |
| Control flow, `try`/`catch`/`throw` | §10 | Control flow; `try` / `catch` / `throw` | #17–#20, #60, #61, #73, #157, #193, #236, #259, #274 | `codegen.py` | `test_control_flow.py`, `test_try_catch.py` |
| Memory: escape analysis, refcounts, cycles, `T?`, `free`/`delete` | §13, §8.18, §10.11 | Structs (reclamation paragraphs); Freeing and deleting | #43, #74–#88, #108, #111, #117–#120, #191–#194, #202–#205, #223, #236, #254, #257, #260–#265 | `festina/escape_analysis.py`, `codegen.py`, `runtime/festina_runtime.c` | `test_escape_analysis.py`, `test_manually_managed.py`, `test_leak_stress.py`, `tests/stress/` |
| SQLite: tables, schema sync, queries, rows | §15, §8.10 | Built-in SQLite | #28–#34, #46, #70, #94, #101, #111–#113, #199, #219, #265 | `festina/sqlite_schema.py`, runtime | `test_sqlite_schema.py`, `test_database_url.py` |
| Files, directories, environment, `argv`, `exec`, time | §16 | Files; Directories; Running other programs; Environment variables; Command-line arguments; Time | #71, #93, #109, #110, #132, #150, #221, #272 | runtime | `test_environment.py`, `test_cli.py` |
| Regular expressions | §8.15 | Regex | #67, #68, #85, #86, #107, #118, #122 | runtime | `test_regex.py` |
| JSON rendering and parsing, logging | §8.21, §16.4, §16.5 | Logging and rendering; `.toStruct()` / `.toArr()`; `troubleshoot()` | #114, #115, #158, #159, #173, #190, #192, #206, #223, #233 | runtime, `codegen.py` | `test_json_parse.py`, `test_troubleshoot.py` |
| Graphics: canvas, window, drawing, style, images | §17 | Graphics | #37, #39, #89–#95, #104, #133–#136, #139, #179, #180, #183, #185, #188, #189, #234, #240, #241 | `runtime/festina_runtime_graphics.c`, `festina_runtime_window*.{c,m,h}`, `festina/colors.py` | `test_graphics.py`, `test_events_and_graphics.py` |
| Events and handlers | §11.5, §12.6 | Graphics (mouse and keyboard events) | #40, #98, #106, #131, #178, #181, #182 | `semantic.py` (`_EVENT_SIGNATURES`) | `test_events_and_graphics.py` |
| Audio and channels | §18 | Audio | #38, #98–#100, #109, #121, #127, #146 | `runtime/festina_runtime_audio.c` | `test_audio.py` |
| Timers, background loads, program lifetime, shutdown | §12 | Timers; Files (`.callback()`); `log()` / `fail()` / `close()`; Graceful shutdown | #69, #131, #161, #163, #165, #172, #177 | `runtime/festina_runtime_async.c` | `test_timers.py`, `test_async_io.py`, `test_graceful_shutdown.py` |
| HTTP, WebSocket, client, TLS | §19 | HTTP and WebSocket servers | #151–#155, #160, #162–#168, #247, #248 | `runtime/festina_runtime_http.c`, `festina_runtime_https.c` | `test_http.py`, `test_secure_port.py` |
| Threads, pools, messaging, hand-off | §20 | Threads | #195–#218, #220, #222, #230–#232, #245, #246 | `runtime/festina_runtime_thread.c`, `semantic.py` | `test_threads.py`, `scripts/thread_tsan_stress.sh` |
| Compile errors, runtime failure, undefined behavior | §14 | Error format | #48, #158, #266, #272 | `festina/errors.py` | `test_semantic_errors.py` |
| Compiler pipeline, CLI, linking, install | §21.1–§21.3, §21.6 | CLI; Compilation pipeline | #3, #47, #59, #144, #145, #147, #250, #253 | `festina/cli.py`, `llvm_backend.py`, `bin/festina`, `install.sh`, `packaging/` | `test_cli.py`, `test_llvm_backend.py`, `test_packaging.py` |
| Platforms: Linux, macOS, Windows | §21.4 | [setup.md](setup.md), [macos.md](macos.md), [windows.md](windows.md) | #121–#129, #169, #170, #235, #238 | window and audio backends, `cli.py` (`_check_feature_supported`) | `test_platform.py`, `.github/workflows/ci.yml` |
| `wasm32-wasi` target and browser host | §21.5 | [wasm.md](wasm.md) | #148, #237, #242, #244, #263 | `runtime/wasm/` | `test_wasm.py`, `test_wasm_browser.py` |
| Bootstrapping: the lexer and parser written in Festina, differential tests | — | — | #271–#276 | `bootstrap/lexer.f`, `bootstrap/parser.f`, `bootstrap/difftest.py`, `bootstrap/astdiff.py`, `bootstrap/cases/` | `test_bootstrap_lexer.py`, `test_bootstrap_parser.py` |
| Removed and superseded features | Annex C | — | the entry cited in each row | — | `test_non_goals.py` |
| Security posture | — | [security.md](security.md) | #192–#194 | — | — |
| Benchmarks and measurements | — | [benchmark.md](benchmark.md) | #103, #105, #153, #171, #239, #244, #254, #268 | `benchmarks/` | — |
| What is verified, and how | — | [tests/CONTRACT.md](tests/CONTRACT.md) | #102, #261 | `tests/conftest.py` | — |
| Open work and deliberate non-work | — | [todo.md](todo.md) | — | — | — |
| Version history | — | [CHANGELOG.md](CHANGELOG.md) | — | — | — |

**Which document answers which question**

| Question | Look in |
|---|---|
| Is X part of the language, and exactly how does it behave? | specification.md (then Annex C if you suspect it was removed) |
| What is the exact signature, and is there an example? | api.md |
| Why is X designed this way, and what else was considered? | decisions.md, the entries the index lists |
| Is X actually implemented and tested? | tests/CONTRACT.md, then the test file in the index |
| What does the owner want next, or deliberately not want? | todo.md |
| What changed in which version? | CHANGELOG.md |
| How do I install, or why does a build fail on this platform? | setup.md, macos.md, windows.md, wasm.md |

## 4 Repository map

| Path | Contents |
|---|---|
| `festina/` | The compiler (Python): `lexer.py`, `parser.py`, `ast.py`, `imports.py`, `semantic.py`, `escape_analysis.py`, `types.py`, `sqlite_schema.py`, `codegen.py` (LLVM IR), `llvm_backend.py`, `cli.py`, `compiler.py`, `errors.py`, `colors.py` |
| `runtime/` | The native C runtime: `festina_runtime.c/.h` plus `_graphics`, `_audio`, `_http`, `_https`, `_thread`, `_async`, the window backends, and `wasm/` (the WASI host) |
| `tests/` | The pytest suite, `stress/` and `valgrind_stress/` programs, `fixtures/`, and `CONTRACT.md` |
| `bootstrap/` | Festina's own lexer and parser written in Festina, with differential tests against the Python originals |
| `examples/`, `benchmarks/` | Runnable programs (every example is compiled by the test suite); benchmark suites |
| `bin/festina`, `install.sh`, `packaging/`, `scripts/` | The CLI entry point, installer, packaged-binary build, and stress scripts |
| `docs/`, `editors/` | The documentation site and editor support |

## 5 Conventions

- The compiler executable is `festina`; source files end in `.f`.
- Compile errors are `file:line:column: error: message`.
- Tests and diagnostics name the decision entry that motivated them.
- Version numbers are `major.minor`; the current version is in
  `festina/__init__.py` and README.md.
