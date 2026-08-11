/*
 * CDP Reverb Processing - Implementation
 *
 * Implements a Freeverb-style FDN reverb with 8 comb filters and 4 allpass filters.
 */

#include "cdp_reverb.h"
#include "cdp_lib_internal.h"

/* Comb filter delay line */
typedef struct {
    float *buffer;
    size_t size;
    size_t read_pos;
    float feedback;
    float damp1;
    float damp2;
    float filterstore;
} comb_filter;

/* Allpass filter delay line */
typedef struct {
    float *buffer;
    size_t size;
    size_t index;
    float feedback;
} allpass_filter;

static void comb_init(comb_filter *c, size_t size, float feedback, float damp) {
    c->buffer = (float*)calloc(size, sizeof(float));
    c->size = size;
    c->read_pos = 0;
    c->feedback = feedback;
    c->damp1 = damp;
    c->damp2 = 1.0f - damp;
    c->filterstore = 0;
}

static void comb_free(comb_filter *c) {
    if (c->buffer) free(c->buffer);
}

static float comb_process(comb_filter *c, float input) {
    float output = c->buffer[c->read_pos];

    /* Low-pass filter the feedback */
    c->filterstore = output * c->damp2 + c->filterstore * c->damp1;

    c->buffer[c->read_pos] = input + c->filterstore * c->feedback;

    c->read_pos++;
    if (c->read_pos >= c->size) c->read_pos = 0;

    return output;
}

static void allpass_init(allpass_filter *a, size_t size, float feedback) {
    a->buffer = (float*)calloc(size, sizeof(float));
    a->size = size;
    a->index = 0;
    a->feedback = feedback;
}

static void allpass_free(allpass_filter *a) {
    if (a->buffer) free(a->buffer);
}

static float allpass_process(allpass_filter *a, float input) {
    float bufout = a->buffer[a->index];

    float output = -input + bufout;
    a->buffer[a->index] = input + bufout * a->feedback;

    a->index++;
    if (a->index >= a->size) a->index = 0;

    return output;
}

/* Freeverb-style tuning constants (in samples at 44100 Hz) */
#define COMB_TUNING_L1 1116
#define COMB_TUNING_L2 1188
#define COMB_TUNING_L3 1277
#define COMB_TUNING_L4 1356
#define COMB_TUNING_L5 1422
#define COMB_TUNING_L6 1491
#define COMB_TUNING_L7 1557
#define COMB_TUNING_L8 1617
#define ALLPASS_TUNING_L1 556
#define ALLPASS_TUNING_L2 441
#define ALLPASS_TUNING_L3 341
#define ALLPASS_TUNING_L4 225

