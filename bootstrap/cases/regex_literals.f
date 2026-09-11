// ...and everything here is a REGEX, because each '/' sits where an
// expression may start.
regex plain = /abc/
regex flagged = /abc/gi
regex escaped = /a\/b/
regex classy = /[0-9]+\.[0-9]+/
regex afterComma = 'x'.replace(/y/g, 'z')
if 'q'.match(/^q$/) { log('yes') }
