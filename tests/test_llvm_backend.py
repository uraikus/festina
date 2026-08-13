"""festina.llvm_backend -- "real compilation, minimal setup" stage 3.

Drives libLLVM's C API directly (via ctypes) to turn LLVM IR text into
an object file in-process, instead of handing a .ll file to `clang`
(the only common compiler with an LLVM-IR-text frontend). See the
module's own docstring and festina/cli.py's for the full rationale.
These tests only need libLLVM itself, not a full clang/gcc toolchain --
skip cleanly (distinct reason) if it can't be found/loaded, which is a
narrower requirement than test_codegen.py's compile_and_run fixture.
"""
import pytest


@pytest.fixture
def skip_if_no_libllvm(llvm_backend):
    if not llvm_backend.available():
        pytest.skip("libLLVM could not be found/loaded in this process -- "
                     "festina.cli falls back to clang's IR frontend in that case")


class TestAvailability:
    def test_available_is_a_bool(self, llvm_backend):
        assert isinstance(llvm_backend.available(), bool)

    def test_available_is_stable_across_calls(self, llvm_backend):
        # _binding() memoizes -- shouldn't reload the shared library or
        # flip its answer between calls in the same process.
        assert llvm_backend.available() == llvm_backend.available()


class TestEmitObjectFile:
    MINIMAL_IR = """
    define i32 @main() {
    entry:
      ret i32 0
    }
    """

    def test_emits_a_valid_elf_object(self, llvm_backend, tmp_path, skip_if_no_libllvm):
        out = tmp_path / "out.o"
        llvm_backend.emit_object_file(self.MINIMAL_IR, str(out))
        assert out.exists()
        # ELF magic bytes -- claude.md #47 wants a native object, not a
        # bitcode blob or anything else libLLVM's other emit modes produce.
        assert out.read_bytes()[:4] == b"\x7fELF"

    def test_real_codegen_output_compiles_to_an_object(self, llvm_backend, parser, semantic, codegen, tmp_path, skip_if_no_libllvm):
        program = parser.parse("log('hello')")
        analyzed = semantic.analyze(program)
        ir = codegen.generate_ir(program, analyzed)
        out = tmp_path / "out.o"
        llvm_backend.emit_object_file(ir, str(out))
        assert out.read_bytes()[:4] == b"\x7fELF"

    def test_invalid_ir_raises_a_clear_error(self, llvm_backend, skip_if_no_libllvm, tmp_path):
        out = tmp_path / "out.o"
        with pytest.raises(llvm_backend.LLVMBackendError):
            llvm_backend.emit_object_file("this is not valid LLVM IR {{{", str(out))

    def test_invalid_ir_does_not_leave_a_partial_object_file(self, llvm_backend, skip_if_no_libllvm, tmp_path):
        out = tmp_path / "out.o"
        with pytest.raises(llvm_backend.LLVMBackendError):
            llvm_backend.emit_object_file("not valid IR", str(out))
        assert not out.exists()
