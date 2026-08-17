/*
 * CDP Pitch-Synchronous Operations (PSOW) Implementation
 *
 * Implements PSOLA-style algorithms for pitch-synchronous grain manipulation.
 */

#include "cdp_psow.h"
#include "cdp_lib_internal.h"
#include <stdlib.h>
#include <string.h>
#include <math.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

/*
 * Find pitch periods by detecting downward zero crossings.
 * Returns array of grain start positions and count.
 */
static int find_pitch_periods(const float* data, size_t length, int sample_rate,
                               size_t** periods, size_t* period_count) {
    /* Unused for now: the bound below assumes 44.1 kHz rather than deriving
     * the shortest plausible period from sample_rate. Kept in the signature
     * because that is the fix, and callers already pass it. */
    (void)sample_rate;
    /* Estimate maximum number of periods (assume min period of ~50 samples at 44.1kHz) */
    size_t max_periods = length / 50 + 100;
    *periods = (size_t*)malloc(max_periods * sizeof(size_t));
    if (*periods == NULL) return -1;

    size_t count = 0;
    int sign = 0;

    /* Find initial sign */
    for (size_t i = 0; i < length && sign == 0; i++) {
        if (data[i] > 0.0001f) sign = 1;
        else if (data[i] < -0.0001f) sign = -1;
    }

    if (sign == 0) {
        free(*periods);
        *periods = NULL;
        *period_count = 0;
        return 0;
    }

    /* First period starts at 0 */
    (*periods)[count++] = 0;

    /* Find downward zero crossings (positive to negative transitions) */
    for (size_t i = 1; i < length && count < max_periods; i++) {
        int new_sign = 0;
        if (data[i] > 0.0001f) new_sign = 1;
        else if (data[i] < -0.0001f) new_sign = -1;

        /* Downward zero crossing: positive to negative */
        if (sign == 1 && new_sign == -1) {
            (*periods)[count++] = i;
        }

        if (new_sign != 0) sign = new_sign;
    }

    *period_count = count;
    return 0;
}

/*
 * Apply raised cosine window to a segment.
 */
static void apply_raised_cosine_window(float* data, size_t length) {
    if (length < 2) return;

    for (size_t i = 0; i < length; i++) {
        double phase = (double)i / (double)(length - 1);
        double window = 0.5 * (1.0 - cos(2.0 * M_PI * phase));
        data[i] *= (float)window;
    }
}

/*
 * Apply half-cosine fade at start and end.
 */
static void apply_fade_window(float* data, size_t length, size_t fade_len) {
    if (fade_len > length / 2) fade_len = length / 2;
    if (fade_len < 1) return;

    /* Fade in */
    for (size_t i = 0; i < fade_len; i++) {
        double phase = (double)i / (double)fade_len;
        double window = 0.5 * (1.0 - cos(M_PI * phase));
        data[i] *= (float)window;
    }

    /* Fade out */
    for (size_t i = 0; i < fade_len; i++) {
        double phase = (double)i / (double)fade_len;
        double window = 0.5 * (1.0 + cos(M_PI * phase));
        data[length - 1 - i] *= (float)window;
    }
}

/*
 * PSOW Stretch - Time stretch using pitch-synchronous overlap-add.
 */
