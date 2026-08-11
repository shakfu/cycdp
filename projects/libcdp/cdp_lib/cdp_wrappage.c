/*
 * CDP Wrappage - Granular Texture with Spatial Distribution
 *
 * Granular reconstitution with stereo spatial spreading.
 * Based on CDP's wrappage/sausage algorithms.
 */

#include "cdp_wrappage.h"
#include "cdp_lib_internal.h"
#include <stdlib.h>
#include <string.h>
#include <math.h>

#define MIN_GRAIN_SIZE 1.0
#define MAX_GRAIN_SIZE 500.0
#define MIN_DENSITY 0.1
#define MAX_DENSITY 10.0
#define MIN_VELOCITY 0.0
#define MAX_VELOCITY 10.0
#define MIN_PITCH -24.0
#define MAX_PITCH 24.0
#define MIN_SPREAD 0.0
#define MAX_SPREAD 1.0
#define MIN_JITTER 0.0
#define MAX_JITTER 1.0
#define MIN_SPLICE 0.5
#define MAX_SPLICE 50.0

/*
 * Interpolate a sample value at a fractional position.
 */
static float interp_sample(const float* data, size_t length, double position) {
    if (position < 0) position = 0;
    if (position >= length - 1) position = length - 1.001;

    size_t idx = (size_t)position;
    double frac = position - idx;

    if (idx + 1 >= length) {
        return data[idx];
    }

    return (float)(data[idx] * (1.0 - frac) + data[idx + 1] * frac);
}

/*
 * Apply wrappage effect.
 */
