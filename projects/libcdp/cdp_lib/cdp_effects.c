/*
 * CDP Effects Functions - Implementation
 *
 * Implements bitcrush, ring modulation, delay, chorus, and flanger effects.
 */

#include "cdp_effects.h"
#include "cdp_lib_internal.h"

cdp_lib_buffer* cdp_lib_bitcrush(cdp_lib_ctx* ctx,
                                  const cdp_lib_buffer* input,
                                  int bit_depth,
                                  int downsample) {
    if (ctx == NULL || input == NULL) {
        return NULL;
    }

    if (bit_depth < 1) bit_depth = 1;
    if (bit_depth > 16) bit_depth = 16;
    if (downsample < 1) downsample = 1;

    /* Create output buffer */
    cdp_lib_buffer* output = cdp_lib_buffer_create(
        input->length, input->channels, input->sample_rate);
    if (output == NULL) {
        snprintf(ctx->error_msg, sizeof(ctx->error_msg),
                 "Failed to allocate output buffer");
        return NULL;
    }

    size_t frames = input->length / input->channels;
    int channels = input->channels;

    /* Calculate quantization levels */
    double levels = pow(2.0, bit_depth);
    double quantize_factor = levels / 2.0;

    /* Sample-and-hold state for downsampling */
    float *hold_values = (float*)calloc(channels, sizeof(float));
    if (hold_values == NULL) {
        cdp_lib_buffer_free(output);
        snprintf(ctx->error_msg, sizeof(ctx->error_msg),
                 "Failed to allocate hold buffer");
        return NULL;
    }

    for (size_t i = 0; i < frames; i++) {
        /* Only sample at downsample intervals */
        if (i % downsample == 0) {
            for (int ch = 0; ch < channels; ch++) {
                float sample = input->data[i * channels + ch];

                /* Quantize to bit_depth */
                /* Scale to 0..levels, round, scale back */
                double scaled = (sample + 1.0) * quantize_factor;
                scaled = floor(scaled);
                if (scaled < 0) scaled = 0;
                if (scaled >= levels) scaled = levels - 1;
                float quantized = (float)((scaled / quantize_factor) - 1.0);

                hold_values[ch] = quantized;
            }
        }

        /* Output held values */
        for (int ch = 0; ch < channels; ch++) {
            output->data[i * channels + ch] = hold_values[ch];
        }
    }

    free(hold_values);
    return output;
}

cdp_lib_buffer* cdp_lib_ring_mod(cdp_lib_ctx* ctx,
                                  const cdp_lib_buffer* input,
                                  double freq,
                                  double mix) {
    if (ctx == NULL || input == NULL) {
        return NULL;
    }

    if (freq <= 0) {
        snprintf(ctx->error_msg, sizeof(ctx->error_msg),
                 "Carrier frequency must be positive");
        return NULL;
    }

    if (mix < 0) mix = 0;
    if (mix > 1) mix = 1;

    /* Create output buffer */
    cdp_lib_buffer* output = cdp_lib_buffer_create(
        input->length, input->channels, input->sample_rate);
    if (output == NULL) {
        snprintf(ctx->error_msg, sizeof(ctx->error_msg),
                 "Failed to allocate output buffer");
        return NULL;
    }

    size_t frames = input->length / input->channels;
    int channels = input->channels;
    double sample_rate = input->sample_rate;

    /* Angular frequency */
    double omega = 2.0 * M_PI * freq / sample_rate;

    for (size_t i = 0; i < frames; i++) {
        /* Carrier signal */
        float carrier = (float)sin(omega * i);

        for (int ch = 0; ch < channels; ch++) {
            float dry = input->data[i * channels + ch];
            float wet = dry * carrier;

            output->data[i * channels + ch] =
                (float)(dry * (1.0 - mix) + wet * mix);
        }
    }

    return output;
}

