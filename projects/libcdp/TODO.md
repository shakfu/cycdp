# CDP Native Library Integration TODO

This document tracks CDP algorithms to be integrated as native library functions (no subprocess overhead).

**Important:** Future development should focus on porting actual CDP algorithms. Non-CDP additions are welcome but should be clearly marked as such.

## Current Status

### Completed: CDP Algorithm Ports

Native implementations of actual CDP algorithms (replacing subprocess calls).

**Spectral Processing (CDP: `blur`, `stretch`, `pvoc`, `spec*`):**

- [x] `time_stretch` - Phase vocoder time stretch

- [x] `spectral_blur` - Spectral averaging/smearing

- [x] `modify_speed` - Playback speed change (affects pitch)

- [x] `pitch_shift` - Pitch shift without changing duration

- [x] `spectral_shift` - Shift all frequencies by Hz offset

- [x] `spectral_stretch` - Differential frequency stretching

**Morphing/Cross-synthesis (CDP: `morph`, `combine`):**

- [x] `morph` - Spectral interpolation between two sounds

- [x] `morph_glide` - Simple spectral glide between two sounds

- [x] `cross_synth` - Cross-synthesis (amp from one, freq from other)

- [x] `morph_glide_native` - Port of CDP specglide (dev/morph/morph.c)

- [x] `morph_bridge_native` - Port of CDP specbridge (dev/morph/morph.c)

- [x] `morph_native` - Port of CDP specmorph (dev/morph/morph.c)

**Filtering (CDP: `filter`, `filtrage`, `synfilt`):**

- [x] `filter_lowpass` - Spectral lowpass filter

- [x] `filter_highpass` - Spectral highpass filter

- [x] `filter_bandpass` - Spectral bandpass filter

- [x] `filter_notch` - Spectral notch (band-reject) filter

- [x] `spectral_focus` - Super-Gaussian frequency enhancement

- [x] `spectral_hilite` - Boost spectral peaks

- [x] `spectral_fold` - Fold spectrum at frequency (metallic)

- [x] `spectral_clean` - Spectral noise gate

**Analysis (CDP: `pitch`, `formants`, `get_partials`):**

- [x] `pitch` - Pitch tracking (YIN algorithm)

- [x] `formants` - Formant analysis (LPC)

- [x] `get_partials` - Partial tracking

**Experimental (CDP: `strange`, `brownian`, `crystal`, `fractal`, `quirk`, etc.):**

- [x] `strange` - Lorenz attractor chaotic modulation

- [x] `brownian` - Random walk modulation (pitch/amp/filter)

- [x] `crystal` - Crystalline textures with decaying echoes

- [x] `fractal` - Recursive wavecycle overlay with pitch ratio and decay

- [x] `quirk` - Probabilistic reverse/dropout transformations

- [x] `chirikov` - Standard map chaotic modulation

- [x] `cantor` - Cantor set fractal gating pattern

- [x] `cascade` - Cascading echoes with pitch/amp/filter decay

- [x] `fracture` - Fragment and scatter audio with gaps

- [x] `tesselate` - Tile-based pattern transformations

**Playback/Time Manipulation (CDP: `extend`, `retime`):**

- [x] `zigzag` - Zigzag playback (alternating forward/backward through audio)

- [x] `iterate` - Iteration with variations (repeated playback with pitch/gain changes)

- [x] `stutter` - Stutter effect (segment repetition with silence inserts)

- [x] `bounce` - Bouncing ball effect (accelerating/decelerating repeats)

- [x] `drunk` - "Drunk walk" random navigation through audio

- [x] `loop` - Looping with crossfades and variations

- [x] `retime` - Time-domain time stretching/compression (TDOLA)

- [x] `scramble` - Waveset scrambling/reordering (shuffle, reverse, by size/level)

- [x] `splinter` - Waveset splintering/fragmentation effects

**Envelope (CDP: `envel`, `tremolo`, `envcut`):**

- [x] `dovetail` - Fade in/out envelopes (linear/exponential)

- [x] `tremolo` - LFO amplitude modulation

- [x] `attack` - Attack transient reshaping

**Distortion (CDP: `distort`, `distortt`, `distcut`, etc.):**

- [x] `distort_overload` - Soft/hard clipping

- [x] `distort_reverse` - Reverse wavecycles (zero-crossing based)

- [x] `distort_fractal` - Recursive wavecycle overlay

- [x] `distort_shuffle` - Segment rearrangement

- [x] `distort_cut` - Waveset segmentation with decaying envelope

