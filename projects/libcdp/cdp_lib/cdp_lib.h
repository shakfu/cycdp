/*
 * CDP Library Interface
 *
 * This provides a clean C API for calling CDP processing functions
 * on memory buffers, bypassing file I/O.
 */

#ifndef CDP_LIB_H
#define CDP_LIB_H

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Error codes */
#define CDP_LIB_OK              0
#define CDP_LIB_ERR_MEMORY     -1
#define CDP_LIB_ERR_PARAM      -2
#define CDP_LIB_ERR_PROCESS    -3
#define CDP_LIB_ERR_INIT       -4

/*
 * Audio buffer structure for CDP library
 */
typedef struct cdp_lib_buffer {
    float *data;           /* Sample data (interleaved if stereo) */
    size_t length;         /* Number of samples */
    int channels;          /* Number of channels */
    int sample_rate;       /* Sample rate in Hz */
} cdp_lib_buffer;

/*
 * CDP library context
 */
typedef struct cdp_lib_ctx cdp_lib_ctx;

/*
 * Initialize CDP library.
 * Must be called before any processing functions.
 *
 * Returns: New context, or NULL on failure.
 */
cdp_lib_ctx* cdp_lib_init(void);

/*
 * Cleanup CDP library and free all resources.
 */
void cdp_lib_cleanup(cdp_lib_ctx* ctx);

/*
 * Get last error message.
 */
const char* cdp_lib_get_error(cdp_lib_ctx* ctx);

/*
 * Create a new buffer.
 */
cdp_lib_buffer* cdp_lib_buffer_create(size_t length, int channels, int sample_rate);

/*
 * Create a buffer from existing data (takes ownership).
 */
cdp_lib_buffer* cdp_lib_buffer_from_data(float *data, size_t length,
                                          int channels, int sample_rate);

/*
 * Free a buffer.
 */
void cdp_lib_buffer_free(cdp_lib_buffer* buf);

/* =========================================================================
 * Processing Functions
 * ========================================================================= */

/*
 * Time-stretch audio without changing pitch.
 *
 * Uses CDP's phase vocoder for high-quality stretching.
 *
 * Args:
 *   ctx: Library context
 *   input: Input audio buffer (will be converted to mono if stereo)
 *   factor: Stretch factor (2.0 = twice as long, 0.5 = half as long)
 *   fft_size: FFT window size (power of 2, 256-8192). Default 1024.
 *   overlap: Overlap factor (1-4). Default 3.
 *
 * Returns: New buffer with stretched audio, or NULL on error.
 */
cdp_lib_buffer* cdp_lib_time_stretch(cdp_lib_ctx* ctx,
                                      const cdp_lib_buffer* input,
                                      double factor,
                                      int fft_size,
                                      int overlap);

/*
 * Spectral blur - average spectrum over time.
 *
 * Creates a smeared, washed-out effect.
 *
 * Args:
 *   ctx: Library context
 *   input: Input audio buffer
 *   blur_time: Time to blur over in seconds
 *   fft_size: FFT window size
 *
 * Returns: New buffer with blurred audio, or NULL on error.
 */
cdp_lib_buffer* cdp_lib_spectral_blur(cdp_lib_ctx* ctx,
                                       const cdp_lib_buffer* input,
                                       double blur_time,
                                       int fft_size);

/*
 * Modify loudness (gain in dB).
 *
 * Args:
 *   ctx: Library context
 *   input: Input audio buffer
 *   gain_db: Gain change in dB (positive = louder)
 *
 * Returns: New buffer with modified loudness, or NULL on error.
 */
cdp_lib_buffer* cdp_lib_loudness(cdp_lib_ctx* ctx,
                                  const cdp_lib_buffer* input,
                                  double gain_db);

/*
 * Change playback speed (affects both pitch and duration).
 *
 * Args:
 *   ctx: Library context
 *   input: Input audio buffer
 *   speed: Speed factor (2.0 = double speed, 0.5 = half speed)
 *
 * Returns: New buffer with modified speed, or NULL on error.
 */
cdp_lib_buffer* cdp_lib_speed(cdp_lib_ctx* ctx,
                               const cdp_lib_buffer* input,
                               double speed);

/*
 * Pitch shift without changing duration.
 *
 * Combines speed change with time-stretch compensation.
 *
 * Args:
 *   ctx: Library context
 *   input: Input audio buffer
 *   semitones: Pitch shift in semitones (positive = up, negative = down)
 *   fft_size: FFT window size for time stretch. Default 1024.
 *
 * Returns: New buffer with pitch-shifted audio, or NULL on error.
 */
