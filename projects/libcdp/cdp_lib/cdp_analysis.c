/*
 * CDP Analysis Functions - Implementation
 *
 * Implements pitch detection (YIN), formant analysis (LPC), and partial tracking.
 */

#include "cdp_analysis.h"
#include "cdp_lib_internal.h"

/* Helper: Apply Hamming window */
static void analysis_apply_hamming(float *frame, int size) {
    for (int i = 0; i < size; i++) {
        double w = 0.54 - 0.46 * cos(2.0 * M_PI * i / (size - 1));
        frame[i] *= (float)w;
    }
}

/* Helper: Compute autocorrelation */
static void analysis_autocorrelation(const float *frame, int size, float *r, int max_lag) {
    for (int lag = 0; lag < max_lag; lag++) {
        double sum = 0;
        for (int i = 0; i < size - lag; i++) {
            sum += frame[i] * frame[i + lag];
        }
        r[lag] = (float)sum;
    }
}

/* Memory Management */
void cdp_pitch_data_free(cdp_pitch_data* data) {
    if (data == NULL) return;
    if (data->pitch) free(data->pitch);
    if (data->confidence) free(data->confidence);
    free(data);
}

void cdp_formant_data_free(cdp_formant_data* data) {
    if (data == NULL) return;
    if (data->f1) free(data->f1);
    if (data->f2) free(data->f2);
    if (data->f3) free(data->f3);
    if (data->f4) free(data->f4);
    if (data->b1) free(data->b1);
    if (data->b2) free(data->b2);
    if (data->b3) free(data->b3);
    if (data->b4) free(data->b4);
    free(data);
}

void cdp_partial_data_free(cdp_partial_data* data) {
    if (data == NULL) return;
    if (data->tracks) {
        for (int i = 0; i < data->num_tracks; i++) {
            if (data->tracks[i].freq) free(data->tracks[i].freq);
            if (data->tracks[i].amp) free(data->tracks[i].amp);
        }
        free(data->tracks);
    }
    free(data);
}

/* YIN Pitch Detection helpers */
static void yin_difference(const float *frame, int size, float *d, int max_lag) {
    d[0] = 0;
    for (int tau = 1; tau < max_lag; tau++) {
        double sum = 0;
        for (int i = 0; i < size - max_lag; i++) {
            double diff = frame[i] - frame[i + tau];
            sum += diff * diff;
        }
        d[tau] = (float)sum;
    }
}

static void yin_cumulative_mean(float *d, int max_lag) {
    d[0] = 1.0f;
    double running_sum = 0;
    for (int tau = 1; tau < max_lag; tau++) {
        running_sum += d[tau];
        if (running_sum > 0) {
            d[tau] = d[tau] * tau / (float)running_sum;
        } else {
            d[tau] = 1.0f;
        }
    }
}

static int yin_find_minimum(const float *d, int max_lag, float threshold) {
    int tau = 2;
    while (tau < max_lag - 1) {
        if (d[tau] < threshold) {
            while (tau + 1 < max_lag - 1 && d[tau + 1] < d[tau]) tau++;
            return tau;
        }
        tau++;
    }
    return -1;
}

static float yin_parabolic_interp(const float *d, int tau, int max_lag) {
    if (tau <= 0 || tau >= max_lag - 1) return (float)tau;
    float s0 = d[tau - 1], s1 = d[tau], s2 = d[tau + 1];
    float denom = 2.0f * (2.0f * s1 - s0 - s2);
    if (fabsf(denom) < 1e-10f) return (float)tau;
    return (float)tau + (s0 - s2) / denom;
}

