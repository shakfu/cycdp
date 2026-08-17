/*
 * CDP Granular Processing - Implementation
 *
 * Implements brassage, freeze, grain cloud, grain extend, and texture operations.
 */

#include "cdp_granular.h"
#include "cdp_lib_internal.h"
#include <time.h>

/*
 * Apply Hanning window to a grain
 */
static void apply_grain_window(float *grain, size_t size) {
    for (size_t i = 0; i < size; i++) {
        float window = 0.5f * (1.0f - cosf(2.0f * (float)M_PI * i / (size - 1)));
        grain[i] *= window;
    }
}

cdp_lib_buffer* cdp_lib_brassage(cdp_lib_ctx* ctx,
                                  const cdp_lib_buffer* input,
                                  double velocity,
                                  double density,
                                  double grainsize_ms,
                                  double scatter,
                                  double pitch_shift,
                                  double amp_variation,
                                  unsigned int seed) {
    if (ctx == NULL || input == NULL) {
        return NULL;
    }

    /* Validate parameters */
    if (velocity <= 0) velocity = 1.0;
    if (density <= 0) density = 1.0;
    if (grainsize_ms <= 0) grainsize_ms = 50.0;
    if (scatter < 0) scatter = 0;
    if (scatter > 1) scatter = 1;
    if (amp_variation < 0) amp_variation = 0;
    if (amp_variation > 1) amp_variation = 1;

    int sample_rate = input->sample_rate;

    /* Initialize PRNG */
    cdp_lib_seed(ctx, seed);

    /* Convert to mono if needed */
    cdp_lib_buffer* mono = cdp_lib_to_mono(ctx, input);
    if (mono == NULL) return NULL;

    size_t input_samples = mono->length;
    double input_duration = (double)input_samples / sample_rate;

    /* Calculate output duration based on velocity */
    double output_duration = input_duration / velocity;
    size_t output_samples = (size_t)(output_duration * sample_rate);

    /* Grain parameters */
    size_t grain_size = (size_t)(grainsize_ms * sample_rate / 1000.0);
    if (grain_size < 10) grain_size = 10;
    if (grain_size > input_samples) grain_size = input_samples;

    /* Calculate grain spacing based on density */
    double grain_spacing = grainsize_ms / density / 2.0 / 1000.0;  /* 50% overlap at density=1 */
    size_t hop_size = (size_t)(grain_spacing * sample_rate);
    if (hop_size < 1) hop_size = 1;

    /* Pitch shift as speed ratio */
    double pitch_ratio = pow(2.0, pitch_shift / 12.0);

    /* Allocate output buffer (initialize to zero) */
    cdp_lib_buffer* output = cdp_lib_buffer_create(output_samples, 1, sample_rate);
    if (output == NULL) {
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Failed to allocate output buffer");
        return NULL;
    }

    /* Allocate grain buffer */
    size_t max_grain = (size_t)(grain_size * 2);  /* Allow for pitch down */
    float *grain = (float*)malloc(max_grain * sizeof(float));
    if (grain == NULL) {
        cdp_lib_buffer_free(mono);
        cdp_lib_buffer_free(output);
        cdp_lib_set_error(ctx, "Failed to allocate grain buffer");
        return NULL;
    }

    /* Process grains */
    double src_pos = 0;  /* Position in source */
    size_t dst_pos = 0;  /* Position in destination */

    while (dst_pos < output_samples && src_pos < input_samples - grain_size) {
        /* Calculate actual grain size with pitch shift */
        size_t actual_grain = (size_t)(grain_size / pitch_ratio);
        if (actual_grain > max_grain) actual_grain = max_grain;
        if (actual_grain < 2) actual_grain = 2;

        /* Apply scatter to source position */
        double scattered_pos = src_pos;
        if (scatter > 0) {
            double scatter_range = grain_size * scatter * 2;
            scattered_pos += (cdp_lib_random(ctx) - 0.5) * scatter_range;
            if (scattered_pos < 0) scattered_pos = 0;
            if (scattered_pos > input_samples - grain_size)
                scattered_pos = input_samples - grain_size;
        }

        /* Extract grain with pitch shift (resampling) */
        for (size_t i = 0; i < actual_grain; i++) {
            double src_idx = scattered_pos + i * pitch_ratio;
            size_t idx0 = (size_t)src_idx;
            double frac = src_idx - idx0;

            if (idx0 + 1 >= input_samples) {
                grain[i] = 0;
            } else {
                /* Linear interpolation */
                grain[i] = (float)(mono->data[idx0] * (1.0 - frac) +
                                    mono->data[idx0 + 1] * frac);
            }
        }

        /* Apply window to grain */
        apply_grain_window(grain, actual_grain);

        /* Apply amplitude variation */
        double amp = 1.0;
        if (amp_variation > 0) {
            amp = 1.0 - amp_variation * cdp_lib_random(ctx);
        }

        /* Overlap-add grain to output */
        for (size_t i = 0; i < actual_grain && dst_pos + i < output_samples; i++) {
            output->data[dst_pos + i] += (float)(grain[i] * amp);
        }

        /* Advance positions */
        src_pos += hop_size * velocity;
        dst_pos += hop_size;
    }

    free(grain);
    cdp_lib_buffer_free(mono);

    cdp_lib_normalize_if_clipping(output, 1.0f);
    return output;
}

