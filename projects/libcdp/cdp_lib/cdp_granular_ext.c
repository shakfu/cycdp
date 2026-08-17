/*
 * CDP Granular Extended - Implementation
 *
 * Implements advanced grain manipulation algorithms.
 */

#include "cdp_granular_ext.h"
#include "cdp_lib_internal.h"
#include <string.h>

/* ========================================================================= */
/* Helper functions                                                          */
/* ========================================================================= */

/* Grain info structure */
typedef struct {
    size_t start;      /* Start sample */
    size_t length;     /* Length in samples */
    float peak;        /* Peak amplitude */
} grain_info;

/*
 * Detect grains in audio using amplitude gating.
 * Returns array of grain_info structs.
 */
static grain_info* detect_grains(const float* data, size_t length,
                                  int sample_rate, double gate,
                                  double grainsize_ms, size_t* grain_count) {
    size_t min_grain = (size_t)(grainsize_ms * sample_rate / 1000.0 * 0.25);
    size_t max_grain = (size_t)(grainsize_ms * sample_rate / 1000.0 * 4.0);
    size_t min_gap = (size_t)(grainsize_ms * sample_rate / 1000.0 * 0.1);

    if (min_grain < 10) min_grain = 10;
    if (min_gap < 5) min_gap = 5;

    float threshold = (float)gate;
    float release_threshold = threshold * 0.5f;

    /* First pass: count grains */
    size_t count = 0;
    int in_grain = 0;
    size_t grain_start = 0;
    size_t gap_count = 0;

    for (size_t i = 0; i < length; i++) {
        float abs_val = fabsf(data[i]);

        if (!in_grain) {
            if (abs_val > threshold) {
                in_grain = 1;
                grain_start = i;
                gap_count = 0;
            }
        } else {
            if (abs_val < release_threshold) {
                gap_count++;
                if (gap_count >= min_gap) {
                    size_t glen = i - gap_count - grain_start;
                    if (glen >= min_grain && glen <= max_grain) {
                        count++;
                    }
                    in_grain = 0;
                }
            } else {
                gap_count = 0;
            }
        }
    }

    /* Handle grain at end of file */
    if (in_grain) {
        size_t glen = length - grain_start;
        if (glen >= min_grain && glen <= max_grain) {
            count++;
        }
    }

    if (count == 0) {
        /* No grains found - create regular intervals */
        size_t grain_size = (size_t)(grainsize_ms * sample_rate / 1000.0);
        count = length / grain_size;
        if (count < 1) count = 1;

        grain_info* grains = (grain_info*)malloc(count * sizeof(grain_info));
        if (grains == NULL) return NULL;

        for (size_t i = 0; i < count; i++) {
            grains[i].start = i * grain_size;
            grains[i].length = grain_size;
            if (grains[i].start + grain_size > length) {
                grains[i].length = length - grains[i].start;
            }

            /* Find peak */
            grains[i].peak = 0;
            for (size_t j = 0; j < grains[i].length; j++) {
                float abs_val = fabsf(data[grains[i].start + j]);
                if (abs_val > grains[i].peak) grains[i].peak = abs_val;
            }
        }

        *grain_count = count;
        return grains;
    }

    /* Allocate grain array */
    grain_info* grains = (grain_info*)malloc(count * sizeof(grain_info));
    if (grains == NULL) return NULL;

    /* Second pass: record grain info */
    size_t idx = 0;
    in_grain = 0;
    gap_count = 0;

    for (size_t i = 0; i < length && idx < count; i++) {
        float abs_val = fabsf(data[i]);

        if (!in_grain) {
            if (abs_val > threshold) {
                in_grain = 1;
                grain_start = i;
                gap_count = 0;
            }
        } else {
            if (abs_val < release_threshold) {
                gap_count++;
                if (gap_count >= min_gap) {
                    size_t glen = i - gap_count - grain_start;
                    if (glen >= min_grain && glen <= max_grain) {
                        grains[idx].start = grain_start;
                        grains[idx].length = glen;

                        /* Find peak */
                        grains[idx].peak = 0;
                        for (size_t j = 0; j < glen; j++) {
                            float av = fabsf(data[grain_start + j]);
                            if (av > grains[idx].peak) grains[idx].peak = av;
                        }
                        idx++;
                    }
                    in_grain = 0;
                }
            } else {
                gap_count = 0;
            }
        }
    }

    /* Handle grain at end */
    if (in_grain && idx < count) {
        size_t glen = length - grain_start;
        if (glen >= min_grain && glen <= max_grain) {
            grains[idx].start = grain_start;
            grains[idx].length = glen;
            grains[idx].peak = 0;
            for (size_t j = 0; j < glen; j++) {
                float av = fabsf(data[grain_start + j]);
                if (av > grains[idx].peak) grains[idx].peak = av;
            }
            idx++;
        }
    }

    *grain_count = idx;
    return grains;
}

