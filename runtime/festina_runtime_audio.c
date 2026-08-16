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

/* claude.md #98: how many voices one `aud` may have playing at once.
 * FESTINA_AUDIO_PLAYER_CAP is a hard ceiling on what setMaxAudioPlayers
 * will accept -- each voice is its own thread and its own ALSA device
 * handle, and a program asking for thousands of those has a bug rather
 * than a requirement. 10 is the default: enough that a game firing
 * overlapping effects never hears one cut another off, small enough
 * that the per-aud array costs nothing worth thinking about. */
#define FESTINA_AUDIO_PLAYER_CAP 64
#define FESTINA_AUDIO_PLAYERS_DEFAULT 10

/* Read by every play() and written only by setMaxAudioPlayers. Not
 * mutex-guarded: Festina programs are single-threaded apart from the
 * playback threads this file spawns, and none of those ever touches
 * it -- setMaxAudioPlayers is only ever reached from program code. */
static int g_max_audio_players = FESTINA_AUDIO_PLAYERS_DEFAULT;

/* One voice = one simultaneous playback of the clip that owns it. The
 * decoded PCM lives once on the FestinaAudio and every voice streams
 * the same buffer read-only, so N voices cost N threads and N device
 * handles, never N copies of the audio. */
typedef struct FestinaVoice {
    struct FestinaAudio *owner;
    pthread_t thread;
    snd_pcm_t *pcm;          /* only meaningful while active */
    int active;              /* a thread is streaming right now */
    int joinable;            /* a thread was started and not yet joined --
                                stays 1 after the thread exits, since a
                                finished thread still has to be joined
                                before its slot can be reused */
    int stop_requested;      /* set by stop()/voice stealing, read by the thread */
    uint64_t started_seq;    /* play order, for choosing which voice to steal */
} FestinaVoice;

typedef struct FestinaAudio {
    int16_t *samples;    /* interleaved PCM, 16-bit signed, little-endian */
    size_t frame_count;  /* frames (per channel), not raw samples */
    int channels;
    unsigned int sample_rate;

    pthread_mutex_t lock;    /* guards everything below */
    FestinaVoice voices[FESTINA_AUDIO_PLAYER_CAP];
    uint64_t next_seq;       /* monotonic, so started_seq orders voices */
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
    FestinaVoice *v = (FestinaVoice *)arg;
    FestinaAudio *a = v->owner;
    const snd_pcm_uframes_t chunk_frames = 4096;
    size_t frame = 0;

    while (frame < a->frame_count) {
        pthread_mutex_lock(&a->lock);
        int stop = v->stop_requested;
        pthread_mutex_unlock(&a->lock);
        if (stop) break;

        size_t remaining = a->frame_count - frame;
        snd_pcm_uframes_t this_chunk =
            remaining < chunk_frames ? (snd_pcm_uframes_t)remaining : chunk_frames;
        /* a->samples is read-only for the whole life of the clip, so
         * every voice streams the same buffer with no lock held here --
         * only the voice's own mutable state above needs guarding. */
        snd_pcm_sframes_t written = snd_pcm_writei(
            v->pcm, a->samples + frame * (size_t)a->channels, this_chunk);
        if (written < 0) {
            /* snd_pcm_recover handles the common recoverable cases
             * (buffer underrun, a suspended device); anything it can't
             * fix, just stop -- there's no Festina-level way to report
             * a mid-playback error after play() has already returned
             * successfully. */
            written = snd_pcm_recover(v->pcm, (int)written, 0);
            if (written < 0) break;
            continue;
        }
        frame += (size_t)written;
    }

    pthread_mutex_lock(&a->lock);
    snd_pcm_close(v->pcm);
    v->pcm = NULL;
    v->active = 0;
    /* joinable stays 1: this thread has exited but nothing has joined
     * it yet, and reusing the slot without joining would leak the
     * thread's own resources. Whoever next claims this slot joins
     * first -- see festina_audio_claim_voice. */
    pthread_mutex_unlock(&a->lock);
    return NULL;
}

/* Joins a slot's finished thread, if it has one, so the slot can be
 * reused or the clip torn down. Must be called with the lock HELD; it
 * drops the lock across the join (a thread that is exiting takes the
 * same lock on its way out, so joining while holding it would
 * deadlock) and takes it again before returning. */
