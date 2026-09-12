// decisions.md #288: struct literals under ASan/LeakSanitizer.
//
// A struct literal is the one construction site that stores into a
// freshly calloc'd header rather than overwriting a slot that may
// already own something, so it deliberately skips the
// load-store-release dance every other field write performs
// (_emit_struct_lit's own comment says why that is safe rather than a
// shortcut). Skipping it is exactly the kind of reasoning a sanitizer
// is for: if the premise is wrong -- a field written twice, a value
// retained that should not have been, a text stored without being
// owned -- it shows up here as a leak per iteration or an invalid free,
// and nowhere in the ordinary suite.
//
// The per-field ownership rules differ by type, so each one is
// exercised: text copies, refcounted values retain, and a value that
// arrives already-fresh must NOT be retained again.
struct Point { x:int  y:int }
struct Person {
    name:text
    age:int
    home:Point
    tags:map[text]
    aliases:arr[text]
}

int i = 0
while i < 4000 {
    // Every field set at once, including two nested literals: the
    // struct-typed field takes a fresh instance nothing else
    // references, which must be stored WITHOUT an extra retain.
    Person full = {
        'name': `person-${i}-0123456789abcdefghijklmnopqrstuvwxyz`,
        'age': i,
        'home': {'x': i, 'y': i},
        'tags': {'k': `v-${i}`},
        'aliases': [`a-${i}`, `b-${i}`]
    }
    log(full.name.length.toText())

    // Partial: the omitted fields are never written at all, so their
    // calloc'd NULLs are what scope exit releases. A release function
    // that mishandled a null field would fail here rather than above.
    Person sparse = {'name': `sparse-${i}`}
    log(sparse.age.toText())

    // Empty: nothing but the header.
    Person bare = {}
    log(bare.age.toText())

    // A text field from an EXISTING binding rather than a literal --
    // the copying path (festina_text_own), where sharing one buffer
    // between the local and the field would be a double free.
    text shared = `shared-${i}-abcdefghijklmnopqrstuvwxyz`
    Person copied = {'name': shared}
    log(copied.name.length.toText())
    log(shared.length.toText())

    // A struct-typed field from an existing binding: the retaining
    // path, where a MISSING retain leaves the field dangling once the
    // local goes out of scope.
    Point origin = {'x': 5, 'y': 6}
    Person located = {'name': `located-${i}`, 'home': origin}
    log(`${located.home.x}${origin.y}`)

    // Reassignment through a literal: the old instance must be
    // released exactly once, the new one owned.
    Person replaced = {'name': `first-${i}`, 'tags': {'a': `x-${i}`}}
    replaced = {'name': `second-${i}`, 'tags': {'b': `y-${i}`}}
    log(replaced.name)

    // A literal into an already-populated field: here there IS an old
    // value, and _emit_assign's own path (not the literal's) owns
    // releasing it.
    Person mutated = {'home': {'x': 1, 'y': 1}}
    mutated.home = {'x': 2, 'y': 2}
    log(mutated.home.x.toText())

    // Array of literals, each element owned by the array.
    arr[Person] crowd = [{'name': `c0-${i}`}, {'name': `c1-${i}`}]
    log(crowd[1].name)

    // Manually managed: nothing automatic runs, and `clear` frees the
    // header and every field it owns.
    Person? owned = {'name': `owned-${i}-abcdefghijklmnopqrstuvwxyz`,
                     'tags': {'k': `v-${i}`}}
    log(owned.name.length.toText())
    clear owned

    i++
}
log('done')
