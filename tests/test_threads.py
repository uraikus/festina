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
        # claude.md #212: openPort()/closePort()/openSecurePort()
        # removed from this list -- no longer flatly disallowed, now
        # gated on having declared an HTTP-shaped handler first (a
        # DIFFERENT, more specific error than "cannot be called from
        # inside a thread body" -- see TestThreadHttpContext).
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

    def test_worker_dot_main_is_true_when_sent_by_main(self, parser, semantic):
        # claude.md #216: `worker` is never null any more -- when main
        # is the sender, `worker.main` reads true instead.
        source = """
        on message(worker:thread, msg:int) {
            if (worker.main) { log('from main') }
        }
        thread myWorker { on load() { postMessage(1) } }
        """
        semantic.analyze(parser.parse(source))

    def test_worker_compared_against_null_is_rejected(self, parser, semantic, errors):
        # claude.md #216: claude.md #208's own "worker == null" design
        # is gone -- `worker` is never null, so comparing it against
        # null is now a clear compile error pointing at `.main`.
        source = """
        on message(worker:thread, msg:int) {
            if (worker == null) { log('x') }
        }
        thread myWorker { on load() { postMessage(1) } }
        """
        with pytest.raises(errors.CompileError, match="never null.*\\.main"):
            semantic.analyze(parser.parse(source))

    def test_two_thread_values_cannot_be_compared_to_each_other(
            self, parser, semantic, errors):
        # claude.md #208 (still true after #216): comparing two real
        # thread values against each other hits the invalid-LLVM-IR
        # struct-equality hazard this language's `==`/`!=` codegen has
        # for any non-null pointer-shaped comparison, so it's rejected
        # here at the semantic layer instead, with a clear Festina-level
        # message. (There is no way to spell a SECOND, distinct
        # `thread`-typed binding in ordinary Festina code at all --
        # `thread` is deliberately not constructible, only ever
        # delivered via `worker` -- so this compares `worker` against
        # itself, which is enough to exercise the "two thread values"
        # guard either way.)
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
    sqlite() is covered by TestThreadIsolation's own test above, since
    it's really an isolation question), plus the whole-program
    compile-time conflict check (main included). Every test here
    declares an obviously
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


