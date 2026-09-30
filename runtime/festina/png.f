// runtime.md phase 1: PNG decoding (RFC 2083) in Festina.
//
// What Cairo's cairo_image_surface_create_from_png_stream does for us
// today. A PNG is a signature, a chunk list, and one zlib stream whose
// bytes are scanlines each prefixed by a filter type -- so the work is
// inflate (see inflate.f), then undoing the filters, then widening
// whatever colour format the file used into RGBA.
//
// Output is one flat arr[int] of bytes, four per pixel, top row first:
// the shape a rasteriser and a surface both want, and the shape
// runtime.md says a decoder can produce (arr[int] is mutable; blob is
// not writable in binary at all).
//
// Supported: every colour type (0 grey, 2 RGB, 3 palette, 4 grey+alpha,
// 6 RGBA) at every bit depth the format allows for it (1, 2, 4, 8, 16),
// with or without Adam7 interlacing, and tRNS for all three kinds it
// can mean -- an alpha per palette entry, or a colour key for grey and
// for RGB. That is every PNG; runtime.md phase 7 slice 6 closed the gap
// (decision 3: "implement"), because once libpng goes there is nothing
// for a file this decoder declines to fall back to.
//
// What "the same as libpng" means here, where it is a choice: Cairo's
// reader (cairo-png.c) expands grey of 1, 2 and 4 bits to 8 by scaling
// (x * 255 / (2^depth - 1): 0/255, 0/85/170/255, 0/17/.../255), keeps
// the HIGH BYTE of a 16-bit sample (png_set_strip_16), turns tRNS into
// alpha 0 for a pixel whose whole sample matches the key at the file's
// own depth (so a 16-bit key is compared at 16 bits, before the strip),
// and applies no gamma whatever a gAMA, sRGB or iCCP chunk says. This
// does the same. (On a machine with cairo 1.17.2 or later the C loader
// does NOT do this for 16-bit files: it returns a float surface the
// runtime reads as ARGB32, which is garbage -- so the decoder here is
// the first correct 16-bit path, not a copy of one.)

import inflate.f

int PNG_W = 0
int PNG_H = 0
int PNG_ERR = 0

int func pngAbs(v:int) {
    if v < 0 { return 0 - v }
    return v
}

int func pngBE32(b:arr[int], at:int) {
    return (b[at] << 24) | (b[at + 1] << 16) | (b[at + 2] << 8) | b[at + 3]
}

// The Paeth predictor (RFC 2083 6.6): pick whichever of left, above and
// upper-left is closest to their linear estimate. Ties go to `a` then
// `b`, and that order is normative -- picking differently produces an
// image that is subtly wrong only on some rows.
int func pngPaeth(a:int, b:int, c:int) {
    int p = a + b - c
    int pa = pngAbs(p - a)
    int pb = pngAbs(p - b)
    int pc = pngAbs(p - c)
    if pa <= pb && pa <= pc { return a }
    if pb <= pc { return b }
    return c
}

// Bits in one pixel of the RAW data (before this decoder widens it to
// four bytes), and the bytes per pixel filtering works in: a pixel of
// fewer than eight bits still filters against "the byte before", so its
// filter distance is one byte, never a fraction of one.
int func pngChannels(colour:int) {
    if colour == 0 { return 1 }
    if colour == 2 { return 3 }
    if colour == 3 { return 1 }
    if colour == 4 { return 2 }
    if colour == 6 { return 4 }
    return 0
}

// Which bit depths each colour type may have (RFC 2083 table 1). A
// file with any other pairing is not a PNG.
bool func pngDepthOk(colour:int, depth:int) {
    if colour == 3 { return depth == 1 || depth == 2 || depth == 4 || depth == 8 }
    if colour == 0 { return depth == 1 || depth == 2 || depth == 4 || depth == 8 || depth == 16 }
    return depth == 8 || depth == 16
}

