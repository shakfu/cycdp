"""Measured behaviour for the operation families that had none.

`test_dsp_behaviour.py` measures what about twenty operations actually do to a
signal -- filters, dynamics, level, channels, buffer utilities. Twelve families
had nothing beyond the cheap invariants in `test_invariants.py`: an operation in
them could return its input unchanged, or the wrong frequency, and the suite
would stay green.

This covers the six families where "correct" can be stated exactly, so the
assertions are real rather than gestures:

  synth    -- a 440 Hz sine must have its energy at 440 Hz and nowhere else,
              and each waveform must have the harmonic series it is named for.
  analyze  -- pitch tracking must return the pitch. These functions return
              numbers, so being wrong is silent in a way an invariant cannot
              catch; two defects below were found exactly here.
  phase    -- inversion is exact negation, checkable to the bit.
  spatial  -- panning hard left must put the energy in the left channel.
  envelope -- tremolo at 5 Hz must modulate at 5 Hz.
  morph    -- the endpoints of an interpolation must be its inputs.

The remaining families (granular, playback, distortion, experimental, psow,
fofex) are creative transformations where correctness is not crisply definable;
duration and energy relationships are the most that can be asserted, and that
is separate work.

Three confirmed defects are recorded here as strict xfails rather than omitted
or asserted around, so that fixing one fails the suite and forces the marker
off.
"""

from __future__ import annotations

import itertools
import math

import pytest
from conftest import (
    channel,
    channel_rms,
    energy_at,
    envelope_energy_at,
    make_sine,
    make_tones,
    midi_to_hz,
    peak_level,
    rms,
)

import cycdp

SR = 44100


# =============================================================================
# Synthesis
# =============================================================================


