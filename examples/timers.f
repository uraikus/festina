// claude.md #69: setTimeout/setInterval/clearTimeout/clearInterval --
// JS-style scheduling. Build and run with:
//
//   ./bin/festina examples/timers.f -o timers
//   ./timers
//
// Festina has no first-class functions or closures, so a timer's
// callback can only be the bare name of an already-declared,
// zero-parameter, void-returning function -- not an inline expression
// or lambda. This program runs for a bit over one second and then
// exits on its own once every timer is done/cleared -- see api.md's
// Timers section for the full design.

int tickCount = 0
// Declared (and assigned a placeholder) before the functions below that
// reference it -- Festina resolves top-level declarations in source
// order, so a function can only see a global declared textually above
// it, the same as any other top-level statement.
int intervalId = 0

void func showMessage() {
    log('one second has passed')
}

// A program keeps running as long as it has a pending setTimeout() or
// an uncleared setInterval() -- so this interval needs to be cleared
// somewhere, or the process would run forever, exactly like an
// uncleared JS interval would. It clears ITSELF after five ticks:
// counting inside the callback is deterministic on any scheduler,
// where racing a second setTimeout against the fifth tick is not --
// the first macOS CI run caught exactly that race (a loaded runner
// fired the stop after only four ticks).
void func tick() {
    tickCount = tickCount + 1
    log(`tick ${tickCount}`)
    if tickCount == 5 {
        log('stopping the interval')
        clearInterval(intervalId)
    }
}

log('scheduling a one-shot timeout and a repeating interval...')

setTimeout(showMessage, 1000)
intervalId = setInterval(tick, 100)
