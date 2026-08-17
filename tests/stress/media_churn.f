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

    // claude.md #110: save()/saveCopy() on all three handle types. Each
    // resolves a path, may adopt it (freeing the old one), and for a
    // clip encodes a PNG on demand -- three allocations a leak could
    // hide in, per call.
    if tile.save(`tile_${i % 3}.png`) {
        total = total + 1
    }
    if tile.save() {                     // now that it has a path
        total = total + 1
    }
    if tile.saveCopy(`tile_copy_${i % 3}.png`) {
        total = total + 1
    }
    if mp3.saveCopy(`clip_${i % 3}.mp3`) {
        total = total + 1
    }
    if save.saveCopy(`save_copy_${i % 3}.dat`) {
        total = total + 1
    }
    // ...and one straight out of a database column, which is the case
    // that had no path at all until #110.
    if rows[0].save != null {
        if rows[0].save.save(`fromdb_${i % 3}.dat`) {
            total = total + 1
        }
    }

    // claude.md #111: manual free -- the escape hatch for the one leak
    // class img/aud still have (an escaping handle). aliasedSheet makes
    // png escaping, which used to leak the whole decoded surface per
    // iteration by design; free closes it by hand.
    img aliasedSheet = png
    free png
    if aliasedSheet == null { log('unreachable') }
    free jpg
    free save

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
