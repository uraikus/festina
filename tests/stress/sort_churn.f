// claude.md #184 (uraikus/festina#76 item 2): .sort() itself never
// retains/copies/releases anything -- a merge sort just repositions
// each element's raw slot within the same buffer, so the interesting
// thing to stress-test isn't ownership transfer (there is none) but
// that nothing about the sort itself (the scratch buffer, the
// comparator trampoline, the indirect calls back into Festina code)
// leaks or corrupts across thousands of calls, for both a text element
// type (a pointer-shaped slot) and a refcounted struct element type
// (also pointer-shaped, but with its own header to double-free if the
// sort ever mishandled a slot).

int func byIntAsc(a:int, b:int) { return a - b }

struct P { v:int tag:text }
P func make(v:int, tag:text) { P p  p.v = v  p.tag = tag  return p }
int func byP(p:P, q:P) { return p.v - q.v }

int total = 0
int i = 0
while i < 1200 {
    arr[int] xs = [5, 3, 8, 1, 9, 2, i % 7]
    xs.sort(byIntAsc)
    total = total + xs[0] + xs[xs.length - 1]

    arr[P] ps = [make(3, 'c'), make(1, `a${i}`), make(2, 'b')]
    ps.sort(byP)
    total = total + ps[0].v + ps[2].v

    // Sorting an already-sorted array (a common early-out check for a
    // real sort implementation) and a single-element array both stress
    // the zero-swap path.
    arr[int] sorted = [1, 2, 3]
    sorted.sort(byIntAsc)
    total = total + sorted[1]

    arr[int] one = [i]
    one.sort(byIntAsc)
    total = total + one[0]

    i = i + 1
}
log(total)
