// claude.md #176: enum -- exercises both runtime representations at
// real iteration counts, not just the few-element cases
// tests/test_codegen.py::TestEnums already covers.
//
// Pure-struct representation (Shape): a widened, self-tagged struct
// header -- repeated reassignment through the SAME enum-typed local
// (starting from its own null zero-value, the exact case that used to
// segfault before this feature's own release wrapper learned to
// null-check before ever reading the tag) exercises both the struct's
// own release-check/free path AND the enum wrapper's tag-dispatch
// path, on every iteration.
//
// Mixed representation (Choice): a real, independently heap-allocated
// {tag, value} box -- alternating which member type is currently
// boxed forces genuine allocate/release churn on the box itself, and
// (when the currently-boxed member is `text`) on the boxed text
// buffer too, exercising the mixed release wrapper's own
// dispatch-by-tag-then-release-the-inner-value path.

struct Circle { radius:int }
struct Square { area:int }
enum Shape = Circle, Square

struct Box { n:int }
enum Choice = int, text, Box

int shapeTotal = 0
Shape shape
int i = 0
while i < 3000 {
    if i % 2 == 0 {
        Circle c
        c.radius = i % 100
        shape = c
    } else {
        Square s
        s.area = i % 100
        shape = s
    }
    if typeof shape == 'Circle' {
        shapeTotal = shapeTotal + shape.radius
    } else {
        shapeTotal = shapeTotal + shape.area
    }
    i = i + 1
}
log(shapeTotal)

int choiceInts = 0
int choiceTexts = 0
int choiceBoxes = 0
Choice choice
i = 0
while i < 3000 {
    int which = i % 3
    if which == 0 {
        int n = i
        choice = n
        choiceInts = choiceInts + 1
    } else {
        if which == 1 {
            text t = `item${i}`
            choice = t
            choiceTexts = choiceTexts + 1
        } else {
            Box b
            b.n = i
            choice = b
            choiceBoxes = choiceBoxes + 1
        }
    }
    i = i + 1
}
log(choiceInts)
log(choiceTexts)
log(choiceBoxes)
log(typeof choice)

// Aliasing churn: two locals sharing the same pure-struct enum value,
// reassigned every iteration -- proves retain-before-release-on-
// self-assignment-through-an-alias never frees something still about
// to be read.
Shape a
Shape b
i = 0
while i < 2000 {
    Circle c
    c.radius = i
    a = c
    b = a
    a = b
    i = i + 1
}
log(a.radius)
log(b.radius)

// claude.md #197: a fresh, WITH-INITIALIZER enum-typed local declared
// INSIDE the loop (not an already-declared variable being reassigned,
// which the churn above already exercises) -- found leaking here, not
// in a thread-related program at all, since _emit_block's own
// scope-exit tracking never scheduled EnumType for release despite
// claude.md #176's own comment on _is_refcounted saying no such
// special-casing was needed. Exercises the mixed representation (a
// fresh box, its own boxed text buffer) at real volume.
int freshTotal = 0
i = 0
while i < 3000 {
    Choice fresh = `fresh${i}`
    if typeof fresh == 'text' {
        freshTotal = freshTotal + 1
    }
    i = i + 1
}
log(freshTotal)
