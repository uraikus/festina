/*
 * Festina native runtime -- audio translation unit: claude.md #38 (aud,
 * loadAudio(), .play()/.stop()/.isPlaying()). See festina_runtime.h's
 * doc comment for the full design rationale -- this file is pure
 * implementation, split out of the single original festina_runtime.c so
 * that a compiled program which never uses audio never needs ALSA (or
 * -pthread, which only audio playback needs) linked in at all (see
 * festina_runtime.h's top-of-file note, and cli.py's per-feature object
 * file selection driven by CodeGen.uses_audio in festina/codegen.py).
 * Self-contained -- unlike graphics, audio shares no state with the
 * core timer loop, so it needs nothing from festina_runtime_internal.h.
 */
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <pthread.h>    /* background audio playback */
#include <alsa/asoundlib.h>
#include "festina_runtime.h"

typedef struct {
    int16_t *samples;    /* interleaved PCM, 16-bit signed, little-endian */
    size_t frame_count;  /* frames (per channel), not raw samples */
    int channels;
    unsigned int sample_rate;

    pthread_mutex_t lock;    /* guards everything below */
    pthread_t thread;        /* only meaningful while thread_running */
    snd_pcm_t *pcm;          /* only meaningful while thread_running */
    int thread_running;      /* a playback thread is spawned and not yet joined */
    int playing;             /* what isPlaying() reports */
    int stop_requested;      /* set by stop()/a restarting play(), read by the thread */
} FestinaAudio;

/* The playback thread's whole job: stream already-decoded PCM to ALSA
 * in small chunks, checking stop_requested between each one so stop()
 * gets a prompt response rather than waiting for the entire clip to
 * finish. The PCM device itself was already opened+configured by
 * festina_audio_play (synchronously, before this thread was even
 * spawned -- see its own comment for why), so this thread's only jobs
 * are writing frames and, on the way out (however it ends: finished,
 * stopped, or an unrecoverable ALSA error), closing the device and
 * resetting state so isPlaying() reflects reality again. */
static void *festina_audio_thread_main(void *arg) {
    FestinaAudio *a = (FestinaAudio *)arg;
    const snd_pcm_uframes_t chunk_frames = 4096;
    size_t frame = 0;

    while (frame < a->frame_count) {
        pthread_mutex_lock(&a->lock);
        int stop = a->stop_requested;
        pthread_mutex_unlock(&a->lock);
        if (stop) break;

        size_t remaining = a->frame_count - frame;
        snd_pcm_uframes_t this_chunk =
            remaining < chunk_frames ? (snd_pcm_uframes_t)remaining : chunk_frames;
        snd_pcm_sframes_t written = snd_pcm_writei(
            a->pcm, a->samples + frame * (size_t)a->channels, this_chunk);
        if (written < 0) {
            /* snd_pcm_recover handles the common recoverable cases
             * (buffer underrun, a suspended device); anything it can't
             * fix, just stop -- there's no Festina-level way to report
             * a mid-playback error after play() has already returned
             * successfully. */
            written = snd_pcm_recover(a->pcm, (int)written, 0);
            if (written < 0) break;
            continue;
        }
        frame += (size_t)written;
    }

    pthread_mutex_lock(&a->lock);
    snd_pcm_close(a->pcm);
    a->pcm = NULL;
    a->playing = 0;
    a->thread_running = 0;
    pthread_mutex_unlock(&a->lock);
    return NULL;
}

