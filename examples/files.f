// claude.md #36/#109: blob -- a file's bytes, loaded from a path.
// Build and run with:
//
//   ./bin/festina examples/files.f -o files_demo
//   ./files_demo
//
// Writes and deletes files in the current directory, so it leaves
// nothing behind. (claude.md #126 round eight: this used to hardcode
// /tmp -- a POSIX-only absolute path a native Windows binary resolves
// under the current drive's root, not MSYS2's own /tmp mapping, since
// a compiled program is a plain UCRT64 executable, not something
// running inside MSYS2's own POSIX emulation layer. Real Windows CI
// caught every operation below failing as a result, since that
// directory tree simply doesn't exist there. Plain relative filenames
// work identically on every platform.)

// claude.md #36's own example was always `blob data = 'path/to/file'`.
// For a long time that stored the PATH and never read the file -- blob
// was a second name for text. claude.md #109 makes it mean what it
// says: the declaration reads the file, and the value keeps the path so
// everything you can do to a file is a method on it.
blob notes = 'festina_files_demo.txt'

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
text dir = './'
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

// claude.md #110: save() writes the bytes back out. With a path it
// ADOPTS that path first, so everything else follows it; saveCopy()
// writes elsewhere and leaves the path alone. Both answer bool.
blob log1 = 'festina_files_demo_3.txt'
log1.write('entry one')
log(`saveCopy: ${log1.saveCopy('festina_files_demo_backup.txt')}`)

// The copy exists, but log1 still points where it did -- so this write
// lands in the original, not the backup.
log1.write('entry two')
log(`original after the copy: ${log1.toText()}`)

blob backup = 'festina_files_demo_backup.txt'
log(`the backup kept the older text: ${backup.toText()}`)

// save(path) is the other half: it moves where this value writes.
log(`save to a new path: ${log1.save('festina_files_demo_4.txt')}`)
log1.delete()          // deletes the NEW path -- save() adopted it

blob three = 'festina_files_demo_3.txt'
log(`the old path survived the move: ${three.exists()}`)

three.delete()
backup.delete()
second.delete()
