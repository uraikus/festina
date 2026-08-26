// claude.md #159/#172: .toStruct(T)/.toArr(T) -- parsing JSON text into
// a fresh struct/arr[T]/map[T] value. #159 gave this scalar-only
// fields/elements; #172 widened it to recurse into a nested struct,
// arr[T] or map[T] field/element -- every _from_json_*_fn_for function
// this exercises is freshly generated PER TARGET TYPE and cached
// (memoized by struct name / element type name), so this loop also
// proves the memoization itself doesn't grow or double-free anything
// across repeated calls to the SAME target type.

struct Point { x:int  y:int }
struct Line { a:Point  b:Point  label:text  tags:arr[text] }
struct Scores { name:text  values:map[int] }
struct Node { n:int  next:Node }

int total = 0
int i = 0
while i < 150 {
    // Flat scalar fields -- claude.md #159's own original v1 shape,
    // still exercised on every pass so a regression there shows up
    // here too, not just in the nested cases below.
    text ptSrc = `{"x":${i},"y":${i * 2}}`
    Point p = ptSrc.toStruct(Point)
    total = total + p.x + p.y

    // A nested struct field (Point inside Line), TWICE in the same
    // struct -- two independent calls into the SAME memoized
    // _from_json_struct_fn_for(Point), each building its own fresh
    // value, neither aliasing the other.
    text lineSrc = `{"a":{"x":${i},"y":1},"b":{"x":2,"y":${i}},` +
                   `"label":"line${i}","tags":["t${i % 3}","u"]}`
    Line ln = lineSrc.toStruct(Line)
    total = total + ln.a.x + ln.b.y + ln.tags.length

    // arr[T] of a struct element -- each element itself built by the
    // same memoized Point parser the flat field above already used.
    text ptsSrc = `[{"x":1,"y":2},{"x":${i},"y":${i}},{"x":3,"y":4}]`
    arr[Point] pts = ptsSrc.toArr(Point)
    total = total + pts.length + pts[1].x

    // Nested arrays -- arr[arr[int]], a different element shape (a
    // container, not a struct) through the identical recursion path.
    text gridSrc = `[[1,2,${i}],[3],[${i},${i},${i},${i}]]`
    arr[arr[int]] grid = gridSrc.toArr(arr[int])
    total = total + grid.length + grid[2].length

    // A struct field of type map[T] -- arbitrary JSON keys, not known
    // field names, the "map[T] target counterpart" claude.md #173
    // closed. A DUPLICATE key within the same object every third pass,
    // so the map builder's own overwrite-releases-the-old-value path
    // (not just the ordinary insert path) gets real exercise too.
    text scoresSrc = i % 3 == 0
        ? `{"name":"p${i}","values":{"a":1,"a":${i},"b":2}}`
        : `{"name":"p${i}","values":{"a":${i},"b":2,"c":3}}`
    Scores sc = scoresSrc.toStruct(Scores)
    total = total + sc.values['a'] + sc.values['b']

    // arr[map[T]] -- a map as an ARRAY ELEMENT rather than a struct
    // field, the other place a map[T] target can appear.
    text mapsSrc = `[{"k":${i}},{"k":${i + 1}}]`
    arr[map[int]] maps = mapsSrc.toArr(map[int])
    total = total + maps[0]['k'] + maps[1]['k']

    // A self-referencing struct (claude.md #17 made the TYPE legal;
    // this is what proves the codegen-level recursion -- the SAME
    // generated function calling itself through its own cached fn_name
    // -- actually terminates and reclaims correctly, not just that it
    // compiles). Three levels deep, varying depth isn't the point here
    // (JSON depth bounds the recursion, not the type's own self-
    // reference), so a fixed depth every pass is enough.
    text nodeSrc = `{"n":${i},"next":{"n":${i + 1},"next":{"n":${i + 2}}}}`
    Node head = nodeSrc.toStruct(Node)
    total = total + head.n + head.next.n + head.next.next.n

    // An alias into a nested field, freed through one binding while
    // the other keeps the whole tree alive -- the same "double
    // ownership of a freshly-built value" shape claude.md #172's own
    // async_io_churn.f exercises for a background-loaded value,
    // exercised here for a JSON-parsed one instead.
    Point aliasA = ln.a
    free aliasA
    total = total + ln.a.x

    i = i + 1
}
log(total)
