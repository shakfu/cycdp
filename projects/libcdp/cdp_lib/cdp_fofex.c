/*
 * CDP FOF Extraction and Synthesis (FOFEX) Implementation
 *
 * Implements FOF extraction and synthesis for formant-preserving
 * pitch manipulation.
 */

#include "cdp_fofex.h"
#include "cdp_lib_internal.h"
#include <stdlib.h>
#include <string.h>
#include <math.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

#define FOF_WINDOW_MS 2.0  /* Cosine window edge length in ms */
#define MIN_PITCH_HZ 50.0
#define MAX_PITCH_HZ 2000.0

/*
 * Find downward zero crossings and return their positions.
 */
static int find_zero_crossings(const float* data, size_t length,
                                size_t** crossings, size_t* count) {
    size_t max_crossings = length / 20 + 100;
    *crossings = (size_t*)malloc(max_crossings * sizeof(size_t));
    if (*crossings == NULL) return -1;

    size_t cnt = 0;
    int sign = 0;

    /* Find initial sign */
    for (size_t i = 0; i < length && sign == 0; i++) {
        if (data[i] > 0.0001f) sign = 1;
        else if (data[i] < -0.0001f) sign = -1;
    }

    if (sign == 0) {
        *count = 0;
        return 0;
    }

    /* Find downward zero crossings */
    for (size_t i = 1; i < length && cnt < max_crossings; i++) {
        int new_sign = 0;
        if (data[i] > 0.0001f) new_sign = 1;
        else if (data[i] < -0.0001f) new_sign = -1;

        if (sign == 1 && new_sign == -1) {
            (*crossings)[cnt++] = i;
        }

        if (new_sign != 0) sign = new_sign;
    }

    *count = cnt;
    return 0;
}

/*
 * Apply raised cosine window to FOF edges.
 */
static void apply_fof_window(float* data, size_t length, size_t edge_samples) {
    if (edge_samples > length / 2) edge_samples = length / 2;
    if (edge_samples < 1) return;

    /* Fade in */
    for (size_t i = 0; i < edge_samples; i++) {
        double phase = (double)(i + 1) / (double)edge_samples;
        double window = (1.0 - cos(phase * M_PI)) / 2.0;
        data[i] *= (float)window;
    }

    /* Fade out */
    for (size_t i = 0; i < edge_samples; i++) {
        double phase = (double)(i + 1) / (double)edge_samples;
        double window = (1.0 + cos(phase * M_PI)) / 2.0;
        data[length - 1 - i] *= (float)window;
    }
}

/*
 * Extract a single FOF at a specified time.
 */
cdp_lib_buffer* cdp_lib_fofex_extract(cdp_lib_ctx* ctx,
                                       const cdp_lib_buffer* input,
                                       double time,
                                       int fof_count,
                                       int window) {
    if (ctx == NULL || input == NULL) {
        if (ctx) cdp_lib_set_error(ctx, "NULL input");
        return NULL;
    }

    if (time < 0) {
        cdp_lib_set_error(ctx, "time must be >= 0");
        return NULL;
    }

    if (fof_count < 1) fof_count = 1;
    if (fof_count > 8) fof_count = 8;

    /* Convert to mono if needed */
    cdp_lib_buffer* mono = cdp_lib_to_mono(ctx, input);
    if (mono == NULL) return NULL;

    size_t num_samples = mono->length;
    int sample_rate = mono->sample_rate;

    /* Find target sample position */
    size_t target_pos = (size_t)(time * sample_rate);
    if (target_pos >= num_samples) {
        target_pos = num_samples > 100 ? num_samples - 100 : 0;
    }

    /* Find zero crossings */
    size_t* crossings = NULL;
    size_t crossing_count = 0;
    if (find_zero_crossings(mono->data, num_samples, &crossings, &crossing_count) < 0) {
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Failed to find zero crossings");
        return NULL;
    }

    if (crossing_count < 2) {
        if (crossings) free(crossings);
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Not enough zero crossings found");
        return NULL;
    }

    /* Find closest crossing to target */
    size_t best_idx = 0;
    size_t min_dist = num_samples;
    for (size_t i = 0; i < crossing_count; i++) {
        size_t dist = (crossings[i] > target_pos) ?
                      (crossings[i] - target_pos) :
                      (target_pos - crossings[i]);
        if (dist < min_dist) {
            min_dist = dist;
            best_idx = i;
        }
    }

    /* Ensure we have enough periods */
    if (best_idx + fof_count >= crossing_count) {
        best_idx = crossing_count > (size_t)fof_count ? crossing_count - fof_count - 1 : 0;
    }

    /* Extract FOF */
    size_t fof_start = crossings[best_idx];
    size_t fof_end = crossings[best_idx + fof_count];
    size_t fof_len = fof_end - fof_start;

    if (fof_len < 10) {
        free(crossings);
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "FOF too short");
        return NULL;
    }

    cdp_lib_buffer* output = cdp_lib_buffer_create(fof_len, 1, sample_rate);
    if (output == NULL) {
        free(crossings);
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Failed to allocate output buffer");
        return NULL;
    }

    memcpy(output->data, &mono->data[fof_start], fof_len * sizeof(float));

    /* Apply window if requested */
    if (window) {
        size_t edge_samples = (size_t)(FOF_WINDOW_MS * 0.001 * sample_rate);
        apply_fof_window(output->data, fof_len, edge_samples);
    }

    free(crossings);
    cdp_lib_buffer_free(mono);

    return output;
}

