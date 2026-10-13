/* runtime.md phase 7, slice 8: which font file a family name means.
 *
 * Cairo's toy API asked fontconfig (Linux), CoreText (macOS) or GDI
 * (Windows) for "the file for this family"; this answers it from the font
 * files themselves, with no platform service. It is plain C because it is
 * plumbing -- walking directories, reading byte ranges -- that Festina has
 * no way to do; everything about the glyphs stays in font.f and text.f.
 *
 * Nothing is read until a program asks for a family other than the bundled
 * sans-serif. Then the platform's font directories are walked once per
 * process, and for each .ttf / .ttc face only the table directory and the
 * `name`, `OS/2` and `head` tables are read: a few kilobytes, not the
 * file. FESTINA_FONT_DIRS (a list separated by ':', or ';' on Windows)
 * replaces the directories -- the test hook, and a way to point a program
 * at its own fonts.
 *
 * What is read and what is not, decided in runtime.md (phase 7, slice 8):
 * TrueType outlines and TrueType collections. A CFF font (.otf, or an
 * 'OTTO' face) is not listed, so a family that exists only as CFF is "not
 * found". A family is its typographic name (name ID 16, else ID 1),
 * compared case-insensitively over ASCII.
 *
 * The header is self-contained (every function is static) so that a test
 * can include it alone and compare what it reads with FreeType's. */
#ifndef FESTINA_FONTSCAN_H
#define FESTINA_FONTSCAN_H

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdatomic.h>

#ifdef _WIN32
#include <windows.h>
#else
#include <dirent.h>
#include <sys/stat.h>
#endif

typedef struct {
    char *family;      /* ASCII-lowercased UTF-8 */
    char *path;        /* UTF-8 */
    int face;          /* index in a collection, 0 for a plain font */
    int weight;        /* 100..900 */
    int italic;        /* italic or oblique */
} FestinaFontFace;

#define FESTINA_FONT_MAX_FILE (16 * 1024 * 1024)   /* a face bigger than this is "not found" */
#define FESTINA_FONT_MAX_FACES 20000
#define FESTINA_FONT_MAX_DEPTH 8

static FestinaFontFace *g_fontscan_faces = NULL;
static size_t g_fontscan_count = 0, g_fontscan_cap = 0;
static atomic_flag g_fontscan_lock = ATOMIC_FLAG_INIT;
static int g_fontscan_done = 0;

/* ---- small readers ---- */

static uint32_t festina_fs_u32(const unsigned char *p) {
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) | ((uint32_t)p[2] << 8) | p[3];
}

static uint32_t festina_fs_u16(const unsigned char *p) {
    return ((uint32_t)p[0] << 8) | p[1];
}

static FILE *festina_fs_open(const char *path) {
#ifdef _WIN32
    wchar_t wide[1024];
    if (!MultiByteToWideChar(CP_UTF8, 0, path, -1, wide, 1024)) return NULL;
    return _wfopen(wide, L"rb");
#else
    return fopen(path, "rb");
#endif
}

/* n bytes at `off` into buf; 1 on success. */
static int festina_fs_read(FILE *f, long off, void *buf, size_t n) {
    if (off < 0 || fseek(f, off, SEEK_SET) != 0) return 0;
    return fread(buf, 1, n, f) == n;
}

/* ---- the name table ---- */

/* Append the code point as UTF-8; returns the new length (bounded). */
static size_t festina_fs_put_utf8(char *out, size_t len, size_t cap, uint32_t cp) {
    if (cp < 0x80) { if (len + 1 < cap) out[len++] = (char)cp; }
    else if (cp < 0x800) {
        if (len + 2 < cap) { out[len++] = (char)(0xC0 | (cp >> 6)); out[len++] = (char)(0x80 | (cp & 0x3F)); }
    } else if (cp < 0x10000) {
        if (len + 3 < cap) {
            out[len++] = (char)(0xE0 | (cp >> 12)); out[len++] = (char)(0x80 | ((cp >> 6) & 0x3F));
            out[len++] = (char)(0x80 | (cp & 0x3F));
        }
    } else {
        if (len + 4 < cap) {
            out[len++] = (char)(0xF0 | (cp >> 18)); out[len++] = (char)(0x80 | ((cp >> 12) & 0x3F));
            out[len++] = (char)(0x80 | ((cp >> 6) & 0x3F)); out[len++] = (char)(0x80 | (cp & 0x3F));
        }
    }
    return len;
}

