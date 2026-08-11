/*
 * CDP Distortion Processing - Implementation
 *
 * Implements overload, reverse cycles, fractal, and shuffle operations.
 */

#include "cdp_distort.h"
#include "cdp_lib_internal.h"

cdp_lib_buffer* cdp_lib_distort_overload(cdp_lib_ctx* ctx,
                                          const cdp_lib_buffer* input,
                                          double clip_level,
                                          double depth) {
    if (ctx == NULL || input == NULL) {
        return NULL;
    }

    if (clip_level <= 0 || clip_level > 1) {
        cdp_lib_set_error(ctx, "clip_level must be between 0 and 1");
        return NULL;
    }

    if (depth < 0 || depth > 1) {
        cdp_lib_set_error(ctx, "depth must be between 0 and 1");
        return NULL;
    }

    /* Convert to mono if needed */
    cdp_lib_buffer* mono = cdp_lib_to_mono(ctx, input);
    if (mono == NULL) return NULL;

    /*
     * Soft clipping formula (depth controls softness):
     * - depth = 0: hard clipping
     * - depth = 1: soft clipping (tanh-like)
     *
     * y = clip_level * tanh(x / clip_level) when depth = 1
     * y = clip(x, -clip_level, clip_level) when depth = 0
     */
    for (size_t i = 0; i < mono->length; i++) {
        float x = mono->data[i];

        if (depth > 0) {
            /* Soft clipping using tanh approximation */
            float normalized = x / (float)clip_level;

            /* Mix between hard and soft clipping based on depth */
            float soft = (float)clip_level * tanhf(normalized);
            float hard;
            if (x > clip_level) hard = (float)clip_level;
            else if (x < -clip_level) hard = -(float)clip_level;
            else hard = x;

            mono->data[i] = (float)(depth * soft + (1.0 - depth) * hard);
        } else {
            /* Pure hard clipping */
            if (x > clip_level) mono->data[i] = (float)clip_level;
            else if (x < -clip_level) mono->data[i] = -(float)clip_level;
        }
    }

    return mono;
}

cdp_lib_buffer* cdp_lib_distort_reverse(cdp_lib_ctx* ctx,
                                         const cdp_lib_buffer* input,
                                         int cycle_count) {
    if (ctx == NULL || input == NULL) {
        return NULL;
    }

    if (cycle_count < 1) {
        cdp_lib_set_error(ctx, "cycle_count must be at least 1");
        return NULL;
    }

    /* Convert to mono if needed */
    cdp_lib_buffer* mono = cdp_lib_to_mono(ctx, input);
    if (mono == NULL) return NULL;

    /*
     * Find zero crossings and reverse groups of cycles.
     */
    size_t num_samples = mono->length;

    /* Find all zero crossings */
    size_t *crossings = (size_t*)malloc(num_samples * sizeof(size_t) / 10 + 100);
    if (crossings == NULL) {
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Failed to allocate crossings array");
        return NULL;
    }

    size_t num_crossings = 0;
    crossings[num_crossings++] = 0;  /* Start */

    for (size_t i = 1; i < num_samples; i++) {
        /* Detect sign change (zero crossing) */
        if ((mono->data[i-1] >= 0 && mono->data[i] < 0) ||
            (mono->data[i-1] < 0 && mono->data[i] >= 0)) {
            crossings[num_crossings++] = i;
        }
    }
    crossings[num_crossings++] = num_samples;  /* End */

    /* Process groups of cycles */
    int cycles_processed = 0;
    size_t group_start = 0;

    for (size_t c = 1; c < num_crossings; c++) {
        cycles_processed++;

        if (cycles_processed >= cycle_count || c == num_crossings - 1) {
            /* Reverse this group */
            size_t group_end = crossings[c];

            /* Reverse samples in this range */
            size_t left = group_start;
            size_t right = group_end - 1;
            while (left < right) {
                float temp = mono->data[left];
                mono->data[left] = mono->data[right];
                mono->data[right] = temp;
                left++;
                right--;
            }

            group_start = group_end;
            cycles_processed = 0;
        }
    }

    free(crossings);
    return mono;
}

cdp_lib_buffer* cdp_lib_distort_fractal(cdp_lib_ctx* ctx,
                                         const cdp_lib_buffer* input,
                                         double scaling,
                                         double loudness) {
    if (ctx == NULL || input == NULL) {
        return NULL;
    }

    if (scaling <= 0) {
        cdp_lib_set_error(ctx, "scaling must be positive");
        return NULL;
    }

    if (loudness < 0 || loudness > 1) {
        cdp_lib_set_error(ctx, "loudness must be between 0 and 1");
        return NULL;
    }

    /* Convert to mono if needed */
    cdp_lib_buffer* mono = cdp_lib_to_mono(ctx, input);
    if (mono == NULL) return NULL;

    size_t num_samples = mono->length;

    /*
     * Fractal distortion: overlay scaled copies of the waveform
     * onto itself at different scales (like a fractal pattern).
     *
     * output[i] = input[i] + k1*input[i*s1] + k2*input[i*s2] + ...
     *
     * where s1, s2, ... are different scaling factors
     */

    /* Allocate output buffer */
    cdp_lib_buffer* output = cdp_lib_buffer_create(num_samples, 1, mono->sample_rate);
    if (output == NULL) {
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Failed to allocate output buffer");
        return NULL;
    }

    /* Number of fractal iterations */
    int num_iterations = 4;
    double iteration_scale = scaling;
    double iteration_gain = 0.5;

    for (size_t i = 0; i < num_samples; i++) {
        double sum = mono->data[i];
        double gain = iteration_gain;
        double scale = iteration_scale;

        for (int iter = 0; iter < num_iterations; iter++) {
            size_t src_idx = (size_t)((double)i * scale);
            if (src_idx < num_samples) {
                sum += gain * mono->data[src_idx];
            }
            gain *= 0.5;
            scale *= scaling;
        }

        output->data[i] = (float)(sum * loudness);
    }

    cdp_lib_buffer_free(mono);

    cdp_lib_normalize_if_clipping(output, 1.0f);
    return output;
}