cdp_lib_buffer* cdp_lib_psow_stretch(cdp_lib_ctx* ctx,
                                      const cdp_lib_buffer* input,
                                      double stretch_factor,
                                      int grain_count) {
    if (ctx == NULL || input == NULL) {
        if (ctx) cdp_lib_set_error(ctx, "NULL input");
        return NULL;
    }

    if (stretch_factor < 0.25 || stretch_factor > 4.0) {
        cdp_lib_set_error(ctx, "stretch_factor must be between 0.25 and 4.0");
        return NULL;
    }

    if (grain_count < 1) grain_count = 1;
    if (grain_count > 8) grain_count = 8;

    /* Convert to mono if needed */
    cdp_lib_buffer* mono = cdp_lib_to_mono(ctx, input);
    if (mono == NULL) return NULL;

    size_t num_samples = mono->length;
    int sample_rate = mono->sample_rate;

    /* Find pitch periods */
    size_t* periods = NULL;
    size_t period_count = 0;

    if (find_pitch_periods(mono->data, num_samples, sample_rate, &periods, &period_count) < 0) {
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Failed to find pitch periods");
        return NULL;
    }

    if (period_count < 2) {
        if (periods) free(periods);
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Not enough pitch periods found");
        return NULL;
    }

    /* Calculate output length */
    size_t output_length = (size_t)(num_samples * stretch_factor) + 1000;

    cdp_lib_buffer* output = cdp_lib_buffer_create(output_length, 1, sample_rate);
    if (output == NULL) {
        free(periods);
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Failed to allocate output buffer");
        return NULL;
    }
    memset(output->data, 0, output_length * sizeof(float));

    /* Allocate grain buffer */
    size_t max_grain_len = 0;
    for (size_t i = 1; i < period_count; i++) {
        size_t len = periods[i] - periods[i-1];
        if (len > max_grain_len) max_grain_len = len;
    }
    max_grain_len *= grain_count;
    max_grain_len += 100;  /* Safety margin */

    float* grain = (float*)malloc(max_grain_len * sizeof(float));
    if (grain == NULL) {
        free(periods);
        cdp_lib_buffer_free(mono);
        cdp_lib_buffer_free(output);
        cdp_lib_set_error(ctx, "Failed to allocate grain buffer");
        return NULL;
    }

    /* Process grains */
    size_t out_pos = 0;
    size_t actual_output_len = 0;

    for (size_t i = 0; i + grain_count < period_count; i += grain_count) {
        /* Extract grain group */
        size_t grain_start = periods[i];
        size_t grain_end = periods[i + grain_count];
        size_t grain_len = grain_end - grain_start;

        if (grain_len > max_grain_len) grain_len = max_grain_len;
        if (grain_start + grain_len > num_samples) {
            grain_len = num_samples - grain_start;
        }

        if (grain_len < 10) continue;

        /* Copy grain with window */
        memcpy(grain, &mono->data[grain_start], grain_len * sizeof(float));

        /* Apply raised cosine window */
        apply_raised_cosine_window(grain, grain_len);

        /* Calculate output position based on stretch factor */
        size_t input_pos = grain_start;
        out_pos = (size_t)(input_pos * stretch_factor);

        /* Overlap-add the grain */
        for (size_t j = 0; j < grain_len && out_pos + j < output_length; j++) {
            output->data[out_pos + j] += grain[j];
        }

        if (out_pos + grain_len > actual_output_len) {
            actual_output_len = out_pos + grain_len;
        }
    }

    /* Trim output */
    if (actual_output_len > 0 && actual_output_len < output_length) {
        output->length = actual_output_len;
    }

    /* Normalize if needed */
    float max_val = 0.0f;
    for (size_t i = 0; i < output->length; i++) {
        float absval = fabsf(output->data[i]);
        if (absval > max_val) max_val = absval;
    }
    if (max_val > 1.0f) {
        float normalizer = 0.95f / max_val;
        for (size_t i = 0; i < output->length; i++) {
            output->data[i] *= normalizer;
        }
    }

    free(grain);
    free(periods);
    cdp_lib_buffer_free(mono);

    return output;
}

/*
 * PSOW Grab - Extract pitch-synchronous grains from a position.
 */
