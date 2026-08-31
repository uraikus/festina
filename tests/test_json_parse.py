"""claude.md #159: text.toStruct(StructName) / text.toArr(ElementType).

JSON deserialization -- the reverse of .toText()'s own JSON rendering
(claude.md #114). Parser/semantic coverage (the type-as-argument
grammar, the v1 scalars-only scope restriction) and real
compile-and-run coverage (happy path, case-insensitive/lenient
parsing, and the throw-on-malformed-input pairing with claude.md #157)
live together in this one file.
"""
import pytest


class TestParsing:
    def test_to_struct_parses(self, parser):
        parser.parse("""
        struct Foo { id:int }
        Foo f = '{}'.toStruct(Foo)
        """)

    def test_to_arr_parses(self, parser):
        parser.parse("arr[int] xs = '[]'.toArr(int)")

    def test_to_arr_of_text_parses(self, parser):
        parser.parse("arr[text] xs = '[]'.toArr(text)")


class TestSemanticErrors:
    def test_to_struct_receiver_must_be_text(self, parser, semantic, errors):
        program = parser.parse("""
        struct Foo { id:int }
        int n = 5
        Foo f = n.toStruct(Foo)
        """)
        with pytest.raises(errors.CompileError, match="can only be called on text"):
            semantic.analyze(program)

    def test_to_struct_argument_must_be_a_struct_name(self, parser, semantic, errors):
        program = parser.parse("int n = '5'.toStruct(int)")
        with pytest.raises(errors.CompileError, match="must be a struct name"):
            semantic.analyze(program)

    def test_to_arr_of_a_struct_element_now_analyzes(self, parser, semantic):
        # claude.md #173 widened claude.md #159's own v1 scope cut --
        # arr[T]'s element type may now itself be a nested struct.
        program = parser.parse("""
        struct Foo { id:int }
        arr[Foo] xs = '[]'.toArr(Foo)
        """)
        semantic.analyze(program)

    def test_to_arr_element_type_rejects_a_genuinely_unsupported_type(
            self, parser, semantic, errors):
        # img/aud/regex/... still don't have a from-JSON shape at all --
        # this is not a scope cut anymore, it's a real, permanent "there
        # is no JSON encoding for this" rejection.
        program = parser.parse("arr[img] xs = '[]'.toArr(img)")
        with pytest.raises(errors.CompileError, match="int/float/bool/text"):
            semantic.analyze(program)

    def test_to_struct_now_accepts_a_nested_arr_field(self, parser, semantic):
        # claude.md #173: was claude.md #159's own v1 scope cut.
        program = parser.parse("""
        struct Bag { xs:arr[int] }
        Bag b = '{}'.toStruct(Bag)
        """)
        semantic.analyze(program)

    def test_to_struct_now_accepts_a_nested_struct_field(self, parser, semantic):
        program = parser.parse("""
        struct Point { x:int  y:int }
        struct Line { a:Point  b:Point }
        Line l = '{}'.toStruct(Line)
        """)
        semantic.analyze(program)

    def test_to_struct_now_accepts_a_map_field(self, parser, semantic):
        program = parser.parse("""
        struct Bag { scores:map[int] }
        Bag b = '{}'.toStruct(Bag)
        """)
        semantic.analyze(program)

    def test_to_struct_still_rejects_a_genuinely_unsupported_field(
            self, parser, semantic, errors):
        program = parser.parse("""
        struct Bag { pic:img }
        Bag b = '{}'.toStruct(Bag)
        """)
        with pytest.raises(errors.CompileError, match="doesn't support field"):
            semantic.analyze(program)

    def test_to_struct_rejects_a_field_nested_inside_an_unsupported_type(
            self, parser, semantic, errors):
        # The violation is two levels deep (arr[img], not img itself) --
        # still caught, since _is_json_parseable_type recurses through
        # the array to its own element type.
        program = parser.parse("""
        struct Bag { pics:arr[img] }
        Bag b = '{}'.toStruct(Bag)
        """)
        with pytest.raises(errors.CompileError, match="doesn't support field"):
            semantic.analyze(program)

    def test_self_referencing_struct_field_analyzes(self, parser, semantic):
        # claude.md #17 made the TYPE legal; claude.md #173 is what
        # makes .toStruct() itself able to reach it without an infinite
        # compile-time recursion -- see _is_json_parseable_type's own
        # cycle-safety comment.
        program = parser.parse("""
        struct Node { n:int  next:Node }
        Node n = '{}'.toStruct(Node)
        """)
        semantic.analyze(program)

    def test_to_struct_resolves_to_the_struct_type(self, parser, semantic, types_mod):
        program = parser.parse("""
        struct Foo { id:int }
        Foo f = '{}'.toStruct(Foo)
        """)
        semantic.analyze(program)

    def test_to_arr_resolves_to_arr_of_element_type(self, parser, semantic):
        program = parser.parse("arr[int] xs = '[]'.toArr(int)")
        semantic.analyze(program)


