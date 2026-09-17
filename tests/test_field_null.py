"""claude.md #333: a TERMINAL read of a struct-typed field answers what
the field holds, instead of creating an empty struct for the question to
be about.

specification.md 8.9.2 creates a field of struct/array/map type the
first time it is reached, which is what makes `b.inner.n` and
`b.xs.push(1)` work with nothing assigned first. Counting every read as
a reach meant a struct field could never be observed absent: the test
created the thing it was testing for. `if node.next != null` was true
for every node in a list; `x.field = null` followed by
`x.field == null` was `false`, so a program could not read back its own
write; and `cur = cur.next` walked a list that extended itself forever.

Reported by uraikus/archtelos-browser, whose every workaround for it was
a parallel boolean or an id that is 0 when absent.

TWO halves, and the second matters as much as the first. Reaching a
field AS A RECEIVER still creates it, or `b.inner.n` would fault. And
the rule is struct-only: an arr[T]/map[T] field's zero value is a real
empty container rather than an absent one, and handing a program a null
array would be a worse bug than this one -- `.length` on a null array
does not fault, it reads whatever is eight bytes past the null page.
Both halves are pinned below.
"""


class TestANullTestDoesNotCreate:
    def test_an_unassigned_struct_field_is_null(self, compile_and_run):
        result = compile_and_run("""
        struct Node {
            next:Node
            tag:int
        }
        Node n
        if n.next == null { log('null') } else { log('vivified') }
        """)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip() == "null"

    def test_not_equal_null_agrees_with_equal_null(self, compile_and_run):
        # `!=` is the spelling the report actually used -- `if node.next
        # != null` was true for every node in a list -- so it is pinned
        # separately rather than assumed to follow from `==`.
        result = compile_and_run("""
        struct Node {
            next:Node
            tag:int
        }
        Node n
        if n.next != null { log('present') } else { log('absent') }
        """)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip() == "absent"

    def test_a_field_assigned_null_reads_back_as_null(self, compile_and_run):
        # The sharpest form of the bug: the assignment stored null and
        # the read created a fresh value over the top of it, so the
        # program could not observe its own write.
        result = compile_and_run("""
        struct Node {
            next:Node
            tag:int
        }
        Node a
        Node b
        a.next = b
        if a.next == null { log('1 null') } else { log('1 set') }
        a.next = null
        if a.next == null { log('2 null') } else { log('2 set') }
        """)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip().splitlines() == ["1 set", "2 null"]

    def test_array_and_map_fields_deliberately_do_NOT_follow(self, compile_and_run):
        # The rule is struct-only, and this pins that rather than
        # leaving it to be discovered. An arr[T]/map[T] field's zero
        # value is a real EMPTY container, not an absent one -- so it is
        # never null, and `h.xs.length` is 0 rather than a fault.
        #
        # Making containers read null too would have been the worse bug:
        # `.length` on a null array does not fault, it reads whatever is
        # eight bytes past the null page. Measured before deciding --
        # `arr[int] xs = null` then `xs.length` printed 94746664194904.
        result = compile_and_run("""
        struct Holder {
            xs:arr[int]
            m:map[int]
        }
        Holder h
        if h.xs == null { log('xs null') } else { log('xs made') }
        if h.m == null { log('m null') } else { log('m made') }
        log(`${h.xs.length}`)
        """)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip().splitlines() == ["xs made", "m made", "0"]

    def test_a_linked_list_walk_terminates(self, compile_and_run):
        # The shape the report was actually blocked on. Before this,
        # `cur.next != null` was true forever and the loop either ran
        # away or needed a parallel boolean to stop it.
        result = compile_and_run("""
        struct Node {
            next:Node
            tag:int
        }
        Node head
        head.tag = 1
        Node second
        second.tag = 2
        head.next = second
        Node third
        third.tag = 3
        second.next = third

        int sum = 0
        Node cur = head
        while cur != null {
            sum = sum + cur.tag
            cur = cur.next
        }
        log(`${sum}`)
        """)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip() == "6"


class TestEveryOtherUseStillCreates:
    """specification.md 8.9.2's other half. A null test that stopped
    creating would be a bad trade if anything else stopped too."""

    def test_a_member_access_through_a_field_still_creates(self, compile_and_run):
        result = compile_and_run("""
        struct Inner { n:int }
        struct Outer { inner:Inner }
        Outer o
        o.inner.n = 5
        log(`${o.inner.n}`)
        """)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip() == "5"

    def test_a_method_call_on_an_array_field_still_creates(self, compile_and_run):
        result = compile_and_run("""
        struct Holder { xs:arr[int] }
        Holder h
        h.xs.push(1)
        h.xs.push(2)
        log(`${h.xs.length}`)
        """)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip() == "2"

    def test_reading_a_field_into_a_binding_still_creates(self, compile_and_run):
        # A plain read is still a reach, so this binds a real empty
        # array rather than null -- and `.length` on it answers 0.
        result = compile_and_run("""
        struct Holder { xs:arr[int] }
        Holder h
        arr[int] taken = h.xs
        log(`${taken.length}`)
        """)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip() == "0"

    def test_a_struct_field_reached_as_a_receiver_is_no_longer_null(self, compile_and_run):
        # The honest consequence of lazy creation, stated as a test
        # rather than left for someone to trip over. Reaching a struct
        # field AS A RECEIVER creates it, so a null test afterwards sees
        # it. `== null` asks "is anything there", which after a
        # reach-through is legitimately yes -- `o.inner.n = 5` has to
        # have somewhere to put the 5.
        result = compile_and_run("""
        struct Inner { n:int }
        struct Outer { inner:Inner }
        Outer o
        if o.inner == null { log('before: null') } else { log('before: made') }
        o.inner.n = 5
        if o.inner == null { log('after: null') } else { log('after: made') }
        """)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip().splitlines() == ["before: null", "after: made"]

    def test_a_local_of_struct_type_is_never_null(self, compile_and_run):
        # Scope check: the rule is about FIELDS. A local or global of
        # these types is created at its declaration, so it never reads
        # as null and nothing here changes that.
        result = compile_and_run("""
        struct Node { tag:int }
        Node local
        if local == null { log('null') } else { log('exists') }
        """)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip() == "exists"
