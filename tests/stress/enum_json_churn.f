// claude.md #267: a struct that is a member of an enum, built by the
// JSON parser.
//
// Every other construction site tags such a struct with its own type
// name in a WIDENED header (claude.md #176: {tag, refcount} instead of
// {refcount}, so the tag sits at payload-16 and the refcount stays at
// payload-8). The from-JSON builder did not, which broke both
// directions at once:
//
//   - a SUCCESSFUL parse produced a struct with no self-tag, which
//     crashed the moment it was used as its enum, and
//   - a FAILING parse released the half-built value through the TAGGED
//     release function, freeing payload-16 of an allocation that only
//     reached payload-8 -- an invalid free.
//
// So this is a memory-corruption test first: it alternates good and bad
// input so both paths run every iteration, and uses the parsed value as
// its enum so a missing tag cannot go unnoticed.

struct Point { x:int  label:text }
struct Tag { name:text }
enum Shape = Point, Tag

int points = 0
int caught = 0
int tags = 0

for int i = 0, i < 500, i++ {
    // Success: parse, read fields, then use it AS the enum.
    Point p = `{"x": ${i}, "label": "p${i}"}`.toStruct(Point)
    if p.x == i { points = points + 1 }
    Shape s
    s = p
    if typeof s == 'Point' { points = points + 1 }

    // The other member, so the tag actually distinguishes something.
    Tag t = `{"name": "t${i}"}`.toStruct(Tag)
    s = t
    if typeof s == 'Tag' { tags = tags + 1 }

    // Failure part-way through an object -- the half-built struct is on
    // the cleanup stack and gets released by the throw.
    try {
        Point bad = `{"x": ${i}, "label": `.toStruct(Point)
        points = points + 1000
    } catch (e:text) {
        caught = caught + 1
    }

    // The array builder shares the same per-struct function.
    arr[Point] many = `[{"x": ${i}}, {"x": ${i}}]`.toArr(Point)
    if many.length == 2 { points = points + 1 }
    Shape fromArr
    fromArr = many[1]
    if typeof fromArr == 'Point' { points = points + 1 }
    try {
        arr[Point] alsoBad = 'not an array'.toArr(Point)
        points = points + 1000
    } catch (e2:text) {
        caught = caught + 1
    }
}

log(points)
log(tags)
log(caught)