/*
 * Apply crossfade envelope to grain edges.
 */
static void apply_grain_envelope(float* grain, size_t length, size_t fade_len) {
    if (fade_len > length / 2) fade_len = length / 2;
    if (fade_len < 1) fade_len = 1;

    /* Fade in */
    for (size_t i = 0; i < fade_len; i++) {
        float env = (float)i / fade_len;
        /* Cosine fade for smoothness */
        env = 0.5f * (1.0f - cosf((float)M_PI * env));
        grain[i] *= env;
    }

    /* Fade out */
    for (size_t i = 0; i < fade_len; i++) {
        float env = (float)i / fade_len;
        env = 0.5f * (1.0f - cosf((float)M_PI * env));
        grain[length - 1 - i] *= env;
    }
}

/*
 * Copy grain to output with overlap-add.
 */
static void overlap_add_grain(float* output, size_t output_len,
                               const float* grain, size_t grain_len,
                               size_t position) {
    for (size_t i = 0; i < grain_len && position + i < output_len; i++) {
        output[position + i] += grain[i];
    }
}

/*
 * Interpolate value from curve (array of time,value pairs).
 */
static double interp_curve(const double* curve, size_t points, double time) {
    if (points == 0 || curve == NULL) return 1.0;
    if (points == 1) return curve[1];

    /* Find bracketing points */
    for (size_t i = 0; i < points - 1; i++) {
        double t0 = curve[i * 2];
        double t1 = curve[(i + 1) * 2];

        if (time >= t0 && time <= t1) {
            double v0 = curve[i * 2 + 1];
            double v1 = curve[(i + 1) * 2 + 1];
            double frac = (time - t0) / (t1 - t0 + 0.0001);
            return v0 + (v1 - v0) * frac;
        }
    }

    /* Beyond end - return last value */
    return curve[(points - 1) * 2 + 1];
}

/* ========================================================================= */
/* Grain Reorder                                                             */
/* ========================================================================= */

cdp_lib_buffer* cdp_lib_grain_reorder(cdp_lib_ctx* ctx,
                                       const cdp_lib_buffer* input,
                                       const int* order,
                                       size_t order_count,
                                       double gate,
                                       double grainsize_ms,
                                       unsigned int seed) {
    if (ctx == NULL || input == NULL) {
        return NULL;
    }

    /* Defaults */
    if (gate <= 0) gate = 0.1;
    if (gate > 1) gate = 1.0;
    if (grainsize_ms <= 0) grainsize_ms = 50.0;

    int sample_rate = input->sample_rate;

    /* Convert to mono */
    cdp_lib_buffer* mono = cdp_lib_to_mono(ctx, input);
    if (mono == NULL) return NULL;

    /* Detect grains */
    size_t grain_count = 0;
    grain_info* grains = detect_grains(mono->data, mono->length, sample_rate,
                                        gate, grainsize_ms, &grain_count);
    if (grains == NULL) {
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Failed to detect grains");
        return NULL;
    }

    if (grain_count == 0) {
        free(grains);
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "No grains found in input");
        return NULL;
    }

    /* Create permutation array */
    size_t* perm = (size_t*)malloc(grain_count * sizeof(size_t));
    if (perm == NULL) {
        free(grains);
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Failed to allocate permutation array");
        return NULL;
    }

    if (order != NULL && order_count > 0) {
        /* Use provided order */
        for (size_t i = 0; i < grain_count; i++) {
            if (i < order_count) {
                size_t idx = (size_t)order[i];
                if (idx >= grain_count) idx = idx % grain_count;
                perm[i] = idx;
            } else {
                /* Cycle through order */
                size_t idx = (size_t)order[i % order_count];
                if (idx >= grain_count) idx = idx % grain_count;
                perm[i] = idx;
            }
        }
    } else {
        /* Shuffle randomly */
        cdp_lib_seed(ctx, seed);
        for (size_t i = 0; i < grain_count; i++) {
            perm[i] = i;
        }
        for (size_t i = grain_count - 1; i > 0; i--) {
            size_t j = cdp_lib_random_u64(ctx) % (i + 1);
            size_t tmp = perm[i];
            perm[i] = perm[j];
            perm[j] = tmp;
        }
    }

    /* Calculate output size (approximately same as input) */
    size_t output_samples = mono->length;

    /* Allocate output */
    cdp_lib_buffer* output = cdp_lib_buffer_create(output_samples, 1, sample_rate);
    if (output == NULL) {
        free(perm);
        free(grains);
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Failed to allocate output buffer");
        return NULL;
    }

    /* Splice length (15ms) */
    size_t splice_len = (size_t)(15.0 * sample_rate / 1000.0);
    if (splice_len < 10) splice_len = 10;

    /* Allocate grain buffer */
    size_t max_grain_len = 0;
    for (size_t i = 0; i < grain_count; i++) {
        if (grains[i].length > max_grain_len) max_grain_len = grains[i].length;
    }

    float* grain_buf = (float*)malloc(max_grain_len * sizeof(float));
    if (grain_buf == NULL) {
        cdp_lib_buffer_free(output);
        free(perm);
        free(grains);
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Failed to allocate grain buffer");
        return NULL;
    }

    /* Output grains in new order */
    size_t write_pos = 0;

    for (size_t i = 0; i < grain_count && write_pos < output_samples; i++) {
        size_t src_idx = perm[i];
        grain_info* g = &grains[src_idx];

        /* Copy grain */
        memcpy(grain_buf, mono->data + g->start, g->length * sizeof(float));

        /* Apply envelope */
        apply_grain_envelope(grain_buf, g->length, splice_len);

        /* Write to output */
        overlap_add_grain(output->data, output_samples, grain_buf, g->length, write_pos);

        /* Advance position (subtract overlap) */
        /* Saturating: g->length can be <= splice_len, and these are size_t,
         * so a plain subtraction underflows to a near-SIZE_MAX advance. */
        write_pos += (g->length > splice_len) ? (g->length - splice_len) : 1;
        if (write_pos < splice_len) write_pos = splice_len;
    }

    free(grain_buf);
    free(perm);
    free(grains);
    cdp_lib_buffer_free(mono);

    /* Trim output to actual length */
    output->length = write_pos + splice_len;
    if (output->length > output_samples) output->length = output_samples;

    cdp_lib_normalize_if_clipping(output, 0.95f);
    return output;
}

