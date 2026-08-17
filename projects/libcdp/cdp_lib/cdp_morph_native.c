/*
 * CDP Morph - Implementation
 *
 * Ports of CDP's specglide, specbridge and specmorph, from dev/morph/morph.c
 * by Trevor Wishart, onto this library's own spectral analysis and synthesis.
 * These are ports, not the original code: the `_native` suffix on the exported
 * names distinguishes them from the simpler generic morph in cdp_morph.c, and
 * does not mean upstream code is running.
 *
 * The approach:
 * 1. Convert audio to spectral data using cdp_spectral_analyze
 * 2. Apply the morph algorithm directly on spectral data
 * 3. Synthesize output back to audio
 */

#include "cdp_morph_native.h"
#include "cdp_lib_internal.h"
#include "cdp_spectral.h"

#include <stdlib.h>
#include <string.h>
#include <math.h>

/* cdp_spectral_analyze's accepted range; mirrored here so the power-of-2
 * rounding below cannot overflow on the way to being rejected. */
#define CDP_MIN_FFT_SIZE 64
#define CDP_MAX_FFT_SIZE 8192

/* Saturate a double into int's range. A plain cast is undefined behaviour when
 * the value does not fit, which UBSan reports and which x86-64 resolves to
 * INT_MIN. */
static double cdp_clamp_to_int(double v) {
    if (!(v == v)) return 0.0;              /* NaN */
    if (v > 2147483000.0) return 2147483000.0;
    if (v < -2147483000.0) return -2147483000.0;
    return v;
}

/* Log base 2 constant (from CDP globcon.h) */
#ifndef LOG10_OF_2
#define LOG10_OF_2 0.301029995
#endif

/*
 * Native implementation of specglide algorithm.
 *
 * This implements the core algorithm from dev/morph/morph.c:specglide()
 * directly using our spectral data structures and shim layer.
 */
