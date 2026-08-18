"""Drive libLLVM directly (via ctypes and its C API) to turn Festina's
generated LLVM IR into an object file, in-process -- no `clang`/`llc`
subprocess involved for this step.

"Real compilation, minimal setup" stage 3 (claude.md #59; see setup.md
for the full staged plan). Historically
`festina/cli.py` handed the whole `.ll` file to `clang`, which
understands LLVM IR text as one
of its accepted input formats. That's a *clang-specific* frontend
feature -- plain `cc`/`gcc` doesn't recognize `.ll` at all (verified:
gcc hands it to `ld`, which treats it as a broken linker script and
fails). Compiling the IR ourselves means the only thing left for an
external C compiler to do is compile the runtime translation units
(festina_runtime.c/_graphics.c/_audio.c) and link plain object files
together -- work gcc can do exactly as well as clang,
which is the actual point: it's no longer clang *specifically* that
using Festina depends on, just "some C compiler," a meaningfully
smaller ask.

Uses ctypes against libLLVM's C API directly -- suitable for
environments with libLLVM but no clang/llc CLI tools -- for
ahead-of-time object-file emission via LLVMTargetMachineEmitToFile
(as opposed to MCJIT execution).

If libLLVM can't be found or loaded, `available()` returns False and
festina/cli.py falls back to shelling out to clang on the .ll file
directly, exactly like before this module existed -- this is additive,
not a replacement path that could regress anything.
"""
import ctypes
import ctypes.util
import os
import platform
import sys

# Reloc mode: LLVMRelocPIC. Needed to match this system's PIE-by-default
# linking -- LLVMRelocDefault (0) produces relocations `ld` rejects when
# linking a PIE (verified: "relocation R_X86_64_32 ... can not be used
# when making a PIE object").
_RELOC_PIC = 2
_CODEGEN_OPT_DEFAULT = 2  # LLVMCodeGenLevelDefault
_CODE_MODEL_DEFAULT = 0   # LLVMCodeModelDefault
_OBJECT_FILE = 1          # LLVMObjectFile (LLVMCodeGenFileType)
# LLVMRunPasses pipeline text -- matches what `clang -O2` runs on IR
# before handing it to the backend (see the _bind() docstring note).
_OPT_PASSES = "default<O2>"

# LLVMInitializeX86Target() etc. are per-architecture symbols (the
# "native target" macro clang normally expands at compile time doesn't
# exist as a single exported symbol in the shared library) -- map the
# Python-reported machine name to the LLVM target-init prefix.
_ARCH_INIT_PREFIX = {
    "x86_64": "X86", "amd64": "X86",
    "aarch64": "AArch64", "arm64": "AArch64",
}


class LLVMBackendError(Exception):
    pass


def _platform_libllvm_paths(platform_name=None, environ=None):
    """Explicit per-platform locations ctypes.util.find_library cannot
    discover on its own -- macos.md/windows.md Phase 0. Homebrew's LLVM
    is keg-only (never on the default dyld search path), and MSYS2's
    DLLs live under the active MinGW environment's bin directory (named
    by $MSYSTEM_PREFIX inside an MSYS2 shell) or the stock install
    roots. Pure function of its inputs so each platform's list is
    unit-testable from any platform (tests/test_platform.py); on Linux
    find_library alone already works, so the list is empty."""
    platform_name = platform_name or sys.platform
    environ = os.environ if environ is None else environ
    if platform_name == "darwin":
        return [
            "/opt/homebrew/opt/llvm/lib/libLLVM.dylib",   # arm64 brew
            "/usr/local/opt/llvm/lib/libLLVM.dylib",      # x86_64 brew
        ]
    if platform_name == "win32":
        roots = [environ.get("MSYSTEM_PREFIX"),
                 r"C:\msys64\ucrt64", r"C:\msys64\mingw64", r"C:\msys64\clang64"]
        paths = []
        for root in roots:
            if not root:
                continue
            paths.append(os.path.join(root, "bin", "libLLVM.dll"))
            paths.extend(os.path.join(root, "bin", f"libLLVM-{v}.dll")
                         for v in range(20, 12, -1))
        return paths
    return []