cdp_lib_buffer* cdp_lib_pitch_shift(cdp_lib_ctx* ctx,
                                     const cdp_lib_buffer* input,
                                     double semitones,
                                     int fft_size);

/*
 * Shift all frequencies by a fixed Hz offset.
 *
 * Unlike pitch shift, this adds a constant Hz value to all frequencies,
 * creating inharmonic effects.
 *
 * Args:
 *   ctx: Library context
 *   input: Input audio buffer
 *   shift_hz: Frequency offset in Hz (positive = up, negative = down)
 *   fft_size: FFT window size. Default 1024.
 *
 * Returns: New buffer with shifted frequencies, or NULL on error.
 */
cdp_lib_buffer* cdp_lib_spectral_shift(cdp_lib_ctx* ctx,
                                        const cdp_lib_buffer* input,
                                        double shift_hz,
                                        int fft_size);

/*
 * Stretch frequencies differentially (higher frequencies stretched more).
 *
 * Creates inharmonic effects by applying progressively more transposition
 * to higher frequencies.
 *
 * Args:
 *   ctx: Library context
 *   input: Input audio buffer
 *   max_stretch: Maximum transposition ratio for highest frequencies
 *   freq_divide: Frequency below which no stretching occurs
 *   exponent: Stretching curve (1.0 = linear, >1 = more effect on highs)
 *   fft_size: FFT window size. Default 1024.
 *
 * Returns: New buffer with stretched spectrum, or NULL on error.
 */
cdp_lib_buffer* cdp_lib_spectral_stretch(cdp_lib_ctx* ctx,
                                          const cdp_lib_buffer* input,
                                          double max_stretch,
                                          double freq_divide,
                                          double exponent,
                                          int fft_size);

/*
 * Apply lowpass filter.
 *
 * Attenuates frequencies above the cutoff.
 *
 * Args:
 *   ctx: Library context
 *   input: Input audio buffer
 *   cutoff_freq: Cutoff frequency in Hz
 *   attenuation_db: Attenuation in dB (negative, e.g. -60)
 *   fft_size: FFT window size. Default 1024.
 *
 * Returns: New buffer with filtered audio, or NULL on error.
 */
cdp_lib_buffer* cdp_lib_filter_lowpass(cdp_lib_ctx* ctx,
                                        const cdp_lib_buffer* input,
                                        double cutoff_freq,
                                        double attenuation_db,
                                        int fft_size);

/*
 * Apply highpass filter.
 *
 * Attenuates frequencies below the cutoff.
 *
 * Args:
 *   ctx: Library context
 *   input: Input audio buffer
 *   cutoff_freq: Cutoff frequency in Hz
 *   attenuation_db: Attenuation in dB (negative, e.g. -60)
 *   fft_size: FFT window size. Default 1024.
 *
 * Returns: New buffer with filtered audio, or NULL on error.
 */
cdp_lib_buffer* cdp_lib_filter_highpass(cdp_lib_ctx* ctx,
                                         const cdp_lib_buffer* input,
                                         double cutoff_freq,
                                         double attenuation_db,
                                         int fft_size);

/*
 * Apply bandpass filter.
 *
 * Passes frequencies between low and high cutoffs.
 *
 * Args:
 *   ctx: Library context
 *   input: Input audio buffer
 *   low_freq: Low cutoff frequency in Hz
 *   high_freq: High cutoff frequency in Hz
 *   attenuation_db: Attenuation in dB (negative, e.g. -60)
 *   fft_size: FFT window size. Default 1024.
 *
 * Returns: New buffer with filtered audio, or NULL on error.
 */
cdp_lib_buffer* cdp_lib_filter_bandpass(cdp_lib_ctx* ctx,
                                         const cdp_lib_buffer* input,
                                         double low_freq,
                                         double high_freq,
                                         double attenuation_db,
                                         int fft_size);

/*
 * Apply notch (band-reject) filter.
 *
 * Attenuates a narrow frequency band.
 *
 * Args:
 *   ctx: Library context
 *   input: Input audio buffer
 *   center_freq: Center frequency to notch out in Hz
 *   width_hz: Width of the notch in Hz
 *   attenuation_db: Attenuation in dB (negative, e.g. -60)
 *   fft_size: FFT window size. Default 1024.
 *
 * Returns: New buffer with filtered audio, or NULL on error.
 */
cdp_lib_buffer* cdp_lib_filter_notch(cdp_lib_ctx* ctx,
                                      const cdp_lib_buffer* input,
                                      double center_freq,
                                      double width_hz,
                                      double attenuation_db,
                                      int fft_size);

