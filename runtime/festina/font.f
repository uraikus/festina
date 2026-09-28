// runtime.md phase 5: TrueType fonts.
//
// A font here is the file's own bytes, read in place: nothing is
// copied out into a second representation, because every table is
// already laid out for random access by glyph index. fntOpen finds the
// tables and checks that each lies inside the file; after that, a
// glyph is a lookup and a decode.
//
// ANSWERING AN ERROR IS THE CONTRACT, as it is for png.f and jpeg.f.
// Anything this does not handle -- CFF outlines, a collection, a font
// with no Unicode cmap -- comes back as a nonzero FNT_ERR, and the
// caller falls through to Cairo for it. A truncated or corrupt file
// does the same rather than reading past the end: every multi-byte
// read goes through fntU16/fntU32, which check the range first.
//
// Tags and magic numbers are written as the integer their four bytes
// make, big-endian, with the tag in a comment: 'head' is 1751474532.

arr[int] FNT_IN = []
int FNT_ERR = 0

int FNT_E_SHORT = 1          // a read fell outside the file
int FNT_E_NOT_SFNT = 2       // not a TrueType/OpenType file at all
int FNT_E_CFF = 3            // CFF or CFF2 outlines ('OTTO') -- refused
int FNT_E_COLLECTION = 4     // a .ttc -- refused until a slice needs one
int FNT_E_TABLE = 5          // a required table is missing
int FNT_E_NO_CMAP = 6        // no Unicode cmap in a format read here
int FNT_E_GLYPH = 7          // a glyph index out of range, or a bad glyph
int FNT_E_COMPOSITE = 8      // a composite glyph -- slice 2 reads these

int FNT_UPEM = 0
int FNT_NGLYPHS = 0
int FNT_ASCENT = 0
int FNT_DESCENT = 0
int FNT_LINEGAP = 0
int FNT_NHMETRICS = 0
bool FNT_LOCA_LONG = false

int FNT_HEAD = 0
int FNT_MAXP = 0
int FNT_HHEA = 0
int FNT_HMTX = 0
int FNT_CMAP = 0
int FNT_LOCA = 0
int FNT_GLYF = 0
int FNT_GLYF_LEN = 0

// The active cmap subtable: its absolute offset and its format.
int FNT_CMAP_SUB = 0
int FNT_CMAP_FMT = 0

// ---- reading ----

bool func fntHas(at:int, n:int) {
    if at < 0 || n < 0 { return false }
    return at + n <= FNT_IN.length
}

int func fntU8(at:int) {
    if !fntHas(at, 1) { FNT_ERR = FNT_E_SHORT return 0 }
    return FNT_IN[at]
}

int func fntU16(at:int) {
    if !fntHas(at, 2) { FNT_ERR = FNT_E_SHORT return 0 }
    return (FNT_IN[at] << 8) | FNT_IN[at + 1]
}

int func fntS16(at:int) {
    int v = fntU16(at)
    if v >= 32768 { return v - 65536 }
    return v
}

int func fntU32(at:int) {
    if !fntHas(at, 4) { FNT_ERR = FNT_E_SHORT return 0 }
    return (FNT_IN[at] << 24) | (FNT_IN[at + 1] << 16) | (FNT_IN[at + 2] << 8) | FNT_IN[at + 3]
}

// ---- the table directory ----

// The offset of a table, or -1 when there is none. A table that IS
// listed but runs past the end of the file, or is shorter than its own
// fixed header, is a damaged file rather than a missing table: that
// sets E_SHORT. Either way every later read inside a table found here
// can only fail on a corrupt glyph, never on the table itself.
int func fntFindTable(tag:int, minLen:int) {
    int n = fntU16(4)
    int i = 0
    while i < n {
        int rec = 12 + (i * 16)
        if fntU32(rec) == tag {
            int off = fntU32(rec + 8)
            int len = fntU32(rec + 12)
            if len < minLen || !fntHas(off, len) { FNT_ERR = FNT_E_SHORT return -1 }
            return off
        }
        i = i + 1
    }
    return -1
}

int func fntTableLength(tag:int) {
    int n = fntU16(4)
    int i = 0
    while i < n {
        int rec = 12 + (i * 16)
        if fntU32(rec) == tag { return fntU32(rec + 12) }
        i = i + 1
    }
    return 0
}

