/*
 * CDP Library - Main Public API
 * Copyright (c) 2024 Composers Desktop Project Ltd
 * SPDX-License-Identifier: LGPL-2.1-or-later
 *
 * A library for audio processing based on the Composers Desktop Project.
 */

#ifndef CDP_H
#define CDP_H

#include "cdp_error.h"
#include "cdp_types.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Library version */
#define CDP_VERSION_MAJOR 0
#define CDP_VERSION_MINOR 1
#define CDP_VERSION_PATCH 0

/**
 * Get library version string.
 */
const char* cdp_version(void);

/* Accepted domain for WAV header fields.
 *
 * cdp_read_file() parses untrusted input, so these are enforced rather than
 * assumed. The channel floor is load-bearing: a fmt chunk declaring zero
 * channels makes the frame size zero, and the frame-count division is then an
 * integer division by zero -- SIGFPE, and the process is gone. The sample-rate
 * ceiling stops a small file from scaling every downstream allocation, since
 * delay lines and output lengths are all proportional to it. */
#define CDP_MAX_WAV_CHANNELS      64
#define CDP_MIN_WAV_SAMPLE_RATE   1
#define CDP_MAX_WAV_SAMPLE_RATE   768000

/*============================================================================
 * Context Management
 *============================================================================*/

/**
 * Create a new CDP context.
 *
 * A context holds all state for a processing operation and must be
 * created before calling any other CDP functions.
 *
 * @return New context, or NULL on allocation failure.
 */
cdp_context* cdp_context_create(void);

/**
 * Destroy a CDP context and free all associated resources.
 *
 * @param ctx Context to destroy (may be NULL).
 */
void cdp_context_destroy(cdp_context* ctx);

/**
 * Get the last error code from a context.
 *
 * @param ctx Context to query.
 * @return Last error code, or CDP_OK if no error.
 */
cdp_error cdp_get_error(const cdp_context* ctx);

/**
 * Get the last error message from a context.
 *
 * @param ctx Context to query.
 * @return Error message string (valid until next CDP call on this context).
 */
const char* cdp_get_error_message(const cdp_context* ctx);

/**
 * Clear any error state in a context.
 *
 * @param ctx Context to clear.
 */
void cdp_clear_error(cdp_context* ctx);

/*============================================================================
 * Buffer Management
 *============================================================================*/

/**
 * Create a new audio buffer.
 *
 * @param frame_count Number of sample frames.
 * @param channels Number of channels.
 * @param sample_rate Sample rate in Hz.
 * @return New buffer, or NULL on allocation failure.
 */
cdp_buffer* cdp_buffer_create(size_t frame_count, int channels, int sample_rate);

/**
 * Create a buffer wrapping existing sample data (no copy).
 *
 * The caller retains ownership of the sample data and must ensure
 * it remains valid for the lifetime of the buffer.
 *
 * @param samples Pointer to sample data.
 * @param sample_count Total number of samples.
 * @param channels Number of channels.
 * @param sample_rate Sample rate in Hz.
 * @return New buffer, or NULL on failure.
 */
cdp_buffer* cdp_buffer_wrap(float* samples, size_t sample_count,
                            int channels, int sample_rate);

/**
 * Create a buffer by copying existing sample data.
 *
 * @param samples Pointer to sample data.
 * @param sample_count Total number of samples.
 * @param channels Number of channels.
 * @param sample_rate Sample rate in Hz.
 * @return New buffer, or NULL on allocation failure.
 */
cdp_buffer* cdp_buffer_copy(const float* samples, size_t sample_count,
                            int channels, int sample_rate);

/**
 * Destroy a buffer and free its resources.
 *
 * @param buf Buffer to destroy (may be NULL).
 */
void cdp_buffer_destroy(cdp_buffer* buf);

/**
 * Resize a buffer, preserving existing samples.
 *
 * @param buf Buffer to resize.
 * @param new_frame_count New frame count.
 * @return CDP_OK on success, error code on failure.
 */
cdp_error cdp_buffer_resize(cdp_buffer* buf, size_t new_frame_count);

