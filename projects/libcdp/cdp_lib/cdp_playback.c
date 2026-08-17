/*
 * CDP Playback/Time Manipulation - Implementation
 *
 * Implements time-based manipulation operations working directly
 * on memory buffers without file I/O.
 */

#include "cdp_playback.h"
#include "cdp_lib_internal.h"

/* Helper: Get number of frames from buffer */
static inline size_t get_frames(const cdp_lib_buffer* buf) {
    return buf->length / buf->channels;
}

/* Helper: Apply crossfade splice envelope (fade in) */
static void apply_splice_in(float* buf, size_t start_sample, size_t splice_samples, int channels)
{
    for (size_t i = 0; i < splice_samples; i++) {
        double env = (double)i / (double)splice_samples;
        for (int ch = 0; ch < channels; ch++) {
            buf[(start_sample + i) * channels + ch] *= (float)env;
        }
    }
}

/* Helper: Apply crossfade splice envelope (fade out) */
static void apply_splice_out(float* buf, size_t start_sample, size_t splice_samples, int channels)
{
    for (size_t i = 0; i < splice_samples; i++) {
        double env = 1.0 - (double)i / (double)splice_samples;
        for (int ch = 0; ch < channels; ch++) {
            buf[(start_sample + i) * channels + ch] *= (float)env;
        }
    }
}

/* Helper: Simple linear interpolation for resampling */
static float lerp_sample(const float* data, double pos, int channels, int ch, size_t max_frames)
{
    size_t i0 = (size_t)floor(pos);
    size_t i1 = i0 + 1;
    if (i1 >= max_frames) i1 = max_frames - 1;
    double frac = pos - floor(pos);
    return (float)(data[i0 * channels + ch] * (1.0 - frac) + data[i1 * channels + ch] * frac);
}

/*
 * Zigzag playback
 */
cdp_lib_buffer* cdp_lib_zigzag(cdp_lib_ctx* ctx,
                                const cdp_lib_buffer* input,
                                const double* times,
                                int num_times,
                                double splice_ms)
{
    if (!ctx || !input || !times || num_times < 2) {
        if (ctx) cdp_lib_set_error(ctx, "zigzag: invalid parameters");
        return NULL;
    }

    int channels = input->channels;
    int sample_rate = input->sample_rate;
    size_t input_frames = get_frames(input);
    size_t splice_samples = (size_t)(splice_ms * 0.001 * sample_rate);
    if (splice_samples < 1) splice_samples = 1;

    /* Calculate output size */
    size_t total_frames = 0;
    for (int i = 0; i < num_times - 1; i++) {
        double seg_dur = fabs(times[i + 1] - times[i]);
        total_frames += (size_t)(seg_dur * sample_rate);
    }

    /* Allocate output data */
    float* out_data = (float*)calloc(total_frames * channels, sizeof(float));
    if (!out_data) {
        cdp_lib_set_error(ctx, "zigzag: memory allocation failed");
        return NULL;
    }

    size_t out_pos = 0;
    int forward = 1; /* Alternating direction */

    for (int seg = 0; seg < num_times - 1; seg++) {
        double t0 = times[seg];
        double t1 = times[seg + 1];

        size_t start_frame = (size_t)(t0 * sample_rate);
        size_t end_frame = (size_t)(t1 * sample_rate);

        /* Clamp to input bounds */
        if (start_frame >= input_frames) start_frame = input_frames - 1;
        if (end_frame >= input_frames) end_frame = input_frames - 1;

        size_t seg_frames = (start_frame < end_frame) ? (end_frame - start_frame) : (start_frame - end_frame);
        if (seg_frames == 0) continue;

        /* Copy segment in current direction */
        if (forward) {
            /* Forward: copy from start to end */
            size_t src = (start_frame < end_frame) ? start_frame : end_frame;
            for (size_t i = 0; i < seg_frames && out_pos < total_frames; i++, out_pos++) {
                for (int ch = 0; ch < channels; ch++) {
                    out_data[out_pos * channels + ch] = input->data[(src + i) * channels + ch];
                }
            }
        } else {
            /* Backward: copy in reverse */
            size_t src = (start_frame > end_frame) ? start_frame : end_frame;
            for (size_t i = 0; i < seg_frames && out_pos < total_frames; i++, out_pos++) {
                for (int ch = 0; ch < channels; ch++) {
                    out_data[out_pos * channels + ch] = input->data[(src - i) * channels + ch];
                }
            }
        }

        /* Apply crossfade at segment boundaries */
        if (seg > 0 && out_pos >= seg_frames + splice_samples) {
            size_t splice_start = out_pos - seg_frames;
            if (splice_start < total_frames) {
                apply_splice_in(out_data, splice_start, splice_samples, channels);
            }
        }
        if (seg < num_times - 2 && out_pos > splice_samples) {
            size_t splice_start = out_pos - splice_samples;
            if (splice_start < total_frames) {
                apply_splice_out(out_data, splice_start, splice_samples, channels);
            }
        }

        forward = !forward; /* Alternate direction */
    }

    cdp_lib_buffer* output = cdp_lib_buffer_from_data(out_data, out_pos * channels, channels, sample_rate);
    if (!output) {
        free(out_data);
        cdp_lib_set_error(ctx, "zigzag: failed to create output buffer");
        return NULL;
    }

    return output;
}

/*
 * Iterate - repeat with variations
 */
cdp_lib_buffer* cdp_lib_iterate(cdp_lib_ctx* ctx,
                                 const cdp_lib_buffer* input,
                                 int repeats,
                                 double delay,
                                 double delay_rand,
                                 double pitch_shift,
                                 double gain_decay,
                                 unsigned int seed)
{
    if (!ctx || !input || repeats < 1 || repeats > 100) {
        if (ctx) cdp_lib_set_error(ctx, "iterate: invalid parameters");
        return NULL;
    }

    int channels = input->channels;
    int sample_rate = input->sample_rate;
    size_t input_frames = get_frames(input);

    /* Initialize random */
    cdp_lib_seed(ctx, seed);

    /* Calculate positions and parameters for each iteration */
    double* positions = (double*)malloc((repeats + 1) * sizeof(double));
    double* gains = (double*)malloc((repeats + 1) * sizeof(double));
    double* pitches = (double*)malloc((repeats + 1) * sizeof(double));

    if (!positions || !gains || !pitches) {
        free(positions);
        free(gains);
        free(pitches);
        cdp_lib_set_error(ctx, "iterate: memory allocation failed");
        return NULL;
    }

    positions[0] = 0;
    gains[0] = 1.0;
    pitches[0] = 1.0;

    double current_pos = 0;
    double current_gain = 1.0;

    for (int i = 1; i <= repeats; i++) {
        /* Calculate delay with random variation */
        double rand_factor = 1.0 + (cdp_lib_random(ctx) - 0.5) * 2.0 * delay_rand;
        current_pos += delay * rand_factor;
        positions[i] = current_pos;

        /* Apply gain decay */
        current_gain *= gain_decay;
        gains[i] = current_gain;

        /* Random pitch shift */
        double rand_pitch = (cdp_lib_random(ctx) - 0.5) * 2.0 * pitch_shift;
        pitches[i] = pow(2.0, rand_pitch / 12.0); /* Semitones to ratio */
    }

    /* Calculate output duration */
    double input_dur = (double)input_frames / sample_rate;
    double max_dur = positions[repeats] + input_dur / pitches[repeats];
    size_t out_frames = (size_t)(max_dur * sample_rate) + 1;

    float* out_data = (float*)calloc(out_frames * channels, sizeof(float));
    if (!out_data) {
        free(positions);
        free(gains);
        free(pitches);
        cdp_lib_set_error(ctx, "iterate: memory allocation failed");
        return NULL;
    }

    /* Render each iteration */
    for (int iter = 0; iter <= repeats; iter++) {
        size_t start_frame = (size_t)(positions[iter] * sample_rate);
        double pitch_ratio = pitches[iter];
        double gain = gains[iter];

        /* Render this iteration with pitch shift */
        double src_pos = 0;
        for (size_t out = start_frame; out < out_frames && src_pos < input_frames - 1; out++) {
            for (int ch = 0; ch < channels; ch++) {
                float sample = lerp_sample(input->data, src_pos, channels, ch, input_frames);
                out_data[out * channels + ch] += (float)(sample * gain);
            }
            src_pos += pitch_ratio;
        }
    }

    /* Normalize to prevent clipping */
    float max_val = 0;
    for (size_t i = 0; i < out_frames * channels; i++) {
        float abs_val = fabsf(out_data[i]);
        if (abs_val > max_val) max_val = abs_val;
    }
    if (max_val > 0.95f) {
        float scale = 0.95f / max_val;
        for (size_t i = 0; i < out_frames * channels; i++) {
            out_data[i] *= scale;
        }
    }

    free(positions);
    free(gains);
    free(pitches);

    cdp_lib_buffer* output = cdp_lib_buffer_from_data(out_data, out_frames * channels, channels, sample_rate);
    if (!output) {
        free(out_data);
        cdp_lib_set_error(ctx, "iterate: failed to create output buffer");
        return NULL;
    }

    return output;
}