/* HIGH-LEVEL: YIN Pitch Detection */
cdp_pitch_data* cdp_lib_pitch(cdp_lib_ctx* ctx, const cdp_lib_buffer* input,
                               double min_freq, double max_freq, int frame_size, int hop_size) {
    if (ctx == NULL || input == NULL) return NULL;
    if (min_freq <= 0) min_freq = 50.0;
    if (max_freq <= 0) max_freq = 2000.0;
    if (frame_size <= 0) frame_size = 2048;
    if (hop_size <= 0) hop_size = 512;

    cdp_lib_buffer *mono = cdp_lib_to_mono(ctx, input);
    if (mono == NULL) return NULL;

    int num_frames = (int)((mono->length - frame_size) / hop_size) + 1;
    if (num_frames <= 0) { cdp_lib_buffer_free(mono); return NULL; }

    cdp_pitch_data *result = (cdp_pitch_data *)calloc(1, sizeof(cdp_pitch_data));
    if (!result) { cdp_lib_buffer_free(mono); return NULL; }

    result->pitch = (float *)calloc(num_frames, sizeof(float));
    result->confidence = (float *)calloc(num_frames, sizeof(float));
    if (!result->pitch || !result->confidence) {
        cdp_lib_buffer_free(mono); cdp_pitch_data_free(result); return NULL;
    }

    result->num_frames = num_frames;
    result->frame_time = (float)hop_size / input->sample_rate;
    result->sample_rate = (float)input->sample_rate;

    int min_lag = (int)(input->sample_rate / max_freq);
    int max_lag = (int)(input->sample_rate / min_freq);
    if (max_lag > frame_size / 2) max_lag = frame_size / 2;
    if (min_lag < 2) min_lag = 2;
    /* min_freq above the sample rate collapses max_lag to zero, so `d` below
     * is a zero-byte allocation that yin_difference then writes into. An empty
     * or inverted lag range means the caller asked for a band the frame cannot
     * resolve; say so rather than searching it. */
    if (max_lag <= min_lag) {
        cdp_lib_buffer_free(mono);
        cdp_pitch_data_free(result);
        snprintf(ctx->error_msg, sizeof(ctx->error_msg),
                 "pitch: [min_freq, max_freq] is not resolvable at this frame "
                 "size (lag range %d..%d)", min_lag, max_lag);
        return NULL;
    }

    float *frame = (float *)malloc(frame_size * sizeof(float));
    float *d = (float *)malloc(max_lag * sizeof(float));
    if (!frame || !d) {
        cdp_lib_buffer_free(mono); free(frame); free(d);
        cdp_pitch_data_free(result); return NULL;
    }

    for (int f = 0; f < num_frames; f++) {
        memcpy(frame, mono->data + f * hop_size, frame_size * sizeof(float));
        yin_difference(frame, frame_size, d, max_lag);
        yin_cumulative_mean(d, max_lag);
        int tau = yin_find_minimum(d, max_lag, 0.15f);
        if (tau >= min_lag && tau <= max_lag) {
            float refined = yin_parabolic_interp(d, tau, max_lag);
            result->pitch[f] = (float)input->sample_rate / refined;
            result->confidence[f] = fmaxf(0, fminf(1, 1.0f - d[tau]));
        }
    }

    cdp_lib_buffer_free(mono); free(frame); free(d);
    return result;
}

/* LPC helpers */
static int lpc_levinson_durbin(const float *r, int order, float *a) {
    float e = r[0];
    if (e <= 0) return -1;
    float *a_prev = (float *)calloc(order, sizeof(float));
    if (!a_prev) return -1;

    for (int i = 0; i < order; i++) {
        float lambda = r[i + 1];
        for (int j = 0; j < i; j++) lambda -= a_prev[j] * r[i - j];
        lambda /= e;
        if (fabsf(lambda) >= 1.0f) { free(a_prev); return -1; }
        a[i] = lambda;
        for (int j = 0; j < i; j++) a[j] = a_prev[j] - lambda * a_prev[i - 1 - j];
        memcpy(a_prev, a, (i + 1) * sizeof(float));
        e *= (1.0f - lambda * lambda);
        if (e <= 0) { free(a_prev); return -1; }
    }
    free(a_prev);
    return 0;
}

static int lpc_find_formants(const float *a, int order, float sr, float *f, float *bw, int max) {
    float *spec = (float *)malloc(512 * sizeof(float));
    if (!spec) return 0;

    for (int i = 0; i < 512; i++) {
        float w = 2.0f * (float)M_PI * i / 512 * sr / 2 / sr;
        float re = 1.0f, im = 0.0f;
        for (int k = 0; k < order; k++) {
            re -= a[k] * cosf((k + 1) * w);
            im += a[k] * sinf((k + 1) * w);
        }
        float mag_sq = re * re + im * im;
        spec[i] = (mag_sq > 1e-10f) ? 1.0f / mag_sq : 0;
    }

    float thresh = 0;
    for (int i = 0; i < 512; i++) if (spec[i] > thresh) thresh = spec[i];
    thresh *= 0.1f;

    int n = 0;
    for (int i = 2; i < 510 && n < max; i++) {
        if (spec[i] > spec[i-1] && spec[i] > spec[i-2] &&
            spec[i] > spec[i+1] && spec[i] > spec[i+2] && spec[i] > thresh) {
            float freq = (float)i / 512 * sr / 2;
            if (freq > 100 && freq < sr/2 - 100) {
                f[n] = freq; bw[n] = sr / 512 * 2; n++;
            }
        }
    }
    free(spec);
    return n;
}

