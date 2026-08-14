// The exact example from README.md's introduction -- build and run with:
//
//   ./bin/festina examples/greet.f -o greet
//   ./greet

table People {
    id:int
    name:text
}

text func greet(name:text) {
    return `Hello, ${name}!`
}

log(greet('Festina'))