/* ========================================================================= */
/* Grain Rerhythm                                                            */
/* ========================================================================= */

cdp_lib_buffer* cdp_lib_grain_rerhythm(cdp_lib_ctx* ctx,
                                        const cdp_lib_buffer* input,
                                        const double* times,
                                        size_t time_count,
                                        const double* ratios,
                                        size_t ratio_count,
                                        double gate,
                                        double grainsize_ms,
                                        unsigned int seed) {
    if (ctx == NULL || input == NULL) {
        return NULL;
    }

    /* Defaults */
    if (gate <= 0) gate = 0.1;
    if (gate > 1) gate = 1.0;
    if (grainsize_ms <= 0) grainsize_ms = 50.0;

    int sample_rate = input->sample_rate;

    cdp_lib_seed(ctx, seed);

    /* Convert to mono */
    cdp_lib_buffer* mono = cdp_lib_to_mono(ctx, input);
    if (mono == NULL) return NULL;

    /* Detect grains */
    size_t grain_count = 0;
    grain_info* grains = detect_grains(mono->data, mono->length, sample_rate,
                                        gate, grainsize_ms, &grain_count);
    if (grains == NULL || grain_count == 0) {
        if (grains) free(grains);
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "No grains found in input");
        return NULL;
    }

    /* Calculate new grain positions */
    double* new_times = (double*)malloc(grain_count * sizeof(double));
    if (new_times == NULL) {
        free(grains);
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Failed to allocate time array");
        return NULL;
    }

    if (times != NULL && time_count > 0) {
        /* Use explicit times */
        for (size_t i = 0; i < grain_count; i++) {
            if (i < time_count) {
                new_times[i] = times[i];
            } else {
                /* Extrapolate */
                double last_gap = (time_count > 1) ?
                    times[time_count - 1] - times[time_count - 2] : 0.1;
                new_times[i] = times[time_count - 1] + (i - time_count + 1) * last_gap;
            }
        }
    } else if (ratios != NULL && ratio_count > 0) {
        /* Use duration ratios */
        new_times[0] = (double)grains[0].start / sample_rate;

        for (size_t i = 1; i < grain_count; i++) {
            double orig_gap = (double)(grains[i].start - grains[i-1].start) / sample_rate;
            double ratio = ratios[(i - 1) % ratio_count];
            new_times[i] = new_times[i - 1] + orig_gap * ratio;
        }
    } else {
        /* Random variation */
        new_times[0] = (double)grains[0].start / sample_rate;

        for (size_t i = 1; i < grain_count; i++) {
            double orig_gap = (double)(grains[i].start - grains[i-1].start) / sample_rate;
            double variation = 0.5 + cdp_lib_random(ctx);  /* 0.5 to 1.5 */
            new_times[i] = new_times[i - 1] + orig_gap * variation;
        }
    }

    /* Calculate output duration */
    double max_time = new_times[grain_count - 1];
    double last_grain_dur = (double)grains[grain_count - 1].length / sample_rate;
    size_t output_samples = (size_t)((max_time + last_grain_dur + 0.1) * sample_rate);

    /* Allocate output */
    cdp_lib_buffer* output = cdp_lib_buffer_create(output_samples, 1, sample_rate);
    if (output == NULL) {
        free(new_times);
        free(grains);
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Failed to allocate output buffer");
        return NULL;
    }

    /* Splice length */
    size_t splice_len = (size_t)(15.0 * sample_rate / 1000.0);
    if (splice_len < 10) splice_len = 10;

    /* Allocate grain buffer */
    size_t max_grain_len = 0;
    for (size_t i = 0; i < grain_count; i++) {
        if (grains[i].length > max_grain_len) max_grain_len = grains[i].length;
    }

    float* grain_buf = (float*)malloc(max_grain_len * sizeof(float));
    if (grain_buf == NULL) {
        cdp_lib_buffer_free(output);
        free(new_times);
        free(grains);
        cdp_lib_buffer_free(mono);
        return NULL;
    }

    /* Output grains at new times */
    for (size_t i = 0; i < grain_count; i++) {
        grain_info* g = &grains[i];
        size_t write_pos = (size_t)(new_times[i] * sample_rate);

        if (write_pos >= output_samples) continue;

        /* Copy grain */
        memcpy(grain_buf, mono->data + g->start, g->length * sizeof(float));

        /* Apply envelope */
        apply_grain_envelope(grain_buf, g->length, splice_len);

        /* Write to output */
        overlap_add_grain(output->data, output_samples, grain_buf, g->length, write_pos);
    }

    free(grain_buf);
    free(new_times);
    free(grains);
    cdp_lib_buffer_free(mono);

    cdp_lib_normalize_if_clipping(output, 0.95f);
    return output;
}