/* HIGH-LEVEL: LPC Formant Analysis */
cdp_formant_data* cdp_lib_formants(cdp_lib_ctx* ctx, const cdp_lib_buffer* input,
                                    int lpc_order, int frame_size, int hop_size) {
    if (ctx == NULL || input == NULL) return NULL;
    if (lpc_order <= 0) lpc_order = 12;
    if (frame_size <= 0) frame_size = 1024;
    if (hop_size <= 0) hop_size = 256;

    cdp_lib_buffer *mono = cdp_lib_to_mono(ctx, input);
    if (!mono) return NULL;

    int num_frames = (int)((mono->length - frame_size) / hop_size) + 1;
    if (num_frames <= 0) { cdp_lib_buffer_free(mono); return NULL; }

    cdp_formant_data *result = (cdp_formant_data *)calloc(1, sizeof(cdp_formant_data));
    if (!result) { cdp_lib_buffer_free(mono); return NULL; }

    result->f1 = (float *)calloc(num_frames, sizeof(float));
    result->f2 = (float *)calloc(num_frames, sizeof(float));
    result->f3 = (float *)calloc(num_frames, sizeof(float));
    result->f4 = (float *)calloc(num_frames, sizeof(float));
    result->b1 = (float *)calloc(num_frames, sizeof(float));
    result->b2 = (float *)calloc(num_frames, sizeof(float));
    result->b3 = (float *)calloc(num_frames, sizeof(float));
    result->b4 = (float *)calloc(num_frames, sizeof(float));
    result->num_frames = num_frames;
    result->frame_time = (float)hop_size / input->sample_rate;
    result->sample_rate = (float)input->sample_rate;

    if (!result->f1 || !result->f2 || !result->f3 || !result->f4 ||
        !result->b1 || !result->b2 || !result->b3 || !result->b4) {
        cdp_formant_data_free(result);
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Out of memory in formant analysis");
        return NULL;
    }

    float *frame = (float *)malloc(frame_size * sizeof(float));
    float *r = (float *)malloc((lpc_order + 1) * sizeof(float));
    float *a = (float *)calloc(lpc_order, sizeof(float));
    float formants[4], bandwidths[4];

    if (!frame || !r || !a) {
        free(frame); free(r); free(a);
        cdp_formant_data_free(result);
        cdp_lib_buffer_free(mono);
        cdp_lib_set_error(ctx, "Out of memory in formant analysis");
        return NULL;
    }

    for (int f = 0; f < num_frames; f++) {
        size_t start = f * hop_size;
        frame[0] = mono->data[start];
        for (int i = 1; i < frame_size && start + i < mono->length; i++)
            frame[i] = mono->data[start + i] - 0.97f * mono->data[start + i - 1];
        analysis_apply_hamming(frame, frame_size);
        analysis_autocorrelation(frame, frame_size, r, lpc_order + 1);
        memset(a, 0, lpc_order * sizeof(float));
        if (lpc_levinson_durbin(r, lpc_order, a) == 0) {
            int nf = lpc_find_formants(a, lpc_order, input->sample_rate, formants, bandwidths, 4);
            result->f1[f] = nf > 0 ? formants[0] : 0;
            result->f2[f] = nf > 1 ? formants[1] : 0;
            result->f3[f] = nf > 2 ? formants[2] : 0;
            result->f4[f] = nf > 3 ? formants[3] : 0;
            result->b1[f] = nf > 0 ? bandwidths[0] : 0;
            result->b2[f] = nf > 1 ? bandwidths[1] : 0;
            result->b3[f] = nf > 2 ? bandwidths[2] : 0;
            result->b4[f] = nf > 3 ? bandwidths[3] : 0;
        }
    }

    cdp_lib_buffer_free(mono); free(frame); free(r); free(a);
    return result;
}

/* Partial tracking types */
typedef struct { float freq; float amp; int bin; int track_id; } analysis_peak;
typedef struct { int id; int start_frame; int last_frame; float last_freq; int active; } analysis_track;

