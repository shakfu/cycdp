/*
 * CDP Spectral Processing - Implementation
 *
 * Implements phase vocoder analysis/synthesis and spectral transformations
 * using CDP's FFT routines but with direct buffer I/O.
 */

#include "cdp_spectral.h"
#include <stdlib.h>
#include <string.h>
#include <math.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

/* External FFT function from CDP's mxfft.c */
/* Signature: fft_(a, b, nseg, n, nspn, isn) */
extern int fft_(float *a, float *b, int nseg, int n, int nspn, int isn);

/*
 * Fill a Hann window.
 *
 * Held as an array rather than applied in place, because both the analysis and
 * the synthesis fold their frame through a rotation, so the windowing and the
 * copy cannot be one pass over the same memory.
 */
static void fill_window(float *window, int size) {
    for (int i = 0; i < size; i++) {
        window[i] = 0.5f * (1.0f - cosf(2.0f * (float)M_PI * i / (size - 1)));
    }
}

/*
 * Convert complex FFT output to amplitude/frequency pairs
 */
static void cartesian_to_polar(float *real, float *imag, float *amp, float *freq,
                                int num_bins, float sample_rate, int fft_size,
                                int hop_size, float *last_phase) {
    float freq_per_bin = sample_rate / fft_size;
    /* Expected phase increment per bin per hop */
    float expect = 2.0f * (float)M_PI * hop_size / fft_size;

    for (int i = 0; i < num_bins; i++) {
        float r = real[i];
        float im = imag[i];

        amp[i] = sqrtf(r * r + im * im);

        float phase = atan2f(im, r);
        float phase_diff = phase - last_phase[i];
        last_phase[i] = phase;

        /* Wrap the raw difference. Correct here only because the caller folds
         * the frame into the transform buffer with a rotation of (n mod N),
         * which references the phase to absolute time: a partial sitting
         * exactly on a bin centre then shows no advance at all between hops,
         * so the raw difference already *is* the deviation. Without that
         * rotation bin i would advance by i*expect per hop and this would have
         * to subtract it first. See cdp_spectral_analyze. */
        while (phase_diff > M_PI) phase_diff -= 2.0f * (float)M_PI;
        while (phase_diff < -M_PI) phase_diff += 2.0f * (float)M_PI;

        /* Deviation in bins, plus the bin's own centre frequency.
         *
         * Negated because mxfft's forward transform uses the opposite sign
         * convention from the one this formula assumes, so a partial above its
         * bin centre measures a falling phase. The negation was invisible for
         * as long as analysis and synthesis were only ever each other's
         * inverse -- it cancelled -- but it made freq[] the reflection of the
         * true frequency about the bin centre, and any transform that reads or
         * writes freq[] as a frequency inherited the error. With it corrected,
         * get_partials reports an isolated tone exactly rather than to within
         * a bin. */
        float freq_dev = -phase_diff / expect;
        freq[i] = (i + freq_dev) * freq_per_bin;
    }
}

/*
 * Convert amplitude/frequency pairs back to complex
 */
static void polar_to_cartesian(float *amp, float *freq, float *real, float *imag,
                                int num_bins, float sample_rate, int fft_size,
                                int hop_size, float *synth_phase) {
    float freq_per_bin = sample_rate / fft_size;
    float expect = 2.0f * (float)M_PI * hop_size / fft_size;

    for (int i = 0; i < num_bins; i++) {
        /* Accumulate only the deviation from the bin's centre frequency. The
         * centre itself is carried by the bin's position in the transform,
         * which the output rotation in cdp_spectral_synthesize references to
         * absolute time -- the mirror of what the analysis does.
         *
         * Accumulating the absolute frequency instead makes the bin index and
         * the phase accumulator both encode position, so the two double-count.
         * That is self-consistent for an unmodified spectrum, which is why it
         * round-tripped, but it means moving amplitude between bins perturbs
         * a relationship the synthesis depends on: measured before this
         * change, a translation was coherent only in multiples of
         * fft_size / (2 * hop) bins and cancelled everywhere in between. */
        float freq_dev = freq[i] / freq_per_bin - i;

        /* Subtracted, matching the sign convention corrected in
         * cartesian_to_polar. The pair has to agree or the round trip stops
         * reconstructing its input. */
        synth_phase[i] -= freq_dev * expect;

        real[i] = amp[i] * cosf(synth_phase[i]);
        imag[i] = amp[i] * sinf(synth_phase[i]);
    }
}