cdp_lib_buffer* cdp_lib_psow_grab(cdp_lib_ctx* ctx,
                                   const cdp_lib_buffer* input,
                                   double time,
                                   double duration,
                                   int grain_count,
                                   double density) {
    if (ctx == NULL || input == NULL) {
        if (ctx) cdp_lib_set_error(ctx, "NULL input");
        return NULL;
    }

    if (time < 0) {
        cdp_lib_set_error(ctx, "time must be >= 0");
        return NULL;
    }

    if (duration < 0) {
        cdp_lib_set_error(ctx, "duration must be >= 0");
        return NULL;
    }

    if (grain_count < 1) grain_count = 1;
    if (grain_count > 16) grain_count = 16;

    if (density < 0.25) density = 0.25;
    if (density > 4.0) density = 4.0;

    /* Convert to mono if needed */
    cdp_lib_buffer* mono = cdp_lib_to_mono(ctx, input);
    if (mono == NULL) return NULL;

    size_t num_samples = mono->length;
    int sample_rate = mono->sample_rate;

    /* Find pitch periods */
    size_t* periods = NULL;
    size_t period_count = 0;

    if (find_pitch_periods(mono->data, num_samples, sample_rate, &periods, &period_count) < 0) {
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Failed to find pitch periods");
        return NULL;
    }

    if (period_count < 2) {
        if (periods) free(periods);
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Not enough pitch periods found");
        return NULL;
    }

    /* Find the grain at the specified time */
    size_t target_sample = (size_t)(time * sample_rate);
    if (target_sample >= num_samples) {
        target_sample = num_samples > 100 ? num_samples - 100 : 0;
    }

    /* Find closest period start */
    size_t grain_index = 0;
    size_t min_dist = num_samples;
    for (size_t i = 0; i < period_count; i++) {
        size_t dist = (periods[i] > target_sample) ?
                      (periods[i] - target_sample) :
                      (target_sample - periods[i]);
        if (dist < min_dist) {
            min_dist = dist;
            grain_index = i;
        }
    }

    /* Ensure we have enough grains */
    if (grain_index + grain_count >= period_count) {
        grain_index = period_count > (size_t)grain_count ? period_count - grain_count - 1 : 0;
    }

    /* Extract the grain(s) */
    size_t grain_start = periods[grain_index];
    size_t grain_end = periods[grain_index + grain_count];
    size_t grain_len = grain_end - grain_start;

    if (grain_start + grain_len > num_samples) {
        grain_len = num_samples - grain_start;
    }

    if (grain_len < 10) {
        free(periods);
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Grain too short");
        return NULL;
    }

    /* Allocate grain buffer */
    float* grain = (float*)malloc(grain_len * sizeof(float));
    if (grain == NULL) {
        free(periods);
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Failed to allocate grain buffer");
        return NULL;
    }

    memcpy(grain, &mono->data[grain_start], grain_len * sizeof(float));

    /* Apply window */
    apply_raised_cosine_window(grain, grain_len);

    /* Calculate output length */
    size_t output_length;
    if (duration <= 0) {
        /* Just return the single grain */
        output_length = grain_len;
    } else {
        output_length = (size_t)(duration * sample_rate) + grain_len;
    }

    cdp_lib_buffer* output = cdp_lib_buffer_create(output_length, 1, sample_rate);
    if (output == NULL) {
        free(grain);
        free(periods);
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Failed to allocate output buffer");
        return NULL;
    }
    memset(output->data, 0, output_length * sizeof(float));

    if (duration <= 0) {
        /* Just copy the grain */
        memcpy(output->data, grain, grain_len * sizeof(float));
        output->length = grain_len;
    } else {
        /* Repeat the grain with overlap */
        double step = grain_len / density;  /* Position increment */
        size_t pos = 0;
        size_t actual_len = 0;

        while (pos < output_length - grain_len) {
            for (size_t j = 0; j < grain_len && pos + j < output_length; j++) {
                output->data[pos + j] += grain[j];
            }
            if (pos + grain_len > actual_len) {
                actual_len = pos + grain_len;
            }
            pos += (size_t)step;
        }

        if (actual_len < output_length) {
            output->length = actual_len;
        }
    }

    /* Normalize if needed */
    float max_val = 0.0f;
    for (size_t i = 0; i < output->length; i++) {
        float absval = fabsf(output->data[i]);
        if (absval > max_val) max_val = absval;
    }
    if (max_val > 1.0f) {
        float normalizer = 0.95f / max_val;
        for (size_t i = 0; i < output->length; i++) {
            output->data[i] *= normalizer;
        }
    }

    free(grain);
    free(periods);
    cdp_lib_buffer_free(mono);

    return output;
}

/*
 * PSOW Dupl - Duplicate grains for time-stretching.
 */