cdp_lib_buffer* cdp_lib_wrappage(cdp_lib_ctx* ctx,
                                  const cdp_lib_buffer* input,
                                  double grain_size,
                                  double density,
                                  double velocity,
                                  double pitch,
                                  double spread,
                                  double jitter,
                                  double splice_ms,
                                  double duration,
                                  unsigned int seed) {
    if (ctx == NULL || input == NULL) {
        if (ctx) cdp_lib_set_error(ctx, "NULL input");
        return NULL;
    }

    if (input->channels != 1) {
        cdp_lib_set_error(ctx, "Input must be mono");
        return NULL;
    }

    /* Validate parameters */
    if (grain_size < MIN_GRAIN_SIZE || grain_size > MAX_GRAIN_SIZE) {
        cdp_lib_set_error(ctx, "grain_size must be between 1.0 and 500.0 ms");
        return NULL;
    }
    if (density < MIN_DENSITY || density > MAX_DENSITY) {
        cdp_lib_set_error(ctx, "density must be between 0.1 and 10.0");
        return NULL;
    }
    if (velocity < MIN_VELOCITY || velocity > MAX_VELOCITY) {
        cdp_lib_set_error(ctx, "velocity must be between 0.0 and 10.0");
        return NULL;
    }
    if (pitch < MIN_PITCH || pitch > MAX_PITCH) {
        cdp_lib_set_error(ctx, "pitch must be between -24.0 and 24.0 semitones");
        return NULL;
    }
    if (spread < MIN_SPREAD || spread > MAX_SPREAD) {
        cdp_lib_set_error(ctx, "spread must be between 0.0 and 1.0");
        return NULL;
    }
    if (jitter < MIN_JITTER || jitter > MAX_JITTER) {
        cdp_lib_set_error(ctx, "jitter must be between 0.0 and 1.0");
        return NULL;
    }
    if (splice_ms < MIN_SPLICE || splice_ms > MAX_SPLICE) {
        cdp_lib_set_error(ctx, "splice_ms must be between 0.5 and 50.0");
        return NULL;
    }
    if (velocity == 0.0 && duration <= 0.0) {
        cdp_lib_set_error(ctx, "duration must be specified when velocity is 0");
        return NULL;
    }

    int sample_rate = input->sample_rate;
    size_t input_length = input->length;
    double input_duration = (double)input_length / sample_rate;

    /* Calculate output duration */
    double out_duration;
    if (duration > 0.0) {
        out_duration = duration;
    } else if (velocity > 0.0) {
        out_duration = input_duration / velocity;
    } else {
        out_duration = input_duration;
    }

    /* Calculate sizes in samples */
    size_t grain_samps = (size_t)(grain_size * sample_rate / 1000.0);
    size_t splice_samps = (size_t)(splice_ms * sample_rate / 1000.0);

    /* Ensure splice is less than half the grain */
    if (splice_samps * 2 >= grain_samps) {
        splice_samps = grain_samps / 2 - 1;
    }
    if (splice_samps < 1) splice_samps = 1;

    /* Calculate grain step (output) based on density */
    size_t out_step = (size_t)(grain_samps / density);
    if (out_step < 1) out_step = 1;

    /* Calculate input step based on velocity and pitch */
    double transpos = pow(2.0, pitch / 12.0);
    size_t in_step = (size_t)(out_step * velocity);

    /* Allocate output buffer (stereo) */
    size_t output_length = (size_t)(out_duration * sample_rate) * 2;
    if (output_length < 2) output_length = 2;

    cdp_lib_buffer* output = cdp_lib_buffer_create(output_length, 2, sample_rate);
    if (output == NULL) {
        cdp_lib_set_error(ctx, "Failed to allocate output buffer");
        return NULL;
    }
    memset(output->data, 0, output_length * sizeof(float));

    /* Allocate grain buffer - always grain_samps output samples */
    size_t grain_buf_size = grain_samps + 16;
    float* grain_buf = (float*)malloc(grain_buf_size * sizeof(float));
    if (grain_buf == NULL) {
        cdp_lib_buffer_free(output);
        cdp_lib_set_error(ctx, "Failed to allocate grain buffer");
        return NULL;
    }

    /* Seed the context PRNG. This was previously a file-static generator
       hardcoded to 42, which made wrappage the only granular operation whose
       randomisation could not be controlled, and shared state across calls. */
    cdp_lib_seed(ctx, seed);

    /* Process grains */
    double in_pos = 0.0;  /* Current position in input (fractional samples) */
    size_t out_pos = 0;   /* Current position in output (stereo samples, i.e., pairs) */
    size_t out_frames = output_length / 2;

    /* Track max amplitude for normalization */
    float max_amp = 0.0f;

    while (out_pos < out_frames) {
        /* Apply jitter to input position */
        double jittered_in_pos = in_pos;
        if (jitter > 0.0) {
            double jitter_range = grain_samps * jitter;
            double jitter_offset = (cdp_lib_random(ctx) - 0.5) * 2.0 * jitter_range;
            jittered_in_pos += jitter_offset;
            if (jittered_in_pos < 0) jittered_in_pos = 0;
        }

        /* Check if we've reached the end of input */
        if (jittered_in_pos + grain_samps * transpos >= input_length) {
            if (velocity > 0.0 && duration <= 0.0) {
                break;  /* End of input reached */
            }
            /* Wrap around for frozen or specified duration */
            jittered_in_pos = fmod(jittered_in_pos, (double)(input_length - grain_samps * transpos - 1));
            if (jittered_in_pos < 0) jittered_in_pos = 0;
        }

        /* Extract grain with pitch shift (interpolation) */
        double src_pos = jittered_in_pos;
        for (size_t i = 0; i < grain_samps; i++) {
            grain_buf[i] = interp_sample(input->data, input_length, src_pos);
            src_pos += transpos;
        }

        /* Apply envelope (linear splices) */
        for (size_t i = 0; i < splice_samps; i++) {
            double env = (double)i / splice_samps;
            grain_buf[i] *= (float)env;
        }
        for (size_t i = 0; i < splice_samps; i++) {
            double env = (double)(splice_samps - i) / splice_samps;
            size_t idx = grain_samps - splice_samps + i;
            if (idx < grain_samps) {
                grain_buf[idx] *= (float)env;
            }
        }

        /* Calculate stereo position for this grain */
        double pan = 0.5;  /* Center by default */
        if (spread > 0.0) {
            /* Random position within spread range, centered */
            pan = 0.5 + (cdp_lib_random(ctx) - 0.5) * spread;
        }

        /* Calculate stereo gains (constant power panning) */
        double pan_angle = pan * M_PI / 2.0;
        float left_gain = (float)cos(pan_angle);
        float right_gain = (float)sin(pan_angle);

        /* Apply jitter to output position */
        size_t jittered_out_pos = out_pos;
        if (jitter > 0.0 && out_step > 1) {
            int jitter_range = (int)(out_step * jitter * 0.5);
            if (jitter_range > 0) {
                int jitter_offset = (int)((cdp_lib_random(ctx) - 0.5) * 2.0 * jitter_range);
                if ((int)jittered_out_pos + jitter_offset >= 0) {
                    jittered_out_pos = (size_t)((int)jittered_out_pos + jitter_offset);
                }
            }
        }

        /* Write grain to output (stereo, overlapping) */
        for (size_t i = 0; i < grain_samps; i++) {
            size_t out_idx = (jittered_out_pos + i) * 2;
            if (out_idx + 1 < output_length) {
                output->data[out_idx] += grain_buf[i] * left_gain;
                output->data[out_idx + 1] += grain_buf[i] * right_gain;

                /* Track max amplitude */
                float abs_left = fabsf(output->data[out_idx]);
                float abs_right = fabsf(output->data[out_idx + 1]);
                if (abs_left > max_amp) max_amp = abs_left;
                if (abs_right > max_amp) max_amp = abs_right;
            }
        }

        /* Advance positions */
        out_pos += out_step;
        in_pos += in_step;

        /* Handle velocity = 0 (freeze) */
        if (velocity == 0.0) {
            /* Stay at the same input position, just vary with jitter */
        }
    }

    /* Normalize if needed */
    if (max_amp > 1.0f) {
        float scale = 0.99f / max_amp;
        for (size_t i = 0; i < output_length; i++) {
            output->data[i] *= scale;
        }
    }

    free(grain_buf);
    return output;
}