def _find_libllvm():
    name = ctypes.util.find_library("LLVM")
    candidates = [name] if name else []
    candidates += [ctypes.util.find_library(f"LLVM-{v}") for v in range(20, 12, -1)]
    candidates += _platform_libllvm_paths()
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return ctypes.CDLL(candidate)
        except OSError:
            continue
    return None


class _Binding:
    """Lazily loads libLLVM and binds just the C API calls this module
    needs. A single instance is reused (see `_binding()`) so the shared
    library is only opened once per process."""

    def __init__(self):
        self.lib = _find_libllvm()
        if self.lib is None:
            return
        arch_prefix = _ARCH_INIT_PREFIX.get(platform.machine().lower())
        if arch_prefix is None:
            self.lib = None  # unsupported architecture -- caller falls back to clang
            return
        self._bind(arch_prefix)

    def _bind(self, arch_prefix):
        lib = self.lib
        c_char_p, c_void_p, c_int = ctypes.c_char_p, ctypes.c_void_p, ctypes.c_int

        def fn(name, restype, argtypes):
            f = getattr(lib, name)
            f.restype = restype
            f.argtypes = argtypes
            return f

        self.init_target_info = fn(f"LLVMInitialize{arch_prefix}TargetInfo", None, [])
        self.init_target = fn(f"LLVMInitialize{arch_prefix}Target", None, [])
        self.init_target_mc = fn(f"LLVMInitialize{arch_prefix}TargetMC", None, [])
        self.init_asm_printer = fn(f"LLVMInitialize{arch_prefix}AsmPrinter", None, [])

        self.context_create = fn("LLVMContextCreate", c_void_p, [])
        self.create_membuf = fn("LLVMCreateMemoryBufferWithMemoryRangeCopy", c_void_p,
                                 [c_char_p, ctypes.c_size_t, c_char_p])
        self.parse_ir = fn("LLVMParseIRInContext", ctypes.c_bool,
                            [c_void_p, c_void_p, ctypes.POINTER(c_void_p), ctypes.POINTER(c_char_p)])
        self.verify_module = fn("LLVMVerifyModule", ctypes.c_bool,
                                 [c_void_p, c_int, ctypes.POINTER(c_char_p)])
        self.dispose_message = fn("LLVMDisposeMessage", None, [c_char_p])

        self.default_target_triple = fn("LLVMGetDefaultTargetTriple", c_char_p, [])
        self.host_cpu_name = fn("LLVMGetHostCPUName", c_char_p, [])
        self.host_cpu_features = fn("LLVMGetHostCPUFeatures", c_char_p, [])
        self.set_target = fn("LLVMSetTarget", None, [c_void_p, c_char_p])
        self.get_target_from_triple = fn("LLVMGetTargetFromTriple", ctypes.c_bool,
                                          [c_char_p, ctypes.POINTER(c_void_p), ctypes.POINTER(c_char_p)])
        self.create_target_machine = fn("LLVMCreateTargetMachine", c_void_p,
                                         [c_void_p, c_char_p, c_char_p, c_char_p, c_int, c_int, c_int])
        self.emit_to_file = fn("LLVMTargetMachineEmitToFile", ctypes.c_bool,
                                [c_void_p, c_void_p, c_char_p, c_int, ctypes.POINTER(c_char_p)])

        # LLVMRunPasses (the "new pass manager" C API, LLVM 13+): runs the
        # actual IR-level optimization pipeline (mem2reg, inlining,
        # tail-call opt, GVN, ...) that CodeGenOptLevel alone does NOT --
        # that only tunes the backend's instruction selection/scheduling.
        # Without this, every local var stays a stack alloca with
        # explicit reloads and no call ever gets inlined/tail-call
        # optimized; verified concretely (see the project's benchmark
        # discussion) that skipping this step alone accounted for
        # Festina running ~2x slower than clang/gcc -O2 on identical
        # logic -- running "default<O2>" here closed nearly the entire
        # gap. `clang -O2` on a .ll file (the fallback path) already gets
        # this for free from its own driver; this fast path has to ask
        # for it explicitly since it talks to libLLVM directly.
        self.create_pass_builder_options = fn("LLVMCreatePassBuilderOptions", c_void_p, [])
        self.dispose_pass_builder_options = fn("LLVMDisposePassBuilderOptions", None, [c_void_p])
        self.run_passes = fn("LLVMRunPasses", c_void_p,
                              [c_void_p, c_char_p, c_void_p, c_void_p])
        self.get_error_message = fn("LLVMGetErrorMessage", c_char_p, [c_void_p])
        self.dispose_error_message = fn("LLVMDisposeErrorMessage", None, [c_char_p])