/*
 * Stutter - segment-based stuttering
 */
cdp_lib_buffer* cdp_lib_stutter(cdp_lib_ctx* ctx,
                                 const cdp_lib_buffer* input,
                                 double segment_ms,
                                 double duration,
                                 double silence_prob,
                                 double silence_min_ms,
                                 double silence_max_ms,
                                 double transpose_range,
                                 unsigned int seed)
{
    if (!ctx || !input || segment_ms < 10 || duration <= 0) {
        if (ctx) cdp_lib_set_error(ctx, "stutter: invalid parameters");
        return NULL;
    }

    int channels = input->channels;
    int sample_rate = input->sample_rate;
    size_t input_frames = get_frames(input);

    /* Initialize random */
    cdp_lib_seed(ctx, seed);

    /* Calculate segment parameters */
    size_t seg_frames = (size_t)(segment_ms * 0.001 * sample_rate);
    size_t splice_frames = (size_t)(0.003 * sample_rate); /* 3ms splice */
    if (splice_frames < 1) splice_frames = 1;
    if (seg_frames < splice_frames * 2) seg_frames = splice_frames * 2;

    /* Count segments in input */
    size_t num_segments = input_frames / seg_frames;
    if (num_segments < 1) num_segments = 1;

    /* Allocate output */
    size_t out_frames = (size_t)(duration * sample_rate);
    float* out_data = (float*)calloc(out_frames * channels, sizeof(float));
    if (!out_data) {
        cdp_lib_set_error(ctx, "stutter: memory allocation failed");
        return NULL;
    }

    /* Allocate segment buffer for transposition */
    float* seg_buf = (float*)malloc(seg_frames * 2 * channels * sizeof(float));
    if (!seg_buf) {
        free(out_data);
        cdp_lib_set_error(ctx, "stutter: memory allocation failed");
        return NULL;
    }

    /* If no segment can ever satisfy the splice length, the retry path below
     * would spin forever without producing output. Say so instead. */
    if (seg_frames < splice_frames * 2) {
        free(seg_buf);
        free(out_data);
        cdp_lib_set_error(ctx,
            "stutter: segment_ms is too short for the splice length");
        return NULL;
    }

    size_t out_pos = 0;
    /* Both `continue` paths below skip an iteration without advancing out_pos,
     * so the loop needs a bound of its own. */
    size_t retries = 0;
    const size_t max_retries = out_frames + 16;

    while (out_pos < out_frames) {
        /* Select random segment */
        size_t seg_idx = (size_t)(cdp_lib_random(ctx) * num_segments);
        if (seg_idx >= num_segments) seg_idx = num_segments - 1;
        size_t seg_start = seg_idx * seg_frames;
        size_t seg_len = seg_frames;

        /* Don't exceed input bounds. seg_start is clamped first because these
         * are size_t: subtracting past the end underflows to a huge length. */
        if (seg_start >= input_frames) {
            if (++retries > max_retries) break;
            continue;
        }
        if (seg_start + seg_len > input_frames) {
            seg_len = input_frames - seg_start;
        }
        if (seg_len < splice_frames * 2) {
            if (++retries > max_retries) break;
            continue;
        }

        /* Random transposition */
        double transpose = 0;
        if (transpose_range > 0) {
            transpose = (cdp_lib_random(ctx) - 0.5) * 2.0 * transpose_range;
        }
        double pitch_ratio = pow(2.0, transpose / 12.0);

        /* Copy segment with transposition */
        double src_pos = 0;
        size_t written = 0;
        while (src_pos < seg_len - 1 && written < seg_frames * 2) {
            for (int ch = 0; ch < channels; ch++) {
                float sample = lerp_sample(input->data + seg_start * channels,
                                           src_pos, channels, ch, seg_len);
                seg_buf[written * channels + ch] = sample;
            }
            written++;
            src_pos += pitch_ratio;
        }

        if (written < splice_frames * 2) {
            if (++retries > max_retries) break;
            continue;
        }

        /* Apply splice envelopes */
        apply_splice_in(seg_buf, 0, splice_frames, channels);
        if (written > splice_frames) {
            apply_splice_out(seg_buf, written - splice_frames, splice_frames, channels);
        }

        /* Copy to output */
        for (size_t i = 0; i < written && out_pos < out_frames; i++, out_pos++) {
            for (int ch = 0; ch < channels; ch++) {
                out_data[out_pos * channels + ch] = seg_buf[i * channels + ch];
            }
        }

        /* Maybe insert silence */
        if (silence_prob > 0 && cdp_lib_random(ctx) < silence_prob) {
            double sil_dur = silence_min_ms + cdp_lib_random(ctx) * (silence_max_ms - silence_min_ms);
            size_t sil_samples = (size_t)(sil_dur * 0.001 * sample_rate);

            for (size_t i = 0; i < sil_samples && out_pos < out_frames; i++, out_pos++) {
                for (int ch = 0; ch < channels; ch++) {
                    out_data[out_pos * channels + ch] = 0;
                }
            }
        }
    }

    free(seg_buf);

    cdp_lib_buffer* output = cdp_lib_buffer_from_data(out_data, out_pos * channels, channels, sample_rate);
    if (!output) {
        free(out_data);
        cdp_lib_set_error(ctx, "stutter: failed to create output buffer");
        return NULL;
    }

    return output;
}

/*
 * Bounce - bouncing ball effect
 */