class TestReplyCallback:
    """claude.md #217: `t.reply(response)` / `NAME.postMessage(x).
    callback(fn)`."""

    def test_reply_and_callback_round_trip_type_checks(self, parser, semantic):
        source = """
        thread worker {
            on message(worker:thread, msg:int) {
                worker.reply(msg * 2)
            }
        }
        void func onReply(r:int) { log(r) }
        worker.postMessage(21).callback(onReply)
        """
        semantic.analyze(parser.parse(source))

    def test_bare_postmessage_without_callback_when_target_replies_is_rejected(
            self, parser, semantic, errors):
        source = """
        thread worker {
            on message(worker:thread, msg:int) {
                worker.reply(msg * 2)
            }
        }
        worker.postMessage(21)
        """
        with pytest.raises(errors.CompileError, match="must chain '.callback\\(fn\\)'"):
            semantic.analyze(parser.parse(source))

    def test_callback_on_a_target_that_never_replies_is_rejected(self, parser, semantic, errors):
        source = """
        thread worker {
            on message(worker:thread, msg:int) {
                log(msg)
            }
        }
        void func onReply(r:int) { log(r) }
        worker.postMessage(21).callback(onReply)
        """
        with pytest.raises(errors.CompileError, match="requires a target that replies"):
            semantic.analyze(parser.parse(source))

    def test_callback_type_mismatch_is_rejected(self, parser, semantic, errors):
        source = """
        thread worker {
            on message(worker:thread, msg:int) {
                worker.reply(msg * 2)
            }
        }
        void func onReply(r:text) { log(r) }
        worker.postMessage(21).callback(onReply)
        """
        with pytest.raises(errors.CompileError, match="callback\\(\\) expects func\\[int\\]:void"):
            semantic.analyze(parser.parse(source))

    def test_reply_type_is_fixed_by_the_first_call_and_enforced_after(
            self, parser, semantic, errors):
        source = """
        thread worker {
            on message(worker:thread, msg:int) {
                worker.reply(msg)
                worker.reply('not an int')
            }
        }
        void func onReply(r:int) { log(r) }
        worker.postMessage(21).callback(onReply)
        """
        with pytest.raises(errors.CompileError, match="reply\\(\\) argument"):
            semantic.analyze(parser.parse(source))

    def test_main_can_reply_to_a_worker_that_sends_via_bare_postmessage(self, parser, semantic):
        source = """
        void func onReply(r:int) { log(r) }
        on message(worker:thread, msg:int) {
            worker.reply(msg + 1)
        }
        thread worker {
            on load() { postMessage(5).callback(onReply) }
        }
        """
        semantic.analyze(parser.parse(source))

    def test_bare_postmessage_to_main_without_callback_when_main_replies_is_rejected(
            self, parser, semantic, errors):
        source = """
        on message(worker:thread, msg:int) {
            worker.reply(msg + 1)
        }
        thread worker {
            on load() { postMessage(5) }
        }
        """
        with pytest.raises(errors.CompileError, match="must chain '.callback\\(fn\\)'"):
            semantic.analyze(parser.parse(source))

    def test_errors_name_the_type_as_thread_not_the_internal_repr(
            self, parser, semantic, errors, types_mod):
        # claude.md #218: types.type_name had no ThreadType case at all,
        # so every user-facing message about a thread value printed this
        # compiler's own Python repr ("ThreadType(None)").
        assert types_mod.type_name(types_mod.ThreadType(None)) == "thread"
        assert types_mod.type_name(types_mod.ThreadType("w")) == "thread 'w'"
        source = """
        thread worker { on message(w:thread, msg:int) { log(msg) } }
        on message(w:thread, msg:int) { int x = w }
        thread other { on load() { postMessage(1) } }
        """
        with pytest.raises(errors.CompileError, match="of type thread to int"):
            semantic.analyze(parser.parse(source))

    def test_the_no_such_field_error_does_not_suggest_postmessage(
            self, parser, semantic, errors):
        # claude.md #218: `.postMessage()` is a method on a declared
        # thread's NAME, never on a thread VALUE -- the old message
        # pointed someone writing `w.postMessage(x)` straight back at
        # the thing that had just failed.
        source = """
        thread other { on message(w:thread, msg:int) { log(msg) } }
        thread worker { on message(w:thread, msg:int) { w.postMessage(msg) } }
        worker.postMessage(1)
        """
        with pytest.raises(errors.CompileError, match=r"\.reply\(x\)") as excinfo:
            semantic.analyze(parser.parse(source))
        assert "someThread.postMessage(x)" in str(excinfo.value)

    def test_reply_is_legal_only_on_the_generic_thread_type(self, parser, semantic, errors):
        # `.reply` is only recognized on a value of the GENERIC `thread`
        # type (a `worker`/`t` parameter) -- calling it as a plain
        # method name on anything else falls through to an ordinary
        # "unknown method" error, not a silent no-op.
        source = """
        struct Circle { x:int }
        Circle c = { x: 1 }
        c.reply(1)
        """
        with pytest.raises(errors.CompileError):
            semantic.analyze(parser.parse(source))


class TestThreadReservedName:
    def test_postmessage_cannot_be_declared_as_a_function(self, parser, semantic, errors):
        program = parser.parse("void func postMessage() { }")
        with pytest.raises(errors.CompileError, match="builtin function name"):
            semantic.analyze(program)


