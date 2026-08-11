"""
cycdp - Python bindings for CDP audio processing library.

Example usage:
    >>> import numpy as np
    >>> import cycdp
    >>> samples = np.array([0.5, 0.3, -0.2], dtype=np.float32)
    >>> cycdp.gain(samples, gain_factor=2.0)
    array([1. , 0.6, -0.4], dtype=float32)
    >>> cycdp.normalize(samples, target=1.0)
    array([1. , 0.6, -0.4], dtype=float32)
"""

from cycdp._core import (
    # Version and utilities
    version,
    gain_to_db,
    db_to_gain,
    # Constants
    FLAG_NONE,
    FLAG_CLIP,
    # Exception
    CDPError,
    # Classes
    Context,
    Buffer,
    # Low-level functions (work with Buffer objects)
    apply_gain,
    apply_gain_db,
    apply_normalize,
    apply_normalize_db,
    apply_phase_invert,
    get_peak,
    # High-level functions (accept any float32 buffer via memoryview)
    gain,
    gain_db,
    normalize,
    normalize_db,
    peak,
    # File I/O
    read_file,
    write_file,
    # Spatial/panning
    pan,
    pan_envelope,
    mirror,
    narrow,
    # Mixing
    mix,
    mix2,
    # Buffer utilities
    reverse,
    fade_in,
    fade_out,
    concat,
    # Channel operations
    to_mono,
    to_stereo,
    extract_channel,
    merge_channels,
    split_channels,
    interleave,
    # Spectral processing
    time_stretch,
    spectral_blur,
    modify_speed,
    pitch_shift,
    spectral_shift,
    spectral_stretch,
    filter_lowpass,
    filter_highpass,
    filter_bandpass,
    filter_notch,
    # Dynamics/effects
    gate,
    bitcrush,
    ring_mod,
    delay,
    chorus,
    flanger,
    # EQ and dynamics
    eq_parametric,
    envelope_follow,
    envelope_apply,
    compressor,
    limiter,
    # Envelope operations
    dovetail,
    tremolo,
    attack,
    # Distortion operations
    distort_overload,
    distort_reverse,
    distort_fractal,
    distort_shuffle,
    distort_cut,
    distort_mark,
    distort_repeat,
    distort_shift,
    distort_warp,
    # Reverb
    reverb,
    # Granular operations
    brassage,
    freeze,
    grain_cloud,
    grain_extend,
    texture_simple,
    texture_multi,
    # Extended granular operations
    grain_reorder,
    grain_rerhythm,
    grain_reverse,
    grain_timewarp,
    grain_repitch,
    grain_position,
    grain_omit,
    grain_duplicate,
    # Morphing/Cross-synthesis
    morph,
    morph_glide,
    cross_synth,
    # Native morph (original CDP algorithms)
    morph_glide_native,
    morph_bridge_native,
    morph_native,
    # Analysis
    pitch,
    formants,
    get_partials,
    # Spectral operations
    spectral_focus,
    spectral_hilite,
    spectral_fold,
    spectral_clean,
    # Experimental operations
    strange,
    brownian,
    crystal,
    fractal,
    quirk,
    chirikov,
    cantor,
    cascade,
    fracture,
    tesselate,
    # Playback/Time manipulation
    zigzag,
    iterate,
    stutter,
    bounce,
    drunk,
    loop,
    retime,
    scramble,
    # Scramble mode constants
    SCRAMBLE_SHUFFLE,
    SCRAMBLE_REVERSE,
    SCRAMBLE_SIZE_UP,
    SCRAMBLE_SIZE_DOWN,
    SCRAMBLE_LEVEL_UP,
    SCRAMBLE_LEVEL_DOWN,
    splinter,
    spin,
    rotor,
    # Synthesis
    synth_wave,
    synth_noise,
    synth_click,
    synth_chord,
    # Waveform constants
    WAVE_SINE,
    WAVE_SQUARE,
    WAVE_SAW,
    WAVE_RAMP,
    WAVE_TRIANGLE,
    # Pitch-synchronous operations (PSOW)
    psow_stretch,
    psow_grab,
    psow_dupl,
    psow_interp,
    # FOF extraction and synthesis (FOFEX)
    fofex_extract,
    fofex_extract_all,
    fofex_synth,
    fofex_repitch,
    # Spatial effects
    flutter,
    # Zigzag/Hover effect
    hover,
    # Silence constriction
    constrict,
    # Phase manipulation
    phase_invert,
    phase_stereo,
    # Granular texture
    wrappage,
)

