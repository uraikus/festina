// decisions.md #283: `clear` under ASan/LeakSanitizer.
//
// The claim being checked is narrow and worth stating: that zeroing a
// block before freeing it neither leaks it nor frees it twice. The
// interesting shape is the mix -- a cleared text, a freed text, and a
// cleared binding that is then cleared again -- because the null store
// after the release is what makes the second clear a no-op rather than
// a double free, and a sanitizer is the only thing that can tell the
// difference from outside.
struct Creds { user:text  token:text }

int i = 0
while i < 4000 {
    text secret = `sk-live-${i}-0123456789abcdefghijklmnopqrstuvwxyz`
    clear secret
    clear secret

    text ordinary = `plain-${i}-0123456789abcdefghijklmnopqrstuvwxyz`
    free ordinary

    text kept = `kept-${i}`
    log(kept.length.toText())

    arr[text] tokens = [`a-${i}`, `b-${i}`]
    clear tokens

    // decisions.md #284: the cascade. A cleared struct frees each field
    // and then its own header, all under the clearing flag, so this is
    // where a double free or a leak in the flagged path would show.
    Creds c
    c.user = `user-${i}`
    c.token = `sk-live-${i}-abcdefghijklmnopqrstuvwxyz`
    clear c

    map[text] secrets = {'k': `v-${i}-abcdefghijklmnopqrstuvwxyz`}
    clear secrets

    // An alias must survive: the flag is only ever read at a free that
    // actually happens, so a release that merely decrements zeroes
    // nothing.
    arr[text] shared = [`s-${i}`]
    arr[text] alias = shared
    clear shared
    log(alias[0])
    i++
}
log('done')