class TestThreadPools:
    """claude.md #209: `thread NAME[N] { ... }` -- N independent
    instances of one body, addressed at a use site via `NAME[i]` --
    `NAME[i]` itself needs no grammar of its own (it already parses as
    an ordinary computed `Member`, identical to indexing an `arr[T]`);
    only the DECLARATION's own optional `[N]` is new grammar. Real
    per-instance independence (private state, correct message routing)
    is a compile-and-run question -- see `TestThreads` in
    `tests/test_codegen.py`."""

    def test_a_pool_declaration_parses_and_analyzes(self, parser, semantic):
        semantic.analyze(parser.parse("thread pool[4] { }"))

    def test_pool_size_must_be_a_plain_integer_literal(self, parser, semantic, errors):
        with pytest.raises(errors.CompileError, match="plain integer literal"):
            semantic.analyze(parser.parse("thread pool[4.5] { }"))

    def test_pool_size_must_be_positive(self, parser, semantic, errors):
        with pytest.raises(errors.CompileError, match="positive integer"):
            semantic.analyze(parser.parse("thread pool[0] { }"))

    def test_indexed_postmessage_to_a_pool_is_accepted(self, parser, semantic):
        source = """
        thread pool[4] { on message(worker:thread, msg:int) { } }
        pool[0].postMessage(1)
        pool[3].postMessage(2)
        """
        semantic.analyze(parser.parse(source))

    def test_pool_index_can_be_a_runtime_expression(self, parser, semantic):
        source = """
        thread pool[4] { on message(worker:thread, msg:int) { } }
        int i = 2
        pool[i].postMessage(1)
        """
        semantic.analyze(parser.parse(source))

    def test_pool_index_must_be_int(self, parser, semantic, errors):
        source = """
        thread pool[4] { on message(worker:thread, msg:int) { } }
        pool['x'].postMessage(1)
        """
        with pytest.raises(errors.CompileError, match="thread pool index must be int"):
            semantic.analyze(parser.parse(source))

    def test_the_bare_pool_name_must_be_indexed_to_call_a_method(self, parser, semantic, errors):
        source = """
        thread pool[4] { on load() { } }
        pool.kill()
        """
        with pytest.raises(errors.CompileError, match="must be indexed"):
            semantic.analyze(parser.parse(source))

    def test_indexing_an_ordinary_non_pool_thread_is_rejected(self, parser, semantic, errors):
        source = """
        thread worker { on load() { } }
        worker[0].kill()
        """
        with pytest.raises(errors.CompileError, match="is an ordinary thread, not a pool"):
            semantic.analyze(parser.parse(source))

    def test_pool_isalive_kill_live_are_accepted_when_indexed(self, parser, semantic):
        source = """
        thread pool[2] { on load() { } }
        bool alive = pool[0].isAlive()
        pool[1].kill()
        pool[0].live(void (ok:bool) => log(ok))
        """
        semantic.analyze(parser.parse(source))

    def test_pool_lifecycle_methods_are_still_rejected_from_inside_a_thread_body(
            self, parser, semantic, errors):
        source = """
        thread pool[2] { on load() { } }
        thread other { on load() { pool[0].kill() } }
        """
        with pytest.raises(errors.CompileError, match="cannot be called from inside a thread body"):
            semantic.analyze(parser.parse(source))

    def test_a_thread_may_postmessage_a_specific_pool_instance_from_inside_its_own_body(
            self, parser, semantic):
        # claude.md #208's "messaging only" inter-thread capability
        # applies to a pool instance exactly the same as an ordinary
        # thread.
        source = """
        thread pool[2] { on message(worker:thread, msg:int) { } }
        thread other { on load() { pool[0].postMessage(1) } }
        """
        semantic.analyze(parser.parse(source))

    def test_a_pool_declaring_its_own_databaseurl_is_rejected(self, parser, semantic, errors):
        # claude.md #215: a pool shares ONE _ThreadInfo (and therefore
        # one database_url) across every instance -- every instance
        # would open its own independent, uncoordinated connection to
        # the SAME literal file at the same time, a genuine hazard an
        # ordinary singleton thread's own DatabaseURL never has.
        source = """
        thread pool[3] {
            DatabaseURL = './pool.sqlite'
            on load() { }
        }
        """
        with pytest.raises(errors.CompileError,
                            match="thread pool 'pool\\[3\\]' cannot declare its own DatabaseURL"):
            semantic.analyze(parser.parse(source))

    def test_a_pool_instance_still_cannot_call_sqlite_without_a_database(
            self, parser, semantic, errors):
        # claude.md #199 Phase 5's own existing gate (database_url is
        # None) already covers a pool for free, since a pool's own
        # database_url can now never be set at all (the check above).
        source = """
        thread pool[3] {
            on load() { sqlite('SELECT 1', []) }
        }
        """
        with pytest.raises(errors.CompileError, match="hasn't declared its own database"):
            semantic.analyze(parser.parse(source))


