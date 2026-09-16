// `name:weak T` -- a struct field that refers to a value without
// keeping it alive, and whose read is CHECKED (decisions.md #332).
//
// Written because nothing else in the corpus declares a weak field, and
// a mechanism no file exercises is unmeasured however carefully both
// sides were written. Every construct the feature adds is below, each
// one placed so that getting it wrong changes the emitted IR rather
// than only the runtime answer:
//
//   1. **The checked read.** A weak field holds a control BLOCK, not
//      the object, so a read is a load plus `festina_weak_get` and not
//      the plain load every other field gets. It is also the one field
//      kind that is never auto-vivified -- creating a value there would
//      create one nothing owns, freed at once and read back as null
//      anyway -- so the `field.make`/`field.done` pair that every other
//      struct-typed field emits must be absent here.
//
//   2. **The store takes no reference.** `c.parent = root` calls
//      `festina_weak_ref` and drops the block that was there, where an
//      ordinary field retains and releases. Both spellings are below,
//      on the same type, so the difference is visible side by side.
//
//   3. **The release cascade drops the block, never the target.**
//      Freeing a struct with a weak field must reach
//      `festina_weak_drop` and must NOT reach the target's own release
//      function.
//
//   4. **A weak-targeted struct needs a wrapper even owning nothing.**
//      `Leaf` below has one int field and would otherwise release
//      through the generic runtime call, which is the one path that
//      could not tell the weak blocks their object was gone.
//
//   5. **Cycle collection skips the edge, in two separate places.**
//      `Node` still reaches itself through `kids`, so it keeps its
//      detector -- but the detector's traversal must not walk `parent`,
//      or a release rooted at a leaf climbs to the root and back down
//      the whole document. `Doc`/`Item` is the other half: there the
//      weak edge is the type graph's ONLY way back, so neither type is
//      cyclic and no detector is generated at all. Those are different
//      code paths and an implementation can get either one alone.

struct Leaf {
    tag:int
}

// Mechanism 4: nothing but a scalar, and the target of a weak field
// below, so its release wrapper exists for exactly one reason.
struct LeafHolder {
    target:weak Leaf
    note:text
}

// Mechanism 5, first half: self-referential through `kids` whatever
// `parent` is, so the detector stays and only the WALK narrows.
struct Node {
    parent:weak Node
    kids:arr[Node]
    tag:int
}

// Mechanism 2's contrast: the identical field shape without the
// modifier, so the retain/release pair and the weak pair sit in one
// file and one struct layout.
struct StrongNode {
    parent:StrongNode
    kids:arr[StrongNode]
    tag:int
}

// Mechanism 5, second half: Doc -> arr[Item] -> Item -> (weak) Doc.
// The only path back is the weak one, so neither type carries a
// detector and the per-release trial is never emitted.
struct Item {
    owner:weak Doc
    label:text
}

struct Doc {
    items:arr[Item]
    title:text
}

// Mechanism 1: an unset weak field, read through `== null`. Every other
// struct-typed field would have vivified here and compared non-null.
int func readsAnUnsetWeakField() {
    Node n
    if n.parent == null { return 1 }
    return 0
}

// Mechanisms 1 and 2 together: stored, then read back while the target
// is still held by something else.
int func readsALiveWeakField() {
    Node root
    root.tag = 7
    Node c
    c.parent = root
    root.kids.push(c)
    Node p = c.parent
    if p == null { return 0 }
    return p.tag
}

// Mechanism 3: the whole tree goes out of scope at the end of this
// body, so the release cascade runs over a struct holding a weak field.
int func releasesAWeakHolder() {
    Leaf l
    l.tag = 3
    LeafHolder h
    h.note = 'held'
    h.target = l
    Leaf back = h.target
    if back == null { return 0 }
    return back.tag + h.note.length
}

// Mechanism 2's contrast, emitted: the same shape with a strong parent,
// which retains on store and releases the old value.
int func strongParentForContrast() {
    StrongNode root
    root.tag = 5
    StrongNode c
    c.parent = root
    root.kids.push(c)
    return root.kids.length + c.parent.tag
}

// Mechanism 5, second half, exercised so the types are actually
// emitted rather than merely declared.
int func weakOnlyBackEdge() {
    Doc d
    d.title = 'doc'
    Item it
    it.label = 'one'
    it.owner = d
    d.items.push(it)
    Doc owner = it.owner
    if owner == null { return 0 }
    return d.items.length + owner.title.length
}

// The shape the feature exists for: a parent pointer on every node, and
// a loop that binds each child to a local. Each of those bindings is
// released at the end of its iteration, and each release runs a trial;
// with `parent` walked, every one of those trials covers the whole
// tree. Small here on purpose -- this file is measured for its IR, not
// its runtime.
int func walksATreeWithParentPointers() {
    Node root
    root.tag = 0
    int i = 0
    while i < 4 {
        Node c
        c.tag = i
        c.parent = root
        root.kids.push(c)
        i++
    }
    int sum = 0
    int j = 0
    while j < root.kids.length {
        Node c = root.kids[j]
        Node up = c.parent
        if up == null { sum = sum + 1 } else { sum = sum + up.kids.length }
        j++
    }
    return sum
}

// Reassignment, so the store path's "drop the block that was there"
// half is reached with a non-null old value rather than only the null
// one a first assignment sees.
int func reassignsAWeakField() {
    Node a
    a.tag = 1
    Node b
    b.tag = 2
    Node c
    c.parent = a
    c.parent = b
    Node p = c.parent
    if p == null { return 0 }
    return p.tag
}

log(`${readsAnUnsetWeakField()}`)
log(`${readsALiveWeakField()}`)
log(`${releasesAWeakHolder()}`)
log(`${strongParentForContrast()}`)
log(`${weakOnlyBackEdge()}`)
log(`${walksATreeWithParentPointers()}`)
log(`${reassignsAWeakField()}`)
