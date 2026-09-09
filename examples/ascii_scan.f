// claude.md #256: `ascii` -- one byte per character, so the character
// count IS the byte count. That lets it live in the value's own header,
// which makes `.length`, `s[i]` and `charCodeAt(i)` O(1) reads instead
// of the UTF-8 walks `text` needs. Scanning character by character is
// therefore linear rather than quadratic -- the difference between a
// tokenizer that works and one that doesn't.
//
// Use `text` for anything a person types or reads; use `ascii` where
// the input really is one byte per character and you index it heavily.
//
//   ./bin/festina compile examples/ascii_scan.f -o ascii_scan
//   ./ascii_scan

ascii line = 'the quick brown fox jumps over the lazy dog'

// One pass: count words, and remember the longest.
int words = 0
int longest = 0
int runStart = 0
int inWord = 0
ascii longestWord = ''

// Runs one past the end so a word touching the last character still
// gets closed out by the same branch as every other word.
for int i = 0, i <= line.length, i++ {
    int isLetter = 0
    if i < line.length {
        int c = line.charCodeAt(i)      // O(1): a byte load, not a walk
        if c >= 97 && c <= 122 { isLetter = 1 }
    }
    if isLetter == 1 {
        if inWord == 0 {
            runStart = i
            words = words + 1
        }
        inWord = 1
    } else {
        if inWord == 1 && i - runStart > longest {
            longest = i - runStart
            longestWord = line.slice(runStart, i)
        }
        inWord = 0
    }
}

log(`${words} words`)
log(`longest: ${longestWord} (${longest} characters)`)

// Indexing hands back a one-character ascii without allocating -- it
// comes from a table of 128 immortal singletons.
log(line[4])

// At the boundary, convert explicitly. `toAscii()` answers null for
// text that isn't representable one byte per character, rather than
// throwing -- the same "test, don't fail" rule `s[i]` follows.
text fromElsewhere = 'parsed at runtime'
ascii scanned = fromElsewhere.toAscii()
log(scanned.length)

text unicode = 'caf'
unicode = unicode + 'é'
log(unicode.toAscii() == null)