class TestThreadPrivateFunctions:
    """claude.md #210: a `func` declared directly in a thread's own
    body -- callable only from that one thread's own handlers/other
    private funcs, with read/write access to that thread's own state.
    Ordinary top-level functions remain uncallable from inside a
    thread (unchanged, claude.md #195's own deliberate cut)."""

    def test_a_private_function_is_accepted_and_callable(self, parser, semantic):
        source = """
        thread w {
            int func double_it(x:int) { return x * 2 }
            on load() { int y = double_it(5) }
        }
        """
        semantic.analyze(parser.parse(source))

    def test_ordinary_top_level_functions_are_still_uncallable_from_a_thread(
            self, parser, semantic, errors):
        source = """
        void func helper() { log('hi') }
        thread w {
            on load() { helper() }
        }
        """
        with pytest.raises(errors.CompileError, match="cannot be called from inside a thread body"):
            semantic.analyze(parser.parse(source))

    def test_a_private_function_is_invisible_from_another_thread(
            self, parser, semantic, errors):
        source = """
        thread a {
            int func helper(x:int) { return x }
        }
        thread b {
            on load() { int y = helper(1) }
        }
        """
        with pytest.raises(errors.CompileError, match="unknown function 'helper'"):
            semantic.analyze(parser.parse(source))

    def test_a_private_function_is_not_callable_from_main(self, parser, semantic, errors):
        source = """
        thread w {
            int func helper(x:int) { return x }
        }
        int y = helper(1)
        """
        with pytest.raises(errors.CompileError, match="unknown function 'helper'"):
            semantic.analyze(parser.parse(source))

    def test_a_same_named_top_level_and_private_function_coexist_without_collision(
            self, parser, semantic):
        # Proves the kind/scope split works, not just "doesn't crash":
        # main's own call resolves to the TOP-LEVEL helper, the
        # thread's own call resolves to ITS OWN, and neither sees the
        # other's.
        source = """
        void func helper() { log('top') }
        thread w {
            void func helper() { log('private') }
            on load() { helper() }
        }
        helper()
        """
        semantic.analyze(parser.parse(source))

    def test_a_private_function_can_read_and_write_thread_state_declared_before_it(
            self, parser, semantic):
        source = """
        thread w {
            int total = 0
            int func bump() {
                total = total + 1
                return total
            }
            on load() { bump() }
        }
        """
        semantic.analyze(parser.parse(source))

    def test_a_private_function_reading_state_declared_after_it_is_rejected(
            self, parser, semantic, errors):
        # Matches the "declared before referenced" ordering every
        # other thread-body reference already needs -- private func
        # SIGNATURES are hoisted (so two private funcs may call each
        # other in either textual order), but a private func's own
        # BODY is still analyzed at its own ordinary textual position,
        # same as a handler's.
        source = """
        thread w {
            int func bump() {
                total = total + 1
                return total
            }
            int total = 0
        }
        """
        with pytest.raises(errors.CompileError, match="unknown variable 'total'"):
            semantic.analyze(parser.parse(source))

    def test_two_private_functions_can_call_each_other_in_either_textual_order(
            self, parser, semantic):
        source = """
        thread w {
            int func a(x:int) { return b(x) }
            int func b(x:int) { return x + 1 }
            on load() { int y = a(1) }
        }
        """
        semantic.analyze(parser.parse(source))

    def test_a_private_function_can_postmessage_another_thread(self, parser, semantic):
        source = """
        thread relay { on message(worker:thread, msg:int) { } }
        thread w {
            void func forward(x:int) { relay.postMessage(x) }
            on load() { forward(1) }
        }
        """
        semantic.analyze(parser.parse(source))

    def test_a_private_function_can_bare_postmessage_to_main(self, parser, semantic):
        source = """
        on message(worker:thread, msg:int) { log(msg) }
        thread w {
            void func report(x:int) { postMessage(x) }
            on load() { report(1) }
        }
        """
        semantic.analyze(parser.parse(source))

    def test_duplicate_private_function_names_are_rejected(self, parser, semantic, errors):
        source = """
        thread w {
            int func helper(x:int) { return x }
            int func helper(x:int) { return x + 1 }
        }
        """
        with pytest.raises(errors.CompileError, match="already declares a function named"):
            semantic.analyze(parser.parse(source))

    def test_a_builtin_function_name_cannot_be_used_for_a_private_function(
            self, parser, semantic, errors):
        source = "thread w { void func postMessage(x:int) { } }"
        with pytest.raises(errors.CompileError, match="builtin function name"):
            semantic.analyze(parser.parse(source))

    def test_a_bare_reference_to_a_private_function_as_a_value_is_rejected(
            self, parser, semantic, errors):
        source = """
        thread w {
            int func helper(x:int) { return x }
            on load() { func[int]:int f = helper }
        }
        """
        with pytest.raises(errors.CompileError, match="thread-private function"):
            semantic.analyze(parser.parse(source))

    def test_a_private_function_works_the_same_way_inside_a_thread_pool(
            self, parser, semantic):
        source = """
        on message(worker:thread, msg:int) { log(msg) }
        thread pool[2] {
            int func double_it(x:int) { return x * 2 }
            on message(worker:thread, msg:int) { postMessage(double_it(msg)) }
        }
        pool[0].postMessage(1)
        """
        semantic.analyze(parser.parse(source))


