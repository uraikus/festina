// claude.md #223: .toStruct(T)/.toArr(T)/a map[T] field or element --
// the PARTIAL-PARSE failure path, at volume. api.md used to document
// this as "one real, honest limitation": a JSON value that fails to
// parse partway through -- a struct's third field turning out to be
// the wrong type, having already parsed the first two; an array's
// fourth element failing, having already collected three -- leaked
// whatever was already built for that one call, the same structural
// class throw's own "any intermediate frame between the try and the
// throw leaks" limitation already documented. Every generated
// _from_json_*_fn_for function now installs its OWN local catch frame
// around its own build loop specifically to close this gap (see
// codegen.py's _emit_json_catch_prologue/_epilogue) -- this file
// deliberately fails PARTWAY THROUGH at every shape that gap could
// apply to (a struct, an array, a map, a nested struct field, a
// self-referencing struct one level deep), every single pass, so a
// regression shows up as a real, reported leak, not a maybe.

struct Person { id:int  name:text  active:bool  score:float }
struct Line { a:Person  label:text }
struct Node { n:int  next:Node }
struct Scores { name:text  values:map[int] }

int caught = 0
int i = 0
while i < 400 {
    // A struct whose THIRD field is malformed -- id and name already
    // parsed (name is a text field, so this also exercises the
    // extra_free_slots key_reg cleanup: the key for "active" was
    // already read before its own value throws).
    try {
        Person p = `{"id":${i},"name":"n${i}","active":123,"score":1.5}`.toStruct(Person)
        log('unreachable')
    } catch (e:text) {
        caught = caught + 1
    }

    // A nested struct field failing -- Person (itself multi-field)
    // nested inside Line, Line's own "a" field partway built (id
    // parsed, name parsed) before "active" throws, one level of
    // recursion up from the flat case above.
    try {
        Line ln = `{"a":{"id":${i},"name":"x","active":"nope","score":1},"label":"L"}`.toStruct(Line)
        log('unreachable')
    } catch (e:text) {
        caught = caught + 1
    }

    // An array whose 4th of 5 elements is malformed -- 3 int elements
    // already pushed into the array header before the throw.
    try {
        arr[int] xs = `[1,2,3,"four",5]`.toArr(int)
        log('unreachable')
    } catch (e:text) {
        caught = caught + 1
    }

    // A map[T] field whose 3rd entry is malformed -- 2 entries already
    // set (key_reg cleanup exercised here too, on the map builder's
    // own copy of this pattern).
    try {
        Scores sc = `{"name":"s${i}","values":{"a":1,"b":2,"c":"bad"}}`.toStruct(Scores)
        log('unreachable')
    } catch (e:text) {
        caught = caught + 1
    }

    // A self-referencing struct failing one level deep -- the OUTER
    // call's own recursive call into the SAME cached from-json
    // function throws, so both the outer and inner frame's own
    // partially-built value must be released (n parsed at both
    // levels; the inner level's own "next" field never even starts).
    try {
        Node head = `{"n":${i},"next":{"n":${i + 1},"next":"not an object"}}`.toStruct(Node)
        log('unreachable')
    } catch (e:text) {
        caught = caught + 1
    }

    // Malformed JSON syntax itself (not a type mismatch) -- proves the
    // fix covers a syntax-level throw the same way it covers a
    // type-mismatch one; both reach festina_throw through the same
    // low-level primitives.
    try {
        arr[int] xs = `[1,2,`.toArr(int)
        log('unreachable')
    } catch (e:text) {
        caught = caught + 1
    }

    i = i + 1
}
log(caught)
