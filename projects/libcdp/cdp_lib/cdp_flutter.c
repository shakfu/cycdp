/*
 * CDP Flutter - Spatial Tremolo Effect Implementation
 *
 * Implements flutter/spatial tremolo that distributes amplitude
 * modulation across stereo or multichannel outputs.
 */

#include "cdp_flutter.h"
#include "cdp_lib_internal.h"
#include <stdlib.h>
#include <string.h>
#include <math.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

#define FLUTTER_TABSIZE 1024
#define MIN_FLUTTER_FREQ 0.1
#define MAX_FLUTTER_FREQ 50.0
#define MAX_FLUTTER_DEPTH 16.0

/*
 * Shuffle an array using Fisher-Yates with constraint that
 * first element doesn't equal last_val (avoids repetition at boundaries).
 */
static void shuffle_with_constraint(cdp_lib_ctx* ctx, int* arr, int n, int last_val) {
    int attempts = 0;
    do {
        /* Fisher-Yates shuffle */
        for (int i = n - 1; i > 0; i--) {
            int j = (int)(cdp_lib_random(ctx) * (i + 1));
            int temp = arr[i];
            arr[i] = arr[j];
            arr[j] = temp;
        }
        attempts++;
    } while (arr[0] == last_val && attempts < 10 && n > 1);
}

/*
 * Create cosine lookup table for tremolo.
 * Table goes from 0 to 1 (inverted cosine starting at minimum).
 */
static double* create_flutter_table(void) {
    double* table = (double*)malloc((FLUTTER_TABSIZE + 1) * sizeof(double));
    if (table == NULL) return NULL;

    for (int i = 0; i < FLUTTER_TABSIZE; i++) {
        double cos_val = cos(M_PI * 2.0 * ((double)i / (double)FLUTTER_TABSIZE));
        cos_val = (cos_val + 1.0) / 2.0;  /* Range 0 to 1 */
        table[i] = 1.0 - cos_val;          /* Invert: starts at 0, peaks at 1 */
    }
    table[FLUTTER_TABSIZE] = 0.0;  /* Wrap-around point */

    return table;
}

/*
 * Apply flutter (spatial tremolo) effect - stereo version.
 */
cdp_lib_buffer* cdp_lib_flutter(cdp_lib_ctx* ctx,
                                 const cdp_lib_buffer* input,
                                 double frequency,
                                 double depth,
                                 double gain,
                                 int randomize) {
    if (ctx == NULL || input == NULL) {
        if (ctx) cdp_lib_set_error(ctx, "NULL input");
        return NULL;
    }

    if (frequency < MIN_FLUTTER_FREQ || frequency > MAX_FLUTTER_FREQ) {
        cdp_lib_set_error(ctx, "frequency must be between 0.1 and 50.0 Hz");
        return NULL;
    }

    if (depth < 0.0 || depth > MAX_FLUTTER_DEPTH) {
        cdp_lib_set_error(ctx, "depth must be between 0.0 and 16.0");
        return NULL;
    }

    if (gain < 0.0 || gain > 1.0) {
        cdp_lib_set_error(ctx, "gain must be between 0.0 and 1.0");
        return NULL;
    }

    /* Convert mono to stereo if needed */
    cdp_lib_buffer* stereo = NULL;
    if (input->channels == 1) {
        /* Manually convert mono to stereo */
        size_t num_frames = input->length;
        /* Allocate num_frames * 2 floats for stereo output */
        stereo = cdp_lib_buffer_create(num_frames * 2, 2, input->sample_rate);
        if (stereo == NULL) {
            cdp_lib_set_error(ctx, "Failed to allocate stereo buffer");
            return NULL;
        }
        for (size_t i = 0; i < num_frames; i++) {
            stereo->data[i * 2] = input->data[i];
            stereo->data[i * 2 + 1] = input->data[i];
        }
    } else if (input->channels == 2) {
        /* Copy stereo input - length is total samples */
        stereo = cdp_lib_buffer_create(input->length, 2, input->sample_rate);
        if (stereo == NULL) {
            cdp_lib_set_error(ctx, "Failed to allocate output buffer");
            return NULL;
        }
        memcpy(stereo->data, input->data, input->length * sizeof(float));
    } else {
        cdp_lib_set_error(ctx, "Input must be mono or stereo for basic flutter");
        return NULL;
    }

    /* Create cosine table */
    double* costab = create_flutter_table();
    if (costab == NULL) {
        cdp_lib_buffer_free(stereo);
        cdp_lib_set_error(ctx, "Failed to allocate cosine table");
        return NULL;
    }

    /* Seed random generator */
    if (randomize) {
        cdp_lib_seed(ctx, (uint64_t)(input->length ^ (unsigned int)(frequency * 1000)));
    }

    size_t num_frames = stereo->length / 2;
    int sample_rate = stereo->sample_rate;
    double tabsize_over_srate = (double)FLUTTER_TABSIZE / (double)sample_rate;

    /* Channel sets: 0 = left, 1 = right */
    int channel_order[2] = {0, 1};
    int current_set = 0;
    int last_set = 1;

    double fcospos = 0.0;
    double last_fcospos = 0.0;

    for (size_t frame = 0; frame < num_frames; frame++) {
        size_t idx = frame * 2;

        /* Check for wrap-around (cycle complete) */
        if (fcospos < last_fcospos) {
            /* Move to next channel set */
            current_set = (current_set + 1) % 2;

            /* Randomize order if requested and we've completed a full cycle */
            if (current_set == 0 && randomize) {
                shuffle_with_constraint(ctx, channel_order, 2, last_set);
                last_set = channel_order[1];
            }
        }
        last_fcospos = fcospos;

        /* Calculate tremolo value from cosine table */
        int cospos = (int)fcospos;
        double frac = fcospos - (double)cospos;
        double locos = costab[cospos];
        double hicos = costab[cospos + 1];
        double val = locos + ((hicos - locos) * frac);  /* Interpolated value */

        /* Apply depth exponent for sharper peaks when depth > 1.0 */
        if (depth > 1.0) {
            val = pow(val, depth);
        }

        /* Adjust for actual depth */
        double actual_depth = (depth > 1.0) ? 1.0 : depth;
        val *= actual_depth;

        /* Base level when tremolo is at minimum */
        double baslevel = 1.0 - actual_depth;
        if (baslevel < 0.0) baslevel = 0.0;

        /* Get samples */
        float left = stereo->data[idx] * (float)gain;
        float right = stereo->data[idx + 1] * (float)gain;

        /* Store original values */
        float left_orig = left;
        float right_orig = right;

        /* Reduce both channels to base level */
        left *= (float)baslevel;
        right *= (float)baslevel;

        /* Add tremolo to current channel set */
        int active_chan = channel_order[current_set];
        if (active_chan == 0) {
            left += left_orig * (float)val;
        } else {
            right += right_orig * (float)val;
        }

        stereo->data[idx] = left;
        stereo->data[idx + 1] = right;

        /* Advance table position */
        fcospos += frequency * tabsize_over_srate;
        while (fcospos >= FLUTTER_TABSIZE) {
            fcospos -= FLUTTER_TABSIZE;
        }
    }

    free(costab);
    return stereo;
}