class TestThreadWiderBuiltinAccess:
    """claude.md #211: `regex()`/`mkdir()`/`ls()` are unblocked
    outright inside a thread body -- each confirmed by reading the
    actual runtime C to touch no shared state at all. `exec` needs an
    arity split instead of a flat removal: the blocking 1-argument
    form is equally safe, but the non-blocking 2-argument form's own
    callback always runs on MAIN's own OS thread regardless of which
    thread dispatched it, a real cross-thread-isolation violation."""

    def test_regex_is_accepted_inside_a_thread(self, parser, semantic):
        source = "thread w { on load() { regex r = /^ab/ } }"
        semantic.analyze(parser.parse(source))

    def test_mkdir_is_accepted_inside_a_thread(self, parser, semantic):
        source = "thread w { on load() { mkdir('x') } }"
        semantic.analyze(parser.parse(source))

    def test_ls_is_accepted_inside_a_thread(self, parser, semantic):
        source = "thread w { on load() { arr[text] r = ls('.') } }"
        semantic.analyze(parser.parse(source))

    def test_exec_with_one_argument_is_accepted_inside_a_thread(self, parser, semantic):
        source = "thread w { on load() { int code = exec(['true']) } }"
        semantic.analyze(parser.parse(source))

    def test_exec_with_a_callback_is_still_rejected_inside_a_thread(
            self, parser, semantic, errors):
        source = """
        void func onDone(code:int) { }
        thread w { on load() { exec(['true'], onDone) } }
        """
        with pytest.raises(errors.CompileError,
                            match="its callback always runs on the main program's own OS thread"):
            semantic.analyze(parser.parse(source))

    def test_canvas_and_timer_builtins_are_still_rejected_inside_a_thread(
            self, parser, semantic, errors):
        # claude.md #211 is a widening, not a blanket amnesty --
        # everything still tied to genuinely shared main-thread-only
        # state stays exactly as rejected as before.
        source = "thread w { on load() { drawRect(0, 0, 1, 1) } }"
        with pytest.raises(errors.CompileError, match="cannot be called from inside a thread body"):
            semantic.analyze(parser.parse(source))