/**
 * Clear a buffer (set all samples to zero).
 *
 * @param buf Buffer to clear.
 */
void cdp_buffer_clear(cdp_buffer* buf);

/*============================================================================
 * File I/O
 *============================================================================*/

/**
 * Read an audio file into a buffer.
 *
 * Currently supports WAV files (16/24/32-bit PCM and 32-bit float).
 *
 * @param ctx Context for error reporting.
 * @param path Path to audio file.
 * @return New buffer containing audio data, or NULL on error.
 */
cdp_buffer* cdp_read_file(cdp_context* ctx, const char* path);

/**
 * Write a buffer to a WAV file (32-bit float).
 *
 * @param ctx Context for error reporting.
 * @param path Path to output file.
 * @param buf Buffer to write.
 * @return CDP_OK on success, error code on failure.
 */
cdp_error cdp_write_file(cdp_context* ctx, const char* path,
                         const cdp_buffer* buf);

/**
 * Write a buffer to a WAV file (16-bit PCM).
 *
 * @param ctx Context for error reporting.
 * @param path Path to output file.
 * @param buf Buffer to write.
 * @return CDP_OK on success, error code on failure.
 */
cdp_error cdp_write_file_pcm16(cdp_context* ctx, const char* path,
                               const cdp_buffer* buf);

/**
 * Write a buffer to a WAV file (24-bit PCM).
 *
 * @param ctx Context for error reporting.
 * @param path Path to output file.
 * @param buf Buffer to write.
 * @return CDP_OK on success, error code on failure.
 */
cdp_error cdp_write_file_pcm24(cdp_context* ctx, const char* path,
                               const cdp_buffer* buf);

/*============================================================================
 * Gain / Amplitude Operations
 *============================================================================*/

/**
 * Apply constant gain to a buffer (in-place).
 *
 * @param ctx Context for error reporting.
 * @param buf Buffer to process.
 * @param gain Linear gain factor (1.0 = unity, 2.0 = +6dB, 0.5 = -6dB).
 * @param flags Processing flags (CDP_FLAG_CLIP to clip output).
 * @return CDP_OK on success, error code on failure.
 */
cdp_error cdp_gain(cdp_context* ctx, cdp_buffer* buf, double gain, cdp_flags flags);

/**
 * Apply gain in decibels to a buffer (in-place).
 *
 * @param ctx Context for error reporting.
 * @param buf Buffer to process.
 * @param gain_db Gain in decibels (0 = unity, +6 = double, -6 = half).
 * @param flags Processing flags.
 * @return CDP_OK on success, error code on failure.
 */
cdp_error cdp_gain_db(cdp_context* ctx, cdp_buffer* buf, double gain_db,
                      cdp_flags flags);

/**
 * Apply time-varying gain envelope to a buffer (in-place).
 *
 * @param ctx Context for error reporting.
 * @param buf Buffer to process.
 * @param points Array of breakpoints (time, gain pairs).
 * @param point_count Number of breakpoints.
 * @param flags Processing flags.
 * @return CDP_OK on success, error code on failure.
 */
cdp_error cdp_gain_envelope(cdp_context* ctx, cdp_buffer* buf,
                            const cdp_breakpoint* points, size_t point_count,
                            cdp_flags flags);

/**
 * Find peak level in a buffer.
 *
 * @param ctx Context for error reporting.
 * @param buf Buffer to analyze.
 * @param peak Output: peak information (may be NULL).
 * @return Peak level (0.0 to 1.0+), or negative on error.
 */
double cdp_find_peak(cdp_context* ctx, const cdp_buffer* buf, cdp_peak* peak);

/**
 * Normalize a buffer to a target peak level (in-place).
 *
 * @param ctx Context for error reporting.
 * @param buf Buffer to process.
 * @param target_level Target peak level (0.0 to 1.0, typically 1.0 or 0.99).
 * @return CDP_OK on success, error code on failure.
 */
cdp_error cdp_normalize(cdp_context* ctx, cdp_buffer* buf, double target_level);

