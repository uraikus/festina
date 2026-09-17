// claude.md #339: the same rule for the other built-in namespace.
// `environment` was pre-registered in the global scope, so a global
// declaration always collided -- and a local one did not, which is why
// this case is a local too. The old behaviour was not silent the way
// Math's was: the error arrived at the first attempt to READ the
// binding, one line further down. It is here so both implementations
// are compared on rejecting the DECLARATION.
//
// Two rejection cases rather than one file carrying both, because the
// first failure wins on both sides -- a second one in the same file
// would never be reached and would pin nothing.

void func f(prefix:text) {
    text environment = 'x'
    log(prefix + environment)
}

f('value: ')