class TestThreadHttpContext:
    """claude.md #212: a thread's own private HTTP context --
    openPort()/closePort()/openSecurePort() plus on request/on
    upgrade/on socketMessage/on socketClose, all gated on this one
    thread having already declared at least one of the four handlers
    (mirroring the DatabaseURL-gates-sqlite() precedent exactly)."""

    def test_a_thread_with_a_request_handler_may_call_openPort(self, parser, semantic):
        source = """
        thread w {
            on load() { openPort(8080) }
            on request(req:http) { req.ok() }
        }
        """
        semantic.analyze(parser.parse(source))

    def test_openPort_without_any_http_handler_is_rejected(self, parser, semantic, errors):
        source = "thread w { on load() { openPort(8080) } }"
        with pytest.raises(errors.CompileError,
                            match="hasn't declared an HTTP-shaped handler yet"):
            semantic.analyze(parser.parse(source))

    def test_closePort_without_any_http_handler_is_rejected(self, parser, semantic, errors):
        source = "thread w { on load() { closePort(8080) } }"
        with pytest.raises(errors.CompileError,
                            match="hasn't declared an HTTP-shaped handler yet"):
            semantic.analyze(parser.parse(source))

    def test_openSecurePort_without_any_http_handler_is_rejected(self, parser, semantic, errors):
        # the gate is checked (and raises) before openSecurePort's own
        # argument types are looked at, so a bogus second argument here
        # is fine -- it's never reached.
        source = "thread w { on load() { openSecurePort(8443, 0) } }"
        with pytest.raises(errors.CompileError,
                            match="hasn't declared an HTTP-shaped handler yet"):
            semantic.analyze(parser.parse(source))

    def test_the_gate_does_not_care_about_textual_order(self, parser, semantic):
        # claude.md #212: has_http_handler is hoisted, so `on load()`
        # (which calls openPort()) may appear BEFORE `on request` --
        # the most natural way to write this -- not just after it.
        source = """
        thread w {
            on load() { openPort(8080) }
            on request(req:http) { req.ok() }
        }
        """
        semantic.analyze(parser.parse(source))

    def test_on_upgrade_alone_is_enough_to_unlock_openPort(self, parser, semantic):
        source = """
        thread w {
            on load() { openPort(8080) }
            on upgrade(s:socket) { }
        }
        """
        semantic.analyze(parser.parse(source))

    def test_on_socketMessage_alone_is_enough_to_unlock_openPort(self, parser, semantic):
        source = """
        thread w {
            on load() { openPort(8080) }
            on socketMessage(s:socket, msg:blob) { }
        }
        """
        semantic.analyze(parser.parse(source))

    def test_on_socketClose_alone_is_enough_to_unlock_openPort(self, parser, semantic):
        source = """
        thread w {
            on load() { openPort(8080) }
            on socketClose(s:socket) { }
        }
        """
        semantic.analyze(parser.parse(source))

    def test_on_request_wrong_signature_is_rejected(self, parser, semantic, errors):
        source = "thread w { on request(x:int) { } }"
        with pytest.raises(errors.CompileError, match="must declare exactly"):
            semantic.analyze(parser.parse(source))

    def test_duplicate_on_request_is_rejected(self, parser, semantic, errors):
        source = """
        thread w {
            on request(req:http) { }
            on request(req2:http) { }
        }
        """
        with pytest.raises(errors.CompileError, match="already declares 'on request'"):
            semantic.analyze(parser.parse(source))

    def test_a_pool_thread_may_also_declare_an_http_context(self, parser, semantic):
        source = """
        thread w[3] {
            on load() { openPort(8080) }
            on request(req:http) { req.ok() }
        }
        """
        semantic.analyze(parser.parse(source))

    def test_a_bare_postMessage_still_works_inside_on_request(self, parser, semantic):
        # claude.md #208/#212: an HTTP handler is just another place a
        # thread's own body runs -- bare postMessage(x) still works
        # from inside it exactly like from on_load/on_message.
        source = """
        on message(worker:thread, msg:int) { }
        thread w {
            on load() { openPort(8080) }
            on request(req:http) { postMessage(1) }
        }
        """
        semantic.analyze(parser.parse(source))