/*
 * Extract all FOFs from audio.
 */
cdp_lib_buffer* cdp_lib_fofex_extract_all(cdp_lib_ctx* ctx,
                                           const cdp_lib_buffer* input,
                                           int fof_count,
                                           double min_level_db,
                                           int window,
                                           int* fof_info) {
    if (ctx == NULL || input == NULL || fof_info == NULL) {
        if (ctx) cdp_lib_set_error(ctx, "NULL input");
        return NULL;
    }

    if (fof_count < 1) fof_count = 1;
    if (fof_count > 4) fof_count = 4;

    /* Convert to mono if needed */
    cdp_lib_buffer* mono = cdp_lib_to_mono(ctx, input);
    if (mono == NULL) return NULL;

    size_t num_samples = mono->length;
    int sample_rate = mono->sample_rate;

    /* Find zero crossings */
    size_t* crossings = NULL;
    size_t crossing_count = 0;
    if (find_zero_crossings(mono->data, num_samples, &crossings, &crossing_count) < 0) {
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Failed to find zero crossings");
        return NULL;
    }

    if (crossing_count < (size_t)(fof_count + 1)) {
        if (crossings) free(crossings);
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Not enough zero crossings found");
        return NULL;
    }

    /* Find maximum FOF length for uniform storage */
    size_t max_fof_len = 0;
    size_t num_fofs = 0;
    for (size_t i = 0; i + fof_count < crossing_count; i += fof_count) {
        size_t fof_len = crossings[i + fof_count] - crossings[i];
        if (fof_len > max_fof_len) max_fof_len = fof_len;
        num_fofs++;
    }

    if (num_fofs == 0) {
        free(crossings);
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "No FOFs found");
        return NULL;
    }

    /* Find peak level for threshold */
    float peak_level = 0.0f;
    for (size_t i = 0; i < num_samples; i++) {
        float absval = fabsf(mono->data[i]);
        if (absval > peak_level) peak_level = absval;
    }

    /* Calculate threshold */
    double threshold = 0.0;
    if (min_level_db < 0) {
        threshold = peak_level * pow(10.0, min_level_db / 20.0);
    }

    /* Count valid FOFs (above threshold) */
    size_t valid_count = 0;
    for (size_t i = 0; i + fof_count < crossing_count; i += fof_count) {
        size_t start = crossings[i];
        size_t end = crossings[i + fof_count];

        /* Find peak in this FOF */
        float fof_peak = 0.0f;
        for (size_t j = start; j < end && j < num_samples; j++) {
            float absval = fabsf(mono->data[j]);
            if (absval > fof_peak) fof_peak = absval;
        }

        if (fof_peak >= threshold) {
            valid_count++;
        }
    }

    if (valid_count == 0) {
        free(crossings);
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "No FOFs above threshold");
        return NULL;
    }

    /* Add padding to max length */
    size_t unit_len = max_fof_len + 10;

    /* Allocate output buffer */
    size_t output_len = unit_len * valid_count;
    cdp_lib_buffer* output = cdp_lib_buffer_create(output_len, 1, sample_rate);
    if (output == NULL) {
        free(crossings);
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Failed to allocate output buffer");
        return NULL;
    }
    memset(output->data, 0, output_len * sizeof(float));

    /* Extract FOFs */
    size_t edge_samples = (size_t)(FOF_WINDOW_MS * 0.001 * sample_rate);
    size_t out_idx = 0;

    for (size_t i = 0; i + fof_count < crossing_count; i += fof_count) {
        size_t start = crossings[i];
        size_t end = crossings[i + fof_count];
        size_t fof_len = end - start;

        /* Check level */
        float fof_peak = 0.0f;
        for (size_t j = start; j < end && j < num_samples; j++) {
            float absval = fabsf(mono->data[j]);
            if (absval > fof_peak) fof_peak = absval;
        }

        if (fof_peak < threshold) continue;

        /* Copy FOF */
        size_t out_pos = out_idx * unit_len;
        for (size_t j = 0; j < fof_len && start + j < num_samples; j++) {
            output->data[out_pos + j] = mono->data[start + j];
        }

        /* Apply window if requested */
        if (window && fof_len > edge_samples * 2) {
            apply_fof_window(&output->data[out_pos], fof_len, edge_samples);
        }

        out_idx++;
    }

    /* Set info */
    fof_info[0] = (int)out_idx;  /* Number of FOFs */
    fof_info[1] = (int)unit_len;  /* Samples per FOF */

    free(crossings);
    cdp_lib_buffer_free(mono);

    return output;
}