cdp_lib_buffer* cdp_lib_delay(cdp_lib_ctx* ctx,
                               const cdp_lib_buffer* input,
                               double delay_ms,
                               double feedback,
                               double mix) {
    if (ctx == NULL || input == NULL) {
        return NULL;
    }

    if (delay_ms <= 0) {
        snprintf(ctx->error_msg, sizeof(ctx->error_msg),
                 "Delay time must be positive");
        return NULL;
    }

    if (feedback < 0) feedback = 0;
    if (feedback >= 1) feedback = 0.99;
    if (mix < 0) mix = 0;
    if (mix > 1) mix = 1;

    int sample_rate = input->sample_rate;
    int channels = input->channels;
    size_t delay_samples = (size_t)(delay_ms * sample_rate / 1000.0);
    /* A delay shorter than one sample still needs a line to read and write:
     * calloc(0, n) returns a non-NULL zero-byte block and the loop below
     * dereferences delay_buf[ch][write_pos] regardless. ASan reports it as a
     * heap-buffer-overflow; uninstrumented it silently corrupts the heap. */
    if (delay_samples < 1) delay_samples = 1;

    /* Create output buffer */
    cdp_lib_buffer* output = cdp_lib_buffer_create(
        input->length, input->channels, input->sample_rate);
    if (output == NULL) {
        snprintf(ctx->error_msg, sizeof(ctx->error_msg),
                 "Failed to allocate output buffer");
        return NULL;
    }

    /* Allocate delay buffers for each channel */
    float **delay_buf = (float**)malloc(channels * sizeof(float*));
    if (delay_buf == NULL) {
        cdp_lib_buffer_free(output);
        snprintf(ctx->error_msg, sizeof(ctx->error_msg),
                 "Failed to allocate delay buffer");
        return NULL;
    }

    for (int ch = 0; ch < channels; ch++) {
        delay_buf[ch] = (float*)calloc(delay_samples, sizeof(float));
        if (delay_buf[ch] == NULL) {
            for (int j = 0; j < ch; j++) free(delay_buf[j]);
            free(delay_buf);
            cdp_lib_buffer_free(output);
            snprintf(ctx->error_msg, sizeof(ctx->error_msg),
                     "Failed to allocate delay buffer");
            return NULL;
        }
    }

    size_t write_pos = 0;
    size_t frames = input->length / channels;

    for (size_t i = 0; i < frames; i++) {
        for (int ch = 0; ch < channels; ch++) {
            float dry = input->data[i * channels + ch];

            /* Read from delay buffer */
            float delayed = delay_buf[ch][write_pos];

            /* Write to delay buffer with feedback */
            delay_buf[ch][write_pos] = dry + delayed * (float)feedback;

            /* Mix output */
            output->data[i * channels + ch] =
                (float)(dry * (1.0 - mix) + delayed * mix);
        }

        /* Advance write position */
        write_pos++;
        if (write_pos >= delay_samples) write_pos = 0;
    }

    /* Cleanup */
    for (int ch = 0; ch < channels; ch++) {
        free(delay_buf[ch]);
    }
    free(delay_buf);

    return output;
}

cdp_lib_buffer* cdp_lib_chorus(cdp_lib_ctx* ctx,
                                const cdp_lib_buffer* input,
                                double rate,
                                double depth_ms,
                                double mix) {
    if (ctx == NULL || input == NULL) {
        return NULL;
    }

    if (rate <= 0) rate = 1.0;
    if (depth_ms <= 0) depth_ms = 5.0;
    if (mix < 0) mix = 0;
    if (mix > 1) mix = 1;

    int sample_rate = input->sample_rate;
    int channels = input->channels;

    /* Base delay around 20-30ms for chorus effect */
    double base_delay_ms = 25.0;
    size_t max_delay_samples = (size_t)((base_delay_ms + depth_ms) * sample_rate / 1000.0) + 10;

    /* Create output buffer */
    cdp_lib_buffer* output = cdp_lib_buffer_create(
        input->length, input->channels, input->sample_rate);
    if (output == NULL) {
        snprintf(ctx->error_msg, sizeof(ctx->error_msg),
                 "Failed to allocate output buffer");
        return NULL;
    }

    /* Allocate delay buffers */
    float **delay_buf = (float**)malloc(channels * sizeof(float*));
    if (delay_buf == NULL) {
        cdp_lib_buffer_free(output);
        snprintf(ctx->error_msg, sizeof(ctx->error_msg),
                 "Failed to allocate delay buffer");
        return NULL;
    }

    for (int ch = 0; ch < channels; ch++) {
        delay_buf[ch] = (float*)calloc(max_delay_samples, sizeof(float));
        if (delay_buf[ch] == NULL) {
            for (int j = 0; j < ch; j++) free(delay_buf[j]);
            free(delay_buf);
            cdp_lib_buffer_free(output);
            snprintf(ctx->error_msg, sizeof(ctx->error_msg),
                     "Failed to allocate delay buffer");
            return NULL;
        }
    }

    double omega = 2.0 * M_PI * rate / sample_rate;
    size_t write_pos = 0;
    size_t frames = input->length / channels;

    /* Convert depth to samples */
    double depth_samples = depth_ms * sample_rate / 1000.0;
    double base_delay_samples = base_delay_ms * sample_rate / 1000.0;

    for (size_t i = 0; i < frames; i++) {
        /* LFO value (0 to 1) */
        double lfo = (1.0 + sin(omega * i)) / 2.0;

        /* Modulated delay time in samples */
        double delay_time = base_delay_samples + lfo * depth_samples;

        /* Calculate read position with linear interpolation */
        double read_pos_f = (double)write_pos - delay_time;
        if (read_pos_f < 0) read_pos_f += max_delay_samples;

        size_t read_pos0 = (size_t)read_pos_f % max_delay_samples;
        size_t read_pos1 = (read_pos0 + 1) % max_delay_samples;
        double frac = read_pos_f - floor(read_pos_f);

        for (int ch = 0; ch < channels; ch++) {
            float dry = input->data[i * channels + ch];

            /* Write to delay buffer */
            delay_buf[ch][write_pos] = dry;

            /* Read with interpolation */
            float delayed = (float)(delay_buf[ch][read_pos0] * (1.0 - frac) +
                                    delay_buf[ch][read_pos1] * frac);

            /* Mix output */
            output->data[i * channels + ch] =
                (float)(dry * (1.0 - mix) + delayed * mix);
        }

        /* Advance write position */
        write_pos++;
        if (write_pos >= max_delay_samples) write_pos = 0;
    }

    /* Cleanup */
    for (int ch = 0; ch < channels; ch++) {
        free(delay_buf[ch]);
    }
    free(delay_buf);

    return output;
}

