// decisions.md #286: more than one arrow function in a single program.
//
// Every arrow compiles to a synthesized top-level function named
// __festina_arrow_N from a monotonic counter, so the NAMES depend on
// the order analysis reaches them. The repository corpus cannot test
// that at all: no file in it has more than one arrow, so every program
// produces exactly __festina_arrow_0 and a counter that never advanced
// would look correct. Breaking the increment in bootstrap/semantic.f
// changed nothing across all 92 files, which is how this gap was found.
//
// Three arrows, one of them nested inside another's body, because the
// numbering rule is pre-order: a name is taken before the body is
// analysed, so an outer arrow gets the lower number.

struct Holder {
    first:func[int]:int
    second:func[int]:int
}

Holder h
h.first = int (x:int) => x + 1
h.second = int (y:int) => y * 2

int func apply(f:func[int]:int, v:int) {
    return f(v)
}

log(apply(h.first, 10).toText())
log(apply(h.second, 10).toText())

// The nested case: the outer arrow's own body contains another arrow,
// so the outer takes its name first. The inner captures nothing --
// there are no closures (specification.md 11.1.4), so it can only
// reference its own parameters and globals.
int func runner(g:func[int]:int) {
    return g(3)
}
int nested = runner(int (z:int) => runner(int (w:int) => w + 100) + z)
log(nested.toText())
