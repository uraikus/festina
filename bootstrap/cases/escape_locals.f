// Escape analysis in action: what claude.md #74 actually decides.
//
// Three things here are invisible in a program that does not have both
// shapes side by side, and each is a different allocation strategy for
// the same source construct:
//
//   1. **A struct local that nothing can reach lives in the frame.**
//      `alloca %struct.P` plus an explicit zeroinitializer, no refcount
//      header, nothing released. One that escapes gets the ordinary
//      calloc'd header and a release at scope exit. Both shapes are
//      below, and a program cannot tell them apart -- which is why the
//      stack path has to zero explicitly, since alloca does not and
//      calloc does.
//
//   2. **A `text` parameter the body lets escape owns its own copy.**
//      The caller still owns what it passed and will free it, so a
//      parameter that is reassigned -- or escapes any other way -- takes
//      a festina_text_own on the way in. One that is only read borrows
//      it for the call's duration and copies nothing. Both are here,
//      in the same function, so the difference is per NAME rather than
//      per function.
//
//   3. **Parameters are freed AFTER the body's own locals.** A
//      parameter is bound outside the body's scope, so the body's scope
//      ends first. Easy to get backwards; `takesBoth` below has an
//      escaping parameter and an escaping local precisely so the order
//      is observable.
//
// Every struct here has scalar fields only. A struct with a struct,
// array, map or text field is released through a generated per-type
// cascade rather than the plain festina_release, and a non-escaping one
// with a struct field needs its FIELDS released even though its own
// storage is in the frame -- both are their own mechanisms.

struct P { x:int  y:int }
struct Q { a:int  b:float  c:bool }

P shared
text sink = ''

// Nothing can reach `p`: every use is a field access, which is the one
// position claude.md #74 calls safe. Frame storage, no header.
int func stackOnly() {
    P p
    p.x = 1
    p.y = 2
    return p.x + p.y
}

// Two of them, so the declaration order of the slots and their storage
// is observable rather than trivially right.
int func twoStackLocals() {
    P a
    Q b
    a.x = 1
    a.y = 2
    b.a = 3
    return a.x + a.y + b.a
}

// `q` is stored into a global, so it outlives the frame: heap header,
// refcount 1, released at scope exit -- and the global assignment
// retains BEFORE releasing, because the two sides can be the same
// object.
void func escapesToAGlobal() {
    P q
    q.x = 7
    q.y = 8
    shared = q
}

// A struct local declared inside a loop body: a fresh decision every
// iteration at the source level, one alloca after hoisting.
int func inALoop() {
    int total = 0
    for int i = 0, i < 3, i++ {
        P t
        t.x = i
        total = total + t.x
    }
    return total
}

// `kept` escapes (it is assigned to a global); `readOnly` does not.
// Same function, same type, different answers.
void func takesBoth(kept:text, readOnly:text) {
    sink = kept
    log(readOnly)
}

// An escaping parameter AND an escaping local, so the free order at
// both exits is pinned: the local first, the parameter second.
void func takesAndDeclares(p:text) {
    text local = 'x'
    sink = p
    sink = local
}

// The same, but leaving through a `return` rather than falling off the
// end -- the two paths unwind separately and have to agree.
int func takesAndReturns(p:text) {
    text local = 'y'
    sink = p
    sink = local
    return 1
}

// A container local gets the same decision, with one difference that
// matters: a frame-allocated container still owns a HEAP data buffer,
// because its elements never live in the frame. So the stack answer
// means "no refcount header, but still free the buffer", not "free
// nothing" the way a struct's does.
arr[int] sharedNums
map[float] sharedRates

void func stackContainers() {
    arr[int] xs
    map[float] rs
    log(1)
}

void func heapContainers() {
    arr[int] ys
    map[float] qs
    sharedNums = ys
    sharedRates = qs
}

// A refcounted PARAMETER gets the same per-name decision a text one
// does, with one difference: text is copy-on-alias, so an escaping text
// parameter takes its own BUFFER, while an escaping struct/arr/map
// parameter takes its own REFERENCE. Either way the caller still owns
// what it passed, so the binding must not share the caller's single
// claim on it -- and a parameter the body only reads takes nothing at
// all.
//
// Measured: with the retain deleted, and again with it applied to every
// refcounted parameter rather than only escaping ones, the whole corpus
// saw only the second. Nothing that matches has an escaping non-scalar
// parameter, so these functions are the only evidence for the half that
// retains.
P heldStruct
arr[int] heldNums
map[int] heldCounts

// `kept` escapes into a global; `borrowed` is only ever read through.
// Same function, same type, different answers.
int func takesStructs(kept:P, borrowed:P) {
    heldStruct = kept
    return borrowed.x + borrowed.y
}

// Both escape, and their releases are NOT interchangeable: an array's
// knows to reclaim its data buffer and a map's its entry table, so a
// file with only one of them cannot tell a correct release from the
// generic one.
int func takesContainers(xs:arr[int], m:map[int]) {
    heldNums = xs
    heldCounts = m
    return xs.length
}

// The same, leaving through a `return` rather than falling off the end,
// and with a body local of its own so the free ORDER is pinned at that
// exit too: the local first, the parameter second.
int func takesAndReturnsContainer(xs:arr[int]) {
    arr[int] mine = [1, 2]
    heldNums = xs
    return mine.length
}

// A borrowed container, so the "takes nothing at all" half is not left
// to the struct case alone.
int func onlyReads(xs:arr[int], m:map[int]) {
    return xs.length + m['a']
}

log(stackOnly())
log(twoStackLocals())
escapesToAGlobal()
log(shared.x)
log(inALoop())
takesBoth('a', 'b')
takesAndDeclares('c')
log(takesAndReturns('d'))
stackContainers()
heapContainers()
log(sink)

P argP
argP.x = 5
argP.y = 6
arr[int] argNums = [1, 2, 3]
map[int] argCounts = {'a': 4}
log(takesStructs(argP, argP))
log(takesContainers(argNums, argCounts))
log(takesAndReturnsContainer(argNums))
log(onlyReads(argNums, argCounts))
log(heldStruct.x)
log(heldNums.length)
log(heldCounts['a'])