/* The best string for name ID `id` into out (NUL-terminated, ASCII-
 * lowercased), or 0 when there is none. Windows Unicode names are
 * preferred (English first), then the Unicode platform, then Macintosh
 * Roman read as Latin-1 -- exact for ASCII, which is what family names
 * are in practice. */
static int festina_fs_name(const unsigned char *name, size_t nlen, int id, char *out, size_t cap) {
    if (nlen < 6) return 0;
    unsigned count = festina_fs_u16(name + 2), strings = festina_fs_u16(name + 4);
    int best = -1, best_rank = 1 << 30;
    for (unsigned i = 0; i < count; i++) {
        size_t rec = 6 + (size_t)i * 12;
        if (rec + 12 > nlen) break;
        unsigned plat = festina_fs_u16(name + rec), enc = festina_fs_u16(name + rec + 2);
        unsigned lang = festina_fs_u16(name + rec + 4), nid = festina_fs_u16(name + rec + 6);
        if ((int)nid != id) continue;
        int rank;
        if (plat == 3 && (enc == 1 || enc == 10)) rank = lang == 0x409 ? 0 : 1;
        else if (plat == 0) rank = 2;
        else if (plat == 1 && enc == 0) rank = 3;
        else continue;
        if (rank < best_rank) { best_rank = rank; best = (int)i; }
    }
    if (best < 0) return 0;
    size_t rec = 6 + (size_t)best * 12;
    unsigned plat = festina_fs_u16(name + rec);
    size_t len = festina_fs_u16(name + rec + 8), off = (size_t)strings + festina_fs_u16(name + rec + 10);
    if (off + len > nlen) return 0;
    const unsigned char *s = name + off;
    size_t o = 0;
    if (plat == 3 || plat == 0) {
        for (size_t i = 0; i + 1 < len; i += 2) {
            uint32_t cp = festina_fs_u16(s + i);
            if (cp >= 0xD800 && cp < 0xDC00 && i + 3 < len) {
                uint32_t lo = festina_fs_u16(s + i + 2);
                if (lo >= 0xDC00 && lo < 0xE000) { cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00); i += 2; }
            }
            o = festina_fs_put_utf8(out, o, cap, cp);
        }
    } else {
        for (size_t i = 0; i < len; i++) o = festina_fs_put_utf8(out, o, cap, s[i]);
    }
    out[o] = 0;
    for (size_t i = 0; i < o; i++) if (out[i] >= 'A' && out[i] <= 'Z') out[i] = (char)(out[i] + 32);
    /* trim */
    size_t a = 0;
    while (out[a] == ' ') a++;
    if (a) memmove(out, out + a, o - a + 1);
    o = strlen(out);
    while (o && out[o - 1] == ' ') out[--o] = 0;
    return o > 0;
}

/* ---- one face ---- */

static void festina_fs_add(const char *family, const char *path, int face, int weight, int italic) {
    if (g_fontscan_count >= FESTINA_FONT_MAX_FACES) return;
    if (g_fontscan_count == g_fontscan_cap) {
        size_t cap = g_fontscan_cap ? g_fontscan_cap * 2 : 64;
        FestinaFontFace *grown = realloc(g_fontscan_faces, cap * sizeof(*grown));
        if (!grown) return;
        g_fontscan_faces = grown;
        g_fontscan_cap = cap;
    }
    char *f = strdup(family), *p = strdup(path);
    if (!f || !p) { free(f); free(p); return; }
    FestinaFontFace *e = &g_fontscan_faces[g_fontscan_count++];
    e->family = f; e->path = p; e->face = face; e->weight = weight; e->italic = italic;
}