cdp_lib_buffer* cdp_morph_glide_native(cdp_lib_ctx* ctx,
                                        const cdp_lib_buffer* input1,
                                        const cdp_lib_buffer* input2,
                                        double duration,
                                        int fft_size) {
    if (ctx == NULL || input1 == NULL || input2 == NULL) {
        return NULL;
    }

    /* Parameter validation */
    if (duration <= 0) duration = 1.0;
    if (fft_size <= 0) fft_size = 1024;

    /* Ensure power of 2. Clamped first: `n *= 2` is signed overflow once the
     * request passes 2^30, and cdp_spectral_analyze rejects anything outside
     * this range anyway. */
    if (fft_size < CDP_MIN_FFT_SIZE) fft_size = CDP_MIN_FFT_SIZE;
    if (fft_size > CDP_MAX_FFT_SIZE) fft_size = CDP_MAX_FFT_SIZE;
    int n = 1;
    while (n < fft_size) n *= 2;
    fft_size = n;

    /* Sample rates must match */
    if (input1->sample_rate != input2->sample_rate) {
        snprintf(ctx->error_msg, sizeof(ctx->error_msg),
                 "Sample rates must match for glide");
        return NULL;
    }

    int sample_rate = input1->sample_rate;

    /* Convert to mono if needed */
    cdp_lib_buffer* mono1 = cdp_lib_to_mono(ctx, input1);
    if (mono1 == NULL) return NULL;

    cdp_lib_buffer* mono2 = cdp_lib_to_mono(ctx, input2);
    if (mono2 == NULL) {
        cdp_lib_buffer_free(mono1);
        return NULL;
    }

    /* Analyze both inputs to spectral domain */
    cdp_spectral_data* spec1 = cdp_spectral_analyze(
        mono1->data, mono1->length, 1, sample_rate, fft_size, 4);
    if (spec1 == NULL) {
        snprintf(ctx->error_msg, sizeof(ctx->error_msg),
                 "Spectral analysis of input1 failed");
        cdp_lib_buffer_free(mono1);
        cdp_lib_buffer_free(mono2);
        return NULL;
    }

    cdp_spectral_data* spec2 = cdp_spectral_analyze(
        mono2->data, mono2->length, 1, sample_rate, fft_size, 4);
    if (spec2 == NULL) {
        snprintf(ctx->error_msg, sizeof(ctx->error_msg),
                 "Spectral analysis of input2 failed");
        cdp_spectral_data_free(spec1);
        cdp_lib_buffer_free(mono1);
        cdp_lib_buffer_free(mono2);
        return NULL;
    }

    /* Get representative frames (use middle frame from each) */
    int frame1_idx = spec1->num_frames / 2;
    int frame2_idx = spec2->num_frames / 2;

    int num_bins = spec1->num_bins;
    double chwidth = spec1->sample_rate / 2.0 / (num_bins - 1);

    /* Get amp/freq data from representative frames */
    float* amp1 = spec1->frames[frame1_idx].data;
    float* freq1 = spec1->frames[frame1_idx].data + num_bins;
    float* amp2 = spec2->frames[frame2_idx].data;
    float* freq2 = spec2->frames[frame2_idx].data + num_bins;

    /* === Establish glide ratios (from establish_glide_ratios) === */
    /* Calculate frequency interpolation factors and amplitude differences */
    double* glide_inf = (double*)calloc(num_bins, sizeof(double));
    int* glide_zero = (int*)calloc(num_bins, sizeof(int));
    float* window0 = (float*)calloc(num_bins * 2, sizeof(float));  /* working buffers */
    float* window1 = (float*)calloc(num_bins * 2, sizeof(float));

    if (!glide_inf || !glide_zero || !window0 || !window1) {
        snprintf(ctx->error_msg, sizeof(ctx->error_msg), "Memory allocation failed");
        goto cleanup_error;
    }

    /* Copy input frames to working buffers */
    for (int cc = 0; cc < num_bins; cc++) {
        window0[cc * 2] = amp1[cc];       /* AMPP */
        window0[cc * 2 + 1] = freq1[cc];  /* FREQ */
        window1[cc * 2] = amp2[cc];
        window1[cc * 2 + 1] = freq2[cc];
    }

    /* Establish glide ratios (from morph.c establish_glide_ratios) */
    for (int cc = 0; cc < num_bins; cc++) {
        int vc = cc * 2;
        glide_zero[cc] = 0;

        float f0 = window0[vc + 1];  /* FREQ */
        float f1 = window1[vc + 1];

        if (fabsf(f0) < 1e-10f || fabsf(f1) < 1e-10f) {
            /* Mark zero frequencies */
            glide_zero[cc] = 1;
            window1[vc] = 0.0f;  /* Set amp to 0 */
            window1[vc + 1] = (float)(cc * chwidth);  /* Set freq to bin center */
        } else {
            /* Amplitude difference */
            window1[vc] = window1[vc] - window0[vc];

            /* Handle negative frequencies */
            if (f0 < 0) f0 = -f0;
            if (f1 < 0) f1 = -f1;

            /* Frequency ratio in log2 */
            glide_inf[cc] = log(f1 / f0) / LOG10_OF_2;
        }
    }

    /* Calculate output frames based on duration */
    int hop_size = fft_size / 4;
    int output_frames = (int)(duration * sample_rate / hop_size);
    if (output_frames < 2) output_frames = 2;

    /* Allocate output spectral data */
    cdp_spectral_data* spec_out = (cdp_spectral_data*)calloc(1, sizeof(cdp_spectral_data));
    if (spec_out == NULL) {
        snprintf(ctx->error_msg, sizeof(ctx->error_msg), "Memory allocation failed");
        goto cleanup_error;
    }

    spec_out->frames = (cdp_spectral_frame*)calloc(output_frames, sizeof(cdp_spectral_frame));
    if (spec_out->frames == NULL) {
        free(spec_out);
        snprintf(ctx->error_msg, sizeof(ctx->error_msg), "Memory allocation failed");
        goto cleanup_error;
    }

    spec_out->num_frames = output_frames;
    spec_out->num_bins = num_bins;
    spec_out->fft_size = fft_size;
    spec_out->overlap = 4;
    spec_out->sample_rate = (float)sample_rate;
    spec_out->frame_time = (float)hop_size / sample_rate;

    /* === Main glide loop (from specglide) === */
    for (int wc = 0; wc < output_frames; wc++) {
        /* Allocate output frame */
        spec_out->frames[wc].data = (float*)malloc(num_bins * 2 * sizeof(float));
        spec_out->frames[wc].num_bins = num_bins;
        spec_out->frames[wc].fft_size = fft_size;
        spec_out->frames[wc].sample_rate = (float)sample_rate;

        if (spec_out->frames[wc].data == NULL) {
            cdp_spectral_data_free(spec_out);
            snprintf(ctx->error_msg, sizeof(ctx->error_msg), "Memory allocation failed");
            goto cleanup_error;
        }

        /* Interpolation factor (0 to 1) */
        double interp_fact = (output_frames > 1)
            ? (double)wc / (double)(output_frames - 1) : 0.5;

        float* out_amp = spec_out->frames[wc].data;
        float* out_freq = spec_out->frames[wc].data + num_bins;

        for (int cc = 0; cc < num_bins; cc++) {
            int vc = cc * 2;

            if (glide_zero[cc]) {
                /* Zero frequency: just copy input2 values */
                out_amp[cc] = window1[vc];
                out_freq[cc] = window1[vc + 1];
            } else {
                /* Amplitude: linear interpolation */
                /* Note: window1[vc] = amp2 - amp0 (difference) */
                out_amp[cc] = window0[vc] + (float)(interp_fact * window1[vc]);

                /* Frequency: exponential interpolation */
                out_freq[cc] = (float)(window0[vc + 1] * pow(2.0, glide_inf[cc] * interp_fact));
            }
        }
    }

    /* Synthesize output */
    size_t synth_len;
    float* synth_data = cdp_spectral_synthesize(spec_out, &synth_len);

    /* Cleanup */
    free(glide_inf);
    free(glide_zero);
    free(window0);
    free(window1);
    cdp_spectral_data_free(spec1);
    cdp_spectral_data_free(spec2);
    cdp_spectral_data_free(spec_out);
    cdp_lib_buffer_free(mono1);
    cdp_lib_buffer_free(mono2);

    if (synth_data == NULL) {
        snprintf(ctx->error_msg, sizeof(ctx->error_msg),
                 "Spectral synthesis failed");
        return NULL;
    }

    /* Create output buffer */
    cdp_lib_buffer* output = cdp_lib_buffer_from_data(synth_data, synth_len, 1, sample_rate);
    if (output == NULL) {
        free(synth_data);
        snprintf(ctx->error_msg, sizeof(ctx->error_msg),
                 "Failed to create output buffer");
        return NULL;
    }

    cdp_lib_normalize_if_clipping(output, 0.95f);
    return output;

cleanup_error:
    if (glide_inf) free(glide_inf);
    if (glide_zero) free(glide_zero);
    if (window0) free(window0);
    if (window1) free(window1);
    cdp_spectral_data_free(spec1);
    cdp_spectral_data_free(spec2);
    cdp_lib_buffer_free(mono1);
    cdp_lib_buffer_free(mono2);
    return NULL;
}