/*
 * Apply flutter with custom channel sets (multichannel).
 */
cdp_lib_buffer* cdp_lib_flutter_multi(cdp_lib_ctx* ctx,
                                       const cdp_lib_buffer* input,
                                       double frequency,
                                       double depth,
                                       double gain,
                                       const int* channel_sets,
                                       int randomize) {
    if (ctx == NULL || input == NULL || channel_sets == NULL) {
        if (ctx) cdp_lib_set_error(ctx, "NULL input");
        return NULL;
    }

    if (frequency < MIN_FLUTTER_FREQ || frequency > MAX_FLUTTER_FREQ) {
        cdp_lib_set_error(ctx, "frequency must be between 0.1 and 50.0 Hz");
        return NULL;
    }

    if (depth < 0.0 || depth > MAX_FLUTTER_DEPTH) {
        cdp_lib_set_error(ctx, "depth must be between 0.0 and 16.0");
        return NULL;
    }

    if (gain < 0.0 || gain > 1.0) {
        cdp_lib_set_error(ctx, "gain must be between 0.0 and 1.0");
        return NULL;
    }

    int channels = input->channels;
    if (channels < 2) {
        cdp_lib_set_error(ctx, "Multichannel flutter requires at least 2 channels");
        return NULL;
    }

    /* Parse channel sets */
    /* Count sets and validate */
    int num_sets = 0;
    int max_channels_per_set = 0;
    int current_count = 0;
    int i = 0;

    while (channel_sets[i] != -2) {
        if (channel_sets[i] == -1) {
            if (current_count > 0) {
                num_sets++;
                if (current_count > max_channels_per_set) {
                    max_channels_per_set = current_count;
                }
                current_count = 0;
            }
        } else {
            if (channel_sets[i] < 0 || channel_sets[i] >= channels) {
                cdp_lib_set_error(ctx, "Invalid channel number in channel set");
                return NULL;
            }
            current_count++;
        }
        i++;
    }

    if (num_sets == 0) {
        cdp_lib_set_error(ctx, "No valid channel sets specified");
        return NULL;
    }

    /* Build channel set arrays */
    int** sets = (int**)malloc(num_sets * sizeof(int*));
    int* set_sizes = (int*)malloc(num_sets * sizeof(int));
    int* set_order = (int*)malloc(num_sets * sizeof(int));

    if (sets == NULL || set_sizes == NULL || set_order == NULL) {
        if (sets) free(sets);
        if (set_sizes) free(set_sizes);
        if (set_order) free(set_order);
        cdp_lib_set_error(ctx, "Failed to allocate channel set storage");
        return NULL;
    }

    for (int s = 0; s < num_sets; s++) {
        sets[s] = (int*)malloc(max_channels_per_set * sizeof(int));
        if (sets[s] == NULL) {
            for (int j = 0; j < s; j++) free(sets[j]);
            free(sets);
            free(set_sizes);
            free(set_order);
            cdp_lib_set_error(ctx, "Failed to allocate channel set");
            return NULL;
        }
        set_order[s] = s;
    }

    /* Fill channel sets */
    i = 0;
    int set_idx = 0;
    int chan_idx = 0;

    while (channel_sets[i] != -2 && set_idx < num_sets) {
        if (channel_sets[i] == -1) {
            set_sizes[set_idx] = chan_idx;
            set_idx++;
            chan_idx = 0;
        } else {
            sets[set_idx][chan_idx] = channel_sets[i];
            chan_idx++;
        }
        i++;
    }

    /* Create output buffer */
    size_t num_frames = input->length / channels;
    cdp_lib_buffer* output = cdp_lib_buffer_create(num_frames, channels, input->sample_rate);
    if (output == NULL) {
        for (int s = 0; s < num_sets; s++) free(sets[s]);
        free(sets);
        free(set_sizes);
        free(set_order);
        cdp_lib_set_error(ctx, "Failed to allocate output buffer");
        return NULL;
    }
    memcpy(output->data, input->data, input->length * sizeof(float));

    /* Create cosine table */
    double* costab = create_flutter_table();
    if (costab == NULL) {
        cdp_lib_buffer_free(output);
        for (int s = 0; s < num_sets; s++) free(sets[s]);
        free(sets);
        free(set_sizes);
        free(set_order);
        cdp_lib_set_error(ctx, "Failed to allocate cosine table");
        return NULL;
    }

    /* Seed random generator */
    if (randomize) {
        cdp_lib_seed(ctx, (uint64_t)(input->length ^ (unsigned int)(frequency * 1000)));
    }

    int sample_rate = input->sample_rate;
    double tabsize_over_srate = (double)FLUTTER_TABSIZE / (double)sample_rate;

    int current_set = 0;
    int last_set = num_sets - 1;
    double fcospos = 0.0;
    double last_fcospos = 0.0;

    /* Temporary storage for frame samples */
    float* frame_store = (float*)malloc(channels * sizeof(float));
    if (frame_store == NULL) {
        free(costab);
        cdp_lib_buffer_free(output);
        for (int s = 0; s < num_sets; s++) free(sets[s]);
        free(sets);
        free(set_sizes);
        free(set_order);
        cdp_lib_set_error(ctx, "Failed to allocate frame storage");
        return NULL;
    }

    for (size_t frame = 0; frame < num_frames; frame++) {
        size_t base_idx = frame * channels;

        /* Check for wrap-around (cycle complete) */
        if (fcospos < last_fcospos) {
            current_set++;
            if (current_set >= num_sets) {
                if (randomize) {
                    shuffle_with_constraint(ctx, set_order, num_sets, last_set);
                    last_set = set_order[num_sets - 1];
                }
                current_set = 0;
            }
        }
        last_fcospos = fcospos;

        /* Calculate tremolo value */
        int cospos = (int)fcospos;
        double frac = fcospos - (double)cospos;
        double locos = costab[cospos];
        double hicos = costab[cospos + 1];
        double val = locos + ((hicos - locos) * frac);

        if (depth > 1.0) {
            val = pow(val, depth);
        }

        double actual_depth = (depth > 1.0) ? 1.0 : depth;
        val *= actual_depth;

        double baslevel = 1.0 - actual_depth;
        if (baslevel < 0.0) baslevel = 0.0;

        /* Apply gain and store original values */
        for (int c = 0; c < channels; c++) {
            output->data[base_idx + c] *= (float)gain;
            frame_store[c] = output->data[base_idx + c];
            output->data[base_idx + c] *= (float)baslevel;
        }

        /* Add tremolo to active channel set */
        int active_set = set_order[current_set];
        for (int j = 0; j < set_sizes[active_set]; j++) {
            int ch = sets[active_set][j];
            output->data[base_idx + ch] += frame_store[ch] * (float)val;
        }

        /* Advance table position */
        fcospos += frequency * tabsize_over_srate;
        while (fcospos >= FLUTTER_TABSIZE) {
            fcospos -= FLUTTER_TABSIZE;
        }
    }

    /* Cleanup */
    free(frame_store);
    free(costab);
    for (int s = 0; s < num_sets; s++) free(sets[s]);
    free(sets);
    free(set_sizes);
    free(set_order);

    return output;
}