// The IHDR fields and the ancillary chunks the conversion needs, in
// globals so a pass over an interlaced image does not carry a dozen
// arguments through every call.
int PNG_COLOUR = 0
int PNG_DEPTH = 8
arr[int] PNG_PAL = []
arr[int] PNG_TRNS = []
bool PNG_KEYED = false
int PNG_KEY_R = 0
int PNG_KEY_G = 0
int PNG_KEY_B = 0

// Where the next sub-image's filtered scanlines start in the inflated
// data. Adam7 is seven of them, one after another.
int PNG_RAWPOS = 0

// Unfilters a w x h sub-image of `bits` bits a pixel out of `raw`, from
// PNG_RAWPOS on, and answers its rows as one flat array with the filter
// bytes gone. Every filter refers to the row ABOVE as already
// reconstructed, which is why this cannot be done per-row in isolation.
arr[int] func pngUnfilter(raw:arr[int], w:int, h:int, bits:int) {
    int stride = Math.floorDiv((w * bits) + 7, 8)
    int bpp = Math.floorDiv(bits, 8)
    if bpp < 1 { bpp = 1 }
    arr[int] flat = []
    int y = 0
    while y < h {
        int rowAt = PNG_RAWPOS + (y * (stride + 1))
        int ft = raw[rowAt]
        rowAt = rowAt + 1
        int x = 0
        while x < stride {
            int cur = raw[rowAt + x]
            int a = 0
            int b = 0
            int c = 0
            if x >= bpp { a = flat[(y * stride) + x - bpp] }
            if y > 0 { b = flat[((y - 1) * stride) + x] }
            if x >= bpp && y > 0 { c = flat[((y - 1) * stride) + x - bpp] }
            int v = cur
            if ft == 1 { v = cur + a }
            if ft == 2 { v = cur + b }
            if ft == 3 { v = cur + Math.floorDiv(a + b, 2) }
            if ft == 4 { v = cur + pngPaeth(a, b, c) }
            flat.push(v & 255)
            x = x + 1
        }
        y = y + 1
    }
    PNG_RAWPOS = PNG_RAWPOS + (h * (stride + 1))
    return flat
}

// Sample `i` of a row starting at `rowAt`, at the file's own depth: the
// whole 16 bits, or the 1, 2 or 4 bits it occupies within its byte
// (leftmost sample in the high bits).
int func pngSample(flat:arr[int], rowAt:int, i:int) {
    int depth = PNG_DEPTH
    if depth == 8 { return flat[rowAt + i] }
    if depth == 16 { return (flat[rowAt + (i * 2)] << 8) | flat[rowAt + (i * 2) + 1] }
    int bit = i * depth
    int byte = flat[rowAt + Math.floorDiv(bit, 8)]
    int shift = 8 - depth - (bit % 8)
    return (byte >> shift) & ((1 << depth) - 1)
}

// A sample of the file's depth as the byte the picture gets: 16 bits
// keep the high byte, 8 stay, and 1, 2 and 4 scale to the full range.
int func pngByte(v:int) {
    int depth = PNG_DEPTH
    if depth == 8 { return v }
    if depth == 16 { return v >> 8 }
    return Math.floorDiv(v * 255, (1 << depth) - 1)
}