cdp_lib_buffer* cdp_lib_freeze(cdp_lib_ctx* ctx,
                                const cdp_lib_buffer* input,
                                double start_time,
                                double end_time,
                                double duration,
                                double delay,
                                double randomize,
                                double pitch_scatter,
                                double amp_cut,
                                double gain,
                                unsigned int seed) {
    if (ctx == NULL || input == NULL) {
        return NULL;
    }

    int sample_rate = input->sample_rate;

    /* Validate parameters */
    if (start_time < 0) start_time = 0;
    if (end_time <= start_time) {
        cdp_lib_set_error(ctx, "end_time must be greater than start_time");
        return NULL;
    }
    if (duration <= 0) {
        cdp_lib_set_error(ctx, "duration must be positive");
        return NULL;
    }
    if (delay <= 0) delay = 0.05;
    if (randomize < 0) randomize = 0;
    if (randomize > 1) randomize = 1;
    if (pitch_scatter < 0) pitch_scatter = 0;
    if (pitch_scatter > 12) pitch_scatter = 12;
    if (amp_cut < 0) amp_cut = 0;
    if (amp_cut > 1) amp_cut = 1;
    if (gain <= 0) gain = 1.0;

    /* Initialize PRNG */
    cdp_lib_seed(ctx, seed);

    /* Convert to mono if needed */
    cdp_lib_buffer* mono = cdp_lib_to_mono(ctx, input);
    if (mono == NULL) return NULL;

    size_t input_samples = mono->length;
    double input_duration = (double)input_samples / sample_rate;

    /* Clamp times to input duration */
    if (start_time >= input_duration) start_time = input_duration * 0.8;
    if (end_time > input_duration) end_time = input_duration;

    /* Calculate segment boundaries */
    size_t seg_start = (size_t)(start_time * sample_rate);
    size_t seg_end = (size_t)(end_time * sample_rate);
    if (seg_end > input_samples) seg_end = input_samples;
    size_t seg_len = seg_end - seg_start;

    if (seg_len < 10) {
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Segment too short");
        return NULL;
    }

    /* Calculate output size */
    size_t output_samples = (size_t)(duration * sample_rate);

    /* Allocate output buffer */
    cdp_lib_buffer* output = cdp_lib_buffer_create(output_samples, 1, sample_rate);
    if (output == NULL) {
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Failed to allocate output buffer");
        return NULL;
    }

    /* Allocate resampled segment buffer (for pitch shifting) */
    size_t max_seg = seg_len * 2;  /* Allow for pitch down */
    float *seg_buf = (float*)malloc(max_seg * sizeof(float));
    if (seg_buf == NULL) {
        cdp_lib_buffer_free(mono);
        cdp_lib_buffer_free(output);
        cdp_lib_set_error(ctx, "Failed to allocate segment buffer");
        return NULL;
    }

    /* Process iterations */
    size_t write_pos = 0;
    size_t base_delay_samples = (size_t)(delay * sample_rate);

    while (write_pos < output_samples) {
        /* Calculate pitch ratio for this iteration */
        double pitch_ratio = 1.0;
        if (pitch_scatter > 0) {
            double pitch_semitones = (cdp_lib_random(ctx) - 0.5) * 2 * pitch_scatter;
            pitch_ratio = pow(2.0, pitch_semitones / 12.0);
        }

        /* Resample segment with pitch shift */
        size_t resampled_len = (size_t)(seg_len / pitch_ratio);
        if (resampled_len > max_seg) resampled_len = max_seg;
        if (resampled_len < 1) resampled_len = 1;

        for (size_t i = 0; i < resampled_len; i++) {
            double src_idx = seg_start + i * pitch_ratio;
            size_t idx0 = (size_t)src_idx;
            double frac = src_idx - idx0;

            if (idx0 + 1 >= input_samples) {
                seg_buf[i] = 0;
            } else {
                seg_buf[i] = (float)(mono->data[idx0] * (1.0 - frac) +
                                     mono->data[idx0 + 1] * frac);
            }
        }

        /* Calculate amplitude for this iteration */
        double iter_amp = gain;
        if (amp_cut > 0) {
            iter_amp *= 1.0 - amp_cut * cdp_lib_random(ctx);
        }

        /* Apply fade envelope */
        size_t fade_len = resampled_len / 10;
        if (fade_len < 10) fade_len = 10;

        /* Crossfade and add to output */
        for (size_t i = 0; i < resampled_len && write_pos + i < output_samples; i++) {
            /* Fade envelope */
            double fade = 1.0;
            if (i < fade_len) {
                fade = (double)i / fade_len;
            } else if (i > resampled_len - fade_len) {
                fade = (double)(resampled_len - i) / fade_len;
            }

            output->data[write_pos + i] += (float)(seg_buf[i] * iter_amp * fade);
        }

        /* Calculate next delay */
        size_t iter_delay = base_delay_samples;
        if (randomize > 0) {
            double rand_factor = 1.0 + (cdp_lib_random(ctx) - 0.5) * 2 * randomize;
            iter_delay = (size_t)(iter_delay * rand_factor);
        }
        if (iter_delay < 1) iter_delay = 1;

        write_pos += iter_delay;
    }

    free(seg_buf);
    cdp_lib_buffer_free(mono);

    cdp_lib_normalize_if_clipping(output, 1.0f);
    return output;
}

