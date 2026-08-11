/*
 * CDP Hover - Zigzag Reading / Pitch Hovering Effect Implementation
 *
 * Implements the CDP hover algorithm that reads through an audio file
 * with zigzag motion at a specified frequency, creating a hovering/
 * vibrato-like pitch effect.
 */

#include "cdp_hover.h"
#include "cdp_lib_internal.h"
#include <stdlib.h>
#include <string.h>
#include <math.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

#define MIN_HOVER_FREQ 0.1
#define MAX_HOVER_FREQ 1000.0
#define MIN_SPLICE_MS 0.1
#define MAX_SPLICE_MS 100.0

/*
 * Apply hover effect - zigzag reading at specified frequency.
 */
cdp_lib_buffer* cdp_lib_hover(cdp_lib_ctx* ctx,
                               const cdp_lib_buffer* input,
                               double frequency,
                               double location,
                               double frq_rand,
                               double loc_rand,
                               double splice_ms,
                               double duration) {
    if (ctx == NULL || input == NULL) {
        if (ctx) cdp_lib_set_error(ctx, "NULL input");
        return NULL;
    }

    /* Hover only works on mono input */
    if (input->channels != 1) {
        cdp_lib_set_error(ctx, "Hover requires mono input");
        return NULL;
    }

    if (frequency < MIN_HOVER_FREQ || frequency > MAX_HOVER_FREQ) {
        cdp_lib_set_error(ctx, "frequency must be between 0.1 and 1000.0 Hz");
        return NULL;
    }

    if (location < 0.0 || location > 1.0) {
        cdp_lib_set_error(ctx, "location must be between 0.0 and 1.0");
        return NULL;
    }

    if (frq_rand < 0.0 || frq_rand > 1.0) {
        cdp_lib_set_error(ctx, "frq_rand must be between 0.0 and 1.0");
        return NULL;
    }

    if (loc_rand < 0.0 || loc_rand > 1.0) {
        cdp_lib_set_error(ctx, "loc_rand must be between 0.0 and 1.0");
        return NULL;
    }

    if (splice_ms < MIN_SPLICE_MS || splice_ms > MAX_SPLICE_MS) {
        cdp_lib_set_error(ctx, "splice_ms must be between 0.1 and 100.0");
        return NULL;
    }

    int sample_rate = input->sample_rate;
    size_t input_length = input->length;
    double input_duration = (double)input_length / (double)sample_rate;

    /* Default duration to input duration if 0 or negative */
    if (duration <= 0.0) {
        duration = input_duration;
    }

    /* Calculate output length */
    size_t output_length = (size_t)(duration * sample_rate);
    if (output_length == 0) {
        cdp_lib_set_error(ctx, "Output duration too short");
        return NULL;
    }

    /* Convert parameters to samples */
    int splice_samps = (int)(splice_ms * 0.001 * sample_rate);
    if (splice_samps < 1) splice_samps = 1;

    /* Traverse = total zig+zag read in samples = sample_rate / frequency */
    int base_traverse = (int)(sample_rate / frequency);
    if (base_traverse < 4) base_traverse = 4;
    if (base_traverse > (int)(input_length * 2)) {
        base_traverse = (int)(input_length * 2);
    }

    /* Check splice length against traverse */
    if (splice_samps * 2 >= base_traverse) {
        cdp_lib_set_error(ctx, "splice_ms too long for given frequency");
        return NULL;
    }

    /* Convert location from normalized (0-1) to sample position */
    int base_location = (int)(location * (input_length - 1));
    if (base_location < 0) base_location = 0;
    if (base_location >= (int)input_length) base_location = (int)input_length - 1;

    /* Seed random generator */
    cdp_lib_seed(ctx, (uint64_t)(input_length ^ (unsigned int)(frequency * 1000)));

    /* Allocate output buffer */
    cdp_lib_buffer* output = cdp_lib_buffer_create(output_length, 1, sample_rate);
    if (output == NULL) {
        cdp_lib_set_error(ctx, "Failed to allocate output buffer");
        return NULL;
    }
    memset(output->data, 0, output_length * sizeof(float));

    /* Allocate splice buffer for crossfading */
    float* splice_buf = (float*)malloc(splice_samps * sizeof(float));
    if (splice_buf == NULL) {
        cdp_lib_buffer_free(output);
        cdp_lib_set_error(ctx, "Failed to allocate splice buffer");
        return NULL;
    }

    double splice_incr = 1.0 / (double)splice_samps;
    size_t out_pos = 0;

    /* Initialize first traverse and location with random variations */
    int traverse = base_traverse;
    int current_location = base_location;

    /* Apply initial random variations */
    if (loc_rand > 0.0) {
        double randvar = loc_rand * cdp_lib_random(ctx);
        randvar = randvar * 2.0 - 1.0;  /* Range -loc_rand to +loc_rand */
        int rand_samps = (int)(traverse * randvar);
        current_location += rand_samps;
    }

    if (frq_rand > 0.0) {
        double randvar = frq_rand * cdp_lib_random(ctx);
        randvar = randvar * 2.0 - 1.0;  /* Range -frq_rand to +frq_rand */
        int rand_samps = (int)(traverse * randvar);
        traverse += rand_samps;
    }

    /* Ensure traverse is valid */
    if (traverse <= splice_samps * 2)
        traverse = (splice_samps * 2) + 1;
    if (traverse > (int)(input_length * 2))
        traverse = (int)(input_length * 2);

    /* The zigzag starts to the left of the location */
    int quarter_cycle = traverse / 4;
    current_location -= quarter_cycle;

    /* Bounds check location */
    if (current_location < 0) current_location = 0;
    if (current_location >= (int)input_length)
        current_location = (int)input_length - 1;

    /* Max traverse based on location */
    int max_traverse = (2 * (int)input_length) - current_location;
    if (traverse > max_traverse)
        traverse = max_traverse;

    /* Main hover loop */
    size_t last_out_pos = out_pos;
    int stall_count = 0;
    const int MAX_STALLS = 10;  /* Safety limit to prevent infinite loops */

    while (out_pos < output_length && stall_count < MAX_STALLS) {
        /* Calculate next location and traverse for drift calculation */
        int next_traverse = base_traverse;
        int next_location = base_location;

        if (loc_rand > 0.0) {
            double randvar = loc_rand * cdp_lib_random(ctx);
            randvar = randvar * 2.0 - 1.0;
            int rand_samps = (int)(next_traverse * randvar);
            next_location += rand_samps;
        }

        if (frq_rand > 0.0) {
            double randvar = frq_rand * cdp_lib_random(ctx);
            randvar = randvar * 2.0 - 1.0;
            int rand_samps = (int)(next_traverse * randvar);
            next_traverse += rand_samps;
        }

        if (next_traverse <= splice_samps * 2)
            next_traverse = (splice_samps * 2) + 1;
        if (next_traverse > (int)(input_length * 2))
            next_traverse = (int)(input_length * 2);

        quarter_cycle = next_traverse / 4;
        next_location -= quarter_cycle;

        if (next_location < 0) next_location = 0;
        if (next_location >= (int)input_length)
            next_location = (int)input_length - 1;

        /* Calculate zig (forward) and zag (backward) based on drift */
        int step = next_location - current_location;
        int zig, zag;

        if (abs(step) > traverse) {
            if (step > 0)
                zig = traverse;     /* Large positive step: full forward */
            else
                zig = 0;            /* Large negative step: full backward */
        } else {
            zig = (step + traverse) / 2;
        }

        /* Ensure zig is non-negative */
        if (zig < 0) zig = 0;

        /* Ensure zig doesn't overshoot end of input */
        if ((zig + current_location) > (int)input_length)
            zig = (int)input_length - current_location;
        if (zig < 0) zig = 0;

        zag = traverse - zig;
        if (zag < 0) zag = 0;

        /* Check zag doesn't overshoot beginning */
        int max_zag = max_traverse - zig;
        if (max_zag < 0) max_zag = 0;
        if (zag > max_zag) {
            zag = max_zag;
            traverse = zig + zag;
        }

        /* Ensure we make at least some progress */
        if (zig + zag < splice_samps + 1) {
            /* Force minimum progress */
            zig = splice_samps + 1;
            zag = 0;
        }

        int total_cycle = zig + zag;
        int endplice_start = total_cycle - splice_samps;
        if (endplice_start < splice_samps) endplice_start = splice_samps;
        double splice = splice_incr;

        /* Read position in input buffer */
        int read_pos = current_location;

        /* Process zig (forward reading) */
        for (int n = 0; n < zig && out_pos < output_length; n++) {
            if (read_pos >= 0 && read_pos < (int)input_length) {
                double val = input->data[read_pos];

                /* Apply splice envelope */
                if (n < splice_samps) {
                    val *= splice;
                    splice += splice_incr;
                } else if (n >= endplice_start) {
                    splice -= splice_incr;
                    if (splice < 0.0) splice = 0.0;
                    val *= splice;
                }

                /* Mix into output with crossfade */
                output->data[out_pos] += (float)val;
            }
            read_pos++;
            out_pos++;
        }

        /* Process zag (backward reading) */
        read_pos--;  /* Start from last zig position */
        for (int n = 0; n < zag && out_pos < output_length; n++) {
            int k = zig + n;  /* Overall position in zig+zag cycle */
            if (read_pos >= 0 && read_pos < (int)input_length) {
                double val = input->data[read_pos];

                /* Apply splice envelope */
                if (k < splice_samps) {
                    val *= splice;
                    splice += splice_incr;
                } else if (k >= endplice_start) {
                    splice -= splice_incr;
                    if (splice < 0.0) splice = 0.0;
                    val *= splice;
                }

                /* Mix into output with crossfade */
                output->data[out_pos] += (float)val;
            }
            read_pos--;
            out_pos++;
        }

        /* Back track for splice overlap, but ensure forward progress */
        if (out_pos > (size_t)splice_samps && out_pos > last_out_pos + 1) {
            out_pos -= splice_samps;
        }

        /* Check for progress to avoid infinite loops */
        if (out_pos <= last_out_pos) {
            stall_count++;
            /* Force progress by advancing output */
            out_pos = last_out_pos + 1;
        } else {
            stall_count = 0;
        }
        last_out_pos = out_pos;

        /* Update for next cycle */
        current_location = next_location;
        traverse = next_traverse;
        max_traverse = (2 * (int)input_length) - current_location;
        if (max_traverse < 1) max_traverse = 1;
        if (traverse > max_traverse)
            traverse = max_traverse;
    }

    free(splice_buf);
    return output;
}
