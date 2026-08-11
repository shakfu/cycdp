"""Assert what operations do to the signal, not merely that they return.

M4: 48% of the suite's assertions were existence-only -- `result.sample_count
> 0` -- and where numeric assertions existed the tolerances were wide enough to
admit obvious breakage. `time_stretch(factor=2.0)` was asserted only to land
between 150% and 250% of the input duration; an implementation 25% wrong would
have shipped green.

Every expectation here was measured against the current implementation first,
then tightened to a bound that still catches a real regression.

These assertions originally targeted only direction and ordering for the
filters and EQ, because the shared overlap-add path lost a constant ~8.9 dB and
parametric EQ appeared not to track its requested gain. That turned out to be
one bug, not two: the synthesis normalization ignored the analysis/synthesis
window energy. With it fixed the gain is exact, so these now assert the real
numbers.
"""

from __future__ import annotations

import pytest
from conftest import (
    db,
    duration_seconds,
    energy_at,
    goertzel,
    make_sine,
    make_tones,
    peak_level,
    rms,
    to_mono_list,
)

import cycdp

LOW = 200.0
HIGH = 3000.0


# =============================================================================
# Filters
# =============================================================================


class TestFiltersActuallyFilter:
    """A filter must attenuate its stopband far more than its passband.

    The passband is also checked for unity gain now: the shared overlap-add
    path used to lose a constant ~8.9 dB, so a filter quietly halved the level
    of the band it was supposed to pass.
    """

    MIN_SEPARATION_DB = 40.0

    def separation(self, result, keep_hz, reject_hz):
        kept = energy_at(result, keep_hz)
        rejected = energy_at(result, reject_hz)
        return db(kept / max(rejected, 1e-12)), kept, rejected

    def test_lowpass_removes_the_high_tone(self, low_high):
        result = cycdp.filter_lowpass(low_high, 1000.0)
        sep, kept, rejected = self.separation(result, LOW, HIGH)
        assert sep > self.MIN_SEPARATION_DB, (
            f"lowpass at 1 kHz separated {LOW} Hz from {HIGH} Hz by only "
            f"{sep:.1f} dB (kept={kept:.5f}, rejected={rejected:.5f})"
        )

    def test_highpass_removes_the_low_tone(self, low_high):
        result = cycdp.filter_highpass(low_high, 1000.0)
        sep, kept, rejected = self.separation(result, HIGH, LOW)
        assert sep > self.MIN_SEPARATION_DB, (
            f"highpass at 1 kHz separated {HIGH} Hz from {LOW} Hz by only "
            f"{sep:.1f} dB (kept={kept:.5f}, rejected={rejected:.5f})"
        )

    def test_bandpass_keeps_only_the_band(self, low_high):
        result = cycdp.filter_bandpass(low_high, 100.0, 500.0)
        sep, kept, rejected = self.separation(result, LOW, HIGH)
        assert sep > self.MIN_SEPARATION_DB, (
            f"bandpass 100-500 Hz separated in-band from out-of-band by only "
            f"{sep:.1f} dB (kept={kept:.5f}, rejected={rejected:.5f})"
        )

    def test_notch_removes_the_targeted_tone(self, low_high):
        result = cycdp.filter_notch(low_high, HIGH, 600.0)
        sep, kept, rejected = self.separation(result, LOW, HIGH)
        assert sep > self.MIN_SEPARATION_DB, (
            f"notch at {HIGH} Hz separated it from {LOW} Hz by only "
            f"{sep:.1f} dB (kept={kept:.5f}, rejected={rejected:.5f})"
        )

    def test_lowpass_and_highpass_are_complementary(self, low_high):
        """The same cutoff must keep opposite halves of the signal."""
        lp = cycdp.filter_lowpass(low_high, 1000.0)
        hp = cycdp.filter_highpass(low_high, 1000.0)
        assert energy_at(lp, LOW) > energy_at(hp, LOW) * 100
        assert energy_at(hp, HIGH) > energy_at(lp, HIGH) * 100


