// claude.md #67, #68, #107: /pattern/flags literals, .test(), .match(),
// .replace(). Build and run with:
//
//   ./bin/festina examples/regex.f -o regex_demo
//   ./regex_demo

regex digits = /[0-9]+/

log(digits.test('room 42'))
log(digits.test('no numbers here'))

text found = 'room 42, building 7'.match(digits)
log(found)

log('room 42'.replace('room', 'suite'))

// claude.md #107: how many matches a replace touches is a property of
// the PATTERN, spelled with JS's own 'g' flag, rather than a choice
// between two method names. Without 'g', the first match only.
log('a1b2c3'.replace(/[0-9]/, '-'))    // a-b2c3
log('a1b2c3'.replace(/[0-9]/g, '-'))   // a-b-c-

regex greeting = /^hello$/i
log(greeting.test('HELLO'))

// \w/\d/\s/\b work as expected (glibc's regcomp() supports them as GNU
// extensions, even in POSIX ERE mode). Flags combine the way they do in
// JS -- /gi is both. See api.md's Regex section for the full list of
// supported flags and shorthand classes.
log(/\w+/gi.test('Hello World'))
log('TEST test'.replace(/test/gi, 'x'))

// A pattern that isn't known until compile time -- built here from a
// variable, but could just as easily come from a function argument or
// user input -- can't use the literal syntax, so regex() (this
// compiler's equivalent of JavaScript's `new RegExp(...)`) is still
// available for exactly that case.
text userPattern = '^suite'
regex dynamic = regex(userPattern)
log(dynamic.test('suite 42'))

// claude.md #107: and a pattern built this way can be global too. The
// flag lives on the compiled pattern, not on the call, so both
// spellings mean the same thing -- which the old .replaceAll() could
// never manage, since the choice was made at the call site and this
// pattern's flags aren't known until it runs.
regex anyDigit = regex('[0-9]', 'g')
log('a1b2c3'.replace(anyDigit, '#'))