cdp_lib_buffer* cdp_lib_distort_shuffle(cdp_lib_ctx* ctx,
                                         const cdp_lib_buffer* input,
                                         int chunk_count,
                                         unsigned int seed) {
    if (ctx == NULL || input == NULL) {
        return NULL;
    }

    if (chunk_count < 2) {
        cdp_lib_set_error(ctx, "chunk_count must be at least 2");
        return NULL;
    }

    /* Convert to mono if needed */
    cdp_lib_buffer* mono = cdp_lib_to_mono(ctx, input);
    if (mono == NULL) return NULL;

    size_t num_samples = mono->length;
    size_t chunk_size = num_samples / chunk_count;

    if (chunk_size < 1) {
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Audio too short for requested chunk count");
        return NULL;
    }

    /* Create output buffer */
    cdp_lib_buffer* output = cdp_lib_buffer_create(num_samples, 1, mono->sample_rate);
    if (output == NULL) {
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Failed to allocate output buffer");
        return NULL;
    }

    /* Create chunk order array */
    int* order = (int*)malloc(chunk_count * sizeof(int));
    if (order == NULL) {
        cdp_lib_buffer_free(mono);
        cdp_lib_buffer_free(output);
        cdp_lib_set_error(ctx, "Failed to allocate order array");
        return NULL;
    }

    /* Initialize order array */
    for (int i = 0; i < chunk_count; i++) {
        order[i] = i;
    }

    /* Shuffle using Fisher-Yates algorithm.
       Uses the context PRNG rather than srand()/rand(): the latter is
       process-global state shared with every other library in the address
       space, and is neither reentrant nor reproducible under concurrency. */
    cdp_lib_seed(ctx, seed);

    for (int i = chunk_count - 1; i > 0; i--) {
        int j = (int)(cdp_lib_random_u64(ctx) % (uint64_t)(i + 1));
        int temp = order[i];
        order[i] = order[j];
        order[j] = temp;
    }

    /* Copy chunks in shuffled order */
    for (int i = 0; i < chunk_count; i++) {
        size_t src_start = (size_t)order[i] * chunk_size;
        size_t dst_start = (size_t)i * chunk_size;
        size_t copy_size = chunk_size;

        /* Handle last chunk (may be larger) */
        if (i == chunk_count - 1) {
            copy_size = num_samples - dst_start;
            if (order[i] == chunk_count - 1) {
                /* Source is also the last chunk */
                size_t src_remaining = num_samples - src_start;
                if (src_remaining < copy_size) copy_size = src_remaining;
            }
        }

        memcpy(output->data + dst_start, mono->data + src_start,
               copy_size * sizeof(float));
    }

    free(order);
    cdp_lib_buffer_free(mono);

    return output;
}

cdp_lib_buffer* cdp_lib_distort_cut(cdp_lib_ctx* ctx,
                                     const cdp_lib_buffer* input,
                                     int cycle_count,
                                     int cycle_step,
                                     double exponent,
                                     double min_level) {
    if (ctx == NULL || input == NULL) {
        return NULL;
    }

    if (cycle_count < 1 || cycle_count > 100) {
        cdp_lib_set_error(ctx, "cycle_count must be between 1 and 100");
        return NULL;
    }

    if (cycle_step < 1 || cycle_step > 100) {
        cdp_lib_set_error(ctx, "cycle_step must be between 1 and 100");
        return NULL;
    }

    if (exponent < 0.1 || exponent > 10.0) {
        cdp_lib_set_error(ctx, "exponent must be between 0.1 and 10.0");
        return NULL;
    }

    if (min_level < 0.0 || min_level > 1.0) {
        cdp_lib_set_error(ctx, "min_level must be between 0.0 and 1.0");
        return NULL;
    }

    /* Convert to mono if needed */
    cdp_lib_buffer* mono = cdp_lib_to_mono(ctx, input);
    if (mono == NULL) return NULL;

    size_t num_samples = mono->length;

    /*
     * Step 1: Find all zero crossings (waveset boundaries)
     * A waveset is one complete cycle (positive half + negative half).
     */
    size_t max_crossings = num_samples / 10 + 100;
    size_t *crossings = (size_t*)malloc(max_crossings * sizeof(size_t));
    if (crossings == NULL) {
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Failed to allocate crossings array");
        return NULL;
    }

    size_t num_wavesets = 0;
    crossings[0] = 0;  /* Start position */

    /* Track initial phase to detect full wavesets */
    int initial_phase = 0;
    int phase = 0;
    size_t i = 0;

    /* Find initial phase */
    while (i < num_samples && initial_phase == 0) {
        if (mono->data[i] > 0) {
            initial_phase = 1;
            phase = 1;
        } else if (mono->data[i] < 0) {
            initial_phase = -1;
            phase = -1;
        }
        i++;
    }

    if (initial_phase == 0) {
        free(crossings);
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "No signal found in audio");
        return NULL;
    }

    /* Find wavesets (full cycles based on initial phase) */
    size_t waveset_start = 0;
    for (; i < num_samples; i++) {
        float sample = mono->data[i];
        float prev = mono->data[i-1];

        if (initial_phase == 1) {
            /* Positive-first: waveset ends when we return to positive after negative */
            if (phase == 1 && sample < 0) {
                phase = -1;
            } else if (phase == -1 && sample >= 0) {
                /* Complete waveset found */
                if (num_wavesets < max_crossings - 1) {
                    crossings[num_wavesets++] = waveset_start;
                    waveset_start = i;
                }
                phase = 1;
            }
        } else {
            /* Negative-first: waveset ends when we return to negative after positive */
            if (phase == -1 && sample > 0) {
                phase = 1;
            } else if (phase == 1 && sample <= 0) {
                /* Complete waveset found */
                if (num_wavesets < max_crossings - 1) {
                    crossings[num_wavesets++] = waveset_start;
                    waveset_start = i;
                }
                phase = -1;
            }
        }
    }
    /* Add final waveset if any samples remain */
    if (waveset_start < num_samples && num_wavesets < max_crossings) {
        crossings[num_wavesets++] = waveset_start;
    }

    if (num_wavesets < (size_t)cycle_count) {
        free(crossings);
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Not enough wavesets for requested cycle_count");
        return NULL;
    }

    /*
     * Step 2: Calculate segments and their envelope application
     * Each segment contains cycle_count wavesets.
     * We step by cycle_step wavesets between segment starts.
     */

    /* Calculate maximum possible output size */
    size_t max_segments = (num_wavesets / (size_t)cycle_step) + 1;
    size_t *seg_starts = (size_t*)malloc(max_segments * sizeof(size_t));
    size_t *seg_lengths = (size_t*)malloc(max_segments * sizeof(size_t));
    if (seg_starts == NULL || seg_lengths == NULL) {
        free(crossings);
        if (seg_starts) free(seg_starts);
        if (seg_lengths) free(seg_lengths);
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Failed to allocate segment arrays");
        return NULL;
    }

    size_t num_segments = 0;
    size_t total_output = 0;

    for (size_t ws = 0; ws + cycle_count <= num_wavesets; ws += (size_t)cycle_step) {
        /* Segment starts at waveset ws */
        size_t start = crossings[ws];
        size_t end;

        /* Segment ends after cycle_count wavesets */
        if (ws + cycle_count < num_wavesets) {
            end = crossings[ws + cycle_count];
        } else {
            end = num_samples;
        }

        size_t length = end - start;
        if (length > 0) {
            seg_starts[num_segments] = start;
            seg_lengths[num_segments] = length;
            total_output += length;
            num_segments++;
        }
    }

    if (num_segments == 0 || total_output == 0) {
        free(crossings);
        free(seg_starts);
        free(seg_lengths);
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "No valid segments found");
        return NULL;
    }

    /*
     * Step 3: Create output buffer and apply envelopes
     */
    cdp_lib_buffer* output = cdp_lib_buffer_create(total_output, 1, mono->sample_rate);
    if (output == NULL) {
        free(crossings);
        free(seg_starts);
        free(seg_lengths);
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Failed to allocate output buffer");
        return NULL;
    }

    size_t out_pos = 0;
    for (size_t s = 0; s < num_segments; s++) {
        size_t start = seg_starts[s];
        size_t length = seg_lengths[s];

        /* Check if segment passes min_level threshold */
        if (min_level > 0.0) {
            float max_sample = 0.0f;
            for (size_t j = 0; j < length; j++) {
                float abs_val = fabsf(mono->data[start + j]);
                if (abs_val > max_sample) max_sample = abs_val;
            }
            if (max_sample < (float)min_level) {
                /* Skip this segment - too quiet */
                /* Reduce total output size */
                total_output -= length;
                continue;
            }
        }

        /* Apply decaying envelope to this segment */
        for (size_t j = 0; j < length; j++) {
            double position = (double)j / (double)length;
            double ee = pow(1.0 - position, exponent);
            output->data[out_pos++] = (float)(mono->data[start + j] * ee);
        }
    }

    /* Resize output if we skipped segments */
    if (out_pos < total_output) {
        output->length = out_pos;
    }

    free(crossings);
    free(seg_starts);
    free(seg_lengths);
    cdp_lib_buffer_free(mono);

    return output;
}