/**
 * Normalize a buffer to a target level in dB (in-place).
 *
 * @param ctx Context for error reporting.
 * @param buf Buffer to process.
 * @param target_db Target peak in dB (0 = 0dBFS, -3 = -3dBFS).
 * @return CDP_OK on success, error code on failure.
 */
cdp_error cdp_normalize_db(cdp_context* ctx, cdp_buffer* buf, double target_db);

/**
 * Invert phase of a buffer (in-place).
 *
 * @param ctx Context for error reporting.
 * @param buf Buffer to process.
 * @return CDP_OK on success, error code on failure.
 */
cdp_error cdp_phase_invert(cdp_context* ctx, cdp_buffer* buf);

/*============================================================================
 * Spatial/Panning Operations
 *============================================================================*/

/**
 * Pan a mono buffer to stereo with a static pan position.
 *
 * Uses CDP's geometric panning model for natural sound positioning.
 *
 * @param ctx Context for error reporting.
 * @param buf Input buffer (must be mono).
 * @param position Pan position: -1.0 = left, 0.0 = center, +1.0 = right.
 *                 Values beyond -1/+1 simulate sound beyond speakers.
 * @return New stereo buffer, or NULL on error.
 */
cdp_buffer* cdp_pan(cdp_context* ctx, const cdp_buffer* buf, double position);

/**
 * Pan a mono buffer to stereo with time-varying position.
 *
 * @param ctx Context for error reporting.
 * @param buf Input buffer (must be mono).
 * @param points Array of (time, position) breakpoints.
 * @param point_count Number of breakpoints.
 * @return New stereo buffer, or NULL on error.
 */
cdp_buffer* cdp_pan_envelope(cdp_context* ctx, const cdp_buffer* buf,
                              const cdp_breakpoint* points, size_t point_count);

/**
 * Mirror (swap) left and right channels of a stereo buffer.
 *
 * @param ctx Context for error reporting.
 * @param buf Input buffer (must be stereo).
 * @return New buffer with swapped channels, or NULL on error.
 */
cdp_buffer* cdp_mirror(cdp_context* ctx, const cdp_buffer* buf);

/**
 * Narrow or widen stereo image.
 *
 * @param ctx Context for error reporting.
 * @param buf Input buffer (must be stereo).
 * @param width Stereo width: 0.0 = mono, 1.0 = unchanged, >1.0 = wider.
 * @return New buffer with adjusted stereo width, or NULL on error.
 */
cdp_buffer* cdp_narrow(cdp_context* ctx, const cdp_buffer* buf, double width);

/*============================================================================
 * Mixing Operations
 *============================================================================*/

/**
 * Mix two buffers together with gains.
 *
 * @param ctx Context for error reporting.
 * @param a First buffer.
 * @param b Second buffer (must have same channels and sample rate as a).
 * @param gain_a Gain for first buffer.
 * @param gain_b Gain for second buffer.
 * @return New buffer containing the mix, or NULL on error.
 */
cdp_buffer* cdp_mix2(cdp_context* ctx, const cdp_buffer* a, const cdp_buffer* b,
                     double gain_a, double gain_b);

/**
 * Mix multiple buffers together with optional gains.
 *
 * @param ctx Context for error reporting.
 * @param buffers Array of buffers to mix (must all have same channels/rate).
 * @param gains Optional array of gains (one per buffer), or NULL for unity.
 * @param count Number of buffers.
 * @return New buffer containing the mix, or NULL on error.
 */
cdp_buffer* cdp_mix(cdp_context* ctx, cdp_buffer** buffers, const double* gains,
                    int count);

/*============================================================================
 * Channel Operations
 *============================================================================*/

/**
 * Convert multi-channel buffer to mono by averaging all channels.
 *
 * @param ctx Context for error reporting.
 * @param buf Input buffer (any channel count).
 * @return New mono buffer, or NULL on error.
 */
cdp_buffer* cdp_to_mono(cdp_context* ctx, const cdp_buffer* buf);