/*
 * Noise gate - silence audio below threshold.
 *
 * Args:
 *   ctx: Library context
 *   input: Input audio buffer
 *   threshold_db: Threshold in dB (e.g. -40)
 *   attack_ms: Attack time in milliseconds
 *   release_ms: Release time in milliseconds
 *   hold_ms: Hold time before release in milliseconds
 *
 * Returns: New buffer with gated audio, or NULL on error.
 */
cdp_lib_buffer* cdp_lib_gate(cdp_lib_ctx* ctx,
                              const cdp_lib_buffer* input,
                              double threshold_db,
                              double attack_ms,
                              double release_ms,
                              double hold_ms);

/*
 * Bitcrush - reduce bit depth and/or sample rate.
 *
 * Args:
 *   ctx: Library context
 *   input: Input audio buffer
 *   bit_depth: Target bit depth (1-16, 16 = no reduction)
 *   downsample: Downsample factor (1 = none, 2 = half rate, etc.)
 *
 * Returns: New buffer with crushed audio, or NULL on error.
 */
cdp_lib_buffer* cdp_lib_bitcrush(cdp_lib_ctx* ctx,
                                  const cdp_lib_buffer* input,
                                  int bit_depth,
                                  int downsample);

/*
 * Ring modulation - multiply signal by a carrier frequency.
 *
 * Args:
 *   ctx: Library context
 *   input: Input audio buffer
 *   freq: Carrier frequency in Hz
 *   mix: Dry/wet mix (0.0 = dry, 1.0 = wet)
 *
 * Returns: New buffer with ring-modulated audio, or NULL on error.
 */
cdp_lib_buffer* cdp_lib_ring_mod(cdp_lib_ctx* ctx,
                                  const cdp_lib_buffer* input,
                                  double freq,
                                  double mix);

/*
 * Delay effect with feedback.
 *
 * Args:
 *   ctx: Library context
 *   input: Input audio buffer
 *   delay_ms: Delay time in milliseconds
 *   feedback: Feedback amount (0.0 to <1.0)
 *   mix: Dry/wet mix (0.0 = dry, 1.0 = wet)
 *
 * Returns: New buffer with delayed audio, or NULL on error.
 */
cdp_lib_buffer* cdp_lib_delay(cdp_lib_ctx* ctx,
                               const cdp_lib_buffer* input,
                               double delay_ms,
                               double feedback,
                               double mix);

/*
 * Chorus effect - modulated delay for thickening.
 *
 * Args:
 *   ctx: Library context
 *   input: Input audio buffer
 *   rate: LFO rate in Hz (typically 0.5-5)
 *   depth_ms: Modulation depth in milliseconds (typically 1-20)
 *   mix: Dry/wet mix (0.0 = dry, 1.0 = wet)
 *
 * Returns: New buffer with chorus effect, or NULL on error.
 */
cdp_lib_buffer* cdp_lib_chorus(cdp_lib_ctx* ctx,
                                const cdp_lib_buffer* input,
                                double rate,
                                double depth_ms,
                                double mix);

/*
 * Flanger effect - short modulated delay with feedback.
 *
 * Args:
 *   ctx: Library context
 *   input: Input audio buffer
 *   rate: LFO rate in Hz (typically 0.1-2)
 *   depth_ms: Modulation depth in milliseconds (typically 1-10)
 *   feedback: Feedback amount (-0.95 to 0.95)
 *   mix: Dry/wet mix (0.0 = dry, 1.0 = wet)
 *
 * Returns: New buffer with flanger effect, or NULL on error.
 */
cdp_lib_buffer* cdp_lib_flanger(cdp_lib_ctx* ctx,
                                 const cdp_lib_buffer* input,
                                 double rate,
                                 double depth_ms,
                                 double feedback,
                                 double mix);

/*
 * Parametric EQ - boost or cut at a frequency with adjustable Q.
 *
 * Applies a bell-shaped gain curve centered at the specified frequency.
 *
 * Args:
 *   ctx: Library context
 *   input: Input audio buffer
 *   center_freq: Center frequency in Hz
 *   gain_db: Gain in dB (positive = boost, negative = cut)
 *   q: Q factor (0.1 to 10, higher = narrower bandwidth)
 *   fft_size: FFT window size. Default 1024.
 *
 * Returns: New buffer with EQ applied, or NULL on error.
 */
cdp_lib_buffer* cdp_lib_eq_parametric(cdp_lib_ctx* ctx,
                                       const cdp_lib_buffer* input,
                                       double center_freq,
                                       double gain_db,
                                       double q,
                                       int fft_size);