cdp_lib_buffer* cdp_lib_bounce(cdp_lib_ctx* ctx,
                                const cdp_lib_buffer* input,
                                int bounces,
                                double initial_delay,
                                double shrink,
                                double end_level,
                                double level_curve,
                                int cut_bounces)
{
    if (!ctx || !input || bounces < 1 || bounces > 100) {
        if (ctx) cdp_lib_set_error(ctx, "bounce: invalid parameters");
        return NULL;
    }
    if (shrink <= 0 || shrink >= 1.0) {
        cdp_lib_set_error(ctx, "bounce: shrink must be between 0 and 1");
        return NULL;
    }

    int channels = input->channels;
    int sample_rate = input->sample_rate;
    size_t input_frames = get_frames(input);
    double input_dur = (double)input_frames / sample_rate;

    /* 3ms splice */
    size_t splice_frames = (size_t)(0.003 * sample_rate);
    if (splice_frames < 1) splice_frames = 1;

    /* Calculate positions and levels for each bounce */
    double* positions = (double*)malloc((bounces + 1) * sizeof(double));
    double* levels = (double*)malloc((bounces + 1) * sizeof(double));
    double* delays = (double*)malloc((bounces + 1) * sizeof(double));

    if (!positions || !levels || !delays) {
        free(positions);
        free(levels);
        free(delays);
        cdp_lib_set_error(ctx, "bounce: memory allocation failed");
        return NULL;
    }

    /* First play is at position 0 */
    positions[0] = 0;
    levels[0] = 1.0;
    delays[0] = initial_delay;

    double current_delay = initial_delay;
    double current_pos = 0;

    for (int i = 1; i <= bounces; i++) {
        current_pos += current_delay;
        positions[i] = current_pos;

        /* Calculate level with curve */
        double t = (double)(bounces - i) / bounces;
        t = pow(t, level_curve);
        levels[i] = end_level + (1.0 - end_level) * t;

        current_delay *= shrink;
        delays[i] = current_delay;
    }

    /* Calculate output duration */
    double out_dur;
    if (cut_bounces) {
        out_dur = positions[bounces] + delays[bounces];
    } else {
        out_dur = positions[bounces] + input_dur;
    }
    size_t out_frames = (size_t)(out_dur * sample_rate) + 1;

    float* out_data = (float*)calloc(out_frames * channels, sizeof(float));
    if (!out_data) {
        free(positions);
        free(levels);
        free(delays);
        cdp_lib_set_error(ctx, "bounce: memory allocation failed");
        return NULL;
    }

    /* Render each bounce */
    for (int b = 0; b <= bounces; b++) {
        size_t start_frame = (size_t)(positions[b] * sample_rate);
        double level = levels[b];

        /* Calculate how much of the source to use */
        size_t src_frames = input_frames;
        if (cut_bounces && b > 0) {
            /* Shrink proportionally to delay shrinkage */
            double ratio = delays[b] / initial_delay;
            src_frames = (size_t)(input_frames * ratio);
            if (src_frames < splice_frames * 2) {
                src_frames = splice_frames * 2;
            }
        }
        if (src_frames > input_frames) src_frames = input_frames;

        /* Add this bounce to output */
        for (size_t i = 0; i < src_frames && start_frame + i < out_frames; i++) {
            double env = 1.0;

            /* Splice in at start (except first) */
            if (b > 0 && i < splice_frames) {
                env = (double)i / splice_frames;
            }
            /* Splice out at end (if cut) */
            if (cut_bounces && i >= src_frames - splice_frames) {
                double t_env = (double)(src_frames - 1 - i) / splice_frames;
                if (t_env < env) env = t_env;
            }

            for (int ch = 0; ch < channels; ch++) {
                out_data[(start_frame + i) * channels + ch] +=
                    (float)(input->data[i * channels + ch] * level * env);
            }
        }
    }

    /* Normalize to prevent clipping */
    float max_val = 0;
    for (size_t i = 0; i < out_frames * channels; i++) {
        float abs_val = fabsf(out_data[i]);
        if (abs_val > max_val) max_val = abs_val;
    }
    if (max_val > 0.95f) {
        float scale = 0.95f / max_val;
        for (size_t i = 0; i < out_frames * channels; i++) {
            out_data[i] *= scale;
        }
    }

    free(positions);
    free(levels);
    free(delays);

    cdp_lib_buffer* output = cdp_lib_buffer_from_data(out_data, out_frames * channels, channels, sample_rate);
    if (!output) {
        free(out_data);
        cdp_lib_set_error(ctx, "bounce: failed to create output buffer");
        return NULL;
    }

    return output;
}

/*
 * Drunk - drunken walk through audio
 */
cdp_lib_buffer* cdp_lib_drunk(cdp_lib_ctx* ctx,
                               const cdp_lib_buffer* input,
                               double duration,
                               double step_ms,
                               double step_rand,
                               double locus,
                               double ambitus,
                               double overlap,
                               double splice_ms,
                               unsigned int seed)
{
    if (!ctx || !input || duration <= 0 || step_ms <= 0) {
        if (ctx) cdp_lib_set_error(ctx, "drunk: invalid parameters");
        return NULL;
    }
    if (overlap < 0) overlap = 0;
    if (overlap > 0.9) overlap = 0.9;
    if (step_rand < 0) step_rand = 0;
    if (step_rand > 1.0) step_rand = 1.0;

    int channels = input->channels;
    int sample_rate = input->sample_rate;
    size_t input_frames = get_frames(input);
    double input_dur = (double)input_frames / sample_rate;

    /* Clamp locus and ambitus to valid range */
    if (locus < 0) locus = 0;
    if (locus > input_dur) locus = input_dur / 2;
    if (ambitus <= 0) ambitus = input_dur / 2;
    if (locus - ambitus < 0 || locus + ambitus > input_dur) {
        /* Adjust ambitus to fit */
        double max_amb_left = locus;
        double max_amb_right = input_dur - locus;
        ambitus = (max_amb_left < max_amb_right) ? max_amb_left : max_amb_right;
        if (ambitus < step_ms * 0.001 * 2) ambitus = step_ms * 0.001 * 2;
    }

    /* Initialize random */
    cdp_lib_seed(ctx, seed);

    /* Calculate parameters */
    size_t step_frames = (size_t)(step_ms * 0.001 * sample_rate);
    size_t splice_frames = (size_t)(splice_ms * 0.001 * sample_rate);
    if (splice_frames < 1) splice_frames = 1;
    if (step_frames < splice_frames * 2) step_frames = splice_frames * 2;

    size_t locus_frame = (size_t)(locus * sample_rate);
    size_t ambitus_frames = (size_t)(ambitus * sample_rate);

    /* Calculate output size (generous allocation) */
    size_t out_frames = (size_t)(duration * sample_rate);
    float* out_data = (float*)calloc(out_frames * channels, sizeof(float));
    if (!out_data) {
        cdp_lib_set_error(ctx, "drunk: memory allocation failed");
        return NULL;
    }

    /* Segment buffer for overlap-add */
    size_t seg_buf_size = step_frames * 2;
    float* seg_buf = (float*)calloc(seg_buf_size * channels, sizeof(float));
    if (!seg_buf) {
        free(out_data);
        cdp_lib_set_error(ctx, "drunk: memory allocation failed");
        return NULL;
    }

    /* A step longer than the input can never yield a usable segment, and the
     * walk below would spin forever retrying. Reject it up front rather than
     * looping. */
    if (step_frames >= input_frames || splice_frames * 2 >= input_frames) {
        free(seg_buf);
        free(out_data);
        cdp_lib_set_error(ctx,
            "drunk: step_ms and splice_ms must be shorter than the input");
        return NULL;
    }

    size_t out_pos = 0;
    size_t current_pos = locus_frame; /* Start at locus */
    /* The retry path below makes no output progress, so it needs its own
     * bound: without one, a walk that can never place a segment loops
     * forever. One retry per output frame is far more than a healthy run
     * needs and still terminates. */
    size_t retries = 0;
    const size_t max_retries = out_frames + 16;

    while (out_pos < out_frames) {
        /* Calculate step size with randomization */
        double rand_val = cdp_lib_random(ctx);
        double step_factor = 1.0 + (rand_val - 0.5) * 2.0 * step_rand;
        size_t this_step = (size_t)(step_frames * step_factor);
        if (this_step < splice_frames * 2) this_step = splice_frames * 2;

        /* Calculate segment boundaries */
        size_t seg_start = current_pos;
        size_t seg_len = this_step;

        /* Ensure we stay within input bounds.
         *
         * seg_start is clamped first: these are size_t, so evaluating
         * `input_frames - seg_start` with seg_start past the end underflows to
         * a near-SIZE_MAX length and the copy below then reads far outside the
         * input. That was a reachable segfault (drunk with a large step_ms). */
        if (seg_start >= input_frames) {
            seg_start = 0;
            current_pos = 0;
        }
        if (seg_start + seg_len > input_frames) {
            seg_len = input_frames - seg_start;
        }
        if (seg_len < splice_frames * 2) {
            /* Can't make a valid segment, jump to a new position */
            current_pos = locus_frame;
            if (++retries > max_retries) break;
            continue;
        }

        /* Copy segment with splice envelopes */
        for (size_t i = 0; i < seg_len; i++) {
            double env = 1.0;

            /* Fade in at start */
            if (i < splice_frames) {
                env = (double)i / splice_frames;
            }
            /* Fade out at end */
            if (i >= seg_len - splice_frames) {
                double t = (double)(seg_len - 1 - i) / splice_frames;
                if (t < env) env = t;
            }

            for (int ch = 0; ch < channels; ch++) {
                seg_buf[i * channels + ch] = (float)(input->data[(seg_start + i) * channels + ch] * env);
            }
        }

        /* Calculate overlap amount */
        size_t overlap_samples = (size_t)(seg_len * overlap);
        if (overlap_samples > splice_frames) overlap_samples = splice_frames;

        /* Write to output with overlap-add */
        size_t write_start = (out_pos > overlap_samples) ? (out_pos - overlap_samples) : 0;
        for (size_t i = 0; i < seg_len && write_start + i < out_frames; i++) {
            for (int ch = 0; ch < channels; ch++) {
                out_data[(write_start + i) * channels + ch] += seg_buf[i * channels + ch];
            }
        }

        /* Advance output position */
        out_pos = write_start + seg_len;

        /* Calculate next position (random walk) */
        double step_dir = (cdp_lib_random(ctx) - 0.5) * 2.0; /* -1 to +1 */
        int64_t step_delta = (int64_t)(step_dir * step_frames);

        int64_t new_pos = (int64_t)current_pos + step_delta;

        /* Keep within ambitus around locus */
        if (new_pos < (int64_t)(locus_frame - ambitus_frames)) {
            /* Bounce off lower limit */
            new_pos = locus_frame - ambitus_frames + labs((int64_t)(locus_frame - ambitus_frames) - new_pos);
        }
        if (new_pos > (int64_t)(locus_frame + ambitus_frames)) {
            /* Bounce off upper limit */
            new_pos = locus_frame + ambitus_frames - (new_pos - (int64_t)(locus_frame + ambitus_frames));
        }

        /* Final bounds check.
         *
         * The upper clamp is applied before the lower one, not after: when
         * step_frames exceeds input_frames it evaluates to a negative value,
         * and the earlier ordering assigned that straight into a size_t. */
        if (new_pos >= (int64_t)input_frames - (int64_t)step_frames) {
            new_pos = (int64_t)input_frames - (int64_t)step_frames - 1;
        }
        if (new_pos < 0) new_pos = 0;
        if (new_pos >= (int64_t)input_frames) new_pos = (int64_t)input_frames - 1;

        current_pos = (size_t)new_pos;
    }

    free(seg_buf);

    /* Normalize to prevent clipping */
    float max_val = 0;
    for (size_t i = 0; i < out_frames * channels; i++) {
        float abs_val = fabsf(out_data[i]);
        if (abs_val > max_val) max_val = abs_val;
    }
    if (max_val > 0.95f) {
        float scale = 0.95f / max_val;
        for (size_t i = 0; i < out_frames * channels; i++) {
            out_data[i] *= scale;
        }
    }

    cdp_lib_buffer* output = cdp_lib_buffer_from_data(out_data, out_frames * channels, channels, sample_rate);
    if (!output) {
        free(out_data);
        cdp_lib_set_error(ctx, "drunk: failed to create output buffer");
        return NULL;
    }

    return output;
}

