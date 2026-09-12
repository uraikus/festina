// Struct globals, their fields, and the lazily-created storage a
// struct-typed FIELD gets on first use (claude.md #97).
//
// Every other construct here is deliberately one the codegen port
// already reproduces, so a difference in this file's IR is a difference
// in field handling and nothing else.
//
// Two things in particular are only measurable with a file like this:
//
//   1. **Nested access auto-vivifies.** `b.origin.x` reaches through a
//      field whose storage does not exist yet, so the read emits a null
//      check, a calloc with a refcount of 1, a store back through the
//      same slot, and a phi over the two paths. `p.x` emits none of
//      that. Both shapes have to be right, and only the nested one
//      exercises the harder path.
//
//   2. **The phi's predecessor is not the block it branched to.** The
//      SECOND reach through `b.origin` starts in `field.done` from the
//      first one, not in `entry`. A generator that names the branch
//      label in its phi instead of the block the value was really
//      computed in agrees with festina/codegen.py on the first access
//      and disagrees on every one after it -- which is why the reaches
//      below are repeated rather than done once.
//
// Containers appear as globals only. An arr[T]/map[T] LOCAL needs the
// escape analysis that picks stack storage over heap, which is a
// separate module (festina/escape_analysis.py) and not ported; a global
// is pure storage and needs none of it.

struct Point { x:int  y:int }
struct Box { origin:Point  w:int  scale:float  flag:bool }

Point p
Box b

arr[int] nums
map[int] counts

// Plain field writes and reads: no auto-vivify anywhere, because `p`
// itself is a global whose storage exists before main runs.
p.x = 3
p.y = 4
log(p.x)
log(p.y)
log(p.x + p.y)

// Fields of every scalar field type the layout distinguishes -- int,
// float and bool are three different LLVM types in the same struct, and
// `color` is the one type outside them that is not a pointer.
b.w = 9
b.scale = 2.5
b.flag = true
log(b.w)
log(b.scale)
log(b.flag)

// First reach through a struct-typed field: this is the write side of
// the auto-vivify path.
b.origin.x = 7
log(b.origin.x)

// Second and third reaches, which is the point of the file: by now
// `b.origin` is non-null at runtime, but the generator cannot know
// that, so each one emits its own null check and phi -- and each phi's
// incoming edge comes from wherever the previous one left off.
b.origin.y = 11
log(b.origin.y)
log(b.origin.x + b.origin.y)

// A field read inside a function, where the temp and label counters are
// at different values and the enclosing block is a function entry
// rather than __festina_main's. The struct comes in as a global rather
// than a parameter: a struct-typed parameter is its own unported
// construct and would mask everything else this file measures.
int func boxArea() {
    return b.origin.x * b.origin.y
}

log(boxArea())

// Control flow around a field access, so the field blocks interleave
// with if/while blocks rather than sitting in a straight line.
if b.origin.x > 5 {
    log(b.origin.x)
} else {
    log(0)
}

int i = 0
while i < 3 {
    b.origin.y = b.origin.y + 1
    i++
}
log(b.origin.y)
