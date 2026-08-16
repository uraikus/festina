/*
 * Festina native runtime -- audio translation unit: claude.md #38 (aud,
 * loadAudio(), .play()/.stop()/.isPlaying()) and claude.md #99 (named
 * channels: play(n)/playLoop(n)/stopAudioPlayer(n)). See festina_runtime.h's
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

/* claude.md #98/#99: the channel table.
 *
 * A CHANNEL is a playback slot: one thread, one ALSA handle, and
 * whichever clip is currently going through it. The table is
 * PROCESS-GLOBAL, not per-`aud`, and claude.md #99 is what forced that
 * -- two different clips have to be able to share a channel:
 *
 *     adventureMusic.playLoop(0)
 *     battleMusic.playLoop(0)     // takes channel 0 over
 *
 * With a per-clip pool (claude.md #98's own shape) those two are
 * different pools and "channel 0" means two different things, so the
 * handover cannot be expressed at all. Global channels also make
 * stopAudioPlayer(0) a plain free function rather than something that
 * would have to name a clip to find the channel.
 *
 * FESTINA_AUDIO_PLAYER_CAP is a hard ceiling: each channel is a thread
 * and a device handle, and a program asking for thousands of those has
 * a bug rather than a requirement. The DEFAULT of 10 bounds only
 * AUTOMATIC assignment -- see festina_audio_claim_channel. */
#define FESTINA_AUDIO_PLAYER_CAP 64
#define FESTINA_AUDIO_PLAYERS_DEFAULT 10

typedef struct FestinaAudio {
    int16_t *samples;    /* interleaved PCM, 16-bit signed, little-endian */
    size_t frame_count;  /* frames (per channel), not raw samples */
    int channels;
    unsigned int sample_rate;
} FestinaAudio;

typedef struct FestinaChannel {
    FestinaAudio *clip;      /* what is playing here; NULL when never used */
    pthread_t thread;
    snd_pcm_t *pcm;          /* only meaningful while active */
    int active;              /* a thread is streaming right now */
    int joinable;            /* a thread was started and not yet joined --
                                stays 1 after the thread exits, since a
                                finished thread still has to be joined
                                before its slot can be reused */
    int stop_requested;      /* set by stop()/stealing, read by the thread */
    int looping;             /* playLoop: restart at the end instead of ending */
    int locked;              /* claude.md #99: playLoop reserves the channel.
                                A locked channel is never chosen by automatic
                                assignment and never stolen -- only an explicit
                                play(n)/playLoop(n) on that exact channel, or
                                stopAudioPlayer(n), can take it back. Without
                                this a looping music track would eventually be
                                stolen by an ordinary sound effect. */
    uint64_t started_seq;    /* play order, for choosing which channel to steal */
} FestinaChannel;

/* One lock for the whole table rather than one per channel: every
 * operation here either walks the table (isPlaying, stop, stealing) or
 * hands a channel from one clip to another, so per-channel locks would
 * have to be taken in bulk anyway. Contention is not a concern -- a
 * playback thread takes it once per 4096-frame chunk, which at 44.1kHz
 * is about once every 93ms. */
static pthread_mutex_t g_audio_lock = PTHREAD_MUTEX_INITIALIZER;
static FestinaChannel g_channels[FESTINA_AUDIO_PLAYER_CAP];
static uint64_t g_next_seq = 1;

/* Read by every play() and written only by setMaxAudioPlayers. Not
 * guarded: Festina programs are single-threaded apart from the playback
 * threads this file spawns, and none of those ever touches it. */
static int g_max_audio_players = FESTINA_AUDIO_PLAYERS_DEFAULT;

/* claude.md #99: an out-of-range channel is CLAMPED rather than
 * rejected, the same call setMaxAudioPlayers already makes for the same
 * reason (claude.md #98) -- this is a tuning knob, and killing a
 * running game over a number that is merely out of range is a worse
 * trade than giving it the nearest workable one. maxAudioPlayers() is
 * there for a program that wants to check rather than guess. */
static int festina_clamp_channel(int64_t channel) {
    if (channel < 0) return 0;
    if (channel >= FESTINA_AUDIO_PLAYER_CAP) return FESTINA_AUDIO_PLAYER_CAP - 1;
    return (int)channel;
}

static int festina_pool_limit(void) {
    int limit = g_max_audio_players;
    if (limit < 1) limit = 1;
    if (limit > FESTINA_AUDIO_PLAYER_CAP) limit = FESTINA_AUDIO_PLAYER_CAP;
    return limit;
}

