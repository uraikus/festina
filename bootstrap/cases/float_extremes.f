// claude.md #342: float literals the canonical dumps used to disagree
// about.
//
// A float renders as its IEEE-754 bit pattern now, because that is what
// a float literal IS. Before, the dumps rendered Python's `repr()` and
// the port echoed the source text with trailing zeros stripped -- which
// agree only when the source spelling already is the shortest
// round-trip form. No corpus file contained a literal where that fails,
// so neither the token dump nor the AST dump had ever been asked.
//
// What is here is bounded by what the WHOLE chain reaches, and that
// bound is worth stating: `cgParseFloat` in bootstrap/codegen.f
// converts a decimal literal exactly only inside a window -- at most 18
// significant digits, with the digit string below 2^53 -- and reports
// anything else unported. So the large-magnitude and excess-precision
// halves of the divergence cannot appear in a corpus file at all until
// that window widens. They are measured instead by
// tests/test_bootstrap_lexer.py, over 652 generated literals including
// subnormals, and todo.md carries what is left.

// ---- below where repr() switches to exponent form ------------------
//
// repr() gives `1e-05` from 1e-4 downward, and the old rule gave the
// digits as written. Festina has no exponent literal (7.5.1), so this
// spelling is the only one available and is an ordinary thing to write.
// Both of these are inside cgParseFloat's window, which is what lets
// them be here.
float tiny = 0.00001
float tinier = 0.0000000001

// ---- the controls --------------------------------------------------
//
// These agreed before and must still agree: a change that fixed the two
// above by breaking these would be no improvement. `1.50` is the
// trailing-zero case the old normalization existed for.
float one = 1.0
float half = 0.5
float trailing = 1.50
float e = 2.718281828459045
float twoFiftyTwo = 4503599627370496.0

log(`${tiny} ${tinier}`)
log(`${one} ${half} ${trailing}`)
log(`${e} ${twoFiftyTwo}`)
