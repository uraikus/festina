"""claude.md #195: `thread NAME { ... }` -- an isolated background
worker with its own OS thread and message queues to/from the main
program.

This file covers everything checkable at the parser/semantic level
(every test here is parser.parse()+semantic.analyze() only, no
compile-and-run): grammar/AST, the isolation scope (a thread body can
see its own state, function names, and type names, but never a global
variable/constant or an ordinary top-level function), the
sendable-type restriction on messages, the symmetric "no dead sends"
rule in both directions, and (claude.md #199 Phase 5)
`DatabaseURL = '<literal>'` as a thread's own first statement plus the
whole-program database-file conflict check. Real runtime behavior
(message round trips, lifecycle methods, and this same DatabaseURL
feature exercised through a real compile-and-run) lives in
`tests/test_codegen.py`'s own `TestThreads`.
"""
import pytest


class TestThreadDecl:
    """Grammar: `thread NAME { ... }` -- no parens, ever (claude.md
    #195/#208's own "no header type at all" design note: a thread's
    own inbound type is whatever `on message(worker:thread, msg:T)`
    declares inside the body -- DECLARED directly, never inferred,
    the same way main's own top-level `on message` handler declares
    its own `msg` type)."""

    def test_empty_thread_parses_and_analyzes(self, parser, semantic):
        semantic.analyze(parser.parse("thread myWorker { }"))

    def test_thread_with_state_and_all_three_handlers_parses(self, parser, semantic):
        source = """
        thread myWorker {
            map[text] state
            on load() {
                state['ready'] = 'true'
            }
            on message(worker:thread, msg:int) {
                log(msg)
            }
            on exit(code:int) {
            }
        }
        myWorker.postMessage(1)
        """
        semantic.analyze(parser.parse(source))

    def test_duplicate_thread_name_is_rejected(self, parser, semantic, errors):
        program = parser.parse("thread myWorker { }\nthread myWorker { }")
        with pytest.raises(errors.CompileError, match="already declared"):
            semantic.analyze(program)

    def test_thread_name_collides_with_a_function_name(self, parser, semantic, errors):
        program = parser.parse("void func myWorker() { }\nthread myWorker { }")
        with pytest.raises(errors.CompileError, match="already declared"):
            semantic.analyze(program)

    def test_unrecognized_event_name_inside_a_thread_is_rejected(self, parser, semantic, errors):
        # claude.md #195: unlike top-level `on <event>` (an unrecognized
        # name there is tolerated -- claude.md #40 -- since it might be
        # a not-yet-implemented future event), a thread body is a
        # closed construct: an unrecognized `on` name inside one is
        # caught immediately.
        program = parser.parse("thread myWorker { on click(x:int) { } }")
        with pytest.raises(errors.CompileError, match="not a thread event"):
            semantic.analyze(program)

    def test_duplicate_on_load_is_rejected(self, parser, semantic, errors):
        source = "thread myWorker { on load() { } on load() { } }"
        with pytest.raises(errors.CompileError, match="already declares 'on load'"):
            semantic.analyze(parser.parse(source))

    def test_duplicate_on_message_is_rejected(self, parser, semantic, errors):
        source = ("thread myWorker { on message(worker:thread, msg:int) { } "
                   "on message(worker:thread, msg:int) { } }")
        with pytest.raises(errors.CompileError, match="already declares 'on message'"):
            semantic.analyze(parser.parse(source))

    def test_on_message_with_wrong_arity_is_rejected(self, parser, semantic, errors):
        source = "thread myWorker { on message(a:thread, b:int, c:int) { } }"
        with pytest.raises(errors.CompileError, match="must declare"):
            semantic.analyze(parser.parse(source))

    def test_on_load_takes_no_parameters(self, parser, semantic, errors):
        source = "thread myWorker { on load(x:int) { } }"
        with pytest.raises(errors.CompileError, match="no parameters"):
            semantic.analyze(parser.parse(source))

    def test_on_exit_requires_one_int_parameter(self, parser, semantic, errors):
        source = "thread myWorker { on exit() { } }"
        with pytest.raises(errors.CompileError, match=r"\(code:int\)"):
            semantic.analyze(parser.parse(source))

    def test_a_loose_statement_in_the_thread_body_is_rejected(self, parser, semantic, errors):
        # claude.md #195: a thread's own top-level body may only
        # contain state declarations and its three handlers -- there is
        # no "run once at thread start" concept other than on load().
        source = "thread myWorker { log('x') }"
        with pytest.raises(errors.CompileError, match="state declarations"):
            semantic.analyze(parser.parse(source))