static int analysis_find_peaks(const float *amp, const float *freq, int num_bins,
                                float min_amp, analysis_peak *peaks, int max_peaks) {
    int n = 0;
    for (int b = 1; b < num_bins - 1 && n < max_peaks; b++) {
        if (amp[b] > amp[b-1] && amp[b] > amp[b+1] && amp[b] > min_amp) {
            peaks[n].freq = freq[b]; peaks[n].amp = amp[b];
            peaks[n].bin = b; peaks[n].track_id = -1; n++;
        }
    }
    return n;
}

/* HIGH-LEVEL: Partial Tracking */
cdp_partial_data* cdp_lib_get_partials(cdp_lib_ctx* ctx, const cdp_lib_buffer* input,
                                        double min_amp_db, int max_partials,
                                        double freq_tolerance, int fft_size, int hop_size) {
    if (ctx == NULL || input == NULL) return NULL;
    if (min_amp_db >= 0) min_amp_db = -60.0;
    if (max_partials <= 0) max_partials = 100;
    if (freq_tolerance <= 0) freq_tolerance = 50.0;
    if (fft_size <= 0) fft_size = 2048;
    if (hop_size <= 0) hop_size = 512;

    float min_amp = (float)pow(10.0, min_amp_db / 20.0);
    /* cdp_spectral_analyze takes an overlap *exponent* -- it computes
     * hop = fft_size >> overlap -- not the ratio. Passing the ratio meant a
     * caller asking for a 512-sample hop with a 2048 FFT got overlap 4 and a
     * hop of 128: a quarter of what it asked for, and inconsistent with the
     * frame_time reported back to the caller, which was derived from the
     * requested hop. */
    int overlap = 0;
    for (int h = fft_size; h > hop_size && overlap < 4; h >>= 1) overlap++;

    cdp_spectral_data *spectral = cdp_spectral_analyze(input->data, input->length,
                                                        input->channels, input->sample_rate,
                                                        fft_size, overlap);
    if (!spectral) { snprintf(ctx->error_msg, sizeof(ctx->error_msg), "Spectral analysis failed"); return NULL; }

    int num_frames = spectral->num_frames;
    int num_bins = spectral->num_bins;

    analysis_peak *peaks = (analysis_peak *)malloc(max_partials * sizeof(analysis_peak));
    int max_tracks = max_partials * 10;
    analysis_track *tracks = (analysis_track *)calloc(max_tracks, sizeof(analysis_track));
    int num_tracks = 0, next_track_id = 0;

    typedef struct { int id; int frame; float freq; float amp; } point_t;
    int max_points = num_frames * max_partials;
    point_t *points = (point_t *)malloc(max_points * sizeof(point_t));
    int num_points = 0;

    if (!peaks || !tracks || !points) {
        cdp_spectral_data_free(spectral); free(peaks); free(tracks); free(points);
        return NULL;
    }

    for (int f = 0; f < num_frames; f++) {
        float *amp = spectral->frames[f].data;
        float *freq = spectral->frames[f].data + num_bins;
        int np = analysis_find_peaks(amp, freq, num_bins, min_amp, peaks, max_partials);

        for (int p = 0; p < np; p++) {
            float best_dist = (float)freq_tolerance;
            int best_track = -1;
            for (int t = 0; t < num_tracks; t++) {
                if (tracks[t].active && tracks[t].last_frame == f - 1) {
                    float dist = fabsf(peaks[p].freq - tracks[t].last_freq);
                    if (dist < best_dist) { best_dist = dist; best_track = t; }
                }
            }
            if (best_track >= 0) {
                peaks[p].track_id = tracks[best_track].id;
                tracks[best_track].last_frame = f;
                tracks[best_track].last_freq = peaks[p].freq;
            } else if (num_tracks < max_tracks) {
                peaks[p].track_id = next_track_id++;
                tracks[num_tracks].id = peaks[p].track_id;
                tracks[num_tracks].start_frame = f;
                tracks[num_tracks].last_frame = f;
                tracks[num_tracks].last_freq = peaks[p].freq;
                tracks[num_tracks].active = 1;
                num_tracks++;
            }
            if (peaks[p].track_id >= 0 && num_points < max_points) {
                points[num_points].id = peaks[p].track_id;
                points[num_points].frame = f;
                points[num_points].freq = peaks[p].freq;
                points[num_points].amp = peaks[p].amp;
                num_points++;
            }
        }
        for (int t = 0; t < num_tracks; t++)
            if (tracks[t].active && tracks[t].last_frame < f) tracks[t].active = 0;
    }

    cdp_spectral_data_free(spectral); free(peaks);

    /* Build result from tracks with >= 3 frames */
    int *ts = (int *)calloc(next_track_id, sizeof(int));
    int *te = (int *)calloc(next_track_id, sizeof(int));
    int *tc = (int *)calloc(next_track_id, sizeof(int));
    if (!ts || !te || !tc) {
        free(ts); free(te); free(tc);
        free(tracks); free(points);
        cdp_lib_set_error(ctx, "Out of memory in partial tracking");
        return NULL;
    }
    for (int i = 0; i < next_track_id; i++) { ts[i] = num_frames; te[i] = -1; }
    for (int i = 0; i < num_points; i++) {
        int tid = points[i].id;
        if (tid >= 0 && tid < next_track_id) {
            if (points[i].frame < ts[tid]) ts[tid] = points[i].frame;
            if (points[i].frame > te[tid]) te[tid] = points[i].frame;
            tc[tid]++;
        }
    }

    int valid = 0;
    for (int i = 0; i < next_track_id; i++) if (tc[i] >= 3) valid++;
    if (valid > max_partials) valid = max_partials;

    cdp_partial_data *result = (cdp_partial_data *)calloc(1, sizeof(cdp_partial_data));
    if (!result) {
        free(tracks); free(points); free(ts); free(te); free(tc);
        cdp_lib_set_error(ctx, "Out of memory in partial tracking");
        return NULL;
    }
    result->tracks = (cdp_partial_track *)calloc(valid, sizeof(cdp_partial_track));
    result->num_tracks = valid;
    result->total_frames = num_frames;
    result->frame_time = (float)hop_size / input->sample_rate;
    result->sample_rate = (float)input->sample_rate;
    result->fft_size = fft_size;

    if (!result->tracks) {
        cdp_partial_data_free(result);
        free(tracks); free(points); free(ts); free(te); free(tc);
        cdp_lib_set_error(ctx, "Out of memory in partial tracking");
        return NULL;
    }

    int ri = 0;
    for (int tid = 0; tid < next_track_id && ri < valid; tid++) {
        if (tc[tid] < 3) continue;
        int len = te[tid] - ts[tid] + 1;
        result->tracks[ri].freq = (float *)calloc(len, sizeof(float));
        result->tracks[ri].amp = (float *)calloc(len, sizeof(float));
        /* tracks[] is zeroed, so a partially built result frees cleanly. */
        if (!result->tracks[ri].freq || !result->tracks[ri].amp) {
            cdp_partial_data_free(result);
            free(tracks); free(points); free(ts); free(te); free(tc);
            cdp_lib_set_error(ctx, "Out of memory in partial tracking");
            return NULL;
        }
        result->tracks[ri].start_frame = ts[tid];
        result->tracks[ri].end_frame = te[tid];
        result->tracks[ri].num_frames = len;
        for (int i = 0; i < num_points; i++) {
            if (points[i].id == tid) {
                int l = points[i].frame - ts[tid];
                if (l >= 0 && l < len) {
                    result->tracks[ri].freq[l] = points[i].freq;
                    result->tracks[ri].amp[l] = points[i].amp;
                }
            }
        }
        ri++;
    }

    free(tracks); free(points); free(ts); free(te); free(tc);
    return result;
}