cdp_spectral_data* cdp_spectral_analyze(const float *audio, size_t num_samples,
                                         int channels, int sample_rate,
                                         int fft_size, int overlap) {
    if (audio == NULL || num_samples == 0) return NULL;
    if (fft_size < 64 || fft_size > 8192) return NULL;
    if ((fft_size & (fft_size - 1)) != 0) return NULL;  /* Must be power of 2 */
    if (overlap < 1 || overlap > 4) overlap = 3;

    int hop_size = fft_size / (1 << overlap);  /* hop = fft_size / 2^overlap */
    int num_bins = fft_size / 2 + 1;

    /* Convert to mono if needed */
    float *mono = NULL;
    size_t mono_samples = num_samples / channels;

    if (channels > 1) {
        mono = (float *)malloc(mono_samples * sizeof(float));
        if (mono == NULL) return NULL;
        for (size_t i = 0; i < mono_samples; i++) {
            float sum = 0;
            for (int c = 0; c < channels; c++) {
                sum += audio[i * channels + c];
            }
            mono[i] = sum / channels;
        }
    } else {
        mono = (float *)malloc(mono_samples * sizeof(float));
        if (mono == NULL) return NULL;
        memcpy(mono, audio, mono_samples * sizeof(float));
    }

    /* Calculate number of frames */
    int num_frames = (int)((mono_samples - fft_size) / hop_size) + 1;
    if (num_frames < 1) {
        free(mono);
        return NULL;
    }

    /* Allocate spectral data */
    cdp_spectral_data *result = (cdp_spectral_data *)calloc(1, sizeof(cdp_spectral_data));
    if (result == NULL) {
        free(mono);
        return NULL;
    }

    result->frames = (cdp_spectral_frame *)calloc(num_frames, sizeof(cdp_spectral_frame));
    if (result->frames == NULL) {
        free(mono);
        free(result);
        return NULL;
    }

    result->num_frames = num_frames;
    result->num_bins = num_bins;
    result->fft_size = fft_size;
    result->overlap = overlap;
    result->sample_rate = (float)sample_rate;
    result->frame_time = (float)hop_size / sample_rate;

    /* Allocate working buffers */
    float *real = (float *)malloc(fft_size * sizeof(float));
    float *imag = (float *)malloc(fft_size * sizeof(float));
    float *last_phase = (float *)calloc(num_bins, sizeof(float));
    float *window = (float *)malloc(fft_size * sizeof(float));

    if (real == NULL || imag == NULL || last_phase == NULL || window == NULL) {
        free(real);
        free(imag);
        free(last_phase);
        free(window);
        cdp_spectral_data_free(result);
        free(mono);
        return NULL;
    }

    fill_window(window, fft_size);

    /* Analyze each frame */
    for (int frame = 0; frame < num_frames; frame++) {
        int offset = frame * hop_size;

        /* Fold the windowed frame into the transform buffer rotated by
         * (offset mod fft_size), following CDP's pvoc (dev/pv/pvoc.c, the
         * `k = nI - analWinLen - 1` index in the analysis loop).
         *
         * This is what makes the bin index mean something. With the rotation
         * the transform's phase is referenced to absolute time rather than to
         * the start of the frame, so a partial sitting exactly on a bin centre
         * shows no phase advance from hop to hop, and the raw phase difference
         * is the deviation. Without it every bin advances by i*expect per hop,
         * which the analysis then has to subtract back out -- self-consistent,
         * but it leaves the bin index carrying no information the synthesis
         * can use, so relocating amplitude between bins does not relocate the
         * sound. That was the root cause behind spectral_shift. */
        memset(real, 0, fft_size * sizeof(float));
        memset(imag, 0, fft_size * sizeof(float));

        int copy_size = fft_size;
        if (offset + copy_size > (int)mono_samples) {
            copy_size = (int)mono_samples - offset;
        }

        int rot = offset % fft_size;
        for (int i = 0; i < copy_size; i++) {
            int k = rot + i;
            if (k >= fft_size) k -= fft_size;
            real[k] = mono[offset + i] * window[i];
        }

        /* Forward FFT */
        fft_(real, imag, 1, fft_size, 1, 1);

        /* Allocate frame data */
        result->frames[frame].data = (float *)malloc(num_bins * 2 * sizeof(float));
        result->frames[frame].num_bins = num_bins;
        result->frames[frame].fft_size = fft_size;
        result->frames[frame].sample_rate = (float)sample_rate;

        if (result->frames[frame].data == NULL) {
            free(real);
            free(imag);
            free(last_phase);
            free(window);
            cdp_spectral_data_free(result);
            free(mono);
            return NULL;
        }

        /* Convert to amplitude/frequency */
        float *amp = result->frames[frame].data;
        float *freq = result->frames[frame].data + num_bins;

        cartesian_to_polar(real, imag, amp, freq, num_bins,
                           (float)sample_rate, fft_size, hop_size, last_phase);
    }

    free(real);
    free(imag);
    free(last_phase);
    free(window);
    free(mono);

    return result;
}

