// claude.md #36/#109: blob -- a file's bytes, loaded from a path.
// Build and run with:
//
//   ./bin/festina examples/files.f -o files_demo
//   ./files_demo
//
// Writes and deletes files under /tmp, so it leaves nothing behind.

// claude.md #36's own example was always `blob data = 'path/to/file'`.
// For a long time that stored the PATH and never read the file -- blob
// was a second name for text. claude.md #109 makes it mean what it
// says: the declaration reads the file, and the value keeps the path so
// everything you can do to a file is a method on it.
blob notes = '/tmp/festina_files_demo.txt'

// A path that isn't there yet is not an error -- it is an empty blob,
// and writing to it is how the file comes into being. Nothing about
// files fails the program (claude.md #93's rule, kept): a missing file
// is something you test for, the same treatment division by zero gets.
log(`exists before writing: ${notes.exists()}`)

log(`write: ${notes.write('hello')}`)
log(`append: ${notes.append(' world')}`)
log(`contents: ${notes.toText()}`)
log(`exists after writing: ${notes.exists()}`)

// write() and append() update the BYTES as well as the file, so
// toText() reports what was just written rather than what the file held
// when the blob was declared.
notes.write('replaced')
log(`after rewriting: ${notes.toText()}`)

// The path may be any text expression, exactly like img and aud.
text dir = '/tmp/'
blob second = dir + 'festina_files_demo_2.txt'
second.write('built from a computed path')
log(second.toText())

// claude.md #109: assigning a blob copies the REFERENCE, not the
// contents -- two names for one file's bytes. Writing through either is
// visible through both, which is what proves they are one handle.
blob alias = notes
notes.write('written through notes')
log(`read through alias: ${alias.toText()}`)

// ...and rebinding one of them releases only ITS reference. `alias`
// still holds the first file's contents, so nothing is freed here; the
// bytes go away when the last reference to them does.
notes = second
log(`notes now: ${notes.toText()}`)
log(`alias still: ${alias.toText()}`)

// delete() removes the FILE. The blob is an ordinary value and is
// unaffected, so "delete it but keep what it said" is expressible.
log(`delete: ${alias.delete()}`)
log(`exists after delete: ${alias.exists()}`)
log(`contents after delete: ${alias.toText()}`)

second.delete()
