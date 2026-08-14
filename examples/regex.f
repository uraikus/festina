// claude.md #67, #68: regex(), .test(), .match(), .replace()/.replaceAll().
// Build and run with:
//
//   ./bin/festina examples/regex.f -o regex_demo
//   ./regex_demo

regex digits = regex('[0-9]+')

log(digits.test('room 42'))
log(digits.test('no numbers here'))

text found = 'room 42, building 7'.match(digits)
log(found)

log('room 42'.replace('room', 'suite'))
log('a1b2c3'.replaceAll(regex('[0-9]'), '-'))

regex greeting = regex('^hello$', 'i')
log(greeting.test('HELLO'))
