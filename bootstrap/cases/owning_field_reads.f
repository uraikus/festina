// claude.md #117/#262: a field read through a base this expression
// OWNS.
//
// `mkOuter().inner` hands back a pointer INTO a value nothing else
// references, and that value is about to be released. So the field's
// own ownership is minted first -- a refcounted field retains, a text
// one copies -- and only then is the base released, whose cascade
// decrements the just-retained value back to exactly the one reference
// this expression holds. A scalar needs no minting: its loaded value
// survives the base by copy.
//
// Written because the corpus could not see the mint. Every file that
// reads a field off an owning base does it through `.length`, which is
// a different path: the length branch drains the chain's parked bases
// and mints nothing, because an i64 owes the base nothing. Breaking
// the mint itself went UNDETECTED, and a mechanism no file exercises is
// unmeasured however carefully both implementations were written.
//
// What this file is shaped to make visible:
//
//   - a REFCOUNTED field (a struct, an array, a map) read off a call
//     result, which must retain before the base goes;
//   - a TEXT field read the same way, which must COPY rather than
//     retain, because text is copy-managed and has no count;
//   - the same reads through a TWO-LINK chain, where the base to
//     release is two frames up and the middle link is an alias that
//     must not be released on its own;
//   - the same reads off a base that is NOT owned, where minting
//     anything would be a reference nobody gives back.

struct Inner {
    label:text
    xs:arr[int]
    counts:map[int]
}

struct Outer {
    inner:Inner
    name:text
}

Inner func mkInner(n:int) {
    Inner x
    x.label = `inner-${n}`
    x.xs = [n, n + 1, n + 2]
    x.counts = {'a': n}
    return x
}

Outer func mkOuter(n:int) {
    Outer o
    o.inner = mkInner(n)
    o.name = `outer-${n}`
    return o
}

int total = 0

// One link, refcounted: the array is retained, the Inner released.
arr[int] borrowedXs = mkInner(1).xs
total = total + borrowedXs.length + borrowedXs[0]

map[int] borrowedCounts = mkInner(2).counts
total = total + borrowedCounts['a']

// One link, a struct field: the same rule, one type up.
Inner fromOuter = mkOuter(3).inner
total = total + fromOuter.xs[1]

// One link, text: COPIED, not retained -- a text has no count, and
// sharing the buffer would free it under this binding when the Inner
// goes.
text borrowedLabel = mkInner(4).label
total = total + borrowedLabel.length

text outerName = mkOuter(5).name
total = total + outerName.length

// Two links: the base to release is two frames up, and `.inner` in
// between is an alias into its graph that must not be released on its
// own.
arr[int] deepXs = mkOuter(6).inner.xs
total = total + deepXs[2]

text deepLabel = mkOuter(7).inner.label
total = total + deepLabel.length

// One link, scalar: nothing is minted at all, because an int survives
// the base by copy.
Inner held = mkInner(8)
int len = mkOuter(9).inner.xs.length
total = total + len + held.xs[0]

// And the same reads off a base this expression does NOT own, where
// minting anything would be a reference nobody ever gives back.
Outer keep = mkOuter(10)
arr[int] aliasXs = keep.inner.xs
text aliasName = keep.name
total = total + aliasXs.length + aliasName.length

log(total)