/*
 * Native implementation of specbridge algorithm.
 *
 * Bridge creates a crossfade between two spectral files with various
 * normalization options and interpolation timing control.
 */
cdp_lib_buffer* cdp_morph_bridge_native(cdp_lib_ctx* ctx,
                                         const cdp_lib_buffer* input1,
                                         const cdp_lib_buffer* input2,
                                         int mode,
                                         double offset,
                                         double interp_start,
                                         double interp_end,
                                         int fft_size) {
    if (ctx == NULL || input1 == NULL || input2 == NULL) {
        return NULL;
    }

    /* Parameter validation */
    if (mode < 0 || mode > 5) mode = 0;
    if (offset < 0) offset = 0;
    if (interp_start < 0) interp_start = 0;
    if (interp_start > 1) interp_start = 1;
    if (interp_end < interp_start) interp_end = interp_start;
    if (interp_end > 1) interp_end = 1;
    if (fft_size <= 0) fft_size = 1024;

    /* Ensure power of 2. Clamped first: `n *= 2` is signed overflow once the
     * request passes 2^30, and cdp_spectral_analyze rejects anything outside
     * this range anyway. */
    if (fft_size < CDP_MIN_FFT_SIZE) fft_size = CDP_MIN_FFT_SIZE;
    if (fft_size > CDP_MAX_FFT_SIZE) fft_size = CDP_MAX_FFT_SIZE;
    int n = 1;
    while (n < fft_size) n *= 2;
    fft_size = n;

    /* Sample rates must match */
    if (input1->sample_rate != input2->sample_rate) {
        snprintf(ctx->error_msg, sizeof(ctx->error_msg),
                 "Sample rates must match for bridge");
        return NULL;
    }

    int sample_rate = input1->sample_rate;

    /* Convert to mono if needed */
    cdp_lib_buffer* mono1 = cdp_lib_to_mono(ctx, input1);
    if (mono1 == NULL) return NULL;

    cdp_lib_buffer* mono2 = cdp_lib_to_mono(ctx, input2);
    if (mono2 == NULL) {
        cdp_lib_buffer_free(mono1);
        return NULL;
    }

    /* Analyze both inputs */
    cdp_spectral_data* spec1 = cdp_spectral_analyze(
        mono1->data, mono1->length, 1, sample_rate, fft_size, 4);
    if (spec1 == NULL) {
        snprintf(ctx->error_msg, sizeof(ctx->error_msg),
                 "Spectral analysis of input1 failed");
        cdp_lib_buffer_free(mono1);
        cdp_lib_buffer_free(mono2);
        return NULL;
    }

    cdp_spectral_data* spec2 = cdp_spectral_analyze(
        mono2->data, mono2->length, 1, sample_rate, fft_size, 4);
    if (spec2 == NULL) {
        snprintf(ctx->error_msg, sizeof(ctx->error_msg),
                 "Spectral analysis of input2 failed");
        cdp_spectral_data_free(spec1);
        cdp_lib_buffer_free(mono1);
        cdp_lib_buffer_free(mono2);
        return NULL;
    }

    int num_bins = spec1->num_bins;
    int hop_size = fft_size / 4;

    /* Calculate offset in frames. Clamped before the cast: converting a
     * double outside int's range is undefined behaviour, and offset is a
     * caller-supplied number of seconds. */
    int offset_frames = (int)cdp_clamp_to_int(offset * sample_rate / hop_size);

    /* Determine output length (max of both inputs considering offset) */
    int output_frames = spec1->num_frames;
    if (spec2->num_frames + offset_frames > output_frames) {
        output_frames = spec2->num_frames + offset_frames;
    }

    /* Calculate interpolation window in frames */
    int interp_start_frame = (int)(interp_start * output_frames);
    int interp_end_frame = (int)(interp_end * output_frames);
    if (interp_end_frame <= interp_start_frame) {
        interp_end_frame = interp_start_frame + 1;
    }

    /* Allocate output spectral data */
    cdp_spectral_data* spec_out = (cdp_spectral_data*)calloc(1, sizeof(cdp_spectral_data));
    if (spec_out == NULL) {
        cdp_spectral_data_free(spec1);
        cdp_spectral_data_free(spec2);
        cdp_lib_buffer_free(mono1);
        cdp_lib_buffer_free(mono2);
        snprintf(ctx->error_msg, sizeof(ctx->error_msg), "Memory allocation failed");
        return NULL;
    }

    spec_out->frames = (cdp_spectral_frame*)calloc(output_frames, sizeof(cdp_spectral_frame));
    if (spec_out->frames == NULL) {
        free(spec_out);
        cdp_spectral_data_free(spec1);
        cdp_spectral_data_free(spec2);
        cdp_lib_buffer_free(mono1);
        cdp_lib_buffer_free(mono2);
        snprintf(ctx->error_msg, sizeof(ctx->error_msg), "Memory allocation failed");
        return NULL;
    }

    spec_out->num_frames = output_frames;
    spec_out->num_bins = num_bins;
    spec_out->fft_size = fft_size;
    spec_out->overlap = 4;
    spec_out->sample_rate = (float)sample_rate;
    spec_out->frame_time = (float)hop_size / sample_rate;

    /* Process each frame */
    for (int f = 0; f < output_frames; f++) {
        /* Allocate frame */
        spec_out->frames[f].data = (float*)calloc(num_bins * 2, sizeof(float));
        spec_out->frames[f].num_bins = num_bins;
        spec_out->frames[f].fft_size = fft_size;
        spec_out->frames[f].sample_rate = (float)sample_rate;

        if (spec_out->frames[f].data == NULL) {
            cdp_spectral_data_free(spec_out);
            cdp_spectral_data_free(spec1);
            cdp_spectral_data_free(spec2);
            cdp_lib_buffer_free(mono1);
            cdp_lib_buffer_free(mono2);
            snprintf(ctx->error_msg, sizeof(ctx->error_msg), "Memory allocation failed");
            return NULL;
        }

        /* Get source frames (with bounds checking) */
        int f1 = f;
        int f2 = f - offset_frames;

        int have_f1 = (f1 >= 0 && f1 < spec1->num_frames);
        int have_f2 = (f2 >= 0 && f2 < spec2->num_frames);

        float* amp1 = have_f1 ? spec1->frames[f1].data : NULL;
        float* freq1 = have_f1 ? spec1->frames[f1].data + num_bins : NULL;
        float* amp2 = have_f2 ? spec2->frames[f2].data : NULL;
        float* freq2 = have_f2 ? spec2->frames[f2].data + num_bins : NULL;

        float* out_amp = spec_out->frames[f].data;
        float* out_freq = spec_out->frames[f].data + num_bins;

        /* Calculate interpolation factor */
        double interp = 0.0;
        if (f < interp_start_frame) {
            interp = 0.0;
        } else if (f >= interp_end_frame) {
            interp = 1.0;
        } else {
            interp = (double)(f - interp_start_frame) / (interp_end_frame - interp_start_frame);
        }

        /* Apply mode-specific normalization (simplified) */
        /* For now, implement basic amplitude interpolation without full normalization */

        for (int b = 0; b < num_bins; b++) {
            float a1 = have_f1 ? amp1[b] : 0.0f;
            float a2 = have_f2 ? amp2[b] : 0.0f;
            float fr1 = have_f1 ? freq1[b] : (float)(b * spec1->sample_rate / 2.0 / (num_bins - 1));
            float fr2 = have_f2 ? freq2[b] : fr1;

            /* Amplitude interpolation */
            out_amp[b] = a1 + (float)interp * (a2 - a1);

            /* Frequency interpolation */
            out_freq[b] = fr1 + (float)interp * (fr2 - fr1);
        }
    }

    /* Synthesize output */
    size_t synth_len;
    float* synth_data = cdp_spectral_synthesize(spec_out, &synth_len);

    cdp_spectral_data_free(spec1);
    cdp_spectral_data_free(spec2);
    cdp_spectral_data_free(spec_out);
    cdp_lib_buffer_free(mono1);
    cdp_lib_buffer_free(mono2);

    if (synth_data == NULL) {
        snprintf(ctx->error_msg, sizeof(ctx->error_msg),
                 "Spectral synthesis failed");
        return NULL;
    }

    cdp_lib_buffer* output = cdp_lib_buffer_from_data(synth_data, synth_len, 1, sample_rate);
    if (output == NULL) {
        free(synth_data);
        snprintf(ctx->error_msg, sizeof(ctx->error_msg),
                 "Failed to create output buffer");
        return NULL;
    }

    cdp_lib_normalize_if_clipping(output, 0.95f);
    return output;
}

