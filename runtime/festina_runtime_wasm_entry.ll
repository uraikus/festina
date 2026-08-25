; claude.md #148/#150: WASM export -- the wasm32-wasi entry-point bridge.
;
; A real, direct discovery, not something documented anywhere obvious
; ahead of time: wasi-libc's own `_start` doesn't call a function
; literally named `main` at all. Compiling ordinary C source through
; clang's normal frontend, `<stdlib.h>`/the crt's own macro machinery
; silently renames a user's `int main(void)` to `__main_void` (or
; `int main(int, char**)` to `__main_argc_argv`) before the compiler
; ever sees it, so `libc.a`'s own weak default definition for whichever
; one matches gets overridden by the real one. Festina's own codegen
; emits raw LLVM IR text directly (see festina/codegen.py's
; `_emit_main_and_entry`) -- it is never run through that C-frontend
; macro at all, so the IR's own literal `define i32 @main(...)` links
; clean on every native target (where `main` really is the expected
; symbol) but leaves wasi-libc's `_start` -> `__main_*` chain looking
; for a symbol nothing defines, confirmed directly as a real
; "wasm-ld: undefined symbol: main" trap on an actual wasm32-wasi link
; before this file existed.
;
; claude.md #150: THIS FILE IS RAW LLVM IR, NOT C -- deliberately, and
; not merely a style choice. A first version of this bridge, written
; in C (`extern int main(int, char**); int __main_argc_argv(int argc,
; char **argv) { return main(argc, argv); }`), compiled and LINKED
; without error but then hung/crashed at actual runtime -- a real,
; hard-won discovery, confirmed by direct disassembly + relocation
; inspection (`llvm-objdump -r`), not guessed at: the SAME C-frontend
; macro that renames a *defined* `int main(int, char**)` to
; `__main_argc_argv` ALSO rewrites any *reference* to the identifier
; `main` in ANY translation unit compiled for this target -- including
; an `extern` declaration and the call built from it. That bridge's own
; `return main(argc, argv)` silently became `return
; __main_argc_argv(argc, argv)` -- calling ITSELF, not Festina's real
; `main` -- confirmed directly via the compiled object's own relocation
; record (`R_WASM_FUNCTION_INDEX_LEB __main_argc_argv+0` at the call
; site, not a reference to `main` at all). At -O0 this produced real,
; visible infinite recursion (a `RuntimeError: memory access out of
; bounds` trap, main calling main calling main in the stack trace); at
; -O2 (this project's own real build flags) the same self-call instead
; became a silent infinite loop -- indistinguishable from a hang, no
; error at all, confirmed as the actual root cause by rebuilding with a
; DIFFERENT function name (`real_main`, unaffected by the macro) in an
; otherwise-identical C bridge and watching the hang disappear.
; Bypassing the C frontend entirely for this one file -- raw IR text
; is used completely verbatim, with no preprocessor pass at all -- is
; what actually fixes this: `declare i32 @main(...)` here can only ever
; mean the real external symbol, never a renamed one, which was
; verified directly (confirmed via the SAME relocation inspection:
; `U main`, not `U __main_argc_argv`) before this replaced the earlier
; C version.
;
; Renaming Festina's own generated `main` symbol in codegen.py itself
; was the other option (for the original, void-arg version of this
; bridge) and was rejected: it would have made codegen target-aware for
; something that is really wasi-libc's own linking convention, not a
; property of the generated program. A minimal bridge object, linked
; only for the wasm32-wasi build (see festina/cli.py's target-specific
; runtime-object selection -- completely absent from every native
; target's own link line), is still the right fix; it just has to be
; written in a form the C frontend's own renaming can't reach.
declare i32 @main(i32 %argc, ptr %argv)

define i32 @__main_argc_argv(i32 %argc, ptr %argv) {
entry:
  %r = call i32 @main(i32 %argc, ptr %argv)
  ret i32 %r
}
