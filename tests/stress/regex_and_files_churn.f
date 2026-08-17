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

    // File round trip: writeFile/readFile/appendFile each allocate.
    if writeFile('churn.txt', `line ${i}`) {
        total = total + 1
    }
    appendFile('churn.txt', '\n')
    text back = readFile('churn.txt')
    if back == '' {
        log('unreachable')
    }
    if fileExists('churn.txt') {
        total = total + 1
    }

    // Time formatting allocates too.
    text stamp = formatTime(now(), '%Y-%m-%d %H:%M:%S')
    if stamp == '' {
        log('unreachable')
    }
    i = i + 1
}
deleteFile('churn.txt')
log(total)
