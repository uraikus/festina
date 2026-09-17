// claude.md #340: the deferred-root buffer, under ASan/LeakSanitizer.
//
// A still-referenced release of a cycle-capable value no longer runs
// its own trial on the spot -- it records the value as a possible root
// and one collection answers a whole batch. That moves three things
// into code every struct/arr/map-using program runs through, and each
// of them is a way to leak or to free twice rather than a way to get a
// wrong answer, so each gets its own section below. The ordinary test
// suite proves the ANSWERS; this proves nothing accumulates and nothing
// is freed out from under the buffer.
//
// Every section prints a checksum, so a section that silently stopped
// doing any work would change the output rather than pass quietly.

struct Node {
    n:int
    next:Node
}

struct Holder {
    label:text
    head:Node
}

// A ring of `size` nodes, closed back on itself, returning the head.
// This is the shape every section below builds on: a genuine reference
// cycle that plain counting can never free.
Node func ring(size:int, base:int) {
    Node head
    head.n = base
    Node cur = head
    for int i = 1, i < size, i++ {
        Node nxt
        nxt.n = base + i
        cur.next = nxt
        cur = nxt
    }
    cur.next = head
    return head
}

int total = 0

// ---- 1. release-while-live churn against ONE shared ring -----------
//
// decisions.md #254's "shared" shape, and the whole reason the buffer
// exists: every drop of a scratch reference into the ring is a release
// that finds the node still referenced, so the old code walked the
// entire ring once per iteration. Here it fills batches instead. The
// ring stays alive throughout and is collected at the end of the scope,
// so a batch that freed a still-referenced node would be caught by the
// reads below it rather than by a leak report.
void func sharedChurn() {
    Node anchor = ring(24, 100)
    for int i = 0, i < 400, i++ {
        Node scratch = anchor
        total = total + scratch.n
        Node other = anchor.next
        total = total + other.n
    }
    log(`shared ${total}`)
}
sharedChurn()

// ---- 2. a buffered root that then dies (the zombie path) -----------
//
// The case the algorithm has to get right and the one a naive buffer
// gets wrong: a value is recorded as a possible root, and THEN its last
// reference goes. Its pointer is sitting in the buffer, so freeing it on
// the spot would leave that buffer holding a dangling pointer for the
// next collection to dereference. The runtime's answer is that
// `festina_release_check` never reports a buffered header as zero, so
// the release skips its free branch and the collection frees the node
// properly -- as an ordinary root whose count is zero.
//
// `drop` is what makes the death happen while buffered: the local
// reference is released (buffering the node), and then the only other
// reference to that ring goes too.
void func zombieChurn() {
    int seen = 0
    for int i = 0, i < 300, i++ {
        Holder h
        h.label = `h${i}`
        h.head = ring(6, i * 10)
        // Read through it, then let the whole holder go. The ring's
        // head is buffered by the field release, and the ring is
        // garbage from that moment on.
        seen = seen + h.head.next.n
    }
    total = total + seen
    log(`zombie ${seen}`)
}
zombieChurn()

// ---- 3. one root inside another root's garbage ---------------------
//
// Two nodes of the SAME ring both end up in the buffer. When the batch
// is answered, the first one's collectWhite walks the ring and reaches
// the second -- whose pointer the buffer still holds. Freeing it there
// would be a use-after-free the moment the loop reached its own entry,
// which is why a buffered node is never freed by another root's sweep
// and waits for its own turn instead.
void func nestedRootsChurn() {
    int seen = 0
    for int i = 0, i < 200, i++ {
        Node head = ring(8, i)
        Node mid = head.next.next
        Node far = head.next.next.next.next
        seen = seen + mid.n + far.n
        // Three references into one ring, all going out of scope
        // together: head, mid and far are each released while the ring
        // still holds them internally, so all three are buffered, and
        // all three are inside each other's reachable subgraph.
    }
    total = total + seen
    log(`nested ${seen}`)
}
nestedRootsChurn()

// ---- 4. containers on the cycle ------------------------------------
//
// A cycle does not have to run through struct fields. arr[T] and map[T]
// release wrappers grow the same buffered branch, and their traversal
// goes through the runtime's type-blind visit/dispose helpers rather
// than through generated field offsets -- a different path to the same
// buffer, and one that frees its own storage separately from its
// elements.
struct Bag {
    items:arr[Bag]
    tag:int
}

void func containerChurn() {
    int seen = 0
    for int i = 0, i < 200, i++ {
        Bag a
        a.tag = i
        Bag b
        b.tag = i + 1
        a.items = [b]
        b.items = [a]
        seen = seen + a.items[0].tag + b.items[0].tag
    }
    total = total + seen
    log(`containers ${seen}`)
}
containerChurn()

// ---- 5. a partial batch left at exit -------------------------------
//
// The buffer collects when it fills. Whatever is still in it when the
// program ends is collected by the flush main() emits, and this section
// exists to leave something there: a handful of rings, far fewer than a
// batch, built and dropped with nothing after them to fill the buffer
// the rest of the way. Without the flush these are exactly the leak
// LeakSanitizer would report.
void func tailChurn() {
    int seen = 0
    for int i = 0, i < 5, i++ {
        Node head = ring(4, i * 1000)
        seen = seen + head.next.n
    }
    total = total + seen
    log(`tail ${seen}`)
}
tailChurn()

log(`total ${total}`)
