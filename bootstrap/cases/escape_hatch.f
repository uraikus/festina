// The three things a program does when it wants to decide something
// for itself: reclaim by hand, pass a function around, and recover
// from an error.
//
// They arrived in one slice because they turned out to share a
// mechanism. `free` marks its target as ESCAPING, which is what forces
// the binding onto the heap -- calling a refcounted release on a frame
// address underflows into the stack frame. `try` makes every tracked
// binding in the whole program register itself for unwinding, because
// a throw crosses frames that know nothing about it. And a function
// value is the one managed-looking thing that is not managed at all.
//
// Measured, not assumed. The breakages live in `bootstrap/canary.py`
// and are re-run by tests/test_bootstrap_canary.py, which FAILS if
// this file stops making them visible.
//
//   1. **`free` nulls the binding, and the null store is half the
//      design.** Every release in this runtime is null-safe, so the
//      automatic scope-exit release that later visits the same binding
//      finds null and does nothing -- manual and automatic reclamation
//      coexist with no bookkeeping between them, `free x` twice is a
//      no-op rather than a double free, and use-after-free THROUGH
//      THIS BINDING is impossible, because reading it afterwards reads
//      the ordinary absent value. A refcounted binding is DECREMENTED
//      rather than forcibly freed, so an alias survives.
//
//   2. **`clear` is `free` with zeroing, and the intent cannot ride on
//      the call site.** A release runs a cascade the statement does
//      not walk, so the flag travels as runtime state set around the
//      whole cascade and every free inside consults it. A value still
//      referenced elsewhere is neither freed nor zeroed, the flag
//      being read only at a free that actually happens.
//
//   3. **A `free` target escapes**, which is the single line that
//      makes the statement safe rather than merely implemented. The
//      cost -- one binding that could have been a frame slot is now on
//      the heap -- is paid only by bindings the program explicitly
//      frees, which are exactly the ones whose lifetime it wanted.
//
//   4. **A first-class function value is a bare pointer.** Never
//      allocated, never freed, no refcount: a declared function is
//      immortal for the process's lifetime, so the value rides every
//      scalar-shaped path unchanged. A bare reference to a function's
//      NAME is its own global symbol -- no address-of step, nothing to
//      load, unlike a variable's storage.
//
//   5. **A comparator and a forEach callback reach Festina code
//      through GENERATED trampolines**, and what each trampoline does
//      is the whole mechanism. The sort one decodes two raw slots as
//      this element type; the forEach one reinterprets a raw i64 as
//      this map's value type. The runtime knows neither.
//
//   6. **`try` changes the whole program, not just its own body.** A
//      throw unwinds frames that never heard of it, so every tracked
//      binding anywhere registers itself on a runtime cleanup stack as
//      it is bound, paired with a function that releases it THROUGH
//      ITS SLOT -- so a binding reassigned before the throw releases
//      what it holds then, and one nulled by `free` releases nothing.
//      A program with no `try` pays nothing at all.
//
//   7. **A thrown text that ALIASES a local is copied first.** The
//      unwinding is about to release that local, and the message has
//      to outlive it.

arr[text] shared = ['kept']
int calls = 0

struct Box {
    label:text
    n:int
}

// Mechanism 4: a function used as a value, and the same function
// called directly, so "the name is the symbol" is visible rather than
// asserted.
int func byAsc(a:int, b:int) { return a - b }
int func byDesc(a:int, b:int) { return b - a }

int func directly() {
    return byAsc(9, 4)
}

// A func-typed binding, reassigned, so the value really is a value
// rather than a spelling of the call.
int func throughAValue() {
    func[int,int]:int cmp = byAsc
    int first = cmp(9, 4)
    cmp = byDesc
    return first + cmp(9, 4)
}

// Mechanism 5, the sort half. The element type is the point: the
// trampoline loads each raw slot AS this element's own LLVM type, so a
// `text` array and an `int` array need different ones and a trampoline
// that assumed i64 would read a pointer as an integer. Both are here
// for exactly that reason, and a second int sort with the other
// comparator shows the trampoline is cached per element type rather
// than per call.
int func byLen(a:text, b:text) { return a.length - b.length }

int func sorts() {
    arr[int] xs = [5, 3, 8, 1]
    xs.sort(byAsc)
    arr[int] ys = [5, 3, 8, 1]
    ys.sort(byDesc)
    arr[text] ws = ['ccc', 'a', 'bb']
    ws.sort(byLen)
    return xs[0] + ys[0] + ws[0].length
}

// Mechanism 5, the forEach half. One callback per value type, because
// the trampoline is generated per call rather than cached.
void func sawInt(v:int, k:text) {
    calls = calls + v
}

void func sawText(v:text, k:text) {
    calls = calls + v.length
}

int func visits() {
    map[int] ns = {'a': 1, 'b': 2}
    map[text] ts = {'a': 'xx', 'b': 'yyy'}
    ns.forEach(sawInt)
    ts.forEach(sawText)
    return calls
}

// Mechanisms 1, 2 and 3. `gone` is freed and then read, which must
// answer the absent value rather than a dangling pointer; `twice` is
// freed twice, which must be a no-op. `alias` shares a reference with
// a global, so the release is a decrement and the global survives.
int func reclaims() {
    text gone = `secret-${calls}`
    free gone
    free gone

    text zeroed = `token-${calls}`
    clear zeroed

    arr[text] owned = ['a', 'b']
    free owned

    arr[text] alias = shared
    free alias

    Box b
    b.label = `box-${calls}`
    b.n = 3
    clear b

    map[text] m = {'k': 'v'}
    clear m

    int scalar = 7
    free scalar
    return shared.length
}

// Mechanism 6 and 7. `label` is declared inside the try and is live
// when the throw happens, so the unwinding has to release it; `before`
// is declared OUTSIDE and must NOT be released twice. The thrown
// message aliases a local, which is mechanism 7.
text func explodes(n:int) {
    text inner = `inner-${n}`
    if n > 0 { throw inner }
    return inner
}

int func recovers() {
    text before = `before-${calls}`
    int caught = 0
    try {
        text label = `label-${calls}`
        arr[text] scratch = [label, 'x']
        text answer = explodes(1)
        calls = calls + answer.length
    } catch (err:text) {
        caught = err.length
    }
    return caught + before.length
}

// A try whose body does NOT throw, so the ordinary exit path -- the
// one that pops the catch frame through the same walk that frees every
// local -- is measured too.
int func quiet() {
    int seen = 0
    try {
        text fine = `fine-${calls}`
        seen = fine.length
    } catch (err:text) {
        seen = 0 - 1
    }
    return seen
}

// Leaving a try body by BREAK rather than by falling off its end: the
// catch frame still has to be popped, and it is the same walk.
int func breaksOut() {
    int n = 0
    int i = 0
    while i < 3 {
        try {
            text t = `t-${i}`
            n = n + t.length
            if i == 1 { break }
        } catch (err:text) {
            n = 0
        }
        i++
    }
    return n
}

log(directly())
log(throughAValue())
log(sorts())
log(visits())
log(reclaims())
log(recovers())
log(quiet())
log(breaksOut())
log(shared.length)
log(calls)
