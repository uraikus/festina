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
// Supported: bit depth 8, colour types 0/2/3/4/6, non-interlaced,
// which is what essentially every PNG in practice is. Interlaced and
// 1/2/4/16-bit files are REFUSED rather than half-decoded -- pngWidth
// answers 0 and the caller falls back. A decoder that quietly returns
// wrong pixels is worse than one that says it cannot.

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

// Bytes per pixel in the RAW data, which is what filtering works on --
// not the four bytes per pixel this decoder eventually produces.
int func pngRawBpp(colour:int) {
    if colour == 0 { return 1 }
    if colour == 2 { return 3 }
    if colour == 3 { return 1 }
    if colour == 4 { return 2 }
    if colour == 6 { return 4 }
    return 0
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
        // and entries past its end are opaque.
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
    if depth != 8 { PNG_ERR = 4 return empty }
    if interlace != 0 { PNG_ERR = 5 return empty }
    int bpp = pngRawBpp(colour)
    if bpp == 0 { PNG_ERR = 6 return empty }

    arr[int] raw = inflateZlib(idat)
    int stride = width * bpp
    if raw.length < (height * (stride + 1)) { PNG_ERR = 7 return empty }

    // ---- unfilter, in place, row by row ----
    //
    // Every filter refers to the row ABOVE as already reconstructed,
    // which is why this cannot be done per-row in isolation: row n's
    // correctness depends on row n-1 having been undone first.
    arr[int] flat = []
    int y = 0
    while y < height {
        int ft = raw[y * (stride + 1)]
        int rowAt = (y * (stride + 1)) + 1
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

    // ---- widen to RGBA --------------------------------------------
    arr[int] out = []
    int py = 0
    while py < height {
        int px = 0
        while px < width {
            int at = (py * stride) + (px * bpp)
            int r = 0
            int g = 0
            int bl = 0
            int al = 255
            if colour == 0 {
                r = flat[at]
                g = flat[at]
                bl = flat[at]
            }
            if colour == 2 {
                r = flat[at]
                g = flat[at + 1]
                bl = flat[at + 2]
            }
            if colour == 3 {
                int idx = flat[at]
                if (idx * 3 + 2) < palette.length {
                    r = palette[idx * 3]
                    g = palette[idx * 3 + 1]
                    bl = palette[idx * 3 + 2]
                }
                if idx < trns.length { al = trns[idx] }
            }
            if colour == 4 {
                r = flat[at]
                g = flat[at]
                bl = flat[at]
                al = flat[at + 1]
            }
            if colour == 6 {
                r = flat[at]
                g = flat[at + 1]
                bl = flat[at + 2]
                al = flat[at + 3]
            }
            out.push(r)
            out.push(g)
            out.push(bl)
            out.push(al)
            px = px + 1
        }
        py = py + 1
    }

    PNG_W = width
    PNG_H = height
    return out
}
