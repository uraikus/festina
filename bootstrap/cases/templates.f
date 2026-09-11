text name = 'world'
text plain = `no interpolation here`
text one = `hello ${name}`
text two = `${name} and ${name}`
text expr = `sum ${1 + 2} done`
text esc = `literal \${not interpolated}`
text trailing = `${name}`
text nestedBraces = `${ `${name}` }`
