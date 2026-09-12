"""Struct literals -- specification.md §8.9.4.

Written against the clause before the implementation existed, per
claude.md §2's order: the clause, then these, watched failing for the
right reason, then the code.

The whole feature rests on one rule -- **the expected type at the
position decides whether a `{...}` is a map literal or a struct
literal** -- so the tests that matter most are not the ones showing a
struct literal working. They are the ones showing that map literals did
not change (`TestMapLiteralsAreUnchanged`), and that the two readings
stay distinguishable in the one place both are reachable at once: a
struct with a `map[T]` field, where the outer braces are a struct and
the inner braces are a map.

Keys are string literals by deliberate design (§8.9.4). An unquoted
identifier is a variable reference in a struct literal exactly as it is
in a map literal, so `{name: 'Brad'}` does NOT mean field `name` -- and
since that is the habit every JavaScript programmer arrives with, the
error it produces is itself specified and tested here.
"""
import pytest


def _analyze(parser, semantic, codegen, src):
    """Compiles all the way to IR, deliberately.

    Stopping at semantic.analyze() would be the weaker check: `Person p
    = {}` already passes analysis today and fails in codegen with
    "cannot infer the value type of an empty map literal". An acceptance
    test that stopped short would be satisfied by an implementation that
    type-checks a struct literal and then cannot emit one.
    """
    program = parser.parse(src, filename="main.f")
    analyzed = semantic.analyze(program, filename="main.f")
    return codegen.generate_ir(program, analyzed, filename="main.f")


def _err(parser, semantic, codegen, errors, src):
    with pytest.raises(errors.CompileError) as excinfo:
        _analyze(parser, semantic, codegen, src)
    return str(excinfo.value)


PERSON = "struct Person {\n    name:text\n    age:int\n    active:bool\n}\n"


class TestAccepted:

    def test_a_full_literal(self, parser, semantic, codegen):
        _analyze(parser, semantic, codegen,
                 PERSON + "Person p = {'name': 'Brad', 'age': 30, 'active': true}\n")

    def test_a_partial_literal(self, parser, semantic, codegen):
        """"Omitted fields keep their zero values" -- §8.9.4."""
        _analyze(parser, semantic, codegen, PERSON + "Person p = {'name': 'Brad'}\n")

    def test_an_empty_literal(self, parser, semantic, codegen):
        """"`User c = {}` -- same as `User c`"."""
        _analyze(parser, semantic, codegen, PERSON + "Person p = {}\n")

    def test_mixed_field_types_are_fine(self, parser, semantic, codegen):
        """The map-literal rule that every value shares one type does
        NOT apply to a struct literal -- each value is checked against
        its own field's declared type instead. This is the single most
        common real literal and the one that fails loudest without the
        feature."""
        _analyze(parser, semantic, codegen, PERSON + "Person p = {'name': 'Brad', 'age': 30}\n")

    def test_shorthand_fills_a_field_from_a_same_named_variable(self, parser, semantic, codegen):
        """`{ name }` already desugars to `{'name': name}` (§8.8), so a
        struct literal gets the shorthand for free."""
        _analyze(parser, semantic, codegen,
                 PERSON + "text name = 'Brad'\nPerson p = { name }\n")

    def test_assignment_to_an_existing_binding(self, parser, semantic, codegen):
        _analyze(parser, semantic, codegen,
                 PERSON + "Person p\np = {'name': 'Brad'}\n")

    def test_a_nested_struct_field(self, parser, semantic, codegen):
        _analyze(parser, semantic, codegen,
                 "struct Point { x:int  y:int }\n"
                 "struct Shape { origin:Point }\n"
                 "Shape s = {'origin': {'x': 1, 'y': 2}}\n")

    def test_elements_of_an_array_literal(self, parser, semantic, codegen):
        _analyze(parser, semantic, codegen,
                 "struct Point { x:int  y:int }\n"
                 "arr[Point] ps = [{'x': 1, 'y': 2}, {'x': 3, 'y': 4}]\n")

    def test_values_of_a_map_literal(self, parser, semantic, codegen):
        _analyze(parser, semantic, codegen,
                 "struct Point { x:int  y:int }\n"
                 "map[Point] m = {'a': {'x': 1, 'y': 2}}\n")

    def test_assignment_to_an_array_element(self, parser, semantic, codegen):
        _analyze(parser, semantic, codegen,
                 "struct Point { x:int  y:int }\n"
                 "arr[Point] ps = [{'x': 1, 'y': 2}]\n"
                 "ps[0] = {'x': 9, 'y': 9}\n")

    def test_assignment_to_a_struct_field(self, parser, semantic, codegen):
        _analyze(parser, semantic, codegen,
                 "struct Point { x:int  y:int }\n"
                 "struct Shape { origin:Point }\n"
                 "Shape s\ns.origin = {'x': 1, 'y': 2}\n")

    def test_a_manually_managed_binding(self, parser, semantic, codegen):
        """§8.18 rule 6: a literal is a fresh construction, so `T?`
        accepts one. This is the spelling that started the feature --
        `Person? brad = {'name': 'Brad'}` followed by `clear brad`."""
        _analyze(parser, semantic, codegen,
                 PERSON + "Person? p = {'name': 'Brad'}\nclear p\n")

    def test_a_self_referencing_field_may_be_null(self, parser, semantic, codegen):
        _analyze(parser, semantic, codegen,
                 "struct Node { n:int  next:Node }\n"
                 "Node head = {'n': 1, 'next': null}\n")

    def test_a_map_typed_field_takes_a_map_literal(self, parser, semantic, codegen):
        """Both readings of `{...}` in one declaration, which is the
        only place they are reachable at once: the outer braces are a
        struct because `Bag` is, the inner are a map because `items`
        is. The expected type decides, one level down."""
        _analyze(parser, semantic, codegen,
                 "struct Bag { items:map[text] }\n"
                 "Bag b = {'items': {'a': 'x'}}\n")

    def test_a_text_value_coerces_to_a_color_field(self, parser, semantic, codegen):
        """§8.9.4: "each value must be assignable to that field's
        declared type under §8.20, including its coercions" -- rule 5,
        the text-to-color compile-time resolution."""
        _analyze(parser, semantic, codegen,
                 "struct Style { ink:color }\nStyle s = {'ink': '#ff0000'}\n")