/* The playback thread's whole job: stream already-decoded PCM to ALSA
 * in small chunks, checking stop_requested between each one so a stop
 * gets a prompt response rather than waiting for the entire clip to
 * finish. The PCM device itself was already opened+configured by
 * festina_audio_play (synchronously, before this thread was even
 * spawned -- see its own comment for why), so this thread's only jobs
 * are writing frames and, on the way out (however it ends: finished,
 * stopped, or an unrecoverable ALSA error), closing the device and
 * resetting state so isPlaying() reflects reality again. */
static void *festina_audio_thread_main(void *arg) {
    FestinaChannel *ch = (FestinaChannel *)arg;
    FestinaAudio *a = ch->clip;
    const snd_pcm_uframes_t chunk_frames = 4096;
    size_t frame = 0;

    for (;;) {
        pthread_mutex_lock(&g_audio_lock);
        int stop = ch->stop_requested;
        int looping = ch->looping;
        pthread_mutex_unlock(&g_audio_lock);
        if (stop) break;

        if (frame >= a->frame_count) {
            /* claude.md #99: playLoop restarts here rather than ending.
             * Seamless in the sense that matters -- the device is never
             * closed and reopened between repetitions, so there is no
             * gap beyond ALSA's own buffering. A zero-length clip would
             * otherwise spin this loop at full speed, so it ends
             * instead: looping silence forever is never what anyone
             * meant. */
            if (!looping || a->frame_count == 0) break;
            frame = 0;
            continue;
        }

        size_t remaining = a->frame_count - frame;
        snd_pcm_uframes_t this_chunk =
            remaining < chunk_frames ? (snd_pcm_uframes_t)remaining : chunk_frames;
        /* a->samples is read-only for the whole life of the clip, so
         * every channel streams the same buffer with no lock held here
         * -- only the channel's own mutable state above needs guarding. */
        snd_pcm_sframes_t written = snd_pcm_writei(
            ch->pcm, a->samples + frame * (size_t)a->channels, this_chunk);
        if (written < 0) {
            /* snd_pcm_recover handles the common recoverable cases
             * (buffer underrun, a suspended device); anything it can't
             * fix, just stop -- there's no Festina-level way to report
             * a mid-playback error after play() has already returned
             * successfully. */
            written = snd_pcm_recover(ch->pcm, (int)written, 0);
            if (written < 0) break;
            continue;
        }
        frame += (size_t)written;
    }

    pthread_mutex_lock(&g_audio_lock);
    snd_pcm_close(ch->pcm);
    ch->pcm = NULL;
    ch->active = 0;
    ch->looping = 0;
    /* The lock is deliberately NOT cleared here. A looping track that
     * hit an unrecoverable device error should leave its channel
     * reserved rather than silently handing it to the next sound
     * effect -- the program asked for that channel and nothing has told
     * it otherwise. stopAudioPlayer(n) and an explicit play(n) both
     * still release it. `joinable` stays 1 for the same reason it did
     * in claude.md #98: this thread has exited but nothing has joined
     * it, and reusing the slot without joining would leak it. */
    pthread_mutex_unlock(&g_audio_lock);
    return NULL;
}

/* Joins a channel's finished thread, if it has one, so the channel can
 * be reused. Must be called with the lock HELD; it drops the lock
 * across the join (an exiting thread takes the same lock on its way
 * out, so joining while holding it would deadlock) and retakes it. */
static void festina_audio_reap_locked(FestinaChannel *ch) {
    if (!ch->joinable) return;
    pthread_t thread = ch->thread;
    ch->joinable = 0;
    pthread_mutex_unlock(&g_audio_lock);
    pthread_join(thread, NULL);
    pthread_mutex_lock(&g_audio_lock);
}

/* Stops a channel and joins it, so the caller can be sure nothing is
 * streaming through it once this returns. Lock HELD, dropped across
 * the join. `release` also clears the claude.md #99 reservation --
 * true for stopAudioPlayer() and for an explicit play(n) taking the
 * channel over, false for stealing (which never touches a locked
 * channel anyway). */
static void festina_audio_halt_locked(FestinaChannel *ch, int release) {
    if (ch->active) {
        ch->stop_requested = 1;
        festina_audio_reap_locked(ch);
    } else {
        festina_audio_reap_locked(ch);
    }
    ch->stop_requested = 0;
    ch->looping = 0;
    if (release) {
        ch->locked = 0;
        ch->clip = NULL;
    }
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
    return a;
}