/* Read the face whose table directory is at `base`; 1 if it was listed. */
static int festina_fs_face(FILE *f, long base, const char *path, int face) {
    unsigned char hdr[12];
    if (!festina_fs_read(f, base, hdr, 12)) return 0;
    uint32_t version = festina_fs_u32(hdr);
    if (version != 0x00010000u && version != 0x74727565u) return 0;      /* 1.0, 'true'; not 'OTTO' */
    unsigned n = festina_fs_u16(hdr + 4);
    if (n == 0 || n > 128) return 0;
    unsigned char dir[128 * 16];
    if (!festina_fs_read(f, base + 12, dir, (size_t)n * 16)) return 0;
    long name_off = -1, os2_off = -1, head_off = -1;
    size_t name_len = 0, os2_len = 0, head_len = 0;
    int glyf = 0, loca = 0;
    for (unsigned i = 0; i < n; i++) {
        const unsigned char *r = dir + i * 16;
        uint32_t tag = festina_fs_u32(r), off = festina_fs_u32(r + 8), len = festina_fs_u32(r + 12);
        if (tag == 0x6E616D65u) { name_off = off; name_len = len; }           /* 'name' */
        else if (tag == 0x4F532F32u) { os2_off = off; os2_len = len; }        /* 'OS/2' */
        else if (tag == 0x68656164u) { head_off = off; head_len = len; }      /* 'head' */
        else if (tag == 0x676C7966u) glyf = 1;                                /* 'glyf' */
        else if (tag == 0x6C6F6361u) loca = 1;                                /* 'loca' */
    }
    if (!glyf || !loca || name_off < 0 || name_len < 6 || name_len > 262144) return 0;
    unsigned char *name = malloc(name_len);
    if (!name) return 0;
    char family[256];
    int ok = festina_fs_read(f, name_off, name, name_len)
             && (festina_fs_name(name, name_len, 16, family, sizeof(family))
                 || festina_fs_name(name, name_len, 1, family, sizeof(family)));
    free(name);
    if (!ok) return 0;

    int mac_bold = 0, mac_italic = 0;
    if (head_off >= 0 && head_len >= 46) {
        unsigned char h[2];
        if (festina_fs_read(f, head_off + 44, h, 2)) {
            unsigned ms = festina_fs_u16(h);
            mac_bold = ms & 1;
            mac_italic = (ms >> 1) & 1;
        }
    }
    int weight = mac_bold ? 700 : 400, italic = mac_italic;
    if (os2_off >= 0 && os2_len >= 6) {
        unsigned char o[64];
        size_t want = os2_len < 64 ? os2_len : 64;
        if (festina_fs_read(f, os2_off, o, want)) {
            unsigned w = festina_fs_u16(o + 4);
            if (w >= 1 && w <= 9) w *= 100;                                    /* the 1-9 scale some old fonts use */
            if (w >= 100 && w <= 900) weight = (int)w;
            if (want >= 64) {
                unsigned sel = festina_fs_u16(o + 62);
                italic = (sel & 1) || ((sel >> 9) & 1);
            }
        }
    }
    festina_fs_add(family, path, face, weight, italic);
    return 1;
}

static void festina_fs_file(const char *path) {
    FILE *f = festina_fs_open(path);
    if (!f) return;
    unsigned char hdr[12];
    if (festina_fs_read(f, 0, hdr, 12)) {
        if (festina_fs_u32(hdr) == 0x74746366u) {                              /* 'ttcf' */
            uint32_t ver = festina_fs_u32(hdr + 4), n = festina_fs_u32(hdr + 8);
            if ((ver == 0x00010000u || ver == 0x00020000u) && n >= 1 && n <= 64) {
                unsigned char offs[64 * 4];
                if (festina_fs_read(f, 12, offs, (size_t)n * 4)) {
                    for (uint32_t i = 0; i < n; i++)
                        festina_fs_face(f, (long)festina_fs_u32(offs + i * 4), path, (int)i);
                }
            }
        } else {
            festina_fs_face(f, 0, path, 0);
        }
    }
    fclose(f);
}