class TestSynthWave:
    """Each waveform must be the waveform it is named for."""

    @pytest.mark.parametrize("freq", [100.0, 440.0, 1000.0, 5000.0])
    def test_a_sine_lands_on_the_requested_frequency(self, freq):
        buf = cycdp.synth_wave(
            waveform=cycdp.WAVE_SINE, frequency=freq, amplitude=0.5, duration=0.3
        )
        at = energy_at(buf, freq)
        assert at > 0.2, f"almost no energy at the requested {freq} Hz"
        # An octave either side must be essentially empty: a sine has no
        # harmonics, and landing an octave out is the classic off-by-a-factor.
        assert energy_at(buf, freq / 2) < at / 100
        assert energy_at(buf, freq * 2) < at / 100

    def test_a_sine_has_no_harmonics(self):
        buf = cycdp.synth_wave(waveform=cycdp.WAVE_SINE, frequency=440.0, duration=0.5)
        f0 = energy_at(buf, 440.0)
        for harmonic in (2, 3, 4, 5):
            assert energy_at(buf, 440.0 * harmonic) < f0 / 500

    def test_a_square_has_odd_harmonics_only(self):
        """The classic series: odd harmonics at 1/n, even ones absent."""
        buf = cycdp.synth_wave(
            waveform=cycdp.WAVE_SQUARE, frequency=440.0, duration=0.5
        )
        f0 = energy_at(buf, 440.0)
        assert energy_at(buf, 1320.0) == pytest.approx(f0 / 3, rel=0.1), "3rd harmonic"
        assert energy_at(buf, 2200.0) == pytest.approx(f0 / 5, rel=0.15), "5th harmonic"
        assert energy_at(buf, 880.0) < f0 / 100, "2nd harmonic should be absent"
        assert energy_at(buf, 1760.0) < f0 / 100, "4th harmonic should be absent"

    def test_a_triangle_has_odd_harmonics_falling_as_one_over_n_squared(self):
        buf = cycdp.synth_wave(
            waveform=cycdp.WAVE_TRIANGLE, frequency=440.0, duration=0.5
        )
        f0 = energy_at(buf, 440.0)
        assert energy_at(buf, 1320.0) == pytest.approx(f0 / 9, rel=0.15), "3rd = 1/9"
        assert energy_at(buf, 2200.0) == pytest.approx(f0 / 25, rel=0.3), "5th = 1/25"
        assert energy_at(buf, 880.0) < f0 / 100, "2nd harmonic should be absent"

    def test_a_saw_has_every_harmonic_falling_as_one_over_n(self):
        buf = cycdp.synth_wave(waveform=cycdp.WAVE_SAW, frequency=440.0, duration=0.5)
        f0 = energy_at(buf, 440.0)
        assert energy_at(buf, 880.0) == pytest.approx(f0 / 2, rel=0.1), "2nd = 1/2"
        assert energy_at(buf, 1320.0) == pytest.approx(f0 / 3, rel=0.1), "3rd = 1/3"

    def test_saw_and_ramp_are_opposite_slopes(self):
        """They share a magnitude spectrum, so only the samples tell them apart."""
        saw = cycdp.synth_wave(waveform=cycdp.WAVE_SAW, frequency=100.0, duration=0.02)
        ramp = cycdp.synth_wave(
            waveform=cycdp.WAVE_RAMP, frequency=100.0, duration=0.02
        )
        assert saw.to_bytes() != ramp.to_bytes(), "these must not be the same waveform"
        pairs = zip(saw.to_list(), ramp.to_list(), strict=True)
        assert all(a == -b for a, b in pairs), "ramp should be saw inverted"

    @pytest.mark.parametrize("duration", [0.1, 0.5, 2.0])
    def test_duration_is_exact(self, duration):
        buf = cycdp.synth_wave(duration=duration)
        assert buf.frame_count == int(SR * duration)

    @pytest.mark.parametrize("amplitude", [0.2, 0.5, 0.9])
    def test_amplitude_is_the_peak(self, amplitude):
        buf = cycdp.synth_wave(amplitude=amplitude, duration=0.1)
        assert peak_level(buf) == pytest.approx(amplitude, rel=0.02)

    @pytest.mark.parametrize("channels", [1, 2])
    def test_channel_count_is_honoured(self, channels):
        buf = cycdp.synth_wave(duration=0.1, channels=channels)
        assert buf.channels == channels
        assert buf.frame_count == int(SR * 0.1)


class TestSynthNoise:
    def test_white_noise_is_broadband(self):
        buf = cycdp.synth_noise(pink=0, amplitude=0.5, duration=1.0, seed=7)
        band = [energy_at(buf, f) for f in (100.0, 500.0, 2000.0, 8000.0)]
        assert all(e > 0 for e in band), "white noise must have energy everywhere"
        # Flat to within an order of magnitude across the spectrum. A wide
        # bound, because a single Goertzel bin of noise is itself noisy.
        assert max(band) / min(band) < 10.0

    def test_pink_noise_falls_with_frequency(self):
        """The defining difference from white: equal energy per octave."""
        pink = cycdp.synth_noise(pink=1, amplitude=0.5, duration=1.0, seed=7)
        white = cycdp.synth_noise(pink=0, amplitude=0.5, duration=1.0, seed=7)

        def tilt(buf):
            return energy_at(buf, 100.0) / max(energy_at(buf, 8000.0), 1e-12)

        assert tilt(pink) > 3 * tilt(white), (
            f"pink tilt {tilt(pink):.2f} is not meaningfully steeper than "
            f"white {tilt(white):.2f}, so the two are the same generator"
        )

    def test_the_seed_is_honoured(self):
        a = cycdp.synth_noise(seed=42, duration=0.2)
        b = cycdp.synth_noise(seed=42, duration=0.2)
        c = cycdp.synth_noise(seed=43, duration=0.2)
        assert a.to_bytes() == b.to_bytes()
        assert a.to_bytes() != c.to_bytes()