cdp_lib_buffer* cdp_lib_reverb(cdp_lib_ctx* ctx,
                                const cdp_lib_buffer* input,
                                double mix,
                                double decay_time,
                                double damping,
                                double lpfreq,
                                double predelay) {
    if (ctx == NULL || input == NULL) {
        return NULL;
    }

    /* Validate parameters */
    if (mix < 0 || mix > 1) mix = 0.5;
    if (decay_time <= 0) decay_time = 2.0;
    if (damping < 0) damping = 0;
    if (damping > 1) damping = 1;
    if (lpfreq <= 0) lpfreq = 8000;
    if (predelay < 0) predelay = 0;

    int sample_rate = input->sample_rate;
    double scale = (double)sample_rate / 44100.0;

    /* Convert to mono if stereo */
    cdp_lib_buffer* mono = cdp_lib_to_mono(ctx, input);
    if (mono == NULL) return NULL;

    size_t input_samples = mono->length;

    /* Calculate output length (input + reverb tail) */
    size_t tail_samples = (size_t)(decay_time * sample_rate);
    size_t output_samples = input_samples + tail_samples;

    /* Calculate pre-delay in samples */
    size_t predelay_samples = (size_t)(predelay * sample_rate / 1000.0);

    /* Allocate output buffer (stereo) */
    cdp_lib_buffer* output = cdp_lib_buffer_create(output_samples * 2, 2, sample_rate);
    if (output == NULL) {
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Failed to allocate output buffer");
        return NULL;
    }

    /* Calculate feedback coefficient from RT60 */
    /* RT60 = -3 * delay_time / log10(feedback^2) */
    /* feedback = 10^(-3 * delay_time / RT60) */
    double avg_delay = (COMB_TUNING_L1 + COMB_TUNING_L8) / 2.0 * scale / sample_rate;
    double room_feedback = pow(10.0, -3.0 * avg_delay / decay_time);
    if (room_feedback > 0.99) room_feedback = 0.99;

    /* Initialize comb filters for left channel */
    comb_filter combs_l[8];
    comb_init(&combs_l[0], (size_t)(COMB_TUNING_L1 * scale), (float)room_feedback, (float)damping);
    comb_init(&combs_l[1], (size_t)(COMB_TUNING_L2 * scale), (float)room_feedback, (float)damping);
    comb_init(&combs_l[2], (size_t)(COMB_TUNING_L3 * scale), (float)room_feedback, (float)damping);
    comb_init(&combs_l[3], (size_t)(COMB_TUNING_L4 * scale), (float)room_feedback, (float)damping);
    comb_init(&combs_l[4], (size_t)(COMB_TUNING_L5 * scale), (float)room_feedback, (float)damping);
    comb_init(&combs_l[5], (size_t)(COMB_TUNING_L6 * scale), (float)room_feedback, (float)damping);
    comb_init(&combs_l[6], (size_t)(COMB_TUNING_L7 * scale), (float)room_feedback, (float)damping);
    comb_init(&combs_l[7], (size_t)(COMB_TUNING_L8 * scale), (float)room_feedback, (float)damping);

    /* Initialize comb filters for right channel (slightly offset for stereo) */
    comb_filter combs_r[8];
    comb_init(&combs_r[0], (size_t)((COMB_TUNING_L1 + 23) * scale), (float)room_feedback, (float)damping);
    comb_init(&combs_r[1], (size_t)((COMB_TUNING_L2 + 23) * scale), (float)room_feedback, (float)damping);
    comb_init(&combs_r[2], (size_t)((COMB_TUNING_L3 + 23) * scale), (float)room_feedback, (float)damping);
    comb_init(&combs_r[3], (size_t)((COMB_TUNING_L4 + 23) * scale), (float)room_feedback, (float)damping);
    comb_init(&combs_r[4], (size_t)((COMB_TUNING_L5 + 23) * scale), (float)room_feedback, (float)damping);
    comb_init(&combs_r[5], (size_t)((COMB_TUNING_L6 + 23) * scale), (float)room_feedback, (float)damping);
    comb_init(&combs_r[6], (size_t)((COMB_TUNING_L7 + 23) * scale), (float)room_feedback, (float)damping);
    comb_init(&combs_r[7], (size_t)((COMB_TUNING_L8 + 23) * scale), (float)room_feedback, (float)damping);

    /* Initialize allpass filters */
    allpass_filter allpasses_l[4], allpasses_r[4];
    allpass_init(&allpasses_l[0], (size_t)(ALLPASS_TUNING_L1 * scale), 0.5f);
    allpass_init(&allpasses_l[1], (size_t)(ALLPASS_TUNING_L2 * scale), 0.5f);
    allpass_init(&allpasses_l[2], (size_t)(ALLPASS_TUNING_L3 * scale), 0.5f);
    allpass_init(&allpasses_l[3], (size_t)(ALLPASS_TUNING_L4 * scale), 0.5f);

    allpass_init(&allpasses_r[0], (size_t)((ALLPASS_TUNING_L1 + 23) * scale), 0.5f);
    allpass_init(&allpasses_r[1], (size_t)((ALLPASS_TUNING_L2 + 23) * scale), 0.5f);
    allpass_init(&allpasses_r[2], (size_t)((ALLPASS_TUNING_L3 + 23) * scale), 0.5f);
    allpass_init(&allpasses_r[3], (size_t)((ALLPASS_TUNING_L4 + 23) * scale), 0.5f);

    /* comb_init/allpass_init cannot report failure, so verify every delay line
       here: comb_process and allpass_process dereference buffer unconditionally.
       comb_free/allpass_free tolerate a NULL buffer, so partial init frees
       cleanly. */
    int delay_lines_ok = 1;
    for (int c = 0; c < 8; c++) {
        if (combs_l[c].buffer == NULL || combs_r[c].buffer == NULL) delay_lines_ok = 0;
    }
    for (int a = 0; a < 4; a++) {
        if (allpasses_l[a].buffer == NULL || allpasses_r[a].buffer == NULL) delay_lines_ok = 0;
    }
    if (!delay_lines_ok) {
        for (int c = 0; c < 8; c++) {
            comb_free(&combs_l[c]);
            comb_free(&combs_r[c]);
        }
        for (int a = 0; a < 4; a++) {
            allpass_free(&allpasses_l[a]);
            allpass_free(&allpasses_r[a]);
        }
        cdp_lib_buffer_free(output);
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Failed to allocate reverb delay lines");
        return NULL;
    }

    /* Pre-delay buffer */
    float *predelay_buf = NULL;
    if (predelay_samples > 0) {
        predelay_buf = (float*)calloc(predelay_samples, sizeof(float));
    }
    size_t predelay_idx = 0;

    /* Simple one-pole lowpass filter state */
    float lp_state_l = 0, lp_state_r = 0;
    double lp_coeff = 1.0 - exp(-2.0 * M_PI * lpfreq / sample_rate);

    /* Process audio */
    for (size_t i = 0; i < output_samples; i++) {
        /* Get input sample (or 0 if past end of input) */
        float in_sample;
        if (i < input_samples) {
            in_sample = mono->data[i];
        } else {
            in_sample = 0;
        }

        /* Apply pre-delay */
        float delayed_in = in_sample;
        if (predelay_buf != NULL) {
            float old = predelay_buf[predelay_idx];
            predelay_buf[predelay_idx] = in_sample;
            predelay_idx++;
            if (predelay_idx >= predelay_samples) predelay_idx = 0;
            delayed_in = old;
        }

        /* Process through comb filters (in parallel) */
        float wet_l = 0, wet_r = 0;
        for (int c = 0; c < 8; c++) {
            wet_l += comb_process(&combs_l[c], delayed_in);
            wet_r += comb_process(&combs_r[c], delayed_in);
        }

        /* Process through allpass filters (in series) */
        for (int a = 0; a < 4; a++) {
            wet_l = allpass_process(&allpasses_l[a], wet_l);
            wet_r = allpass_process(&allpasses_r[a], wet_r);
        }

        /* Apply lowpass filter */
        lp_state_l += (float)(lp_coeff * (wet_l - lp_state_l));
        lp_state_r += (float)(lp_coeff * (wet_r - lp_state_r));
        wet_l = lp_state_l;
        wet_r = lp_state_r;

        /* Scale down wet signal */
        wet_l *= 0.015f;
        wet_r *= 0.015f;

        /* Mix dry and wet */
        float dry = (i < input_samples) ? mono->data[i] : 0.0f;
        float out_l = (float)(dry * (1.0 - mix) + wet_l * mix);
        float out_r = (float)(dry * (1.0 - mix) + wet_r * mix);

        output->data[i * 2] = out_l;
        output->data[i * 2 + 1] = out_r;
    }

    /* Cleanup */
    for (int c = 0; c < 8; c++) {
        comb_free(&combs_l[c]);
        comb_free(&combs_r[c]);
    }
    for (int a = 0; a < 4; a++) {
        allpass_free(&allpasses_l[a]);
        allpass_free(&allpasses_r[a]);
    }
    if (predelay_buf) free(predelay_buf);
    cdp_lib_buffer_free(mono);

    return output;
}