/* ---- walking directories ---- */

static int festina_fs_has_ext(const char *name, const char *ext) {
    size_t n = strlen(name), e = strlen(ext);
    if (n <= e) return 0;
    for (size_t i = 0; i < e; i++) {
        char c = name[n - e + i];
        if (c >= 'A' && c <= 'Z') c = (char)(c + 32);
        if (c != ext[i]) return 0;
    }
    return 1;
}

static void festina_fs_walk(const char *dir, int depth) {
    char **names = NULL;
    unsigned char *isdir = NULL;
    size_t n = 0, cap = 0;
#ifdef _WIN32
    wchar_t wpat[1100];
    wchar_t wdir[1024];
    if (!MultiByteToWideChar(CP_UTF8, 0, dir, -1, wdir, 1024)) return;
    _snwprintf(wpat, 1100, L"%ls\\*", wdir);
    WIN32_FIND_DATAW fd;
    HANDLE h = FindFirstFileW(wpat, &fd);
    if (h == INVALID_HANDLE_VALUE) return;
    do {
        if (!wcscmp(fd.cFileName, L".") || !wcscmp(fd.cFileName, L"..")) continue;
        char utf8[1024];
        if (!WideCharToMultiByte(CP_UTF8, 0, fd.cFileName, -1, utf8, sizeof(utf8), NULL, NULL)) continue;
        if (n == cap) {
            cap = cap ? cap * 2 : 64;
            char **nn = realloc(names, cap * sizeof(*nn));
            unsigned char *nd = realloc(isdir, cap);
            if (!nn || !nd) { names = nn ? nn : names; isdir = nd ? nd : isdir; break; }
            names = nn; isdir = nd;
        }
        names[n] = strdup(utf8);
        isdir[n] = (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) ? 1 : 0;
        if (names[n]) n++;
    } while (FindNextFileW(h, &fd));
    FindClose(h);
#else
    DIR *d = opendir(dir);
    if (!d) return;
    struct dirent *ent;
    while ((ent = readdir(d)) != NULL) {
        if (!strcmp(ent->d_name, ".") || !strcmp(ent->d_name, "..")) continue;
        if (n == cap) {
            cap = cap ? cap * 2 : 64;
            char **nn = realloc(names, cap * sizeof(*nn));
            unsigned char *nd = realloc(isdir, cap);
            if (!nn || !nd) { names = nn ? nn : names; isdir = nd ? nd : isdir; break; }
            names = nn; isdir = nd;
        }
        names[n] = strdup(ent->d_name);
        isdir[n] = 0;
        if (names[n]) n++;
    }
    closedir(d);
#endif
    /* Sorted by name, so the same machine always lists the same faces in
     * the same order -- which is what decides between two faces that tie. */
    if (n > 1) {
        /* keep isdir parallel: sort an index by name */
        size_t *idx = malloc(n * sizeof(*idx));
        if (idx) {
            for (size_t i = 0; i < n; i++) idx[i] = i;
            /* insertion sort: directories hold tens to hundreds of names */
            for (size_t i = 1; i < n; i++) {
                size_t v = idx[i];
                size_t j = i;
                while (j > 0 && strcmp(names[idx[j - 1]], names[v]) > 0) { idx[j] = idx[j - 1]; j--; }
                idx[j] = v;
            }
            char **sorted = malloc(n * sizeof(*sorted));
            unsigned char *sd = malloc(n);
            if (sorted && sd) {
                for (size_t i = 0; i < n; i++) { sorted[i] = names[idx[i]]; sd[i] = isdir[idx[i]]; }
                memcpy(names, sorted, n * sizeof(*sorted));
                memcpy(isdir, sd, n);
            }
            free(sorted); free(sd); free(idx);
        }
    }
    for (size_t i = 0; i < n; i++) {
        size_t need = strlen(dir) + strlen(names[i]) + 2;
        char *path = malloc(need);
        if (path) {
#ifdef _WIN32
            snprintf(path, need, "%s\\%s", dir, names[i]);
#else
            snprintf(path, need, "%s/%s", dir, names[i]);
            struct stat st;
            if (stat(path, &st) == 0) isdir[i] = S_ISDIR(st.st_mode) ? 1 : 0;
#endif
            if (isdir[i]) {
                if (depth < FESTINA_FONT_MAX_DEPTH) festina_fs_walk(path, depth + 1);
            } else if (festina_fs_has_ext(names[i], ".ttf") || festina_fs_has_ext(names[i], ".ttc")) {
                festina_fs_file(path);
            }
            free(path);
        }
        free(names[i]);
    }
    free(names);
    free(isdir);
}

