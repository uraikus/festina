// claude.md #130: splice(start, count, insertArr) -- the newly-inserted
// range is always retained (struct/arr/map/img/aud/regex/blob) or
// copied (text) into the destination, independent of the source array,
// whether that source is a fresh literal (released here once spliced
// in, claude.md #117's own "owning" rule) or a named binding (left
// alone, still holding its own references). Exercises both shapes,
// thousands of times, for both a text element type and a refcounted
// struct element type.

struct P { v:int tag:text }
P func make(v:int, tag:text) { P p  p.v = v  p.tag = tag  return p }

int total = 0
int i = 0
while i < 1200 {
    // Text: a fresh literal insert source (owning, released once
    // spliced in) alongside a named one (left alive afterward).
    arr[text] words = ['a', 'b', 'c', 'd']
    arr[text] gone = words.splice(1, 2, ['x', 'y', 'z'])
    total = total + gone.length + words.length

    arr[text] extra = [`e${i}`, `f${i}`]
    arr[text] gone2 = words.splice(0, 1, extra)
    total = total + gone2.length + words.length + extra.length
    free extra

    // Pure insertion (count 0) and pure removal (insert empty) both
    // exercise the growing/shrinking split in festina_array_splice_insert.
    arr[text] grown = words.splice(0, 0, ['head'])
    total = total + grown.length + words.length

    // Struct elements: retain (not copy) is the interesting path.
    arr[P] ps = [make(1, 'a'), make(2, 'b'), make(3, 'c')]
    arr[P] src = [make(9, `s${i}`)]
    arr[P] goneP = ps.splice(1, 1, src)
    total = total + goneP[0].v + ps[1].v + src[0].v
    free src

    arr[P] goneP2 = ps.splice(0, 1, [make(20, 'lit')])
    total = total + goneP2[0].v + ps[0].v

    i = i + 1
}
log(total)