/*
 * Helper: Find waveset group starting near a given sample position.
 * Returns the start sample and length of the waveset group.
 */
static int find_waveset_group(const float* data, size_t num_samples, size_t target_pos,
                               size_t unit_samples, size_t* out_start, size_t* out_length) {
    /* Clamp target position */
    if (target_pos >= num_samples) {
        target_pos = num_samples - 1;
    }

    /* Find the zero crossing nearest to target_pos going backward */
    size_t start = target_pos;
    while (start > 0) {
        if ((data[start-1] >= 0 && data[start] < 0) ||
            (data[start-1] < 0 && data[start] >= 0)) {
            break;
        }
        start--;
    }

    /* Find the initial phase at start */
    int initial_phase = 0;
    size_t i = start;
    while (i < num_samples && initial_phase == 0) {
        if (data[i] > 0) initial_phase = 1;
        else if (data[i] < 0) initial_phase = -1;
        i++;
    }

    if (initial_phase == 0) {
        *out_start = start;
        *out_length = 0;
        return 0;
    }

    /* Find end of waveset group (approximately unit_samples in size) */
    int phase = initial_phase;
    size_t group_samples = 0;
    i = start;

    while (i < num_samples && group_samples < unit_samples * 2) {
        float sample = data[i];

        if (initial_phase == 1) {
            /* Positive-first waveset */
            if (phase == 1 && sample < 0) {
                phase = -1;
            } else if (phase == -1 && sample >= 0) {
                /* Completed one waveset */
                phase = 1;
                if (group_samples >= unit_samples) break;
            }
        } else {
            /* Negative-first waveset */
            if (phase == -1 && sample > 0) {
                phase = 1;
            } else if (phase == 1 && sample <= 0) {
                /* Completed one waveset */
                phase = -1;
                if (group_samples >= unit_samples) break;
            }
        }
        group_samples++;
        i++;
    }

    *out_start = start;
    *out_length = (i > start) ? (i - start) : 1;
    return 1;
}

/*
 * Helper: Interpolate samples between two buffers.
 * Handles different lengths by time-stretching the shorter one.
 */
static void interpolate_wavesets(const float* buf1, size_t len1,
                                  const float* buf2, size_t len2,
                                  float* output, size_t out_len,
                                  double interp_factor) {
    size_t max_len = (len1 > len2) ? len1 : len2;

    for (size_t i = 0; i < out_len; i++) {
        /* Position in output (0-1) */
        double out_pos = (double)i / (double)out_len;

        /* Read from buf1 with time stretch if needed */
        double pos1 = out_pos * (double)len1;
        size_t idx1 = (size_t)pos1;
        double frac1 = pos1 - (double)idx1;
        if (idx1 >= len1 - 1) { idx1 = len1 - 2; frac1 = 1.0; }
        if (idx1 >= len1) idx1 = len1 - 1;
        float val1 = (float)((1.0 - frac1) * buf1[idx1] + frac1 * buf1[idx1 + 1]);

        /* Read from buf2 with time stretch if needed */
        double pos2 = out_pos * (double)len2;
        size_t idx2 = (size_t)pos2;
        double frac2 = pos2 - (double)idx2;
        if (idx2 >= len2 - 1) { idx2 = len2 - 2; frac2 = 1.0; }
        if (idx2 >= len2) idx2 = len2 - 1;
        float val2 = (float)((1.0 - frac2) * buf2[idx2] + frac2 * buf2[idx2 + 1]);

        /* Interpolate between the two values */
        output[i] = (float)((1.0 - interp_factor) * val1 + interp_factor * val2);
    }
}