/**
 * Convert mono buffer to stereo by duplicating the channel.
 *
 * @param ctx Context for error reporting.
 * @param buf Input buffer (must be mono).
 * @return New stereo buffer, or NULL on error.
 */
cdp_buffer* cdp_to_stereo(cdp_context* ctx, const cdp_buffer* buf);

/**
 * Extract a single channel from a multi-channel buffer.
 *
 * @param ctx Context for error reporting.
 * @param buf Input buffer.
 * @param channel Channel index (0-based, 0=left, 1=right for stereo).
 * @return New mono buffer containing the extracted channel, or NULL on error.
 */
cdp_buffer* cdp_extract_channel(cdp_context* ctx, const cdp_buffer* buf, int channel);

/**
 * Merge two mono buffers into a stereo buffer.
 *
 * @param ctx Context for error reporting.
 * @param left Left channel buffer (must be mono).
 * @param right Right channel buffer (must be mono, same length as left).
 * @return New stereo buffer, or NULL on error.
 */
cdp_buffer* cdp_merge_channels(cdp_context* ctx, const cdp_buffer* left,
                                const cdp_buffer* right);

/**
 * Split a multi-channel buffer into separate mono buffers.
 *
 * @param ctx Context for error reporting.
 * @param buf Input buffer.
 * @param out_num_channels Output: number of channels (may be NULL).
 * @return Array of mono buffers (one per channel), or NULL on error.
 *         Caller must free each buffer and the array itself.
 */
cdp_buffer** cdp_split_channels(cdp_context* ctx, const cdp_buffer* buf,
                                 int* out_num_channels);

/**
 * Interleave multiple mono buffers into a single multi-channel buffer.
 *
 * @param ctx Context for error reporting.
 * @param buffers Array of mono buffers.
 * @param num_channels Number of buffers in the array.
 * @return New interleaved buffer, or NULL on error.
 */
cdp_buffer* cdp_interleave(cdp_context* ctx, cdp_buffer** buffers, int num_channels);

/*============================================================================
 * Buffer Utilities
 *============================================================================*/

/**
 * Reverse audio buffer.
 *
 * @param ctx Context for error reporting.
 * @param buf Input buffer.
 * @return New buffer with reversed audio, or NULL on error.
 */
cdp_buffer* cdp_reverse(cdp_context* ctx, const cdp_buffer* buf);

/**
 * Apply fade in to buffer (in-place).
 *
 * @param ctx Context for error reporting.
 * @param buf Buffer to process.
 * @param duration Fade duration in seconds.
 * @param fade_type 0 = linear, 1 = exponential (equal power).
 * @return CDP_OK on success, error code on failure.
 */
cdp_error cdp_fade_in(cdp_context* ctx, cdp_buffer* buf, double duration, int fade_type);

/**
 * Apply fade out to buffer (in-place).
 *
 * @param ctx Context for error reporting.
 * @param buf Buffer to process.
 * @param duration Fade duration in seconds.
 * @param fade_type 0 = linear, 1 = exponential (equal power).
 * @return CDP_OK on success, error code on failure.
 */
cdp_error cdp_fade_out(cdp_context* ctx, cdp_buffer* buf, double duration, int fade_type);

/**
 * Concatenate multiple buffers into one.
 *
 * @param ctx Context for error reporting.
 * @param buffers Array of buffers (must all have same channels/rate).
 * @param count Number of buffers.
 * @return New concatenated buffer, or NULL on error.
 */
cdp_buffer* cdp_concat(cdp_context* ctx, cdp_buffer** buffers, int count);

/*============================================================================
 * Conversion Utilities
 *============================================================================*/

/**
 * Convert linear gain to decibels.
 *
 * @param gain Linear gain factor.
 * @return Gain in dB (returns -INFINITY for gain <= 0).
 */
double cdp_gain_to_db(double gain);

/**
 * Convert decibels to linear gain.
 *
 * @param db Gain in decibels.
 * @return Linear gain factor.
 */
double cdp_db_to_gain(double db);

#ifdef __cplusplus
}
#endif

#endif /* CDP_H */