class TestSpectralPathHasUnityGain:
    """Operations that should not change level must not change it.

    Every operation routed through cdp_spectral_synthesize lost a constant
    ~8.9 dB: both analysis and synthesis apply a Hann window, but the
    normalization divided only by the overlap factor and ignored the window
    energy, leaving a gain of sum(w^2)/N = 3/8.

    Measured on the steady-state portion, since the first and last frames are
    deliberately tapered by the overlap-add and are not expected to be unity.
    """

    @staticmethod
    def steady_state_gain(original, processed, freq=1000.0):
        src, dst = to_mono_list(original), to_mono_list(processed)
        lo, hi = 0.2, 0.8
        a = src[int(len(src) * lo) : int(len(src) * hi)]
        b = dst[int(len(dst) * lo) : int(len(dst) * hi)]
        return db(
            goertzel(b, freq, processed.sample_rate)
            / goertzel(a, freq, original.sample_rate)
        )

    @pytest.mark.parametrize(
        "name,operation",
        [
            ("eq_parametric 0dB", lambda b: cycdp.eq_parametric(b, 1000.0, 0.0, 2.0)),
            ("filter_lowpass", lambda b: cycdp.filter_lowpass(b, 5000.0)),
            ("filter_highpass", lambda b: cycdp.filter_highpass(b, 200.0)),
            ("time_stretch x1", lambda b: cycdp.time_stretch(b, 1.0)),
        ],
    )
    def test_pass_through_preserves_level(self, name, operation):
        sine = make_sine(1000.0, 1.0)
        gain = self.steady_state_gain(sine, operation(sine))
        assert gain == pytest.approx(0.0, abs=0.6), (
            f"{name} should pass 1 kHz through unchanged but changed it by "
            f"{gain:+.2f} dB"
        )


class TestParametricEq:
    """EQ must deliver the gain it was asked for, at the centre frequency."""

    @pytest.mark.parametrize("gain_db", [-18.0, -12.0, -6.0, 6.0, 12.0, 18.0])
    def test_requested_gain_is_delivered(self, gain_db):
        """Within 1 dB of the request across a wide range.

        The shared overlap-add normalization used to leave a constant -8.9 dB
        offset, so a +12 dB boost measured +3.1 dB and 0 dB was not a no-op.
        """
        sine = make_sine(1000.0)
        base = energy_at(sine, 1000.0)
        result = energy_at(cycdp.eq_parametric(sine, 1000.0, gain_db, 2.0), 1000.0)

        measured = db(result / base)
        assert measured == pytest.approx(gain_db, abs=1.0), (
            f"eq_parametric({gain_db:+} dB) delivered {measured:+.2f} dB"
        )

    def test_zero_gain_is_a_no_op(self):
        """The case that exposed the offset: 0 dB must change nothing."""
        sine = make_sine(1000.0)
        result = cycdp.eq_parametric(sine, 1000.0, 0.0, 2.0)
        measured = db(energy_at(result, 1000.0) / energy_at(sine, 1000.0))
        assert measured == pytest.approx(0.0, abs=0.5), (
            f"a 0 dB EQ changed the level by {measured:+.2f} dB"
        )


# =============================================================================
# Time and pitch
# =============================================================================


class TestTimeStretchAccuracy:
    """Duration must land near the requested factor.

    Measured error is 2-3%; the previous assertion allowed 50-250%.
    """

    @pytest.mark.parametrize("factor", [0.5, 2.0, 3.0])
    def test_duration_tracks_the_factor(self, sine, factor):
        result = cycdp.time_stretch(sine, factor)
        expected = duration_seconds(sine) * factor
        actual = duration_seconds(result)
        assert actual == pytest.approx(expected, rel=0.05), (
            f"time_stretch(factor={factor}) produced {actual:.4f}s, "
            f"expected ~{expected:.4f}s"
        )

    def test_pitch_is_preserved(self, sine):
        """Time stretching must not move the fundamental."""
        result = cycdp.time_stretch(sine, 2.0)
        assert energy_at(result, 440.0) > energy_at(result, 880.0) * 10
        assert energy_at(result, 440.0) > energy_at(result, 220.0) * 10