float* cdp_spectral_synthesize(const cdp_spectral_data *spectral,
                                size_t *out_samples) {
    if (spectral == NULL || spectral->frames == NULL) return NULL;

    int fft_size = spectral->fft_size;
    int hop_size = fft_size / (1 << spectral->overlap);
    int num_bins = spectral->num_bins;
    int num_frames = spectral->num_frames;

    /* Output length */
    size_t output_len = (size_t)((num_frames - 1) * hop_size + fft_size);
    float *output = (float *)calloc(output_len, sizeof(float));
    if (output == NULL) return NULL;

    /* Allocate working buffers */
    float *real = (float *)malloc(fft_size * sizeof(float));
    float *imag = (float *)malloc(fft_size * sizeof(float));
    float *synth_phase = (float *)calloc(num_bins, sizeof(float));
    float *frame_out = (float *)malloc(fft_size * sizeof(float));
    float *window = (float *)malloc(fft_size * sizeof(float));

    if (real == NULL || imag == NULL || synth_phase == NULL ||
        frame_out == NULL || window == NULL) {
        free(real);
        free(imag);
        free(synth_phase);
        free(frame_out);
        free(window);
        free(output);
        return NULL;
    }

    fill_window(window, fft_size);

    /* Synthesize each frame */
    for (int frame = 0; frame < num_frames; frame++) {
        float *amp = spectral->frames[frame].data;
        float *freq = spectral->frames[frame].data + num_bins;

        /* Convert to complex */
        memset(real, 0, fft_size * sizeof(float));
        memset(imag, 0, fft_size * sizeof(float));

        polar_to_cartesian(amp, freq, real, imag, num_bins,
                           spectral->sample_rate, fft_size, hop_size, synth_phase);

        /* Mirror for negative frequencies */
        for (int i = 1; i < num_bins - 1; i++) {
            real[fft_size - i] = real[i];
            imag[fft_size - i] = -imag[i];
        }

        /* Inverse FFT */
        fft_(real, imag, 1, fft_size, 1, -1);

        /* Window and overlap-add, unrotating by (offset mod fft_size) to
         * undo what the analysis folded in. CDP does the same on its way out
         * (dev/pv/pvoc.c, the `k = nO - synWinLen - 1` index in the
         * overlap-add loop). The two rotations have to match or the frames
         * land on top of each other at the wrong alignment. */
        int offset = frame * hop_size;
        int rot = offset % fft_size;

        for (int i = 0; i < fft_size && offset + i < (int)output_len; i++) {
            int k = rot + i;
            if (k >= fft_size) k -= fft_size;
            output[offset + i] += real[k] * window[i];
        }
    }

    /*
     * Normalize for the window pair and the overlap.
     *
     * A Hann window is applied on analysis and again here on synthesis, so
     * each output sample accumulates sum_k w^2[n - kH], which under the COLA
     * condition equals sum(w^2)/H. Dividing by that restores unity gain.
     *
     * The previous 1/2^overlap accounted only for the hop and ignored the
     * window energy entirely, leaving a constant sum(w^2)/N = 3/8 (-8.5 dB)
     * attenuation on every operation that goes through this path -- filters,
     * parametric EQ, time stretching, morphing. A 0 dB EQ was not a no-op.
     *
     * sum(w^2) is computed rather than hardcoded to 3N/8 because
     * apply_window() divides by (size - 1), which is not exactly the periodic
     * Hann the closed form assumes.
     */
    double w2_sum = 0.0;
    for (int i = 0; i < fft_size; i++) {
        double w = 0.5 * (1.0 - cos(2.0 * M_PI * i / (fft_size - 1)));
        w2_sum += w * w;
    }
    float norm = (w2_sum > 0.0) ? (float)(hop_size / w2_sum)
                                : 1.0f / (1 << spectral->overlap);
    for (size_t i = 0; i < output_len; i++) {
        output[i] *= norm;
    }

    free(real);
    free(imag);
    free(synth_phase);
    free(frame_out);
    free(window);

    *out_samples = output_len;
    return output;
}

