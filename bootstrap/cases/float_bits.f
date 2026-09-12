// decisions.md #290: float literals, for the IEEE-754 encoder in
// bootstrap/codegen.f.
//
// The repository corpus contains exactly eight distinct float literals,
// all between 0.1 and 127.0, so it cannot tell a correct encoder from
// one that happens to work on small tidy numbers. These exercise the
// parts that actually vary: a power of two (mantissa all zero), a
// repeating binary fraction, values either side of 1.0 so the
// normalize loop runs in both directions, a large exponent and a small
// one, and a trailing-zero spelling that must denote the same double as
// its shorter form.
float p2 = 2.0
float p2big = 1024.0
float p2small = 0.5
float third = 0.3333333333
float tenth = 0.1
float justOver = 1.0000000001
float justUnder = 0.9999999999
float big = 999999999999.5
// 0.0001 rather than something smaller: below 1e-4 Python's repr()
// switches to scientific notation and bootstrap/lexer.f does not
// follow it there (see todo.md). This still drives the normalize loop
// fourteen times in the negative direction, which is what the encoder
// needs exercised.
float small = 0.0001
float trailing = 1.50
float zero = 0.0
float one = 1.0