/*
 * Loop - loop a section with variations
 */
cdp_lib_buffer* cdp_lib_loop(cdp_lib_ctx* ctx,
                              const cdp_lib_buffer* input,
                              double start,
                              double length_ms,
                              double step_ms,
                              double search_ms,
                              int repeats,
                              double splice_ms,
                              unsigned int seed)
{
    if (!ctx || !input || length_ms <= 0 || repeats < 1 || repeats > 1000) {
        if (ctx) cdp_lib_set_error(ctx, "loop: invalid parameters");
        return NULL;
    }

    int channels = input->channels;
    int sample_rate = input->sample_rate;
    size_t input_frames = get_frames(input);

    /* Initialize random */
    cdp_lib_seed(ctx, seed);

    /* Convert parameters to samples */
    size_t loop_frames = (size_t)(length_ms * 0.001 * sample_rate);
    size_t step_frames = (size_t)(step_ms * 0.001 * sample_rate);
    size_t search_frames = (size_t)(search_ms * 0.001 * sample_rate);
    size_t splice_frames = (size_t)(splice_ms * 0.001 * sample_rate);
    if (splice_frames < 1) splice_frames = 1;

    /* Ensure loop is long enough for splices */
    if (loop_frames < splice_frames * 2) {
        loop_frames = splice_frames * 2;
    }

    /* Convert start position */
    size_t start_frame = (size_t)(start * sample_rate);
    if (start_frame >= input_frames) {
        start_frame = 0;
    }

    /* Calculate output size */
    /* Each loop contributes (loop_frames - splice_frames) due to crossfade overlap */
    size_t effective_loop = loop_frames - splice_frames;
    if (effective_loop < splice_frames) effective_loop = splice_frames;
    size_t out_frames = loop_frames + (repeats - 1) * effective_loop + splice_frames;

    float* out_data = (float*)calloc(out_frames * channels, sizeof(float));
    if (!out_data) {
        cdp_lib_set_error(ctx, "loop: memory allocation failed");
        return NULL;
    }

    size_t out_pos = 0;
    size_t current_start = start_frame;

    for (int rep = 0; rep < repeats; rep++) {
        /* Calculate loop start with random search */
        size_t loop_start = current_start;
        if (search_frames > 0 && rep > 0) {
            int64_t offset = (int64_t)((cdp_lib_random(ctx) - 0.5) * 2.0 * search_frames);
            int64_t new_start = (int64_t)current_start + offset;
            if (new_start < 0) new_start = 0;
            if (new_start >= (int64_t)input_frames) new_start = 0;
            loop_start = (size_t)new_start;
        }

        /* Ensure we can read the full loop */
        size_t actual_loop_len = loop_frames;
        if (loop_start + actual_loop_len > input_frames) {
            actual_loop_len = input_frames - loop_start;
        }
        if (actual_loop_len < splice_frames * 2) {
            /* Loop too short, wrap to start */
            loop_start = 0;
            actual_loop_len = loop_frames;
            if (actual_loop_len > input_frames) {
                actual_loop_len = input_frames;
            }
        }

        /* Calculate write position with overlap */
        size_t write_pos;
        if (rep == 0) {
            write_pos = 0;
        } else {
            /* Overlap with previous loop for crossfade */
            write_pos = out_pos - splice_frames;
        }

        /* Write loop with splice envelopes */
        for (size_t i = 0; i < actual_loop_len && write_pos + i < out_frames; i++) {
            double env = 1.0;

            /* Fade in at start (except first rep) */
            if (rep > 0 && i < splice_frames) {
                env = (double)i / splice_frames;
            }
            /* Fade out at end (except last rep) */
            if (rep < repeats - 1 && i >= actual_loop_len - splice_frames) {
                double t = (double)(actual_loop_len - 1 - i) / splice_frames;
                if (t < env) env = t;
            }

            for (int ch = 0; ch < channels; ch++) {
                out_data[(write_pos + i) * channels + ch] +=
                    (float)(input->data[(loop_start + i) * channels + ch] * env);
            }
        }

        out_pos = write_pos + actual_loop_len;

        /* Step to next loop position */
        if (step_frames > 0) {
            current_start += step_frames;
            if (current_start >= input_frames) {
                current_start = start_frame; /* Wrap back */
            }
        }
    }

    /* Trim output to actual written size */
    if (out_pos < out_frames) {
        out_frames = out_pos;
    }

    /* Normalize to prevent clipping */
    float max_val = 0;
    for (size_t i = 0; i < out_frames * channels; i++) {
        float abs_val = fabsf(out_data[i]);
        if (abs_val > max_val) max_val = abs_val;
    }
    if (max_val > 0.95f) {
        float scale = 0.95f / max_val;
        for (size_t i = 0; i < out_frames * channels; i++) {
            out_data[i] *= scale;
        }
    }

    cdp_lib_buffer* output = cdp_lib_buffer_from_data(out_data, out_frames * channels, channels, sample_rate);
    if (!output) {
        free(out_data);
        cdp_lib_set_error(ctx, "loop: failed to create output buffer");
        return NULL;
    }

    return output;
}