static void festina_audio_reap_locked(FestinaAudio *a, FestinaVoice *v) {
    if (!v->joinable) return;
    pthread_t thread = v->thread;
    v->joinable = 0;
    pthread_mutex_unlock(&a->lock);
    pthread_join(thread, NULL);
    pthread_mutex_lock(&a->lock);
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

/* claude.md #98: picks the slot this play() will use. Called with the
 * lock HELD (and may drop/retake it, via festina_audio_reap_locked).
 *
 * The order of preference is what makes overlapping playback behave
 * the way a game expects:
 *   1. An idle slot below the current limit -- the common case, and
 *      the whole point: a second play() while the first is still
 *      going gets its own voice instead of cutting the first off.
 *   2. If every slot in range is busy, steal the OLDEST one. Something
 *      has to give at the limit, and the sound that has been playing
 *      longest is the one closest to finishing anyway -- dropping the
 *      new play instead would make a machine-gun effect go silent at
 *      exactly the moment it is firing fastest.
 *
 * Note that at a limit of 1 this reduces exactly to the old behaviour
 * (play() while playing restarts from the beginning), which is what
 * makes setMaxAudioPlayers(1) a genuine way to ask for it back. */
static FestinaVoice *festina_audio_claim_voice(FestinaAudio *a) {
    int limit = g_max_audio_players;
    if (limit < 1) limit = 1;
    if (limit > FESTINA_AUDIO_PLAYER_CAP) limit = FESTINA_AUDIO_PLAYER_CAP;

    for (int i = 0; i < limit; i++) {
        if (!a->voices[i].active) {
            festina_audio_reap_locked(a, &a->voices[i]);
            /* Re-checked after the reap: the lock was dropped, so
             * another voice could have finished and this slot could
             * (in principle) have been claimed. Nothing else claims
             * slots today -- play() is only ever reached from the
             * single Festina program thread -- but re-testing costs
             * one branch and removes the assumption entirely. */
            if (!a->voices[i].active) return &a->voices[i];
        }
    }

    FestinaVoice *oldest = &a->voices[0];
    for (int i = 1; i < limit; i++) {
        if (a->voices[i].started_seq < oldest->started_seq) oldest = &a->voices[i];
    }
    oldest->stop_requested = 1;
    pthread_mutex_unlock(&a->lock);
    pthread_join(oldest->thread, NULL);
    pthread_mutex_lock(&a->lock);
    oldest->joinable = 0;
    return oldest;
}

void festina_set_max_audio_players(int64_t max) {
    /* Clamped rather than rejected: this is a tuning knob, and failing
     * a program outright over a number that is merely unreasonable
     * would be a worse trade than quietly giving it the nearest
     * workable one. 1 is always meaningful (it is the old
     * cut-off-the-previous-sound behaviour). */
    if (max < 1) max = 1;
    if (max > FESTINA_AUDIO_PLAYER_CAP) max = FESTINA_AUDIO_PLAYER_CAP;
    g_max_audio_players = (int)max;
}

int64_t festina_get_max_audio_players(void) {
    return g_max_audio_players;
}

void festina_audio_play(void *audio) {
    FestinaAudio *a = (FestinaAudio *)audio;
    if (!a) return;

    pthread_mutex_lock(&a->lock);
    FestinaVoice *v = festina_audio_claim_voice(a);

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

    v->owner = a;
    v->pcm = pcm;
    v->stop_requested = 0;
    v->started_seq = a->next_seq++;
    v->active = 1;  /* set synchronously, before spawning, so isPlaying()
                       is deterministic immediately after play() returns */
    rc = pthread_create(&v->thread, NULL, festina_audio_thread_main, v);
    if (rc != 0) {
        snd_pcm_close(pcm);
        v->pcm = NULL;
        v->active = 0;
        pthread_mutex_unlock(&a->lock);
        festina_fail("could not start an audio playback thread");
        return;
    }
    v->joinable = 1;
    pthread_mutex_unlock(&a->lock);
}

void festina_audio_stop(void *audio) {
    FestinaAudio *a = (FestinaAudio *)audio;
    if (!a) return;

    /* Stops EVERY voice, not just one: `sound.stop()` names the clip,
     * and a program that asked for a clip to stop while three copies
     * of it are overlapping means all three. There is no syntax for
     * naming an individual voice, and inventing one would mean
     * exposing the pool -- which is exactly what claude.md #98 exists
     * to keep out of the language.
     *
     * Signals all of them before joining any, so N voices cost one
     * clip's worth of latency rather than N. As before, this joins
     * rather than merely signalling, so isPlaying() is guaranteed
     * false the instant stop() returns -- not just "false soon". */
    pthread_mutex_lock(&a->lock);
    for (int i = 0; i < FESTINA_AUDIO_PLAYER_CAP; i++) a->voices[i].stop_requested = 1;
    for (int i = 0; i < FESTINA_AUDIO_PLAYER_CAP; i++) {
        festina_audio_reap_locked(a, &a->voices[i]);
    }
    pthread_mutex_unlock(&a->lock);
}

int8_t festina_audio_is_playing(void *audio) {
    FestinaAudio *a = (FestinaAudio *)audio;
    if (!a) return 0;
    /* True while ANY voice is still streaming -- the mirror image of
     * stop()'s "the clip" reading. */
    pthread_mutex_lock(&a->lock);
    int8_t playing = 0;
    for (int i = 0; i < FESTINA_AUDIO_PLAYER_CAP; i++) {
        if (a->voices[i].active) { playing = 1; break; }
    }
    pthread_mutex_unlock(&a->lock);
    return playing;
}
