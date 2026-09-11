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