- [x] `distort_mark` - Interpolate between waveset groups at time markers

- [x] `distort_repeat` - Time-stretch by repeating wavecycles

- [x] `distort_shift` - Shift/swap half-wavecycle groups

- [x] `distort_warp` - Progressive warp distortion with modular sample folding

**Reverb & Spatial (CDP: `reverb`, `rmverb`, `panorama`, `spin`, `rotor`):**

- [x] `reverb` - FDN reverb (8 comb + 4 allpass filters)

- [x] `spin` - Spatial spinning (rotating stereo position with doppler)

- [x] `rotor` - Dual-rotation modulation (pitch + amplitude interference patterns)

**Synthesis (CDP: `synth`, `wave`):**

- [x] `synth_wave` - Waveform synthesis (sine, square, saw, ramp, triangle)

- [x] `synth_noise` - Noise generation (white, pink)

- [x] `synth_click` - Click track generation

- [x] `synth_chord` - Chord synthesis from MIDI pitch list

**Granular (CDP: `brassage`, `grain`, `texture`):**

- [x] `brassage` - Granular resynthesis with pitch/time params

- [x] `freeze` - Segment repetition with crossfade

- [x] `grain_cloud` - Grain cloud generation from amplitude-detected grains

- [x] `grain_extend` - Extend duration using grain repetition

- [x] `texture_simple` - Simple texture layering with pitch/amp/spatial variation

- [x] `texture_multi` - Multi-layer grouped texture generation

**Extended Granular (CDP: `grain` modes):**

- [x] `grain_reorder` - Reorder detected grains (shuffle, reverse, rotate)

- [x] `grain_rerhythm` - Change timing/rhythm of grains

- [x] `grain_reverse` - Reverse individual grains in place

- [x] `grain_timewarp` - Time-stretch/compress grain spacing

- [x] `grain_repitch` - Pitch-shift grains with interpolation

- [x] `grain_position` - Reposition grains in stereo field

- [x] `grain_omit` - Probabilistically omit grains

- [x] `grain_duplicate` - Duplicate grains with variations

**Core (from original libcdp):**

- [x] `gain`, `gain_db` - Amplitude adjustment

- [x] `normalize`, `normalize_db` - Peak normalization

- [x] `phase_invert` - Phase inversion

- [x] `pan`, `pan_envelope` - Stereo panning

- [x] `mirror`, `narrow` - Stereo field manipulation

- [x] `mix`, `mix2` - Audio mixing

- [x] `reverse`, `fade_in`, `fade_out`, `concat` - Buffer utilities

- [x] `to_mono`, `to_stereo`, `extract_channel`, `merge_channels`, `split_channels`, `interleave` - Channel operations

- [x] `read_file`, `write_file` - WAV I/O (float, PCM16, PCM24)

### Completed: Non-CDP Additions

Useful audio processing functions not derived from CDP algorithms.

**Dynamics (standard DSP, not in CDP):**

- [x] `gate` - Noise gate with attack/release/hold

- [x] `compressor` - Dynamic range compression

- [x] `limiter` - Hard/soft limiting

- [x] `envelope_follow` - Extract amplitude envelope (RMS/peak tracking)

- [x] `envelope_apply` - Apply envelope to sound

**EQ (standard DSP):**

- [x] `eq_parametric` - Parametric EQ with Q factor

**Effects (standard DSP):**

- [x] `bitcrush` - Bit depth and sample rate reduction

- [x] `ring_mod` - Ring modulation (carrier multiply)

- [x] `delay` - Feedback delay with mix control

- [x] `chorus` - Modulated delay (LFO-based)

- [x] `flanger` - Short modulated delay with feedback

---

## Future Priorities: CDP Algorithm Ports

Focus on actual CDP algorithms. Reference the CDP executable list at the bottom.

### ~~Priority 1: Analysis (CDP: `pitch`, `formants`, `get_partials`)~~ DONE

- [x] `pitch` - Pitch tracking (YIN algorithm)

- [x] `formants` - Formant analysis (LPC)

- [x] `get_partials` - Partial tracking (peak tracking)

### ~~Priority 2: Additional Spectral (CDP: `focus`, `hilite`, `specfold`)~~ DONE

- [x] `spectral_focus` - Enhance frequencies with super-Gaussian curve

- [x] `spectral_hilite` - Boost spectral peaks above threshold

- [x] `spectral_fold` - Fold spectrum at frequency (metallic effects)

- [x] `spectral_clean` - Spectral noise gate

