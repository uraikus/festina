"""`clear` -- `free` that zeroes the bytes first (specification.md §10.11).

Written against the specification clause before the implementation
existed, per claude.md §2's order: the clause, then these, watched
failing for the right reason, then the code.

The hard part to test is not that `clear` frees -- `free` already does
that and the leak suite already covers it. It is that the bytes are
actually overwritten, which no defined operation can observe: reading a
released buffer from inside the language is undefined (§14.4), and
reading it from the harness would be testing the allocator rather than
the compiler. What can be checked exactly is that `clear` reaches the
zeroing entry point and `free` does not, with the `free` half as the
control.
"""
import pytest


class TestGrammar:

    def test_clear_parses(self, parser, semantic, errors):
        program = parser.parse("text secret = 'hunter2'\nclear secret\n",
                               filename="main.f")
        semantic.analyze(program, filename="main.f")

    def test_clear_is_a_reserved_word(self, parser, errors):
        """§7.4. Reserving it is what makes `int clear = 1` a clear
        error rather than a program that silently shadows a keyword."""
        with pytest.raises(errors.CompileError):
            parser.parse("int clear = 1\n", filename="main.f")

    def test_clear_needs_a_bare_name(self, parser, errors):
        """*ClearStatement* ::= `clear` *Identifier* -- like `free`,
        not an arbitrary expression."""
        with pytest.raises(errors.CompileError):
            parser.parse("struct S { a:int }\nS s\nclear s.a\n",
                         filename="main.f")


class TestAcceptedAndRejectedWhereFreeIs:
    """"accepted wherever `free` is, rejected wherever `free` is" --
    so these mirror the `free` rules rather than inventing new ones."""

    def test_a_constant_cannot_be_cleared(self, parser, semantic, errors):
        src = "const text k = 'x'\nclear k\n"
        with pytest.raises(errors.CompileError):
            semantic.analyze(parser.parse(src, filename="main.f"),
                             filename="main.f")

    def test_an_ordinary_parameter_cannot_be_cleared(self, parser, semantic, errors):
        src = "void func f(t:text) {\n    clear t\n}\n"
        with pytest.raises(errors.CompileError):
            semantic.analyze(parser.parse(src, filename="main.f"),
                             filename="main.f")

    def test_a_manually_managed_parameter_can_be_cleared(self, parser, semantic):
        src = "void func f(b:blob?) {\n    clear b\n}\n"
        semantic.analyze(parser.parse(src, filename="main.f"),
                         filename="main.f")

    def test_an_unknown_name_is_an_error(self, parser, semantic, errors):
        with pytest.raises(errors.CompileError):
            semantic.analyze(parser.parse("clear nope\n", filename="main.f"),
                             filename="main.f")


class TestRuntimeBehaviour:

    def test_clear_nulls_the_binding(self, compile_and_run):
        """"leaves the binding `null` exactly as `free` does"."""
        result = compile_and_run("""
text secret = 'hunter2'
clear secret
log(`${secret == null}`)
""")
        assert result.stdout.strip() == "true"

    def test_clearing_twice_is_a_no_op(self, compile_and_run):
        result = compile_and_run("""
text secret = 'hunter2'
clear secret
clear secret
log('survived')
""")
        assert result.stdout.strip() == "survived"

    def test_a_struct_can_be_cleared(self, compile_and_run):
        result = compile_and_run("""
struct Person { name:text  token:text }
Person? brad
brad.name = 'Brad'
brad.token = 'sk-secret'
clear brad
log('cleared')
""")
        assert result.stdout.strip() == "cleared"

    def test_an_array_can_be_cleared(self, compile_and_run):
        result = compile_and_run("""
arr[text] keys = ['alpha', 'beta']
clear keys
log(`${keys == null}`)
""")
        assert result.stdout.strip() == "true"

    def test_a_scalar_clear_degenerates_to_null(self, compile_and_run):
        result = compile_and_run("""
int n = 7
clear n
log(`${n == null}`)
""")
        assert result.stdout.strip() == "true"