cdp_lib_buffer* cdp_lib_distort_mark(cdp_lib_ctx* ctx,
                                      const cdp_lib_buffer* input,
                                      const double* markers,
                                      int marker_count,
                                      double unit_ms,
                                      double stretch,
                                      double random,
                                      int flip_phase,
                                      unsigned int seed) {
    if (ctx == NULL || input == NULL || markers == NULL) {
        return NULL;
    }

    if (marker_count < 2) {
        cdp_lib_set_error(ctx, "marker_count must be at least 2");
        return NULL;
    }

    if (unit_ms < 1.0 || unit_ms > 100.0) {
        cdp_lib_set_error(ctx, "unit_ms must be between 1.0 and 100.0");
        return NULL;
    }

    if (stretch < 0.5 || stretch > 2.0) {
        cdp_lib_set_error(ctx, "stretch must be between 0.5 and 2.0");
        return NULL;
    }

    if (random < 0.0 || random > 1.0) {
        cdp_lib_set_error(ctx, "random must be between 0.0 and 1.0");
        return NULL;
    }

    /* Convert to mono if needed */
    cdp_lib_buffer* mono = cdp_lib_to_mono(ctx, input);
    if (mono == NULL) return NULL;

    size_t num_samples = mono->length;
    int sample_rate = mono->sample_rate;
    double duration = (double)num_samples / (double)sample_rate;

    /* Calculate unit size in samples */
    size_t unit_samples = (size_t)(unit_ms * sample_rate / 1000.0);
    if (unit_samples < 10) unit_samples = 10;

    /* Validate markers are in range and sorted */
    for (int i = 0; i < marker_count; i++) {
        if (markers[i] < 0.0 || markers[i] > duration) {
            cdp_lib_buffer_free(mono);
            cdp_lib_set_error(ctx, "Marker time out of range");
            return NULL;
        }
        if (i > 0 && markers[i] <= markers[i-1]) {
            cdp_lib_buffer_free(mono);
            cdp_lib_set_error(ctx, "Markers must be in ascending order");
            return NULL;
        }
    }

    /* Initialize the context PRNG (see the note in distort_shuffle). */
    cdp_lib_seed(ctx, seed);

    /* Find waveset groups at each marker */
    size_t* group_starts = (size_t*)malloc(marker_count * sizeof(size_t));
    size_t* group_lengths = (size_t*)malloc(marker_count * sizeof(size_t));
    if (group_starts == NULL || group_lengths == NULL) {
        if (group_starts) free(group_starts);
        if (group_lengths) free(group_lengths);
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Failed to allocate group arrays");
        return NULL;
    }

    for (int i = 0; i < marker_count; i++) {
        size_t target_pos = (size_t)(markers[i] * sample_rate);
        find_waveset_group(mono->data, num_samples, target_pos, unit_samples,
                           &group_starts[i], &group_lengths[i]);

        /* Ensure non-zero length */
        if (group_lengths[i] == 0) {
            group_lengths[i] = unit_samples;
            if (group_starts[i] + group_lengths[i] > num_samples) {
                group_lengths[i] = num_samples - group_starts[i];
            }
        }
    }

    /*
     * Calculate output size:
     * For each pair of adjacent markers, we create interpolated wavesets.
     * Number of interps depends on time between markers and stretch factor.
     */
    size_t total_output = 0;
    int* interp_counts = (int*)malloc((marker_count - 1) * sizeof(int));
    if (interp_counts == NULL) {
        free(group_starts);
        free(group_lengths);
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Failed to allocate interp counts");
        return NULL;
    }

    for (int i = 0; i < marker_count - 1; i++) {
        /* Time between markers in samples */
        double time_between = (markers[i+1] - markers[i]) * sample_rate;
        /* Average group length */
        double avg_len = (group_lengths[i] + group_lengths[i+1]) / 2.0;
        /* Number of interpolated wavesets */
        int count = (int)(time_between * stretch / avg_len);
        if (count < 2) count = 2;
        interp_counts[i] = count;

        /* Add to total output */
        for (int j = 0; j < count; j++) {
            double t = (double)j / (double)(count - 1);
            size_t len = (size_t)((1.0 - t) * group_lengths[i] + t * group_lengths[i+1]);
            /* Apply randomization */
            if (random > 0.0) {
                double rnd = cdp_lib_random(ctx) * 0.5 * random;
                len = (size_t)(len * (1.0 - rnd));
            }
            if (len < 1) len = 1;
            total_output += len;
        }
    }

    /* Create output buffer */
    cdp_lib_buffer* output = cdp_lib_buffer_create(total_output, 1, sample_rate);
    if (output == NULL) {
        free(group_starts);
        free(group_lengths);
        free(interp_counts);
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Failed to allocate output buffer");
        return NULL;
    }

    /* Allocate temp buffer for interpolation */
    size_t max_group_len = 0;
    for (int i = 0; i < marker_count; i++) {
        if (group_lengths[i] > max_group_len) max_group_len = group_lengths[i];
    }
    float* temp_buf = (float*)malloc((max_group_len + 1) * sizeof(float));
    if (temp_buf == NULL) {
        free(group_starts);
        free(group_lengths);
        free(interp_counts);
        cdp_lib_buffer_free(mono);
        cdp_lib_buffer_free(output);
        cdp_lib_set_error(ctx, "Failed to allocate temp buffer");
        return NULL;
    }

    /* Generate interpolated wavesets */
    size_t out_pos = 0;
    int invert_phase = 0;

    /* Reseed so this pass is reproducible independently of the measuring
       pass above, which also draws from the PRNG. */
    cdp_lib_seed(ctx, seed);

    for (int i = 0; i < marker_count - 1; i++) {
        float* buf1 = mono->data + group_starts[i];
        size_t len1 = group_lengths[i];
        float* buf2 = mono->data + group_starts[i+1];
        size_t len2 = group_lengths[i+1];

        int count = interp_counts[i];

        for (int j = 0; j < count; j++) {
            /* Interpolation factor (0 to 1) */
            double t = (count > 1) ? (double)j / (double)(count - 1) : 0.0;

            /* Calculate output length for this waveset */
            size_t out_len = (size_t)((1.0 - t) * len1 + t * len2);

            /* Apply randomization */
            if (random > 0.0) {
                double rnd = cdp_lib_random(ctx) * 0.5 * random;
                out_len = (size_t)(out_len * (1.0 - rnd));
            }
            if (out_len < 1) out_len = 1;
            if (out_pos + out_len > total_output) out_len = total_output - out_pos;

            /* Interpolate between the two waveset groups */
            interpolate_wavesets(buf1, len1, buf2, len2, temp_buf, out_len, t);

            /* Copy to output, with optional phase flip */
            if (flip_phase && invert_phase) {
                for (size_t k = 0; k < out_len; k++) {
                    output->data[out_pos + k] = -temp_buf[k];
                }
            } else {
                memcpy(output->data + out_pos, temp_buf, out_len * sizeof(float));
            }

            out_pos += out_len;
            if (flip_phase) invert_phase = !invert_phase;
        }
    }

    /* Trim output if we wrote less than expected */
    if (out_pos < total_output) {
        output->length = out_pos;
    }

    free(group_starts);
    free(group_lengths);
    free(interp_counts);
    free(temp_buf);
    cdp_lib_buffer_free(mono);

    return output;
}

