/* Regenerates tests/fixtures/jpeg/*.jpg and their *.rgb references.
 *
 *   cc make_fixtures.c -o make_fixtures -ljpeg && ./make_fixtures
 *
 * Each case is a synthetic picture (a flat field, a gradient, a disc, a
 * noisy patch, hard edges -- so blocks run from empty to dense and the
 * progressive end-of-band runs get exercised) compressed by libjpeg with
 * one particular set of options. The .rgb beside it is what libjpeg itself
 * decodes that file to, with the settings the runtime's C loader uses
 * (default DCT, fancy upsampling, RGB output): the independent reference
 * tests/test_jpeg_full.py holds jpeg.f to. Committed rather than made at
 * test time so the tests need no libjpeg headers. */
#include <setjmp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <jpeglib.h>

static void synth(unsigned char *p, int w, int h) {
    unsigned s = 12345;
    for (int y = 0; y < h; y++) for (int x = 0; x < w; x++) {
        unsigned char *q = p + (y * w + x) * 3;
        int r, g, b;
        if (x < w / 3) { r = 200; g = 180; b = 60; }                          /* flat */
        else if (x < 2 * w / 3) { r = (x * 255) / w; g = (y * 255) / h; b = ((x + y) * 255) / (w + h); } /* smooth */
        else { s = s * 1103515245u + 12345u; r = (s >> 16) & 255; g = (s >> 8) & 255; b = (s >> 20) & 255; } /* noise */
        int dx = x - w / 2, dy = y - h / 2;
        if (dx * dx + dy * dy < (h * h) / 16) { r = 20; g = 40; b = 220; }    /* disc, hard edge */
        if ((x / 4 + y / 4) % 7 == 0 && y > h / 2) { r = 255 - r; g = 255 - g; b = 255 - b; }
        q[0] = r; q[1] = g; q[2] = b;
    }
}

struct cfg { const char *name; int w, h; int gray; int hs, vs; int quality; int progressive; int restart; int rgb; int scans3; };

static void write_file(const char *path, const void *d, size_t n) {
    FILE *f = fopen(path, "wb"); fwrite(d, 1, n, f); fclose(f);
}

static void make(const struct cfg *c) {
    unsigned char *img = malloc(c->w * c->h * 3);
    synth(img, c->w, c->h);
    struct jpeg_compress_struct ci; struct jpeg_error_mgr er;
    ci.err = jpeg_std_error(&er); jpeg_create_compress(&ci);
    unsigned char *out = NULL; unsigned long n = 0;
    jpeg_mem_dest(&ci, &out, &n);
    ci.image_width = c->w; ci.image_height = c->h;
    unsigned char *grey = NULL;
    if (c->gray) {
        ci.input_components = 1; ci.in_color_space = JCS_GRAYSCALE;
        grey = malloc(c->w * c->h);
        for (int i = 0; i < c->w * c->h; i++) grey[i] = (img[i*3] * 77 + img[i*3+1] * 150 + img[i*3+2] * 29) >> 8;
    } else { ci.input_components = 3; ci.in_color_space = JCS_RGB; }
    jpeg_set_defaults(&ci);
    if (c->rgb) jpeg_set_colorspace(&ci, JCS_RGB);
    jpeg_set_quality(&ci, c->quality, TRUE);
    if (!c->gray) { ci.comp_info[0].h_samp_factor = c->hs; ci.comp_info[0].v_samp_factor = c->vs;
                    for (int k = 1; k < 3; k++) { ci.comp_info[k].h_samp_factor = 1; ci.comp_info[k].v_samp_factor = 1; } }
    if (c->progressive) jpeg_simple_progression(&ci);
    if (c->scans3) {       /* baseline, one scan per component */
        static jpeg_scan_info sc[3];
        for (int k = 0; k < 3; k++) { sc[k].comps_in_scan = 1; sc[k].component_index[0] = k; sc[k].Ss = 0; sc[k].Se = 63; sc[k].Ah = 0; sc[k].Al = 0; }
        ci.scan_info = sc; ci.num_scans = 3; ci.optimize_coding = TRUE;
    }
    if (c->restart) ci.restart_interval = c->restart;
    jpeg_start_compress(&ci, TRUE);
    while (ci.next_scanline < ci.image_height) {
        JSAMPROW row = c->gray ? grey + ci.next_scanline * c->w : img + ci.next_scanline * c->w * 3;
        jpeg_write_scanlines(&ci, &row, 1);
    }
    jpeg_finish_compress(&ci); jpeg_destroy_compress(&ci);

    char path[256]; snprintf(path, sizeof path, "%s.jpg", c->name); write_file(path, out, n);

    struct jpeg_decompress_struct di; struct jpeg_error_mgr de;
    di.err = jpeg_std_error(&de); jpeg_create_decompress(&di);
    jpeg_mem_src(&di, out, n); jpeg_read_header(&di, TRUE);
    di.out_color_space = JCS_RGB; jpeg_start_decompress(&di);
    unsigned char *ref = malloc(di.output_width * di.output_height * 3);
    while (di.output_scanline < di.output_height) {
        JSAMPROW row = ref + di.output_scanline * di.output_width * 3;
        jpeg_read_scanlines(&di, &row, 1);
    }
    snprintf(path, sizeof path, "%s.rgb", c->name);
    write_file(path, ref, di.output_width * di.output_height * 3);
    printf("%-22s %3dx%-3d %5lu bytes\n", c->name, c->w, c->h, n);
    jpeg_finish_decompress(&di); jpeg_destroy_decompress(&di);
    free(ref); free(img); free(grey);
}