### ~~Priority 3: Experimental (CDP: `strange`, `brownian`, `crystal`)~~ DONE

- [x] `strange` - Lorenz attractor chaotic modulation

- [x] `brownian` - Random walk modulation (pitch/amp/filter)

- [x] `crystal` - Crystalline textures with decaying echoes

### ~~Priority 4: Additional Experimental (CDP: `fractal`, `quirk`, etc.)~~ DONE

- [x] `fractal` - Recursive wavecycle overlay with pitch ratio and decay

- [x] `quirk` - Probabilistic reverse/dropout transformations

- [x] `chirikov` - Standard map chaotic modulation

- [x] `cantor` - Cantor set fractal gating pattern

- [x] `cascade` - Cascading echoes with pitch/amp/filter decay

- [x] `fracture` - Fragment and scatter audio with gaps

- [x] `tesselate` - Tile-based pattern transformations

### ~~Priority 5: Time/Playback Manipulation (CDP: `extend`, `retime`)~~ DONE

From `dev/extend/` and `dev/standalone/`:

- [x] `zigzag` - Zigzag playback (alternating forward/backward through audio)

- [x] `iterate` - Iteration with variations (repeated playback with pitch/gain changes)

- [x] `drunk` - "Drunk walk" random navigation through audio

- [x] `retime` - Time-domain time stretching/compression (TDOLA with crossfades)

- [x] `loop` - Looping with crossfades and variations

### ~~Priority 6: Segment Manipulation (CDP: `scramble`, `stutter`)~~ DONE

From `dev/science/` and `dev/standalone/`:

- [x] `stutter` - Stutter effect (segment repetition with silence inserts)

- [x] `scramble` - Waveset scrambling/reordering (shuffle, reverse, by size/level)

- [x] `bounce` - Bouncing ball effect (accelerating/decelerating repeats)

- [x] `splinter` - Waveset splintering/fragmentation effects

### ~~Priority 7: Spatial Effects (CDP: `spin`, `rotor`)~~ DONE

From `dev/science/`:

- [x] `spin` - Spatial spinning (rotating stereo position)

- [x] `rotor` - Dual-rotation modulation (pitch + amplitude interference patterns)

### ~~Priority 8: Synthesis (CDP: `synth`, `wave`)~~ DONE

From `dev/synth/`:

- [x] `synth_wave` - Waveform synthesis (sine, square, saw, ramp, triangle)

- [x] `synth_noise` - Noise generation (white, pink)

- [x] `synth_click` - Click track generation

- [x] `synth_chord` - Chord synthesis from pitch list

### Priority 9: Additional Distortion (CDP: `distcut`, `distmark`, `distrep`)

From `dev/standnew/`:

- [x] `distort_cut` - Cut-based distortion (waveset segmentation with decaying envelope)

- [x] `distort_mark` - Marker-based distortion (interpolate between waveset groups at time markers)

- [x] `distort_repeat` - Repetition-based distortion (time-stretch by repeating wavecycles)

- [x] `distort_shift` - Shift/swap half-wavecycle groups for phase distortion

- [x] `distort_warp` - Progressive warp distortion with modular sample folding

### Priority 10: Pitch-Synchronous Operations (CDP: `psow`, `fofex`)

From `dev/standalone/`:

- [x] `psow_stretch` - Time-stretch while preserving pitch using PSOLA

- [x] `psow_grab` - Extract pitch-synchronous grains from a position

- [x] `psow_dupl` - Duplicate grains for time-stretching

- [x] `psow_interp` - Interpolate between two grains

- [x] `fofex_extract` - Extract single FOF at specified time

- [x] `fofex_extract_all` - Extract all FOFs to uniform-length bank

- [x] `fofex_synth` - Synthesize audio from FOFs at target pitch

- [x] `fofex_repitch` - Repitch audio with optional formant preservation

### Priority 11: Additional Pitch-Synchronous (CDP: `flutter`, `hover`, etc.)

From `dev/standalone/`:

- [x] `flutter` - Spatial tremolo (loudness tremulation distributed across channels)

- [x] `hover` - Zigzag reading at specified frequency for hovering pitch effect

- [x] `constrict` - Silence constriction (shorten/remove silent sections)

- [x] `phase` - Phase manipulation (invert + stereo enhancement)

- [x] `wrappage` - Granular texture with stereo spatial distribution

---

## Deferred: Composition System

The CDP texture algorithms in `dev/texture/` are fundamentally different from audio processing - they are an **algorithmic composition system** that generates MIDI-like note event sequences rather than processing audio directly.