class TestThreadIsolation:
    """claude.md #195: a thread body can see its own locals/state,
    function NAMES, and struct/table/enum type names -- never a global
    variable/constant, and never call an ordinary top-level function
    (its body isn't isolated the same way)."""

    def test_referencing_an_outer_global_variable_is_rejected(self, parser, semantic, errors):
        source = """
        int counter = 0
        thread myWorker {
            on load() { counter = counter + 1 }
        }
        """
        with pytest.raises(errors.CompileError, match="unknown variable 'counter'"):
            semantic.analyze(parser.parse(source))

    def test_referencing_an_outer_constant_is_rejected(self, parser, semantic, errors):
        source = """
        const int LIMIT = 10
        thread myWorker {
            on load() { log(LIMIT) }
        }
        """
        with pytest.raises(errors.CompileError, match="unknown variable 'LIMIT'"):
            semantic.analyze(parser.parse(source))

    def test_calling_an_ordinary_top_level_function_is_rejected(self, parser, semantic, errors):
        source = """
        void func helper() { log('hi') }
        thread myWorker {
            on load() { helper() }
        }
        """
        with pytest.raises(errors.CompileError, match="cannot be called from inside a thread body"):
            semantic.analyze(parser.parse(source))

    @pytest.mark.parametrize("builtin_call", [
        "drawRect(0, 0, 10, 10)",
        "render()",
        "saveCanvas()",
        "setTimeout(otherFunc, 100)",
        "exec(['ls'])",
        "openPort(8080)",
    ])
    def test_disallowed_builtins_are_rejected_inside_a_thread(
            self, parser, semantic, errors, builtin_call):
        source = f"""
        void func otherFunc() {{ }}
        thread myWorker {{
            on load() {{ {builtin_call} }}
        }}
        """
        with pytest.raises(errors.CompileError, match="cannot be called from inside a thread body"):
            semantic.analyze(parser.parse(source))

    def test_sqlite_is_rejected_inside_a_thread_with_no_database_url(
            self, parser, semantic, errors):
        # claude.md #199 Phase 5: sqlite() moved off the flat,
        # unconditional _THREAD_DISALLOWED_BUILTINS set above -- it's
        # allowed for a thread that declared its own DatabaseURL (see
        # TestThreadDatabaseUrl below), so a thread that DIDN'T gets its
        # own, more specific error naming the actual fix rather than
        # the generic "touches state shared with the main program" one.
        source = """
        thread myWorker {
            on load() { sqlite('SELECT 1') }
        }
        """
        with pytest.raises(errors.CompileError,
                            match="cannot be called from inside thread 'myWorker' -- "
                                  "it hasn't declared its own database"):
            semantic.analyze(parser.parse(source))

    def test_a_state_var_is_visible_across_every_handler_in_the_same_thread(
            self, parser, semantic):
        source = """
        thread myWorker {
            map[text] state
            on load() { state['a'] = 'x' }
            on message(worker:thread, msg:int) { state['b'] = 'y' }
        }
        myWorker.postMessage(1)
        """
        semantic.analyze(parser.parse(source))

    def test_a_state_var_from_one_thread_is_invisible_in_another(
            self, parser, semantic, errors):
        source = """
        thread a { map[text] state }
        thread b {
            on load() { state['x'] = 'y' }
        }
        """
        with pytest.raises(errors.CompileError, match="unknown variable 'state'"):
            semantic.analyze(parser.parse(source))

    def test_struct_type_names_are_visible_inside_a_thread(self, parser, semantic):
        # claude.md #195: type names (struct/table/enum) are a separate
        # namespace outside Scope entirely, so they're never cut off by
        # the isolation boundary -- only VALUE names (variables,
        # constants, functions) are.
        source = """
        struct Point { x:int y:int }
        thread myWorker {
            on load() {
                Point p
                p.x = 1
            }
        }
        """
        semantic.analyze(parser.parse(source))

    def test_a_struct_field_used_inside_a_thread_resolves_even_when_declared_after(
            self, parser, semantic, errors):
        # claude.md #195: this is the actual bug an earlier draft of
        # this feature had (a special early analysis pass, positioned
        # before struct FIELDS were resolved -- see analyze_thread's
        # own comment). Threads now analyze at ordinary third-pass
        # position, so this needs the struct declared BEFORE the
        # thread, exactly like an ordinary global variable already
        # needs to be (see the sibling test below) -- not hoisted.
        source = """
        struct Packet { username:text }
        thread myWorker {
            on message(worker:thread, msg:Packet) { log(msg.username) }
        }
        myWorker.postMessage(p)
        """
        # p is never declared as a variable here -- this specific
        # source is expected to fail on THAT, not on field resolution;
        # the real point is captured by the codegen-level round-trip
        # test in TestThreadCodegenStub below instead. This test only
        # pins that struct field resolution itself doesn't blow up.
        with pytest.raises(errors.CompileError, match="unknown variable 'p'"):
            semantic.analyze(parser.parse(source))

    def test_a_thread_declared_before_a_global_it_would_reference_still_isolates(
            self, parser, semantic, errors):
        # Order doesn't rescue a global reference either way -- it's
        # unconditionally invisible from inside a thread body,
        # regardless of whether the global comes before or after.
        source = """
        thread myWorker {
            on load() { log(counter) }
        }
        int counter = 0
        """
        with pytest.raises(errors.CompileError, match="unknown variable 'counter'"):
            semantic.analyze(parser.parse(source))


