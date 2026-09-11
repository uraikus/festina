// _split_template counts ${...} brace depth without knowing about string
// literals, so a '}' INSIDE a string closes the interpolation early and
// the fragment left behind fails to lex. Both lexers reject this the
// same way -- it is a shared limitation, pinned here so it stays shared.
map[int] m = {}
text bad = `brace ${ m['}'] } tail`
