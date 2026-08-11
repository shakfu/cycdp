# libcdp - Native CDP Audio Processing Library

A native C library providing high-performance audio processing algorithms based on the Composers Desktop Project (CDP). This library eliminates subprocess overhead by implementing CDP algorithms directly in C, accessible via Python bindings (cycdp).

## Features

- Native C implementations of CDP spectral and granular algorithms
- Zero-copy buffer interface for efficient memory usage
- Concurrent use supported: one context per thread, no shared mutable state on any processing path (see Thread safety)
- Python bindings via Cython (see cycdp)

## Thread safety

Processing functions may be called concurrently from multiple threads, provided
each thread uses its own context.

The previous claim here -- "thread-safe design (no global state)" -- was not
true when written. The Python bindings held a single process-wide context whose
`error_msg` buffer and PRNG state every call mutated, `cdp_distort.c` used the C
runtime's global `srand`/`rand`, and `cdp_wrappage.c`, `cdp_flutter.c` and
`cdp_hover.c` each kept a file-static generator. It is true now, and the
following is what "true" means precisely.

**Guaranteed.**

- Each `cdp_lib_ctx` is independent. `cdp_lib_thread_ctx()` returns a
  per-thread context, which is what the Python bindings use; a caller managing
  contexts directly must not share one across threads, since every call writes
  `ctx->error_msg` and draws from `ctx->prng_state`.
- No processing path uses global or file-static mutable state. All
  randomisation draws from the caller's context, so a seeded operation is
  reproducible regardless of what other threads are doing.
- Input and output buffers are owned by the caller and are not shared between
  operations.

Verified with ThreadSanitizer over a mixed six-thread workload covering the
spectral, granular, distortion and morph paths: no reported races. The
per-thread context is not precautionary -- restoring the shared singleton makes
TSan report a data race on `ctx->prng_state` in `cdp_lib_distort_shuffle`.

**Not guaranteed.**

- A single `cdp_lib_ctx` used concurrently from two threads. Give each thread
  its own.
- `errstr`, a process-wide buffer the vendored FFT (`mxfft.c`) writes on its
  error paths. Reaching it requires an FFT workspace allocation failure --
  invalid transform sizes are rejected earlier -- and the only consequence is
  an interleaved error message. Audio data is unaffected.
- `cdp_shim.c` holds process-wide I/O slot state. Nothing in the current
  library calls it, so it is unreachable from the Python API, but it would need
  thread-local storage before being wired up.

**Not applicable.** These functions are not thread-safe by design and are
unrelated to processing: `cdp_shim_*`, and any direct manipulation of a shared
context's error state.

## Building

```bash
cd cdp_lib/build
cmake ..
make
```

## API Overview

All processing functions follow a consistent pattern:

```c
cdp_lib_buffer* cdp_lib_<function>(cdp_lib_ctx* ctx,
                                    const cdp_lib_buffer* input,
                                    <parameters...>);
```

- Returns a newly allocated buffer (caller must free with `cdp_lib_buffer_free`)
- Sets error message in context on failure (retrieve with `cdp_lib_get_error`)
- Input buffers are not modified

## Implemented Algorithms

### CDP Algorithm Ports

Native implementations of actual CDP algorithms.

#### Spectral Processing

| Function | CDP Equivalent | Description |
|----------|---------------|-------------|
| `time_stretch` | `stretch`, `pvoc` | Phase vocoder time stretch without pitch change |
| `spectral_blur` | `blur` | Spectral averaging/smearing over time |
| `modify_speed` | `modify` | Playback speed change (affects pitch) |
| `pitch_shift` | `repitch` | Pitch shift without changing duration |
| `spectral_shift` | `specshift` | Shift all frequencies by Hz offset |
| `spectral_stretch` | `spectstr` | Differential frequency stretching (inharmonic) |

#### Morphing / Cross-synthesis

| Function | CDP Equivalent | Description |
|----------|---------------|-------------|
| `morph` | `morph` (SPECMORPH) | Spectral interpolation between two sounds |
| `morph_glide` | `morph` (SPECGLIDE) | Simple spectral glide between two sounds |
| `cross_synth` | `combine` | Cross-synthesis: amp from one, freq from other |

#### Filtering

| Function | CDP Equivalent | Description |
|----------|---------------|-------------|
| `filter_lowpass` | `filter` | Spectral lowpass filter |
| `filter_highpass` | `filter` | Spectral highpass filter |
| `filter_bandpass` | `filter` | Spectral bandpass filter |
| `filter_notch` | `filter` | Spectral notch (band-reject) filter |

#### Envelope Operations

| Function | CDP Equivalent | Description |
|----------|---------------|-------------|
| `dovetail` | `envel` | Fade in/out envelopes (linear/exponential) |
| `tremolo` | `tremolo` | LFO amplitude modulation |
| `attack` | `envel` | Attack transient reshaping |

#### Distortion