cdp_spectral_data* cdp_spectral_time_stretch(const cdp_spectral_data *input,
                                              double factor) {
    if (input == NULL || factor <= 0) return NULL;
    if (input->num_frames < 1 || input->frames == NULL) return NULL;

    int out_frames = (int)(input->num_frames * factor);
    if (out_frames < 1) out_frames = 1;

    /* Allocate output */
    cdp_spectral_data *output = (cdp_spectral_data *)calloc(1, sizeof(cdp_spectral_data));
    if (output == NULL) return NULL;

    output->frames = (cdp_spectral_frame *)calloc(out_frames, sizeof(cdp_spectral_frame));
    if (output->frames == NULL) {
        free(output);
        return NULL;
    }

    output->num_frames = out_frames;
    output->num_bins = input->num_bins;
    output->fft_size = input->fft_size;
    output->overlap = input->overlap;
    output->sample_rate = input->sample_rate;
    output->frame_time = input->frame_time;

    int num_bins = input->num_bins;

    /* Interpolate frames */
    for (int out_frame = 0; out_frame < out_frames; out_frame++) {
        /* Position in input */
        double in_pos = out_frame / factor;
        int in_frame = (int)in_pos;
        double frac = in_pos - in_frame;

        if (in_frame >= input->num_frames - 1) {
            in_frame = input->num_frames - 2;
            frac = 1.0;
        }
        if (in_frame < 0) {
            in_frame = 0;
            frac = 0.0;
        }

        /* The clamps above assume at least two input frames. With exactly one
           (an input barely longer than the FFT window) they leave in_frame at
           0 and the interpolation below then reads frames[1], one past the
           end. Clamp the second index independently and interpolate against
           the same frame. */
        int in_frame_next = in_frame + 1;
        if (in_frame_next >= input->num_frames) {
            in_frame_next = input->num_frames - 1;
        }

        /* Allocate output frame */
        output->frames[out_frame].data = (float *)malloc(num_bins * 2 * sizeof(float));
        output->frames[out_frame].num_bins = num_bins;
        output->frames[out_frame].fft_size = input->fft_size;
        output->frames[out_frame].sample_rate = input->sample_rate;

        if (output->frames[out_frame].data == NULL) {
            cdp_spectral_data_free(output);
            return NULL;
        }

        /* Interpolate between frames */
        float *in_amp0 = input->frames[in_frame].data;
        float *in_freq0 = input->frames[in_frame].data + num_bins;
        float *in_amp1 = input->frames[in_frame_next].data;
        float *in_freq1 = input->frames[in_frame_next].data + num_bins;

        float *out_amp = output->frames[out_frame].data;
        float *out_freq = output->frames[out_frame].data + num_bins;

        for (int bin = 0; bin < num_bins; bin++) {
            out_amp[bin] = (float)(in_amp0[bin] + (in_amp1[bin] - in_amp0[bin]) * frac);
            out_freq[bin] = (float)(in_freq0[bin] + (in_freq1[bin] - in_freq0[bin]) * frac);
        }
    }

    return output;
}

