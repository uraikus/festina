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
#include <time.h>       /* nanosleep -- the null device's pacing stub */
#include <pthread.h>    /* background audio playback */
/* claude.md #121 / macos.md Phase 1: the DEVICE layer is behind the
 * festina_pcm_* seam below, so only the per-platform implementation
 * includes its platform's audio API. FESTINA_AUDIO_DEVICE_EXTERNAL is
 * the white-box test harnesses' hook: they define it and provide their
 * own festina_pcm_dev_* stubs, which is what makes the channel-pool
 * harnesses run on machines with no audio stack at all. */
#if !defined(FESTINA_AUDIO_DEVICE_EXTERNAL)
#  if defined(__APPLE__)
#    include <AudioToolbox/AudioToolbox.h>
#  elif defined(_WIN32)
#    include <windows.h>
#    include <mmsystem.h>    /* windows.md Phase 1: waveOut -- winmm, no COM */
#  else
#    include <alsa/asoundlib.h>
#  endif
#endif
#include <mpg123.h>    /* claude.md #101: MP3 decoding */
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
    /* claude.md #101: the bytes this clip was decoded FROM, kept so a
     * `file:aud` table column round-trips byte for byte -- an MP3 stays
     * an MP3 rather than becoming a much larger WAV. */
    unsigned char *bytes;
    size_t byte_count;
    /* claude.md #110: the path this clip was loaded from, so save() with
     * no argument has somewhere to write. Empty (never NULL) for a clip
     * decoded from bytes rather than a file -- a database column -- which
     * is the case save(path) exists for and save() refuses. */
    char *path;
} FestinaAudio;

/* ---- the audio DEVICE seam -- claude.md #121, macos.md/windows.md
 * Phase 1 ----
 *
 * Everything above and below this section is portable: the channel
 * pool, the WAV/MP3 decoding, the pthread streaming model. The one
 * thing that differs per platform is the device a stream of
 * interleaved 16-bit frames is pushed into, and its whole surface is
 * these three functions:
 *
 *   festina_pcm_open(channels, rate, errbuf, n)  -> handle or NULL
 *   festina_pcm_write(handle, frames, count)     -> frames written, <0 fatal
 *   festina_pcm_close(handle)
 *
 * write() BLOCKS until the device has room -- that is the contract the
 * per-channel streaming threads are built on, and every backend
 * reproduces it (ALSA's blocking writei; AudioQueue's fixed buffer
 * pool plus a condition variable). Recoverable device hiccups are the
 * backend's own problem (ALSA's snd_pcm_recover lives inside its
 * write, not in the shared thread loop).
 *
 * FESTINA_AUDIO_NULL=1 in the environment turns open() into a null
 * sink that accepts frames instantly -- one CI/testing mechanism for
 * every platform, one layer below the ALSA-only ~/.asoundrc trick the
 * test suite historically used, and honest about being a shim: it
 * exists so play()/stop()/isPlaying() are exercisable on machines
 * with no audio device (macOS CI, containers), not to fake playback
 * timing. */

typedef struct FestinaPcm {
    int is_null;   /* the FESTINA_AUDIO_NULL sink -- no backend handle */
    void *dev;     /* the backend's own handle otherwise */
} FestinaPcm;

/* The three per-backend functions the dispatch below routes to. A
 * white-box harness (FESTINA_AUDIO_DEVICE_EXTERNAL) supplies these
 * three symbols itself instead of any real backend. */
#if defined(FESTINA_AUDIO_DEVICE_EXTERNAL)
void *festina_pcm_dev_open(int channels, unsigned int rate,
                           char *errbuf, size_t errbuf_size);
long festina_pcm_dev_write(void *dev, const int16_t *frames, size_t frame_count);
void festina_pcm_dev_close(void *dev);

#elif defined(__APPLE__)
/* macos.md Phase 1: AudioQueue. N preallocated buffers plus a
 * condition variable reproduce ALSA's blocking push exactly: write()
 * waits for a free buffer, fills it, enqueues it; the completion
 * callback returns buffers to the free stack. Per-channel queues
 * mirror the per-channel ALSA handles one-to-one, and CoreAudio
 * software-mixes, so the device-busy retry path simply never fires.
 *
 * NOTE (macos.md): compiled and null-shim-exercised by the macOS CI
 * job; real-device playback still needs verification on hardware
 * before the darwin audio gate in festina/cli.py is lifted. */
#define FESTINA_AQ_BUFFERS 4
#define FESTINA_AQ_CHUNK_FRAMES 4096

typedef struct {
    AudioQueueRef queue;
    pthread_mutex_t lock;
    pthread_cond_t cond;
    AudioQueueBufferRef free_bufs[FESTINA_AQ_BUFFERS];
    int free_count;
    int channels;
    int started;
} FestinaAqDev;

static void festina_aq_done(void *user, AudioQueueRef q, AudioQueueBufferRef buf) {
    (void)q;
    FestinaAqDev *d = (FestinaAqDev *)user;
    pthread_mutex_lock(&d->lock);
    d->free_bufs[d->free_count++] = buf;
    pthread_cond_signal(&d->cond);
    pthread_mutex_unlock(&d->lock);
}