class TestThreadSendableTypes:
    """claude.md #195: a message may only be a type this runtime can
    mechanically deep-clone with no shared OS resource."""

    @pytest.mark.parametrize("msg_type", ["int", "float", "bool", "text", "blob", "img", "aud", "url"])
    def test_scalar_and_handle_types_are_sendable(self, parser, semantic, msg_type):
        source = f"thread myWorker {{ on message(worker:thread, msg:{msg_type}) {{ }} }}"
        semantic.analyze(parser.parse(source))

    @pytest.mark.parametrize("msg_type", ["http", "socket", "regex"])
    def test_connection_bound_types_are_rejected(self, parser, semantic, errors, msg_type):
        source = f"thread myWorker {{ on message(worker:thread, msg:{msg_type}) {{ }} }}"
        with pytest.raises(errors.CompileError, match="cannot cross a thread boundary"):
            semantic.analyze(parser.parse(source))

    def test_a_struct_containing_a_socket_field_is_rejected(self, parser, semantic, errors):
        source = """
        struct Bad { s:socket }
        thread myWorker {
            on message(worker:thread, msg:Bad) { }
        }
        """
        with pytest.raises(errors.CompileError, match="cannot cross a thread boundary"):
            semantic.analyze(parser.parse(source))

    def test_a_plain_struct_is_sendable(self, parser, semantic):
        source = """
        struct Packet { username:text data:int }
        thread myWorker {
            on message(worker:thread, msg:Packet) { }
        }
        """
        semantic.analyze(parser.parse(source))

    def test_arr_and_map_of_sendable_element_types_are_sendable(self, parser, semantic):
        source = """
        thread a { on message(worker:thread, msg:arr[int]) { } }
        thread b { on message(worker:thread, msg:map[text]) { } }
        """
        semantic.analyze(parser.parse(source))

    def test_an_enum_of_sendable_members_is_sendable(self, parser, semantic):
        source = """
        enum DataPacket = int, text
        thread myWorker {
            on message(worker:thread, msg:DataPacket) { }
        }
        """
        semantic.analyze(parser.parse(source))