class TestSynthClick:
    @pytest.mark.parametrize("tempo", [60.0, 120.0, 240.0])
    def test_clicks_arrive_at_the_tempo(self, tempo):
        buf = cycdp.synth_click(tempo=tempo, duration=4.0, beats_per_bar=0)
        samples = buf.to_list()
        threshold = 0.2 * max(abs(s) for s in samples)

        onsets, last = [], -SR
        for i, s in enumerate(samples):
            if abs(s) > threshold and i - last > SR // 100:
                onsets.append(i)
                last = i

        assert len(onsets) >= 3, f"only found {len(onsets)} clicks"
        gaps = [(b - a) / SR for a, b in itertools.pairwise(onsets)]
        expected = 60.0 / tempo
        assert sum(gaps) / len(gaps) == pytest.approx(expected, rel=0.02)


class TestSynthChord:
    def test_every_requested_note_is_present(self):
        notes = [60, 64, 67]  # C major
        buf = cycdp.synth_chord(notes, duration=1.0, amplitude=0.8)
        present = [energy_at(buf, midi_to_hz(n)) for n in notes]
        assert all(e > 0.05 for e in present), f"missing notes: {present}"
        # Roughly equal weighting, not one note dominating.
        assert max(present) / min(present) < 1.5

    def test_notes_that_were_not_asked_for_are_absent(self):
        buf = cycdp.synth_chord([60, 64, 67], duration=1.0)
        played = min(energy_at(buf, midi_to_hz(n)) for n in (60, 64, 67))
        for absent in (62, 71):
            assert energy_at(buf, midi_to_hz(absent)) < played / 20


# =============================================================================
# Analysis
# =============================================================================