class TestRejected:

    def test_an_unknown_field(self, parser, semantic, codegen, errors):
        msg = _err(parser, semantic, codegen, errors,
                   PERSON + "Person p = {'nope': 1}\n")
        assert "nope" in msg and "Person" in msg

    def test_the_same_field_twice(self, parser, semantic, codegen, errors):
        msg = _err(parser, semantic, codegen, errors,
                   PERSON + "Person p = {'age': 1, 'age': 2}\n")
        assert "age" in msg

    def test_a_wrong_value_type(self, parser, semantic, codegen, errors):
        msg = _err(parser, semantic, codegen, errors,
                   PERSON + "Person p = {'age': 'thirty'}\n")
        assert "age" in msg and "int" in msg

    def test_a_bareword_key_says_to_quote_it(self, parser, semantic, codegen, errors):
        """The JavaScript habit. `{name: 'Brad'}` parses as "key is the
        VALUE of variable name" (§8.8), so without a targeted message
        this reports `unknown variable 'name'` -- true, and useless.
        The message has to name the fix."""
        msg = _err(parser, semantic, codegen, errors,
                   PERSON + "Person p = {name: 'Brad'}\n")
        assert "'name'" in msg
        assert "quote" in msg.lower() or "string literal" in msg.lower()

    def test_a_computed_key(self, parser, semantic, codegen, errors):
        """Fields are resolved at compile time, so even a key that IS a
        text expression cannot name one."""
        msg = _err(parser, semantic, codegen, errors,
                   PERSON + "text k = 'name'\nPerson p = {k: 'Brad'}\n")
        assert "string literal" in msg.lower() or "quote" in msg.lower()

    def test_an_argument_position(self, parser, semantic, codegen, errors):
        """Annex D: arguments are deliberately not struct-literal
        positions. It must stay an error rather than quietly building a
        map and failing somewhere less obvious."""
        _err(parser, semantic, codegen, errors,
             PERSON + "void func greet(p:Person) {\n    log(p.name)\n}\n"
             "greet({'name': 'Brad'})\n")

    def test_a_return_position(self, parser, semantic, codegen, errors):
        _err(parser, semantic, codegen, errors,
             PERSON + "Person func make() {\n    return {'name': 'Brad'}\n}\n")


class TestMapLiteralsAreUnchanged:
    """The control. Every acceptance above widens what `{...}` can mean,
    so what protects the language is evidence that the OTHER reading did
    not move. If these ever start failing, the feature has eaten the map
    literal rather than sitting beside it."""

    def test_a_map_literal_still_infers_as_a_map(self, parser, semantic, codegen):
        _analyze(parser, semantic, codegen, "map[text] m = {'a': 'x'}\n")

    def test_a_bareword_key_is_still_a_variable_reference(self, parser, semantic, codegen):
        """In a MAP literal `{k: 'x'}` means "the key is the value of
        k". That is the rule the struct literal deliberately does not
        contradict."""
        _analyze(parser, semantic, codegen, "text k = 'a'\nmap[text] m = {k: 'x'}\n")

    def test_mixed_value_types_are_still_rejected_for_a_map(self, parser, semantic, codegen, errors):
        msg = _err(parser, semantic, codegen, errors, "map[text] m = {'a': 'x', 'b': 1}\n")
        assert "same type" in msg

    def test_an_http_literal_still_works(self, parser, semantic, codegen):
        """claude.md #164's `http req = {...}` is its own contextual
        reading of `{...}` and predates this one. HttpType is not a
        struct type, so the two must not collide."""
        _analyze(parser, semantic, codegen,
                 "http req = {'url': 'https://example.com', 'method': 'GET'}\n")