/* Helper: find grains based on amplitude threshold */
static int find_grains(const float* data, size_t length, int sample_rate,
                       double gate, double grainsize_ms,
                       size_t** grain_starts, size_t** grain_lengths, size_t* grain_count) {

    size_t min_grain = (size_t)(grainsize_ms * sample_rate / 1000.0 * 0.5);
    size_t max_grain = (size_t)(grainsize_ms * sample_rate / 1000.0 * 2.0);
    if (min_grain < 10) min_grain = 10;

    /* First pass: count grains */
    size_t count = 0;
    int in_grain = 0;
    size_t grain_start = 0;

    for (size_t i = 0; i < length; i++) {
        float abs_val = fabsf(data[i]);
        if (!in_grain && abs_val > gate) {
            in_grain = 1;
            grain_start = i;
        } else if (in_grain && abs_val < gate * 0.5) {
            size_t glen = i - grain_start;
            if (glen >= min_grain && glen <= max_grain) {
                count++;
            }
            in_grain = 0;
        }
    }

    if (count == 0) {
        /* No grains found - use regular intervals */
        count = length / (size_t)(grainsize_ms * sample_rate / 1000.0);
        if (count < 1) count = 1;

        *grain_starts = (size_t*)malloc(count * sizeof(size_t));
        *grain_lengths = (size_t*)malloc(count * sizeof(size_t));
        if (*grain_starts == NULL || *grain_lengths == NULL) {
            free(*grain_starts);
            free(*grain_lengths);
            return -1;
        }

        size_t grain_size = (size_t)(grainsize_ms * sample_rate / 1000.0);
        for (size_t i = 0; i < count; i++) {
            (*grain_starts)[i] = i * grain_size;
            (*grain_lengths)[i] = grain_size;
            if ((*grain_starts)[i] + grain_size > length) {
                (*grain_lengths)[i] = length - (*grain_starts)[i];
            }
        }
        *grain_count = count;
        return 0;
    }

    /* Allocate arrays */
    *grain_starts = (size_t*)malloc(count * sizeof(size_t));
    *grain_lengths = (size_t*)malloc(count * sizeof(size_t));
    if (*grain_starts == NULL || *grain_lengths == NULL) {
        free(*grain_starts);
        free(*grain_lengths);
        return -1;
    }

    /* Second pass: record grain positions */
    size_t idx = 0;
    in_grain = 0;

    for (size_t i = 0; i < length && idx < count; i++) {
        float abs_val = fabsf(data[i]);
        if (!in_grain && abs_val > gate) {
            in_grain = 1;
            grain_start = i;
        } else if (in_grain && abs_val < gate * 0.5) {
            size_t glen = i - grain_start;
            if (glen >= min_grain && glen <= max_grain) {
                (*grain_starts)[idx] = grain_start;
                (*grain_lengths)[idx] = glen;
                idx++;
            }
            in_grain = 0;
        }
    }

    *grain_count = idx;
    return 0;
}

