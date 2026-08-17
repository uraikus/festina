"""Code generation -- claude.md #47 (executable generation), plus the
runtime-facing halves of #7/#8 (entry point + startup), #26 (arrays),
#28-34 (automatic SQLite schema sync, sqlite() queries), #41/#42
(log/fail), #45 (string interpolation).

Two kinds of tests here:

- Tests needing no C toolchain at all -- either they only check that
  festina.codegen raises a CompileError (nothing left actually needs
  this treatment; every claude.md construct this compiler targets now
  generates real code), or, like TestUnrecognizedEventName below, they
  only inspect the generated IR text directly.
- End-to-end tests actually compile a Festina program to a native
  executable (via the `compile_and_run` fixture) and check its real
  stdout/exit code/festina.sqlite -- these skip cleanly if no C compiler
  is on PATH, since that's an environment limitation, not a missing
  Festina feature.
"""
import os
import shutil
import sqlite3
import struct
import subprocess
import time
import wave

import pytest

_EXAMPLES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples")
# claude.md #101: real JPEG/MP3 files, committed rather than generated,
# because nothing in this repo can encode either and a test that only
# exercised a hand-rolled approximation would prove nothing about
# libjpeg/libmpg123 actually being wired up. Both are tiny (a 16x16
# gradient and a fifth of a second of a 440Hz tone).
_FIXTURES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
_JPEG_FIXTURE = os.path.join(_FIXTURES_DIR, "gradient.jpg")
_MP3_FIXTURE = os.path.join(_FIXTURES_DIR, "tone.mp3")


# ---- no C toolchain needed -- IR-text-only checks ----

class TestUnrecognizedEventName:
    def _generate(self, parser, semantic, codegen, source, filename="main.f"):
        program = parser.parse(source, filename=filename)
        analyzed = semantic.analyze(program, filename=filename)
        return codegen.generate_ir(program, analyzed, filename=filename)

    def test_unrecognized_event_name_still_compiles_but_is_never_called(self, parser, semantic, codegen):
        # claude.md #40 only ever shows mouse and keyboard events --
        # any other
        # name still compiles (it's checked like any other code) but
        # there's no event source for it, so it's simply dead code, not
        # a compile error. See TestGraphics for the same point end to
        # end (the declared handler never fires).
        source = "on somethingElse(a:int) {\n    log(a)\n}"
        ir = self._generate(parser, semantic, codegen, source)
        assert "@__festina_on_somethingElse" in ir


# ---- end-to-end: real compiled, real executed programs ----

class TestArithmeticAndControlFlow:
    """claude.md #14-16, #18-20, #23-24: functions, expressions, if/else,
    ternary all produce correct runtime behavior, not just valid IR."""

    def test_function_call_and_arithmetic(self, compile_and_run):
        source = """
        int func add(a:int, b:int) {
            return a + b
        }
        log(add(2, 3))
        log(add(10, -4))
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["5", "6"]

    def test_if_else_branches(self, compile_and_run):
        source = """
        int func classify(n:int) {
            if n > 0 {
                return 1
            } else {
                return -1
            }
        }
        log(classify(5))
        log(classify(-5))
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["1", "-1"]

    def test_ternary_result(self, compile_and_run):
        source = """
        int x = 7
        text label = x > 5 ? 'big' : 'small'
        log(label)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "big"

    def test_logical_and_or_short_circuit(self, compile_and_run):
        # claude.md doesn't spell out short-circuit evaluation explicitly,
        # but it's the JavaScript-familiar behavior claude.md #45 asks for
        # ("Festina should retain familiar JavaScript conventions").
        source = """
        bool func sideEffect(tag:text) {
            log(tag)
            return true
        }
        bool r1 = false && sideEffect('should-not-print-1')
        bool r2 = true || sideEffect('should-not-print-2')
        log(r1)
        log(r2)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["false", "true"]

    def test_float_and_bool_and_text_log(self, compile_and_run):
        source = """
        float pi = 3.5
        bool enabled = true
        text name = 'Festina'
        log(pi)
        log(enabled)
        log(name)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["3.5", "true", "Festina"]


class TestStrings:
    """claude.md #9, #45: template string interpolation."""

    def _ir(self, parser, semantic, codegen, source, filename="main.f"):
        program = parser.parse(source, filename=filename)
        analyzed = semantic.analyze(program, filename=filename)
        return codegen.generate_ir(program, analyzed, filename=filename)

    def test_template_interpolation(self, compile_and_run):
        source = """
        text func greet(name:text) {
            return `Hello, ${name}!`
        }
        log(greet('World'))
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "Hello, World!"

    def test_multiple_interpolations(self, compile_and_run):
        source = """
        int x = 3
        int y = 4
        log(`(${x}, ${y})`)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "(3, 4)"

    # -- claude.md #82: a template literal skips concatenating with an
    # empty literal piece entirely (starts/ends with an interpolation,
    # or has two interpolations back to back) instead of emitting a
    # wasted `festina_str_concat("", ...)`/`festina_str_concat(..., "")`
    # call for it -- correctness (output is identical either way) and
    # the actual call-count reduction are both worth locking in
    # separately, so the optimization can't silently regress back to
    # the old always-two-calls-per-interpolation shape.

    def test_interpolation_at_the_start_produces_correct_output(self, compile_and_run):
        source = """
        text name = 'World'
        log(`${name}!`)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "World!"

    def test_interpolation_at_the_end_produces_correct_output(self, compile_and_run):
        source = """
        text name = 'World'
        log(`Hello, ${name}`)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "Hello, World"

    def test_bare_interpolation_with_no_surrounding_text_produces_correct_output(
            self, compile_and_run):
        source = """
        text name = 'World'
        log(`${name}`)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "World"

    def test_adjacent_interpolations_with_no_text_between_them_produce_correct_output(
            self, compile_and_run):
        source = """
        int x = 3
        int y = 4
        log(`${x}${y}`)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "34"

    def test_a_leading_empty_piece_emits_no_concat_for_it(self, parser, semantic, codegen):
        # `${name}!` has parts = ["", "!"] -- the leading "" is never
        # concatenated at all; only ONE festina_str_concat call remains
        # (appending "!"), not two.
        source = """
        void func f(name:text) {
            log(`${name}!`)
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        f_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define void @f("))
        f_body = "\n".join(ir.splitlines()[f_start:])
        assert f_body.count("call ptr @festina_str_concat(") == 1

    def test_a_trailing_empty_piece_emits_no_concat_for_it(self, parser, semantic, codegen):
        # `Hello, ${name}` has parts = ["Hello, ", ""] -- the trailing ""
        # is never concatenated; only ONE call remains (prepending
        # "Hello, ").
        source = """
        void func f(name:text) {
            log(`Hello, ${name}`)
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        f_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define void @f("))
        f_body = "\n".join(ir.splitlines()[f_start:])
        assert f_body.count("call ptr @festina_str_concat(") == 1

    def test_a_bare_interpolation_emits_no_concat_call_at_all(self, parser, semantic, codegen):
        # `${name}` has parts = ["", ""] -- both empty, so the
        # interpolated value's own text is used directly, with zero
        # festina_str_concat calls (the old codegen emitted two: `"" +
        # name`, then `+ ""`).
        source = """
        void func f(name:text) {
            log(`${name}`)
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        f_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define void @f("))
        f_body = "\n".join(ir.splitlines()[f_start:])
        assert f_body.count("call ptr @festina_str_concat(") == 0

    def test_adjacent_interpolations_emit_exactly_one_concat_for_the_empty_piece_between_them(
            self, parser, semantic, codegen):
        # `${x}${y}` has parts = ["", "", ""] -- the leading and
        # trailing pieces are both skipped (same as the bare-
        # interpolation case), but the piece BETWEEN x and y is also
        # "" and must still result in exactly one concat joining x's
        # and y's own text together (there's no way to skip joining two
        # genuinely different runtime values into one string).
        source = """
        void func f(x:int, y:int) {
            log(`${x}${y}`)
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        f_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define void @f("))
        f_body = "\n".join(ir.splitlines()[f_start:])
        assert f_body.count("call ptr @festina_str_concat(") == 1

    def test_a_template_with_no_empty_pieces_is_unaffected(self, parser, semantic, codegen):
        # `(${x}, ${y})` has parts = ["(", ", ", ")"] -- none empty, so
        # every concat call this template always needed is still there:
        # 4 total (join x in, join ", " in, join y in, join ")" in),
        # unchanged from before claude.md #82.
        source = """
        void func f(x:int, y:int) {
            log(`(${x}, ${y})`)
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        f_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define void @f("))
        f_body = "\n".join(ir.splitlines()[f_start:])
        assert f_body.count("call ptr @festina_str_concat(") == 4


class TestBlob:
    """claude.md #36, given its real meaning by claude.md #109.

    #36's only worked example was always `blob data = 'path/to/file'`,
    and for a long time that stored the PATH and never read the file --
    blob was a second name for `text`. #109 makes the example mean what
    it says: a blob is the file's BYTES, loaded at the declaration,
    keeping the path so the file can be written, appended to, tested
    for and deleted through the same value.
    """

    def test_a_blob_loads_the_bytes_at_its_path(self, compile_and_run, tmp_path):
        path = tmp_path / "data.txt"
        path.write_text("the contents")
        source = f"blob data = '{path}'\nlog(data.toText())"
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "the contents"

    def test_logging_a_blob_prints_its_contents(self, compile_and_run, tmp_path):
        # claude.md #115: log(blob) and `${blob}` print the contents --
        # the blob's own toText(), which is what the implicit conversion
        # means. (#114 briefly made this an error; a blob is very often
        # a text file, so the conversion it already had wins.)
        path = tmp_path / "data.txt"
        path.write_text("the contents")
        source = f"""
        blob data = '{path}'
        log(data)
        log(`inline: ${{data}}`)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["the contents", "inline: the contents"]

    def test_a_missing_path_is_an_empty_blob_not_a_failure(self, compile_and_run):
        # claude.md #93's rule, inherited: a missing file is something
        # you test for, not something that stops the program. It is also
        # how a file that does not exist yet gets created.
        source = ("blob data = '/nonexistent/nowhere.txt'\n"
                  "log(data.exists())\n"
                  "log(data.toText() == '')\n")
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["false", "true"]

    def test_write_append_read_exists_delete(self, compile_and_run, tmp_path):
        path = tmp_path / "notes.txt"
        source = f"""
        blob f = '{path}'
        log(f.write('hello'))
        log(f.append(' world'))
        log(f.toText())
        log(f.exists())
        log(f.delete())
        log(f.exists())
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == [
            "true", "true", "hello world", "true", "true", "false"]

    def test_the_bytes_survive_deleting_the_file(self, compile_and_run, tmp_path):
        # .delete() removes the FILE. The blob is an ordinary value and
        # is unaffected, which is what makes "delete it but keep what it
        # said" expressible.
        path = tmp_path / "notes.txt"
        source = f"""
        blob f = '{path}'
        f.write('remembered')
        f.delete()
        log(f.exists())
        log(f.toText())
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["false", "remembered"]

    def test_write_updates_what_to_text_reports(self, compile_and_run, tmp_path):
        # Not just the file: the in-memory bytes too, so toText() after
        # write() reports what was written rather than what the file
        # held when the blob was declared.
        path = tmp_path / "notes.txt"
        path.write_text("original")
        source = f"""
        blob f = '{path}'
        log(f.toText())
        f.write('replaced')
        log(f.toText())
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["original", "replaced"]

    def test_the_path_may_be_any_text_expression(self, compile_and_run, tmp_path):
        path = tmp_path / "data.txt"
        path.write_text("found")
        source = f"""
        text dir = '{tmp_path}/'
        blob f = dir + 'data.txt'
        log(f.toText())
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "found"

    def test_assigning_a_blob_shares_one_handle(self, compile_and_run, tmp_path):
        # claude.md #109: "if a blob = another blob, have it copy the
        # reference of that current file". Writing through one is
        # visible through the other, which is what proves they are one
        # handle rather than two copies.
        path = tmp_path / "shared.txt"
        source = f"""
        blob a = '{path}'
        a.write('first')
        blob b = a
        a.write('second')
        log(b.toText())
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "second"

    def test_reassigning_leaves_an_earlier_reference_intact(
            self, compile_and_run, tmp_path):
        # The other half of the same rule: rebinding `a` must not
        # disturb `keep`, which still holds the first file. Verified
        # leak-free separately under LeakSanitizer -- this pins that the
        # surviving reference is also still READABLE, i.e. that the
        # release did not free a handle someone else still held.
        p1 = tmp_path / "one.txt"
        p2 = tmp_path / "two.txt"
        source = f"""
        blob a = '{p1}'
        a.write('file one')
        blob keep = a
        a = '{p2}'
        a.write('file two')
        log(keep.toText())
        log(a.toText())
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["file one", "file two"]

    def test_a_blob_is_no_longer_comparable_to_text(self, parser, semantic, errors):
        # It used to be, because it WAS a text. A handle and a string
        # are not the same kind of thing, and comparing them would have
        # compared a pointer against a string's contents.
        program = parser.parse("blob data = 'x'\ntext t = 'x'\nlog(data == t)")
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)

    def test_a_blob_compares_against_null(self, compile_and_run, tmp_path):
        path = tmp_path / "data.txt"
        path.write_text("x")
        source = f"blob f = '{path}'\nlog(f == null)\nlog(f != null)"
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["false", "true"]

    def test_an_unknown_blob_method_is_rejected(self, parser, semantic, errors):
        program = parser.parse("blob f = 'x'\nf.slurp()")
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)

    def test_write_requires_exactly_one_text_argument(self, parser, semantic, errors):
        program = parser.parse("blob f = 'x'\nf.write()")
        with pytest.raises(errors.CompileError, match="write"):
            semantic.analyze(program)

    def test_to_text_takes_no_arguments(self, parser, semantic, errors):
        program = parser.parse("blob f = 'x'\nlog(f.toText('extra'))")
        with pytest.raises(errors.CompileError, match="toText"):
            semantic.analyze(program)


class TestStructs:
    """claude.md #27: structs are native in-memory objects with typed,
    assignable fields."""

    def test_struct_field_assignment_and_read(self, compile_and_run):
        source = """
        struct Point {
            x:int
            y:int
        }
        Point p
        p.x = 3
        p.y = 4
        log(p.x + p.y)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "7"

    def test_struct_passed_to_function(self, compile_and_run):
        source = """
        struct User {
            id:int
            name:text
        }
        int func idOf(u:User) {
            return u.id
        }
        User user
        user.id = 42
        user.name = 'Patrick'
        log(idOf(user))
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "42"

    def test_struct_returned_by_value_from_function(self, compile_and_run):
        # Regression test: struct backing storage used to be a stack
        # alloca even for locals, so returning one handed the caller a
        # pointer into a popped stack frame -- verified to silently print
        # garbage before the fix (calloc'd storage instead; see
        # festina/codegen.py's module docstring).
        source = """
        struct Point {
            x:int
            y:int
        }
        Point func origin() {
            Point p
            p.x = 0
            p.y = 0
            return p
        }
        Point func makePoint(a:int, b:int) {
            Point p
            p.x = a
            p.y = b
            return p
        }
        Point o = origin()
        log(o.x)
        log(o.y)
        Point q = makePoint(7, 8)
        log(q.x)
        log(q.y)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["0", "0", "7", "8"]

    def test_local_struct_fields_default_to_zero(self, compile_and_run):
        # calloc, not malloc, for struct backing storage -- a local
        # struct's unset fields should read as zero, same as a global
        # struct's (`zeroinitializer`), not garbage.
        source = """
        struct Counters {
            hits:int
            misses:int
        }
        Counters c
        log(c.hits)
        log(c.misses)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["0", "0"]


class TestSelfReferencingStructs:
    """claude.md #106: a struct may name its own type in a field, and may
    name a struct declared later in the file. Both used to be rejected
    with "unknown type", not because the representation could not hold
    them -- a struct-typed field is a pointer -- but because
    analyze_struct registered the finished struct only after resolving
    every field."""

    def test_struct_may_reference_its_own_type(self, compile_and_run):
        source = """
        struct Node {
            n:int
            next:Node
        }
        Node head
        head.n = 1
        head.next.n = 2
        log(head.n + head.next.n)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "3"

    def test_a_linked_list_can_be_built_and_walked(self, compile_and_run):
        # claude.md #97's auto-vivification is what makes the build side
        # work with no extra machinery: reaching THROUGH a null struct
        # field allocates it. The walk then reads back what was written.
        source = """
        struct Node {
            n:int
            next:Node
        }
        Node head
        head.n = 1
        head.next.n = 2
        head.next.next.n = 3

        int total = 0
        Node cursor = head
        for int i = 0, i < 3, i++ {
            total = total + cursor.n
            cursor = cursor.next
        }
        log(total)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "6"

    def test_two_structs_may_reference_each_other(self, compile_and_run):
        source = """
        struct A {
            n:int
            b:B
        }
        struct B {
            n:int
            a:A
        }
        A first
        first.n = 1
        first.b.n = 2
        first.b.a.n = 3
        log(first.n + first.b.n + first.b.a.n)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "6"

    def test_a_struct_may_reference_one_declared_later(self, compile_and_run):
        # The forward-reference half of the same fix -- this failed for
        # exactly the same reason and with the same error message.
        source = """
        struct Outer {
            inner:Inner
        }
        struct Inner {
            n:int
        }
        Outer o
        o.inner.n = 42
        log(o.inner.n)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "42"

    def test_a_self_referencing_struct_still_has_a_release_wrapper(
            self, parser, semantic, codegen):
        # A struct with a struct-typed field needs the per-type release
        # wrapper of claude.md #78, and here that field is the struct
        # itself. The wrapper's cache entry is written before the field
        # loop recurses, so exactly ONE wrapper is generated and it
        # calls itself -- rather than the compiler recursing forever.
        source = ("struct Node { n:int next:Node }\n"
                  "Node func make(v:int) {\n"
                  "    Node p\n"
                  "    p.n = v\n"
                  "    return p\n"
                  "}\n"
                  "Node head = make(1)\n"
                  "head.next = make(2)\n"
                  "log(head.n + head.next.n)\n")
        program = parser.parse(source, filename="main.f")
        analyzed = semantic.analyze(program, filename="main.f")
        ir = codegen.generate_ir(program, analyzed, filename="main.f")
        define = "define void @__festina_release_struct_Node(ptr %payload) {"
        assert ir.count(define) == 1
        # ...and the one wrapper genuinely recurses into itself: the
        # cascade over its `next` field is a call to the very function
        # being defined, which is only well-founded because the cache
        # entry is written before the field loop runs.
        body = ir.split(define, 1)[1].split("\n}", 1)[0]
        assert "call void @__festina_release_struct_Node(" in body

    def test_a_reference_cycle_runs_correctly_and_does_not_crash(self, compile_and_run):
        # claude.md #106's accepted cost, pinned as behavior rather than
        # hidden. `a.next = a` is a reference cycle, which refcounting
        # cannot free -- so this LEAKS, deliberately and permanently
        # until something traces (see todo.md's "What's still ahead").
        # What must never regress is that it stays a leak: a cycle whose
        # counts never reach zero must not become a double-free or a
        # use-after-free, both of which would be far worse than the
        # leak and both of which a naive "break the cycle on release"
        # fix would risk.
        source = """
        struct Node {
            n:int
            next:Node
        }
        Node a
        a.n = 7
        a.next = a
        log(a.next.next.next.n)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "7"

    def test_breaking_a_cycle_with_null_reclaims_it(self, compile_and_run):
        # The workaround api.md recommends, pinned so it stays true:
        # clearing the back-reference drops the count to zero and the
        # value is reclaimed normally. Verified separately under
        # LeakSanitizer (50 iterations, zero leaked bytes -- against
        # 1,200 for the identical program without the `= null`); this
        # test pins the behavior, since the sanitizer does not run here.
        source = """
        struct Node {
            n:int
            next:Node
        }
        void func build() {
            Node a
            a.n = 7
            a.next = a
            a.next = null
        }
        for int i = 0, i < 50, i++ {
            build()
        }
        log('done')
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "done"

    def test_a_duplicate_struct_name_is_still_rejected(self, parser, semantic, errors):
        # The pre-pass registers every name up front, so the duplicate
        # check can no longer just ask whether the name is present --
        # it has to ask whether it has real fields. Get that wrong and
        # every struct is a duplicate of itself.
        program = parser.parse("struct A { n:int }\nstruct A { m:int }\n")
        with pytest.raises(errors.CompileError, match="already declared"):
            semantic.analyze(program)

    def test_a_struct_colliding_with_a_table_name_is_still_rejected(
            self, parser, semantic, errors):
        program = parser.parse("table A { n:int }\nstruct A { m:int }\n")
        with pytest.raises(errors.CompileError, match="already declared"):
            semantic.analyze(program)

    def test_an_unknown_field_type_is_still_rejected(self, parser, semantic, errors):
        # And the failed declaration must not leave a half-registered
        # name behind for a later declaration to collide with.
        program = parser.parse("struct A { n:Nonexistent }\n")
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)

    def test_a_name_left_by_a_failed_struct_does_not_become_a_duplicate(
            self, parser, semantic, errors):
        # The failure above must report the unknown type, not a
        # spurious "already declared" from its own placeholder.
        program = parser.parse("struct A { n:Nonexistent }\n")
        with pytest.raises(errors.CompileError) as excinfo:
            semantic.analyze(program)
        assert "already declared" not in str(excinfo.value)


class TestArrays:
    """claude.md #26: arr[T] with elements sized/typed at compile time,
    from a literal, read and written by index. `.length` (#63) and
    for/while loops (#60/#61) are covered in TestArrayLength and
    TestLoops below. No growth and no bounds checking -- claude.md
    doesn't specify either (see the module docstring in
    festina/codegen.py), so they aren't implemented."""

    def test_literal_index_read(self, compile_and_run):
        result = compile_and_run("arr[int] nums = [10, 20, 30]\nlog(nums[0])\nlog(nums[2])")
        assert result.stdout.splitlines() == ["10", "30"]

    def test_indexed_write(self, compile_and_run):
        source = """
        arr[int] nums = [1, 2, 3]
        nums[1] = 99
        log(nums[1])
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "99"

    def test_index_by_variable_expression(self, compile_and_run):
        source = """
        arr[int] nums = [5, 6, 7]
        int i = 2
        log(nums[i])
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "7"

    def test_array_of_floats_and_text(self, compile_and_run):
        source = """
        arr[float] prices = [1.5, 2.5, 3.0]
        arr[text] names = ['a', 'b', 'c']
        log(prices[1])
        log(names[2])
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["2.5", "c"]

    def test_declaration_without_initializer(self, compile_and_run):
        # claude.md #26's own examples (`arr[int] numbers`) have no
        # initializer.
        result = compile_and_run("arr[int] empty\nlog('declared fine')")
        assert result.returncode == 0
        assert result.stdout.strip() == "declared fine"

    def test_nested_arrays(self, compile_and_run):
        # claude.md #26: "Nested arrays are valid: arr[arr[int]] matrix".
        source = """
        arr[arr[int]] matrix = [[1, 2], [3, 4]]
        log(matrix[0][1])
        log(matrix[1][0])
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["2", "3"]

    def test_array_as_function_parameter_and_return_value(self, compile_and_run):
        source = """
        int func sum3(nums:arr[int]) {
            return nums[0] + nums[1] + nums[2]
        }
        arr[int] func makeRange(a:int, b:int) {
            return [a, b, a + b]
        }
        log(sum3([1, 2, 3]))
        arr[int] r = makeRange(3, 4)
        log(r[0])
        log(r[1])
        log(r[2])
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["6", "3", "4", "7"]

    def test_array_of_structs(self, compile_and_run):
        source = """
        struct Point {
            x:int
            y:int
        }
        Point p1
        p1.x = 1
        p1.y = 2
        Point p2
        p2.x = 3
        p2.y = 4
        arr[Point] points = [p1, p2]
        log(points[0].x)
        log(points[1].y)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["1", "4"]


class TestArrayLength:
    """claude.md #63: every array has a built-in read-only `.length`."""

    def test_length_of_literal(self, compile_and_run):
        result = compile_and_run("arr[int] values = [1, 2, 3]\nlog(values.length)")
        assert result.stdout.strip() == "3"

    def test_length_of_empty_array(self, compile_and_run):
        result = compile_and_run("arr[int] values = []\nlog(values.length)")
        assert result.stdout.strip() == "0"

    def test_length_updates_after_reassignment(self, compile_and_run):
        source = """
        arr[int] values = [1, 2]
        values = [1, 2, 3, 4, 5]
        log(values.length)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "5"


class TestMaps:
    """claude.md #72: map[T] -- { key: value, ... } literals, indexed
    get/set, .forEach()."""

    def test_literal_with_string_and_variable_keys(self, compile_and_run):
        source = """
        text npc2Id = 'npc2'
        map[int] npcHealths = {'npc1': 10, npc2Id: 15}
        log(npcHealths['npc1'])
        log(npcHealths[npc2Id])
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["10", "15"]

    def test_map_of_text_values(self, compile_and_run):
        source = """
        text npc2Id = 'npc2'
        map[text] npcNames = {'npc1': 'jim', npc2Id: 'john'}
        log(npcNames['npc1'])
        log(npcNames[npc2Id])
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["jim", "john"]

    def test_missing_key_returns_null(self, compile_and_run):
        # claude.md #72: "If the key is not present in the map, the
        # result is null" -- text's null already prints as an empty
        # line (see TestRegex's identical match()-with-no-match test).
        source = """
        map[text] m = {'a': 'x'}
        log('before')
        log(m['missing'])
        log('after')
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["before", "", "after"]

    def test_missing_key_on_int_map_returns_the_int_null_sentinel(self, compile_and_run):
        # Same null representation int already uses everywhere else
        # (e.g. division by zero -- claude.md #57) -- not a special
        # case invented for maps.
        div_by_zero = compile_and_run("int a = 1\nint b = 0\nlog(a / b)")
        missing_key = compile_and_run("map[int] m = {'a': 1}\nlog(m['missing'])")
        assert missing_key.stdout == div_by_zero.stdout

    def test_empty_map_literal(self, compile_and_run):
        result = compile_and_run("map[int] m = {}\nlog(m['x'])")
        assert result.returncode == 0

    def test_duplicate_key_in_a_literal_last_one_wins(self, compile_and_run):
        # Two *literal* string keys colliding (`{'a': 1, 'a': 2}`) is now
        # a compile-time error instead (see tests/test_maps.py's
        # TestMapLiteral -- knowable for free, almost always a typo).
        # "last value wins" for a genuine runtime collision still holds
        # and is still exercised here, via a variable key that happens
        # to equal an earlier literal key only at runtime.
        source = "text k = 'a'\nmap[int] m = {'a': 1, k: 2}\nlog(m['a'])"
        result = compile_and_run(source)
        assert result.stdout.strip() == "2"

    def test_write_adds_a_new_key(self, compile_and_run):
        source = """
        map[int] m = {'a': 1}
        m['b'] = 2
        log(m['a'])
        log(m['b'])
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["1", "2"]

    def test_write_replaces_an_existing_key(self, compile_and_run):
        source = """
        map[int] m = {'a': 1}
        m['a'] = 30
        log(m['a'])
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "30"

    def test_write_with_a_variable_key(self, compile_and_run):
        source = """
        text npc2Id = 'npc2'
        map[int] npcHealths = {'npc1': 10, npc2Id: 15}
        npcHealths[npc2Id] = 30
        log(npcHealths[npc2Id])
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "30"

    def test_zero_initialized_map_variable_behaves_as_empty(self, compile_and_run):
        source = """
        map[text] m
        log(m['x'])
        m['x'] = 'now set'
        log(m['x'])
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["", "now set"]

    def test_map_as_a_struct_field(self, compile_and_run):
        source = """
        struct Holder {
            scores:map[int]
        }
        Holder h
        h.scores = {'a': 1}
        h.scores['b'] = 2
        log(h.scores['a'])
        log(h.scores['b'])
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["1", "2"]

    def test_map_assignment_on_a_non_addressable_target_is_a_clear_error(self, parser, semantic, codegen, errors):
        source = """
        map[int] func getMap() {
            map[int] m = {'a': 1}
            return m
        }
        getMap()['a'] = 5
        """
        program = parser.parse(source)
        analyzed = semantic.analyze(program)
        with pytest.raises(errors.CompileError, match="plain variable or field"):
            codegen.generate_ir(program, analyzed)

    def test_forEach_visits_every_entry(self, compile_and_run):
        source = """
        void func logHealth(h:int, key:text) {
            log(`${key} ${h.toText()}`)
        }
        map[int] npcHealths = {'npc1': 10, 'npc2': 15}
        npcHealths.forEach(logHealth)
        """
        result = compile_and_run(source)
        assert sorted(result.stdout.splitlines()) == ["npc1 10", "npc2 15"]

    def test_forEach_with_a_float_valued_map(self, compile_and_run):
        # Exercises the .forEach() trampoline's float (double)
        # reinterpretation path specifically -- see
        # _emit_map_foreach_trampoline's own comment on why a real
        # trampoline is needed at all, not just for int.
        source = """
        void func logPrice(v:float, key:text) {
            log(`${key} ${v}`)
        }
        map[float] prices = {'apple': 1.5}
        prices.forEach(logPrice)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "apple 1.5"

    def test_forEach_with_a_bool_valued_map(self, compile_and_run):
        source = """
        void func logFlag(v:bool, key:text) {
            log(`${key} ${v}`)
        }
        map[bool] flags = {'ready': true}
        flags.forEach(logFlag)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "ready true"

    def test_forEach_with_a_struct_valued_map(self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        void func logPoint(v:Point, key:text) {
            log(`${key}: (${v.x},${v.y})`)
        }
        Point origin
        origin.x = 0
        origin.y = 0
        map[Point] points = {'origin': origin}
        points.forEach(logPoint)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "origin: (0,0)"


class TestLoops:
    """claude.md #60 (for loops), #61 (while loops), #66 (postfix ++/--)."""

    def test_for_loop_array_iteration_example(self, compile_and_run):
        # The exact worked example from claude.md #60.
        source = """
        arr[int] array = [10, 20, 30]
        for int x = 0, x < array.length, x++ {
            log(array[x])
        }
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["10", "20", "30"]

    def test_for_loop_counts_from_zero(self, compile_and_run):
        result = compile_and_run("for int x = 0, x < 5, x++ {\n    log(x)\n}")
        assert result.stdout.splitlines() == ["0", "1", "2", "3", "4"]

    def test_for_loop_with_decrement_update(self, compile_and_run):
        result = compile_and_run("for int x = 3, x > 0, x-- {\n    log(x)\n}")
        assert result.stdout.splitlines() == ["3", "2", "1"]

    def test_for_loop_body_never_runs_when_condition_starts_false(self, compile_and_run):
        source = "for int x = 0, x < 0, x++ {\n    log('never')\n}\nlog('after')"
        result = compile_and_run(source)
        assert result.stdout.strip() == "after"

    def test_for_loop_init_variable_does_not_leak_past_the_loop(self, compile_and_run):
        # claude.md #60: "The initialization variable is scoped to the
        # loop body" -- a same-named outer variable must be unaffected.
        source = """
        int x = 99
        for int x = 0, x < 3, x++ {
        }
        log(x)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "99"

    def test_nested_for_loops(self, compile_and_run):
        source = """
        for int i = 0, i < 2, i++ {
            for int j = 0, j < 2, j++ {
                log(i * 10 + j)
            }
        }
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["0", "1", "10", "11"]

    def test_while_loop_counts_up(self, compile_and_run):
        source = """
        int e = 0
        while e < 5 {
            log(e)
            e++
        }
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["0", "1", "2", "3", "4"]

    def test_while_loop_body_never_runs_when_condition_starts_false(self, compile_and_run):
        source = "int e = 10\nwhile e < 5 {\n    log('never')\n}\nlog('after')"
        result = compile_and_run(source)
        assert result.stdout.strip() == "after"

    def test_while_true_exits_via_return_inside_the_loop(self, compile_and_run):
        # `return` from the enclosing function still works as a way out
        # of an infinite loop's body too, same as before claude.md #73
        # added break/continue -- this isn't the only way out anymore
        # (see TestBreakAndContinue below), just still a valid one.
        source = """
        void func run() {
            int count = 0
            while true {
                log(count)
                count++
                if count == 3 {
                    return
                }
            }
        }
        run()
        log('done')
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["0", "1", "2", "done"]

    def test_postfix_increment_and_decrement(self, compile_and_run):
        source = """
        int i = 5
        i++
        log(i)
        i--
        i--
        log(i)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["6", "4"]

    def test_postfix_increment_returns_pre_increment_value(self, compile_and_run):
        # Standard postfix semantics: `i++` evaluates to the value *before*
        # incrementing.
        source = "int i = 5\nlog(i++)\nlog(i)"
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["5", "6"]

    def test_iterative_fibonacci(self, compile_and_run):
        # A real loop-driven computation, not just a counter -- exercises
        # for + array-free accumulation together.
        source = """
        int func fibIter(n:int) {
            int a = 0
            int b = 1
            for int i = 0, i < n, i++ {
                int next = a + b
                a = b
                b = next
            }
            return a
        }
        log(fibIter(10))
        log(fibIter(20))
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["55", "6765"]


class TestBreakAndContinue:
    """claude.md #73."""

    def test_break_stops_the_loop_immediately(self, compile_and_run):
        source = """
        for int i = 0, i < 10, i++ {
            if i == 5 {
                break
            }
            log(i)
        }
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["0", "1", "2", "3", "4"]

    def test_continue_skips_the_rest_of_that_iteration(self, compile_and_run):
        source = """
        for int i = 0, i < 5, i++ {
            if i % 2 == 0 {
                continue
            }
            log(i)
        }
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["1", "3"]

    def test_claude_mds_own_worked_example(self, compile_and_run):
        # claude.md #73's own example: "This logs 1, 3."
        source = """
        for int i = 0, i < 10, i++ {
            if i == 5 {
                break
            }
            if i % 2 == 0 {
                continue
            }
            log(i)
        }
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["1", "3"]

    def test_continue_in_a_for_loop_still_runs_the_update_expression(self, compile_and_run):
        # claude.md #73: "the update expression still runs before the
        # condition is checked again" -- if continue skipped straight to
        # the condition instead, i would never advance and this would
        # loop forever (or time out); it doesn't.
        source = """
        for int i = 0, i < 5, i++ {
            continue
        }
        log('done')
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "done"

    def test_break_in_a_while_loop(self, compile_and_run):
        source = """
        int i = 0
        while true {
            if i >= 3 {
                break
            }
            log(i)
            i++
        }
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["0", "1", "2"]

    def test_continue_in_a_while_loop(self, compile_and_run):
        source = """
        int i = 0
        while i < 5 {
            i++
            if i % 2 == 0 {
                continue
            }
            log(i)
        }
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["1", "3", "5"]

    def test_break_only_exits_the_innermost_loop(self, compile_and_run):
        source = """
        for int i = 0, i < 3, i++ {
            for int j = 0, j < 3, j++ {
                if j == 1 {
                    break
                }
                log(`${i},${j}`)
            }
        }
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["0,0", "1,0", "2,0"]

    def test_continue_only_affects_the_innermost_loop(self, compile_and_run):
        source = """
        for int i = 0, i < 2, i++ {
            for int j = 0, j < 3, j++ {
                if j == 1 {
                    continue
                }
                log(`${i},${j}`)
            }
        }
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["0,0", "0,2", "1,0", "1,2"]

    def test_statements_after_break_in_the_same_block_are_not_run(self, compile_and_run):
        source = """
        for int i = 0, i < 3, i++ {
            if i == 1 {
                break
                log('unreachable')
            }
            log(i)
        }
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["0"]


class TestAutomaticMemoryReclamation:
    """claude.md #74, stage 1: a local struct/arr[T]/map[T] declared
    directly in a function/event handler's own top-level body is freed
    automatically at every return, when tests/test_escape_analysis.py's
    find_escaping_names proves it never escapes. That module is tested
    exhaustively on its own (every syntactic escaping/non-escaping
    pattern, no C compiler needed) -- this class checks the other half:
    that the analysis's result actually gets wired into real generated
    IR (free() calls landing in exactly the right place) and, more
    importantly, that real compiled programs still produce correct
    output in both the freed and (still-leaking, unchanged) escaping
    cases."""

    def _ir(self, parser, semantic, codegen, source, filename="main.f"):
        program = parser.parse(source, filename=filename)
        analyzed = semantic.analyze(program, filename=filename)
        return codegen.generate_ir(program, analyzed, filename=filename)

    # ---- IR-level: no C compiler needed ----

    def test_non_escaping_struct_local_is_stack_allocated(self, parser, semantic, codegen):
        # claude.md #43/#74/#75: a struct local proven never to escape
        # its declaring function is now a real stack alloca, not a
        # calloc+free pair -- see _emit_stmt's own VarDecl comment for
        # why reusing the exact same escape-analysis proof is sound for
        # allocation, not just for freeing.
        source = """
        struct Point { x:int y:int }
        void func f() {
            Point p
            p.x = 1
            log(p.x)
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "%p.storage" in ir
        assert "alloca %struct.Point" in ir
        assert "store %struct.Point zeroinitializer" in ir
        assert "call ptr @calloc(" not in ir
        assert "call void @free(" not in ir

    def test_non_escaping_array_local_declared_with_a_literal_initializer_is_stack_allocated(
            self, parser, semantic, codegen):
        # claude.md #81: a with-initializer arr[T]/map[T] local used to
        # never stack-allocate at all (mirroring claude.md #77's own
        # original struct rule) -- always refcounted, released through
        # festina_release_array/_map. Now, when the initializer is a
        # literal written directly here AND the local never escapes
        # (both true in this exact source), the header itself is
        # stack-allocated instead, exactly like a no-init non-escaping
        # local already was (see
        # test_no_init_non_escaping_array_local_frees_its_data_pointer_directly,
        # just below) -- only the data buffer this literal still
        # malloc's needs freeing at scope-exit, through a bare @free(),
        # never festina_release_array. The ESCAPING with-init case is
        # unaffected and still fully refcounted -- see
        # test_with_init_array_local_is_refcounted_via_a_shared_header.
        source = """
        void func f() {
            arr[int] a = [1, 2, 3]
            log(a[0])
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "getelementptr %struct._FestinaArray, ptr" in ir
        assert "alloca %struct._FestinaArray" in ir
        assert "call void @free(" in ir
        assert "call void @festina_release_array(" not in ir

    def test_no_init_non_escaping_array_local_frees_its_data_pointer_directly(
            self, parser, semantic, codegen):
        # A no-init arr[T] local that never escapes still stack-
        # allocates its own header (claude.md #74, unchanged) -- only
        # its data buffer needs freeing at scope-exit, through a bare
        # @free(), never festina_release_array (the header itself was
        # never heap-allocated/refcounted at all).
        source = """
        void func f() {
            arr[int] a
            log(a.length)
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "call void @free(" in ir
        assert "call void @festina_release_array(" not in ir

    def test_non_escaping_map_local_declared_with_a_literal_initializer_is_stack_allocated(
            self, parser, semantic, codegen):
        # claude.md #81: same as the array case above -- a with-
        # initializer map[T] local used to always be refcounted,
        # released through festina_release_map (which itself calls
        # festina_map_free_entries internally, in C). Now, non-escaping
        # with a literal initializer written directly here, its header
        # is stack-allocated instead, so festina_map_free_entries is
        # called directly in this function's own IR (not hidden inside
        # festina_release_map anymore) to free just the entries buffer.
        source = """
        void func f() {
            map[int] m = {'a': 1}
            log(m['a'])
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "getelementptr %struct._FestinaMap, ptr" in ir
        assert "alloca %struct._FestinaMap" in ir
        assert "call void @festina_map_free_entries(" in ir
        assert "call void @festina_release_map(" not in ir

    def test_escaping_map_local_declared_with_an_initializer_is_refcounted(
            self, parser, semantic, codegen):
        # The escaping counterpart to the non-escaping test just above
        # (mirroring test_with_init_array_local_is_refcounted_via_a_shared_header):
        # `m` here is assigned into a global, so it can't be stack-
        # allocated at all -- still always refcounted, released through
        # festina_release_map, exactly as every with-init map local was
        # before claude.md #81.
        source = """
        map[int] g
        void func f() {
            map[int] m = {'a': 1}
            g = m
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "getelementptr %struct._FestinaMap, ptr" in ir
        assert "call void @festina_release_map(" in ir
        assert "call void @festina_map_free_entries(" not in ir

    def test_no_init_non_escaping_map_local_frees_its_entries_pointer_directly(
            self, parser, semantic, codegen):
        # claude.md #74/#75: a map's entries buffer has its own nested
        # per-entry key allocation (see festina_map_set's own comment),
        # so freeing it goes through festina_map_free_entries -- which
        # frees each entry's key too -- not a plain @free(entries) that
        # would leak them. A no-init map that never escapes still
        # stack-allocates its own header (claude.md #74, unchanged), so
        # this call is still emitted directly in the IR, unlike the
        # with-initializer case above.
        source = """
        void func f() {
            map[int] m
            m['a'] = 1
            log(m['a'])
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "call void @festina_map_free_entries(" in ir
        assert "call void @festina_release_map(" not in ir

    def test_returned_struct_is_not_freed(self, parser, semantic, codegen):
        source = """
        struct Point { x:int y:int }
        Point func makePoint() {
            Point p
            p.x = 1
            return p
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "call void @free(" not in ir

    def test_struct_passed_to_a_non_retaining_function_is_now_stack_allocated(
            self, parser, semantic, codegen):
        # claude.md #74 stage 2 (interprocedural): takesPoint only reads
        # p.x (a Member.obj-safe use, same rule as any local's own
        # fields) -- its own parameter never escapes within its own
        # body, so escaping_params[takesPoint] = {} and f()'s own `p`,
        # passed there and never used any other way, is now provably
        # safe too. This is the exact case stage 1's own module
        # docstring called out as its stated limitation ("even if the
        # called function provably doesn't retain it") -- combined with
        # the stack-allocation swap (claude.md #43/#74/#75), f()'s own
        # `p` is now a stack alloca, not a calloc'd allocation at all.
        source = """
        struct Point { x:int y:int }
        void func takesPoint(p:Point) {
            log(p.x)
        }
        void func f() {
            Point p
            takesPoint(p)
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        f_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define void @f("))
        f_body = "\n".join(ir.splitlines()[f_start:])
        assert "alloca %struct.Point" in f_body
        assert "call ptr @calloc(" not in f_body

    def test_loop_local_struct_is_stack_allocated_and_reused_across_iterations(
            self, parser, semantic, codegen):
        # claude.md #43/#74/#75: the loop-body/break/continue-scoped
        # freeing machinery still applies to *when* a struct local's
        # storage is considered dead, even though "dead" no longer
        # means "call free() here" for a struct -- it means the very
        # next textual reach of the same VarDecl (the next iteration)
        # re-zeros the *same* stack address rather than allocating a
        # fresh one, since LLVM's alloca reserves one fixed slot for
        # the whole enclosing function regardless of which basic block
        # contains it. The alloca and its zeroinitializer store must
        # both be inside the loop body (so re-zeroing genuinely
        # happens every iteration, not just once before the loop), and
        # there must be no calloc/free anywhere in the function at all.
        source = """
        struct Point { x:int y:int }
        void func f() {
            for int i = 0, i < 3, i++ {
                Point p
                p.x = i
                log(p.x)
            }
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        lines = ir.splitlines()
        body_start = next(i for i, l in enumerate(lines) if l.strip().startswith("for.body"))
        body_end = next(i for i in range(body_start, len(lines)) if lines[i].strip().startswith("br label %for.update"))
        body_lines = lines[body_start:body_end]
        assert any("alloca %struct.Point" in l for l in body_lines)
        assert any("store %struct.Point zeroinitializer" in l for l in body_lines)
        assert "call ptr @calloc(" not in ir
        assert "call void @free(" not in ir

    def test_nested_if_declared_struct_is_stack_allocated(self, parser, semantic, codegen):
        source = """
        struct Point { x:int y:int }
        void func f(cond:bool) {
            if cond {
                Point p
                p.x = 1
                log(p.x)
            }
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "alloca %struct.Point" in ir
        assert "call ptr @calloc(" not in ir

    def test_struct_passed_to_a_retaining_function_is_still_not_freed(self, parser, semantic, codegen):
        # The mirror case: retains actually stores its own parameter
        # into a global (an unconditional hard escape, same rule as
        # ever) -- escaping_params[retains] = {0}, so f()'s own `p`,
        # passed at that same position, still correctly escapes too.
        # Interprocedural analysis only ever widens what's PROVEN safe;
        # it must never widen what's proven unsafe.
        source = """
        struct Point { x:int y:int }
        Point stash
        void func retains(p:Point) {
            stash = p
        }
        void func f() {
            Point p
            retains(p)
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "call void @free(" not in ir

    def test_struct_assigned_into_a_global_is_not_freed(self, parser, semantic, codegen):
        source = """
        struct Point { x:int y:int }
        Point g
        void func f() {
            Point p
            g = p
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "call void @free(" not in ir

    def test_reassigned_struct_is_not_freed(self, parser, semantic, codegen):
        source = """
        struct Point { x:int y:int }
        void func f() {
            Point p
            Point q
            p = q
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "call void @free(" not in ir

    def test_a_loop_local_array_is_freed_inside_the_loop_body(self, parser, semantic, codegen):
        # claude.md #74's nested-block extension: a loop-body-declared
        # local is freed at the end of *every* iteration -- exactly
        # one free call, and it must be inside the loop body's own
        # block (part of the runtime back-edge cycle), not just once
        # after the loop as a whole exits. Uses arr[int], not a
        # struct -- since the stack-allocation swap (claude.md #43/#74/
        # #75), a non-escaping struct local no longer goes through this
        # free-scheduling machinery at all (see
        # test_a_loop_local_struct_is_reused_across_iterations_via_the_same_alloca
        # for the struct/stack-allocation equivalent of this same
        # shape). `p` here is non-escaping with a literal initializer,
        # so its own HEADER is stack-allocated (claude.md #81) and only
        # its data buffer needs freeing at scope-exit (a plain @free(),
        # not festina_release_array) -- still exactly the right type to
        # exercise the free-scheduling logic itself with, just through
        # a different runtime call than before #81.
        source = """
        void func f() {
            for int i = 0, i < 3, i++ {
                arr[int] p = [i]
                log(p[0])
            }
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        lines = ir.splitlines()
        body_start = next(i for i, l in enumerate(lines) if l.strip().startswith("for.body"))
        body_end = next(i for i in range(body_start, len(lines)) if lines[i].strip().startswith("br label %for.update"))
        body_lines = lines[body_start:body_end]
        assert sum("call void @free(" in l for l in body_lines) == 1
        assert ir.count("call void @free(") == 1

    def test_a_nested_if_declared_array_is_freed_at_the_ifs_own_end(self, parser, semantic, codegen):
        source = """
        void func f(cond:bool) {
            if cond {
                arr[int] p = [1]
                log(p[0])
            }
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "call void @free(" in ir

    def test_break_frees_the_loop_local_but_not_an_outer_scope_local(self, parser, semantic, codegen):
        source = """
        void func f() {
            arr[int] outer = [100]
            for int i = 0, i < 5, i++ {
                arr[int] inner = [i]
                if inner[0] == 2 {
                    break
                }
                log(inner[0])
            }
            log(outer[0])
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        lines = ir.splitlines()
        break_block = next(i for i, l in enumerate(lines) if l.strip().startswith("if.then"))
        break_block_end = next(i for i in range(break_block, len(lines)) if lines[i].strip().startswith("br label %for.end"))
        break_lines = lines[break_block:break_block_end]
        # Exactly one free on the break path -- inner's, not outer's
        # (outer is declared outside the loop, merely used inside it).
        assert sum("call void @free(" in l for l in break_lines) == 1
        # outer is still freed exactly once overall, at the function's
        # own end (after the loop, whichever way it was exited).
        assert ir.count("call void @free(") == 3  # inner (break path) + inner (fall-through path) + outer

    def test_continue_frees_locals_declared_since_the_loop_body_began(self, parser, semantic, codegen):
        source = """
        void func f() {
            for int i = 0, i < 5, i++ {
                arr[int] p = [i]
                if p[0] % 2 == 0 {
                    continue
                }
                log(p[0])
            }
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        lines = ir.splitlines()
        continue_block = next(i for i, l in enumerate(lines) if l.strip().startswith("if.then"))
        continue_block_end = next(i for i in range(continue_block, len(lines)) if lines[i].strip().startswith("br label %for.update"))
        continue_lines = lines[continue_block:continue_block_end]
        assert sum("call void @free(" in l for l in continue_lines) == 1

    def test_nested_if_inside_a_loop_frees_correctly_on_both_the_break_and_fallthrough_paths(self, parser, semantic, codegen):
        source = """
        void func f() {
            for int i = 0, i < 5, i++ {
                arr[int] p = [i]
                if p[0] == 3 {
                    break
                }
                log(p[0])
            }
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        # One free on the break path (p, via break's own free-before-
        # branch), one on the normal fall-through-to-next-iteration path
        # (p, via the loop body's own natural end) -- two total, both p,
        # never both taken on the same iteration.
        assert ir.count("call void @free(") == 2

    def test_early_return_before_the_declaration_has_no_free_on_that_path(self, parser, semantic, codegen):
        source = """
        void func f(cond:bool) {
            if cond {
                return
            }
            arr[int] p = [1]
            log(p[0])
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        lines = ir.splitlines()
        func_start = next(i for i, l in enumerate(lines) if l.startswith("define void @f("))
        func_end = next(i for i in range(func_start, len(lines)) if lines[i] == "}")
        func_lines = lines[func_start:func_end]
        # The very first `ret void` (the early-return path, before p is
        # declared) must not be preceded by a free call anywhere earlier
        # in the function; the second (the fall-through path, after p is
        # declared) must be.
        ret_indices = [i for i, l in enumerate(func_lines) if l.strip() == "ret void"]
        assert len(ret_indices) == 2
        assert not any("call void @free(" in l for l in func_lines[:ret_indices[0] + 1])
        assert any("call void @free(" in l for l in func_lines[ret_indices[0] + 1:ret_indices[1] + 1])

    def test_event_handler_locals_are_analyzed_too(self, parser, semantic, codegen):
        source = """
        on mouseDown(x:int, y:int) {
            arr[int] p = [x]
            log(p[0])
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "call void @free(" in ir

    def test_event_handler_struct_local_is_stack_allocated_too(self, parser, semantic, codegen):
        source = """
        struct Point { x:int y:int }
        on mouseDown(x:int, y:int) {
            Point p
            p.x = x
            log(p.x)
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "alloca %struct.Point" in ir
        assert "call ptr @calloc(" not in ir

    # ---- end-to-end: real compiled programs, real output ----

    def test_non_escaping_struct_local_still_produces_correct_output(self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        void func f() {
            Point p
            p.x = 5
            p.y = 10
            log(p.x + p.y)
        }
        f()
        log('done')
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["15", "done"]

    def test_returning_a_struct_still_works_correctly(self, compile_and_run):
        # The exact shape of bug this stage must never reintroduce --
        # see security.md's original "returning a struct by value"
        # fix, and this session's own live demonstration of what
        # freeing an escaping local does to it.
        source = """
        struct Point { x:int y:int }
        Point func makePoint(a:int, b:int) {
            Point p
            p.x = a
            p.y = b
            return p
        }
        Point q1 = makePoint(10, 20)
        Point q2 = makePoint(999, 888)
        log(q1.x)
        log(q1.y)
        log(q2.x)
        log(q2.y)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["10", "20", "999", "888"]

    def test_recursive_function_with_a_non_escaping_struct_local_keeps_each_calls_own_value(
            self, compile_and_run):
        # The single most important correctness question the stack-
        # allocation swap (claude.md #43/#74/#75) raises: does each
        # recursive call really get its own, distinct stack slot for
        # p, or could a wrong assumption about LLVM's calling
        # convention let a deeper call's own p.x overwrite an
        # outstanding shallower call's? recur(n)'s own p.x must still
        # read back correctly *after* its own recursive call to
        # recur(n-1) returns -- if calls shared one slot, this would
        # read n-1's (or some other call's) value instead of n's own.
        source = """
        struct Point { x:int y:int }
        int func recur(n:int) {
            Point p
            p.x = n
            if n == 0 {
                return p.x
            }
            int inner = recur(n - 1)
            return p.x * 100 + inner
        }
        log(recur(3))
        log(recur(5))
        """
        result = compile_and_run(source)
        # recur(0)=0, recur(1)=100, recur(2)=300, recur(3)=600,
        # recur(4)=1000, recur(5)=1500 -- hand-derived, each building on
        # the previous: recur(n) = n*100 + recur(n-1).
        assert result.stdout.splitlines() == ["600", "1500"]

    def test_struct_assigned_into_a_global_keeps_its_value(self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        Point g
        void func f() {
            Point p
            p.x = 7
            g = p
        }
        f()
        log(g.x)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "7"

    def test_struct_passed_to_another_function_keeps_its_value(self, compile_and_run):
        # Since claude.md #74 stage 2, takesPoint's own read-only use of
        # its parameter (see test_struct_passed_to_a_non_retaining_function_is_now_freed)
        # means p is now provably safe throughout f() too -- p ends up
        # freed at the end of f(), but only *after* both reads (inside
        # takesPoint and f()'s own trailing log(p.x)) have already
        # happened, so the values here must still come out correct.
        source = """
        struct Point { x:int y:int }
        void func takesPoint(p:Point) {
            log(p.x)
        }
        void func f() {
            Point p
            p.x = 3
            takesPoint(p)
            log(p.x)
        }
        f()
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["3", "3"]

    def test_reassignment_keeps_the_new_values_valid(self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        void func f() {
            Point p
            p.x = 1
            Point q
            q.x = 2
            p = q
            log(p.x)
        }
        f()
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "2"

    def test_early_return_before_declaration_runs_correctly_on_both_paths(self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        void func f(cond:bool) {
            if cond {
                log('early')
                return
            }
            Point p
            p.x = 42
            log(p.x)
        }
        f(true)
        f(false)
        log('done')
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["early", "42", "done"]

    def test_non_escaping_array_and_map_locals_still_produce_correct_output(self, compile_and_run):
        source = """
        void func useArray() {
            arr[int] a = [1, 2, 3]
            log(a[0] + a[1] + a[2])
        }
        void func useMap() {
            map[int] m = {'a': 1, 'b': 2}
            log(m['a'] + m['b'])
        }
        useArray()
        useMap()
        log('done')
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["6", "3", "done"]

    def test_calling_a_freeing_function_many_times_does_not_crash(self, compile_and_run):
        # Not a memory-bound proof (see tests/CONTRACT.md for why an
        # RSS-based measurement isn't reliable against this compiler's
        # own O2 pipeline, which can eliminate an unobserved allocation
        # entirely) -- a basic robustness check that repeated alloc/free
        # of the same struct across many calls doesn't corrupt anything
        # an allocator would notice (a bad free()/double free is exactly
        # the kind of thing that tends to crash loudly under real
        # allocator bookkeeping, especially across enough iterations to
        # exercise its free-list reuse).
        source = """
        struct Point { x:int y:int }
        void func useLocal(n:int) {
            Point p
            p.x = n
            p.y = n * 2
        }
        int total = 0
        for int i = 0, i < 50000, i++ {
            useLocal(i)
            total = total + 1
        }
        log(total)
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.strip() == "50000"

    def test_nested_if_declared_struct_produces_correct_output(self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        void func f(cond:bool) {
            if cond {
                Point p
                p.x = 5
                log(p.x)
            }
            log('after')
        }
        f(true)
        f(false)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["5", "after", "after"]

    def test_loop_local_struct_produces_correct_output_every_iteration(self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        void func f() {
            for int i = 0, i < 5, i++ {
                Point p
                p.x = i * i
                log(p.x)
            }
        }
        f()
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["0", "1", "4", "9", "16"]

    def test_break_and_continue_with_loop_local_structs_produce_correct_output(self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        void func withBreak() {
            for int i = 0, i < 5, i++ {
                Point p
                p.x = i
                if p.x == 2 {
                    break
                }
                log(p.x)
            }
            log('after break loop')
        }
        void func withContinue() {
            for int i = 0, i < 5, i++ {
                Point p
                p.x = i
                if p.x % 2 == 0 {
                    continue
                }
                log(p.x)
            }
        }
        withBreak()
        withContinue()
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["0", "1", "after break loop", "1", "3"]

    def test_outer_scope_struct_survives_break_and_continue_from_a_nested_loop(self, compile_and_run):
        # The critical case: a struct declared OUTSIDE a loop and merely
        # used (not declared) inside it must keep its correct value --
        # break/continue only free locals declared since the loop's own
        # body began, never anything from an outer scope.
        source = """
        struct Point { x:int y:int }
        void func f() {
            Point outer
            outer.x = 100
            for int i = 0, i < 3, i++ {
                Point inner
                inner.x = i
                if inner.x == 1 {
                    break
                }
                log(inner.x + outer.x)
            }
            log(outer.x)
        }
        f()
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["100", "100"]

    def test_while_loop_local_struct_produces_correct_output(self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        void func f() {
            int i = 0
            while i < 4 {
                Point p
                p.x = i
                if p.x % 3 == 0 {
                    Point r
                    r.x = p.x * 100
                    log(r.x)
                }
                i = i + 1
            }
        }
        f()
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["0", "300"]

    def test_struct_escaping_a_loop_into_a_global_still_works_correctly(self, compile_and_run):
        # A value that genuinely escapes (assigned into a global) from
        # inside a loop must never be freed, on any path, including
        # break/continue -- it's excluded from every frame entirely,
        # the same escape_analysis.find_escaping_names result as ever.
        source = """
        struct Point { x:int y:int }
        Point g
        void func f() {
            for int i = 0, i < 3, i++ {
                Point p
                p.x = i
                g = p
            }
        }
        f()
        log(g.x)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "2"

    def test_many_loop_iterations_with_nested_if_and_break_continue_does_not_crash(self, compile_and_run):
        # A heavier robustness check than the stage-1 version above --
        # this one exercises the actual new machinery: a loop-local
        # struct freed every iteration, an if nested inside the loop
        # body with its own struct local, and break/continue both firing
        # repeatedly across many iterations, all in the same loop.
        source = """
        struct Point { x:int y:int }
        void func run(n:int) {
            Point outer
            outer.x = 0
            for int i = 0, i < n, i++ {
                Point p
                p.x = i
                if p.x % 7 == 0 {
                    continue
                }
                if p.x % 13 == 0 {
                    break
                }
                Point q
                q.x = p.x * 2
                outer.x = outer.x + q.x
            }
            log(outer.x)
        }
        for int i = 0, i < 20000, i++ {
            run(50)
        }
        log('done')
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.splitlines()[-1] == "done"

    # ---- edge cases beyond the two increments' own core scenarios ----
    # Added in a follow-up robustness pass over the whole feature (not a
    # new increment -- no new codegen.py behavior was needed for any of
    # these; they exercise combinations the two increments' own tests
    # didn't specifically spell out). Verified against real generated IR
    # and real compiled output here, and additionally against a combined
    # 5000-iteration program exercising every one of these patterns
    # together under AddressSanitizer/LeakSanitizer (see tests/CONTRACT.md)
    # -- zero ASan errors, zero leaks.

    def test_break_in_a_nested_loop_frees_only_the_inner_loops_own_locals(self, parser, semantic, codegen):
        # A loop-local declared in an OUTER loop's body, merely used (not
        # re-declared) inside a nested inner loop, must survive the inner
        # loop's own break -- free_depth is captured fresh by each
        # _emit_for call, so the inner loop's break must never reach past
        # its own body's frame down into the outer loop's. arr[T], not a
        # struct -- see test_a_loop_local_array_is_freed_inside_the_loop_body's
        # own note on why arr[T] is what still exercises this machinery
        # since the stack-allocation swap.
        source = """
        void func f() {
            for int i = 0, i < 3, i++ {
                arr[int] mid = [i]
                for int j = 0, j < 3, j++ {
                    arr[int] inner = [j]
                    if inner[0] == 1 {
                        break
                    }
                }
                log(mid[0])
            }
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        lines = ir.splitlines()
        inner_break_block = next(i for i, l in enumerate(lines) if l.strip().startswith("if.then"))
        inner_break_end = next(i for i in range(inner_break_block, len(lines))
                                if lines[i].strip().startswith("br label %for.end"))
        break_lines = lines[inner_break_block:inner_break_end]
        # Exactly one free on the inner break path -- inner's own, not mid's.
        assert sum("call void @free(" in l for l in break_lines) == 1

    def test_break_in_a_nested_loop_does_not_corrupt_the_outer_loops_local(self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        void func f() {
            for int i = 0, i < 3, i++ {
                Point mid
                mid.x = i * 100
                for int j = 0, j < 3, j++ {
                    Point inner
                    inner.x = j
                    if inner.x == 1 {
                        break
                    }
                }
                log(mid.x)
            }
        }
        f()
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["0", "100", "200"]

    def test_return_from_a_nested_if_after_the_loop_locals_declaration_frees_it(self, compile_and_run):
        # A return nested two levels deep inside a loop body (an if
        # inside the loop), textually AFTER the loop-local's own
        # declaration -- down_to=0 on Return must reach through the
        # if-then's own frame *and* the loop body's frame *and* the
        # function's own top-level frame, all at once.
        source = """
        struct Point { x:int y:int }
        int func f(n:int) {
            for int i = 0, i < n, i++ {
                Point p
                p.x = i
                if p.x == 3 {
                    return p.x * 100
                }
            }
            return -1
        }
        log(f(10))
        log(f(2))
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["300", "-1"]

    def test_return_from_a_nested_if_before_the_loop_locals_declaration_has_no_free_on_that_path(
            self, parser, semantic, codegen):
        # The mirror of test_early_return_before_the_declaration_has_no_free_on_that_path,
        # but with the early return nested inside a loop body instead of
        # directly in the function body -- the loop-local's VarDecl
        # hasn't been walked yet when this path's Return fires, so its
        # frame must still be empty on this specific path.
        source = """
        struct Point { x:int y:int }
        int func f(n:int) {
            for int i = 0, i < n, i++ {
                if i == 2 {
                    return 777
                }
                Point p
                p.x = i
            }
            return -1
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        lines = ir.splitlines()
        func_start = next(i for i, l in enumerate(lines) if l.startswith("define i64 @f("))
        func_end = next(i for i in range(func_start, len(lines)) if lines[i] == "}")
        func_lines = lines[func_start:func_end]
        ret_lines = [i for i, l in enumerate(func_lines) if l.strip().startswith("ret i64 777")]
        assert len(ret_lines) == 1
        assert not any("call void @free(" in l for l in func_lines[:ret_lines[0] + 1])

    def test_return_from_a_nested_if_before_the_loop_locals_declaration_produces_correct_output(
            self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        int func f(n:int) {
            for int i = 0, i < n, i++ {
                if i == 2 {
                    return 777
                }
                Point p
                p.x = i
            }
            return -1
        }
        log(f(10))
        log(f(1))
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["777", "-1"]

    def test_else_if_chain_frees_each_branchs_own_struct_local(self, compile_and_run):
        # Each `else if` is its own nested IfStmt (see parser.py's
        # parse_if), so each branch's then-block is its own _emit_block
        # frame -- a struct declared in one arm must never affect another
        # arm's freeing, and the un-taken arms' structs must never even
        # be allocated (ordinary control flow, unrelated to #74, but
        # worth pinning down together with the rest of this).
        source = """
        struct Point { x:int y:int }
        int func f(n:int) {
            if n == 0 {
                Point a
                a.x = 10
                return a.x
            } else if n == 1 {
                Point b
                b.x = 20
                return b.x
            } else if n == 2 {
                Point c
                c.x = 30
                return c.x
            } else {
                Point d
                d.x = 40
                return d.x
            }
        }
        log(f(0))
        log(f(1))
        log(f(2))
        log(f(3))
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["10", "20", "30", "40"]

    def test_bare_nested_block_frees_its_own_local(self, compile_and_run):
        # A standalone `{ }` block (not attached to if/while/for) is
        # still routed through the same _emit_block -- and two sibling
        # bare blocks reusing the same local name must not collide or
        # double-free (each is its own Env/frame).
        source = """
        struct Point { x:int y:int }
        int func f() {
            int total = 0
            {
                Point p
                p.x = 5
                total = total + p.x
            }
            {
                Point p
                p.x = 6
                total = total + p.x
            }
            return total
        }
        log(f())
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "11"

    def test_sibling_if_else_branches_can_declare_the_same_local_name_without_double_free(
            self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        int func f(cond:bool) {
            if cond {
                Point p
                p.x = 1
                return p.x
            } else {
                Point p
                p.x = 2
                return p.x
            }
        }
        log(f(true))
        log(f(false))
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["1", "2"]

    def test_combined_nested_loops_elif_and_bare_blocks_do_not_crash_under_heavy_iteration(
            self, compile_and_run):
        # The same combination verified separately by hand under
        # AddressSanitizer/LeakSanitizer (see tests/CONTRACT.md) -- here
        # as an ordinary correctness-and-no-crash pytest check: nested
        # for-in-for with an inner break, return after and before a
        # loop-local's own declaration, a 4-way else-if chain, bare
        # blocks with a shadowed name, and if/else sibling shadowing,
        # all run back to back many times.
        source = """
        struct Point { x:int y:int }
        int func nestedForBreak() {
            Point outer
            outer.x = 1000
            int total = 0
            for int i = 0, i < 4, i++ {
                Point mid
                mid.x = i
                for int j = 0, j < 4, j++ {
                    Point inner
                    inner.x = j
                    if inner.x == 2 {
                        break
                    }
                    total = total + inner.x
                }
                total = total + mid.x
            }
            total = total + outer.x
            return total
        }
        int func elifChain(n:int) {
            if n == 0 {
                Point a
                a.x = 10
                return a.x
            } else if n == 1 {
                Point b
                b.x = 20
                return b.x
            } else {
                Point c
                c.x = 30
                return c.x
            }
        }
        int func bareBlocks() {
            int total = 0
            {
                Point p
                p.x = 5
                total = total + p.x
            }
            {
                Point p
                p.x = 6
                total = total + p.x
            }
            return total
        }
        for int i = 0, i < 3000, i++ {
            if nestedForBreak() != 1010 {
                fail('nestedForBreak drifted')
            }
            if elifChain(i % 3) != (10 + (i % 3) * 10) {
                fail('elifChain drifted')
            }
            if bareBlocks() != 11 {
                fail('bareBlocks drifted')
            }
        }
        log('done')
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.strip() == "done"

    # ---- interprocedural (claude.md #74 stage 2) ----
    # See festina/escape_analysis.py's own module docstring and
    # tests/test_escape_analysis.py::TestInterproceduralEscapingParams
    # for the analysis in isolation (no C compiler, no codegen at all).
    # These check the other half: that CodeGen actually builds a
    # correct escaping_params table one function at a time, in program
    # order, and wires it into real generated IR and real compiled
    # output -- including the two cases the analysis-only tests can't
    # reach on their own (a real multi-function program, and a real
    # self-recursive function).

    def test_transitive_chain_of_non_retaining_calls_stack_allocates_the_original_local(
            self, parser, semantic, codegen):
        # a() -> b() -> c(), and c only reads its own parameter -- the
        # "safe" result has to propagate through b's own analysis (b's
        # parameter is only ever used as a pass-through argument to c)
        # before a's own local can be proven safe. Requires
        # escaping_params[c] to already exist by the time b is
        # analyzed, and escaping_params[b] to already exist by the time
        # a is analyzed -- exactly the program-order guarantee
        # CodeGen.escaping_params's own comment describes. Checks for a
        # stack alloca, not a free() -- since the stack-allocation swap
        # (claude.md #43/#74/#75), a struct proven safe this way never
        # goes through calloc+free at all.
        source = """
        struct Point { x:int y:int }
        void func c(q:Point) {
            log(q.x)
        }
        void func b(r:Point) {
            c(r)
        }
        void func a() {
            Point p
            b(p)
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        a_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define void @a("))
        a_body = "\n".join(ir.splitlines()[a_start:])
        assert "alloca %struct.Point" in a_body
        assert "call ptr @calloc(" not in a_body

    def test_transitive_chain_stops_freeing_at_the_first_retaining_link(
            self, parser, semantic, codegen):
        # Same shape, but c now retains its own parameter (stores it
        # into a global) -- the escaping-ness must propagate all the
        # way back UP the chain: b's own argument to c escapes, so b's
        # own parameter escapes, so a's own local passed to b escapes
        # too. Nothing anywhere in this chain should be freed.
        source = """
        struct Point { x:int y:int }
        Point stash
        void func c(q:Point) {
            stash = q
        }
        void func b(r:Point) {
            c(r)
        }
        void func a() {
            Point p
            b(p)
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "call void @free(" not in ir

    def test_self_recursive_function_passing_its_own_param_stays_conservative(
            self, parser, semantic, codegen):
        # f calls itself, passing its own struct parameter straight
        # through. self.escaping_params has no entry for 'f' yet at the
        # point f's own body is being walked (f isn't registered until
        # AFTER its own analysis completes), so this recursive call
        # site falls back to the original unconditional "any call
        # argument escapes" default, exactly like a call to an unknown
        # builtin would -- p is marked escaping via that one use, even
        # though every OTHER use of p in f is a safe field read. A
        # caller of f() must see that same conservative result: it must
        # not free its own local either, even though nothing in this
        # program actually corrupts anything either way.
        source = """
        struct Point { x:int y:int }
        void func f(p:Point, n:int) {
            log(p.x)
            if n > 0 {
                f(p, n - 1)
            }
        }
        void func g() {
            Point p
            f(p, 3)
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "call void @free(" not in ir

    def test_only_the_provably_safe_parameter_position_is_exempted(
            self, parser, semantic, codegen):
        # mixed(a, b): a is only ever read (safe), b is stored into a
        # global (escapes) -- escaping_params[mixed] must end up
        # {1}, not {0, 1} or {} -- and f()'s own call must stack-
        # allocate exactly the argument at the safe position (p,
        # position 0) and leave the other (q, position 1) heap-
        # allocated with a refcount header (claude.md #77 -- q is
        # escaping AND never returned, so it's also scheduled for
        # release, not just left leaking calloc'd), exactly the same
        # non-stack-allocated treatment escaping structs always got.
        source = """
        struct Point { x:int y:int }
        Point stash
        void func mixed(a:Point, b:Point) {
            log(a.x)
            stash = b
        }
        void func f() {
            Point p
            Point q
            mixed(p, q)
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        f_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define void @f("))
        f_body = "\n".join(ir.splitlines()[f_start:])
        p_storage_line = next(l for l in f_body.splitlines() if l.strip().startswith("%p.storage."))
        q_raw_line = next(l for l in f_body.splitlines() if l.strip().startswith("%q.raw."))
        assert "alloca %struct.Point" in p_storage_line
        assert "call ptr @calloc(" in q_raw_line
        assert "%p.raw." not in f_body
        assert "call void @festina_release(" in f_body

    def test_transitive_chain_produces_correct_output(self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        int func c(q:Point) {
            return q.x * 10
        }
        int func b(r:Point) {
            return c(r) + 1
        }
        int func a(n:int) {
            Point p
            p.x = n
            return b(p)
        }
        log(a(5))
        log(a(7))
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["51", "71"]

    def test_multi_param_mixed_escaping_produces_correct_output(self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        Point stash
        void func mixed(a:Point, b:Point) {
            log(a.x)
            stash = b
        }
        void func f() {
            Point p
            p.x = 1
            Point q
            q.x = 2
            mixed(p, q)
        }
        f()
        log(stash.x)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["1", "2"]

    def test_self_recursive_function_produces_correct_output_and_does_not_crash(
            self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        void func f(p:Point, n:int) {
            log(p.x)
            if n > 0 {
                f(p, n - 1)
            }
        }
        void func g() {
            Point p
            p.x = 42
            f(p, 3)
        }
        g()
        log('done')
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["42", "42", "42", "42", "done"]

    def test_interprocedural_combination_does_not_crash_under_heavy_iteration(
            self, compile_and_run):
        # The same combination verified separately by hand under
        # AddressSanitizer/LeakSanitizer (see tests/CONTRACT.md): a
        # transitive non-retaining chain, a transitive retaining chain,
        # a self-recursive function, and a multi-parameter
        # mixed-escaping function, all called many times in a loop.
        source = """
        struct Point { x:int y:int }
        Point stash
        int func readOnly(q:Point) {
            return q.x
        }
        int func passThrough(r:Point) {
            return readOnly(r)
        }
        int func nonRetainingChain(n:int) {
            Point p
            p.x = n
            return passThrough(p)
        }
        void func retains(q:Point) {
            stash = q
        }
        void func retainingChain(r:Point) {
            retains(r)
        }
        void func mixed(a:Point, b:Point) {
            log(a.x)
            stash = b
        }
        void func recur(p:Point, n:int) {
            if n > 0 {
                recur(p, n - 1)
            }
        }
        for int i = 0, i < 3000, i++ {
            if nonRetainingChain(i) != i {
                fail('nonRetainingChain drifted')
            }
            Point retainMe
            retainMe.x = i
            retainingChain(retainMe)
            if stash.x != i {
                fail('retainingChain drifted')
            }
            Point a
            a.x = i
            Point b
            b.x = i * 2
            mixed(a, b)
            if stash.x != i * 2 {
                fail('mixed drifted')
            }
            Point r
            r.x = i
            recur(r, 5)
        }
        log('done')
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.splitlines()[-1] == "done"

    # ---- reference counting (claude.md #77) ----
    # Scope for this stage: struct-typed GLOBALS (retain new/release old
    # on every reassignment) and escaping struct LOCALS that are never
    # themselves returned anywhere in their own function (released at
    # the same scope-exit points stages 1/2 already track). Struct
    # fields, arr[T]/map[T] values, and a value returned from a
    # function are all explicitly out of scope -- see todo.md.

    def test_global_struct_uses_a_sentinel_header_for_static_storage(
            self, parser, semantic, codegen):
        source = """
        struct Point { x:int y:int }
        Point g
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "@g.header = global {i64, %struct.Point} {i64 -1, %struct.Point zeroinitializer}" in ir
        assert "@g = global ptr getelementptr({i64, %struct.Point}, ptr @g.header, i32 0, i32 1)" in ir

    def test_global_struct_reassignment_retains_new_and_releases_old(
            self, parser, semantic, codegen):
        source = """
        struct Point { x:int y:int }
        Point g
        void func f() {
            Point p
            g = p
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        f_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define void @f("))
        f_body = "\n".join(ir.splitlines()[f_start:])
        # retain happens before release -- see _emit_assign's own
        # comment on why the order matters for self-assignment safety.
        retain_idx = f_body.index("call void @festina_retain(")
        release_idx = f_body.index("call void @festina_release(")
        assert retain_idx < release_idx

    def test_escaping_local_never_returned_is_released_at_scope_exit(
            self, parser, semantic, codegen):
        source = """
        struct Point { x:int y:int }
        Point g
        void func f() {
            Point p
            g = p
            log(p.x)
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        f_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define void @f("))
        f_body = "\n".join(ir.splitlines()[f_start:])
        # One release from the global assignment itself (releasing the
        # OLD global value, not p), one more from p's own scope-exit --
        # two total.
        assert f_body.count("call void @festina_release(") == 2

    def test_escaping_local_that_is_sometimes_returned_is_now_released_at_scope_exit(
            self, parser, semantic, codegen):
        # p is returned on ONE path (cond true) but not the other (cond
        # false, falls through to the end) -- textually identical
        # `return p` on both paths here, but that's just this source's
        # own shape, not something the retain-on-Return logic cares
        # about: every Return of a struct-typed value retains it first
        # (since a bare Identifier is an "aliasing" source, not an
        # owning one) and then _emit_free_active_locals releases every
        # active local, p included, on every path -- retain-then-
        # release-everything nets out to exactly one surviving
        # reference, the one just handed to the caller, on whichever
        # path actually runs. Three releases total: the global
        # assignment's own release of ITS previous value, plus one from
        # each of the two Return statements' own _emit_free_active_locals
        # call (only one of which runs on any given call, but both
        # appear in the IR).
        source = """
        struct Point { x:int y:int }
        Point g
        Point func f(cond:bool) {
            Point p
            g = p
            if cond {
                return p
            }
            return p
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        f_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define ptr @f("))
        f_body = "\n".join(ir.splitlines()[f_start:])
        assert f_body.count("call void @festina_release(") == 3
        # And a matching retain for each of the two Return statements,
        # on top of the one from `g = p` itself -- three total.
        assert f_body.count("call void @festina_retain(") == 3

    def test_loop_local_escaping_struct_is_released_every_iteration(
            self, parser, semantic, codegen):
        source = """
        struct Point { x:int y:int }
        Point g
        void func f() {
            for int i = 0, i < 3, i++ {
                Point p
                p.x = i
                g = p
            }
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        lines = ir.splitlines()
        body_start = next(i for i, l in enumerate(lines) if l.strip().startswith("for.body"))
        body_end = next(i for i in range(body_start, len(lines)) if lines[i].strip().startswith("br label %for.update"))
        body_lines = lines[body_start:body_end]
        # One release for the OLD global value, one for p's own
        # per-iteration scope-exit -- both inside the loop body, every
        # iteration, not just once after the loop as a whole exits.
        assert sum("call void @festina_release(" in l for l in body_lines) == 2

    def test_break_releases_the_escaping_loop_local(self, parser, semantic, codegen):
        source = """
        struct Point { x:int y:int }
        Point g
        void func f() {
            for int i = 0, i < 5, i++ {
                Point p
                p.x = i
                g = p
                if p.x == 2 {
                    break
                }
            }
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        lines = ir.splitlines()
        break_block = next(i for i, l in enumerate(lines) if l.strip().startswith("if.then"))
        break_block_end = next(i for i in range(break_block, len(lines)) if lines[i].strip().startswith("br label %for.end"))
        break_lines = lines[break_block:break_block_end]
        assert any("call void @festina_release(" in l for l in break_lines)

    def test_global_reassignment_in_a_loop_produces_correct_final_value(
            self, compile_and_run):
        # claude.md #77's own motivating case, found and documented
        # earlier in this same effort: a global repeatedly reassigned
        # inside a loop used to orphan every value but the last one,
        # leaking each -- now they're actually freed, and the reachable
        # (last) one must still read back correctly.
        source = """
        struct Point { x:int y:int }
        Point g
        void func run() {
            for int i = 0, i < 500, i++ {
                Point p
                p.x = i
                g = p
            }
        }
        run()
        log(g.x)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "499"

    def test_intermediate_global_values_stay_correct_across_reassignments(
            self, compile_and_run):
        # Not just the FINAL value -- every value read back immediately
        # after its own assignment (before the next iteration's release
        # could possibly touch it) must be correct too.
        source = """
        struct Point { x:int y:int }
        Point g
        void func run() {
            for int i = 0, i < 5, i++ {
                Point p
                p.x = i * i
                g = p
                log(g.x)
            }
        }
        run()
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["0", "1", "4", "9", "16"]

    def test_self_assignment_of_a_global_struct_does_not_crash(self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        Point g
        void func f() {
            Point p
            p.x = 7
            g = p
            g = g
            g = g
        }
        f()
        log(g.x)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "7"

    def test_escaping_and_sometimes_returned_local_still_produces_correct_output(
            self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        Point g
        Point func f(cond:bool) {
            Point p
            p.x = 3
            g = p
            if cond {
                return p
            }
            return p
        }
        Point q1 = f(true)
        Point q2 = f(false)
        log(q1.x)
        log(q2.x)
        log(g.x)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["3", "3", "3"]

    def test_two_different_global_structs_are_tracked_independently(
            self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        Point g1
        Point g2
        void func f() {
            Point p
            p.x = 1
            g1 = p
            Point q
            q.x = 2
            g2 = q
        }
        f()
        log(g1.x)
        log(g2.x)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["1", "2"]

    def test_local_struct_declared_with_a_call_result_initializer_keeps_the_value(
            self, compile_and_run):
        # A real, pre-existing (not introduced by claude.md #77) bug
        # found while testing this stage: `Point r = make(5)` for a
        # LOCAL r (inside a function -- a top-level/global one already
        # worked, via a completely different code path,
        # _emit_toplevel_stmt) silently discarded the initializer and
        # left r's fields at their stack-allocated zero value instead,
        # since _emit_stmt's own StructType VarDecl branch never
        # actually looked at stmt.init at all. Traced by hand-tracing
        # the generated IR (`%r.storage = alloca %struct.Point; store
        # ... zeroinitializer` with the make(5) call's own return value
        # never referenced again) before fixing it, not just from the
        # symptom.
        source = """
        struct Point { x:int y:int }
        Point func make(n:int) {
            Point p
            p.x = n
            return p
        }
        void func f() {
            Point r = make(5)
            log(r.x)
        }
        f()
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "5"

    def test_reassigning_a_local_struct_to_alias_another_does_not_double_release(
            self, compile_and_run):
        # A real bug found while first building claude.md #77's own
        # narrower initial scope: `Point q; q = p;` (q reassigned, after
        # its own declaration, to alias p's storage) then BOTH p and q
        # separately escaping (to two different globals) meant both
        # were scheduled for release at their own, independent scope-
        # exits -- with no retain anywhere to account for q's
        # reassignment creating a second reference, the second release
        # would decrement an already-zero (and, one more reassignment
        # later, already-freed) refcount. That narrower scope's own fix
        # was to exclude any reassigned struct local from release
        # tracking entirely (it leaked instead); this stage's own
        # widening replaces that exclusion with the actual missing
        # piece -- _emit_local_struct_retain_release now retains
        # whatever a reassignment's source aliases, which is what makes
        # it safe to schedule q for release too, not just avoid double-
        # releasing it. Confirmed via a real AddressSanitizer build with
        # the generated code itself properly instrumented
        # (`sanitize_address` added to every `define` -- see
        # tests/CONTRACT.md's own note on why
        # `clang -fsanitize=address -c file.ll` alone does not
        # instrument raw LLVM IR the way it does C source), not just
        # from this test's own passing assertion.
        source = """
        struct Point { x:int y:int }
        Point g1
        Point g2
        Point g3
        void func f() {
            Point p
            p.x = 5
            Point q
            q = p
            g1 = p
            g2 = q
        }
        f()
        Point other
        other.x = 999
        g1 = other
        log(g2.x)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "5"

    def test_struct_declared_with_an_initializer_is_now_included_in_release_tracking(
            self, parser, semantic, codegen):
        # claude.md #77 widened: r's own initializer is a Call (make's
        # return value), an "owning" source per _is_owning_struct_source
        # -- no retain needed there, r's alias just carries make()'s own
        # +1 forward. `g = r` retains it (2). Two releases total: r's
        # own scope-exit (2 -> 1, correctly still alive via g) and the
        # global assignment's own release of g's previous value
        # (static, a no-op, but the call still happens).
        source = """
        struct Point { x:int y:int }
        Point g
        Point func make(n:int) {
            Point p
            p.x = n
            return p
        }
        void func f() {
            Point r = make(5)
            g = r
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        f_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define void @f("))
        f_body = "\n".join(ir.splitlines()[f_start:])
        assert f_body.count("call void @festina_release(") == 2

    def test_reassigned_struct_is_now_included_in_release_tracking(
            self, parser, semantic, codegen):
        # claude.md #77 widened: q's own reassignment (`q = p`, p a bare
        # Identifier -- an "aliasing" source) now retains p's value and
        # releases q's OWN original allocation (freed immediately,
        # nothing else ever referenced it) -- this is exactly what makes
        # it safe to now ALSO schedule q for release at its own scope-
        # exit, unlike before this widening. Five releases total: q's
        # own reassignment releasing its original allocation, the two
        # global assignments' own release of their previous (static,
        # no-op) values, and p's and q's own scope-exit releases. Final
        # refcount ends at 2 (g1 and g2, the two true live references),
        # matching reality -- see this test's own compile-and-run
        # sibling
        # (test_reassigning_a_local_struct_to_alias_another_does_not_double_release)
        # for the correctness confirmation, not just the IR shape.
        source = """
        struct Point { x:int y:int }
        Point g1
        Point g2
        void func f() {
            Point p
            Point q
            q = p
            g1 = p
            g2 = q
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        f_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define void @f("))
        f_body = "\n".join(ir.splitlines()[f_start:])
        assert f_body.count("call void @festina_release(") == 5

    # ---- widening local retain/release (claude.md #77, same stage) ----

    def test_local_reassignment_from_an_existing_identifier_retains(
            self, parser, semantic, codegen):
        source = """
        struct Point { x:int y:int }
        void func f() {
            Point p
            Point q
            q = p
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        f_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define void @f("))
        f_body = "\n".join(ir.splitlines()[f_start:])
        assert "call void @festina_retain(" in f_body

    def test_local_reassignment_from_a_call_result_does_not_retain(
            self, parser, semantic, codegen):
        # make()'s own return value is a fresh, uniquely-owned value --
        # aliasing it into q needs no retain (see
        # _is_owning_struct_source's own comment).
        source = """
        struct Point { x:int y:int }
        Point func make(n:int) {
            Point p
            p.x = n
            return p
        }
        void func f() {
            Point q
            q = make(5)
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        f_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define void @f("))
        f_body = "\n".join(ir.splitlines()[f_start:])
        assert "call void @festina_retain(" not in f_body
        # Still releases q's own original allocation on the way out.
        assert "call void @festina_release(" in f_body

    def test_vardecl_init_from_an_existing_identifier_retains(
            self, parser, semantic, codegen):
        source = """
        struct Point { x:int y:int }
        void func f() {
            Point p
            Point q = p
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        f_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define void @f("))
        f_body = "\n".join(ir.splitlines()[f_start:])
        assert "call void @festina_retain(" in f_body

    def test_vardecl_init_from_a_call_result_does_not_retain(
            self, parser, semantic, codegen):
        source = """
        struct Point { x:int y:int }
        Point func make(n:int) {
            Point p
            p.x = n
            return p
        }
        void func f() {
            Point r = make(5)
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        f_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define void @f("))
        f_body = "\n".join(ir.splitlines()[f_start:])
        assert "call void @festina_retain(" not in f_body
        assert "call void @festina_release(" in f_body

    def test_with_init_local_that_never_further_escapes_is_freed_correctly(
            self, compile_and_run):
        # The gap stage 4's own initial, narrower scope explicitly left
        # open: a local declared with an initializer and never
        # otherwise escaping used to leak permanently, since it was
        # excluded from release tracking entirely. Now included --
        # correctness confirmed here; the actual freeing (not just "no
        # crash") is confirmed under AddressSanitizer/LeakSanitizer
        # (see tests/CONTRACT.md).
        source = """
        struct Point { x:int y:int }
        Point func make(n:int) {
            Point p
            p.x = n
            return p
        }
        void func f(n:int) {
            Point r = make(n)
            log(r.x)
        }
        for int i = 0, i < 5, i++ {
            f(i * i)
        }
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["0", "1", "4", "9", "16"]

    def test_reassignment_chain_through_two_globals_keeps_correct_values(
            self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        Point g1
        Point g2
        void func f(n:int) {
            Point p
            p.x = n
            Point q
            q = p
            g1 = p
            g2 = q
        }
        for int i = 0, i < 500, i++ {
            f(i)
        }
        log(g1.x)
        log(g2.x)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["499", "499"]

    def test_struct_field_read_used_as_a_reassignment_source_retains_correctly(
            self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        struct Outer { inner:Point label:text }
        Point g
        void func f(n:int) {
            Point p
            p.x = n
            Outer o
            o.inner = p
            Point q
            q = o.inner
            g = q
        }
        for int i = 0, i < 500, i++ {
            f(i)
        }
        log(g.x)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "499"

    def test_self_reassignment_of_a_local_struct_does_not_crash(self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        void func f(n:int) {
            Point p
            p.x = n
            p = p
            p = p
            log(p.x)
        }
        f(7)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "7"

    def test_widened_local_retain_release_combination_does_not_crash(
            self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        Point g1
        Point g2
        Point func make(n:int) {
            Point p
            p.x = n
            return p
        }
        void func run(iterations:int) {
            for int i = 0, i < iterations, i++ {
                Point r = make(i)
                if r.x != i {
                    fail('with-init local drifted')
                }
                Point p
                p.x = i
                Point q
                q = p
                g1 = p
                g2 = q
                if g1.x != i || g2.x != i {
                    fail('reassignment chain drifted')
                }
                Point acc
                acc = r
                acc = q
                if acc.x != i {
                    fail('multi-reassignment drifted')
                }
            }
        }
        run(3000)
        log('done')
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.strip() == "done"

    def test_many_global_reassignments_with_break_and_continue_does_not_crash(
            self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        Point g
        void func run(n:int) {
            for int i = 0, i < n, i++ {
                Point p
                p.x = i
                g = p
                if p.x % 7 == 0 {
                    continue
                }
                if p.x % 97 == 0 {
                    break
                }
            }
        }
        for int i = 0, i < 3000, i++ {
            run(50)
        }
        log('done')
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.strip() == "done"

    # -- retaining a function's own Return value (claude.md #77, widened
    # further): the last of the three conditions the original stage-4
    # scope excluded a struct local for is now handled too. See
    # tests/CONTRACT.md for the full writeup.

    def test_return_of_a_bare_identifier_retains(self, parser, semantic, codegen):
        source = """
        struct Point { x:int y:int }
        Point func f() {
            Point p
            return p
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        f_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define ptr @f("))
        f_body = "\n".join(ir.splitlines()[f_start:])
        assert "call void @festina_retain(" in f_body
        assert "call void @festina_release(" in f_body

    def test_return_of_a_call_result_does_not_retain(self, parser, semantic, codegen):
        source = """
        struct Point { x:int y:int }
        Point func make() {
            Point p
            return p
        }
        Point func f() {
            return make()
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        lines = ir.splitlines()
        f_start = next(i for i, l in enumerate(lines) if l.startswith("define ptr @f("))
        f_end = next(i for i in range(f_start, len(lines)) if lines[i].strip() == "}")
        f_body = "\n".join(lines[f_start:f_end])
        # f() itself has nothing to retain -- its own Return value is a
        # fresh, uniquely-owned call result, the same "no retain needed"
        # case a VarDecl-with-initializer or plain reassignment already
        # gets from a Call source. (make()'s own Return of its bare
        # local p is a separate, unrelated retain -- deliberately
        # excluded by only looking at f()'s own body.)
        assert "call void @festina_retain(" not in f_body
        assert "call void @festina_release(" not in f_body

    def test_returning_a_parameter_directly_keeps_the_callers_copy_correct(
            self, compile_and_run):
        # The soundness case this widening exists for, not just a leak
        # count: identity(x) hands x's own storage straight back out.
        # Without retaining it there, y (the caller's own copy of the
        # return value) would alias x's storage with no reference of
        # its own -- x's later scope-exit release would free memory y
        # still points to, and reading y afterward would be a genuine
        # use-after-free. Reading x AND y, well after x's own local
        # would ordinarily have been released, is the actual check.
        source = """
        struct Point { x:int y:int }
        Point func identity(p:Point) {
            return p
        }
        void func run() {
            for int i = 0, i < 2000, i++ {
                Point x
                x.x = i
                Point y = identity(x)
                if x.x != i || y.x != i {
                    fail('identity aliasing drifted')
                }
            }
        }
        run()
        log('done')
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.strip() == "done"

    def test_returning_a_ternary_between_two_locals_frees_the_untaken_branch(
            self, compile_and_run):
        # Neither branch of `cond ? a : b` is a bare Identifier Return
        # value in the old, narrower sense (the Return's own value
        # expression is the Ternary itself) -- this only works soundly
        # because the retain applies to whatever the Ternary evaluates
        # to at runtime, and _emit_free_active_locals still releases
        # BOTH a and b unconditionally afterward: the branch that was
        # actually returned nets out to one surviving reference (the
        # retain cancels its own release), and the untaken branch is
        # simply released and freed, same as if it had never been
        # returned at all.
        source = """
        struct Point { x:int y:int }
        Point func pick(cond:bool, n:int) {
            Point a
            a.x = n
            Point b
            b.x = n * 2
            return cond ? a : b
        }
        void func run() {
            for int i = 0, i < 2000, i++ {
                Point p = pick(i % 2 == 0, i)
                int expected = (i % 2 == 0) ? i : i * 2
                if p.x != expected {
                    fail('ternary return drifted')
                }
            }
        }
        run()
        log('done')
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.strip() == "done"

    # -- releasing a discarded return value (claude.md #77, same stage):
    # the one struct-return leak left standing after the retain-on-Return
    # fix above -- a call result never bound to anything at all. See
    # tests/CONTRACT.md for the full writeup.

    def test_discarded_struct_returning_call_result_is_released(
            self, parser, semantic, codegen):
        source = """
        struct Point { x:int y:int }
        Point func make(n:int) {
            Point p
            p.x = n
            return p
        }
        void func f() {
            make(5)
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        f_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define void @f("))
        f_body = "\n".join(ir.splitlines()[f_start:])
        assert "call void @festina_release(" in f_body

    def test_discarded_void_call_result_is_not_released(self, parser, semantic, codegen):
        # A negative check alongside the positive one above: a void
        # call has nothing to release (_emit_call returns ("0", None)
        # for it), and this fix only ever fires for a StructType result
        # -- confirms it doesn't misfire on the overwhelmingly more
        # common "call something for its side effects" case.
        source = """
        void func sideEffect() {
            log(1)
        }
        void func f() {
            sideEffect()
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        f_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define void @f("))
        f_body = "\n".join(ir.splitlines()[f_start:])
        assert "call void @festina_release(" not in f_body

    def test_discarded_struct_return_used_only_for_side_effects_still_crashes_safely(
            self, compile_and_run):
        # The struct value itself is thrown away, but the call still
        # runs and its side effect (the global write) still must happen
        # -- releasing the return value doesn't mean skipping the call.
        source = """
        struct Point { x:int y:int }
        int counter
        Point func makeAndCount(n:int) {
            counter = counter + 1
            Point p
            p.x = n
            return p
        }
        void func run(iterations:int) {
            for int i = 0, i < iterations, i++ {
                makeAndCount(i)
            }
        }
        run(2000)
        log(counter)
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.strip() == "2000"

    # -- reference counting for a struct's own struct-typed fields
    # (claude.md #78, new section): the nested-field gap sections 74-77
    # deliberately left open. See tests/CONTRACT.md for the full
    # writeup.

    def test_struct_field_write_from_an_identifier_retains(self, parser, semantic, codegen):
        source = """
        struct Inner { v:int }
        struct Outer { inner:Inner }
        void func f() {
            Inner i
            Outer o
            o.inner = i
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        f_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define void @f("))
        f_body = "\n".join(ir.splitlines()[f_start:])
        assert "call void @festina_retain(" in f_body

    def test_struct_field_write_from_a_call_result_does_not_retain(self, parser, semantic, codegen):
        source = """
        struct Inner { v:int }
        struct Outer { inner:Inner }
        Inner func make() {
            Inner i
            return i
        }
        void func f() {
            Outer o
            o.inner = make()
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        lines = ir.splitlines()
        f_start = next(i for i, l in enumerate(lines) if l.startswith("define void @f("))
        f_end = next(i for i in range(f_start, len(lines)) if lines[i].strip() == "}")
        f_body = "\n".join(lines[f_start:f_end])
        assert "call void @festina_retain(" not in f_body

    def test_struct_with_no_struct_fields_still_uses_the_generic_release(
            self, parser, semantic, codegen):
        # The common case (the overwhelming majority of structs have no
        # struct-typed field of their own) must stay exactly as cheap as
        # claude.md #77 already made it -- no per-type wrapper function
        # generated, no extra indirection, when there's nothing for a
        # release to cascade into.
        source = """
        struct Point { x:int y:int }
        Point g
        void func f() {
            Point p
            g = p
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "@__festina_release_struct_" not in ir
        assert "call void @festina_release(" in ir

    def test_struct_with_a_nested_struct_field_gets_a_dedicated_release_function(
            self, parser, semantic, codegen):
        source = """
        struct Inner { v:int }
        struct Outer { inner:Inner }
        Outer g
        void func f() {
            Outer o
            g = o
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "define void @__festina_release_struct_Outer(ptr %payload)" in ir
        # The wrapper itself must release the nested field via the
        # plain generic release (Inner has no struct-typed field of its
        # own) before freeing Outer's own storage.
        wrapper_start = ir.index("define void @__festina_release_struct_Outer(")
        wrapper_end = ir.index("\n}\n", wrapper_start)
        wrapper_body = ir[wrapper_start:wrapper_end]
        assert "call i8 @festina_release_check(" in wrapper_body
        assert "call void @festina_release(" in wrapper_body
        assert "call void @free(" in wrapper_body
        # And every release site for an Outer value (here, g's own
        # reassignment and o's own scope-exit) must call the wrapper,
        # not the plain generic release, directly.
        assert ir.count("call void @__festina_release_struct_Outer(") >= 2

    def test_deeply_nested_struct_fields_cascade_through_every_level(
            self, parser, semantic, codegen):
        # A -> B -> C, three levels deep -- confirms the recursive
        # wrapper-generation handles more than one level, not just the
        # immediate-child case every test above exercises.
        source = """
        struct C { v:int }
        struct B { c:C }
        struct A { b:B }
        A g
        void func f() {
            A a
            g = a
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "define void @__festina_release_struct_A(" in ir
        assert "define void @__festina_release_struct_B(" in ir
        # C has no struct-typed field of its own -- no wrapper needed.
        assert "define void @__festina_release_struct_C(" not in ir
        a_start = ir.index("define void @__festina_release_struct_A(")
        a_end = ir.index("\n}\n", a_start)
        a_body = ir[a_start:a_end]
        assert "call void @__festina_release_struct_B(" in a_body
        b_start = ir.index("define void @__festina_release_struct_B(")
        b_end = ir.index("\n}\n", b_start)
        b_body = ir[b_start:b_end]
        assert "call void @festina_release(" in b_body

    def test_nested_struct_field_reads_and_writes_correctly(self, compile_and_run):
        source = """
        struct Inner { v:int }
        struct Outer { inner:Inner }
        Outer g
        void func f(n:int) {
            Inner i
            i.v = n
            Outer o
            o.inner = i
            g = o
        }
        f(42)
        log(g.inner.v)
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.strip() == "42"

    def test_original_local_stays_correct_after_being_written_into_a_field(
            self, compile_and_run):
        # The exact aliasing hazard todo.md documented as the reason
        # this was deliberately deferred: `outer.field = someLocal`
        # aliases someLocal's own storage, not a copy. Without
        # retaining that reference on the way in, someLocal's own
        # later scope-exit release could free memory outer.field still
        # points to. Reading BOTH someLocal and outer.field.v, well
        # after someLocal's own scope would ordinarily have released
        # it, is the actual check -- 2000 iterations so a real
        # use-after-free (not just a lucky read of not-yet-reused
        # memory) would reliably surface as wrong output.
        source = """
        struct Inner { v:int }
        struct Outer { inner:Inner }
        void func f(n:int) {
            Inner i
            i.v = n
            Outer o
            o.inner = i
            if i.v != n || o.inner.v != n {
                fail('field aliasing drifted before scope exit')
            }
        }
        void func run(iterations:int) {
            for int i = 0, i < iterations, i++ {
                f(i)
            }
        }
        run(2000)
        log('done')
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.strip() == "done"

    def test_reassigning_a_struct_field_releases_the_old_value_correctly(
            self, compile_and_run):
        source = """
        struct Inner { v:int }
        struct Outer { inner:Inner }
        void func f(n:int) {
            Inner a
            a.v = n
            Inner b
            b.v = n * 2
            Outer o
            o.inner = a
            o.inner = b
            if o.inner.v != n * 2 {
                fail('field reassignment drifted')
            }
        }
        void func run(iterations:int) {
            for int i = 0, i < iterations, i++ {
                f(i)
            }
        }
        run(2000)
        log('done')
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.strip() == "done"

    def test_self_assignment_of_a_struct_field_does_not_crash(self, compile_and_run):
        source = """
        struct Inner { v:int }
        struct Outer { inner:Inner }
        void func f(n:int) {
            Inner i
            i.v = n
            Outer o
            o.inner = i
            o.inner = o.inner
            o.inner = o.inner
            if o.inner.v != n {
                fail('self-assignment corrupted')
            }
        }
        void func run(iterations:int) {
            for int i = 0, i < iterations, i++ {
                f(i)
            }
        }
        run(2000)
        log('done')
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.strip() == "done"

    def test_freeing_an_outer_struct_frees_its_nested_field_too(self, compile_and_run):
        # A correctness/no-crash check that the OUTER struct being
        # freed doesn't corrupt anything -- the actual leak-vs-freed
        # verification is done with a real AddressSanitizer/
        # LeakSanitizer run (see tests/CONTRACT.md); pytest itself
        # doesn't drive ASan builds.
        source = """
        struct Inner { v:int }
        struct Outer { inner:Inner }
        Outer g
        void func f(n:int) {
            Inner i
            i.v = n
            Outer o
            o.inner = i
            g = o
        }
        void func run(iterations:int) {
            for int i = 0, i < iterations, i++ {
                f(i)
            }
        }
        run(2000)
        log(g.inner.v)
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.strip() == "1999"

    # -- reference counting for arr[T]/map[T] values that escape
    # (claude.md #79, new section): arr[T]/map[T] is now a `ptr` to a
    # heap-allocated header, the same indirect, shared-identity
    # representation a struct value already has -- closing both the
    # escaping-array/map leak this whole roadmap item names, and a
    # separate, pre-existing memory-safety bug this representation
    # change fixes as a side effect (growing a map through one alias
    # left any other alias holding a dangling pointer). See
    # tests/CONTRACT.md for the full writeup.

    def test_with_init_array_local_is_refcounted_via_a_shared_header(
            self, parser, semantic, codegen):
        source = """
        arr[int] g
        void func f() {
            arr[int] a = [1, 2, 3]
            g = a
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        f_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define void @f("))
        f_body = "\n".join(ir.splitlines()[f_start:])
        # A literal source is "owning" -- no retain for a's own
        # declaration -- but g's own reassignment always retains.
        assert f_body.count("call void @festina_retain(") == 1
        assert "call void @festina_release_array(" in f_body

    def test_array_field_write_from_an_identifier_retains(self, parser, semantic, codegen):
        source = """
        struct Bag { items:arr[int] }
        void func f() {
            arr[int] a
            Bag b
            b.items = a
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        f_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define void @f("))
        f_body = "\n".join(ir.splitlines()[f_start:])
        assert "call void @festina_retain(" in f_body

    def test_array_field_write_from_a_literal_does_not_retain(self, parser, semantic, codegen):
        source = """
        struct Bag { items:arr[int] }
        void func f() {
            Bag b
            b.items = [1, 2, 3]
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        f_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define void @f("))
        f_body = "\n".join(ir.splitlines()[f_start:])
        assert "call void @festina_retain(" not in f_body

    def test_struct_with_an_array_field_gets_a_dedicated_release_function(
            self, parser, semantic, codegen):
        source = """
        struct Bag { items:arr[int] }
        Bag g
        void func f() {
            Bag b
            g = b
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "define void @__festina_release_struct_Bag(ptr %payload)" in ir
        wrapper_start = ir.index("define void @__festina_release_struct_Bag(")
        wrapper_end = ir.index("\n}\n", wrapper_start)
        wrapper_body = ir[wrapper_start:wrapper_end]
        assert "call void @festina_release_array(" in wrapper_body

    def test_no_struct_or_array_type_uses_extractvalue_anymore(self, parser, semantic, codegen):
        # claude.md #79: arr[T]/map[T] moved from a plain aggregate
        # VALUE to a `ptr`, the same representation change structs
        # already went through -- extractvalue on a FESTINA_ARRAY_LLVM_TYPE/
        # FESTINA_MAP_LLVM_TYPE value should never appear anywhere in
        # generated IR anymore (every read now goes through a GEP+load
        # on the pointer instead).
        source = """
        void func printer(v:int, k:text) {
            log(v)
        }
        arr[int] a = [1, 2, 3]
        map[int] m = {'x': 1}
        log(a.length)
        log(a[0])
        log(m['x'])
        m.forEach(printer)
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "extractvalue" not in ir

    def test_map_growth_through_one_alias_is_visible_through_another(self, compile_and_run):
        # The exact pre-existing (unrelated to refcounting) bug this
        # representation change fixes as a side effect: growing a map
        # via one alias used to leave any OTHER alias holding a stale
        # pointer into memory festina_map_set's own realloc had already
        # moved or freed -- confirmed directly, a real segfault, before
        # this section existed. Both aliases now share one identical
        # heap header, so festina_map_set's own mutation is visible to
        # both immediately, correctly, every time.
        source = """
        void func run(iterations:int) {
            for int i = 0, i < iterations, i++ {
                map[int] a = {'x': i}
                map[int] b = a
                b['y'] = i + 1
                if a['y'] != i + 1 {
                    fail('map growth not visible through original alias')
                }
                b['x'] = i + 100
                if a['x'] != i + 100 {
                    fail('map mutation not visible through original alias')
                }
            }
        }
        run(2000)
        log('done')
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.strip() == "done"

    def test_array_and_map_function_parameters_alias_the_callers_own_value(
            self, compile_and_run):
        source = """
        void func mutateFirst(a:arr[int], v:int) {
            a[0] = v
        }
        void func addEntry(m:map[int], k:text, v:int) {
            m[k] = v
        }
        void func run(iterations:int) {
            for int i = 0, i < iterations, i++ {
                arr[int] a = [i]
                mutateFirst(a, i + 1)
                if a[0] != i + 1 {
                    fail('array param mutation not visible to caller')
                }
                map[int] m = {'a': i}
                addEntry(m, 'b', i + 1)
                if m['b'] != i + 1 {
                    fail('map param mutation not visible to caller')
                }
            }
        }
        run(2000)
        log('done')
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.strip() == "done"

    def test_returning_an_array_or_map_keeps_the_correct_value(self, compile_and_run):
        source = """
        arr[int] func makeArr(n:int) {
            arr[int] a = [n, n * 2, n * 3]
            return a
        }
        map[int] func makeMap(n:int) {
            map[int] m = {'a': n}
            return m
        }
        void func run(iterations:int) {
            for int i = 0, i < iterations, i++ {
                arr[int] r = makeArr(i)
                if r[0] != i || r[1] != i * 2 || r[2] != i * 3 {
                    fail('returned array drifted')
                }
                map[int] mr = makeMap(i)
                if mr['a'] != i {
                    fail('returned map drifted')
                }
            }
        }
        run(2000)
        log('done')
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.strip() == "done"

    def test_discarded_array_and_map_call_results_do_not_crash(self, compile_and_run):
        source = """
        arr[int] func makeArr(n:int) {
            arr[int] a = [n]
            return a
        }
        map[int] func makeMap(n:int) {
            map[int] m = {'a': n}
            return m
        }
        void func run(iterations:int) {
            for int i = 0, i < iterations, i++ {
                makeArr(i)
                makeMap(i)
            }
        }
        run(2000)
        log('done')
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.strip() == "done"

    def test_recursive_function_over_an_array_parameter_works_correctly(self, compile_and_run):
        source = """
        int func sumRecursive(a:arr[int], idx:int) {
            if idx >= a.length {
                return 0
            }
            return a[idx] + sumRecursive(a, idx + 1)
        }
        void func run(iterations:int) {
            for int i = 0, i < iterations, i++ {
                arr[int] nums = [i, i + 1, i + 2, i + 3]
                if sumRecursive(nums, 0) != i + (i + 1) + (i + 2) + (i + 3) {
                    fail('sumRecursive drifted')
                }
            }
        }
        run(1000)
        log('done')
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.strip() == "done"

    def test_struct_field_of_array_and_map_type_reads_and_writes_correctly(
            self, compile_and_run):
        source = """
        struct Bag { items:arr[int] counts:map[int] }
        void func run(iterations:int) {
            for int i = 0, i < iterations, i++ {
                arr[int] items = [i, i + 1, i + 2]
                map[int] counts = {'total': i}
                Bag bag
                bag.items = items
                bag.counts = counts
                if bag.items[0] != i || bag.counts['total'] != i {
                    fail('struct arr/map field drifted')
                }
                bag.items = [i + 10]
                if bag.items[0] != i + 10 {
                    fail('struct arr field reassignment drifted')
                }
            }
        }
        run(2000)
        log('done')
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.strip() == "done"

    def test_global_array_reassigned_in_a_loop_produces_correct_final_value(
            self, compile_and_run):
        # claude.md #79's own motivating case, the same as claude.md
        # #77's original one for structs: a global repeatedly
        # reassigned inside a loop used to leak every value but the
        # last (arrays weren't refcounted at all before this section);
        # now each iteration's own value is correctly released, and the
        # final (reachable) one still reads back correctly.
        source = """
        arr[int] g
        void func run(iterations:int) {
            for int i = 0, i < iterations, i++ {
                arr[int] a = [i]
                g = a
            }
        }
        run(2000)
        log(g[0])
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.strip() == "1999"

    # -- reference counting for individual elements/values stored
    # *inside* an arr[T]/map[T] (claude.md #80, new section): claude.md
    # #79 made the array/map's own header a refcounted, shared-identity
    # value, but never touched what happens to a struct/arr/map ELEMENT
    # once it's copied into the array's data buffer or a map's entry --
    # that element was never retained on the way in, and never released
    # on the way out, leaving a struct-typed array element readable
    # after its original binding's own scope had already released it
    # (a confirmed, reproduced heap-use-after-free). See tests/CONTRACT.md
    # for the full writeup.

    def test_struct_element_of_an_escaping_array_survives_its_source_scope(
            self, compile_and_run):
        # The exact use-after-free claude.md #79 left open and #80
        # closes: a struct built fresh inside f(), stored as an
        # array's sole element, the array escaping through a global
        # while the struct's own local scope ends when f() returns.
        source = """
        struct Point { x:int y:int }
        arr[Point] g

        void func f(n:int) {
            Point p
            p.x = n
            arr[Point] pts = [p]
            g = pts
        }

        void func run(iterations:int) {
            for int i = 0, i < iterations, i++ {
                f(i)
                if g[0].x != i {
                    fail('array element of struct type did not survive its source scope')
                }
            }
        }
        run(2000)
        log('done')
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.strip() == "done"

    def test_struct_value_of_an_escaping_map_survives_its_source_scope(
            self, compile_and_run):
        source = """
        struct Point { x:int y:int }
        map[Point] g

        void func f(n:int) {
            Point p
            p.x = n
            map[Point] pts = {'a': p}
            g = pts
        }

        void func run(iterations:int) {
            for int i = 0, i < iterations, i++ {
                f(i)
                if g['a'].x != i {
                    fail('map value of struct type did not survive its source scope')
                }
            }
        }
        run(2000)
        log('done')
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.strip() == "done"

    def test_array_of_arrays_element_survives_its_source_scope(self, compile_and_run):
        source = """
        arr[arr[int]] g

        void func f(n:int) {
            arr[int] inner = [n, n + 1]
            arr[arr[int]] outer = [inner]
            g = outer
        }

        void func run(iterations:int) {
            for int i = 0, i < iterations, i++ {
                f(i)
                if g[0][0] != i || g[0][1] != i + 1 {
                    fail('array-of-arrays element did not survive its source scope')
                }
            }
        }
        run(2000)
        log('done')
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.strip() == "done"

    def test_reassigning_an_array_element_releases_the_old_value_correctly(
            self, compile_and_run):
        # arr[i]=v now shares the exact retain-new/release-old path
        # struct-field writes already use -- the old element (still
        # reachable nowhere else) must be released, and the new one
        # (an aliased identifier, not a fresh literal) must be retained,
        # without either double-freeing or leaking.
        source = """
        struct Box { v:int }
        void func run(iterations:int) {
            for int i = 0, i < iterations, i++ {
                Box b0
                Box b1
                arr[Box] boxes = [b0, b1]
                Box replacement
                replacement.v = i
                boxes[0] = replacement
                if boxes[0].v != i {
                    fail('array element reassignment drifted')
                }
            }
        }
        run(2000)
        log('done')
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.strip() == "done"

    def test_reassigning_a_map_value_releases_the_old_value_correctly(
            self, compile_and_run):
        source = """
        struct Box { v:int }
        void func run(iterations:int) {
            for int i = 0, i < iterations, i++ {
                Box b0
                map[Box] boxes = {'a': b0}
                Box replacement
                replacement.v = i
                boxes['a'] = replacement
                if boxes['a'].v != i {
                    fail('map value reassignment drifted')
                }
            }
        }
        run(2000)
        log('done')
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.strip() == "done"

    def test_non_escaping_array_of_structs_frees_its_elements_too(
            self, compile_and_run):
        # A purely local, never-escaping arr[Box] still stack-allocates
        # its own header (claude.md #76) but its Box elements are
        # heap-allocated (structs always are) -- the _StackArrayOrMap
        # scope-exit path must release each element before freeing the
        # data buffer, not just free the buffer itself.
        source = """
        struct Box { v:int }
        void func f(n:int) {
            Box b0
            Box b1
            arr[Box] boxes = [b0, b1]
            boxes[0].v = n
            boxes[1].v = n + 1
        }
        void func run(iterations:int) {
            for int i = 0, i < iterations, i++ {
                f(i)
            }
        }
        run(20000)
        log('done')
        """
        result = compile_and_run(source, args=None)
        assert result.returncode == 0
        assert result.stdout.strip() == "done"

    def test_array_literal_element_from_an_identifier_retains(self, parser, semantic, codegen):
        source = """
        struct Box { v:int }
        void func f() {
            Box b
            arr[Box] boxes = [b]
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        f_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define void @f("))
        f_body = "\n".join(ir.splitlines()[f_start:])
        assert "call void @festina_retain(" in f_body

    def test_array_literal_element_from_a_call_does_not_retain(self, parser, semantic, codegen):
        source = """
        struct Box { v:int }
        Box func makeBox() {
            Box b
            return b
        }
        void func f() {
            arr[Box] boxes = [makeBox()]
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        f_start = next(i for i, l in enumerate(ir.splitlines()) if l.startswith("define void @f("))
        f_body = "\n".join(ir.splitlines()[f_start:])
        assert "call void @festina_retain(" not in f_body

    def test_array_of_structs_release_wrapper_cascades_into_each_element(
            self, parser, semantic, codegen):
        source = """
        struct Box { v:int }
        arr[arr[Box]] g
        void func f() {
            Box b
            arr[Box] boxes = [b]
            arr[arr[Box]] outer = [boxes]
            g = outer
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        # A dedicated per-element-type release wrapper is generated for
        # arr[Box] (Box is refcounted), and outer's own wrapper (for
        # arr[arr[Box]]) must call into it rather than the generic,
        # element-blind @festina_release_array.
        assert "define void @__festina_release_array_" in ir
        wrapper_names = [
            l.split("define void @")[1].split("(")[0]
            for l in ir.splitlines() if l.startswith("define void @__festina_release_array_")
        ]
        assert len(wrapper_names) == 2
        outer_wrapper_body = None
        for name in wrapper_names:
            start = ir.index(f"define void @{name}(")
            end = ir.index("\n}\n", start)
            body = ir[start:end]
            if any(other in body for other in wrapper_names if other != name):
                outer_wrapper_body = body
        assert outer_wrapper_body is not None

    def test_map_of_structs_release_wrapper_uses_map_for_each(
            self, parser, semantic, codegen):
        source = """
        struct Box { v:int }
        map[Box] g
        void func f() {
            Box b
            map[Box] boxes = {'a': b}
            g = boxes
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "define void @__festina_release_map_" in ir
        wrapper_start = ir.index("define void @__festina_release_map_")
        wrapper_end = ir.index("\n}\n", wrapper_start)
        wrapper_body = ir[wrapper_start:wrapper_end]
        assert "call void @festina_map_for_each(" in wrapper_body

    def test_map_of_ints_still_uses_the_generic_release_function(
            self, parser, semantic, codegen):
        # An int-valued map has nothing to cascade into -- it must keep
        # using the plain, generic @festina_release_map from claude.md
        # #79 rather than generating a needless per-type wrapper.
        source = """
        map[int] g
        void func f() {
            map[int] m = {'a': 1}
            g = m
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "define void @__festina_release_map_" not in ir
        assert "call void @festina_release_map(" in ir


def _find_window(display, timeout=20):
    # A window actually appears in ~0.2s, measured, consistently -- this
    # timeout is generous insurance, not a figure anything is expected
    # to approach.
    #
    # It used to be 20s for a reason that turned out to be a
    # misdiagnosis, recorded here so it isn't re-derived: TestGraphics
    # was intermittently flaky (roughly a third of full-suite runs,
    # essentially never in isolation), and that was attributed to
    # contention pushing a window's startup past the old 10s, so the
    # timeout was doubled. Raising it never helped, and could not have:
    # the compiled program had already exited by then. The real cause
    # was a single un-retried XOpenDisplay in festina_graphics_init --
    # one transient connection refusal under load killed the program
    # outright, with a fatal error naming entirely the wrong cause ("is
    # $DISPLAY set?"). Fixed in the runtime (claude.md #87), which is
    # what actually made these tests deterministic; see that section for
    # the forensics proving the server was alive and reachable the whole
    # time. Module-level (not a method) so TestTimers's one combined
    # graphics+timers test can reuse it too.
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = subprocess.run(
            ["xdotool", "search", "--name", "Festina"],
            env=dict(os.environ, DISPLAY=display),
            capture_output=True, text=True,
        )
        wids = result.stdout.split()
        if wids:
            return wids[0]
        time.sleep(0.2)
    raise AssertionError("the Festina canvas window never appeared")


def _xwd_pixels(display, wid, points):
    """Capture a window with `xwd` and return the RGB at each (x, y).

    claude.md #89's colours are only really verified by looking at what
    actually landed on the canvas -- asserting the runtime call was
    emitted proves the plumbing, not that 'red' is red. xwd ships with
    the same x11-apps/x11-utils tier as xdotool, which these tests
    already require, so this needs no new dependency.

    XWD is a fixed 25-word big-endian header, then the window name, then
    the colormap, then rows of `bytes_per_line`; the r/g/b masks in the
    header say where each channel sits inside a pixel.
    """
    import struct
    dump = subprocess.run(["xwd", "-id", wid], env=dict(os.environ, DISPLAY=display),
                          capture_output=True, check=True).stdout
    hdr = struct.unpack(">25I", dump[:100])
    header_size, bpp, bytes_per_line = hdr[0], hdr[11], hdr[12]
    red_mask, green_mask, blue_mask, ncolors = hdr[14], hdr[15], hdr[16], hdr[19]
    body = dump[header_size + ncolors * 12:]

    def channel(value, mask):
        if not mask:
            return 0
        shift = (mask & -mask).bit_length() - 1
        width = bin(mask >> shift).count("1")
        c = (value & mask) >> shift
        return c << (8 - width) if width < 8 else c

    out = []
    nbytes = bpp // 8
    for x, y in points:
        base = y * bytes_per_line + x * nbytes
        value = int.from_bytes(body[base:base + nbytes], "little")
        out.append((channel(value, red_mask), channel(value, green_mask),
                    channel(value, blue_mask)))
    return out


def _wait_for_output(stdout_path, predicate, timeout=20):
    # Polls instead of a fixed sleep-then-assert, for the same reason
    # x_display polls for Xvfb readiness instead of a fixed sleep (see
    # conftest.py): reliable running one test in isolation but flaky
    # under full-suite load, since dispatch latency (X server ->
    # compiled handler -> log() -> this process's read) isn't constant.
    # Returns the last text read so a timed-out caller's own assert
    # still shows what actually came back, not just "timed out".
    # Module-level for the same reason _find_window is.
    deadline = time.time() + timeout
    text = ""
    while time.time() < deadline:
        text = stdout_path.read_text()
        if predicate(text):
            return text
        time.sleep(0.1)
    return text


class TestGraphics:
    """claude.md #37 (image), #39 (graphics), #40 (events) -- a real
    X11 window rendered via Cairo, not a file written to disk (see
    festina/codegen.py's module docstring's "Graphics" note for the
    full design).

    Three tiers, cheapest first:
    - Compilation only (no display needed at all) -- catches codegen/
      linking bugs (e.g. a wrong declare signature) fast.
    - The "no display available" error path -- also doesn't need a
      real display; it specifically tests running with none.
    - Real interactive tests against a window (via the x_display /
      run_graphics_program fixtures -- prefers an already-set DISPLAY,
      otherwise spins up a throwaway Xvfb instance; skips cleanly if
      neither Xvfb nor xdotool is available). These were also verified
      manually beyond what's automated here: actually capturing the
      rendered window (via xwd) and visually confirming drawRect/
      drawCircle/drawText/drawImage all render at the right positions,
      in the right shapes -- not automated here (would add xwd/netpbm
      as further test-only dependencies for something the manual check
      already gave high confidence in), but the click/mouse/key/resize
      dispatch tests below do run automatically, since xdotool + log()
      output is enough to prove real input reaches the compiled handler
      without needing to capture pixels at all.

    `on close` is the one handler NOT covered here even though the
    others in this tier are: it fires on the exact same WM_DELETE_WINDOW
    ClientMessage the window's own close-button handling already uses,
    and (as already true of that close-button path before `on close`
    ever existed) a bare Xvfb instance runs no window manager to
    translate `xdotool windowclose` into that message -- verified
    directly: it leaves the process running rather than triggering the
    handler. An environment limitation of the test setup, not a gap in
    the app's own (standard) handling of that protocol -- see
    festina_runtime.c's festina_handle_graphics_event for the actual
    dispatch, right alongside the click/mouse/key/resize dispatch this
    class does verify.

    One test in this class runs against a *fourth*, separate tier: a
    real window manager (`openbox`, via the `x_display_with_wm` fixture)
    rather than the bare, WM-less Xvfb every other test here uses --
    needed because a whole class of window-manager-reparenting race
    exists only under a real WM, and a bare Xvfb instance can never
    reproduce it no matter how many times a graphics program is run
    against it. Skips cleanly if `openbox` isn't installed, same as the
    rest of this tier skips cleanly without Xvfb/xdotool.
    """

    def test_compiles_and_links_successfully(self, cli_mod, tmp_path):
        # No display needed -- just confirms codegen emits valid IR and
        # the result links against Cairo/X11 successfully (claude.md
        # #59: graphics is a real new link-time dependency -- see
        # festina/cli.py's cairo-xlib wiring).
        if not (shutil.which("clang") or shutil.which("gcc") or shutil.which("cc")):
            pytest.skip("no C compiler (clang/gcc/cc) on PATH")
        source = """
        img icon = 'nonexistent.png'
        drawRect(0, 0, 100, 100)
        drawCircle(50, 50, 25)
        drawText('Hello', 20, 20)
        drawImage(icon, 10, 10)
        log(`${clientWidth}x${clientHeight}`)

        on mouseDown(x:int, y:int) {
            log(`press at ${x}, ${y}`)
        }
        on mouseUp(x:int, y:int) {
            log(`release at ${x}, ${y}`)
        }
        on mouse(x:int, y:int) {
            log(`mouse at ${x}, ${y}`)
        }
        on key(key:text) {
            log(`key ${key}`)
        }
        on resize() {
            log(`resize ${clientWidth} ${clientHeight}`)
        }
        on close() {
            log('closing')
        }
        """
        src_path = tmp_path / "main.f"
        src_path.write_text(source)
        out_path = tmp_path / "program"
        result_path = cli_mod.compile_file(str(src_path), str(out_path))
        assert result_path == str(out_path)
        assert out_path.exists()

    def test_missing_display_is_a_clear_runtime_error(self, compile_and_run, monkeypatch):
        # claude.md #95: it is render() that needs a display now, not
        # drawing -- drawing paints an offscreen canvas and is perfectly
        # happy with no X server, which is what makes headless PNG
        # output possible. So this is the call that must report it.
        monkeypatch.delenv("DISPLAY", raising=False)
        result = compile_and_run("drawRect(0, 0, 10, 10)\nrender()")
        assert result.returncode == 1
        assert "X display" in result.stderr

    def test_drawing_without_a_display_is_no_longer_an_error(
            self, compile_and_run, monkeypatch):
        monkeypatch.delenv("DISPLAY", raising=False)
        result = compile_and_run("drawRect(0, 0, 10, 10)\nlog('drew headlessly')")
        assert result.returncode == 0
        assert result.stdout == "drew headlessly\n"

    def test_invalid_image_path_is_a_clear_runtime_error(self, compile_and_run, monkeypatch):
        # claude.md #101 split "cannot open the file" from "cannot
        # decode what is in it" -- they are different mistakes and the
        # old single message named neither precisely.
        monkeypatch.delenv("DISPLAY", raising=False)
        result = compile_and_run("img icon = '/nonexistent/path.png'\nlog('unreachable')")
        assert result.returncode == 1
        assert "could not open image file" in result.stderr
        assert "unreachable" not in result.stdout

    def test_an_undecodable_image_names_both_supported_formats(self, compile_and_run, tmp_path,
                                                                 monkeypatch):
        monkeypatch.delenv("DISPLAY", raising=False)
        (tmp_path / "bad.png").write_bytes(b"this is not an image at all")
        result = compile_and_run("img icon = 'bad.png'\nlog('unreachable')")
        assert result.returncode == 1
        assert "not a PNG or JPEG" in result.stderr
        assert "unreachable" not in result.stdout

    def test_program_without_graphics_never_opens_a_window(self, compile_and_run, monkeypatch):
        # self.uses_graphics gates festina_graphics_init() -- a program
        # that never calls a graphics function or declares on
        # mouseDown/mouse must behave exactly as before: no window, no
        # blocking event loop, normal immediate exit. Verified here by
        # deliberately having no display available at all and
        # confirming the program still succeeds (if it tried to open a
        # window, it would fail exactly like the test above).
        monkeypatch.delenv("DISPLAY", raising=False)
        result = compile_and_run("log('no graphics here')")
        assert result.returncode == 0
        assert result.stdout.strip() == "no graphics here"

    def test_mouse_down_and_up_dispatch_separately_with_correct_coordinates(
            self, run_graphics_program, x_display):
        # claude.md #106: one xdotool "click" is a press AND a release,
        # so a program declaring both handlers sees two distinct events
        # from it -- which is the whole point of the split. `on click`
        # used to collapse them into the single line this once asserted.
        source = ("on mouseDown(x:int, y:int) {\n    log(`down ${x} ${y}`)\n}\n"
                  "on mouseUp(x:int, y:int) {\n    log(`up ${x} ${y}`)\n}\n")
        proc, stdout_path = run_graphics_program(source)
        try:
            wid = _find_window(x_display)
            env = dict(os.environ, DISPLAY=x_display)
            subprocess.run(["xdotool", "mousemove", "--window", wid, "150", "220"], env=env, check=True)
            subprocess.run(["xdotool", "click", "--window", wid, "1"], env=env, check=True)
            text = _wait_for_output(stdout_path, lambda t: "up 150 220" in t)
            # Order matters: the press has to arrive before the release.
            assert text.splitlines() == ["down 150 220", "up 150 220"]
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_a_drag_reports_different_press_and_release_coordinates(
            self, run_graphics_program, x_display):
        # claude.md #106's motivating case: hold the button down, move,
        # then let go. `on click` could not express this at all -- it
        # only ever saw the press. Here the two events report the two
        # ends of the drag, which is what makes it reconstructible.
        source = ("on mouseDown(x:int, y:int) {\n    log(`down ${x} ${y}`)\n}\n"
                  "on mouseUp(x:int, y:int) {\n    log(`up ${x} ${y}`)\n}\n")
        proc, stdout_path = run_graphics_program(source)
        try:
            wid = _find_window(x_display)
            env = dict(os.environ, DISPLAY=x_display)
            subprocess.run(["xdotool", "mousemove", "--window", wid, "100", "100"], env=env, check=True)
            subprocess.run(["xdotool", "mousedown", "--window", wid, "1"], env=env, check=True)
            _wait_for_output(stdout_path, lambda t: "down 100 100" in t)
            subprocess.run(["xdotool", "mousemove", "--window", wid, "400", "350"], env=env, check=True)
            subprocess.run(["xdotool", "mouseup", "--window", wid, "1"], env=env, check=True)
            text = _wait_for_output(stdout_path, lambda t: "up " in t)
            assert "down 100 100" in text
            assert "up 400 350" in text
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_mouse_move_dispatches_to_handler_with_correct_coordinates(self, run_graphics_program, x_display):
        source = "on mouse(x:int, y:int) {\n    log(`mouse ${x} ${y}`)\n}"
        proc, stdout_path = run_graphics_program(source)
        try:
            wid = _find_window(x_display)
            env = dict(os.environ, DISPLAY=x_display)
            subprocess.run(["xdotool", "mousemove", "--window", wid, "300", "400"], env=env, check=True)
            text = _wait_for_output(stdout_path, lambda t: "mouse 300 400" in t)
            assert "mouse 300 400" in text
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_key_down_dispatches_printable_and_named_keys(self, run_graphics_program, x_display):
        # claude.md #40/#98: `on keyDown(key:text)`. A printable key
        # (e.g. "a") comes back as its own character; a non-printable one
        # (e.g. Escape, whose ASCII value is an unprintable control code)
        # is not a useful `text` value, so it falls back to X11's own key
        # name instead -- see festina_key_name in the graphics runtime.
        source = "on keyDown(key:text) {\n    log(`key ${key}`)\n}"
        proc, stdout_path = run_graphics_program(source)
        try:
            wid = _find_window(x_display)
            env = dict(os.environ, DISPLAY=x_display)
            # The window needs real keyboard focus for KeyPress events to
            # reach it at all -- see festina_graphics_init's XSetInputFocus
            # call, needed since a bare Xvfb instance runs no window
            # manager to hand focus over the way a real desktop would.
            time.sleep(0.3)
            subprocess.run(["xdotool", "key", "--window", wid, "a"], env=env, check=True)
            subprocess.run(["xdotool", "key", "--window", wid, "Escape"], env=env, check=True)
            text = _wait_for_output(stdout_path, lambda t: len(t.splitlines()) >= 2)
            assert text.splitlines() == ["key a", "key Escape"]
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_key_up_fires_separately_and_reports_the_same_name(self, run_graphics_program, x_display):
        # claude.md #98: the whole point of the split -- a press and a
        # release are now distinguishable, and both name the same key the
        # same way (one shared festina_key_name), so a program can match
        # a release against the press that started it.
        source = ("on keyDown(key:text) {\n    log(`down ${key}`)\n}\n"
                  "on keyUp(key:text) {\n    log(`up ${key}`)\n}")
        proc, stdout_path = run_graphics_program(source)
        try:
            wid = _find_window(x_display)
            env = dict(os.environ, DISPLAY=x_display)
            time.sleep(0.3)
            subprocess.run(["xdotool", "key", "--window", wid, "a"], env=env, check=True)
            subprocess.run(["xdotool", "key", "--window", wid, "Left"], env=env, check=True)
            text = _wait_for_output(stdout_path, lambda t: len(t.splitlines()) >= 4)
            assert text.splitlines() == ["down a", "up a", "down Left", "up Left"]
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_only_the_declared_key_handler_fires(self, run_graphics_program, x_display):
        # A program that declares only keyUp must not also get keyDown's
        # events -- they are two independent registrations, not one
        # handler called twice.
        source = "on keyUp(key:text) {\n    log(`up ${key}`)\n}"
        proc, stdout_path = run_graphics_program(source)
        try:
            wid = _find_window(x_display)
            env = dict(os.environ, DISPLAY=x_display)
            time.sleep(0.3)
            subprocess.run(["xdotool", "key", "--window", wid, "b"], env=env, check=True)
            text = _wait_for_output(stdout_path, lambda t: t.strip() != "")
            time.sleep(0.3)
            with open(stdout_path) as f:
                assert f.read().splitlines() == ["up b"]
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_holding_a_key_does_not_fire_a_phantom_key_up(self, run_graphics_program, x_display):
        # claude.md #98: X's own auto-repeat synthesizes a
        # KeyRelease/KeyPress pair per repeat unless XKB's detectable
        # auto-repeat is on. Either way a HELD key must produce exactly
        # one keyUp -- the one where it is actually let go -- or the
        # split would be useless for the movement keys it exists for.
        source = ("on keyDown(key:text) {\n    log('down')\n}\n"
                  "on keyUp(key:text) {\n    log('up')\n}")
        proc, stdout_path = run_graphics_program(source)
        try:
            wid = _find_window(x_display)
            env = dict(os.environ, DISPLAY=x_display)
            time.sleep(0.3)
            subprocess.run(["xdotool", "keydown", "--window", wid, "a"], env=env, check=True)
            time.sleep(1.0)   # long enough for auto-repeat to kick in
            subprocess.run(["xdotool", "keyup", "--window", wid, "a"], env=env, check=True)
            _wait_for_output(stdout_path, lambda t: "up" in t.splitlines())
            time.sleep(0.3)
            with open(stdout_path) as f:
                lines = f.read().splitlines()
            assert lines.count("up") == 1, lines
            assert lines[-1] == "up", lines
            assert lines[0] == "down", lines
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_client_size_matches_the_initial_canvas_before_any_resize(self, run_graphics_program, x_display):
        # claude.md #95: reading the canvas size no longer opens a
        # window -- render() is what does, so the window this asserts on
        # comes from that call rather than from the size read.
        source = "log(`${clientWidth}x${clientHeight}`)\nrender()"
        proc, stdout_path = run_graphics_program(source)
        try:
            _find_window(x_display)  # also proves a bare reference opens a window
            text = _wait_for_output(stdout_path, lambda t: t.strip() != "")
            assert text.strip() == "800x600"
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_resize_dispatches_to_handler_and_updates_client_size(self, run_graphics_program, x_display):
        source = "on resize() {\n    log(`resize ${clientWidth} ${clientHeight}`)\n}"
        proc, stdout_path = run_graphics_program(source)
        try:
            wid = _find_window(x_display)
            env = dict(os.environ, DISPLAY=x_display)
            subprocess.run(["xdotool", "windowsize", wid, "640", "480"], env=env, check=True)
            text = _wait_for_output(stdout_path, lambda t: "resize 640 480" in t)
            assert "resize 640 480" in text
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_graphics_init_does_not_crash_under_a_real_window_manager(
            self, run_graphics_program, x_display_with_wm):
        # A confirmed, reproduced regression, not a hypothetical: this
        # exact source, against this exact fixture, crashed with "X
        # Error of failed request: BadMatch ... X_SetInputFocus" before
        # the fix -- reproduced directly against `openbox`, not assumed
        # from reading the X11 spec. `x_display` (every other test in
        # this class) is a bare Xvfb instance with no window manager at
        # all to race with, so it can never reproduce this on its own:
        # festina_graphics_init's own best-effort XSetInputFocus call
        # always just succeeds there, no matter how many times it's run.
        # See festina_ignore_focus_error's own comment in
        # festina_runtime_graphics.c for the fix this guards.
        # claude.md #95: reading the canvas size no longer opens a
        # window -- render() is what does, so the window this asserts on
        # comes from that call rather than from the size read.
        source = "log(`${clientWidth}x${clientHeight}`)\nrender()"
        proc, stdout_path = run_graphics_program(source, display=x_display_with_wm)
        try:
            _find_window(x_display_with_wm)  # the window actually opened and is mapped
            text = _wait_for_output(stdout_path, lambda t: t.strip() != "")
            assert text.strip() == "800x600"
            assert proc.poll() is None, (
                "graphics program exited unexpectedly under a real window manager "
                f"(the XSetInputFocus BadMatch regression?) -- stdout:\n"
                f"{stdout_path.read_text()}"
            )
        finally:
            proc.terminate()
            proc.wait(timeout=5)


class TestExampleGraphicsAndGame:
    """Interactive regression coverage for examples/graphics.f and
    examples/tic_tac_toe.f -- the two examples that need a real (or
    virtual) X server, so they can't join tests/test_examples.py's
    plain compile-and-check-stdout sweep. Lives here, not there, so it
    can reuse this file's own _find_window/_wait_for_output helpers and
    x_display/run_graphics_program fixtures, the same as TestGraphics
    above and TestTimers's combined graphics+timers test below."""

    def test_graphics_demo_dispatches_mouse_key_and_resize(self, run_graphics_program, x_display):
        source = open(os.path.join(_EXAMPLES_DIR, "graphics.f")).read()
        proc, stdout_path = run_graphics_program(source)
        try:
            wid = _find_window(x_display)
            env = dict(os.environ, DISPLAY=x_display)
            text = _wait_for_output(stdout_path, lambda t: "canvas started at 800x600" in t)
            assert "canvas started at 800x600" in text

            time.sleep(0.3)  # keyboard focus -- see TestGraphics's own note on this
            subprocess.run(["xdotool", "mousemove", "--window", wid, "100", "100"], env=env, check=True)
            subprocess.run(["xdotool", "click", "--window", wid, "1"], env=env, check=True)
            subprocess.run(["xdotool", "key", "--window", wid, "a"], env=env, check=True)
            subprocess.run(["xdotool", "windowsize", wid, "900", "700"], env=env, check=True)

            text = _wait_for_output(stdout_path, lambda t: "resized to 900x700" in t)
            # claude.md #106: one xdotool click produces both halves.
            assert "pressed at 100, 100" in text
            assert "released at 100, 100" in text
            assert "key pressed: a" in text
            assert "resized to 900x700" in text
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_tic_tac_toe_detects_a_win(self, run_graphics_program, x_display):
        # Same board layout as the example's own source (GRID_X=250,
        # GRID_Y=150, CELL=100) -- clicks the top row for X, the middle
        # row for O, then the rest of the top row, so X wins with three
        # across the top: (0,0), (1,0), (2,0).
        source = open(os.path.join(_EXAMPLES_DIR, "tic_tac_toe.f")).read()
        proc, stdout_path = run_graphics_program(source)
        try:
            wid = _find_window(x_display)
            env = dict(os.environ, DISPLAY=x_display)

            def click(x, y):
                subprocess.run(["xdotool", "mousemove", "--window", wid, str(x), str(y)], env=env, check=True)
                subprocess.run(["xdotool", "click", "--window", wid, "1"], env=env, check=True)
                time.sleep(0.15)

            click(300, 200)  # X: top-left
            click(300, 300)  # O: middle-left
            click(400, 200)  # X: top-middle
            click(400, 300)  # O: middle-middle
            click(500, 200)  # X: top-right -- completes the top row

            text = _wait_for_output(stdout_path, lambda t: "X wins!" in t)
            assert "X wins!" in text
        finally:
            proc.terminate()
            proc.wait(timeout=5)


class TestTimers:
    """claude.md #69: setTimeout/setInterval/clearTimeout/clearInterval.
    Added because Festina otherwise has no way to schedule work after
    the fact, the same gap JS's setTimeout/setInterval fill. See
    festina/semantic.py's _infer_call (the setTimeout/setInterval
    branch) and runtime/festina_runtime.h's doc comment for the full
    design.

    Most of this needs no display at all -- a timers-only program never
    opens a window (CodeGen.uses_timers is a separate flag from
    uses_graphics) -- so these mostly use compile_and_run like any other
    runtime-behavior test. The one exception,
    test_timers_and_graphics_work_together, is the one thing that
    genuinely needs a real window: proving festina_run_event_loop's
    select()-based multiplexing keeps both a `setInterval` callback and
    `on mouseDown` responsive together, not just one or the other.
    """

    def test_timeout_fires_once_after_the_delay(self, compile_and_run):
        source = (
            "void func onTimeout() {\n"
            "    log('fired')\n"
            "}\n"
            "log('start')\n"
            "setTimeout(onTimeout, 10)\n"
        )
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["start", "fired"]

    def test_interval_fires_repeatedly_until_cleared(self, compile_and_run):
        source = (
            "int count = 0\n"
            "int intervalId = 0\n"
            "void func onInterval() {\n"
            "    count = count + 1\n"
            "    log(`tick ${count}`)\n"
            "    if (count >= 3) {\n"
            "        clearInterval(intervalId)\n"
            "    }\n"
            "}\n"
            "intervalId = setInterval(onInterval, 5)\n"
        )
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["tick 1", "tick 2", "tick 3"]

    def test_clear_timeout_cancels_a_pending_callback(self, compile_and_run):
        source = (
            "void func shouldNotFire() {\n"
            "    log('should not happen')\n"
            "}\n"
            "void func shouldFire() {\n"
            "    log('fired')\n"
            "}\n"
            "int id = setTimeout(shouldNotFire, 50)\n"
            "clearTimeout(id)\n"
            "setTimeout(shouldFire, 5)\n"
        )
        result = compile_and_run(source)
        assert result.returncode == 0
        assert "should not happen" not in result.stdout
        assert "fired" in result.stdout

    def test_callback_can_schedule_another_timer(self, compile_and_run):
        # Proves a timer created *from inside* a firing callback is
        # still picked up by the same run -- festina_run_event_loop
        # recomputes the earliest deadline fresh on every pass rather
        # than fixing it once at loop entry, and festina_add_timer's
        # array can safely grow (realloc) from inside a callback that's
        # itself being called from a loop iterating that same array.
        source = (
            "int calls = 0\n"
            "void func step() {\n"
            "    calls = calls + 1\n"
            "    log(`step ${calls}`)\n"
            "    if (calls < 3) {\n"
            "        setTimeout(step, 5)\n"
            "    }\n"
            "}\n"
            "setTimeout(step, 5)\n"
        )
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["step 1", "step 2", "step 3"]

    def test_program_with_only_timeouts_exits_once_they_all_fire(self, compile_and_run):
        # No graphics, no uncleared interval -- festina_run_event_loop
        # must actually return once there's nothing left to wait for,
        # not block forever; compile_and_run's own subprocess timeout
        # (15s) would turn a regression here into a hard failure rather
        # than a slow pass, but this asserts on the normal, fast path.
        source = "void func cb() {\n    log('done')\n}\nsetTimeout(cb, 5)\n"
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "done"

    def test_uncleared_interval_keeps_the_program_running(self, tmp_path, codegen, cli_mod):
        # The one JS-matching behavior compile_and_run's fixed 15s
        # timeout can't cleanly demonstrate (it would just make the test
        # slow, not prove anything a shorter wait doesn't already show)
        # -- drive it directly instead: start the compiled program in
        # the background, confirm it's still alive and still logging
        # after a short wait, then kill it ourselves. No DISPLAY needed
        # at all here -- this is exactly the "timers without graphics"
        # case CodeGen.uses_timers exists to keep separate from
        # uses_graphics.
        if not (shutil.which("clang") or shutil.which("gcc") or shutil.which("cc")):
            pytest.skip("no C compiler (clang/gcc/cc) on PATH")
        source = "void func tick() {\n    log('tick')\n}\nsetInterval(tick, 5)\n"
        src_path = tmp_path / "main.f"
        src_path.write_text(source)
        out_path = tmp_path / "program"
        cli_mod.compile_file(str(src_path), str(out_path))
        stdout_path = tmp_path / "stdout.log"
        proc = subprocess.Popen(
            ["stdbuf", "-oL", str(out_path)],
            cwd=tmp_path, stdout=open(stdout_path, "w"), stderr=subprocess.STDOUT,
        )
        try:
            time.sleep(0.3)
            assert proc.poll() is None, "an uncleared setInterval should keep the program running"
            lines = stdout_path.read_text().splitlines()
            assert len(lines) >= 2, f"expected multiple 'tick's by now, got {lines!r}"
            assert all(line == "tick" for line in lines)
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_timers_and_graphics_work_together(self, run_graphics_program, x_display):
        source = (
            "void func tick() {\n"
            "    log('tick')\n"
            "}\n"
            "on mouseDown(x:int, y:int) {\n"
            "    log(`click ${x} ${y}`)\n"
            "}\n"
            "drawRect(0, 0, 10, 10)\n"
            "setInterval(tick, 15)\n"
        )
        proc, stdout_path = run_graphics_program(source)
        try:
            wid = _find_window(x_display)
            env = dict(os.environ, DISPLAY=x_display)
            text = _wait_for_output(stdout_path, lambda t: "tick" in t)
            assert "tick" in text, "the interval never fired alongside the open window"

            subprocess.run(["xdotool", "mousemove", "--window", wid, "5", "5"], env=env, check=True)
            subprocess.run(["xdotool", "click", "--window", wid, "1"], env=env, check=True)
            text = _wait_for_output(stdout_path, lambda t: "click 5 5" in t)
            assert "click 5 5" in text
            # The interval kept firing before *and* after the click --
            # proves festina_run_event_loop's select() call is genuinely
            # multiplexing both, not just alternating or starving one.
            assert text.count("tick") >= 2
        finally:
            proc.terminate()
            proc.wait(timeout=5)


def _write_wav(path, duration_s=0.2, sample_rate=8000, channels=1):
    """A minimal, valid 16-bit PCM WAV file (silence -- only the
    container format matters for claude.md #38's loadAudio())."""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(b"\x00\x00" * int(duration_s * sample_rate) * channels)


class TestAudio:
    """claude.md #38/#99/#100: aud, `aud m = 'path'`, .play()/
    .playLoop()/.isPlaying(), and stopAudioPlayer().

    Unlike TestGraphics, none of this needs an opt-in skip tier: the
    null-device trick audio_null_env uses (see conftest.py) needs no
    extra tool install the way Xvfb does -- ALSA's null plugin ships
    inside alsa-lib itself, which festina_runtime.c already links
    against unconditionally. So every test here only needs what
    compile_and_run already requires: a working C compiler.

    Two of the deterministic-by-design guarantees these tests lean on
    (see festina_runtime.c's festina_audio_play/_stop for why each is
    actually guaranteed, not just usually true): isPlaying() is true
    the instant play() returns (the flag is set synchronously, before
    the background playback thread is even spawned), and isPlaying()
    is false the instant stop() returns (stop() joins the thread before
    returning, it doesn't just signal it and hope).
    """

    def test_compiles_and_links_successfully(self, cli_mod, tmp_path):
        # No audio device needed -- just confirms codegen emits valid IR
        # and the result links against ALSA/pthread successfully
        # (claude.md #59: audio is a real new link-time dependency --
        # see festina/cli.py's alsa wiring).
        if not (shutil.which("clang") or shutil.which("gcc") or shutil.which("cc")):
            pytest.skip("no C compiler (clang/gcc/cc) on PATH")
        source = """
        aud music = 'nonexistent.wav'
        music.play()
        stopAudioPlayer()
        log(music.isPlaying())
        """
        src_path = tmp_path / "main.f"
        src_path.write_text(source)
        out_path = tmp_path / "program"
        result_path = cli_mod.compile_file(str(src_path), str(out_path))
        assert result_path == str(out_path)
        assert out_path.exists()

    def test_missing_audio_device_is_a_clear_runtime_error(self, compile_and_run, tmp_path):
        wav_name = "clip.wav"
        _write_wav(tmp_path / wav_name)
        # A fresh, empty HOME (no .asoundrc) -- guarantees ALSA's
        # "default" device resolution fails the same way it does in
        # this dev sandbox generally (verified: no /dev/snd node at
        # all), regardless of whatever the real test-runner's own
        # environment happens to have.
        empty_home = tmp_path / "empty_home"
        empty_home.mkdir()
        result = compile_and_run(
            f"aud music = '{wav_name}'\nmusic.play()\nlog('unreachable')",
            env={"HOME": str(empty_home)},
        )
        assert result.returncode == 1
        assert "could not open an audio output device" in result.stderr
        assert "unreachable" not in result.stdout

    def test_invalid_audio_path_is_a_clear_runtime_error(self, compile_and_run):
        result = compile_and_run(
            "aud music = '/nonexistent/path.wav'\nlog('unreachable')"
        )
        assert result.returncode == 1
        assert "could not open audio file" in result.stderr
        assert "unreachable" not in result.stdout

    def test_undecodable_audio_is_a_clear_runtime_error(self, compile_and_run, tmp_path):
        (tmp_path / "bad.wav").write_bytes(b"this is not a wav file at all")
        result = compile_and_run("aud music = 'bad.wav'\nlog('unreachable')")
        assert result.returncode == 1
        # claude.md #101: the message names both formats this runtime
        # decodes, since "not a WAV" stopped being the whole story.
        assert "not 16-bit PCM WAV or MP3" in result.stderr
        assert "unreachable" not in result.stdout

    def test_is_playing_true_immediately_after_play(self, compile_and_run, tmp_path, audio_null_env):
        _write_wav(tmp_path / "clip.wav")
        source = (
            "aud music = 'clip.wav'\n"
            "music.play()\n"
            "log(music.isPlaying())\n"
        )
        result = compile_and_run(source, env=audio_null_env)
        assert result.returncode == 0
        assert result.stdout.strip() == "true"

    def test_is_playing_false_immediately_after_stop(self, compile_and_run, tmp_path, audio_null_env):
        _write_wav(tmp_path / "clip.wav")
        source = (
            "aud music = 'clip.wav'\n"
            "music.play()\n"
            "stopAudioPlayer()\n"
            "log(music.isPlaying())\n"
        )
        result = compile_and_run(source, env=audio_null_env)
        assert result.returncode == 0
        assert result.stdout.strip() == "false"

    def test_stop_when_nothing_playing_is_a_safe_no_op(self, compile_and_run, tmp_path, audio_null_env):
        _write_wav(tmp_path / "clip.wav")
        source = "aud music = 'clip.wav'\nstopAudioPlayer()\nlog(music.isPlaying())\n"
        result = compile_and_run(source, env=audio_null_env)
        assert result.returncode == 0
        assert result.stdout.strip() == "false"

    def test_calling_play_again_while_playing_restarts_without_crashing(
        self, compile_and_run, tmp_path, audio_null_env
    ):
        _write_wav(tmp_path / "clip.wav", duration_s=1.0)
        source = (
            "aud music = 'clip.wav'\n"
            "music.play()\n"
            "music.play()\n"
            "log(music.isPlaying())\n"
        )
        result = compile_and_run(source, env=audio_null_env)
        assert result.returncode == 0
        assert result.stdout.strip() == "true"

    def test_max_audio_players_is_readable_and_clamped(self, compile_and_run):
        # claude.md #98: the limit is a tuning knob, so an unreasonable
        # value is clamped into [1, 64] rather than failing the program.
        # maxAudioPlayers() reads back what was actually applied.
        source = (
            "log(maxAudioPlayers())\n"
            "setMaxAudioPlayers(4)\n"
            "log(maxAudioPlayers())\n"
            "setMaxAudioPlayers(0)\n"
            "log(maxAudioPlayers())\n"
            "setMaxAudioPlayers(9999)\n"
            "log(maxAudioPlayers())\n"
        )
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["10", "4", "1", "64"]

    def test_setting_the_limit_links_the_audio_runtime(self, parser, semantic, codegen):
        # Both builtins live in the audio translation unit, so naming
        # either has to mark the program as using audio -- otherwise the
        # link would fail with an undefined symbol.
        program = parser.parse("setMaxAudioPlayers(3)", filename="main.f")
        analyzed = semantic.analyze(program, filename="main.f")
        gen = codegen.CodeGen(analyzed, "main.f")
        gen.generate(program)
        assert gen.uses_audio is True

    def test_overlapping_plays_all_keep_playing(self, compile_and_run, tmp_path, audio_null_env):
        # claude.md #98: the behaviour this replaced would have had the
        # second play() stop the first. Festina has no way to count
        # voices (deliberately -- the pool is not language surface), so
        # what is observable here is that three overlapping playbacks all
        # report as playing and nothing crashes; TestAudioVoicePool below
        # opens the runtime up and counts the voices directly.
        #
        # playLoop, not play, and that is not incidental: the null ALSA
        # device this runs against consumes PCM instantly (measured -- a
        # 2-second clip finishes in 0ms), so a one-shot can legitimately
        # finish between the play() call and the next statement. Written
        # with play() this test passed in isolation and failed under
        # full-suite load, which is a race in the TEST rather than in the
        # runtime -- "isPlaying() is true the instant play() returns" is
        # still honoured, it just says nothing about the statement after.
        # A loop never finishes on its own, so there is no window at all.
        _write_wav(tmp_path / "clip.wav", duration_s=1.0)
        source = (
            "aud music = 'clip.wav'\n"
            "music.playLoop(0)\n"
            "music.playLoop(1)\n"
            "music.playLoop(2)\n"
            "log(music.isPlaying())\n"
            "stopAudioPlayer()\n"
            "log(music.isPlaying())\n"
        )
        result = compile_and_run(source, env=audio_null_env)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["true", "false"]

    def test_a_limit_of_one_still_restarts_rather_than_failing(
        self, compile_and_run, tmp_path, audio_null_env
    ):
        # setMaxAudioPlayers(1) is the documented way to ask for the old
        # cut-off-the-previous-sound behaviour back.
        _write_wav(tmp_path / "clip.wav", duration_s=1.0)
        source = (
            "setMaxAudioPlayers(1)\n"
            "aud music = 'clip.wav'\n"
            "music.play()\n"
            "music.play()\n"
            "log(music.isPlaying())\n"
        )
        result = compile_and_run(source, env=audio_null_env)
        assert result.returncode == 0
        assert result.stdout.strip() == "true"

    def test_a_path_declares_a_clip_and_really_loads_it(self, compile_and_run, tmp_path,
                                                          audio_null_env):
        # claude.md #100: `aud m = 'path'`. The proof that it is a real
        # load and not just a type-check allowance is that playing it
        # works -- and that a bad path fails exactly the way
        # loadAudio()'s own bad path does.
        _write_wav(tmp_path / "clip.wav", duration_s=0.5)
        source = (
            "aud music = 'clip.wav'\n"
            "music.play()\n"
            "log(music.isPlaying())\n"
        )
        result = compile_and_run(source, env=audio_null_env)
        assert result.returncode == 0
        assert result.stdout.strip() == "true"

    def test_a_path_may_be_a_computed_text_expression(self, compile_and_run, tmp_path,
                                                        audio_null_env):
        # Unlike color/font (resolved at compile time, so literal-only),
        # this becomes a real loadAudio() call, so any text works.
        _write_wav(tmp_path / "clip.wav", duration_s=0.5)
        source = (
            "text name = 'clip'\n"
            "aud music = name + '.wav'\n"
            "music.play()\n"
            "log(music.isPlaying())\n"
        )
        result = compile_and_run(source, env=audio_null_env)
        assert result.returncode == 0
        assert result.stdout.strip() == "true"

    def test_a_bad_path_in_the_short_form_fails_the_same_way(self, compile_and_run):
        result = compile_and_run("aud music = '/nonexistent/path.wav'\nlog('unreachable')")
        assert result.returncode == 1
        assert "could not open audio file" in result.stderr
        assert "unreachable" not in result.stdout

    def test_stop_is_back_and_is_clip_wide(self, parser, semantic):
        # claude.md #109: #100 removed stop() because "stop this clip"
        # has only one honest reading -- every channel playing it -- and
        # that is rarely what an overlapping-effects program wants. True,
        # and not a reason to withhold it: silencing a looping hum or a
        # music bed is a real thing to want, and doing it by hand meant
        # tracking channels the runtime already knows. play() returning
        # its channel covers the other case, so both exist now.
        program = parser.parse("aud music = 'x.wav'\nmusic.stop()", filename="main.f")
        semantic.analyze(program, filename="main.f")

    def test_stop_by_channel_is_still_how_one_playback_is_addressed(
            self, parser, semantic, errors):
        program = parser.parse("aud music = 'x.wav'\nmusic.stop(2)", filename="main.f")
        with pytest.raises(errors.CompileError, match="stopAudioPlayer"):
            semantic.analyze(program, filename="main.f")

    def test_channels_and_loops_compile_and_run(self, compile_and_run, tmp_path, audio_null_env):
        # claude.md #99: every shape of the new surface, through the
        # real compiler and the real runtime. What is observable from
        # Festina is that these run and that stop/isPlaying still agree;
        # which CHANNEL each landed on is checked white-box in
        # TestAudioChannels, since the language deliberately cannot see
        # it.
        _write_wav(tmp_path / "clip.wav", duration_s=1.0)
        source = (
            "aud music = 'clip.wav'\n"
            "music.play()\n"
            "music.play(3)\n"
            "music.playLoop()\n"
            "music.playLoop(0)\n"
            "log(music.isPlaying())\n"
            "stopAudioPlayer(0)\n"
            "stopAudioPlayer(3)\n"
            "stopAudioPlayer()\n"
            "log(music.isPlaying())\n"
        )
        result = compile_and_run(source, env=audio_null_env)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["true", "false"]

    def test_a_looping_track_outlives_its_own_length(self, compile_and_run, tmp_path,
                                                       audio_null_env):
        # The one loop property observable from Festina: a clip that
        # would long since have finished is still playing. 50ms of
        # audio, checked 400ms later.
        _write_wav(tmp_path / "clip.wav", duration_s=0.05)
        source = (
            "aud music = 'clip.wav'\n"
            "void func check() {\n"
            "    log(`still playing: ${music.isPlaying()}`)\n"
            "    stopAudioPlayer(0)\n"
            "    log(`after stop: ${music.isPlaying()}`)\n"
            "}\n"
            "music.playLoop(0)\n"
            "setTimeout(check, 400)\n"
        )
        result = compile_and_run(source, env=audio_null_env)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["still playing: true", "after stop: false"]

    def test_a_non_looping_play_still_finishes_on_its_own(self, compile_and_run, tmp_path,
                                                            audio_null_env):
        # The control for the test above -- same clip, same delay,
        # play() instead of playLoop(). Without this pair, "still
        # playing" would not distinguish looping from a slow device.
        _write_wav(tmp_path / "clip.wav", duration_s=0.05)
        source = (
            "aud music = 'clip.wav'\n"
            "void func check() {\n"
            "    log(`still playing: ${music.isPlaying()}`)\n"
            "}\n"
            "music.play(0)\n"
            "setTimeout(check, 400)\n"
        )
        result = compile_and_run(source, env=audio_null_env)
        assert result.returncode == 0
        assert result.stdout.strip() == "still playing: false"

    def test_the_music_handover_from_the_motivating_example(self, compile_and_run, tmp_path,
                                                              audio_null_env):
        # claude.md #99's own worked example, shortened: two tracks
        # trading channel 0 back and forth. The point is that
        # isPlaying() flips each time, which only works if the second
        # playLoop(0) genuinely evicts the first.
        _write_wav(tmp_path / "adventure.wav", duration_s=0.05)
        _write_wav(tmp_path / "battle.wav", duration_s=0.05)
        source = (
            "aud adventureMusic = 'adventure.wav'\n"
            "aud battleMusic = 'battle.wav'\n"
            "void func changeMusic() {\n"
            "    if adventureMusic.isPlaying() {\n"
            "        battleMusic.playLoop(0)\n"
            "        log('battle')\n"
            "    } else {\n"
            "        adventureMusic.playLoop(0)\n"
            "        log('adventure')\n"
            "    }\n"
            "}\n"
            "int id = 0\n"
            "void func done() {\n"
            "    stopAudioPlayer(0)\n"
            "    clearInterval(id)\n"
            "}\n"
            "adventureMusic.playLoop(0)\n"
            "id = setInterval(changeMusic, 100)\n"
            "setTimeout(done, 450)\n"
        )
        result = compile_and_run(source, env=audio_null_env)
        assert result.returncode == 0
        lines = result.stdout.splitlines()
        # Strict alternation: each tick sees the other track playing.
        assert lines[:4] == ["battle", "adventure", "battle", "adventure"]

    def test_timers_and_audio_work_together(self, compile_and_run, tmp_path, audio_null_env):
        # A short clip finishes (isPlaying() -> false) on its own, with
        # no stop() call -- checked from a setTimeout callback, proving
        # audio playback and the timer event loop coexist correctly
        # (the background playback thread doesn't block __festina_main()
        # or festina_run_event_loop() on the main thread).
        _write_wav(tmp_path / "clip.wav", duration_s=0.05)
        source = (
            "aud music = 'clip.wav'\n"
            "void func check() {\n"
            "    log(`playing after delay: ${music.isPlaying()}`)\n"
            "}\n"
            "music.play()\n"
            "setTimeout(check, 200)\n"
        )
        result = compile_and_run(source, env=audio_null_env)
        assert result.returncode == 0
        assert result.stdout.strip() == "playing after delay: false"


_VOICE_POOL_HARNESS = r"""
/* claude.md #98: a WHITE-BOX check of the per-aud voice pool.
 *
 * Two things make this a C harness rather than a Festina program.
 *
 * First, the pool is deliberately not language surface: a Festina
 * program can ask whether a clip is playing, never how many copies of
 * it are, and stop() names the clip rather than one playback of it.
 * Adding a counter to the language purely to make this testable would
 * be the tail wagging the dog, so this includes the translation unit
 * directly, which gives it FestinaAudio/FestinaVoice (both file-local
 * types) and lets it count active voices for real.
 *
 * Second, the ALSA device layer is REPLACED here, via the macros
 * below, and that is not a shortcut -- it is the only way this test
 * can exist. The null ALSA device the rest of the audio tests use
 * consumes PCM instantly (measured: a 2-second clip finishes in 0ms),
 * so under it every voice is finished before the next play() begins
 * and there is no concurrency left to observe. A stub that sleeps per
 * chunk gives playback real duration under the harness's own control,
 * and needs no sound hardware, no ALSA config, and no device at all.
 * Everything above the device -- the pool, the stealing, the slot
 * reuse, the joining -- is the genuine runtime code.
 */
#include <alsa/asoundlib.h>
#include <time.h>

static int harness_pcm_open(snd_pcm_t **out) { *out = (snd_pcm_t *)1; return 0; }
static long harness_pcm_writei(snd_pcm_t *pcm, const void *buf, unsigned long frames) {
    (void)pcm; (void)buf;
    /* ~10ms per 4096-frame chunk, so a clip of a few chunks plays for
     * long enough that back-to-back play() calls genuinely overlap. */
    struct timespec ts = { 0, 10L * 1000L * 1000L };
    nanosleep(&ts, NULL);
    return (long)frames;
}

#define snd_pcm_open(pcmp, name, stream, mode) harness_pcm_open(pcmp)
#define snd_pcm_set_params(pcm, f, a, c, r, s, l) 0
#define snd_pcm_writei(pcm, buf, frames) harness_pcm_writei(pcm, buf, frames)
#define snd_pcm_recover(pcm, err, silent) (-1)
#define snd_pcm_close(pcm) 0

#include "festina_runtime_audio.c"

/* The only thing the audio unit needs from the core runtime, supplied
 * here so this harness does not have to link festina_runtime.c (and
 * with it sqlite3) for a test that is entirely about audio. */
void festina_fail(const char *msg) {
    fprintf(stderr, "fail: %s\n", msg ? msg : "");
    exit(1);
}

/* claude.md #110: the audio unit's save()/saveCopy() delegate the shared
 * write-and-maybe-adopt-the-path policy to the core runtime. Stubbed
 * rather than linked for the same reason festina_fail is: these
 * harnesses are entirely about the channel pool, and pulling in
 * festina_runtime.c would drag sqlite3 along with it. */
int8_t festina_save_bytes(const char *target, char **own_path,
                          const void *data, int64_t len,
                          const char *what, int8_t adopt) {
    (void)target; (void)own_path; (void)data; (void)len; (void)what; (void)adopt;
    return 0;
}

static int active_voices(void *audio) {
    int n = 0;
    pthread_mutex_lock(&g_audio_lock);
    for (int i = 0; i < FESTINA_AUDIO_PLAYER_CAP; i++) {
        if (g_channels[i].active && g_channels[i].clip == audio) n++;
    }
    pthread_mutex_unlock(&g_audio_lock);
    return n;
}

int main(int argc, char **argv) {
    if (argc < 2) return 2;
    void *clip = festina_load_audio(argv[1]);

    printf("default %lld\n", (long long)festina_get_max_audio_players());

    /* Three overlapping plays, well inside the default limit of 10.
     * Before claude.md #98 the second would have cut the first off and
     * this would read 1. */
    festina_audio_play_on(clip, 0, 0, 0);
    festina_audio_play_on(clip, 0, 0, 0);
    festina_audio_play_on(clip, 0, 0, 0);
    printf("three %d\n", active_voices(clip));
    printf("isplaying %d\n", (int)festina_audio_is_playing(clip));

    /* claude.md #100: playback is stopped by CHANNEL now, and a
     * negative channel means every channel. */
    festina_stop_audio_player(-1);
    printf("stopped %d\n", active_voices(clip));
    printf("isplaying_after_stop %d\n", (int)festina_audio_is_playing(clip));

    /* At the limit, the oldest voice is stolen rather than the new play
     * being dropped -- so the count saturates at the limit, never
     * exceeds it, and never collapses to zero however many plays pile
     * up. */
    festina_set_max_audio_players(2);
    for (int i = 0; i < 6; i++) festina_audio_play_on(clip, 0, 0, 0);
    printf("limit2 %d\n", active_voices(clip));

    /* A limit of 1 is exactly the old behaviour: one voice, restarted. */
    festina_stop_audio_player(-1);
    festina_set_max_audio_players(1);
    festina_audio_play_on(clip, 0, 0, 0);
    festina_audio_play_on(clip, 0, 0, 0);
    printf("limit1 %d\n", active_voices(clip));

    festina_stop_audio_player(-1);
    printf("final %d\n", active_voices(clip));

    /* Slots are REUSED, not grown: 40 plays through a pool of 3 must
     * never show more than 3 voices, which only holds if a finished
     * thread is joined and its slot reclaimed (see
     * festina_audio_reap_locked). */
    festina_set_max_audio_players(3);
    int peak = 0;
    for (int i = 0; i < 40; i++) {
        festina_audio_play_on(clip, 0, 0, 0);
        int n = active_voices(clip);
        if (n > peak) peak = n;
    }
    printf("peak %d\n", peak);
    festina_stop_audio_player(-1);
    printf("drained %d\n", active_voices(clip));
    return 0;
}
"""


_SINGLE_STREAM_HARNESS = r"""
/* claude.md #98: a "default" device that does NO software mixing, so
 * the second concurrent open fails with EBUSY -- a bare hw: device
 * with no dmix, which is ordinary on minimal/embedded Linux and on any
 * machine where another program holds the device exclusively.
 *
 * The voice pool opens one handle per voice, so this is the case where
 * layering is not physically available. What must NOT happen is the
 * program dying: overlapping plays have to degrade back to cutting
 * each other off, which is exactly what they did before there was a
 * pool at all.
 */
#include <alsa/asoundlib.h>
#include <time.h>

static int g_open_count = 0;
static int lim_open(snd_pcm_t **p) {
    if (g_open_count >= 1) return -EBUSY;
    g_open_count++;
    *p = (snd_pcm_t *)(long)g_open_count;
    return 0;
}
static int lim_close(snd_pcm_t *p) { (void)p; g_open_count--; return 0; }
static long lim_writei(snd_pcm_t *p, const void *b, unsigned long f) {
    (void)p; (void)b;
    struct timespec ts = { 0, 10L * 1000L * 1000L };
    nanosleep(&ts, NULL);
    return (long)f;
}

#define snd_pcm_open(pcmp, name, stream, mode) lim_open(pcmp)
#define snd_pcm_set_params(pcm, f, a, c, r, s, l) 0
#define snd_pcm_writei(pcm, buf, frames) lim_writei(pcm, buf, frames)
#define snd_pcm_recover(pcm, err, silent) (-1)
#define snd_pcm_close(pcm) lim_close(pcm)

#include "festina_runtime_audio.c"

void festina_fail(const char *msg) {
    fprintf(stderr, "fail: %s\n", msg ? msg : "");
    exit(1);
}

/* claude.md #110: the audio unit's save()/saveCopy() delegate the shared
 * write-and-maybe-adopt-the-path policy to the core runtime. Stubbed
 * rather than linked for the same reason festina_fail is: these
 * harnesses are entirely about the channel pool, and pulling in
 * festina_runtime.c would drag sqlite3 along with it. */
int8_t festina_save_bytes(const char *target, char **own_path,
                          const void *data, int64_t len,
                          const char *what, int8_t adopt) {
    (void)target; (void)own_path; (void)data; (void)len; (void)what; (void)adopt;
    return 0;
}

static int active_voices(void *audio) {
    int n = 0;
    pthread_mutex_lock(&g_audio_lock);
    for (int i = 0; i < FESTINA_AUDIO_PLAYER_CAP; i++) {
        if (g_channels[i].active && g_channels[i].clip == audio) n++;
    }
    pthread_mutex_unlock(&g_audio_lock);
    return n;
}

int main(int argc, char **argv) {
    if (argc < 2) return 2;
    void *clip = festina_load_audio(argv[1]);
    for (int i = 0; i < 5; i++) festina_audio_play_on(clip, 0, 0, 0);
    /* One stream is all the device has, so one voice is all there is --
     * and crucially the program is still running to say so. */
    printf("voices %d\n", active_voices(clip));
    printf("open_handles %d\n", g_open_count);
    printf("isplaying %d\n", (int)festina_audio_is_playing(clip));
    festina_stop_audio_player(-1);
    printf("after_stop %d\n", active_voices(clip));
    printf("leaked_handles %d\n", g_open_count);
    return 0;
}
"""


_CHANNEL_HARNESS = r"""
/* claude.md #99: named channels -- play(n)/playLoop(n)/
 * stopAudioPlayer(n), and the reservation playLoop takes out.
 *
 * White-box for the same two reasons the pool harness is (see
 * _VOICE_POOL_HARNESS): a Festina program cannot see which channel a
 * clip landed on, and the null ALSA device consumes PCM instantly so
 * there is no concurrency to observe under it. The device layer is
 * stubbed; the channel table is the real one.
 */
#include <alsa/asoundlib.h>
#include <time.h>

static int harness_pcm_open(snd_pcm_t **out) { *out = (snd_pcm_t *)1; return 0; }
static long harness_pcm_writei(snd_pcm_t *pcm, const void *buf, unsigned long frames) {
    (void)pcm; (void)buf;
    struct timespec ts = { 0, 10L * 1000L * 1000L };
    nanosleep(&ts, NULL);
    return (long)frames;
}

#define snd_pcm_open(pcmp, name, stream, mode) harness_pcm_open(pcmp)
#define snd_pcm_set_params(pcm, f, a, c, r, s, l) 0
#define snd_pcm_writei(pcm, buf, frames) harness_pcm_writei(pcm, buf, frames)
#define snd_pcm_recover(pcm, err, silent) (-1)
#define snd_pcm_close(pcm) 0

#include "festina_runtime_audio.c"

void festina_fail(const char *msg) {
    fprintf(stderr, "fail: %s\n", msg ? msg : "");
    exit(1);
}

/* claude.md #110: the audio unit's save()/saveCopy() delegate the shared
 * write-and-maybe-adopt-the-path policy to the core runtime. Stubbed
 * rather than linked for the same reason festina_fail is: these
 * harnesses are entirely about the channel pool, and pulling in
 * festina_runtime.c would drag sqlite3 along with it. */
int8_t festina_save_bytes(const char *target, char **own_path,
                          const void *data, int64_t len,
                          const char *what, int8_t adopt) {
    (void)target; (void)own_path; (void)data; (void)len; (void)what; (void)adopt;
    return 0;
}

/* Which channel index a clip is playing on, or -1. */
static int channel_of(void *clip) {
    int found = -1;
    pthread_mutex_lock(&g_audio_lock);
    for (int i = 0; i < FESTINA_AUDIO_PLAYER_CAP; i++) {
        if (g_channels[i].active && g_channels[i].clip == clip) { found = i; break; }
    }
    pthread_mutex_unlock(&g_audio_lock);
    return found;
}

static int locked_at(int i) {
    pthread_mutex_lock(&g_audio_lock);
    int v = g_channels[i].locked;
    pthread_mutex_unlock(&g_audio_lock);
    return v;
}

static int active_at(int i) {
    pthread_mutex_lock(&g_audio_lock);
    int v = g_channels[i].active;
    pthread_mutex_unlock(&g_audio_lock);
    return v;
}

static int active_total(void) {
    int n = 0;
    pthread_mutex_lock(&g_audio_lock);
    for (int i = 0; i < FESTINA_AUDIO_PLAYER_CAP; i++) if (g_channels[i].active) n++;
    pthread_mutex_unlock(&g_audio_lock);
    return n;
}

int main(int argc, char **argv) {
    if (argc < 3) return 2;
    void *adventure = festina_load_audio(argv[1]);
    void *battle = festina_load_audio(argv[2]);

    /* An explicit channel is honoured exactly. */
    festina_audio_play_on(adventure, 5, 1, 0);
    printf("explicit %d\n", channel_of(adventure));
    festina_stop_audio_player(5);

    /* playLoop reserves the channel it uses. */
    festina_audio_play_on(adventure, 0, 1, 1);
    printf("loop_channel %d\n", channel_of(adventure));
    printf("loop_locked %d\n", locked_at(0));

    /* A reserved channel is never taken by automatic assignment, even
     * under pressure: 30 pooled plays with a limit of 3 must never land
     * on channel 0 or evict what is looping there. */
    festina_set_max_audio_players(3);
    int stole_channel_0 = 0;
    for (int i = 0; i < 30; i++) {
        festina_audio_play_on(battle, 0, 0, 0);
        pthread_mutex_lock(&g_audio_lock);
        if (g_channels[0].clip != adventure) stole_channel_0 = 1;
        pthread_mutex_unlock(&g_audio_lock);
    }
    printf("stole_reserved %d\n", stole_channel_0);

    /* The looping clip is STILL playing after all that -- it has run
     * far past its own length, which only a real loop does. */
    printf("still_looping %d\n", (int)festina_audio_is_playing(adventure));

    /* The handover from the user's own example: a different clip named
     * on the same channel takes it over. */
    festina_audio_play_on(battle, 0, 1, 1);
    printf("handover_battle %d\n", channel_of(battle));
    printf("handover_adventure_playing %d\n", (int)festina_audio_is_playing(adventure));
    printf("handover_still_locked %d\n", locked_at(0));

    /* An explicit one-shot play() on a reserved channel takes it over
     * AND releases the reservation. */
    festina_audio_play_on(adventure, 0, 1, 0);
    printf("oneshot_released %d\n", locked_at(0));

    /* stopAudioPlayer(n) stops that channel and releases it. Asserted
     * per CHANNEL, not per clip: isPlaying() is deliberately clip-wide
     * (claude.md #98), so with the same clip also running on channel 0
     * from the block above it would stay true regardless of what
     * channel 2 does -- which would make this assert nothing. */
    festina_audio_play_on(adventure, 2, 1, 1);
    printf("before_stop_locked %d\n", locked_at(2));
    printf("before_stop_active %d\n", active_at(2));
    festina_stop_audio_player(2);
    printf("after_stop_locked %d\n", locked_at(2));
    printf("after_stop_active %d\n", active_at(2));

    /* A bare stopAudioPlayer() stops everything. */
    festina_audio_play_on(adventure, 1, 1, 1);
    festina_audio_play_on(battle, 4, 1, 1);
    festina_audio_play_on(battle, 0, 0, 0);
    printf("before_stop_all %d\n", active_total() > 0);
    festina_stop_audio_player(-1);
    printf("after_stop_all %d\n", active_total());
    printf("after_stop_all_locks %d\n", locked_at(1) + locked_at(4));

    /* A bare stopAudioPlayer() releases reservations too -- a looping
     * track told to stop is not still owed its channel. */
    festina_audio_play_on(adventure, 7, 1, 1);
    festina_stop_audio_player(-1);
    printf("clip_stop_locked %d\n", locked_at(7));
    printf("clip_stop_playing %d\n", (int)festina_audio_is_playing(adventure));
    return 0;
}
"""


class TestCircleMaskFastPath:
    """claude.md #104: filled circles are rasterized once per radius and
    stamped thereafter, instead of tessellating the curve every time.

    Measured on the canvas benchmark, circles were 90% of the frame --
    20,000 of them cost 76 ms against 10 ms for the same number of
    rectangles -- because cairo_arc + cairo_fill turns the curve into
    Beziers and scan-converts a general polygon on every call. Caching
    an A8 alpha mask per radius is 4.4x faster on that workload and took
    the whole frame from 90 ms to 31 ms.

    Everything here is about the fast path being INVISIBLE. It is only
    correct while the mask lands exactly where a tessellated circle
    would, so anything that would move it off the pixel grid -- a scale,
    a rotation, a fractional translation -- has to fall back, and a
    border has to fall back because a stroke needs a real path. Verified
    against the tessellating fallback over a whole 800x600 frame
    covering every one of these cases: 5 pixels of 480,000 differed, all
    by 1/255, all inside a gradient (sampling rounding, not geometry).
    """

    def _canvas(self, compile_and_run, monkeypatch, body, name="out.png"):
        monkeypatch.delenv("DISPLAY", raising=False)
        result = compile_and_run(f"clearCanvas()\n{body}\nlog(saveCanvas('{name}'))")
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "true"
        return result

    def test_a_filled_circle_is_actually_filled(self, compile_and_run, tmp_path, monkeypatch):
        self._canvas(compile_and_run, monkeypatch,
                      "fillStyle(200, 30, 30)\ndrawCircle(60, 60, 20)")
        _, _, pixel = _decode_png(str(tmp_path / "out.png"))
        assert pixel(60, 60) == (200, 30, 30)      # centre
        assert pixel(60, 45) == (200, 30, 30)      # inside, near the top edge
        assert pixel(60, 20) == (255, 255, 255)    # well outside
        assert pixel(95, 60) == (255, 255, 255)

    @pytest.mark.parametrize("radius", [1, 2, 3, 4, 8, 16, 32])
    def test_every_radius_covers_the_right_extent(self, compile_and_run, tmp_path,
                                                    monkeypatch, radius):
        # A mask stamped half a pixel off would show up here as an edge
        # landing one pixel early or late, at every radius independently.
        self._canvas(compile_and_run, monkeypatch,
                      f"fillStyle(0, 0, 0)\ndrawCircle(100, 100, {radius})")
        _, _, pixel = _decode_png(str(tmp_path / "out.png"))
        # Inked, not necessarily SOLID: a radius-1 circle does not fully
        # cover even its own centre pixel, so Cairo antialiases it to
        # grey -- in the fallback exactly as much as in the fast path
        # (compared directly: zero differing pixels at r=1). Demanding
        # pure black here would be testing Cairo's coverage arithmetic
        # rather than this cache.
        assert pixel(100, 100)[0] < 200, (radius, pixel(100, 100))
        # Just outside the circle in each direction is untouched white.
        for dx, dy in ((radius + 2, 0), (-radius - 2, 0), (0, radius + 2), (0, -radius - 2)):
            assert pixel(100 + dx, 100 + dy) == (255, 255, 255), (radius, dx, dy)

    def test_a_bordered_circle_still_gets_its_border(self, compile_and_run, tmp_path,
                                                      monkeypatch):
        # Must fall back: a stroke needs a real path, and the mask has
        # none. If the fast path ever swallowed this, the border would
        # silently vanish.
        self._canvas(compile_and_run, monkeypatch,
                      "color navy = 'navy'\n"
                      "fillStyle(255, 255, 0)\n"
                      "borderColor(navy)\n"
                      "lineWidth(4)\n"
                      "drawCircle(60, 60, 20)")
        _, _, pixel = _decode_png(str(tmp_path / "out.png"))
        assert pixel(60, 60) == (255, 255, 0)          # the fill
        r, g, b = pixel(60, 40)                         # on the border ring
        assert b > r and b > g, (r, g, b)

    def test_a_scaled_circle_is_the_scaled_size(self, compile_and_run, tmp_path, monkeypatch):
        # Must fall back: stamping a pre-rasterized mask under a scale
        # would resample it. The give-away is the extent -- a radius-10
        # circle drawn at scale 2 has to reach 20 pixels out.
        self._canvas(compile_and_run, monkeypatch,
                      "fillStyle(0, 0, 0)\nscale(2.0, 2.0)\ndrawCircle(50, 50, 10)")
        _, _, pixel = _decode_png(str(tmp_path / "out.png"))
        assert pixel(100, 100) == (0, 0, 0)
        assert pixel(100, 84) == (0, 0, 0)         # 16px out, inside a radius-20 circle
        assert pixel(100, 78) == (255, 255, 255)   # 22px out, beyond it

    def test_a_translated_circle_moves(self, compile_and_run, tmp_path, monkeypatch):
        # A whole-number translation KEEPS the fast path, so this checks
        # the offset is applied rather than ignored.
        self._canvas(compile_and_run, monkeypatch,
                      "fillStyle(0, 0, 0)\ntranslate(100, 50)\ndrawCircle(30, 30, 10)")
        _, _, pixel = _decode_png(str(tmp_path / "out.png"))
        assert pixel(130, 80) == (0, 0, 0)
        assert pixel(30, 30) == (255, 255, 255)

    def test_alpha_applies_to_a_circle(self, compile_and_run, tmp_path, monkeypatch):
        self._canvas(compile_and_run, monkeypatch,
                      "fillStyle(0, 0, 0)\nfillAlpha(0.5)\ndrawCircle(60, 60, 20)")
        _, _, pixel = _decode_png(str(tmp_path / "out.png"))
        r, g, b = pixel(60, 60)
        # Half-transparent black over white: mid grey, not solid black.
        assert 100 < r < 160 and r == g == b, (r, g, b)

    def test_a_gradient_fills_a_circle(self, compile_and_run, tmp_path, monkeypatch):
        self._canvas(compile_and_run, monkeypatch,
                      "color a = 'red'\n"
                      "color b = 'blue'\n"
                      "fillLinearGradient(40, 0, a, 80, 0, b)\n"
                      "drawCircle(60, 60, 20)")
        _, _, pixel = _decode_png(str(tmp_path / "out.png"))
        left = pixel(45, 60)
        right = pixel(75, 60)
        assert left[0] > left[2], left      # red end
        assert right[2] > right[0], right   # blue end

    def test_a_degenerate_radius_draws_nothing_and_does_not_crash(self, compile_and_run,
                                                                    tmp_path, monkeypatch):
        self._canvas(compile_and_run, monkeypatch,
                      "fillStyle(0, 0, 0)\ndrawCircle(60, 60, 0)\ndrawCircle(90, 60, -5)")
        _, _, pixel = _decode_png(str(tmp_path / "out.png"))
        assert pixel(60, 60) == (255, 255, 255)
        assert pixel(90, 60) == (255, 255, 255)

    def test_many_distinct_radii_do_not_break_the_cache(self, compile_and_run, tmp_path,
                                                          monkeypatch):
        # The cache holds 16 entries and evicts round-robin, so a program
        # cycling through more radii than that exercises eviction on
        # every draw. Correctness must not depend on a hit.
        body = "fillStyle(0, 0, 0)\n" + "\n".join(
            f"drawCircle({12 + (i % 30) * 25}, {30 + (i // 30) * 40}, {1 + i % 20})"
            for i in range(60))
        self._canvas(compile_and_run, monkeypatch, body)
        _, _, pixel = _decode_png(str(tmp_path / "out.png"))
        # Every one of the 60 got drawn somewhere: count inked centres
        # rather than assert a colour, since the smallest radii are
        # antialiased grey (see the extent test above).
        inked = sum(1 for i in range(60)
                     if pixel(12 + (i % 30) * 25, 30 + (i // 30) * 40)[0] < 200)
        assert inked == 60, inked


class TestAllNullLiterals:
    """claude.md #102: `arr[text] a = [null]` / `map[int] m = {'k': null}`.

    A literal whose values are ALL null infers its element type as null
    itself, and that was rejected against every declared element type --
    so the one literal shape meaning "entries, none of them meaningful
    yet" could not be written at all. `a.push(null)` and `m[k] = null`
    were both already fine, and `[null, 'x']` inferred text without
    complaint, which is what makes this an inconsistency rather than a
    policy.
    """

    def test_an_all_null_array_literal(self, compile_and_run):
        result = compile_and_run(
            "arr[text] a = [null]\nlog(a.length)\nlog(a[0] == null)")
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["1", "true"]

    def test_an_all_null_map_literal(self, compile_and_run):
        result = compile_and_run(
            "map[text] m = {'a': null, 'b': null}\nlog(m['a'] == null)\nlog(m['b'] == null)")
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["true", "true"]

    def test_the_declared_element_type_still_decides_the_null(self, compile_and_run):
        # An int's null is its own reserved sentinel, not a pointer --
        # so this also proves the declared type reaches the elements
        # rather than them being left as generic pointers.
        result = compile_and_run(
            "arr[int] a = [null, null]\nlog(a[1])\nmap[int] m = {'k': null}\nlog(m['k'])")
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["-9223372036854775808"] * 2

    def test_a_mixed_literal_still_infers_normally(self, compile_and_run):
        result = compile_and_run("arr[text] a = [null, 'x']\nlog(a.length)\nlog(a[1])")
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["2", "x"]

    def test_a_genuine_type_mismatch_is_still_rejected(self, parser, semantic, errors):
        # The allowance is for null specifically, not a hole in element
        # type checking.
        for source in ["arr[int] a = ['x']", "map[int] m = {'k': 'x'}"]:
            program = parser.parse(source, filename="main.f")
            with pytest.raises(errors.CompileError, match="cannot assign"):
                semantic.analyze(program, filename="main.f")


class TestNullComparisonOnEveryType:
    """claude.md #102: `x == null` on a pointer-backed type.

    Every one of these used to fail the COMPILE with an LLVM parse error
    naming a generated temporary -- `icmp eq i64 <a ptr>, null` is not
    valid IR at all. That is an internal-error message for something
    entirely reasonable to write, and it covered struct, arr[T], map[T],
    img, aud and regex: every managed type in the language except text.
    """

    @pytest.mark.parametrize("decl,expr", [
        ("struct S { n:int }\nS x", "x"),
        ("arr[int] x = []", "x"),
        ("map[int] x = {}", "x"),
        ("regex x = regex('a')", "x"),
        ("text x = 'a'", "x"),
        ("int x = 1", "x"),
        ("bool x = true", "x"),
    ])
    def test_a_live_value_is_not_null(self, compile_and_run, decl, expr):
        result = compile_and_run(f"{decl}\nlog({expr} == null)\nlog({expr} != null)")
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["false", "true"]

    def test_float_keeps_its_documented_nan_behaviour(self, compile_and_run):
        # Deliberately NOT in the list above. A null float is a real
        # NaN, and IEEE-754 says every ordered comparison against a NaN
        # is false -- so a live float is neither == null nor != null.
        # api.md states this; pinned here so the fix above is not
        # mistaken for a licence to "correct" it.
        result = compile_and_run("float x = 1.5\nlog(x == null)\nlog(x != null)")
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["false", "false"]

    def test_media_handles_compare_against_null(self, compile_and_run, tmp_path,
                                                  monkeypatch, sprite_sheet_png):
        monkeypatch.delenv("DISPLAY", raising=False)
        sheet = os.path.basename(sprite_sheet_png)
        result = compile_and_run(
            f"img p = '{sheet}'\naud a = 'beep.wav'\n"
            "log(p == null)\nlog(a == null)\n",
            env={"HOME": str(tmp_path)},
        ) if False else compile_and_run(f"img p = '{sheet}'\nlog(p == null)\nlog(p != null)")
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["false", "true"]

    def test_a_null_media_column_reads_as_null(self, compile_and_run, tmp_path,
                                                monkeypatch):
        # The case that surfaced this: a nullable BLOB column has to be
        # checkable, and there is no other way to ask.
        monkeypatch.delenv("DISPLAY", raising=False)
        source = """
        table Asset {
            name:text
            pic:img
        }
        sqlite('INSERT INTO Asset (name) VALUES (?)', ['nopic'])
        arr[Asset] rows = sqlite('SELECT * FROM Asset')
        log(rows[0].name)
        log(rows[0].pic == null)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["nopic", "true"]


class TestMediaColumnsLinkTheirRuntime:
    """claude.md #102: a table column of type `aud`/`img` is by itself
    enough to make a program use that feature.

    Two things need it -- main() registers the media decoders, and the
    per-table row release function calls that type's destructor -- and
    both emit calls into a translation unit that would otherwise not be
    linked. A program whose only use of audio was `file:aud` in a table
    therefore failed at the LINK step with an undefined reference to
    festina_audio_free: a compiler bug reported as a linker error.
    """

    @pytest.mark.parametrize("column,flag", [("aud", "uses_audio"),
                                              ("img", "uses_graphics_code")])
    def test_the_column_alone_sets_the_flag(self, parser, semantic, codegen, column, flag):
        program = parser.parse(
            f"table T {{ name:text asset:{column} }}\nlog('x')", filename="main.f")
        analyzed = semantic.analyze(program, filename="main.f")
        gen = codegen.CodeGen(analyzed, "main.f")
        gen.generate(program)
        assert getattr(gen, flag) is True

    def test_such_a_program_actually_links_and_runs(self, compile_and_run, monkeypatch):
        # The end-to-end version: nothing here names a single audio or
        # graphics function, so only the column types can pull the
        # runtime in.
        monkeypatch.delenv("DISPLAY", raising=False)
        source = """
        table T {
            name:text
            clip:aud
            pic:img
        }
        sqlite('INSERT INTO T (name) VALUES (?)', ['row'])
        arr[T] rows = sqlite('SELECT * FROM T')
        log(rows[0].name)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "row"


class TestFloatToIntIsNeverUndefined:
    """claude.md #102: Math.floor/ceil/round/trunc of a NaN, an infinity
    or an out-of-range double.

    A bare `fptosi` is undefined behaviour for all three -- not "some
    unspecified integer", genuinely undefined, and it behaved like it.
    Measured before the fix: `Math.floor(1.0 / 0.0)` printed a different
    value on every build, once a stack ADDRESS, and in one program
    `Math.floor(nan)` answered 1 while `Math.ceil(nan)` on the next line
    answered the null sentinel -- the optimizer had folded two identical
    UB sites differently. A language that returns null for division by
    zero rather than crashing cannot then hand back a stack address for
    Math.floor of that same null.
    """

    @pytest.mark.parametrize("fn", ["floor", "ceil", "round", "trunc"])
    def test_a_null_float_rounds_to_a_null_int(self, compile_and_run, fn):
        result = compile_and_run(
            f"float nan = 1.0 / 0.0\nlog(Math.{fn}(nan) == null)")
        assert result.returncode == 0
        assert result.stdout.strip() == "true"

    @pytest.mark.parametrize("fn", ["floor", "ceil", "round", "trunc"])
    def test_an_infinity_rounds_to_a_null_int(self, compile_and_run, fn):
        result = compile_and_run(
            "float inf = Math.exp(10000.0)\n"
            f"log(Math.{fn}(inf) == null)\n"
            "float ninf = 0.0 - inf\n"
            f"log(Math.{fn}(ninf) == null)\n")
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["true", "true"]

    def test_the_answer_is_the_same_on_every_run(self, compile_and_run):
        # The original symptom was non-determinism, so this checks for
        # non-determinism specifically rather than for a value.
        source = "float nan = 1.0 / 0.0\nlog(Math.floor(nan))\nlog(Math.ceil(nan))"
        first = compile_and_run(source).stdout
        for _ in range(3):
            assert compile_and_run(source).stdout == first
        assert set(first.split()) == {"-9223372036854775808"}

    def test_ordinary_values_are_untouched(self, compile_and_run):
        result = compile_and_run(
            "log(Math.floor(3.7))\nlog(Math.ceil(3.2))\nlog(Math.round(3.5))\n"
            "log(Math.round(0.0 - 3.5))\nlog(Math.trunc(0.0 - 3.9))\n"
            "log(Math.floor(0.0))\n")
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["3", "4", "4", "-4", "-3", "0"]


class TestDiscardedCallResultReachedForAField:
    """claude.md #102: `makeThing().count` -- a call result produced
    solely to read one field out of, with nothing binding it.

    claude.md #77 already released a call result discarded as a bare
    statement, on the reasoning that a Call's result is fresh and
    unshared by construction so that expression is provably its only
    reference. Reading a field off it is the same situation and was
    simply never covered: measured at one whole struct per evaluation,
    which in a loop is per iteration. Found by the leak stress suite.
    """

    def test_the_value_is_still_correct(self, compile_and_run):
        source = """
        struct Config { retries:int name:text }
        Config func load() {
            Config c
            c.retries = 3
            c.name = 'main'
            return c
        }
        int total = 0
        for int i = 0, i < 5, i++ {
            total = total + load().retries
        }
        log(total)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "15"

    def test_the_receiver_is_released_exactly_once(self, parser, semantic, codegen):
        source = """
        struct Config { retries:int }
        Config func load() {
            Config c
            c.retries = 3
            return c
        }
        void func use() {
            log(load().retries)
        }
        use()
        """
        program = parser.parse(source, filename="main.f")
        analyzed = semantic.analyze(program, filename="main.f")
        ir = codegen.generate_ir(program, analyzed, filename="main.f")
        body = ir.split("define void @use()")[1].split("\n}")[0]
        assert body.count("@festina_release") == 1

    def test_a_managed_field_survives_being_read_off_a_call(self, compile_and_run):
        # The restriction is load-bearing, not conservative: releasing
        # the parent recursively releases its struct-typed fields, so
        # doing it for a managed field would free the very value just
        # loaded. This case therefore still leaks, deliberately -- and
        # what must never happen is the alternative, which is that the
        # value read back is garbage or the program crashes. Pinned so a
        # later "optimization" cannot quietly turn a documented leak
        # into a use-after-free.
        source = """
        struct Inner { n:int label:text }
        struct Outer { inner:Inner }
        Outer func make(n:int) {
            Outer o
            o.inner.n = n
            o.inner.label = `label ${n}`
            return o
        }
        for int i = 0, i < 20, i++ {
            Inner got = make(i).inner
            if got.n != i {
                log('corrupted')
            }
            if got.label != `label ${i}` {
                log('corrupted')
            }
        }
        log('intact')
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "intact"


class TestChainedCallResultReachedForAField:
    """claude.md #108: `make().inner.n` -- a call result reached through
    a CHAIN of fields, which claude.md #102 could not cover.

    #102 released the receiver of a one-step read (`make().n`) and had
    to give up on `make().inner`, because releasing the parent there
    frees the Inner it is about to hand back. But that left the longer
    chain leaking the entire object graph: at `.inner` the field type is
    managed so nothing is released, and at `.n` the receiver is a Member
    rather than a Call so nothing notices the call result exists.
    Measured under LeakSanitizer at 5,200 bytes over 100 iterations.

    The decision now happens at the outermost link, where the type of
    the value that actually escapes the chain is known.
    """

    def test_a_chained_read_produces_the_right_value(self, compile_and_run):
        source = """
        struct Inner { n:int label:text }
        struct Outer { inner:Inner tag:text }
        Outer func make(v:int) {
            Outer o
            o.inner.n = v
            o.inner.label = 'L'
            o.tag = 'T'
            return o
        }
        int total = 0
        for int i = 0, i < 100, i++ {
            total = total + make(i).inner.n
        }
        log(total)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "4950"

    def test_the_chain_releases_the_call_result_exactly_once(
            self, parser, semantic, codegen):
        source = """
        struct Inner { n:int }
        struct Outer { inner:Inner }
        Outer func make() {
            Outer o
            o.inner.n = 3
            return o
        }
        void func use() {
            log(make().inner.n)
        }
        use()
        """
        program = parser.parse(source, filename="main.f")
        analyzed = semantic.analyze(program, filename="main.f")
        ir = codegen.generate_ir(program, analyzed, filename="main.f")
        body = ir.split("define void @use()")[1].split("\n}")[0]
        # Exactly one release, of the Outer -- Outer has a struct-typed
        # field so it gets #78's per-type wrapper rather than the plain
        # runtime function, and the wrapper cascades into Inner itself.
        assert body.count("@__festina_release_struct_Outer(") == 1
        assert body.count("@festina_release") == 0

    def test_a_chain_ending_in_a_managed_value_is_still_not_released(
            self, parser, semantic, codegen):
        # The restriction #102 identified is unchanged and still
        # load-bearing: if the value escaping the chain is itself
        # managed, releasing the parent would free it. Nothing is
        # emitted, and the leak stands -- deliberately.
        source = """
        struct Inner { n:int }
        struct Outer { inner:Inner }
        Outer func make() {
            Outer o
            o.inner.n = 3
            return o
        }
        void func use() {
            Inner got = make().inner
            log(got.n)
        }
        use()
        """
        program = parser.parse(source, filename="main.f")
        analyzed = semantic.analyze(program, filename="main.f")
        ir = codegen.generate_ir(program, analyzed, filename="main.f")
        body = ir.split("define void @use()")[1].split("\n}")[0]
        assert "@__festina_release_struct_Outer(" not in body

    def test_a_chain_ending_in_text_is_still_not_released(self, compile_and_run):
        # Same reasoning, and the same thing that must never happen:
        # the text read back must be intact, not freed out from under
        # the binding.
        source = """
        struct Inner { n:int label:text }
        struct Outer { inner:Inner }
        Outer func make(v:int) {
            Outer o
            o.inner.n = v
            o.inner.label = `label ${v}`
            return o
        }
        for int i = 0, i < 20, i++ {
            text got = make(i).inner.label
            if got != `label ${i}` {
                log('corrupted')
            }
        }
        log('intact')
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "intact"

    def test_length_off_a_call_result_is_released(self, parser, semantic, codegen):
        # claude.md #108: .length never reached _emit_member_load at all,
        # so #102 never covered `rowsFor(x).length` even though its own
        # docstring claimed it did. A length is an i64 copy that owes the
        # array nothing, so the receiver is always releasable.
        source = """
        arr[int] func rows(v:int) {
            arr[int] a = [v, v, v]
            return a
        }
        void func use() {
            log(rows(1).length)
        }
        use()
        """
        program = parser.parse(source, filename="main.f")
        analyzed = semantic.analyze(program, filename="main.f")
        ir = codegen.generate_ir(program, analyzed, filename="main.f")
        body = ir.split("define void @use()")[1].split("\n}")[0]
        assert body.count("@festina_release") == 1

    def test_length_reached_through_a_chain_is_released(self, compile_and_run):
        source = """
        struct Inner { items:arr[int] }
        struct Outer { inner:Inner }
        Outer func make(v:int) {
            Outer o
            o.inner.items.push(v)
            o.inner.items.push(v)
            return o
        }
        int total = 0
        for int i = 0, i < 50, i++ {
            total = total + make(i).inner.items.length
        }
        log(total)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "100"

    def test_a_member_load_in_a_call_argument_is_not_swallowed_by_the_chain(
            self, compile_and_run):
        # "Part of this chain" is decided by AST node identity, not by
        # "a chain is in flight". A member load reached while emitting a
        # call ARGUMENT belongs to no chain, and treating it as one
        # would move its release to a point that may never arrive.
        source = """
        struct Inner { n:int }
        struct Outer { inner:Inner }
        Outer func make(v:int) {
            Outer o
            o.inner.n = v
            return o
        }
        int total = 0
        for int i = 0, i < 50, i++ {
            total = total + make(make(i).inner.n).inner.n
        }
        log(total)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "1225"


class TestAudioChannelReturnAndClipStop:
    """claude.md #109: play()/playLoop() return the channel they chose,
    and aud.stop() is back as a clip-wide stop.

    These are two halves of one fix. claude.md #100 removed stop()
    because "stop this clip" only honestly means "every channel playing
    it", which is rarely what an overlapping-effects program wants --
    but the alternative it pointed at, stopAudioPlayer(n), needed a
    channel number that automatic assignment never told anyone. So the
    pool was addressable only by naming channels by hand, which is to
    say by not using the pool.
    """

    def test_play_returns_distinct_pooled_channels(
            self, compile_and_run, tmp_path, audio_null_env):
        _write_wav(tmp_path / "clip.wav", duration_s=1.0)
        source = """
        aud clip = 'clip.wav'
        int a = clip.play()
        int b = clip.play()
        int c = clip.play()
        log(a != b)
        log(b != c)
        log(a >= 0)
        """
        result = compile_and_run(source, env=audio_null_env)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["true", "true", "true"]

    def test_play_on_an_explicit_channel_returns_that_channel(
            self, compile_and_run, tmp_path, audio_null_env):
        _write_wav(tmp_path / "clip.wav", duration_s=1.0)
        source = """
        aud clip = 'clip.wav'
        log(clip.play(3))
        log(clip.playLoop(5))
        """
        result = compile_and_run(source, env=audio_null_env)
        assert result.stdout.splitlines() == ["3", "5"]

    def test_the_returned_channel_can_be_stopped(
            self, compile_and_run, tmp_path, audio_null_env):
        # The whole point: a channel chosen automatically is now
        # addressable, without the program having picked it up front.
        _write_wav(tmp_path / "clip.wav", duration_s=1.0)
        source = """
        aud clip = 'clip.wav'
        int ch = clip.playLoop()
        log(clip.isPlaying())
        stopAudioPlayer(ch)
        log(clip.isPlaying())
        """
        result = compile_and_run(source, env=audio_null_env)
        assert result.stdout.splitlines() == ["true", "false"]

    def test_stop_silences_every_channel_playing_the_clip(
            self, compile_and_run, tmp_path, audio_null_env):
        # Three overlapping voices of one clip, stopped by one call --
        # the case claude.md #100 said stop() could only mean, restored
        # because it is a real thing to want.
        # playLoop rather than play: against ALSA's null device a
        # one-shot drains instantly, so a finished voice would be
        # indistinguishable from a stopped one and the test would pass
        # for the wrong reason. A loop only ends when something stops it.
        _write_wav(tmp_path / "clip.wav", duration_s=1.0)
        source = """
        aud clip = 'clip.wav'
        clip.playLoop()
        clip.playLoop()
        clip.playLoop()
        log(clip.isPlaying())
        clip.stop()
        log(clip.isPlaying())
        """
        result = compile_and_run(source, env=audio_null_env)
        assert result.stdout.splitlines() == ["true", "false"]

    def test_stop_leaves_another_clip_alone(
            self, compile_and_run, tmp_path, audio_null_env):
        # Clip-wide, not global -- the distinction between stop() and
        # stopAudioPlayer() with no argument.
        _write_wav(tmp_path / "a.wav", duration_s=1.0)
        _write_wav(tmp_path / "b.wav", duration_s=1.0)
        source = """
        aud a = 'a.wav'
        aud b = 'b.wav'
        a.playLoop()
        b.playLoop()
        a.stop()
        log(a.isPlaying())
        log(b.isPlaying())
        """
        result = compile_and_run(source, env=audio_null_env)
        assert result.stdout.splitlines() == ["false", "true"]

    def test_stop_returns_a_reserved_channel_to_the_pool(
            self, compile_and_run, tmp_path, audio_null_env):
        # A playLoop channel is reserved (claude.md #99). Stopping the
        # clip has to release that reservation too -- silencing the
        # sound while leaving the channel locked would quietly shrink
        # the pool every time.
        _write_wav(tmp_path / "clip.wav", duration_s=1.0)
        source = """
        aud clip = 'clip.wav'
        setMaxAudioPlayers(1)
        int first = clip.playLoop()
        clip.stop()
        int second = clip.play()
        log(first == second)
        """
        result = compile_and_run(source, env=audio_null_env)
        assert result.stdout.strip() == "true"

    def test_a_play_that_finds_no_channel_returns_minus_one(
            self, compile_and_run, tmp_path, audio_null_env):
        # -1 means "nothing was played", and there is exactly one way to
        # reach it from Festina: every channel in the table reserved by
        # playLoop, with none left to claim. (A clip that failed to LOAD
        # cannot reach it -- festina_load_audio fails the program at the
        # declaration, long before any play() runs.) Returning a channel
        # number here would name one this call never used.
        _write_wav(tmp_path / "clip.wav", duration_s=1.0)
        source = """
        aud clip = 'clip.wav'
        setMaxAudioPlayers(64)
        for int i = 0, i < 64, i++ {
            clip.playLoop(i)
        }
        log(clip.play())
        """
        result = compile_and_run(source, env=audio_null_env)
        assert result.returncode == 0
        assert result.stdout.strip() == "-1"


class TestJsonRendering:
    '''claude.md #114: any non-text value in log() or `${}` compiles as
    its .toText(). int/float/bool keep their stringifiers; struct/table
    row/arr/map render JSON-like through generated per-type functions;
    blob/img/aud are compile errors, since a blob's bytes may be binary
    (its explicit .toText() exists for when that is wanted) and img/aud
    have no text form at all.'''

    def test_a_struct_renders_as_json(self, compile_and_run):
        source = '''
        struct Inner { n:int  tag:text }
        struct P { id:int  ok:bool  name:text  inner:Inner  xs:arr[int] }
        P p
        p.id = 7
        p.ok = true
        p.name = 'x'
        p.inner.n = 3
        p.inner.tag = 'in'
        p.xs.push(1)
        p.xs.push(2)
        log(p)
        '''
        result = compile_and_run(source)
        assert result.stdout.strip() == (
            '{"id":7,"ok":true,"name":"x",'
            '"inner":{"n":3,"tag":"in"},"xs":[1,2]}')

    def test_arrays_and_maps_render_as_json(self, compile_and_run):
        source = '''
        arr[int] nums = [1, 2, 3]
        log(nums)
        arr[text] words = ['a', null, 'c']
        log(words)
        map[int] m = {'one': 1}
        log(m)
        log(`inline: ${nums}`)
        '''
        result = compile_and_run(source)
        assert result.stdout.splitlines() == [
            '[1,2,3]', '["a",null,"c"]', '{"one":1}', 'inline: [1,2,3]']

    def test_text_is_escaped_and_assigned_null_renders_null(self, compile_and_run):
        # The name contains a real double quote and a real tab, which
        # must come out as \" and \t. An UNASSIGNED scalar field renders
        # its zero (claude.md #97: calloc'd storage reads 0, not the
        # null sentinel); a field explicitly made null renders null.
        source = r'''
        struct P { name:text  score:float  missing:float }
        P p
        p.name = 'has "quotes" and	tab'
        p.score = 1.0 / 0.0
        log(p)
        '''
        result = compile_and_run(source)
        assert result.stdout.strip() == (
            '{"name":"has \\"quotes\\" and\\ttab","score":null,"missing":0}')

    def test_a_table_row_renders_with_database_null_and_omits_undefined(
            self, compile_and_run, tmp_path):
        # A database NULL is the JSON null; a column the query never
        # selected is OMITTED, exactly what JSON.stringify does for an
        # undefined property -- the analogy claude.md #111 built.
        db = tmp_path / 't.sqlite'
        source = f'''
        DatabaseURL = '{db}'
        table T {{ id:int  name:text  ok:bool }}
        sqlite('INSERT INTO T (id, name, ok) VALUES (?, ?, ?)', [1, null, 1])
        arr[T] rows = sqlite('SELECT * FROM T')
        log(rows[0])
        arr[T] partial = sqlite('SELECT id FROM T')
        log(partial[0])
        log(rows)
        '''
        result = compile_and_run(source)
        assert result.stdout.splitlines() == [
            '{"id":1,"name":null,"ok":true}',
            '{"id":1}',
            '[{"id":1,"name":null,"ok":true}]']

    def test_explicit_to_text_is_the_same_rendering(self, compile_and_run):
        source = '''
        arr[int] nums = [1, 2]
        text json = nums.toText()
        log(json)
        struct P { n:int }
        P p
        p.n = 5
        log(p.toText())
        '''
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ['[1,2]', '{"n":5}']

    def test_a_null_container_renders_as_null(self, compile_and_run):
        source = '''
        struct P { n:int }
        P nothing = null
        log(nothing)
        log(`v: ${nothing}`)
        '''
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ['null', 'v: null']

    def test_a_cycle_truncates_instead_of_crashing(self, compile_and_run):
        # claude.md #106 made cycles constructible; a debug rendering
        # that overflowed the stack on one would crash the program it
        # exists to debug. Depth caps at 32, rendering null beyond.
        source = '''
        struct Node { n:int  next:Node }
        Node a
        a.n = 1
        a.next = a
        log(a)
        '''
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip().endswith('null}' + '}' * 32)

    def test_an_opaque_handle_in_a_struct_renders_as_a_placeholder(
            self, compile_and_run, tmp_path):
        path = tmp_path / 'f.txt'
        source = f'''
        struct Holder {{ name:text  data:blob }}
        Holder empty
        empty.name = 'no file'
        log(empty)
        Holder full
        full.name = 'with file'
        full.data = '{path}'
        log(full)
        '''
        result = compile_and_run(source)
        assert result.stdout.splitlines() == [
            '{"name":"no file","data":null}',
            '{"name":"with file","data":"<blob>"}']

    @pytest.mark.parametrize('decl,use', [
        ("img v = 'x.png'", 'log(v)'),
        ("img v = 'x.png'", 'log(`${v}`)'),
        ("aud v = 'x.wav'", 'log(v)'),
        ("aud v = 'x.wav'", 'log(`${v}`)'),
    ])
    def test_img_and_aud_in_log_or_template_are_compile_errors(
            self, parser, semantic, codegen, decl, use):
        # claude.md #115: blob renders (it has a text form); img and aud
        # do not, so they refuse.
        program = parser.parse(f'{decl}\n{use}', filename='main.f')
        analyzed = semantic.analyze(program, filename='main.f')
        with pytest.raises(Exception, match=r'text form'):
            codegen.generate_ir(program, analyzed, filename='main.f')


class TestStatementCache:
    """claude.md #113: literal SQL is prepared once per call site and
    reset+reused ever after -- the sqlite counterpart of claude.md #85's
    regex literal cache, driven by the same compile-time fact (the text
    cannot change). Dynamic SQL keeps the per-call prepare. Measured:
    20,000 one-row SELECTs 164ms -> 55ms; with WAL, 20,000 INSERTs
    16.7s -> 0.3s."""

    def test_a_literal_site_reuses_its_statement_across_params(
            self, compile_and_run, tmp_path):
        # Same call site, different bound parameters each iteration --
        # the reset+rebind path, which is where a caching bug would
        # show as stale results.
        db = tmp_path / "t.sqlite"
        source = f"""
        DatabaseURL = '{db}'
        table T {{ id:int  v:int }}
        for int i = 0, i < 5, i++ {{
            sqlite('INSERT INTO T (id, v) VALUES (?, ?)', [i, i * 10])
        }}
        int total = 0
        for int i = 0, i < 5, i++ {{
            arr[T] rows = sqlite('SELECT * FROM T WHERE id = ?', [i])
            total = total + rows[0].v
        }}
        log(total)
        log(sqliteInt('SELECT count(*) FROM T'))
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["100", "5"]

    def test_literal_sql_gets_a_cache_slot_and_dynamic_sql_does_not(
            self, parser, semantic, codegen):
        source = """
        table T { id:int }
        sqlite('INSERT INTO T (id) VALUES (1)')
        text tbl = 'T'
        sqlite(`DELETE FROM ${tbl}`)
        """
        program = parser.parse(source, filename="main.f")
        analyzed = semantic.analyze(program, filename="main.f")
        ir = codegen.generate_ir(program, analyzed, filename="main.f")
        # One literal site -> one slot; the template site stays on the
        # per-call prepare, because its SQL can differ every evaluation.
        assert ir.count("@__festina_stmtcache_") >= 2  # global + use
        assert ir.count("call ptr @festina_sqlite_prepare_cached(") == 1
        assert ir.count("call ptr @festina_sqlite_prepare(") == 1

    def test_two_identical_literals_get_independent_slots(
            self, compile_and_run, tmp_path):
        # Per-SITE, not per-text: two textually identical queries are
        # two statements, so one being mid-collection can never disturb
        # the other. Cheap insurance rather than a measured need.
        db = tmp_path / "t.sqlite"
        source = f"""
        DatabaseURL = '{db}'
        table T {{ id:int }}
        sqlite('INSERT INTO T (id) VALUES (1)')
        arr[T] a = sqlite('SELECT * FROM T')
        arr[T] b = sqlite('SELECT * FROM T')
        log(a[0].id + b[0].id)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "2"


class TestStructQueryTargets:
    """claude.md #112: `arr[SomeStruct] q = sqlite(...)` -- a struct as
    the query's landing spot. A table's declared columns can never chase
    a query's aliases, so `SELECT id AS whatever`, a JOIN, or a computed
    column had nowhere typed to land; a struct names its fields after
    the result's own column names, and -- unlike declaring a table --
    carries no CREATE TABLE side effect. Collection shares claude.md
    #111's name matching; each row then becomes a real refcounted
    struct, indistinguishable from one built by hand."""

    def test_the_spec_example(self, compile_and_run, tmp_path):
        db = tmp_path / "t.sqlite"
        source = f"""
        DatabaseURL = '{db}'
        table examples {{ id:int  name:text }}
        sqlite('INSERT INTO examples (id, name) VALUES (?, ?)', [7, 'seven'])
        struct data {{
          whatever:int
        }}
        arr[data] query = sqlite('select id as whatever from examples')
        log(query.length)
        log(query[0].whatever)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["1", "7"]

    def test_computed_columns_land_by_alias(self, compile_and_run, tmp_path):
        # The case a table can never express: the column does not exist
        # anywhere in the schema, only in the query.
        db = tmp_path / "t.sqlite"
        source = f"""
        DatabaseURL = '{db}'
        table examples {{ id:int  name:text }}
        sqlite('INSERT INTO examples (id, name) VALUES (?, ?)', [7, 'seven'])
        sqlite('INSERT INTO examples (id, name) VALUES (?, ?)', [8, 'eight'])
        struct summary {{ total:int  biggest:text }}
        arr[summary] agg = sqlite(
            "select count(*) as total, max(name) as biggest from examples")
        log(agg[0].total)
        log(agg[0].biggest)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["2", "seven"]

    def test_an_unmatched_field_reads_null(self, compile_and_run, tmp_path):
        db = tmp_path / "t.sqlite"
        source = f"""
        DatabaseURL = '{db}'
        table examples {{ id:int }}
        sqlite('INSERT INTO examples (id) VALUES (1)')
        struct wide {{ whatever:int  missing:text }}
        arr[wide] w = sqlite('select id as whatever from examples')
        log(w[0].missing == null)
        log(w[0].whatever)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["true", "1"]

    def test_the_result_is_an_ordinary_struct(self, compile_and_run, tmp_path):
        # Refcounted like any other: an element aliased out of the array
        # survives freeing the array, fields can be reassigned and
        # deleted, and the whole thing can be freed by hand.
        db = tmp_path / "t.sqlite"
        source = f"""
        DatabaseURL = '{db}'
        table examples {{ id:int  name:text }}
        sqlite('INSERT INTO examples (id, name) VALUES (?, ?)', [7, 'seven'])
        struct data {{ whatever:int  label:text }}
        arr[data] q = sqlite('select id as whatever, name as label from examples')
        data keep = q[0]
        free q
        log(q == null)
        log(keep.label)
        keep.label = 'renamed'
        log(keep.label)
        delete keep.label
        log(keep.label == null)
        free keep
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == [
            "true", "seven", "renamed", "true"]

    def test_a_blob_column_lands_in_a_struct_field(self, compile_and_run, tmp_path):
        db = tmp_path / "t.sqlite"
        src = tmp_path / "payload.txt"
        source = f"""
        DatabaseURL = '{db}'
        table examples {{ id:int  pic:blob }}
        blob payload = '{src}'
        payload.write('the bytes')
        sqlite('INSERT INTO examples (id, pic) VALUES (?, ?)', [1, payload])
        struct data {{ pic:blob }}
        arr[data] q = sqlite('select pic from examples')
        log(q[0].pic.toText())
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "the bytes"

    def test_a_non_queryable_field_is_a_clear_error(self, parser, semantic, codegen):
        source = (
            "struct bad { xs:arr[int] }\n"
            "arr[bad] q = sqlite('select 1')\n"
        )
        program = parser.parse(source, filename="main.f")
        analyzed = semantic.analyze(program, filename="main.f")
        with pytest.raises(Exception, match="xs"):
            codegen.generate_ir(program, analyzed, filename="main.f")

    def test_undefined_stays_a_table_row_method(self, parser, semantic, errors):
        # A struct instance from a query is an ordinary struct -- the
        # presence mask is a row concept, deliberately dropped in the
        # conversion, so undefined() is not offered.
        program = parser.parse(
            "struct data { whatever:int }\n"
            "arr[data] q = sqlite('select 1 as whatever')\n"
            "log(q[0].undefined('whatever'))")
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)


class TestFreeStatement:
    """claude.md #111: `free name` -- release what the binding holds,
    null the binding. For refcounted types it is a DECREMENT, so a value
    something else still references survives; for img/aud it is the
    manual escape hatch for the escaping-handle leak; for value types it
    degenerates to `x = null`. Composable with automatic reclamation
    because every release in the runtime is null-safe and a free target
    counts as escaping (see escape_analysis's own comment)."""

    def test_freeing_nulls_the_binding(self, compile_and_run, tmp_path):
        path = tmp_path / "b.txt"
        source = f"""
        blob b = '{path}'
        free b
        log(b == null)
        int n = 5
        free n
        log(n == null)
        text t = 'x'
        free t
        log(t == null)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["true", "true", "true"]

    def test_freeing_twice_is_a_no_op(self, compile_and_run):
        source = """
        struct P { n:int }
        P p
        p.n = 1
        free p
        free p
        log('survived')
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "survived"

    def test_a_shared_value_survives_freeing_one_reference(self, compile_and_run):
        # The user-facing half of "free is a decrement": the array is
        # freed, the element it shared with `keep` is not. Verified
        # leak-free AND corruption-free under ASan separately; this pins
        # the visible behavior.
        source = """
        struct P { n:int  label:text }
        P keep
        keep.n = 7
        keep.label = 'held'
        arr[P] xs = [keep, keep]
        free xs
        log(xs == null)
        log(keep.label)
        free keep
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["true", "held"]

    def test_freeing_a_clip_source_leaves_its_clips_alone(
            self, compile_and_run, tmp_path, sprite_sheet_png):
        # The motivating example: clips carry their own surfaces, so the
        # sheet can go the moment the clips are cut.
        source = f"""
        img spritesheet = '{sprite_sheet_png}'
        img grass = spritesheet.clip(0, 0, 31, 31)
        img dirt = spritesheet.clip(32, 0, 31, 31)
        free spritesheet
        log(spritesheet == null)
        log(grass.width)
        log(dirt.width)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["true", "31", "31"]

    def test_freeing_a_regex_literal_binding_keeps_the_cache_intact(
            self, compile_and_run):
        # A /pattern/ literal is compiled once and cached for the whole
        # process (claude.md #85); the value carries a `cached` mark and
        # festina_regex_free no-ops on it, so free is safe on a regex
        # binding whichever way it was produced.
        source = """
        for int i = 0, i < 3, i++ {
            regex r = /a+/i
            log(r.test('AAA'))
            free r
        }
        regex dyn = regex('[0-9]+')
        free dyn
        log(dyn == null)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["true", "true", "true", "true"]

    def test_freeing_a_query_row_drops_the_binding_without_freeing(
            self, compile_and_run, tmp_path):
        # A row is owned by the array it came from -- freeing it here
        # would double-free at the array's own release, so `free row`
        # only nulls the binding. Freeing the ARRAY is the real release.
        db = tmp_path / "t.sqlite"
        source = f"""
        DatabaseURL = '{db}'
        table T {{ id:int }}
        sqlite('INSERT INTO T (id) VALUES (1)')
        arr[T] rows = sqlite('SELECT * FROM T')
        T first = rows[0]
        free first
        log(first == null)
        log(rows[0].id)
        free rows
        log(rows == null)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["true", "1", "true"]

    def test_free_on_an_unknown_name_is_a_compile_error(self, parser, semantic, errors):
        program = parser.parse("free nothing")
        with pytest.raises(errors.CompileError, match="unknown variable"):
            semantic.analyze(program)

    def test_free_on_a_constant_is_a_compile_error(self, parser, semantic, errors):
        program = parser.parse("const int N = 5\nfree N")
        with pytest.raises(errors.CompileError, match="constant"):
            semantic.analyze(program)

    def test_free_on_a_parameter_is_a_compile_error(self, parser, semantic, errors):
        # A parameter borrows its caller's value (claude.md #84).
        program = parser.parse(
            "void func f(t:text) {\n    free t\n}\nf('x')")
        with pytest.raises(errors.CompileError, match="parameter"):
            semantic.analyze(program)


class TestDeleteStatement:
    """claude.md #111: `delete`, JS-shaped. A map entry stops existing;
    a struct field reads null afterwards; a query-row field reads null
    AND its presence bit clears, so undefined() reports it like a column
    the query never selected."""

    def test_both_map_forms_from_the_spec_example(self, compile_and_run):
        source = """
        map[text] example = {'data': 'some data', 'more-data': 'Some more data'}
        delete example.data
        delete example['more-data']
        log(example['data'] == null)
        log(example['more-data'] == null)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["true", "true"]

    def test_a_deleted_key_stops_existing_for_for_each(self, compile_and_run):
        # Removal, not set-to-null: forEach never visits the deleted key,
        # which null could not express.
        source = """
        void func show(v:int, k:text) {
            log(`${k}=${v}`)
        }
        map[int] m = {'a': 1, 'b': 2, 'c': 3}
        delete m.b
        m.forEach(show)
        """
        result = compile_and_run(source)
        assert sorted(result.stdout.splitlines()) == ["a=1", "c=3"]

    def test_deleting_a_missing_key_is_a_no_op(self, compile_and_run):
        source = """
        map[int] m = {'a': 1}
        delete m.nothing
        delete m['also-nothing']
        log(m['a'])
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "1"

    def test_a_computed_key_expression_works(self, compile_and_run):
        source = """
        map[int] m = {'k1': 10, 'k2': 20}
        int i = 1
        delete m[`k${i}`]
        log(m['k1'] == null)
        log(m['k2'])
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["true", "20"]

    def test_a_deleted_struct_field_reads_null(self, compile_and_run):
        source = """
        struct P { n:int  label:text }
        P p
        p.n = 5
        p.label = 'x'
        delete p.label
        delete p.n
        log(p.label == null)
        log(p.n == null)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["true", "true"]

    def test_delete_on_a_non_container_is_a_compile_error(self, parser, semantic, errors):
        program = parser.parse("int x = 5\nint y = 1\ndelete y.something")
        with pytest.raises(errors.CompileError, match="delete"):
            semantic.analyze(program)

    def test_delete_of_a_whole_variable_says_to_use_free(self, parser, errors):
        with pytest.raises(errors.CompileError, match="free"):
            parser.parse("int x = 5\ndelete x")

    def test_deleting_an_unknown_struct_field_is_a_compile_error(
            self, parser, semantic, errors):
        program = parser.parse("struct P { n:int }\nP p\ndelete p.missing")
        with pytest.raises(errors.CompileError, match="missing"):
            semantic.analyze(program)

    def test_blob_delete_method_still_works(self, compile_and_run, tmp_path):
        # `delete` became a keyword; member names accept keywords
        # (parser.eat_name), so blob's method is untouched.
        path = tmp_path / "f.txt"
        source = f"""
        blob f = '{path}'
        f.write('x')
        log(f.delete())
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "true"


class TestUndefinedAndNameMatchedColumns:
    """claude.md #111: result columns match declared columns by NAME
    (positional matching silently misaligned every partial or reordered
    SELECT), and each row records which declared columns the result set
    actually contained -- read by row.undefined('col'), which is the
    difference between "the database said NULL" and "the query never
    asked"."""

    def test_the_spec_example(self, compile_and_run, tmp_path):
        db = tmp_path / "t.sqlite"
        source = f"""
        DatabaseURL = '{db}'
        table examples {{ id:int  name:text }}
        sqlite('INSERT INTO examples (id, name) VALUES (?, ?)', [1, null])
        arr[examples] data = sqlite('select id from examples')
        if data[0] != null {{
          log('should log')
          if data[0].name == null && data[0].undefined('name') {{
            log('should also log')
          }}
        }}
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["should log", "should also log"]

    def test_a_genuine_database_null_is_not_undefined(self, compile_and_run, tmp_path):
        # The whole point of the distinction: NULL that came from the
        # database is a VALUE, and undefined() says so.
        db = tmp_path / "t.sqlite"
        source = f"""
        DatabaseURL = '{db}'
        table examples {{ id:int  name:text }}
        sqlite('INSERT INTO examples (id, name) VALUES (?, ?)', [1, null])
        arr[examples] data = sqlite('select * from examples')
        log(data[0].name == null)
        log(data[0].undefined('name'))
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["true", "false"]

    def test_a_deleted_row_column_becomes_undefined(self, compile_and_run, tmp_path):
        db = tmp_path / "t.sqlite"
        source = f"""
        DatabaseURL = '{db}'
        table examples {{ id:int  name:text }}
        sqlite('INSERT INTO examples (id, name) VALUES (?, ?)', [1, 'x'])
        arr[examples] data = sqlite('select * from examples')
        log(data[0].undefined('name'))
        delete data[0].name
        log(data[0].name == null)
        log(data[0].undefined('name'))
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["false", "true", "true"]

    def test_a_reordered_select_lands_by_name(self, compile_and_run, tmp_path):
        # The bug the name matching fixes: this used to read name's text
        # into the id slot as an integer.
        db = tmp_path / "t.sqlite"
        source = f"""
        DatabaseURL = '{db}'
        table T {{ id:int  name:text  score:float }}
        sqlite('INSERT INTO T (id, name, score) VALUES (?, ?, ?)', [7, 'seven', 1.5])
        arr[T] rows = sqlite('select score, name from T')
        log(rows[0].name)
        log(rows[0].score)
        log(rows[0].id == null)
        log(rows[0].undefined('id'))
        log(rows[0].undefined('score'))
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == [
            "seven", "1.5", "true", "true", "false"]

    def test_an_unknown_column_name_fails_clearly(self, compile_and_run, tmp_path):
        # Asking about a column the table does not have is a typo, and
        # true or false would both bury it.
        db = tmp_path / "t.sqlite"
        source = f"""
        DatabaseURL = '{db}'
        table T {{ id:int }}
        sqlite('INSERT INTO T (id) VALUES (1)')
        arr[T] rows = sqlite('SELECT * FROM T')
        log(rows[0].undefined('nmae'))
        """
        result = compile_and_run(source)
        assert result.returncode != 0
        assert "no column by that name" in result.stderr

    def test_undefined_requires_one_text_argument(self, parser, semantic, errors):
        program = parser.parse(
            "table T { id:int }\n"
            "arr[T] rows = sqlite('SELECT * FROM T')\n"
            "log(rows[0].undefined(5))")
        with pytest.raises(errors.CompileError, match="must be text"):
            semantic.analyze(program)


class TestSaveAndSaveCopy:
    """claude.md #110: save()/saveCopy() on blob, img and aud.

    One policy for all three, because all three are the same shape of
    value (claude.md #101/#109: content plus the bytes it came from).
    save() writes to the path the handle already has; save(path) adopts
    that path first; saveCopy(path) writes elsewhere and leaves the
    handle's own path alone.

    This is what closes the gap claude.md #109 shipped knowingly: a
    handle with no path -- an img from clip(), anything out of a
    database column -- could not reach the disk at all.
    """

    # ---- blob ----

    def test_blob_save_writes_to_its_own_path(self, compile_and_run, tmp_path):
        path = tmp_path / "notes.txt"
        source = f"""
        blob f = '{path}'
        f.write('written')
        f.delete()
        log(f.exists())
        log(f.save())
        log(f.exists())
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["false", "true", "true"]
        assert path.read_text() == "written"

    def test_blob_save_with_a_path_adopts_it(self, compile_and_run, tmp_path):
        first = tmp_path / "one.txt"
        second = tmp_path / "two.txt"
        source = f"""
        blob f = '{first}'
        f.write('content')
        log(f.save('{second}'))
        // the path CHANGED, so everything else follows it
        log(f.delete())
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["true", "true"]
        assert second.exists() is False, "delete() should have followed the new path"
        assert first.read_text() == "content", "the original must be untouched"

    def test_blob_save_copy_leaves_the_path_alone(self, compile_and_run, tmp_path):
        first = tmp_path / "one.txt"
        copy = tmp_path / "copy.txt"
        source = f"""
        blob f = '{first}'
        f.write('original')
        log(f.saveCopy('{copy}'))
        f.write('changed after the copy')
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "true"
        # The later write went to the ORIGINAL path, not the copy --
        # which is the whole difference between save and saveCopy.
        assert first.read_text() == "changed after the copy"
        assert copy.read_text() == "original"

    def test_a_pathless_blob_save_fails_and_says_why(self, compile_and_run, tmp_path):
        # claude.md #110: a program asking to save something to nowhere
        # has a bug, so this fails rather than answering false. An
        # unwritable directory is a condition of the filesystem and
        # still answers false -- see the test below.
        db = tmp_path / "t.sqlite"
        src = tmp_path / "payload.txt"
        source = f"""
        DatabaseURL = '{db}'
        table Saves {{ name:text  data:blob }}
        blob f = '{src}'
        f.write('stored')
        sqlite('INSERT INTO Saves (name, data) VALUES (?, ?)', ['s', f])
        arr[Saves] rows = sqlite('SELECT * FROM Saves')
        log(rows[0].data.save())
        """
        result = compile_and_run(source)
        assert result.returncode != 0
        assert "no path to save() to" in result.stderr
        assert "blob" in result.stderr

    def test_saving_somewhere_impossible_returns_false(self, compile_and_run, tmp_path):
        path = tmp_path / "notes.txt"
        source = f"""
        blob f = '{path}'
        f.write('x')
        log(f.saveCopy('/definitely/not/a/directory/x.txt'))
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "false"

    def test_a_failed_save_does_not_adopt_the_path(self, compile_and_run, tmp_path):
        # Adopting a path the write never reached would leave exists()
        # answering false about a path the program was just told it has.
        path = tmp_path / "notes.txt"
        source = f"""
        blob f = '{path}'
        f.write('x')
        log(f.save('/definitely/not/a/directory/x.txt'))
        log(f.exists())
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["false", "true"]

    def test_a_database_blob_reaches_the_disk_byte_identically(
            self, compile_and_run, tmp_path, sprite_sheet_png):
        # The gap claude.md #109 recorded as open, closed. A blob out of
        # a column has bytes and no path; save(path) gives it one.
        db = tmp_path / "t.sqlite"
        out = tmp_path / "recovered.png"
        source = f"""
        DatabaseURL = '{db}'
        table Assets {{ name:text  data:blob }}
        blob png = '{sprite_sheet_png}'
        sqlite('INSERT INTO Assets (name, data) VALUES (?, ?)', ['tiles', png])
        arr[Assets] rows = sqlite('SELECT * FROM Assets')
        blob back = rows[0].data
        log(back.exists())
        log(back.save('{out}'))
        log(back.exists())
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["false", "true", "true"]
        assert out.read_bytes() == open(sprite_sheet_png, "rb").read()

    # ---- img ----

    def test_a_clipped_image_can_be_saved_to_a_path(
            self, compile_and_run, tmp_path, sprite_sheet_png):
        # claude.md #110's motivating case, and #109's other open gap: a
        # clip() result has never been on disk, so save(path) is the only
        # way it ever gets there.
        out = tmp_path / "grass.png"
        source = f"""
        img spritesheet = '{sprite_sheet_png}'
        img grass = spritesheet.clip(0, 0, 32, 32)
        log(grass.save('{out}'))
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "true"
        data = out.read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n", "a clip encodes as PNG"
        width, height = struct.unpack(">II", data[16:24])
        assert (width, height) == (32, 32)

    def test_saving_a_clip_then_saving_again_with_no_argument(
            self, compile_and_run, tmp_path, sprite_sheet_png):
        # save(path) adopted the path, so the bare save() now works on a
        # value that had none a moment ago.
        out = tmp_path / "grass.png"
        source = f"""
        img spritesheet = '{sprite_sheet_png}'
        img grass = spritesheet.clip(0, 0, 32, 32)
        log(grass.save('{out}'))
        log(grass.save())
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["true", "true"]

    def test_a_pathless_image_save_fails_and_names_img(
            self, compile_and_run, tmp_path, sprite_sheet_png):
        source = f"""
        img spritesheet = '{sprite_sheet_png}'
        img grass = spritesheet.clip(0, 0, 32, 32)
        log(grass.save())
        """
        result = compile_and_run(source)
        assert result.returncode != 0
        assert "this img has no path to save() to" in result.stderr

    def test_an_image_save_copy_preserves_its_source_bytes(
            self, compile_and_run, tmp_path, sprite_sheet_png):
        # claude.md #101 keeps the bytes an image was loaded from, so a
        # copy is byte-identical rather than re-encoded.
        out = tmp_path / "copy.png"
        source = f"""
        img sheet = '{sprite_sheet_png}'
        log(sheet.saveCopy('{out}'))
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "true"
        assert out.read_bytes() == open(sprite_sheet_png, "rb").read()

    def test_a_jpeg_stays_a_jpeg(self, compile_and_run, tmp_path):
        src = os.path.join(_FIXTURES_DIR, "gradient.jpg")
        out = tmp_path / "copy.jpg"
        source = f"""
        img grad = '{src}'
        log(grad.saveCopy('{out}'))
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "true"
        assert out.read_bytes() == open(src, "rb").read()

    # ---- aud ----

    def test_an_mp3_stays_an_mp3(self, compile_and_run, tmp_path):
        # Not re-encoded as WAV, for the same reason claude.md #101's
        # aud columns round-trip: the clip keeps its own encoded bytes.
        src = os.path.join(_FIXTURES_DIR, "tone.mp3")
        out = tmp_path / "copy.mp3"
        source = f"""
        aud tone = '{src}'
        log(tone.saveCopy('{out}'))
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "true"
        assert out.read_bytes() == open(src, "rb").read()

    def test_an_audio_save_with_a_path_adopts_it(self, compile_and_run, tmp_path):
        src = os.path.join(_FIXTURES_DIR, "beep.wav")
        first = tmp_path / "a.wav"
        second = tmp_path / "b.wav"
        source = f"""
        aud clip = '{src}'
        log(clip.save('{first}'))
        log(clip.save('{second}'))
        log(clip.save())
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["true", "true", "true"]
        original = open(src, "rb").read()
        assert first.read_bytes() == original
        assert second.read_bytes() == original

    def test_a_resized_image_keeps_its_path_and_saves_at_the_new_size(
            self, compile_and_run, tmp_path, sprite_sheet_png):
        # resize() mutates in place (claude.md #92), so the path survives
        # it -- save() with no argument overwrites the file the image came
        # from, with the resized version. That follows from what save()
        # means rather than being a special case, and is worth pinning
        # because it is destructive.
        src = tmp_path / "sheet.png"
        src.write_bytes(open(sprite_sheet_png, "rb").read())
        source = f"""
        img sheet = '{src}'
        sheet.resize(16, 16)
        log(sheet.save())
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "true"
        data = src.read_bytes()
        width, height = struct.unpack(">II", data[16:24])
        assert (width, height) == (16, 16)

    def test_an_audio_column_reaches_the_disk_byte_identically(
            self, compile_and_run, tmp_path):
        src = os.path.join(_FIXTURES_DIR, "tone.mp3")
        db = tmp_path / "t.sqlite"
        out = tmp_path / "recovered.mp3"
        source = f"""
        DatabaseURL = '{db}'
        table Tracks {{ name:text  clip:aud }}
        aud tone = '{src}'
        sqlite('INSERT INTO Tracks (name, clip) VALUES (?, ?)', ['tone', tone])
        arr[Tracks] rows = sqlite('SELECT * FROM Tracks')
        log(rows[0].clip.save('{out}'))
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "true"
        assert out.read_bytes() == open(src, "rb").read()

    # ---- the surface itself ----

    @pytest.mark.parametrize("decl", [
        "blob v = 'x'",
        "img v = 'x.png'",
        "aud v = 'x.wav'",
    ])
    def test_save_copy_requires_its_path(self, parser, semantic, errors, decl):
        program = parser.parse(f"{decl}\nlog(v.saveCopy())")
        with pytest.raises(errors.CompileError, match="saveCopy"):
            semantic.analyze(program)

    @pytest.mark.parametrize("decl", [
        "blob v = 'x'",
        "img v = 'x.png'",
        "aud v = 'x.wav'",
    ])
    def test_save_takes_at_most_one_path(self, parser, semantic, errors, decl):
        program = parser.parse(f"{decl}\nlog(v.save('a', 'b'))")
        with pytest.raises(errors.CompileError, match="save"):
            semantic.analyze(program)

    @pytest.mark.parametrize("decl", [
        "blob v = 'x'",
        "img v = 'x.png'",
        "aud v = 'x.wav'",
    ])
    def test_the_path_must_be_text(self, parser, semantic, errors, decl):
        program = parser.parse(f"{decl}\nlog(v.save(5))")
        with pytest.raises(errors.CompileError, match="must be text"):
            semantic.analyze(program)

    @pytest.mark.parametrize("decl,method", [
        ("blob v = 'x'", "save"),
        ("img v = 'x.png'", "saveCopy"),
        ("aud v = 'x.wav'", "save"),
    ])
    def test_both_return_bool(self, parser, semantic, decl, method):
        arg = "'p'" if method == "saveCopy" else ""
        program = parser.parse(f"{decl}\nbool ok = v.{method}({arg})")
        semantic.analyze(program)

    def test_saving_is_not_offered_on_a_type_that_has_no_bytes(
            self, parser, semantic, errors):
        # text has no path and no bytes-plus-origin shape, so this is an
        # ordinary unknown-method error rather than a save it cannot do.
        program = parser.parse("text t = 'x'\nlog(t.save('p'))")
        with pytest.raises(errors.CompileError):
            semantic.analyze(program)


class TestBlobColumns:
    """claude.md #109: a blob column stores the file's BYTES, so it
    round-trips. Same treatment claude.md #101 gave aud/img columns, and
    for the same reason -- all three are content plus the bytes it came
    from."""

    def test_a_blob_column_round_trips_its_contents(
            self, compile_and_run, tmp_path):
        db = tmp_path / "t.sqlite"
        src = tmp_path / "payload.txt"
        source = f"""
        DatabaseURL = '{db}'
        table Saves {{ name:text  data:blob }}
        blob f = '{src}'
        f.write('the payload')
        sqlite('INSERT INTO Saves (name, data) VALUES (?, ?)', ['slot1', f])
        arr[Saves] rows = sqlite('SELECT * FROM Saves')
        log(rows.length)
        log(rows[0].name)
        log(rows[0].data.toText())
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["1", "slot1", "the payload"]

    def test_binary_content_survives_the_round_trip_intact(
            self, compile_and_run, tmp_path, sprite_sheet_png):
        # The case a text column could never handle: a PNG is full of
        # NUL bytes, and reading the column as a C string would stop at
        # the first one. Checked by length, since a truncation is
        # exactly what a length comparison catches.
        db = tmp_path / "t.sqlite"
        out = tmp_path / "back.png"
        source = f"""
        DatabaseURL = '{db}'
        table Assets {{ name:text  data:blob }}
        blob png = '{sprite_sheet_png}'
        sqlite('INSERT INTO Assets (name, data) VALUES (?, ?)', ['tiles', png])
        arr[Assets] rows = sqlite('SELECT * FROM Assets')
        blob back = rows[0].data
        blob out = '{out}'
        log(back.toText() != '')
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        # The real proof is byte-level and lives outside the language,
        # which has no way to compare binary buffers: read the stored
        # column back with Python and diff it against the source file.
        import sqlite3
        stored = sqlite3.connect(str(db)).execute(
            "select data from Assets").fetchone()[0]
        assert bytes(stored) == open(sprite_sheet_png, "rb").read()

    def test_a_column_blob_has_no_path(self, compile_and_run, tmp_path):
        # It has bytes and nowhere to put them: a path is meaningful
        # only on the machine that stored it, so the contents are what
        # round-trips. exists()/write()/delete() answer false rather
        # than inventing a temporary file.
        db = tmp_path / "t.sqlite"
        src = tmp_path / "payload.txt"
        source = f"""
        DatabaseURL = '{db}'
        table Saves {{ name:text  data:blob }}
        blob f = '{src}'
        f.write('stored')
        sqlite('INSERT INTO Saves (name, data) VALUES (?, ?)', ['s', f])
        arr[Saves] rows = sqlite('SELECT * FROM Saves')
        blob back = rows[0].data
        log(back.toText())
        log(back.exists())
        log(back.write('nope'))
        log(back.delete())
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["stored", "false", "false", "false"]

    def test_a_null_blob_column_reads_back_as_null(self, compile_and_run, tmp_path):
        db = tmp_path / "t.sqlite"
        source = f"""
        DatabaseURL = '{db}'
        table Saves {{ name:text  data:blob }}
        sqlite('INSERT INTO Saves (name) VALUES (?)', ['empty'])
        arr[Saves] rows = sqlite('SELECT * FROM Saves')
        log(rows[0].data == null)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "true"

    def test_the_column_type_is_a_sql_blob(self, sqlite_schema):
        assert sqlite_schema.TYPE_MAP["blob"] == "BLOB"


class TestMediaFormatsAndPaths:
    """claude.md #101: `img sprite = 'sprite.png'` alongside claude.md
    #100's `aud`, JPEG and MP3 decoding, and both types as sqlite BLOB
    columns.

    The format tests run against REAL files committed under
    tests/fixtures/ rather than anything generated here. Nothing in this
    repo can encode a JPEG or an MP3, so a hand-rolled approximation
    would only prove that a hand-rolled approximation decodes -- the
    point is that libjpeg and libmpg123 are genuinely wired up.
    """

    def test_a_path_declares_an_image(self, compile_and_run, tmp_path, monkeypatch,
                                       sprite_sheet_png):
        # The img counterpart of claude.md #100's aud form. Headless on
        # purpose: decoding needs no display, and the short form must not
        # quietly demand one where loadImage() never did.
        monkeypatch.delenv("DISPLAY", raising=False)
        name = os.path.basename(sprite_sheet_png)   # already written into tmp_path
        result = compile_and_run(
            f"img sheet = '{name}'\nlog(`${{sheet.width}}x${{sheet.height}}`)")
        assert result.returncode == 0
        assert result.stdout.strip() == "128x64"

    def test_an_image_path_may_be_a_computed_text_expression(self, compile_and_run, tmp_path,
                                                               monkeypatch, sprite_sheet_png):
        monkeypatch.delenv("DISPLAY", raising=False)
        stem = os.path.basename(sprite_sheet_png)[:-len(".png")]
        result = compile_and_run(
            f"text name = '{stem}'\nimg sheet = name + '.png'\nlog(sheet.width)")
        assert result.returncode == 0
        assert result.stdout.strip() == "128"

    def test_jpeg_decodes(self, compile_and_run, tmp_path, monkeypatch):
        monkeypatch.delenv("DISPLAY", raising=False)
        shutil.copy(_JPEG_FIXTURE, tmp_path / "gradient.jpg")
        result = compile_and_run(
            "img photo = 'gradient.jpg'\nlog(`${photo.width}x${photo.height}`)")
        assert result.returncode == 0
        assert result.stdout.strip() == "16x16"

    def test_jpeg_pixels_are_right_way_round(self, compile_and_run, tmp_path, monkeypatch):
        # The fixture is a gradient: red rises with x, green with y, blue
        # is flat at 128. That makes a channel swap (the classic mistake
        # converting libjpeg's RGB into Cairo's native-endian pixel)
        # impossible to miss, which "it decoded to 16x16" would not
        # catch. Tolerance is for JPEG being lossy, nothing else.
        monkeypatch.delenv("DISPLAY", raising=False)
        shutil.copy(_JPEG_FIXTURE, tmp_path / "gradient.jpg")
        result = compile_and_run(
            "img photo = 'gradient.jpg'\n"
            "drawImage(photo, 0, 0)\n"
            "log(saveCanvas('out.png'))\n"
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "true"
        _, _, pixel = _decode_png(str(tmp_path / "out.png"))
        for x, y, expected in [(0, 0, (0, 0, 128)), (15, 0, (240, 0, 128)),
                                (0, 15, (0, 240, 128)), (15, 15, (240, 240, 128))]:
            got = pixel(x, y)
            worst = max(abs(g - e) for g, e in zip(got, expected))
            assert worst <= 24, f"({x},{y}) expected ~{expected}, got {got}"

    def test_mp3_decodes_and_plays(self, compile_and_run, tmp_path, audio_null_env):
        shutil.copy(_MP3_FIXTURE, tmp_path / "tone.mp3")
        result = compile_and_run(
            "aud tone = 'tone.mp3'\n"
            "tone.play()\n"
            "log(tone.isPlaying())\n",
            env=audio_null_env,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "true"

    def test_format_is_sniffed_from_content_not_the_extension(self, compile_and_run, tmp_path,
                                                                monkeypatch):
        # A blob out of a database has no extension at all, so the
        # decoder never had any business trusting one. Same JPEG, named
        # ".png".
        monkeypatch.delenv("DISPLAY", raising=False)
        shutil.copy(_JPEG_FIXTURE, tmp_path / "lying.png")
        result = compile_and_run("img photo = 'lying.png'\nlog(photo.width)")
        assert result.returncode == 0
        assert result.stdout.strip() == "16"


class TestMediaColumnsInTables:
    """claude.md #101: `file:aud` / `pic:img` table columns, stored as
    SQLite BLOBs.

    The stored bytes are the asset's OWN encoded bytes, not a
    re-encoding, so a round trip is byte-identical and an MP3 stays an
    MP3 rather than becoming a much larger WAV. That is checked here by
    reading the database back with Python's own sqlite3 and comparing
    against the fixture file, which is the only way to prove it is a
    real BLOB rather than something that merely round-trips through
    Festina.
    """

    def _db(self, tmp_path):
        return sqlite3.connect(str(tmp_path / "festina.sqlite"))

    def test_the_column_type_is_blob(self, compile_and_run, tmp_path):
        source = """
        table Music {
            name:text
            file:aud
        }
        table Sprites {
            name:text
            pic:img
        }
        log('synced')
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        schema = dict(self._db(tmp_path).execute(
            "SELECT name, sql FROM sqlite_master WHERE name IN ('Music','Sprites')").fetchall())
        # TEXT (what these used to fall through to) would have silently
        # truncated at the first NUL byte in a PNG header.
        assert "file BLOB" in schema["Music"]
        assert "pic BLOB" in schema["Sprites"]

    def test_binding_an_aud_stores_its_own_bytes(self, compile_and_run, tmp_path, audio_null_env):
        shutil.copy(_MP3_FIXTURE, tmp_path / "tone.mp3")
        source = """
        table Music {
            name:text
            file:aud
        }
        aud track = 'tone.mp3'
        sqlite('INSERT INTO Music (name, file) VALUES (?, ?)', ['theme', track])
        log('inserted')
        """
        result = compile_and_run(source, env=audio_null_env)
        assert result.returncode == 0
        stored = self._db(tmp_path).execute("SELECT file FROM Music").fetchone()[0]
        assert isinstance(stored, bytes)
        assert stored == open(_MP3_FIXTURE, "rb").read()

    def test_binding_an_img_stores_its_own_bytes(self, compile_and_run, tmp_path, monkeypatch):
        monkeypatch.delenv("DISPLAY", raising=False)
        shutil.copy(_JPEG_FIXTURE, tmp_path / "gradient.jpg")
        source = """
        table Sprites {
            name:text
            pic:img
        }
        img hero = 'gradient.jpg'
        sqlite('INSERT INTO Sprites (name, pic) VALUES (?, ?)', ['hero', hero])
        log('inserted')
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        stored = self._db(tmp_path).execute("SELECT pic FROM Sprites").fetchone()[0]
        assert isinstance(stored, bytes)
        # Byte-identical: a JPEG stays a JPEG rather than being
        # re-encoded as PNG on the way in.
        assert stored == open(_JPEG_FIXTURE, "rb").read()

    def test_a_stored_clip_reads_back_as_a_playable_aud(self, compile_and_run, tmp_path,
                                                          audio_null_env):
        shutil.copy(_MP3_FIXTURE, tmp_path / "tone.mp3")
        source = """
        table Music {
            name:text
            file:aud
        }
        aud track = 'tone.mp3'
        sqlite('INSERT INTO Music (name, file) VALUES (?, ?)', ['theme', track])
        arr[Music] rows = sqlite('SELECT * FROM Music')
        log(rows[0].name)
        rows[0].file.play()
        log(rows[0].file.isPlaying())
        stopAudioPlayer()
        log(rows[0].file.isPlaying())
        """
        result = compile_and_run(source, env=audio_null_env)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["theme", "true", "false"]

    def test_a_stored_image_reads_back_with_its_real_size(self, compile_and_run, tmp_path,
                                                            monkeypatch):
        monkeypatch.delenv("DISPLAY", raising=False)
        shutil.copy(_JPEG_FIXTURE, tmp_path / "gradient.jpg")
        source = """
        table Sprites {
            name:text
            pic:img
        }
        img hero = 'gradient.jpg'
        sqlite('INSERT INTO Sprites (name, pic) VALUES (?, ?)', ['hero', hero])
        arr[Sprites] rows = sqlite('SELECT * FROM Sprites')
        log(`${rows[0].name} ${rows[0].pic.width}x${rows[0].pic.height}`)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "hero 16x16"

    def test_a_clipped_image_with_no_source_bytes_is_encoded_on_demand(
        self, compile_and_run, tmp_path, monkeypatch, sprite_sheet_png
    ):
        # A clip() result never came from a file, so it has no bytes to
        # store -- festina_image_bytes encodes PNG for it. The round trip
        # has to preserve the SIZE of the clipped tile, not the sheet.
        monkeypatch.delenv("DISPLAY", raising=False)
        sheet = os.path.basename(sprite_sheet_png)
        source = f"""
        table Sprites {{
            name:text
            pic:img
        }}
        img sheet = '{sheet}'
        img tile = sheet.clip(0, 0, 32, 32)
        sqlite('INSERT INTO Sprites (name, pic) VALUES (?, ?)', ['tile', tile])
        arr[Sprites] rows = sqlite('SELECT * FROM Sprites')
        log(`${{rows[0].pic.width}}x${{rows[0].pic.height}}`)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "32x32"
        stored = self._db(tmp_path).execute("SELECT pic FROM Sprites").fetchone()[0]
        assert stored[:8] == b"\x89PNG\r\n\x1a\n"


class TestAudioChannels:
    """claude.md #99: channels are named, and playLoop reserves one.

    The pool from claude.md #98 was per-`aud`, which cannot express the
    thing this section exists for -- two different clips sharing one
    channel:

        adventureMusic.playLoop(0)
        battleMusic.playLoop(0)     // takes channel 0 over

    With a per-clip pool those are two different pools and "channel 0"
    means two different things. So the pool became process-global and
    its slots became channels, which is also what lets
    stopAudioPlayer(0) be a plain free function rather than something
    that would have to name a clip to find the channel.
    """

    def _run(self, tmp_path):
        cc = shutil.which("clang") or shutil.which("gcc") or shutil.which("cc")
        if not cc:
            pytest.skip("no C compiler (clang/gcc/cc) on PATH")
        # claude.md #101: the audio unit needs libmpg123 too now.
        alsa = subprocess.run(["pkg-config", "--cflags", "--libs", "alsa", "libmpg123"],
                               capture_output=True, text=True)
        if alsa.returncode != 0:
            pytest.skip("alsa/libmpg123 dev headers are not installed")
        runtime_dir = os.path.join(os.path.dirname(_EXAMPLES_DIR), "runtime")
        harness = tmp_path / "channel_harness.c"
        harness.write_text(_CHANNEL_HARNESS)
        binary = tmp_path / "channel_harness"
        a = tmp_path / "adventure.wav"
        b = tmp_path / "battle.wav"
        # Short clips: the looping assertions below only mean something
        # if a non-looping clip would have finished long before them.
        _write_wav(a, duration_s=0.5)
        _write_wav(b, duration_s=0.5)
        build = subprocess.run(
            [cc, "-I", runtime_dir, str(harness), "-o", str(binary), "-pthread"]
            + alsa.stdout.split(),
            capture_output=True, text=True,
        )
        assert build.returncode == 0, build.stderr
        run = subprocess.run([str(binary), str(a), str(b)], capture_output=True,
                              text=True, timeout=180)
        assert run.returncode == 0, run.stderr
        return dict(line.split() for line in run.stdout.splitlines())

    def test_an_explicit_channel_is_honoured_exactly(self, tmp_path):
        out = self._run(tmp_path)
        assert out["explicit"] == "5"

    def test_play_loop_reserves_its_channel_and_keeps_playing(self, tmp_path):
        out = self._run(tmp_path)
        assert out["loop_channel"] == "0"
        assert out["loop_locked"] == "1"
        # 30 pooled plays through a limit of 3, and the reservation is
        # never touched -- without the lock, stealing would have taken
        # channel 0 almost immediately.
        assert out["stole_reserved"] == "0"
        # And it is still going, far past its own half-second length,
        # which only a real loop does.
        assert out["still_looping"] == "1"

    def test_a_second_clip_can_take_the_channel_over(self, tmp_path):
        # The handover straight out of the motivating example.
        out = self._run(tmp_path)
        assert out["handover_battle"] == "0"
        assert out["handover_adventure_playing"] == "0"
        assert out["handover_still_locked"] == "1"

    def test_an_explicit_one_shot_play_releases_the_reservation(self, tmp_path):
        # "or if the channel is explicitly listed in play()/playLoop()":
        # play(n) takes the channel over AND hands it back to the pool,
        # since a one-shot has nothing to reserve it for.
        out = self._run(tmp_path)
        assert out["oneshot_released"] == "0"

    def test_stop_audio_player_stops_and_releases_one_channel(self, tmp_path):
        out = self._run(tmp_path)
        assert out["before_stop_locked"] == "1"
        assert out["before_stop_active"] == "1"
        assert out["after_stop_locked"] == "0"
        assert out["after_stop_active"] == "0"

    def test_a_bare_stop_audio_player_stops_every_channel(self, tmp_path):
        out = self._run(tmp_path)
        assert out["before_stop_all"] == "1"
        assert out["after_stop_all"] == "0"
        assert out["after_stop_all_locks"] == "0"

    def test_stopping_everything_releases_every_reservation(self, tmp_path):
        out = self._run(tmp_path)
        assert out["clip_stop_locked"] == "0"
        assert out["clip_stop_playing"] == "0"


class TestAudioOnANonMixingDevice:
    """claude.md #98: the pool opens one ALSA handle per voice, and not
    every "default" device does software mixing. On a bare hw: device
    with no dmix -- ordinary on minimal/embedded Linux, and on any
    machine where another program holds the device exclusively -- the
    second concurrent open fails with EBUSY.

    Treating that as fatal (which it briefly was) meant an overlapping
    play() killed the program outright on such a system, with an error
    claiming there was no audio device when there plainly was one. That
    was a regression the pool introduced: the single-voice design it
    replaced never had two handles open, so it could never hit it.

    The right answer is to give a playing voice's handle back and retry,
    which degrades to exactly the pre-pool behaviour -- overlapping
    plays cut each other off instead of layering. On a device that
    cannot mix, layering was never physically possible, and quietly
    getting fewer simultaneous sounds beats not running.
    """

    def _run(self, tmp_path):
        cc = shutil.which("clang") or shutil.which("gcc") or shutil.which("cc")
        if not cc:
            pytest.skip("no C compiler (clang/gcc/cc) on PATH")
        # claude.md #101: the audio unit needs libmpg123 too now.
        alsa = subprocess.run(["pkg-config", "--cflags", "--libs", "alsa", "libmpg123"],
                               capture_output=True, text=True)
        if alsa.returncode != 0:
            pytest.skip("alsa/libmpg123 dev headers are not installed")
        runtime_dir = os.path.join(os.path.dirname(_EXAMPLES_DIR), "runtime")
        harness = tmp_path / "single_stream_harness.c"
        harness.write_text(_SINGLE_STREAM_HARNESS)
        binary = tmp_path / "single_stream_harness"
        wav = tmp_path / "clip.wav"
        _write_wav(wav, duration_s=2.0)
        build = subprocess.run(
            [cc, "-I", runtime_dir, str(harness), "-o", str(binary), "-pthread"]
            + alsa.stdout.split(),
            capture_output=True, text=True,
        )
        assert build.returncode == 0, build.stderr
        run = subprocess.run([str(binary), str(wav)], capture_output=True, text=True,
                              timeout=120)
        return run

    def test_overlapping_plays_degrade_instead_of_killing_the_program(self, tmp_path):
        run = self._run(tmp_path)
        assert run.returncode == 0, run.stderr
        out = dict(line.split() for line in run.stdout.splitlines())
        # Five plays, one stream: one voice, no crash, still playing.
        assert out["voices"] == "1"
        assert out["open_handles"] == "1"
        assert out["isplaying"] == "1"

    def test_no_handle_is_leaked_by_the_retry(self, tmp_path):
        run = self._run(tmp_path)
        assert run.returncode == 0, run.stderr
        out = dict(line.split() for line in run.stdout.splitlines())
        # Every failed open must close nothing and every freed voice must
        # release its handle -- otherwise the device would stay busy and
        # the retry could never succeed a second time.
        assert out["after_stop"] == "0"
        assert out["leaked_handles"] == "0"


class TestAudioVoicePool:
    """claude.md #98: `aud.play()` no longer cuts off a playback that is
    already running -- each clip owns a pool of voices, one thread and
    one ALSA handle per simultaneous playback, all streaming the same
    decoded PCM read-only.

    See _VOICE_POOL_HARNESS's own comment for why this is a white-box C
    test: the pool is deliberately invisible to the language, and the
    null ALSA device the other audio tests use consumes PCM instantly,
    so under it there is no concurrency left to observe at all.
    """

    def _run_harness(self, tmp_path):
        cc = shutil.which("clang") or shutil.which("gcc") or shutil.which("cc")
        if not cc:
            pytest.skip("no C compiler (clang/gcc/cc) on PATH")
        # claude.md #101: the audio unit needs libmpg123 too now.
        alsa = subprocess.run(["pkg-config", "--cflags", "--libs", "alsa", "libmpg123"],
                               capture_output=True, text=True)
        if alsa.returncode != 0:
            pytest.skip("alsa/libmpg123 dev headers are not installed")

        runtime_dir = os.path.join(os.path.dirname(_EXAMPLES_DIR), "runtime")
        harness = tmp_path / "voice_pool_harness.c"
        harness.write_text(_VOICE_POOL_HARNESS)
        binary = tmp_path / "voice_pool_harness"
        # 2 seconds at 8kHz is 16000 frames -- four 4096-frame chunks,
        # so ~40ms of playback per voice under the harness's stub. Long
        # enough that back-to-back plays overlap, short enough that 40
        # of them through a pool of 3 still finish quickly.
        wav = tmp_path / "clip.wav"
        _write_wav(wav, duration_s=2.0)

        build = subprocess.run(
            [cc, "-I", runtime_dir, str(harness),
             "-o", str(binary), "-pthread"] + alsa.stdout.split(),
            capture_output=True, text=True,
        )
        assert build.returncode == 0, build.stderr
        run = subprocess.run([str(binary), str(wav)], capture_output=True, text=True,
                              timeout=180)
        assert run.returncode == 0, run.stderr
        return dict(line.split() for line in run.stdout.splitlines())

    def test_overlapping_plays_each_get_their_own_voice(self, tmp_path):
        out = self._run_harness(tmp_path)
        assert out["default"] == "10"
        # The whole point: three plays, three voices, none cut off.
        assert out["three"] == "3"
        assert out["isplaying"] == "1"

    def test_stopping_every_channel_ends_every_voice(self, tmp_path):
        out = self._run_harness(tmp_path)
        assert out["stopped"] == "0"
        assert out["isplaying_after_stop"] == "0"
        assert out["final"] == "0"
        assert out["drained"] == "0"

    def test_the_limit_caps_the_pool_and_steals_rather_than_dropping(self, tmp_path):
        out = self._run_harness(tmp_path)
        # Six plays into a limit of 2: saturated, never exceeded, and
        # still 2 rather than 0 -- the older voices were stolen and
        # replaced, not merely stopped, and no play() was dropped.
        assert out["limit2"] == "2"
        # A limit of 1 is the old cut-off-the-previous-sound behaviour.
        assert out["limit1"] == "1"

    def test_slots_are_reused_rather_than_grown(self, tmp_path):
        # 40 plays through a pool of 3. This only holds if a finished
        # thread is joined and its slot reclaimed -- otherwise the pool
        # would either overflow or leak one thread per play over the
        # life of a long-running game.
        out = self._run_harness(tmp_path)
        assert out["peak"] == "3"


class TestRegex:
    """claude.md #67 (regular expressions), #68 (string match/replace)."""

    def test_test_matches_and_does_not_match(self, compile_and_run):
        source = """
        regex digits = regex('[0-9]+')
        log(digits.test('room 42'))
        log(digits.test('no numbers'))
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["true", "false"]

    def test_case_insensitive_flag(self, compile_and_run):
        source = """
        regex greeting = regex('^hello$', 'i')
        log(greeting.test('HELLO'))
        log(greeting.test('goodbye'))
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["true", "false"]

    def test_match_returns_first_match(self, compile_and_run):
        source = """
        regex digits = regex('[0-9]+')
        log('room 42, building 7'.match(digits))
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "42"

    def test_match_with_no_match_returns_null(self, compile_and_run):
        # claude.md #68: match() returns null (claude.md #25: null is
        # valid for every type) if there's no match -- text's null is
        # represented as a plain NULL pointer (see festina_runtime.h's
        # doc comment on festina_regex_match), which festina_log_text
        # already prints as an empty line.
        source = """
        regex digits = regex('[0-9]+')
        text found = 'no numbers here'.match(digits)
        log('before')
        log(found)
        log('after')
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["before", "", "after"]

    def test_replace_with_literal_text_search(self, compile_and_run):
        result = compile_and_run("log('room 42'.replace('room', 'suite'))")
        assert result.stdout.strip() == "suite 42"

    def test_replace_only_replaces_first_occurrence(self, compile_and_run):
        result = compile_and_run("log('a-b-c'.replace('-', '_'))")
        assert result.stdout.strip() == "a_b-c"

    def test_a_text_search_never_replaces_more_than_the_first(self, compile_and_run):
        # claude.md #107: .replaceAll() is gone and a plain-text search
        # carries no flags, so there is no longer any way to replace
        # every occurrence of a literal substring except by making it a
        # /g pattern. That is a real narrowing of the text path and is
        # pinned here so it cannot drift back silently.
        result = compile_and_run("log('a-b-c'.replace('-', '_'))")
        assert result.stdout.strip() == "a_b-c"

    def test_a_g_pattern_is_how_every_occurrence_is_spelled_now(self, compile_and_run):
        result = compile_and_run(r"log('a-b-c'.replace(/-/g, '_'))")
        assert result.stdout.strip() == "a_b_c"

    def test_replace_with_no_match_returns_original_unchanged(self, compile_and_run):
        result = compile_and_run("log('hello world'.replace('zzz', 'nope'))")
        assert result.stdout.strip() == "hello world"

    def test_a_dynamic_regex_can_carry_the_g_flag(self, compile_and_run):
        # claude.md #107's real gain over .replaceAll(): the flag lives
        # on the compiled pattern, so a pattern whose flags are only
        # known at run time can be global. The old design decided
        # first-vs-every at the CALL SITE, which a runtime-built
        # pattern could never influence.
        result = compile_and_run("log('a1b2c3'.replace(regex('[0-9]', 'g'), '-'))")
        assert result.stdout.strip() == "a-b-c-"

    def test_a_dynamic_regex_without_g_replaces_the_first_only(self, compile_and_run):
        result = compile_and_run("log('a1b2c3'.replace(regex('[0-9]'), '-'))")
        assert result.stdout.strip() == "a-b2c3"

    def test_a_dynamic_regex_flag_string_may_combine_g_and_i(self, compile_and_run):
        result = compile_and_run("log('TEST test'.replace(regex('test', 'gi'), 'x'))")
        assert result.stdout.strip() == "x x"

    def test_replace_with_regex_search_first_match_only(self, compile_and_run):
        result = compile_and_run("log('a1b2c3'.replace(regex('[0-9]'), '-'))")
        assert result.stdout.strip() == "a-b2c3"

    def test_replace_all_zero_width_match_does_not_hang(self, compile_and_run):
        # claude.md #54's ambiguity rule doesn't cover this -- it's a
        # straightforward correctness requirement, not something to
        # leave unresolved: a pattern that can match zero-width (e.g.
        # "x*" where there's no "x") must not spin the runtime forever.
        result = compile_and_run("log('abc'.replace(regex('x*', 'g'), '-'))")
        assert result.returncode == 0
        assert result.stdout.strip() == "-a-b-c-"

    def test_original_text_value_is_unchanged_after_replace(self, compile_and_run):
        source = """
        text original = 'room 42'
        text renamed = original.replace('room', 'suite')
        log(original)
        log(renamed)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["room 42", "suite 42"]

    def test_invalid_pattern_is_a_clear_runtime_error(self, compile_and_run):
        # claude.md #67: "An invalid pattern is a runtime error (fail()),
        # not a compile-time error."
        result = compile_and_run("regex bad = regex('[unclosed')\nlog('unreachable')")
        assert result.returncode == 1
        assert "invalid regex pattern" in result.stderr
        assert "unreachable" not in result.stdout


class TestRegexLiteral:
    """claude.md #67: /pattern/flags -- same end-to-end behavior as
    TestRegex above (a literal compiles down to exactly the same
    festina_regex_compile() call), just via the new literal syntax."""

    def test_test_matches_and_does_not_match(self, compile_and_run):
        source = """
        regex digits = /[0-9]+/
        log(digits.test('room 42'))
        log(digits.test('no numbers'))
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["true", "false"]

    def test_i_flag_matches_case_insensitively(self, compile_and_run):
        source = """
        regex greeting = /^hello$/i
        log(greeting.test('HELLO'))
        log(greeting.test('goodbye'))
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["true", "false"]

    def test_g_flag_is_accepted_and_still_only_case_sensitive_by_default(self, compile_and_run):
        # 'g' alone has no additional effect (see the parser's own
        # comment on _SUPPORTED_REGEX_FLAGS) -- this just confirms
        # accepting it doesn't silently turn on case-insensitivity too.
        source = """
        regex digits = /[a-z]+/g
        log(digits.test('room'))
        log(digits.test('ROOM'))
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["true", "false"]

    def test_word_shorthand_class_matches_via_glibcs_gnu_extension(self, compile_and_run):
        # \w -- not official POSIX ERE syntax, but glibc's regcomp()
        # supports it as a GNU extension even in REG_EXTENDED mode
        # (verified directly against this runtime's own libc) -- this is
        # exactly the case that makes the JS-familiar shorthand classes
        # work in practice, not just the narrower official POSIX escapes.
        result = compile_and_run(r"log(/\w+/.test('hello world'))")
        assert result.stdout.strip() == "true"

    def test_combined_flags_from_the_readme_example(self, compile_and_run):
        result = compile_and_run(r"log(/\w+/gi.test('Hello'))")
        assert result.stdout.strip() == "true"

    def test_match_returns_first_match(self, compile_and_run):
        result = compile_and_run("log('room 42, building 7'.match(/[0-9]+/))")
        assert result.stdout.strip() == "42"

    def test_a_g_literal_replaces_every_match(self, compile_and_run):
        result = compile_and_run("log('a1b2c3'.replace(/[0-9]/g, '-'))")
        assert result.stdout.strip() == "a-b-c-"

    def test_the_same_literal_without_g_replaces_only_the_first(self, compile_and_run):
        result = compile_and_run("log('a1b2c3'.replace(/[0-9]/, '-'))")
        assert result.stdout.strip() == "a-b2c3"

    def test_gi_together_are_global_and_case_insensitive(self, compile_and_run):
        # The user's own example spelling: /test/gi.
        result = compile_and_run("log('TEST test'.replace(/test/gi, 'x'))")
        assert result.stdout.strip() == "x x"

    def test_i_alone_is_case_insensitive_but_not_global(self, compile_and_run):
        result = compile_and_run("log('TEST test'.replace(/test/i, 'x'))")
        assert result.stdout.strip() == "x test"

    def test_g_does_not_make_test_stateful(self, compile_and_run):
        # claude.md #107: JS's /g gives .test() a lastIndex that makes
        # repeated calls alternate true/false. Deliberately not
        # reproduced -- the same test against the same string is the
        # same answer every time.
        source = """
        regex p = /[0-9]/g
        log(p.test('a1'))
        log(p.test('a1'))
        log(p.test('a1'))
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["true", "true", "true"]

    def test_g_does_not_change_what_match_returns(self, compile_and_run):
        # claude.md #107: in JS, /g makes .match() return an array
        # instead of a string. Festina's .match() returns text, and a
        # return TYPE cannot depend on a flag that regex(p, f) only
        # knows at run time -- so 'g' is ignored here, by design.
        result = compile_and_run("log('a1b2c3'.match(/[0-9]/g))")
        assert result.stdout.strip() == "1"

    def test_escaped_slash_in_a_literal_pattern_matches_a_literal_slash(self, compile_and_run):
        result = compile_and_run(r"log(/a\/b/.test('a/b'))")
        assert result.stdout.strip() == "true"

    def test_used_directly_without_a_named_variable(self, compile_and_run):
        # No `regex x = ...` in between -- the literal is itself a
        # complete expression, usable anywhere a regex value is.
        result = compile_and_run("log(/[0-9]+/.test('42'))")
        assert result.stdout.strip() == "true"

    def test_a_real_division_right_after_this_feature_still_works(self, compile_and_run):
        # End-to-end confirmation that adding regex literals didn't
        # break ordinary division anywhere a human could plausibly
        # confuse the two -- see test_lexer.py::TestRegexLiterals for
        # the exhaustive disambiguation matrix at the tokenizer level.
        result = compile_and_run("int a = 10\nint b = 2\nlog(a / b)")
        assert result.stdout.strip() == "5"


class TestNumericConversion:
    """claude.md #55 (no implicit int/float conversion), #56 (Math),
    #57 (division/modulo by zero returns null). See
    tests/test_numeric_conversion.py for the parser/semantic-only tests;
    these check the actual runtime behavior of a compiled program."""

    @pytest.mark.parametrize("fn,expected", [
        ("floor", "19"), ("ceil", "20"), ("round", "20"), ("trunc", "19"),
    ])
    def test_math_function_runtime_result(self, compile_and_run, fn, expected):
        result = compile_and_run(f"float price = 19.99\nlog(Math.{fn}(price))")
        assert result.stdout.strip() == expected

    def test_to_float_runtime_result(self, compile_and_run):
        source = """
        int a = 5
        float b = 2.5
        float c = a.toFloat() + b
        log(c)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "7.5"

    def test_to_text_runtime_result(self, compile_and_run):
        source = """
        int i = 42
        float f = 3.14
        bool b = true
        log(i.toText())
        log(f.toText())
        log(b.toText())
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["42", "3.14", "true"]

    def test_to_text_matches_template_interpolation(self, compile_and_run):
        result = compile_and_run("int i = 42\nlog(i.toText())\nlog(`${i}`)")
        lines = result.stdout.splitlines()
        assert lines[0] == lines[1]

    def test_mixed_int_float_rejected_end_to_end(self, compile_and_run, errors):
        # Confirms the whole pipeline (not just semantic.py in isolation)
        # rejects this -- semantic analysis raises before ever reaching
        # a linker, so this is a CompileError, not a nonzero exit code.
        with pytest.raises(errors.CompileError, match="int and float"):
            compile_and_run("int a = 5\nfloat b = 2.5\nfloat c = a + b")

    def test_int_division_by_zero_returns_null(self, compile_and_run):
        # claude.md #57: must not crash (SIGFPE) and must not silently
        # compute garbage -- the sentinel is intentionally an
        # implementation detail (see codegen.py's module docstring), so
        # this only checks the process survives and produces *a* value,
        # not the exact sentinel bit pattern.
        source = """
        int a = 10
        int b = 0
        int result = a / b
        log('survived')
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "survived"

    def test_int_modulo_by_zero_returns_null(self, compile_and_run):
        source = "int a = 10\nint b = 0\nint result = a % b\nlog('survived')"
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "survived"

    def test_float_division_by_zero_returns_null(self, compile_and_run):
        source = "float a = 5.0\nfloat b = 0.0\nfloat result = a / b\nlog('survived')"
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "survived"

    def test_int_division_by_nonzero_is_unaffected(self, compile_and_run):
        result = compile_and_run("int a = 10\nint b = 4\nlog(a / b)\nlog(a % b)")
        assert result.stdout.splitlines() == ["2", "2"]

    def test_null_int_and_float_assignment(self, compile_and_run):
        # Regression test: `null` used to lower to the LLVM keyword
        # `null` unconditionally, which is only valid for pointer types --
        # `int x = null` failed to link before this fix.
        result = compile_and_run("int a = null\nfloat b = null\nlog('assigned fine')")
        assert result.returncode == 0
        assert result.stdout.strip() == "assigned fine"

    def test_null_bool_assignment(self, compile_and_run):
        # Regression test: same "null is only valid IR for a pointer
        # type" link failure as int/float, but bool needed one extra
        # step to fix -- see festina/codegen.py's module docstring's
        # "Null for bool" note (bool widened from i1 to i8 to make room
        # for a third, reserved value).
        result = compile_and_run("bool a = null\nlog('assigned fine')")
        assert result.returncode == 0
        assert result.stdout.strip() == "assigned fine"

    def test_comparing_a_null_int_against_the_null_literal(self, compile_and_run):
        # Regression test, found alongside the bool-null fix: `x == null`
        # for a concretely-typed int/float/bool operand used to reach
        # codegen as `icmp eq i64 %x, null` -- also invalid IR (null is
        # only valid for a pointer type), independent of bool at all.
        result = compile_and_run("int a = 1 / 0\nlog(a == null)")
        assert result.stdout.strip() == "true"

    def test_comparing_a_non_null_int_against_the_null_literal(self, compile_and_run):
        result = compile_and_run("int a = 5\nlog(a == null)")
        assert result.stdout.strip() == "false"

    def test_comparing_a_null_float_against_the_null_literal(self, compile_and_run):
        # Unlike int/bool, this is "false", not "true" -- FLOAT_NULL_CONST
        # is a real NaN, and IEEE-754's `oeq`/`one` ("ordered" compares,
        # which is what claude.md's == / != already lower to -- see
        # _emit_binop's fcmp dict) are false for *any* comparison
        # involving NaN, including a NaN compared with itself. This test
        # exists to confirm the fix at least compiles/runs cleanly now
        # (it used to be an LLVM IR parse error -- see the int case
        # above) -- not to claim float null-checking via == is reliable
        # the way it is for int/bool, which it structurally can't be
        # without changing every == / != to unordered compares, a
        # separate, unrelated design decision this fix doesn't make.
        result = compile_and_run("float a = 1.0 / 0.0\nlog(a == null)")
        assert result.stdout.strip() == "false"

    def test_comparing_a_null_bool_against_the_null_literal(self, compile_and_run):
        result = compile_and_run("bool a = null\nlog(a == null)")
        assert result.stdout.strip() == "true"

    def test_comparing_a_non_null_bool_against_the_null_literal(self, compile_and_run):
        result = compile_and_run("bool a = true\nlog(a == null)")
        assert result.stdout.strip() == "false"

    def test_null_bool_survives_a_function_call_round_trip(self, compile_and_run):
        source = """
        bool func identity(b:bool) {
            return b
        }
        log(identity(null) == null)
        log(identity(false) == null)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["true", "false"]

    def test_null_bool_struct_field(self, compile_and_run):
        source = """
        struct Flag { value:bool }
        Flag f
        f.value = null
        log(f.value == null)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "true"

    def test_ordinary_bool_logic_is_unaffected_by_the_widened_representation(self, compile_and_run):
        # claude.md #21/#22-ish sanity check: none of &&/||/!/==/!= on
        # ordinary (non-null) bool values should have changed behavior
        # just because the underlying storage widened from i1 to i8.
        source = """
        bool a = true
        bool b = false
        log(a && b)
        log(a || b)
        log(!a)
        log(!b)
        log(a == b)
        log(a != b)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == ["false", "true", "false", "true", "false", "true"]

    def test_float_literal_needing_scientific_notation(self, compile_and_run):
        # Regression test: _format_double used repr(), which switches to
        # scientific notation for small/large magnitudes (e.g. 1e-07) --
        # LLVM's float-literal grammar rejected that as invalid syntax.
        result = compile_and_run("float tiny = 0.0000001\nlog('compiled fine')")
        assert result.returncode == 0
        assert result.stdout.strip() == "compiled fine"


class TestFail:
    """claude.md #42: fail() is the runtime failure mechanism."""

    def test_fail_exits_nonzero_with_message(self, compile_and_run):
        source = """
        bool ok = false
        if ok != true {
            fail('Test failed')
        }
        log('unreachable')
        """
        result = compile_and_run(source)
        assert result.returncode == 1
        assert "Test failed" in result.stderr
        assert "unreachable" not in result.stdout


class TestEntryPoint:
    """claude.md #7: the programmer never defines main(); top-level
    executable statements run automatically."""

    def test_program_runs_without_a_declared_main(self, compile_and_run):
        result = compile_and_run("log('hello from entry')")
        assert result.returncode == 0
        assert result.stdout.strip() == "hello from entry"


class TestMultiFileCompilation:
    """claude.md #5, #6: `import file.f` pulls a file and its whole
    dependency graph into the current compilation unit -- one real
    compiled program spanning multiple source files, not per-file
    namespacing or runtime modules."""

    def test_struct_and_function_shared_across_files(self, compile_multi_and_run):
        files = {
            "main.f": (
                "import shapes.f\n"
                "Point p\n"
                "p.x = 3\n"
                "p.y = 4\n"
                "log(describe(p))\n"
            ),
            "shapes.f": (
                "struct Point {\n    x:int\n    y:int\n}\n"
                "text func describe(p:Point) {\n"
                "    return `(${p.x}, ${p.y})`\n"
                "}\n"
            ),
        }
        result = compile_multi_and_run(files)
        assert result.returncode == 0
        assert result.stdout.strip() == "(3, 4)"

    def test_transitive_imports(self, compile_multi_and_run):
        # main.f -> ui.f -> database.f
        files = {
            "main.f": "import ui.f\nshowGreeting()\n",
            "ui.f": "import database.f\nvoid func showGreeting() {\n    log(greeting())\n}\n",
            "database.f": "text func greeting() {\n    return 'hello from database.f'\n}\n",
        }
        result = compile_multi_and_run(files)
        assert result.returncode == 0
        assert result.stdout.strip() == "hello from database.f"

    def test_diamond_import_is_not_duplicated(self, compile_multi_and_run):
        # main.f imports both ui.f and database.f directly, and ui.f also
        # imports database.f -- claude.md #6: "each source file must be
        # processed only once." If database.f's `table Items` were
        # emitted twice, this would fail to compile (duplicate LLVM
        # globals) rather than merely produce a wrong answer.
        files = {
            "main.f": (
                "import ui.f\n"
                "import database.f\n"
                "sqlite('INSERT INTO Items (id) VALUES (1)')\n"
                "arr[Items] items = sqlite('SELECT * FROM Items')\n"
                "log(items.length)\n"
            ),
            "ui.f": "import database.f\n",
            "database.f": "table Items {\n    id:int\n}\n",
        }
        result = compile_multi_and_run(files)
        assert result.returncode == 0
        assert result.stdout.strip() == "1"

    def test_table_declared_in_imported_file_syncs_schema(self, compile_multi_and_run, tmp_path):
        files = {
            "main.f": "import schema.f\nlog('synced')\n",
            "schema.f": "table People {\n    id:int\n    name:text\n}\n",
        }
        result = compile_multi_and_run(files)
        assert result.returncode == 0
        assert result.stdout.strip() == "synced"
        assert (tmp_path / "festina.sqlite").exists()

    def test_compile_error_in_an_imported_file_is_reported(self, cli_mod, errors, tmp_path):
        # Full-pipeline version of TestBuildProgram's equivalent check in
        # test_imports.py -- goes through festina.cli.compile_file itself.
        # No C compiler needed: this fails during semantic analysis,
        # before compile_file ever reaches the link step.
        files = {
            "main.f": "import broken.f\nlog('start')\n",
            "broken.f": "log(undefinedVariable)\n",
        }
        for relpath, content in files.items():
            p = tmp_path / relpath
            p.write_text(content)
        with pytest.raises(errors.CompileError) as exc_info:
            cli_mod.compile_file(str(tmp_path / "main.f"), str(tmp_path / "out"))
        assert "broken.f" in str(exc_info.value)


class TestAutomaticSqliteSchemaSync:
    """claude.md #8, #28-31: festina.sqlite is created/opened
    automatically and each declared table's schema is synchronized
    before the entry function runs -- worked examples from #31."""

    def _schema(self, db_path, table):
        conn = sqlite3.connect(db_path)
        try:
            return conn.execute(f"PRAGMA table_info({table})").fetchall()
        finally:
            conn.close()

    def test_missing_table_is_created(self, compile_and_run, tmp_path):
        source = """
        table People {
            id:int
            name:text
        }
        log('synced')
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        db = tmp_path / "festina.sqlite"
        assert db.exists()
        cols = {row[1]: row[2] for row in self._schema(db, "People")}
        assert cols == {"id": "INTEGER", "name": "TEXT"}

    def test_missing_column_is_added_and_data_preserved(self, compile_and_run, tmp_path):
        compile_and_run("table People {\n    id:int\n    name:text\n}\nlog('v1')")
        db = tmp_path / "festina.sqlite"
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO People (id, name) VALUES (1, 'Patrick')")
        conn.commit()
        conn.close()

        result = compile_and_run(
            "table People {\n    id:int\n    name:text\n    age:int\n}\nlog('v2')",
            filename="v2.f",
        )
        assert result.returncode == 0
        cols = {row[1]: row[2] for row in self._schema(db, "People")}
        assert cols == {"id": "INTEGER", "name": "TEXT", "age": "INTEGER"}
        rows = sqlite3.connect(db).execute("SELECT id, name FROM People").fetchall()
        assert rows == [(1, "Patrick")]

    def test_obsolete_column_is_dropped_data_preserved(self, compile_and_run, tmp_path):
        # claude.md #31 worked example: People(id, name, obsolete) -> People(id, name).
        compile_and_run(
            "table People {\n    id:int\n    name:text\n    obsolete:text\n}\nlog('v1')"
        )
        db = tmp_path / "festina.sqlite"
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO People (id, name, obsolete) VALUES (1, 'Patrick', 'junk')")
        conn.commit()
        conn.close()

        result = compile_and_run(
            "table People {\n    id:int\n    name:text\n}\nlog('v2')", filename="v2.f",
        )
        assert result.returncode == 0
        cols = {row[1] for row in self._schema(db, "People")}
        assert cols == {"id", "name"}
        rows = sqlite3.connect(db).execute("SELECT id, name FROM People").fetchall()
        assert rows == [(1, "Patrick")]

    def test_column_rename_via_declaration_change(self, compile_and_run, tmp_path):
        # claude.md #31 worked example: People(id, name) -> People(id, full_name).
        compile_and_run("table People {\n    id:int\n    name:text\n}\nlog('v1')")
        db = tmp_path / "festina.sqlite"
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO People (id, name) VALUES (1, 'Patrick')")
        conn.commit()
        conn.close()

        result = compile_and_run(
            "table People {\n    id:int\n    full_name:text\n}\nlog('v2')", filename="v2.f",
        )
        assert result.returncode == 0
        cols = {row[1] for row in self._schema(db, "People")}
        assert cols == {"id", "full_name"}
        rows = sqlite3.connect(db).execute("SELECT id FROM People").fetchall()
        assert rows == [(1,)]  # id survives the rebuild; the old `name` data does not

    def test_incompatible_column_type_is_altered_data_cast(self, compile_and_run, tmp_path):
        compile_and_run("table Items {\n    id:int\n    price:int\n}\nlog('v1')")
        db = tmp_path / "festina.sqlite"
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO Items (id, price) VALUES (1, 100)")
        conn.commit()
        conn.close()

        result = compile_and_run(
            "table Items {\n    id:int\n    price:float\n}\nlog('v2')", filename="v2.f",
        )
        assert result.returncode == 0
        cols = {row[1]: row[2] for row in self._schema(db, "Items")}
        assert cols == {"id": "INTEGER", "price": "REAL"}
        rows = sqlite3.connect(db).execute("SELECT id, price FROM Items").fetchall()
        assert rows == [(1, 100.0)]

    def test_no_tables_declared_means_no_db_file(self, compile_and_run, tmp_path):
        # claude.md #29: the database is only ever touched automatically --
        # a program with no `table` declarations shouldn't create one.
        result = compile_and_run("log('no tables here')")
        assert result.returncode == 0
        assert not (tmp_path / "festina.sqlite").exists()

    def test_a_table_too_wide_for_the_runtimes_sql_buffer_fails_cleanly(self, compile_and_run):
        # Security regression test: festina_sync_table builds several
        # SQL statements incrementally across a loop over the declared
        # columns, using the (deceptively unsafe -- see
        # festina_check_sql_buffer's own comment in festina_runtime.c)
        # `pos += snprintf(buf + pos, sizeof(buf) - pos, ...)` idiom.
        # Once accumulated output exceeds the fixed-size buffer, the
        # *next* call's "remaining space" computation used to
        # underflow (unsigned arithmetic) to a huge number, hand
        # snprintf permission to write far past the buffer, and
        # genuinely stack-smash -- verified directly with
        # AddressSanitizer before this was fixed. A table with enough
        # columns (or long enough names) to overflow the 2048-byte
        # CREATE TABLE buffer must now fail cleanly instead.
        cols = "\n".join(
            f"    col_{i}_{'x' * 60}:text" for i in range(40)
        )
        source = f"table Big {{\n{cols}\n}}\nlog('unreachable')"
        result = compile_and_run(source)
        assert result.returncode == 1
        assert "too long for this compiler's fixed-size buffer" in result.stderr
        assert "unreachable" not in result.stdout


class TestDatabaseURL:
    """claude.md #70: DatabaseURL = <expr>, the entry file's own first
    statement, overriding festina.sqlite's default location."""

    def test_no_directive_uses_the_default_filename(self, compile_and_run, tmp_path):
        result = compile_and_run("table People {\n    id:int\n}\nlog('built')")
        assert result.returncode == 0
        assert (tmp_path / "festina.sqlite").exists()

    def test_string_literal_directive_changes_the_path(self, compile_and_run, tmp_path):
        source = "DatabaseURL = 'custom.sqlite'\ntable People {\n    id:int\n}\nlog('built')"
        result = compile_and_run(source)
        assert result.returncode == 0
        assert (tmp_path / "custom.sqlite").exists()
        assert not (tmp_path / "festina.sqlite").exists()

    def test_directive_from_environment_variable(self, compile_and_run, tmp_path):
        source = "DatabaseURL = environment.DB_PATH\ntable People {\n    id:int\n}\nlog('built')"
        result = compile_and_run(source, env={"DB_PATH": "from_env.sqlite"})
        assert result.returncode == 0
        assert (tmp_path / "from_env.sqlite").exists()

    def test_data_actually_lands_in_the_configured_database(self, compile_and_run, tmp_path):
        source = """
        DatabaseURL = 'game.sqlite'
        table People {
            id:int
            name:text
        }
        sqlite('INSERT INTO People (id, name) VALUES (?, ?)', [1, 'Patrick'])
        arr[People] people = sqlite('SELECT * FROM People')
        log(people[0].name)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "Patrick"
        import sqlite3
        conn = sqlite3.connect(tmp_path / "game.sqlite")
        rows = conn.execute("SELECT name FROM People").fetchall()
        assert rows == [("Patrick",)]


class TestEnvironment:
    """claude.md #71: environment.NAME / environment[keyExpr]."""

    def test_reads_a_set_variable(self, compile_and_run):
        result = compile_and_run("log(environment.FESTINA_TEST_VAR)", env={"FESTINA_TEST_VAR": "hello"})
        assert result.stdout.strip() == "hello"

    def test_unset_variable_is_null(self, compile_and_run, monkeypatch):
        monkeypatch.delenv("FESTINA_DEFINITELY_UNSET_VAR", raising=False)
        result = compile_and_run("log('before')\nlog(environment.FESTINA_DEFINITELY_UNSET_VAR)\nlog('after')")
        assert result.stdout.splitlines() == ["before", "", "after"]

    def test_computed_access_with_a_variable_key(self, compile_and_run):
        source = "text k = 'FESTINA_TEST_VAR'\nlog(environment[k])"
        result = compile_and_run(source, env={"FESTINA_TEST_VAR": "computed"})
        assert result.stdout.strip() == "computed"

    def test_null_check_pattern(self, compile_and_run, monkeypatch):
        monkeypatch.delenv("FESTINA_DEFINITELY_UNSET_VAR", raising=False)
        source = """
        text apiKey = environment.FESTINA_DEFINITELY_UNSET_VAR
        if apiKey == null {
            log('not set')
        } else {
            log('set')
        }
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "not set"


class TestSqliteQueries:
    """claude.md #32-34: sqlite() queries, parameterized queries, and
    query result types (arr[Table])."""

    def test_select_into_arr_table_with_field_access(self, compile_and_run):
        source = """
        table People {
            id:int
            name:text
            score:float
        }
        sqlite('INSERT INTO People (id, name, score) VALUES (?, ?, ?)', [1, 'Patrick', 9.5])
        sqlite('INSERT INTO People (id, name, score) VALUES (?, ?, ?)', [2, 'Ada', 10.0])
        arr[People] people = sqlite('SELECT * FROM People ORDER BY id')
        log(people[0].id)
        log(people[0].name)
        log(people[0].score)
        log(people[1].name)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == ["1", "Patrick", "9.5", "Ada"]

    def test_parameterized_insert_and_select(self, compile_and_run):
        # claude.md #33's own example: sqlite(sql, [1, 'Patrick']) -- a
        # heterogeneously-typed literal bound as parameters.
        source = """
        table People {
            id:int
            name:text
        }
        sqlite('INSERT INTO People (id, name) VALUES (?, ?)', [1, 'Patrick'])
        sqlite('INSERT INTO People (id, name) VALUES (?, ?)', [2, 'Ada'])
        arr[People] found = sqlite('SELECT * FROM People WHERE id = ?', [2])
        log(found[0].name)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "Ada"

    def test_null_column_value_round_trips(self, compile_and_run):
        # claude.md #57: a SQL NULL column comes back as the same
        # reserved null sentinel used elsewhere for a null float (a quiet
        # NaN -- see test_float_division_by_zero_returns_null and the
        # module docstring's "Null for int/float" note), not a crash or a
        # 0.0.
        source = """
        table People {
            id:int
            score:float
        }
        sqlite('INSERT INTO People (id, score) VALUES (?, ?)', [1, null])
        arr[People] people = sqlite('SELECT * FROM People')
        log(people[0].score)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "nan"

    def test_columns_map_by_name_not_position(self, compile_and_run):
        # claude.md #111 INVERTED this test, which used to pin positional
        # mapping (and was named test_columns_map_by_position_not_name).
        # Positional mapping was a bug hiding behind the SELECT * habit:
        # `SELECT name FROM People` read the name's text into the id slot
        # as an integer, and `SELECT id` read a result column that did
        # not exist. Matching is by name now, case-insensitively, so a
        # REORDERED select lands every value in its declared column --
        # and an alias renames a column away from its declared name, so
        # aliased columns are simply not matched (they read as null and
        # undefined()). Alias TO a declared name to remap deliberately.
        source = """
        table People {
            id:int
            name:text
        }
        sqlite('INSERT INTO People (id, name) VALUES (?, ?)', [1, 'Patrick'])
        arr[People] people = sqlite('SELECT name, id FROM People')
        log(people[0].id)
        log(people[0].name)
        arr[People] aliased = sqlite('SELECT id AS whatever FROM People')
        log(aliased[0].id == null)
        log(aliased[0].undefined('id'))
        arr[People] remapped = sqlite("SELECT 'Renamed' AS name FROM People")
        log(remapped[0].name)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.splitlines() == [
            "1", "Patrick", "true", "true", "Renamed"]

    def test_exec_only_query_discards_result(self, compile_and_run):
        # A statement whose result isn't captured into an arr[Table]
        # (here, INSERT) just runs to completion.
        source = """
        table People {
            id:int
        }
        sqlite('INSERT INTO People (id) VALUES (1)')
        log('done')
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "done"

    def test_non_literal_params_argument_is_a_clear_error(self, parser, semantic, codegen, errors):
        # claude.md #33's params must be a literal array -- see the
        # module docstring's "Query rows" note for why (a real arr[T]
        # value can't represent claude.md's own heterogeneously-typed
        # example).
        source = """
        table People {
            id:int
        }
        arr[int] ids = [1]
        sqlite('SELECT * FROM People WHERE id = ?', ids)
        """
        program = parser.parse(source, filename="main.f")
        analyzed = semantic.analyze(program, filename="main.f")
        with pytest.raises(errors.CompileError, match="literal array"):
            codegen.generate_ir(program, analyzed, filename="main.f")

    def test_schema_sync_and_query_against_same_table_do_not_collide(self, compile_and_run):
        # _table_arrays' globals are shared between schema sync (main())
        # and query codegen -- this would previously have redefined the
        # same LLVM globals (a link error) if not cached.
        source = """
        table People {
            id:int
            name:text
        }
        sqlite('INSERT INTO People (id, name) VALUES (?, ?)', [1, 'Patrick'])
        arr[People] people = sqlite('SELECT * FROM People')
        log(people[0].name)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "Patrick"


class TestMinimalRuntimeDependencies:
    """"Real compilation, minimal setup" stage 1: a compiled program
    shouldn't need libsqlite3.so installed on the machine that runs it,
    if a static sqlite3 archive was available at compile time (see
    festina/cli.py's _sqlite_link_flags)."""

    def test_compiled_binary_does_not_dynamically_link_libsqlite3(self, compile_and_run, tmp_path):
        # compile_and_run's own fixture skip (no clang) covers this test
        # too; ldd is the other half of the toolchain this needs.
        if not shutil.which("ldd"):
            pytest.skip("ldd not on PATH -- cannot inspect the binary's dynamic dependencies")
        from festina import cli as cli_mod

        _, statically_linked = cli_mod._sqlite_link_flags(shutil.which("clang"))
        if not statically_linked:
            pytest.skip("no static libsqlite3.a available in this environment -- "
                        "_sqlite_link_flags already fell back to dynamic linking, "
                        "which is the correct behavior, just not what this test checks")

        compile_and_run("table People {\n    id:int\n}\nlog('built')")
        binary = tmp_path / "program"
        assert binary.exists()

        ldd_output = subprocess.run(["ldd", str(binary)], capture_output=True, text=True).stdout
        assert "libsqlite3" not in ldd_output


class TestSlimBinaries:
    """claude.md #59's binary-slimming requirement: "if a canvas isn't
    used, make sure the binary remains slim". The runtime is split into
    core/graphics/audio translation units (runtime/festina_runtime.c/
    _graphics.c/_audio.c) specifically so a compiled program's `cc`
    invocation only ever gets -lcairo/-lX11/-lasound on its command line
    when that program actually uses graphics/audio (see festina/cli.py's
    _runtime_objects_and_link_libs, driven by CodeGen.uses_graphics/
    uses_graphics_code/uses_audio) -- confirmed here by inspecting the
    linked binary's actual dynamic dependencies (ldd), not just that
    compilation succeeded, since --gc-sections/--as-needed alone was
    verified NOT to remove an unused library from a single-object-file
    build (the linker's "is this needed" decision is made against the
    whole translation unit before dead-code elimination runs)."""

    def _ldd(self, binary):
        if not shutil.which("ldd"):
            pytest.skip("ldd not on PATH -- cannot inspect the binary's dynamic dependencies")
        return subprocess.run(["ldd", str(binary)], capture_output=True, text=True).stdout

    def test_graphics_and_audio_free_binary_links_neither(self, compile_and_run, tmp_path):
        compile_and_run("log('hi')")
        ldd_output = self._ldd(tmp_path / "program")
        assert "libcairo" not in ldd_output
        assert "libX11" not in ldd_output
        assert "libasound" not in ldd_output

    def test_graphics_binary_links_cairo_and_x11_but_not_alsa(self, tmp_path):
        if not (shutil.which("clang") or shutil.which("gcc") or shutil.which("cc")):
            pytest.skip("no C compiler (clang/gcc/cc) on PATH")
        from festina import cli as cli_mod

        src = tmp_path / "main.f"
        src.write_text("drawRect(1, 1, 2, 2)")
        out = tmp_path / "program"
        cli_mod.compile_file(str(src), str(out), cc=shutil.which("clang") or shutil.which("gcc") or shutil.which("cc"))
        ldd_output = self._ldd(out)
        assert "libcairo" in ldd_output
        assert "libX11" in ldd_output
        assert "libasound" not in ldd_output

    def test_audio_binary_links_alsa_but_not_cairo_or_x11(self, tmp_path):
        if not (shutil.which("clang") or shutil.which("gcc") or shutil.which("cc")):
            pytest.skip("no C compiler (clang/gcc/cc) on PATH")
        from festina import cli as cli_mod

        # Loading a nonexistent file fails at *runtime*, not compile
        # time (claude.md #38's loadAudio() has no compile-time path
        # validation) -- irrelevant here, this only checks what got
        # linked, never runs the binary.
        src = tmp_path / "main.f"
        src.write_text("aud music = 'nonexistent.wav'")
        out = tmp_path / "program"
        cli_mod.compile_file(str(src), str(out), cc=shutil.which("clang") or shutil.which("gcc") or shutil.which("cc"))
        ldd_output = self._ldd(out)
        assert "libasound" in ldd_output
        assert "libcairo" not in ldd_output
        assert "libX11" not in ldd_output

    def test_loadimage_alone_still_links_successfully_without_opening_a_window(self, tmp_path):
        # Regression test: loadImage() deliberately does NOT set
        # CodeGen.uses_graphics (see _emit_graphics_call's doc comment --
        # decoding a PNG needs no X server), but festina_load_image()
        # still lives in the graphics object file, not core. Without
        # CodeGen.uses_graphics_code as a separate, broader linking
        # signal, this compiled fine but failed to *link*
        # ("undefined reference to festina_load_image").
        if not (shutil.which("clang") or shutil.which("gcc") or shutil.which("cc")):
            pytest.skip("no C compiler (clang/gcc/cc) on PATH")
        from festina import cli as cli_mod

        src = tmp_path / "main.f"
        src.write_text("img icon = 'nonexistent.png'")
        out = tmp_path / "program"
        cli_mod.compile_file(str(src), str(out), cc=shutil.which("clang") or shutil.which("gcc") or shutil.which("cc"))
        assert out.exists()


class TestMinimalBuildDependencies:
    """"Real compilation, minimal setup" stage 3: using Festina shouldn't
    require clang *specifically*. Before this stage, cc had to be clang
    because it was handed the .ll file directly (the only common
    compiler with an LLVM-IR-text frontend); now festina.llvm_backend
    compiles that step itself via libLLVM, so cc's remaining job --
    compiling festina_runtime.c and linking plain object files -- is
    compiler-agnostic. See festina/cli.py's module docstring."""

    def test_gcc_works_as_cc_when_libllvm_is_available(self, parser, semantic, codegen, tmp_path, llvm_backend):
        if not shutil.which("gcc"):
            pytest.skip("gcc not on PATH")
        if not llvm_backend.available():
            pytest.skip("libLLVM unavailable -- gcc-as-cc only works via the "
                        "libLLVM path (the clang-IR-frontend fallback needs clang)")
        from festina import cli as cli_mod

        src = tmp_path / "main.f"
        src.write_text("table People {\n    id:int\n    name:text\n}\nlog('built with gcc')")
        out = tmp_path / "program"
        cli_mod.compile_file(str(src), str(out), cc="gcc")
        assert out.exists()

        result = subprocess.run([str(out)], cwd=tmp_path, capture_output=True, text=True, timeout=15)
        assert result.returncode == 0
        assert result.stdout.strip() == "built with gcc"

    def test_falls_back_to_clang_ir_frontend_when_libllvm_unavailable(self, parser, semantic, codegen, tmp_path, monkeypatch):
        clang = shutil.which("clang")
        if not clang:
            pytest.skip("clang not on PATH -- nothing to fall back to")
        from festina import cli as cli_mod, llvm_backend

        class _Unavailable:
            lib = None

        monkeypatch.setattr(llvm_backend, "_binding_instance", _Unavailable())
        assert llvm_backend.available() is False

        src = tmp_path / "main.f"
        src.write_text("log('built via fallback')")
        out = tmp_path / "program"
        cli_mod.compile_file(str(src), str(out), cc=clang)
        assert out.exists()

        result = subprocess.run([str(out)], cwd=tmp_path, capture_output=True, text=True, timeout=15)
        assert result.returncode == 0
        assert result.stdout.strip() == "built via fallback"


class TestMissingDependencyErrors:
    """claude.md #59: a genuinely missing dependency must fail with a
    clear, actionable message naming it and how to install it -- not a
    raw exception. Verified directly: subprocess.run(..., check=False)
    does *not* catch "the executable doesn't exist at all" the way it
    catches a nonzero exit code, so this needs its own handling
    (festina/cli.py's _run_tool). path_without is a shared conftest.py
    fixture -- test_cli.py's TestDoctor reuses it too."""

    def test_missing_pkg_config_gives_actionable_error(self, parser, semantic, codegen, cli_mod, errors, tmp_path, path_without, monkeypatch):
        path_without("pkg-config")
        # claude.md #59's per-feature object file split (see
        # festina/cli.py's _RUNTIME_FEATURES) means a graphics/audio-free
        # program like this one never calls pkg-config for cairo-xlib/
        # alsa at all -- only sqlite3, which is always needed. sqlite3's
        # own flags are cached process-wide (_sqlite_link_cache, keyed by
        # `cc`) across every compile_file call in this test session, so
        # without clearing it here, an earlier test's successful compile
        # would make this one a silent cache hit that never calls
        # pkg-config, never noticing it's missing.
        monkeypatch.setattr(cli_mod, "_sqlite_link_cache", {})
        src = tmp_path / "main.f"
        src.write_text("log('hi')")
        with pytest.raises(errors.CompileError, match="pkg-config.*install"):
            cli_mod.compile_file(str(src), str(tmp_path / "out"), cc="clang")

    def test_missing_cc_gives_actionable_error(self, parser, semantic, codegen, cli_mod, errors, tmp_path, path_without):
        path_without("clang", "gcc", "cc")
        src = tmp_path / "main.f"
        src.write_text("log('hi')")
        with pytest.raises(errors.CompileError, match="clang.*install"):
            cli_mod.compile_file(str(src), str(tmp_path / "out"), cc="clang")


class TestTextReferenceManagement:
    """claude.md #83: every text-typed binding (local, global, struct
    field, array element, map value, parameter) always holds either NULL
    or a heap buffer it owns EXCLUSIVELY -- never a bare alias of a
    `.str.N` literal constant or of another binding's buffer. Unlike
    struct/arr[T]/map[T] (claude.md #77/#79, a refcount header in front
    of the payload), text keeps its plain `char*` representation
    everywhere -- sqlite, regex, log, and every other existing `char*`
    consumer are untouched -- and gets its exclusivity by COPYING at
    each binding site (festina_text_own, a NULL-safe strdup) whenever
    the value's source isn't already a fresh buffer. That invariant is
    what makes freeing unconditional: a text local is always safe to
    free on reassignment and at scope exit, with no escape analysis
    needed, because no other binding can ever be sharing its buffer.

    Before this section text was never freed anywhere at all, which is
    why benchmarks/string_concat.f spent its whole runtime growing the
    heap (816 brk() calls, now 3)."""

    def _ir(self, parser, semantic, codegen, source, filename="main.f"):
        program = parser.parse(source, filename=filename)
        analyzed = semantic.analyze(program, filename=filename)
        return codegen.generate_ir(program, analyzed, filename=filename)

    # ---- IR-level: no C compiler needed ----

    def test_text_local_from_a_literal_is_copied_not_aliased(self, parser, semantic, codegen):
        # A StringLit is a pointer to a `.str.N` global CONSTANT -- if a
        # local aliased it directly, the scope-exit free below would be
        # handing free() a pointer into the binary's own static data.
        source = """
        void func f() {
            text a = 'lit'
            log(a)
        }
        f()
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "call ptr @festina_text_own(ptr @.str." in ir

    def test_text_local_from_a_call_takes_ownership_with_no_copy(self, parser, semantic, codegen):
        # A Call already returns a fresh, exclusively-held buffer
        # (_is_owning_text_source), so copying it would be pure waste --
        # the returned pointer is stored directly.
        source = """
        text func make() { return `x` }
        void func f() {
            text b = make()
            log(b)
        }
        f()
        """
        ir = self._ir(parser, semantic, codegen, source)
        body = ir.split("define void @f()")[1].split("\n}")[0]
        assert "call ptr @make()" in body
        # the call's result is stored straight into b's slot, uncopied
        assert "@festina_text_own" not in body

    def test_uninitialized_text_local_is_null_initialized(self, parser, semantic, codegen):
        # Prerequisite for freeing text at all: before this section a
        # `text s` with no initializer got an alloca and no store
        # whatsoever, so its slot held genuine uninitialized garbage --
        # freeing that at scope exit would be freeing a wild pointer.
        source = """
        void func f() {
            text u
            log(u)
        }
        f()
        """
        ir = self._ir(parser, semantic, codegen, source)
        body = ir.split("define void @f()")[1].split("\n}")[0]
        assert "store ptr null" in body

    def test_text_local_reassignment_frees_the_old_buffer(self, parser, semantic, codegen):
        source = """
        text func make() { return `x` }
        void func f() {
            text a = 'lit'
            a = make()
            log(a)
        }
        f()
        """
        ir = self._ir(parser, semantic, codegen, source)
        body = ir.split("define void @f()")[1].split("\n}")[0]
        assert "call void @free(" in body

    def test_bare_interpolation_template_copies_its_only_piece(self, parser, semantic, codegen):
        # `` `${s}` `` performs no concatenation at all (claude.md #82
        # skips the two empty literal pieces), so without an explicit
        # copy it would hand back s's OWN buffer -- and since every
        # TemplateLit counts as owning, the caller would then free a
        # buffer s still points at.
        source = """
        void func f(s:text) { log(`${s}`) }
        f('x')
        """
        ir = self._ir(parser, semantic, codegen, source)
        body = ir.split("define void @f(ptr %arg.s)")[1].split("\n}")[0]
        assert "@festina_text_own" in body
        assert "@festina_str_concat" not in body

    def test_interpolation_followed_by_a_literal_piece_skips_the_copy(self, parser, semantic, codegen):
        # `` `${s}!` `` DOES concatenate, and festina_str_concat already
        # mallocs a fresh buffer from its two operands -- copying s
        # first would allocate a second buffer that nothing ever frees
        # (festina_str_concat never frees what it is handed). This was a
        # real leak, caught by LeakSanitizer while building this section.
        source = """
        void func f(s:text) { log(`${s}!`) }
        f('x')
        """
        ir = self._ir(parser, semantic, codegen, source)
        body = ir.split("define void @f(ptr %arg.s)")[1].split("\n}")[0]
        assert "@festina_str_concat" in body
        # the only festina_text_own left is the parameter's own binding
        # copy (claude.md #84), never one feeding the concat
        assert body.count("@festina_text_own") == 1

    def test_chained_template_frees_every_intermediate_concat(self, parser, semantic, codegen):
        # Each festina_str_concat copies both operands into a brand new
        # buffer and leaves them untouched, so a template chaining four
        # of them leaks three intermediates unless each is freed the
        # moment the next concat has finished copying out of it.
        source = """
        void func f(s:text) { log(`a${s}b${s}c`) }
        f('x')
        """
        ir = self._ir(parser, semantic, codegen, source)
        body = ir.split("define void @f(ptr %arg.s)")[1].split("\n}")[0]
        concats = body.count("@festina_str_concat")
        assert concats == 4
        # 3 intermediates + the final result (freed after log) + the
        # parameter's own scope-exit free
        assert body.count("call void @free(") == concats + 1

    def test_text_temporary_passed_as_an_argument_is_freed_after_the_call(
            self, parser, semantic, codegen):
        # Callees never take ownership of a text argument -- one they
        # reassign is copied at binding (claude.md #84), one they only
        # read is borrowed for the call's duration -- so the caller
        # still owns what it passed and must free it, or it leaks with
        # no binding anywhere left to free it later.
        source = """
        text func make() { return `x` }
        void func f() {
            log(make())
        }
        f()
        """
        ir = self._ir(parser, semantic, codegen, source)
        body = ir.split("define void @f()")[1].split("\n}")[0]
        assert "@festina_log_text" in body
        assert "call void @free(" in body

    def test_borrowed_text_argument_is_not_freed_by_the_caller(self, parser, semantic, codegen):
        # A bare Identifier argument is the variable's own buffer, not a
        # temporary -- freeing it here would leave the variable dangling.
        source = """
        void func f() {
            text a = 'lit'
            log(a)
        }
        f()
        """
        ir = self._ir(parser, semantic, codegen, source)
        body = ir.split("define void @f()")[1].split("\n}")[0]
        # exactly one free: a's own scope-exit free, none after the log
        assert body.count("call void @free(") == 1

    # ---- behavioural: real compiled programs ----

    def test_text_locals_globals_and_reassignment_stay_correct(self, compile_and_run):
        source = """
        text g = 'initial'
        text func wrap(s:text) { return `<${s}>` }
        void func run() {
            text a = 'hello'
            text b = wrap(a)
            text c = `${a} ${b}`
            a = wrap(b)
            log(a)
            log(b)
            log(c)
            g = wrap('global')
            log(g)
        }
        run()
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == (
            "<<hello>>\n"
            "<hello>\n"
            "hello <hello>\n"
            "<global>\n"
        )

    def test_text_in_struct_fields_arrays_and_maps_stays_correct(self, compile_and_run):
        source = """
        struct Person { name:text }
        text func wrap(s:text) { return `<${s}>` }
        void func run() {
            Person p
            p.name = 'field'
            log(p.name)
            p.name = wrap('field2')
            log(p.name)
            p.name = `tmpl ${p.name}`
            log(p.name)

            arr[text] names = ['one', wrap('two')]
            names[0] = wrap('changed')
            log(names[0])
            log(names[1])

            map[text] m = {'k1': 'v1'}
            m['k1'] = wrap('v1b')
            m['k2'] = `tmpl-${m['k1']}`
            log(m['k1'])
            log(m['k2'])
        }
        run()
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == (
            "field\n"
            "<field2>\n"
            "tmpl <field2>\n"
            "<changed>\n"
            "<two>\n"
            "<v1b>\n"
            "tmpl-<v1b>\n"
        )

    def test_repeated_concatenation_in_a_loop_builds_the_right_string(self, compile_and_run):
        # benchmarks/string_concat.f's own shape: `s` is freed and
        # replaced every iteration, so the heap stays flat instead of
        # growing by one leaked buffer per iteration.
        source = """
        void func run() {
            text s = ''
            for int i = 0, i < 200, i++ {
                s = `${s}x`
            }
            log(s)
        }
        run()
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "x" * 200 + "\n"

    def test_text_methods_on_temporaries_stay_correct(self, compile_and_run):
        source = """
        text func wrap(s:text) { return `<${s}>` }
        void func run() {
            log('room 42'.replace(/[0-9]+/, 'N'))
            log(wrap('abc').replace('b', 'Z'))
            log(`${'hello'}`.replace(/l/g, 'L'))
        }
        run()
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "room N\n<aZc>\nheLLo\n"


class TestParameterReassignmentOwnership:
    """claude.md #84: a struct/arr[T]/map[T] parameter is passed as the
    caller's own raw pointer, unretained -- a deliberately "borrowed"
    convention, since a callee that only reads its parameter has no
    reason to touch the refcount. But a callee that REASSIGNS its own
    parameter (`p = somethingElse`) runs the ordinary local-reassignment
    path, which releases whatever the binding currently holds -- and for
    a borrowed parameter that is the CALLER's live value, dropping its
    refcount to zero and freeing it out from under the caller.

    That was a real, pre-existing use-after-free (confirmed under
    AddressSanitizer with a heap-allocated local passed to a
    parameter-reassigning callee), not something introduced by the text
    work -- it was found while designing text's own parameter handling,
    which has exactly the same shape. The fix: any parameter escape
    analysis shows the callee assigns to is given its own reference at
    binding time (festina_retain for struct/arr[T]/map[T], a
    festina_text_own copy for text) and released at the callee's own
    scope exit, so the callee is never mutating a binding it doesn't own.

    Note the retain/copy is keyed on the whole `escaping` set rather
    than on reassignment alone. Every reassigned name is necessarily in
    that set (escape_analysis._walk_assign_target adds every bare
    Identifier assignment target), so this is safe but conservative --
    it also copies a text parameter that merely gets interpolated or
    passed along. See todo.md."""

    def _ir(self, parser, semantic, codegen, source, filename="main.f"):
        program = parser.parse(source, filename=filename)
        analyzed = semantic.analyze(program, filename=filename)
        return codegen.generate_ir(program, analyzed, filename=filename)

    def test_reassigned_struct_parameter_is_retained_at_binding(self, parser, semantic, codegen):
        source = """
        struct Point { x:int }
        void func mutate(p:Point) {
            Point fresh
            fresh.x = 999
            p = fresh
        }
        Point func make() { Point o o.x = 1 return o }
        void func run() {
            Point original = make()
            mutate(original)
            log(original.x)
        }
        run()
        """
        ir = self._ir(parser, semantic, codegen, source)
        body = ir.split("define void @mutate(ptr %arg.p)")[1].split("\n}")[0]
        assert "call void @festina_retain(ptr %arg.p)" in body

    def test_reassigned_text_parameter_is_copied_at_binding(self, parser, semantic, codegen):
        source = """
        void func mutate(s:text) { s = `${s}!` }
        mutate('x')
        """
        ir = self._ir(parser, semantic, codegen, source)
        body = ir.split("define void @mutate(ptr %arg.s)")[1].split("\n}")[0]
        assert "call ptr @festina_text_own(ptr %arg.s)" in body

    def test_callers_struct_survives_a_callee_reassigning_its_parameter(self, compile_and_run):
        # The regression this section exists for: before the fix,
        # `original` was freed by mutate()'s own reassignment and
        # original.x read freed memory.
        source = """
        struct Point { x:int }
        void func mutate(p:Point) {
            Point fresh
            fresh.x = 999
            p = fresh
        }
        Point func make() { Point o o.x = 1 return o }
        void func run() {
            Point original = make()
            mutate(original)
            log(original.x)
        }
        run()
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "1\n"

    def test_callers_text_survives_a_callee_reassigning_its_parameter(self, compile_and_run):
        source = """
        void func mutate(s:text) { s = `${s}!` }
        text func make() { return `hello` }
        void func run() {
            text original = make()
            mutate(original)
            log(original)
        }
        run()
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "hello\n"


class TestQueryRowAndRegexReclamation:
    """claude.md #85: two leak classes the text work (claude.md #83)
    surfaced but did not itself cause, both pre-existing and both
    unbounded rather than one-off.

    A sqlite result row is deliberately not shaped like any other
    Festina value: `festina_sqlite_collect_rows` builds each one as a
    plain `malloc(col_count * sizeof(int64_t))` with each text/blob
    column strdup'd into its slot, and no refcount header in front of
    it. Every `isinstance(t, (StructType, ArrayType, MapType))` check in
    codegen misses `TableType` entirely, so nothing ever freed a row or
    its text columns -- and `arr[People] rows = sqlite(...)` is the
    language's most central idiom, so every query leaked its whole row
    set. The array header and its pointer buffer WERE already freed
    (an arr[T] is an arr[T] whatever its element type); only the rows
    hanging off it were not.

    Because a row has no refcount header, this cannot reuse
    `festina_release`, and -- more importantly -- the array owns its
    rows outright: a `People p = rows[0]` local is only ever borrowing
    one. So the per-row free is reached solely from
    `_release_fn_for_array`'s own cascade, never from
    `_release_fn_for`, which would otherwise let an arbitrary
    TableType-typed binding double-free a row the array still owns.

    Separately, a regex compiled by a runtime `regex(...)` call was
    never freed either, so a `regex(...)` inside a loop leaked a full
    compiled automaton (several KB) per iteration. A /pattern/ literal
    is compiled once into a process-lifetime cache and must NOT be
    freed, which is exactly what separates the two: only `regex(...)`
    is an ast.Call."""

    def _ir(self, parser, semantic, codegen, source, filename="main.f"):
        program = parser.parse(source, filename=filename)
        analyzed = semantic.analyze(program, filename=filename)
        return codegen.generate_ir(program, analyzed, filename=filename)

    def test_array_of_query_rows_gets_a_per_row_release_cascade(self, parser, semantic, codegen):
        source = """
        table People { id:int  name:text }
        void func run() {
            arr[People] rows = sqlite('SELECT * FROM People')
            log(rows.length)
        }
        run()
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "@__festina_release_row_People" in ir
        # the generated row release frees the text column and the row
        row_fn = ir.split("define void @__festina_release_row_People")[1].split("\n}")[0]
        assert row_fn.count("call void @free(") == 2

    def test_row_release_frees_each_column_by_its_own_kind(
            self, parser, semantic, codegen):
        # int/float/bool columns are plain i64 slots, never heap
        # pointers -- freeing one would be freeing an integer.
        #
        # claude.md #109: a blob column is a real handle now, so it goes
        # through festina_blob_release rather than a plain @free. Doing
        # otherwise would leak its path and byte buffer and skip its
        # refcount entirely -- the same mistake claude.md #101 fixed for
        # aud/img columns, arriving here by the same route.
        source = """
        table Mixed { id:int  score:float  ok:bool  name:text  data:blob }
        void func run() {
            arr[Mixed] rows = sqlite('SELECT * FROM Mixed')
            log(rows.length)
        }
        run()
        """
        ir = self._ir(parser, semantic, codegen, source)
        row_fn = ir.split("define void @__festina_release_row_Mixed")[1].split("\n}")[0]
        # The text column, plus the row buffer itself.
        assert row_fn.count("call void @free(") == 2
        assert row_fn.count("call void @festina_blob_release(") == 1

    def test_runtime_regex_temporary_is_freed(self, parser, semantic, codegen):
        source = """
        void func run() {
            log(regex('[0-9]+').test('abc 42'))
        }
        run()
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "call void @festina_regex_free(" in ir

    def test_cached_regex_literal_is_never_freed(self, parser, semantic, codegen):
        # A /pattern/ literal is compiled once and reused for the life
        # of the process -- freeing it would leave the cache holding a
        # dangling regex_t for every later evaluation.
        source = """
        void func run() {
            log(/[0-9]+/.test('abc 42'))
        }
        run()
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "call void @festina_regex_free(" not in ir

    def test_query_rows_and_borrowed_row_stay_correct(self, compile_and_run, tmp_path):
        source = """
        table People { id:int  name:text }
        void func run() {
            sqlite('INSERT INTO People (id, name) VALUES (?, ?)', [1, 'alice'])
            sqlite('INSERT INTO People (id, name) VALUES (?, ?)', [2, 'bob'])
            arr[People] rows = sqlite('SELECT * FROM People ORDER BY id')
            log(rows.length)
            People first = rows[0]
            log(first.name)
            text copied = rows[1].name
            log(copied)
        }
        run()
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "2\nalice\nbob\n"

    def test_runtime_and_literal_regexes_stay_correct_together(self, compile_and_run):
        source = """
        void func run() {
            for int i = 0, i < 3, i++ {
                log(regex('[0-9]+').test('abc 42'))
                log('room 42'.replace(regex('[0-9]+'), 'N'))
                log('room 42'.match(regex('[0-9]+')))
                log(/[0-9]+/.test('abc 42'))
                log('room 42'.replace(/[0-9]+/, 'N'))
            }
        }
        run()
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "true\nroom N\n42\ntrue\nroom N\n" * 3


class TestOwnedRegexLocals:
    """claude.md #86: a `regex` local this scope provably owns outright
    -- one whose initializer is a `regex(...)` Call (freshly compiled by
    festina_regex_compile, so nothing else can reference it yet) and
    whose name escape analysis proves never leaves the function -- is
    freed at scope exit. Before this, `regex r = regex(p)` inside a loop
    leaked a full compiled automaton (several KB) per iteration; #85 had
    only closed the case where the regex is used as a temporary in the
    same expression that compiled it.

    Both halves of the ownership test are load-bearing, and relaxing
    either frees something still in use. A `/pattern/` literal
    initializer is a pointer into a process-lifetime cache, so freeing
    it would leave every later evaluation of that literal running
    regexec against freed memory. An escaping regex has no equivalent of
    text's copy-on-alias escape hatch -- a regex "copy" would mean
    recompiling, and the pattern string isn't retained to recompile
    from -- so it is left to leak exactly as before rather than freed
    while another binding may still point at it."""

    def _ir(self, parser, semantic, codegen, source, filename="main.f"):
        program = parser.parse(source, filename=filename)
        analyzed = semantic.analyze(program, filename=filename)
        return codegen.generate_ir(program, analyzed, filename=filename)

    def test_non_escaping_regex_local_from_a_call_is_freed(self, parser, semantic, codegen):
        source = """
        void func f() {
            regex r = regex('[0-9]+')
            log(r.test('42'))
        }
        f()
        """
        ir = self._ir(parser, semantic, codegen, source)
        body = ir.split("define void @f()")[1].split("\n}")[0]
        assert "call void @festina_regex_free(" in body

    def test_regex_local_from_a_literal_is_never_freed(self, parser, semantic, codegen):
        source = """
        void func f() {
            regex r = /[0-9]+/
            log(r.test('42'))
        }
        f()
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "call void @festina_regex_free(" not in ir

    def test_escaping_regex_local_is_not_freed(self, parser, semantic, codegen):
        source = """
        regex g = /[a-z]+/
        void func f() {
            regex r = regex('[0-9]+')
            g = r
        }
        f()
        """
        ir = self._ir(parser, semantic, codegen, source)
        body = ir.split("define void @f()")[1].split("\n}")[0]
        assert "call void @festina_regex_free(" not in body

    def test_owned_literal_and_escaping_regexes_stay_correct_together(self, compile_and_run):
        source = """
        regex g = /[0-9]+/
        void func owned() {
            for int i = 0, i < 50, i++ {
                regex r = regex('[0-9]+')
                log(r.test('abc 42'))
            }
        }
        void func fromLiteral() {
            regex r = /[a-z]+/
            log(r.test('abc'))
        }
        void func escapes() {
            regex r = regex('[0-9]+')
            g = r
            log(r.test('42'))
        }
        owned()
        fromLiteral()
        escapes()
        log(g.test('42'))
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "true\n" * 53


class TestStructTextFieldReclamation:
    """claude.md #88: a struct's own text-typed field is freed when the
    struct is, in both directions the same predicate gates.

    claude.md #83 added the field-level free itself but never widened
    the eligibility check that decides whether a struct needs field
    cleanup at all -- it still only counted struct/arr[T]/map[T] fields
    (`_struct_has_own_refcounted_field`, now
    `_struct_has_own_managed_field`). So a struct whose only managed
    field is a text one fell through both paths: stack-allocated, it was
    never scheduled for field release at all; heap-allocated, it got the
    plain generic @festina_release instead of a per-struct wrapper.
    Either way the field's buffer was never freed. Caught by an ASan run
    over a struct whose text field is reassigned in a loop."""

    def _ir(self, parser, semantic, codegen, source, filename="main.f"):
        program = parser.parse(source, filename=filename)
        analyzed = semantic.analyze(program, filename=filename)
        return codegen.generate_ir(program, analyzed, filename=filename)

    def test_struct_with_only_a_text_field_gets_a_release_wrapper(self, parser, semantic, codegen):
        source = """
        struct Person { name:text }
        Person g
        Person func make() { Person p  p.name = `x`  return p }
        g = make()
        log(g.name)
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "define void @__festina_release_struct_Person" in ir
        wrapper = ir.split("define void @__festina_release_struct_Person")[1].split("\n}")[0]
        # the text field, then the struct's own header
        assert wrapper.count("call void @free(") == 2

    def test_stack_allocated_struct_frees_its_text_field(self, parser, semantic, codegen):
        source = """
        struct Person { name:text }
        void func f() {
            Person p
            p.name = `x`
            log(p.name)
        }
        f()
        """
        ir = self._ir(parser, semantic, codegen, source)
        body = ir.split("define void @f()")[1].split("\n}")[0]
        # stack-allocated: no calloc for the struct, but the field is freed
        assert "alloca %struct.Person" in body
        assert "call void @free(" in body

    def test_struct_text_fields_stay_correct_across_stack_heap_and_global(self, compile_and_run):
        source = """
        struct Person { name:text  age:int }
        Person g
        text func upper(s:text) { return `[${s}]` }
        Person func make(n:text) { Person p  p.name = upper(n)  return p }
        void func run() {
            Person p
            p.name = 'field'
            p.name = upper(p.name)
            log(p.name)
            Person q = make('heap')
            log(q.name)
        }
        run()
        g = make('global')
        log(g.name)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "[field]\n[heap]\n[global]\n"




class TestColorAndFontTypes:
    """claude.md #91: `color` and `font` are real types, and a value of
    either is resolved to its compiled form once, at the declaration
    that names it.

    ```festina
    color brand = '#4a90d9'
    font  body  = '13px arial bold'
    fillStyle(brand)
    changeFont(body)
    ```

    A `color` compiles to a packed 0xRRGGBB integer (negative meaning
    'none'), so passing one costs a single register. A `font` compiles
    to a pointer to a static `%struct._FestinaFont` constant -- size,
    slant, weight, family -- living in the binary's read-only data, so
    declaring a font costs no runtime work at all and `changeFont()`
    passes one pointer.

    Neither type interacts with the reference-counting or
    text-ownership machinery: a colour is a plain integer, and a font
    points at a constant nothing allocates or frees.

    A colour name or font shorthand can therefore only come from a
    literal. Anything chosen at runtime uses `fillStyle(r, g, b)` or
    `changeFont(px, style, family)`, which are strictly more capable for
    that job anyway."""

    def _ir(self, parser, semantic, codegen, source, filename="main.f"):
        program = parser.parse(source, filename=filename)
        analyzed = semantic.analyze(program, filename=filename)
        return codegen.generate_ir(program, analyzed, filename=filename)

    # ---- the color type ----

    def test_a_color_declaration_compiles_to_a_packed_integer(
            self, parser, semantic, codegen):
        # 0x4a90d9 == 4886745; one immediate, not three arguments.
        ir = self._ir(parser, semantic, codegen, "color brand = '#4a90d9'")
        assert "4886745" in ir

    def test_every_css_name_and_hex_shape_resolves(self, parser, semantic, codegen):
        cases = {
            "red": 0xFF0000,
            "RED": 0xFF0000,
            "rebeccapurple": 0x663399,
            "yellowgreen": 0x9ACD32,
            "#00f": 0x0000FF,
            "#00FF7F": 0x00FF7F,
        }
        for spelling, packed in cases.items():
            ir = self._ir(parser, semantic, codegen, f"color c = '{spelling}'")
            assert str(packed) in ir, f"{spelling} did not resolve to {packed}"

    def test_none_is_the_negative_sentinel(self, parser, semantic, codegen):
        # A negative value can't be a real packed colour, so it says
        # "no colour" without needing a second field or function.
        for spelling in ["none", "transparent"]:
            ir = self._ir(parser, semantic, codegen, f"color c = '{spelling}'")
            assert "-1" in ir

    def test_fillStyle_takes_a_color_value(self, parser, semantic, codegen):
        source = """
        color brand = 'teal'
        fillStyle(brand)
        borderColor(brand)
        drawRect(0, 0, 10, 10)
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "call void @festina_set_fill_color(i64 " in ir
        assert "call void @festina_set_border_color(i64 " in ir

    def test_a_bad_colour_name_fails_at_the_declaration(
            self, parser, semantic, codegen, errors):
        with pytest.raises(errors.CompileError, match="nosuchcolour"):
            self._ir(parser, semantic, codegen, "color c = 'nosuchcolour'")
        for bad in ["#12345", "#xyz", "#"]:
            with pytest.raises(errors.CompileError, match="not a colour"):
                self._ir(parser, semantic, codegen, f"color c = '{bad}'")

    def test_a_text_literal_is_not_a_color(self, parser, semantic, errors):
        # The whole point of the type: fillStyle('red') no longer works,
        # because a colour name has to be resolved at a declaration.
        program = parser.parse("fillStyle('red')", filename="main.f")
        with pytest.raises(errors.CompileError):
            semantic.analyze(program, filename="main.f")

    def test_a_runtime_text_cannot_become_a_color(
            self, parser, semantic, codegen, errors):
        source = """
        text name = 'red'
        color c = name
        """
        with pytest.raises(errors.CompileError, match="must come from a literal"):
            self._ir(parser, semantic, codegen, source)

    def test_the_explicit_rgb_form_remains_for_runtime_colours(
            self, parser, semantic, codegen):
        source = """
        int shade = 200
        fillStyle(shade, 0, 255 - shade)
        borderColor(1, 2, 3)
        drawRect(0, 0, 10, 10)
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "call void @festina_set_border_rgb(i64 1, i64 2, i64 3)" in ir
        assert "@festina_set_fill_rgb(i64 %" in ir

    def test_colors_can_be_copied_and_passed_around(self, compile_and_run):
        # A colour is an ordinary value: assignable, passable, returnable.
        source = """
        color brand = '#4a90d9'
        color func pick(c:color) { return c }
        void func run() {
            color local = brand
            fillStyle(pick(local))
            log('ok')
        }
        run()
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "ok\n"

    # ---- the font type ----

    def test_a_font_declaration_compiles_to_a_static_record(
            self, parser, semantic, codegen):
        ir = self._ir(parser, semantic, codegen, "font body = '13px arial bold'")
        assert "%struct._FestinaFont = type { i64, i64, i64, ptr }" in ir
        # 13px, upright, bold, a real family pointer
        assert "private constant %struct._FestinaFont { i64 13, i64 0, i64 1, ptr @" in ir

    def test_font_words_may_appear_in_any_order(self, parser, semantic, codegen):
        # Every ordering of the same three facts yields the same record.
        for spec in ["arial 14px bold", "bold 14px arial", "14px arial bold",
                     "bold arial 14px"]:
            ir = self._ir(parser, semantic, codegen, f"font f = '{spec}'")
            assert "{ i64 14, i64 0, i64 1, ptr @" in ir

    def test_omitted_font_parts_are_left_alone(self, parser, semantic, codegen):
        # size only -- family stays null, meaning "don't change it"
        ir = self._ir(parser, semantic, codegen, "font f = '12px'")
        assert "{ i64 12, i64 0, i64 0, ptr null }" in ir
        # family only -- px 0 means "don't change the size"
        ir = self._ir(parser, semantic, codegen, "font f = 'monospace'")
        assert "{ i64 0, i64 0, i64 0, ptr @" in ir
        # italic and bold together
        ir = self._ir(parser, semantic, codegen, "font f = 'italic bold 9px'")
        assert "{ i64 9, i64 1, i64 1, ptr null }" in ir

    def test_identical_fonts_share_one_constant(self, parser, semantic, codegen):
        # Keyed on the resolved parts, not the source text, so differently
        # written but identical fonts collapse together.
        source = """
        font a = 'bold 13px arial'
        font b = 'arial bold 13px'
        font c = '20px serif'
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert ir.count("private constant %struct._FestinaFont") == 2

    def test_changeFont_takes_a_font_value(self, parser, semantic, codegen):
        source = """
        font body = '13px arial'
        changeFont(body)
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "call void @festina_set_font_value(ptr " in ir

    def test_an_empty_font_literal_is_rejected(self, parser, semantic, codegen, errors):
        with pytest.raises(errors.CompileError, match="says nothing about a font"):
            self._ir(parser, semantic, codegen, "font f = ''")

    def test_a_runtime_text_cannot_become_a_font(
            self, parser, semantic, codegen, errors):
        source = """
        text spec = '14px'
        font f = spec
        """
        with pytest.raises(errors.CompileError, match="must come from a literal"):
            self._ir(parser, semantic, codegen, source)

    def test_the_explicit_font_form_remains_for_runtime_sizes(
            self, parser, semantic, codegen):
        source = """
        int size = 22
        changeFont(size, null, null)
        changeFont(18, 'italic', 'serif')
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "@festina_set_font(i64 %" in ir
        assert "call void @festina_set_font(i64 18, ptr @" in ir

    def test_font_is_a_type_name_not_a_function(self, parser, semantic, errors):
        # `font(...)` was the setter before #91; it is a type now, so the
        # old spelling must not silently keep working.
        program = parser.parse("font('14px')", filename="main.f")
        with pytest.raises(errors.CompileError):
            semantic.analyze(program, filename="main.f")

    def test_a_user_function_may_not_shadow_changeFont(self, parser, semantic, errors):
        program = parser.parse("void func changeFont() { log('mine') }",
                                filename="main.f")
        with pytest.raises(errors.CompileError):
            semantic.analyze(program, filename="main.f")

    # ---- behaviour ----

    def test_measuring_alone_does_not_open_a_canvas_window(
            self, parser, semantic, codegen):
        source = """
        font body = '16px sans-serif'
        color brand = 'red'
        changeFont(body)
        fillStyle(brand)
        log(measureTextWidth('hello'))
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "call void @festina_graphics_init()" not in ir

    def test_drawing_alone_no_longer_opens_a_canvas_window(
            self, parser, semantic, codegen):
        # claude.md #95: drawing paints the offscreen canvas, which needs
        # no display at all -- render() is the one call that needs a GUI.
        source = """
        color brand = 'red'
        fillStyle(brand)
        drawRect(0, 0, 10, 10)
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "call void @festina_graphics_init()" not in ir

    def test_render_is_what_opens_the_window(self, parser, semantic, codegen):
        source = """
        color brand = 'red'
        fillStyle(brand)
        drawRect(0, 0, 10, 10)
        render()
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "call void @festina_graphics_init()" in ir

    def test_text_metrics_follow_the_declared_font(self, compile_and_run):
        source = """
        font small = '8px sans-serif'
        font big = '32px sans-serif'
        changeFont(small)
        int w1 = measureTextWidth('Hello')
        changeFont(big)
        int w2 = measureTextWidth('Hello')
        log(w2 > w1)
        changeFont(32, null, null)
        log(measureTextWidth('Hello') == w2)
        log(measureTextWidth(''))
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "true\ntrue\n0\n"


class TestCanvasStyleRendersRealPixels:
    """claude.md #89/#91, the tier the IR-level tests can't reach: that
    the declared colours actually land on the canvas. Asserting a packed
    integer appears in the IR proves the resolution, not that 'red'
    comes out red, that `#00f` expands to `#0000ff`, or that a
    `color`-typed 'none' genuinely leaves an interior unpainted.

    Same opt-in tier as the rest of TestGraphics: needs a real display
    (Xvfb is fine) plus `xdotool`/`xwd`."""

    def test_fill_border_and_none_render_the_expected_pixels(
            self, run_graphics_program, x_display):
        if not shutil.which("xwd"):
            pytest.skip("xwd isn't installed -- needed to read real canvas pixels")
        source = """
        color red = 'red'
        color green = '#00ff00'
        color blue = '#00f'
        color yellow = 'yellow'
        color black = 'black'
        color nofill = 'none'
        color purple = 'purple'
        color teal = 'teal'

        fillStyle(red)     drawRect(10, 10, 100, 100)
        fillStyle(green)   drawRect(150, 10, 100, 100)
        fillStyle(blue)    drawRect(290, 10, 100, 100)

        fillStyle(yellow)  borderColor(black)  lineWidth(10)
        drawRect(10, 150, 100, 100)

        fillStyle(nofill)  borderColor(purple)  lineWidth(8)
        drawRect(150, 150, 100, 100)

        borderColor(nofill)  fillStyle(teal)
        drawRect(290, 150, 100, 100)
        render()
        """
        proc, _stdout_path = run_graphics_program(source)
        try:
            wid = _find_window(x_display)
            time.sleep(0.5)
            got = _xwd_pixels(x_display, wid, [
                (60, 60),    # 'red'
                (200, 60),   # '#00ff00'
                (340, 60),   # '#00f' -> #0000ff
                (60, 200),   # yellow fill, inside its black border
                (10, 200),   # on that 10px black border
                (152, 200),  # purple border of the unfilled rect
                (200, 200),  # its interior -- 'none', so untouched
                (340, 200),  # teal, after borderColor('none') turned it off
                (500, 400),  # untouched canvas background
            ])
            assert got[0] == (255, 0, 0)
            assert got[1] == (0, 255, 0)
            assert got[2] == (0, 0, 255)
            assert got[3] == (255, 255, 0)
            assert got[4] == (0, 0, 0)
            assert got[5] == (128, 0, 128)
            assert got[6] == (255, 255, 255)
            assert got[7] == (0, 128, 128)
            assert got[8] == (255, 255, 255)
        finally:
            proc.terminate()

    def test_a_declared_font_changes_what_is_drawn(self, run_graphics_program, x_display):
        if not shutil.which("xwd"):
            pytest.skip("xwd isn't installed -- needed to read real canvas pixels")
        source = """
        color black = 'black'
        font tiny = '10px sans-serif'
        font huge = '48px sans-serif'
        fillStyle(black)
        changeFont(tiny)
        drawText('Hg', 20, 40)
        changeFont(huge)
        drawText('Hg', 20, 140)
        render()
        """
        proc, _stdout_path = run_graphics_program(source)
        try:
            wid = _find_window(x_display)
            time.sleep(0.5)
            small = [(x, y) for x in range(20, 70, 2) for y in range(20, 45, 2)]
            big = [(x, y) for x in range(20, 70, 2) for y in range(95, 145, 2)]
            small_inked = sum(1 for p in _xwd_pixels(x_display, wid, small)
                              if p != (255, 255, 255))
            big_inked = sum(1 for p in _xwd_pixels(x_display, wid, big)
                            if p != (255, 255, 255))
            assert small_inked > 0, "the 10px text drew nothing at all"
            assert big_inked > small_inked, (
                f"48px text inked {big_inked} sampled pixels, 10px inked "
                f"{small_inked} -- changeFont() had no effect")
        finally:
            proc.terminate()


class TestImageClipResizeAndSize:
    """claude.md #92: `img` gains `.width`/`.height` and the two methods
    a spritesheet actually needs.

    ```festina
    img sheet = 'sheet.png'
    img grass = sheet.clip(0, 0, 64, 64)   // a new image
    grass.resize(32, 32)                    // in place
    ```

    `clip` returns a NEW image and leaves the source untouched, so one
    sheet can be clipped as many times as a program likes. `resize`
    changes the image IN PLACE -- it reads as a statement, so it has to
    -- which is why an `img` value is a pointer to a small box holding
    the Cairo surface rather than the surface itself: a Cairo surface
    can't be resized in place, and boxing it means every binding
    sharing an image sees the new one.

    That box is also why img now has an ownership story (`_OwnedImage`,
    the same shape claude.md #86 uses for regex): clip() exists to be
    called repeatedly, so without scope-exit reclamation, extracting
    frames in a loop would leak a whole surface per iteration."""

    def _ir(self, parser, semantic, codegen, source, filename="main.f"):
        program = parser.parse(source, filename=filename)
        analyzed = semantic.analyze(program, filename=filename)
        return codegen.generate_ir(program, analyzed, filename=filename)

    # ---- IR level ----

    def test_width_and_height_emit_runtime_calls(self, parser, semantic, codegen):
        source = """
        img sheet = 's.png'
        log(sheet.width)
        log(sheet.height)
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "call i64 @festina_image_width(ptr" in ir
        assert "call i64 @festina_image_height(ptr" in ir

    def test_clip_and_resize_emit_runtime_calls(self, parser, semantic, codegen):
        source = """
        img sheet = 's.png'
        img tile = sheet.clip(0, 32, 64, 64)
        tile.resize(16, 16)
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert ("call ptr @festina_image_clip(ptr %" in ir
                and "i64 0, i64 32, i64 64, i64 64)" in ir)
        assert "call void @festina_image_resize(ptr %" in ir

    def test_a_clipped_local_is_freed_at_scope_exit(self, parser, semantic, codegen):
        # Without this, clipping inside a loop leaks a surface per pass.
        source = """
        void func f(sheet:img) {
            img tile = sheet.clip(0, 0, 8, 8)
            drawImage(tile, 0, 0)
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        body = ir.split("define void @f(ptr %arg.sheet)")[1].split("\n}")[0]
        assert "call void @festina_image_free(ptr" in body

    def test_a_borrowed_image_is_not_freed(self, parser, semantic, codegen):
        # `other` merely aliases an image the caller owns -- freeing it
        # here would destroy a surface still in use.
        source = """
        void func f(sheet:img) {
            img other = sheet
            drawImage(other, 0, 0)
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        body = ir.split("define void @f(ptr %arg.sheet)")[1].split("\n}")[0]
        assert "call void @festina_image_free(ptr" not in body

    def test_an_escaping_image_is_not_freed(self, parser, semantic, codegen):
        source = """
        img kept
        void func f(sheet:img) {
            img tile = sheet.clip(0, 0, 8, 8)
            kept = tile
        }
        """
        ir = self._ir(parser, semantic, codegen, source)
        body = ir.split("define void @f(ptr %arg.sheet)")[1].split("\n}")[0]
        assert "call void @festina_image_free(ptr" not in body

    # ---- type checking ----

    def test_wrong_arity_and_types_are_rejected(self, parser, semantic, errors):
        for source in [
            "img s = 'a.png'\ns.clip(0, 0, 64)",
            "img s = 'a.png'\ns.resize(1, 2, 3)",
            "img s = 'a.png'\ns.clip(0, 0, 64, 'x')",
            "img s = 'a.png'\ns.resize('a', 2)",
        ]:
            program = parser.parse(source, filename="main.f")
            with pytest.raises(errors.CompileError):
                semantic.analyze(program, filename="main.f")

    def test_an_unknown_img_field_is_rejected(self, parser, semantic, errors):
        # This branch used to be a permissive `return None`, which
        # silently accepted every typo on an img.
        program = parser.parse("img s = 'a.png'\nlog(s.widht)",
                                filename="main.f")
        with pytest.raises(errors.CompileError, match="img has no field"):
            semantic.analyze(program, filename="main.f")

    def test_a_method_referenced_without_calling_says_so(self, parser, semantic, errors):
        program = parser.parse("img s = 'a.png'\nlog(s.clip)",
                                filename="main.f")
        with pytest.raises(errors.CompileError, match="is a method on img"):
            semantic.analyze(program, filename="main.f")

    def test_a_struct_field_named_width_still_works(self, compile_and_run):
        # `.width` is only special on an img; a struct may still declare
        # one, and the object must be evaluated exactly once either way.
        source = """
        struct Box { width:int  height:int }
        Box func make() { Box b  b.width = 7  b.height = 9  return b }
        log(make().width)
        log(make().height)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "7\n9\n"

    # ---- behaviour, against a real PNG ----

    def test_clip_resize_and_dimensions_against_a_real_sheet(
            self, compile_and_run, sprite_sheet_png):
        source = f"""
        img sheet = '{sprite_sheet_png}'
        log(`${{sheet.width}}x${{sheet.height}}`)

        img tile = sheet.clip(32, 32, 32, 32)
        log(`${{tile.width}}x${{tile.height}}`)

        // clipping leaves the source alone
        log(`${{sheet.width}}x${{sheet.height}}`)

        // resize changes the image in place
        tile.resize(8, 4)
        log(`${{tile.width}}x${{tile.height}}`)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "128x64\n32x32\n128x64\n8x4\n"

    def test_resize_is_visible_through_every_binding(
            self, compile_and_run, sprite_sheet_png):
        # An img is a shared handle, so resizing through one name is
        # visible through another -- that is what "in place" means.
        source = f"""
        img sheet = '{sprite_sheet_png}'
        img a = sheet.clip(0, 0, 32, 32)
        img b = a
        a.resize(4, 4)
        log(`${{b.width}}x${{b.height}}`)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "4x4\n"

    def test_a_non_positive_size_fails_clearly(self, compile_and_run, sprite_sheet_png):
        for call in ["sheet.clip(0, 0, 0, 8)", "sheet.resize(8, 0)"]:
            source = f"""
            img sheet = '{sprite_sheet_png}'
            {call}
            """
            result = compile_and_run(source)
            assert result.returncode != 0
            assert "must both be positive" in result.stdout + result.stderr


class TestImageClipRendersRealPixels:
    """claude.md #92, the tier the rest can't reach: that clip() lifts
    the region it was actually asked for. Asserting the runtime call was
    emitted, or that the result reports 32x32, proves neither that the
    right pixels came out nor that the offset was applied in the right
    direction.

    Same opt-in tier as the rest of TestGraphics: a real display plus
    `xdotool`/`xwd`."""

    def test_each_clipped_tile_carries_its_own_colour(
            self, run_graphics_program, x_display, sprite_sheet_png):
        if not shutil.which("xwd"):
            pytest.skip("xwd isn't installed -- needed to read real canvas pixels")
        # The fixture's grid: tile (col, row) -> index row*4+col.
        # (0,0) red, (1,0) green, (1,1) cyan.
        source = f"""
        img sheet = '{sprite_sheet_png}'
        img red = sheet.clip(0, 0, 32, 32)
        img green = sheet.clip(32, 0, 32, 32)
        img cyan = sheet.clip(32, 32, 32, 32)
        drawImage(red, 10, 10)
        drawImage(green, 100, 10)
        drawImage(cyan, 200, 10)
        render()
        """
        proc, _stdout_path = run_graphics_program(source)
        try:
            wid = _find_window(x_display)
            time.sleep(0.5)
            got = _xwd_pixels(x_display, wid, [(25, 25), (115, 25), (215, 25)])
            assert got[0] == (255, 0, 0), "tile (0,0) should be red"
            assert got[1] == (0, 255, 0), "tile (1,0) should be green"
            assert got[2] == (0, 255, 255), "tile (1,1) should be cyan"
        finally:
            proc.terminate()

    def test_a_resized_tile_covers_its_new_size(
            self, run_graphics_program, x_display, sprite_sheet_png):
        if not shutil.which("xwd"):
            pytest.skip("xwd isn't installed -- needed to read real canvas pixels")
        source = f"""
        img sheet = '{sprite_sheet_png}'
        img big = sheet.clip(0, 0, 32, 32)
        big.resize(96, 96)
        drawImage(big, 10, 10)
        render()
        """
        proc, _stdout_path = run_graphics_program(source)
        try:
            wid = _find_window(x_display)
            time.sleep(0.5)
            # inside the scaled-up tile, and past where the 32x32
            # original would have ended
            got = _xwd_pixels(x_display, wid, [(20, 20), (90, 90), (120, 120)])
            assert got[0] == (255, 0, 0)
            assert got[1] == (255, 0, 0), "the tile did not scale up to 96x96"
            assert got[2] == (255, 255, 255), "it scaled past its requested size"
        finally:
            proc.terminate()


class TestMathFileAndTime:
    """claude.md #93: the standard-library gaps that needed no new
    dependency at all -- `-lm` and libc are already on every link line,
    and Cairo's PNG *writer* is compiled into the same library whose
    reader `loadImage` already uses.

    `Math` had only floor/ceil/round/trunc, so a program couldn't take a
    square root or a sine; there was no way to read or write a file, and
    no way to ask the time even though the timer machinery already calls
    clock_gettime. Rounding still answers with an `int` (claude.md #56);
    everything added here answers in `float`, because "which integer" and
    "which real number" are different questions and conflating them would
    make `Math.sqrt(2.0)` silently an int."""

    def _ir(self, parser, semantic, codegen, source, filename="main.f"):
        program = parser.parse(source, filename=filename)
        analyzed = semantic.analyze(program, filename=filename)
        return codegen.generate_ir(program, analyzed, filename=filename)

    # ---- math ----

    def test_float_math_uses_llvm_intrinsics_where_they_exist(
            self, parser, semantic, codegen):
        # Real intrinsics optimize and constant-fold in ways an opaque
        # libm call can't.
        ir = self._ir(parser, semantic, codegen, "log(Math.sqrt(2.0))")
        assert "call double @llvm.sqrt.f64(double" in ir
        ir = self._ir(parser, semantic, codegen, "log(Math.pow(2.0, 8.0))")
        assert "call double @llvm.pow.f64(double" in ir

    def test_math_without_an_intrinsic_falls_back_to_libm(
            self, parser, semantic, codegen):
        # -lm is already unconditional on every link line.
        ir = self._ir(parser, semantic, codegen, "log(Math.tan(1.0))")
        assert "call double @tan(double" in ir

    def test_rounding_still_returns_int_and_the_rest_returns_float(
            self, parser, semantic, codegen, errors):
        # An int-typed binding accepts floor() and rejects sqrt().
        self._ir(parser, semantic, codegen, "int a = Math.floor(3.7)\nlog(a)")
        self._ir(parser, semantic, codegen, "float b = Math.sqrt(9.0)\nlog(b)")
        with pytest.raises(errors.CompileError):
            self._ir(parser, semantic, codegen, "int c = Math.sqrt(9.0)")

    def test_math_constants_are_compile_time_values(self, parser, semantic, codegen):
        ir = self._ir(parser, semantic, codegen, "log(Math.PI)")
        # emitted as a raw double bit pattern, not a runtime call
        assert "0x400921FB54442D18" in ir
        assert "@festina_" not in ir.split("define void @__festina_main")[1].split("\n}")[0].replace(
            "festina_log_float", "")

    def test_math_arity_and_types_are_checked(self, parser, semantic, errors):
        for source in ["log(Math.sqrt())", "log(Math.sqrt(1.0, 2.0))",
                       "log(Math.pow(2.0))", "log(Math.sqrt(4))"]:
            program = parser.parse(source, filename="main.f")
            with pytest.raises(errors.CompileError):
                semantic.analyze(program, filename="main.f")

    def test_an_unknown_math_member_is_rejected(self, parser, semantic, errors):
        program = parser.parse("log(Math.TAU)", filename="main.f")
        with pytest.raises(errors.CompileError, match="Math has no member"):
            semantic.analyze(program, filename="main.f")

    def test_a_math_function_named_without_calling_says_so(
            self, parser, semantic, errors):
        program = parser.parse("log(Math.sqrt)", filename="main.f")
        with pytest.raises(errors.CompileError, match="is a function"):
            semantic.analyze(program, filename="main.f")

    def test_math_computes_the_right_answers(self, compile_and_run):
        source = """
        log(Math.sqrt(16.0))
        log(Math.pow(2.0, 10.0))
        log(Math.abs(0.0 - 5.5))
        log(Math.min(3.0, 7.0))
        log(Math.max(3.0, 7.0))
        log(Math.floor(3.7))
        log(Math.sin(0.0))
        log(Math.cos(0.0))
        log(Math.log(Math.E))
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "4\n1024\n5.5\n3\n7\n3\n0\n1\n1\n"

    def test_random_stays_in_range_and_varies(self, compile_and_run):
        # [0, 1) specifically: 1.0 would break the standard
        # `arr[floor(random() * length)]` idiom.
        source = """
        bool inRange = true
        float first = Math.random()
        bool varied = false
        for int i = 0, i < 200, i++ {
            float r = Math.random()
            if r < 0.0 || r >= 1.0 { inRange = false }
            if r != first { varied = true }
        }
        log(inRange)
        log(varied)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "true\ntrue\n"

    # ---- files, via blob since claude.md #109 ----

    def test_file_round_trip(self, compile_and_run, tmp_path):
        # claude.md #109: the same round trip claude.md #93's five free
        # functions did, asked of the value that already holds the path.
        path = str(tmp_path / "note.txt")
        source = f"""
        blob f = '{path}'
        log(f.write('hello'))
        log(f.append(' world'))
        log(f.toText())
        log(f.exists())
        log(f.delete())
        log(f.exists())
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "true\ntrue\nhello world\ntrue\ntrue\nfalse\n"

    def test_a_missing_file_is_an_ordinary_condition_not_a_crash(
            self, compile_and_run, tmp_path):
        # A missing file is something to test for, the same reasoning
        # claude.md #57 applies to division by zero. claude.md #109
        # keeps that rule: declaring a blob on a path that is not there
        # yields an empty blob rather than failing.
        missing = str(tmp_path / "nope.txt")
        source = f"""
        blob f = '{missing}'
        log(f.toText() == '')
        log(f.exists())
        log(f.delete())
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "true\nfalse\nfalse\n"

    def test_writing_somewhere_impossible_returns_false(self, compile_and_run):
        source = ("blob f = '/definitely/not/a/directory/x.txt'\n"
                  "log(f.write('hi'))")
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "false\n"

    def test_a_file_round_trips_through_text_operations(
            self, compile_and_run, tmp_path):
        # The value toText() hands back is an ordinary owned text, so it
        # composes with everything else (claude.md #83).
        path = str(tmp_path / "data.txt")
        source = f"""
        blob f = '{path}'
        f.write('a,b,c')
        text body = f.toText()
        log(body.replace(/,/g, '-'))
        log(`${{body}}!`)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "a-b-c\na,b,c!\n"

    def test_the_removed_file_functions_name_their_replacement(
            self, parser, semantic, errors):
        # claude.md #109: removed rather than aliased, each with an
        # error that shows the blob spelling.
        for name, call in [("readFile", "readFile('x')"),
                           ("writeFile", "writeFile('x', 'y')"),
                           ("appendFile", "appendFile('x', 'y')"),
                           ("fileExists", "fileExists('x')"),
                           ("deleteFile", "deleteFile('x')")]:
            program = parser.parse(f"log({call})")
            with pytest.raises(errors.CompileError, match=name) as excinfo:
                semantic.analyze(program)
            assert "blob" in str(excinfo.value)

    # ---- time ----

    def test_now_and_formatTime(self, compile_and_run):
        source = """
        int t = now()
        log(t > 1700000000000)
        log(formatTime(0, '%Y'))
        """
        result = compile_and_run(source, env={"TZ": "UTC"})
        assert result.returncode == 0
        assert result.stdout == "true\n1970\n"

    def test_now_advances(self, compile_and_run):
        source = """
        int a = now()
        int total = 0
        for int i = 0, i < 2000000, i++ { total = total + i }
        int b = now()
        log(b >= a)
        log(total > 0)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "true\ntrue\n"


def _decode_png(path):
    """Minimal PNG decoder -> (width, height, pixel(x, y) -> (r, g, b)).

    claude.md #93's saveCanvas is only really verified by reading the
    file back and finding the drawing in it -- "the call returned true"
    proves the plumbing, not that the canvas was captured rather than a
    blank surface. Written out here (zlib plus the five PNG filter
    types) because the compiler has no image library and neither should
    its tests; the same reasoning as the sprite_sheet_png fixture, which
    encodes rather than decodes.
    """
    import struct
    import zlib

    data = open(path, "rb").read()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{path} is not a PNG"
    pos, idat, width, height, ctype = 8, b"", 0, 0, 0
    while pos < len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        tag = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + length]
        if tag == b"IHDR":
            width, height, _depth, ctype = struct.unpack(">IIBB", chunk[:10])
        elif tag == b"IDAT":
            idat += chunk
        pos += 12 + length
    bpp = {2: 3, 6: 4}[ctype]
    stride = width * bpp
    raw = zlib.decompress(idat)
    out, prev, i = bytearray(), bytearray(stride), 0
    for _y in range(height):
        filt = raw[i]
        i += 1
        line = bytearray(raw[i:i + stride])
        i += stride
        for x in range(stride):
            a = line[x - bpp] if x >= bpp else 0
            b = prev[x]
            c = prev[x - bpp] if x >= bpp else 0
            if filt == 1:
                line[x] = (line[x] + a) & 255
            elif filt == 2:
                line[x] = (line[x] + b) & 255
            elif filt == 3:
                line[x] = (line[x] + (a + b) // 2) & 255
            elif filt == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[x] = (line[x] + pred) & 255
        out += line
        prev = line

    def pixel(x, y):
        off = y * stride + x * bpp
        return tuple(out[off:off + 3])

    return width, height, pixel


class TestSaveCanvas:
    """claude.md #93: saveCanvas() writes the canvas to a PNG through
    Cairo's own writer -- compiled into the very library whose reader
    `loadImage` already uses, so it costs no new dependency and no
    encoder to write.

    It saves the BACKING surface rather than the window, so the result
    is what the program drew rather than whatever happened to be
    unobscured on screen. Needs a display only because the canvas has to
    exist at all."""

    def test_the_saved_png_contains_what_was_drawn(self, compile_and_run, tmp_path):
        # claude.md #95: no display, no window, no event loop. This used
        # to need all three -- a program whose whole job was writing a
        # PNG still opened a window and then blocked forever in the
        # event loop, which is exactly what the render() split fixed.
        out = str(tmp_path / "canvas.png")
        source = f"""
        color red = 'red'
        color blue = 'blue'
        fillStyle(red)
        drawRect(0, 0, 40, 40)
        fillStyle(blue)
        drawRect(40, 0, 40, 40)
        log(saveCanvas('{out}'))
        """
        result = compile_and_run(source, env={"DISPLAY": ""})
        assert result.returncode == 0, (
            "drawing and saving should need no display at all: "
            + result.stdout + result.stderr)
        assert result.stdout == "true\n"
        width, height, pixel = _decode_png(out)
        assert (width, height) == (800, 600)
        assert pixel(20, 20) == (255, 0, 0), "the red rect is missing"
        assert pixel(60, 20) == (0, 0, 255), "the blue rect is missing"
        assert pixel(400, 300) == (255, 255, 255), "background should be white"

    def test_an_unwritable_path_returns_false(self, compile_and_run):
        source = """
        drawRect(0, 0, 10, 10)
        log(saveCanvas('/definitely/not/a/directory/out.png'))
        """
        result = compile_and_run(source, env={"DISPLAY": ""})
        assert result.returncode == 0
        assert result.stdout == "false\n"


class TestScalarQueries:
    """claude.md #94: sqliteInt/sqliteFloat/sqliteText take one value out
    of a query without a `table` declaration to hold it.

    That declaration isn't free: a `table` CREATES a real table
    (claude.md #28-31's automatic schema sync), so before this, asking
    for a `count(*)` or a single `json_extract` meant leaving a
    throwaway table behind in the database forever. These share the
    prepare-and-bind path `sqlite()` already uses; only the stepping
    differs.

    A query matching no rows, or whose value is SQL NULL, answers with
    Festina's own null for that type -- an ordinary result to test for,
    the same treatment claude.md #57 gives division by zero."""

    def test_scalars_read_values_without_a_result_table(self, compile_and_run):
        source = """
        table Post { id:int  title:text  score:float }
        sqlite(`DELETE FROM Post`)
        sqlite(`INSERT INTO Post (id, title, score) VALUES (?, ?, ?)`, [1, 'alpha', 1.5])
        sqlite(`INSERT INTO Post (id, title, score) VALUES (?, ?, ?)`, [2, 'beta', 2.5])
        log(sqliteInt(`SELECT count(*) FROM Post`))
        log(sqliteText(`SELECT title FROM Post WHERE id = ?`, [2]))
        log(sqliteFloat(`SELECT sum(score) FROM Post`))
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "2\nbeta\n4\n"

    def test_no_matching_row_is_null(self, compile_and_run):
        source = """
        table Post { id:int  title:text }
        sqlite(`DELETE FROM Post`)
        log(sqliteText(`SELECT title FROM Post WHERE id = ?`, [99]) == null)
        log(sqliteInt(`SELECT id FROM Post WHERE id = ?`, [99]) == null)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "true\ntrue\n"

    def test_a_scalar_query_creates_no_extra_table(self, compile_and_run, tmp_path):
        # The gap this exists to close: the only table in the database
        # afterwards should be the one actually declared.
        source = """
        table Post { id:int }
        sqlite(`DELETE FROM Post`)
        sqlite(`INSERT INTO Post (id) VALUES (?)`, [1])
        log(sqliteInt(`SELECT count(*) FROM Post`))
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "1\n"
        import sqlite3
        db = sqlite3.connect(str(tmp_path / "festina.sqlite"))
        names = sorted(r[0] for r in db.execute(
            "select name from sqlite_master where type='table' "
            "and name not like 'sqlite_%'"))
        db.close()
        assert names == ["Post"]

    def test_json1_works_through_scalar_queries(self, compile_and_run):
        # SQLite's JSON1 needs no compiler feature at all -- it is
        # ordinary SQL. This locks in that it stays reachable.
        source = """
        log(sqliteInt(`SELECT json_extract('{"n":42}','$.n')`))
        log(sqliteText(`SELECT json_extract('{"name":"ada"}','$.name')`))
        log(sqliteInt(`SELECT json_array_length('[1,2,3]')`))
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "42\nada\n3\n"

    def test_fts5_full_text_search_works_end_to_end(self, compile_and_run):
        # Likewise FTS5: a virtual table, an index rebuild and a ranked
        # MATCH query, all through SQL the language already passes
        # through untouched.
        source = """
        table Post { id:int  title:text  body:text }
        sqlite(`DELETE FROM Post`)
        sqlite(`INSERT INTO Post (id, title, body) VALUES (?, ?, ?)`,
               [1, 'Compilers', 'a compiler turns source into machine code'])
        sqlite(`INSERT INTO Post (id, title, body) VALUES (?, ?, ?)`,
               [2, 'Gardening', 'planting tomatoes in spring soil'])
        sqlite(`INSERT INTO Post (id, title, body) VALUES (?, ?, ?)`,
               [3, 'Machines', 'machine learning and machine code differ'])
        sqlite(`DROP TABLE IF EXISTS PostSearch`)
        sqlite(`CREATE VIRTUAL TABLE PostSearch USING fts5(title, body, content='Post', content_rowid='id')`)
        sqlite(`INSERT INTO PostSearch(PostSearch) VALUES('rebuild')`)
        log(sqliteInt(`SELECT count(*) FROM PostSearch WHERE PostSearch MATCH ?`, ['machine']))
        log(sqliteText(`SELECT title FROM PostSearch WHERE PostSearch MATCH ? ORDER BY rank`, ['tomatoes']))
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "2\nGardening\n"


class TestCanvasPathsTransformsAndGradients:
    """claude.md #94: the canvas gains arbitrary shapes.

    Before this it could draw exactly three things -- a rectangle, a
    circle and a line of text -- with no way to express a triangle, a
    polygon, a curve, a rotated anything, a gradient or transparency.

    Every drawing call builds its own short-lived Cairo context, so the
    transform lives outside all of them and is applied to each; that is
    what makes `translate(100, 0)` affect the NEXT drawRect rather than
    nothing. `saveState`/`restoreState` save the whole drawing state,
    matching the canvas `save()`/`restore()` they mirror."""

    def _ir(self, parser, semantic, codegen, source, filename="main.f"):
        program = parser.parse(source, filename=filename)
        analyzed = semantic.analyze(program, filename=filename)
        return codegen.generate_ir(program, analyzed, filename=filename)

    def test_path_and_transform_calls_are_emitted(self, parser, semantic, codegen):
        source = """
        beginPath()
        moveTo(0, 0)
        lineTo(10, 10)
        curveTo(1, 2, 3, 4, 5, 6)
        closePath()
        fillPath()
        strokePath()
        translate(5, 5)
        rotate(45.0)
        scale(2.0, 2.0)
        resetTransform()
        saveState()
        restoreState()
        fillAlpha(0.5)
        """
        ir = self._ir(parser, semantic, codegen, source)
        for fn in ["begin_path", "move_to", "line_to", "curve_to", "close_path",
                   "fill_path", "stroke_path", "translate", "rotate", "scale",
                   "reset_transform", "save_state", "restore_state", "set_alpha"]:
            assert f"@festina_{fn}(" in ir, f"festina_{fn} not emitted"

    def test_gradients_take_color_values(self, parser, semantic, codegen):
        source = """
        color a = 'red'
        color b = 'blue'
        fillLinearGradient(0, 0, a, 100, 0, b)
        fillRadialGradient(50, 50, 40, a, b)
        drawRect(0, 0, 10, 10)
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "@festina_fill_linear_gradient(" in ir
        assert "@festina_fill_radial_gradient(" in ir

    def test_a_gradient_rejects_a_non_color(self, parser, semantic, errors):
        program = parser.parse(
            "fillLinearGradient(0, 0, 'red', 100, 0, 'blue')", filename="main.f")
        with pytest.raises(errors.CompileError):
            semantic.analyze(program, filename="main.f")

    def test_path_calls_do_not_open_a_canvas(self, parser, semantic, codegen):
        # claude.md #95: a path paints the offscreen canvas like any
        # other drawing -- only render() needs a window.
        ir = self._ir(parser, semantic, codegen, "beginPath()\nmoveTo(0,0)\nfillPath()")
        assert "call void @festina_graphics_init()" not in ir

    def test_transforms_and_state_open_nothing(self, parser, semantic, codegen):
        # Pure state, exactly like claude.md #89's own style setters --
        # a program that only sets a transform shouldn't get a window.
        source = """
        saveState()
        translate(5, 5)
        rotate(45.0)
        scale(2.0, 2.0)
        fillAlpha(0.5)
        restoreState()
        resetTransform()
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "call void @festina_graphics_init()" not in ir

    def test_building_a_path_with_none_open_fails_clearly(self, compile_and_run):
        result = compile_and_run("moveTo(10, 10)")
        assert result.returncode != 0
        assert "beginPath" in result.stdout + result.stderr

    def test_restoring_more_than_was_saved_fails_clearly(self, compile_and_run):
        # No drawing, so no display needed -- restoreState is pure state.
        result = compile_and_run("restoreState()")
        assert result.returncode != 0
        assert "restoreState" in result.stdout + result.stderr


class TestCanvasPathsRenderRealPixels:
    """claude.md #94, the tier the IR tests can't reach: that a path is
    a real shape rather than its bounding box, that a transform actually
    moves what is drawn next, that a gradient interpolates, and that
    alpha blends. Needs a display plus `xdotool`/`xwd`."""

    def test_paths_transforms_gradients_and_alpha_render(
            self, run_graphics_program, x_display):
        if not shutil.which("xwd"):
            pytest.skip("xwd isn't installed -- needed to read real canvas pixels")
        source = """
        color red = 'red'
        color blue = 'blue'
        color black = 'black'
        color none = 'none'

        fillStyle(red)
        beginPath()
        moveTo(50, 50)
        lineTo(150, 50)
        lineTo(100, 140)
        closePath()
        fillPath()

        fillStyle(none)
        borderColor(black)
        lineWidth(6)
        beginPath()
        moveTo(200, 50)
        lineTo(300, 140)
        strokePath()

        saveState()
        translate(400, 40)
        fillStyle(blue)
        drawRect(0, 0, 60, 60)
        restoreState()
        fillStyle(blue)
        drawRect(400, 200, 20, 20)

        fillLinearGradient(50, 300, red, 250, 300, blue)
        drawRect(50, 280, 200, 60)

        fillStyle(red)
        fillAlpha(0.5)
        drawRect(500, 280, 80, 60)
        render()
        """
        proc, _stdout_path = run_graphics_program(source)
        try:
            wid = _find_window(x_display)
            time.sleep(0.5)
            got = _xwd_pixels(x_display, wid, [
                (100, 80),   # inside the triangle
                (55, 130),   # inside its bounding box but OUTSIDE the shape
                (250, 95),   # on the stroked diagonal
                (430, 70),   # the translated square
                (405, 205),  # drawn after restoreState -- untransformed
                (55, 300),   # gradient, red end
                (245, 300),  # gradient, blue end
                (540, 300),  # 50% red over white
            ])
            assert got[0] == (255, 0, 0), "the triangle did not fill"
            assert got[1] == (255, 255, 255), (
                "a corner outside the triangle was filled -- the path was "
                "treated as its bounding box")
            assert got[2] == (0, 0, 0), "the stroked path is missing"
            assert got[3] == (0, 0, 255), "translate() did not move drawRect"
            assert got[4] == (0, 0, 255), "restoreState() did not undo translate()"
            assert got[5][0] > 200 and got[5][2] < 60, "gradient start is not red"
            assert got[6][2] > 200 and got[6][0] < 60, "gradient end is not blue"
            assert got[7] == (255, 127, 127), "50% red over white should blend"
        finally:
            proc.terminate()


class TestRenderClearAndHeadless:
    """claude.md #95: drawing paints an offscreen canvas; `render()` is
    the one call that puts it on screen.

    That split does three things at once. A program that draws and saves
    a PNG never opens a window, never enters an event loop, and runs
    with no display at all -- previously impossible, since any drawing
    forced a window. "Does this program need a GUI?" becomes answerable
    by looking for `render()`. And a frame costs one blit instead of one
    per shape: every draw call used to flush the whole canvas to X, so
    2000 rectangles took ~1.6s; batched behind one render() they take
    ~1ms.

    `clearCanvas()` is the other half of animation -- without it a canvas
    could only ever accumulate, so nothing could move."""

    def _ir(self, parser, semantic, codegen, source, filename="main.f"):
        program = parser.parse(source, filename=filename)
        analyzed = semantic.analyze(program, filename=filename)
        return codegen.generate_ir(program, analyzed, filename=filename)

    def test_render_and_clears_emit_their_calls(self, parser, semantic, codegen):
        source = """
        clearCanvas()
        clearRect(10, 10, 20, 20)
        render()
        """
        ir = self._ir(parser, semantic, codegen, source)
        assert "call void @festina_clear_canvas()" in ir
        assert "call void @festina_clear_rect(i64 10, i64 10, i64 20, i64 20)" in ir
        assert "call void @festina_render()" in ir

    def test_clearing_alone_does_not_open_a_window(self, parser, semantic, codegen):
        ir = self._ir(parser, semantic, codegen, "clearCanvas()\nclearRect(0,0,5,5)")
        assert "call void @festina_graphics_init()" not in ir

    def test_saving_a_canvas_needs_no_display(self, compile_and_run, tmp_path):
        # The capability the split exists for.
        out = str(tmp_path / "out.png")
        source = f"""
        color red = 'red'
        fillStyle(red)
        drawRect(0, 0, 30, 30)
        log(saveCanvas('{out}'))
        log('exited on its own')
        """
        result = compile_and_run(source, env={"DISPLAY": ""})
        assert result.returncode == 0
        assert result.stdout == "true\nexited on its own\n"

    def test_clear_canvas_erases_everything(self, compile_and_run, tmp_path):
        out = str(tmp_path / "cleared.png")
        source = f"""
        color red = 'red'
        fillStyle(red)
        drawRect(0, 0, 100, 100)
        clearCanvas()
        log(saveCanvas('{out}'))
        """
        result = compile_and_run(source, env={"DISPLAY": ""})
        assert result.returncode == 0
        _w, _h, pixel = _decode_png(out)
        assert pixel(50, 50) == (255, 255, 255), "clearCanvas() left the rect behind"

    def test_clear_rect_erases_only_its_region(self, compile_and_run, tmp_path):
        out = str(tmp_path / "partial.png")
        source = f"""
        color red = 'red'
        fillStyle(red)
        drawRect(0, 0, 200, 200)
        clearRect(50, 50, 40, 40)
        log(saveCanvas('{out}'))
        """
        result = compile_and_run(source, env={"DISPLAY": ""})
        assert result.returncode == 0
        _w, _h, pixel = _decode_png(out)
        assert pixel(60, 60) == (255, 255, 255), "clearRect() did not erase its region"
        assert pixel(150, 150) == (255, 0, 0), "clearRect() erased outside its region"
        assert pixel(10, 10) == (255, 0, 0), "clearRect() erased outside its region"

    def test_drawing_survives_until_render(self, run_graphics_program, x_display):
        # Drawing before the window exists is not an error -- the canvas
        # is offscreen, and render() presents whatever is on it.
        if not shutil.which("xwd"):
            pytest.skip("xwd isn't installed -- needed to read real canvas pixels")
        source = """
        color red = 'red'
        fillStyle(red)
        drawRect(10, 10, 60, 60)
        render()
        """
        proc, _stdout_path = run_graphics_program(source)
        try:
            wid = _find_window(x_display)
            time.sleep(0.5)
            got = _xwd_pixels(x_display, wid, [(30, 30), (400, 300)])
            assert got[0] == (255, 0, 0), "what was drawn before render() was lost"
            assert got[1] == (255, 255, 255)
        finally:
            proc.terminate()


class TestArrayMethods:
    """claude.md #96: push/pop/shift/unshift/splice, JS-shaped.

    Before these, an array's length was fixed at construction and
    writing past the end was an unchecked heap overflow -- so there was
    no way to express a list that grows, which is most of what a program
    does with one.

    The ownership half matters as much as the resizing: `xs.push(s)`
    follows the same rule `xs[i] = s` already does (claude.md #80/#83),
    retaining a struct/array/map element and copying a text one unless
    its source is already owning. Removal transfers rather than
    releases -- pop/shift hand the element back and splice hands it to
    the returned array."""

    def test_push_and_pop(self, compile_and_run):
        source = """
        arr[int] xs = [1, 2, 3]
        log(xs.push(4))
        log(xs.length)
        log(xs.pop())
        log(xs.length)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "4\n4\n4\n3\n"

    def test_shift_and_unshift(self, compile_and_run):
        source = """
        arr[int] xs = [1, 2, 3]
        log(xs.shift())
        log(xs[0])
        log(xs.unshift(99))
        log(xs[0])
        log(xs.length)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "1\n2\n3\n99\n3\n"

    def test_splice_removes_and_returns(self, compile_and_run):
        source = """
        arr[int] xs = [10, 20, 30, 40, 50]
        arr[int] cut = xs.splice(1, 2)
        log(`${cut.length}: ${cut[0]},${cut[1]}`)
        log(`${xs.length}: ${xs[0]},${xs[1]},${xs[2]}`)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "2: 20,30\n3: 10,40,50\n"

    def test_splice_clamps_like_javascript(self, compile_and_run):
        # A negative start counts back from the end, and an oversized
        # count clamps -- so splice(i, 1) at a boundary is a no-op
        # rather than a crash.
        source = """
        arr[int] a = [1, 2, 3, 4, 5]
        arr[int] tail = a.splice(-2, 5)
        log(`${tail.length}: ${tail[0]},${tail[1]}`)
        log(a.length)
        arr[int] b = [1, 2]
        arr[int] none = b.splice(10, 3)
        log(none.length)
        log(b.length)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "2: 4,5\n3\n0\n2\n"

    def test_popping_an_empty_array_is_null(self, compile_and_run):
        # Null, not zero: an empty pop() must be distinguishable from
        # popping a real 0.
        source = """
        arr[int] e = []
        log(e.pop() == null)
        log(e.shift() == null)
        arr[int] z = [0]
        log(z.pop() == null)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "true\ntrue\nfalse\n"

    def test_growing_from_empty(self, compile_and_run):
        source = """
        arr[int] xs = []
        for int i = 0, i < 100, i++ { xs.push(i * 2) }
        log(xs.length)
        log(xs[0])
        log(xs[99])
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "100\n0\n198\n"

    def test_text_elements_are_owned_not_shared(self, compile_and_run):
        # Pushing a text binding must copy it (claude.md #83), or the
        # array and the variable would share one buffer.
        source = """
        arr[text] names = []
        text n = 'first'
        names.push(n)
        n = 'changed'
        log(names[0])
        log(names.pop())
        log(names.length)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "first\nfirst\n0\n"

    def test_struct_elements_survive_push_and_pop(self, compile_and_run):
        source = """
        struct P { x:int }
        P func make(v:int) { P p  p.x = v  return p }
        arr[P] ps = []
        ps.push(make(7))
        ps.push(make(9))
        log(ps.length)
        P last = ps.pop()
        log(last.x)
        log(ps[0].x)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "2\n9\n7\n"

    def test_wrong_arity_and_types_are_rejected(self, parser, semantic, errors):
        for source in [
            "arr[int] xs = [1]\nxs.push()",
            "arr[int] xs = [1]\nxs.push(1, 2)",
            "arr[int] xs = [1]\nxs.pop(1)",
            "arr[int] xs = [1]\nxs.push('a')",
            "arr[int] xs = [1]\nxs.splice(1)",
            "arr[int] xs = [1]\nxs.splice(1, 'a')",
        ]:
            program = parser.parse(source, filename="main.f")
            with pytest.raises(errors.CompileError):
                semantic.analyze(program, filename="main.f")

    def test_a_queue_round_trips(self, compile_and_run):
        # The shape a game's entity list actually takes.
        source = """
        arr[text] queue = []
        queue.push('a')
        queue.push('b')
        queue.push('c')
        log(queue.shift())
        queue.push('d')
        log(queue.shift())
        log(`${queue.length}: ${queue[0]},${queue[1]}`)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "a\nb\n2: c,d\n"



class TestArrayIndexOf:
    """claude.md #97: xs.indexOf(v) -- the first index holding v, or -1.

    -1 rather than null because the answer is an index and every use of
    one is a comparison or a splice argument, both of which read
    naturally against -1 and neither of which would against null. It is
    also what JavaScript's own indexOf answers, and claude.md #26's
    arrays are JS-shaped.

    The comparison is by 8-byte slot for everything except text, which
    compares by content -- two equal strings are almost always two
    different buffers under claude.md #83's copy-on-alias rule, so
    identity would make indexOf useless for exactly the element type
    it's most often used with."""

    def test_int_elements_found_and_missing(self, compile_and_run):
        source = """
        arr[int] xs = [10, 20, 30]
        log(xs.indexOf(30))
        log(xs.indexOf(10))
        log(xs.indexOf(99))
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "2\n0\n-1\n"

    def test_text_elements_compare_by_content_not_identity(self, compile_and_run):
        # Every one of these needles is a DIFFERENT buffer from the
        # array's own element (claude.md #83 copies text on binding), so
        # a pointer comparison would answer -1 for all three.
        source = """
        text func bang(s:text) {
            return s + '!'
        }
        arr[text] names = ['ada!', 'grace!', 'alan!']
        text who = 'grace'
        log(names.indexOf('alan!'))
        log(names.indexOf(bang(who)))
        log(names.indexOf(`${who}!`))
        log(names.indexOf(who + '!'))
        log(names.indexOf('nobody'))
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "2\n1\n1\n1\n-1\n"

    def test_first_match_wins_and_empty_is_minus_one(self, compile_and_run):
        source = """
        arr[int] dupes = [5, 5, 5]
        log(dupes.indexOf(5))
        arr[int] empty = []
        log(empty.indexOf(1))
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "0\n-1\n"

    def test_bool_elements(self, compile_and_run):
        # arr[bool] is the one element type whose slot is 1 byte wide,
        # not 8 -- see _elem_size.
        source = """
        arr[bool] flags = [true, true, false]
        log(flags.indexOf(false))
        log(flags.indexOf(true))
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "2\n0\n"

    def test_float_elements(self, compile_and_run):
        source = """
        arr[float] fs = [1.5, 2.5]
        log(fs.indexOf(2.5))
        log(fs.indexOf(9.5))
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "1\n-1\n"

    def test_struct_elements_match_by_identity(self, compile_and_run):
        # Two structs with identical fields are two distinct values --
        # claude.md #79's arr[T] holds each element's own header
        # pointer, so this is the same "aliasing means sharing one
        # address" identity every other struct operation uses.
        source = """
        struct Point { x:int y:int }
        Point a
        a.x = 1
        a.y = 2
        Point b
        b.x = 1
        b.y = 2
        arr[Point] ps = [a, b]
        log(ps.indexOf(a))
        log(ps.indexOf(b))
        Point c
        c.x = 1
        c.y = 2
        log(ps.indexOf(c))
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "0\n1\n-1\n"

    def test_composes_with_splice(self, compile_and_run):
        # The reason -1 is the right answer shape: this is what removal
        # by value looks like, and it needs no separate "was it found"
        # dance for the found case.
        source = """
        arr[text] queue = ['a', 'b', 'c']
        arr[text] gone = queue.splice(queue.indexOf('b'), 1)
        log(gone[0])
        log(`${queue.length}: ${queue[0]},${queue[1]}`)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "b\n2: a,c\n"

    def test_wrong_arity_or_element_type_is_a_compile_error(self, parser, semantic, errors):
        for source in [
            "arr[int] xs = [1]\nlog(xs.indexOf())",
            "arr[int] xs = [1]\nlog(xs.indexOf(1, 2))",
            "arr[int] xs = [1]\nlog(xs.indexOf('a'))",
        ]:
            program = parser.parse(source, filename="main.f")
            with pytest.raises(errors.CompileError):
                semantic.analyze(program, filename="main.f")


class TestBoolArrayElementStride:
    """claude.md #97: arr[bool] is the one array whose element slot is
    a single byte -- bool lowers to i8 (see _llvm_type's own note on
    why it isn't i1), while int/float/text/struct/arr/map all lower to
    something 8 bytes wide.

    claude.md #96's array helpers move elements by a byte count the
    compiler hands them, and that count was hardcoded to 8. For an
    arr[bool] that made push() write to byte 8*i while xs[i] read byte
    i: the value went in and a neighbouring element's byte came back
    out. These pin every helper against the stride indexing actually
    uses."""

    def test_push_is_readable_at_its_own_index(self, compile_and_run):
        source = """
        arr[bool] bs = [true, false]
        bs.push(true)
        log(bs.length)
        log(bs[0])
        log(bs[1])
        log(bs[2])
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "3\ntrue\nfalse\ntrue\n"

    def test_unshift_shift_and_splice(self, compile_and_run):
        source = """
        arr[bool] bs = [true, false, true, false]
        bs.unshift(true)
        log(`${bs.length} ${bs[0]} ${bs[1]}`)
        log(bs.shift())
        arr[bool] cut = bs.splice(1, 2)
        log(`${cut.length} ${cut[0]} ${cut[1]}`)
        log(`${bs.length} ${bs[0]} ${bs[1]}`)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == (
            "5 true true\n"
            "true\n"
            "2 false true\n"
            "2 true false\n"
        )

    def test_pop_on_an_empty_bool_array_is_null(self, compile_and_run):
        source = """
        arr[bool] bs = []
        log(bs.pop())
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "null\n"


class TestUnassignedNestedFieldsAutoVivify:
    """claude.md #97: reaching THROUGH an unassigned struct/arr/map
    field used to segfault.

    claude.md's own rule is that an uninitialized field reads as its
    zero value, and for int/float/bool/text that already held. But a
    struct/arr/map field starts as a null pointer (calloc, or a global's
    zeroinitializer, gives it nothing else), so `o.inner.n` dereferenced
    null -- a crash, not a zero. Both reads and writes crashed, and the
    array and map cases crashed the same way the struct one did.

    The fix gives such a field real storage the first time it is
    reached, lazily. Lazily rather than eagerly at declaration because
    one mechanism then covers stack locals, heap locals, globals (whose
    storage is a compile-time zeroinitializer with nowhere to run an
    initializer at all), parameters, and fields nested arbitrarily deep
    inside other fields."""

    def test_reading_through_an_unassigned_struct_field(self, compile_and_run):
        source = """
        struct Inner { n:int label:text }
        struct Outer { inner:Inner }
        Outer o
        log(o.inner.n)
        log(o.inner.label)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        # A null text logs as an empty line -- the existing convention,
        # unchanged here; what matters is that it does not crash.
        assert result.stdout == "0\n\n"

    def test_reading_an_unassigned_array_or_map_field(self, compile_and_run):
        source = """
        struct Bag { xs:arr[int] m:map[int] }
        Bag b
        log(b.xs.length)
        log(b.m['nothing'])
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        # An absent map[int] key answers int's own null, which logs as
        # its reserved sentinel (see codegen's INT_NULL_CONST).
        assert result.stdout == "0\n-9223372036854775808\n"

    def test_the_storage_is_created_once_not_per_access(self, compile_and_run):
        # The identity check: if each read vivified a FRESH value, the
        # write below would land somewhere the read never looks at.
        source = """
        struct Inner { n:int }
        struct Outer { inner:Inner }
        Outer o
        o.inner.n = 5
        log(o.inner.n)
        o.inner.n = o.inner.n + 1
        log(o.inner.n)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "5\n6\n"

    def test_pushing_onto_an_unassigned_array_field(self, compile_and_run):
        source = """
        struct Bag { xs:arr[int] }
        Bag b
        b.xs.push(1)
        b.xs.push(2)
        log(`${b.xs.length}: ${b.xs[0]},${b.xs[1]}`)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "2: 1,2\n"

    def test_setting_a_key_on_an_unassigned_map_field(self, compile_and_run):
        source = """
        struct Bag { m:map[int] }
        Bag b
        b.m['k'] = 9
        log(b.m['k'])
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "9\n"

    def test_an_explicit_assignment_still_wins(self, compile_and_run):
        source = """
        struct Inner { n:int }
        struct Outer { inner:Inner }
        Inner i
        i.n = 7
        Outer o
        o.inner = i
        log(o.inner.n)
        i.n = 8
        log(o.inner.n)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        # Assigned by reference, so the later write to `i` is visible
        # through `o.inner` -- claude.md #79's shared-header identity.
        assert result.stdout == "7\n8\n"

    def test_deeply_nested_unassigned_fields(self, compile_and_run):
        source = """
        struct C { n:int }
        struct B { c:C }
        struct A { b:B }
        A a
        log(a.b.c.n)
        a.b.c.n = 3
        log(a.b.c.n)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "0\n3\n"


class TestTextConcatOwnership:
    """claude.md #97: a `+` on text is an owning source.

    claude.md #83 classified only a Call and a template literal as
    "already a fresh, exclusively-owned buffer". A text `+` compiles to
    exactly one festina_str_concat, which mallocs unconditionally with
    no operand-passthrough path -- so it is just as owning, and leaving
    it out meant every binding of a concatenation copied a buffer that
    was already exclusively owned and dropped the original: `text j = a
    + b` and `return s + '!'` each leaked one buffer per evaluation.

    The other half is that festina_str_concat COPIES from both operands
    and keeps neither, so a chained `a + b + c` has to free its own
    intermediate -- the same fix _emit_template already applies to its
    own intermediates.

    These are behaviour tests; the leaks themselves were measured under
    LeakSanitizer, which is not available here."""

    def test_chained_concatenation_frees_its_intermediate(self, parser, semantic, codegen):
        source = """
        text a = 'aa'
        text b = 'bb'
        text c = 'cc'
        void func use() {
            text joined = a + b + c
        }
        use()
        """
        program = parser.parse(source, filename="main.f")
        analyzed = semantic.analyze(program, filename="main.f")
        ir = codegen.generate_ir(program, analyzed, filename="main.f")
        body = ir.split("define void @use()")[1].split("\n}")[0]
        # Two concats, one free of the first one's result -- and no
        # festina_text_own, since the outer concat is itself owning.
        assert body.count("@festina_str_concat") == 2
        # One free for the `a + b` intermediate, one for `joined` at
        # scope exit -- and no festina_text_own at all, since the outer
        # concat is itself owning and needs no copy.
        assert body.count("call void @free(") == 2
        assert "@festina_text_own" not in body

    def test_returning_a_concatenation_does_not_copy_it(self, parser, semantic, codegen):
        source = """
        text func bang(s:text) {
            return s + '!'
        }
        log(bang('x'))
        """
        program = parser.parse(source, filename="main.f")
        analyzed = semantic.analyze(program, filename="main.f")
        ir = codegen.generate_ir(program, analyzed, filename="main.f")
        body = ir.split("define ptr @bang(")[1].split("\n}")[0]
        assert body.count("@festina_str_concat") == 1
        # The only text_own left is the parameter binding (claude.md
        # #84), never the returned concatenation itself.
        assert body.count("@festina_text_own") == 1

    def test_concatenation_still_produces_the_right_values(self, compile_and_run):
        source = """
        text func bang(s:text) {
            return s + '!'
        }
        text a = 'aa'
        text b = 'bb'
        log(a + b + 'cc')
        log(bang(a + b))
        text kept = a + b
        log(kept)
        log(kept + kept)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "aabbcc\naabb!\naabb\naabbaabb\n"

    def test_comparing_two_computed_texts(self, compile_and_run):
        source = """
        text a = 'aa'
        log(a + 'b' == 'aab')
        log(a + 'b' != 'aab')
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "true\nfalse\n"


class TestComputedMapKeyOwnership:
    """claude.md #97: festina_map_set strdups the key it is given (see
    its own comment on why it never aliases the caller's pointer) and
    festina_map_get only reads it, so a key the CALLER allocated --
    `m[`s${i}`] = v`, `m[a + b]` -- has no owner left once the call
    returns. Both sites now free it."""

    def test_a_computed_key_round_trips(self, compile_and_run):
        source = """
        map[int] scores = {}
        for int i = 0, i < 3, i++ {
            scores[`s${i}`] = i * 10
        }
        log(scores['s0'])
        log(scores[`s${1}`])
        log(scores['s' + '2'])
        log(scores['missing'])
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "0\n10\n20\n-9223372036854775808\n"

    def test_the_map_keeps_its_own_copy_of_the_key(self, compile_and_run):
        # The key buffer is freed at the call site now, so the map must
        # genuinely own its copy for this lookup to still work.
        source = """
        map[text] m = {}
        text k = 'na'
        m[k + 'me'] = 'ada'
        k = 'zzz'
        log(m['name'])
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "ada\n"


class TestTopLevelBlockScopeTracking:
    """claude.md #97: claude.md #74's scope tracking now covers the
    top-level statements too.

    A top-level VarDecl is a global and is unaffected. What this
    reaches is a local declared inside a NESTED block at top level --
    `text row = a + b` in a top-level `while` body -- which was emitted
    as an ordinary alloca and, with tracking off, never freed: one
    leaked buffer per iteration, in exactly the shape a game loop is
    written in. Leak-freedom was measured under LeakSanitizer; what is
    pinned here is that the values stay correct, since the same switch
    also turns on claude.md #81's stack promotion for these locals."""

    def test_locals_in_a_top_level_loop_stay_correct(self, compile_and_run):
        source = """
        struct P { x:int y:int }
        arr[P] kept = []
        int total = 0
        for int i = 0, i < 5, i++ {
            P local
            local.x = i
            local.y = i * 2
            arr[int] nums = [i, i + 1]
            text row = `${local.x},${local.y}` + '|'
            total = total + nums[1]
            if i % 2 == 0 {
                kept.push(local)
            }
        }
        log(total)
        log(kept.length)
        log(`${kept[0].x}/${kept[0].y} ${kept[2].x}/${kept[2].y}`)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "15\n3\n0/0 4/8\n"

    def test_a_map_local_in_a_top_level_loop(self, compile_and_run):
        source = """
        text last = ''
        for int i = 0, i < 3, i++ {
            map[text] m = {'k': `v${i}`}
            last = m['k']
        }
        log(last)
        """
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout == "v2\n"
