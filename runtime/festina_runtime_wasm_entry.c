/* claude.md #148: WASM export -- the wasm32-wasi entry-point bridge.
 *
 * A real, direct discovery, not something documented anywhere obvious
 * ahead of time: wasi-libc's own `_start` doesn't call a function
 * literally named `main` at all. Compiling ordinary C source through
 * clang's normal frontend, `<stdlib.h>`/the crt's own macro machinery
 * silently renames a user's `int main(void)` to `__main_void` (or
 * `int main(int, char**)` to `__main_argc_argv`) before the compiler
 * ever sees it, so `libc.a(__main_void.o)`'s own weak default
 * definition gets overridden by the real one. Festina's own codegen
 * emits raw LLVM IR text directly (see festina/codegen.py's
 * `_emit_main_and_entry`) -- it is never run through that C-frontend
 * macro at all, so the IR's own literal `define i32 @main()` links
 * clean on every native target (where `main` really is the expected
 * symbol) but leaves wasi-libc's `_start` -> `__main_void` chain
 * looking for a symbol nothing defines, confirmed directly as a real
 * "wasm-ld: undefined symbol: main" trap on an actual wasm32-wasi link
 * before this file existed.
 *
 * Renaming Festina's own generated `main` symbol in codegen.py itself
 * was the other option and was rejected: it would have made codegen
 * target-aware for something that is really wasi-libc's own linking
 * convention, not a property of the generated program. A one-line
 * bridge object, linked only for the wasm32-wasi build (see
 * festina/cli.py's target-specific runtime-object selection --
 * completely absent from every native target's own link line), is the
 * whole fix. */
extern int main(void);
int __main_void(void) { return main(); }
