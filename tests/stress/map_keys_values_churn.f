// claude.md #186 (uraikus/festina#76 item 7): map[T].keys()/.values()
// -- the interesting ownership case is .values() on a refcounted (text
// or struct) value type: each collected element is retained/copied
// independently of the map's own live reference, so freeing the SOURCE
// map right after collecting must leave the returned array's elements
// completely unaffected. Exercises a scalar value type (int, no
// ownership work at all), a text value type (copy), and a struct value
// type (retain) -- plus .keys() on an otherwise-empty map, and the
// snapshot staying independent of a later delete on the source map.

struct P { v:int tag:text }
P func make(v:int, tag:text) { P p  p.v = v  p.tag = tag  return p }

int total = 0
int i = 0
while i < 1200 {
    map[int] scores
    scores[`a${i}`] = 1
    scores[`b${i}`] = 2
    scores[`c${i}`] = 3
    arr[text] ks = scores.keys()
    arr[int] vs = scores.values()
    total = total + ks.length + vs.length
    delete scores[`a${i}`]
    total = total + ks.length   // snapshot unaffected by the delete

    map[text] names
    names['x'] = `hello${i}`
    names['y'] = `world${i}`
    arr[text] tvs = names.values()
    free names
    total = total + tvs.length

    map[P] ps
    ps['p1'] = make(i, `tag${i}`)
    ps['p2'] = make(i + 1, `tag${i}b`)
    arr[P] pvs = ps.values()
    free ps
    total = total + pvs[0].v + pvs[1].v

    map[int] empty
    arr[text] ek = empty.keys()
    total = total + ek.length

    i = i + 1
}
log(total)
