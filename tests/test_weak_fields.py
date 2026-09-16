"""claude.md #332: `name:weak T` -- a struct field that refers to a
value without keeping it alive, and whose read is CHECKED: it yields
`null` once the target's last ordinary reference is gone, never a
reference to a freed value.

The point of the feature is specification.md 13.3's last clause: a weak
edge is not walked by cycle collection and does not count when deciding
whether a type can form a cycle at all. A parent pointer declared
`weak` takes a tree from "every release walks the whole document" to
"every release walks its own subtree", and where the weak edge is the
type graph's ONLY way back, the detector is not generated at all.

Grammar/semantic checks run on parser.parse() + semantic.analyze()
alone. The representation and the lifetime behaviour need real
compile-and-run. The "a weak field does not leak its target, and does
not free it either" sanitizer proof lives in tests/test_leak_stress.py,
which has the toolchain this file's fixtures do not.
"""
import pytest


class TestGrammar:
    def test_weak_field_parses(self, parser):
        prog = parser.parse("struct Node {\n parent:weak Node\n tag:int\n}")
        fields = prog.body[0].fields
        assert fields[0].name == "parent"
        assert fields[0].weak is True
        assert fields[1].name == "tag"
        assert fields[1].weak is False

    def test_a_plain_field_is_not_weak(self, parser):
        prog = parser.parse("struct Node {\n parent:Node\n}")
        assert prog.body[0].fields[0].weak is False

    def test_weak_is_still_usable_as_an_ordinary_name(self, parser):
        # `weak` is a MODIFIER in one position, not a reserved word --
        # taking the whole identifier away from every program that
        # already uses it would be a gratuitous break, and the parser
        # only ever looks for it straight after a field's ':'.
        parser.parse("int weak = 3\nlog(`${weak}`)")


class TestSemanticRejections:
    """specification.md 13.5's compile errors, each on its own -- the
    list is short and every entry is a place `weak` would mean
    something this implementation does not do."""

    def _err(self, parser, semantic, errors, source, match):
        with pytest.raises(errors.CompileError, match=match):
            semantic.analyze(parser.parse(source))

    def test_weak_on_a_table_field_is_rejected(self, parser, semantic, errors):
        self._err(parser, semantic, errors,
                  "table Row {\n owner:weak Row\n}", "weak")

    def test_weak_on_a_non_struct_type_is_rejected(self, parser, semantic, errors):
        self._err(parser, semantic, errors,
                  "struct S {\n xs:weak arr[int]\n}", "weak")

    def test_weak_on_a_scalar_is_rejected(self, parser, semantic, errors):
        self._err(parser, semantic, errors,
                  "struct S {\n n:weak int\n}", "weak")

    def test_weak_on_a_text_field_is_rejected(self, parser, semantic, errors):
        self._err(parser, semantic, errors,
                  "struct S {\n s:weak text\n}", "weak")


class TestReadsAreChecked:
    """The half that separates this from `T?`: a weak read can answer
    `null`, and never answers a freed pointer."""

    def test_an_unset_weak_field_reads_as_null(self, compile_and_run):
        # specification.md 8.9.2's one exception: a weak field is never
        # auto-vivified. Every other struct-typed field would have
        # created an empty Node here and compared non-null.
        result = compile_and_run("""
        struct Node {
            parent:weak Node
            tag:int
        }
        Node n
        if n.parent == null { log('null') } else { log('vivified') }
        """)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip() == "null"

    def test_a_live_weak_field_reads_back_as_its_target(self, compile_and_run):
        result = compile_and_run("""
        struct Node {
            parent:weak Node
            kids:arr[Node]
            tag:int
        }
        Node root
        root.tag = 7
        Node c
        c.parent = root
        root.kids.push(c)
        Node p = c.parent
        if p == null { log('lost') } else { log(`${p.tag}`) }
        """)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip() == "7"

    def test_a_weak_field_reads_null_once_its_target_is_gone(self, compile_and_run):
        # The whole safety claim in one program: `holder.target` is the
        # only thing still naming the Node when the scope that owned it
        # ends, and because it is weak that is not enough to keep it --
        # so the read afterwards must answer null rather than a pointer
        # into freed memory.
        result = compile_and_run("""
        struct Thing { tag:int }
        struct Holder { target:weak Thing }

        Holder h

        void func attach() {
            Thing t
            t.tag = 42
            h.target = t
        }

        attach()
        if h.target == null { log('gone') } else { log('still here') }
        """)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip() == "gone"

    def test_a_weak_field_does_not_keep_its_target_alive(self, compile_and_run):
        # The same claim from the other side: an ordinary field in the
        # identical program DOES keep it, so this pins the difference
        # rather than just asserting one half of it.
        result = compile_and_run("""
        struct Thing { tag:int }
        struct WeakHolder { target:weak Thing }
        struct StrongHolder { target:Thing }

        WeakHolder w
        StrongHolder s

        void func attach() {
            Thing t
            t.tag = 42
            w.target = t
            s.target = t
        }

        attach()
        Thing st = s.target
        log(`${st.tag}`)
        if w.target == null { log('weak gone') } else { log('weak alive') }
        """)
        assert result.returncode == 0, result.stdout + result.stderr
        # The strong holder keeps it, so the weak one still resolves.
        assert result.stdout.strip().splitlines() == ["42", "weak alive"]

    def test_an_upgraded_read_survives_its_target_being_dropped(self, compile_and_run):
        # specification.md 13.5: a read that yields non-null yields an
        # ORDINARY counted reference. Dropping the last other reference
        # after the read must not free it underneath the binding.
        result = compile_and_run("""
        struct Thing { tag:int }
        struct Holder { target:weak Thing }

        Holder h
        Thing keep

        void func attach() {
            Thing t
            t.tag = 9
            h.target = t
            keep = t
        }

        attach()
        Thing up = h.target
        keep = null
        log(`${up.tag}`)
        """)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip() == "9"