class TestPitchShiftAccuracy:
    """The fundamental must move to the expected frequency and vacate the old."""

    @pytest.mark.parametrize("semitones", [12, -12, 7])
    def test_fundamental_moves(self, sine, semitones):
        result = cycdp.pitch_shift(sine, semitones)
        target = 440.0 * 2 ** (semitones / 12.0)

        at_target = energy_at(result, target)
        at_original = energy_at(result, 440.0)

        assert at_target > 0.02, (
            f"pitch_shift({semitones}) left only {at_target:.5f} at the "
            f"expected {target:.1f} Hz"
        )
        assert at_target > at_original * 20, (
            f"pitch_shift({semitones}) left {at_original:.5f} at the original "
            f"440 Hz versus {at_target:.5f} at {target:.1f} Hz"
        )

    def test_duration_is_preserved(self, sine):
        result = cycdp.pitch_shift(sine, 7)
        assert duration_seconds(result) == pytest.approx(
            duration_seconds(sine), rel=0.05
        )


# =============================================================================
# Dynamics
# =============================================================================


class TestDynamics:
    def test_limiter_enforces_its_ceiling(self):
        loud = make_sine(440.0, 0.5, amplitude=0.9)
        result = cycdp.limiter(loud, threshold_db=-12.0)
        ceiling = 10 ** (-12.0 / 20.0)

        assert peak_level(loud) > ceiling, "test signal must exceed the ceiling"
        assert peak_level(result) == pytest.approx(ceiling, rel=0.1), (
            f"limiter at -12 dB produced a peak of {peak_level(result):.4f}, "
            f"expected ~{ceiling:.4f}"
        )

    def test_compressor_reduces_level_above_threshold(self):
        loud = make_sine(440.0, 0.5, amplitude=0.9)
        result = cycdp.compressor(loud, threshold_db=-20.0, ratio=8.0)
        assert rms(result) < rms(loud), "compression did not reduce the level"

    def test_higher_ratio_compresses_harder(self):
        loud = make_sine(440.0, 0.5, amplitude=0.9)
        gentle = cycdp.compressor(loud, threshold_db=-20.0, ratio=2.0)
        hard = cycdp.compressor(loud, threshold_db=-20.0, ratio=16.0)
        assert rms(hard) < rms(gentle), (
            f"ratio 16 ({rms(hard):.4f}) did not compress more than "
            f"ratio 2 ({rms(gentle):.4f})"
        )

    def test_gate_silences_below_threshold(self):
        quiet = make_sine(440.0, 0.3, amplitude=0.01)
        result = cycdp.gate(quiet, threshold_db=-20.0)
        assert rms(result) < rms(quiet) * 0.1, (
            f"gate left {rms(result):.6f} of a {rms(quiet):.6f} signal that is "
            f"well below the threshold"
        )

    def test_gate_passes_above_threshold(self):
        loud = make_sine(440.0, 0.3, amplitude=0.8)
        result = cycdp.gate(loud, threshold_db=-40.0)
        assert rms(result) > rms(loud) * 0.5, "gate suppressed a signal above threshold"


# =============================================================================
# Modulation and effects
# =============================================================================


class TestRingModulation:
    def test_produces_sidebands_and_suppresses_the_carrier(self):
        """Ring modulation of f by m yields f-m and f+m, with f suppressed."""
        carrier, modulator = 1000.0, 100.0
        result = cycdp.ring_mod(make_sine(carrier), modulator)

        lower = energy_at(result, carrier - modulator)
        upper = energy_at(result, carrier + modulator)
        at_carrier = energy_at(result, carrier)

        assert lower > 0.05, f"missing lower sideband: {lower:.5f}"
        assert upper > 0.05, f"missing upper sideband: {upper:.5f}"
        assert lower == pytest.approx(upper, rel=0.2), "sidebands are asymmetric"
        assert at_carrier < lower * 0.1, (
            f"carrier not suppressed: {at_carrier:.5f} against sidebands of {lower:.5f}"
        )


class TestReverb:
    def test_adds_a_tail_and_returns_stereo(self, sine):
        result = cycdp.reverb(sine, mix=0.5, decay_time=1.0)
        assert result.channels == 2
        assert duration_seconds(result) > duration_seconds(sine) + 0.5, (
            "reverb with a 1 s decay did not extend the signal"
        )

    def test_longer_decay_produces_a_longer_tail(self, sine):
        short = cycdp.reverb(sine, mix=0.5, decay_time=0.5)
        long = cycdp.reverb(sine, mix=0.5, decay_time=2.0)
        assert duration_seconds(long) > duration_seconds(short)