/*
 * Extract amplitude envelope from audio.
 *
 * Uses peak or RMS detection with attack/release smoothing.
 *
 * Args:
 *   ctx: Library context
 *   input: Input audio buffer
 *   attack_ms: Attack time in milliseconds (how fast envelope rises)
 *   release_ms: Release time in milliseconds (how fast envelope falls)
 *   mode: 0 = peak detection, 1 = RMS detection
 *
 * Returns: New buffer containing envelope values, or NULL on error.
 */
cdp_lib_buffer* cdp_lib_envelope_follow(cdp_lib_ctx* ctx,
                                         const cdp_lib_buffer* input,
                                         double attack_ms,
                                         double release_ms,
                                         int mode);

/*
 * Apply an envelope to audio (amplitude modulation).
 *
 * Multiplies the input audio by the envelope values.
 *
 * Args:
 *   ctx: Library context
 *   input: Input audio buffer
 *   envelope: Envelope buffer (from envelope_follow or generated)
 *   depth: Modulation depth (0.0 = no effect, 1.0 = full modulation)
 *
 * Returns: New buffer with envelope applied, or NULL on error.
 */
cdp_lib_buffer* cdp_lib_envelope_apply(cdp_lib_ctx* ctx,
                                        const cdp_lib_buffer* input,
                                        const cdp_lib_buffer* envelope,
                                        double depth);

/*
 * Dynamic range compressor.
 *
 * Reduces the volume of audio above the threshold.
 *
 * Args:
 *   ctx: Library context
 *   input: Input audio buffer
 *   threshold_db: Level above which compression starts (e.g., -20)
 *   ratio: Compression ratio (e.g., 4.0 means 4:1)
 *   attack_ms: Attack time in milliseconds
 *   release_ms: Release time in milliseconds
 *   makeup_gain_db: Makeup gain in dB to compensate for level reduction
 *
 * Returns: New buffer with compression applied, or NULL on error.
 */
cdp_lib_buffer* cdp_lib_compressor(cdp_lib_ctx* ctx,
                                    const cdp_lib_buffer* input,
                                    double threshold_db,
                                    double ratio,
                                    double attack_ms,
                                    double release_ms,
                                    double makeup_gain_db);

/*
 * Limiter - prevent audio from exceeding a threshold.
 *
 * Args:
 *   ctx: Library context
 *   input: Input audio buffer
 *   threshold_db: Maximum level in dB (e.g., -0.1)
 *   attack_ms: Attack time in milliseconds (0 for hard limiting)
 *   release_ms: Release time in milliseconds
 *
 * Returns: New buffer with limiting applied, or NULL on error.
 */
cdp_lib_buffer* cdp_lib_limiter(cdp_lib_ctx* ctx,
                                 const cdp_lib_buffer* input,
                                 double threshold_db,
                                 double attack_ms,
                                 double release_ms);

/* =========================================================================
 * Morphing and Cross-synthesis (CDP: morph, combine)
 * ========================================================================= */

/*
 * Spectral morph between two sounds (CDP: SPECMORPH).
 *
 * Interpolates amplitude and frequency between two sounds over time.
 * Amplitude interpolation is linear; frequency interpolation is exponential
 * (for natural pitch transitions).
 *
 * Args:
 *   ctx: Library context
 *   input1: First input audio buffer (source)
 *   input2: Second input audio buffer (target)
 *   morph_start: Time when morphing begins (0.0 to 1.0 of duration)
 *   morph_end: Time when morphing ends (0.0 to 1.0 of duration)
 *   exponent: Interpolation curve (1.0 = linear, <1 = fast start, >1 = slow start)
 *   fft_size: FFT window size. Default 1024.
 *
 * Returns: New buffer with morphed audio, or NULL on error.
 */
cdp_lib_buffer* cdp_lib_morph(cdp_lib_ctx* ctx,
                               const cdp_lib_buffer* input1,
                               const cdp_lib_buffer* input2,
                               double morph_start,
                               double morph_end,
                               double exponent,
                               int fft_size);

/*
 * Simple spectral glide between two sounds (CDP: SPECGLIDE).
 *
 * Creates a linear glide from one spectrum to another over the specified
 * duration. Amplitude interpolates linearly; frequency interpolates
 * exponentially.
 *
 * Args:
 *   ctx: Library context
 *   input1: First input audio buffer
 *   input2: Second input audio buffer
 *   duration: Output duration in seconds
 *   fft_size: FFT window size. Default 1024.
 *
 * Returns: New buffer with glided audio, or NULL on error.
 */
cdp_lib_buffer* cdp_lib_morph_glide(cdp_lib_ctx* ctx,
                                     const cdp_lib_buffer* input1,
                                     const cdp_lib_buffer* input2,
                                     double duration,
                                     int fft_size);