int main(void) {
    const struct cfg cases[] = {
        /* name                w   h  gray hs vs  q  prog rst rgb scans3 */
        {"base_444",          33, 21, 0, 1, 1, 90, 0, 0, 0, 0},
        {"base_420",          37, 23, 0, 2, 2, 85, 0, 0, 0, 0},
        {"base_422",          40, 17, 0, 2, 1, 80, 0, 0, 0, 0},
        {"base_440",          17, 40, 0, 1, 2, 80, 0, 0, 0, 0},
        {"base_gray",         19, 13, 1, 1, 1, 90, 0, 0, 0, 0},
        {"base_420_restart",  64, 48, 0, 2, 2, 75, 0, 3, 0, 0},
        {"base_three_scans",  41, 29, 0, 2, 2, 85, 0, 0, 0, 1},
        {"base_rgb",          25, 19, 0, 1, 1, 90, 0, 0, 1, 0},
        {"prog_444",          33, 21, 0, 1, 1, 90, 1, 0, 0, 0},
        {"prog_420",          37, 23, 0, 2, 2, 85, 1, 0, 0, 0},
        {"prog_422",          40, 17, 0, 2, 1, 80, 1, 0, 0, 0},
        {"prog_440",          17, 40, 0, 1, 2, 80, 1, 0, 0, 0},
        {"prog_gray",         19, 13, 1, 1, 1, 90, 1, 0, 0, 0},
        {"prog_420_restart",  64, 48, 0, 2, 2, 75, 1, 3, 0, 0},
        {"prog_big",         129, 97, 0, 2, 2, 60, 1, 0, 0, 0},
        {"prog_q30",          64, 64, 0, 2, 2, 30, 1, 0, 0, 0},
        {"prog_q98",          48, 40, 0, 1, 1, 98, 1, 0, 0, 0},
        {"prog_rgb",          25, 19, 0, 1, 1, 90, 1, 0, 1, 0},
        {"prog_1x1",           1,  1, 0, 1, 1, 90, 1, 0, 0, 0},
        {"prog_8x8",           8,  8, 0, 1, 1, 90, 1, 0, 0, 0},
    };
    for (size_t i = 0; i < sizeof cases / sizeof cases[0]; i++) make(&cases[i]);
    return 0;
}