static void festina_fs_scan_dirs(void) {
    const char *over = getenv("FESTINA_FONT_DIRS");
#ifdef _WIN32
    const char sep = ';';
#else
    const char sep = ':';
#endif
    if (over) {
        const char *p = over;
        while (*p) {
            const char *e = strchr(p, sep);
            size_t len = e ? (size_t)(e - p) : strlen(p);
            if (len > 0 && len < 1000) {
                char dir[1024];
                memcpy(dir, p, len);
                dir[len] = 0;
                festina_fs_walk(dir, 0);
            }
            p += len;
            if (*p == sep) p++;
        }
        return;
    }
    char buf[1100];
#ifdef _WIN32
    const char *win = getenv("WINDIR");
    if (!win) win = getenv("SystemRoot");
    snprintf(buf, sizeof(buf), "%s\\Fonts", win ? win : "C:\\Windows");
    festina_fs_walk(buf, 0);
    const char *local = getenv("LOCALAPPDATA");
    if (local) {
        snprintf(buf, sizeof(buf), "%s\\Microsoft\\Windows\\Fonts", local);
        festina_fs_walk(buf, 0);
    }
#else
    const char *home = getenv("HOME");
#ifdef __APPLE__
    festina_fs_walk("/System/Library/Fonts", 0);
    festina_fs_walk("/Library/Fonts", 0);
    if (home) { snprintf(buf, sizeof(buf), "%s/Library/Fonts", home); festina_fs_walk(buf, 0); }
#else
    festina_fs_walk("/usr/share/fonts", 0);
    festina_fs_walk("/usr/local/share/fonts", 0);
    if (home) {
        snprintf(buf, sizeof(buf), "%s/.fonts", home);
        festina_fs_walk(buf, 0);
        snprintf(buf, sizeof(buf), "%s/.local/share/fonts", home);
        festina_fs_walk(buf, 0);
    }
#endif
#endif
}

/* Walk the font directories, once per process. */
static void festina_fontscan_ensure(void) {
    if (g_fontscan_done) return;
    while (atomic_flag_test_and_set_explicit(&g_fontscan_lock, memory_order_acquire)) { /* spin */ }
    if (!g_fontscan_done) {
        festina_fs_scan_dirs();
        g_fontscan_done = 1;
    }
    atomic_flag_clear_explicit(&g_fontscan_lock, memory_order_release);
}

/* ---- choosing ---- */

typedef struct {
    int bundled;                   /* 1: the bundled DejaVu Sans, nothing found */
    const FestinaFontFace *face;   /* otherwise the face to open */
    int synth_bold, synth_italic;  /* what the chosen face lacks of what was asked */
} FestinaFontChoice;

static const char *const g_font_serif[] = {
    "times new roman", "liberation serif", "dejavu serif", "noto serif", "times", NULL };
static const char *const g_font_mono[] = {
    "courier new", "menlo", "consolas", "liberation mono", "dejavu sans mono", "noto sans mono",
    "courier", NULL };