cdp_lib_buffer* cdp_lib_flanger(cdp_lib_ctx* ctx,
                                 const cdp_lib_buffer* input,
                                 double rate,
                                 double depth_ms,
                                 double feedback,
                                 double mix) {
    if (ctx == NULL || input == NULL) {
        return NULL;
    }

    if (rate <= 0) rate = 0.5;
    if (depth_ms <= 0) depth_ms = 3.0;
    if (feedback < -0.95) feedback = -0.95;
    if (feedback > 0.95) feedback = 0.95;
    if (mix < 0) mix = 0;
    if (mix > 1) mix = 1;

    int sample_rate = input->sample_rate;
    int channels = input->channels;

    /* Base delay around 1-5ms for flanger effect */
    double base_delay_ms = 2.0;
    size_t max_delay_samples = (size_t)((base_delay_ms + depth_ms) * sample_rate / 1000.0) + 10;

    /* Create output buffer */
    cdp_lib_buffer* output = cdp_lib_buffer_create(
        input->length, input->channels, input->sample_rate);
    if (output == NULL) {
        snprintf(ctx->error_msg, sizeof(ctx->error_msg),
                 "Failed to allocate output buffer");
        return NULL;
    }

    /* Allocate delay buffers */
    float **delay_buf = (float**)malloc(channels * sizeof(float*));
    if (delay_buf == NULL) {
        cdp_lib_buffer_free(output);
        snprintf(ctx->error_msg, sizeof(ctx->error_msg),
                 "Failed to allocate delay buffer");
        return NULL;
    }

    for (int ch = 0; ch < channels; ch++) {
        delay_buf[ch] = (float*)calloc(max_delay_samples, sizeof(float));
        if (delay_buf[ch] == NULL) {
            for (int j = 0; j < ch; j++) free(delay_buf[j]);
            free(delay_buf);
            cdp_lib_buffer_free(output);
            snprintf(ctx->error_msg, sizeof(ctx->error_msg),
                     "Failed to allocate delay buffer");
            return NULL;
        }
    }

    double omega = 2.0 * M_PI * rate / sample_rate;
    size_t write_pos = 0;
    size_t frames = input->length / channels;

    /* Convert depth to samples */
    double depth_samples = depth_ms * sample_rate / 1000.0;
    double base_delay_samples = base_delay_ms * sample_rate / 1000.0;

    for (size_t i = 0; i < frames; i++) {
        /* LFO value (0 to 1) */
        double lfo = (1.0 + sin(omega * i)) / 2.0;

        /* Modulated delay time in samples */
        double delay_time = base_delay_samples + lfo * depth_samples;

        /* Calculate read position with linear interpolation */
        double read_pos_f = (double)write_pos - delay_time;
        if (read_pos_f < 0) read_pos_f += max_delay_samples;

        size_t read_pos0 = (size_t)read_pos_f % max_delay_samples;
        size_t read_pos1 = (read_pos0 + 1) % max_delay_samples;
        double frac = read_pos_f - floor(read_pos_f);

        for (int ch = 0; ch < channels; ch++) {
            float dry = input->data[i * channels + ch];

            /* Read with interpolation */
            float delayed = (float)(delay_buf[ch][read_pos0] * (1.0 - frac) +
                                    delay_buf[ch][read_pos1] * frac);

            /* Write to delay buffer with feedback */
            delay_buf[ch][write_pos] = dry + delayed * (float)feedback;

            /* Mix output */
            output->data[i * channels + ch] =
                (float)(dry * (1.0 - mix) + delayed * mix);
        }

        /* Advance write position */
        write_pos++;
        if (write_pos >= max_delay_samples) write_pos = 0;
    }

    /* Cleanup */
    for (int ch = 0; ch < channels; ch++) {
        free(delay_buf[ch]);
    }
    free(delay_buf);

    return output;
}