/* ========================================================================= */
/* Grain Reverse                                                             */
/* ========================================================================= */

cdp_lib_buffer* cdp_lib_grain_reverse(cdp_lib_ctx* ctx,
                                       const cdp_lib_buffer* input,
                                       double gate,
                                       double grainsize_ms) {
    if (ctx == NULL || input == NULL) {
        return NULL;
    }

    /* Defaults */
    if (gate <= 0) gate = 0.1;
    if (gate > 1) gate = 1.0;
    if (grainsize_ms <= 0) grainsize_ms = 50.0;

    int sample_rate = input->sample_rate;

    /* Convert to mono */
    cdp_lib_buffer* mono = cdp_lib_to_mono(ctx, input);
    if (mono == NULL) return NULL;

    /* Detect grains */
    size_t grain_count = 0;
    grain_info* grains = detect_grains(mono->data, mono->length, sample_rate,
                                        gate, grainsize_ms, &grain_count);
    if (grains == NULL || grain_count == 0) {
        if (grains) free(grains);
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "No grains found in input");
        return NULL;
    }

    /* Calculate output size */
    size_t output_samples = mono->length;

    /* Allocate output */
    cdp_lib_buffer* output = cdp_lib_buffer_create(output_samples, 1, sample_rate);
    if (output == NULL) {
        free(grains);
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Failed to allocate output buffer");
        return NULL;
    }

    /* Splice length */
    size_t splice_len = (size_t)(15.0 * sample_rate / 1000.0);
    if (splice_len < 10) splice_len = 10;

    /* Allocate grain buffer */
    size_t max_grain_len = 0;
    for (size_t i = 0; i < grain_count; i++) {
        if (grains[i].length > max_grain_len) max_grain_len = grains[i].length;
    }

    float* grain_buf = (float*)malloc(max_grain_len * sizeof(float));
    if (grain_buf == NULL) {
        cdp_lib_buffer_free(output);
        free(grains);
        cdp_lib_buffer_free(mono);
        return NULL;
    }

    /* Output grains in reverse order */
    size_t write_pos = 0;

    for (size_t i = grain_count; i > 0 && write_pos < output_samples; i--) {
        grain_info* g = &grains[i - 1];

        /* Copy grain */
        memcpy(grain_buf, mono->data + g->start, g->length * sizeof(float));

        /* Apply envelope */
        apply_grain_envelope(grain_buf, g->length, splice_len);

        /* Write to output */
        overlap_add_grain(output->data, output_samples, grain_buf, g->length, write_pos);

        /* Advance position */
        /* Saturating: g->length can be <= splice_len, and these are size_t,
         * so a plain subtraction underflows to a near-SIZE_MAX advance. */
        write_pos += (g->length > splice_len) ? (g->length - splice_len) : 1;
    }

    free(grain_buf);
    free(grains);
    cdp_lib_buffer_free(mono);

    /* Trim output */
    output->length = write_pos + splice_len;
    if (output->length > output_samples) output->length = output_samples;

    cdp_lib_normalize_if_clipping(output, 0.95f);
    return output;
}

/* ========================================================================= */
/* Grain Timewarp                                                            */
/* ========================================================================= */

