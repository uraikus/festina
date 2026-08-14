// claude.md #67, #68: /pattern/flags literals, .test(), .match(),
// .replace()/.replaceAll(). Build and run with:
//
//   ./bin/festina examples/regex.f -o regex_demo
//   ./regex_demo

regex digits = /[0-9]+/

log(digits.test('room 42'))
log(digits.test('no numbers here'))

text found = 'room 42, building 7'.match(digits)
log(found)

log('room 42'.replace('room', 'suite'))
log('a1b2c3'.replaceAll(/[0-9]/, '-'))

regex greeting = /^hello$/i
log(greeting.test('HELLO'))

// \w/\d/\s/\b work as expected (glibc's regcomp() supports them as GNU
// extensions, even in POSIX ERE mode) -- 'g' is accepted for JS
// familiarity but has no additional effect (.replace()/.replaceAll()
// already say first-vs-every-match explicitly). See api.md's Regex
// section for the full list of supported flags and shorthand classes.
log(/\w+/gi.test('Hello World'))

// A pattern that isn't known until compile time -- built here from a
// variable, but could just as easily come from a function argument or
// user input -- can't use the literal syntax, so regex() (this
// compiler's equivalent of JavaScript's `new RegExp(...)`) is still
// available for exactly that case.
text userPattern = '^suite'
regex dynamic = regex(userPattern)
log(dynamic.test('suite 42'))
