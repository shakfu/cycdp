# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- Amplitude CLI commands were silently no-ops. The default `-n 0.95` normalization was applied unconditionally, so `gain --gain-factor 0.1` and `--gain-factor 2.0` produced the same output level and `limiter --threshold-db -20` produced a 0.95 peak. Affects `gain`, `gain-db`, `normalize`, `normalize-db`, `limiter`, `compressor`, `gate`, `envelope-follow` and `envelope-apply`; see "Changed" for the new default.

- `phase_invert()` no longer rejects raw float32 buffers. It was defined twice in `_core.pyx` and the second definition silently shadowed the first, so the documented `phase_invert(samples, sample_rate=...)` form raised `TypeError`. Both call styles now work through a single definition.

- Heap buffer overflow segfaulting `time_stretch()` on inputs at or just above `fft_size`. The frame-interpolation clamp in `cdp_spectral_time_stretch` assumed at least two analysis frames and read past the end of the frame array.

- Type stubs (`_core.pyi`) disagreed with the implementation in 60 of 129 functions -- wrong parameter names, arity and defaults. Since the package ships `py.typed`, type checkers rejected correct calls and accepted incorrect ones. Stubs are now generated from `_core.pyx`.

- 38 unchecked allocations in `cdp_analysis.c` and `cdp_reverb.c` that could dereference NULL and crash the interpreter instead of raising.

- Unchecked allocations and a workspace leak on the error path in the vendored FFT (`projects/cpd8/dev/pv/mxfft.c`), which runs once per analysis frame and so affected every spectral operation. Marked with `cycdp patch:` comments for future re-vendoring.

- Duplicate `phase_invert` entry in `__all__`.

### Changed

- CLI output normalization is now per-command. The amplitude commands listed above are exempt by default; all others keep the `0.95` default. Passing `-n/--normalize` explicitly still forces normalization for any command, and `--no-normalize` is unchanged. Scripts relying on amplitude commands emitting a 0.95 peak will see different output levels.

- Coverage now measures the Cython core. It previously reported 94% while measuring only `cli.py`, with `_core.pyx` contributing zero statements. `make coverage` rebuilds with line tracing and reports 2,300 measured statements (`_core.pyx` at 85%, 87% overall).

- CI type-checks all of `src/cycdp/` rather than only `__init__.py`, and lint, format and type-check now cover `scripts/`. Coverage moved out of the `qa` job into a dedicated `coverage` job.

- `make qa` runs `format` first so lint and tests see the formatted tree, and now includes `stubs-check`.

### Added

- `make sanitize` and a matching CI job, building with AddressSanitizer and UndefinedBehaviorSanitizer and running the suite under them. This class of bug is invisible to the ordinary suite, which passes cleanly until a NULL dereference or overflow takes down the interpreter; the `time_stretch` overflow above was found this way.

- `make stubs` regenerates `_core.pyi` from `_core.pyx`; `make stubs-check` fails on stale stubs and runs in CI.

- `tests/test_signatures.py`, asserting that the committed stubs match the compiled module, that no function is defined twice in `_core.pyx`, and that `__all__` has no duplicates. Independent of the generator, so drift fails the suite even if the generator is never run.

- `tests/test_coverage_setup.py`, which fails when coverage runs without the Cython core being measured, so the instrumentation cannot regress back into reporting a number that describes only the pure-Python files.

- Regression tests for the CLI normalization policy, both `phase_invert` call styles, and `time_stretch` on short inputs.

- `CYCDP_COVERAGE` and `CYCDP_SANITIZE` CMake options, both off by default and neither suitable for released wheels.

- `cython` in the dev dependency group, required at test time by `Cython.Coverage` to map `.pyx` lines.

## [0.1.2]

### Added

- Command-line interface accessible via `cycdp` console script and `python3 -m cycdp`

  - All 100+ audio processing functions exposed as flat subcommands (e.g. `cycdp time-stretch`, `cycdp reverb`)

  - Four input modes: single-file processing, dual-file processing (morph, mix), synthesis (no input), and analysis (text/JSON/CSV output)

  - `cycdp list [category]` for category-grouped command discovery

  - `cycdp info <file>` for audio file metadata

  - `cycdp version` for version information

  - Global options: `-o/--output` (file or directory), `-n/--normalize`, `--no-normalize`, `--format`

  - Automatic output naming when `-o` is omitted (`<input_stem>_<command>.wav`)

  - Category-grouped `--help` output via custom formatter

- Type stubs (`_core.pyi`) for IDE autocompletion and type checking

- PyPI publication metadata (URLs, classifiers, keywords)

### Fixed

- Buffer overflow in `cdp_flutter.c` mono-to-stereo conversion causing segfaults

- Variable Length Array (VLA) compatibility for MSVC in `io.c`

- `M_PI` undefined error for MSVC in `utils.c`

- Linux build failure by enabling position-independent code

## [0.1.1]

### Added (Demos)

**Synthesis Demos (01-07)** - Generate test sounds and demonstrate API:

- `01_basic_operations.py` - Buffers, gain, fades, panning, mixing

