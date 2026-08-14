// claude.md #72: map[T] -- { key: value, ... } literals, indexed
// get/set, .forEach(). Build and run with:
//
//   ./bin/festina examples/maps.f -o maps_demo
//   ./maps_demo

// Keys are always text -- 'npc1' is a string literal key, npc2Id is a
// reference to this already-declared variable's own text value, not
// bareword-as-string-name shorthand the way a plain JS object literal
// has.
text npc2Id = 'npc2'
map[int] npcHealths = {'npc1': 10, npc2Id: 15}
map[text] npcNames = {'npc1': 'jim', npc2Id: 'john'}

log(npcHealths['npc1'])
log(npcHealths[npc2Id])
log(npcNames['npc1'])
log(npcNames[npc2Id])

// A missing key reads back as null, not an error.
log(npcHealths['no-such-npc'])

// Writing works the same way as reading -- ['literal'] or [variable] --
// and adds a new key or replaces an existing one.
npcHealths['npc1'] = 30
npcHealths['npc3'] = 5
log(npcHealths['npc1'])
log(npcHealths['npc3'])

// .forEach(callback) visits every entry -- (value, key), in that order.
// The callback is an already-declared function, the same "bare name,
// not an arbitrary expression" restriction setTimeout()'s callback has
// (Festina has no first-class functions/closures).
void func logHealth(h:int, key:text) {
    log(`${key}: ${h.toText()}`)
}
npcHealths.forEach(logHealth)