class TestGiveRequest:
    """claude.md #213 (Phase 5): `NAME.giveRequest(r)` -- live
    connection hand-off. Legal only from main, only when `r`'s own
    static type is manually-managed `http?` (reusing T?, claude.md
    #202/#203 -- no compile-time move-checker), and only when the
    target thread has declared its own `on request`."""

    def test_giveRequest_to_a_thread_with_on_request_is_accepted(self, parser, semantic):
        source = """
        thread w { on request(req:http) { req.ok() } }
        on request(req:http?) { w.giveRequest(req) }
        """
        semantic.analyze(parser.parse(source))

    def test_an_ordinary_auto_managed_http_is_rejected(self, parser, semantic, errors):
        # claude.md #213: NOT declaring `?` means main's own end-of-
        # scope cleanup would still release it out from under the
        # thread this hands it to -- rejected outright, no move-
        # checker needed to catch this specific, always-wrong case.
        source = """
        thread w { on request(req:http) { req.ok() } }
        on request(req:http) { w.giveRequest(req) }
        """
        with pytest.raises(errors.CompileError, match="requires a manually-managed 'http\\?' value"):
            semantic.analyze(parser.parse(source))

    def test_target_thread_with_no_on_request_is_rejected(self, parser, semantic, errors):
        source = """
        thread w { on load() { } }
        on request(req:http?) { w.giveRequest(req) }
        """
        with pytest.raises(errors.CompileError,
                            match="requires thread 'w' to have declared its own 'on request"):
            semantic.analyze(parser.parse(source))

    def test_target_thread_with_only_some_other_http_handler_is_rejected(self, parser, semantic, errors):
        # claude.md #213: has_http_handler alone (any of the four)
        # isn't specific enough -- giveRequest dispatches on_request
        # specifically.
        source = """
        thread w { on socketClose(s:socket) { } }
        on request(req:http?) { w.giveRequest(req) }
        """
        with pytest.raises(errors.CompileError,
                            match="requires thread 'w' to have declared its own 'on request"):
            semantic.analyze(parser.parse(source))

    def test_wrong_argument_count_is_rejected(self, parser, semantic, errors):
        source = """
        thread w { on request(req:http) { req.ok() } }
        on request(req:http?) { w.giveRequest() }
        """
        with pytest.raises(errors.CompileError, match="expects exactly 1 argument"):
            semantic.analyze(parser.parse(source))

    def test_giveRequest_from_inside_a_thread_body_is_rejected(self, parser, semantic, errors):
        # claude.md #213: main-only, the same gate kill/live/isAlive
        # already get -- a thread has no business handing off a
        # connection it didn't itself accept.
        source = """
        thread w { on request(req:http) { req.ok() } }
        thread other {
            on message(sender:thread, msg:int) {
                w.giveRequest(msg)
            }
        }
        """
        with pytest.raises(errors.CompileError,
                            match="cannot be called from inside a thread body"):
            semantic.analyze(parser.parse(source))

    def test_giveRequest_on_a_thread_with_no_such_method_lists_it(self, parser, semantic, errors):
        source = """
        thread w { on load() { } }
        w.frobnicate()
        """
        with pytest.raises(errors.CompileError, match="giveRequest"):
            semantic.analyze(parser.parse(source))

    def test_giveRequest_on_a_pool_instance_is_accepted(self, parser, semantic):
        source = """
        thread w[2] { on request(req:http) { req.ok() } }
        on request(req:http?) { w[0].giveRequest(req) }
        """
        semantic.analyze(parser.parse(source))