cdp_lib_buffer* cdp_lib_grain_cloud(cdp_lib_ctx* ctx,
                                     const cdp_lib_buffer* input,
                                     double gate,
                                     double grainsize_ms,
                                     double density,
                                     double duration,
                                     double scatter,
                                     unsigned int seed) {
    if (ctx == NULL || input == NULL) {
        return NULL;
    }

    /* Validate parameters */
    if (gate <= 0) gate = 0.1;
    if (gate > 1) gate = 1.0;
    if (grainsize_ms <= 0) grainsize_ms = 50.0;
    if (density <= 0) density = 10.0;
    if (duration <= 0) duration = (double)input->length / input->sample_rate;
    if (scatter < 0) scatter = 0;
    if (scatter > 1) scatter = 1;

    int sample_rate = input->sample_rate;

    /* Initialize PRNG */
    cdp_lib_seed(ctx, seed);

    /* Convert to mono if needed */
    cdp_lib_buffer* mono = cdp_lib_to_mono(ctx, input);
    if (mono == NULL) return NULL;

    /* Find grains in source */
    size_t* grain_starts = NULL;
    size_t* grain_lengths = NULL;
    size_t grain_count = 0;

    if (find_grains(mono->data, mono->length, sample_rate, gate, grainsize_ms,
                    &grain_starts, &grain_lengths, &grain_count) < 0) {
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Failed to allocate grain arrays");
        return NULL;
    }

    /* Calculate output size */
    size_t output_samples = (size_t)(duration * sample_rate);

    /* Allocate output buffer */
    cdp_lib_buffer* output = cdp_lib_buffer_create(output_samples, 1, sample_rate);
    if (output == NULL) {
        free(grain_starts);
        free(grain_lengths);
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Failed to allocate output buffer");
        return NULL;
    }

    /* Calculate grain interval based on density */
    double grain_interval = 1.0 / density;
    size_t hop_samples = (size_t)(grain_interval * sample_rate);
    if (hop_samples < 1) hop_samples = 1;

    /* Generate grain cloud */
    size_t write_pos = 0;

    while (write_pos < output_samples) {
        /* Select a random grain */
        size_t grain_idx = (size_t)(cdp_lib_random_u64(ctx) % grain_count);
        size_t src_start = grain_starts[grain_idx];
        size_t grain_len = grain_lengths[grain_idx];

        /* Apply scatter to write position */
        size_t actual_pos = write_pos;
        if (scatter > 0) {
            double scatter_range = hop_samples * scatter;
            int scatter_offset = (int)((cdp_lib_random(ctx) - 0.5) * 2 * scatter_range);
            if ((int)write_pos + scatter_offset >= 0) {
                actual_pos = write_pos + scatter_offset;
            }
        }

        /* Apply window and add grain to output */
        for (size_t i = 0; i < grain_len && actual_pos + i < output_samples; i++) {
            /* Hann window */
            double window = 0.5 * (1.0 - cos(2.0 * M_PI * i / grain_len));

            if (src_start + i < mono->length) {
                output->data[actual_pos + i] += (float)(mono->data[src_start + i] * window);
            }
        }

        write_pos += hop_samples;
    }

    free(grain_starts);
    free(grain_lengths);
    cdp_lib_buffer_free(mono);

    cdp_lib_normalize_if_clipping(output, 0.95f);
    return output;
}