static void *festina_pcm_dev_open(int channels, unsigned int rate,
                                  char *errbuf, size_t errbuf_size) {
    FestinaAqDev *d = calloc(1, sizeof(FestinaAqDev));
    if (!d) {
        snprintf(errbuf, errbuf_size, "out of memory opening an audio queue");
        return NULL;
    }
    d->channels = channels;
    pthread_mutex_init(&d->lock, NULL);
    pthread_cond_init(&d->cond, NULL);

    AudioStreamBasicDescription fmt;
    memset(&fmt, 0, sizeof(fmt));
    fmt.mSampleRate = (Float64)rate;
    fmt.mFormatID = kAudioFormatLinearPCM;
    fmt.mFormatFlags = kLinearPCMFormatFlagIsSignedInteger | kAudioFormatFlagIsPacked;
    fmt.mBitsPerChannel = 16;
    fmt.mChannelsPerFrame = (UInt32)channels;
    fmt.mBytesPerFrame = (UInt32)(2 * channels);
    fmt.mFramesPerPacket = 1;
    fmt.mBytesPerPacket = (UInt32)(2 * channels);

    OSStatus rc = AudioQueueNewOutput(&fmt, festina_aq_done, d, NULL, NULL, 0, &d->queue);
    if (rc != noErr) {
        snprintf(errbuf, errbuf_size, "AudioQueueNewOutput failed (OSStatus %d)", (int)rc);
        pthread_mutex_destroy(&d->lock);
        pthread_cond_destroy(&d->cond);
        free(d);
        return NULL;
    }
    UInt32 buf_bytes = (UInt32)(FESTINA_AQ_CHUNK_FRAMES * 2 * channels);
    for (int i = 0; i < FESTINA_AQ_BUFFERS; i++) {
        AudioQueueBufferRef buf;
        rc = AudioQueueAllocateBuffer(d->queue, buf_bytes, &buf);
        if (rc != noErr) {
            snprintf(errbuf, errbuf_size, "AudioQueueAllocateBuffer failed (OSStatus %d)", (int)rc);
            AudioQueueDispose(d->queue, true);
            pthread_mutex_destroy(&d->lock);
            pthread_cond_destroy(&d->cond);
            free(d);
            return NULL;
        }
        d->free_bufs[d->free_count++] = buf;
    }
    return d;
}

static long festina_pcm_dev_write(void *dev, const int16_t *frames, size_t frame_count) {
    FestinaAqDev *d = (FestinaAqDev *)dev;
    if (frame_count > FESTINA_AQ_CHUNK_FRAMES) frame_count = FESTINA_AQ_CHUNK_FRAMES;

    pthread_mutex_lock(&d->lock);
    while (d->free_count == 0) pthread_cond_wait(&d->cond, &d->lock);
    AudioQueueBufferRef buf = d->free_bufs[--d->free_count];
    pthread_mutex_unlock(&d->lock);

    size_t bytes = frame_count * 2 * (size_t)d->channels;
    memcpy(buf->mAudioData, frames, bytes);
    buf->mAudioDataByteSize = (UInt32)bytes;
    if (AudioQueueEnqueueBuffer(d->queue, buf, 0, NULL) != noErr) {
        festina_aq_done(d, d->queue, buf);   /* hand the buffer back */
        return -1;
    }
    if (!d->started) {
        if (AudioQueueStart(d->queue, NULL) != noErr) return -1;
        d->started = 1;
    }
    return (long)frame_count;
}

static void festina_pcm_dev_close(void *dev) {
    FestinaAqDev *d = (FestinaAqDev *)dev;
    /* Synchronous stop: every in-flight buffer's callback fires before
     * this returns, so disposing afterwards races nothing. */
    AudioQueueStop(d->queue, true);
    AudioQueueDispose(d->queue, true);
    pthread_mutex_destroy(&d->lock);
    pthread_cond_destroy(&d->cond);
    free(d);
}

#elif defined(_WIN32)
/* Windows (windows.md Phase 1): winmm's waveOut, not WASAPI -- WASAPI is
 * COM-based and event-driven, and buys latency we don't need for a
 * demo-scale runtime. This reproduces the AudioQueue shim just above:
 * a fixed pool of buffers, a completion callback that returns buffers to
 * a free list, and a condition variable so festina_pcm_dev_write blocks
 * exactly like ALSA's snd_pcm_writei does when the device has no room. */
#define FESTINA_WO_BUFFERS 4
#define FESTINA_WO_CHUNK_FRAMES 4096

typedef struct {
    HWAVEOUT hwo;
    pthread_mutex_t lock;
    pthread_cond_t cond;
    WAVEHDR *bufs[FESTINA_WO_BUFFERS];
    WAVEHDR *free_bufs[FESTINA_WO_BUFFERS];
    int free_count;
    int channels;
} FestinaWoDev;

/* waveOutOpen's CALLBACK_FUNCTION contract: this runs on some internal
 * thread, not ours, and may only touch a short allow-list of Win32 APIs --
 * no blocking calls. Storing the freed header and signalling our own
 * condition variable is exactly the AudioQueue callback's shape. */
static void CALLBACK festina_wo_proc(HWAVEOUT hwo, UINT msg, DWORD_PTR instance,
                                      DWORD_PTR param1, DWORD_PTR param2) {
    (void)hwo; (void)param2;
    if (msg != WOM_DONE) return;
    FestinaWoDev *d = (FestinaWoDev *)instance;
    WAVEHDR *hdr = (WAVEHDR *)param1;
    pthread_mutex_lock(&d->lock);
    d->free_bufs[d->free_count++] = hdr;
    pthread_cond_signal(&d->cond);
    pthread_mutex_unlock(&d->lock);
}

