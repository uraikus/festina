// claude.md #259: a throw that crosses one of the runtime's OWN C
// frames on its way to the catching try.
//
// claude.md #236's cleanup stack already releases every Festina-side
// local of every intermediate frame. What it did not cover was memory a
// runtime C frame had allocated for ITSELF and was still holding when
// the callback it invoked threw -- the longjmp skips that frame's own
// free() entirely. Only two runtime functions call back into Festina
// code from a frame a Festina try can be sitting below:
// festina_array_sort (which allocates a merge-sort scratch buffer) and
// festina_map_for_each (which allocates nothing). Every other callback
// into Festina -- timers, mouse/key handlers, on request, on message --
// is dispatched from an event-loop frame with no Festina try beneath
// it, so a throw there ends the program rather than unwinding.
//
// This churns both, plus the shapes most likely to unbalance the
// cleanup stack rather than merely leak: a nested sort inside a
// comparator, and a throw the comparator CATCHES ITSELF (where the sort
// must go on to finish normally and free its scratch on the ordinary
// path, not the throw path).

int func cmpThrows(a:int, b:int) {
    if a == 7 { throw 'comparator gave up' }
    return a - b
}

int func cmpPlain(a:int, b:int) {
    return a - b
}

// A comparator that sorts ANOTHER array on every comparison: the inner
// sort's own scratch is pushed and popped inside the outer one's, so an
// unbalanced push would show up here as a wrong answer or a crash, not
// just as bytes.
int func cmpNested(a:int, b:int) {
    arr[int] inner = [3, 1, 2]
    inner.sort(cmpPlain)
    return a - b + inner[0] - 1
}

// Catches its own throw, so the enclosing sort never sees one and must
// complete normally.
int func cmpCatchesItself(a:int, b:int) {
    try {
        if a == 4 { throw 'handled here' }
    } catch (error:text) {
        return 0 - 1
    }
    return a - b
}

int caught = 0
int completed = 0

for int iter = 0, iter < 2000, iter++ {
    arr[int] xs = [9, 3, 7, 1, 5, 8, 2, 6, 4, 0]
    try {
        xs.sort(cmpThrows)
        completed = completed + 1
    } catch (error:text) {
        caught = caught + 1
    }

    // Managed locals live across the throw as well, so the cleanup
    // stack has real work above the scratch entry rather than the
    // scratch entry alone.
    arr[text] words = ['delta', 'alpha', 'charlie']
    try {
        text label = `run ${iter}`
        arr[int] ys = [7, 2, 9]
        ys.sort(cmpThrows)
        completed = completed + 1
    } catch (error:text) {
        caught = caught + 1
    }

    arr[int] ns = [5, 2, 8, 1]
    ns.sort(cmpNested)
    arr[int] ms = [4, 6, 2]
    ms.sort(cmpCatchesItself)

    // festina_map_for_each's own frame: allocates nothing, but a throw
    // out of the callback still has to leave the stack balanced.
    map[int] scores = {'a': 1, 'b': 2, 'c': 3}
    try {
        scores.forEach(visit)
    } catch (error:text) {
        caught = caught + 1
    }
    words.push('echo')
}

log(caught)
log(completed)

void func visit(value:int, key:text) {
    if value == 2 { throw `stopped at ${key}` }
}