__all__ = [
    # Version
    "version",
    # Utilities
    "gain_to_db",
    "db_to_gain",
    # Constants
    "FLAG_NONE",
    "FLAG_CLIP",
    # Exception
    "CDPError",
    # Classes
    "Context",
    "Buffer",
    # Low-level functions
    "apply_gain",
    "apply_gain_db",
    "apply_normalize",
    "apply_normalize_db",
    "apply_phase_invert",
    "get_peak",
    # High-level functions
    "gain",
    "gain_db",
    "normalize",
    "normalize_db",
    "phase_invert",
    "peak",
    # File I/O
    "read_file",
    "write_file",
    # Spatial/panning
    "pan",
    "pan_envelope",
    "mirror",
    "narrow",
    # Mixing
    "mix",
    "mix2",
    # Buffer utilities
    "reverse",
    "fade_in",
    "fade_out",
    "concat",
    # Channel operations
    "to_mono",
    "to_stereo",
    "extract_channel",
    "merge_channels",
    "split_channels",
    "interleave",
    # Spectral processing
    "time_stretch",
    "spectral_blur",
    "modify_speed",
    "pitch_shift",
    "spectral_shift",
    "spectral_stretch",
    "filter_lowpass",
    "filter_highpass",
    "filter_bandpass",
    "filter_notch",
    # Dynamics/effects
    "gate",
    "bitcrush",
    "ring_mod",
    "delay",
    "chorus",
    "flanger",
    # EQ and dynamics
    "eq_parametric",
    "envelope_follow",
    "envelope_apply",
    "compressor",
    "limiter",
    # Envelope operations
    "dovetail",
    "tremolo",
    "attack",
    # Distortion operations
    "distort_overload",
    "distort_reverse",
    "distort_fractal",
    "distort_shuffle",
    "distort_cut",
    "distort_mark",
    "distort_repeat",
    "distort_shift",
    "distort_warp",
    # Reverb
    "reverb",
    # Granular operations
    "brassage",
    "freeze",
    "grain_cloud",
    "grain_extend",
    "texture_simple",
    "texture_multi",
    # Extended granular operations
    "grain_reorder",
    "grain_rerhythm",
    "grain_reverse",
    "grain_timewarp",
    "grain_repitch",
    "grain_position",
    "grain_omit",
    "grain_duplicate",
    # Morphing/Cross-synthesis
    "morph",
    "morph_glide",
    "cross_synth",
    # Native morph (original CDP algorithms)
    "morph_glide_native",
    "morph_bridge_native",
    "morph_native",
    # Analysis
    "pitch",
    "formants",
    "get_partials",
    # Spectral operations
    "spectral_focus",
    "spectral_hilite",
    "spectral_fold",
    "spectral_clean",
    # Experimental operations
    "strange",
    "brownian",
    "crystal",
    "fractal",
    "quirk",
    "chirikov",
    "cantor",
    "cascade",
    "fracture",
    "tesselate",
    # Playback/Time manipulation
    "zigzag",
    "iterate",
    "stutter",
    "bounce",
    "drunk",
    "loop",
    "retime",
    "scramble",
    # Scramble mode constants
    "SCRAMBLE_SHUFFLE",
    "SCRAMBLE_REVERSE",
    "SCRAMBLE_SIZE_UP",
    "SCRAMBLE_SIZE_DOWN",
    "SCRAMBLE_LEVEL_UP",
    "SCRAMBLE_LEVEL_DOWN",
    # Waveset fragmentation
    "splinter",
    # Spatial effects
    "spin",
    "rotor",
    # Synthesis
    "synth_wave",
    "synth_noise",
    "synth_click",
    "synth_chord",
    # Waveform constants
    "WAVE_SINE",
    "WAVE_SQUARE",
    "WAVE_SAW",
    "WAVE_RAMP",
    "WAVE_TRIANGLE",
    # Pitch-synchronous operations (PSOW)
    "psow_stretch",
    "psow_grab",
    "psow_dupl",
    "psow_interp",
    # FOF extraction and synthesis (FOFEX)
    "fofex_extract",
    "fofex_extract_all",
    "fofex_synth",
    "fofex_repitch",
    # Spatial effects
    "flutter",
    # Zigzag/Hover effect
    "hover",
    # Silence constriction
    "constrict",
    # Phase manipulation ("phase_invert" is listed with the high-level
    # functions above -- it accepts both a Buffer and a raw float32 buffer)
    "phase_stereo",
    # Granular texture
    "wrappage",
]

__version__ = "0.1.2"
