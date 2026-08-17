/*
 * CDP Library - File I/O Implementation
 * Copyright (c) 2024 Composers Desktop Project Ltd
 * SPDX-License-Identifier: LGPL-2.1-or-later
 *
 * Minimal WAV file reader/writer. Supports:
 * - PCM 16-bit, 24-bit, 32-bit integer
 * - 32-bit float
 * - Mono and stereo (and multichannel)
 */

#include "cdp.h"
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

/* Internal error setter (defined in context.c) */
extern void cdp_set_error(cdp_context* ctx, cdp_error err, const char* fmt, ...);

/* WAV format constants */
#define WAVE_FORMAT_PCM        0x0001
#define WAVE_FORMAT_IEEE_FLOAT 0x0003
#define WAVE_FORMAT_EXTENSIBLE 0xFFFE

/* Helper to read little-endian values */
static uint16_t read_u16_le(const uint8_t* p) {
    return (uint16_t)p[0] | ((uint16_t)p[1] << 8);
}

static uint32_t read_u32_le(const uint8_t* p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

/* Helper to write little-endian values */
static void write_u16_le(uint8_t* p, uint16_t v) {
    p[0] = (uint8_t)(v & 0xFF);
    p[1] = (uint8_t)((v >> 8) & 0xFF);
}

static void write_u32_le(uint8_t* p, uint32_t v) {
    p[0] = (uint8_t)(v & 0xFF);
    p[1] = (uint8_t)((v >> 8) & 0xFF);
    p[2] = (uint8_t)((v >> 16) & 0xFF);
    p[3] = (uint8_t)((v >> 24) & 0xFF);
}

/* Convert 24-bit signed to float */
static float int24_to_float(const uint8_t* p) {
    int32_t val = (int32_t)p[0] | ((int32_t)p[1] << 8) | ((int32_t)p[2] << 16);
    /* Sign extend from 24-bit */
    if (val & 0x800000) {
        val |= 0xFF000000;
    }
    return (float)val / 8388608.0f;  /* 2^23 */
}

/* Convert float to 24-bit signed.
 *
 * The NaN case is not hypothetical padding: a comparison-based clamp cannot
 * reject NaN (both `v > 1.0f` and `v < -1.0f` are false for it), so it would
 * reach the cast, and converting NaN to an integer is undefined behaviour.
 * Silence is the only defensible substitute -- there is no sample value it
 * means. */
static void float_to_int24(uint8_t* p, float v) {
    if (!(v == v)) v = 0.0f;  /* NaN */
    /* Clamp to [-1, 1]; also catches +/-Inf */
    if (v > 1.0f) v = 1.0f;
    if (v < -1.0f) v = -1.0f;
    int32_t val = (int32_t)(v * 8388607.0f);  /* 2^23 - 1 */
    p[0] = (uint8_t)(val & 0xFF);
    p[1] = (uint8_t)((val >> 8) & 0xFF);
    p[2] = (uint8_t)((val >> 16) & 0xFF);
}

/*============================================================================
 * WAV Reading
 *============================================================================*/

cdp_buffer* cdp_read_file(cdp_context* ctx, const char* path)
{
    FILE* f = NULL;
    cdp_buffer* buf = NULL;
    uint8_t header[44];
    uint8_t chunk_header[8];
    uint8_t* raw_data = NULL;

    if (!path) {
        if (ctx) cdp_set_error(ctx, CDP_ERROR_INVALID_ARG, "Path is NULL");
        return NULL;
    }

    f = fopen(path, "rb");
    if (!f) {
        if (ctx) cdp_set_error(ctx, CDP_ERROR_IO, "Cannot open file: %s", path);
        return NULL;
    }

    /* Actual file length, used below to reject a data chunk that claims to be
     * larger than the file. Without it, four attacker-controlled bytes request
     * an arbitrary allocation: a 100-byte file declaring data_size 0xFFFFFFF0
     * attempts a 4 GB malloc before the short read is noticed. */
    long file_size = -1;
    if (fseek(f, 0, SEEK_END) == 0) {
        file_size = ftell(f);
    }
    if (fseek(f, 0, SEEK_SET) != 0) {
        if (ctx) cdp_set_error(ctx, CDP_ERROR_IO, "Cannot seek file: %s", path);
        goto error;
    }

    /* Read RIFF header (12 bytes) */
    if (fread(header, 1, 12, f) != 12) {
        if (ctx) cdp_set_error(ctx, CDP_ERROR_FORMAT, "Cannot read RIFF header");
        goto error;
    }

    /* Verify RIFF/WAVE */
    if (memcmp(header, "RIFF", 4) != 0 || memcmp(header + 8, "WAVE", 4) != 0) {
        if (ctx) cdp_set_error(ctx, CDP_ERROR_FORMAT, "Not a WAV file");
        goto error;
    }

    /* Find fmt chunk */
    uint16_t audio_format = 0;
    uint16_t num_channels = 0;
    uint32_t sample_rate = 0;
    uint16_t bits_per_sample = 0;
    int found_fmt = 0;

    while (fread(chunk_header, 1, 8, f) == 8) {
        uint32_t chunk_size = read_u32_le(chunk_header + 4);

        if (memcmp(chunk_header, "fmt ", 4) == 0) {
            if (chunk_size < 16) {
                if (ctx) cdp_set_error(ctx, CDP_ERROR_FORMAT, "Invalid fmt chunk");
                goto error;
            }

            uint8_t fmt[40];
            size_t to_read = chunk_size < 40 ? chunk_size : 40;
            if (fread(fmt, 1, to_read, f) != to_read) {
                if (ctx) cdp_set_error(ctx, CDP_ERROR_FORMAT, "Cannot read fmt chunk");
                goto error;
            }

            audio_format = read_u16_le(fmt);
            num_channels = read_u16_le(fmt + 2);
            sample_rate = read_u32_le(fmt + 4);
            bits_per_sample = read_u16_le(fmt + 14);

            /* Handle WAVE_FORMAT_EXTENSIBLE */
            if (audio_format == WAVE_FORMAT_EXTENSIBLE && chunk_size >= 40) {
                /* Actual format is in SubFormat GUID (first 2 bytes) */
                audio_format = read_u16_le(fmt + 24);
            }

            /* Skip rest of chunk if any (see the unknown-chunk branch below
             * for why the range check matters on 32-bit `long`). */
            if (chunk_size > to_read) {
                uint32_t skip = chunk_size - (uint32_t)to_read;
                if (skip > (uint32_t)LONG_MAX ||
                    fseek(f, (long)skip, SEEK_CUR) != 0) {
                    if (ctx) cdp_set_error(ctx, CDP_ERROR_FORMAT,
                        "Truncated fmt chunk");
                    goto error;
                }
            }

            found_fmt = 1;
        }
        else if (memcmp(chunk_header, "data", 4) == 0) {
            if (!found_fmt) {
                if (ctx) cdp_set_error(ctx, CDP_ERROR_FORMAT, "data chunk before fmt chunk");
                goto error;
            }

            /* Validate format */
            if (audio_format != WAVE_FORMAT_PCM && audio_format != WAVE_FORMAT_IEEE_FLOAT) {
                if (ctx) cdp_set_error(ctx, CDP_ERROR_FORMAT,
                    "Unsupported audio format: %d", audio_format);
                goto error;
            }

            if (bits_per_sample != 16 && bits_per_sample != 24 &&
                bits_per_sample != 32) {
                if (ctx) cdp_set_error(ctx, CDP_ERROR_FORMAT,
                    "Unsupported bit depth: %d", bits_per_sample);
                goto error;
            }

            /* num_channels and sample_rate come straight out of the file and
             * must be checked before they are used to compute anything.
             *
             * Zero channels is the dangerous one: frame_size becomes 0 and the
             * division below is integer division by zero, which raises SIGFPE
             * on x86-64 and takes the whole process down. cdp_buffer_create
             * rejects channels <= 0, but that is one line too late.
             *
             * An absurd-but-positive sample rate is the other: it survives the
             * cast to int and then scales delay lines and output lengths in
             * every downstream operation -- a 4 KB file declaring 2e9 Hz makes
             * reverb ask for several gigabytes. */
            if (num_channels < 1 || num_channels > CDP_MAX_WAV_CHANNELS) {
                if (ctx) cdp_set_error(ctx, CDP_ERROR_FORMAT,
                    "Unsupported channel count: %u", (unsigned)num_channels);
                goto error;
            }
            if (sample_rate < CDP_MIN_WAV_SAMPLE_RATE ||
                sample_rate > CDP_MAX_WAV_SAMPLE_RATE) {
                if (ctx) cdp_set_error(ctx, CDP_ERROR_FORMAT,
                    "Unsupported sample rate: %u Hz", (unsigned)sample_rate);
                goto error;
            }

            /* A declared data size larger than what is left in the file is
             * either corruption or an amplification attempt; either way there
             * is no reason to allocate for it. */
            if (file_size >= 0) {
                long here = ftell(f);
                if (here >= 0 && (unsigned long)chunk_size >
                                 (unsigned long)(file_size - here)) {
                    if (ctx) cdp_set_error(ctx, CDP_ERROR_FORMAT,
                        "data chunk declares %u bytes but only %ld remain",
                        (unsigned)chunk_size, file_size - here);
                    goto error;
                }
            }

            /* Calculate samples */
            uint32_t bytes_per_sample = bits_per_sample / 8;
            uint32_t frame_size = bytes_per_sample * num_channels;
            size_t frame_count = chunk_size / frame_size;
            size_t sample_count = frame_count * num_channels;

            /* Create buffer */
            buf = cdp_buffer_create(frame_count, num_channels, (int)sample_rate);
            if (!buf) {
                if (ctx) cdp_set_error(ctx, CDP_ERROR_MEMORY, "Cannot allocate buffer");
                goto error;
            }

            /* Read raw data */
            raw_data = (uint8_t*)malloc(chunk_size);
            if (!raw_data) {
                if (ctx) cdp_set_error(ctx, CDP_ERROR_MEMORY, "Cannot allocate read buffer");
                goto error;
            }

            if (fread(raw_data, 1, chunk_size, f) != chunk_size) {
                if (ctx) cdp_set_error(ctx, CDP_ERROR_IO, "Cannot read audio data");
                goto error;
            }

            /* Convert to float */
            float* samples = buf->samples;
            const uint8_t* src = raw_data;

            if (audio_format == WAVE_FORMAT_IEEE_FLOAT && bits_per_sample == 32) {
                /* 32-bit float - direct copy */
                memcpy(samples, raw_data, sample_count * sizeof(float));
            }
            else if (audio_format == WAVE_FORMAT_PCM) {
                if (bits_per_sample == 16) {
                    /* 16-bit PCM */
                    for (size_t i = 0; i < sample_count; i++) {
                        int16_t val = (int16_t)read_u16_le(src);
                        samples[i] = (float)val / 32768.0f;
                        src += 2;
                    }
                }
                else if (bits_per_sample == 24) {
                    /* 24-bit PCM */
                    for (size_t i = 0; i < sample_count; i++) {
                        samples[i] = int24_to_float(src);
                        src += 3;
                    }
                }
                else if (bits_per_sample == 32) {
                    /* 32-bit PCM */
                    for (size_t i = 0; i < sample_count; i++) {
                        int32_t val = (int32_t)read_u32_le(src);
                        samples[i] = (float)val / 2147483648.0f;  /* 2^31 */
                        src += 4;
                    }
                }
            }

            free(raw_data);
            raw_data = NULL;
            fclose(f);
            return buf;
        }
        else {
            /* Skip unknown chunk.
             *
             * chunk_size is uint32 and `long` is 32-bit on Windows, so a chunk
             * declaring more than LONG_MAX bytes would cast to a negative
             * offset and seek *backwards* -- the scan would then re-read the
             * same bytes forever. Reject rather than seek. */
            if (chunk_size > (uint32_t)LONG_MAX) {
                if (ctx) cdp_set_error(ctx, CDP_ERROR_FORMAT,
                    "Chunk size out of range: %u", (unsigned)chunk_size);
                goto error;
            }
            if (fseek(f, (long)chunk_size, SEEK_CUR) != 0) {
                if (ctx) cdp_set_error(ctx, CDP_ERROR_FORMAT,
                    "Truncated chunk");
                goto error;
            }
        }
    }

    if (ctx) cdp_set_error(ctx, CDP_ERROR_FORMAT, "No data chunk found");

error:
    if (raw_data) free(raw_data);
    if (buf) cdp_buffer_destroy(buf);
    if (f) fclose(f);
    return NULL;
}

/*============================================================================
 * WAV Writing
 *============================================================================*/

cdp_error cdp_write_file(cdp_context* ctx, const char* path, const cdp_buffer* buf)
{
    if (!path) {
        if (ctx) cdp_set_error(ctx, CDP_ERROR_INVALID_ARG, "Path is NULL");
        return CDP_ERROR_INVALID_ARG;
    }
    if (!buf || !buf->samples) {
        if (ctx) cdp_set_error(ctx, CDP_ERROR_INVALID_ARG, "Invalid buffer");
        return CDP_ERROR_INVALID_ARG;
    }

    FILE* f = fopen(path, "wb");
    if (!f) {
        if (ctx) cdp_set_error(ctx, CDP_ERROR_IO, "Cannot create file: %s", path);
        return CDP_ERROR_IO;
    }

    /* Write as 32-bit float WAV */
    uint32_t num_channels = (uint32_t)buf->info.channels;
    uint32_t sample_rate = (uint32_t)buf->info.sample_rate;
    uint32_t bits_per_sample = 32;
    uint32_t bytes_per_sample = bits_per_sample / 8;
    uint32_t block_align = num_channels * bytes_per_sample;
    uint32_t byte_rate = sample_rate * block_align;
    /* A WAV chunk size is 32 bits. Past that the cast silently truncates and
     * the file is written "successfully" with a header that understates its
     * own payload -- a corrupt file and a zero exit status, which is worse
     * than an error. */
    if (buf->sample_count > (size_t)(UINT32_MAX - 36) / bytes_per_sample) {
        if (ctx) cdp_set_error(ctx, CDP_ERROR_DATA,
            "Buffer is too large for a WAV file (%zu samples)",
            buf->sample_count);
        fclose(f);
        return CDP_ERROR_DATA;
    }
    uint32_t data_size = (uint32_t)(buf->sample_count * bytes_per_sample);
    uint32_t file_size = 36 + data_size;

    uint8_t header[44];

    /* RIFF header */
    memcpy(header, "RIFF", 4);
    write_u32_le(header + 4, file_size);
    memcpy(header + 8, "WAVE", 4);

    /* fmt chunk */
    memcpy(header + 12, "fmt ", 4);
    write_u32_le(header + 16, 16);  /* chunk size */
    write_u16_le(header + 20, WAVE_FORMAT_IEEE_FLOAT);
    write_u16_le(header + 22, (uint16_t)num_channels);
    write_u32_le(header + 24, sample_rate);
    write_u32_le(header + 28, byte_rate);
    write_u16_le(header + 32, (uint16_t)block_align);
    write_u16_le(header + 34, (uint16_t)bits_per_sample);

    /* data chunk header */
    memcpy(header + 36, "data", 4);
    write_u32_le(header + 40, data_size);

    /* Write header */
    if (fwrite(header, 1, 44, f) != 44) {
        if (ctx) cdp_set_error(ctx, CDP_ERROR_IO, "Cannot write WAV header");
        fclose(f);
        return CDP_ERROR_IO;
    }

    /* Write samples (already float32, just write directly) */
    if (fwrite(buf->samples, sizeof(float), buf->sample_count, f) != buf->sample_count) {
        if (ctx) cdp_set_error(ctx, CDP_ERROR_IO, "Cannot write audio data");
        fclose(f);
        return CDP_ERROR_IO;
    }

    fclose(f);

    if (ctx) cdp_clear_error(ctx);
    return CDP_OK;
}

/*============================================================================
 * WAV Writing (16-bit PCM)
 *============================================================================*/

cdp_error cdp_write_file_pcm16(cdp_context* ctx, const char* path, const cdp_buffer* buf)
{
    if (!path) {
        if (ctx) cdp_set_error(ctx, CDP_ERROR_INVALID_ARG, "Path is NULL");
        return CDP_ERROR_INVALID_ARG;
    }
    if (!buf || !buf->samples) {
        if (ctx) cdp_set_error(ctx, CDP_ERROR_INVALID_ARG, "Invalid buffer");
        return CDP_ERROR_INVALID_ARG;
    }

    FILE* f = fopen(path, "wb");
    if (!f) {
        if (ctx) cdp_set_error(ctx, CDP_ERROR_IO, "Cannot create file: %s", path);
        return CDP_ERROR_IO;
    }

    /* Write as 16-bit PCM WAV */
    uint32_t num_channels = (uint32_t)buf->info.channels;
    uint32_t sample_rate = (uint32_t)buf->info.sample_rate;
    uint32_t bits_per_sample = 16;
    uint32_t bytes_per_sample = bits_per_sample / 8;
    uint32_t block_align = num_channels * bytes_per_sample;
    uint32_t byte_rate = sample_rate * block_align;
    /* A WAV chunk size is 32 bits. Past that the cast silently truncates and
     * the file is written "successfully" with a header that understates its
     * own payload -- a corrupt file and a zero exit status, which is worse
     * than an error. */
    if (buf->sample_count > (size_t)(UINT32_MAX - 36) / bytes_per_sample) {
        if (ctx) cdp_set_error(ctx, CDP_ERROR_DATA,
            "Buffer is too large for a WAV file (%zu samples)",
            buf->sample_count);
        fclose(f);
        return CDP_ERROR_DATA;
    }
    uint32_t data_size = (uint32_t)(buf->sample_count * bytes_per_sample);
    uint32_t file_size = 36 + data_size;

    uint8_t header[44];

    /* RIFF header */
    memcpy(header, "RIFF", 4);
    write_u32_le(header + 4, file_size);
    memcpy(header + 8, "WAVE", 4);

    /* fmt chunk */
    memcpy(header + 12, "fmt ", 4);
    write_u32_le(header + 16, 16);  /* chunk size */
    write_u16_le(header + 20, WAVE_FORMAT_PCM);
    write_u16_le(header + 22, (uint16_t)num_channels);
    write_u32_le(header + 24, sample_rate);
    write_u32_le(header + 28, byte_rate);
    write_u16_le(header + 32, (uint16_t)block_align);
    write_u16_le(header + 34, (uint16_t)bits_per_sample);

    /* data chunk header */
    memcpy(header + 36, "data", 4);
    write_u32_le(header + 40, data_size);

    /* Write header */
    if (fwrite(header, 1, 44, f) != 44) {
        if (ctx) cdp_set_error(ctx, CDP_ERROR_IO, "Cannot write WAV header");
        fclose(f);
        return CDP_ERROR_IO;
    }

    /* Convert and write samples */
    const float* samples = buf->samples;
    size_t sample_count = buf->sample_count;

    /* Write in chunks to avoid huge allocations */
#define CHUNK_SIZE_PCM16 4096
    int16_t chunk[CHUNK_SIZE_PCM16];

    for (size_t i = 0; i < sample_count; i += CHUNK_SIZE_PCM16) {
        size_t count = (i + CHUNK_SIZE_PCM16 <= sample_count) ? CHUNK_SIZE_PCM16 : (sample_count - i);

        for (size_t j = 0; j < count; j++) {
            float v = samples[i + j];
            if (!(v == v)) v = 0.0f;  /* NaN: see float_to_int24 */
            /* Clamp to [-1, 1]; also catches +/-Inf */
            if (v > 1.0f) v = 1.0f;
            if (v < -1.0f) v = -1.0f;
            chunk[j] = (int16_t)(v * 32767.0f);
        }

        if (fwrite(chunk, sizeof(int16_t), count, f) != count) {
            if (ctx) cdp_set_error(ctx, CDP_ERROR_IO, "Cannot write audio data");
            fclose(f);
            return CDP_ERROR_IO;
        }
    }

    fclose(f);

    if (ctx) cdp_clear_error(ctx);
    return CDP_OK;
}

/*============================================================================
 * WAV Writing (24-bit PCM)
 *============================================================================*/

cdp_error cdp_write_file_pcm24(cdp_context* ctx, const char* path, const cdp_buffer* buf)
{
    if (!path) {
        if (ctx) cdp_set_error(ctx, CDP_ERROR_INVALID_ARG, "Path is NULL");
        return CDP_ERROR_INVALID_ARG;
    }
    if (!buf || !buf->samples) {
        if (ctx) cdp_set_error(ctx, CDP_ERROR_INVALID_ARG, "Invalid buffer");
        return CDP_ERROR_INVALID_ARG;
    }

    FILE* f = fopen(path, "wb");
    if (!f) {
        if (ctx) cdp_set_error(ctx, CDP_ERROR_IO, "Cannot create file: %s", path);
        return CDP_ERROR_IO;
    }

    /* Write as 24-bit PCM WAV */
    uint32_t num_channels = (uint32_t)buf->info.channels;
    uint32_t sample_rate = (uint32_t)buf->info.sample_rate;
    uint32_t bits_per_sample = 24;
    uint32_t bytes_per_sample = 3;
    uint32_t block_align = num_channels * bytes_per_sample;
    uint32_t byte_rate = sample_rate * block_align;
    /* A WAV chunk size is 32 bits. Past that the cast silently truncates and
     * the file is written "successfully" with a header that understates its
     * own payload -- a corrupt file and a zero exit status, which is worse
     * than an error. */
    if (buf->sample_count > (size_t)(UINT32_MAX - 36) / bytes_per_sample) {
        if (ctx) cdp_set_error(ctx, CDP_ERROR_DATA,
            "Buffer is too large for a WAV file (%zu samples)",
            buf->sample_count);
        fclose(f);
        return CDP_ERROR_DATA;
    }
    uint32_t data_size = (uint32_t)(buf->sample_count * bytes_per_sample);
    uint32_t file_size = 36 + data_size;

    uint8_t header[44];

    /* RIFF header */
    memcpy(header, "RIFF", 4);
    write_u32_le(header + 4, file_size);
    memcpy(header + 8, "WAVE", 4);

    /* fmt chunk */
    memcpy(header + 12, "fmt ", 4);
    write_u32_le(header + 16, 16);  /* chunk size */
    write_u16_le(header + 20, WAVE_FORMAT_PCM);
    write_u16_le(header + 22, (uint16_t)num_channels);
    write_u32_le(header + 24, sample_rate);
    write_u32_le(header + 28, byte_rate);
    write_u16_le(header + 32, (uint16_t)block_align);
    write_u16_le(header + 34, (uint16_t)bits_per_sample);

    /* data chunk header */
    memcpy(header + 36, "data", 4);
    write_u32_le(header + 40, data_size);

    /* Write header */
    if (fwrite(header, 1, 44, f) != 44) {
        if (ctx) cdp_set_error(ctx, CDP_ERROR_IO, "Cannot write WAV header");
        fclose(f);
        return CDP_ERROR_IO;
    }

    /* Convert and write samples */
    const float* samples = buf->samples;
    size_t sample_count = buf->sample_count;

    /* Write in chunks */
#define CHUNK_SAMPLES_PCM24 4096
    uint8_t chunk[CHUNK_SAMPLES_PCM24 * 3];

    for (size_t i = 0; i < sample_count; i += CHUNK_SAMPLES_PCM24) {
        size_t count = (i + CHUNK_SAMPLES_PCM24 <= sample_count) ? CHUNK_SAMPLES_PCM24 : (sample_count - i);

        for (size_t j = 0; j < count; j++) {
            float_to_int24(chunk + j * 3, samples[i + j]);
        }

        if (fwrite(chunk, 3, count, f) != count) {
            if (ctx) cdp_set_error(ctx, CDP_ERROR_IO, "Cannot write audio data");
            fclose(f);
            return CDP_ERROR_IO;
        }
    }

    fclose(f);

    if (ctx) cdp_clear_error(ctx);
    return CDP_OK;
}
