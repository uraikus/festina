// claude.md #339: specification.md 6.7 -- `Math` is a built-in
// namespace and may not be declared. A rejection case rather than a
// running one, so what the two implementations are compared on is the
// POSITION they reject at, which is the whole point of the fix: the
// error used to arrive at the first `Math.` that could not be resolved,
// somewhere below the declaration that was being ignored.
//
// The declaration is a LOCAL deliberately. A global one collides with
// the reserved-name registration in the global scope and would be
// caught by machinery that predates this rule; a local is one scope
// down, where nothing was checking.

float func twice(x:float) {
    return x * 2.0
}

void func report() {
    // Everything above this line is well-formed, so a port that
    // rejected the file for some unrelated reason would land on a
    // different line and fail the comparison rather than pass it.
    float scaled = twice(1.5)
    text Math = 'hello'
    log(`${scaled} ${Math}`)
}

report()