cdp_spectral_data* cdp_spectral_blur(const cdp_spectral_data *input,
                                      int num_windows) {
    if (input == NULL || num_windows < 1) return NULL;

    /* Allocate output (same size as input) */
    cdp_spectral_data *output = (cdp_spectral_data *)calloc(1, sizeof(cdp_spectral_data));
    if (output == NULL) return NULL;

    output->frames = (cdp_spectral_frame *)calloc(input->num_frames, sizeof(cdp_spectral_frame));
    if (output->frames == NULL) {
        free(output);
        return NULL;
    }

    output->num_frames = input->num_frames;
    output->num_bins = input->num_bins;
    output->fft_size = input->fft_size;
    output->overlap = input->overlap;
    output->sample_rate = input->sample_rate;
    output->frame_time = input->frame_time;

    int num_bins = input->num_bins;
    int half_win = num_windows / 2;

    /* Average frames */
    for (int out_frame = 0; out_frame < input->num_frames; out_frame++) {
        output->frames[out_frame].data = (float *)calloc(num_bins * 2, sizeof(float));
        output->frames[out_frame].num_bins = num_bins;
        output->frames[out_frame].fft_size = input->fft_size;
        output->frames[out_frame].sample_rate = input->sample_rate;

        if (output->frames[out_frame].data == NULL) {
            cdp_spectral_data_free(output);
            return NULL;
        }

        float *out_amp = output->frames[out_frame].data;
        float *out_freq = output->frames[out_frame].data + num_bins;

        int count = 0;
        int start = out_frame - half_win;
        int end = out_frame + half_win;

        if (start < 0) start = 0;
        if (end >= input->num_frames) end = input->num_frames - 1;

        for (int in_frame = start; in_frame <= end; in_frame++) {
            float *in_amp = input->frames[in_frame].data;
            float *in_freq = input->frames[in_frame].data + num_bins;

            for (int bin = 0; bin < num_bins; bin++) {
                out_amp[bin] += in_amp[bin];
                out_freq[bin] += in_freq[bin];
            }
            count++;
        }

        if (count > 0) {
            float inv_count = 1.0f / count;
            for (int bin = 0; bin < num_bins; bin++) {
                out_amp[bin] *= inv_count;
                out_freq[bin] *= inv_count;
            }
        }
    }

    return output;
}

void cdp_spectral_data_free(cdp_spectral_data *data) {
    if (data == NULL) return;

    if (data->frames != NULL) {
        for (int i = 0; i < data->num_frames; i++) {
            if (data->frames[i].data != NULL) {
                free(data->frames[i].data);
            }
        }
        free(data->frames);
    }

    free(data);
}

/*
 * Helper to allocate and copy spectral data structure
 */
static cdp_spectral_data* cdp_spectral_data_copy(const cdp_spectral_data *input) {
    if (input == NULL) return NULL;

    cdp_spectral_data *output = (cdp_spectral_data *)calloc(1, sizeof(cdp_spectral_data));
    if (output == NULL) return NULL;

    output->frames = (cdp_spectral_frame *)calloc(input->num_frames, sizeof(cdp_spectral_frame));
    if (output->frames == NULL) {
        free(output);
        return NULL;
    }

    output->num_frames = input->num_frames;
    output->num_bins = input->num_bins;
    output->fft_size = input->fft_size;
    output->overlap = input->overlap;
    output->sample_rate = input->sample_rate;
    output->frame_time = input->frame_time;

    int num_bins = input->num_bins;

    for (int frame = 0; frame < input->num_frames; frame++) {
        output->frames[frame].data = (float *)malloc(num_bins * 2 * sizeof(float));
        output->frames[frame].num_bins = num_bins;
        output->frames[frame].fft_size = input->fft_size;
        output->frames[frame].sample_rate = input->sample_rate;

        if (output->frames[frame].data == NULL) {
            cdp_spectral_data_free(output);
            return NULL;
        }

        memcpy(output->frames[frame].data, input->frames[frame].data,
               num_bins * 2 * sizeof(float));
    }

    return output;
}

/*
 * Move each bin's (amplitude, frequency) pair to the destination bin the
 * caller nominates, writing into a separate output frame.
 *
 * Both frequency transforms below used to rewrite freq[] in place and leave
 * the amplitude where it was. That does not move anything. The synthesis
 * reconstructs bin i at bin i's centre frequency and uses the inter-frame
 * phase advance only for the sub-bin remainder, so a frequency more than about
 * one bin away from the amplitude carrying it decoheres across the overlap-add
 * rather than relocating. Measured on a 440 Hz tone: a 2.3-bin shift kept 9%
 * of the energy and what survived peaked on the wrong side of the input, and a
 * 2x stretch of a 2 kHz tone left the peak at 1930 Hz having destroyed 87% of
 * the signal.
 *
 * The destination is nominated per *bin index*, not derived here from the new
 * frequency. Deriving it from the frequency looks more natural and is wrong: a
 * sinusoid's main lobe spans several bins that the analysis all assigns
 * essentially the same frequency, so every one of them maps to a single
 * destination and the lobe collapses to a point. Translating by index carries
 * the lobe across intact. Measured: the frequency-derived version broke even
 * the identity case, taking a 440 Hz tone from 0.354 RMS to 0.013.
 *
 * Where several source bins do land in the same destination the loudest wins.
 * Summing would be wrong -- the amplitudes represent different frequencies and
 * are not in phase, so adding them overstates the result.
 *
 * Destinations nothing maps to are silent, with their frequency set to the bin
 * centre so the synthesis has a defined value rather than a stale one.
 */