class TestThreadMessagePassing:
    """claude.md #208: `NAME.postMessage(x)` (main -> thread, or thread
    -> thread) and bare `postMessage(x)` (thread -> main) both check
    against a receiver's own DECLARED `on message(worker:thread, msg:T)`
    handler -- never inferred/merged. Since a real analysis pass runs
    in strict textual program order (see analyze()'s own third loop),
    a receiver's `on message` handler must be declared textually
    BEFORE any send that targets it is analyzed, exactly like any other
    "declared before referenced" rule this language already has."""

    def test_postmessage_type_must_match_the_inbound_type(self, parser, semantic, errors):
        source = """
        thread myWorker { on message(worker:thread, msg:int) { } }
        myWorker.postMessage('wrong type')
        """
        with pytest.raises(errors.CompileError, match="postMessage"):
            semantic.analyze(parser.parse(source))

    def test_postmessage_to_a_thread_with_no_on_message_handler_is_rejected(
            self, parser, semantic, errors):
        source = """
        thread myWorker { on load() { } }
        myWorker.postMessage(5)
        """
        with pytest.raises(errors.CompileError, match="declares no 'on message' handler"):
            semantic.analyze(parser.parse(source))

    def test_a_bare_postmessage_with_no_top_level_on_message_handler_is_rejected(
            self, parser, semantic, errors):
        # claude.md #208: replaces the old, end-of-analyze() "no dead
        # sends" check -- a bare postMessage(x) targets main, checked
        # directly against `_main_message_type[0]`, which stays None
        # when the program never declares a top-level
        # `on message(worker:thread, msg:T)` handler at all.
        source = "thread myWorker { on load() { postMessage(1) } }"
        with pytest.raises(errors.CompileError, match="no top-level 'on message"):
            semantic.analyze(parser.parse(source))

    def test_a_thread_that_never_posts_to_main_needs_no_top_level_handler(
            self, parser, semantic):
        # A thread that only ever RECEIVES messages (from main, via the
        # named form), never sends any of its own, needs no top-level
        # `on message` declared anywhere -- nothing here would ever
        # need one.
        source = """
        thread myWorker { on message(worker:thread, msg:int) { } }
        myWorker.postMessage(5)
        """
        semantic.analyze(parser.parse(source))

    def test_bare_postmessage_requires_the_top_level_handler_declared_first(
            self, parser, semantic, errors):
        # Analysis runs in strict textual program order -- a bare
        # postMessage(x) call site analyzed BEFORE the top-level
        # `on message` handler it would target is analyzed sees
        # `_main_message_type[0]` still None, same as if no handler
        # were declared anywhere at all.
        source = """
        thread myWorker { on load() { postMessage(1) } }
        on message(worker:thread, msg:int) { log(msg) }
        """
        with pytest.raises(errors.CompileError, match="no top-level 'on message"):
            semantic.analyze(parser.parse(source))

    def test_bare_postmessage_works_once_the_top_level_handler_is_declared_first(
            self, parser, semantic):
        source = """
        on message(worker:thread, msg:int) { log(msg) }
        thread myWorker { on load() { postMessage(1) } }
        """
        semantic.analyze(parser.parse(source))

    def test_bare_postmessage_argument_type_must_match_the_top_level_handler(
            self, parser, semantic, errors):
        source = """
        on message(worker:thread, msg:text) { log(msg) }
        thread myWorker { on load() { postMessage(1) } }
        """
        with pytest.raises(errors.CompileError, match="postMessage"):
            semantic.analyze(parser.parse(source))

    def test_a_second_postmessage_call_with_a_mismatched_type_is_rejected(
            self, parser, semantic, errors):
        # claude.md #208: there is no more "posts more than one type"
        # inference -- `msg`'s type is DECLARED once, by the top-level
        # `on message` handler, and every send checks against that
        # fixed type directly (an earlier design auto-synthesized an
        # anonymous enum from scattered call sites instead, and it was
        # a dead end -- see _check_message_handler_params's own
        # history comment).
        source = """
        on message(worker:thread, msg:int) { log(msg) }
        thread myWorker {
            on load() {
                postMessage(1)
                postMessage('x')
            }
        }
        """
        with pytest.raises(errors.CompileError, match="postMessage"):
            semantic.analyze(parser.parse(source))

    def test_postmessage_of_multiple_enum_member_types_works_via_the_declared_enum(
            self, parser, semantic):
        # The fix the rejection above points at: assigning `1`/`'x'`
        # INTO an `Out`-typed local first (check_assignable's own
        # enum-member coercion, claude.md #176) makes each call site's
        # argument type already EnumType('Out') by the time postMessage
        # checks it against the top-level handler's own declared
        # `msg:Out` type.
        source = """
        enum Out = int, text
        on message(worker:thread, msg:Out) { log(typeof msg) }
        thread myWorker {
            on load() {
                Out a = 1
                postMessage(a)
                Out b = 'x'
                postMessage(b)
            }
        }
        """
        analyzed = semantic.analyze(parser.parse(source))
        assert analyzed.main_message_type is not None

    def test_worker_parameter_is_null_when_sent_by_main(self, parser, semantic):
        source = """
        on message(worker:thread, msg:int) {
            if (worker == null) { log('from main') }
        }
        thread myWorker { on load() { postMessage(1) } }
        """
        semantic.analyze(parser.parse(source))

    def test_two_thread_values_cannot_be_compared_to_each_other(
            self, parser, semantic, errors):
        # claude.md #208: `worker` may only ever be compared against
        # null -- comparing two real thread values against each other
        # hits the invalid-LLVM-IR struct-equality hazard this
        # language's `==`/`!=` codegen has for any non-null pointer-
        # shaped comparison, so it's rejected here at the semantic
        # layer instead, with a clear Festina-level message. (There is
        # no way to spell a SECOND, distinct `thread`-typed binding in
        # ordinary Festina code at all -- `thread` is deliberately not
        # constructible, only ever delivered via `worker` -- so this
        # compares `worker` against itself, which is enough to exercise
        # the "two thread values" guard either way.)
        source = """
        on message(worker:thread, msg:int) {
            if (worker == worker) { log('x') }
        }
        thread myWorker { on load() { postMessage(1) } }
        """
        with pytest.raises(errors.CompileError, match="between two thread values"):
            semantic.analyze(parser.parse(source))

    def test_a_thread_may_postmessage_another_thread_directly(self, parser, semantic):
        # claude.md #208: the "messaging only" inter-thread capability
        # -- a thread body may NAME.postMessage(x) another thread
        # directly, not just main.
        source = """
        thread a { on message(worker:thread, msg:int) { } }
        thread b { on load() { a.postMessage(1) } }
        """
        semantic.analyze(parser.parse(source))

    def test_a_thread_still_cannot_kill_live_or_isalive_another_thread(
            self, parser, semantic, errors):
        source = """
        thread a { on load() { } }
        thread b { on load() { a.kill() } }
        """
        with pytest.raises(errors.CompileError, match="cannot be called from inside a thread body"):
            semantic.analyze(parser.parse(source))