cdp_lib_buffer* cdp_lib_psow_dupl(cdp_lib_ctx* ctx,
                                   const cdp_lib_buffer* input,
                                   int repeat_count,
                                   int grain_count) {
    if (ctx == NULL || input == NULL) {
        if (ctx) cdp_lib_set_error(ctx, "NULL input");
        return NULL;
    }

    if (repeat_count < 1) repeat_count = 1;
    if (repeat_count > 8) repeat_count = 8;

    if (grain_count < 1) grain_count = 1;
    if (grain_count > 8) grain_count = 8;

    /* Convert to mono if needed */
    cdp_lib_buffer* mono = cdp_lib_to_mono(ctx, input);
    if (mono == NULL) return NULL;

    size_t num_samples = mono->length;
    int sample_rate = mono->sample_rate;

    /* Find pitch periods */
    size_t* periods = NULL;
    size_t period_count = 0;

    if (find_pitch_periods(mono->data, num_samples, sample_rate, &periods, &period_count) < 0) {
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Failed to find pitch periods");
        return NULL;
    }

    if (period_count < 2) {
        if (periods) free(periods);
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Not enough pitch periods found");
        return NULL;
    }

    /* Calculate output length (each grain group is repeated) */
    size_t output_length = num_samples * repeat_count + 1000;

    cdp_lib_buffer* output = cdp_lib_buffer_create(output_length, 1, sample_rate);
    if (output == NULL) {
        free(periods);
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Failed to allocate output buffer");
        return NULL;
    }
    memset(output->data, 0, output_length * sizeof(float));

    /* Allocate grain buffer */
    size_t max_grain_len = 0;
    for (size_t i = 1; i < period_count; i++) {
        size_t len = periods[i] - periods[i-1];
        if (len > max_grain_len) max_grain_len = len;
    }
    max_grain_len *= grain_count;
    max_grain_len += 100;

    float* grain = (float*)malloc(max_grain_len * sizeof(float));
    if (grain == NULL) {
        free(periods);
        cdp_lib_buffer_free(mono);
        cdp_lib_buffer_free(output);
        cdp_lib_set_error(ctx, "Failed to allocate grain buffer");
        return NULL;
    }

    /* Process grains */
    size_t out_pos = 0;

    for (size_t i = 0; i + grain_count < period_count; i += grain_count) {
        /* Extract grain group */
        size_t grain_start = periods[i];
        size_t grain_end = periods[i + grain_count];
        size_t grain_len = grain_end - grain_start;

        if (grain_len > max_grain_len) grain_len = max_grain_len;
        if (grain_start + grain_len > num_samples) {
            grain_len = num_samples - grain_start;
        }

        if (grain_len < 10) continue;

        /* Copy grain with window */
        memcpy(grain, &mono->data[grain_start], grain_len * sizeof(float));
        apply_fade_window(grain, grain_len, grain_len / 8);

        /* Output repeated grains */
        for (int r = 0; r < repeat_count; r++) {
            if (out_pos + grain_len >= output_length) break;

            for (size_t j = 0; j < grain_len; j++) {
                output->data[out_pos + j] += grain[j];
            }
            out_pos += grain_len;
        }
    }

    /* Trim output */
    if (out_pos > 0 && out_pos < output_length) {
        output->length = out_pos;
    }

    /* Normalize if needed */
    float max_val = 0.0f;
    for (size_t i = 0; i < output->length; i++) {
        float absval = fabsf(output->data[i]);
        if (absval > max_val) max_val = absval;
    }
    if (max_val > 1.0f) {
        float normalizer = 0.95f / max_val;
        for (size_t i = 0; i < output->length; i++) {
            output->data[i] *= normalizer;
        }
    }

    free(grain);
    free(periods);
    cdp_lib_buffer_free(mono);

    return output;
}

/*
 * PSOW Interp - Interpolate between two grains.
 */