# =============================================================================
# Level operations
# =============================================================================


class TestLevelOperations:
    @pytest.mark.parametrize("factor", [0.25, 0.5, 2.0])
    def test_gain_scales_exactly(self, factor):
        sine = make_sine(440.0, 0.2, amplitude=0.3)
        result = cycdp.gain(memoryview(sine), gain_factor=factor)
        assert peak_level(result) == pytest.approx(0.3 * factor, rel=1e-4)

    @pytest.mark.parametrize("target", [0.5, 0.95, 1.0])
    def test_normalize_hits_its_target(self, target):
        sine = make_sine(440.0, 0.2, amplitude=0.2)
        result = cycdp.normalize(memoryview(sine), target=target)
        assert peak_level(result) == pytest.approx(target, rel=1e-4)

    def test_normalize_preserves_relative_levels(self):
        """Normalisation must scale, not reshape."""
        quiet = make_sine(440.0, 0.2, amplitude=0.1)
        before = energy_at(quiet, 440.0) / rms(quiet)
        result = cycdp.normalize(memoryview(quiet), target=0.9)
        after = energy_at(result, 440.0) / rms(result)
        assert after == pytest.approx(before, rel=1e-3)


# =============================================================================
# Channel operations
# =============================================================================


class TestChannelOperations:
    def test_to_stereo_preserves_content_in_both_channels(self, sine):
        stereo = cycdp.to_stereo(sine)
        assert stereo.channels == 2
        samples = stereo.to_list()
        assert samples[0::2] == pytest.approx(samples[1::2]), (
            "to_stereo produced channels that differ"
        )

    def test_to_mono_averages_without_changing_a_shared_signal(self, sine):
        """Mono -> stereo -> mono must round-trip."""
        back = cycdp.to_mono(cycdp.to_stereo(sine))
        assert energy_at(back, 440.0) == pytest.approx(energy_at(sine, 440.0), rel=1e-3)

    def test_mix2_sums_two_signals(self):
        a = make_sine(440.0, 0.2, amplitude=0.3)
        b = make_sine(1000.0, 0.2, amplitude=0.3)
        mixed = cycdp.mix2(a, b)
        assert energy_at(mixed, 440.0) > 0.1
        assert energy_at(mixed, 1000.0) > 0.1


# =============================================================================
# Buffer utilities
# =============================================================================


class TestBufferUtilities:
    def test_reverse_is_its_own_inverse(self):
        original = make_tones([300.0, 700.0], duration=0.1)
        back = cycdp.reverse(cycdp.reverse(original))
        assert back.to_list() == pytest.approx(original.to_list(), abs=1e-6)

    # A fade endpoint is "silent" at 80 dB below peak. An absolute epsilon is
    # the wrong bound here: float32 rounding leaves a few parts per million,
    # which is inaudible, while a broken fade would leave the full signal.
    SILENCE_BELOW_PEAK_DB = -80.0

    def test_fade_in_starts_silent_and_reaches_full_level(self, sine):
        result = cycdp.fade_in(sine, 0.2)
        samples = result.to_list()
        first = abs(samples[0])
        assert db(first / peak_level(result)) < self.SILENCE_BELOW_PEAK_DB, (
            f"fade-in started at {first:.3e} "
            f"({db(first / peak_level(result)):.1f} dB below peak)"
        )
        assert peak_level(result) == pytest.approx(peak_level(sine), rel=0.05)

    def test_fade_out_ends_silent(self, sine):
        result = cycdp.fade_out(sine, 0.2)
        last = abs(result.to_list()[-1])
        assert db(last / peak_level(result)) < self.SILENCE_BELOW_PEAK_DB, (
            f"fade-out ended at {last:.3e} "
            f"({db(last / peak_level(result)):.1f} dB below peak)"
        )

    def test_concat_lengths_add(self):
        a = make_sine(440.0, 0.2)
        b = make_sine(880.0, 0.3)
        joined = cycdp.concat([a, b])
        assert joined.frame_count == a.frame_count + b.frame_count
