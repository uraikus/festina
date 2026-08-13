// A tour of what festina/ actually implements today -- see README.md's
// "Implementation Status" section. Build and run with:
//
//   ./bin/festina examples/hello.f -o hello
//   ./hello

struct Point {
    x:int
    y:int
}

int func manhattan(a:Point, b:Point) {
    int dx = a.x - b.x
    int dy = a.y - b.y
    return (dx > 0 ? dx : -dx) + (dy > 0 ? dy : -dy)
}

text func describe(name:text, distance:int) {
    return `${name} is ${distance} steps away`
}

// claude.md #8, #28-31: festina.sqlite is created automatically and this
// table's schema is synchronized before any of the code below runs.
table Visits {
    id:int
    place:text
}

Point origin
origin.x = 0
origin.y = 0

Point destination
destination.x = 3
destination.y = 4

int distance = manhattan(origin, destination)
log(describe('destination', distance))

if distance > 5 {
    log('that is a long walk')
} else {
    log('not too far')
}
