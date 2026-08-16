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

// claude.md #98: play() does NOT cut off a playback already running --
// sound goes out through a pool of channels (10 by default), so these
// two layer on top of the one above instead of restarting it. That is
// what makes a rapid-fire sound effect work: the faster it fires, the
// more copies overlap, rather than each one silencing the last.
log(`pooled channels: ${maxAudioPlayers()}`)
beep.play()
beep.play()

// setMaxAudioPlayers(1) is how to ask for the old behaviour back: one
// channel, restarted from the beginning on every play().

// claude.md #99: channels are numbered and process-global, so a program
// can reserve one for music and leave the rest to the pool. playLoop
// repeats until stopped AND reserves its channel, so no sound effect can
// ever steal it -- which is the whole reason the reservation exists.
beep.playLoop(0)
log(`looping on channel 0: ${beep.isPlaying()}`)
stopAudioPlayer(0)          // stop that channel and hand it back
log(`after stopAudioPlayer(0): ${beep.isPlaying()}`)

// Back to a one-shot for the timer demo below.
beep.play()

// setTimeout, not a busy-loop -- playback runs on its own background
// thread (see api.md's Audio section), so the program is free to keep
// scheduling other work while the clip plays.
void func checkStillPlaying() {
    log(`isPlaying() after 100ms: ${beep.isPlaying()}`)
}

void func stopEarly() {
    // stop() names the CLIP, so all three overlapping voices end here.
    log('stopping early')
    beep.stop()
    log(`isPlaying() after stop(): ${beep.isPlaying()}`)   // false immediately
}

setTimeout(checkStillPlaying, 100)
setTimeout(stopEarly, 200)