class TestTheBytesAreReallyZeroed:
    """The claim the statement exists to make.

    A released buffer is not readable from inside the language by any
    defined means, so this reads it back through the one type that can
    address raw bytes at all, and compares `clear` against `free` on
    identical programs. The `free` case is the control: if BOTH come
    back zeroed, the test proves nothing about `clear` -- it proves the
    allocator happened to zero the page.
    """

    def _ir(self, parser, semantic, codegen, statement):
        src = ("text secret = 'SECRETSECRETSECRETSECRETSECRETSECRETSECRET"
               "SECRETSECRETSECRETSECR'\n%s secret\n" % statement)
        program = parser.parse(src, filename="main.f")
        analyzed = semantic.analyze(program, filename="main.f")
        return codegen.generate_ir(program, analyzed, filename="main.f")

    def test_clear_emits_a_zeroing_call_and_free_does_not(
            self, parser, semantic, codegen):
        """The observable difference between the two statements, at the
        only layer where it IS observable.

        Reading a released buffer back from inside the language is
        undefined (specification.md 14.4) and reading it from the test
        harness would be testing the allocator, not the compiler. What
        can be checked exactly is that `clear` reaches the zeroing entry
        point and `free` does not -- and the `free` half is the control:
        if both emitted the call, the test would prove nothing."""
        cleared = self._ir(parser, semantic, codegen, "clear")
        freed = self._ir(parser, semantic, codegen, "free")
        # The CALL, not the declaration: every module declares the
        # runtime's entry points unconditionally, so matching the bare
        # name passes for `free` too. The control half of this test is
        # what caught that.
        assert "call void @festina_clear_text" in cleared
        assert "call void @festina_clear_text" not in freed
        assert "call void @free" in freed


class TestClearDoesNotWipeASurvivingAlias:
    """"A value another binding still holds is neither freed nor
    zeroed" -- overwriting a buffer another binding can still read
    would be a use-after-free by construction, so the safe rule is the
    specified one."""

    def test_an_aliased_array_survives_clear_intact(self, compile_and_run):
        result = compile_and_run("""
arr[text] a = ['alpha', 'beta']
arr[text] b = a
clear a
log(b[0])
log(b[1])
""")
        assert result.stdout.split() == ["alpha", "beta"]

    def test_an_aliased_struct_survives_clear_intact(self, compile_and_run):
        result = compile_and_run("""
struct Person { name:text  token:text }
Person p
p.name = 'Brad'
p.token = 'sk-secret'
Person q = p
clear p
log(q.name)
log(q.token)
""")
        assert result.stdout.split() == ["Brad", "sk-secret"]


class TestTheCascade:
    """decisions.md #284: zeroing follows the release cascade, so a
    struct's fields and a container's elements go too.

    Checked at the IR layer for the same reason the `text` case is: a
    released buffer has no defined reading. What IS checkable is that
    the cascade's free sites reach the zeroing allocator entry point
    rather than plain `free`, and that the clearing intent is scoped to
    the one statement rather than left switched on.
    """

    def _ir(self, parser, semantic, codegen, src):
        program = parser.parse(src, filename="main.f")
        analyzed = semantic.analyze(program, filename="main.f")
        return codegen.generate_ir(program, analyzed, filename="main.f")

    def test_clearing_a_struct_brackets_the_release(self, parser, semantic, codegen):
        """The intent has to reach a cascade the caller does not walk
        itself -- a generated per-struct release function frees each
        field and then the header -- so it travels as runtime state set
        around the release, not as an argument."""
        ir = self._ir(parser, semantic, codegen, """
struct Person { name:text  token:text }
Person? brad
brad.name = 'Brad'
brad.token = 'sk'
clear brad
""")
        assert "call void @festina_begin_clearing()" in ir
        assert "call void @festina_end_clearing()" in ir

    def test_freeing_a_struct_does_not(self, parser, semantic, codegen):
        """The control. Without it the assertions above would pass on a
        compiler that switched clearing on unconditionally."""
        ir = self._ir(parser, semantic, codegen, """
struct Person { name:text  token:text }
Person? brad
brad.name = 'Brad'
brad.token = 'sk'
free brad
""")
        assert "call void @festina_begin_clearing()" not in ir

    def test_clearing_an_array_brackets_the_release(self, parser, semantic, codegen):
        ir = self._ir(parser, semantic, codegen, """
arr[text] keys = ['alpha', 'beta']
clear keys
""")
        assert "call void @festina_begin_clearing()" in ir

    def test_a_structs_text_field_is_freed_through_the_zeroing_path(
            self, parser, semantic, codegen):
        """The site that matters most, and the one first missed.

        A struct holding a secret holds it in a text FIELD. Wiping only
        the struct's own storage would leave the secret itself sitting
        in the heap, and `clear` would be a lie for its most obvious
        use. This was routed through plain @free until an unrelated
        codegen test's free-counting assertion pointed at it."""
        ir = self._ir(parser, semantic, codegen, """
struct Creds { user:text  token:text }
Creds? c
c.user = 'brad'
c.token = 'sk'
clear c
""")
        wrapper = ir.split("define void @__festina_release_struct_Creds", 1)[1]
        wrapper = wrapper.split("\n}", 1)[0]
        # Two text fields plus the struct header, none through plain
        # @free -- an unzeroed free here is a secret left in the heap.
        assert wrapper.count("call void @festina_free_z(") == 3
        assert "call void @free(" not in wrapper

    def test_a_generated_struct_release_frees_through_the_zeroing_path(
            self, parser, semantic, codegen):
        """The cascade's own free sites must consult the flag, or
        bracketing the call would set state nothing reads."""
        ir = self._ir(parser, semantic, codegen, """
struct Inner { a:text }
struct Outer { inner:Inner  b:text }
Outer? o
clear o
""")
        assert "define void @__festina_release_struct_Outer" in ir
        cascade = ir.split("define void @__festina_release_struct_Outer", 1)[1]
        cascade = cascade.split("\n}", 1)[0]
        assert "@festina_free_z" in cascade, (
            "the generated cascade still frees through plain @free, so "
            "the clearing flag would never be consulted")