| Function | CDP Equivalent | Description |
|----------|---------------|-------------|
| `distort_overload` | `distort` | Soft/hard clipping distortion |
| `distort_reverse` | `distort` | Reverse wavecycles at zero-crossings |
| `distort_fractal` | `distort` | Recursive wavecycle overlay |
| `distort_shuffle` | `distort` | Segment rearrangement |

#### Reverb & Spatial

| Function | CDP Equivalent | Description |
|----------|---------------|-------------|
| `reverb` | `reverb` | FDN reverb (8 comb + 4 allpass filters) |

#### Granular / Texture

| Function | CDP Equivalent | Description |
|----------|---------------|-------------|
| `brassage` | `brassage` | Granular resynthesis with pitch/time control |
| `freeze` | `freeze` | Segment repetition with crossfade |
| `grain_cloud` | `grain` | Grain cloud from amplitude-detected grains |
| `grain_extend` | `grainex` | Extend duration using grain repetition |
| `texture_simple` | `texture` (SIMPLE_TEX) | Simple texture layering |
| `texture_multi` | `texture` (GROUPS) | Multi-layer grouped texture |

#### Core Operations

| Function | Description |
|----------|-------------|
| `gain`, `gain_db` | Amplitude adjustment (linear/dB) |
| `normalize`, `normalize_db` | Peak normalization |
| `phase_invert` | Phase inversion (multiply by -1) |
| `pan`, `pan_envelope` | Stereo panning (static/envelope) |
| `mirror`, `narrow` | Stereo field manipulation |
| `mix`, `mix2` | Audio mixing |
| `reverse` | Reverse audio |
| `fade_in`, `fade_out` | Apply fade envelopes |
| `concat` | Concatenate buffers |

#### Channel Operations

| Function | Description |
|----------|-------------|
| `to_mono` | Convert to mono (mix channels) |
| `to_stereo` | Convert mono to stereo |
| `extract_channel` | Extract single channel |
| `merge_channels` | Merge mono buffers to multichannel |
| `split_channels` | Split multichannel to mono buffers |
| `interleave` | Interleave samples from multiple buffers |

#### File I/O

| Function | Description |
|----------|-------------|
| `read_file` | Read WAV file (float, PCM16, PCM24) |
| `write_file` | Write WAV file (float, PCM16, PCM24) |

### Non-CDP Additions

Standard DSP functions not derived from CDP algorithms.

#### Dynamics

| Function | Description |
|----------|-------------|
| `gate` | Noise gate with attack/release/hold |
| `compressor` | Dynamic range compression |
| `limiter` | Hard/soft peak limiting |
| `envelope_follow` | Extract amplitude envelope (peak/RMS) |
| `envelope_apply` | Apply envelope to sound |

#### EQ

| Function | Description |
|----------|-------------|
| `eq_parametric` | Parametric EQ with center freq, gain, Q |

#### Effects

| Function | Description |
|----------|-------------|
| `bitcrush` | Bit depth and sample rate reduction |
| `ring_mod` | Ring modulation with carrier frequency |
| `delay` | Feedback delay with mix control |
| `chorus` | Modulated delay (LFO-based) |
| `flanger` | Short modulated delay with feedback |

## Usage Example

```c
#include "cdp_lib.h"

int main() {
    // Initialize library
    cdp_lib_ctx* ctx = cdp_lib_init();
    if (ctx == NULL) return 1;

    // Read input file
    cdp_lib_buffer* input = cdp_lib_read_file(ctx, "input.wav");
    if (input == NULL) {
        printf("Error: %s\n", cdp_lib_get_error(ctx));
        cdp_lib_cleanup(ctx);
        return 1;
    }

    // Apply time stretch (2x slower)
    cdp_lib_buffer* stretched = cdp_lib_time_stretch(ctx, input, 2.0, 1024, 4);
    if (stretched == NULL) {
        printf("Error: %s\n", cdp_lib_get_error(ctx));
    } else {
        // Write output
        cdp_lib_write_file(ctx, stretched, "output.wav", 0);
        cdp_lib_buffer_free(stretched);
    }

    // Cleanup
    cdp_lib_buffer_free(input);
    cdp_lib_cleanup(ctx);
    return 0;
}
```

## Python Bindings

See the `cycdp` package for Python bindings:

```python
import cycdp

# Read audio
buf = cycdp.read_file("input.wav")

# Apply time stretch
stretched = cycdp.time_stretch(buf, factor=2.0)

# Morph between two sounds
buf2 = cycdp.read_file("target.wav")
morphed = cycdp.morph(buf, buf2, morph_start=0.2, morph_end=0.8)

# Write output
cycdp.write_file(morphed, "output.wav")
```

## License

This library is part of the CDP System. See LICENSE for details.

## References

- [Composers Desktop Project](http://www.composersdesktop.com)
- [Trevor Wishart](http://www.trevorwishart.co.uk)