cdp_lib_buffer* cdp_lib_psow_interp(cdp_lib_ctx* ctx,
                                     const cdp_lib_buffer* grain1,
                                     const cdp_lib_buffer* grain2,
                                     double start_dur,
                                     double interp_dur,
                                     double end_dur) {
    if (ctx == NULL || grain1 == NULL || grain2 == NULL) {
        if (ctx) cdp_lib_set_error(ctx, "NULL input");
        return NULL;
    }

    if (start_dur < 0) start_dur = 0;
    if (interp_dur < 0.01) interp_dur = 0.01;
    if (end_dur < 0) end_dur = 0;

    /* Convert to mono if needed */
    cdp_lib_buffer* mono1 = cdp_lib_to_mono(ctx, grain1);
    if (mono1 == NULL) return NULL;

    cdp_lib_buffer* mono2 = cdp_lib_to_mono(ctx, grain2);
    if (mono2 == NULL) {
        cdp_lib_buffer_free(mono1);
        return NULL;
    }

    int sample_rate = mono1->sample_rate;
    size_t len1 = mono1->length;
    size_t len2 = mono2->length;

    /* Calculate output length */
    size_t start_samples = (size_t)(start_dur * sample_rate);
    size_t interp_samples = (size_t)(interp_dur * sample_rate);
    size_t end_samples = (size_t)(end_dur * sample_rate);
    size_t output_length = start_samples + interp_samples + end_samples + 100;

    cdp_lib_buffer* output = cdp_lib_buffer_create(output_length, 1, sample_rate);
    if (output == NULL) {
        cdp_lib_buffer_free(mono1);
        cdp_lib_buffer_free(mono2);
        cdp_lib_set_error(ctx, "Failed to allocate output buffer");
        return NULL;
    }
    memset(output->data, 0, output_length * sizeof(float));

    size_t out_pos = 0;

    /* Start section: repeat grain1 */
    if (start_samples > 0) {
        size_t pos = 0;
        while (pos < start_samples) {
            for (size_t j = 0; j < len1 && out_pos < output_length; j++) {
                output->data[out_pos++] = mono1->data[j];
            }
            pos += len1;
        }
    }

    /* Interpolation section */
    size_t steps = interp_samples / ((len1 + len2) / 2);
    if (steps < 2) steps = 2;

    for (size_t step = 0; step < steps && out_pos < output_length; step++) {
        double mix = (double)step / (double)(steps - 1);  /* 0 to 1 */

        /* Interpolate grain lengths */
        size_t interp_len = (size_t)(len1 * (1.0 - mix) + len2 * mix);

        for (size_t j = 0; j < interp_len && out_pos < output_length; j++) {
            double phase1 = (double)j / (double)interp_len * len1;
            double phase2 = (double)j / (double)interp_len * len2;

            /* Linear interpolation within each grain */
            size_t idx1 = (size_t)phase1;
            size_t idx2 = (size_t)phase2;
            double frac1 = phase1 - idx1;
            double frac2 = phase2 - idx2;

            float val1 = 0, val2 = 0;
            if (idx1 < len1 - 1) {
                val1 = mono1->data[idx1] * (1.0f - (float)frac1) +
                       mono1->data[idx1 + 1] * (float)frac1;
            } else if (idx1 < len1) {
                val1 = mono1->data[idx1];
            }

            if (idx2 < len2 - 1) {
                val2 = mono2->data[idx2] * (1.0f - (float)frac2) +
                       mono2->data[idx2 + 1] * (float)frac2;
            } else if (idx2 < len2) {
                val2 = mono2->data[idx2];
            }

            /* Cross-fade between grains */
            output->data[out_pos++] = val1 * (1.0f - (float)mix) + val2 * (float)mix;
        }
    }

    /* End section: repeat grain2 */
    if (end_samples > 0) {
        size_t pos = 0;
        while (pos < end_samples && out_pos < output_length) {
            for (size_t j = 0; j < len2 && out_pos < output_length; j++) {
                output->data[out_pos++] = mono2->data[j];
            }
            pos += len2;
        }
    }

    /* Trim output */
    if (out_pos < output_length) {
        output->length = out_pos;
    }

    /* Normalize if needed */
    float max_val = 0.0f;
    for (size_t i = 0; i < output->length; i++) {
        float absval = fabsf(output->data[i]);
        if (absval > max_val) max_val = absval;
    }
    if (max_val > 1.0f) {
        float normalizer = 0.95f / max_val;
        for (size_t i = 0; i < output->length; i++) {
            output->data[i] *= normalizer;
        }
    }

    cdp_lib_buffer_free(mono1);
    cdp_lib_buffer_free(mono2);

    return output;
}