/* LOW-LEVEL: CDP-style pitch from spectrum */
static int find_loudest_peaks_sorted(const float *amp, const float *freq, int num_bins,
                                      float min_freq, float *pf, float *pa, int n) {
    int cnt = 0;
    for (int b = 1; b < num_bins - 1; b++) {
        if (amp[b] > amp[b-1] && amp[b] > amp[b+1] && freq[b] >= min_freq) {
            int pos = cnt;
            while (pos > 0 && amp[b] > pa[pos - 1]) {
                if (pos < n) { pf[pos] = pf[pos-1]; pa[pos] = pa[pos-1]; }
                pos--;
            }
            if (pos < n) { pf[pos] = freq[b]; pa[pos] = amp[b]; if (cnt < n) cnt++; }
        }
    }
    return cnt;
}

static float find_fundamental_harmonic(const float *pf, int np, float minf, float maxf) {
    if (np < 2) return 0;
    float lo = pf[0] < pf[1] ? pf[0] : pf[1];
    float hi = pf[0] > pf[1] ? pf[0] : pf[1];
    for (int n = 1; n <= 8; n++) {
        for (int m = n + 1; m <= 16; m++) {
            float exp = lo * (float)m / (float)n;
            if (fabsf(exp - hi) / hi < 0.05f) {
                float f0 = lo / (float)n;
                if (f0 >= minf && f0 <= maxf) return f0;
            }
        }
    }
    float low = pf[0];
    for (int i = 1; i < np; i++) if (pf[i] < low) low = pf[i];
    return (low >= minf && low <= maxf) ? low : 0;
}