cdp_lib_buffer* cdp_lib_grain_extend(cdp_lib_ctx* ctx,
                                      const cdp_lib_buffer* input,
                                      double grainsize_ms,
                                      double trough,
                                      double extension,
                                      double start_time,
                                      double end_time,
                                      unsigned int seed) {
    if (ctx == NULL || input == NULL) {
        return NULL;
    }

    /* Validate parameters */
    if (grainsize_ms <= 0) grainsize_ms = 15.0;
    if (trough <= 0) trough = 0.3;
    if (trough > 1) trough = 1.0;
    if (extension <= 0) extension = 1.0;

    int sample_rate = input->sample_rate;
    double input_duration = (double)input->length / sample_rate / input->channels;

    if (start_time < 0) start_time = 0;
    if (end_time <= start_time) end_time = input_duration;
    if (end_time > input_duration) end_time = input_duration;

    /* Initialize PRNG */
    cdp_lib_seed(ctx, seed);

    /* Convert to mono if needed */
    cdp_lib_buffer* mono = cdp_lib_to_mono(ctx, input);
    if (mono == NULL) return NULL;

    size_t input_samples = mono->length;

    /* Calculate segment boundaries */
    size_t seg_start = (size_t)(start_time * sample_rate);
    size_t seg_end = (size_t)(end_time * sample_rate);
    if (seg_end > input_samples) seg_end = input_samples;
    size_t seg_len = seg_end - seg_start;

    /* Find grains in segment using envelope tracking */
    size_t window_samples = (size_t)(grainsize_ms * sample_rate / 1000.0);
    if (window_samples < 10) window_samples = 10;

    /* Calculate envelope */
    size_t env_len = seg_len / window_samples + 1;
    float* envelope = (float*)malloc(env_len * sizeof(float));
    if (envelope == NULL) {
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Failed to allocate envelope buffer");
        return NULL;
    }

    for (size_t i = 0; i < env_len; i++) {
        size_t start = seg_start + i * window_samples;
        size_t end = start + window_samples;
        if (end > seg_end) end = seg_end;

        float max_val = 0;
        for (size_t j = start; j < end; j++) {
            float abs_val = fabsf(mono->data[j]);
            if (abs_val > max_val) max_val = abs_val;
        }
        envelope[i] = max_val;
    }

    /* Find grain boundaries (peaks in envelope) */
    size_t max_grains = env_len;
    size_t* grain_indices = (size_t*)malloc(max_grains * sizeof(size_t));
    if (grain_indices == NULL) {
        free(envelope);
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Failed to allocate grain indices");
        return NULL;
    }

    size_t grain_count = 0;
    for (size_t i = 1; i < env_len - 1 && grain_count < max_grains; i++) {
        /* Check if this is a peak */
        if (envelope[i] > envelope[i-1] && envelope[i] > envelope[i+1]) {
            /* Check trough depths on either side */
            float left_trough = envelope[i-1] / (envelope[i] + 0.0001f);
            float right_trough = envelope[i+1] / (envelope[i] + 0.0001f);

            if (left_trough < trough || right_trough < trough) {
                grain_indices[grain_count++] = i;
            }
        }
    }

    free(envelope);

    /* If no grains found, use regular spacing */
    if (grain_count == 0) {
        grain_count = seg_len / window_samples;
        if (grain_count < 1) grain_count = 1;
        for (size_t i = 0; i < grain_count; i++) {
            grain_indices[i] = i;
        }
    }

    /* Calculate output size */
    size_t output_samples = input_samples + (size_t)(extension * sample_rate);

    /* Allocate output buffer */
    cdp_lib_buffer* output = cdp_lib_buffer_create(output_samples, 1, sample_rate);
    if (output == NULL) {
        free(grain_indices);
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Failed to allocate output buffer");
        return NULL;
    }

    /* Copy audio before segment */
    for (size_t i = 0; i < seg_start && i < output_samples; i++) {
        output->data[i] = mono->data[i];
    }

    /* Generate extended grains */
    size_t write_pos = seg_start;
    size_t extension_samples = (size_t)(extension * sample_rate);
    size_t target_end = seg_start + seg_len + extension_samples;

    /* Create permutation array for grain order variation */
    size_t* perm = (size_t*)malloc(grain_count * sizeof(size_t));
    if (perm == NULL) {
        free(grain_indices);
        cdp_lib_buffer_free(output);
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Failed to allocate permutation array");
        return NULL;
    }

    for (size_t i = 0; i < grain_count; i++) {
        perm[i] = i;
    }

    /* Splice length for crossfades (15ms as per CDP) */
    size_t splice_len = (size_t)(15.0 * sample_rate / 1000.0);
    if (splice_len < 10) splice_len = 10;

    size_t perm_idx = 0;
    while (write_pos < target_end && write_pos < output_samples) {
        /* Shuffle permutation when we've used all grains */
        if (perm_idx >= grain_count) {
            for (size_t i = grain_count - 1; i > 0; i--) {
                size_t j = cdp_lib_random_u64(ctx) % (i + 1);
                size_t tmp = perm[i];
                perm[i] = perm[j];
                perm[j] = tmp;
            }
            perm_idx = 0;
        }

        /* Get grain boundaries */
        size_t grain_idx = grain_indices[perm[perm_idx++]];
        size_t g_start = seg_start + grain_idx * window_samples;
        size_t grain_end;

        /* Find next grain or use segment end */
        if (perm_idx < grain_count) {
            size_t next_idx = grain_indices[perm_idx < grain_count ? perm[perm_idx] : 0];
            grain_end = seg_start + next_idx * window_samples;
        } else {
            grain_end = g_start + window_samples * 2;
        }

        if (grain_end > seg_end) grain_end = seg_end;
        size_t grain_len = grain_end - g_start;
        if (grain_len < window_samples) grain_len = window_samples;

        /* Copy grain with crossfade */
        for (size_t i = 0; i < grain_len && write_pos + i < output_samples; i++) {
            /* Crossfade envelope */
            double fade = 1.0;
            if (i < splice_len) {
                fade = (double)i / splice_len;
            } else if (i > grain_len - splice_len) {
                fade = (double)(grain_len - i) / splice_len;
            }

            size_t src_idx = g_start + i;
            if (src_idx < input_samples) {
                output->data[write_pos + i] += (float)(mono->data[src_idx] * fade);
            }
        }

        /* Overlap splices. The advance must be strictly positive: grain_len
         * and splice_len are both derived from the sample rate and are equal
         * whenever grainsize_ms is the default 15 ms, which made this loop
         * spin forever. A shorter grain than splice would underflow instead. */
        write_pos += (grain_len > splice_len) ? (grain_len - splice_len) : 1;
    }

    /* Copy audio after segment */
    size_t src_pos = seg_end;
    while (write_pos < output_samples && src_pos < input_samples) {
        output->data[write_pos++] = mono->data[src_pos++];
    }

    free(perm);
    free(grain_indices);
    cdp_lib_buffer_free(mono);

    cdp_lib_normalize_if_clipping(output, 0.95f);
    return output;
}