/*
 * Cross-synthesis: combine amplitude from one sound with frequencies from another.
 *
 * Takes the amplitude envelope from input1 and applies it to the frequency
 * content of input2 (or vice versa based on mode).
 *
 * Args:
 *   ctx: Library context
 *   input1: First input audio buffer (amplitude source by default)
 *   input2: Second input audio buffer (frequency source by default)
 *   mode: 0 = amp from input1, freq from input2
 *         1 = amp from input2, freq from input1
 *   mix: Mix between original and cross-synthesized (0.0 = original, 1.0 = full cross)
 *   fft_size: FFT window size. Default 1024.
 *
 * Returns: New buffer with cross-synthesized audio, or NULL on error.
 */
cdp_lib_buffer* cdp_lib_cross_synth(cdp_lib_ctx* ctx,
                                     const cdp_lib_buffer* input1,
                                     const cdp_lib_buffer* input2,
                                     int mode,
                                     double mix,
                                     int fft_size);

/* =========================================================================
 * Analysis Functions (CDP: pitch, formants, get_partials)
 * ========================================================================= */

/*
 * Pitch tracking result - frequency values over time
 */
typedef struct cdp_pitch_data {
    float *pitch;          /* Pitch in Hz for each frame (0 = unvoiced) */
    float *confidence;     /* Confidence/amplitude for each frame (0-1) */
    int num_frames;        /* Number of analysis frames */
    float frame_time;      /* Time between frames in seconds */
    float sample_rate;     /* Original sample rate */
} cdp_pitch_data;

/*
 * Formant analysis result - formant frequencies over time
 */
typedef struct cdp_formant_data {
    float *f1;             /* First formant frequency (Hz) */
    float *f2;             /* Second formant frequency (Hz) */
    float *f3;             /* Third formant frequency (Hz) */
    float *f4;             /* Fourth formant frequency (Hz) */
    float *b1;             /* First formant bandwidth (Hz) */
    float *b2;             /* Second formant bandwidth (Hz) */
    float *b3;             /* Third formant bandwidth (Hz) */
    float *b4;             /* Fourth formant bandwidth (Hz) */
    int num_frames;        /* Number of analysis frames */
    float frame_time;      /* Time between frames in seconds */
    float sample_rate;     /* Original sample rate */
} cdp_formant_data;

/*
 * Single partial track (sinusoidal trajectory)
 */
typedef struct cdp_partial_track {
    float *freq;           /* Frequency values over time */
    float *amp;            /* Amplitude values over time */
    int start_frame;       /* Frame index where track starts */
    int end_frame;         /* Frame index where track ends */
    int num_frames;        /* Length of track (end - start) */
} cdp_partial_track;

/*
 * Partial tracking result - sinusoidal partials over time
 */
typedef struct cdp_partial_data {
    cdp_partial_track *tracks; /* Array of partial tracks */
    int num_tracks;            /* Number of tracks */
    int total_frames;          /* Total number of analysis frames */
    float frame_time;          /* Time between frames in seconds */
    float sample_rate;         /* Original sample rate */
    int fft_size;              /* FFT size used for analysis */
} cdp_partial_data;

/* Memory management */
void cdp_pitch_data_free(cdp_pitch_data* data);
void cdp_formant_data_free(cdp_formant_data* data);
void cdp_partial_data_free(cdp_partial_data* data);

/*
 * Extract pitch contour from audio using YIN algorithm.
 *
 * Uses autocorrelation-based pitch detection. Works directly on audio.
 * Returns pitch in Hz for each analysis frame, with 0 indicating unvoiced.
 *
 * Args:
 *   ctx: Library context
 *   input: Input audio buffer (will be converted to mono if stereo)
 *   min_freq: Minimum expected frequency in Hz (default 50)
 *   max_freq: Maximum expected frequency in Hz (default 2000)
 *   frame_size: Analysis frame size in samples (default 2048)
 *   hop_size: Hop size in samples (default 512)
 *
 * Returns: Pitch data, or NULL on error.
 */
cdp_pitch_data* cdp_lib_pitch(cdp_lib_ctx* ctx,
                               const cdp_lib_buffer* input,
                               double min_freq,
                               double max_freq,
                               int frame_size,
                               int hop_size);

/*
 * Extract formant frequencies from audio using LPC analysis.
 *
 * Uses Linear Predictive Coding to estimate formant frequencies.
 * Returns up to 4 formants (F1-F4) with bandwidths for each frame.
 *
 * Args:
 *   ctx: Library context
 *   input: Input audio buffer (will be converted to mono if stereo)
 *   lpc_order: LPC order (default 12, higher = more formants but less stable)
 *   frame_size: Analysis frame size in samples (default 1024)
 *   hop_size: Hop size in samples (default 256)
 *
 * Returns: Formant data, or NULL on error.
 */