cdp_lib_buffer* cdp_lib_distort_repeat(cdp_lib_ctx* ctx,
                                        const cdp_lib_buffer* input,
                                        int multiplier,
                                        int cycle_count,
                                        int skip_cycles,
                                        double splice_ms,
                                        int mode) {
    if (ctx == NULL || input == NULL) {
        return NULL;
    }

    if (multiplier < 1 || multiplier > 100) {
        cdp_lib_set_error(ctx, "multiplier must be between 1 and 100");
        return NULL;
    }

    if (cycle_count < 1 || cycle_count > 100) {
        cdp_lib_set_error(ctx, "cycle_count must be between 1 and 100");
        return NULL;
    }

    if (skip_cycles < 0) {
        cdp_lib_set_error(ctx, "skip_cycles must be 0 or greater");
        return NULL;
    }

    if (splice_ms < 1.0 || splice_ms > 50.0) {
        cdp_lib_set_error(ctx, "splice_ms must be between 1.0 and 50.0");
        return NULL;
    }

    if (mode < 0 || mode > 1) {
        cdp_lib_set_error(ctx, "mode must be 0 or 1");
        return NULL;
    }

    /* Convert to mono if needed */
    cdp_lib_buffer* mono = cdp_lib_to_mono(ctx, input);
    if (mono == NULL) return NULL;

    size_t num_samples = mono->length;
    int sample_rate = mono->sample_rate;
    int splice_samples = (int)(splice_ms * sample_rate / 1000.0);

    /*
     * Step 1: Find all waveset boundaries (zero crossings that complete a cycle)
     */
    size_t max_wavesets = num_samples / 10 + 100;
    size_t* waveset_starts = (size_t*)malloc(max_wavesets * sizeof(size_t));
    if (waveset_starts == NULL) {
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Failed to allocate waveset array");
        return NULL;
    }

    size_t waveset_count = 0;
    int start_phase = 0;
    int zero_crossed = 0;

    for (size_t i = 0; i < num_samples && waveset_count < max_wavesets; i++) {
        float sample = mono->data[i];

        if (start_phase == 0) {
            /* Looking for initial phase */
            if (sample > 0) {
                start_phase = 1;
                waveset_starts[waveset_count++] = i;
            } else if (sample < 0) {
                start_phase = -1;
                waveset_starts[waveset_count++] = i;
            }
        } else if (start_phase == 1) {
            /* Started positive, looking for return to positive after negative */
            if (zero_crossed && sample > 0) {
                waveset_starts[waveset_count++] = i;
                zero_crossed = 0;
            }
            if (!zero_crossed && sample < 0) {
                zero_crossed = 1;
            }
        } else {
            /* Started negative, looking for return to negative after positive */
            if (zero_crossed && sample < 0) {
                waveset_starts[waveset_count++] = i;
                zero_crossed = 0;
            }
            if (!zero_crossed && sample > 0) {
                zero_crossed = 1;
            }
        }
    }

    /* Skip initial cycles if requested */
    if (skip_cycles > 0) {
        if ((size_t)skip_cycles >= waveset_count) {
            free(waveset_starts);
            cdp_lib_buffer_free(mono);
            cdp_lib_set_error(ctx, "skip_cycles exceeds available wavesets");
            return NULL;
        }
        size_t new_count = waveset_count - skip_cycles;
        memmove(waveset_starts, waveset_starts + skip_cycles, new_count * sizeof(size_t));
        waveset_count = new_count;
    }

    if (waveset_count < (size_t)cycle_count + 1) {
        free(waveset_starts);
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Not enough wavesets for cycle_count");
        return NULL;
    }

    /*
     * Step 2: Calculate output size
     */
    size_t total_output = 0;
    size_t start_waveset = 0;

    while (start_waveset + cycle_count < waveset_count) {
        size_t start_sample = waveset_starts[start_waveset];
        size_t end_sample = waveset_starts[start_waveset + cycle_count];
        size_t group_samples = end_sample - start_sample;

        /* Each group is repeated 'multiplier' times */
        total_output += group_samples * multiplier;

        if (mode == 0) {
            /* Time-stretch mode: advance to next group */
            start_waveset += cycle_count;
        } else {
            /* No-stretch mode: skip ahead based on output position */
            size_t output_pos = total_output;
            /* Find waveset nearest to where we'd be without stretching */
            size_t target_sample = waveset_starts[0] + output_pos;
            while (start_waveset < waveset_count - 1 &&
                   waveset_starts[start_waveset] < target_sample) {
                start_waveset++;
            }
            /* Choose the nearest waveset */
            if (start_waveset > 0) {
                size_t diff_prev = target_sample - waveset_starts[start_waveset - 1];
                size_t diff_curr = waveset_starts[start_waveset] - target_sample;
                if (diff_prev < diff_curr) {
                    start_waveset--;
                }
            }
        }
    }

    if (total_output == 0) {
        free(waveset_starts);
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "No output generated");
        return NULL;
    }

    /*
     * Step 3: Create output buffer and generate repeated wavesets
     */
    cdp_lib_buffer* output = cdp_lib_buffer_create(total_output, 1, sample_rate);
    if (output == NULL) {
        free(waveset_starts);
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Failed to allocate output buffer");
        return NULL;
    }

    size_t out_pos = 0;
    start_waveset = 0;

    while (start_waveset + cycle_count < waveset_count && out_pos < total_output) {
        size_t start_sample = waveset_starts[start_waveset];
        size_t end_sample = waveset_starts[start_waveset + cycle_count];
        size_t group_samples = end_sample - start_sample;
        size_t total_rep_samples = group_samples * multiplier;

        /* Determine splice length for this group */
        int splic = splice_samples;
        if (total_rep_samples <= (size_t)(splic * 2)) {
            splic = (int)(total_rep_samples / 2);
        }

        size_t total_written = 0;
        size_t reverse_count = total_rep_samples - 1;

        /* Repeat the group 'multiplier' times */
        for (int rep = 0; rep < multiplier; rep++) {
            for (size_t i = 0; i < group_samples && out_pos < total_output; i++) {
                float sample = mono->data[start_sample + i];

                /* Apply splices at start and end of entire repeated section */
                if (total_written < (size_t)splic) {
                    /* Fade in at start */
                    double ratio = (double)total_written / (double)splic;
                    sample = (float)(sample * ratio);
                } else if (reverse_count < (size_t)splic) {
                    /* Fade out at end */
                    double ratio = (double)reverse_count / (double)splic;
                    sample = (float)(sample * ratio);
                }

                output->data[out_pos++] = sample;
                total_written++;
                reverse_count--;
            }
        }

        /* Advance to next group */
        if (mode == 0) {
            /* Time-stretch mode: advance to next group */
            start_waveset += cycle_count;
        } else {
            /* No-stretch mode: skip ahead based on output position */
            size_t target_sample = waveset_starts[0] + out_pos;
            while (start_waveset < waveset_count - 1 &&
                   waveset_starts[start_waveset] < target_sample) {
                start_waveset++;
            }
            if (start_waveset > 0) {
                size_t diff_prev = target_sample - waveset_starts[start_waveset - 1];
                size_t diff_curr = waveset_starts[start_waveset] - target_sample;
                if (diff_prev < diff_curr) {
                    start_waveset--;
                }
            }
        }
    }

    /* Trim output if needed */
    if (out_pos < total_output) {
        output->length = out_pos;
    }

    free(waveset_starts);
    cdp_lib_buffer_free(mono);

    return output;
}

