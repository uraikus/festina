// claude.md #272: \0 is rejected at compile time now. text is
// NUL-terminated, so it could never hold the value this escape asks for
// -- 'a\0b'.length answered 1, silently. Both lexers reject it here.
text bad = 'a\0b'
