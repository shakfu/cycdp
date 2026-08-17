/*
 * CDP Phase - Phase Manipulation Effects Implementation
 *
 * Provides phase inversion and stereo enhancement via phase shifting.
 */

#include "cdp_phase.h"
#include "cdp_lib_internal.h"
#include <stdlib.h>
#include <string.h>
#include <math.h>

/*
 * Apply phase inversion - inverts the phase of all samples.
 */
cdp_lib_buffer* cdp_lib_phase_invert(cdp_lib_ctx* ctx,
                                      const cdp_lib_buffer* input) {
    if (ctx == NULL || input == NULL) {
        if (ctx) cdp_lib_set_error(ctx, "NULL input");
        return NULL;
    }

    size_t length = input->length;
    int channels = input->channels;
    int sample_rate = input->sample_rate;

    if (length == 0) {
        cdp_lib_set_error(ctx, "Input buffer is empty");
        return NULL;
    }

    /* Allocate output buffer */
    cdp_lib_buffer* output = cdp_lib_buffer_create(length, channels, sample_rate);
    if (output == NULL) {
        cdp_lib_set_error(ctx, "Failed to allocate output buffer");
        return NULL;
    }

    /* Invert phase: multiply all samples by -1 */
    for (size_t i = 0; i < length; i++) {
        output->data[i] = -input->data[i];
    }

    return output;
}

/*
 * Enhance stereo separation using phase subtraction.
 */
cdp_lib_buffer* cdp_lib_phase_stereo(cdp_lib_ctx* ctx,
                                      const cdp_lib_buffer* input,
                                      double transfer) {
    if (ctx == NULL || input == NULL) {
        if (ctx) cdp_lib_set_error(ctx, "NULL input");
        return NULL;
    }

    if (input->channels != 2) {
        cdp_lib_set_error(ctx, "Input must be stereo for stereo enhancement");
        return NULL;
    }

    if (transfer < 0.0 || transfer > 1.0) {
        cdp_lib_set_error(ctx, "transfer must be between 0.0 and 1.0");
        return NULL;
    }

    size_t length = input->length;
    int sample_rate = input->sample_rate;

    if (length == 0) {
        cdp_lib_set_error(ctx, "Input buffer is empty");
        return NULL;
    }

    /* If transfer is zero, just copy the input */
    if (transfer == 0.0) {
        cdp_lib_buffer* output = cdp_lib_buffer_create(length, 2, sample_rate);
        if (output == NULL) {
            cdp_lib_set_error(ctx, "Failed to allocate output buffer");
            return NULL;
        }
        memcpy(output->data, input->data, length * sizeof(float));
        return output;
    }

    /* First pass: find maximum input sample */
    double max_input = 0.0;
    for (size_t i = 0; i < length; i += 2) {
        double abs_val = fabs(input->data[i]);
        if (abs_val > max_input) max_input = abs_val;
        abs_val = fabs(input->data[i + 1]);
        if (abs_val > max_input) max_input = abs_val;
    }

    /* Second pass: find maximum output sample (before scaling) */
    double max_output = 0.0;
    for (size_t i = 0; i < length; i += 2) {
        float left = input->data[i];
        float right = input->data[i + 1];

        double super_left = left - (transfer * right);
        double super_right = right - (transfer * left);

        double abs_left = fabs(super_left);
        double abs_right = fabs(super_right);

        if (abs_left > max_output) max_output = abs_left;
        if (abs_right > max_output) max_output = abs_right;
    }

    /* Calculate scale factor to maintain original level */
    double scale_factor = 1.0;
    if (max_output > 0.0) {
        scale_factor = max_input / max_output;
    }

    /* Allocate output buffer */
    cdp_lib_buffer* output = cdp_lib_buffer_create(length, 2, sample_rate);
    if (output == NULL) {
        cdp_lib_set_error(ctx, "Failed to allocate output buffer");
        return NULL;
    }

    /* Apply stereo enhancement */
    for (size_t i = 0; i < length; i += 2) {
        float left = input->data[i];
        float right = input->data[i + 1];

        double super_left = left - (transfer * right);
        double super_right = right - (transfer * left);

        output->data[i] = (float)(super_left * scale_factor);
        output->data[i + 1] = (float)(super_right * scale_factor);
    }

    return output;
}