void *festina_load_audio(const char *path) {
    if (!path) path = "";
    FILE *f = fopen(path, "rb");
    if (!f) {
        char msg[512];
        snprintf(msg, sizeof(msg), "could not open audio file '%s': %s", path, strerror(errno));
        festina_fail(msg);
    }

    unsigned char riff_hdr[12];
    if (fread(riff_hdr, 1, 12, f) != 12 ||
            memcmp(riff_hdr, "RIFF", 4) != 0 || memcmp(riff_hdr + 8, "WAVE", 4) != 0) {
        fclose(f);
        char msg[512];
        snprintf(msg, sizeof(msg),
                 "could not load audio '%s': not a WAV file (only 16-bit PCM WAV audio is supported)",
                 path);
        festina_fail(msg);
    }

    int have_fmt = 0, have_data = 0, is_pcm = 0;
    int16_t channels = 0, bits_per_sample = 0;
    uint32_t sample_rate = 0;
    unsigned char *data = NULL;
    uint32_t data_size = 0;

    unsigned char chunk_hdr[8];
    while (fread(chunk_hdr, 1, 8, f) == 8) {
        uint32_t chunk_size = (uint32_t)chunk_hdr[4] | ((uint32_t)chunk_hdr[5] << 8) |
                               ((uint32_t)chunk_hdr[6] << 16) | ((uint32_t)chunk_hdr[7] << 24);
        if (memcmp(chunk_hdr, "fmt ", 4) == 0 && chunk_size >= 16) {
            unsigned char fmt[16];
            if (fread(fmt, 1, 16, f) != 16) break;
            int16_t audio_format = (int16_t)(fmt[0] | (fmt[1] << 8));
            channels = (int16_t)(fmt[2] | (fmt[3] << 8));
            sample_rate = (uint32_t)fmt[4] | ((uint32_t)fmt[5] << 8) |
                          ((uint32_t)fmt[6] << 16) | ((uint32_t)fmt[7] << 24);
            bits_per_sample = (int16_t)(fmt[14] | (fmt[15] << 8));
            is_pcm = (audio_format == 1);
            have_fmt = 1;
            if (chunk_size > 16 && fseek(f, (long)(chunk_size - 16), SEEK_CUR) != 0) break;
        } else if (memcmp(chunk_hdr, "data", 4) == 0) {
            data = malloc(chunk_size ? chunk_size : 1);
            if (!data || fread(data, 1, chunk_size, f) != chunk_size) {
                free(data);
                data = NULL;
                break;
            }
            data_size = chunk_size;
            have_data = 1;
            if (have_fmt) break; /* ignore any chunks after data -- metadata etc. */
        } else {
            /* skip an unrecognized chunk -- chunks are padded to an even
             * length, so an odd-sized chunk has one extra byte to skip. */
            if (fseek(f, (long)chunk_size + (long)(chunk_size % 2), SEEK_CUR) != 0) break;
        }
    }
    fclose(f);

    if (!have_fmt || !is_pcm || !have_data || bits_per_sample != 16 || channels < 1) {
        free(data);
        char msg[512];
        snprintf(msg, sizeof(msg),
                 "could not load audio '%s': only 16-bit PCM WAV audio is supported", path);
        festina_fail(msg);
    }

    FestinaAudio *a = calloc(1, sizeof(FestinaAudio));
    if (!a) festina_fail("out of memory loading audio");
    /* WAV's PCM data is little-endian; every target this compiler
     * generates code for (x86/x86_64, ARM in its default mode) is too,
     * so the bytes malloc'd above are already exactly int16_t samples
     * with no conversion needed -- the same little-endian assumption
     * this runtime already makes for int/float elsewhere. */
    a->samples = (int16_t *)data;
    a->frame_count = data_size / (size_t)(channels * 2);
    a->channels = channels;
    a->sample_rate = sample_rate;
    pthread_mutex_init(&a->lock, NULL);
    return a;
}

void festina_audio_play(void *audio) {
    FestinaAudio *a = (FestinaAudio *)audio;
    if (!a) return;

    pthread_mutex_lock(&a->lock);
    if (a->thread_running) {
        /* claude.md #38 doesn't say what play() does while already
         * playing -- restarting from the beginning is the least
         * surprising choice (matching an <audio> element's own
         * .play() called again mid-playback), so stop the current
         * playback thread first. */
        a->stop_requested = 1;
        pthread_mutex_unlock(&a->lock);
        pthread_join(a->thread, NULL);
        pthread_mutex_lock(&a->lock);
    }

    /* Opened here, synchronously, rather than inside the background
     * thread -- so a missing/unusable audio device fails loudly and
     * immediately at the play() call site (festina_fail(), same as
     * "could not open the X display" for graphics), not silently on a
     * background thread with no way to report it back. */
    snd_pcm_t *pcm = NULL;
    int rc = snd_pcm_open(&pcm, "default", SND_PCM_STREAM_PLAYBACK, 0);
    if (rc >= 0) {
        rc = snd_pcm_set_params(pcm, SND_PCM_FORMAT_S16_LE, SND_PCM_ACCESS_RW_INTERLEAVED,
                                 (unsigned int)a->channels, a->sample_rate, 1, 500000);
    }
    if (rc < 0) {
        pthread_mutex_unlock(&a->lock);
        char msg[256];
        snprintf(msg, sizeof(msg),
                 "could not open an audio output device: %s (is any audio device available?)",
                 snd_strerror(rc));
        festina_fail(msg);
        return; /* unreachable -- festina_fail() calls exit() */
    }

    a->pcm = pcm;
    a->stop_requested = 0;
    a->playing = 1; /* set synchronously, before spawning, so isPlaying()
                        is deterministic immediately after play() returns */
    a->thread_running = 1;
    rc = pthread_create(&a->thread, NULL, festina_audio_thread_main, a);
    if (rc != 0) {
        snd_pcm_close(pcm);
        a->pcm = NULL;
        a->playing = 0;
        a->thread_running = 0;
        pthread_mutex_unlock(&a->lock);
        festina_fail("could not start an audio playback thread");
        return;
    }
    pthread_mutex_unlock(&a->lock);
}

void festina_audio_stop(void *audio) {
    FestinaAudio *a = (FestinaAudio *)audio;
    if (!a) return;

    pthread_mutex_lock(&a->lock);
    if (!a->thread_running) {
        pthread_mutex_unlock(&a->lock);
        return; /* nothing playing -- a safe no-op, matching a real
                    media player's stop() on an already-stopped clip */
    }
    a->stop_requested = 1;
    pthread_mutex_unlock(&a->lock);
    /* Joins (not just signals) before returning, so isPlaying() is
     * guaranteed false the instant stop() returns -- not just "false
     * soon". The thread sets playing/thread_running itself right
     * before it exits (see festina_audio_thread_main). */
    pthread_join(a->thread, NULL);
}

int8_t festina_audio_is_playing(void *audio) {
    FestinaAudio *a = (FestinaAudio *)audio;
    if (!a) return 0;
    pthread_mutex_lock(&a->lock);
    int8_t playing = (int8_t)a->playing;
    pthread_mutex_unlock(&a->lock);
    return playing;
}