cdp_formant_data* cdp_lib_formants(cdp_lib_ctx* ctx,
                                    const cdp_lib_buffer* input,
                                    int lpc_order,
                                    int frame_size,
                                    int hop_size);

/*
 * Extract sinusoidal partials from audio using peak tracking.
 *
 * Performs spectral analysis internally, then tracks peaks over time.
 * Each partial is a continuous frequency/amplitude trajectory.
 *
 * Args:
 *   ctx: Library context
 *   input: Input audio buffer (will be converted to mono if stereo)
 *   min_amp_db: Minimum amplitude in dB to consider as partial (default -60)
 *   max_partials: Maximum number of partials to track (default 100)
 *   freq_tolerance: Frequency tolerance for track continuation in Hz (default 50)
 *   fft_size: FFT size for analysis (default 2048)
 *   hop_size: Hop size in samples (default 512)
 *
 * Returns: Partial data, or NULL on error.
 */
cdp_partial_data* cdp_lib_get_partials(cdp_lib_ctx* ctx,
                                        const cdp_lib_buffer* input,
                                        double min_amp_db,
                                        int max_partials,
                                        double freq_tolerance,
                                        int fft_size,
                                        int hop_size);

/* =========================================================================
 * Spectral Operations (CDP: focus, hilite, fold, clean)
 * ========================================================================= */

/*
 * Spectral focus - enhance frequencies around a center point.
 *
 * Uses a super-Gaussian curve (exponent 4) for sharper focus than
 * standard parametric EQ.
 *
 * Args:
 *   ctx: Library context
 *   input: Input audio buffer
 *   center_freq: Center frequency in Hz
 *   bandwidth: Bandwidth in Hz (half-power width)
 *   gain_db: Gain to apply in dB (can be negative to attenuate)
 *   fft_size: FFT window size. Default 1024.
 *
 * Returns: New buffer with focused frequencies, or NULL on error.
 */
cdp_lib_buffer* cdp_lib_spectral_focus(cdp_lib_ctx* ctx,
                                        const cdp_lib_buffer* input,
                                        double center_freq,
                                        double bandwidth,
                                        double gain_db,
                                        int fft_size);

/*
 * Spectral hilite - boost spectral peaks above threshold.
 *
 * Detects local maxima in each spectral frame and boosts them selectively,
 * emphasizing the harmonic structure.
 *
 * Args:
 *   ctx: Library context
 *   input: Input audio buffer
 *   threshold_db: Only boost peaks above this level (relative to frame peak)
 *   boost_db: Amount to boost peaks in dB
 *   fft_size: FFT window size. Default 1024.
 *
 * Returns: New buffer with highlighted peaks, or NULL on error.
 */
cdp_lib_buffer* cdp_lib_spectral_hilite(cdp_lib_ctx* ctx,
                                         const cdp_lib_buffer* input,
                                         double threshold_db,
                                         double boost_db,
                                         int fft_size);

/*
 * Spectral fold - fold spectrum at frequency (metallic effects).
 *
 * Frequencies above fold point are mirrored back down, creating
 * complex inharmonic textures with a metallic quality.
 *
 * Args:
 *   ctx: Library context
 *   input: Input audio buffer
 *   fold_freq: Frequency at which to fold the spectrum
 *   fft_size: FFT window size. Default 1024.
 *
 * Returns: New buffer with folded spectrum, or NULL on error.
 */
cdp_lib_buffer* cdp_lib_spectral_fold(cdp_lib_ctx* ctx,
                                       const cdp_lib_buffer* input,
                                       double fold_freq,
                                       int fft_size);

/*
 * Spectral clean - spectral noise gate.
 *
 * Zeros spectral bins below a per-frame threshold, removing
 * low-level noise and artifacts.
 *
 * Args:
 *   ctx: Library context
 *   input: Input audio buffer
 *   threshold_db: Threshold in dB below frame peak (e.g., -40)
 *   fft_size: FFT window size. Default 1024.
 *
 * Returns: New buffer with cleaned spectrum, or NULL on error.
 */
cdp_lib_buffer* cdp_lib_spectral_clean(cdp_lib_ctx* ctx,
                                        const cdp_lib_buffer* input,
                                        double threshold_db,
                                        int fft_size);

/* =========================================================================
 * Experimental Operations (CDP: strange, brownian, crystal)
 * ========================================================================= */