static void *festina_pcm_dev_open(int channels, unsigned int rate,
                                  char *errbuf, size_t errbuf_size) {
    FestinaWoDev *d = calloc(1, sizeof(FestinaWoDev));
    if (!d) {
        snprintf(errbuf, errbuf_size, "out of memory opening a waveOut device");
        return NULL;
    }
    d->channels = channels;
    pthread_mutex_init(&d->lock, NULL);
    pthread_cond_init(&d->cond, NULL);

    WAVEFORMATEX fmt;
    memset(&fmt, 0, sizeof(fmt));
    fmt.wFormatTag = WAVE_FORMAT_PCM;
    fmt.nChannels = (WORD)channels;
    fmt.nSamplesPerSec = rate;
    fmt.wBitsPerSample = 16;
    fmt.nBlockAlign = (WORD)(channels * 2);
    fmt.nAvgBytesPerSec = rate * fmt.nBlockAlign;

    MMRESULT rc = waveOutOpen(&d->hwo, WAVE_MAPPER, &fmt,
                               (DWORD_PTR)festina_wo_proc, (DWORD_PTR)d,
                               CALLBACK_FUNCTION);
    if (rc != MMSYSERR_NOERROR) {
        snprintf(errbuf, errbuf_size, "waveOutOpen failed (MMRESULT %u)", (unsigned)rc);
        pthread_mutex_destroy(&d->lock);
        pthread_cond_destroy(&d->cond);
        free(d);
        return NULL;
    }

    size_t buf_bytes = (size_t)FESTINA_WO_CHUNK_FRAMES * 2 * (size_t)channels;
    for (int i = 0; i < FESTINA_WO_BUFFERS; i++) {
        WAVEHDR *hdr = calloc(1, sizeof(WAVEHDR));
        char *data = malloc(buf_bytes);
        if (!hdr || !data) {
            snprintf(errbuf, errbuf_size, "out of memory allocating waveOut buffers");
            free(hdr); free(data);
            for (int j = 0; j < i; j++) {
                waveOutUnprepareHeader(d->hwo, d->bufs[j], sizeof(WAVEHDR));
                free(d->bufs[j]->lpData);
                free(d->bufs[j]);
            }
            waveOutClose(d->hwo);
            pthread_mutex_destroy(&d->lock);
            pthread_cond_destroy(&d->cond);
            free(d);
            return NULL;
        }
        hdr->lpData = data;
        hdr->dwBufferLength = (DWORD)buf_bytes;
        rc = waveOutPrepareHeader(d->hwo, hdr, sizeof(WAVEHDR));
        if (rc != MMSYSERR_NOERROR) {
            snprintf(errbuf, errbuf_size, "waveOutPrepareHeader failed (MMRESULT %u)", (unsigned)rc);
            free(data); free(hdr);
            for (int j = 0; j < i; j++) {
                waveOutUnprepareHeader(d->hwo, d->bufs[j], sizeof(WAVEHDR));
                free(d->bufs[j]->lpData);
                free(d->bufs[j]);
            }
            waveOutClose(d->hwo);
            pthread_mutex_destroy(&d->lock);
            pthread_cond_destroy(&d->cond);
            free(d);
            return NULL;
        }
        d->bufs[i] = hdr;
        d->free_bufs[d->free_count++] = hdr;
    }
    return d;
}

static long festina_pcm_dev_write(void *dev, const int16_t *frames, size_t frame_count) {
    FestinaWoDev *d = (FestinaWoDev *)dev;
    if (frame_count > FESTINA_WO_CHUNK_FRAMES) frame_count = FESTINA_WO_CHUNK_FRAMES;

    pthread_mutex_lock(&d->lock);
    while (d->free_count == 0) pthread_cond_wait(&d->cond, &d->lock);
    WAVEHDR *hdr = d->free_bufs[--d->free_count];
    pthread_mutex_unlock(&d->lock);

    size_t bytes = frame_count * 2 * (size_t)d->channels;
    memcpy(hdr->lpData, frames, bytes);
    hdr->dwBufferLength = (DWORD)bytes;
    MMRESULT rc = waveOutWrite(d->hwo, hdr, sizeof(WAVEHDR));
    if (rc != MMSYSERR_NOERROR) {
        /* waveOutWrite failed outright -- it will never call us back for
         * this header, so return it to the free list ourselves instead
         * of leaking it out of the pool. */
        pthread_mutex_lock(&d->lock);
        d->free_bufs[d->free_count++] = hdr;
        pthread_mutex_unlock(&d->lock);
        return -1;
    }
    return (long)frame_count;
}

static void festina_pcm_dev_close(void *dev) {
    FestinaWoDev *d = (FestinaWoDev *)dev;
    /* waveOutReset is the synchronous counterpart to AudioQueueStop(...,
     * true) above: every pending buffer's WOM_DONE fires before this
     * returns, so unpreparing/freeing them right after races nothing. */
    waveOutReset(d->hwo);
    for (int i = 0; i < FESTINA_WO_BUFFERS; i++) {
        waveOutUnprepareHeader(d->hwo, d->bufs[i], sizeof(WAVEHDR));
        free(d->bufs[i]->lpData);
        free(d->bufs[i]);
    }
    waveOutClose(d->hwo);
    pthread_mutex_destroy(&d->lock);
    pthread_cond_destroy(&d->cond);
    free(d);
}