/*
 * Synthesize audio using FOFs.
 */
cdp_lib_buffer* cdp_lib_fofex_synth(cdp_lib_ctx* ctx,
                                     const cdp_lib_buffer* fof,
                                     double duration,
                                     double frequency,
                                     double amplitude,
                                     int fof_index,
                                     int fof_unit_len) {
    if (ctx == NULL || fof == NULL) {
        if (ctx) cdp_lib_set_error(ctx, "NULL input");
        return NULL;
    }

    if (duration < 0.001 || duration > 3600.0) {
        cdp_lib_set_error(ctx, "duration must be between 0.001 and 3600 seconds");
        return NULL;
    }

    if (frequency < 20.0 || frequency > 5000.0) {
        cdp_lib_set_error(ctx, "frequency must be between 20 and 5000 Hz");
        return NULL;
    }

    if (amplitude < 0.0 || amplitude > 1.0) {
        cdp_lib_set_error(ctx, "amplitude must be between 0.0 and 1.0");
        return NULL;
    }

    int sample_rate = fof->sample_rate;
    size_t fof_len;
    const float* fof_data;
    size_t num_fofs = 1;

    /* Determine FOF to use */
    if (fof_unit_len > 0) {
        /* Bank of FOFs */
        num_fofs = fof->length / fof_unit_len;
        fof_len = fof_unit_len;

        if (fof_index < 0) {
            /* Average all FOFs (will be done during synthesis) */
            fof_data = fof->data;
        } else {
            if ((size_t)fof_index >= num_fofs) {
                fof_index = (int)num_fofs - 1;
            }
            fof_data = &fof->data[fof_index * fof_unit_len];
        }
    } else {
        /* Single FOF */
        fof_len = fof->length;
        fof_data = fof->data;
        fof_index = 0;
    }

    /* Calculate output length */
    size_t output_len = (size_t)(duration * sample_rate);
    cdp_lib_buffer* output = cdp_lib_buffer_create(output_len, 1, sample_rate);
    if (output == NULL) {
        cdp_lib_set_error(ctx, "Failed to allocate output buffer");
        return NULL;
    }
    memset(output->data, 0, output_len * sizeof(float));

    /* Calculate pitch period */
    double period = (double)sample_rate / frequency;

    /* Synthesize by overlapping FOFs at target pitch */
    double pos = 0.0;
    while ((size_t)pos < output_len) {
        size_t out_pos = (size_t)pos;

        if (fof_index < 0 && num_fofs > 1 && fof_unit_len > 0) {
            /* Average all FOFs */
            for (size_t j = 0; j < fof_len && out_pos + j < output_len; j++) {
                float sum = 0.0f;
                for (size_t f = 0; f < num_fofs; f++) {
                    sum += fof->data[f * fof_unit_len + j];
                }
                output->data[out_pos + j] += (sum / num_fofs) * (float)amplitude;
            }
        } else {
            /* Use single FOF */
            for (size_t j = 0; j < fof_len && out_pos + j < output_len; j++) {
                output->data[out_pos + j] += fof_data[j] * (float)amplitude;
            }
        }

        pos += period;
    }

    /* Normalize if needed */
    float max_val = 0.0f;
    for (size_t i = 0; i < output_len; i++) {
        float absval = fabsf(output->data[i]);
        if (absval > max_val) max_val = absval;
    }
    if (max_val > 1.0f) {
        float normalizer = 0.95f / max_val;
        for (size_t i = 0; i < output_len; i++) {
            output->data[i] *= normalizer;
        }
    }

    return output;
}