class TestRuntimeBehavior:
    def test_to_struct_parses_every_scalar_field_type(self, compile_and_run):
        source = """
        struct Person { id:int name:text active:bool score:float }
        Person p = '{"id": 7, "name": "Ada", "active": true, "score": 9.5}'.toStruct(Person)
        log(p)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == '{"id":7,"name":"Ada","active":true,"score":9.5}'

    def test_to_arr_parses_int_float_bool_text(self, compile_and_run):
        result = compile_and_run("""
        log('[1,2,3,4,5]'.toArr(int))
        log('[1.5,2.5]'.toArr(float))
        log('[true,false]'.toArr(bool))
        log('["a","b","c"]'.toArr(text))
        """)
        assert result.stdout.splitlines() == [
            "[1,2,3,4,5]", "[1.5,2.5]", "[true,false]", '["a","b","c"]',
        ]

    def test_unknown_json_keys_are_silently_skipped(self, compile_and_run):
        source = """
        struct Person { id:int }
        Person p = '{"id": 1, "extra": {"nested": [1,2,3]}, "more": "x"}'.toStruct(Person)
        log(p)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == '{"id":1}'

    def test_missing_json_keys_keep_the_zero_value(self, compile_and_run):
        source = """
        struct Person { id:int name:text }
        Person p = '{}'.toStruct(Person)
        log(p)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == '{"id":0,"name":null}'

    def test_keys_match_case_insensitively(self, compile_and_run):
        source = """
        struct Person { id:int name:text }
        Person p = '{"ID": 1, "NaMe": "x"}'.toStruct(Person)
        log(p)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == '{"id":1,"name":"x"}'

    def test_duplicate_key_last_one_wins(self, compile_and_run):
        source = """
        struct Person { id:int }
        Person p = '{"id": 1, "id": 2}'.toStruct(Person)
        log(p)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == '{"id":2}'

    def test_json_null_becomes_the_field_type_own_null(self, compile_and_run):
        source = """
        struct Person { id:int name:text }
        Person p = '{"id": null, "name": null}'.toStruct(Person)
        log(p.id)
        log(p.name)
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines()[1] == ""  # null text prints as empty

    def test_malformed_json_throws_caught_by_try_catch(self, compile_and_run):
        source = """
        struct Person { id:int name:text }
        try {
            Person p = 'not json'.toStruct(Person)
            log('unreachable')
        } catch (e:text) {
            log(`caught: ${e}`)
        }
        log('done')
        """
        result = compile_and_run(source)
        assert result.stdout.splitlines() == [
            "caught: expected '{' at position 0, found 'n'", "done",
        ]

    def test_large_int_fields_keep_full_64_bit_precision(self, compile_and_run):
        # claude.md #192: an integer field used to be routed through the
        # double festina_json_parse_number returns, so any |n| > 2^53
        # lost low bits and, worst of all, INT64_MAX rounded to 2^63
        # whose double->int64 conversion lands on INT64_MIN -- which IS
        # the int-null sentinel, so a valid max int read back as null.
        # Integer-shaped tokens now parse through strtoll directly.
        source = '''
        struct T { a:int  b:int  c:int }
        T v = '{"a":9223372036854775807,"b":-9223372036854775808,"c":9007199254740993}'.toStruct(T)
        log(v.a)
        log(v.b)
        log(v.c)
        '''
        result = compile_and_run(source)
        assert result.stdout.splitlines() == [
            "9223372036854775807",
            "-9223372036854775808",
            "9007199254740993",
        ]

    def test_deeply_nested_unknown_field_throws_instead_of_crashing(
            self, compile_and_run):
        # claude.md #192: an unknown struct field's value is skipped by
        # recursive descent (festina_json_skip_value), one C stack frame
        # per '{'/'['. Hostile input -- reachable through req.toStruct()
        # on a network body -- used to overflow the stack and SIGSEGV;
        # a depth cap now makes it the same catchable throw every other
        # malformed input is.
        deep = "[" * 5000 + "]" * 5000
        source = '''
        struct T {{ x:int }}
        try {{
            T v = '{{"x":1,"junk":{deep}}}'.toStruct(T)
            log(v.x)
        }} catch (e:text) {{
            log('caught')
        }}
        '''.format(deep=deep)
        result = compile_and_run(source)
        assert result.returncode == 0
        assert result.stdout.strip() == "caught"

    def test_type_mismatch_throws(self, compile_and_run):
        source = """
        try {
            arr[int] xs = '[1,2,"three"]'.toArr(int)
        } catch (e:text) {
            log(`caught: ${e}`)
        }
        """
        result = compile_and_run(source)
        assert result.stdout.strip().startswith("caught:")

    def test_trailing_data_throws(self, compile_and_run):
        source = """
        struct Person { id:int }
        try {
            Person p = '{"id":1}extra'.toStruct(Person)
        } catch (e:text) {
            log(`caught: ${e}`)
        }
        """
        result = compile_and_run(source)
        assert "trailing" in result.stdout

    def test_uncaught_parse_error_behaves_like_fail(self, compile_and_run):
        result = compile_and_run("""
        struct Person { id:int }
        Person p = 'nope'.toStruct(Person)
        """)
        assert result.returncode == 1
        assert result.stderr.strip().startswith("fail:")

    def test_nested_struct_field_parses(self, compile_and_run):
        source = """
        struct Point { x:int  y:int }
        struct Line { a:Point  b:Point  label:text }
        Line l = '{"a":{"x":1,"y":2},"b":{"x":3,"y":4},"label":"hi"}'.toStruct(Line)
        log(`${l.a.x},${l.a.y} ${l.b.x},${l.b.y} ${l.label}`)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "1,2 3,4 hi"

    def test_arr_of_struct_elements_parses(self, compile_and_run):
        source = """
        struct Point { x:int  y:int }
        arr[Point] pts = '[{"x":1,"y":2},{"x":3,"y":4}]'.toArr(Point)
        log(`${pts.length} ${pts[0].x} ${pts[1].y}`)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "2 1 4"

    def test_nested_arr_of_arr_parses(self, compile_and_run):
        source = """
        arr[arr[int]] grid = '[[1,2,3],[4,5]]'.toArr(arr[int])
        log(`${grid.length} ${grid[0].length} ${grid[1][1]}`)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "2 3 5"

    def test_map_field_parses_arbitrary_keys(self, compile_and_run):
        source = """
        struct Scores { name:text  values:map[int] }
        Scores s = '{"name":"ada","values":{"a":1,"b":2,"c":3}}'.toStruct(Scores)
        log(`${s.name} ${s.values['a']} ${s.values['b']} ${s.values['c']}`)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "ada 1 2 3"

    def test_arr_of_map_elements_parses(self, compile_and_run):
        source = """
        arr[map[int]] maps = '[{"x":1},{"y":2}]'.toArr(map[int])
        log(`${maps.length} ${maps[0]['x']} ${maps[1]['y']}`)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "2 1 2"

    def test_self_referencing_struct_parses_to_the_actual_depth_present(self, compile_and_run):
        source = """
        struct Node { n:int  next:Node }
        Node head = '{"n":1,"next":{"n":2,"next":{"n":3}}}'.toStruct(Node)
        log(`${head.n} ${head.next.n} ${head.next.next.n}`)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "1 2 3"

    def test_duplicate_key_on_a_nested_map_field_last_one_wins(self, compile_and_run):
        # Exercises _from_json_map_value's own overwrite-releases-the-
        # old-value path (leak coverage lives in
        # tests/stress/json_parse_churn.f; this pins the observable
        # VALUE that path leaves behind).
        source = """
        struct Wrap { a:map[int] }
        Wrap w = '{"a":{"x":1,"x":2,"y":3}}'.toStruct(Wrap)
        log(`${w.a['x']} ${w.a['y']}`)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "2 3"

    def test_duplicate_key_on_a_nested_struct_field_last_one_wins(self, compile_and_run):
        source = """
        struct Point { x:int  y:int }
        struct Wrap { a:Point }
        Wrap w = '{"a":{"x":1,"y":1},"a":{"x":2,"y":2}}'.toStruct(Wrap)
        log(`${w.a.x} ${w.a.y}`)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "2 2"

    def test_successful_parses_leak_nothing_in_a_loop(self, compile_and_run):
        # claude.md #159's own leak caveat is strictly error-path-only
        # (see runtime/festina_runtime.c's own comment) -- not a
        # Valgrind run itself (this suite doesn't do that here), but
        # pins the OBSERVABLE happy-path behavior across many repeated
        # calls that the caveat's own claim depends on.
        source = """
        struct Person { id:int name:text }
        int i = 0
        while (i < 30) {
            i = i + 1
            Person p = '{"id": 1, "name": "x"}'.toStruct(Person)
            arr[int] xs = '[1,2,3]'.toArr(int)
        }
        log('done')
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "done"
