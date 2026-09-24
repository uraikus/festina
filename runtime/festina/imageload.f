// runtime.md: the decoders, wired to `img`.
//
// This is what `img photo = 'x.png'` calls before the C loader gets a
// chance. It sniffs the file, hands it to png.f or jpeg.f, and turns
// the result into an image with imageFromPixels (claude.md #346).
//
// ANSWERING null IS THE CONTRACT. Everything these decoders decline --
// a 16-bit or interlaced PNG, a progressive JPEG, a GIF, a file that
// is not an image at all -- comes back null, and codegen falls through
// to festina_load_image for it. That is what lets the port be partial
// without being a regression: a format we have not written yet loads
// exactly as well as it did before.

import png.f
import jpeg.f

img func festinaDecodeImage(path:text) {
    blob raw = path
    if raw.length < 4 { return null }

    arr[int] bytes = []
    int i = 0
    while i < raw.length {
        bytes.push(raw.byteAt(i))
        i = i + 1
    }

    // 137 P N G
    if bytes[0] == 137 && bytes[1] == 80 && bytes[2] == 78 && bytes[3] == 71 {
        arr[int] px = pngDecode(bytes)
        if PNG_ERR == 0 { return imageFromPixels(px, PNG_W, PNG_H) }
        return null
    }

    // FF D8 -- JPEG's start-of-image marker.
    if bytes[0] == 255 && bytes[1] == 216 {
        arr[int] px = jpgDecode(bytes)
        if JPG_ERR == 0 { return imageFromPixels(px, JPG_W, JPG_H) }
        return null
    }

    return null
}