cdp_pitch_data* cdp_lib_pitch_from_spectrum(cdp_lib_ctx* ctx, const cdp_spectral_data* spectral,
                                             double min_freq, double max_freq, int num_peaks) {
    if (!ctx || !spectral) return NULL;
    if (min_freq <= 0) min_freq = 50.0;
    if (max_freq <= 0) max_freq = 2000.0;
    if (num_peaks <= 0) num_peaks = 8;
    if (num_peaks > 16) num_peaks = 16;

    cdp_pitch_data *result = (cdp_pitch_data *)calloc(1, sizeof(cdp_pitch_data));
    if (!result) {
        cdp_lib_set_error(ctx, "Out of memory in pitch analysis");
        return NULL;
    }
    result->pitch = (float *)calloc(spectral->num_frames, sizeof(float));
    result->confidence = (float *)calloc(spectral->num_frames, sizeof(float));
    result->num_frames = spectral->num_frames;
    result->frame_time = spectral->frame_time;
    result->sample_rate = spectral->sample_rate;

    float *pf = (float *)malloc(num_peaks * sizeof(float));
    float *pa = (float *)malloc(num_peaks * sizeof(float));

    if (!result->pitch || !result->confidence || !pf || !pa) {
        free(pf); free(pa);
        cdp_pitch_data_free(result);
        cdp_lib_set_error(ctx, "Out of memory in pitch analysis");
        return NULL;
    }

    for (int f = 0; f < spectral->num_frames; f++) {
        float *amp = spectral->frames[f].data;
        float *freq = spectral->frames[f].data + spectral->num_bins;
        int np = find_loudest_peaks_sorted(amp, freq, spectral->num_bins, (float)min_freq, pf, pa, num_peaks);
        float f0 = find_fundamental_harmonic(pf, np, (float)min_freq, (float)max_freq);
        result->pitch[f] = f0;
        result->confidence[f] = (f0 > 0 && np > 0) ? (pa[0] > 0.01f ? 1.0f : pa[0] / 0.01f) : 0;
    }

    free(pf); free(pa);
    return result;
}

/* LOW-LEVEL: CDP-style formants from spectrum */
cdp_formant_data* cdp_lib_formants_from_spectrum(cdp_lib_ctx* ctx, const cdp_spectral_data* spectral,
                                                  int bands_per_octave) {
    if (!ctx || !spectral) return NULL;
    if (bands_per_octave <= 0) bands_per_octave = 6;
    if (bands_per_octave > 12) bands_per_octave = 12;

    cdp_formant_data *result = (cdp_formant_data *)calloc(1, sizeof(cdp_formant_data));
    if (!result) {
        cdp_lib_set_error(ctx, "Out of memory in formant analysis");
        return NULL;
    }
    result->f1 = (float *)calloc(spectral->num_frames, sizeof(float));
    result->f2 = (float *)calloc(spectral->num_frames, sizeof(float));
    result->f3 = (float *)calloc(spectral->num_frames, sizeof(float));
    result->f4 = (float *)calloc(spectral->num_frames, sizeof(float));
    result->b1 = (float *)calloc(spectral->num_frames, sizeof(float));
    result->b2 = (float *)calloc(spectral->num_frames, sizeof(float));
    result->b3 = (float *)calloc(spectral->num_frames, sizeof(float));
    result->b4 = (float *)calloc(spectral->num_frames, sizeof(float));
    result->num_frames = spectral->num_frames;
    result->frame_time = spectral->frame_time;
    result->sample_rate = spectral->sample_rate;

    if (!result->f1 || !result->f2 || !result->f3 || !result->f4 ||
        !result->b1 || !result->b2 || !result->b3 || !result->b4) {
        cdp_formant_data_free(result);
        cdp_lib_set_error(ctx, "Out of memory in formant analysis");
        return NULL;
    }

    float fpb = spectral->sample_rate / (2.0f * (spectral->num_bins - 1));

    for (int f = 0; f < spectral->num_frames; f++) {
        float *amp = spectral->frames[f].data;
        float form[4] = {0}, bw[4] = {0};
        int nf = 0;
        for (int b = 2; b < spectral->num_bins - 2 && nf < 4; b++) {
            if (amp[b] > amp[b-1] && amp[b] > amp[b-2] && amp[b] > amp[b+1] && amp[b] > amp[b+2]) {
                float freq = b * fpb;
                if (freq > 100 && freq < spectral->sample_rate/2 - 100) {
                    form[nf] = freq;
                    int w = 1;
                    while (b-w > 0 && b+w < spectral->num_bins && amp[b-w] > amp[b]*0.5f && amp[b+w] > amp[b]*0.5f) w++;
                    bw[nf] = 2 * w * fpb;
                    nf++;
                }
            }
        }
        result->f1[f] = form[0]; result->f2[f] = form[1]; result->f3[f] = form[2]; result->f4[f] = form[3];
        result->b1[f] = bw[0]; result->b2[f] = bw[1]; result->b3[f] = bw[2]; result->b4[f] = bw[3];
    }
    return result;
}