cdp_lib_buffer* cdp_lib_grain_timewarp(cdp_lib_ctx* ctx,
                                        const cdp_lib_buffer* input,
                                        double stretch,
                                        const double* stretch_curve,
                                        size_t curve_points,
                                        double gate,
                                        double grainsize_ms) {
    if (ctx == NULL || input == NULL) {
        return NULL;
    }

    /* Defaults */
    if (stretch <= 0) stretch = 1.0;
    if (gate <= 0) gate = 0.1;
    if (gate > 1) gate = 1.0;
    if (grainsize_ms <= 0) grainsize_ms = 50.0;

    int sample_rate = input->sample_rate;
    double input_duration = (double)input->length / sample_rate / input->channels;

    /* Convert to mono */
    cdp_lib_buffer* mono = cdp_lib_to_mono(ctx, input);
    if (mono == NULL) return NULL;

    /* Detect grains */
    size_t grain_count = 0;
    grain_info* grains = detect_grains(mono->data, mono->length, sample_rate,
                                        gate, grainsize_ms, &grain_count);
    if (grains == NULL || grain_count == 0) {
        if (grains) free(grains);
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "No grains found in input");
        return NULL;
    }

    /* Calculate new grain times */
    double* new_times = (double*)malloc(grain_count * sizeof(double));
    if (new_times == NULL) {
        free(grains);
        cdp_lib_buffer_free(mono);
        return NULL;
    }

    new_times[0] = (double)grains[0].start / sample_rate;

    for (size_t i = 1; i < grain_count; i++) {
        double orig_time = (double)grains[i].start / sample_rate;
        double prev_orig = (double)grains[i-1].start / sample_rate;
        double orig_gap = orig_time - prev_orig;

        /* Get stretch factor at this time */
        double local_stretch = stretch;
        if (stretch_curve != NULL && curve_points > 0) {
            local_stretch = interp_curve(stretch_curve, curve_points,
                                          orig_time / input_duration);
        }

        new_times[i] = new_times[i - 1] + orig_gap * local_stretch;
    }

    /* Calculate output duration */
    double max_time = new_times[grain_count - 1];
    double last_grain_dur = (double)grains[grain_count - 1].length / sample_rate;
    size_t output_samples = (size_t)((max_time + last_grain_dur + 0.1) * sample_rate);

    /* Allocate output */
    cdp_lib_buffer* output = cdp_lib_buffer_create(output_samples, 1, sample_rate);
    if (output == NULL) {
        free(new_times);
        free(grains);
        cdp_lib_buffer_free(mono);
        return NULL;
    }

    /* Splice length */
    size_t splice_len = (size_t)(15.0 * sample_rate / 1000.0);

    /* Allocate grain buffer */
    size_t max_grain_len = 0;
    for (size_t i = 0; i < grain_count; i++) {
        if (grains[i].length > max_grain_len) max_grain_len = grains[i].length;
    }

    float* grain_buf = (float*)malloc(max_grain_len * sizeof(float));
    if (grain_buf == NULL) {
        cdp_lib_buffer_free(output);
        free(new_times);
        free(grains);
        cdp_lib_buffer_free(mono);
        return NULL;
    }

    /* Output grains at warped times */
    for (size_t i = 0; i < grain_count; i++) {
        grain_info* g = &grains[i];
        size_t write_pos = (size_t)(new_times[i] * sample_rate);

        if (write_pos >= output_samples) continue;

        /* Copy grain */
        memcpy(grain_buf, mono->data + g->start, g->length * sizeof(float));

        /* Apply envelope */
        apply_grain_envelope(grain_buf, g->length, splice_len);

        /* Write to output */
        overlap_add_grain(output->data, output_samples, grain_buf, g->length, write_pos);
    }

    free(grain_buf);
    free(new_times);
    free(grains);
    cdp_lib_buffer_free(mono);

    cdp_lib_normalize_if_clipping(output, 0.95f);
    return output;
}

/* ========================================================================= */
/* Grain Repitch                                                             */
/* ========================================================================= */