cdp_lib_buffer* cdp_lib_texture_simple(cdp_lib_ctx* ctx,
                                        const cdp_lib_buffer* input,
                                        double duration,
                                        double density,
                                        double pitch_range,
                                        double amp_range,
                                        double spatial_range,
                                        unsigned int seed) {
    if (ctx == NULL || input == NULL) {
        return NULL;
    }

    /* Validate parameters */
    if (duration <= 0) duration = 5.0;
    if (density <= 0) density = 5.0;
    if (pitch_range < 0) pitch_range = 0;
    if (pitch_range > 24) pitch_range = 24;
    if (amp_range < 0) amp_range = 0;
    if (amp_range > 1) amp_range = 1;
    if (spatial_range < 0) spatial_range = 0;
    if (spatial_range > 1) spatial_range = 1;

    int sample_rate = input->sample_rate;

    /* Initialize PRNG */
    cdp_lib_seed(ctx, seed);

    /* Convert to mono source if needed */
    cdp_lib_buffer* mono = cdp_lib_to_mono(ctx, input);
    if (mono == NULL) return NULL;

    size_t input_samples = mono->length;

    /* Calculate output size (stereo) */
    size_t output_samples = (size_t)(duration * sample_rate);

    /* Allocate stereo output buffer */
    cdp_lib_buffer* output = cdp_lib_buffer_create(output_samples * 2, 2, sample_rate);
    if (output == NULL) {
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Failed to allocate output buffer");
        return NULL;
    }

    /* Calculate number of events */
    size_t event_count = (size_t)(duration * density);
    if (event_count < 1) event_count = 1;

    /* Splice length for note attacks/releases (15ms) */
    size_t splice_samples = (size_t)(15.0 * sample_rate / 1000.0);
    if (splice_samples < 10) splice_samples = 10;

    /* Generate texture events */
    for (size_t ev = 0; ev < event_count; ev++) {
        /* Random event time */
        double event_time = cdp_lib_random(ctx) * duration;
        size_t event_pos = (size_t)(event_time * sample_rate);

        /* Random pitch (transposition) */
        double pitch_semitones = (cdp_lib_random(ctx) - 0.5) * 2 * pitch_range;
        double pitch_ratio = pow(2.0, pitch_semitones / 12.0);

        /* Random amplitude */
        double amp = 1.0 - amp_range * cdp_lib_random(ctx);

        /* Random stereo position */
        double pan = 0.5;
        if (spatial_range > 0) {
            pan = 0.5 + (cdp_lib_random(ctx) - 0.5) * spatial_range;
        }
        double left_gain = cos(pan * M_PI / 2);
        double right_gain = sin(pan * M_PI / 2);

        /* Calculate note duration (resampled source length) */
        size_t note_len = (size_t)(input_samples / pitch_ratio);

        /* Add note to output */
        for (size_t i = 0; i < note_len && event_pos + i < output_samples; i++) {
            /* Source position with pitch shift */
            double src_idx = i * pitch_ratio;
            size_t idx0 = (size_t)src_idx;
            double frac = src_idx - idx0;

            if (idx0 + 1 >= input_samples) break;

            /* Interpolated sample */
            float sample = (float)(mono->data[idx0] * (1.0 - frac) +
                                   mono->data[idx0 + 1] * frac);

            /* Apply envelope (fade in/out) */
            double env = 1.0;
            if (i < splice_samples) {
                env = (double)i / splice_samples;
            } else if (i > note_len - splice_samples) {
                env = (double)(note_len - i) / splice_samples;
            }

            /* Apply amp and panning */
            float final_sample = (float)(sample * amp * env);
            size_t out_idx = (event_pos + i) * 2;

            if (out_idx + 1 < output->length) {
                output->data[out_idx] += (float)(final_sample * left_gain);
                output->data[out_idx + 1] += (float)(final_sample * right_gain);
            }
        }
    }

    cdp_lib_buffer_free(mono);

    cdp_lib_normalize_if_clipping(output, 0.95f);
    return output;
}