_binding_instance = None


def _binding():
    global _binding_instance
    if _binding_instance is None:
        _binding_instance = _Binding()
    return _binding_instance


def available():
    """True if libLLVM was found, loaded, and this process's architecture
    is one this module knows the target-init symbol names for."""
    return _binding().lib is not None


def emit_object_file(ir_text, out_path, filename="<ir>"):
    """Compile LLVM IR text to a native object file at `out_path`, using
    libLLVM directly. Raises LLVMBackendError on any failure (bad IR,
    verifier failure, target/codegen error) -- callers should treat that
    the same as a clang failure, not fall back silently mid-compile."""
    b = _binding()
    if b.lib is None:
        raise LLVMBackendError("libLLVM is not available in this process")

    b.init_target_info()
    b.init_target()
    b.init_target_mc()
    b.init_asm_printer()

    ir_bytes = ir_text.encode("utf-8")
    ctx = b.context_create()
    buf = b.create_membuf(ir_bytes, len(ir_bytes), filename.encode("utf-8", "replace"))

    mod = ctypes.c_void_p()
    err = ctypes.c_char_p()
    if b.parse_ir(ctx, buf, ctypes.byref(mod), ctypes.byref(err)):
        message = err.value.decode(errors="replace") if err.value else "unknown parse error"
        raise LLVMBackendError(f"LLVM IR parse error: {message}")

    verify_err = ctypes.c_char_p()
    # LLVMVerifierFailureAction = 1 (ReturnStatusAction): report, don't abort the process.
    if b.verify_module(mod, 1, ctypes.byref(verify_err)):
        message = verify_err.value.decode(errors="replace") if verify_err.value else "unknown verify error"
        raise LLVMBackendError(f"LLVM IR verification failed: {message}")

    triple = b.default_target_triple()
    b.set_target(mod, triple)
    target = ctypes.c_void_p()
    target_err = ctypes.c_char_p()
    if b.get_target_from_triple(triple, ctypes.byref(target), ctypes.byref(target_err)):
        message = target_err.value.decode(errors="replace") if target_err.value else "unknown target error"
        raise LLVMBackendError(f"could not resolve target '{triple.decode()}': {message}")

    cpu = b.host_cpu_name()
    features = b.host_cpu_features()
    tm = b.create_target_machine(target, triple, cpu, features,
                                  _CODEGEN_OPT_DEFAULT, _RELOC_PIC, _CODE_MODEL_DEFAULT)
    if not tm:
        raise LLVMBackendError("failed to create an LLVM target machine")

    # See the _bind() docstring note on run_passes: this is the step that
    # actually optimizes the IR (mem2reg, inlining, tail-call opt, ...),
    # not just tunes backend instruction selection.
    pb_options = b.create_pass_builder_options()
    try:
        pass_err = b.run_passes(mod, _OPT_PASSES.encode("ascii"), tm, pb_options)
        if pass_err:
            msg_ptr = b.get_error_message(pass_err)
            message = msg_ptr.decode(errors="replace") if msg_ptr else "unknown optimization error"
            if msg_ptr:
                b.dispose_error_message(msg_ptr)
            raise LLVMBackendError(f"LLVM optimization passes failed: {message}")
    finally:
        b.dispose_pass_builder_options(pb_options)

    emit_err = ctypes.c_char_p()
    if b.emit_to_file(tm, mod, out_path.encode("utf-8"), _OBJECT_FILE, ctypes.byref(emit_err)):
        message = emit_err.value.decode(errors="replace") if emit_err.value else "unknown codegen error"
        raise LLVMBackendError(f"object emission failed: {message}")