cdp_lib_buffer* cdp_lib_grain_repitch(cdp_lib_ctx* ctx,
                                       const cdp_lib_buffer* input,
                                       double pitch_semitones,
                                       const double* pitch_curve,
                                       size_t curve_points,
                                       double gate,
                                       double grainsize_ms) {
    if (ctx == NULL || input == NULL) {
        return NULL;
    }

    /* Defaults */
    if (gate <= 0) gate = 0.1;
    if (gate > 1) gate = 1.0;
    if (grainsize_ms <= 0) grainsize_ms = 50.0;

    int sample_rate = input->sample_rate;
    double input_duration = (double)input->length / sample_rate / input->channels;

    /* Convert to mono */
    cdp_lib_buffer* mono = cdp_lib_to_mono(ctx, input);
    if (mono == NULL) return NULL;

    /* Detect grains */
    size_t grain_count = 0;
    grain_info* grains = detect_grains(mono->data, mono->length, sample_rate,
                                        gate, grainsize_ms, &grain_count);
    if (grains == NULL || grain_count == 0) {
        if (grains) free(grains);
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "No grains found in input");
        return NULL;
    }

    /* Calculate output size (approximately same as input) */
    size_t output_samples = mono->length;

    /* Allocate output */
    cdp_lib_buffer* output = cdp_lib_buffer_create(output_samples, 1, sample_rate);
    if (output == NULL) {
        free(grains);
        cdp_lib_buffer_free(mono);
        return NULL;
    }

    /* Splice length */
    size_t splice_len = (size_t)(15.0 * sample_rate / 1000.0);

    /* Find max grain length accounting for pitch down */
    size_t max_grain_len = 0;
    for (size_t i = 0; i < grain_count; i++) {
        size_t len = grains[i].length * 2;  /* Allow for pitch down */
        if (len > max_grain_len) max_grain_len = len;
    }

    float* grain_buf = (float*)malloc(max_grain_len * sizeof(float));
    if (grain_buf == NULL) {
        cdp_lib_buffer_free(output);
        free(grains);
        cdp_lib_buffer_free(mono);
        return NULL;
    }

    /* Process each grain. Each is written back at its original position, so
     * unlike the other granular passes there is no running write cursor. */
    for (size_t i = 0; i < grain_count; i++) {
        grain_info* g = &grains[i];
        double grain_time = (double)g->start / sample_rate;

        /* Get pitch shift at this time */
        double semitones = pitch_semitones;
        if (pitch_curve != NULL && curve_points > 0) {
            semitones = interp_curve(pitch_curve, curve_points,
                                      grain_time / input_duration);
        }

        double pitch_ratio = pow(2.0, semitones / 12.0);

        /* Resample grain */
        size_t new_len = (size_t)(g->length / pitch_ratio);
        if (new_len > max_grain_len) new_len = max_grain_len;
        if (new_len < 2) new_len = 2;

        for (size_t j = 0; j < new_len; j++) {
            double src_idx = j * pitch_ratio;
            size_t idx0 = (size_t)src_idx;
            double frac = src_idx - idx0;

            size_t src_pos = g->start + idx0;
            if (src_pos + 1 >= mono->length) {
                grain_buf[j] = 0;
            } else {
                grain_buf[j] = (float)(mono->data[src_pos] * (1.0 - frac) +
                                       mono->data[src_pos + 1] * frac);
            }
        }

        /* Apply envelope */
        apply_grain_envelope(grain_buf, new_len, splice_len);

        /* Write to output at original timing */
        size_t target_pos = g->start;
        if (target_pos + new_len > output_samples) {
            new_len = output_samples - target_pos;
        }

        overlap_add_grain(output->data, output_samples, grain_buf, new_len, target_pos);
    }

    free(grain_buf);
    free(grains);
    cdp_lib_buffer_free(mono);

    cdp_lib_normalize_if_clipping(output, 0.95f);
    return output;
}

/* ========================================================================= */
/* Grain Position                                                            */
/* ========================================================================= */

