# cycdp

Python bindings for the [CDP8](https://github.com/ComposersDesktop/CDP8) (Composers Desktop Project, Release 8) audio processing library.

## Overview

The [Composers Desktop Project](https://www.composersdesktop.com) (CDP) is a venerable suite of over 500 sound transformation programs developed since the late 1980s by Trevor Wishart, Richard Orton, and others. It occupies a unique niche in audio processing: where most tools focus on mixing, mastering, or standard effects, CDP specializes in deep spectral manipulation, granular synthesis, pitch-synchronous operations, waveset distortion, and other techniques rooted in the electroacoustic and computer music traditions.

Historically, CDP programs are invoked as standalone command-line executables that read and write sound files, which makes integration into modern workflows cumbersome. **cycdp** works differently: a C library reimplements a curated subset of CDP's algorithms to operate directly on memory buffers, and Cython bindings expose it to Python. The result is native-speed audio processing with a Pythonic API and no subprocess overhead.

### What "reimplements" means

The algorithms here are **independent ports**, not the original CDP executables wrapped or linked. Each was written against the corresponding program in the CDP8 source tree -- `dev/morph/morph.c` for the morph family, `dev/grain/grain1.c` for the granular operations, and so on -- reproducing the technique while dropping the file I/O and command-line parsing that the originals are built around. `projects/libcdp/DEV_GUIDE.md` describes that process.

The practical consequence: cycdp gives you CDP's *techniques*, and output that is close to but not bit-identical with the original programs. Where fidelity to a specific CDP release matters, compare against the executable rather than assuming equivalence. The only upstream CDP source compiled into the extension is the FFT (`dev/pv/mxfft.c`).

The exception is the phase vocoder underneath the spectral operations, which follows CDP's `dev/pv/pvoc.c` closely rather than loosely -- the rotated frame folding, the deviation-from-bin-centre phase accumulation, and the sign conventions are all CDP's. That is deliberate: a looser reading of the algorithm had three defects that cancelled on any analyse-then-resynthesise path and so were invisible until an operation tried to *read* a frequency or move energy between bins.

### Design principles

- **Zero-copy output.** A result `Buffer` exposes its samples through the Python buffer protocol, so reading it into numpy or `array.array` copies nothing. Input is copied once into library-owned memory on the way in -- a few microseconds for a typical buffer, against milliseconds for the processing itself.

- **No numpy dependency.** Operates on any object supporting the Python buffer protocol (`array.array`, `memoryview`, numpy arrays, etc.). Numpy is optional, not required.

- **Functional API.** Most functions accept a buffer and return a new buffer, leaving the original unchanged. Low-level in-place alternatives are also available.

- **Self-contained.** The C library is compiled into the extension; no external CDP installation is needed.

## Features

**Spectral Processing** -- Time stretching (preserving pitch), pitch shifting (preserving duration), spectral blur, shift, stretch, focus, hilite, fold, and noise cleaning.

**Granular Synthesis** -- Classic brassage, freeze, grain clouds, grain time-extension, simple and multi-layer texture synthesis, wrappage, plus extended grain operations (reorder, rerhythm, reverse, timewarp, repitch, stereo positioning, omit, duplicate).

**Pitch-Synchronous Operations (PSOW)** -- Time-stretching that preserves pitch via PSOLA, grain extraction and interpolation, and a hover effect for sustained pitched textures.

**FOF Extraction and Synthesis** -- Extract pitch-synchronous grains (FOFs), build a grain bank, resynthesize at arbitrary pitch and duration, and repitch with optional formant preservation.

**Morphing and Cross-Synthesis** -- Spectral morphing between two sounds, gliding morphs over time, and vocoder-style cross-synthesis.

**Distortion** -- Waveset-based techniques: overload/saturation, reverse, fractal, shuffle, cut with decaying envelopes, marker-based interpolation, wavecycle repetition, half-wavecycle shifting, and progressive warp with sample folding.

**Dynamics and EQ** -- Compressor, limiter, noise gate, parametric EQ, envelope follower, and envelope application.

**Filters** -- Lowpass, highpass, bandpass, and notch (band-reject).

**Effects** -- Reverb (FDN: 8 comb + 4 allpass), delay, chorus, flanger, ring modulation, bitcrush, tremolo, and attack reshaping.

**Spatial Processing** -- Static and envelope-driven panning, stereo mirror and width control, spinning rotation with optional doppler, dual-rotation modulation, spatial tremolo, and phase-based stereo enhancement.

**Playback and Time Manipulation** -- Zigzag, iterate, stutter, bounce, drunk-walk navigation, looping with crossfades, TDOLA time-stretching, waveset scrambling, splinter, and silence constriction.

**Experimental / Chaos** -- Strange attractor (Lorenz), Brownian motion, crystal growth, fractal, Chirikov map, Cantor set, cascade, fracture, and tesselation transformations.

**Analysis** -- Pitch tracking (YIN), formant analysis (LPC), and partial/harmonic extraction.

**Synthesis** -- Waveform generation (sine, square, saw, ramp, triangle), white and pink noise, click/metronome tracks, and chord synthesis from MIDI notes.

**Core Operations** -- Gain (linear and dB), normalization, phase inversion, peak detection, channel conversion (mono/stereo, split, merge, interleave), mixing, reverse, fade in/out, and concatenation.

**File I/O** -- Read and write WAV files (float32, PCM16, PCM24).

## Concurrency

Processing calls release the GIL, so they run in parallel across threads:

```python
import concurrent.futures as cf
import cycdp

buf = cycdp.read_file("input.wav")

with cf.ThreadPoolExecutor(max_workers=4) as pool:
    results = list(pool.map(lambda f: cycdp.time_stretch(buf, f),
                            [1.5, 2.0, 2.5, 3.0]))
```

Each thread gets its own library context, so seeded operations stay reproducible under contention and error messages do not interleave. Buffers are owned by the caller; passing the same input Buffer to concurrent operations is safe because each call copies it before processing.

Verified with ThreadSanitizer over a mixed multi-threaded workload. On a four-core machine, eight `time_stretch` calls run about 3.4x faster across four threads than sequentially.

## Installation

```bash
pip install cycdp
```

Requires Python 3.11 or later. Wheels are built against the CPython stable ABI,
so a single `cp311-abi3` wheel per platform serves 3.11 and every later
interpreter, including ones released after the wheel was.

If you prefer to build from source:

```bash
# clone the repository
git clone https://github.com/shakfu/cycdp.git
cd cycdp

# Build and install in development mode
make build

# Or with uv directly
uv sync
```

## Quick Start

### Command Line

```bash
# Process audio
cycdp time-stretch input.wav --factor 2.0 -o stretched.wav
cycdp reverb input.wav --decay-time 3.0 --mix 0.5
cycdp pitch-shift input.wav --semitones 5 -o shifted.wav

# Two-input operations
cycdp morph voice.wav pad.wav --morph-end 0.7 -o morphed.wav
cycdp mix2 track1.wav track2.wav -o mixed.wav

# Synthesis (no input file)
cycdp synth-wave --waveform saw --frequency 220 --duration 2.0 -o tone.wav
cycdp synth-chord --midi-notes 60 64 67 --duration 1.0 -o chord.wav

# Analysis (output to stdout)
cycdp pitch input.wav
cycdp pitch input.wav --format json
cycdp formants input.wav --format csv -o formants.csv

# Utilities
cycdp info input.wav
cycdp list                  # all commands grouped by category
cycdp list spectral         # commands in one category
cycdp version
```

Output is auto-normalized to 0.95 peak level by default. Use `--no-normalize` to disable, or `-n 0.8` to set a different target. When `-o` is omitted, output is written alongside the input as `<input_stem>_<command>.wav`.

Also accessible as `python3 -m cycdp`.

### Python API

```python
import cycdp

# Load audio file
buf = cycdp.read_file("input.wav")

# Apply processing
stretched = cycdp.time_stretch(buf, factor=2.0)
shifted = cycdp.pitch_shift(buf, semitones=5)

# Save result
cycdp.write_file("output.wav", stretched)
```

## Usage

### High-level API

Works with any float32 buffer (numpy arrays, `array.array('f')`, memoryview, etc.):

```python
import array
import cycdp

# Create sample data
samples = array.array('f', [0.5, 0.3, -0.2, 0.8, -0.4])

# Apply gain (linear or decibels)
result = cycdp.gain(samples, gain_factor=2.0)
result = cycdp.gain_db(samples, db=6.0)  # +6dB = ~2x

# Normalize to target peak level
result = cycdp.normalize(samples, target=1.0)
result = cycdp.normalize_db(samples, target_db=-3.0)  # -3dBFS

# Phase invert
result = cycdp.phase_invert(samples)

# Find peak level
level, position = cycdp.peak(samples)
```

### With numpy

```python
import numpy as np
import cycdp

samples = np.random.randn(44100).astype(np.float32) * 0.5
result = cycdp.normalize(samples, target=0.9)

# Result supports buffer protocol - zero-copy to numpy
output = np.asarray(result)
```

### File I/O

```python
import cycdp

# Read audio file (returns Buffer)
buf = cycdp.read_file("input.wav")

# Write audio file
cycdp.write_file("output.wav", buf)
```

### Low-level API

For more control, use explicit Context and Buffer objects:

```python
import cycdp

# Create context and buffer
ctx = cycdp.Context()
buf = cycdp.Buffer.create(1000, channels=2, sample_rate=44100)

# Fill buffer
for i in range(len(buf)):
    buf[i] = 0.5

# Process in-place
cycdp.apply_gain(ctx, buf, 2.0, clip=True)
cycdp.apply_normalize(ctx, buf, target_level=0.9)

# Get peak info
level, pos = cycdp.get_peak(ctx, buf)

# Access via buffer protocol
mv = memoryview(buf)
```

## API Reference

### File I/O

| Function | Description |
|----------|-------------|
| `read_file(path)` | Read audio file, returns Buffer |
| `write_file(path, buffer)` | Write buffer to audio file |

### Gain and Normalization

| Function | Description |
|----------|-------------|
| `gain(samples, gain_factor, ...)` | Apply linear gain |
| `gain_db(samples, db, ...)` | Apply gain in decibels |
| `normalize(samples, target, ...)` | Normalize to target peak (0-1) |
| `normalize_db(samples, target_db, ...)` | Normalize to target dB |
| `phase_invert(samples, ...)` | Invert phase |
| `peak(samples, ...)` | Find peak level and position |

### Spatial and Panning

| Function | Description |
|----------|-------------|
| `pan(samples, position, ...)` | Pan mono to stereo (-1 to 1) |
| `pan_envelope(samples, envelope, ...)` | Pan with time-varying envelope |
| `mirror(samples, ...)` | Mirror/swap stereo channels |
| `narrow(samples, width, ...)` | Adjust stereo width (0=mono, 1=full) |

### Mixing

| Function | Description |
|----------|-------------|
| `mix(buffers, ...)` | Mix multiple buffers together |
| `mix2(buf1, buf2, ...)` | Mix two buffers |

### Buffer Utilities

| Function | Description |
|----------|-------------|
| `reverse(samples, ...)` | Reverse audio |
| `fade_in(samples, duration, ...)` | Apply fade in |
| `fade_out(samples, duration, ...)` | Apply fade out |
| `concat(buffers, ...)` | Concatenate buffers |

### Channel Operations

| Function | Description |
|----------|-------------|
| `to_mono(samples, ...)` | Convert to mono |
| `to_stereo(samples, ...)` | Convert mono to stereo |
| `extract_channel(samples, channel, ...)` | Extract single channel |
| `merge_channels(left, right, ...)` | Merge two mono buffers to stereo |
| `split_channels(samples, ...)` | Split stereo to two mono buffers |
| `interleave(channels, ...)` | Interleave multiple mono buffers |

### Time and Pitch

| Function | Description |
|----------|-------------|
| `time_stretch(samples, stretch_factor, ...)` | Time stretch without pitch change |
| `modify_speed(samples, speed, ...)` | Change speed (affects pitch) |
| `pitch_shift(samples, semitones, ...)` | Shift pitch without time change |

### Spectral Processing

| Function | Description |
|----------|-------------|
| `spectral_blur(samples, blur_amount, ...)` | Blur/smear spectrum over time |
| `spectral_shift(samples, shift, ...)` | Shift spectrum up/down |
| `spectral_stretch(samples, stretch, ...)` | Stretch/compress spectrum |
| `spectral_focus(samples, freq, bandwidth, ...)` | Focus on frequency region |
| `spectral_hilite(samples, freq, gain, ...)` | Highlight frequency region |
| `spectral_fold(samples, freq, ...)` | Fold spectrum around frequency |
| `spectral_clean(samples, threshold, ...)` | Remove spectral noise |

### Filters

| Function | Description |
|----------|-------------|
| `filter_lowpass(samples, cutoff, ...)` | Low-pass filter |
| `filter_highpass(samples, cutoff, ...)` | High-pass filter |
| `filter_bandpass(samples, low, high, ...)` | Band-pass filter |
| `filter_notch(samples, freq, width, ...)` | Notch/band-reject filter |

### Dynamics and EQ

| Function | Description |
|----------|-------------|
| `gate(samples, threshold, ...)` | Noise gate |
| `compressor(samples, threshold, ratio, ...)` | Dynamic range compressor |
| `limiter(samples, threshold, ...)` | Peak limiter |
| `eq_parametric(samples, freq, gain, q, ...)` | Parametric EQ band |
| `envelope_follow(samples, ...)` | Extract amplitude envelope |
| `envelope_apply(samples, envelope, ...)` | Apply envelope to audio |

### Effects

| Function | Description |
|----------|-------------|
| `bitcrush(samples, bits, ...)` | Bit depth reduction |
| `ring_mod(samples, freq, ...)` | Ring modulation |
| `delay(samples, time, feedback, ...)` | Delay effect |
| `chorus(samples, depth, rate, ...)` | Chorus effect |
| `flanger(samples, depth, rate, ...)` | Flanger effect |
| `reverb(samples, size, damping, ...)` | Reverb effect |

### Envelope Shaping

| Function | Description |
|----------|-------------|
| `dovetail(samples, fade_time, ...)` | Apply dovetail fades |
| `tremolo(samples, rate, depth, ...)` | Tremolo effect |
| `attack(samples, attack_time, ...)` | Modify attack transient |

### Distortion

| Function | Description |
|----------|-------------|
| `distort_overload(samples, gain, ...)` | Overload/saturation distortion |
| `distort_reverse(samples, ...)` | Reverse distortion effect |
| `distort_fractal(samples, ...)` | Fractal distortion |
| `distort_shuffle(samples, ...)` | Shuffle distortion |
| `distort_cut(samples, cycle_count, ...)` | Waveset cut with decaying envelope |
| `distort_mark(samples, markers, ...)` | Interpolate wavesets at time markers |
| `distort_repeat(samples, multiplier, ...)` | Time-stretch by repeating wavecycles |
| `distort_shift(samples, group_size, ...)` | Shift/swap half-wavecycle groups |
| `distort_warp(samples, warp, ...)` | Progressive warp distortion with sample folding |

### Granular Processing

| Function | Description |
|----------|-------------|
| `brassage(samples, ...)` | Granular brassage |
| `freeze(samples, position, ...)` | Granular freeze at position |
| `grain_cloud(samples, density, ...)` | Granular cloud synthesis |
| `grain_extend(samples, extension, ...)` | Granular time extension |
| `texture_simple(samples, ...)` | Simple texture synthesis |
| `texture_multi(samples, ...)` | Multi-layer texture synthesis |

### Morphing and Cross-synthesis

| Function | Description |
|----------|-------------|
| `morph(buf1, buf2, amount, ...)` | Spectral morph between sounds |
| `morph_glide(buf1, buf2, ...)` | Gliding morph over time |
| `cross_synth(carrier, modulator, ...)` | Cross-synthesis (vocoder-like) |

### Analysis

| Function | Description |
|----------|-------------|
| `pitch(samples, ...)` | Extract pitch data |
| `formants(samples, ...)` | Extract formant data |
| `get_partials(samples, ...)` | Extract partial/harmonic data |

### Experimental/Chaos

| Function | Description |
|----------|-------------|
| `strange(samples, ...)` | Strange attractor transformation |
| `brownian(samples, ...)` | Brownian motion transformation |
| `crystal(samples, ...)` | Crystal growth patterns |
| `fractal(samples, ...)` | Fractal transformation |
| `quirk(samples, ...)` | Quirky transformation |
| `chirikov(samples, ...)` | Chirikov map transformation |
| `cantor(samples, ...)` | Cantor set transformation |
| `cascade(samples, ...)` | Cascade transformation |
| `fracture(samples, ...)` | Fracture transformation |
| `tesselate(samples, ...)` | Tesselation transformation |

### Playback/Time Manipulation

| Function | Description |
|----------|-------------|
| `zigzag(samples, times, ...)` | Alternating forward/backward playback through time points |
| `iterate(samples, repeats, ...)` | Repeat audio with pitch shift and gain decay variations |
| `stutter(samples, segment_ms, ...)` | Segment-based stuttering with silence inserts |
| `bounce(samples, bounces, ...)` | Bouncing ball effect with accelerating repeats |
| `drunk(samples, duration, ...)` | Random "drunk walk" navigation through audio |
| `loop(samples, start, length_ms, ...)` | Loop a section with crossfades and variations |
| `retime(samples, ratio, ...)` | Time-domain time stretch/compress (TDOLA) |
| `scramble(samples, mode, ...)` | Reorder wavesets (shuffle, reverse, by size/level) |
| `splinter(samples, start, ...)` | Fragmenting effect with shrinking repeats |
| `hover(samples, frequency, location, ...)` | Zigzag reading at specified frequency for hovering pitch effect |
| `constrict(samples, constriction)` | Shorten or remove silent sections |
| `phase_invert(samples)` | Invert phase (multiply all samples by -1) |
| `phase_stereo(samples, transfer)` | Enhance stereo separation via phase subtraction |
| `wrappage(samples, grain_size, density, ...)` | Granular texture with stereo spatial distribution |

### Spatial Effects

| Function | Description |
|----------|-------------|
| `spin(samples, rate, ...)` | Rotate audio around stereo field with optional doppler |
| `rotor(samples, pitch_rate, amp_rate, ...)` | Dual-rotation modulation (pitch + amplitude interference) |
| `flutter(samples, frequency, depth, ...)` | Spatial tremolo (loudness modulation alternating L/R) |

### Extended Granular

| Function | Description |
|----------|-------------|
| `grain_reorder(samples, mode, ...)` | Reorder detected grains (shuffle, reverse, rotate) |
| `grain_rerhythm(samples, factor, ...)` | Change timing/rhythm of grains |
| `grain_reverse(samples, ...)` | Reverse individual grains in place |
| `grain_timewarp(samples, factor, ...)` | Time-stretch/compress grain spacing |
| `grain_repitch(samples, semitones, ...)` | Pitch-shift grains with interpolation |
| `grain_position(samples, spread, ...)` | Reposition grains in stereo field |
| `grain_omit(samples, probability, ...)` | Probabilistically omit grains |
| `grain_duplicate(samples, count, ...)` | Duplicate grains with variations |

### Pitch-Synchronous Operations (PSOW)

| Function | Description |
|----------|-------------|
| `psow_stretch(samples, stretch_factor, ...)` | Time-stretch while preserving pitch (PSOLA) |
| `psow_grab(samples, time, duration, ...)` | Extract pitch-synchronous grains from position |
| `psow_dupl(samples, repeat_count, ...)` | Duplicate grains for time-stretching |
| `psow_interp(grain1, grain2, ...)` | Interpolate between two grains |

### FOF Extraction and Synthesis (FOFEX)

| Function | Description |
|----------|-------------|
| `fofex_extract(samples, time, ...)` | Extract single FOF (pitch-synchronous grain) at time |
| `fofex_extract_all(samples, ...)` | Extract all FOFs to uniform-length bank |
| `fofex_synth(fof_bank, duration, frequency, ...)` | Synthesize audio from FOFs at target pitch |
| `fofex_repitch(samples, pitch_shift, ...)` | Repitch audio with optional formant preservation |

### Synthesis

| Function | Description |
|----------|-------------|
| `synth_wave(waveform, frequency, ...)` | Generate waveforms (sine, square, saw, ramp, triangle) |
| `synth_noise(pink, amplitude, ...)` | Generate white or pink noise |
| `synth_click(tempo, beats_per_bar, ...)` | Generate click/metronome track |
| `synth_chord(midi_notes, ...)` | Synthesize chord from MIDI note list |

### Utility Functions

| Function | Description |
|----------|-------------|
| `gain_to_db(gain)` | Convert linear gain to decibels |
| `db_to_gain(db)` | Convert decibels to linear gain |
| `version()` | Get library version string |

### Low-level Functions

These work with explicit Context and Buffer objects:

| Function | Description |
|----------|-------------|
| `apply_gain(ctx, buf, gain, clip)` | Apply gain in-place |
| `apply_gain_db(ctx, buf, db, clip)` | Apply dB gain in-place |
| `apply_normalize(ctx, buf, target)` | Normalize in-place |
| `apply_normalize_db(ctx, buf, target_db)` | Normalize to dB in-place |
| `apply_phase_invert(ctx, buf)` | Invert phase in-place |
| `get_peak(ctx, buf)` | Get peak level and position |

### Classes

- `Context` - Processing context (holds error state)

- `Buffer` - Audio buffer with buffer protocol support

  - `Buffer.create(frames, channels, sample_rate)` - Create new buffer

  - Supports indexing, len(), and memoryview

### Constants

**Processing flags:**

- `FLAG_NONE` - No processing flags

- `FLAG_CLIP` - Clip output to [-1.0, 1.0]

**Waveform types (for `synth_wave`):**

- `WAVE_SINE` - Sine wave

- `WAVE_SQUARE` - Square wave

- `WAVE_SAW` - Sawtooth wave

- `WAVE_RAMP` - Ramp (reverse sawtooth) wave

- `WAVE_TRIANGLE` - Triangle wave

**Scramble modes (for `scramble`):**

- `SCRAMBLE_SHUFFLE` - Random shuffle

- `SCRAMBLE_REVERSE` - Reverse order

- `SCRAMBLE_SIZE_UP` - Sort by size (smallest first)

- `SCRAMBLE_SIZE_DOWN` - Sort by size (largest first)

- `SCRAMBLE_LEVEL_UP` - Sort by level (quietest first)

- `SCRAMBLE_LEVEL_DOWN` - Sort by level (loudest first)

### Exceptions

- `CDPError` - Raised on processing errors

## Architecture

```text
Python                  cycdp (high-level API)
                            |
Cython                  _core.pyx
                        - parameter validation

                        - Buffer <-> C conversion, error translation

                        - releases the GIL around every processing call
                            |
              +-------------+-------------+
              |                           |
C         libcdp                      cdp_lib
      (buffers, gain,             (spectral, granular, morph,
       channels, mixing,           distortion, PSOW, FOFEX, ...
       spatial, WAV I/O)           -- algorithms ported from CDP8)
              |                           |
              +-------------+-------------+
                            |
                        mxfft.c
                 (the one upstream CDP8 source
                  compiled in: FFT routines)
```

**libcdp** (`projects/libcdp/src/`) -- Core C library: buffer management, gain, channel operations, mixing, spatial processing, WAV read/write, and shared utilities. All of it operates on memory buffers.

**cdp_lib** (`projects/libcdp/cdp_lib/`) -- The processing algorithms, one `.c`/`.h` pair per category (spectral, granular, morph, distortion, playback, PSOW, FOFEX, experimental, ...). Each is an independent port of the corresponding CDP program, written against the upstream source but structured around buffers rather than files. See `projects/libcdp/DEV_GUIDE.md`.

**CDP8 sources** (`projects/cpd8/`) -- The upstream CDP8 tree, vendored as the reference the ports are written against. `dev/pv/mxfft.c` is the only file compiled into the extension, with `dev/newinclude` and `dev/include` on the include path for the headers it needs. Everything else in the tree is source material, not a build input.

### An approach that was tried and dropped

`projects/libcdp/cdp_lib/cdp_shim.*` and `cdp_io_redirect.*` implement a fake `sfsys`: a slot table of in-memory "files" plus `#define`s that would redirect CDP's soundfile calls (`sndopenEx`, `fgetfbufEx`, `fputfbufEx`, `sndseekEx`) to it. Had it worked, an unmodified CDP program source could have been compiled and called in-process, making all ~500 of them available by compiling rather than rewriting.

**It is not part of the build.** Intercepting I/O turned out to be necessary but nowhere near sufficient: CDP algorithms are `main()` programs with command-line parsing and extensive global state, so porting each core loop proved cheaper. The files remain in the tree as the record of the approach, with the reasoning and the obstacles in the header comment of `cdp_shim.h`. Two tests keep the decision honest -- `test_shim_is_not_compiled` fails if either file returns to `CDP_LIB_SOURCES`, and `test_shim_remains_unreachable` fails if anything starts calling into them.

Reviving it would mean solving the hosting problem first. Its I/O slot state is process-global by design, faithfully mirroring the CDP programs it was meant to host, and the processing paths release the GIL -- so the right ownership model follows from whatever solves the hosting problem rather than preceding it.

### Directory layout

```text
cycdp/
  src/cycdp/
    __init__.py                 # Public exports
    __main__.py                 # Entry point for python3 -m cycdp
    cli.py                      # CLI: registry, parser, handlers
    _core.pyx                   # Cython bindings
    _core.pyi                   # Type stubs
    cdp_lib.pxd                 # Cython declarations for C layer
  projects/
    libcdp/
      include/
        cdp.h                   # Public C API
        cdp_error.h             # Error codes
        cdp_types.h             # Type definitions
      src/                      # Reimplemented core (buffer, gain, channel, ...)
      cdp_lib/
        cdp_lib.h/.c            # Main library entry point
        cdp_spectral.h/.c       # Phase vocoder + spectral operations
        cdp_granular.h/.c       # Granular synthesis
        cdp_morph.h/.c          # Morphing
        cdp_distort.h/.c        # Waveset distortion
        cdp_*.h/.c              # Other categories
        cdp_shim.h/.c           # NOT BUILT: abandoned sfsys shim, kept
        cdp_io_redirect.h/.c    #   as a record -- see cdp_shim.h
    cpd8/dev/                   # Upstream CDP8 sources (FFT, includes)
  tests/                        # Python tests
  demos/                        # Example scripts
  CMakeLists.txt                # Builds extension
```

## Demos

The `demos/` directory contains example scripts demonstrating cycdp usage.

### Run All Demos

```bash
make demos        # Run all demos, output WAV files to build/
make demos-clean  # Remove generated WAV files
```

### Synthesis Demos (01-07)

These generate test sounds programmatically and demonstrate the API:

```bash
python demos/01_basic_operations.py   # Buffers, gain, fades, panning, mixing
python demos/02_effects_and_processing.py  # Delay, reverb, modulation, filters
python demos/03_spectral_processing.py     # Blur, time stretch, pitch shift, freeze
python demos/04_granular_synthesis.py      # Brassage, wrappage, grain ops
python demos/05_pitch_synchronous.py       # PSOW, FOF, hover
python demos/06_creative_techniques.py     # Effect chains, recipes
python demos/07_morphing.py                # Morph, glide, cross-synthesis
```

### FX Processing Demos (fx01-fx07)

CLI tools for processing real audio files:

```bash
# Basic usage
python demos/fx01_time_and_pitch.py input.wav -o output_dir/

# All FX demos:
python demos/fx01_time_and_pitch.py input.wav      # Time stretch, pitch shift
python demos/fx02_spectral_effects.py input.wav    # Blur, focus, fold, freeze
python demos/fx03_granular.py input.wav            # Brassage, wrappage, grains
python demos/fx04_reverb_delay_mod.py input.wav    # Reverb, delay, modulation
python demos/fx05_distortion_dynamics.py input.wav # Distortion, filters, dynamics
python demos/fx06_psow_fof.py input.wav            # PSOW, FOF, hover
python demos/fx07_creative_chains.py input.wav     # Complex effect chains
```

Each FX demo generates multiple output files showcasing different parameter settings.

## Development

```bash
# Build
make build

# Run tests
make test

# Lint and format
make lint
make format

# Type check
make typecheck

# Full QA
make qa

# Build wheel
make wheel

# See all targets
make help
```

## Adding New Operations

To add more CDP operations:

1. Add C implementation to `projects/libcdp/cdp_lib/<operation>.c`

2. Add function declarations to appropriate header in `projects/libcdp/cdp_lib/`

3. Export from `projects/libcdp/cdp_lib/cdp_lib.h`

4. Update `CMakeLists.txt` to include new source file

5. Add Cython declarations to `src/cycdp/cdp_lib.pxd`

6. Add Cython bindings to `src/cycdp/_core.pyx`

7. Export from `src/cycdp/__init__.py`

8. Add tests to `tests/`

## License

LGPL-2.1-or-later (same as CDP)