- `02_effects_and_processing.py` - Delay, reverb, modulation, distortion, filters

- `03_spectral_processing.py` - Blur, focus, time stretch, pitch shift, freeze

- `04_granular_synthesis.py` - Brassage, wrappage, grain manipulation

- `05_pitch_synchronous.py` - PSOW stretch/grab, FOF repitch, hover

- `06_creative_techniques.py` - Effect chains and sound design recipes

- `07_morphing.py` - Spectral morph, glide, cross-synthesis

**FX Processing Demos (fx01-fx07)** - CLI tools for processing real audio:

- `fx01_time_and_pitch.py` - Time stretch, pitch shift, spectral shift

- `fx02_spectral_effects.py` - Blur, focus, fold, freeze effects

- `fx03_granular.py` - Brassage, wrappage, grain operations

- `fx04_reverb_delay_mod.py` - Reverb, delay, tremolo, chorus, flanger, ring mod

- `fx05_distortion_dynamics.py` - Waveset distortion, bitcrush, filters, compression

- `fx06_psow_fof.py` - PSOW stretch/grab, FOF repitch, hover

- `fx07_creative_chains.py` - Complex effect chains (ambient, industrial, shimmer, etc.)

All FX demos accept `input.wav -o output_dir/` arguments.

**Makefile targets:**

- `make demos` - Run all demos, output to `build/`

- `make demos-clean` - Remove generated WAV files

### Added (CDP Algorithm Ports)

**Analysis:**

- `pitch` - pitch tracking using YIN algorithm (CDP: pitch)

- `formants` - formant analysis using LPC (CDP: formants)

- `get_partials` - partial/harmonic tracking (CDP: get_partials)

**Spectral Processing:**

- `spectral_focus` - super-Gaussian frequency enhancement (CDP: focus)

- `spectral_hilite` - boost spectral peaks above threshold (CDP: hilite)

- `spectral_fold` - fold spectrum at frequency for metallic effects (CDP: specfold)

- `spectral_clean` - spectral noise gate (CDP: speclean)

**Experimental/Chaos:**

- `strange` - Lorenz attractor chaotic modulation (CDP: strange)

- `brownian` - random walk modulation of pitch/amp/filter (CDP: brownian)

- `crystal` - crystalline textures with decaying echoes (CDP: crystal)

- `fractal` - recursive wavecycle overlay with pitch ratio and decay (CDP: fractal)

- `quirk` - probabilistic reverse/dropout transformations (CDP: quirk)

- `chirikov` - standard map chaotic modulation (CDP: chirikov)

- `cantor` - Cantor set fractal gating pattern (CDP: cantor)

- `cascade` - cascading echoes with pitch/amp/filter decay (CDP: cascade)

- `fracture` - fragment and scatter audio with gaps (CDP: fracture)

- `tesselate` - tile-based pattern transformations (CDP: tesselate)

**Playback/Time Manipulation:**

- `zigzag` - alternating forward/backward playback through time points (CDP: zigzag)

- `iterate` - repeated playback with pitch/gain variations (CDP: iterate)

- `stutter` - segment-based stuttering with silence inserts (CDP: stutter)

- `bounce` - bouncing ball effect with accelerating repeats (CDP: bounce)

- `drunk` - "drunk walk" random navigation through audio (CDP: drunk)

- `loop` - looping with crossfades and variations (CDP: loop)

- `retime` - time-domain time stretching/compression using TDOLA (CDP: retime)

- `scramble` - waveset reordering (shuffle, reverse, by size/level) (CDP: scramble)

- `splinter` - fragmenting effect with shrinking repeats (CDP: splinter)

- `hover` - zigzag reading at specified frequency for hovering pitch effect (CDP: hover)

- `constrict` - shorten or remove silent sections (CDP: constrict)

- `phase_invert` - invert phase of audio signal (CDP: phase mode 1)

- `phase_stereo` - enhance stereo separation via phase subtraction (CDP: phase mode 2)

- `wrappage` - granular texture with stereo spatial distribution (CDP: wrappage)

**Spatial Effects:**

- `spin` - rotate audio around stereo field with optional doppler (CDP: spin)

- `rotor` - dual-rotation modulation creating interference patterns (CDP: rotor)

- `flutter` - spatial tremolo with loudness modulation alternating L/R (CDP: flutter)

**Extended Granular:**

- `grain_reorder` - reorder detected grains (shuffle, reverse, rotate) (CDP: grain)

- `grain_rerhythm` - change timing/rhythm of grains (CDP: grain)

- `grain_reverse` - reverse individual grains in place (CDP: grain)

- `grain_timewarp` - time-stretch/compress grain spacing (CDP: grain)

- `grain_repitch` - pitch-shift grains with interpolation (CDP: grain)

- `grain_position` - reposition grains in stereo field (CDP: grain)

- `grain_omit` - probabilistically omit grains (CDP: grain)

- `grain_duplicate` - duplicate grains with variations (CDP: grain)

**Granular/Texture:**

- `grain_cloud` - grain cloud generation from amplitude-detected grains (CDP: grain)

- `grain_extend` - extend duration using grain repetition (CDP: grainex extend)

