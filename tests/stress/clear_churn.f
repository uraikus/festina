// decisions.md #283: `clear` under ASan/LeakSanitizer.
//
// The claim being checked is narrow and worth stating: that zeroing a
// block before freeing it neither leaks it nor frees it twice. The
// interesting shape is the mix -- a cleared text, a freed text, and a
// cleared binding that is then cleared again -- because the null store
// after the release is what makes the second clear a no-op rather than
// a double free, and a sanitizer is the only thing that can tell the
// difference from outside.
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
    i++
}
log('done')