// Open a font. Returns FNT_ERR: 0 when every table this file needs is
// present and inside the file.
int func fntOpen(src:arr[int]) {
    FNT_IN = src
    FNT_ERR = 0
    FNT_CMAP_SUB = 0
    FNT_CMAP_FMT = 0
    if src.length < 12 { FNT_ERR = FNT_E_SHORT return FNT_ERR }

    int version = fntU32(0)
    if version == 1330926671 { FNT_ERR = FNT_E_CFF return FNT_ERR }          // 'OTTO'
    if version == 1953784678 { FNT_ERR = FNT_E_COLLECTION return FNT_ERR }   // 'ttcf'
    if version != 65536 && version != 1953658213 {                           // 1.0, 'true'
        FNT_ERR = FNT_E_NOT_SFNT
        return FNT_ERR
    }
    if !fntHas(12, fntU16(4) * 16) { FNT_ERR = FNT_E_SHORT return FNT_ERR }

    FNT_HEAD = fntFindTable(1751474532, 54)      // 'head'
    FNT_MAXP = fntFindTable(1835104368, 6)       // 'maxp'
    FNT_HHEA = fntFindTable(1751672161, 36)      // 'hhea'
    FNT_HMTX = fntFindTable(1752003704, 4)       // 'hmtx'
    FNT_CMAP = fntFindTable(1668112752, 4)       // 'cmap'
    FNT_LOCA = fntFindTable(1819239265, 2)       // 'loca'
    FNT_GLYF = fntFindTable(1735162214, 0)       // 'glyf'
    if FNT_ERR != 0 { return FNT_ERR }

    // No glyf but a CFF table is a CFF font with a TrueType version
    // number -- rare, and refused for the same reason 'OTTO' is.
    if FNT_GLYF < 0 {
        if fntFindTable(1128678944, 0) >= 0 || fntFindTable(1128678962, 0) >= 0 {  // 'CFF ', 'CFF2'
            FNT_ERR = FNT_E_CFF
            return FNT_ERR
        }
    }
    if FNT_HEAD < 0 || FNT_MAXP < 0 || FNT_HHEA < 0 || FNT_HMTX < 0 ||
       FNT_CMAP < 0 || FNT_LOCA < 0 || FNT_GLYF < 0 {
        FNT_ERR = FNT_E_TABLE
        return FNT_ERR
    }
    FNT_GLYF_LEN = fntTableLength(1735162214)

    FNT_UPEM = fntU16(FNT_HEAD + 18)
    FNT_LOCA_LONG = fntS16(FNT_HEAD + 50) == 1
    FNT_NGLYPHS = fntU16(FNT_MAXP + 4)
    FNT_ASCENT = fntS16(FNT_HHEA + 4)
    FNT_DESCENT = fntS16(FNT_HHEA + 6)
    FNT_LINEGAP = fntS16(FNT_HHEA + 8)
    FNT_NHMETRICS = fntU16(FNT_HHEA + 34)
    if FNT_UPEM == 0 || FNT_NHMETRICS == 0 { FNT_ERR = FNT_E_TABLE return FNT_ERR }

    int locaEntry = 2
    if FNT_LOCA_LONG { locaEntry = 4 }
    int hmtxNeeds = (FNT_NHMETRICS * 4) + ((FNT_NGLYPHS - FNT_NHMETRICS) * 2)
    if FNT_NHMETRICS > FNT_NGLYPHS { hmtxNeeds = FNT_NHMETRICS * 4 }
    if !fntHas(FNT_LOCA, (FNT_NGLYPHS + 1) * locaEntry) || !fntHas(FNT_HMTX, hmtxNeeds) {
        FNT_ERR = FNT_E_SHORT
        return FNT_ERR
    }
    if FNT_ERR != 0 { return FNT_ERR }

    // The cmap: a full-Unicode table (format 12) when there is one,
    // since it is the only kind that reaches past U+FFFF, and the BMP
    // table (format 4) otherwise -- the preference FreeType has too.
    if !fntSelectCmap(12) {
        if !fntSelectCmap(4) {
            FNT_ERR = FNT_E_NO_CMAP
            return FNT_ERR
        }
    }
    return FNT_ERR
}

// ---- cmap ----

