// The classic JS lexical ambiguity, and the only shape that actually
// TESTS it: two '/' on ONE line. With a single '/' per line the regex
// attempt fails on the newline and falls back to division anyway, so
// such a line passes whether or not the denylist works at all -- the
// first version of this file made exactly that mistake.
//
// Every '/' below is DIVISION, because each follows something that can
// end an expression.
int a = 100
int b = a / 2 / 5
arr[int] xs = [80, 40]
int c = xs[0] / 2 / 4
int d = (a + b) / 5 / 2
int e = 60 / 3 / 2
a++
int f = a / 2 / 1
