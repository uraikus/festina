// claude.md #85/#86/#93/#102: regexes (literal, cached once per source
// location, and dynamic, recompiled per call) plus the file and time
// builtins, which all hand back freshly allocated text.

int total = 0
int i = 0
while i < 1500 {
    text subject = `room ${i}, building ${i % 7}`

    // A literal regex is a process-lifetime cached pointer and must
    // never be freed; a dynamic one is compiled per call and must be.
    if /[0-9]+/.test(subject) {
        total = total + 1
    }
    regex dynamic = regex(`building ${i % 7}`)
    if dynamic.test(subject) {
        total = total + 1
    }
    if regex('[a-z]+').test(subject) {
        total = total + 1
    }

    // Match/replace produce fresh text through several different
    // runtime functions.
    text found = subject.match(/[0-9]+/)
    text swapped = subject.replace(/room/, 'suite')
    text allSwapped = subject.replace(/[0-9]/g, '#')
    if found == swapped {
        log('unreachable')
    }
    if allSwapped == '' {
        log('unreachable')
    }

    // claude.md #109: a file round trip through a blob. Every one of
    // these allocates -- the handle itself, its path, its byte buffer,
    // and toText()'s owned copy -- and a fresh blob is declared each
    // iteration, so a missed release here is one leak per pass.
    blob f = 'churn.txt'
    if f.write(`line ${i}`) {
        total = total + 1
    }
    f.append('\n')
    text back = f.toText()
    if back == '' {
        log('unreachable')
    }
    if f.exists() {
        total = total + 1
    }

    // ...and aliasing one, which is what makes the refcount do work
    // rather than just counting to one and back. `shared` is released
    // at scope exit; `f` is released too, and only the second of those
    // actually frees anything.
    blob shared = f
    if shared.toText() != back {
        log('unreachable')
    }

    // Time formatting allocates too.
    text stamp = formatTime(now(), '%Y-%m-%d %H:%M:%S')
    if stamp == '' {
        log('unreachable')
    }
    i = i + 1
}
blob leftover = 'churn.txt'
leftover.delete()
log(total)