/*
 * Resynthesize audio with modified pitch using FOFs.
 */
cdp_lib_buffer* cdp_lib_fofex_repitch(cdp_lib_ctx* ctx,
                                       const cdp_lib_buffer* input,
                                       double pitch_shift,
                                       int preserve_formants) {
    if (ctx == NULL || input == NULL) {
        if (ctx) cdp_lib_set_error(ctx, "NULL input");
        return NULL;
    }

    if (pitch_shift < -24.0 || pitch_shift > 24.0) {
        cdp_lib_set_error(ctx, "pitch_shift must be between -24 and +24 semitones");
        return NULL;
    }

    /* Convert to mono if needed */
    cdp_lib_buffer* mono = cdp_lib_to_mono(ctx, input);
    if (mono == NULL) return NULL;

    size_t num_samples = mono->length;
    int sample_rate = mono->sample_rate;

    /* Find zero crossings */
    size_t* crossings = NULL;
    size_t crossing_count = 0;
    if (find_zero_crossings(mono->data, num_samples, &crossings, &crossing_count) < 0) {
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Failed to find zero crossings");
        return NULL;
    }

    if (crossing_count < 3) {
        if (crossings) free(crossings);
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Not enough zero crossings found");
        return NULL;
    }

    /* Calculate pitch ratio */
    double ratio = pow(2.0, pitch_shift / 12.0);

    /* Calculate output length */
    size_t output_len;
    if (preserve_formants) {
        /* Same duration as input */
        output_len = num_samples;
    } else {
        /* Duration changes with pitch */
        output_len = (size_t)(num_samples / ratio);
    }

    cdp_lib_buffer* output = cdp_lib_buffer_create(output_len, 1, sample_rate);
    if (output == NULL) {
        free(crossings);
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Failed to allocate output buffer");
        return NULL;
    }
    memset(output->data, 0, output_len * sizeof(float));

    size_t edge_samples = (size_t)(FOF_WINDOW_MS * 0.001 * sample_rate);

    if (preserve_formants) {
        /* PSOLA-style: resample FOFs at new pitch */
        double out_pos = 0.0;
        size_t fof_idx = 0;

        while (out_pos < output_len && fof_idx + 1 < crossing_count) {
            /* Get current FOF */
            size_t fof_start = crossings[fof_idx];
            size_t fof_end = crossings[fof_idx + 1];
            size_t fof_len = fof_end - fof_start;

            if (fof_len < 4) {
                fof_idx++;
                continue;
            }

            /* Copy FOF with window */
            size_t out_start = (size_t)out_pos;
            for (size_t j = 0; j < fof_len && out_start + j < output_len; j++) {
                float sample = mono->data[fof_start + j];

                /* Apply simple window */
                double window = 1.0;
                if (j < edge_samples) {
                    window = (1.0 - cos(M_PI * j / edge_samples)) / 2.0;
                } else if (j >= fof_len - edge_samples) {
                    size_t k = fof_len - 1 - j;
                    window = (1.0 - cos(M_PI * k / edge_samples)) / 2.0;
                }

                output->data[out_start + j] += sample * (float)window;
            }

            /* Advance by new period */
            out_pos += fof_len / ratio;
            fof_idx++;
        }
    } else {
        /* Simple resample (formants shift with pitch) */
        for (size_t i = 0; i < output_len; i++) {
            double src_pos = i * ratio;
            size_t src_idx = (size_t)src_pos;
            double frac = src_pos - src_idx;

            if (src_idx + 1 < num_samples) {
                output->data[i] = mono->data[src_idx] * (1.0f - (float)frac) +
                                  mono->data[src_idx + 1] * (float)frac;
            } else if (src_idx < num_samples) {
                output->data[i] = mono->data[src_idx];
            }
        }
    }

    /* Normalize if needed */
    float max_val = 0.0f;
    for (size_t i = 0; i < output_len; i++) {
        float absval = fabsf(output->data[i]);
        if (absval > max_val) max_val = absval;
    }
    if (max_val > 1.0f) {
        float normalizer = 0.95f / max_val;
        for (size_t i = 0; i < output_len; i++) {
            output->data[i] *= normalizer;
        }
    }

    free(crossings);
    cdp_lib_buffer_free(mono);

    return output;
}
