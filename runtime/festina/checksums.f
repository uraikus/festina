// runtime.md phase 1: the two checksums a PNG carries.
//
// Written in Festina rather than taken from Cairo/zlib because they are
// pure arithmetic over bytes -- no OS, no allocation beyond an array,
// nothing a decoder cannot express. They are here before the decoder
// that needs them because phase 0 needs a REAL component to prove the
// injection mechanism against; a fixture would have proved less.
//
// Both take arr[int] rather than blob: a decoder builds its output in
// an array (blob has no binary writer -- see todo.md), so the array is
// the shape the rest of the pipeline speaks.

// zlib's Adler-32, over the raw bytes. The modulus is the largest
// prime below 65536, which is the whole reason the algorithm works.
int func adler32(bytes:arr[int]) {
    int a = 1
    int b = 0
    int i = 0
    while i < bytes.length {
        a = (a + bytes[i]) % 65521
        b = (b + a) % 65521
        i++
    }
    return (b * 65536) + a
}

// PNG's CRC-32 (IEEE 802.3), computed a bit at a time rather than from
// a 256-entry table. A table would be faster and is what zlib ships;
// this is the version whose correctness can be read off the
// specification, and a PNG has one CRC per chunk rather than one per
// byte, so the table is not where a decoder's time goes.
int func crc32(bytes:arr[int]) {
    int crc = 4294967295
    int i = 0
    while i < bytes.length {
        crc = crc ^ bytes[i]
        int bit = 0
        while bit < 8 {
            if (crc & 1) == 1 {
                // 0xEDB88320 -- the reversed IEEE polynomial, which is
                // the form that goes with shifting right.
                crc = (crc >> 1) ^ 3988292384
            } else {
                crc = crc >> 1
            }
            bit++
        }
        i++
    }
    return crc ^ 4294967295
}