/*
 * Retime - time-domain time stretching/compression using overlap-add.
 *
 * TDOLA (Time Domain Overlap Add) algorithm:
 * - Divides input into overlapping grains
 * - Repositions grains at new intervals for stretching/compression
 * - Applies triangular windows and overlap-add for smooth blending
 */
cdp_lib_buffer* cdp_lib_retime(cdp_lib_ctx* ctx,
                                const cdp_lib_buffer* input,
                                double ratio,
                                double grain_ms,
                                double overlap)
{
    if (!ctx || !input) {
        if (ctx) cdp_lib_set_error(ctx, "retime: invalid parameters");
        return NULL;
    }

    /* Validate ratio (0.25 to 4.0 is reasonable for TDOLA) */
    if (ratio <= 0.0 || ratio > 10.0) {
        cdp_lib_set_error(ctx, "retime: ratio must be > 0 and <= 10");
        return NULL;
    }

    /* Validate grain size */
    if (grain_ms < 5.0) grain_ms = 5.0;
    if (grain_ms > 500.0) grain_ms = 500.0;

    /* Validate overlap */
    if (overlap < 0.1) overlap = 0.1;
    if (overlap > 0.9) overlap = 0.9;

    int channels = input->channels;
    int sample_rate = input->sample_rate;
    size_t input_frames = get_frames(input);

    if (input_frames < 2) {
        cdp_lib_set_error(ctx, "retime: input too short");
        return NULL;
    }

    /* Convert grain size to samples */
    size_t grain_frames = (size_t)(grain_ms * 0.001 * sample_rate);
    if (grain_frames < 32) grain_frames = 32;
    if (grain_frames > input_frames / 2) grain_frames = input_frames / 2;

    /* Calculate analysis and synthesis hop sizes */
    /* Analysis hop: how far we advance in input for each grain */
    size_t analysis_hop = (size_t)(grain_frames * (1.0 - overlap));
    if (analysis_hop < 1) analysis_hop = 1;

    /* Synthesis hop: how far we advance in output for each grain */
    /* For time stretching: synthesis_hop = analysis_hop / ratio */
    /* ratio < 1 -> longer output, larger synthesis hop */
    /* ratio > 1 -> shorter output, smaller synthesis hop */
    size_t synthesis_hop = (size_t)(analysis_hop / ratio);
    if (synthesis_hop < 1) synthesis_hop = 1;

    /* Calculate output size */
    size_t num_grains = (input_frames - grain_frames) / analysis_hop + 1;
    if (num_grains < 1) num_grains = 1;
    size_t out_frames = (num_grains - 1) * synthesis_hop + grain_frames;

    /* Allocate output buffer */
    float* out_data = (float*)calloc(out_frames * channels, sizeof(float));
    if (!out_data) {
        cdp_lib_set_error(ctx, "retime: memory allocation failed");
        return NULL;
    }

    /* Allocate normalization buffer (for OLA gain correction) */
    float* norm_data = (float*)calloc(out_frames, sizeof(float));
    if (!norm_data) {
        free(out_data);
        cdp_lib_set_error(ctx, "retime: memory allocation failed");
        return NULL;
    }

    /* Process each grain */
    size_t in_pos = 0;
    size_t out_pos = 0;

    for (size_t grain = 0; grain < num_grains; grain++) {
        /* Calculate window/envelope for this grain (triangular) */
        for (size_t i = 0; i < grain_frames; i++) {
            /* Triangular window */
            double t = (double)i / (double)(grain_frames - 1);
            double window = (t < 0.5) ? (2.0 * t) : (2.0 * (1.0 - t));

            /* Read input sample position */
            size_t in_sample = in_pos + i;
            if (in_sample >= input_frames) in_sample = input_frames - 1;

            /* Write output sample position */
            size_t out_sample = out_pos + i;
            if (out_sample >= out_frames) break;

            /* Apply window and add to output */
            for (int ch = 0; ch < channels; ch++) {
                out_data[out_sample * channels + ch] +=
                    (float)(input->data[in_sample * channels + ch] * window);
            }
            norm_data[out_sample] += (float)window;
        }

        /* Advance positions */
        in_pos += analysis_hop;
        out_pos += synthesis_hop;

        /* Safety check */
        if (in_pos >= input_frames - grain_frames / 2) break;
    }

    /* Normalize by accumulated window values to prevent amplitude variations */
    for (size_t i = 0; i < out_frames; i++) {
        if (norm_data[i] > 0.001f) {
            float scale = 1.0f / norm_data[i];
            for (int ch = 0; ch < channels; ch++) {
                out_data[i * channels + ch] *= scale;
            }
        }
    }

    free(norm_data);

    /* Final peak limiting to prevent clipping */
    float max_val = 0;
    for (size_t i = 0; i < out_frames * channels; i++) {
        float abs_val = fabsf(out_data[i]);
        if (abs_val > max_val) max_val = abs_val;
    }
    if (max_val > 0.99f) {
        float scale = 0.95f / max_val;
        for (size_t i = 0; i < out_frames * channels; i++) {
            out_data[i] *= scale;
        }
    }

    cdp_lib_buffer* output = cdp_lib_buffer_from_data(out_data, out_frames * channels, channels, sample_rate);
    if (!output) {
        free(out_data);
        cdp_lib_set_error(ctx, "retime: failed to create output buffer");
        return NULL;
    }

    return output;
}

/* Waveset info structure for scramble */
typedef struct {
    size_t start;   /* Start sample (frame index) */
    size_t end;     /* End sample (frame index) */
    float level;    /* Peak level in waveset */
} waveset_info;

/* Compare functions for qsort */
static int compare_waveset_size_up(const void* a, const void* b) {
    const waveset_info* wa = (const waveset_info*)a;
    const waveset_info* wb = (const waveset_info*)b;
    size_t len_a = wa->end - wa->start;
    size_t len_b = wb->end - wb->start;
    if (len_a < len_b) return -1;
    if (len_a > len_b) return 1;
    return 0;
}

static int compare_waveset_size_down(const void* a, const void* b) {
    return -compare_waveset_size_up(a, b);
}

static int compare_waveset_level_up(const void* a, const void* b) {
    const waveset_info* wa = (const waveset_info*)a;
    const waveset_info* wb = (const waveset_info*)b;
    if (wa->level < wb->level) return -1;
    if (wa->level > wb->level) return 1;
    return 0;
}

static int compare_waveset_level_down(const void* a, const void* b) {
    return -compare_waveset_level_up(a, b);
}

/*
 * Scramble - reorder wavesets based on zero crossings.
 */