static void spectral_relocate(const float *in_amp, const int *dest,
                              const float *new_freq,
                              float *out_amp, float *out_freq,
                              int num_bins, float freq_per_bin) {
    for (int bin = 0; bin < num_bins; bin++) {
        out_amp[bin] = 0.0f;
        out_freq[bin] = bin * freq_per_bin;
    }

    for (int bin = 0; bin < num_bins; bin++) {
        int target = dest[bin];
        if (target < 0 || target >= num_bins) continue;

        /* Rejects NaN as well as out-of-band frequencies. */
        if (!(new_freq[bin] > 0.0f)) continue;

        if (in_amp[bin] > out_amp[target]) {
            out_amp[target] = in_amp[bin];
            out_freq[target] = new_freq[bin];
        }
    }
}

cdp_spectral_data* cdp_spectral_freq_shift(const cdp_spectral_data *input,
                                            double shift_hz) {
    if (input == NULL) return NULL;

    cdp_spectral_data *output = cdp_spectral_data_copy(input);
    if (output == NULL) return NULL;

    int num_bins = input->num_bins;
    float freq_per_bin = input->sample_rate / input->fft_size;

    /* A shift is a rigid translation of the spectrum, so every bin moves by
     * the same whole number of bins and the remainder -- always under half a
     * bin -- is left to the frequency field, which the phase advance handles
     * accurately. */
    int bin_shift = (int)floorf((float)shift_hz / freq_per_bin + 0.5f);

    float *new_freq = (float *)malloc(num_bins * sizeof(float));
    int *dest = (int *)malloc(num_bins * sizeof(int));
    if (new_freq == NULL || dest == NULL) {
        free(new_freq);
        free(dest);
        cdp_spectral_data_free(output);
        return NULL;
    }

    for (int frame = 0; frame < output->num_frames; frame++) {
        const float *in_amp = input->frames[frame].data;
        const float *in_freq = input->frames[frame].data + num_bins;
        float *out_amp = output->frames[frame].data;
        float *out_freq = output->frames[frame].data + num_bins;

        for (int bin = 0; bin < num_bins; bin++) {
            dest[bin] = bin + bin_shift;
            new_freq[bin] = in_freq[bin] + (float)shift_hz;
        }

        spectral_relocate(in_amp, dest, new_freq, out_amp, out_freq,
                          num_bins, freq_per_bin);
    }

    free(new_freq);
    free(dest);
    return output;
}

cdp_spectral_data* cdp_spectral_freq_stretch(const cdp_spectral_data *input,
                                              double max_stretch,
                                              double freq_divide,
                                              double exponent) {
    if (input == NULL || max_stretch <= 0 || exponent <= 0) return NULL;

    cdp_spectral_data *output = cdp_spectral_data_copy(input);
    if (output == NULL) return NULL;

    int num_bins = input->num_bins;
    float nyquist = input->sample_rate / 2.0f;
    float freq_per_bin = input->sample_rate / input->fft_size;

    /* Calculate stretch range */
    double stretch_range = max_stretch - 1.0;

    float *new_freq = (float *)malloc(num_bins * sizeof(float));
    int *dest = (int *)malloc(num_bins * sizeof(int));
    if (new_freq == NULL || dest == NULL) {
        free(new_freq);
        free(dest);
        cdp_spectral_data_free(output);
        return NULL;
    }

    /* The stretch factor varies with frequency, so unlike a shift the
     * displacement has to be computed per bin. It is derived from the bin's
     * own centre frequency rather than from the analysed frequency of whatever
     * is in it: neighbouring bins of one partial share an analysed frequency,
     * so using that would send the whole main lobe to a single destination.
     * Centre frequencies are distinct by construction, so the lobe is carried
     * across as a unit. */
    for (int bin = 0; bin < num_bins; bin++) {
        float centre = bin * freq_per_bin;
        double factor = 1.0;

        if (centre > freq_divide) {
            double pos = (centre - freq_divide) / (nyquist - freq_divide);
            if (pos < 0) pos = 0;
            if (pos > 1) pos = 1;
            factor = 1.0 + stretch_range * pow(pos, exponent);
        }

        dest[bin] = (int)floor(bin * factor + 0.5);
    }

    for (int frame = 0; frame < output->num_frames; frame++) {
        const float *in_amp = input->frames[frame].data;
        const float *in_freq = input->frames[frame].data + num_bins;
        float *out_amp = output->frames[frame].data;
        float *out_freq = output->frames[frame].data + num_bins;

        for (int bin = 0; bin < num_bins; bin++) {
            float f = in_freq[bin];
            new_freq[bin] = f;

            if (f > freq_divide) {
                /* Calculate position in stretch range (0 to 1) */
                double pos = (f - freq_divide) / (nyquist - freq_divide);
                if (pos < 0) pos = 0;
                if (pos > 1) pos = 1;

                /* Apply exponent curve */
                double stretch_factor = 1.0 + stretch_range * pow(pos, exponent);

                /* Apply stretch */
                new_freq[bin] = (float)(f * stretch_factor);
            }
        }

        /* See spectral_relocate: the amplitude has to travel with the
         * frequency, or the partial stays where it was and cancels. */
        spectral_relocate(in_amp, dest, new_freq, out_amp, out_freq,
                          num_bins, freq_per_bin);
    }

    free(new_freq);
    free(dest);
    return output;
}

