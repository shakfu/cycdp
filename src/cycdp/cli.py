"""CLI for cycdp -- exposes all audio processing functions as subcommands.

Usage:
    cycdp time-stretch input.wav --factor 2.0 -o output.wav
    cycdp reverb input.wav --decay-time 2.0 --mix 0.5
    cycdp list spectral
    python3 -m cycdp version
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import cycdp

# =============================================================================
# Sentinel for required parameters
# =============================================================================

REQUIRED = object()

# =============================================================================
# Constant mappings (CLI string -> library int constant)
# =============================================================================

WAVEFORM_MAP = {
    "sine": cycdp.WAVE_SINE,
    "square": cycdp.WAVE_SQUARE,
    "saw": cycdp.WAVE_SAW,
    "ramp": cycdp.WAVE_RAMP,
    "triangle": cycdp.WAVE_TRIANGLE,
}

SCRAMBLE_MAP = {
    "shuffle": cycdp.SCRAMBLE_SHUFFLE,
    "reverse": cycdp.SCRAMBLE_REVERSE,
    "size-up": cycdp.SCRAMBLE_SIZE_UP,
    "size-down": cycdp.SCRAMBLE_SIZE_DOWN,
    "level-up": cycdp.SCRAMBLE_LEVEL_UP,
    "level-down": cycdp.SCRAMBLE_LEVEL_DOWN,
}

# =============================================================================
# Categories
# =============================================================================

CATEGORIES = {
    "spectral": "Spectral processing (FFT-based time/pitch/frequency manipulation)",
    "filter": "Frequency-domain filters",
    "effect": "Effects (reverb, delay, chorus, flanger, ring mod, bitcrush)",
    "dynamics": "Dynamics and EQ (compressor, limiter, gate, parametric EQ)",
    "distortion": "Waveset-based distortion",
    "granular": "Granular synthesis and texture generation",
    "envelope": "Envelope shaping (dovetail, tremolo, attack)",
    "playback": "Playback and time manipulation",
    "spatial": "Spatial processing and panning",
    "phase": "Phase manipulation",
    "experimental": "Experimental / chaos transformations",
    "psow": "Pitch-synchronous operations (PSOLA)",
    "fofex": "FOF extraction and synthesis",
    "morph": "Morphing and cross-synthesis",
    "synth": "Synthesis (waveforms, noise, clicks, chords)",
    "analyze": "Analysis (pitch, formants, partials)",
    "gain": "Gain, normalization, and peak detection",
    "buffer": "Buffer utilities (reverse, fade, concat)",
    "channel": "Channel operations (mono/stereo conversion, split, merge)",
    "mix": "Mixing",
}

# =============================================================================
# Command registry
# =============================================================================
#
# Each entry maps a CLI command name (hyphens) to:
#   func     - Python function name (underscores) on the cycdp module
#   category - key into CATEGORIES
#   input    - "single" | "dual" | "synth" | "analysis"
#   help     - one-line description
#   params   - {cli-flag: (type, default_or_REQUIRED, help_text)}
#
# Special type markers:
#   "json"       - parsed as JSON string (for list/tuple params)
#   "nargs+"     - list of ints via nargs='+'
#   "waveform"   - string mapped via WAVEFORM_MAP
#   "scramble"   - string mapped via SCRAMBLE_MAP
#   "bool"       - boolean flag (store_true)
#
# Parameter names and defaults are matched exactly to the Cython source
# (_core.pyx). The test suite asserts this agreement (see
# tests/test_signatures.py), so drift fails CI rather than reaching users.
#
# Commands listed in NO_AUTO_NORMALIZE are exempt from the default output
# normalization, because for those the output level is the operation itself
# and normalizing would silently discard the user's parameters.

# Amplitude operations: normalizing their output to a fixed peak would undo
# exactly what the command was asked to do. These still honour an explicit
# -n/--normalize, they just do not get it by default.
NO_AUTO_NORMALIZE = frozenset(
    {
        "gain",
        "gain-db",
        "normalize",
        "normalize-db",
        "limiter",
        "compressor",
        "gate",
        "envelope-follow",
        "envelope-apply",
    }
)

COMMANDS: dict[str, dict[str, Any]] = {
    # -- spectral --
    "time-stretch": {
        "func": "time_stretch",
        "category": "spectral",
        "input": "single",
        "help": "Time-stretch without changing pitch (phase vocoder)",
        "params": {
            "factor": (float, REQUIRED, "Stretch factor (2.0 = double duration)"),
            "fft-size": (int, 1024, "FFT window size"),
            "overlap": (int, 3, "Overlap factor"),
        },
    },
    "spectral-blur": {
        "func": "spectral_blur",
        "category": "spectral",
        "input": "single",
        "help": "Blur/smear the spectrum over time",
        "params": {
            "blur-time": (float, REQUIRED, "Blur duration in seconds"),
            "fft-size": (int, 1024, "FFT window size"),
        },
    },
    "modify-speed": {
        "func": "modify_speed",
        "category": "spectral",
        "input": "single",
        "help": "Change playback speed (affects both time and pitch)",
        "params": {
            "speed-factor": (float, REQUIRED, "Speed factor (2.0 = double speed)"),
        },
    },
    "pitch-shift": {
        "func": "pitch_shift",
        "category": "spectral",
        "input": "single",
        "help": "Shift pitch without changing duration",
        "params": {
            "semitones": (float, REQUIRED, "Pitch shift in semitones"),
            "fft-size": (int, 1024, "FFT window size"),
        },
    },
    "spectral-shift": {
        "func": "spectral_shift",
        "category": "spectral",
        "input": "single",
        "help": "Shift spectrum up/down by a fixed frequency",
        "params": {
            "shift-hz": (float, REQUIRED, "Frequency shift in Hz"),
            "fft-size": (int, 1024, "FFT window size"),
        },
    },
    "spectral-stretch": {
        "func": "spectral_stretch",
        "category": "spectral",
        "input": "single",
        "help": "Stretch or compress the frequency spectrum",
        "params": {
            "max-stretch": (float, REQUIRED, "Maximum stretch factor"),
            "freq-divide": (float, 1000.0, "Frequency divide point in Hz"),
            "exponent": (float, 1.0, "Stretch exponent"),
            "fft-size": (int, 1024, "FFT window size"),
        },
    },
    "spectral-focus": {
        "func": "spectral_focus",
        "category": "spectral",
        "input": "single",
        "help": "Focus on a frequency region",
        "params": {
            "center-freq": (float, 1000.0, "Center frequency in Hz"),
            "bandwidth": (float, 200.0, "Bandwidth in Hz"),
            "gain-db": (float, 6.0, "Gain for focused region in dB"),
            "fft-size": (int, 1024, "FFT window size"),
        },
    },
    "spectral-hilite": {
        "func": "spectral_hilite",
        "category": "spectral",
        "input": "single",
        "help": "Highlight spectral peaks above threshold",
        "params": {
            "threshold-db": (float, -20.0, "Threshold in dB"),
            "boost-db": (float, 6.0, "Boost for highlighted peaks in dB"),
            "fft-size": (int, 1024, "FFT window size"),
        },
    },
    "spectral-fold": {
        "func": "spectral_fold",
        "category": "spectral",
        "input": "single",
        "help": "Fold spectrum around a frequency",
        "params": {
            "fold-freq": (float, 2000.0, "Fold frequency in Hz"),
            "fft-size": (int, 1024, "FFT window size"),
        },
    },
    "spectral-clean": {
        "func": "spectral_clean",
        "category": "spectral",
        "input": "single",
        "help": "Remove spectral noise below threshold",
        "params": {
            "threshold-db": (float, -40.0, "Noise threshold in dB"),
            "fft-size": (int, 1024, "FFT window size"),
        },
    },
    # -- filter --
    "filter-lowpass": {
        "func": "filter_lowpass",
        "category": "filter",
        "input": "single",
        "help": "Apply low-pass filter",
        "params": {
            "cutoff-freq": (float, REQUIRED, "Cutoff frequency in Hz"),
            "attenuation-db": (float, -60.0, "Attenuation in dB"),
            "fft-size": (int, 1024, "FFT window size"),
        },
    },
    "filter-highpass": {
        "func": "filter_highpass",
        "category": "filter",
        "input": "single",
        "help": "Apply high-pass filter",
        "params": {
            "cutoff-freq": (float, REQUIRED, "Cutoff frequency in Hz"),
            "attenuation-db": (float, -60.0, "Attenuation in dB"),
            "fft-size": (int, 1024, "FFT window size"),
        },
    },
    "filter-bandpass": {
        "func": "filter_bandpass",
        "category": "filter",
        "input": "single",
        "help": "Apply band-pass filter",
        "params": {
            "low-freq": (float, REQUIRED, "Low cutoff frequency in Hz"),
            "high-freq": (float, REQUIRED, "High cutoff frequency in Hz"),
            "attenuation-db": (float, -60.0, "Attenuation in dB"),
            "fft-size": (int, 1024, "FFT window size"),
        },
    },
    "filter-notch": {
        "func": "filter_notch",
        "category": "filter",
        "input": "single",
        "help": "Apply notch (band-reject) filter",
        "params": {
            "center-freq": (float, REQUIRED, "Center frequency in Hz"),
            "width-hz": (float, REQUIRED, "Notch width in Hz"),
            "fft-size": (int, 1024, "FFT window size"),
        },
    },
    # -- effect --
    "reverb": {
        "func": "reverb",
        "category": "effect",
        "input": "single",
        "help": "Apply reverb (FDN: 8 comb + 4 allpass)",
        "params": {
            "mix": (float, 0.5, "Dry/wet mix (0=dry, 1=wet)"),
            "decay-time": (float, 2.0, "Decay time in seconds"),
            "damping": (float, 0.5, "High-frequency damping (0-1)"),
            "lpfreq": (float, 8000.0, "Low-pass filter frequency in Hz"),
            "predelay": (float, 0.0, "Pre-delay in seconds"),
        },
    },
    "delay": {
        "func": "delay",
        "category": "effect",
        "input": "single",
        "help": "Apply delay effect",
        "params": {
            "delay-ms": (float, REQUIRED, "Delay time in milliseconds"),
            "feedback": (float, 0.3, "Feedback amount (0-1)"),
            "mix": (float, 0.5, "Dry/wet mix (0=dry, 1=wet)"),
        },
    },
    "chorus": {
        "func": "chorus",
        "category": "effect",
        "input": "single",
        "help": "Apply chorus effect",
        "params": {
            "rate": (float, 1.5, "Modulation rate in Hz"),
            "depth-ms": (float, 5.0, "Modulation depth in ms"),
            "mix": (float, 0.5, "Dry/wet mix (0=dry, 1=wet)"),
        },
    },
    "flanger": {
        "func": "flanger",
        "category": "effect",
        "input": "single",
        "help": "Apply flanger effect",
        "params": {
            "rate": (float, 0.5, "Modulation rate in Hz"),
            "depth-ms": (float, 3.0, "Modulation depth in ms"),
            "feedback": (float, 0.5, "Feedback amount"),
            "mix": (float, 0.5, "Dry/wet mix (0=dry, 1=wet)"),
        },
    },
    "ring-mod": {
        "func": "ring_mod",
        "category": "effect",
        "input": "single",
        "help": "Apply ring modulation",
        "params": {
            "freq": (float, REQUIRED, "Modulation frequency in Hz"),
            "mix": (float, 1.0, "Dry/wet mix (0=dry, 1=wet)"),
        },
    },
    "bitcrush": {
        "func": "bitcrush",
        "category": "effect",
        "input": "single",
        "help": "Apply bit depth reduction and downsampling",
        "params": {
            "bit-depth": (int, 8, "Bit depth (1-16)"),
            "downsample": (int, 1, "Downsample factor"),
        },
    },
    # -- dynamics --
    "gate": {
        "func": "gate",
        "category": "dynamics",
        "input": "single",
        "help": "Apply noise gate",
        "params": {
            "threshold-db": (float, REQUIRED, "Gate threshold in dB"),
            "attack-ms": (float, 1.0, "Attack time in ms"),
            "release-ms": (float, 50.0, "Release time in ms"),
            "hold-ms": (float, 10.0, "Hold time in ms"),
        },
    },
    "compressor": {
        "func": "compressor",
        "category": "dynamics",
        "input": "single",
        "help": "Apply dynamic range compression",
        "params": {
            "threshold-db": (float, -20.0, "Threshold in dB"),
            "ratio": (float, 4.0, "Compression ratio"),
            "attack-ms": (float, 10.0, "Attack time in ms"),
            "release-ms": (float, 100.0, "Release time in ms"),
            "makeup-gain-db": (float, 0.0, "Makeup gain in dB"),
        },
    },
    "limiter": {
        "func": "limiter",
        "category": "dynamics",
        "input": "single",
        "help": "Apply peak limiting",
        "params": {
            "threshold-db": (float, -0.1, "Threshold in dB"),
            "attack-ms": (float, 0.0, "Attack time in ms"),
            "release-ms": (float, 50.0, "Release time in ms"),
        },
    },
    "eq-parametric": {
        "func": "eq_parametric",
        "category": "dynamics",
        "input": "single",
        "help": "Apply parametric EQ band",
        "params": {
            "center-freq": (float, REQUIRED, "Center frequency in Hz"),
            "gain-db": (float, REQUIRED, "Gain in dB"),
            "q": (float, 1.0, "Q factor"),
            "fft-size": (int, 1024, "FFT window size"),
        },
    },
    "envelope-follow": {
        "func": "envelope_follow",
        "category": "dynamics",
        "input": "single",
        "help": "Extract amplitude envelope from audio",
        "params": {
            "attack-ms": (float, 10.0, "Attack time in ms"),
            "release-ms": (float, 100.0, "Release time in ms"),
            "mode": (str, "peak", "Detection mode: peak or rms"),
        },
    },
    "constrict": {
        "func": "constrict",
        "category": "dynamics",
        "input": "single",
        "help": "Shorten or remove silent sections",
        "params": {
            "constriction": (float, 50.0, "Constriction amount in ms"),
        },
    },
    # -- distortion --
    "distort-overload": {
        "func": "distort_overload",
        "category": "distortion",
        "input": "single",
        "help": "Apply overload/saturation distortion",
        "params": {
            "clip-level": (float, REQUIRED, "Clip level (0-1)"),
            "depth": (float, 0.5, "Distortion depth"),
        },
    },
    "distort-reverse": {
        "func": "distort_reverse",
        "category": "distortion",
        "input": "single",
        "help": "Reverse individual wavecycles",
        "params": {
            "cycle-count": (int, REQUIRED, "Number of cycles per group"),
        },
    },
    "distort-fractal": {
        "func": "distort_fractal",
        "category": "distortion",
        "input": "single",
        "help": "Apply fractal distortion",
        "params": {
            "scaling": (float, REQUIRED, "Fractal scaling factor"),
            "loudness": (float, 1.0, "Output loudness"),
        },
    },
    "distort-shuffle": {
        "func": "distort_shuffle",
        "category": "distortion",
        "input": "single",
        "help": "Shuffle wavecycle chunks",
        "params": {
            "chunk-count": (int, REQUIRED, "Number of chunks"),
            "seed": (int, 0, "Random seed"),
        },
    },
    "distort-cut": {
        "func": "distort_cut",
        "category": "distortion",
        "input": "single",
        "help": "Waveset cut with decaying envelope",
        "params": {
            "cycle-count": (int, 4, "Cycles per cut group"),
            "cycle-step": (int, 4, "Step between groups"),
            "exponent": (float, 1.0, "Envelope exponent"),
            "min-level": (float, 0.0, "Minimum level"),
        },
    },
    "distort-mark": {
        "func": "distort_mark",
        "category": "distortion",
        "input": "single",
        "help": "Interpolate wavesets at time markers",
        "params": {
            "markers": ("json", REQUIRED, "Time markers as JSON list of floats"),
            "unit-ms": (float, 10.0, "Unit time in ms"),
            "stretch": (float, 1.0, "Time stretch factor"),
            "random": (float, 0.0, "Randomization amount"),
            "seed": (int, 0, "Random seed"),
        },
    },
    "distort-repeat": {
        "func": "distort_repeat",
        "category": "distortion",
        "input": "single",
        "help": "Time-stretch by repeating wavecycles",
        "params": {
            "multiplier": (int, 2, "Repeat multiplier"),
            "cycle-count": (int, 1, "Cycles per group"),
            "skip-cycles": (int, 0, "Cycles to skip"),
            "splice-ms": (float, 15.0, "Splice/crossfade in ms"),
            "mode": (int, 0, "Repeat mode"),
        },
    },
    "distort-shift": {
        "func": "distort_shift",
        "category": "distortion",
        "input": "single",
        "help": "Shift/swap half-wavecycle groups",
        "params": {
            "group-size": (int, 1, "Group size"),
            "shift": (int, 1, "Shift amount"),
            "mode": (int, 0, "Shift mode"),
        },
    },
    "distort-warp": {
        "func": "distort_warp",
        "category": "distortion",
        "input": "single",
        "help": "Progressive warp distortion with sample folding",
        "params": {
            "warp": (float, 0.001, "Warp amount"),
            "mode": (int, 0, "Warp mode"),
            "waveset-count": (int, 1, "Waveset count"),
        },
    },
    # -- granular --
    "brassage": {
        "func": "brassage",
        "category": "granular",
        "input": "single",
        "help": "Granular brassage (time-stretch, pitch-shift, scatter)",
        "params": {
            "velocity": (float, 1.0, "Playback velocity"),
            "density": (float, 1.0, "Grain density"),
            "grainsize-ms": (float, 50.0, "Grain size in ms"),
            "scatter": (float, 0.0, "Scatter amount"),
            "pitch-shift": (float, 0.0, "Pitch shift in semitones"),
            "amp-variation": (float, 0.0, "Amplitude variation"),
            "seed": (int, 0, "Random seed"),
        },
    },
    "freeze": {
        "func": "freeze",
        "category": "granular",
        "input": "single",
        "help": "Granular freeze at a position",
        "params": {
            "start-time": (float, REQUIRED, "Start time in seconds"),
            "end-time": (float, REQUIRED, "End time in seconds"),
            "duration": (float, REQUIRED, "Output duration in seconds"),
            "delay": (float, 0.05, "Grain delay in seconds"),
            "randomize": (float, 0.2, "Randomization amount"),
            "pitch-scatter": (float, 0.0, "Pitch scatter in semitones"),
            "amp-cut": (float, 0.1, "Amplitude cut threshold"),
            "gain": (float, 1.0, "Output gain"),
            "seed": (int, 0, "Random seed"),
        },
    },
    "grain-cloud": {
        "func": "grain_cloud",
        "category": "granular",
        "input": "single",
        "help": "Grain cloud generation from amplitude-detected grains",
        "params": {
            "gate": (float, 0.1, "Amplitude gate threshold"),
            "grainsize-ms": (float, 50.0, "Grain size in ms"),
            "density": (float, 10.0, "Grain density"),
            "duration": (float, 0.0, "Output duration (0 = auto)"),
            "scatter": (float, 0.3, "Scatter amount"),
            "seed": (int, 0, "Random seed"),
        },
    },
    "grain-extend": {
        "func": "grain_extend",
        "category": "granular",
        "input": "single",
        "help": "Extend duration using grain repetition",
        "params": {
            "grainsize-ms": (float, 15.0, "Grain size in ms"),
            "trough": (float, 0.3, "Trough detection threshold"),
            "extension": (float, 1.0, "Extension factor"),
            "start-time": (float, 0.0, "Start time in seconds"),
            "end-time": (float, 0.0, "End time (0 = end of file)"),
            "seed": (int, 0, "Random seed"),
        },
    },
    "texture-simple": {
        "func": "texture_simple",
        "category": "granular",
        "input": "single",
        "help": "Simple texture synthesis",
        "params": {
            "duration": (float, 5.0, "Output duration in seconds"),
            "density": (float, 5.0, "Grain density"),
            "pitch-range": (float, 6.0, "Pitch range in semitones"),
            "amp-range": (float, 0.3, "Amplitude range"),
            "spatial-range": (float, 0.8, "Spatial range"),
            "seed": (int, 0, "Random seed"),
        },
    },
    "texture-multi": {
        "func": "texture_multi",
        "category": "granular",
        "input": "single",
        "help": "Multi-layer grouped texture synthesis",
        "params": {
            "duration": (float, 5.0, "Output duration in seconds"),
            "density": (float, 2.0, "Grain density"),
            "group-size": (int, 4, "Group size"),
            "group-spread": (float, 0.2, "Group spread"),
            "pitch-range": (float, 8.0, "Pitch range in semitones"),
            "pitch-center": (float, 0.0, "Pitch center offset"),
            "amp-decay": (float, 0.3, "Amplitude decay"),
            "seed": (int, 0, "Random seed"),
        },
    },
    "grain-reorder": {
        "func": "grain_reorder",
        "category": "granular",
        "input": "single",
        "help": "Reorder detected grains",
        "params": {
            "order": ("json", None, "Grain order as JSON list of ints"),
            "gate": (float, 0.1, "Amplitude gate threshold"),
            "grainsize-ms": (float, 50.0, "Grain size in ms"),
            "seed": (int, 0, "Random seed"),
        },
    },
    "grain-rerhythm": {
        "func": "grain_rerhythm",
        "category": "granular",
        "input": "single",
        "help": "Change timing/rhythm of grains",
        "params": {
            "times": ("json", None, "Times as JSON list of floats"),
            "ratios": ("json", None, "Ratios as JSON list of floats"),
            "gate": (float, 0.1, "Amplitude gate threshold"),
            "grainsize-ms": (float, 50.0, "Grain size in ms"),
            "seed": (int, 0, "Random seed"),
        },
    },
    "grain-reverse": {
        "func": "grain_reverse",
        "category": "granular",
        "input": "single",
        "help": "Reverse individual grains in place",
        "params": {
            "gate": (float, 0.1, "Amplitude gate threshold"),
            "grainsize-ms": (float, 50.0, "Grain size in ms"),
        },
    },
    "grain-timewarp": {
        "func": "grain_timewarp",
        "category": "granular",
        "input": "single",
        "help": "Time-stretch/compress grain spacing",
        "params": {
            "stretch": (float, 1.0, "Stretch factor"),
            "stretch-curve": (
                "json",
                None,
                "Curve as JSON list of [time, value] pairs",
            ),
            "gate": (float, 0.1, "Amplitude gate threshold"),
            "grainsize-ms": (float, 50.0, "Grain size in ms"),
        },
    },
    "grain-repitch": {
        "func": "grain_repitch",
        "category": "granular",
        "input": "single",
        "help": "Pitch-shift grains with interpolation",
        "params": {
            "pitch-semitones": (float, 0.0, "Pitch shift in semitones"),
            "pitch-curve": ("json", None, "Curve as JSON list of [time, value] pairs"),
            "gate": (float, 0.1, "Amplitude gate threshold"),
            "grainsize-ms": (float, 50.0, "Grain size in ms"),
        },
    },
    "grain-position": {
        "func": "grain_position",
        "category": "granular",
        "input": "single",
        "help": "Reposition grains in stereo field",
        "params": {
            "positions": ("json", None, "Positions as JSON list of floats"),
            "duration": (float, 0.0, "Output duration (0 = auto)"),
            "gate": (float, 0.1, "Amplitude gate threshold"),
            "grainsize-ms": (float, 50.0, "Grain size in ms"),
        },
    },
    "grain-omit": {
        "func": "grain_omit",
        "category": "granular",
        "input": "single",
        "help": "Probabilistically omit grains",
        "params": {
            "keep": (int, 1, "Keep N grains"),
            "out-of": (int, 2, "Out of N grains"),
            "gate": (float, 0.1, "Amplitude gate threshold"),
            "grainsize-ms": (float, 50.0, "Grain size in ms"),
            "seed": (int, 0, "Random seed"),
        },
    },
    "grain-duplicate": {
        "func": "grain_duplicate",
        "category": "granular",
        "input": "single",
        "help": "Duplicate grains with variations",
        "params": {
            "repeats": (int, 2, "Number of repeats"),
            "gate": (float, 0.1, "Amplitude gate threshold"),
            "grainsize-ms": (float, 50.0, "Grain size in ms"),
            "seed": (int, 0, "Random seed"),
        },
    },
    "wrappage": {
        "func": "wrappage",
        "category": "granular",
        "input": "single",
        "help": "Granular texture with stereo spatial distribution",
        "params": {
            "grain-size": (float, 50.0, "Grain size in ms"),
            "density": (float, 1.0, "Grain density"),
            "velocity": (float, 1.0, "Playback velocity"),
            "pitch": (float, 0.0, "Pitch shift in semitones"),
            "spread": (float, 1.0, "Stereo spread"),
            "jitter": (float, 0.1, "Timing jitter"),
            "splice-ms": (float, 5.0, "Splice/crossfade in ms"),
            "duration": (float, 0.0, "Output duration (0 = auto)"),
        },
    },
    "hover": {
        "func": "hover",
        "category": "granular",
        "input": "single",
        "help": "Zigzag reading at specified frequency for hovering pitch",
        "params": {
            "frequency": (float, 440.0, "Hover frequency in Hz"),
            "location": (float, 0.5, "Read location (0-1)"),
            "frq-rand": (float, 0.1, "Frequency randomization"),
            "loc-rand": (float, 0.1, "Location randomization"),
            "splice-ms": (float, 1.0, "Splice/crossfade in ms"),
            "duration": (float, 0.0, "Output duration (0 = auto)"),
        },
    },
    # -- envelope --
    "dovetail": {
        "func": "dovetail",
        "category": "envelope",
        "input": "single",
        "help": "Apply dovetail fades (fade in + fade out)",
        "params": {
            "fade-in-dur": (float, REQUIRED, "Fade-in duration in seconds"),
            "fade-out-dur": (float, REQUIRED, "Fade-out duration in seconds"),
            "fade-type": (str, "exponential", "Fade type: exponential or linear"),
        },
    },
    "tremolo": {
        "func": "tremolo",
        "category": "envelope",
        "input": "single",
        "help": "Apply tremolo (amplitude modulation)",
        "params": {
            "freq": (float, REQUIRED, "Tremolo frequency in Hz"),
            "depth": (float, REQUIRED, "Tremolo depth (0-1)"),
            "gain": (float, 1.0, "Output gain"),
        },
    },
    "attack": {
        "func": "attack",
        "category": "envelope",
        "input": "single",
        "help": "Modify attack transient",
        "params": {
            "attack-gain": (float, REQUIRED, "Attack gain multiplier"),
            "attack-time": (float, REQUIRED, "Attack time in seconds"),
        },
    },
    # -- playback --
    "zigzag": {
        "func": "zigzag",
        "category": "playback",
        "input": "single",
        "help": "Alternating forward/backward playback through time points",
        "params": {
            "times": ("json", REQUIRED, "Time points as JSON list of floats"),
            "splice-ms": (float, 15.0, "Splice/crossfade in ms"),
        },
    },
    "iterate": {
        "func": "iterate",
        "category": "playback",
        "input": "single",
        "help": "Repeat with pitch shift and gain decay variations",
        "params": {
            "repeats": (int, 4, "Number of repeats"),
            "delay": (float, 0.5, "Delay between repeats in seconds"),
            "delay-rand": (float, 0.0, "Delay randomization"),
            "pitch-shift": (float, 0.0, "Pitch shift per repeat in semitones"),
            "gain-decay": (float, 1.0, "Gain decay per repeat"),
            "seed": (int, 0, "Random seed"),
        },
    },
    "stutter": {
        "func": "stutter",
        "category": "playback",
        "input": "single",
        "help": "Segment-based stuttering with silence inserts",
        "params": {
            "segment-ms": (float, 100.0, "Segment size in ms"),
            "duration": (float, 5.0, "Output duration in seconds"),
            "silence-prob": (float, 0.2, "Silence probability"),
            "silence-min-ms": (float, 10.0, "Minimum silence duration in ms"),
            "silence-max-ms": (float, 100.0, "Maximum silence duration in ms"),
            "transpose-range": (float, 0.0, "Transpose range in semitones"),
            "seed": (int, 0, "Random seed"),
        },
    },
    "bounce": {
        "func": "bounce",
        "category": "playback",
        "input": "single",
        "help": "Bouncing ball effect with accelerating repeats",
        "params": {
            "bounces": (int, 8, "Number of bounces"),
            "initial-delay": (float, 0.5, "Initial delay in seconds"),
            "shrink": (float, 0.7, "Delay shrink per bounce"),
            "end-level": (float, 0.1, "Final level"),
            "level-curve": (float, 1.0, "Level decay curve exponent"),
        },
    },
    "drunk": {
        "func": "drunk",
        "category": "playback",
        "input": "single",
        "help": "Random 'drunk walk' navigation through audio",
        "params": {
            "duration": (float, 5.0, "Output duration in seconds"),
            "step-ms": (float, 100.0, "Step size in ms"),
            "step-rand": (float, 0.3, "Step randomization"),
            "locus": (float, 0.0, "Center position (0-1)"),
            "ambitus": (float, 0.0, "Range limit (0 = unlimited)"),
            "overlap": (float, 0.1, "Overlap factor"),
            "splice-ms": (float, 15.0, "Splice/crossfade in ms"),
            "seed": (int, 0, "Random seed"),
        },
    },
    "loop": {
        "func": "loop",
        "category": "playback",
        "input": "single",
        "help": "Loop a section with crossfades",
        "params": {
            "start": (float, 0.0, "Loop start time in seconds"),
            "length-ms": (float, 500.0, "Loop length in ms"),
            "step-ms": (float, 0.0, "Step between loops in ms"),
            "search-ms": (float, 0.0, "Zero-crossing search window in ms"),
            "repeats": (int, 4, "Number of repetitions"),
            "splice-ms": (float, 15.0, "Splice/crossfade in ms"),
            "seed": (int, 0, "Random seed"),
        },
    },
    "retime": {
        "func": "retime",
        "category": "playback",
        "input": "single",
        "help": "Time-domain time stretch/compress (TDOLA)",
        "params": {
            "ratio": (float, 1.0, "Time ratio"),
            "grain-ms": (float, 50.0, "Grain size in ms"),
            "overlap": (float, 0.5, "Overlap factor"),
        },
    },
    "scramble": {
        "func": "scramble",
        "category": "playback",
        "input": "single",
        "help": "Reorder wavesets (shuffle, reverse, by size/level)",
        "params": {
            "mode": (
                "scramble",
                "shuffle",
                "Mode: shuffle, reverse, size-up, size-down, level-up, level-down",
            ),
            "group-size": (int, 2, "Group size"),
            "seed": (int, 0, "Random seed"),
        },
    },
    "splinter": {
        "func": "splinter",
        "category": "playback",
        "input": "single",
        "help": "Fragmenting effect with shrinking repeats",
        "params": {
            "start": (float, 0.0, "Start position in seconds"),
            "duration-ms": (float, 50.0, "Fragment duration in ms"),
            "repeats": (int, 20, "Number of repeats"),
            "min-shrink": (float, 0.1, "Minimum shrink factor"),
            "shrink-curve": (float, 1.0, "Shrink curve exponent"),
            "accel": (float, 1.5, "Acceleration factor"),
            "seed": (int, 0, "Random seed"),
        },
    },
    # -- spatial --
    "pan": {
        "func": "pan",
        "category": "spatial",
        "input": "single",
        "help": "Pan mono to stereo with static position",
        "params": {
            "position": (float, 0.0, "Pan position (-1=left, 0=center, 1=right)"),
        },
    },
    "pan-envelope": {
        "func": "pan_envelope",
        "category": "spatial",
        "input": "single",
        "help": "Pan mono to stereo with time-varying position",
        "params": {
            "points": (
                "json",
                REQUIRED,
                "Points as JSON list of [time, position] pairs",
            ),
        },
    },
    "mirror": {
        "func": "mirror",
        "category": "spatial",
        "input": "single",
        "help": "Swap left and right channels",
        "params": {},
    },
    "narrow": {
        "func": "narrow",
        "category": "spatial",
        "input": "single",
        "help": "Narrow or widen stereo image",
        "params": {
            "width": (float, 1.0, "Stereo width (0=mono, 1=normal, >1=wider)"),
        },
    },
    "spin": {
        "func": "spin",
        "category": "spatial",
        "input": "single",
        "help": "Rotate audio around stereo field with optional doppler",
        "params": {
            "rate": (float, 1.0, "Rotation rate in Hz"),
            "doppler": (float, 0.0, "Doppler effect depth"),
            "depth": (float, 1.0, "Rotation depth"),
        },
    },
    "rotor": {
        "func": "rotor",
        "category": "spatial",
        "input": "single",
        "help": "Dual-rotation modulation (pitch + amplitude interference)",
        "params": {
            "pitch-rate": (float, 1.0, "Pitch modulation rate in Hz"),
            "pitch-depth": (float, 2.0, "Pitch modulation depth"),
            "amp-rate": (float, 1.5, "Amplitude modulation rate in Hz"),
            "amp-depth": (float, 0.5, "Amplitude modulation depth"),
            "phase-offset": (float, 0.0, "Phase offset"),
        },
    },
    "flutter": {
        "func": "flutter",
        "category": "spatial",
        "input": "single",
        "help": "Spatial tremolo (loudness modulation alternating L/R)",
        "params": {
            "frequency": (float, 4.0, "Flutter frequency in Hz"),
            "depth": (float, 1.0, "Flutter depth"),
            "gain": (float, 1.0, "Output gain"),
        },
    },
    # -- phase --
    "phase-invert": {
        "func": "phase_invert",
        "category": "phase",
        "input": "single",
        "help": "Invert phase of audio",
        "params": {},
    },
    "phase-stereo": {
        "func": "phase_stereo",
        "category": "phase",
        "input": "single",
        "help": "Enhance stereo separation via phase subtraction",
        "params": {
            "transfer": (float, 1.0, "Phase transfer amount"),
        },
    },
    # -- experimental --
    "strange": {
        "func": "strange",
        "category": "experimental",
        "input": "single",
        "help": "Strange attractor transformation",
        "params": {
            "chaos-amount": (float, 0.5, "Chaos amount (0-1)"),
            "rate": (float, 2.0, "Transformation rate"),
            "seed": (int, 0, "Random seed"),
        },
    },
    "brownian": {
        "func": "brownian",
        "category": "experimental",
        "input": "single",
        "help": "Brownian motion transformation",
        "params": {
            "step-size": (float, 0.1, "Step size"),
            "smoothing": (float, 0.9, "Smoothing factor"),
            "target": (int, 0, "Target mode"),
            "seed": (int, 0, "Random seed"),
        },
    },
    "crystal": {
        "func": "crystal",
        "category": "experimental",
        "input": "single",
        "help": "Crystal growth patterns",
        "params": {
            "density": (float, 50.0, "Crystal density"),
            "decay": (float, 0.5, "Decay factor"),
            "pitch-scatter": (float, 2.0, "Pitch scatter in semitones"),
            "seed": (int, 0, "Random seed"),
        },
    },
    "fractal": {
        "func": "fractal",
        "category": "experimental",
        "input": "single",
        "help": "Fractal transformation",
        "params": {
            "depth": (int, 3, "Recursion depth"),
            "pitch-ratio": (float, 0.5, "Pitch ratio per level"),
            "decay": (float, 0.7, "Amplitude decay per level"),
            "seed": (int, 0, "Random seed"),
        },
    },
    "quirk": {
        "func": "quirk",
        "category": "experimental",
        "input": "single",
        "help": "Random quirky transformation",
        "params": {
            "probability": (float, 0.3, "Event probability"),
            "intensity": (float, 0.5, "Effect intensity"),
            "mode": (int, 2, "Quirk mode"),
            "seed": (int, 0, "Random seed"),
        },
    },
    "chirikov": {
        "func": "chirikov",
        "category": "experimental",
        "input": "single",
        "help": "Chirikov map transformation",
        "params": {
            "k-param": (float, 2.0, "K parameter"),
            "mod-depth": (float, 0.5, "Modulation depth"),
            "rate": (float, 2.0, "Modulation rate"),
            "seed": (int, 0, "Random seed"),
        },
    },
    "cantor": {
        "func": "cantor",
        "category": "experimental",
        "input": "single",
        "help": "Cantor set transformation (recursive silence insertion)",
        "params": {
            "depth": (int, 4, "Recursion depth"),
            "duty-cycle": (float, 0.5, "Duty cycle"),
            "smooth-ms": (float, 5.0, "Smoothing time in ms"),
            "seed": (int, 0, "Random seed"),
        },
    },
    "cascade": {
        "func": "cascade",
        "category": "experimental",
        "input": "single",
        "help": "Cascading echo with pitch shift",
        "params": {
            "num-echoes": (int, 6, "Number of echoes"),
            "delay-ms": (float, 100.0, "Delay between echoes in ms"),
            "pitch-decay": (float, 0.95, "Pitch decay per echo"),
            "amp-decay": (float, 0.7, "Amplitude decay per echo"),
            "filter-decay": (float, 0.8, "Filter decay per echo"),
            "seed": (int, 0, "Random seed"),
        },
    },
    "fracture": {
        "func": "fracture",
        "category": "experimental",
        "input": "single",
        "help": "Fragment audio with gaps and scatter",
        "params": {
            "fragment-ms": (float, 50.0, "Fragment size in ms"),
            "gap-ratio": (float, 0.5, "Gap ratio"),
            "scatter": (float, 0.3, "Scatter amount"),
            "seed": (int, 0, "Random seed"),
        },
    },
    "tesselate": {
        "func": "tesselate",
        "category": "experimental",
        "input": "single",
        "help": "Tesselation/tiling transformation",
        "params": {
            "tile-ms": (float, 50.0, "Tile size in ms"),
            "pattern": (int, 1, "Tiling pattern"),
            "overlap": (float, 0.25, "Overlap factor"),
            "transform": (float, 0.3, "Transform amount"),
            "seed": (int, 0, "Random seed"),
        },
    },
    # -- psow --
    "psow-stretch": {
        "func": "psow_stretch",
        "category": "psow",
        "input": "single",
        "help": "Time-stretch preserving pitch (PSOLA)",
        "params": {
            "stretch-factor": (float, 1.0, "Stretch factor"),
            "grain-count": (int, 1, "Grains per period"),
        },
    },
    "psow-grab": {
        "func": "psow_grab",
        "category": "psow",
        "input": "single",
        "help": "Extract pitch-synchronous grains from position",
        "params": {
            "time": (float, 0.0, "Grab time in seconds"),
            "duration": (float, 0.0, "Grab duration (0 = auto)"),
            "grain-count": (int, 1, "Grains per period"),
            "density": (float, 1.0, "Grain density"),
        },
    },
    "psow-dupl": {
        "func": "psow_dupl",
        "category": "psow",
        "input": "single",
        "help": "Duplicate pitch-synchronous grains for stretching",
        "params": {
            "repeat-count": (int, 2, "Number of repetitions"),
            "grain-count": (int, 1, "Grains per period"),
        },
    },
    "psow-interp": {
        "func": "psow_interp",
        "category": "psow",
        "input": "dual",
        "help": "Interpolate between two pitch-synchronous grains",
        "params": {
            "start-dur": (float, 0.1, "Start grain duration"),
            "interp-dur": (float, 0.5, "Interpolation duration"),
            "end-dur": (float, 0.1, "End grain duration"),
        },
    },
    # -- fofex --
    "fofex-extract": {
        "func": "fofex_extract",
        "category": "fofex",
        "input": "single",
        "help": "Extract single FOF (pitch-synchronous grain) at time",
        "params": {
            "time": (float, REQUIRED, "Extraction time in seconds"),
            "fof-count": (int, 1, "Number of FOFs"),
            "window": ("bool", True, "Apply window"),
        },
    },
    "fofex-extract-all": {
        "func": "fofex_extract_all",
        "category": "fofex",
        "input": "single",
        "help": "Extract all FOFs to uniform-length bank",
        "params": {
            "fof-count": (int, 1, "Number of FOFs"),
            "min-level-db": (float, 0.0, "Minimum level in dB"),
            "window": ("bool", True, "Apply window"),
        },
    },
    "fofex-synth": {
        "func": "fofex_synth",
        "category": "fofex",
        "input": "single",
        "help": "Synthesize audio from FOF bank at target pitch",
        "params": {
            "duration": (float, REQUIRED, "Output duration in seconds"),
            "frequency": (float, REQUIRED, "Target frequency in Hz"),
            "amplitude": (float, 0.8, "Output amplitude"),
        },
    },
    "fofex-repitch": {
        "func": "fofex_repitch",
        "category": "fofex",
        "input": "single",
        "help": "Repitch audio with optional formant preservation",
        "params": {
            "pitch-shift": (float, REQUIRED, "Pitch shift in semitones"),
            "preserve-formants": ("bool", True, "Preserve formants"),
        },
    },
    # -- morph --
    "morph": {
        "func": "morph",
        "category": "morph",
        "input": "dual",
        "help": "Spectral morph between two sounds",
        "params": {
            "morph-start": (float, 0.0, "Morph start amount (0=first)"),
            "morph-end": (float, 1.0, "Morph end amount (1=second)"),
            "exponent": (float, 1.0, "Morph curve exponent"),
            "fft-size": (int, 1024, "FFT window size"),
        },
    },
    "morph-glide": {
        "func": "morph_glide",
        "category": "morph",
        "input": "dual",
        "help": "Gliding morph over time (0->1 over duration)",
        "params": {
            "duration": (float, 1.0, "Morph duration in seconds"),
            "fft-size": (int, 1024, "FFT window size"),
        },
    },
    "cross-synth": {
        "func": "cross_synth",
        "category": "morph",
        "input": "dual",
        "help": "Cross-synthesis (vocoder-like)",
        "params": {
            "mode": (int, 0, "Cross-synthesis mode"),
            "mix": (float, 1.0, "Dry/wet mix"),
            "fft-size": (int, 1024, "FFT window size"),
        },
    },
    "morph-glide-native": {
        "func": "morph_glide_native",
        "category": "morph",
        "input": "dual",
        "help": "Native CDP specglide morph",
        "params": {
            "duration": (float, 1.0, "Morph duration in seconds"),
            "fft-size": (int, 1024, "FFT window size"),
        },
    },
    "morph-bridge-native": {
        "func": "morph_bridge_native",
        "category": "morph",
        "input": "dual",
        "help": "Native CDP specbridge morph",
        "params": {
            "mode": (int, 0, "Bridge mode"),
            "offset": (float, 0.0, "Offset"),
            "interp-start": (float, 0.0, "Interpolation start"),
            "interp-end": (float, 1.0, "Interpolation end"),
            "fft-size": (int, 1024, "FFT window size"),
        },
    },
    "morph-native": {
        "func": "morph_native",
        "category": "morph",
        "input": "dual",
        "help": "Native CDP specmorph",
        "params": {
            "mode": (int, 0, "Morph mode"),
            "amp-start": (float, 0.0, "Amplitude morph start"),
            "amp-end": (float, 1.0, "Amplitude morph end"),
            "freq-start": (float, 0.0, "Frequency morph start"),
            "freq-end": (float, 1.0, "Frequency morph end"),
            "amp-exp": (float, 1.0, "Amplitude exponent"),
            "freq-exp": (float, 1.0, "Frequency exponent"),
            "stagger": (float, 0.0, "Stagger amount"),
            "fft-size": (int, 1024, "FFT window size"),
        },
    },
    # -- synth --
    "synth-wave": {
        "func": "synth_wave",
        "category": "synth",
        "input": "synth",
        "help": "Generate waveform (sine, square, saw, ramp, triangle)",
        "params": {
            "waveform": (
                "waveform",
                "sine",
                "Waveform: sine, square, saw, ramp, triangle",
            ),
            "frequency": (float, 440.0, "Frequency in Hz"),
            "amplitude": (float, 0.8, "Amplitude (0-1)"),
            "duration": (float, 1.0, "Duration in seconds"),
            "sample-rate": (int, 44100, "Sample rate"),
        },
    },
    "synth-noise": {
        "func": "synth_noise",
        "category": "synth",
        "input": "synth",
        "help": "Generate white or pink noise",
        "params": {
            "pink": (int, 0, "Pink noise (1) or white noise (0)"),
            "amplitude": (float, 0.8, "Amplitude (0-1)"),
            "duration": (float, 1.0, "Duration in seconds"),
            "sample-rate": (int, 44100, "Sample rate"),
            "seed": (int, 0, "Random seed"),
        },
    },
    "synth-click": {
        "func": "synth_click",
        "category": "synth",
        "input": "synth",
        "help": "Generate click/metronome track",
        "params": {
            "tempo": (float, 120.0, "Tempo in BPM"),
            "beats-per-bar": (int, 4, "Beats per bar"),
            "duration": (float, 10.0, "Duration in seconds"),
            "click-freq": (float, 1000.0, "Click frequency in Hz"),
            "click-dur-ms": (float, 10.0, "Click duration in ms"),
            "sample-rate": (int, 44100, "Sample rate"),
        },
    },
    "synth-chord": {
        "func": "synth_chord",
        "category": "synth",
        "input": "synth",
        "help": "Synthesize chord from MIDI note list",
        "params": {
            "midi-notes": ("nargs+", REQUIRED, "MIDI note numbers"),
            "amplitude": (float, 0.8, "Amplitude (0-1)"),
            "duration": (float, 1.0, "Duration in seconds"),
            "detune-cents": (float, 0.0, "Detune in cents"),
            "sample-rate": (int, 44100, "Sample rate"),
        },
    },
    # -- analyze --
    "pitch": {
        "func": "pitch",
        "category": "analyze",
        "input": "analysis",
        "help": "Extract pitch data (YIN algorithm)",
        "params": {
            "min-freq": (float, 50.0, "Minimum frequency in Hz"),
            "max-freq": (float, 2000.0, "Maximum frequency in Hz"),
            "frame-size": (int, 2048, "Analysis frame size"),
            "hop-size": (int, 512, "Hop size between frames"),
        },
    },
    "formants": {
        "func": "formants",
        "category": "analyze",
        "input": "analysis",
        "help": "Extract formant data (LPC analysis)",
        "params": {
            "lpc-order": (int, 12, "LPC order"),
            "frame-size": (int, 1024, "Analysis frame size"),
            "hop-size": (int, 256, "Hop size between frames"),
        },
    },
    "get-partials": {
        "func": "get_partials",
        "category": "analyze",
        "input": "analysis",
        "help": "Extract partial/harmonic data",
        "params": {
            "min-amp-db": (float, -60.0, "Minimum amplitude in dB"),
            "max-partials": (int, 100, "Maximum number of partials"),
            "freq-tolerance": (float, 50.0, "Frequency tolerance in Hz"),
            "fft-size": (int, 2048, "FFT window size"),
            "hop-size": (int, 512, "Hop size between frames"),
        },
    },
    # -- gain --
    "gain": {
        "func": "gain",
        "category": "gain",
        "input": "single",
        "help": "Apply gain to audio",
        "params": {
            "gain-factor": (float, 1.0, "Gain multiplier"),
        },
    },
    "gain-db": {
        "func": "gain_db",
        "category": "gain",
        "input": "single",
        "help": "Apply gain in decibels",
        "params": {
            "db": (float, 0.0, "Gain in dB"),
        },
    },
    "normalize": {
        "func": "normalize",
        "category": "gain",
        "input": "single",
        "help": "Normalize to target peak level",
        "params": {
            "target": (float, 1.0, "Target peak level"),
        },
    },
    "normalize-db": {
        "func": "normalize_db",
        "category": "gain",
        "input": "single",
        "help": "Normalize to target peak level in dB",
        "params": {
            "target-db": (float, 0.0, "Target peak level in dB"),
        },
    },
    "peak": {
        "func": "peak",
        "category": "gain",
        "input": "analysis",
        "help": "Find peak level in audio",
        "params": {},
    },
    # -- buffer --
    "reverse": {
        "func": "reverse",
        "category": "buffer",
        "input": "single",
        "help": "Reverse audio",
        "params": {},
    },
    "fade-in": {
        "func": "fade_in",
        "category": "buffer",
        "input": "single",
        "help": "Apply fade in",
        "params": {
            "duration": (float, REQUIRED, "Fade duration in seconds"),
            "curve": (str, "linear", "Curve type: linear or cosine"),
        },
    },
    "fade-out": {
        "func": "fade_out",
        "category": "buffer",
        "input": "single",
        "help": "Apply fade out",
        "params": {
            "duration": (float, REQUIRED, "Fade duration in seconds"),
            "curve": (str, "linear", "Curve type: linear or cosine"),
        },
    },
    # -- channel --
    "to-mono": {
        "func": "to_mono",
        "category": "channel",
        "input": "single",
        "help": "Convert to mono by averaging channels",
        "params": {},
    },
    "to-stereo": {
        "func": "to_stereo",
        "category": "channel",
        "input": "single",
        "help": "Convert mono to stereo by duplicating channel",
        "params": {},
    },
    "extract-channel": {
        "func": "extract_channel",
        "category": "channel",
        "input": "single",
        "help": "Extract a single channel",
        "params": {
            "channel": (int, REQUIRED, "Channel index (0-based)"),
        },
    },
    # -- mix --
    "mix2": {
        "func": "mix2",
        "category": "mix",
        "input": "dual",
        "help": "Mix two audio files together",
        "params": {
            "gain-a": (float, 1.0, "Gain for first input"),
            "gain-b": (float, 1.0, "Gain for second input"),
        },
    },
    # -- envelope-apply (dual: applies envelope from one buffer to another) --
    "envelope-apply": {
        "func": "envelope_apply",
        "category": "dynamics",
        "input": "dual",
        "help": "Apply envelope from one audio to another",
        "params": {
            "depth": (float, 1.0, "Envelope application depth"),
        },
    },
}


# =============================================================================
# Custom help formatter
# =============================================================================


class CategoryHelpFormatter(argparse.RawDescriptionHelpFormatter):
    """Formatter that groups subcommands by category."""

    def _format_action(self, action):
        if isinstance(action, argparse._SubParsersAction):
            parts = []
            by_cat: dict[str, list[tuple[str, str]]] = {}
            for choice_name, choice_parser in action.choices.items():
                spec = COMMANDS.get(choice_name)
                if spec:
                    cat = spec["category"]
                    by_cat.setdefault(cat, []).append((choice_name, spec["help"]))
                else:
                    by_cat.setdefault("utility", []).append(
                        (choice_name, choice_parser.description or "")
                    )

            cat_order = [c for c in CATEGORIES if c in by_cat]
            if "utility" in by_cat:
                cat_order.append("utility")

            for cat in cat_order:
                cmds = by_cat[cat]
                if cat == "utility":
                    label = "Utility commands"
                else:
                    label = CATEGORIES.get(cat, cat)
                parts.append(f"\n  {label}:")
                max_name = max(len(c[0]) for c in cmds)
                for name, help_text in sorted(cmds):
                    parts.append(f"    {name:<{max_name}}  {help_text}")

            return "\n".join(parts) + "\n"
        return super()._format_action(action)


# =============================================================================
# Parser construction
# =============================================================================


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cycdp",
        description="cycdp - CDP audio processing CLI",
        formatter_class=CategoryHelpFormatter,
    )

    subparsers = parser.add_subparsers(
        dest="command", title="commands", metavar="<command>"
    )

    for cmd_name, spec in COMMANDS.items():
        input_type = spec["input"]
        sub = subparsers.add_parser(
            cmd_name,
            help=spec["help"],
            description=spec["help"],
        )

        if input_type in ("single", "analysis"):
            sub.add_argument("input", help="Input WAV file")
        elif input_type == "dual":
            sub.add_argument("input1", help="First input WAV file")
            sub.add_argument("input2", help="Second input WAV file")

        for param_name, (ptype, default, help_text) in spec["params"].items():
            flag = f"--{param_name}"
            kwarg_name = param_name.replace("-", "_")

            if ptype == "json":
                kw: dict = {"type": str, "help": help_text, "dest": kwarg_name}
                if default is REQUIRED:
                    kw["required"] = True
                else:
                    kw["default"] = default
                sub.add_argument(flag, **kw)
            elif ptype == "nargs+":
                kw = {
                    "type": int,
                    "nargs": "+",
                    "help": help_text,
                    "dest": kwarg_name,
                }
                if default is REQUIRED:
                    kw["required"] = True
                else:
                    kw["default"] = default
                sub.add_argument(flag, **kw)
            elif ptype == "waveform":
                kw = {
                    "type": str,
                    "choices": list(WAVEFORM_MAP.keys()),
                    "help": help_text,
                    "dest": kwarg_name,
                }
                if default is REQUIRED:
                    kw["required"] = True
                else:
                    kw["default"] = default
                sub.add_argument(flag, **kw)
            elif ptype == "scramble":
                kw = {
                    "type": str,
                    "choices": list(SCRAMBLE_MAP.keys()),
                    "help": help_text,
                    "dest": kwarg_name,
                }
                if default is REQUIRED:
                    kw["required"] = True
                else:
                    kw["default"] = default
                sub.add_argument(flag, **kw)
            elif ptype == "bool":
                if default:
                    sub.add_argument(
                        f"--no-{param_name}",
                        action="store_false",
                        dest=kwarg_name,
                        help=f"Disable: {help_text}",
                    )
                    sub.set_defaults(**{kwarg_name: True})
                else:
                    sub.add_argument(
                        flag,
                        action="store_true",
                        dest=kwarg_name,
                        help=help_text,
                    )
            else:
                kw = {"type": ptype, "help": help_text, "dest": kwarg_name}
                if default is REQUIRED:
                    kw["required"] = True
                else:
                    kw["default"] = default
                sub.add_argument(flag, **kw)

        # Global options for audio-producing commands
        if input_type in ("single", "dual", "synth"):
            sub.add_argument(
                "-o",
                "--output",
                help="Output file path, or directory (auto-names file)",
            )
            if cmd_name in NO_AUTO_NORMALIZE:
                sub.add_argument(
                    "-n",
                    "--normalize",
                    type=float,
                    default=None,
                    help=(
                        "Normalize output to this peak level "
                        "(off by default for this command)"
                    ),
                )
            else:
                sub.add_argument(
                    "-n",
                    "--normalize",
                    type=float,
                    default=0.95,
                    help="Normalize output to this peak level (default: 0.95)",
                )
            sub.add_argument(
                "--no-normalize",
                action="store_true",
                default=False,
                help="Skip normalization",
            )
            sub.add_argument(
                "--format",
                choices=["float", "pcm16", "pcm24"],
                default="float",
                help="Audio output format (default: float)",
            )
        elif input_type == "analysis":
            sub.add_argument(
                "-o",
                "--output",
                help="Write output to file instead of stdout",
            )
            sub.add_argument(
                "--format",
                choices=["text", "json", "csv"],
                default="text",
                help="Output format (default: text)",
            )

    # Built-in utility commands
    sub_list = subparsers.add_parser(
        "list", description="List available commands by category"
    )
    sub_list.add_argument(
        "category",
        nargs="?",
        choices=list(CATEGORIES.keys()),
        help="Show commands in a specific category",
    )

    subparsers.add_parser("version", description="Show version information")

    sub_info = subparsers.add_parser("info", description="Show audio file information")
    sub_info.add_argument("input", help="Input WAV file")

    return parser


# =============================================================================
# Output path resolution
# =============================================================================


def resolve_output_path(args, cmd_name: str, input_path: str | None = None) -> str:
    output = getattr(args, "output", None)

    if output:
        if os.path.isdir(output):
            stem = _input_stem(input_path) if input_path else "output"
            return os.path.join(output, f"{stem}_{cmd_name}.wav")
        return output

    if input_path:
        stem = _input_stem(input_path)
        dirn = os.path.dirname(input_path) or "."
        return os.path.join(dirn, f"{stem}_{cmd_name}.wav")

    return f"output_{cmd_name}.wav"


def _input_stem(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0]


# =============================================================================
# Parameter preparation
# =============================================================================


def prepare_kwargs(spec: dict, args: argparse.Namespace) -> dict:
    """Convert parsed args to kwargs for the target function."""
    kwargs = {}
    for param_name, (ptype, _default, _help) in spec["params"].items():
        kwarg_name = param_name.replace("-", "_")
        value = getattr(args, kwarg_name, None)
        if value is None:
            continue

        if ptype == "json":
            value = json.loads(value)
        elif ptype == "waveform":
            value = WAVEFORM_MAP[value]
        elif ptype == "scramble":
            value = SCRAMBLE_MAP[value]

        kwargs[kwarg_name] = value

    return kwargs


# =============================================================================
# Handlers
# =============================================================================


def maybe_normalize(result, args: argparse.Namespace):
    """Apply output normalization unless it is disabled for this invocation.

    ``args.normalize`` is None for commands in NO_AUTO_NORMALIZE unless the
    user passed -n explicitly, so amplitude operations keep the level they
    were asked to produce.
    """
    if args.no_normalize or args.normalize is None:
        return result
    return cycdp.normalize(result, target=args.normalize)


def handle_single(cmd_name: str, spec: dict, args: argparse.Namespace) -> None:
    input_path = args.input
    if not os.path.exists(input_path):
        print(f"Error: file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    buf = cycdp.read_file(input_path)
    func = getattr(cycdp, spec["func"])
    kwargs = prepare_kwargs(spec, args)
    result = func(buf, **kwargs)

    result = maybe_normalize(result, args)

    output_path = resolve_output_path(args, cmd_name, input_path)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    cycdp.write_file(output_path, result, format=args.format)
    print(output_path)


def handle_dual(cmd_name: str, spec: dict, args: argparse.Namespace) -> None:
    for path in (args.input1, args.input2):
        if not os.path.exists(path):
            print(f"Error: file not found: {path}", file=sys.stderr)
            sys.exit(1)

    buf1 = cycdp.read_file(args.input1)
    buf2 = cycdp.read_file(args.input2)
    func = getattr(cycdp, spec["func"])
    kwargs = prepare_kwargs(spec, args)
    result = func(buf1, buf2, **kwargs)

    result = maybe_normalize(result, args)

    output_path = resolve_output_path(args, cmd_name, args.input1)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    cycdp.write_file(output_path, result, format=args.format)
    print(output_path)


def handle_synth(cmd_name: str, spec: dict, args: argparse.Namespace) -> None:
    func = getattr(cycdp, spec["func"])
    kwargs = prepare_kwargs(spec, args)
    result = func(**kwargs)

    result = maybe_normalize(result, args)

    output_path = resolve_output_path(args, cmd_name)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    cycdp.write_file(output_path, result, format=args.format)
    print(output_path)


def handle_analysis(cmd_name: str, spec: dict, args: argparse.Namespace) -> None:
    input_path = args.input
    if not os.path.exists(input_path):
        print(f"Error: file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    buf = cycdp.read_file(input_path)
    func = getattr(cycdp, spec["func"])
    kwargs = prepare_kwargs(spec, args)
    result = func(buf, **kwargs)

    fmt = args.format
    output_text = format_analysis(cmd_name, result, fmt)

    if args.output:
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w") as f:
            f.write(output_text)
        print(args.output)
    else:
        print(output_text)


# =============================================================================
# Analysis output formatting
# =============================================================================
#
# Analysis functions return dicts, not tuples/lists. The actual return
# formats (from _core.pyx):
#
# pitch -> dict with 'pitch', 'confidence', 'num_frames', 'frame_time', 'sample_rate'
# formants -> dict with 'f1'-'f4', 'b1'-'b4', 'num_frames', 'frame_time', 'sample_rate'
# get_partials -> dict with 'tracks', 'num_tracks', 'total_frames', etc.
# peak -> tuple (level, position)


def format_analysis(cmd_name: str, data, fmt: str) -> str:
    if fmt == "json":
        return json.dumps(data, indent=2)

    if fmt == "csv":
        return _format_csv(cmd_name, data)

    return _format_text(cmd_name, data)


def _format_text(cmd_name: str, data) -> str:
    lines = []
    if cmd_name == "pitch":
        pitches = data["pitch"]
        confidences = data["confidence"]
        frame_time = data["frame_time"]
        lines.append(
            f"{'Frame':>6}  {'Time (s)':>10}  {'Freq (Hz)':>10}  {'Confidence':>10}"
        )
        lines.append("-" * 42)
        for i, (freq, conf) in enumerate(zip(pitches, confidences)):
            t = i * frame_time
            lines.append(f"{i:6d}  {t:10.4f}  {freq:10.2f}  {conf:10.4f}")
    elif cmd_name == "formants":
        num_frames = data["num_frames"]
        frame_time = data["frame_time"]
        lines.append(
            f"{'Frame':>6}  {'Time':>8}  {'F1':>8}  {'F2':>8}  {'F3':>8}  {'F4':>8}"
        )
        lines.append("-" * 54)
        for i in range(num_frames):
            t = i * frame_time
            f1 = data["f1"][i] if i < len(data["f1"]) else 0
            f2 = data["f2"][i] if i < len(data["f2"]) else 0
            f3 = data["f3"][i] if i < len(data["f3"]) else 0
            f4 = data["f4"][i] if i < len(data["f4"]) else 0
            lines.append(
                f"{i:6d}  {t:8.4f}  {f1:8.1f}  {f2:8.1f}  {f3:8.1f}  {f4:8.1f}"
            )
    elif cmd_name == "get-partials":
        tracks = data["tracks"]
        lines.append(f"Partial tracks: {data['num_tracks']}")
        lines.append(f"Total frames: {data['total_frames']}")
        lines.append("")
        for i, track in enumerate(tracks):
            avg_freq = sum(track["freq"]) / len(track["freq"]) if track["freq"] else 0
            avg_amp = sum(track["amp"]) / len(track["amp"]) if track["amp"] else 0
            lines.append(
                f"Track {i:3d}: frames {track['start_frame']}-{track['end_frame']}, "
                f"avg freq {avg_freq:.1f} Hz, avg amp {avg_amp:.6f}"
            )
    elif cmd_name == "peak":
        level, pos = data
        lines.append(f"Peak level: {level:.6f}")
        lines.append(f"Peak position: {pos}")
    else:
        lines.append(str(data))
    return "\n".join(lines)


def _format_csv(cmd_name: str, data) -> str:
    lines = []
    if cmd_name == "pitch":
        pitches = data["pitch"]
        confidences = data["confidence"]
        frame_time = data["frame_time"]
        lines.append("frame,time,frequency,confidence")
        for i, (freq, conf) in enumerate(zip(pitches, confidences)):
            t = i * frame_time
            lines.append(f"{i},{t},{freq},{conf}")
    elif cmd_name == "formants":
        num_frames = data["num_frames"]
        frame_time = data["frame_time"]
        lines.append("frame,time,f1,b1,f2,b2,f3,b3,f4,b4")
        for i in range(num_frames):
            t = i * frame_time
            row = [str(i), str(t)]
            for k in range(1, 5):
                fk = data[f"f{k}"]
                bk = data[f"b{k}"]
                row.append(str(fk[i]) if i < len(fk) else "")
                row.append(str(bk[i]) if i < len(bk) else "")
            lines.append(",".join(row))
    elif cmd_name == "get-partials":
        lines.append("track,start_frame,end_frame,avg_freq,avg_amp")
        for i, track in enumerate(data["tracks"]):
            avg_freq = sum(track["freq"]) / len(track["freq"]) if track["freq"] else 0
            avg_amp = sum(track["amp"]) / len(track["amp"]) if track["amp"] else 0
            lines.append(
                f"{i},{track['start_frame']},{track['end_frame']},{avg_freq},{avg_amp}"
            )
    elif cmd_name == "peak":
        level, pos = data
        lines.append("level,position")
        lines.append(f"{level},{pos}")
    else:
        lines.append(str(data))
    return "\n".join(lines)


# =============================================================================
# Utility command handlers
# =============================================================================


def handle_version() -> None:
    print(f"cycdp {cycdp.__version__}")


def handle_list(args: argparse.Namespace) -> None:
    category = args.category

    if category:
        if category not in CATEGORIES:
            print(f"Error: unknown category '{category}'", file=sys.stderr)
            print(f"Available: {', '.join(sorted(CATEGORIES))}", file=sys.stderr)
            sys.exit(1)
        _print_category(category)
    else:
        for cat in CATEGORIES:
            _print_category(cat)
            print()


def _print_category(category: str) -> None:
    desc = CATEGORIES[category]
    cmds = [
        (name, spec["help"])
        for name, spec in COMMANDS.items()
        if spec["category"] == category
    ]
    if not cmds:
        return
    print(f"{category}: {desc}")
    max_name = max(len(c[0]) for c in cmds)
    for name, help_text in sorted(cmds):
        print(f"  {name:<{max_name}}  {help_text}")


def handle_info(args: argparse.Namespace) -> None:
    input_path = args.input
    if not os.path.exists(input_path):
        print(f"Error: file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    buf = cycdp.read_file(input_path)
    duration = buf.frame_count / buf.sample_rate
    peak_level, peak_pos = cycdp.peak(buf)  # type: ignore[arg-type]

    print(f"File:        {input_path}")
    print(f"Duration:    {duration:.4f}s")
    print(f"Channels:    {buf.channels}")
    print(f"Sample rate: {buf.sample_rate} Hz")
    print(f"Frames:      {buf.frame_count}")
    print(f"Samples:     {buf.sample_count}")
    print(f"Peak level:  {peak_level:.6f} ({cycdp.gain_to_db(peak_level):.2f} dB)")
    print(f"Peak frame:  {peak_pos}")


# =============================================================================
# Main entry point
# =============================================================================


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return

    if args.command == "version":
        handle_version()
        return
    if args.command == "list":
        handle_list(args)
        return
    if args.command == "info":
        handle_info(args)
        return

    spec = COMMANDS[args.command]
    input_type = spec["input"]

    if input_type == "single":
        handle_single(args.command, spec, args)
    elif input_type == "dual":
        handle_dual(args.command, spec, args)
    elif input_type == "synth":
        handle_synth(args.command, spec, args)
    elif input_type == "analysis":
        handle_analysis(args.command, spec, args)


if __name__ == "__main__":
    main()