#else
/* Linux: the original six ALSA calls, moved behind the seam verbatim.
 * snd_pcm_recover lives here now -- "retry the recoverable cases,
 * report the rest" is a device property, not channel-pool logic. */
static void *festina_pcm_dev_open(int channels, unsigned int rate,
                                  char *errbuf, size_t errbuf_size) {
    snd_pcm_t *pcm = NULL;
    int rc = snd_pcm_open(&pcm, "default", SND_PCM_STREAM_PLAYBACK, 0);
    if (rc >= 0) {
        rc = snd_pcm_set_params(pcm, SND_PCM_FORMAT_S16_LE, SND_PCM_ACCESS_RW_INTERLEAVED,
                                 (unsigned int)channels, rate, 1, 500000);
        if (rc < 0) snd_pcm_close(pcm);
    }
    if (rc < 0) {
        snprintf(errbuf, errbuf_size, "%s", snd_strerror(rc));
        return NULL;
    }
    return pcm;
}

static long festina_pcm_dev_write(void *dev, const int16_t *frames, size_t frame_count) {
    snd_pcm_sframes_t written = snd_pcm_writei((snd_pcm_t *)dev, frames,
                                               (snd_pcm_uframes_t)frame_count);
    if (written < 0) {
        written = snd_pcm_recover((snd_pcm_t *)dev, (int)written, 0);
        if (written < 0) return -1;
        return 0;   /* recovered; nothing consumed -- the caller retries */
    }
    return (long)written;
}

static void festina_pcm_dev_close(void *dev) {
    snd_pcm_close((snd_pcm_t *)dev);
}
#endif

static FestinaPcm *festina_pcm_open(int channels, unsigned int rate,
                                    char *errbuf, size_t errbuf_size) {
    FestinaPcm *pcm = calloc(1, sizeof(FestinaPcm));
    if (!pcm) {
        snprintf(errbuf, errbuf_size, "out of memory opening an audio device");
        return NULL;
    }
    const char *null_dev = getenv("FESTINA_AUDIO_NULL");
    if (null_dev && *null_dev && strcmp(null_dev, "0") != 0) {
        pcm->is_null = 1;
        return pcm;
    }
    pcm->dev = festina_pcm_dev_open(channels, rate, errbuf, errbuf_size);
    if (!pcm->dev) {
        free(pcm);
        return NULL;
    }
    return pcm;
}

static long festina_pcm_write(FestinaPcm *pcm, const int16_t *frames, size_t frame_count) {
    if (pcm->is_null) return (long)frame_count;   /* instant sink, like ALSA's null plugin */
    return festina_pcm_dev_write(pcm->dev, frames, frame_count);
}

static void festina_pcm_close(FestinaPcm *pcm) {
    if (!pcm->is_null) festina_pcm_dev_close(pcm->dev);
    free(pcm);
}

typedef struct FestinaChannel {
    FestinaAudio *clip;      /* what is playing here; NULL when never used */
    pthread_t thread;
    FestinaPcm *pcm;         /* only meaningful while active */
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
    const size_t chunk_frames = 4096;
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
        size_t this_chunk = remaining < chunk_frames ? remaining : chunk_frames;
        /* a->samples is read-only for the whole life of the clip, so
         * every channel streams the same buffer with no lock held here
         * -- only the channel's own mutable state above needs guarding.
         * claude.md #121: recoverable device hiccups are handled inside
         * festina_pcm_write (0 = recovered, retry); a negative return
         * is unrecoverable and just stops -- there's no Festina-level
         * way to report a mid-playback error after play() has already
         * returned successfully. */
        long written = festina_pcm_write(
            ch->pcm, a->samples + frame * (size_t)a->channels, this_chunk);
        if (written < 0) break;
        frame += (size_t)written;
    }

    pthread_mutex_lock(&g_audio_lock);
    festina_pcm_close(ch->pcm);
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

/* claude.md #101: decoding from MEMORY is the primitive, and loading a
 * path is "read the file, then decode the bytes" -- which is what lets
 * an `aud` come out of a sqlite BLOB column as easily as out of a file.
 * Sniffing is by content, not by file extension: a blob out of a
 * database has no extension, and an extension was never evidence of
 * anything anyway. */

/* WAV (16-bit PCM). Walks the RIFF chunk list over a byte buffer.
 * Returns 0 and leaves *out_* untouched on anything it cannot use. */