- `texture_simple` - simple texture layering (CDP: texture SIMPLE_TEX)

- `texture_multi` - multi-layer grouped texture (CDP: texture GROUPS)

**Morphing/Cross-synthesis:**

- `morph` - spectral interpolation between two sounds (CDP: SPECMORPH)

- `morph_glide` - simple spectral glide between two sounds (CDP: SPECGLIDE)

- `cross_synth` - combine amp from one sound with freq from another (CDP: combine)

- `morph_glide_native` - native CDP specglide wrapper (original algorithm)

- `morph_bridge_native` - native CDP specbridge wrapper (original algorithm)

- `morph_native` - native CDP specmorph wrapper (original algorithm)

**Synthesis:**

- `synth_wave` - waveform synthesis (sine, square, saw, ramp, triangle) (CDP: wave)

- `synth_noise` - noise generation (white, pink) (CDP: synth noise)

- `synth_click` - click/metronome track generation (CDP: click)

- `synth_chord` - chord synthesis from MIDI pitch list (CDP: multi_syn)

**Pitch-Synchronous Operations (PSOW):**

- `psow_stretch` - time-stretch while preserving pitch using PSOLA (CDP: psow stretch)

- `psow_grab` - extract pitch-synchronous grains from a position (CDP: psow grab)

- `psow_dupl` - duplicate grains for time-stretching (CDP: psow dupl)

- `psow_interp` - interpolate between two grains (CDP: psow interp)

**FOF Extraction and Synthesis (FOFEX):**

- `fofex_extract` - extract single FOF at specified time (CDP: fofex)

- `fofex_extract_all` - extract all FOFs to uniform-length bank (CDP: fofex)

- `fofex_synth` - synthesize audio from FOFs at target pitch (CDP: fofex)

- `fofex_repitch` - repitch audio with optional formant preservation (CDP: fofex)

**Distortion:**

- `distort_cut` - waveset segmentation with decaying envelope (CDP: distcut)

- `distort_mark` - interpolate between waveset groups at time markers (CDP: distmark)

- `distort_repeat` - time-stretch by repeating wavecycles (CDP: distrep)

- `distort_shift` - shift/swap half-wavecycle groups (CDP: distshift)

- `distort_warp` - progressive warp distortion with modular sample folding (CDP: distwarp)

**Filtering:**

- `filter_bandpass` - spectral bandpass filter

- `filter_notch` - spectral notch (band-reject) filter

### Added (Non-CDP Additions)

Standard DSP functions not derived from CDP algorithms:

**EQ:**

- `eq_parametric` - parametric equalizer with center frequency, gain, and Q factor

**Dynamics:**

- `gate` - noise gate with attack/release/hold envelope

- `compressor` - dynamic range compression with threshold, ratio, attack/release

- `limiter` - peak limiting with attack/release

- `envelope_follow` - extract amplitude envelope (peak or RMS mode)

- `envelope_apply` - apply envelope to sound with depth control

**Effects:**

- `bitcrush` - bit depth and sample rate reduction

- `ring_mod` - ring modulation with carrier frequency

- `delay` - feedback delay with mix control

- `chorus` - modulated delay (LFO-based)

- `flanger` - short modulated delay with feedback

### Added (Constants)

- Waveform types: `WAVE_SINE`, `WAVE_SQUARE`, `WAVE_SAW`, `WAVE_RAMP`, `WAVE_TRIANGLE`

- Scramble modes: `SCRAMBLE_SHUFFLE`, `SCRAMBLE_REVERSE`, `SCRAMBLE_SIZE_UP`, `SCRAMBLE_SIZE_DOWN`, `SCRAMBLE_LEVEL_UP`, `SCRAMBLE_LEVEL_DOWN`

### Fixed

- Phase vocoder frequency calculation now uses correct hop size

- Spectral filters now use bin center frequency for accurate filtering

## [0.1.0]

### Added

- Native CDP library integration (no subprocess overhead)

- **Spectral processing:** `time_stretch`, `spectral_blur`, `modify_speed`, `pitch_shift`, `spectral_shift`, `spectral_stretch`, `filter_lowpass`, `filter_highpass`

- **Envelope operations:** `dovetail`, `tremolo`, `attack`

- **Distortion:** `distort_overload`, `distort_reverse`, `distort_fractal`, `distort_shuffle`

- **Reverb:** `reverb` - FDN reverb (8 comb + 4 allpass filters)

- **Granular:** `brassage`, `freeze`

- **Core operations:** `gain`, `gain_db`, `normalize`, `normalize_db`, `phase_invert`

- **Spatial:** `pan`, `pan_envelope`, `mirror`, `narrow`

- **Mixing:** `mix`, `mix2`

- **Buffer utilities:** `reverse`, `fade_in`, `fade_out`, `concat`

- **Channel operations:** `to_mono`, `to_stereo`, `extract_channel`, `merge_channels`, `split_channels`, `interleave`

- **File I/O:** `read_file`, `write_file` (WAV: float, PCM16, PCM24)

- Cython bindings with zero-copy buffer protocol support

- Comprehensive test suite