/*
 * Strange attractor (Lorenz) modulation.
 *
 * Uses the Lorenz attractor to chaotically modulate pitch and amplitude.
 * Creates complex, evolving timbral changes that are deterministic but
 * appear chaotic.
 *
 * Args:
 *   ctx: Library context
 *   input: Input audio buffer
 *   chaos_amount: Amount of chaotic modulation (0.0 to 1.0)
 *   rate: Speed of chaotic evolution (typically 0.1 to 10.0)
 *   seed: Random seed for initial attractor state
 *
 * Returns: New buffer with chaotic modulation, or NULL on error.
 */
cdp_lib_buffer* cdp_lib_strange(cdp_lib_ctx* ctx,
                                 const cdp_lib_buffer* input,
                                 double chaos_amount,
                                 double rate,
                                 unsigned int seed);

/*
 * Brownian (random walk) modulation.
 *
 * Applies random walk modulation to pitch, amplitude, or filter cutoff.
 * Creates organic, drifting parameter changes.
 *
 * Args:
 *   ctx: Library context
 *   input: Input audio buffer
 *   step_size: Maximum step size per frame (in target units)
 *   smoothing: Smoothing factor (0.0 to 1.0, higher = smoother)
 *   target: Modulation target: 0=pitch (semitones), 1=amp (dB), 2=filter (Hz)
 *   seed: Random seed for reproducibility
 *
 * Returns: New buffer with random walk modulation, or NULL on error.
 */
cdp_lib_buffer* cdp_lib_brownian(cdp_lib_ctx* ctx,
                                  const cdp_lib_buffer* input,
                                  double step_size,
                                  double smoothing,
                                  int target,
                                  unsigned int seed);

/*
 * Crystal textures - granular with decaying echoes.
 *
 * Extracts small grains and creates shimmering, crystalline textures
 * through multiple decaying echo layers with pitch scatter.
 *
 * Args:
 *   ctx: Library context
 *   input: Input audio buffer
 *   density: Grain density (grains per second, typically 20-200)
 *   decay: Echo decay time in seconds
 *   pitch_scatter: Random pitch variation in semitones
 *   seed: Random seed for reproducibility
 *
 * Returns: New buffer with crystalline texture, or NULL on error.
 */
cdp_lib_buffer* cdp_lib_crystal(cdp_lib_ctx* ctx,
                                 const cdp_lib_buffer* input,
                                 double density,
                                 double decay,
                                 double pitch_scatter,
                                 unsigned int seed);

/*
 * Fractal processing - self-similar recursive layering.
 *
 * Creates fractal textures by recursively layering pitch-shifted copies
 * of the input at decreasing amplitudes.
 *
 * Args:
 *   ctx: Library context
 *   input: Input audio buffer
 *   depth: Recursion depth (1-6)
 *   pitch_ratio: Pitch ratio between layers (e.g., 0.5 for octave down)
 *   decay: Amplitude decay per layer (0.0 to 1.0)
 *   seed: Random seed for timing variations
 *
 * Returns: New buffer with fractal processing, or NULL on error.
 */
cdp_lib_buffer* cdp_lib_fractal(cdp_lib_ctx* ctx,
                                 const cdp_lib_buffer* input,
                                 int depth,
                                 double pitch_ratio,
                                 double decay,
                                 unsigned int seed);

/*
 * Quirk - unpredictable glitchy transformations.
 *
 * Applies random pitch and timing shifts with probability-based triggers.
 *
 * Args:
 *   ctx: Library context
 *   input: Input audio buffer
 *   probability: Probability of quirk (0.0 to 1.0)
 *   intensity: Intensity of quirks (0.0 to 1.0)
 *   mode: 0=pitch, 1=timing, 2=both
 *   seed: Random seed for reproducibility
 *
 * Returns: New buffer with quirk effects, or NULL on error.
 */
cdp_lib_buffer* cdp_lib_quirk(cdp_lib_ctx* ctx,
                               const cdp_lib_buffer* input,
                               double probability,
                               double intensity,
                               int mode,
                               unsigned int seed);

/*
 * Chirikov map modulation - chaotic standard map.
 *
 * Uses the Chirikov standard map for pitch/amplitude modulation.
 * Different chaotic character from Lorenz attractor.
 *
 * Args:
 *   ctx: Library context
 *   input: Input audio buffer
 *   k_param: Chirikov K parameter (0.5 to 10.0)
 *   mod_depth: Modulation depth (0.0 to 1.0)
 *   rate: Rate of map iteration
 *   seed: Random seed for initial conditions
 *
 * Returns: New buffer with Chirikov modulation, or NULL on error.
 */
cdp_lib_buffer* cdp_lib_chirikov(cdp_lib_ctx* ctx,
                                  const cdp_lib_buffer* input,
                                  double k_param,
                                  double mod_depth,
                                  double rate,
                                  unsigned int seed);

