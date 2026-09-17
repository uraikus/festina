"""claude.md #334: `'' == null` was `true`.

`festina_str_eq` coerced a null `char *` to `""` on both sides before
comparing, so the empty string and the absent string were the same
value under `==`. That is right for two ordinary `text` operands, where
a null one having no content is defensible, and wrong the moment either
side is `null` itself: the question `x == null` asks whether anything is
there, and it answered "no" for a string that was there and empty.

It matters immediately for HTML, where `<input checked>` is an
attribute whose value IS the empty string -- indistinguishable, before
this, from an attribute that is not present at all. Reported by
uraikus/archtelos-browser.

`ascii`, the sibling type, has always been null-strict
(`festina_ascii_eq` opens with `if (a == b) return 1; if (!a || !b)
return 0;`). So this is the two string types disagreeing rather than a
design choice, and the fix is the one that makes them agree.
"""


class TestEmptyIsNotNull:
    """The four places the report named, plus the two it did not."""

    def test_an_empty_local_is_not_null(self, compile_and_run):
        result = compile_and_run("""
        text a = ''
        if a == null { log('null') } else { log('present') }
        """)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip() == "present"

    def test_an_unassigned_text_local_is_still_null(self, compile_and_run):
        # The other half: this must NOT change. `text` zero value is
        # null (specification.md 8.2), and a fix that made everything
        # non-null would be no better than the bug.
        result = compile_and_run("""
        text b
        if b == null { log('null') } else { log('present') }
        """)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip() == "null"

    def test_an_empty_struct_field_is_not_null(self, compile_and_run):
        result = compile_and_run("""
        struct S { s:text }
        S st
        st.s = ''
        if st.s == null { log('null') } else { log('present') }
        """)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip() == "present"

    def test_an_empty_array_element_is_not_null(self, compile_and_run):
        result = compile_and_run("""
        arr[text] xs = ['']
        if xs[0] == null { log('null') } else { log('present') }
        """)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip() == "present"

    def test_an_empty_map_value_is_not_null(self, compile_and_run):
        result = compile_and_run("""
        map[text] m = {'k': ''}
        if m['k'] == null { log('null') } else { log('present') }
        """)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip() == "present"

    def test_a_computed_empty_string_is_not_null(self, compile_and_run):
        # Not a literal and not a field -- a heap buffer that happens to
        # hold zero bytes, which is the shape a real program produces.
        result = compile_and_run("""
        text c = 'x'
        text d = c.slice(1, 1)
        if d == null { log('null') } else { log('present') }
        log(`${d.length}`)
        """)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip().splitlines() == ["present", "0"]

    def test_the_empty_literal_is_not_null(self, compile_and_run):
        result = compile_and_run("""
        if '' == null { log('null') } else { log('present') }
        """)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip() == "present"


class TestTheMirrorDirection:
    """`null == ''` is the same confusion read the other way round, and
    a fix that left it true would have swapped one surprise for a
    subtler one: two values that are not equal to each other."""

    def test_a_null_text_does_not_equal_the_empty_string(self, compile_and_run):
        result = compile_and_run("""
        text n
        if n == '' { log('equal') } else { log('different') }
        """)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip() == "different"

    def test_two_null_texts_are_equal(self, compile_and_run):
        result = compile_and_run("""
        text a
        text b
        if a == b { log('equal') } else { log('different') }
        """)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip() == "equal"

    def test_not_equal_agrees_throughout(self, compile_and_run):
        result = compile_and_run("""
        text e = ''
        text n
        if e != null { log('e != null') }
        if n != '' { log('n != empty') }
        if e != n { log('e != n') }
        """)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip().splitlines() == [
            "e != null", "n != empty", "e != n"]


class TestOrdinaryComparisonIsUnchanged:
    """Everything that was already right stays right -- the change is
    about null, not about content."""

    def test_content_equality_still_works(self, compile_and_run):
        result = compile_and_run("""
        text a = 'hello'
        text b = 'hel' + 'lo'
        text c = 'other'
        if a == b { log('same') } else { log('differs') }
        if a == c { log('wrong') } else { log('right') }
        if '' == '' { log('empties equal') }
        """)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip().splitlines() == [
            "same", "right", "empties equal"]

    def test_a_map_still_finds_an_empty_valued_key(self, compile_and_run):
        # festina_str_eq is also the map's own key comparison, so the
        # change reaches bucket lookup. An empty VALUE must still be
        # found, and an empty KEY must still work.
        result = compile_and_run("""
        map[text] m = {'k': '', '': 'empty key'}
        log(m['k'])
        log(m[''])
        if m['missing'] == null { log('missing is null') }
        """)
        assert result.returncode == 0, result.stdout + result.stderr
        # Compared unstripped: `log('')` emits a genuinely empty line,
        # and .strip() would silently eat the very thing under test.
        assert result.stdout == "\nempty key\nmissing is null\n" 

    def test_ascii_is_unchanged_and_agrees(self, compile_and_run):
        # The type that was already right, asserted alongside so the two
        # can be seen to agree rather than assumed to.
        result = compile_and_run("""
        ascii a = ''
        ascii b
        if a == null { log('a null') } else { log('a present') }
        if b == null { log('b null') } else { log('b present') }
        """)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip().splitlines() == ["a present", "b null"]