### What It Does

- Generates note events (pitch, duration, timing, amplitude, instrument)

- Uses harmonic sets/fields to constrain pitches to scales/chords

- Supports motifs, decorations, ornaments, and grouped events

- Outputs note lists, not audio buffers

### Modes Available

- Simple texture (random notes within constraints)

- Clumped textures: `IS_GROUPS`, `IS_DECOR`, `IS_MOTIFS`, `IS_ORNATE`

- Harmonic field textures: `do_simple_hftexture`, `do_clumped_hftexture`

### Implementation Approach

Would require a separate subsystem:

1. New data structures for note events, motifs, harmonic sets

2. Composition engine separate from audio processing

3. Renderer to convert note events to audio (sample playback or synthesis)

### Files

- `dev/texture/texture1.c` - Core texture generation

- `dev/texture/texture2.c` - `do_texture()` main function

- `dev/texture/texture3.c` - Harmonic field textures

- `dev/texture/texture4.c` - Support functions

- `dev/texture/texture5.c` - Additional modes

**Status:** Deferred - requires significant architectural work for a composition subsystem.

---

## Implementation Notes

### Architecture

Each native function should:

1. Accept `cdp_lib_buffer*` for input/output

2. Return newly allocated buffer (caller frees)

3. Set error message in context on failure

4. Be thread-safe (no global state)

### FFT Infrastructure

We have `fft_()` from `mxfft.c` available. For spectral operations:

- Use `cdp_spectral_analyze()` for STFT

- Use `cdp_spectral_synthesize()` for inverse STFT

- Extend `cdp_spectral_data` structure as needed

### Testing

Each new algorithm needs:

1. C unit test in `test_cdp_lib.c`

2. Python test in `tests/test_pycdp.py`

### Cython Bindings

For each C function:

1. Add declaration to `cdp_lib.pxd`

2. Add wrapper function to `_core.pyx`

3. Export in `__init__.py`

### Porting CDP Algorithms

When porting a CDP algorithm:

1. Study the original CDP source code

2. Understand the algorithm's parameters and behavior

3. Implement following the architecture above

4. Test against known CDP outputs where possible

---

## CDP Executable Reference (220+ total)

### Spectral Processing

`blur`, `focus`, `hilite`, `spec`, `specanal`, `specav`, `specenv`, `specfnu`, `specfold`, `specgrids`, `speclean`, `specnu`, `specross`, `specsphinx`, `spectrum`, `spectstr`, `spectune`, `spectwin`, `speculate`, `specvu`

### Time/Pitch Modification

`modify`, `stretch`, `stretcha`, `pvoc`, `repitch`, `pitch`, `pmodify`, `retime`, `ts`, `strans`

### Time/Playback (dev/extend/)

`zigzag`, `iterate`, `drunk`, `loop`, `scramble`

### Filtering

`filter`, `filtrage`, `synfilt`, `notchinvert`

### Distortion

`distort`, `distortt`, `distcut`, `distmark`, `distmore`, `distrep`, `distshift`, `distwarp`

### Envelope

`envel`, `envcut`, `envnu`, `envspeak`, `tremolo`, `tremenv`

### Granular/Texture

`grain`, `grainex`, `texture`, `newtex`, `texmchan`, `brassage`

### Spatial

`reverb`, `rmverb`, `mchanrev`, `panorama`, `abfpan`, `abfpan2`, `mchanpan`, `spin`, `rotor`

### Synthesis

`synth`, `newsynth`, `multisynth`, `multiosc`, `phasor`, `impulse`, `waveform`

### Morphing/Combining

`morph`, `newmorph`, `combine`, `submix`, `multimix`, `newmix`, `nmix`

### Analysis

`pitch`, `pitchinfo`, `formants`, `get_partials`, `features`, `onset`, `peak`, `peakfind`, `rmsinfo`, `sndinfo`, `specinfo`

### Utilities

`housekeep`, `sfedit`, `sfprops`, `channelx`, `tostereo`, `mton`

### Experimental/Advanced (dev/science/)

`strange`, `fractal`, `frfractal`, `quirk`, `crystal`, `brownian`, `chirikov`, `cantor`, `cascade`, `fracture`, `tesselate`, `stutter`, `scramble`, `bounce`, `splinter`, `crumble`, `strands`, `tweet`

### Pitch-Synchronous (dev/standalone/)

`psow`, `fofex`, `flutter`, `hover`, `constrict`, `phase`, `wrappage`