/* LOW-LEVEL: CDP-style partials from spectrum given pitch */
cdp_partial_data* cdp_lib_partials_from_spectrum(cdp_lib_ctx* ctx, const cdp_spectral_data* spectral,
                                                  const cdp_pitch_data* pitch, int max_harmonics,
                                                  double amp_threshold) {
    if (!ctx || !spectral || !pitch) return NULL;
    if (max_harmonics <= 0) max_harmonics = 32;
    if (amp_threshold >= 0) amp_threshold = -60.0;

    float min_amp = (float)pow(10.0, amp_threshold / 20.0);
    int num_frames = spectral->num_frames < pitch->num_frames ? spectral->num_frames : pitch->num_frames;
    float fpb = spectral->sample_rate / (2.0f * (spectral->num_bins - 1));

    cdp_partial_data *result = (cdp_partial_data *)calloc(1, sizeof(cdp_partial_data));
    if (!result) {
        cdp_lib_set_error(ctx, "Out of memory in partial analysis");
        return NULL;
    }
    result->tracks = (cdp_partial_track *)calloc(max_harmonics, sizeof(cdp_partial_track));
    result->num_tracks = max_harmonics;
    result->total_frames = num_frames;
    result->frame_time = spectral->frame_time;
    result->sample_rate = spectral->sample_rate;
    result->fft_size = spectral->fft_size;

    if (!result->tracks) {
        cdp_partial_data_free(result);
        cdp_lib_set_error(ctx, "Out of memory in partial analysis");
        return NULL;
    }

    for (int h = 0; h < max_harmonics; h++) {
        result->tracks[h].freq = (float *)calloc(num_frames, sizeof(float));
        result->tracks[h].amp = (float *)calloc(num_frames, sizeof(float));
        /* tracks[] is zeroed, so a partially built result frees cleanly. */
        if (!result->tracks[h].freq || !result->tracks[h].amp) {
            cdp_partial_data_free(result);
            cdp_lib_set_error(ctx, "Out of memory in partial analysis");
            return NULL;
        }
        result->tracks[h].start_frame = 0;
        result->tracks[h].end_frame = num_frames - 1;
        result->tracks[h].num_frames = num_frames;
    }

    for (int f = 0; f < num_frames; f++) {
        float f0 = pitch->pitch[f];
        if (f0 <= 0) continue;
        float *amp = spectral->frames[f].data;
        float *freq = spectral->frames[f].data + spectral->num_bins;

        for (int h = 0; h < max_harmonics; h++) {
            float tf = f0 * (h + 1);
            if (tf >= spectral->sample_rate / 2) break;
            int tb = (int)(tf / fpb + 0.5f);
            if (tb < 1 || tb >= spectral->num_bins - 1) continue;

            int bb = tb; float ba = amp[tb];
            for (int d = -3; d <= 3; d++) {
                int b = tb + d;
                if (b >= 1 && b < spectral->num_bins - 1 && amp[b] > ba) { ba = amp[b]; bb = b; }
            }
            if (ba >= min_amp) {
                result->tracks[h].freq[f] = freq[bb];
                result->tracks[h].amp[f] = ba;
            }
        }
    }
    return result;
}