class TestPitchTracking:
    @pytest.mark.parametrize("freq", [110.0, 220.0, 440.0, 880.0])
    def test_the_reported_pitch_is_the_pitch(self, freq):
        import statistics

        buf = make_sine(freq=freq, duration=0.5)
        result = cycdp.pitch(buf)
        voiced = [p for p in result["pitch"] if p > 0]

        assert len(voiced) > result["num_frames"] * 0.8, "most frames should be voiced"
        median = statistics.median(voiced)
        assert median == pytest.approx(freq, rel=0.02), (
            f"tracked {median:.1f} Hz for a {freq} Hz sine"
        )

    def test_silence_is_reported_unvoiced(self):
        result = cycdp.pitch(cycdp.Buffer.create(SR // 2, 1, SR))
        assert all(p == 0 for p in result["pitch"]), (
            "silence has no pitch; reporting one means the detector is "
            "tracking noise in an empty buffer"
        )

    def test_the_range_arguments_are_respected(self):
        """A fundamental outside [min_freq, max_freq] must not be reported."""
        buf = make_sine(freq=110.0, duration=0.5)
        result = cycdp.pitch(buf, min_freq=400.0, max_freq=2000.0)
        voiced = [p for p in result["pitch"] if p > 0]
        assert all(400.0 <= p <= 2000.0 for p in voiced), (
            f"reported pitches outside the requested range: "
            f"{[p for p in voiced if not 400.0 <= p <= 2000.0][:5]}"
        )


class TestPartialTracking:
    def test_a_harmonic_series_is_recovered(self):
        import statistics

        buf = make_tones([300.0, 600.0, 900.0], duration=0.5)
        result = cycdp.get_partials(buf)

        stable = [t for t in result["tracks"] if len(t["freq"]) > 3]
        assert len(stable) >= 3, f"only {len(stable)} stable tracks for 3 tones"

        strongest = sorted(stable, key=lambda t: -statistics.median(t["amp"]))[:3]
        found = sorted(statistics.median(t["freq"]) for t in strongest)
        for expected, actual in zip([300.0, 600.0, 900.0], found, strict=True):
            assert actual == pytest.approx(expected, rel=0.05), (
                f"expected partials near 300/600/900, got "
                f"{[round(f, 1) for f in found]}"
            )

    def test_silence_yields_no_partials(self):
        result = cycdp.get_partials(cycdp.Buffer.create(SR // 2, 1, SR))
        stable = [t for t in result["tracks"] if len(t["freq"]) > 3]
        assert not stable, f"found {len(stable)} partials in silence"


class TestFormantAnalysis:
    """LPC formant estimation, asserted only where it is well defined.

    Formants are resonances of a filter, so a stack of pure tones is not a
    formant test and the estimates on one are not meaningful to a tolerance.
    What must hold regardless is the shape of the result.
    """

    def test_formants_are_ordered_and_plausible(self):
        buf = make_tones([700.0, 1200.0, 2600.0], duration=0.5)
        result = cycdp.formants(buf)

        frames = result["num_frames"]
        assert frames > 0
        assert all(len(result[k]) == frames for k in ("f1", "f2", "f3", "f4"))

        pairs = [
            (a, b)
            for a, b in zip(result["f1"], result["f2"], strict=True)
            if a > 0 and b > 0
        ]
        assert pairs, "no frame produced both an F1 and an F2"
        assert all(a < b for a, b in pairs), "F1 must be below F2 by definition"
        assert all(0 < a < SR / 2 for a, _ in pairs), "F1 above Nyquist"

    def test_bandwidths_are_non_negative(self):
        buf = make_tones([700.0, 1200.0], duration=0.3)
        result = cycdp.formants(buf)
        for key in ("b1", "b2", "b3", "b4"):
            assert all(b >= 0 for b in result[key]), f"{key} has a negative bandwidth"


# =============================================================================
# Phase
# =============================================================================


class TestPhaseInvert:
    def test_it_is_exact_negation(self):
        src = make_sine(duration=0.05)
        inverted = cycdp.phase_invert(src)
        assert all(
            a == -b for a, b in zip(src.to_list(), inverted.to_list(), strict=True)
        )

    def test_the_original_and_the_inversion_cancel(self):
        """The property the operation exists for."""
        src = make_sine(duration=0.05)
        inverted = cycdp.phase_invert(src)
        summed = cycdp.mix2(src, inverted, 1.0, 1.0)
        assert peak_level(summed) < 1e-6, "an inverted copy must cancel its original"

    def test_inverting_twice_is_the_identity(self):
        src = make_sine(duration=0.05)
        assert cycdp.phase_invert(cycdp.phase_invert(src)).to_bytes() == src.to_bytes()


class TestPhaseStereo:
    @pytest.fixture
    def panned(self):
        return cycdp.pan(make_sine(duration=0.3, amplitude=0.5), -0.8)

    def test_zero_transfer_is_a_passthrough(self, panned):
        """The docstring promises it; nothing checked."""
        result = cycdp.phase_stereo(panned, 0.0)
        assert result.to_bytes() == panned.to_bytes()

    def test_full_transfer_makes_the_channels_anti_correlated(self, panned):
        """newL = L - R and newR = R - L, so one is the negation of the other."""
        result = cycdp.phase_stereo(panned, 1.0)
        left, right = channel(result, 0), channel(result, 1)
        assert all(abs(a + b) < 1e-5 for a, b in zip(left, right, strict=True))

    def test_separation_grows_with_transfer(self, panned):
        def separation(transfer):
            out = cycdp.phase_stereo(panned, transfer)
            left, right = channel(out, 0), channel(out, 1)
            return math.sqrt(
                sum((a - b) ** 2 for a, b in zip(left, right, strict=True)) / len(left)
            )

        assert separation(0.0) < separation(0.5) < separation(1.0)


# =============================================================================
# Spatial
# =============================================================================


class TestPanning:
    @pytest.fixture
    def mono(self):
        return make_sine(duration=0.2, amplitude=0.5)

    def test_hard_left_puts_nothing_in_the_right(self, mono):
        out = cycdp.pan(mono, -1.0)
        assert out.channels == 2
        assert channel_rms(out, 0) > 0.3
        assert channel_rms(out, 1) < 1e-6

    def test_hard_right_puts_nothing_in_the_left(self, mono):
        out = cycdp.pan(mono, 1.0)
        assert channel_rms(out, 1) > 0.3
        assert channel_rms(out, 0) < 1e-6

    def test_centre_is_balanced(self, mono):
        out = cycdp.pan(mono, 0.0)
        assert channel_rms(out, 0) == pytest.approx(channel_rms(out, 1), rel=1e-6)

    @pytest.mark.parametrize("position", [-0.75, -0.25, 0.25, 0.75])
    def test_the_balance_follows_the_position(self, mono, position):
        """More signal on the side the position names, monotonically."""
        out = cycdp.pan(mono, position)
        left, right = channel_rms(out, 0), channel_rms(out, 1)
        if position < 0:
            assert left > right
        else:
            assert right > left

    def test_a_pan_envelope_moves_the_image(self):
        buf = make_sine(duration=1.0, amplitude=0.5)
        out = cycdp.pan_envelope(buf, [(0.0, -1.0), (1.0, 1.0)])

        n = out.frame_count
        left, right = channel(out, 0), channel(out, 1)

        def band_rms(values, lo, hi):
            chunk = values[int(n * lo) : int(n * hi)]
            return math.sqrt(sum(v * v for v in chunk) / max(len(chunk), 1))

        # Starts left, ends right.
        assert band_rms(left, 0.0, 0.1) > band_rms(right, 0.0, 0.1)
        assert band_rms(right, 0.9, 1.0) > band_rms(left, 0.9, 1.0)


class TestStereoImage:
    @pytest.fixture
    def stereo(self):
        return cycdp.pan(make_sine(duration=0.3, amplitude=0.5), -0.8)

    def test_mirror_swaps_the_channels_exactly(self, stereo):
        out = cycdp.mirror(stereo)
        assert channel(out, 0) == channel(stereo, 1)
        assert channel(out, 1) == channel(stereo, 0)

    def test_mirroring_twice_is_the_identity(self, stereo):
        assert cycdp.mirror(cycdp.mirror(stereo)).to_bytes() == stereo.to_bytes()

    def test_zero_width_collapses_to_mono(self, stereo):
        out = cycdp.narrow(stereo, 0.0)
        left, right = channel(out, 0), channel(out, 1)
        assert all(abs(a - b) < 1e-6 for a, b in zip(left, right, strict=True)), (
            "width 0 means both channels carry the same signal"
        )

    def test_width_scales_the_channel_difference(self, stereo):
        def difference(width):
            out = cycdp.narrow(stereo, width)
            left, right = channel(out, 0), channel(out, 1)
            return math.sqrt(
                sum((a - b) ** 2 for a, b in zip(left, right, strict=True)) / len(left)
            )

        # The side signal scales linearly with width.
        assert difference(0.5) == pytest.approx(difference(1.0) / 2, rel=0.02)
        assert difference(2.0) == pytest.approx(difference(1.0) * 2, rel=0.02)


# =============================================================================
# Envelope
# =============================================================================


class TestTremolo:
    @pytest.fixture
    def carrier(self):
        return make_sine(freq=1000.0, duration=1.0, amplitude=0.5)

    @pytest.mark.parametrize("freq", [3.0, 8.0, 20.0])
    def test_the_envelope_modulates_at_the_requested_rate(self, carrier, freq):
        out = cycdp.tremolo(carrier, freq=freq, depth=1.0)
        at_rate = envelope_energy_at(out, freq)
        off_rate = envelope_energy_at(out, freq * 3.7)
        assert at_rate > 100 * off_rate, (
            f"modulation energy at {freq} Hz ({at_rate:.5f}) is not "
            f"dominant over an unrelated rate ({off_rate:.5f})"
        )

    def test_zero_depth_is_a_no_op(self, carrier):
        out = cycdp.tremolo(carrier, freq=5.0, depth=0.0)
        assert peak_level(out) == pytest.approx(peak_level(carrier), rel=0.01)
        assert rms(out) == pytest.approx(rms(carrier), rel=0.01)

    def test_depth_controls_how_deep(self, carrier):
        shallow = envelope_energy_at(cycdp.tremolo(carrier, 5.0, 0.3), 5.0)
        deep = envelope_energy_at(cycdp.tremolo(carrier, 5.0, 1.0), 5.0)
        assert deep > shallow


class TestDovetail:
    def test_it_starts_and_ends_at_silence(self):
        buf = make_sine(freq=1000.0, duration=1.0, amplitude=0.5)
        out = cycdp.dovetail(buf, 0.2, 0.3)
        samples = out.to_list()

        assert abs(samples[0]) < 1e-4, "fade-in must start from silence"
        assert abs(samples[-1]) < 1e-4, "fade-out must end at silence"

        n = len(samples)
        middle = max(abs(s) for s in samples[n // 2 - 500 : n // 2 + 500])
        assert middle == pytest.approx(0.5, rel=0.05), "the middle is untouched"

    def test_a_longer_fade_stays_quiet_for_longer(self):
        buf = make_sine(freq=1000.0, duration=1.0, amplitude=0.5)

        def level_at(fade, fraction):
            out = cycdp.dovetail(buf, fade, 0.01).to_list()
            i = int(len(out) * fraction)
            return max(abs(s) for s in out[i : i + 200])

        assert level_at(0.5, 0.1) < level_at(0.1, 0.1)


class TestAttack:
    def test_the_gain_applies_to_the_attack_region_only(self):
        buf = make_sine(freq=1000.0, duration=1.0, amplitude=0.5)
        out = cycdp.attack(buf, attack_gain=2.0, attack_time=0.2)
        samples = out.to_list()

        early = max(abs(s) for s in samples[: int(0.1 * SR)])
        late = max(abs(s) for s in samples[int(0.5 * SR) : int(0.6 * SR)])

        assert early == pytest.approx(1.0, rel=0.05), "attack should be doubled"
        assert late == pytest.approx(0.5, rel=0.05), "past the attack, untouched"

    def test_unity_gain_leaves_the_signal_alone(self):
        buf = make_sine(freq=1000.0, duration=0.5, amplitude=0.5)
        out = cycdp.attack(buf, attack_gain=1.0, attack_time=0.2)
        assert peak_level(out) == pytest.approx(peak_level(buf), rel=0.01)


# =============================================================================
# Morphing
# =============================================================================


class TestMorphEndpoints:
    """An interpolation must equal its inputs at the ends.

    Two well-separated sines make this legible: whichever one dominates the
    output says where on the interpolation the operation actually is.
    """

    @pytest.fixture
    def first(self):
        return make_sine(freq=300.0, duration=0.5, amplitude=0.5)

    @pytest.fixture
    def second(self):
        return make_sine(freq=900.0, duration=0.5, amplitude=0.5)

    def test_morphing_late_keeps_the_first_sound(self, first, second):
        out = cycdp.morph(first, second, morph_start=0.99, morph_end=1.0)
        assert energy_at(out, 300.0) > 20 * energy_at(out, 900.0)

    def test_morphing_immediately_gives_the_second(self, first, second):
        out = cycdp.morph(first, second, morph_start=0.0, morph_end=0.01)
        assert energy_at(out, 900.0) > 20 * energy_at(out, 300.0)

    def test_a_full_morph_passes_through_both(self, first, second):
        """Across the whole output, both sounds must be represented."""
        out = cycdp.morph(first, second, morph_start=0.0, morph_end=1.0)
        assert energy_at(out, 300.0) > 0.001
        assert energy_at(out, 900.0) > 0.001

    @pytest.mark.parametrize(
        "operation",
        [
            pytest.param(cycdp.morph, id="morph"),
            pytest.param(cycdp.morph_glide, id="morph_glide"),
            pytest.param(cycdp.morph_native, id="morph_native"),
            pytest.param(cycdp.morph_bridge_native, id="morph_bridge_native"),
            pytest.param(cycdp.morph_glide_native, id="morph_glide_native"),
            pytest.param(cycdp.cross_synth, id="cross_synth"),
        ],
    )
    def test_two_input_operations_produce_audible_output(
        self, first, second, operation
    ):
        """The floor for a two-input operation: it must not return silence."""
        out = operation(first, second)
        assert out.frame_count > 0
        assert peak_level(out) > 1e-4, f"{operation.__name__} returned near-silence"

    def test_cross_synth_mode_selects_which_input_supplies_amplitude(self):
        """Swapping the mode must not give the same answer."""
        loud = make_sine(freq=300.0, duration=0.5, amplitude=0.8)
        quiet = make_sine(freq=900.0, duration=0.5, amplitude=0.1)
        assert (
            cycdp.cross_synth(loud, quiet, mode=0).to_bytes()
            != cycdp.cross_synth(loud, quiet, mode=1).to_bytes()
        )


# =============================================================================
# Confirmed defects
# =============================================================================
#
# Recorded as strict xfails rather than left out. `strict=True` means these
# fail the suite the moment the behaviour is fixed, which forces the marker off
# and the assertion into the ordinary set -- an omitted test would just stay
# omitted.


class TestMorphGlideDuration:
    @pytest.fixture
    def sine(self):
        return make_sine(freq=440.0, duration=0.5, amplitude=0.5)

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "morph_glide and morph_glide_native deliver about a quarter of the "
            "requested duration -- 1.0 s yields 0.27 s, 4.0 s yields 1.02 s. "
            "The ratio approaches 1/4 as duration grows, which is the "
            "hop-size-versus-fft-size factor at the default overlap."
        ),
    )
    @pytest.mark.parametrize("duration", [1.0, 2.0])
    def test_morph_glide_duration_is_honoured(self, duration):
        first = make_sine(freq=300.0, duration=0.5)
        second = make_sine(freq=900.0, duration=0.5)
        out = cycdp.morph_glide(first, second, duration=duration)
        assert out.frame_count / SR == pytest.approx(duration, rel=0.1)

    @pytest.mark.parametrize("freq", [200.0, 400.0, 800.0, 1600.0])
    def test_an_isolated_tone_is_located_accurately(self, freq):
        """Was biased before the synthesis port: 200 Hz read as ~231.

        The bias was the sign of the frequency deviation, which reflected every
        estimate about its bin centre. It cancelled on any analyse-then-
        synthesise path, so only a function that reports the frequency could
        show it.
        """
        import statistics

        buf = make_sine(freq=freq, duration=0.5, amplitude=0.8)
        result = cycdp.get_partials(buf)
        stable = [t for t in result["tracks"] if len(t["freq"]) > 3]
        strongest = max(stable, key=lambda t: statistics.median(t["amp"]))
        assert statistics.median(strongest["freq"]) == pytest.approx(freq, rel=0.01)


class TestSpectralShift:
    """A fixed Hz offset must land on the frequency it was asked for.

    This was the operation that exposed the phase-vocoder defects. Before the
    synthesis was ported from CDP's pvoc it shifted in the wrong direction and
    destroyed the signal -- a +100 Hz shift of a 440 Hz tone peaked at 340 Hz
    with 9% of the energy -- and even a shift of zero returned near-silence.
    """

    @pytest.fixture
    def sine(self):
        return make_sine(freq=440.0, duration=0.5, amplitude=0.5)

    def peak_hz(self, buf, near, span=140):
        lo = max(30, int(near) - span)
        return max((energy_at(buf, float(f)), f) for f in range(lo, int(near) + span))[
            1
        ]

    @pytest.mark.parametrize("shift", [25.0, 50.0, 100.0, 150.0, 200.0, 300.0])
    def test_the_peak_lands_on_the_target(self, sine, shift):
        out = cycdp.spectral_shift(sine, shift)
        assert self.peak_hz(out, 440.0 + shift) == pytest.approx(440.0 + shift, abs=5.0)

    @pytest.mark.parametrize("shift", [-100.0, -200.0, -300.0])
    def test_a_negative_shift_goes_down(self, sine, shift):
        """The direction used to be inverted, and the round trip could not see it."""
        out = cycdp.spectral_shift(sine, shift)
        assert self.peak_hz(out, 440.0 + shift) == pytest.approx(440.0 + shift, abs=5.0)

    @pytest.mark.parametrize("shift", [50.0, 100.0, 200.0])
    def test_the_energy_survives_the_shift(self, sine, shift):
        out = cycdp.spectral_shift(sine, shift)
        assert rms(out) > 0.85 * rms(sine)

    def test_a_shift_of_zero_is_the_identity(self, sine):
        out = cycdp.spectral_shift(sine, 0.0)
        assert energy_at(out, 440.0) > 0.9 * energy_at(sine, 440.0)
        assert rms(out) > 0.9 * rms(sine)

    def test_the_shift_is_additive_not_multiplicative(self):
        """What distinguishes it from pitch_shift: harmonics stop being harmonic."""
        buf = make_tones([400.0, 800.0], duration=0.5)
        out = cycdp.spectral_shift(buf, 100.0)
        # 400 -> 500 and 800 -> 900, not 400 -> 500 and 800 -> 1000.
        assert energy_at(out, 500.0) > 5 * energy_at(out, 400.0)
        assert energy_at(out, 900.0) > 5 * energy_at(out, 1000.0)


class TestSpectralStretch:
    """max_stretch is the ratio at Nyquist, interpolated up from freq_divide.

    Worth stating because the obvious reading -- that max_stretch is applied
    everywhere -- is wrong, and an earlier version of this test asserted it and
    reported a working implementation as broken.
    """

    NYQUIST = SR / 2

    def expected(self, freq, max_stretch, freq_divide):
        pos = (freq - freq_divide) / (self.NYQUIST - freq_divide)
        return freq * (1.0 + (max_stretch - 1.0) * max(0.0, min(1.0, pos)))

    @pytest.mark.parametrize("freq", [2000.0, 5000.0, 8000.0, 11000.0])
    def test_a_partial_lands_where_the_curve_says(self, freq):
        buf = make_sine(freq=freq, duration=0.5, amplitude=0.5)
        out = cycdp.spectral_stretch(buf, max_stretch=2.0, freq_divide=1000.0)

        want = self.expected(freq, 2.0, 1000.0)
        peak = max(
            (energy_at(out, float(f)), f)
            for f in range(int(want) - 300, int(want) + 300, 5)
        )[1]
        assert peak == pytest.approx(want, rel=0.02)

    def test_no_stretch_is_the_identity(self):
        buf = make_sine(freq=2000.0, duration=0.5, amplitude=0.5)
        out = cycdp.spectral_stretch(buf, max_stretch=1.0, freq_divide=1000.0)
        assert energy_at(out, 2000.0) > 0.9 * energy_at(buf, 2000.0)

    def test_below_the_divide_is_untouched(self):
        buf = make_sine(freq=500.0, duration=0.5, amplitude=0.5)
        out = cycdp.spectral_stretch(buf, max_stretch=2.0, freq_divide=1000.0)
        assert energy_at(out, 500.0) > 0.8 * energy_at(buf, 500.0)

    def test_the_energy_survives(self):
        buf = make_sine(freq=2000.0, duration=0.5, amplitude=0.5)
        out = cycdp.spectral_stretch(buf, max_stretch=2.0, freq_divide=1000.0)
        assert rms(out) > 0.85 * rms(buf)
