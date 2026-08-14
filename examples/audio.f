// claude.md #38: aud, loadAudio(), .play()/.stop()/.isPlaying(). Build
// and run with:
//
//   ./bin/festina examples/audio.f -o audio_demo
//   ./audio_demo
//
// Needs a working ALSA "default" output device. examples/beep.wav is a
// short (0.35s, 440Hz) generated tone -- loadAudio() only supports WAV
// (16-bit PCM), not claude.md's own .mp3 example; see api.md's Audio
// section for why. Run from the repository root so the relative path
// below resolves (or edit it to point at any 16-bit PCM WAV file).

aud beep = loadAudio('examples/beep.wav')

log('playing...')
beep.play()
log(`isPlaying(): ${beep.isPlaying()}`)   // true immediately -- see api.md

// setTimeout, not a busy-loop -- playback runs on its own background
// thread (see api.md's Audio section), so the program is free to keep
// scheduling other work while the clip plays.
void func checkStillPlaying() {
    log(`isPlaying() after 100ms: ${beep.isPlaying()}`)
}

void func stopEarly() {
    log('stopping early')
    beep.stop()
    log(`isPlaying() after stop(): ${beep.isPlaying()}`)   // false immediately
}

setTimeout(checkStillPlaying, 100)
setTimeout(stopEarly, 200)
