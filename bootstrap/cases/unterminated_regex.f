// No closing '/' before the newline, so this is NOT a regex literal --
// the Python lexer falls through and lexes the '/' as division.
int n = 4
log(n)
int m = / 2
