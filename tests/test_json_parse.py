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


class TestJsonParsingNeedsNoSjlj:
    """claude.md #233: .toStruct()/.toArr() must never make a program a
    "uses try" one. claude.md #223 put a real llvm.eh.sjlj.setjmp frame
    inside every generated JSON builder and set CodeGen.uses_try for
    it -- which is the one flag cli.py's wasm32-wasi and macOS gates
    key off, so JSON parsing was silently rejected on both (and the
    builder-side catch never ran on Windows). The builders' cleanup is
    the runtime's cleanup stack now (plain C, no setjmp), so the flag
    stays exactly what the program's own source says."""

    def _flag(self, parser, semantic, codegen, source):
        program = parser.parse(source)
        analyzed = semantic.analyze(program)
        gen = codegen.CodeGen(analyzed)
        ir = gen.generate(program)
        return gen.uses_try, ir

    def test_to_struct_and_to_arr_leave_uses_try_unset(self, parser, semantic, codegen):
        uses_try, ir = self._flag(parser, semantic, codegen, """
        struct Person { id:int  name:text  tags:arr[text]  scores:map[int] }
        Person p = '{"id":1,"name":"a","tags":["x"],"scores":{"q":2}}'.toStruct(Person)
        arr[Person] ps = '[]'.toArr(Person)
        log(p.id + ps.length)
        """)
        assert uses_try is False
        # The intrinsic is always DECLARED (the runtime prelude is
        # fixed); what must be absent is any actual call to it.
        assert "call i32 @llvm.eh.sjlj.setjmp" not in ir
        # and the cleanup stack is what replaced it
        assert "call void @festina_cleanup_push" in ir
        assert "call void @festina_cleanup_pop" in ir

    def test_an_explicit_try_still_sets_it(self, parser, semantic, codegen):
        uses_try, ir = self._flag(parser, semantic, codegen,
                                  "try { log('x') } catch (e:text) { log(e) }\n")
        assert uses_try is True
        assert "call i32 @llvm.eh.sjlj.setjmp" in ir


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

    def test_duplicate_text_key_whose_second_value_fails_does_not_double_free(
            self, compile_and_run):
        # claude.md #233: the builder used to free the first "name"
        # BEFORE reading the second value; that read throws, and the
        # throw-time release of the half-built struct then freed the
        # same buffer again (an "Invalid free()" under Valgrind; glibc
        # aborts the process outright on the double free it detects).
        # 200 rounds so a detected double free cannot hide behind
        # allocator luck.
        source = """
        struct Person { id:int  name:text }
        int caught = 0
        int i = 0
        while i < 200 {
            try {
                Person p = `{"id":${i},"name":"first","name":5}`.toStruct(Person)
                log('unreachable')
            } catch (e:text) {
                caught = caught + 1
            }
            i = i + 1
        }
        log(caught)
        """
        result = compile_and_run(source)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "200"

    def test_trailing_data_after_a_complete_value_is_caught_and_the_program_continues(
            self, compile_and_run):
        # claude.md #233: the finished value is registered on the
        # cleanup stack across festina_json_expect_end's own trailing-
        # data check (it used to leak there -- see
        # tests/valgrind_stress/json_parse_fail_churn.f, which measures
        # that under Valgrind; this test pins the visible contract: a
        # catchable throw, then the program carries on).
        source = """
        struct Person { id:int  name:text }
        int caught = 0
        int i = 0
        while i < 100 {
            try {
                Person p = `{"id":${i},"name":"whole"} trailing`.toStruct(Person)
                log('unreachable')
            } catch (e:text) {
                if i == 0 { log(e) }
                caught = caught + 1
            }
            i = i + 1
        }
        log(caught)
        """
        result = compile_and_run(source)
        assert result.returncode == 0, result.stderr
        lines = result.stdout.splitlines()
        assert "trailing" in lines[0]
        assert lines[1] == "100"

    def test_pathologically_deep_self_referencing_nesting_throws_instead_of_crashing(
            self, compile_and_run):
        # claude.md #233: a self-referencing struct's own builder
        # recursion had no depth cap at all (claude.md #192's cap only
        # covered an UNKNOWN field's skipped value) -- 300k levels of
        # `{"next":` recursed straight off the C stack (SIGSEGV,
        # confirmed). The cleanup stack's bound now converts that into
        # the same catchable throw, and the ~1000 levels already built
        # are released on the way out (Valgrind-measured in
        # tests/valgrind_stress/json_parse_fail_churn.f).
        deep = '{"n":1,"next":' * 5000 + '{"n":2}' + "}" * 5000
        source = '''
        struct Node { n:int  next:Node }
        try {
            Node chain = '%s'.toStruct(Node)
            log('unreachable')
        } catch (e:text) {
            log(`caught: ${e}`)
        }
        log('still running')
        ''' % deep
        result = compile_and_run(source)
        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines() == [
            "caught: JSON nested too deeply (more than 1024 levels)",
            "still running",
        ]

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

    def test_u_escape_decodes_a_bmp_codepoint(self, compile_and_run):
        # claude.md #206: \uXXXX used to throw "not yet supported" --
        # now decoded into its UTF-8 encoding, same as every other
        # escape. Festina source writes `\\u00e9` (two literal
        # backslashes) because the lexer's own `\\` unescaping collapses
        # that down to ONE literal backslash + `u00e9` before the JSON
        # parser ever sees it -- which is the actual é escape.
        source = r"""
        struct Person { name:text }
        Person p = '{"name":"caf\\u00e9"}'.toStruct(Person)
        log(p.name)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "café"

    def test_u_escape_decodes_a_surrogate_pair(self, compile_and_run):
        # A high + low surrogate pair combining into one astral-plane
        # codepoint (U+1F600 GRINNING FACE), the four-byte UTF-8 case.
        source = r"""
        struct Person { name:text }
        Person p = '{"name":"\\ud83d\\ude00"}'.toStruct(Person)
        log(p.name)
        """
        result = compile_and_run(source)
        assert result.stdout.strip() == "\U0001F600"

    def test_unpaired_high_surrogate_throws(self, compile_and_run):
        source = r"""
        struct Person { name:text }
        try {
            Person p = '{"name":"\\ud83d"}'.toStruct(Person)
            log('unreachable')
        } catch (e:text) {
            log(`caught: ${e}`)
        }
        """
        result = compile_and_run(source)
        assert "unpaired UTF-16 surrogate" in result.stdout

    def test_lone_low_surrogate_throws(self, compile_and_run):
        source = r"""
        struct Person { name:text }
        try {
            Person p = '{"name":"\\ude00"}'.toStruct(Person)
            log('unreachable')
        } catch (e:text) {
            log(`caught: ${e}`)
        }
        """
        result = compile_and_run(source)
        assert "unpaired UTF-16 surrogate" in result.stdout

    def test_high_surrogate_followed_by_non_surrogate_throws(self, compile_and_run):
        source = r"""
        struct Person { name:text }
        try {
            Person p = '{"name":"\\ud83d\\u0041"}'.toStruct(Person)
            log('unreachable')
        } catch (e:text) {
            log(`caught: ${e}`)
        }
        """
        result = compile_and_run(source)
        assert "low surrogate" in result.stdout

    def test_truncated_u_escape_throws(self, compile_and_run):
        source = r"""
        struct Person { name:text }
        try {
            Person p = '{"name":"\\u12"}'.toStruct(Person)
            log('unreachable')
        } catch (e:text) {
            log(`caught: ${e}`)
        }
        """
        result = compile_and_run(source)
        assert "caught:" in result.stdout

    def test_invalid_hex_digit_in_u_escape_throws(self, compile_and_run):
        source = r"""
        struct Person { name:text }
        try {
            Person p = '{"name":"\\u12zz"}'.toStruct(Person)
            log('unreachable')
        } catch (e:text) {
            log(`caught: ${e}`)
        }
        """
        result = compile_and_run(source)
        assert "invalid \\u escape" in result.stdout