cdp_lib_buffer* cdp_lib_scramble(cdp_lib_ctx* ctx,
                                  const cdp_lib_buffer* input,
                                  int mode,
                                  int group_size,
                                  unsigned int seed)
{
    if (!ctx || !input) {
        if (ctx) cdp_lib_set_error(ctx, "scramble: invalid parameters");
        return NULL;
    }

    /* Validate mode (0-5) */
    if (mode < 0 || mode > 5) {
        cdp_lib_set_error(ctx, "scramble: mode must be 0-5");
        return NULL;
    }

    /* Validate group size */
    if (group_size < 1) group_size = 2;
    if (group_size > 64) group_size = 64;

    int channels = input->channels;
    int sample_rate = input->sample_rate;
    size_t input_frames = get_frames(input);

    if (input_frames < 4) {
        cdp_lib_set_error(ctx, "scramble: input too short");
        return NULL;
    }

    /* Initialize random */
    cdp_lib_seed(ctx, seed);

    /* Phase 1: Count wavesets by detecting zero crossings */
    /* Use first channel for detection */
    int phase = 0;      /* 0 = not started, 1 = positive, -1 = negative */
    int phase_count = 0;
    size_t waveset_count = 0;

    for (size_t i = 0; i < input_frames; i++) {
        float sample = input->data[i * channels];
        if (sample == 0.0f) continue;

        if (phase == 0) {
            /* First non-zero sample */
            phase = (sample > 0) ? 1 : -1;
            waveset_count++;
        } else if (phase == 1 && sample < 0) {
            if (++phase_count >= group_size) {
                waveset_count++;
                phase_count = 0;
            }
            phase = -1;
        } else if (phase == -1 && sample > 0) {
            if (++phase_count >= group_size) {
                waveset_count++;
                phase_count = 0;
            }
            phase = 1;
        }
    }

    if (waveset_count < 2) {
        cdp_lib_set_error(ctx, "scramble: not enough wavesets found");
        return NULL;
    }

    /* Allocate waveset info array */
    waveset_info* wavesets = (waveset_info*)malloc(waveset_count * sizeof(waveset_info));
    if (!wavesets) {
        cdp_lib_set_error(ctx, "scramble: memory allocation failed");
        return NULL;
    }

    /* Phase 2: Store waveset boundaries and calculate levels */
    phase = 0;
    phase_count = 0;
    size_t ws_index = 0;
    size_t current_start = 0;
    float current_max = 0;

    for (size_t i = 0; i < input_frames; i++) {
        float sample = input->data[i * channels];
        float abs_sample = fabsf(sample);
        if (abs_sample > current_max) current_max = abs_sample;

        if (sample == 0.0f) continue;

        if (phase == 0) {
            phase = (sample > 0) ? 1 : -1;
            current_start = i;
            current_max = abs_sample;
        } else if ((phase == 1 && sample < 0) || (phase == -1 && sample > 0)) {
            if (++phase_count >= group_size) {
                if (ws_index < waveset_count) {
                    wavesets[ws_index].start = current_start;
                    wavesets[ws_index].end = i;
                    wavesets[ws_index].level = current_max;
                    ws_index++;
                }
                current_start = i;
                current_max = abs_sample;
                phase_count = 0;
            }
            phase = (sample > 0) ? 1 : -1;
        }
    }

    /* Handle final waveset */
    if (ws_index < waveset_count && current_start < input_frames - 1) {
        wavesets[ws_index].start = current_start;
        wavesets[ws_index].end = input_frames;
        wavesets[ws_index].level = current_max;
        ws_index++;
    }

    waveset_count = ws_index; /* Actual count stored */

    if (waveset_count < 2) {
        free(wavesets);
        cdp_lib_set_error(ctx, "scramble: not enough wavesets found");
        return NULL;
    }

    /* Phase 3: Reorder wavesets according to mode */
    switch (mode) {
    case 0: /* shuffle - Fisher-Yates */
        for (size_t i = waveset_count - 1; i > 0; i--) {
            size_t j = (size_t)(cdp_lib_random(ctx) * (i + 1));
            if (j > i) j = i;
            waveset_info temp = wavesets[i];
            wavesets[i] = wavesets[j];
            wavesets[j] = temp;
        }
        break;

    case 1: /* reverse */
        for (size_t i = 0; i < waveset_count / 2; i++) {
            waveset_info temp = wavesets[i];
            wavesets[i] = wavesets[waveset_count - 1 - i];
            wavesets[waveset_count - 1 - i] = temp;
        }
        break;

    case 2: /* by_size_up (smallest first - rising pitch) */
        qsort(wavesets, waveset_count, sizeof(waveset_info), compare_waveset_size_up);
        break;

    case 3: /* by_size_down (largest first - falling pitch) */
        qsort(wavesets, waveset_count, sizeof(waveset_info), compare_waveset_size_down);
        break;

    case 4: /* by_level_up (quietest first) */
        qsort(wavesets, waveset_count, sizeof(waveset_info), compare_waveset_level_up);
        break;

    case 5: /* by_level_down (loudest first) */
        qsort(wavesets, waveset_count, sizeof(waveset_info), compare_waveset_level_down);
        break;
    }

    /* Phase 4: Calculate output size */
    size_t out_frames = 0;
    for (size_t i = 0; i < waveset_count; i++) {
        out_frames += wavesets[i].end - wavesets[i].start;
    }

    /* Allocate output buffer */
    float* out_data = (float*)calloc(out_frames * channels, sizeof(float));
    if (!out_data) {
        free(wavesets);
        cdp_lib_set_error(ctx, "scramble: memory allocation failed");
        return NULL;
    }

    /* Phase 5: Copy wavesets in new order */
    size_t out_pos = 0;
    for (size_t i = 0; i < waveset_count; i++) {
        size_t ws_start = wavesets[i].start;
        size_t ws_len = wavesets[i].end - wavesets[i].start;

        for (size_t j = 0; j < ws_len && out_pos < out_frames; j++, out_pos++) {
            for (int ch = 0; ch < channels; ch++) {
                out_data[out_pos * channels + ch] = input->data[(ws_start + j) * channels + ch];
            }
        }
    }

    free(wavesets);

    cdp_lib_buffer* output = cdp_lib_buffer_from_data(out_data, out_frames * channels, channels, sample_rate);
    if (!output) {
        free(out_data);
        cdp_lib_set_error(ctx, "scramble: failed to create output buffer");
        return NULL;
    }

    return output;
}

/*
 * Splinter - create fragmenting/splintering effect.
 *
 * Takes a segment and progressively shrinks and repeats it,
 * creating a percussive fragmentation effect.
 */