/* claude.md #98/#99: picks the channel this play() will use. Called
 * with the lock HELD (and may drop/retake it, via the helpers above).
 * Returns NULL only in the one case where there is genuinely nowhere
 * to play -- see the end of this function.
 *
 * An EXPLICIT channel always wins outright: the program named it, so
 * whatever was there is stopped and the channel handed over, locked or
 * not. That is what makes `battleMusic.playLoop(0)` take channel 0
 * away from `adventureMusic.playLoop(0)`, which is the whole point of
 * naming channels.
 *
 * Automatic assignment prefers, in order:
 *   1. An idle, unlocked channel below the pool limit -- the common
 *      case, and what makes overlapping effects layer instead of
 *      cutting each other off.
 *   2. If all of those are busy, the OLDEST unlocked one below the
 *      limit is stolen. Something has to give at the limit, and the
 *      sound playing longest is closest to finishing anyway, whereas
 *      dropping the NEW play would silence a rapid-fire effect at
 *      exactly the moment it fires fastest.
 *   3. An idle unlocked channel ABOVE the limit, if reserved channels
 *      have eaten the whole pool. A program that locks channels 0-9
 *      and then fires a sound effect should still hear it; the limit
 *      exists to bound automatic growth, not to strand a program that
 *      reserved its way out of the pool.
 * If every channel in the table is locked, there is nothing left that
 * automatic assignment is allowed to touch, and the play is dropped.
 * That is the program's own doing and the only alternative would be to
 * break a reservation it explicitly asked for. */
static FestinaChannel *festina_audio_claim_channel(int64_t channel, int explicit_channel) {
    if (explicit_channel) {
        FestinaChannel *ch = &g_channels[festina_clamp_channel(channel)];
        festina_audio_halt_locked(ch, 1);
        return ch;
    }

    int limit = festina_pool_limit();
    for (int i = 0; i < limit; i++) {
        FestinaChannel *ch = &g_channels[i];
        if (ch->active || ch->locked) continue;
        festina_audio_reap_locked(ch);
        if (!ch->active && !ch->locked) return ch;
    }

    FestinaChannel *oldest = NULL;
    for (int i = 0; i < limit; i++) {
        FestinaChannel *ch = &g_channels[i];
        if (!ch->active || ch->locked) continue;
        if (!oldest || ch->started_seq < oldest->started_seq) oldest = ch;
    }
    if (oldest) {
        festina_audio_halt_locked(oldest, 0);
        return oldest;
    }

    for (int i = limit; i < FESTINA_AUDIO_PLAYER_CAP; i++) {
        FestinaChannel *ch = &g_channels[i];
        if (ch->active || ch->locked) continue;
        festina_audio_reap_locked(ch);
        if (!ch->active && !ch->locked) return ch;
    }
    return NULL;
}

/* Stops and joins the oldest ACTIVE unlocked channel other than
 * `except`, returning 1 if there was one. Lock HELD.
 *
 * This exists for a failure mode the pool introduced and the original
 * one-thread-per-clip design could not have: this file opens one ALSA
 * handle per channel, and not every "default" device does software
 * mixing. On a bare hw: device with no dmix -- ordinary on minimal and
 * embedded Linux, and on any machine where another program holds the
 * device exclusively -- the SECOND concurrent open fails with EBUSY.
 * Treating that as fatal (which it was, briefly) meant an overlapping
 * play() killed the program outright on such a system, with an error
 * message about there being no audio device when there plainly was one.
 *
 * Freeing a channel and retrying degrades that case back to exactly
 * the pre-pool behaviour -- overlapping plays cut each other off
 * instead of layering -- which is the right answer: on a device that
 * cannot mix, layering was never physically possible, and silently
 * getting fewer simultaneous sounds beats not running. Locked channels
 * are skipped even here: a reserved music track losing its handle to a
 * sound effect would be exactly the takeover claude.md #99's lock
 * exists to prevent. */