cdp_lib_buffer* cdp_lib_texture_multi(cdp_lib_ctx* ctx,
                                       const cdp_lib_buffer* input,
                                       double duration,
                                       double density,
                                       int group_size,
                                       double group_spread,
                                       double pitch_range,
                                       double pitch_center,
                                       double amp_decay,
                                       unsigned int seed) {
    if (ctx == NULL || input == NULL) {
        return NULL;
    }

    /* Validate parameters */
    if (duration <= 0) duration = 5.0;
    if (density <= 0) density = 2.0;
    if (group_size < 1) group_size = 1;
    if (group_size > 16) group_size = 16;
    if (group_spread < 0) group_spread = 0.1;
    if (pitch_range < 0) pitch_range = 0;
    if (pitch_range > 24) pitch_range = 24;
    if (amp_decay < 0) amp_decay = 0;
    if (amp_decay > 1) amp_decay = 1;

    int sample_rate = input->sample_rate;

    /* Convert to mono source if needed */
    cdp_lib_buffer* mono = cdp_lib_to_mono(ctx, input);
    if (mono == NULL) return NULL;

    size_t input_samples = mono->length;

    /* Initialize PRNG */
    cdp_lib_seed(ctx, seed);

    /* Calculate output size (stereo) */
    size_t output_samples = (size_t)(duration * sample_rate);

    /* Allocate stereo output buffer */
    cdp_lib_buffer* output = cdp_lib_buffer_create(output_samples * 2, 2, sample_rate);
    if (output == NULL) {
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Failed to allocate output buffer");
        return NULL;
    }

    /* Calculate number of groups */
    size_t group_count = (size_t)(duration * density);
    if (group_count < 1) group_count = 1;

    /* Splice length (15ms) */
    size_t splice_samples = (size_t)(15.0 * sample_rate / 1000.0);
    if (splice_samples < 10) splice_samples = 10;

    /* Generate texture groups */
    for (size_t g = 0; g < group_count; g++) {
        /* Random group time */
        double group_time = cdp_lib_random(ctx) * duration;

        /* Random group pitch center */
        double group_pitch = pitch_center + (cdp_lib_random(ctx) - 0.5) * pitch_range;

        /* Random stereo position for group */
        double group_pan = 0.5 + (cdp_lib_random(ctx) - 0.5) * 0.8;

        /* Actual notes in this group (vary slightly) */
        int notes_in_group = group_size + (int)(cdp_lib_random_u64(ctx) % 3) - 1;
        if (notes_in_group < 1) notes_in_group = 1;

        /* Generate notes in group */
        for (int n = 0; n < notes_in_group; n++) {
            /* Note time within group */
            double note_offset = ((double)n / notes_in_group) * group_spread;
            note_offset += (cdp_lib_random(ctx) - 0.5) * group_spread * 0.3;
            double note_time = group_time + note_offset;

            if (note_time < 0) note_time = 0;
            if (note_time >= duration) continue;

            size_t note_pos = (size_t)(note_time * sample_rate);

            /* Note pitch (spread around group center) */
            double note_pitch = group_pitch + (cdp_lib_random(ctx) - 0.5) * 4;
            double pitch_ratio = pow(2.0, note_pitch / 12.0);

            /* Note amplitude (decay through group) */
            double amp = 1.0 - amp_decay * ((double)n / notes_in_group);
            amp *= 0.5 + 0.5 * cdp_lib_random(ctx);  /* Add variation */

            /* Note pan (spread around group pan) */
            double note_pan = group_pan + (cdp_lib_random(ctx) - 0.5) * 0.3;
            if (note_pan < 0) note_pan = 0;
            if (note_pan > 1) note_pan = 1;
            double left_gain = cos(note_pan * M_PI / 2);
            double right_gain = sin(note_pan * M_PI / 2);

            /* Calculate note duration */
            size_t note_len = (size_t)(input_samples / pitch_ratio);

            /* Add note to output */
            for (size_t i = 0; i < note_len && note_pos + i < output_samples; i++) {
                /* Source position with pitch shift */
                double src_idx = i * pitch_ratio;
                size_t idx0 = (size_t)src_idx;
                double frac = src_idx - idx0;

                if (idx0 + 1 >= input_samples) break;

                /* Interpolated sample */
                float sample = (float)(mono->data[idx0] * (1.0 - frac) +
                                       mono->data[idx0 + 1] * frac);

                /* Apply envelope */
                double env = 1.0;
                if (i < splice_samples) {
                    env = (double)i / splice_samples;
                } else if (i > note_len - splice_samples) {
                    env = (double)(note_len - i) / splice_samples;
                }

                /* Apply amp and panning */
                float final_sample = (float)(sample * amp * env);
                size_t out_idx = (note_pos + i) * 2;

                if (out_idx + 1 < output->length) {
                    output->data[out_idx] += (float)(final_sample * left_gain);
                    output->data[out_idx + 1] += (float)(final_sample * right_gain);
                }
            }
        }
    }

    cdp_lib_buffer_free(mono);

    cdp_lib_normalize_if_clipping(output, 0.95f);
    return output;
}