// One sub-image, widened to RGBA into its places in `out`: pixel (px, py)
// of a sub-image whose origin is (x0, y0) and whose pixels lie dx and dy
// apart lands at (x0 + px*dx, y0 + py*dy) of the full picture. A plain
// image is the one sub-image with dx = dy = 1.
void func pngWiden(flat:arr[int], pw:int, ph:int, x0:int, y0:int, dx:int, dy:int,
                   width:int, out:arr[int]) {
    int colour = PNG_COLOUR
    int ch = pngChannels(colour)
    int stride = Math.floorDiv((pw * ch * PNG_DEPTH) + 7, 8)
    int py = 0
    while py < ph {
        int rowAt = py * stride
        int px = 0
        while px < pw {
            int r = 0
            int g = 0
            int bl = 0
            int al = 255
            int i = px * ch
            if colour == 0 {
                int s = pngSample(flat, rowAt, i)
                r = pngByte(s)
                g = r
                bl = r
                if PNG_KEYED && s == PNG_KEY_R { al = 0 }
            }
            if colour == 2 {
                int sr = pngSample(flat, rowAt, i)
                int sg = pngSample(flat, rowAt, i + 1)
                int sb = pngSample(flat, rowAt, i + 2)
                r = pngByte(sr)
                g = pngByte(sg)
                bl = pngByte(sb)
                if PNG_KEYED && sr == PNG_KEY_R && sg == PNG_KEY_G && sb == PNG_KEY_B { al = 0 }
            }
            if colour == 3 {
                int idx = pngSample(flat, rowAt, i)
                if (idx * 3 + 2) < PNG_PAL.length {
                    r = PNG_PAL[idx * 3]
                    g = PNG_PAL[idx * 3 + 1]
                    bl = PNG_PAL[idx * 3 + 2]
                }
                if idx < PNG_TRNS.length { al = PNG_TRNS[idx] }
            }
            if colour == 4 {
                r = pngByte(pngSample(flat, rowAt, i))
                g = r
                bl = r
                al = pngByte(pngSample(flat, rowAt, i + 1))
            }
            if colour == 6 {
                r = pngByte(pngSample(flat, rowAt, i))
                g = pngByte(pngSample(flat, rowAt, i + 1))
                bl = pngByte(pngSample(flat, rowAt, i + 2))
                al = pngByte(pngSample(flat, rowAt, i + 3))
            }
            int at = (((y0 + (py * dy)) * width) + (x0 + (px * dx))) * 4
            out[at] = r
            out[at + 1] = g
            out[at + 2] = bl
            out[at + 3] = al
            px = px + 1
        }
        py = py + 1
    }
}

