// claude.md #92/#101/#102: images and audio clips loaded, derived,
// stored, read back and reclaimed. Both are handles owning something
// large (a Cairo surface, a block of decoded PCM), so a leak here is
// measured in megabytes rather than bytes.

table Asset {
    name:text
    pic:img
    clip:aud
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
    sqlite('INSERT INTO Asset (name, pic, clip) VALUES (?, ?, ?)',
            [`a${i}`, tile, mp3])
    arr[Asset] rows = sqlite('SELECT * FROM Asset ORDER BY rowid DESC LIMIT 2')
    total = total + rows[0].pic.width
    if rows[0].clip != null {
        total = total + 1
    }

    // Playback through the channel pool, including a reserved channel.
    wav.play()
    mp3.playLoop(0)
    stopAudioPlayer(0)
    stopAudioPlayer()
    i = i + 1
}
log(total)