class TestThreadLifecycleMethods:
    """claude.md #195: .kill()/.live(callback)/.isAlive() -- main-
    program-only, never callable from inside any thread's own body."""

    def test_kill_takes_no_arguments(self, parser, semantic, errors):
        source = """
        thread myWorker { on load() { } }
        myWorker.kill(1)
        """
        with pytest.raises(errors.CompileError, match="kill\\(\\) expects no arguments"):
            semantic.analyze(parser.parse(source))

    def test_live_requires_a_bool_callback(self, parser, semantic, errors):
        source = """
        thread myWorker { on load() { } }
        myWorker.live(void (ok:int) => log(ok))
        """
        with pytest.raises(errors.CompileError, match="live\\(\\) expects func\\[bool\\]:void"):
            semantic.analyze(parser.parse(source))

    def test_isalive_returns_bool(self, parser, semantic):
        source = """
        thread myWorker { on load() { } }
        bool alive = myWorker.isAlive()
        """
        semantic.analyze(parser.parse(source))

    @pytest.mark.parametrize("call", ["kill()", "isAlive()", "live(void (ok:bool) => log(ok))"])
    def test_lifecycle_methods_are_rejected_from_inside_any_thread_body(
            self, parser, semantic, errors, call):
        source = f"""
        thread a {{ on load() {{ }} }}
        thread b {{ on load() {{ a.{call} }} }}
        """
        with pytest.raises(errors.CompileError, match="cannot be called from inside a thread body"):
            semantic.analyze(parser.parse(source))

    def test_unknown_thread_method_is_rejected(self, parser, semantic, errors):
        source = """
        thread myWorker { on load() { } }
        myWorker.explode()
        """
        with pytest.raises(errors.CompileError, match="has no method 'explode'"):
            semantic.analyze(parser.parse(source))


