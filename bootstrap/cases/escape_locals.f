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

log(stackOnly())
log(twoStackLocals())
escapesToAGlobal()
log(shared.x)
log(inALoop())
takesBoth('a', 'b')
takesAndDeclares('c')
log(takesAndReturns('d'))
log(sink)
