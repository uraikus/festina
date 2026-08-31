// claude.md #190: the JSON-rendering optimization itself --
// festina_sb_append_n (skips the runtime strlen() rescan on every
// codegen-emitted punctuation/field-key append) and
// festina_sb_append_json_text's own run-scanning escape loop (bulk
// memcpy of unescaped runs instead of a byte-at-a-time switch/
// strlen/memcpy sequence). Exercises every shape _json_fn_for
// generates a walker for -- struct (including nested and empty),
// arr[T], map[T], and a table row -- with text fields that actually
// need escaping (quotes, backslashes, newlines/tabs, control
// characters, and multi-byte UTF-8, none of which the "safe run"
// fast path should mishandle) so a bug in either optimization would
// show up as a real ASan error, not just a cosmetically-wrong string
// nothing here would notice.

struct Inner { n:int tag:text }
struct Empty { }
struct Item {
    id:int
    name:text
    note:text
    price:float
    active:bool
    tags:arr[text]
    inner:Inner
}

table Rows { id:int label:text }

int total = 0
int i = 0
while i < 800 {
    Item it
    it.id = i
    it.name = `name ${i} with "quotes" and \\backslash`
    it.note = 'line one\nline two\ttabbed\rcarriage, unicode: café 中文 🎉, control:'
    it.price = 1.5
    it.active = i % 2 == 0
    it.tags = ['a"b', 'c\\d', `tag${i}`]
    it.inner.n = i
    it.inner.tag = 'inner "value"'
    text rendered = it.toText()
    if rendered != '' { total = total + 1 }
    free rendered

    Empty e
    text erendered = e.toText()
    if erendered == '{}' { total = total + 1 }
    free erendered

    arr[text] words = ['plain', 'has "quotes"', 'has\nnewline', 'unicode café']
    text arendered = words.toText()
    if arendered != '' { total = total + 1 }
    free arendered

    map[text] m
    m['a'] = 'value "one"'
    m['b'] = `value ${i}`
    text mrendered = m.toText()
    if mrendered != '' { total = total + 1 }
    free mrendered

    sqlite('INSERT INTO Rows (id, label) VALUES (?, ?)', [i, `row "${i}"`])
    arr[Rows] rows = sqlite('SELECT id, label FROM Rows WHERE id = ?', [i])
    text trendered = rows[0].toText()
    if trendered != '' { total = total + 1 }
    free trendered

    i = i + 1
}
log(total)
