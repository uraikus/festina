// claude.md #341: the `test` type (specification.md 11.7), as the
// corpus sees it.
//
// A corpus file is compiled the way `festina compile` compiles one,
// with assertions OFF -- so what the differential harnesses can measure
// here is the front end (a declaration parses and analyses the same on
// both sides) and codegen's STRIPPING half (an ordinary build emits
// nothing for any of it). The emitting half has no harness that can see
// it until `bootstrap/irdumpf.f` grows a test build of its own, which
// is recorded in todo.md rather than pretended about here.
//
// So this file is deliberately a working PROGRAM with assertions
// scattered through it: every log() below runs and prints, and none of
// the test declarations or assertions contributes a single IR line.

test basicMath = 'basic math test'
basicMath(2 + 2, 4)
basicMath(3 - 1, 2)
basicMath(2 - 2, 4)

// A description built at runtime rather than written as a literal --
// 11.7 makes it an expression on purpose, and a port that accepted only
// a literal would differ here rather than on the simple case above.
text subject = 'interpolation'
test described = `string ${subject}`

text name = 'Patrick'
text greeting = `Hello, ${name}!`
described(greeting, 'Hello, Patrick!')

// Every type 11.7.1 allows, so a port that restricted the set
// differently rejects this file rather than agreeing about it.
test kinds = 'the comparable types'
kinds(1, 1)
kinds(1.5, 1.5)
kinds(true, true)
kinds('a', 'a')
kinds(1, null)
kinds.near(0.1 + 0.2, 0.3, 0.0001)

// An assertion is an EXPRESSION of type bool (11.7.1), which is a
// separate thing for a port to get right: the call has to type as bool
// wherever a bool belongs, not only as a bare statement.
bool passed = kinds(2, 2)
log(`assertion answered: ${passed}`)

// Grouping is by the binding CALLED, not by position -- so an assertion
// inside a function body belongs to the group its callee names, and the
// declaration is nowhere near it.
void func check(n:int) {
    basicMath(n * 2, n + n)
}
check(3)
check(4)

int total = 0
for int i = 0, i < 4, i++ {
    total = total + i
    basicMath(total, total)
}
log(`total ${total}`)
log('done')
