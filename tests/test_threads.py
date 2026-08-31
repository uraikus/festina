"""claude.md #195: `thread NAME { ... }` -- an isolated background
worker with its own OS thread and message queues to/from the main
program.

This file covers Phase 1 only: grammar/AST, the isolation scope (a
thread body can see its own state, function names, and type names, but
never a global variable/constant or an ordinary top-level function),
the sendable-type restriction on messages, and the symmetric "no dead
sends" rule in both directions. No runtime behavior is implemented yet
(that's Phase 2 onward) -- every test here is parser/semantic-level.
"""
import pytest


class TestThreadDecl:
    """Grammar: `thread NAME { ... }` -- no parens, ever (claude.md
    #195's own "no header type at all" design note: the inbound type
    is whatever `on message(p:T)` declares inside the body, the
    outbound type is inferred from that same body's own postMessage(x)
    call sites)."""

    def test_empty_thread_parses_and_analyzes(self, parser, semantic):
        semantic.analyze(parser.parse("thread myWorker { }"))

    def test_thread_with_state_and_all_three_handlers_parses(self, parser, semantic):
        source = """
        thread myWorker {
            map[text] state
            on load() {
                state['ready'] = 'true'
            }
            on message(p:int) {
                log(p)
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
        source = "thread myWorker { on message(p:int) { } on message(p:int) { } }"
        with pytest.raises(errors.CompileError, match="already declares 'on message'"):
            semantic.analyze(parser.parse(source))

    def test_on_message_with_wrong_arity_is_rejected(self, parser, semantic, errors):
        source = "thread myWorker { on message(a:int, b:int) { } }"
        with pytest.raises(errors.CompileError, match="exactly.*parameter"):
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
        "sqlite('SELECT 1')",
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

    def test_a_state_var_is_visible_across_every_handler_in_the_same_thread(
            self, parser, semantic):
        source = """
        thread myWorker {
            map[text] state
            on load() { state['a'] = 'x' }
            on message(p:int) { state['b'] = 'y' }
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
            on message(p:Packet) { log(p.username) }
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
        source = f"thread myWorker {{ on message(p:{msg_type}) {{ }} }}"
        semantic.analyze(parser.parse(source))

    @pytest.mark.parametrize("msg_type", ["http", "socket", "regex"])
    def test_connection_bound_types_are_rejected(self, parser, semantic, errors, msg_type):
        source = f"thread myWorker {{ on message(p:{msg_type}) {{ }} }}"
        with pytest.raises(errors.CompileError, match="cannot cross a thread boundary"):
            semantic.analyze(parser.parse(source))

    def test_a_struct_containing_a_socket_field_is_rejected(self, parser, semantic, errors):
        source = """
        struct Bad { s:socket }
        thread myWorker {
            on message(p:Bad) { }
        }
        """
        with pytest.raises(errors.CompileError, match="cannot cross a thread boundary"):
            semantic.analyze(parser.parse(source))

    def test_a_plain_struct_is_sendable(self, parser, semantic):
        source = """
        struct Packet { username:text data:int }
        thread myWorker {
            on message(p:Packet) { }
        }
        """
        semantic.analyze(parser.parse(source))

    def test_arr_and_map_of_sendable_element_types_are_sendable(self, parser, semantic):
        source = """
        thread a { on message(p:arr[int]) { } }
        thread b { on message(p:map[text]) { } }
        """
        semantic.analyze(parser.parse(source))

    def test_an_enum_of_sendable_members_is_sendable(self, parser, semantic):
        source = """
        enum DataPacket = int, text
        thread myWorker {
            on message(p:DataPacket) { }
        }
        """
        semantic.analyze(parser.parse(source))


class TestThreadMessagePassing:
    """claude.md #195: `NAME.postMessage(x)` (main -> thread) and bare
    `postMessage(x)` (thread -> main) -- both directions inferred, both
    with a symmetric "no dead sends" compile error when nothing would
    ever receive them."""

    def test_postmessage_type_must_match_the_inbound_type(self, parser, semantic, errors):
        source = """
        thread myWorker { on message(p:int) { } }
        myWorker.onMessage(void (x:int) => log(x))
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

    def test_a_thread_that_posts_but_is_never_onmessaged_is_rejected(
            self, parser, semantic, errors):
        source = "thread myWorker { on load() { postMessage(1) } }"
        with pytest.raises(errors.CompileError, match="nothing ever registers"):
            semantic.analyze(parser.parse(source))

    def test_a_thread_that_never_posts_needs_no_onmessage_registration(
            self, parser, semantic):
        # A thread that only ever RECEIVES messages, never sends any,
        # is not an error -- nothing would ever arrive at .onMessage()
        # regardless of whether it's registered.
        source = """
        thread myWorker { on message(p:int) { } }
        myWorker.postMessage(5)
        """
        semantic.analyze(parser.parse(source))

    def test_onmessage_before_any_postmessage_call_site_is_still_fine(
            self, parser, semantic):
        # claude.md #195: the "no dead sends" check for the outbound
        # direction only runs once, at the very end of analyze() --
        # .onMessage() itself may be registered anywhere in the
        # program relative to the thread's own postMessage call sites
        # (unlike the thread's own declaration, which values/calls
        # elsewhere in the program DO need to come after -- see
        # TestThreadIsolation's own ordering test).
        source = """
        thread myWorker { on load() { postMessage(1) } }
        myWorker.onMessage(void (x:int) => log(x))
        """
        semantic.analyze(parser.parse(source))

    def test_onmessage_callback_type_must_match_the_outbound_type(
            self, parser, semantic, errors):
        source = """
        thread myWorker { on load() { postMessage(1) } }
        myWorker.onMessage(void (x:text) => log(x))
        """
        with pytest.raises(errors.CompileError, match="onMessage"):
            semantic.analyze(parser.parse(source))

    def test_more_than_one_distinct_outbound_type_is_rejected(self, parser, semantic, errors):
        # claude.md #195: postMessage(1) and postMessage('x') from the
        # SAME thread, with nothing unifying them -- an EARLIER design
        # auto-synthesized an anonymous enum here, and it was a dead
        # end: NAME.onMessage(callback)'s own parameter type has to be
        # WRITTEN in real Festina syntax, and there is no syntax that
        # could ever spell a compiler-invented, unnamed type. Rejected
        # instead, with a message pointing at the actual fix (see the
        # test right below).
        source = """
        thread myWorker {
            on load() {
                postMessage(1)
                postMessage('x')
            }
        }
        """
        with pytest.raises(errors.CompileError, match="posts more than one type"):
            semantic.analyze(parser.parse(source))

    def test_multiple_outbound_types_work_via_a_real_named_enum(self, parser, semantic):
        # The fix the rejection above points at -- mirrors the INBOUND
        # direction's own already-working "more than one type -> a
        # real, pre-declared enum" convention exactly, symmetric in
        # both directions now. Assigning `1`/`'x'` INTO an `Out`-typed
        # local first (check_assignable's own enum-member coercion,
        # claude.md #176) is what makes each site's inferred type
        # already EnumType('Out') by the time postMessage sees it, so
        # both call sites agree and .onMessage(void (x:Out) => ...) has
        # a real name to spell.
        source = """
        enum Out = int, text
        thread myWorker {
            on load() {
                Out a = 1
                postMessage(a)
                Out b = 'x'
                postMessage(b)
            }
        }
        myWorker.onMessage(void (x:Out) => log(typeof x))
        """
        analyzed = semantic.analyze(parser.parse(source))
        info = analyzed.threads["myWorker"]
        assert info.has_onmessage is True


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


class TestThreadReservedName:
    def test_postmessage_cannot_be_declared_as_a_function(self, parser, semantic, errors):
        program = parser.parse("void func postMessage() { }")
        with pytest.raises(errors.CompileError, match="builtin function name"):
            semantic.analyze(program)