cdp_lib_buffer* cdp_lib_grain_position(cdp_lib_ctx* ctx,
                                        const cdp_lib_buffer* input,
                                        const double* positions,
                                        size_t position_count,
                                        double duration,
                                        double gate,
                                        double grainsize_ms) {
    if (ctx == NULL || input == NULL) {
        return NULL;
    }

    /* Defaults */
    if (gate <= 0) gate = 0.1;
    if (gate > 1) gate = 1.0;
    if (grainsize_ms <= 0) grainsize_ms = 50.0;

    int sample_rate = input->sample_rate;

    /* Convert to mono */
    cdp_lib_buffer* mono = cdp_lib_to_mono(ctx, input);
    if (mono == NULL) return NULL;

    /* Detect grains */
    size_t grain_count = 0;
    grain_info* grains = detect_grains(mono->data, mono->length, sample_rate,
                                        gate, grainsize_ms, &grain_count);
    if (grains == NULL || grain_count == 0) {
        if (grains) free(grains);
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "No grains found in input");
        return NULL;
    }

    /* Calculate output duration */
    if (duration <= 0) {
        /* Auto: use max position + last grain length */
        duration = 0;
        if (positions != NULL && position_count > 0) {
            for (size_t i = 0; i < position_count; i++) {
                if (positions[i] > duration) duration = positions[i];
            }
        }
        double last_grain_dur = (double)grains[grain_count - 1].length / sample_rate;
        duration += last_grain_dur + 0.1;
    }

    size_t output_samples = (size_t)(duration * sample_rate);

    /* Allocate output */
    cdp_lib_buffer* output = cdp_lib_buffer_create(output_samples, 1, sample_rate);
    if (output == NULL) {
        free(grains);
        cdp_lib_buffer_free(mono);
        return NULL;
    }

    /* Splice length */
    size_t splice_len = (size_t)(15.0 * sample_rate / 1000.0);

    /* Find max grain length */
    size_t max_grain_len = 0;
    for (size_t i = 0; i < grain_count; i++) {
        if (grains[i].length > max_grain_len) max_grain_len = grains[i].length;
    }

    float* grain_buf = (float*)malloc(max_grain_len * sizeof(float));
    if (grain_buf == NULL) {
        cdp_lib_buffer_free(output);
        free(grains);
        cdp_lib_buffer_free(mono);
        return NULL;
    }

    /* Place grains at specified positions */
    size_t num_to_place = (position_count < grain_count) ? position_count : grain_count;
    if (positions == NULL || position_count == 0) {
        num_to_place = grain_count;
    }

    for (size_t i = 0; i < num_to_place; i++) {
        grain_info* g = &grains[i % grain_count];

        /* Get position */
        double pos_time;
        if (positions != NULL && i < position_count) {
            pos_time = positions[i];
        } else {
            pos_time = (double)g->start / sample_rate;
        }

        size_t write_pos = (size_t)(pos_time * sample_rate);
        if (write_pos >= output_samples) continue;

        /* Copy grain */
        memcpy(grain_buf, mono->data + g->start, g->length * sizeof(float));

        /* Apply envelope */
        apply_grain_envelope(grain_buf, g->length, splice_len);

        /* Write to output */
        overlap_add_grain(output->data, output_samples, grain_buf, g->length, write_pos);
    }

    free(grain_buf);
    free(grains);
    cdp_lib_buffer_free(mono);

    cdp_lib_normalize_if_clipping(output, 0.95f);
    return output;
}

/* ========================================================================= */
/* Grain Omit                                                                */
/* ========================================================================= */

cdp_lib_buffer* cdp_lib_grain_omit(cdp_lib_ctx* ctx,
                                    const cdp_lib_buffer* input,
                                    int keep,
                                    int out_of,
                                    double gate,
                                    double grainsize_ms,
                                    unsigned int seed) {
    if (ctx == NULL || input == NULL) {
        return NULL;
    }

    /* Validate */
    if (keep < 1) keep = 1;
    if (out_of < 1) out_of = 1;
    if (keep > out_of) keep = out_of;
    if (gate <= 0) gate = 0.1;
    if (gate > 1) gate = 1.0;
    if (grainsize_ms <= 0) grainsize_ms = 50.0;

    int sample_rate = input->sample_rate;
    cdp_lib_seed(ctx, seed);

    /* Convert to mono */
    cdp_lib_buffer* mono = cdp_lib_to_mono(ctx, input);
    if (mono == NULL) return NULL;

    /* Detect grains */
    size_t grain_count = 0;
    grain_info* grains = detect_grains(mono->data, mono->length, sample_rate,
                                        gate, grainsize_ms, &grain_count);
    if (grains == NULL || grain_count == 0) {
        if (grains) free(grains);
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "No grains found in input");
        return NULL;
    }

    /* Create keep pattern */
    int* keep_mask = (int*)calloc(grain_count, sizeof(int));
    if (keep_mask == NULL) {
        free(grains);
        cdp_lib_buffer_free(mono);
        return NULL;
    }

    /* Mark grains to keep */
    for (size_t i = 0; i < grain_count; i++) {
        int group_pos = i % out_of;
        if (group_pos < keep) {
            keep_mask[i] = 1;
        }
    }

    /* Calculate output size */
    size_t total_kept = 0;
    for (size_t i = 0; i < grain_count; i++) {
        if (keep_mask[i]) total_kept += grains[i].length;
    }

    size_t output_samples = total_kept + (size_t)(0.1 * sample_rate);

    /* Allocate output */
    cdp_lib_buffer* output = cdp_lib_buffer_create(output_samples, 1, sample_rate);
    if (output == NULL) {
        free(keep_mask);
        free(grains);
        cdp_lib_buffer_free(mono);
        return NULL;
    }

    /* Splice length */
    size_t splice_len = (size_t)(15.0 * sample_rate / 1000.0);

    /* Find max grain length */
    size_t max_grain_len = 0;
    for (size_t i = 0; i < grain_count; i++) {
        if (grains[i].length > max_grain_len) max_grain_len = grains[i].length;
    }

    float* grain_buf = (float*)malloc(max_grain_len * sizeof(float));
    if (grain_buf == NULL) {
        cdp_lib_buffer_free(output);
        free(keep_mask);
        free(grains);
        cdp_lib_buffer_free(mono);
        return NULL;
    }

    /* Output kept grains */
    size_t write_pos = 0;

    for (size_t i = 0; i < grain_count && write_pos < output_samples; i++) {
        if (!keep_mask[i]) continue;

        grain_info* g = &grains[i];

        /* Copy grain */
        memcpy(grain_buf, mono->data + g->start, g->length * sizeof(float));

        /* Apply envelope */
        apply_grain_envelope(grain_buf, g->length, splice_len);

        /* Write to output */
        overlap_add_grain(output->data, output_samples, grain_buf, g->length, write_pos);

        /* Saturating: g->length can be <= splice_len, and these are size_t,
         * so a plain subtraction underflows to a near-SIZE_MAX advance. */
        write_pos += (g->length > splice_len) ? (g->length - splice_len) : 1;
    }

    free(grain_buf);
    free(keep_mask);
    free(grains);
    cdp_lib_buffer_free(mono);

    /* Trim output */
    output->length = write_pos + splice_len;
    if (output->length > output_samples) output->length = output_samples;

    cdp_lib_normalize_if_clipping(output, 0.95f);
    return output;
}