// Make the first Unicode subtable of this format the active one.
// Unicode means platform 0 (any encoding), or Windows (3) with
// encoding 1 (BMP) or 10 (full). Returns whether there was one.
bool func fntSelectCmap(format:int) {
    int n = fntU16(FNT_CMAP + 2)
    int i = 0
    while i < n {
        int rec = FNT_CMAP + 4 + (i * 8)
        int platform = fntU16(rec)
        int encoding = fntU16(rec + 2)
        int sub = FNT_CMAP + fntU32(rec + 4)
        bool unicode = platform == 0 || (platform == 3 && (encoding == 1 || encoding == 10))
        if unicode && fntHas(sub, 2) && fntU16(sub) == format {
            FNT_CMAP_SUB = sub
            FNT_CMAP_FMT = format
            return true
        }
        i = i + 1
    }
    return false
}

// Format 4: segments of 16-bit code points, searched by their end
// codes, which are sorted. A glyph found through idRangeOffset of 0
// is still offset by idDelta, modulo 65536, and one that reads 0 from
// the glyph array is missing, whatever idDelta says.
int func fntCmap4(cp:int) {
    if cp > 65535 { return 0 }
    int sub = FNT_CMAP_SUB
    int segX2 = fntU16(sub + 6)
    int segs = Math.floorDiv(segX2, 2)
    int lo = 0
    int hi = segs - 1
    while lo < hi {
        int mid = Math.floorDiv(lo + hi, 2)
        if fntU16(sub + 14 + (mid * 2)) < cp { lo = mid + 1 } else { hi = mid }
    }
    int endCode = fntU16(sub + 14 + (lo * 2))
    int startAt = sub + 16 + segX2 + (lo * 2)
    int start = fntU16(startAt)
    if cp > endCode || cp < start { return 0 }
    int delta = fntU16(startAt + segX2)
    int roAt = startAt + (segX2 * 2)
    int ro = fntU16(roAt)
    if ro == 0 { return (cp + delta) & 65535 }
    int g = fntU16(roAt + ro + ((cp - start) * 2))
    if g == 0 { return 0 }
    return (g + delta) & 65535
}

// Format 12: sorted groups of (first, last, first glyph).
int func fntCmap12(cp:int) {
    int sub = FNT_CMAP_SUB
    int groups = fntU32(sub + 12)
    int lo = 0
    int hi = groups - 1
    while lo <= hi {
        int mid = Math.floorDiv(lo + hi, 2)
        int at = sub + 16 + (mid * 12)
        int first = fntU32(at)
        int last = fntU32(at + 4)
        if cp < first {
            hi = mid - 1
        } else if cp > last {
            lo = mid + 1
        } else {
            return fntU32(at + 8) + (cp - first)
        }
    }
    return 0
}

// The glyph for a code point, or 0 -- glyph 0 is .notdef, which is what
// a font draws for a character it does not have. A cmap pointing past
// the last glyph answers 0 too, as FreeType's FT_Get_Char_Index does.
int func fntGlyphIndex(cp:int) {
    if cp < 0 { return 0 }
    int g = 0
    if FNT_CMAP_FMT == 12 { g = fntCmap12(cp) } else { g = fntCmap4(cp) }
    if g >= FNT_NGLYPHS { return 0 }
    return g
}

// ---- metrics ----

// Advance width in font units. Glyphs past numberOfHMetrics share the
// last advance -- the table stores it once for a run of monospaced
// glyphs at the end.
int func fntAdvance(g:int) {
    if g < FNT_NHMETRICS { return fntU16(FNT_HMTX + (g * 4)) }
    return fntU16(FNT_HMTX + ((FNT_NHMETRICS - 1) * 4))
}

int func fntLsb(g:int) {
    if g < FNT_NHMETRICS { return fntS16(FNT_HMTX + (g * 4) + 2) }
    return fntS16(FNT_HMTX + (FNT_NHMETRICS * 4) + ((g - FNT_NHMETRICS) * 2))
}

// ---- glyphs ----

// Where a glyph's data starts inside the file, with its length through
// FNT_GLYPH_LEN. A length of 0 is a glyph with no outline -- a space.
int FNT_GLYPH_LEN = 0