cdp_spectral_data* cdp_spectral_filter_lowpass(const cdp_spectral_data *input,
                                                double cutoff_freq,
                                                double attenuation_db) {
    if (input == NULL || cutoff_freq <= 0) return NULL;

    cdp_spectral_data *output = cdp_spectral_data_copy(input);
    if (output == NULL) return NULL;

    /* Convert attenuation to linear scale */
    float attenuation = (float)pow(10.0, attenuation_db / 20.0);
    if (attenuation > 1.0f) attenuation = 1.0f;

    int num_bins = input->num_bins;
    float freq_per_bin = input->sample_rate / input->fft_size;

    for (int frame = 0; frame < output->num_frames; frame++) {
        float *amp = output->frames[frame].data;

        for (int bin = 0; bin < num_bins; bin++) {
            float bin_freq = bin * freq_per_bin;
            if (bin_freq > cutoff_freq) {
                amp[bin] *= attenuation;
            }
        }
    }

    return output;
}

cdp_spectral_data* cdp_spectral_filter_highpass(const cdp_spectral_data *input,
                                                 double cutoff_freq,
                                                 double attenuation_db) {
    if (input == NULL || cutoff_freq <= 0) return NULL;

    cdp_spectral_data *output = cdp_spectral_data_copy(input);
    if (output == NULL) return NULL;

    /* Convert attenuation to linear scale */
    float attenuation = (float)pow(10.0, attenuation_db / 20.0);
    if (attenuation > 1.0f) attenuation = 1.0f;

    int num_bins = input->num_bins;
    float freq_per_bin = input->sample_rate / input->fft_size;

    for (int frame = 0; frame < output->num_frames; frame++) {
        float *amp = output->frames[frame].data;

        for (int bin = 0; bin < num_bins; bin++) {
            float bin_freq = bin * freq_per_bin;
            if (bin_freq < cutoff_freq) {
                amp[bin] *= attenuation;
            }
        }
    }

    return output;
}

cdp_spectral_data* cdp_spectral_focus(const cdp_spectral_data *input,
                                       double center_freq,
                                       double bandwidth,
                                       double gain_db) {
    if (input == NULL || center_freq <= 0 || bandwidth <= 0) return NULL;

    cdp_spectral_data *output = cdp_spectral_data_copy(input);
    if (output == NULL) return NULL;

    /* Convert gain to linear scale */
    float gain = (float)pow(10.0, gain_db / 20.0);
    float half_bw = (float)(bandwidth / 2.0);
    int num_bins = input->num_bins;
    float freq_per_bin = input->sample_rate / input->fft_size;

    for (int frame = 0; frame < output->num_frames; frame++) {
        float *amp = output->frames[frame].data;

        for (int bin = 0; bin < num_bins; bin++) {
            float bin_freq = bin * freq_per_bin;
            float dist = fabsf(bin_freq - (float)center_freq);

            /* Super-Gaussian curve with exponent 4 for sharp focus */
            float norm_dist = dist / half_bw;
            float curve = expf(-0.5f * norm_dist * norm_dist * norm_dist * norm_dist);

            /* Interpolate between 1.0 and gain based on curve */
            float applied_gain = 1.0f + (gain - 1.0f) * curve;
            amp[bin] *= applied_gain;
        }
    }

    return output;
}