class TestThreadDatabaseUrl:
    """claude.md #199 Phase 5: `DatabaseURL = '<literal>'` as a thread's
    own first statement -- its own private sqlite handle (gating
    sqlite()/sqliteInt()/sqliteFloat()/sqliteText() is covered by
    TestThreadIsolation's own test above, since it's really an
    isolation question), plus the whole-program compile-time conflict
    check (main included). Every test here declares an obviously
    thread-specific literal path (never 'festina.sqlite') -- a bare
    parser.parse()/semantic.analyze() pair, unlike festina.imports.
    build_program, never sets Program.database_url at all, so the
    conflict check's own main-program fallback always treats a
    file-less parse as using the default 'festina.sqlite' path (the
    right conservative answer when nothing proves otherwise -- see
    analyze()'s own db_contexts comment)."""

    def test_a_valid_database_url_lets_the_thread_call_sqlite(self, parser, semantic):
        source = """
        thread worker {
            DatabaseURL = 'worker_db.sqlite'
            on load() { sqlite('SELECT 1') }
        }
        """
        semantic.analyze(parser.parse(source))

    def test_database_url_after_another_statement_is_rejected(self, parser, semantic, errors):
        source = """
        thread worker {
            on load() { log('hi') }
            DatabaseURL = 'worker_db.sqlite'
        }
        """
        with pytest.raises(errors.CompileError,
                            match="must be the first statement in the thread's own body"):
            semantic.analyze(parser.parse(source))

    @pytest.mark.parametrize("value_expr", [
        "name",              # a plain identifier
        "`db_${name}.sqlite`",  # a template literal
        "name + '.sqlite'",  # a concatenation
    ])
    def test_a_non_literal_database_url_is_rejected(self, parser, semantic, errors, value_expr):
        source = f"""
        text name = 'worker'
        thread worker {{
            DatabaseURL = {value_expr}
            on load() {{ log('hi') }}
        }}
        """
        with pytest.raises(errors.CompileError,
                            match="DatabaseURL must be a plain string literal"):
            semantic.analyze(parser.parse(source))

    def test_two_threads_with_the_same_literal_database_url_are_rejected(
            self, parser, semantic, errors):
        source = """
        thread a {
            DatabaseURL = 'shared_worker_db.sqlite'
            on load() { sqlite('SELECT 1') }
        }
        thread b {
            DatabaseURL = 'shared_worker_db.sqlite'
            on load() { sqlite('SELECT 1') }
        }
        """
        with pytest.raises(errors.CompileError,
                            match="thread 'a' and thread 'b' would both open the same "
                                  "database file"):
            semantic.analyze(parser.parse(source))

    def test_two_threads_with_distinct_literal_database_urls_are_fine(
            self, parser, semantic):
        source = """
        thread a {
            DatabaseURL = 'worker_a_db.sqlite'
            on load() { sqlite('SELECT 1') }
        }
        thread b {
            DatabaseURL = 'worker_b_db.sqlite'
            on load() { sqlite('SELECT 1') }
        }
        """
        semantic.analyze(parser.parse(source))

    def test_a_thread_sharing_an_explicit_main_database_url_is_rejected(
            self, parser, semantic, errors, ast_mod):
        # festina.imports.build_program (not exercised by a bare
        # parser.parse() call) is what normally sets Program.
        # database_url from the entry file's own leading `DatabaseURL =
        # '<expr>'` statement -- set directly here to exercise the
        # conflict check's MAIN-program-is-explicit-and-literal branch
        # without going through the full file-based compile pipeline
        # (see TestThreads.test_a_thread_sharing_the_main_programs_
        # database_url_is_a_clear_compile_error in test_codegen.py for
        # that same scenario exercised end to end).
        source = """
        thread worker {
            DatabaseURL = 'shared.sqlite'
            on load() { sqlite('SELECT 1') }
        }
        """
        program = parser.parse(source)
        program.database_url = ast_mod.StringLit("shared.sqlite")
        with pytest.raises(errors.CompileError,
                            match="the main program and thread 'worker' would both "
                                  "open the same database file"):
            semantic.analyze(program)

    def test_a_thread_sharing_the_implicit_default_main_database_is_rejected(
            self, parser, semantic, errors):
        # No explicit main DatabaseURL at all (program.database_url is
        # None, both here -- a bare parse -- and for a real file that
        # never assigns one) still means the main program opens
        # 'festina.sqlite' by default, and that default counts too.
        source = """
        thread worker {
            DatabaseURL = 'festina.sqlite'
            on load() { sqlite('SELECT 1') }
        }
        """
        with pytest.raises(errors.CompileError,
                            match="the main program and thread 'worker' would both "
                                  "open the same database file \\('festina.sqlite'\\)"):
            semantic.analyze(parser.parse(source))


class TestThreadReservedName:
    def test_postmessage_cannot_be_declared_as_a_function(self, parser, semantic, errors):
        program = parser.parse("void func postMessage() { }")
        with pytest.raises(errors.CompileError, match="builtin function name"):
            semantic.analyze(program)
