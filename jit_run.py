#!/usr/bin/env python3
"""
Runs a .ll (LLVM IR) file by JIT-compiling it with the real LLVM engine,
using libLLVM's C API directly via ctypes. This lets us execute the
compiler's output in this container even though no `clang`/`lli`/`llc`
binaries are installed here -- only the raw libLLVM shared library.

On a normal dev machine with a full LLVM toolchain you would instead do:
    clang -O2 out.ll runtime/runtime.c -lsqlite3 -lcairo -lm -o program
    ./program
which is simpler and produces a native standalone binary (see build.sh).
"""
import ctypes
import sys
import os

LLVM = ctypes.CDLL('/usr/lib/x86_64-linux-gnu/libLLVM-20.so')

# ---- minimal LLVM-C API bindings (just what we need) ----
c_char_p = ctypes.c_char_p
c_void_p = ctypes.c_void_p
c_int = ctypes.c_int
c_uint = ctypes.c_uint
c_uint64 = ctypes.c_uint64

LLVM.LLVMContextCreate.restype = c_void_p
LLVM.LLVMCreateMemoryBufferWithMemoryRangeCopy.restype = c_void_p
LLVM.LLVMCreateMemoryBufferWithMemoryRangeCopy.argtypes = [c_char_p, ctypes.c_size_t, c_char_p]

LLVM.LLVMParseIRInContext.restype = ctypes.c_bool
LLVM.LLVMParseIRInContext.argtypes = [c_void_p, c_void_p, ctypes.POINTER(c_void_p), ctypes.POINTER(c_char_p)]

LLVM.LLVMVerifyModule.restype = ctypes.c_bool
LLVM.LLVMVerifyModule.argtypes = [c_void_p, c_int, ctypes.POINTER(c_char_p)]

LLVM.LLVMLinkInMCJIT.restype = None
# "Native target" init is normally a macro (LLVMInitializeNativeTarget) that expands
# to the host arch's init functions at compile time in C -- as a shared lib it only
# exports the per-arch symbols, so call the x86 ones directly (this container is x86_64).
LLVM.LLVMInitializeX86Target.restype = None
LLVM.LLVMInitializeX86TargetInfo.restype = None
LLVM.LLVMInitializeX86TargetMC.restype = None
LLVM.LLVMInitializeX86AsmPrinter.restype = None

LLVM.LLVMCreateExecutionEngineForModule.restype = ctypes.c_bool
LLVM.LLVMCreateExecutionEngineForModule.argtypes = [ctypes.POINTER(c_void_p), c_void_p, ctypes.POINTER(c_char_p)]

LLVM.LLVMAddGlobalMapping.restype = None
LLVM.LLVMAddGlobalMapping.argtypes = [c_void_p, c_void_p, c_void_p]

LLVM.LLVMGetNamedFunction.restype = c_void_p
LLVM.LLVMGetNamedFunction.argtypes = [c_void_p, c_char_p]

LLVM.LLVMRunFunctionAsMain.restype = c_int
LLVM.LLVMRunFunctionAsMain.argtypes = [c_void_p, c_void_p, c_uint, ctypes.POINTER(c_char_p), ctypes.POINTER(c_char_p)]

# Runtime functions the JIT'd module calls extern; we resolve them by loading
# libjsruntime.so into our own process and letting MCJIT's default symbol
# resolution (dlsym on the process) find them -- simplest approach: dlopen
# with RTLD_GLOBAL before creating the engine.


def main():
    if len(sys.argv) < 2:
        print('usage: jit_run.py program.ll [runtime.so]', file=sys.stderr)
        sys.exit(1)
    ll_path = sys.argv[1]
    runtime_so = sys.argv[2] if len(sys.argv) > 2 else os.path.join(
        os.path.dirname(__file__), 'runtime', 'libjsruntime.so')

    # Load runtime symbols into the process global symbol table so MCJIT can resolve them.
    ctypes.CDLL(runtime_so, mode=ctypes.RTLD_GLOBAL)

    with open(ll_path, 'rb') as f:
        ir_text = f.read()

    LLVM.LLVMInitializeX86TargetInfo()
    LLVM.LLVMInitializeX86Target()
    LLVM.LLVMInitializeX86TargetMC()
    LLVM.LLVMInitializeX86AsmPrinter()
    LLVM.LLVMLinkInMCJIT()

    ctx = LLVM.LLVMContextCreate()
    buf = LLVM.LLVMCreateMemoryBufferWithMemoryRangeCopy(ir_text, len(ir_text), b'module')

    mod = c_void_p()
    err = c_char_p()
    failed = LLVM.LLVMParseIRInContext(ctx, buf, ctypes.byref(mod), ctypes.byref(err))
    if failed:
        print('IR parse error:', err.value.decode() if err.value else '?', file=sys.stderr)
        sys.exit(1)

    verr = c_char_p()
    # LLVMVerifierFailureAction: 1 = ReturnStatusAction (don't abort process)
    bad = LLVM.LLVMVerifyModule(mod, 1, ctypes.byref(verr))
    if bad:
        print('IR verify error:', verr.value.decode() if verr.value else '?', file=sys.stderr)
        sys.exit(1)

    engine = c_void_p()
    eerr = c_char_p()
    failed = LLVM.LLVMCreateExecutionEngineForModule(ctypes.byref(engine), mod, ctypes.byref(eerr))
    if failed:
        print('engine create error:', eerr.value.decode() if eerr.value else '?', file=sys.stderr)
        sys.exit(1)

    main_fn = LLVM.LLVMGetNamedFunction(mod, b'main')
    if not main_fn:
        print('no main() found in module', file=sys.stderr)
        sys.exit(1)

    argv0 = (c_char_p)(b'program')
    argv_arr = (c_char_p * 1)(b'program')
    rc = LLVM.LLVMRunFunctionAsMain(engine, main_fn, 1, argv_arr, None)
    sys.exit(rc)


if __name__ == '__main__':
    main()