class TestCycleCollectionIsSkipped:
    """specification.md 13.3/13.5: the reason the feature exists."""

    def _ir(self, source, tmp_path):
        import sys
        sys.path.insert(0, "bootstrap")
        import irdump
        src = tmp_path / "w.f"
        src.write_text(source, encoding="utf-8")
        return "\n".join(irdump.dump_file(str(src)))

    def test_a_weak_only_back_edge_generates_no_detector(self, tmp_path):
        # Doc -> arr[Child] -> Child -> (weak) Doc. The only path back
        # to Doc is the weak one, so neither type can form a cycle and
        # specification.md 13.5's last line applies: the program pays
        # nothing at all.
        ir = self._ir("""
        struct Child {
            parent:weak Doc
            tag:int
        }
        struct Doc {
            kids:arr[Child]
            name:text
        }
        Doc d
        Child c
        c.parent = d
        d.kids.push(c)
        log(`${d.kids.length}`)
        """, tmp_path)
        # The `declare` lines for the runtime's type-blind helpers are
        # unconditional boilerplate in every module; what this is about
        # is whether any per-type traversal is DEFINED and whether any
        # release actually runs a trial.
        assert "define void @__festina_cycle" not in ir
        assert "call i8 @festina_cycle_candidate" not in ir

    def test_the_same_shape_with_a_strong_back_edge_does_generate_one(self, tmp_path):
        # The control. Identical but for the modifier, so this pins that
        # the assertion above is about `weak` and not about the shape.
        ir = self._ir("""
        struct Child {
            parent:Doc
            tag:int
        }
        struct Doc {
            kids:arr[Child]
            name:text
        }
        Doc d
        Child c
        c.parent = d
        d.kids.push(c)
        log(`${d.kids.length}`)
        """, tmp_path)
        assert "define void @__festina_cycle" in ir
        assert "call i8 @festina_cycle_candidate" in ir

    def test_a_self_referential_type_keeps_its_detector(self, tmp_path):
        # `weak` narrows what the collector WALKS without switching it
        # off: Node still reaches itself through kids, so the detector
        # is still generated. Worth pinning, because the performance
        # win here comes from the smaller walk rather than from the
        # machinery going away, and those are easy to conflate.
        # The Node bindings are function LOCALS on purpose: a detector
        # is generated for the release wrapper, and a program whose
        # every Node is a top-level global never releases one, so it
        # emits no wrapper and would assert nothing.
        ir = self._ir("""
        struct Node {
            parent:weak Node
            kids:arr[Node]
            tag:int
        }
        int func build() {
            Node root
            Node c
            c.parent = root
            root.kids.push(c)
            return root.kids.length
        }
        log(`${build()}`)
        """, tmp_path)
        assert "define void @__festina_cycle" in ir

    def test_a_parent_pointer_tree_is_no_longer_quadratic(self, compile_and_run):
        # todo.md's reported case, reduced: 8,421 nodes took 1,645ms
        # instead of 1ms because a release rooted at any node climbed
        # the parent chain to the root and walked the whole document.
        # Measured as a RATIO rather than a wall-clock bound, so it
        # answers "did the quadratic go away" instead of "is this
        # machine fast": doubling the node count must not multiply the
        # work by anything like four.
        import time

        def run(n):
            source = """
            struct Node {
                parent:weak Node
                kids:arr[Node]
                tag:int
            }
            int func build(n:int) {
                Node root
                int i = 0
                while i < n {
                    Node c
                    c.tag = i
                    c.parent = root
                    root.kids.push(c)
                    i++
                }
                int sum = 0
                int j = 0
                while j < n {
                    Node c = root.kids[j]
                    sum = sum + c.tag
                    j++
                }
                return sum
            }
            log(`${build(__N__)}`)
            """.replace("__N__", str(n))
            started = time.monotonic()
            result = compile_and_run(source, filename=f"tree{n}.f")
            assert result.returncode == 0, result.stdout + result.stderr
            return time.monotonic() - started

        small = run(2000)
        large = run(8000)
        # Four times the nodes. Quadratic would be ~16x the WORK; the
        # fixed compile cost in both numbers only softens that, and the
        # measured broken case at these sizes is 0.161s vs 2.694s.
        assert large < small * 6, (
            f"2000 nodes took {small:.3f}s, 8000 took {large:.3f}s -- "
            "that is the quadratic walk coming back")
