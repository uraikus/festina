// claude.md #236: a throw reached THROUGH intermediate frames -- functions
// that merely call something which eventually throws, with no try of
// their own -- must release every local those frames hold on its way to
// the catching try. Before #236 this was the one documented leak of the
// try/throw mechanism (claude.md #157): the longjmp skipped every
// intermediate frame's scope-exit code. Now every managed local is
// registered on the runtime's per-thread cleanup stack as it is bound
// (codegen.py's _track_local), and festina_throw releases the entries
// above the catching frame, newest first.
//
// Run under AddressSanitizer/LeakSanitizer by scripts/leak_stress.sh --
// possible at all only since claude.md #235 moved try/catch onto libc's
// own setjmp/longjmp, which ASan intercepts (the LLVM SjLj intrinsics
// used to die with SIGILL under it). Every kind of tracked local is
// represented: an owned text, a stack-allocated literal arr and map
// (whose data/entries buffers are heap), a with-init struct (always
// refcounted) holding a text and an arr[text], a refcounted arr of
// structs, a blob handle, a text declared inside a loop body, an
// escaping (retained) parameter, and a catch variable in a frame that
// rethrows. Expected: 0 leaks across every shape, and the program's own
// `caught` count proving each throw really was caught.

struct P { id:int  name:text  tags:arr[text] }
int caught = 0

void func deepest(i:int) {
    text t = `deep ${i}`
    if i % 2 == 0 {
        throw t              // aliases a local the unwind will free -- owned copy first
    }
    throw 'odd'
}

// The intermediate frame: nothing here catches, nothing here throws
// directly, every local must still go.
void func middle(i:int) {
    text s = `mid ${i}`
    arr[int] xs = [i, i + 1, i + 2]
    map[text] m = {'k': s, 'j': 'fixed'}
    P p
    p.id = i
    p.name = s
    p.tags.push('a')
    arr[P] ps = [p]          // p escapes into the array: a refcounted heap struct
    P q                      // never escapes: stack storage, managed fields still freed
    q.name = `q ${i}`
    q.tags.push('b')
    blob b = 'throw_unwind_churn_no_such_file.txt'
    int k = 0
    while k < 3 {
        text inner = `loop ${k}`
        if k == 2 {
            deepest(i)
        }
        k = k + 1
    }
}

// A parameter that escapes (reassigned) is retained on entry and
// tracked exactly like a local.
void func holder(held:arr[int], i:int) {
    held = [i]
    text o = `outer ${i}`
    middle(i)
    log('unreachable')
}

int i = 0
while i < 400 {
    try {
        holder([1, 2, 3], i)
    } catch (e:text) {
        caught = caught + 1
    }
    i = i + 1
}

// A frame with its own try that RETHROWS from the catch: the catch
// variable, the locals declared before the inner try, and the ones in
// the inner try body all belong to different depths of the same stack.
void func rethrower(i:int) {
    text a = `a ${i}`
    arr[text] words = ['x', 'y']
    try {
        text b = `b ${i}`
        deepest(i)
    } catch (e:text) {
        text c = `c ${e}`
        throw c
    }
}

i = 0
while i < 400 {
    try {
        rethrower(i)
    } catch (e:text) {
        caught = caught + 1
    }
    i = i + 1
}

// A JSON parse failure two frames down: the builder's own cleanup
// entries sit above the intermediate frames' locals on the same stack
// and are released first.
struct Person { id:int  name:text }

void func parses(src:text) {
    text label = `parsing ${src}`
    Person who = src.toStruct(Person)
    log(who.name)
}

void func via(i:int) {
    arr[text] scratch = [`${i}`, 'z']
    parses(`{"id": ${i}, "name": ${i}}`)
}

i = 0
while i < 400 {
    try {
        via(i)
    } catch (e:text) {
        caught = caught + 1
    }
    i = i + 1
}

// The ordinary, non-throwing path through the same frames must still be
// balanced (every push popped) -- run it plenty, then a throw after it.
void func quiet(i:int) {
    text s = `quiet ${i}`
    P p
    p.name = s
    p.tags.push('t')
    arr[P] ps = [p]
    if i < 0 { throw s }
}

i = 0
while i < 400 {
    quiet(i)
    i = i + 1
}
try { quiet(-1) } catch (e:text) { caught = caught + 1 }

log(caught)