int func fntGlyphData(g:int) {
    int a = 0
    int b = 0
    if FNT_LOCA_LONG {
        a = fntU32(FNT_LOCA + (g * 4))
        b = fntU32(FNT_LOCA + (g * 4) + 4)
    } else {
        a = fntU16(FNT_LOCA + (g * 2)) * 2
        b = fntU16(FNT_LOCA + (g * 2) + 2) * 2
    }
    FNT_GLYPH_LEN = b - a
    if b < a || b > FNT_GLYF_LEN { FNT_ERR = FNT_E_GLYPH FNT_GLYPH_LEN = 0 }
    return FNT_GLYF + a
}

// A glyph's outline in font units, appended to the four arrays: one x,
// y and on-curve flag per point, and for each contour the index of its
// last point -- TrueType's own representation, so that it can be
// compared point for point with FreeType's. Returns the number of
// contours, or -1 with FNT_ERR set.
//
// Points are the file's own, off-curve ones included; the implied
// on-curve point between two off-curve ones is the renderer's business,
// not the parser's.
//
// With one adjustment, the one FreeType makes: a glyph is placed so
// that its left edge sits its left side bearing (from hmtx) to the
// right of the origin. The glyph header's xMin normally says the same
// thing, and where the two disagree the outline moves by lsb - xMin.
// In DejaVu Sans they disagree for exactly six glyphs, by one unit
// each, and those were the only simple glyphs that differed from
// FreeType before this.
int func fntGlyph(g:int, xs:arr[int], ys:arr[int], onCurve:arr[int], ends:arr[int]) {
    FNT_ERR = 0
    if g < 0 || g >= FNT_NGLYPHS { FNT_ERR = FNT_E_GLYPH return -1 }
    int at = fntGlyphData(g)
    if FNT_ERR != 0 { return -1 }
    if FNT_GLYPH_LEN == 0 { return 0 }
    int contours = fntS16(at)
    if contours < 0 { FNT_ERR = FNT_E_COMPOSITE return -1 }
    int base = xs.length
    int n = fntSimpleGlyph(at, contours, xs, ys, onCurve, ends)
    if n < 0 { return n }
    int shift = fntLsb(g) - fntS16(at + 2)
    if shift != 0 {
        int i = base
        while i < xs.length {
            xs[i] = xs[i] + shift
            i = i + 1
        }
    }
    return n
}

int func fntSimpleGlyph(at:int, contours:int, xs:arr[int], ys:arr[int], onCurve:arr[int],
                        ends:arr[int]) {
    int base = xs.length
    int p = at + 10
    int npts = 0
    int c = 0
    int last = -1
    while c < contours {
        int e = fntU16(p + (c * 2))
        // End points only ever increase; a file where they do not is
        // corrupt, and trusting it would index outside the point list.
        if e <= last && c > 0 { FNT_ERR = FNT_E_GLYPH return -1 }
        last = e
        ends.push(base + e)
        c = c + 1
    }
    if contours > 0 { npts = last + 1 }
    p = p + (contours * 2)
    int instructions = fntU16(p)
    p = p + 2 + instructions

    // Flags, with the repeat bit (8): the next byte says how many more
    // times this flag applies.
    arr[int] flags = []
    while flags.length < npts {
        int f = fntU8(p)
        p = p + 1
        flags.push(f)
        if (f & 8) != 0 {
            int r = fntU8(p)
            p = p + 1
            int k = 0
            while k < r && flags.length < npts {
                flags.push(f)
                k = k + 1
            }
        }
        if FNT_ERR != 0 { return -1 }
    }

    // Coordinates are deltas. A short one (bit 1 for x, 2 for y) is a
    // byte whose sign is the "same" bit (4 / 5); a long one is a signed
    // 16-bit value unless the same bit says it repeats the previous.
    int x = 0
    int i = 0
    while i < npts {
        int f = flags[i]
        if (f & 2) != 0 {
            int d = fntU8(p)
            p = p + 1
            if (f & 16) != 0 { x = x + d } else { x = x - d }
        } else if (f & 16) == 0 {
            x = x + fntS16(p)
            p = p + 2
        }
        xs.push(x)
        onCurve.push(f & 1)
        i = i + 1
    }
    int y = 0
    i = 0
    while i < npts {
        int f = flags[i]
        if (f & 4) != 0 {
            int d = fntU8(p)
            p = p + 1
            if (f & 32) != 0 { y = y + d } else { y = y - d }
        } else if (f & 32) == 0 {
            y = y + fntS16(p)
            p = p + 2
        }
        ys.push(y)
        i = i + 1
    }
    if FNT_ERR != 0 { return -1 }
    return contours
}