cdp_spectral_data* cdp_spectral_hilite(const cdp_spectral_data *input,
                                        double threshold_db,
                                        double boost_db) {
    if (input == NULL) return NULL;

    cdp_spectral_data *output = cdp_spectral_data_copy(input);
    if (output == NULL) return NULL;

    float boost = (float)pow(10.0, boost_db / 20.0);
    float threshold_ratio = (float)pow(10.0, threshold_db / 20.0);
    int num_bins = input->num_bins;

    for (int frame = 0; frame < output->num_frames; frame++) {
        float *amp = output->frames[frame].data;

        /* Find peak amplitude in this frame */
        float peak = 0.0f;
        for (int bin = 0; bin < num_bins; bin++) {
            if (amp[bin] > peak) peak = amp[bin];
        }

        float abs_threshold = peak * threshold_ratio;

        /* Detect local maxima and boost them */
        for (int bin = 1; bin < num_bins - 1; bin++) {
            /* Is this a local maximum? */
            if (amp[bin] > amp[bin - 1] && amp[bin] > amp[bin + 1]) {
                /* Is it above threshold? */
                if (amp[bin] > abs_threshold) {
                    /* Boost the peak */
                    amp[bin] *= boost;
                    /* Apply partial boost to neighbors for smoothing */
                    float neighbor_boost = 1.0f + (boost - 1.0f) * 0.5f;
                    amp[bin - 1] *= neighbor_boost;
                    amp[bin + 1] *= neighbor_boost;
                }
            }
        }
    }

    return output;
}

cdp_spectral_data* cdp_spectral_fold(const cdp_spectral_data *input,
                                      double fold_freq) {
    if (input == NULL || fold_freq <= 0) return NULL;

    cdp_spectral_data *output = cdp_spectral_data_copy(input);
    if (output == NULL) return NULL;

    int num_bins = input->num_bins;
    float freq_per_bin = input->sample_rate / input->fft_size;
    int fold_bin = (int)(fold_freq / freq_per_bin);

    /* Clamp fold_bin to valid range */
    if (fold_bin < 1) fold_bin = 1;
    if (fold_bin >= num_bins) fold_bin = num_bins - 1;

    for (int frame = 0; frame < output->num_frames; frame++) {
        float *amp = output->frames[frame].data;
        float *freq = output->frames[frame].data + num_bins;

        /* Temporary buffer for accumulating folded energy */
        float *folded_amp = (float *)calloc(num_bins, sizeof(float));
        if (folded_amp == NULL) {
            cdp_spectral_data_free(output);
            return NULL;
        }

        /* Copy bins below fold point */
        for (int bin = 0; bin < fold_bin && bin < num_bins; bin++) {
            folded_amp[bin] = amp[bin];
        }

        /* Fold bins above fold point */
        for (int bin = fold_bin; bin < num_bins; bin++) {
            int mirror = fold_bin - (bin - fold_bin);

            /* Handle multiple reflections */
            while (mirror < 0 || mirror >= fold_bin) {
                if (mirror < 0) {
                    mirror = -mirror;
                }
                if (mirror >= fold_bin) {
                    mirror = 2 * fold_bin - mirror - 1;
                }
            }

            if (mirror >= 0 && mirror < num_bins) {
                folded_amp[mirror] += amp[bin];
            }
        }

        /* Copy back and update frequencies */
        for (int bin = 0; bin < num_bins; bin++) {
            amp[bin] = folded_amp[bin];
            freq[bin] = bin * freq_per_bin;
        }

        free(folded_amp);
    }

    return output;
}

cdp_spectral_data* cdp_spectral_clean(const cdp_spectral_data *input,
                                       double threshold_db) {
    if (input == NULL) return NULL;

    cdp_spectral_data *output = cdp_spectral_data_copy(input);
    if (output == NULL) return NULL;

    float threshold_ratio = (float)pow(10.0, threshold_db / 20.0);
    int num_bins = input->num_bins;

    for (int frame = 0; frame < output->num_frames; frame++) {
        float *amp = output->frames[frame].data;

        /* Find peak amplitude in this frame */
        float peak = 0.0f;
        for (int bin = 0; bin < num_bins; bin++) {
            if (amp[bin] > peak) peak = amp[bin];
        }

        float abs_threshold = peak * threshold_ratio;

        /* Zero bins below threshold */
        for (int bin = 0; bin < num_bins; bin++) {
            if (amp[bin] < abs_threshold) {
                amp[bin] = 0.0f;
            }
        }
    }

    return output;
}