/*
 * Native implementation of specmorph algorithm.
 *
 * Full spectral morphing with independent amplitude and frequency timing.
 */
cdp_lib_buffer* cdp_morph_morph_native(cdp_lib_ctx* ctx,
                                        const cdp_lib_buffer* input1,
                                        const cdp_lib_buffer* input2,
                                        int mode,
                                        double amp_start,
                                        double amp_end,
                                        double freq_start,
                                        double freq_end,
                                        double amp_exp,
                                        double freq_exp,
                                        double stagger,
                                        int fft_size) {
    if (ctx == NULL || input1 == NULL || input2 == NULL) {
        return NULL;
    }

    /* Parameter validation */
    if (mode < 0 || mode > 1) mode = 0;  /* 0=linear, 1=cosine */
    if (amp_start < 0) amp_start = 0;
    if (amp_start > 1) amp_start = 1;
    if (amp_end < amp_start) amp_end = amp_start;
    if (amp_end > 1) amp_end = 1;
    if (freq_start < 0) freq_start = 0;
    if (freq_start > 1) freq_start = 1;
    if (freq_end < freq_start) freq_end = freq_start;
    if (freq_end > 1) freq_end = 1;
    if (amp_exp <= 0) amp_exp = 1.0;
    if (freq_exp <= 0) freq_exp = 1.0;
    if (fft_size <= 0) fft_size = 1024;

    /* Ensure power of 2. Clamped first: `n *= 2` is signed overflow once the
     * request passes 2^30, and cdp_spectral_analyze rejects anything outside
     * this range anyway. */
    if (fft_size < CDP_MIN_FFT_SIZE) fft_size = CDP_MIN_FFT_SIZE;
    if (fft_size > CDP_MAX_FFT_SIZE) fft_size = CDP_MAX_FFT_SIZE;
    int n = 1;
    while (n < fft_size) n *= 2;
    fft_size = n;

    /* Sample rates must match */
    if (input1->sample_rate != input2->sample_rate) {
        snprintf(ctx->error_msg, sizeof(ctx->error_msg),
                 "Sample rates must match for morph");
        return NULL;
    }

    int sample_rate = input1->sample_rate;

    /* Convert to mono if needed */
    cdp_lib_buffer* mono1 = cdp_lib_to_mono(ctx, input1);
    if (mono1 == NULL) return NULL;

    cdp_lib_buffer* mono2 = cdp_lib_to_mono(ctx, input2);
    if (mono2 == NULL) {
        cdp_lib_buffer_free(mono1);
        return NULL;
    }

    /* Analyze both inputs */
    cdp_spectral_data* spec1 = cdp_spectral_analyze(
        mono1->data, mono1->length, 1, sample_rate, fft_size, 4);
    if (spec1 == NULL) {
        snprintf(ctx->error_msg, sizeof(ctx->error_msg),
                 "Spectral analysis of input1 failed");
        cdp_lib_buffer_free(mono1);
        cdp_lib_buffer_free(mono2);
        return NULL;
    }

    cdp_spectral_data* spec2 = cdp_spectral_analyze(
        mono2->data, mono2->length, 1, sample_rate, fft_size, 4);
    if (spec2 == NULL) {
        snprintf(ctx->error_msg, sizeof(ctx->error_msg),
                 "Spectral analysis of input2 failed");
        cdp_spectral_data_free(spec1);
        cdp_lib_buffer_free(mono1);
        cdp_lib_buffer_free(mono2);
        return NULL;
    }

    int num_bins = spec1->num_bins;
    int hop_size = fft_size / 4;

    /* Calculate stagger in frames; clamped before the cast, as above. */
    int stagger_frames = (int)cdp_clamp_to_int(stagger * sample_rate / hop_size);

    /* Output frames = max considering stagger */
    int output_frames = spec1->num_frames;
    if (spec2->num_frames + stagger_frames > output_frames) {
        output_frames = spec2->num_frames + stagger_frames;
    }

    /* Convert timing parameters to frame indices */
    int amp_start_frame = (int)(amp_start * output_frames);
    int amp_end_frame = (int)(amp_end * output_frames);
    int freq_start_frame = (int)(freq_start * output_frames);
    int freq_end_frame = (int)(freq_end * output_frames);

    /* Build cosine lookup table if needed */
    double cos_table[257];  /* MPH_COSTABSIZE + 1 for wraparound */
    if (mode == 1) {
        for (int i = 0; i <= 256; i++) {
            double phase = (double)i / 256.0 * M_PI;
            cos_table[i] = (1.0 - cos(phase)) / 2.0;
        }
    }

    /* Allocate output spectral data */
    cdp_spectral_data* spec_out = (cdp_spectral_data*)calloc(1, sizeof(cdp_spectral_data));
    if (spec_out == NULL) {
        cdp_spectral_data_free(spec1);
        cdp_spectral_data_free(spec2);
        cdp_lib_buffer_free(mono1);
        cdp_lib_buffer_free(mono2);
        snprintf(ctx->error_msg, sizeof(ctx->error_msg), "Memory allocation failed");
        return NULL;
    }

    spec_out->frames = (cdp_spectral_frame*)calloc(output_frames, sizeof(cdp_spectral_frame));
    if (spec_out->frames == NULL) {
        free(spec_out);
        cdp_spectral_data_free(spec1);
        cdp_spectral_data_free(spec2);
        cdp_lib_buffer_free(mono1);
        cdp_lib_buffer_free(mono2);
        snprintf(ctx->error_msg, sizeof(ctx->error_msg), "Memory allocation failed");
        return NULL;
    }

    spec_out->num_frames = output_frames;
    spec_out->num_bins = num_bins;
    spec_out->fft_size = fft_size;
    spec_out->overlap = 4;
    spec_out->sample_rate = (float)sample_rate;
    spec_out->frame_time = (float)hop_size / sample_rate;

    /* Process each frame */
    for (int f = 0; f < output_frames; f++) {
        /* Allocate frame */
        spec_out->frames[f].data = (float*)calloc(num_bins * 2, sizeof(float));
        spec_out->frames[f].num_bins = num_bins;
        spec_out->frames[f].fft_size = fft_size;
        spec_out->frames[f].sample_rate = (float)sample_rate;

        if (spec_out->frames[f].data == NULL) {
            cdp_spectral_data_free(spec_out);
            cdp_spectral_data_free(spec1);
            cdp_spectral_data_free(spec2);
            cdp_lib_buffer_free(mono1);
            cdp_lib_buffer_free(mono2);
            snprintf(ctx->error_msg, sizeof(ctx->error_msg), "Memory allocation failed");
            return NULL;
        }

        /* Get source frames (with stagger and bounds checking) */
        int f1 = f < spec1->num_frames ? f : spec1->num_frames - 1;
        int f2 = f - stagger_frames;
        if (f2 < 0) f2 = 0;
        if (f2 >= spec2->num_frames) f2 = spec2->num_frames - 1;

        float* amp1 = spec1->frames[f1].data;
        float* freq1 = spec1->frames[f1].data + num_bins;
        float* amp2 = spec2->frames[f2].data;
        float* freq2 = spec2->frames[f2].data + num_bins;

        float* out_amp = spec_out->frames[f].data;
        float* out_freq = spec_out->frames[f].data + num_bins;

        /* Calculate amplitude interpolation */
        double amp_interp = 0.0;
        if (f > amp_start_frame && f < amp_end_frame) {
            double alen = (double)(amp_end_frame - amp_start_frame);
            amp_interp = (double)(f - amp_start_frame) / alen;

            if (mode == 0) {
                /* Linear with exponent */
                amp_interp = pow(amp_interp, amp_exp);
            } else {
                /* Cosine interpolation */
                int idx = (int)(amp_interp * 256);
                if (idx > 256) idx = 256;
                amp_interp = cos_table[idx];
            }
        } else if (f >= amp_end_frame) {
            amp_interp = 1.0;
        }

        /* Calculate frequency interpolation */
        double freq_interp = 0.0;
        if (f > freq_start_frame && f < freq_end_frame) {
            double flen = (double)(freq_end_frame - freq_start_frame);
            freq_interp = (double)(f - freq_start_frame) / flen;

            if (mode == 0) {
                freq_interp = pow(freq_interp, freq_exp);
            } else {
                int idx = (int)(freq_interp * 256);
                if (idx > 256) idx = 256;
                freq_interp = cos_table[idx];
            }
        } else if (f >= freq_end_frame) {
            freq_interp = 1.0;
        }

        /* Apply to each bin */
        for (int b = 0; b < num_bins; b++) {
            /* Amplitude morph */
            float a_diff = amp2[b] - amp1[b];
            out_amp[b] = amp1[b] + (float)(amp_interp * a_diff);

            /* Frequency morph */
            float f_diff = freq2[b] - freq1[b];
            out_freq[b] = freq1[b] + (float)(freq_interp * f_diff);
        }
    }

    /* Synthesize output */
    size_t synth_len;
    float* synth_data = cdp_spectral_synthesize(spec_out, &synth_len);

    cdp_spectral_data_free(spec1);
    cdp_spectral_data_free(spec2);
    cdp_spectral_data_free(spec_out);
    cdp_lib_buffer_free(mono1);
    cdp_lib_buffer_free(mono2);

    if (synth_data == NULL) {
        snprintf(ctx->error_msg, sizeof(ctx->error_msg),
                 "Spectral synthesis failed");
        return NULL;
    }

    cdp_lib_buffer* output = cdp_lib_buffer_from_data(synth_data, synth_len, 1, sample_rate);
    if (output == NULL) {
        free(synth_data);
        snprintf(ctx->error_msg, sizeof(ctx->error_msg),
                 "Failed to create output buffer");
        return NULL;
    }

    cdp_lib_normalize_if_clipping(output, 0.95f);
    return output;
}