static int festina_decode_wav(const unsigned char *data, size_t len,
                               int16_t **out_samples, size_t *out_frames,
                               int *out_channels, unsigned int *out_rate) {
    if (len < 12 || memcmp(data, "RIFF", 4) != 0 || memcmp(data + 8, "WAVE", 4) != 0) return 0;

    size_t pos = 12;
    int have_fmt = 0, is_pcm = 0;
    int16_t channels = 0, bits_per_sample = 0;
    uint32_t sample_rate = 0;
    const unsigned char *audio = NULL;
    uint32_t audio_size = 0;

    while (pos + 8 <= len) {
        uint32_t chunk_size = (uint32_t)data[pos + 4] | ((uint32_t)data[pos + 5] << 8) |
                               ((uint32_t)data[pos + 6] << 16) | ((uint32_t)data[pos + 7] << 24);
        const unsigned char *body = data + pos + 8;
        size_t available = len - (pos + 8);
        if (chunk_size > available) chunk_size = (uint32_t)available;

        if (memcmp(data + pos, "fmt ", 4) == 0 && chunk_size >= 16) {
            int16_t audio_format = (int16_t)(body[0] | (body[1] << 8));
            channels = (int16_t)(body[2] | (body[3] << 8));
            sample_rate = (uint32_t)body[4] | ((uint32_t)body[5] << 8) |
                          ((uint32_t)body[6] << 16) | ((uint32_t)body[7] << 24);
            bits_per_sample = (int16_t)(body[14] | (body[15] << 8));
            is_pcm = (audio_format == 1);
            have_fmt = 1;
        } else if (memcmp(data + pos, "data", 4) == 0) {
            audio = body;
            audio_size = chunk_size;
            if (have_fmt) break; /* ignore any chunks after data -- metadata etc. */
        }
        /* Chunks are padded to an even length, so an odd-sized one has
         * one extra byte to skip. */
        pos += 8 + chunk_size + (chunk_size % 2);
    }

    if (!have_fmt || !is_pcm || !audio || bits_per_sample != 16 || channels < 1) return 0;

    int16_t *samples = malloc(audio_size ? audio_size : 1);
    if (!samples) festina_fail("out of memory loading audio");
    /* WAV's PCM data is little-endian; every target this compiler
     * generates code for (x86/x86_64, ARM in its default mode) is too,
     * so these bytes are already exactly int16_t samples with no
     * conversion needed -- the same little-endian assumption this
     * runtime already makes for int/float elsewhere. */
    memcpy(samples, audio, audio_size);
    *out_samples = samples;
    *out_frames = audio_size / (size_t)(channels * 2);
    *out_channels = channels;
    *out_rate = sample_rate;
    return 1;
}

/* MP3, via libmpg123. Feeding the whole buffer at once and reading
 * until the decoder is done is the simplest correct shape, and the
 * whole file is already in memory anyway. */
static pthread_once_t g_mpg123_init_once = PTHREAD_ONCE_INIT;
static int g_mpg123_init_ok = 0;
static void festina_mpg123_init_once(void) {
    g_mpg123_init_ok = (mpg123_init() == MPG123_OK);
}
static int festina_decode_mp3(const unsigned char *data, size_t len,
                               int16_t **out_samples, size_t *out_frames,
                               int *out_channels, unsigned int *out_rate) {
    /* claude.md #171: this used to be a plain `static int initialized`
     * flag, harmless when festina_decode_mp3 could only ever be called
     * from the main thread. Extending .callback() to `aud` makes it
     * reachable from several async-io worker threads at once (a
     * concurrent `aud.callback()` dispatch racing an ordinary
     * synchronous `aud a = 'x.mp3'` load on main, or several
     * background loads racing each other) -- a bare flag read/written
     * with no synchronization is exactly the kind of thing
     * ThreadSanitizer flags immediately (and mpg123_init() itself gives
     * no guarantee about being safe to call from two threads at once).
     * pthread_once makes the first call -- whichever thread wins -- the
     * only one that actually calls mpg123_init(), and makes every other
     * caller, on any thread, block until that one finishes. */
    pthread_once(&g_mpg123_init_once, festina_mpg123_init_once);
    if (!g_mpg123_init_ok) return 0;

    int err = MPG123_OK;
    mpg123_handle *mh = mpg123_new(NULL, &err);
    if (!mh) return 0;

    /* Ask for signed 16-bit at whatever rate the file actually is:
     * ALSA is configured per clip from these values anyway, so there is
     * nothing to gain by resampling here and something to lose. */
    mpg123_format_none(mh);
    long rates[] = { 8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000 };
    for (size_t i = 0; i < sizeof(rates) / sizeof(rates[0]); i++) {
        mpg123_format(mh, rates[i], MPG123_MONO | MPG123_STEREO, MPG123_ENC_SIGNED_16);
    }

    if (mpg123_open_feed(mh) != MPG123_OK) { mpg123_delete(mh); return 0; }

    unsigned char *pcm = NULL;
    size_t pcm_len = 0;
    unsigned char chunk[16384];
    size_t done = 0;
    int rc = mpg123_decode(mh, data, len, chunk, sizeof(chunk), &done);
    for (;;) {
        if (done > 0) {
            unsigned char *grown = realloc(pcm, pcm_len + done);
            if (!grown) { free(pcm); mpg123_delete(mh); festina_fail("out of memory loading audio"); }
            memcpy(grown + pcm_len, chunk, done);
            pcm = grown;
            pcm_len += done;
        }
        if (rc == MPG123_NEED_MORE || rc == MPG123_DONE) break;
        if (rc != MPG123_OK && rc != MPG123_NEW_FORMAT) break;
        rc = mpg123_decode(mh, NULL, 0, chunk, sizeof(chunk), &done);
    }

    long rate = 0;
    int channels = 0, encoding = 0;
    int have_format = mpg123_getformat(mh, &rate, &channels, &encoding) == MPG123_OK;
    mpg123_delete(mh);

    if (!pcm || pcm_len == 0 || !have_format || channels < 1 || rate <= 0) {
        free(pcm);
        return 0;
    }
    *out_samples = (int16_t *)pcm;
    *out_frames = pcm_len / (size_t)(channels * 2);
    *out_channels = channels;
    *out_rate = (unsigned int)rate;
    return 1;
}