cdp_lib_buffer* cdp_lib_distort_shift(cdp_lib_ctx* ctx,
                                       const cdp_lib_buffer* input,
                                       int group_size,
                                       int shift,
                                       int mode) {
    if (ctx == NULL || input == NULL) {
        return NULL;
    }

    if (group_size < 1 || group_size > 50) {
        cdp_lib_set_error(ctx, "group_size must be between 1 and 50");
        return NULL;
    }

    if (mode == 0 && (shift < 1 || shift > 50)) {
        cdp_lib_set_error(ctx, "shift must be between 1 and 50");
        return NULL;
    }

    if (mode < 0 || mode > 1) {
        cdp_lib_set_error(ctx, "mode must be 0 or 1");
        return NULL;
    }

    /* Convert to mono if needed */
    cdp_lib_buffer* mono = cdp_lib_to_mono(ctx, input);
    if (mono == NULL) return NULL;

    size_t num_samples = mono->length;

    /*
     * Step 1: Find all half-waveset boundaries (zero crossings)
     * A half-waveset is from one zero crossing to the next.
     */
    size_t max_half_wavesets = num_samples / 5 + 100;
    size_t* half_ws_starts = (size_t*)malloc(max_half_wavesets * sizeof(size_t));
    size_t* half_ws_lengths = (size_t*)malloc(max_half_wavesets * sizeof(size_t));
    if (half_ws_starts == NULL || half_ws_lengths == NULL) {
        if (half_ws_starts) free(half_ws_starts);
        if (half_ws_lengths) free(half_ws_lengths);
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Failed to allocate half-waveset arrays");
        return NULL;
    }

    size_t half_ws_count = 0;
    int current_sign = 0;  /* 0 = zero/unknown, 1 = positive, -1 = negative */

    /* Find initial sign */
    size_t i = 0;
    while (i < num_samples && current_sign == 0) {
        if (mono->data[i] > 0) current_sign = 1;
        else if (mono->data[i] < 0) current_sign = -1;
        i++;
    }

    if (current_sign == 0) {
        free(half_ws_starts);
        free(half_ws_lengths);
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "No signal found");
        return NULL;
    }

    /* Start first half-waveset */
    size_t current_start = 0;
    half_ws_starts[half_ws_count] = 0;

    /* Find all zero crossings */
    for (; i < num_samples && half_ws_count < max_half_wavesets - 1; i++) {
        int new_sign = 0;
        if (mono->data[i] > 0) new_sign = 1;
        else if (mono->data[i] < 0) new_sign = -1;

        if (new_sign != 0 && new_sign != current_sign) {
            /* Zero crossing found */
            half_ws_lengths[half_ws_count] = i - current_start;
            half_ws_count++;
            half_ws_starts[half_ws_count] = i;
            current_start = i;
            current_sign = new_sign;
        }
    }
    /* Add final half-waveset */
    if (current_start < num_samples) {
        half_ws_lengths[half_ws_count] = num_samples - current_start;
        half_ws_count++;
    }

    if (half_ws_count == 0) {
        free(half_ws_starts);
        free(half_ws_lengths);
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "No half-wavesets found");
        return NULL;
    }

    /*
     * Step 2: Calculate group parameters
     * gpsize = (group_size * 2) - 1 gives the number of half-wavesets per group
     */
    int gpsize = (group_size * 2) - 1;
    if (gpsize < 1) gpsize = 1;

    size_t num_groups = half_ws_count / gpsize;
    if (num_groups < 2) {
        free(half_ws_starts);
        free(half_ws_lengths);
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Not enough half-wavesets for grouping");
        return NULL;
    }

    /*
     * Step 3: Create output buffer (same size as input)
     */
    cdp_lib_buffer* output = cdp_lib_buffer_create(num_samples, 1, mono->sample_rate);
    if (output == NULL) {
        free(half_ws_starts);
        free(half_ws_lengths);
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Failed to allocate output buffer");
        return NULL;
    }

    size_t out_pos = 0;

    if (mode == 0) {
        /*
         * Mode 0: Shift alternate groups forward
         * For each pair of groups: copy first group, then copy shifted second group
         */
        int shift_amount = 2 * shift * gpsize;  /* Shift in half-wavesets */

        for (size_t k = 0; k < half_ws_count; k += gpsize * 2) {
            /* Copy first group (non-shifted) */
            size_t kend = k + gpsize;
            if (kend > half_ws_count) kend = half_ws_count;

            for (size_t ws = k; ws < kend; ws++) {
                size_t start = half_ws_starts[ws];
                size_t len = half_ws_lengths[ws];
                for (size_t s = 0; s < len && out_pos < num_samples; s++) {
                    output->data[out_pos++] = mono->data[start + s];
                }
            }

            /* Copy second group (shifted) */
            int j = (int)(k + gpsize) - shift_amount;
            while (j < 0) j += (int)half_ws_count;
            size_t jend = (size_t)j + gpsize;

            for (size_t ws_idx = (size_t)j; ws_idx < jend; ws_idx++) {
                size_t ws = ws_idx % half_ws_count;  /* Wrap around */
                size_t start = half_ws_starts[ws];
                size_t len = half_ws_lengths[ws];
                for (size_t s = 0; s < len && out_pos < num_samples; s++) {
                    output->data[out_pos++] = mono->data[start + s];
                }
            }
        }
    } else {
        /*
         * Mode 1: Swap adjacent groups
         * Pattern for groups A B C D E F: outputs as A B' C D' E F'
         * where B' and C swap, D' and E swap, etc.
         * Actually the pattern is more complex: A B C D -> A D C B pattern
         */

        /* Simplified swap: swap pairs of adjacent groups */
        for (size_t k = 0; k < half_ws_count; k += gpsize * 2) {
            size_t first_start = k;
            size_t first_end = k + gpsize;
            if (first_end > half_ws_count) first_end = half_ws_count;

            size_t second_start = k + gpsize;
            size_t second_end = second_start + gpsize;
            if (second_end > half_ws_count) second_end = half_ws_count;

            /* Copy second group first (swap) */
            if (second_start < half_ws_count) {
                for (size_t ws = second_start; ws < second_end; ws++) {
                    size_t start = half_ws_starts[ws];
                    size_t len = half_ws_lengths[ws];
                    for (size_t s = 0; s < len && out_pos < num_samples; s++) {
                        output->data[out_pos++] = mono->data[start + s];
                    }
                }
            }

            /* Then copy first group */
            for (size_t ws = first_start; ws < first_end; ws++) {
                size_t start = half_ws_starts[ws];
                size_t len = half_ws_lengths[ws];
                for (size_t s = 0; s < len && out_pos < num_samples; s++) {
                    output->data[out_pos++] = mono->data[start + s];
                }
            }
        }
    }

    /* Trim output if needed */
    if (out_pos < num_samples) {
        output->length = out_pos;
    }

    free(half_ws_starts);
    free(half_ws_lengths);
    cdp_lib_buffer_free(mono);

    return output;
}