class TestTheWipeActuallyHappens:
    """The one claim nothing else in this file proves.

    Every other test here checks that `clear` reaches the zeroing path.
    None of them checks that the path writes zeros -- and it is exactly
    the kind of code a C compiler is entitled to delete, since the
    storage is about to be freed. So this compiles the runtime at -O2
    and inspects the bytes directly, which is the only place the answer
    is observable.
    """

    PROBE = r"""
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
void festina_zeroize(void *p);
void festina_begin_clearing(void);
void festina_end_clearing(void);

int main(void) {
    char *p = malloc(64);
    memset(p, 'S', 64);
    festina_zeroize(p);
    int nonzero = 0;
    for (int i = 0; i < 64; i++) if (p[i]) nonzero++;

    char *a = malloc(64); memset(a, 'A', 64);
    char *b = malloc(64); memset(b, 'B', 64);
    festina_begin_clearing();
    festina_zeroize(a);
    festina_end_clearing();
    int az = 0, bz = 0;
    for (int i = 0; i < 64; i++) { if (!a[i]) az++; if (!b[i]) bz++; }
    printf("%d %d %d\n", nonzero, az, bz);
    free(p); free(a); free(b);
    return 0;
}
"""

    def test_zeroize_writes_zeros_at_O2(self, tmp_path):
        import subprocess
        import os
        from tests.conftest import _require_c_compiler
        cc = _require_c_compiler()
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        src = tmp_path / "zprobe.c"
        src.write_text(self.PROBE)
        exe = tmp_path / "zprobe"
        build = subprocess.run(
            [cc, "-O2", "-I", os.path.join(root, "runtime"), str(src),
             os.path.join(root, "runtime", "festina_runtime.c"),
             "-o", str(exe), "-lsqlite3", "-lm", "-lpthread", "-ldl"],
            capture_output=True, text=True, encoding="utf-8")
        if build.returncode != 0:
            pytest.skip(f"cannot link the runtime here: {build.stderr[-300:]}")
        out = subprocess.run([str(exe)], capture_output=True, text=True,
                             encoding="utf-8", timeout=60)
        nonzero, cleared, untouched = (int(x) for x in out.stdout.split())
        # -O2 is the point: a store to memory about to be freed is what
        # a compiler is entitled to delete, and the volatile pointer in
        # festina_zeroize is the defence. If this ever reports nonzero
        # bytes, that defence has stopped working.
        assert nonzero == 0, f"{nonzero} of 64 bytes survived zeroize at -O2"
        assert cleared == 64
        # The control: an untouched buffer must NOT come back zeroed, or
        # the test is measuring the allocator rather than the wipe.
        assert untouched == 0