void *festina_audio_from_bytes(const void *data, int64_t len, const char *label) {
    const unsigned char *bytes = (const unsigned char *)data;
    if (!label) label = "<blob>";
    if (!bytes || len <= 0) {
        char msg[512];
        snprintf(msg, sizeof(msg), "could not load audio '%s': no audio data", label);
        festina_fail(msg);
    }

    int16_t *samples = NULL;
    size_t frames = 0;
    int channels = 0;
    unsigned int rate = 0;
    int ok = 0;

    if (len >= 12 && memcmp(bytes, "RIFF", 4) == 0 && memcmp(bytes + 8, "WAVE", 4) == 0) {
        ok = festina_decode_wav(bytes, (size_t)len, &samples, &frames, &channels, &rate);
        if (!ok) {
            char msg[512];
            snprintf(msg, sizeof(msg),
                     "could not load audio '%s': only 16-bit PCM WAV audio is supported "
                     "(this WAV is compressed or is not 16-bit)", label);
            festina_fail(msg);
        }
    } else {
        /* Anything else is offered to the MP3 decoder. An ID3v2 tag
         * ("ID3") or a raw frame sync (0xFF 0xEx) are the two ordinary
         * openings, but mpg123 resyncs on its own, so handing it the
         * buffer and believing its answer beats trying to out-guess it
         * here. */
        ok = festina_decode_mp3(bytes, (size_t)len, &samples, &frames, &channels, &rate);
        if (!ok) {
            char msg[512];
            snprintf(msg, sizeof(msg),
                     "could not load audio '%s': not 16-bit PCM WAV or MP3 "
                     "(those are the two formats this runtime decodes)", label);
            festina_fail(msg);
        }
    }

    /* claude.md #118: the clip is REFERENCE COUNTED now, behind the
     * same i64 header immediately before the payload that structs/
     * arrays/maps/blobs carry (festina_retain/festina_release_check in
     * the core runtime) -- see festina_audio_free below for what that
     * bought. */
    char *raw = calloc(1, sizeof(int64_t) + sizeof(FestinaAudio));
    if (!raw) festina_fail("out of memory loading audio");
    *(int64_t *)raw = 1;
    FestinaAudio *a = (FestinaAudio *)(raw + sizeof(int64_t));
    a->samples = samples;
    a->frame_count = frames;
    a->channels = channels;
    a->sample_rate = rate;
    /* claude.md #101: kept so a `file:aud` table column round-trips
     * byte for byte rather than being re-encoded -- an MP3 stays an
     * MP3 rather than becoming a much larger WAV. */
    a->bytes = malloc((size_t)len);
    if (!a->bytes) festina_fail("out of memory loading audio");
    memcpy(a->bytes, bytes, (size_t)len);
    a->byte_count = (size_t)len;
    /* claude.md #110: empty rather than NULL, so the shared
     * festina_save_bytes never has to special-case it. festina_load_audio
     * replaces this with the real path. */
    a->path = strdup("");
    if (!a->path) festina_fail("out of memory loading audio");
    return a;
}

/* claude.md #101/#118: the aud counterpart of festina_blob_release --
 * decrement, and only on the last reference destroy the clip. A clip
 * that other bindings still reference keeps playing untouched; only
 * genuine destruction stops every channel still streaming it first,
 * which costs nothing and turns "freed while a thread is still
 * streaming it" from a crash into an impossibility. */
void festina_audio_free(void *audio) {
    FestinaAudio *a = (FestinaAudio *)audio;
    if (!a) return;
    if (!festina_release_check(audio)) return;
    pthread_mutex_lock(&g_audio_lock);
    int busy = 0;
    for (int i = 0; i < FESTINA_AUDIO_PLAYER_CAP; i++) {
        if (g_channels[i].clip == a) { g_channels[i].stop_requested = 1; busy = 1; }
    }
    if (busy) {
        for (int i = 0; i < FESTINA_AUDIO_PLAYER_CAP; i++) {
            if (g_channels[i].clip == a) festina_audio_halt_locked(&g_channels[i], 1);
        }
    }
    pthread_mutex_unlock(&g_audio_lock);
    free(a->samples);
    free(a->bytes);
    free(a->path);   /* claude.md #110 */
    free((char *)audio - sizeof(int64_t));
}

/* claude.md #110: writes the clip's own encoded bytes -- so an MP3 saves
 * as an MP3 rather than being re-encoded, the same property that makes
 * a `file:aud` column round-trip byte for byte (claude.md #101). */
int8_t festina_audio_save(void *audio, const char *target) {
    if (!audio) return 0;
    FestinaAudio *a = (FestinaAudio *)audio;
    return festina_save_bytes(target, &a->path, a->bytes,
                              (int64_t)a->byte_count, "aud", 1);
}

int8_t festina_audio_save_copy(void *audio, const char *target) {
    if (!audio) return 0;
    FestinaAudio *a = (FestinaAudio *)audio;
    return festina_save_bytes(target, &a->path, a->bytes,
                              (int64_t)a->byte_count, "aud", 0);
}

const void *festina_audio_bytes(void *audio, int64_t *out_len) {
    FestinaAudio *a = (FestinaAudio *)audio;
    if (out_len) *out_len = 0;
    if (!a) return NULL;
    if (out_len) *out_len = (int64_t)a->byte_count;
    return a->bytes;
}