class TestRuntimeBehaviour:

    def test_fields_hold_what_the_literal_said(self, compile_and_run):
        result = compile_and_run(PERSON + """
Person p = {'name': 'Brad', 'age': 30, 'active': true}
log(`${p.name} ${p.age} ${p.active}`)
""")
        assert result.stdout.strip() == "Brad 30 true"

    def test_omitted_fields_are_zero(self, compile_and_run):
        result = compile_and_run(PERSON + """
Person p = {'name': 'Brad'}
log(`${p.name} ${p.age} ${p.active}`)
""")
        assert result.stdout.strip() == "Brad 0 false"

    def test_an_empty_literal_matches_a_bare_declaration(self, compile_and_run):
        result = compile_and_run(PERSON + """
Person a = {}
Person b
log(`${a.age == b.age} ${a.active == b.active}`)
""")
        assert result.stdout.strip() == "true true"

    def test_assignment_replaces_rather_than_updates(self, compile_and_run):
        """§8.9.4: "a literal always produces a complete, fresh
        instance, so assigning one to an existing binding replaces the
        whole value rather than updating the named fields". The
        surprising half of the rule, so it gets its own test."""
        result = compile_and_run(PERSON + """
Person p = {'name': 'Brad', 'age': 30}
p = {'age': 31}
log(`[${p.name}] ${p.age}`)
""")
        assert result.stdout.strip() == "[] 31"

    def test_the_literal_is_a_fresh_instance_not_an_alias(self, compile_and_run):
        """Structs are references (§8.9.1). Two literals must be two
        instances -- if the literal were somehow shared, mutating one
        would show in the other."""
        result = compile_and_run(PERSON + """
Person a = {'age': 1}
Person b = {'age': 2}
a.age = 99
log(`${a.age} ${b.age}`)
""")
        assert result.stdout.strip() == "99 2"

    def test_a_nested_literal(self, compile_and_run):
        result = compile_and_run("""
struct Point { x:int  y:int }
struct Shape { origin:Point  label:text }
Shape s = {'origin': {'x': 3, 'y': 4}, 'label': 'box'}
log(`${s.label} ${s.origin.x} ${s.origin.y}`)
""")
        assert result.stdout.strip() == "box 3 4"

    def test_a_struct_with_a_map_field(self, compile_and_run):
        """Both readings of `{...}` in one declaration: the outer braces
        are a struct, the inner are a map."""
        result = compile_and_run("""
struct Bag { name:text  items:map[text] }
Bag b = {'name': 'toolbox', 'items': {'a': 'hammer'}}
log(`${b.name} ${b.items['a']}`)
""")
        assert result.stdout.strip() == "toolbox hammer"

    def test_an_array_of_struct_literals(self, compile_and_run):
        result = compile_and_run("""
struct Point { x:int  y:int }
arr[Point] ps = [{'x': 1, 'y': 2}, {'x': 3, 'y': 4}]
log(`${ps.length} ${ps[0].x} ${ps[1].y}`)
""")
        assert result.stdout.strip() == "2 1 4"

    def test_shorthand_reads_the_variable(self, compile_and_run):
        result = compile_and_run(PERSON + """
text name = 'Brad'
int age = 30
Person p = { name, age }
log(`${p.name} ${p.age}`)
""")
        assert result.stdout.strip() == "Brad 30"

    def test_a_manually_managed_literal_can_be_cleared(self, compile_and_run):
        """The spelling the feature came from."""
        result = compile_and_run(PERSON + """
Person? brad = {'name': 'Brad', 'age': 30}
log(brad.name)
clear brad
log(`${brad == null}`)
""")
        assert result.stdout.strip().splitlines() == ["Brad", "true"]

    def test_a_literal_assigned_to_a_field(self, compile_and_run):
        result = compile_and_run("""
struct Point { x:int  y:int }
struct Shape { origin:Point }
Shape s
s.origin = {'x': 7, 'y': 8}
log(`${s.origin.x} ${s.origin.y}`)
""")
        assert result.stdout.strip() == "7 8"

    def test_text_fields_survive_scope_exit(self, compile_and_run):
        """A struct literal stores refcounted values into a fresh
        instance. Building one inside a loop and reading it after is
        where a missing retain or a double release would show up as a
        crash or garbage rather than as a compile error."""
        result = compile_and_run("""
struct Person { name:text  age:int }
arr[Person] people
for int i = 0, i < 3, i++ {
    Person p = {'name': `person-${i}`, 'age': i}
    people.push(p)
}
log(`${people[0].name} ${people[2].name} ${people.length}`)
""")
        assert result.stdout.strip() == "person-0 person-2 3"
