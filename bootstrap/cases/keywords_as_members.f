// Every keyword stays a valid MEMBER name -- Parser.eat_name accepts
// keyword tokens, which is what keeps blob's own .delete() parsing.
blob f = 'x.txt'
f.delete()
text s = 'hello world'
if s.match(/world/) { log('found') }