cdp_lib_buffer* cdp_lib_splinter(cdp_lib_ctx* ctx,
                                  const cdp_lib_buffer* input,
                                  double start,
                                  double duration_ms,
                                  int repeats,
                                  double min_shrink,
                                  double shrink_curve,
                                  double accel,
                                  unsigned int seed)
{
    if (!ctx || !input) {
        if (ctx) cdp_lib_set_error(ctx, "splinter: invalid parameters");
        return NULL;
    }

    /* Validate parameters */
    if (duration_ms < 5.0) duration_ms = 5.0;
    if (duration_ms > 5000.0) duration_ms = 5000.0;
    if (repeats < 2) repeats = 2;
    if (repeats > 500) repeats = 500;
    if (min_shrink < 0.01) min_shrink = 0.01;
    if (min_shrink > 1.0) min_shrink = 1.0;
    if (shrink_curve < 0.1) shrink_curve = 0.1;
    if (shrink_curve > 10.0) shrink_curve = 10.0;
    if (accel < 0.5) accel = 0.5;
    if (accel > 4.0) accel = 4.0;

    int channels = input->channels;
    int sample_rate = input->sample_rate;
    size_t input_frames = get_frames(input);

    /* Initialize random */
    cdp_lib_seed(ctx, seed);

    /* Convert start position to frames */
    size_t start_frame = (size_t)(start * sample_rate);
    if (start_frame >= input_frames) {
        start_frame = 0;
    }

    /* Convert duration to frames */
    size_t seg_frames = (size_t)(duration_ms * 0.001 * sample_rate);
    if (seg_frames < 16) seg_frames = 16;
    if (start_frame + seg_frames > input_frames) {
        seg_frames = input_frames - start_frame;
    }
    if (seg_frames < 16) {
        cdp_lib_set_error(ctx, "splinter: segment too short");
        return NULL;
    }

    /* Calculate splice length (2ms) */
    size_t splice_frames = (size_t)(0.002 * sample_rate);
    if (splice_frames < 4) splice_frames = 4;
    if (splice_frames > seg_frames / 4) splice_frames = seg_frames / 4;

    /* Pre-calculate shrinkage for each repeat */
    double* shrink_factors = (double*)malloc(repeats * sizeof(double));
    if (!shrink_factors) {
        cdp_lib_set_error(ctx, "splinter: memory allocation failed");
        return NULL;
    }

    /* Calculate shrinkage factors using curve */
    for (int i = 0; i < repeats; i++) {
        double t = (double)i / (double)(repeats - 1);  /* 0 to 1 */
        double curved_t = pow(t, shrink_curve);
        shrink_factors[i] = 1.0 - curved_t * (1.0 - min_shrink);
    }

    /* Pre-calculate timing (inter-onset intervals) */
    double* intervals = (double*)malloc(repeats * sizeof(double));
    if (!intervals) {
        free(shrink_factors);
        cdp_lib_set_error(ctx, "splinter: memory allocation failed");
        return NULL;
    }

    /* Base interval is the segment duration */
    double base_interval = (double)seg_frames;
    double total_time = 0;
    for (int i = 0; i < repeats; i++) {
        /* Shrink interval based on shrink factor and acceleration */
        double shrink = shrink_factors[i];
        double time_factor = pow(shrink, accel);
        intervals[i] = base_interval * time_factor;
        if (intervals[i] < splice_frames * 2) {
            intervals[i] = splice_frames * 2;
        }
        total_time += intervals[i];
    }

    /* Calculate output size */
    size_t out_frames = (size_t)total_time + seg_frames + splice_frames * 2;

    /* Allocate output buffer */
    float* out_data = (float*)calloc(out_frames * channels, sizeof(float));
    if (!out_data) {
        free(shrink_factors);
        free(intervals);
        cdp_lib_set_error(ctx, "splinter: memory allocation failed");
        return NULL;
    }

    /* Allocate temporary buffer for shrunk segment */
    float* shrunk_buf = (float*)malloc(seg_frames * channels * sizeof(float));
    if (!shrunk_buf) {
        free(shrink_factors);
        free(intervals);
        free(out_data);
        cdp_lib_set_error(ctx, "splinter: memory allocation failed");
        return NULL;
    }

    /* Process each repeat */
    double out_pos = 0;
    for (int rep = 0; rep < repeats; rep++) {
        double shrink = shrink_factors[rep];

        /* Calculate shrunk segment length */
        size_t shrunk_frames = (size_t)(seg_frames * shrink);
        if (shrunk_frames < splice_frames * 2) {
            shrunk_frames = splice_frames * 2;
        }

        /* Clear shrunk buffer */
        memset(shrunk_buf, 0, seg_frames * channels * sizeof(float));

        /* Resample (shrink) the segment using linear interpolation */
        double read_incr = (double)seg_frames / (double)shrunk_frames;
        for (size_t i = 0; i < shrunk_frames; i++) {
            double src_pos = i * read_incr;
            size_t src_i = (size_t)src_pos;
            double frac = src_pos - src_i;

            if (src_i + 1 >= seg_frames) {
                src_i = seg_frames - 2;
                frac = 1.0;
            }

            for (int ch = 0; ch < channels; ch++) {
                float v0 = input->data[(start_frame + src_i) * channels + ch];
                float v1 = input->data[(start_frame + src_i + 1) * channels + ch];
                shrunk_buf[i * channels + ch] = (float)(v0 + frac * (v1 - v0));
            }
        }

        /* Apply fade in/out to shrunk segment */
        for (size_t i = 0; i < splice_frames && i < shrunk_frames; i++) {
            double env_in = (double)i / splice_frames;
            double env_out = (double)(splice_frames - 1 - i) / splice_frames;
            size_t out_idx = shrunk_frames - splice_frames + i;

            for (int ch = 0; ch < channels; ch++) {
                shrunk_buf[i * channels + ch] *= (float)env_in;
                if (out_idx < shrunk_frames) {
                    shrunk_buf[out_idx * channels + ch] *= (float)env_out;
                }
            }
        }

        /* Write shrunk segment to output with overlap-add */
        size_t write_pos = (size_t)out_pos;
        for (size_t i = 0; i < shrunk_frames; i++) {
            size_t dst = write_pos + i;
            if (dst >= out_frames) break;
            for (int ch = 0; ch < channels; ch++) {
                out_data[dst * channels + ch] += shrunk_buf[i * channels + ch];
            }
        }

        /* Advance output position */
        out_pos += intervals[rep];
    }

    free(shrink_factors);
    free(intervals);
    free(shrunk_buf);

    /* Trim to actual written content */
    size_t actual_frames = (size_t)out_pos + splice_frames;
    if (actual_frames > out_frames) actual_frames = out_frames;

    /* Normalize to prevent clipping */
    float max_val = 0;
    for (size_t i = 0; i < actual_frames * channels; i++) {
        float abs_val = fabsf(out_data[i]);
        if (abs_val > max_val) max_val = abs_val;
    }
    if (max_val > 0.95f) {
        float scale = 0.95f / max_val;
        for (size_t i = 0; i < actual_frames * channels; i++) {
            out_data[i] *= scale;
        }
    }

    cdp_lib_buffer* output = cdp_lib_buffer_from_data(out_data, actual_frames * channels, channels, sample_rate);
    if (!output) {
        free(out_data);
        cdp_lib_set_error(ctx, "splinter: failed to create output buffer");
        return NULL;
    }

    return output;
}

/*
 * Spin - rotate audio around the stereo field.
 *
 * Creates a spinning/rotating spatial effect using equal-power panning
 * with optional doppler pitch shift for realism.
 */
cdp_lib_buffer* cdp_lib_spin(cdp_lib_ctx* ctx,
                              const cdp_lib_buffer* input,
                              double rate,
                              double doppler,
                              double depth)
{
    if (!ctx || !input) {
        if (ctx) cdp_lib_set_error(ctx, "spin: invalid parameters");
        return NULL;
    }

    /* Validate parameters */
    if (rate < -20.0) rate = -20.0;
    if (rate > 20.0) rate = 20.0;
    if (doppler < 0.0) doppler = 0.0;
    if (doppler > 12.0) doppler = 12.0;
    if (depth < 0.0) depth = 0.0;
    if (depth > 1.0) depth = 1.0;

    int channels = input->channels;
    int sample_rate = input->sample_rate;
    size_t input_frames = get_frames(input);

    if (input_frames < 2) {
        cdp_lib_set_error(ctx, "spin: input too short");
        return NULL;
    }

    /* Output is always stereo */
    int out_channels = 2;

    /* For doppler, we need to potentially stretch/compress, estimate max length */
    /* Doppler max shift is +/- doppler semitones, which is a pitch ratio of 2^(doppler/12) */
    /* At max slowdown, we need more samples; max speedup, fewer */
    double max_pitch_ratio = pow(2.0, doppler / 12.0);
    size_t out_frames_estimate = (size_t)(input_frames * max_pitch_ratio * 1.1) + 1024;

    /* If no doppler, output length equals input length */
    if (doppler < 0.001) {
        out_frames_estimate = input_frames;
    }

    /* Allocate output buffer */
    float* out_data = (float*)calloc(out_frames_estimate * out_channels, sizeof(float));
    if (!out_data) {
        cdp_lib_set_error(ctx, "spin: memory allocation failed");
        return NULL;
    }

    /* Pre-calculate angular frequency (radians per sample) */
    double omega = 2.0 * M_PI * rate / sample_rate;

    /* Process each sample */
    double phase = 0.0;           /* Current phase in radians */
    double read_pos = 0.0;        /* Position in input (for doppler) */
    size_t out_idx = 0;

    while (read_pos < input_frames - 1 && out_idx < out_frames_estimate) {
        /* Get current position in rotation (-1 to +1, where 0 is center) */
        double pos = sin(phase) * depth;  /* -depth to +depth */

        /* Equal-power panning coefficients */
        /* Map pos from [-1, 1] to [0, PI/2] for pan angle */
        double pan_angle = (pos + 1.0) * 0.25 * M_PI;  /* 0 to PI/2 */
        double left_gain = cos(pan_angle);
        double right_gain = sin(pan_angle);

        /* Get input sample (with interpolation for doppler) */
        size_t i0 = (size_t)read_pos;
        size_t i1 = i0 + 1;
        if (i1 >= input_frames) i1 = input_frames - 1;
        double frac = read_pos - floor(read_pos);

        float in_left, in_right;
        if (channels == 1) {
            /* Mono input */
            float mono = (float)(input->data[i0] * (1.0 - frac) + input->data[i1] * frac);
            in_left = mono;
            in_right = mono;
        } else {
            /* Stereo input - interpolate each channel */
            in_left = (float)(input->data[i0 * 2] * (1.0 - frac) +
                             input->data[i1 * 2] * frac);
            in_right = (float)(input->data[i0 * 2 + 1] * (1.0 - frac) +
                              input->data[i1 * 2 + 1] * frac);
        }

        /* Apply panning - mix input channels with rotation */
        /* When rotating, we blend the stereo image */
        float mono_mix = (in_left + in_right) * 0.5f;
        float stereo_left = in_left * (float)(1.0 - depth) + mono_mix * (float)depth;
        float stereo_right = in_right * (float)(1.0 - depth) + mono_mix * (float)depth;

        /* Apply rotation panning */
        out_data[out_idx * 2] = stereo_left * (float)left_gain + stereo_right * (float)(1.0 - left_gain);
        out_data[out_idx * 2 + 1] = stereo_left * (float)(1.0 - right_gain) + stereo_right * (float)right_gain;

        /* Advance phase */
        phase += omega;
        if (phase > 2.0 * M_PI) phase -= 2.0 * M_PI;
        if (phase < -2.0 * M_PI) phase += 2.0 * M_PI;

        /* Calculate read position increment (for doppler) */
        double read_incr = 1.0;
        if (doppler > 0.001) {
            /* Doppler: when moving towards listener (left), pitch up; away (right), pitch down */
            /* Velocity is derivative of sin, which is cos */
            double velocity = cos(phase) * rate;  /* Normalized velocity */
            /* Scale by doppler amount (semitones) */
            double doppler_ratio = pow(2.0, (velocity * doppler) / (12.0 * 20.0));
            read_incr = doppler_ratio;
        }

        read_pos += read_incr;
        out_idx++;
    }

    /* Trim to actual length */
    size_t actual_frames = out_idx;

    /* Normalize if needed */
    float max_val = 0;
    for (size_t i = 0; i < actual_frames * out_channels; i++) {
        float abs_val = fabsf(out_data[i]);
        if (abs_val > max_val) max_val = abs_val;
    }
    if (max_val > 0.95f) {
        float scale = 0.95f / max_val;
        for (size_t i = 0; i < actual_frames * out_channels; i++) {
            out_data[i] *= scale;
        }
    }

    cdp_lib_buffer* output = cdp_lib_buffer_from_data(out_data, actual_frames * out_channels, out_channels, sample_rate);
    if (!output) {
        free(out_data);
        cdp_lib_set_error(ctx, "spin: failed to create output buffer");
        return NULL;
    }

    return output;
}