// Decodes to RGBA bytes. On refusal answers an empty array with
// PNG_ERR set and PNG_W/PNG_H zero.
arr[int] func pngDecode(src:arr[int]) {
    PNG_W = 0
    PNG_H = 0
    PNG_ERR = 0
    arr[int] empty = []

    if src.length < 8 { PNG_ERR = 1 return empty }
    // 137 P N G \r \n 26 \n -- the \r\n is there to catch a transfer
    // that converted line endings, which is exactly the corruption a
    // text-mode FTP used to cause.
    if src[0] != 137 { PNG_ERR = 1 return empty }
    if src[1] != 80 { PNG_ERR = 1 return empty }
    if src[2] != 78 { PNG_ERR = 1 return empty }
    if src[3] != 71 { PNG_ERR = 1 return empty }

    int width = 0
    int height = 0
    int depth = 0
    int colour = 0
    int interlace = 0
    arr[int] idat = []
    arr[int] palette = []
    arr[int] trns = []

    int pos = 8
    bool done = false
    while done == false && (pos + 8) <= src.length {
        int clen = pngBE32(src, pos)
        int t0 = src[pos + 4]
        int t1 = src[pos + 5]
        int t2 = src[pos + 6]
        int t3 = src[pos + 7]
        int body = pos + 8
        if (body + clen) > src.length { PNG_ERR = 2 return empty }

        // IHDR
        if t0 == 73 && t1 == 72 && t2 == 68 && t3 == 82 {
            width = pngBE32(src, body)
            height = pngBE32(src, body + 4)
            depth = src[body + 8]
            colour = src[body + 9]
            interlace = src[body + 12]
        }
        // PLTE
        if t0 == 80 && t1 == 76 && t2 == 84 && t3 == 69 {
            int k = 0
            while k < clen {
                palette.push(src[body + k])
                k = k + 1
            }
        }
        // tRNS -- for colour type 3 this is one alpha per palette entry,
        // and entries past its end are opaque; for grey and RGB it is
        // one colour, at the file's own depth, that means "transparent".
        if t0 == 116 && t1 == 82 && t2 == 78 && t3 == 83 {
            int k = 0
            while k < clen {
                trns.push(src[body + k])
                k = k + 1
            }
        }
        // IDAT -- one zlib stream SPLIT across chunks, so these
        // concatenate before inflating rather than inflating each.
        if t0 == 73 && t1 == 68 && t2 == 65 && t3 == 84 {
            int k = 0
            while k < clen {
                idat.push(src[body + k])
                k = k + 1
            }
        }
        // IEND
        if t0 == 73 && t1 == 69 && t2 == 78 && t3 == 68 { done = true }

        pos = body + clen + 4
    }

    if width <= 0 || height <= 0 { PNG_ERR = 3 return empty }
    int ch = pngChannels(colour)
    if ch == 0 { PNG_ERR = 6 return empty }
    // Not a depth this colour type may have: not a PNG.
    if !pngDepthOk(colour, depth) { PNG_ERR = 4 return empty }
    if interlace != 0 && interlace != 1 { PNG_ERR = 5 return empty }
    if colour == 3 && palette.length < 3 { PNG_ERR = 6 return empty }

    PNG_COLOUR = colour
    PNG_DEPTH = depth
    PNG_PAL = palette
    PNG_TRNS = []
    PNG_KEYED = false
    PNG_KEY_R = 0
    PNG_KEY_G = 0
    PNG_KEY_B = 0
    if colour == 3 { PNG_TRNS = trns }
    if colour == 0 && trns.length >= 2 {
        PNG_KEYED = true
        PNG_KEY_R = (trns[0] << 8) | trns[1]
    }
    if colour == 2 && trns.length >= 6 {
        PNG_KEYED = true
        PNG_KEY_R = (trns[0] << 8) | trns[1]
        PNG_KEY_G = (trns[2] << 8) | trns[3]
        PNG_KEY_B = (trns[4] << 8) | trns[5]
    }

    arr[int] raw = inflateZlib(idat)
    int bits = ch * depth

    // The whole picture, transparent black, for the sub-images to be
    // written into: an interlaced image arrives in scattered pieces.
    arr[int] out = []
    int total = width * height * 4
    int z = 0
    while z < total {
        out.push(0)
        z = z + 1
    }

    PNG_RAWPOS = 0
    if interlace == 0 {
        int need = height * (Math.floorDiv((width * bits) + 7, 8) + 1)
        if raw.length < need { PNG_ERR = 7 return empty }
        arr[int] flat = pngUnfilter(raw, width, height, bits)
        pngWiden(flat, width, height, 0, 0, 1, 1, width, out)
    } else {
        // Adam7 (RFC 2083 8.2): seven passes over the picture, each a
        // smaller image of every 8th, 4th or 2nd pixel from its own
        // starting corner. A pass with no pixels has no scanlines at all,
        // not even a filter byte.
        arr[int] startX = [0, 4, 0, 2, 0, 1, 0]
        arr[int] startY = [0, 0, 4, 0, 2, 0, 1]
        arr[int] stepX = [8, 8, 4, 4, 2, 2, 1]
        arr[int] stepY = [8, 8, 8, 4, 4, 2, 2]
        int pass = 0
        while pass < 7 {
            int pw = 0
            int ph = 0
            if width > startX[pass] { pw = Math.floorDiv(width - startX[pass] + stepX[pass] - 1, stepX[pass]) }
            if height > startY[pass] { ph = Math.floorDiv(height - startY[pass] + stepY[pass] - 1, stepY[pass]) }
            if pw > 0 && ph > 0 {
                int need = PNG_RAWPOS + (ph * (Math.floorDiv((pw * bits) + 7, 8) + 1))
                if raw.length < need { PNG_ERR = 7 return empty }
                arr[int] flat = pngUnfilter(raw, pw, ph, bits)
                pngWiden(flat, pw, ph, startX[pass], startY[pass], stepX[pass], stepY[pass], width, out)
            }
            pass = pass + 1
        }
    }

    PNG_W = width
    PNG_H = height
    return out
}