/*
 * Progressive warp distortion with modular sample folding.
 *
 * The algorithm:
 * 1. Extract amplitude envelope (20ms windows) for gain normalization
 * 2. For each sample, add progressive incrval
 * 3. Apply modular folding transformation:
 *    - val = sample + incrval
 *    - i = floor(val), make even (round up if odd)
 *    - j = i/2
 *    - if j is odd: val = i - val (fold back)
 *    - else: val = val - i (fold forward)
 * 4. Multiply by envelope gain for normalization
 * 5. In mode 0, incrval increments every sample
 *    In mode 1, incrval increments every N wavesets
 */
cdp_lib_buffer* cdp_lib_distort_warp(cdp_lib_ctx* ctx,
                                      const cdp_lib_buffer* input,
                                      double warp,
                                      int mode,
                                      int waveset_count) {
    if (ctx == NULL || input == NULL) {
        if (ctx) cdp_lib_set_error(ctx, "NULL input");
        return NULL;
    }

    if (warp < 0.0001 || warp > 0.1) {
        cdp_lib_set_error(ctx, "warp must be between 0.0001 and 0.1");
        return NULL;
    }

    if (mode < 0 || mode > 1) {
        cdp_lib_set_error(ctx, "mode must be 0 or 1");
        return NULL;
    }

    if (mode == 1 && waveset_count < 1) {
        cdp_lib_set_error(ctx, "waveset_count must be >= 1 for mode 1");
        return NULL;
    }
    if (waveset_count > 256) waveset_count = 256;

    /* For mode 1, we need mono input */
    cdp_lib_buffer* working;
    int chans;

    if (mode == 1) {
        /* Mode 1 requires mono */
        working = cdp_lib_to_mono(ctx, input);
        if (working == NULL) return NULL;
        chans = 1;
    } else {
        /* Mode 0 works with stereo */
        working = cdp_lib_buffer_create(input->length, input->channels, input->sample_rate);
        if (working == NULL) {
            cdp_lib_set_error(ctx, "Failed to allocate working buffer");
            return NULL;
        }
        memcpy(working->data, input->data, input->length * sizeof(float));
        chans = input->channels;
    }

    size_t num_samples = working->length;
    size_t num_frames = num_samples / chans;
    int sample_rate = working->sample_rate;

    /*
     * Step 1: Extract amplitude envelope using 20ms windows
     */
    #define ENV_WIN_MS 20
    int winsize = (int)((ENV_WIN_MS * 0.001) * sample_rate);
    if (winsize < 1) winsize = 1;

    size_t env_size = num_frames / winsize + 2;
    double* env = (double*)malloc(env_size * sizeof(double));
    if (env == NULL) {
        cdp_lib_buffer_free(working);
        cdp_lib_set_error(ctx, "Failed to allocate envelope buffer");
        return NULL;
    }

    /* Extract envelope (max sample in each window) */
    size_t env_idx = 0;
    int envcnt = 0;
    double maxsamp = 0.0;

    for (size_t i = 0; i < num_frames; i++) {
        double val = fabs(working->data[i * chans]);  /* Use first channel for envelope */
        if (val > maxsamp) maxsamp = val;
        envcnt++;
        if (envcnt >= winsize) {
            env[env_idx++] = maxsamp;
            maxsamp = 0.0;
            envcnt = 0;
        }
    }
    /* Final partial window */
    if (envcnt > 0 && env_idx < env_size) {
        env[env_idx++] = maxsamp;
    }
    /* Wraparound point */
    if (env_idx < env_size) {
        env[env_idx] = env[env_idx > 0 ? env_idx - 1 : 0];
        env_idx++;
    }
    size_t actual_env_size = env_idx;

    /*
     * Step 2: Apply warp transformation
     */
    cdp_lib_buffer* output = cdp_lib_buffer_create(num_samples, chans, sample_rate);
    if (output == NULL) {
        free(env);
        cdp_lib_buffer_free(working);
        cdp_lib_set_error(ctx, "Failed to allocate output buffer");
        return NULL;
    }

    double incrval = warp;
    double srate = (double)sample_rate;

    if (mode == 0) {
        /*
         * Mode 0: Warp increment per sample (samplewise)
         * Works with stereo - applies same warp to all channels
         */
        for (size_t frame = 0; frame < num_frames; frame++) {
            /* Calculate envelope gain with interpolation */
            size_t env_pos = frame / winsize;
            if (env_pos >= actual_env_size - 1) env_pos = actual_env_size - 2;

            double gain = env[env_pos];
            double nextgain = env[env_pos + 1];
            size_t partwindow = frame - (winsize * env_pos);
            double frac = (double)partwindow / (double)winsize;
            double diff = (nextgain - gain) * frac;
            gain += diff;

            /* Avoid division by zero */
            if (gain < 0.0001) gain = 0.0001;

            for (int ch = 0; ch < chans; ch++) {
                size_t idx = frame * chans + ch;
                double val = working->data[idx] + incrval;

                /* Apply sin curve to fractional part (smoothing) */
                double thisincr = val - floor(val);
                thisincr = sin(thisincr * M_PI / 2.0);
                val = floor(val) + thisincr;

                /* Modular folding transformation */
                int i = (int)floor(val);
                if (i % 2 != 0) i++;  /* Make even (round up if odd) */
                int j = i / 2;

                if (j % 2 != 0) {
                    /* Odd j: fold back */
                    val = (double)i - val;
                } else {
                    /* Even j: fold forward */
                    val = val - (double)i;
                }

                output->data[idx] = (float)(val * gain);
            }

            /* Increment warp value */
            incrval += warp;
        }
    } else {
        /*
         * Mode 1: Warp increment per waveset group (wavesetwise)
         * Mono only
         */
        int init = 1;
        int initial_phase = 0;
        int phase = 0;
        int waveset_cnt = 0;

        for (size_t frame = 0; frame < num_frames; frame++) {
            /* Calculate envelope gain with interpolation */
            size_t env_pos = frame / winsize;
            if (env_pos >= actual_env_size - 1) env_pos = actual_env_size - 2;

            double gain = env[env_pos];
            double nextgain = env[env_pos + 1];
            size_t partwindow = frame - (winsize * env_pos);
            double frac = (double)partwindow / (double)winsize;
            double diff = (nextgain - gain) * frac;
            gain += diff;

            if (gain < 0.0001) gain = 0.0001;

            float sample = working->data[frame];

            /* Track phase for waveset detection */
            if (init) {
                if (sample == 0.0f) {
                    output->data[frame] = 0.0f;
                    continue;
                }
                if (sample > 0.0f) {
                    initial_phase = 1;
                } else {
                    initial_phase = -1;
                }
                phase = initial_phase;
                init = 0;
            }

            /* Detect waveset boundaries (zero crossings completing a full cycle) */
            if (initial_phase == 1) {
                if (phase == initial_phase) {
                    if (sample < 0.0f) {
                        phase = -phase;  /* Entered negative phase */
                    }
                } else {
                    if (sample >= 0.0f) {
                        /* Completed full waveset (positive->negative->positive) */
                        waveset_cnt++;
                        phase = -phase;
                        if (waveset_cnt >= waveset_count) {
                            waveset_cnt = 0;
                            incrval += warp;
                        }
                    }
                }
            } else {
                if (phase == initial_phase) {
                    if (sample >= 0.0f) {
                        phase = -phase;  /* Entered positive phase */
                    }
                } else {
                    if (sample < 0.0f) {
                        /* Completed full waveset (negative->positive->negative) */
                        waveset_cnt++;
                        phase = -phase;
                        if (waveset_cnt >= waveset_count) {
                            waveset_cnt = 0;
                            incrval += warp;
                        }
                    }
                }
            }

            /* Apply warp transformation */
            double val = sample + incrval;

            /* Modular folding transformation */
            int i = (int)floor(val);
            if (i % 2 != 0) i++;
            int j = i / 2;

            if (j % 2 != 0) {
                val = (double)i - val;
            } else {
                val = val - (double)i;
            }

            output->data[frame] = (float)(val * gain);
        }
    }

    free(env);
    cdp_lib_buffer_free(working);

    return output;
}