/*
 * Cantor set gating - fractal silence pattern.
 *
 * Applies recursive middle-third removal pattern as gating.
 *
 * Args:
 *   ctx: Library context
 *   input: Input audio buffer
 *   depth: Recursion depth (1-8)
 *   duty_cycle: Proportion of audio kept (0.0 to 1.0)
 *   smooth_ms: Crossfade time in milliseconds
 *   seed: Random seed for variation
 *
 * Returns: New buffer with Cantor gating, or NULL on error.
 */
cdp_lib_buffer* cdp_lib_cantor(cdp_lib_ctx* ctx,
                                const cdp_lib_buffer* input,
                                int depth,
                                double duty_cycle,
                                double smooth_ms,
                                unsigned int seed);

/*
 * Cascade - cascading echoes with progressive transformation.
 *
 * Creates cascading delays with pitch shift, filtering, and decay.
 *
 * Args:
 *   ctx: Library context
 *   input: Input audio buffer
 *   num_echoes: Number of stages (1-12)
 *   delay_ms: Base delay in milliseconds
 *   pitch_decay: Pitch ratio per stage (e.g., 0.95)
 *   amp_decay: Amplitude decay per stage
 *   filter_decay: Filter cutoff decay per stage
 *   seed: Random seed for timing jitter
 *
 * Returns: New buffer with cascading echoes, or NULL on error.
 */
cdp_lib_buffer* cdp_lib_cascade(cdp_lib_ctx* ctx,
                                 const cdp_lib_buffer* input,
                                 int num_echoes,
                                 double delay_ms,
                                 double pitch_decay,
                                 double amp_decay,
                                 double filter_decay,
                                 unsigned int seed);

/*
 * Fracture - break audio into fragments with gaps.
 *
 * Creates broken, fractured textures by fragmenting audio.
 *
 * Args:
 *   ctx: Library context
 *   input: Input audio buffer
 *   fragment_ms: Average fragment size in milliseconds
 *   gap_ratio: Ratio of gaps to fragments (0.0 to 2.0)
 *   scatter: Fragment reordering amount (0.0 to 1.0)
 *   seed: Random seed for reproducibility
 *
 * Returns: New buffer with fractured audio, or NULL on error.
 */
cdp_lib_buffer* cdp_lib_fracture(cdp_lib_ctx* ctx,
                                  const cdp_lib_buffer* input,
                                  double fragment_ms,
                                  double gap_ratio,
                                  double scatter,
                                  unsigned int seed);

/*
 * Tesselate - tile audio segments in patterns.
 *
 * Arranges audio tiles in various patterns with transformations.
 *
 * Args:
 *   ctx: Library context
 *   input: Input audio buffer
 *   tile_ms: Tile size in milliseconds
 *   pattern: 0=repeat, 1=mirror, 2=rotate, 3=random
 *   overlap: Tile overlap ratio (0.0 to 0.5)
 *   transform: Transform intensity (0.0 to 1.0)
 *   seed: Random seed for random pattern
 *
 * Returns: New buffer with tessellated audio, or NULL on error.
 */
cdp_lib_buffer* cdp_lib_tesselate(cdp_lib_ctx* ctx,
                                   const cdp_lib_buffer* input,
                                   double tile_ms,
                                   int pattern,
                                   double overlap,
                                   double transform,
                                   unsigned int seed);

/*
 * Return this thread's context, creating it on first use.
 *
 * The Python bindings release the GIL around processing calls, so a single
 * shared context would be a data race: every call writes ctx->error_msg and
 * draws from ctx->prng_state, so two concurrent operations would corrupt each
 * other's error reporting and consume each other's seeded random stream.
 *
 * One context per thread costs ~528 bytes and is released automatically when
 * the thread exits, via a pthread key destructor (FLS on Windows). It used to
 * be retained for the life of the process, which was described as "bounded by
 * thread count" -- true only for a fixed pool. A process that creates a thread
 * per request accumulates one context per thread ever created, measured at
 * ~2 MB per 3,000 short-lived threads with no upper limit.
 *
 * Returns: The calling thread's context, or NULL if allocation failed.
 */
cdp_lib_ctx* cdp_lib_thread_ctx(void);

/*
 * Free the calling thread's context immediately.
 *
 * Not normally needed -- thread exit does this. Useful for a long-lived thread
 * that will not call into the library again, and for embedders whose threads
 * are not created by pthreads (where the destructor hook cannot fire). Safe to
 * call when no context exists, and safe to call more than once; the next
 * processing call simply creates a fresh context.
 */
void cdp_lib_release_thread_ctx(void);

#ifdef __cplusplus
}
#endif

#endif /* CDP_LIB_H */