/*
 * Rotor - dual-rotation modulation effect.
 *
 * Applies two independent rotating modulators (pitch and amplitude)
 * with different cycle lengths, creating evolving interference patterns.
 */
cdp_lib_buffer* cdp_lib_rotor(cdp_lib_ctx* ctx,
                               const cdp_lib_buffer* input,
                               double pitch_rate,
                               double pitch_depth,
                               double amp_rate,
                               double amp_depth,
                               double phase_offset)
{
    if (!ctx || !input) {
        if (ctx) cdp_lib_set_error(ctx, "rotor: invalid parameters");
        return NULL;
    }

    /* Validate parameters */
    if (pitch_rate < 0.01) pitch_rate = 0.01;
    if (pitch_rate > 20.0) pitch_rate = 20.0;
    if (pitch_depth < 0.0) pitch_depth = 0.0;
    if (pitch_depth > 12.0) pitch_depth = 12.0;
    if (amp_rate < 0.01) amp_rate = 0.01;
    if (amp_rate > 20.0) amp_rate = 20.0;
    if (amp_depth < 0.0) amp_depth = 0.0;
    if (amp_depth > 1.0) amp_depth = 1.0;
    if (phase_offset < 0.0) phase_offset = 0.0;
    if (phase_offset > 1.0) phase_offset = 1.0;

    int channels = input->channels;
    int sample_rate = input->sample_rate;
    size_t input_frames = get_frames(input);

    if (input_frames < 2) {
        cdp_lib_set_error(ctx, "rotor: input too short");
        return NULL;
    }

    /* Calculate maximum output length accounting for pitch shift */
    /* Max pitch down = pitch_depth semitones = 2^(pitch_depth/12) slower */
    double max_slowdown = pow(2.0, pitch_depth / 12.0);
    size_t out_frames_max = (size_t)(input_frames * max_slowdown * 1.1) + 1024;

    /* If no pitch modulation, output length equals input */
    if (pitch_depth < 0.001) {
        out_frames_max = input_frames;
    }

    /* Allocate output buffer */
    float* out_data = (float*)calloc(out_frames_max * channels, sizeof(float));
    if (!out_data) {
        cdp_lib_set_error(ctx, "rotor: memory allocation failed");
        return NULL;
    }

    /* Pre-calculate angular frequencies (radians per sample) */
    double pitch_omega = 2.0 * M_PI * pitch_rate / sample_rate;
    double amp_omega = 2.0 * M_PI * amp_rate / sample_rate;

    /* Initial phases */
    double pitch_phase = 0.0;
    double amp_phase = phase_offset * 2.0 * M_PI;  /* Convert 0-1 to radians */

    /* Process with variable-rate resampling for pitch modulation */
    double read_pos = 0.0;
    size_t out_idx = 0;

    while (read_pos < input_frames - 1 && out_idx < out_frames_max) {
        /* Calculate pitch modulation (in semitones) */
        /* sin gives -1 to +1, scale by depth */
        double pitch_mod = sin(pitch_phase) * pitch_depth;

        /* Calculate playback rate ratio */
        /* Positive pitch_mod = pitch up = faster playback */
        double pitch_ratio = pow(2.0, pitch_mod / 12.0);

        /* Calculate amplitude modulation */
        /* sin gives -1 to +1, map to (1-depth) to 1 for unipolar modulation */
        double amp_mod = 1.0 - amp_depth * (1.0 - (sin(amp_phase) + 1.0) * 0.5);

        /* Interpolate input sample */
        size_t i0 = (size_t)read_pos;
        size_t i1 = i0 + 1;
        if (i1 >= input_frames) i1 = input_frames - 1;
        double frac = read_pos - floor(read_pos);

        /* Write output samples with amplitude modulation */
        for (int ch = 0; ch < channels; ch++) {
            float v0 = input->data[i0 * channels + ch];
            float v1 = input->data[i1 * channels + ch];
            float interp = (float)(v0 + frac * (v1 - v0));
            out_data[out_idx * channels + ch] = interp * (float)amp_mod;
        }

        /* Advance phases */
        pitch_phase += pitch_omega;
        amp_phase += amp_omega;

        /* Keep phases in reasonable range */
        if (pitch_phase > 2.0 * M_PI) pitch_phase -= 2.0 * M_PI;
        if (amp_phase > 2.0 * M_PI) amp_phase -= 2.0 * M_PI;

        /* Advance read position by pitch ratio */
        read_pos += pitch_ratio;
        out_idx++;
    }

    /* Trim to actual length */
    size_t actual_frames = out_idx;

    /* Normalize if needed */
    float max_val = 0;
    for (size_t i = 0; i < actual_frames * channels; i++) {
        float abs_val = fabsf(out_data[i]);
        if (abs_val > max_val) max_val = abs_val;
    }
    if (max_val > 0.95f) {
        float scale = 0.95f / max_val;
        for (size_t i = 0; i < actual_frames * channels; i++) {
            out_data[i] *= scale;
        }
    }

    cdp_lib_buffer* output = cdp_lib_buffer_from_data(out_data, actual_frames * channels, channels, sample_rate);
    if (!output) {
        free(out_data);
        cdp_lib_set_error(ctx, "rotor: failed to create output buffer");
        return NULL;
    }

    return output;
}
