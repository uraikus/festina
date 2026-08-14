// A small library file -- imported by examples/multifile.f to
// demonstrate claude.md #5/#6 (imports, import resolution): everything
// declared here (the struct and the function) is available in whatever
// file imports this one, as if it had all been written in one file.

struct Point {
    x:int
    y:int
}

text func describePoint(p:Point) {
    return `(${p.x}, ${p.y})`
}
