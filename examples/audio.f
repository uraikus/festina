// claude.md #38/#99/#100/#109: aud, .play()/.playLoop()/.isPlaying()/
// .stop(), and stopAudioPlayer(). Build
// and run with:
//
//   ./bin/festina examples/audio.f -o audio_demo
//   ./audio_demo
//
// Needs a working ALSA "default" output device. examples/beep.wav is a
// short (0.35s, 440Hz) generated tone. WAV (16-bit PCM) and MP3 are
// both supported; see api.md's Audio section. Run from the repository
// root so the relative path
// below resolves (or edit it to point at any 16-bit PCM WAV file).

// claude.md #100/#101: a path declares the clip -- the same way a
// blob, a color, a font and an img are each written as the text
// that reads best. claude.md #109 removed loadAudio(), so this is
// now the only spelling. WAV (16-bit PCM) and MP3 are both decoded,
// sniffed from the file's contents rather than its extension.
aud beep = 'examples/beep.wav'

log('playing...')

// claude.md #109: play() hands back the channel it chose. Automatic
// assignment picks one the program could not otherwise learn, which
// used to leave the pool addressable only by naming channels by hand.
int first = beep.play()
log(`playing on channel ${first}`)
log(`isPlaying(): ${beep.isPlaying()}`)   // true immediately -- see api.md

// claude.md #98: play() does NOT cut off a playback already running --
// sound goes out through a pool of channels (10 by default), so these
// two layer on top of the one above instead of restarting it. That is
// what makes a rapid-fire sound effect work: the faster it fires, the
// more copies overlap, rather than each one silencing the last.
log(`pooled channels: ${maxAudioPlayers()}`)
int second = beep.play()
int third = beep.play()
log(`three channels: ${first} ${second} ${third}`)

// ...and any one of them can be stopped on its own, now that its
// number is known.
stopAudioPlayer(third)

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
    // claude.md #109: beep.stop() is back, and stops every channel
    // playing THIS clip -- which is the right tool for "silence this
    // sound" and the wrong one for "end just that gunshot", where
    // stopAudioPlayer(channel) is what the number above is for. A bare
    // stopAudioPlayer() stops every channel of every clip.
    log('stopping early')
    beep.stop()
    log(`isPlaying() after beep.stop(): ${beep.isPlaying()}`)   // false immediately
    stopAudioPlayer()
}

setTimeout(checkStillPlaying, 100)
setTimeout(stopEarly, 200)