void *festina_load_audio(const char *path) {
    if (!path) path = "";
    FILE *f = fopen(path, "rb");
    if (!f) {
        char msg[512];
        snprintf(msg, sizeof(msg), "could not open audio file '%s': %s", path, strerror(errno));
        festina_fail(msg);
    }
    if (fseek(f, 0, SEEK_END) != 0) { fclose(f); festina_fail("could not read audio file"); }
    long size = ftell(f);
    if (size < 0) { fclose(f); festina_fail("could not read audio file"); }
    rewind(f);
    unsigned char *data = malloc((size_t)size ? (size_t)size : 1);
    if (!data) { fclose(f); festina_fail("out of memory loading audio"); }
    size_t got = fread(data, 1, (size_t)size, f);
    fclose(f);
    if (got != (size_t)size) {
        free(data);
        char msg[512];
        snprintf(msg, sizeof(msg), "could not read audio file '%s'", path);
        festina_fail(msg);
    }
    void *clip = festina_audio_from_bytes(data, (int64_t)size, path);
    free(data);
    /* claude.md #110: set here rather than in festina_audio_from_bytes,
     * because that entry point is also how a database column becomes a
     * clip -- and one of those genuinely has no path. */
    FestinaAudio *loaded = (FestinaAudio *)clip;
    free(loaded->path);
    loaded->path = strdup(path);
    if (!loaded->path) festina_fail("out of memory loading audio");
    return clip;
}

/* claude.md #171 (extends claude.md #165's `.callback()` to `aud`):
 * an empty, silent clip -- 1 channel, a sane default rate, zero
 * frames -- exactly as "unpopulated" as festina_image_load_dispatch's
 * own 1x1 transparent placeholder, or a background blob load's own
 * empty bytes/length. play() on it is harmless: festina_audio_thread_main's
 * very first `frame >= a->frame_count` check (0 >= 0) is true
 * immediately, so it opens a device, plays nothing, and closes again --
 * never the "channels/rate are both 0" shape that would otherwise risk
 * festina_pcm_open() itself failing and calling festina_fail() on
 * something that was only ever "not loaded yet", not a real error. */
static void *festina_audio_placeholder(void) {
    char *raw = calloc(1, sizeof(int64_t) + sizeof(FestinaAudio));
    if (!raw) festina_fail("out of memory allocating an audio clip");
    *(int64_t *)raw = 1;
    FestinaAudio *a = (FestinaAudio *)(raw + sizeof(int64_t));
    a->samples = NULL;
    a->frame_count = 0;
    a->channels = 1;
    a->sample_rate = 44100;
    a->bytes = NULL;
    a->byte_count = 0;
    a->path = strdup("");
    if (!a->path) festina_fail("out of memory allocating an audio clip");
    return a;
}

/* claude.md #171: runs on a background worker thread (see
 * festina_runtime_async.c) -- reads and decodes `a->path` (already set,
 * at construction time, by festina_audio_load_dispatch below) and
 * fills in samples/frame_count/channels/sample_rate/bytes/byte_count IN
 * PLACE, mutating the same clip the caller already got back
 * immediately. Matches festina_blob_load_worker's own contract exactly:
 * NEVER calls festina_fail() except on genuine out-of-memory (the one
 * failure category festina_decode_wav/festina_decode_mp3 already
 * treat as fatal everywhere, worker thread or not -- see
 * festina_runtime_async.c's own top comment) -- a missing file, an
 * unrecognized format, and genuinely corrupt WAV/MP3 data are all just
 * "stays the empty placeholder", the graceful outcome festina_load_audio's
 * synchronous, festina_fail()-on-any-of-those-three contract deliberately
 * does NOT give a caller that has no chance to catch it. */
static void festina_audio_load_worker(void *payload) {
    FestinaAudio *a = (FestinaAudio *)payload;
    if (!a->path || !*a->path) return;
    FILE *f = fopen(a->path, "rb");
    if (!f) return;
    if (fseek(f, 0, SEEK_END) != 0) { fclose(f); return; }
    long size = ftell(f);
    if (size < 0) { fclose(f); return; }
    rewind(f);
    unsigned char *data = malloc((size_t)size ? (size_t)size : 1);
    if (!data) { fclose(f); festina_fail("out of memory loading audio"); }
    size_t got = fread(data, 1, (size_t)size, f);
    fclose(f);
    if (got != (size_t)size) { free(data); return; }

    int16_t *samples = NULL;
    size_t frames = 0;
    int channels = 0;
    unsigned int rate = 0;
    int ok;
    if ((size_t)size >= 12 && memcmp(data, "RIFF", 4) == 0 && memcmp(data + 8, "WAVE", 4) == 0) {
        ok = festina_decode_wav(data, (size_t)size, &samples, &frames, &channels, &rate);
    } else {
        ok = festina_decode_mp3(data, (size_t)size, &samples, &frames, &channels, &rate);
    }
    if (!ok) { free(data); return; }

    free(a->samples);
    a->samples = samples;
    a->frame_count = frames;
    a->channels = channels;
    a->sample_rate = rate;
    free(a->bytes);
    a->bytes = data;
    a->byte_count = (size_t)size;
}

/* claude.md #171: codegen's own entry point for a `.callback()`-carrying
 * aud construction, mirroring festina_blob_load_dispatch/
 * festina_image_load_dispatch exactly -- NULL callback is the unchanged,
 * fully synchronous festina_load_audio path; non-NULL returns the
 * placeholder above immediately (path already correct) and hands the
 * real load to the async-io pool. */
