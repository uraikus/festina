// claude.md #92/#101/#102: images and audio clips loaded, derived,
// stored, read back and reclaimed. Both are handles owning something
// large (a Cairo surface, a block of decoded PCM), so a leak here is
// measured in megabytes rather than bytes.

table Asset {
    name:text
    pic:img
    clip:aud
    save:blob
}

sqlite('DELETE FROM Asset')

int total = 0
int i = 0
while i < 120 {
    // Both formats, both types, loaded from a path.
    img png = 'tiles.png'
    img jpg = 'gradient.jpg'
    aud mp3 = 'tone.mp3'
    aud wav = 'beep.wav'

    // Derived images: clip() allocates a new surface, resize() replaces
    // one in place and drops the source bytes with it.
    img tile = png.clip(0, 0, 32, 32)
    img shrunk = png.clip(32, 0, 32, 32)
    shrunk.resize(8, 8)
    total = total + tile.width + shrunk.height + jpg.width

    // Stored as BLOBs and read back as freshly decoded handles. The
    // clipped tile has no source bytes, so this also exercises the
    // encode-on-demand path.
    blob payload = 'tiles.png'
    sqlite('INSERT INTO Asset (name, pic, clip, save) VALUES (?, ?, ?, ?)',
            [`a${i}`, tile, mp3, payload])
    arr[Asset] rows = sqlite('SELECT * FROM Asset ORDER BY rowid DESC LIMIT 2')
    total = total + rows[0].pic.width
    if rows[0].clip != null {
        total = total + 1
    }
    // claude.md #109: a blob column round-trips its bytes, so this is a
    // fresh handle each pass, released with the row.
    if rows[0].save != null {
        total = total + 1
    }

    // claude.md #109: a blob is the third handle of this shape --
    // content plus the bytes it came from -- and the only refcounted
    // one, so it is the only one where aliasing has to be tracked
    // rather than merely proven absent.
    blob save = `save_${i % 4}.dat`
    save.write(`state ${i}`)
    blob alsoSave = save
    if alsoSave.exists() {
        total = total + 1
    }
    save = 'save_other.dat'          // releases the old handle
    save.write('other')

    // Playback through the channel pool, including a reserved channel.
    // claude.md #109: play/playLoop hand back their channel, and
    // stop() silences every voice of one clip.
    int ch = wav.play()
    total = total + ch
    mp3.playLoop(0)
    stopAudioPlayer(0)
    wav.stop()
    stopAudioPlayer()
    i = i + 1
}
log(total)