/* ========================================================================= */
/* Grain Duplicate                                                           */
/* ========================================================================= */

cdp_lib_buffer* cdp_lib_grain_duplicate(cdp_lib_ctx* ctx,
                                         const cdp_lib_buffer* input,
                                         int repeats,
                                         double gate,
                                         double grainsize_ms,
                                         unsigned int seed) {
    if (ctx == NULL || input == NULL) {
        return NULL;
    }

    /* Validate */
    if (repeats < 1) repeats = 1;
    if (repeats > 100) repeats = 100;
    if (gate <= 0) gate = 0.1;
    if (gate > 1) gate = 1.0;
    if (grainsize_ms <= 0) grainsize_ms = 50.0;

    int sample_rate = input->sample_rate;
    cdp_lib_seed(ctx, seed);

    /* Convert to mono */
    cdp_lib_buffer* mono = cdp_lib_to_mono(ctx, input);
    if (mono == NULL) return NULL;

    /* Detect grains */
    size_t grain_count = 0;
    grain_info* grains = detect_grains(mono->data, mono->length, sample_rate,
                                        gate, grainsize_ms, &grain_count);
    if (grains == NULL || grain_count == 0) {
        if (grains) free(grains);
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "No grains found in input");
        return NULL;
    }

    /* Calculate output size */
    size_t total_grain_samples = 0;
    for (size_t i = 0; i < grain_count; i++) {
        total_grain_samples += grains[i].length;
    }

    size_t output_samples = total_grain_samples * repeats + (size_t)(0.1 * sample_rate);

    /* Allocate output */
    cdp_lib_buffer* output = cdp_lib_buffer_create(output_samples, 1, sample_rate);
    if (output == NULL) {
        free(grains);
        cdp_lib_buffer_free(mono);
        return NULL;
    }

    /* Splice length */
    size_t splice_len = (size_t)(15.0 * sample_rate / 1000.0);

    /* Find max grain length */
    size_t max_grain_len = 0;
    for (size_t i = 0; i < grain_count; i++) {
        if (grains[i].length > max_grain_len) max_grain_len = grains[i].length;
    }

    float* grain_buf = (float*)malloc(max_grain_len * sizeof(float));
    if (grain_buf == NULL) {
        cdp_lib_buffer_free(output);
        free(grains);
        cdp_lib_buffer_free(mono);
        return NULL;
    }

    /* Output grains with repeats */
    size_t write_pos = 0;

    for (size_t i = 0; i < grain_count && write_pos < output_samples; i++) {
        grain_info* g = &grains[i];

        for (int r = 0; r < repeats && write_pos < output_samples; r++) {
            /* Copy grain */
            memcpy(grain_buf, mono->data + g->start, g->length * sizeof(float));

            /* Apply envelope */
            apply_grain_envelope(grain_buf, g->length, splice_len);

            /* Slight amplitude variation for natural sound */
            float amp = 1.0f - 0.1f * (float)cdp_lib_random(ctx);
            for (size_t j = 0; j < g->length; j++) {
                grain_buf[j] *= amp;
            }

            /* Write to output */
            overlap_add_grain(output->data, output_samples, grain_buf, g->length, write_pos);

            /* Saturating: g->length can be <= splice_len, and these are
             * size_t, so a plain subtraction underflows to a near-SIZE_MAX
             * advance. */
            write_pos += (g->length > splice_len) ? (g->length - splice_len) : 1;
        }
    }

    free(grain_buf);
    free(grains);
    cdp_lib_buffer_free(mono);

    /* Trim output */
    output->length = write_pos + splice_len;
    if (output->length > output_samples) output->length = output_samples;

    cdp_lib_normalize_if_clipping(output, 0.95f);
    return output;
}