void *festina_audio_load_dispatch(const char *path, void (*callback)(void *)) {
    if (!callback) return festina_load_audio(path);
    if (!path) path = "";
    void *clip = festina_audio_placeholder();
    FestinaAudio *a = (FestinaAudio *)clip;
    free(a->path);
    a->path = strdup(path);
    if (!a->path) festina_fail("out of memory allocating an audio clip");
    festina_retain(clip);
    festina_async_io_dispatch(clip, festina_audio_load_worker, callback, festina_audio_free);
    return clip;
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
/* claude.md #109: returns the channel it actually played on, so a
 * program can address the playback it just started -- `int ch =
 * gunshot.play()` then `stopAudioPlayer(ch)`. Automatic assignment
 * chooses a channel the caller cannot otherwise learn, which made the
 * pool addressable only by re-specifying a channel by hand and so
 * defeating the pool. -1 means nothing was played: a null clip, or
 * every channel reserved by playLoop with none left to claim. */
int64_t festina_audio_play_on(void *audio, int64_t channel, int8_t explicit_channel,
                               int8_t looping) {
    FestinaAudio *a = (FestinaAudio *)audio;
    if (!a) return -1;

    pthread_mutex_lock(&g_audio_lock);
    FestinaChannel *ch = festina_audio_claim_channel(channel, explicit_channel);
    if (!ch) {
        /* Every channel is reserved -- see festina_audio_claim_channel. */
        pthread_mutex_unlock(&g_audio_lock);
        return -1;
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
     * audio device at all, which is what the error below claims.
     * (claude.md #121: through the festina_pcm_* seam now; on backends
     * that always software-mix -- CoreAudio, the null sink -- the first
     * open simply succeeds and this loop degenerates.) */
    FestinaPcm *pcm = NULL;
    char errbuf[192];
    errbuf[0] = '\0';
    for (;;) {
        pcm = festina_pcm_open(a->channels, a->sample_rate, errbuf, sizeof(errbuf));
        if (pcm) break;
        if (!festina_audio_free_oldest_locked(ch)) break;
    }
    if (!pcm) {
        pthread_mutex_unlock(&g_audio_lock);
        char msg[256];
        snprintf(msg, sizeof(msg),
                 "could not open an audio output device: %s (is any audio device available?)",
                 errbuf);
        festina_fail(msg);
        return -1; /* unreachable -- festina_fail() calls exit() */
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
    int rc = pthread_create(&ch->thread, NULL, festina_audio_thread_main, ch);
    if (rc != 0) {
        festina_pcm_close(pcm);
        ch->pcm = NULL;
        ch->active = 0;
        ch->locked = 0;
        pthread_mutex_unlock(&g_audio_lock);
        festina_fail("could not start an audio playback thread");
        return -1; /* unreachable -- festina_fail() calls exit() */
    }
    ch->joinable = 1;
    int64_t chosen = (int64_t)(ch - g_channels);
    pthread_mutex_unlock(&g_audio_lock);
    return chosen;
}

/* claude.md #109: aud.stop() is BACK, and means what claude.md #100
 * said its only honest reading was -- stop every channel playing this
 * clip. #100 removed it on the grounds that this is almost never what
 * a program firing overlapping effects wants, which is true and is
 * also not a reason to withhold it: "silence this sound, wherever it
 * is" is a real thing to want (a looping engine hum, a dialogue line,
 * a music bed on more than one channel), and the alternative was
 * tracking channel numbers by hand for something the runtime already
 * knows. play()/playLoop() returning their channel is what covers the
 * other case, so the two now sit side by side rather than one
 * standing in for the other.
 *
 * Joins rather than merely signalling, exactly like
 * festina_stop_audio_player, so every voice of this clip is
 * guaranteed idle the instant this returns. Stopping a clip that is
 * not playing is a safe no-op. */
void festina_audio_stop_clip(void *audio) {
    FestinaAudio *a = (FestinaAudio *)audio;
    if (!a) return;
    pthread_mutex_lock(&g_audio_lock);
    int busy = 0;
    for (int i = 0; i < FESTINA_AUDIO_PLAYER_CAP; i++) {
        if (g_channels[i].clip == a && g_channels[i].active) {
            g_channels[i].stop_requested = 1;
            busy = 1;
        }
    }
    if (busy) {
        for (int i = 0; i < FESTINA_AUDIO_PLAYER_CAP; i++) {
            /* release=1: a channel this clip had RESERVED via playLoop
             * goes back to the pool, the same as stopAudioPlayer(n)
             * would do for it. Stopping the sound and leaving its
             * reservation standing would quietly shrink the pool. */
            if (g_channels[i].clip == a) festina_audio_halt_locked(&g_channels[i], 1);
        }
    }
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

/* todo.md's own long-standing gap: play()/playLoop() hand back the
 * channel they used (claude.md #109), and festina_audio_is_playing()
 * above answers "is this CLIP playing anywhere", but there was no way
 * to ask about the CHANNEL itself -- the one thing a program actually
 * has in hand after `int ch = engine.play()` once it no longer has a
 * live reference to which clip is on it (a different clip may since
 * have taken the channel over via play(ch)/playLoop(ch), stealing, or
 * automatic reassignment after this one finished). Clamped into
 * [0, 64) exactly like festina_stop_audio_player and every other
 * channel-accepting call, so a bad channel number answers "no" rather
 * than crashing the program -- the same "a tuning/addressing mistake
 * should not kill a running program" rule this whole pool already
 * applies everywhere else. */
int8_t festina_channel_is_playing(int64_t channel) {
    pthread_mutex_lock(&g_audio_lock);
    int8_t playing = g_channels[festina_clamp_channel(channel)].active;
    pthread_mutex_unlock(&g_audio_lock);
    return playing;
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