/* The face of `family` (already lowercased) nearest the request, or NULL.
 * Italic-ness matters first, then distance from the wanted weight; a tie
 * keeps the first face found, which is the same on every run. */
static const FestinaFontFace *festina_font_nearest(const char *family, int bold, int italic) {
    const FestinaFontFace *best = NULL;
    long best_score = 0;
    int target = bold ? 700 : 400;
    for (size_t i = 0; i < g_fontscan_count; i++) {
        const FestinaFontFace *f = &g_fontscan_faces[i];
        if (strcmp(f->family, family) != 0) continue;
        long dw = f->weight - target;
        long score = (dw < 0 ? -dw : dw) + (f->italic != italic ? 1000 : 0);
        if (!best || score < best_score) { best = f; best_score = score; }
    }
    return best;
}

static void festina_font_finish(FestinaFontChoice *c, const FestinaFontFace *f, int bold, int italic) {
    c->bundled = 0;
    c->face = f;
    c->synth_bold = bold && f->weight < 600;
    c->synth_italic = italic && !f->italic;
}

/* `list` is the family string as the program wrote it: names separated by
 * commas, each optionally quoted, tried in turn. 'sans-serif' (and an
 * empty list) is the bundled face; 'serif' and 'monospace' try fixed
 * lists; a name nothing matches moves on to the next; when none is found
 * the bundled face is drawn, with whatever bold or italic was asked
 * synthesized from it. */
static FestinaFontChoice festina_font_resolve(const char *list, int bold, int italic) {
    FestinaFontChoice c;
    c.bundled = 1; c.face = NULL; c.synth_bold = bold; c.synth_italic = italic;
    if (!list) return c;
    const char *p = list;
    while (*p) {
        const char *e = strchr(p, ',');
        size_t len = e ? (size_t)(e - p) : strlen(p);
        char tok[256];
        size_t a = 0, b = len;
        while (a < b && (p[a] == ' ' || p[a] == '\t' || p[a] == '"' || p[a] == '\'')) a++;
        while (b > a && (p[b - 1] == ' ' || p[b - 1] == '\t' || p[b - 1] == '"' || p[b - 1] == '\'')) b--;
        if (b - a > 0 && b - a < sizeof(tok)) {
            for (size_t i = a; i < b; i++) {
                char ch = p[i];
                tok[i - a] = (ch >= 'A' && ch <= 'Z') ? (char)(ch + 32) : ch;
            }
            tok[b - a] = 0;
            if (!strcmp(tok, "sans-serif")) return c;
            const char *const *generic = NULL;
            if (!strcmp(tok, "serif")) generic = g_font_serif;
            else if (!strcmp(tok, "monospace")) generic = g_font_mono;
            festina_fontscan_ensure();
            if (generic) {
                for (int i = 0; generic[i]; i++) {
                    const FestinaFontFace *f = festina_font_nearest(generic[i], bold, italic);
                    if (f) { festina_font_finish(&c, f, bold, italic); return c; }
                }
            } else {
                const FestinaFontFace *f = festina_font_nearest(tok, bold, italic);
                if (f) { festina_font_finish(&c, f, bold, italic); return c; }
            }
        }
        p += len;
        if (*p == ',') p++;
    }
    return c;
}

/* The whole file of a face, malloc'd, or NULL (unreadable, empty, or over
 * the limit). */
static unsigned char *festina_font_read_file(const char *path, size_t *len) {
    FILE *f = festina_fs_open(path);
    if (!f) return NULL;
    unsigned char *buf = NULL;
    if (fseek(f, 0, SEEK_END) == 0) {
        long size = ftell(f);
        if (size > 0 && size <= FESTINA_FONT_MAX_FILE && fseek(f, 0, SEEK_SET) == 0) {
            buf = malloc((size_t)size);
            if (buf && fread(buf, 1, (size_t)size, f) == (size_t)size) *len = (size_t)size;
            else { free(buf); buf = NULL; }
        }
    }
    fclose(f);
    return buf;
}

#endif /* FESTINA_FONTSCAN_H */