static int festina_audio_free_oldest_locked(FestinaChannel *except) {
    FestinaChannel *oldest = NULL;
    for (int i = 0; i < FESTINA_AUDIO_PLAYER_CAP; i++) {
        FestinaChannel *ch = &g_channels[i];
        if (ch == except || !ch->active || ch->locked) continue;
        if (!oldest || ch->started_seq < oldest->started_seq) oldest = ch;
    }
    if (!oldest) return 0;
    festina_audio_halt_locked(oldest, 0);
    return 1;
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

/* claude.md #38/#99. `channel` is the channel to play on and
 * `explicit_channel` says whether the program actually named it (a
 * bare play() passes 0 and gets automatic assignment); `looping` is
 * playLoop() rather than play(). One function rather than four
 * because the four differ only in these two flags -- everything about
 * claiming a channel, opening a device and spawning a thread is
 * identical. */
void festina_audio_play_on(void *audio, int64_t channel, int8_t explicit_channel,
                            int8_t looping) {
    FestinaAudio *a = (FestinaAudio *)audio;
    if (!a) return;

    pthread_mutex_lock(&g_audio_lock);
    FestinaChannel *ch = festina_audio_claim_channel(channel, explicit_channel);
    if (!ch) {
        /* Every channel is reserved -- see festina_audio_claim_channel. */
        pthread_mutex_unlock(&g_audio_lock);
        return;
    }

    /* Opened here, synchronously, rather than inside the background
     * thread -- so a missing/unusable audio device fails loudly and
     * immediately at the play() call site (festina_fail(), same as
     * "could not open the X display" for graphics), not silently on a
     * background thread with no way to report it back.
     *
     * The retry loop distinguishes the two ways this can fail, which
     * matters because only ONE of them is actually fatal. "The device
     * will not give me an Nth simultaneous stream" is a limit of the
     * device, not the absence of one, and the answer is to give a
     * playing channel's handle back and try again -- see
     * festina_audio_free_oldest_locked. Only when there is no other
     * channel left to free has the program genuinely failed to open any
     * audio device at all, which is what the error below claims. */
    snd_pcm_t *pcm = NULL;
    int rc;
    for (;;) {
        pcm = NULL;
        rc = snd_pcm_open(&pcm, "default", SND_PCM_STREAM_PLAYBACK, 0);
        if (rc >= 0) {
            rc = snd_pcm_set_params(pcm, SND_PCM_FORMAT_S16_LE, SND_PCM_ACCESS_RW_INTERLEAVED,
                                     (unsigned int)a->channels, a->sample_rate, 1, 500000);
            if (rc < 0) snd_pcm_close(pcm);
        }
        if (rc >= 0) break;
        if (!festina_audio_free_oldest_locked(ch)) break;
    }
    if (rc < 0) {
        pthread_mutex_unlock(&g_audio_lock);
        char msg[256];
        snprintf(msg, sizeof(msg),
                 "could not open an audio output device: %s (is any audio device available?)",
                 snd_strerror(rc));
        festina_fail(msg);
        return; /* unreachable -- festina_fail() calls exit() */
    }

    ch->clip = a;
    ch->pcm = pcm;
    ch->stop_requested = 0;
    ch->looping = looping ? 1 : 0;
    /* claude.md #99: playLoop reserves, play releases. That single
     * assignment is the whole "or if the channel is explicitly listed
     * in play()/playLoop()" rule -- an explicit play(n) on a reserved
     * channel takes it over AND hands it back to the pool, while
     * playLoop(n) takes it over and keeps it. */
    ch->locked = looping ? 1 : 0;
    ch->started_seq = g_next_seq++;
    ch->active = 1;  /* set synchronously, before spawning, so isPlaying()
                        is deterministic immediately after play() returns */
    rc = pthread_create(&ch->thread, NULL, festina_audio_thread_main, ch);
    if (rc != 0) {
        snd_pcm_close(pcm);
        ch->pcm = NULL;
        ch->active = 0;
        ch->locked = 0;
        pthread_mutex_unlock(&g_audio_lock);
        festina_fail("could not start an audio playback thread");
        return;
    }
    ch->joinable = 1;
    pthread_mutex_unlock(&g_audio_lock);
}

/* claude.md #99: stopAudioPlayer(n) -- stop one channel and release
 * its reservation. A negative channel means every channel, which is
 * what a bare stopAudioPlayer() compiles to: "stop all audio" is the
 * obvious reading of naming no channel, and there is no other way to
 * say it.
 *
 * JOINS rather than merely signalling, so a channel is guaranteed
 * idle the instant this returns, not just "idle soon". */
void festina_stop_audio_player(int64_t channel) {
    pthread_mutex_lock(&g_audio_lock);
    if (channel < 0) {
        for (int i = 0; i < FESTINA_AUDIO_PLAYER_CAP; i++) {
            g_channels[i].stop_requested = 1;
        }
        for (int i = 0; i < FESTINA_AUDIO_PLAYER_CAP; i++) {
            festina_audio_halt_locked(&g_channels[i], 1);
        }
    } else {
        festina_audio_halt_locked(&g_channels[festina_clamp_channel(channel)], 1);
    }
    pthread_mutex_unlock(&g_audio_lock);
}

int8_t festina_audio_is_playing(void *audio) {
    FestinaAudio *a = (FestinaAudio *)audio;
    if (!a) return 0;
    /* True while ANY channel is still streaming this clip -- the mirror
     * image of stop()'s "the clip" reading. */
    pthread_mutex_lock(&g_audio_lock);
    int8_t playing = 0;
    for (int i = 0; i < FESTINA_AUDIO_PLAYER_CAP; i++) {
        if (g_channels[i].active && g_channels[i].clip == a) { playing = 1; break; }
    }
    pthread_mutex_unlock(&g_audio_lock);
    return playing;
}
