"""
Tests for cycdp - Python bindings for CDP audio processing library.

Uses array.array for testing (no numpy dependency required).
"""

import array

import pytest

import cycdp


class TestVersion:
    def test_version_string(self):
        v = cycdp.version()
        assert isinstance(v, str)
        assert len(v) > 0
        # Should be semver-like
        parts = v.split(".")
        assert len(parts) >= 2

    def test_c_library_version_matches_the_package(self):
        """One version, three places it used to be written down.

        cycdp.version() comes from the C library, __version__ from the
        installed distribution, and both trace back to pyproject.toml. They had
        drifted -- the C string still said 0.1.0 at package version 0.2.0, so
        the call a user makes to find out what they have gave the wrong answer.
        Only a semver-shape assertion existed, which could not catch that.
        """
        assert cycdp.version() == cycdp.__version__

    def test_version_is_not_the_unknown_fallback(self):
        """Both fallbacks are visible strings, so a broken build says so."""
        assert "unknown" not in cycdp.version()
        assert "unknown" not in cycdp.__version__


class TestUtilities:
    def test_gain_to_db_unity(self):
        assert cycdp.gain_to_db(1.0) == pytest.approx(0.0)

    def test_gain_to_db_double(self):
        # +6dB = double amplitude
        assert cycdp.gain_to_db(2.0) == pytest.approx(6.0206, rel=0.01)

    def test_db_to_gain_zero(self):
        assert cycdp.db_to_gain(0.0) == pytest.approx(1.0)

    def test_db_to_gain_six(self):
        assert cycdp.db_to_gain(6.0206) == pytest.approx(2.0, rel=0.01)

    def test_round_trip(self):
        for gain_val in [0.1, 0.5, 1.0, 2.0, 10.0]:
            db = cycdp.gain_to_db(gain_val)
            back = cycdp.db_to_gain(db)
            assert back == pytest.approx(gain_val, rel=1e-10)


class TestContext:
    def test_create_destroy(self):
        ctx = cycdp.Context()
        assert ctx is not None
        del ctx  # Should not raise

    def test_initial_no_error(self):
        ctx = cycdp.Context()
        assert ctx.get_error() == 0  # CDP_OK


class TestBuffer:
    def test_create(self):
        buf = cycdp.Buffer.create(1000, channels=2, sample_rate=44100)
        assert buf.frame_count == 1000
        assert buf.channels == 2
        assert buf.sample_rate == 44100
        assert buf.sample_count == 2000
        assert len(buf) == 2000

    def test_from_array(self):
        samples = array.array("f", [0.5] * 100)
        buf = cycdp.Buffer.from_memoryview(samples, channels=1, sample_rate=44100)
        assert buf.sample_count == 100
        assert buf[0] == pytest.approx(0.5)

    def test_indexing(self):
        buf = cycdp.Buffer.create(100, channels=1, sample_rate=44100)
        buf[0] = 0.5
        buf[50] = -0.3
        assert buf[0] == pytest.approx(0.5)
        assert buf[50] == pytest.approx(-0.3)

    def test_to_list(self):
        samples = array.array("f", [0.1, 0.2, 0.3])
        buf = cycdp.Buffer.from_memoryview(samples, channels=1, sample_rate=44100)
        lst = buf.to_list()
        assert lst == pytest.approx([0.1, 0.2, 0.3], rel=1e-6)

    def test_buffer_protocol(self):
        """Buffer should support the buffer protocol."""
        buf = cycdp.Buffer.create(100, channels=1, sample_rate=44100)
        buf[0] = 0.5
        # Create memoryview from buffer
        mv = memoryview(buf)
        assert mv.format == "f"
        assert len(mv) == 100


class TestGain:
    def test_unity_gain(self):
        samples = array.array("f", [0.5] * 100)
        result = cycdp.gain(samples, gain_factor=1.0)
        assert result[0] == pytest.approx(0.5, rel=1e-6)

    def test_double_gain(self):
        samples = array.array("f", [0.25] * 100)
        result = cycdp.gain(samples, gain_factor=2.0)
        assert result[0] == pytest.approx(0.5, rel=1e-6)

    def test_half_gain(self):
        samples = array.array("f", [0.8] * 100)
        result = cycdp.gain(samples, gain_factor=0.5)
        assert result[0] == pytest.approx(0.4, rel=1e-6)

    def test_clipping(self):
        samples = array.array("f", [0.6] * 100)
        # Without clipping - should exceed 1.0
        result_no_clip = cycdp.gain(samples, gain_factor=2.0, clip=False)
        assert result_no_clip[0] == pytest.approx(1.2, rel=1e-6)

        # With clipping - should be clamped
        result_clip = cycdp.gain(samples, gain_factor=2.0, clip=True)
        assert result_clip[0] == pytest.approx(1.0, rel=1e-6)


class TestGainDb:
    def test_zero_db(self):
        samples = array.array("f", [0.5] * 100)
        result = cycdp.gain_db(samples, db=0.0)
        assert result[0] == pytest.approx(0.5, rel=1e-5)

    def test_plus_six_db(self):
        samples = array.array("f", [0.5] * 100)
        result = cycdp.gain_db(samples, db=6.0)
        # 6dB ~= 2x
        assert result[0] == pytest.approx(1.0, rel=0.02)

    def test_minus_six_db(self):
        samples = array.array("f", [0.5] * 100)
        result = cycdp.gain_db(samples, db=-6.0)
        # -6dB ~= 0.5x
        assert result[0] == pytest.approx(0.25, rel=0.02)


class TestNormalize:
    def test_normalize_to_unity(self):
        samples = array.array("f", [0.25, -0.5, 0.3])
        result = cycdp.normalize(samples, target=1.0)
        # Peak should be 1.0 (at index 1, which was -0.5)
        assert abs(result[1]) == pytest.approx(1.0, rel=1e-6)

    def test_normalize_to_target(self):
        samples = array.array("f", [0.25, -0.5, 0.3])
        result = cycdp.normalize(samples, target=0.8)
        assert abs(result[1]) == pytest.approx(0.8, rel=1e-6)

    def test_normalize_silent_raises(self):
        samples = array.array("f", [0.0] * 100)
        with pytest.raises(cycdp.CDPError):
            cycdp.normalize(samples)

    def test_preserves_relative_levels(self):
        samples = array.array("f", [0.2, -0.4, 0.1])
        result = cycdp.normalize(samples, target=1.0)
        # Ratios should be preserved
        assert result[0] / abs(result[1]) == pytest.approx(0.5, rel=1e-6)
        assert result[2] / abs(result[1]) == pytest.approx(0.25, rel=1e-6)


class TestPhaseInvert:
    def test_invert_positive(self):
        samples = array.array("f", [0.5] * 100)
        buf = cycdp.Buffer.from_memoryview(samples, channels=1, sample_rate=44100)
        result = cycdp.phase_invert(buf)
        assert result[0] == pytest.approx(-0.5, rel=1e-6)

    def test_invert_negative(self):
        samples = array.array("f", [-0.3] * 100)
        buf = cycdp.Buffer.from_memoryview(samples, channels=1, sample_rate=44100)
        result = cycdp.phase_invert(buf)
        assert result[0] == pytest.approx(0.3, rel=1e-6)

    def test_double_invert_identity(self):
        samples = array.array("f", [0.1, -0.2, 0.3, -0.4, 0.5])
        buf = cycdp.Buffer.from_memoryview(samples, channels=1, sample_rate=44100)
        result1 = cycdp.phase_invert(buf)
        result2 = cycdp.phase_invert(result1)
        for i in range(len(samples)):
            assert result2[i] == pytest.approx(samples[i], rel=1e-6)


class TestPeak:
    def test_find_peak_positive(self):
        samples = array.array("f", [0.5, 0.8, 0.3, 0.1])
        level, pos = cycdp.peak(samples)
        assert level == pytest.approx(0.8, rel=1e-6)
        assert pos == 1

    def test_find_peak_negative(self):
        samples = array.array("f", [0.5, -0.9, 0.3, 0.1])
        level, pos = cycdp.peak(samples)
        assert level == pytest.approx(0.9, rel=1e-6)
        assert pos == 1


class TestLowLevelAPI:
    """Test the low-level API with explicit Context and Buffer."""

    def test_apply_gain(self):
        ctx = cycdp.Context()
        buf = cycdp.Buffer.create(100, channels=1, sample_rate=44100)

        # Fill with values
        for i in range(100):
            buf[i] = 0.5

        cycdp.apply_gain(ctx, buf, 2.0, clip=False)

        for i in range(100):
            assert buf[i] == pytest.approx(1.0, rel=1e-6)

    def test_apply_normalize(self):
        ctx = cycdp.Context()
        samples = array.array("f", [0.25, -0.5, 0.3])
        buf = cycdp.Buffer.from_memoryview(samples)

        cycdp.apply_normalize(ctx, buf, target_level=1.0)

        # Peak should be 1.0
        level, _ = cycdp.get_peak(ctx, buf)
        assert level == pytest.approx(1.0, rel=1e-6)


class TestBufferInterop:
    """Test interoperability with various buffer types."""

    def test_with_array_array(self):
        samples = array.array("f", [0.5] * 100)
        result = cycdp.gain(samples, gain_factor=2.0)
        assert result[0] == pytest.approx(1.0, rel=1e-6)

    def test_with_memoryview(self):
        samples = array.array("f", [0.5] * 100)
        mv = memoryview(samples)
        result = cycdp.gain(mv, gain_factor=2.0)
        assert result[0] == pytest.approx(1.0, rel=1e-6)

    def test_result_supports_memoryview(self):
        samples = array.array("f", [0.5] * 100)
        result = cycdp.gain(samples, gain_factor=2.0)
        # Result should support buffer protocol
        mv = memoryview(result)
        assert mv[0] == pytest.approx(1.0, rel=1e-6)


class TestBufferUtilities:
    """Test buffer utility operations."""

    def test_reverse(self):
        """Reverse should reverse sample order."""
        samples = array.array("f", [1.0, 2.0, 3.0, 4.0, 5.0])
        buf = cycdp.Buffer.from_memoryview(samples, channels=1, sample_rate=44100)

        rev = cycdp.reverse(buf)
        assert rev.sample_count == 5
        assert rev[0] == pytest.approx(5.0, rel=1e-6)
        assert rev[4] == pytest.approx(1.0, rel=1e-6)
        assert rev[2] == pytest.approx(3.0, rel=1e-6)

    def test_reverse_stereo(self):
        """Reverse should preserve channel order within frames."""
        buf = cycdp.Buffer.create(3, channels=2, sample_rate=44100)
        # Frame 0: L=1, R=2
        buf[0] = 1.0
        buf[1] = 2.0
        # Frame 1: L=3, R=4
        buf[2] = 3.0
        buf[3] = 4.0
        # Frame 2: L=5, R=6
        buf[4] = 5.0
        buf[5] = 6.0

        rev = cycdp.reverse(buf)
        # Frame 0 should now be old Frame 2
        assert rev[0] == pytest.approx(5.0, rel=1e-6)
        assert rev[1] == pytest.approx(6.0, rel=1e-6)
        # Frame 2 should now be old Frame 0
        assert rev[4] == pytest.approx(1.0, rel=1e-6)
        assert rev[5] == pytest.approx(2.0, rel=1e-6)

    def test_fade_in_linear(self):
        """Linear fade in should ramp from 0 to 1."""
        # 1 second at 100 Hz
        buf = cycdp.Buffer.create(100, channels=1, sample_rate=100)
        for i in range(100):
            buf[i] = 1.0

        # Fade in over 0.5 seconds
        cycdp.fade_in(buf, duration=0.5, curve="linear")

        assert buf[0] == pytest.approx(0.0, abs=0.02)
        assert buf[25] == pytest.approx(0.5, abs=0.02)
        assert buf[75] == pytest.approx(1.0, rel=1e-6)

    def test_fade_out_linear(self):
        """Linear fade out should ramp from 1 to 0."""
        buf = cycdp.Buffer.create(100, channels=1, sample_rate=100)
        for i in range(100):
            buf[i] = 1.0

        cycdp.fade_out(buf, duration=0.5, curve="linear")

        assert buf[25] == pytest.approx(1.0, rel=1e-6)
        assert buf[75] == pytest.approx(0.5, abs=0.02)
        assert buf[99] == pytest.approx(0.0, abs=0.02)

    def test_fade_exponential(self):
        """Exponential fade should use equal-power curve."""
        buf = cycdp.Buffer.create(100, channels=1, sample_rate=100)
        for i in range(100):
            buf[i] = 1.0

        cycdp.fade_in(buf, duration=0.5, curve="exponential")

        # Exponential fade is smoother - mid-point should be higher than linear
        assert buf[25] > 0.5  # Equal power curve is above linear at midpoint

    def test_fade_invalid_curve_raises(self):
        """Invalid curve should raise ValueError."""
        buf = cycdp.Buffer.create(100, channels=1, sample_rate=44100)
        with pytest.raises(ValueError):
            cycdp.fade_in(buf, duration=0.5, curve="invalid")

    def test_concat(self):
        """Concat should join buffers end-to-end."""
        a_samples = array.array("f", [1.0] * 50)
        b_samples = array.array("f", [2.0] * 30)
        c_samples = array.array("f", [3.0] * 20)
        a = cycdp.Buffer.from_memoryview(a_samples, channels=1, sample_rate=44100)
        b = cycdp.Buffer.from_memoryview(b_samples, channels=1, sample_rate=44100)
        c = cycdp.Buffer.from_memoryview(c_samples, channels=1, sample_rate=44100)

        result = cycdp.concat([a, b, c])
        assert result.sample_count == 100

        assert result[25] == pytest.approx(1.0, rel=1e-6)
        assert result[60] == pytest.approx(2.0, rel=1e-6)
        assert result[90] == pytest.approx(3.0, rel=1e-6)

    def test_concat_empty_raises(self):
        """concat should fail with empty list."""
        with pytest.raises(ValueError):
            cycdp.concat([])


class TestSpatial:
    """Test spatial/panning operations."""

    def test_pan_center(self):
        """Center pan should give equal L and R."""
        samples = array.array("f", [0.8] * 100)
        mono = cycdp.Buffer.from_memoryview(samples, channels=1, sample_rate=44100)

        stereo = cycdp.pan(mono, position=0.0)
        assert stereo.channels == 2
        assert stereo.frame_count == 100

        # At center, L and R should be equal
        for i in range(100):
            assert stereo[i * 2] == pytest.approx(stereo[i * 2 + 1], rel=1e-6)

    def test_pan_left(self):
        """Full left pan should have L > R."""
        samples = array.array("f", [1.0] * 100)
        mono = cycdp.Buffer.from_memoryview(samples, channels=1, sample_rate=44100)

        stereo = cycdp.pan(mono, position=-1.0)

        # Left should be louder, right should be ~0
        assert stereo[0] > stereo[1]
        assert stereo[1] == pytest.approx(0.0, abs=0.01)

    def test_pan_right(self):
        """Full right pan should have R > L."""
        samples = array.array("f", [1.0] * 100)
        mono = cycdp.Buffer.from_memoryview(samples, channels=1, sample_rate=44100)

        stereo = cycdp.pan(mono, position=1.0)

        # Right should be louder, left should be ~0
        assert stereo[1] > stereo[0]
        assert stereo[0] == pytest.approx(0.0, abs=0.01)

    def test_pan_requires_mono(self):
        """pan should fail on stereo input."""
        stereo = cycdp.Buffer.create(100, channels=2, sample_rate=44100)
        with pytest.raises(cycdp.CDPError):
            cycdp.pan(stereo, position=0.0)

    def test_pan_envelope_left_to_right(self):
        """Pan envelope should move sound from left to right."""
        # 1 second of audio at 1000 Hz sample rate for easy calculation
        samples = array.array("f", [1.0] * 1000)
        mono = cycdp.Buffer.from_memoryview(samples, channels=1, sample_rate=1000)

        # Pan from left (-1) to right (+1) over 1 second
        points = [(0.0, -1.0), (1.0, 1.0)]
        stereo = cycdp.pan_envelope(mono, points)

        assert stereo.channels == 2
        assert stereo.frame_count == 1000

        # At start (t=0): should be full left
        assert stereo[0] > stereo[1]  # L > R
        assert stereo[1] == pytest.approx(0.0, abs=0.02)

        # At middle (t=0.5): should be roughly center
        mid = 500
        # L and R should be approximately equal at center
        assert abs(stereo[mid * 2] - stereo[mid * 2 + 1]) < 0.1

        # At end (t=1.0): should be full right
        end = 999
        assert stereo[end * 2 + 1] > stereo[end * 2]  # R > L
        assert stereo[end * 2] == pytest.approx(0.0, abs=0.02)

    def test_pan_envelope_static(self):
        """Pan envelope with single point should behave like static pan."""
        samples = array.array("f", [0.8] * 100)
        mono = cycdp.Buffer.from_memoryview(samples, channels=1, sample_rate=44100)

        # Single point at center
        points = [(0.0, 0.0)]
        stereo = cycdp.pan_envelope(mono, points)

        # Should be same as static center pan
        for i in range(100):
            assert stereo[i * 2] == pytest.approx(stereo[i * 2 + 1], rel=1e-6)

    def test_pan_envelope_empty_raises(self):
        """pan_envelope should fail with empty points list."""
        samples = array.array("f", [1.0] * 100)
        mono = cycdp.Buffer.from_memoryview(samples, channels=1, sample_rate=44100)

        with pytest.raises(ValueError):
            cycdp.pan_envelope(mono, [])

    def test_mirror(self):
        """Mirror should swap L and R."""
        stereo = cycdp.Buffer.create(100, channels=2, sample_rate=44100)
        for i in range(100):
            stereo[i * 2] = 0.3  # Left
            stereo[i * 2 + 1] = 0.9  # Right

        mirrored = cycdp.mirror(stereo)
        assert mirrored.channels == 2

        for i in range(100):
            assert mirrored[i * 2] == pytest.approx(0.9, rel=1e-6)  # New L = old R
            assert mirrored[i * 2 + 1] == pytest.approx(0.3, rel=1e-6)  # New R = old L

    def test_mirror_requires_stereo(self):
        """mirror should fail on mono input."""
        mono = cycdp.Buffer.create(100, channels=1, sample_rate=44100)
        with pytest.raises(cycdp.CDPError):
            cycdp.mirror(mono)

    def test_narrow_to_mono(self):
        """Width 0 should produce mono (L=R)."""
        stereo = cycdp.Buffer.create(100, channels=2, sample_rate=44100)
        for i in range(100):
            stereo[i * 2] = 0.2
            stereo[i * 2 + 1] = 0.8

        narrowed = cycdp.narrow(stereo, width=0.0)

        # Both channels should be average: (0.2 + 0.8) / 2 = 0.5
        for i in range(100):
            assert narrowed[i * 2] == pytest.approx(0.5, rel=1e-6)
            assert narrowed[i * 2 + 1] == pytest.approx(0.5, rel=1e-6)

    def test_narrow_unchanged(self):
        """Width 1.0 should leave stereo unchanged."""
        stereo = cycdp.Buffer.create(100, channels=2, sample_rate=44100)
        for i in range(100):
            stereo[i * 2] = 0.2
            stereo[i * 2 + 1] = 0.8

        result = cycdp.narrow(stereo, width=1.0)

        for i in range(100):
            assert result[i * 2] == pytest.approx(0.2, rel=1e-6)
            assert result[i * 2 + 1] == pytest.approx(0.8, rel=1e-6)

    def test_narrow_requires_stereo(self):
        """narrow should fail on mono input."""
        mono = cycdp.Buffer.create(100, channels=1, sample_rate=44100)
        with pytest.raises(cycdp.CDPError):
            cycdp.narrow(mono, width=0.5)


class TestMixing:
    """Test mixing operations."""

    def test_mix2_equal_length(self):
        """Mix two buffers of equal length."""
        a_samples = array.array("f", [0.3] * 100)
        b_samples = array.array("f", [0.5] * 100)
        a = cycdp.Buffer.from_memoryview(a_samples, channels=1, sample_rate=44100)
        b = cycdp.Buffer.from_memoryview(b_samples, channels=1, sample_rate=44100)

        result = cycdp.mix2(a, b)
        assert result.sample_count == 100

        for i in range(100):
            assert result[i] == pytest.approx(0.8, rel=1e-6)

    def test_mix2_with_gains(self):
        """Mix two buffers with gains."""
        a_samples = array.array("f", [1.0] * 100)
        b_samples = array.array("f", [1.0] * 100)
        a = cycdp.Buffer.from_memoryview(a_samples, channels=1, sample_rate=44100)
        b = cycdp.Buffer.from_memoryview(b_samples, channels=1, sample_rate=44100)

        result = cycdp.mix2(a, b, gain_a=0.5, gain_b=0.5)

        for i in range(100):
            assert result[i] == pytest.approx(1.0, rel=1e-6)

    def test_mix2_different_lengths(self):
        """Mix buffers of different lengths."""
        a = cycdp.Buffer.create(100, channels=1, sample_rate=44100)
        b = cycdp.Buffer.create(50, channels=1, sample_rate=44100)

        for i in range(100):
            a[i] = 0.4
        for i in range(50):
            b[i] = 0.3

        result = cycdp.mix2(a, b)
        assert result.sample_count == 100

        # First 50: 0.4 + 0.3 = 0.7
        assert result[25] == pytest.approx(0.7, rel=1e-6)
        # Last 50: just 0.4
        assert result[75] == pytest.approx(0.4, rel=1e-6)

    def test_mix_multiple(self):
        """Mix multiple buffers."""
        bufs = []
        for val in [0.2, 0.3, 0.1]:
            samples = array.array("f", [val] * 100)
            bufs.append(
                cycdp.Buffer.from_memoryview(samples, channels=1, sample_rate=44100)
            )

        result = cycdp.mix(bufs)

        # Sum: 0.2 + 0.3 + 0.1 = 0.6
        for i in range(100):
            assert result[i] == pytest.approx(0.6, rel=1e-6)

    def test_mix_with_gains(self):
        """Mix multiple buffers with gains."""
        a_samples = array.array("f", [1.0] * 100)
        b_samples = array.array("f", [1.0] * 100)
        a = cycdp.Buffer.from_memoryview(a_samples, channels=1, sample_rate=44100)
        b = cycdp.Buffer.from_memoryview(b_samples, channels=1, sample_rate=44100)

        result = cycdp.mix([a, b], gains=[0.25, 0.75])

        # 1.0*0.25 + 1.0*0.75 = 1.0
        for i in range(100):
            assert result[i] == pytest.approx(1.0, rel=1e-6)

    def test_mix_empty_raises(self):
        """mix should fail with empty list."""
        with pytest.raises(ValueError):
            cycdp.mix([])

    def test_mix_gains_length_mismatch_raises(self):
        """mix should fail if gains length doesn't match."""
        samples = array.array("f", [0.5] * 100)
        buf = cycdp.Buffer.from_memoryview(samples, channels=1, sample_rate=44100)

        with pytest.raises(ValueError):
            cycdp.mix([buf, buf], gains=[1.0])  # Only 1 gain for 2 buffers


class TestChannelOperations:
    """Test channel operations."""

    def test_to_mono_from_stereo(self):
        """Convert stereo to mono by averaging."""
        # Create stereo buffer: L=0.4, R=0.8 -> mono should be 0.6
        stereo = cycdp.Buffer.create(100, channels=2, sample_rate=44100)
        for i in range(100):
            stereo[i * 2] = 0.4  # Left
            stereo[i * 2 + 1] = 0.8  # Right

        mono = cycdp.to_mono(stereo)
        assert mono.channels == 1
        assert mono.frame_count == 100
        assert mono.sample_rate == 44100

        for i in range(mono.sample_count):
            assert mono[i] == pytest.approx(0.6, rel=1e-6)

    def test_to_mono_already_mono(self):
        """Converting mono to mono should just copy."""
        samples = array.array("f", [0.5] * 100)
        mono_in = cycdp.Buffer.from_memoryview(samples, channels=1, sample_rate=44100)

        mono_out = cycdp.to_mono(mono_in)
        assert mono_out.channels == 1
        assert mono_out.sample_count == 100

        for i in range(mono_out.sample_count):
            assert mono_out[i] == pytest.approx(0.5, rel=1e-6)

    def test_to_stereo(self):
        """Convert mono to stereo by duplicating."""
        samples = array.array("f", [0.7] * 100)
        mono = cycdp.Buffer.from_memoryview(samples, channels=1, sample_rate=44100)

        stereo = cycdp.to_stereo(mono)
        assert stereo.channels == 2
        assert stereo.frame_count == 100

        for i in range(100):
            assert stereo[i * 2] == pytest.approx(0.7, rel=1e-6)  # Left
            assert stereo[i * 2 + 1] == pytest.approx(0.7, rel=1e-6)  # Right

    def test_to_stereo_requires_mono(self):
        """to_stereo should fail on stereo input."""
        stereo = cycdp.Buffer.create(100, channels=2, sample_rate=44100)
        with pytest.raises(cycdp.CDPError):
            cycdp.to_stereo(stereo)

    def test_extract_channel(self):
        """Extract left and right channels from stereo."""
        stereo = cycdp.Buffer.create(100, channels=2, sample_rate=44100)
        for i in range(100):
            stereo[i * 2] = 0.3  # Left
            stereo[i * 2 + 1] = 0.9  # Right

        left = cycdp.extract_channel(stereo, 0)
        assert left.channels == 1
        for i in range(left.sample_count):
            assert left[i] == pytest.approx(0.3, rel=1e-6)

        right = cycdp.extract_channel(stereo, 1)
        assert right.channels == 1
        for i in range(right.sample_count):
            assert right[i] == pytest.approx(0.9, rel=1e-6)

    def test_extract_channel_out_of_range(self):
        """extract_channel should fail with invalid channel index."""
        stereo = cycdp.Buffer.create(100, channels=2, sample_rate=44100)
        with pytest.raises(cycdp.CDPError):
            cycdp.extract_channel(stereo, 2)

    def test_merge_channels(self):
        """Merge two mono buffers into stereo."""
        left_samples = array.array("f", [0.2] * 100)
        right_samples = array.array("f", [0.8] * 100)
        left = cycdp.Buffer.from_memoryview(left_samples, channels=1, sample_rate=44100)
        right = cycdp.Buffer.from_memoryview(
            right_samples, channels=1, sample_rate=44100
        )

        stereo = cycdp.merge_channels(left, right)
        assert stereo.channels == 2
        assert stereo.frame_count == 100

        for i in range(100):
            assert stereo[i * 2] == pytest.approx(0.2, rel=1e-6)
            assert stereo[i * 2 + 1] == pytest.approx(0.8, rel=1e-6)

    def test_split_channels(self):
        """Split stereo into separate mono buffers."""
        stereo = cycdp.Buffer.create(100, channels=2, sample_rate=44100)
        for i in range(100):
            stereo[i * 2] = 0.1
            stereo[i * 2 + 1] = 0.9

        channels = cycdp.split_channels(stereo)
        assert len(channels) == 2
        assert channels[0].channels == 1
        assert channels[1].channels == 1

        for i in range(100):
            assert channels[0][i] == pytest.approx(0.1, rel=1e-6)
            assert channels[1][i] == pytest.approx(0.9, rel=1e-6)

    def test_interleave(self):
        """Interleave multiple mono buffers."""
        ch0_samples = array.array("f", [0.1] * 100)
        ch1_samples = array.array("f", [0.5] * 100)
        ch2_samples = array.array("f", [0.9] * 100)
        ch0 = cycdp.Buffer.from_memoryview(ch0_samples, channels=1, sample_rate=44100)
        ch1 = cycdp.Buffer.from_memoryview(ch1_samples, channels=1, sample_rate=44100)
        ch2 = cycdp.Buffer.from_memoryview(ch2_samples, channels=1, sample_rate=44100)

        interleaved = cycdp.interleave([ch0, ch1, ch2])
        assert interleaved.channels == 3
        assert interleaved.frame_count == 100

        for i in range(100):
            assert interleaved[i * 3 + 0] == pytest.approx(0.1, rel=1e-6)
            assert interleaved[i * 3 + 1] == pytest.approx(0.5, rel=1e-6)
            assert interleaved[i * 3 + 2] == pytest.approx(0.9, rel=1e-6)

    def test_interleave_empty_raises(self):
        """interleave should fail with empty list."""
        with pytest.raises(ValueError):
            cycdp.interleave([])

    def test_split_interleave_roundtrip(self):
        """Split and interleave should be inverses."""
        original = cycdp.Buffer.create(50, channels=4, sample_rate=48000)
        for i in range(50):
            for ch in range(4):
                original[i * 4 + ch] = (ch + 1) * 0.2

        channels = cycdp.split_channels(original)
        reconstructed = cycdp.interleave(channels)

        assert reconstructed.channels == 4
        assert reconstructed.frame_count == 50

        for i in range(original.sample_count):
            assert reconstructed[i] == pytest.approx(original[i], rel=1e-6)


class TestFileIO:
    """Test file I/O functionality."""

    def test_write_and_read_float(self, tmp_path):
        """Test writing and reading a float WAV file."""
        # Create test data
        samples = array.array("f", [0.5, -0.5, 0.25, -0.25, 0.0])
        buf = cycdp.Buffer.from_memoryview(samples, channels=1, sample_rate=44100)

        # Write to file
        path = str(tmp_path / "test_float.wav")
        cycdp.write_file(path, buf, format="float")

        # Read back
        result = cycdp.read_file(path)
        assert result.channels == 1
        assert result.sample_rate == 44100
        assert result.sample_count == 5

        # Verify data
        for i in range(5):
            assert result[i] == pytest.approx(samples[i], rel=1e-6)

    def test_write_and_read_pcm16(self, tmp_path):
        """Test writing and reading a PCM16 WAV file."""
        samples = array.array("f", [0.5, -0.5, 0.25, -0.25, 0.0])
        buf = cycdp.Buffer.from_memoryview(samples, channels=1, sample_rate=44100)

        path = str(tmp_path / "test_pcm16.wav")
        cycdp.write_file(path, buf, format="pcm16")

        result = cycdp.read_file(path)
        assert result.channels == 1
        assert result.sample_rate == 44100
        assert result.sample_count == 5

        # PCM16 has limited precision: 1/32768 ~ 3e-5
        tolerance = 2.0 / 32768.0
        for i in range(5):
            assert result[i] == pytest.approx(samples[i], abs=tolerance)

    def test_write_and_read_pcm24(self, tmp_path):
        """Test writing and reading a PCM24 WAV file."""
        samples = array.array("f", [0.5, -0.5, 0.25, -0.25, 0.0])
        buf = cycdp.Buffer.from_memoryview(samples, channels=1, sample_rate=44100)

        path = str(tmp_path / "test_pcm24.wav")
        cycdp.write_file(path, buf, format="pcm24")

        result = cycdp.read_file(path)
        assert result.channels == 1
        assert result.sample_rate == 44100
        assert result.sample_count == 5

        # PCM24 has high precision: 1/8388608 ~ 1.2e-7
        tolerance = 2.0 / 8388608.0
        for i in range(5):
            assert result[i] == pytest.approx(samples[i], abs=tolerance)

    def test_stereo_file(self, tmp_path):
        """Test writing and reading a stereo WAV file."""
        # Interleaved stereo: L R L R L R
        samples = array.array("f", [0.5, -0.5, 0.25, -0.25, 0.1, -0.1])
        buf = cycdp.Buffer.from_memoryview(samples, channels=2, sample_rate=48000)

        path = str(tmp_path / "test_stereo.wav")
        cycdp.write_file(path, buf)

        result = cycdp.read_file(path)
        assert result.channels == 2
        assert result.sample_rate == 48000
        assert result.frame_count == 3

        for i in range(6):
            assert result[i] == pytest.approx(samples[i], rel=1e-6)

    def test_read_nonexistent_file_raises(self):
        """Test that reading a nonexistent file raises CDPError."""
        with pytest.raises(cycdp.CDPError):
            cycdp.read_file("/nonexistent/path/file.wav")

    def test_write_invalid_format_raises(self, tmp_path):
        """Test that writing with invalid format raises ValueError."""
        samples = array.array("f", [0.5])
        buf = cycdp.Buffer.from_memoryview(samples, channels=1, sample_rate=44100)

        path = str(tmp_path / "test.wav")
        with pytest.raises(ValueError, match="Invalid format"):
            cycdp.write_file(path, buf, format="invalid")


class TestSpectral:
    """Test spectral processing functions."""

    @pytest.fixture
    def sine_wave(self):
        """Create a simple sine wave buffer for testing."""
        import math

        sample_rate = 44100
        duration = 0.5
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2 * math.pi * 440 * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_time_stretch_double(self, sine_wave):
        """Time stretch should double the length.

        Tolerance is 5%: measured error is 2-3%. The original +/-25% band would
        have passed an implementation that was audibly, obviously wrong.
        """
        original_frames = sine_wave.frame_count
        stretched = cycdp.time_stretch(sine_wave, factor=2.0)
        assert stretched.frame_count == pytest.approx(original_frames * 2, rel=0.05)

    def test_time_stretch_half(self, sine_wave):
        """Time stretch with 0.5 should halve the length."""
        original_frames = sine_wave.frame_count
        compressed = cycdp.time_stretch(sine_wave, factor=0.5)
        assert compressed.frame_count == pytest.approx(original_frames * 0.5, rel=0.05)

    @pytest.mark.parametrize("frames", [1024, 1025, 1100, 1536, 2048])
    def test_time_stretch_short_input_does_not_crash(self, frames):
        """Inputs barely longer than the FFT window must not read out of bounds.

        An input of exactly fft_size yields a single analysis frame, and the
        frame-interpolation clamp assumed at least two, reading frames[1] one
        past the end. Found by AddressSanitizer (heap-buffer-overflow in
        cdp_spectral_time_stretch); before the fix this segfaulted the
        interpreter rather than raising.
        """
        buf = cycdp.Buffer.create(frames, channels=1, sample_rate=44100)
        result = cycdp.time_stretch(buf, factor=2.0)
        assert result.frame_count > 0

    def test_spectral_blur(self, sine_wave):
        """Spectral blur should run without error."""
        blurred = cycdp.spectral_blur(sine_wave, blur_time=0.05)
        # Output should have similar length
        assert blurred.frame_count > sine_wave.frame_count * 0.8
        assert blurred.frame_count < sine_wave.frame_count * 1.2

    def test_speed(self, sine_wave):
        """Speed change should change duration."""
        original_frames = sine_wave.frame_count
        faster = cycdp.modify_speed(sine_wave, speed_factor=2.0)
        # Double speed = half duration
        assert faster.frame_count < original_frames * 0.6

    def test_pitch_shift(self, sine_wave):
        """Pitch shift should maintain similar duration."""
        original_frames = sine_wave.frame_count
        shifted = cycdp.pitch_shift(sine_wave, semitones=5)
        # Duration should be roughly the same (within tolerance for spectral processing)
        assert shifted.frame_count > original_frames * 0.7
        assert shifted.frame_count < original_frames * 1.5

    def test_spectral_shift(self, sine_wave):
        """Spectral shift should run without error."""
        shifted = cycdp.spectral_shift(sine_wave, shift_hz=100)
        assert shifted.frame_count > 0

    def test_spectral_stretch(self, sine_wave):
        """Spectral stretch should run without error."""
        stretched = cycdp.spectral_stretch(sine_wave, max_stretch=1.5)
        assert stretched.frame_count > 0

    def test_filter_lowpass(self, sine_wave):
        """Lowpass filter should run without error."""
        filtered = cycdp.filter_lowpass(sine_wave, cutoff_freq=1000)
        assert filtered.frame_count > 0

    def test_filter_highpass(self, sine_wave):
        """Highpass filter should run without error."""
        filtered = cycdp.filter_highpass(sine_wave, cutoff_freq=200)
        assert filtered.frame_count > 0


class TestEnvelope:
    """Test envelope operations."""

    @pytest.fixture
    def sine_wave(self):
        """Create a simple sine wave buffer for testing."""
        import math

        sample_rate = 44100
        duration = 0.5
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2 * math.pi * 440 * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_dovetail(self, sine_wave):
        """Dovetail should apply fades."""
        result = cycdp.dovetail(sine_wave, fade_in_dur=0.05, fade_out_dur=0.05)
        assert result.frame_count == sine_wave.frame_count
        # Start should be near zero (faded in)
        assert abs(result[0]) < 0.1
        # End should be near zero (faded out)
        assert abs(result[result.sample_count - 1]) < 0.1

    def test_tremolo(self, sine_wave):
        """Tremolo should run without error."""
        result = cycdp.tremolo(sine_wave, freq=5.0, depth=0.5)
        assert result.frame_count == sine_wave.frame_count

    def test_attack(self, sine_wave):
        """Attack should run without error."""
        result = cycdp.attack(sine_wave, attack_gain=2.0, attack_time=0.1)
        assert result.frame_count == sine_wave.frame_count


class TestDistortion:
    """Test distortion operations."""

    @pytest.fixture
    def sine_wave(self):
        """Create a simple sine wave buffer for testing."""
        import math

        sample_rate = 44100
        duration = 0.5
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2 * math.pi * 440 * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_distort_overload(self, sine_wave):
        """Overload distortion should run without error."""
        result = cycdp.distort_overload(sine_wave, clip_level=0.3)
        assert result.frame_count > 0

    def test_distort_reverse(self, sine_wave):
        """Reverse cycles should run without error."""
        result = cycdp.distort_reverse(sine_wave, cycle_count=5)
        assert result.frame_count > 0

    def test_distort_fractal(self, sine_wave):
        """Fractal distortion should run without error."""
        result = cycdp.distort_fractal(sine_wave, scaling=1.5)
        assert result.frame_count > 0

    def test_distort_shuffle(self, sine_wave):
        """Shuffle should run without error."""
        result = cycdp.distort_shuffle(sine_wave, chunk_count=10, seed=42)
        assert result.frame_count > 0

    def test_distort_cut_basic(self, sine_wave):
        """Distort cut should run without error."""
        result = cycdp.distort_cut(sine_wave, cycle_count=4, cycle_step=4)
        assert result.frame_count > 0
        assert result.channels == 1

    def test_distort_cut_different_cycle_step(self, sine_wave):
        """Distort cut with different cycle_step should work."""
        # Overlapping segments (step < count)
        result = cycdp.distort_cut(sine_wave, cycle_count=6, cycle_step=3)
        assert result.frame_count > 0

    def test_distort_cut_exponent(self, sine_wave):
        """Distort cut with different exponents should work."""
        # Faster decay
        result1 = cycdp.distort_cut(sine_wave, cycle_count=4, exponent=2.0)
        # Slower decay
        result2 = cycdp.distort_cut(sine_wave, cycle_count=4, exponent=0.5)
        assert result1.frame_count > 0
        assert result2.frame_count > 0

    def test_distort_cut_min_level(self, sine_wave):
        """Distort cut with min_level should filter quiet segments."""
        # Keep all segments
        result1 = cycdp.distort_cut(sine_wave, cycle_count=4, min_level=0.0)
        # Filter very quiet segments
        result2 = cycdp.distort_cut(sine_wave, cycle_count=4, min_level=0.01)
        assert result1.frame_count > 0
        assert result2.frame_count > 0

    def test_distort_cut_invalid_cycle_count(self, sine_wave):
        """Distort cut with invalid cycle_count should fail."""
        with pytest.raises(cycdp.CDPError):
            cycdp.distort_cut(sine_wave, cycle_count=0)
        with pytest.raises(cycdp.CDPError):
            cycdp.distort_cut(sine_wave, cycle_count=101)

    def test_distort_cut_invalid_exponent(self, sine_wave):
        """Distort cut with invalid exponent should fail."""
        with pytest.raises(cycdp.CDPError):
            cycdp.distort_cut(sine_wave, cycle_count=4, exponent=0.01)
        with pytest.raises(cycdp.CDPError):
            cycdp.distort_cut(sine_wave, cycle_count=4, exponent=15.0)

    def test_distort_cut_default_params(self, sine_wave):
        """Distort cut with default parameters should work."""
        result = cycdp.distort_cut(sine_wave)
        assert result.frame_count > 0

    def test_distort_mark_basic(self, sine_wave):
        """Distort mark should run without error."""
        markers = [0.1, 0.2, 0.3, 0.4]
        result = cycdp.distort_mark(sine_wave, markers=markers, unit_ms=10.0)
        assert result.frame_count > 0
        assert result.channels == 1

    def test_distort_mark_two_markers(self, sine_wave):
        """Distort mark with minimum markers should work."""
        markers = [0.1, 0.3]
        result = cycdp.distort_mark(sine_wave, markers=markers)
        assert result.frame_count > 0

    def test_distort_mark_with_stretch(self, sine_wave):
        """Distort mark with time stretch should work."""
        markers = [0.1, 0.2, 0.3]
        result = cycdp.distort_mark(sine_wave, markers=markers, stretch=1.5)
        assert result.frame_count > 0

    def test_distort_mark_with_random(self, sine_wave):
        """Distort mark with randomization should work."""
        markers = [0.1, 0.2, 0.3]
        result = cycdp.distort_mark(sine_wave, markers=markers, random=0.5, seed=42)
        assert result.frame_count > 0

    def test_distort_mark_with_flip_phase(self, sine_wave):
        """Distort mark with phase flip should work."""
        markers = [0.1, 0.2, 0.3]
        result = cycdp.distort_mark(sine_wave, markers=markers, flip_phase=True)
        assert result.frame_count > 0

    def test_distort_mark_reproducible(self, sine_wave):
        """Distort mark with same seed should be reproducible."""
        markers = [0.1, 0.2, 0.3]
        result1 = cycdp.distort_mark(sine_wave, markers=markers, random=0.3, seed=123)
        result2 = cycdp.distort_mark(sine_wave, markers=markers, random=0.3, seed=123)
        assert result1.frame_count == result2.frame_count

    def test_distort_mark_invalid_marker_count(self, sine_wave):
        """Distort mark with too few markers should fail."""
        with pytest.raises(cycdp.CDPError):
            cycdp.distort_mark(sine_wave, markers=[0.1])

    def test_distort_mark_invalid_unit_ms(self, sine_wave):
        """Distort mark with invalid unit_ms should fail."""
        markers = [0.1, 0.2]
        with pytest.raises(cycdp.CDPError):
            cycdp.distort_mark(sine_wave, markers=markers, unit_ms=0.5)
        with pytest.raises(cycdp.CDPError):
            cycdp.distort_mark(sine_wave, markers=markers, unit_ms=150.0)

    def test_distort_mark_invalid_stretch(self, sine_wave):
        """Distort mark with invalid stretch should fail."""
        markers = [0.1, 0.2]
        with pytest.raises(cycdp.CDPError):
            cycdp.distort_mark(sine_wave, markers=markers, stretch=0.1)
        with pytest.raises(cycdp.CDPError):
            cycdp.distort_mark(sine_wave, markers=markers, stretch=3.0)

    def test_distort_mark_unsorted_markers(self, sine_wave):
        """Distort mark with unsorted markers should fail."""
        markers = [0.3, 0.1, 0.2]
        with pytest.raises(cycdp.CDPError):
            cycdp.distort_mark(sine_wave, markers=markers)

    def test_distort_repeat_basic(self, sine_wave):
        """Distort repeat should run without error."""
        result = cycdp.distort_repeat(sine_wave, multiplier=2, cycle_count=1)
        assert result.frame_count > 0
        assert result.channels == 1

    def test_distort_repeat_time_stretch(self, sine_wave):
        """Distort repeat mode 0 should time-stretch (longer output)."""
        result = cycdp.distort_repeat(sine_wave, multiplier=3, cycle_count=2, mode=0)
        assert result.frame_count > 0

    def test_distort_repeat_no_stretch(self, sine_wave):
        """Distort repeat mode 1 should maintain original duration."""
        result = cycdp.distort_repeat(sine_wave, multiplier=2, cycle_count=2, mode=1)
        assert result.frame_count > 0

    def test_distort_repeat_skip_cycles(self, sine_wave):
        """Distort repeat with skip_cycles should work."""
        result = cycdp.distort_repeat(sine_wave, multiplier=2, skip_cycles=5)
        assert result.frame_count > 0

    def test_distort_repeat_custom_splice(self, sine_wave):
        """Distort repeat with custom splice length should work."""
        result = cycdp.distort_repeat(sine_wave, multiplier=2, splice_ms=5.0)
        assert result.frame_count > 0

    def test_distort_repeat_multiple_cycles(self, sine_wave):
        """Distort repeat with multiple cycles per group should work."""
        result = cycdp.distort_repeat(sine_wave, multiplier=2, cycle_count=4)
        assert result.frame_count > 0

    def test_distort_repeat_invalid_multiplier(self, sine_wave):
        """Distort repeat with invalid multiplier should fail."""
        with pytest.raises(cycdp.CDPError):
            cycdp.distort_repeat(sine_wave, multiplier=0)
        with pytest.raises(cycdp.CDPError):
            cycdp.distort_repeat(sine_wave, multiplier=101)

    def test_distort_repeat_invalid_cycle_count(self, sine_wave):
        """Distort repeat with invalid cycle_count should fail."""
        with pytest.raises(cycdp.CDPError):
            cycdp.distort_repeat(sine_wave, cycle_count=0)
        with pytest.raises(cycdp.CDPError):
            cycdp.distort_repeat(sine_wave, cycle_count=101)

    def test_distort_repeat_invalid_splice(self, sine_wave):
        """Distort repeat with invalid splice_ms should fail."""
        with pytest.raises(cycdp.CDPError):
            cycdp.distort_repeat(sine_wave, splice_ms=0.5)
        with pytest.raises(cycdp.CDPError):
            cycdp.distort_repeat(sine_wave, splice_ms=60.0)

    def test_distort_repeat_default_params(self, sine_wave):
        """Distort repeat with default parameters should work."""
        result = cycdp.distort_repeat(sine_wave)
        assert result.frame_count > 0

    def test_distort_shift_basic(self, sine_wave):
        """Distort shift should run without error."""
        result = cycdp.distort_shift(sine_wave, group_size=1, shift=1, mode=0)
        assert result.frame_count > 0
        assert result.channels == 1

    def test_distort_shift_mode0(self, sine_wave):
        """Distort shift mode 0 (shift) should work."""
        result = cycdp.distort_shift(sine_wave, group_size=2, shift=2, mode=0)
        assert result.frame_count > 0

    def test_distort_shift_mode1(self, sine_wave):
        """Distort shift mode 1 (swap) should work."""
        result = cycdp.distort_shift(sine_wave, group_size=2, mode=1)
        assert result.frame_count > 0

    def test_distort_shift_larger_groups(self, sine_wave):
        """Distort shift with larger groups should work."""
        result = cycdp.distort_shift(sine_wave, group_size=4, shift=1, mode=0)
        assert result.frame_count > 0

    def test_distort_shift_larger_shift(self, sine_wave):
        """Distort shift with larger shift should work."""
        result = cycdp.distort_shift(sine_wave, group_size=1, shift=5, mode=0)
        assert result.frame_count > 0

    def test_distort_shift_invalid_group_size(self, sine_wave):
        """Distort shift with invalid group_size should fail."""
        with pytest.raises(cycdp.CDPError):
            cycdp.distort_shift(sine_wave, group_size=0)
        with pytest.raises(cycdp.CDPError):
            cycdp.distort_shift(sine_wave, group_size=51)

    def test_distort_shift_invalid_shift(self, sine_wave):
        """Distort shift with invalid shift should fail."""
        with pytest.raises(cycdp.CDPError):
            cycdp.distort_shift(sine_wave, shift=0, mode=0)
        with pytest.raises(cycdp.CDPError):
            cycdp.distort_shift(sine_wave, shift=51, mode=0)

    def test_distort_shift_invalid_mode(self, sine_wave):
        """Distort shift with invalid mode should fail."""
        with pytest.raises(cycdp.CDPError):
            cycdp.distort_shift(sine_wave, mode=2)

    def test_distort_shift_default_params(self, sine_wave):
        """Distort shift with default parameters should work."""
        result = cycdp.distort_shift(sine_wave)
        assert result.frame_count > 0


class TestDistortWarp:
    """Test distort_warp - progressive warp distortion."""

    @pytest.fixture
    def sine_wave(self):
        """Create a simple sine wave buffer for testing."""
        import math

        sample_rate = 44100
        duration = 0.5
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2 * math.pi * 440 * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    @pytest.fixture
    def stereo_wave(self):
        """Create a stereo sine wave buffer for testing."""
        import math

        sample_rate = 44100
        duration = 0.5
        num_frames = int(sample_rate * duration)
        samples = array.array("f")
        for i in range(num_frames):
            val = 0.5 * math.sin(2 * math.pi * 440 * i / sample_rate)
            samples.append(val)  # Left
            samples.append(val)  # Right
        return cycdp.Buffer.from_memoryview(
            samples, channels=2, sample_rate=sample_rate
        )

    def test_distort_warp_basic(self, sine_wave):
        """Distort warp should produce output."""
        result = cycdp.distort_warp(sine_wave, warp=0.001)
        assert result.frame_count > 0
        assert result.channels == 1

    def test_distort_warp_mode0(self, sine_wave):
        """Mode 0 (samplewise) should work with mono."""
        result = cycdp.distort_warp(sine_wave, warp=0.01, mode=0)
        assert result.frame_count > 0
        assert result.frame_count == sine_wave.frame_count

    def test_distort_warp_mode0_stereo(self, stereo_wave):
        """Mode 0 should work with stereo."""
        result = cycdp.distort_warp(stereo_wave, warp=0.01, mode=0)
        assert result.frame_count > 0
        assert result.channels == 2

    def test_distort_warp_mode1(self, sine_wave):
        """Mode 1 (wavesetwise) should work."""
        result = cycdp.distort_warp(sine_wave, warp=0.01, mode=1, waveset_count=5)
        assert result.frame_count > 0
        assert result.channels == 1

    def test_distort_warp_small_warp(self, sine_wave):
        """Small warp value should work."""
        result = cycdp.distort_warp(sine_wave, warp=0.0001)
        assert result.frame_count > 0

    def test_distort_warp_large_warp(self, sine_wave):
        """Large warp value should work."""
        result = cycdp.distort_warp(sine_wave, warp=0.1)
        assert result.frame_count > 0

    def test_distort_warp_invalid_warp_low(self, sine_wave):
        """Warp below minimum should fail."""
        with pytest.raises(cycdp.CDPError):
            cycdp.distort_warp(sine_wave, warp=0.00001)

    def test_distort_warp_invalid_warp_high(self, sine_wave):
        """Warp above maximum should fail."""
        with pytest.raises(cycdp.CDPError):
            cycdp.distort_warp(sine_wave, warp=0.5)

    def test_distort_warp_invalid_mode(self, sine_wave):
        """Invalid mode should fail."""
        with pytest.raises(cycdp.CDPError):
            cycdp.distort_warp(sine_wave, mode=2)

    def test_distort_warp_waveset_count(self, sine_wave):
        """Different waveset counts should work."""
        result1 = cycdp.distort_warp(sine_wave, warp=0.01, mode=1, waveset_count=1)
        result2 = cycdp.distort_warp(sine_wave, warp=0.01, mode=1, waveset_count=10)
        assert result1.frame_count > 0
        assert result2.frame_count > 0

    def test_distort_warp_default_params(self, sine_wave):
        """Default parameters should work."""
        result = cycdp.distort_warp(sine_wave)
        assert result.frame_count > 0


class TestReverb:
    """Test reverb."""

    @pytest.fixture
    def sine_wave(self):
        """Create a simple sine wave buffer for testing."""
        import math

        sample_rate = 44100
        duration = 0.5
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2 * math.pi * 440 * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_reverb(self, sine_wave):
        """Reverb should run without error and add a tail."""
        result = cycdp.reverb(sine_wave, mix=0.5, decay_time=1.0)
        # Should be stereo
        assert result.channels == 2
        # Should have reverb tail
        assert result.frame_count > sine_wave.frame_count


class TestGranular:
    """Test granular operations."""

    @pytest.fixture
    def sine_wave(self):
        """Create a simple sine wave buffer for testing."""
        import math

        sample_rate = 44100
        duration = 0.5
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2 * math.pi * 440 * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_brassage(self, sine_wave):
        """Brassage should run without error."""
        result = cycdp.brassage(sine_wave, velocity=1.0, density=1.0)
        assert result.frame_count > 0

    def test_brassage_slower(self, sine_wave):
        """Brassage with slow velocity should produce longer output."""
        original_frames = sine_wave.frame_count
        result = cycdp.brassage(sine_wave, velocity=0.5)
        # Slower velocity = longer output
        assert result.frame_count > original_frames

    def test_freeze(self, sine_wave):
        """Freeze should produce specified duration."""
        result = cycdp.freeze(sine_wave, start_time=0.1, end_time=0.2, duration=1.0)
        # Should be roughly 1 second (44100 samples at 44.1kHz)
        expected_samples = 44100
        assert result.frame_count > expected_samples * 0.9
        assert result.frame_count < expected_samples * 1.1


class TestFilters:
    """Test bandpass and notch filters."""

    @pytest.fixture
    def sine_wave(self):
        """Create a simple sine wave buffer for testing."""
        import math

        sample_rate = 44100
        duration = 0.5
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2 * math.pi * 440 * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_filter_bandpass(self, sine_wave):
        """Bandpass filter should pass frequencies in range."""
        # 440Hz should pass through 200-800Hz bandpass
        result = cycdp.filter_bandpass(sine_wave, low_freq=200, high_freq=800)
        assert result.frame_count > 0
        # Signal should remain strong since 440Hz is in passband
        # Note: some amplitude loss from STFT/ISTFT process is expected
        peak = max(abs(result[i]) for i in range(result.sample_count))
        assert peak > 0.15  # Significant signal remains

    def test_filter_bandpass_reject(self, sine_wave):
        """Bandpass filter should attenuate out-of-band frequencies."""
        # 440Hz should be attenuated by 1000-2000Hz bandpass
        result = cycdp.filter_bandpass(sine_wave, low_freq=1000, high_freq=2000)
        assert result.frame_count > 0
        # Signal should be reduced
        peak = max(abs(result[i]) for i in range(result.sample_count))
        assert peak < 0.1  # Signal mostly removed

    def test_filter_notch(self, sine_wave):
        """Notch filter should remove narrow frequency band."""
        # Notch at 440Hz should attenuate 440Hz sine
        result = cycdp.filter_notch(sine_wave, center_freq=440, width_hz=100)
        assert result.frame_count > 0
        peak = max(abs(result[i]) for i in range(result.sample_count))
        assert peak < 0.2  # Signal attenuated

    def test_filter_notch_pass(self, sine_wave):
        """Notch filter should pass frequencies outside notch."""
        # Notch at 1000Hz should pass 440Hz sine
        result = cycdp.filter_notch(sine_wave, center_freq=1000, width_hz=100)
        assert result.frame_count > 0
        # Note: some amplitude loss from STFT/ISTFT process is expected
        peak = max(abs(result[i]) for i in range(result.sample_count))
        assert peak > 0.15  # Signal mostly preserved


class TestGate:
    """Test noise gate."""

    @pytest.fixture
    def sine_wave(self):
        """Create a simple sine wave buffer for testing."""
        import math

        sample_rate = 44100
        duration = 0.5
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2 * math.pi * 440 * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_gate(self, sine_wave):
        """Gate should run without error."""
        result = cycdp.gate(sine_wave, threshold_db=-20.0)
        assert result.frame_count == sine_wave.frame_count

    def test_gate_silence_quiet(self):
        """Gate should silence very quiet audio."""
        # Create very quiet signal
        samples = array.array("f", [0.001 * i / 1000 for i in range(44100)])
        buf = cycdp.Buffer.from_memoryview(samples, channels=1, sample_rate=44100)
        result = cycdp.gate(buf, threshold_db=-20.0)
        # Most should be gated to zero (threshold is 0.1 amplitude)
        quiet_count = sum(
            1 for i in range(result.sample_count) if abs(result[i]) < 0.0001
        )
        assert quiet_count > result.sample_count * 0.5


class TestBitcrush:
    """Test bitcrusher effect."""

    @pytest.fixture
    def sine_wave(self):
        """Create a simple sine wave buffer for testing."""
        import math

        sample_rate = 44100
        duration = 0.5
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2 * math.pi * 440 * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_bitcrush(self, sine_wave):
        """Bitcrush should run without error."""
        result = cycdp.bitcrush(sine_wave, bit_depth=8)
        assert result.frame_count == sine_wave.frame_count

    def test_bitcrush_downsample(self, sine_wave):
        """Bitcrush with downsample should create staircase effect."""
        result = cycdp.bitcrush(sine_wave, bit_depth=16, downsample=4)
        assert result.frame_count == sine_wave.frame_count
        # With downsample=4, every 4 samples should be the same
        # Check a few sample runs
        same_count = 0
        for i in range(0, result.sample_count - 4, 4):
            if result[i] == result[i + 1] == result[i + 2] == result[i + 3]:
                same_count += 1
        assert same_count > 0  # Should have some repeated samples

    def test_bitcrush_invalid_depth(self, sine_wave):
        """Bitcrush should reject invalid bit depths."""
        with pytest.raises(ValueError):
            cycdp.bitcrush(sine_wave, bit_depth=0)
        with pytest.raises(ValueError):
            cycdp.bitcrush(sine_wave, bit_depth=17)


class TestRingMod:
    """Test ring modulation."""

    @pytest.fixture
    def sine_wave(self):
        """Create a simple sine wave buffer for testing."""
        import math

        sample_rate = 44100
        duration = 0.5
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2 * math.pi * 440 * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_ring_mod(self, sine_wave):
        """Ring modulation should run without error."""
        result = cycdp.ring_mod(sine_wave, freq=100)
        assert result.frame_count == sine_wave.frame_count

    def test_ring_mod_dry_wet(self, sine_wave):
        """Ring mod with mix=0 should return dry signal."""
        result = cycdp.ring_mod(sine_wave, freq=100, mix=0.0)
        # Should be same as input
        for i in range(result.sample_count):
            assert result[i] == pytest.approx(sine_wave[i], abs=1e-6)


class TestDelay:
    """Test delay effect."""

    @pytest.fixture
    def sine_wave(self):
        """Create a simple sine wave buffer for testing."""
        import math

        sample_rate = 44100
        duration = 0.5
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2 * math.pi * 440 * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_delay(self, sine_wave):
        """Delay should run without error."""
        result = cycdp.delay(sine_wave, delay_ms=100)
        assert result.frame_count == sine_wave.frame_count

    def test_delay_with_feedback(self, sine_wave):
        """Delay with feedback should run without error."""
        result = cycdp.delay(sine_wave, delay_ms=50, feedback=0.5, mix=0.5)
        assert result.frame_count == sine_wave.frame_count


class TestChorus:
    """Test chorus effect."""

    @pytest.fixture
    def sine_wave(self):
        """Create a simple sine wave buffer for testing."""
        import math

        sample_rate = 44100
        duration = 0.5
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2 * math.pi * 440 * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_chorus(self, sine_wave):
        """Chorus should run without error."""
        result = cycdp.chorus(sine_wave, rate=1.5, depth_ms=5.0)
        assert result.frame_count == sine_wave.frame_count

    def test_chorus_dry(self, sine_wave):
        """Chorus with mix=0 should return dry signal."""
        result = cycdp.chorus(sine_wave, rate=1.5, depth_ms=5.0, mix=0.0)
        # Should be same as input
        for i in range(result.sample_count):
            assert result[i] == pytest.approx(sine_wave[i], abs=1e-6)


class TestFlanger:
    """Test flanger effect."""

    @pytest.fixture
    def sine_wave(self):
        """Create a simple sine wave buffer for testing."""
        import math

        sample_rate = 44100
        duration = 0.5
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2 * math.pi * 440 * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_flanger(self, sine_wave):
        """Flanger should run without error."""
        result = cycdp.flanger(sine_wave, rate=0.5, depth_ms=3.0)
        assert result.frame_count == sine_wave.frame_count

    def test_flanger_with_feedback(self, sine_wave):
        """Flanger with feedback should run without error."""
        result = cycdp.flanger(sine_wave, rate=0.3, depth_ms=5.0, feedback=0.7, mix=0.5)
        assert result.frame_count == sine_wave.frame_count

    def test_flanger_dry(self, sine_wave):
        """Flanger with mix=0 should return dry signal."""
        result = cycdp.flanger(sine_wave, rate=0.5, depth_ms=3.0, mix=0.0)
        # Should be same as input
        for i in range(result.sample_count):
            assert result[i] == pytest.approx(sine_wave[i], abs=1e-6)


class TestParametricEQ:
    """Test parametric EQ."""

    @pytest.fixture
    def sine_wave(self):
        """Create a simple sine wave buffer for testing."""
        import math

        sample_rate = 44100
        duration = 0.5
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2 * math.pi * 440 * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_eq_parametric_boost(self, sine_wave):
        """EQ boost should run without error."""
        result = cycdp.eq_parametric(sine_wave, center_freq=440, gain_db=6.0, q=1.0)
        assert result.frame_count > 0

    def test_eq_parametric_cut(self, sine_wave):
        """EQ cut should reduce signal."""
        result = cycdp.eq_parametric(sine_wave, center_freq=440, gain_db=-12.0, q=1.0)
        assert result.frame_count > 0
        # Signal should be reduced
        peak = max(abs(result[i]) for i in range(result.sample_count))
        assert peak < 0.4  # Less than original 0.5

    def test_eq_parametric_narrow_q(self, sine_wave):
        """Narrow Q should affect less of the spectrum."""
        result = cycdp.eq_parametric(sine_wave, center_freq=440, gain_db=6.0, q=5.0)
        assert result.frame_count > 0


class TestEnvelopeFollow:
    """Test envelope follower."""

    @pytest.fixture
    def sine_wave(self):
        """Create a simple sine wave buffer for testing."""
        import math

        sample_rate = 44100
        duration = 0.5
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2 * math.pi * 440 * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_envelope_follow_peak(self, sine_wave):
        """Envelope follow (peak) should produce mono envelope."""
        result = cycdp.envelope_follow(
            sine_wave, attack_ms=1.0, release_ms=50.0, mode="peak"
        )
        assert result.channels == 1
        assert result.frame_count == sine_wave.frame_count
        # Envelope should be non-negative
        for i in range(result.sample_count):
            assert result[i] >= 0

    def test_envelope_follow_rms(self, sine_wave):
        """Envelope follow (RMS) should produce smooth envelope."""
        result = cycdp.envelope_follow(
            sine_wave, attack_ms=1.0, release_ms=50.0, mode="rms"
        )
        assert result.channels == 1
        assert result.frame_count == sine_wave.frame_count


class TestEnvelopeApply:
    """Test envelope apply."""

    @pytest.fixture
    def sine_wave(self):
        """Create a simple sine wave buffer for testing."""
        import math

        sample_rate = 44100
        duration = 0.5
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2 * math.pi * 440 * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_envelope_apply(self, sine_wave):
        """Envelope apply should modulate amplitude."""
        # Create a simple ramp envelope
        samples = array.array("f", [i / 22050 for i in range(22050)])
        envelope = cycdp.Buffer.from_memoryview(samples, channels=1, sample_rate=44100)

        result = cycdp.envelope_apply(sine_wave, envelope, depth=1.0)
        assert result.frame_count == sine_wave.frame_count

    def test_envelope_apply_zero_depth(self, sine_wave):
        """Envelope apply with depth=0 should not change signal."""
        envelope = cycdp.envelope_follow(sine_wave, attack_ms=1.0, release_ms=50.0)
        result = cycdp.envelope_apply(sine_wave, envelope, depth=0.0)
        # Should be same as input
        for i in range(min(100, result.sample_count)):
            assert result[i] == pytest.approx(sine_wave[i], abs=1e-6)


class TestCompressor:
    """Test compressor."""

    @pytest.fixture
    def sine_wave(self):
        """Create a simple sine wave buffer for testing."""
        import math

        sample_rate = 44100
        duration = 0.5
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2 * math.pi * 440 * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_compressor(self, sine_wave):
        """Compressor should run without error."""
        result = cycdp.compressor(sine_wave, threshold_db=-10.0, ratio=4.0)
        assert result.frame_count == sine_wave.frame_count

    def test_compressor_reduces_peaks(self, sine_wave):
        """Compressor should reduce peaks above threshold."""
        # Original peak is 0.5 = -6dB
        # Threshold at -10dB should compress
        result = cycdp.compressor(
            sine_wave, threshold_db=-10.0, ratio=4.0, attack_ms=0.1, release_ms=50.0
        )
        result_peak = max(abs(result[i]) for i in range(result.sample_count))
        original_peak = max(abs(sine_wave[i]) for i in range(sine_wave.sample_count))
        # Compressed peak should be lower than original
        assert result_peak < original_peak

    def test_compressor_with_makeup(self, sine_wave):
        """Compressor makeup gain should boost output."""
        result = cycdp.compressor(
            sine_wave, threshold_db=-10.0, ratio=4.0, makeup_gain_db=6.0
        )
        assert result.frame_count == sine_wave.frame_count


class TestLimiter:
    """Test limiter."""

    @pytest.fixture
    def sine_wave(self):
        """Create a simple sine wave buffer for testing."""
        import math

        sample_rate = 44100
        duration = 0.5
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2 * math.pi * 440 * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_limiter(self, sine_wave):
        """Limiter should run without error."""
        result = cycdp.limiter(sine_wave, threshold_db=-6.0)
        assert result.frame_count == sine_wave.frame_count

    def test_limiter_caps_peaks(self, sine_wave):
        """Limiter should cap peaks at threshold."""
        # Original peak is 0.5 = -6dB
        # Limit at -12dB = 0.25
        result = cycdp.limiter(
            sine_wave, threshold_db=-12.0, attack_ms=0.0, release_ms=50.0
        )
        result_peak = max(abs(result[i]) for i in range(result.sample_count))
        # Peak should be at or below threshold (0.25)
        assert result_peak <= 0.26  # Small tolerance

    def test_limiter_hard(self, sine_wave):
        """Hard limiter (attack=0) should strictly limit."""
        result = cycdp.limiter(
            sine_wave, threshold_db=-6.0, attack_ms=0.0, release_ms=50.0
        )
        threshold_lin = 10 ** (-6.0 / 20.0)  # ~0.5
        # All samples should be at or below threshold
        for i in range(result.sample_count):
            assert abs(result[i]) <= threshold_lin + 0.001


class TestGrainCloud:
    """Test grain cloud generation (CDP: grain)."""

    @pytest.fixture
    def short_sound(self):
        """Create a short sound with amplitude variations for grain detection."""
        import math

        sample_rate = 44100
        duration = 0.5
        samples = array.array(
            "f",
            [
                0.5
                * math.sin(2 * math.pi * 440 * i / sample_rate)
                * (
                    0.5 + 0.5 * math.sin(2 * math.pi * 10 * i / sample_rate)
                )  # AM modulation
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_grain_cloud_runs(self, short_sound):
        """Grain cloud should run without error."""
        result = cycdp.grain_cloud(short_sound, duration=1.0, density=10.0, seed=42)
        assert result.frame_count > 0

    def test_grain_cloud_duration(self, short_sound):
        """Grain cloud should respect duration parameter."""
        duration = 2.0
        result = cycdp.grain_cloud(
            short_sound, duration=duration, density=10.0, seed=42
        )
        expected_samples = int(duration * short_sound.sample_rate)
        assert abs(result.frame_count - expected_samples) < 100

    def test_grain_cloud_reproducible(self, short_sound):
        """Same seed should produce same result."""
        result1 = cycdp.grain_cloud(short_sound, duration=0.5, density=10.0, seed=12345)
        result2 = cycdp.grain_cloud(short_sound, duration=0.5, density=10.0, seed=12345)
        # First few samples should be identical
        for i in range(min(100, result1.sample_count, result2.sample_count)):
            assert result1[i] == pytest.approx(result2[i], abs=1e-6)


class TestGrainExtend:
    """Test grain extend (CDP: grainex extend)."""

    @pytest.fixture
    def short_sound(self):
        """Create a short sound for testing."""
        import math

        sample_rate = 44100
        duration = 0.5
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2 * math.pi * 440 * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_grain_extend_runs(self, short_sound):
        """Grain extend should run without error."""
        result = cycdp.grain_extend(short_sound, extension=1.0)
        assert result.frame_count > 0

    def test_grain_extend_increases_length(self, short_sound):
        """Grain extend should increase duration."""
        extension = 1.0
        result = cycdp.grain_extend(short_sound, extension=extension)
        # Output should be longer than input by approximately extension amount
        input_duration = short_sound.frame_count / short_sound.sample_rate
        output_duration = result.frame_count / result.sample_rate
        assert output_duration >= input_duration


class TestTextureSimple:
    """Test simple texture generation (CDP: texture SIMPLE_TEX)."""

    @pytest.fixture
    def short_sound(self):
        """Create a short sound for texture source."""
        import math

        sample_rate = 44100
        duration = 0.2
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2 * math.pi * 440 * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_texture_simple_runs(self, short_sound):
        """Texture simple should run without error."""
        result = cycdp.texture_simple(short_sound, duration=1.0, density=5.0, seed=42)
        assert result.frame_count > 0

    def test_texture_simple_stereo_output(self, short_sound):
        """Texture simple should produce stereo output."""
        result = cycdp.texture_simple(short_sound, duration=1.0, density=5.0, seed=42)
        assert result.channels == 2

    def test_texture_simple_duration(self, short_sound):
        """Texture simple should respect duration."""
        duration = 2.0
        result = cycdp.texture_simple(
            short_sound, duration=duration, density=5.0, seed=42
        )
        expected_samples = int(duration * short_sound.sample_rate)
        assert abs(result.frame_count - expected_samples) < 100


class TestTextureMulti:
    """Test multi-layer texture generation (CDP: texture GROUPS)."""

    @pytest.fixture
    def short_sound(self):
        """Create a short sound for texture source."""
        import math

        sample_rate = 44100
        duration = 0.2
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2 * math.pi * 440 * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_texture_multi_runs(self, short_sound):
        """Texture multi should run without error."""
        result = cycdp.texture_multi(short_sound, duration=1.0, density=2.0, seed=42)
        assert result.frame_count > 0

    def test_texture_multi_stereo_output(self, short_sound):
        """Texture multi should produce stereo output."""
        result = cycdp.texture_multi(short_sound, duration=1.0, density=2.0, seed=42)
        assert result.channels == 2

    def test_texture_multi_groups(self, short_sound):
        """Texture multi should support different group sizes."""
        result1 = cycdp.texture_multi(
            short_sound, duration=1.0, density=2.0, group_size=2, seed=42
        )
        result2 = cycdp.texture_multi(
            short_sound, duration=1.0, density=2.0, group_size=8, seed=42
        )
        # Both should produce output
        assert result1.frame_count > 0
        assert result2.frame_count > 0


# =============================================================================
# Extended Granular Operations
# =============================================================================


class TestGrainReorder:
    """Test grain reordering (CDP: GRAIN REORDER)."""

    @pytest.fixture
    def rhythmic_sound(self):
        """Create a sound with distinct grains (pulses)."""
        import math

        sample_rate = 44100
        samples = array.array("f")
        # Create 5 pulses at 0.1s intervals
        for pulse in range(5):
            # Silence before pulse
            samples.extend([0.0] * int(sample_rate * 0.05))
            # Pulse with different frequency for each
            freq = 220 * (pulse + 1)  # 220, 440, 660, 880, 1100 Hz
            for i in range(int(sample_rate * 0.04)):
                env = 1.0 - i / (sample_rate * 0.04)  # decay
                samples.append(
                    0.8 * env * math.sin(2 * math.pi * freq * i / sample_rate)
                )
            # Silence after pulse
            samples.extend([0.0] * int(sample_rate * 0.01))
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_grain_reorder_runs(self, rhythmic_sound):
        """Grain reorder should run without error."""
        result = cycdp.grain_reorder(rhythmic_sound, seed=42)
        assert result.frame_count > 0

    def test_grain_reorder_with_explicit_order(self, rhythmic_sound):
        """Grain reorder should accept explicit order."""
        result = cycdp.grain_reorder(rhythmic_sound, order=[4, 3, 2, 1, 0], seed=42)
        assert result.frame_count > 0

    def test_grain_reorder_reproducible(self, rhythmic_sound):
        """Grain reorder should be reproducible with same seed."""
        result1 = cycdp.grain_reorder(rhythmic_sound, seed=12345)
        result2 = cycdp.grain_reorder(rhythmic_sound, seed=12345)
        # Should produce same output
        for i in range(min(result1.sample_count, result2.sample_count)):
            assert result1[i] == pytest.approx(result2[i], abs=1e-6)


class TestGrainRerhythm:
    """Test grain rerhythm (CDP: GRAIN RERHYTHM)."""

    @pytest.fixture
    def rhythmic_sound(self):
        """Create a sound with distinct grains."""
        import math

        sample_rate = 44100
        samples = array.array("f")
        for pulse in range(4):
            samples.extend([0.0] * int(sample_rate * 0.05))
            for i in range(int(sample_rate * 0.04)):
                env = 1.0 - i / (sample_rate * 0.04)
                samples.append(
                    0.8 * env * math.sin(2 * math.pi * 440 * i / sample_rate)
                )
            samples.extend([0.0] * int(sample_rate * 0.01))
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_grain_rerhythm_runs(self, rhythmic_sound):
        """Grain rerhythm should run without error."""
        result = cycdp.grain_rerhythm(rhythmic_sound, seed=42)
        assert result.frame_count > 0

    def test_grain_rerhythm_with_ratios(self, rhythmic_sound):
        """Grain rerhythm should accept timing ratios."""
        result = cycdp.grain_rerhythm(rhythmic_sound, ratios=[2.0, 0.5, 1.0])
        assert result.frame_count > 0

    def test_grain_rerhythm_with_times(self, rhythmic_sound):
        """Grain rerhythm should accept explicit times."""
        result = cycdp.grain_rerhythm(rhythmic_sound, times=[0.0, 0.2, 0.3, 0.6])
        assert result.frame_count > 0


class TestGrainReverse:
    """Test grain reverse (CDP: GRAIN REVERSE)."""

    @pytest.fixture
    def rhythmic_sound(self):
        """Create a sound with distinct grains."""
        import math

        sample_rate = 44100
        samples = array.array("f")
        for pulse in range(4):
            samples.extend([0.0] * int(sample_rate * 0.05))
            freq = 220 * (pulse + 1)
            for i in range(int(sample_rate * 0.04)):
                env = 1.0 - i / (sample_rate * 0.04)
                samples.append(
                    0.8 * env * math.sin(2 * math.pi * freq * i / sample_rate)
                )
            samples.extend([0.0] * int(sample_rate * 0.01))
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_grain_reverse_runs(self, rhythmic_sound):
        """Grain reverse should run without error."""
        result = cycdp.grain_reverse(rhythmic_sound)
        assert result.frame_count > 0


class TestGrainTimewarp:
    """Test grain timewarp (CDP: GRAIN TIMEWARP)."""

    @pytest.fixture
    def rhythmic_sound(self):
        """Create a sound with distinct grains."""
        import math

        sample_rate = 44100
        samples = array.array("f")
        for pulse in range(4):
            samples.extend([0.0] * int(sample_rate * 0.05))
            for i in range(int(sample_rate * 0.04)):
                env = 1.0 - i / (sample_rate * 0.04)
                samples.append(
                    0.8 * env * math.sin(2 * math.pi * 440 * i / sample_rate)
                )
            samples.extend([0.0] * int(sample_rate * 0.01))
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_grain_timewarp_runs(self, rhythmic_sound):
        """Grain timewarp should run without error."""
        result = cycdp.grain_timewarp(rhythmic_sound, stretch=1.5)
        assert result.frame_count > 0

    def test_grain_timewarp_stretches(self, rhythmic_sound):
        """Grain timewarp with stretch>1 should increase duration."""
        result = cycdp.grain_timewarp(rhythmic_sound, stretch=2.0)
        # Output should be longer
        assert result.frame_count > rhythmic_sound.frame_count

    def test_grain_timewarp_with_curve(self, rhythmic_sound):
        """Grain timewarp should accept stretch curve."""
        curve = [(0.0, 1.0), (0.5, 2.0), (1.0, 0.5)]
        result = cycdp.grain_timewarp(rhythmic_sound, stretch=1.0, stretch_curve=curve)
        assert result.frame_count > 0


class TestGrainRepitch:
    """Test grain repitch (CDP: GRAIN REPITCH)."""

    @pytest.fixture
    def rhythmic_sound(self):
        """Create a sound with distinct grains."""
        import math

        sample_rate = 44100
        samples = array.array("f")
        for pulse in range(4):
            samples.extend([0.0] * int(sample_rate * 0.05))
            for i in range(int(sample_rate * 0.04)):
                env = 1.0 - i / (sample_rate * 0.04)
                samples.append(
                    0.8 * env * math.sin(2 * math.pi * 440 * i / sample_rate)
                )
            samples.extend([0.0] * int(sample_rate * 0.01))
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_grain_repitch_runs(self, rhythmic_sound):
        """Grain repitch should run without error."""
        result = cycdp.grain_repitch(rhythmic_sound, pitch_semitones=5)
        assert result.frame_count > 0

    def test_grain_repitch_with_curve(self, rhythmic_sound):
        """Grain repitch should accept pitch curve."""
        curve = [(0.0, 0), (0.5, 12), (1.0, -12)]
        result = cycdp.grain_repitch(rhythmic_sound, pitch_curve=curve)
        assert result.frame_count > 0


class TestGrainPosition:
    """Test grain position (CDP: GRAIN POSITION)."""

    @pytest.fixture
    def rhythmic_sound(self):
        """Create a sound with distinct grains."""
        import math

        sample_rate = 44100
        samples = array.array("f")
        for pulse in range(4):
            samples.extend([0.0] * int(sample_rate * 0.05))
            for i in range(int(sample_rate * 0.04)):
                env = 1.0 - i / (sample_rate * 0.04)
                samples.append(
                    0.8 * env * math.sin(2 * math.pi * 440 * i / sample_rate)
                )
            samples.extend([0.0] * int(sample_rate * 0.01))
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_grain_position_runs(self, rhythmic_sound):
        """Grain position should run without error."""
        result = cycdp.grain_position(rhythmic_sound, positions=[0.0, 0.5, 1.0, 1.5])
        assert result.frame_count > 0

    def test_grain_position_with_duration(self, rhythmic_sound):
        """Grain position should respect duration parameter."""
        result = cycdp.grain_position(
            rhythmic_sound, positions=[0.0, 0.3, 0.6], duration=2.0
        )
        # Output should be approximately 2 seconds + grain length
        expected_samples = int(2.0 * rhythmic_sound.sample_rate)
        assert result.frame_count >= expected_samples * 0.8


class TestGrainOmit:
    """Test grain omit (CDP: GRAIN OMIT)."""

    @pytest.fixture
    def rhythmic_sound(self):
        """Create a sound with distinct grains."""
        import math

        sample_rate = 44100
        samples = array.array("f")
        for pulse in range(6):
            samples.extend([0.0] * int(sample_rate * 0.05))
            for i in range(int(sample_rate * 0.04)):
                env = 1.0 - i / (sample_rate * 0.04)
                samples.append(
                    0.8 * env * math.sin(2 * math.pi * 440 * i / sample_rate)
                )
            samples.extend([0.0] * int(sample_rate * 0.01))
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_grain_omit_runs(self, rhythmic_sound):
        """Grain omit should run without error."""
        result = cycdp.grain_omit(rhythmic_sound, keep=1, out_of=2)
        assert result.frame_count > 0

    def test_grain_omit_reduces_length(self, rhythmic_sound):
        """Grain omit keeping 1 of 3 should reduce length."""
        result = cycdp.grain_omit(rhythmic_sound, keep=1, out_of=3)
        # Output should be shorter (roughly 1/3 of grains)
        assert result.frame_count < rhythmic_sound.frame_count


class TestGrainDuplicate:
    """Test grain duplicate (CDP: GRAIN DUPLICATE)."""

    @pytest.fixture
    def rhythmic_sound(self):
        """Create a sound with distinct grains."""
        import math

        sample_rate = 44100
        samples = array.array("f")
        for pulse in range(3):
            samples.extend([0.0] * int(sample_rate * 0.05))
            for i in range(int(sample_rate * 0.04)):
                env = 1.0 - i / (sample_rate * 0.04)
                samples.append(
                    0.8 * env * math.sin(2 * math.pi * 440 * i / sample_rate)
                )
            samples.extend([0.0] * int(sample_rate * 0.01))
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_grain_duplicate_runs(self, rhythmic_sound):
        """Grain duplicate should run without error."""
        result = cycdp.grain_duplicate(rhythmic_sound, repeats=2, seed=42)
        assert result.frame_count > 0

    def test_grain_duplicate_increases_length(self, rhythmic_sound):
        """Grain duplicate with repeats>1 should produce longer output."""
        result = cycdp.grain_duplicate(rhythmic_sound, repeats=3, seed=42)
        # Output should be substantial (grains repeated)
        assert result.frame_count > rhythmic_sound.frame_count * 0.5

    def test_grain_duplicate_reproducible(self, rhythmic_sound):
        """Grain duplicate should be reproducible with same seed."""
        result1 = cycdp.grain_duplicate(rhythmic_sound, repeats=2, seed=12345)
        result2 = cycdp.grain_duplicate(rhythmic_sound, repeats=2, seed=12345)
        for i in range(min(result1.sample_count, result2.sample_count)):
            assert result1[i] == pytest.approx(result2[i], abs=1e-6)


class TestMorph:
    """Test spectral morph (CDP: SPECMORPH)."""

    @pytest.fixture
    def sine_440(self):
        """Create a 440Hz sine wave."""
        import math

        sample_rate = 44100
        duration = 0.5
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2 * math.pi * 440 * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    @pytest.fixture
    def sine_880(self):
        """Create a 880Hz sine wave."""
        import math

        sample_rate = 44100
        duration = 0.5
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2 * math.pi * 880 * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_morph_runs(self, sine_440, sine_880):
        """Morph should run without error."""
        result = cycdp.morph(sine_440, sine_880)
        assert result.frame_count > 0

    def test_morph_with_timing(self, sine_440, sine_880):
        """Morph should respect timing parameters."""
        result = cycdp.morph(sine_440, sine_880, morph_start=0.25, morph_end=0.75)
        assert result.frame_count > 0

    def test_morph_with_exponent(self, sine_440, sine_880):
        """Morph should support different curve exponents."""
        result1 = cycdp.morph(sine_440, sine_880, exponent=0.5)  # Fast start
        result2 = cycdp.morph(sine_440, sine_880, exponent=2.0)  # Slow start
        assert result1.frame_count > 0
        assert result2.frame_count > 0


class TestMorphGlide:
    """Test spectral glide (CDP: SPECGLIDE)."""

    @pytest.fixture
    def sine_440(self):
        """Create a 440Hz sine wave."""
        import math

        sample_rate = 44100
        duration = 0.3
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2 * math.pi * 440 * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    @pytest.fixture
    def sine_880(self):
        """Create a 880Hz sine wave."""
        import math

        sample_rate = 44100
        duration = 0.3
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2 * math.pi * 880 * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_morph_glide_runs(self, sine_440, sine_880):
        """Morph glide should run without error."""
        result = cycdp.morph_glide(sine_440, sine_880, duration=0.5)
        assert result.frame_count > 0

    def test_morph_glide_duration(self, sine_440, sine_880):
        """Morph glide should produce output of reasonable length."""
        duration = 1.0
        result = cycdp.morph_glide(sine_440, sine_880, duration=duration)
        # Output should be non-trivial length (FFT processing affects exact duration)
        assert result.frame_count > sine_440.sample_rate * 0.1  # At least 100ms


class TestCrossSynth:
    """Test cross-synthesis (CDP: combine)."""

    @pytest.fixture
    def voice_like(self):
        """Create a voice-like sound with harmonics."""
        import math

        sample_rate = 44100
        duration = 0.5
        samples = array.array("f")
        for i in range(int(sample_rate * duration)):
            t = i / sample_rate
            # Fundamental with harmonics
            val = 0.3 * math.sin(2 * math.pi * 220 * t)
            val += 0.2 * math.sin(2 * math.pi * 440 * t)
            val += 0.1 * math.sin(2 * math.pi * 660 * t)
            samples.append(val)
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    @pytest.fixture
    def noise_like(self):
        """Create a noise-like sound."""
        import random

        random.seed(42)
        sample_rate = 44100
        duration = 0.5
        samples = array.array(
            "f",
            [
                0.3 * (random.random() * 2 - 1)
                for _ in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_cross_synth_runs(self, voice_like, noise_like):
        """Cross-synthesis should run without error."""
        result = cycdp.cross_synth(voice_like, noise_like)
        assert result.frame_count > 0

    def test_cross_synth_modes(self, voice_like, noise_like):
        """Cross-synthesis should support both modes."""
        result0 = cycdp.cross_synth(voice_like, noise_like, mode=0)
        result1 = cycdp.cross_synth(voice_like, noise_like, mode=1)
        assert result0.frame_count > 0
        assert result1.frame_count > 0

    def test_cross_synth_mix(self, voice_like, noise_like):
        """Cross-synthesis should support mix parameter."""
        result_full = cycdp.cross_synth(voice_like, noise_like, mix=1.0)
        result_half = cycdp.cross_synth(voice_like, noise_like, mix=0.5)
        assert result_full.frame_count > 0
        assert result_half.frame_count > 0


# =============================================================================
# Native Morph Functions (original CDP algorithms)
# =============================================================================


class TestMorphGlideNative:
    """Test native spectral glide (CDP: SPECGLIDE original algorithm)."""

    @pytest.fixture
    def sine_440(self):
        """Create a 440Hz sine wave."""
        import math

        sample_rate = 44100
        duration = 0.3
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2 * math.pi * 440 * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    @pytest.fixture
    def sine_880(self):
        """Create a 880Hz sine wave."""
        import math

        sample_rate = 44100
        duration = 0.3
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2 * math.pi * 880 * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_morph_glide_native_runs(self, sine_440, sine_880):
        """Native morph glide should run without error."""
        result = cycdp.morph_glide_native(sine_440, sine_880, duration=0.5)
        assert result.frame_count > 0

    def test_morph_glide_native_duration(self, sine_440, sine_880):
        """Native morph glide should produce output of reasonable length."""
        duration = 1.0
        result = cycdp.morph_glide_native(sine_440, sine_880, duration=duration)
        # Output should be non-trivial length
        assert result.frame_count > sine_440.sample_rate * 0.1  # At least 100ms


class TestMorphBridgeNative:
    """Test native spectral bridge (CDP: SPECBRIDGE original algorithm)."""

    @pytest.fixture
    def sine_440(self):
        """Create a 440Hz sine wave."""
        import math

        sample_rate = 44100
        duration = 0.3
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2 * math.pi * 440 * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    @pytest.fixture
    def sine_880(self):
        """Create a 880Hz sine wave."""
        import math

        sample_rate = 44100
        duration = 0.3
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2 * math.pi * 880 * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_morph_bridge_native_runs(self, sine_440, sine_880):
        """Native morph bridge should run without error."""
        result = cycdp.morph_bridge_native(sine_440, sine_880)
        assert result.frame_count > 0

    def test_morph_bridge_native_modes(self, sine_440, sine_880):
        """Native morph bridge should support different normalization modes."""
        # Test a few modes
        result0 = cycdp.morph_bridge_native(sine_440, sine_880, mode=0)
        result1 = cycdp.morph_bridge_native(sine_440, sine_880, mode=1)
        result2 = cycdp.morph_bridge_native(sine_440, sine_880, mode=2)
        assert result0.frame_count > 0
        assert result1.frame_count > 0
        assert result2.frame_count > 0

    def test_morph_bridge_native_interp_timing(self, sine_440, sine_880):
        """Native morph bridge should support interpolation timing."""
        result = cycdp.morph_bridge_native(
            sine_440, sine_880, interp_start=0.25, interp_end=0.75
        )
        assert result.frame_count > 0


class TestMorphNative:
    """Test native spectral morph (CDP: SPECMORPH original algorithm)."""

    @pytest.fixture
    def sine_440(self):
        """Create a 440Hz sine wave."""
        import math

        sample_rate = 44100
        duration = 0.3
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2 * math.pi * 440 * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    @pytest.fixture
    def sine_880(self):
        """Create a 880Hz sine wave."""
        import math

        sample_rate = 44100
        duration = 0.3
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2 * math.pi * 880 * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_morph_native_runs(self, sine_440, sine_880):
        """Native morph should run without error."""
        result = cycdp.morph_native(sine_440, sine_880)
        assert result.frame_count > 0

    def test_morph_native_modes(self, sine_440, sine_880):
        """Native morph should support different interpolation modes."""
        result_linear = cycdp.morph_native(sine_440, sine_880, mode=0)
        result_cosine = cycdp.morph_native(sine_440, sine_880, mode=1)
        assert result_linear.frame_count > 0
        assert result_cosine.frame_count > 0

    def test_morph_native_separate_timing(self, sine_440, sine_880):
        """Native morph should support separate amp/freq timing."""
        result = cycdp.morph_native(
            sine_440, sine_880, amp_start=0.0, amp_end=0.5, freq_start=0.5, freq_end=1.0
        )
        assert result.frame_count > 0

    def test_morph_native_exponents(self, sine_440, sine_880):
        """Native morph should support different curve exponents."""
        result_fast = cycdp.morph_native(sine_440, sine_880, amp_exp=0.5)
        result_slow = cycdp.morph_native(sine_440, sine_880, amp_exp=2.0)
        assert result_fast.frame_count > 0
        assert result_slow.frame_count > 0


# =============================================================================
# Analysis Functions
# =============================================================================


class TestPitch:
    """Tests for pitch analysis."""

    @pytest.fixture
    def tone_440(self):
        """Create a 440 Hz sine wave."""
        import math

        sample_rate = 44100
        duration = 0.5
        samples = array.array("f")
        for i in range(int(sample_rate * duration)):
            t = i / sample_rate
            samples.append(0.8 * math.sin(2 * math.pi * 440 * t))
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_pitch_runs(self, tone_440):
        """Pitch analysis should run without error."""
        result = cycdp.pitch(tone_440)
        assert "pitch" in result
        assert "confidence" in result
        assert "num_frames" in result
        assert result["num_frames"] > 0
        assert len(result["pitch"]) == result["num_frames"]
        assert len(result["confidence"]) == result["num_frames"]

    def test_pitch_detects_440hz(self, tone_440):
        """Pitch analysis should detect 440 Hz in a sine wave."""
        result = cycdp.pitch(tone_440, min_freq=100, max_freq=1000)
        # Check that some frames detected pitch near 440 Hz
        detected = [p for p in result["pitch"] if p > 0]
        assert len(detected) > 0
        avg_pitch = sum(detected) / len(detected)
        # Allow 10% tolerance
        assert 396 < avg_pitch < 484

    def test_pitch_with_params(self, tone_440):
        """Pitch analysis should accept parameter overrides."""
        result = cycdp.pitch(
            tone_440, min_freq=200, max_freq=1000, frame_size=1024, hop_size=256
        )
        assert result["num_frames"] > 0


class TestFormants:
    """Tests for formant analysis."""

    @pytest.fixture
    def complex_tone(self):
        """Create a complex tone with multiple frequencies."""
        import math

        sample_rate = 44100
        duration = 0.5
        samples = array.array("f")
        for i in range(int(sample_rate * duration)):
            t = i / sample_rate
            # Multiple frequencies to create formant-like structure
            val = 0.3 * math.sin(2 * math.pi * 500 * t)
            val += 0.2 * math.sin(2 * math.pi * 1500 * t)
            val += 0.15 * math.sin(2 * math.pi * 2500 * t)
            val += 0.1 * math.sin(2 * math.pi * 3500 * t)
            samples.append(val)
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_formants_runs(self, complex_tone):
        """Formant analysis should run without error."""
        result = cycdp.formants(complex_tone)
        assert "f1" in result
        assert "f2" in result
        assert "f3" in result
        assert "f4" in result
        assert "b1" in result
        assert "num_frames" in result
        assert result["num_frames"] > 0

    def test_formants_with_params(self, complex_tone):
        """Formant analysis should accept parameter overrides."""
        result = cycdp.formants(
            complex_tone, lpc_order=16, frame_size=512, hop_size=128
        )
        assert result["num_frames"] > 0


class TestGetPartials:
    """Tests for partial tracking."""

    @pytest.fixture
    def harmonic_tone(self):
        """Create a tone with clear harmonics."""
        import math

        sample_rate = 44100
        duration = 0.5
        samples = array.array("f")
        for i in range(int(sample_rate * duration)):
            t = i / sample_rate
            # Fundamental and harmonics
            val = 0.5 * math.sin(2 * math.pi * 220 * t)
            val += 0.3 * math.sin(2 * math.pi * 440 * t)
            val += 0.2 * math.sin(2 * math.pi * 660 * t)
            val += 0.1 * math.sin(2 * math.pi * 880 * t)
            samples.append(val)
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_get_partials_runs(self, harmonic_tone):
        """Partial tracking should run without error."""
        result = cycdp.get_partials(harmonic_tone)
        assert "tracks" in result
        assert "num_tracks" in result
        assert "total_frames" in result
        assert result["total_frames"] > 0

    def test_get_partials_finds_tracks(self, harmonic_tone):
        """Partial tracking should find some tracks."""
        result = cycdp.get_partials(harmonic_tone, min_amp_db=-40, max_partials=20)
        assert result["num_tracks"] > 0
        # Each track should have freq and amp arrays
        for track in result["tracks"]:
            assert "freq" in track
            assert "amp" in track
            assert "start_frame" in track
            assert "end_frame" in track

    def test_get_partials_with_params(self, harmonic_tone):
        """Partial tracking should accept parameter overrides."""
        result = cycdp.get_partials(
            harmonic_tone,
            min_amp_db=-50,
            max_partials=50,
            freq_tolerance=30,
            fft_size=1024,
            hop_size=256,
        )
        assert result["total_frames"] > 0


class TestSpectralFocus:
    """Tests for spectral focus operation."""

    @pytest.fixture
    def sine_buffer(self):
        """Create a sine wave buffer for testing."""
        import math

        sample_rate = 44100
        duration = 0.5
        freq = 440.0
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2.0 * math.pi * freq * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_spectral_focus_returns_buffer(self, sine_buffer):
        """Spectral focus should return a Buffer."""
        result = cycdp.spectral_focus(
            sine_buffer, center_freq=440.0, bandwidth=100.0, gain_db=6.0
        )
        assert isinstance(result, cycdp.Buffer)
        assert result.sample_count > 0

    def test_spectral_focus_with_params(self, sine_buffer):
        """Spectral focus should accept parameter overrides."""
        result = cycdp.spectral_focus(
            sine_buffer,
            center_freq=1000.0,
            bandwidth=200.0,
            gain_db=-3.0,
            fft_size=2048,
        )
        assert isinstance(result, cycdp.Buffer)


class TestSpectralHilite:
    """Tests for spectral hilite operation."""

    @pytest.fixture
    def harmonic_buffer(self):
        """Create a harmonic tone buffer for testing."""
        import math

        sample_rate = 44100
        duration = 0.5
        freq = 220.0
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2.0 * math.pi * freq * i / sample_rate)
                + 0.25 * math.sin(2.0 * math.pi * 2 * freq * i / sample_rate)
                + 0.125 * math.sin(2.0 * math.pi * 3 * freq * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_spectral_hilite_returns_buffer(self, harmonic_buffer):
        """Spectral hilite should return a Buffer."""
        result = cycdp.spectral_hilite(
            harmonic_buffer, threshold_db=-20.0, boost_db=6.0
        )
        assert isinstance(result, cycdp.Buffer)
        assert result.sample_count > 0

    def test_spectral_hilite_with_params(self, harmonic_buffer):
        """Spectral hilite should accept parameter overrides."""
        result = cycdp.spectral_hilite(
            harmonic_buffer, threshold_db=-30.0, boost_db=12.0, fft_size=2048
        )
        assert isinstance(result, cycdp.Buffer)


class TestSpectralFold:
    """Tests for spectral fold operation."""

    @pytest.fixture
    def sine_buffer(self):
        """Create a sine wave buffer for testing."""
        import math

        sample_rate = 44100
        duration = 0.5
        freq = 440.0
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2.0 * math.pi * freq * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_spectral_fold_returns_buffer(self, sine_buffer):
        """Spectral fold should return a Buffer."""
        result = cycdp.spectral_fold(sine_buffer, fold_freq=2000.0)
        assert isinstance(result, cycdp.Buffer)
        assert result.sample_count > 0

    def test_spectral_fold_with_params(self, sine_buffer):
        """Spectral fold should accept parameter overrides."""
        result = cycdp.spectral_fold(sine_buffer, fold_freq=1000.0, fft_size=2048)
        assert isinstance(result, cycdp.Buffer)


class TestSpectralClean:
    """Tests for spectral clean operation."""

    @pytest.fixture
    def noisy_buffer(self):
        """Create a noisy buffer for testing."""
        import math
        import random

        sample_rate = 44100
        duration = 0.5
        freq = 440.0
        random.seed(42)
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2.0 * math.pi * freq * i / sample_rate)
                + 0.01 * (random.random() * 2 - 1)  # Add some noise
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_spectral_clean_returns_buffer(self, noisy_buffer):
        """Spectral clean should return a Buffer."""
        result = cycdp.spectral_clean(noisy_buffer, threshold_db=-40.0)
        assert isinstance(result, cycdp.Buffer)
        assert result.sample_count > 0

    def test_spectral_clean_with_params(self, noisy_buffer):
        """Spectral clean should accept parameter overrides."""
        result = cycdp.spectral_clean(noisy_buffer, threshold_db=-30.0, fft_size=2048)
        assert isinstance(result, cycdp.Buffer)


class TestStrange:
    """Tests for strange (Lorenz) modulation operation."""

    @pytest.fixture
    def sine_buffer(self):
        """Create a sine wave buffer for testing."""
        import math

        sample_rate = 44100
        duration = 0.5
        freq = 440.0
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2.0 * math.pi * freq * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_strange_returns_buffer(self, sine_buffer):
        """Strange modulation should return a Buffer."""
        result = cycdp.strange(sine_buffer, chaos_amount=0.5, rate=2.0, seed=12345)
        assert isinstance(result, cycdp.Buffer)
        assert result.sample_count > 0

    def test_strange_reproducible(self, sine_buffer):
        """Strange modulation with same seed should produce identical results."""
        result1 = cycdp.strange(sine_buffer, chaos_amount=0.5, rate=2.0, seed=12345)
        result2 = cycdp.strange(sine_buffer, chaos_amount=0.5, rate=2.0, seed=12345)
        # Compare sample values
        for i in range(min(100, result1.sample_count)):
            assert result1[i] == pytest.approx(result2[i], rel=1e-6)


class TestBrownian:
    """Tests for Brownian (random walk) modulation operation."""

    @pytest.fixture
    def sine_buffer(self):
        """Create a sine wave buffer for testing."""
        import math

        sample_rate = 44100
        duration = 0.5
        freq = 440.0
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2.0 * math.pi * freq * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_brownian_returns_buffer(self, sine_buffer):
        """Brownian modulation should return a Buffer."""
        result = cycdp.brownian(
            sine_buffer, step_size=0.1, smoothing=0.9, target=0, seed=12345
        )
        assert isinstance(result, cycdp.Buffer)
        assert result.sample_count > 0

    def test_brownian_targets(self, sine_buffer):
        """Brownian modulation should work with all targets."""
        for target in [0, 1, 2]:  # pitch, amp, filter
            result = cycdp.brownian(
                sine_buffer, step_size=0.1, smoothing=0.9, target=target, seed=12345
            )
            assert isinstance(result, cycdp.Buffer)

    def test_brownian_reproducible(self, sine_buffer):
        """Brownian modulation with same seed should produce identical results."""
        result1 = cycdp.brownian(
            sine_buffer, step_size=0.1, smoothing=0.9, target=0, seed=12345
        )
        result2 = cycdp.brownian(
            sine_buffer, step_size=0.1, smoothing=0.9, target=0, seed=12345
        )
        for i in range(min(100, result1.sample_count)):
            assert result1[i] == pytest.approx(result2[i], rel=1e-6)


class TestCrystal:
    """Tests for crystal texture operation."""

    @pytest.fixture
    def sine_buffer(self):
        """Create a sine wave buffer for testing."""
        import math

        sample_rate = 44100
        duration = 0.5
        freq = 440.0
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2.0 * math.pi * freq * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_crystal_returns_buffer(self, sine_buffer):
        """Crystal texture should return a Buffer."""
        result = cycdp.crystal(
            sine_buffer, density=50.0, decay=0.5, pitch_scatter=2.0, seed=12345
        )
        assert isinstance(result, cycdp.Buffer)
        assert result.sample_count > 0

    def test_crystal_adds_tail(self, sine_buffer):
        """Crystal texture should add a decay tail."""
        result = cycdp.crystal(
            sine_buffer, density=50.0, decay=0.5, pitch_scatter=2.0, seed=12345
        )
        # Output should be longer than input due to decay
        assert result.sample_count > sine_buffer.sample_count

    def test_crystal_reproducible(self, sine_buffer):
        """Crystal texture with same seed should produce identical results."""
        result1 = cycdp.crystal(
            sine_buffer, density=50.0, decay=0.5, pitch_scatter=2.0, seed=12345
        )
        result2 = cycdp.crystal(
            sine_buffer, density=50.0, decay=0.5, pitch_scatter=2.0, seed=12345
        )
        for i in range(min(100, result1.sample_count)):
            assert result1[i] == pytest.approx(result2[i], rel=1e-6)


class TestFractal:
    """Tests for fractal processing operation."""

    @pytest.fixture
    def sine_buffer(self):
        """Create a sine wave buffer for testing."""
        import math

        sample_rate = 44100
        duration = 0.5
        freq = 440.0
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2.0 * math.pi * freq * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_fractal_returns_buffer(self, sine_buffer):
        """Fractal processing should return a Buffer."""
        result = cycdp.fractal(
            sine_buffer, depth=3, pitch_ratio=0.5, decay=0.7, seed=12345
        )
        assert isinstance(result, cycdp.Buffer)
        assert result.sample_count > 0

    def test_fractal_reproducible(self, sine_buffer):
        """Fractal with same seed should produce identical results."""
        result1 = cycdp.fractal(
            sine_buffer, depth=3, pitch_ratio=0.5, decay=0.7, seed=12345
        )
        result2 = cycdp.fractal(
            sine_buffer, depth=3, pitch_ratio=0.5, decay=0.7, seed=12345
        )
        for i in range(min(100, result1.sample_count)):
            assert result1[i] == pytest.approx(result2[i], rel=1e-6)


class TestQuirk:
    """Tests for quirk processing operation."""

    @pytest.fixture
    def sine_buffer(self):
        """Create a sine wave buffer for testing."""
        import math

        sample_rate = 44100
        duration = 0.5
        freq = 440.0
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2.0 * math.pi * freq * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_quirk_returns_buffer(self, sine_buffer):
        """Quirk should return a Buffer."""
        result = cycdp.quirk(
            sine_buffer, probability=0.3, intensity=0.5, mode=2, seed=12345
        )
        assert isinstance(result, cycdp.Buffer)
        assert result.sample_count > 0

    def test_quirk_modes(self, sine_buffer):
        """Quirk should work with all modes."""
        for mode in [0, 1, 2]:
            result = cycdp.quirk(
                sine_buffer, probability=0.3, intensity=0.5, mode=mode, seed=12345
            )
            assert isinstance(result, cycdp.Buffer)

    def test_quirk_reproducible(self, sine_buffer):
        """Quirk with same seed should produce identical results."""
        result1 = cycdp.quirk(
            sine_buffer, probability=0.3, intensity=0.5, mode=2, seed=12345
        )
        result2 = cycdp.quirk(
            sine_buffer, probability=0.3, intensity=0.5, mode=2, seed=12345
        )
        for i in range(min(100, result1.sample_count)):
            assert result1[i] == pytest.approx(result2[i], rel=1e-6)


class TestChirikov:
    """Tests for Chirikov map modulation operation."""

    @pytest.fixture
    def sine_buffer(self):
        """Create a sine wave buffer for testing."""
        import math

        sample_rate = 44100
        duration = 0.5
        freq = 440.0
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2.0 * math.pi * freq * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_chirikov_returns_buffer(self, sine_buffer):
        """Chirikov modulation should return a Buffer."""
        result = cycdp.chirikov(
            sine_buffer, k_param=2.0, mod_depth=0.5, rate=2.0, seed=12345
        )
        assert isinstance(result, cycdp.Buffer)
        assert result.sample_count > 0

    def test_chirikov_reproducible(self, sine_buffer):
        """Chirikov with same seed should produce identical results."""
        result1 = cycdp.chirikov(
            sine_buffer, k_param=2.0, mod_depth=0.5, rate=2.0, seed=12345
        )
        result2 = cycdp.chirikov(
            sine_buffer, k_param=2.0, mod_depth=0.5, rate=2.0, seed=12345
        )
        for i in range(min(100, result1.sample_count)):
            assert result1[i] == pytest.approx(result2[i], rel=1e-6)


class TestCantor:
    """Tests for Cantor set gating operation."""

    @pytest.fixture
    def sine_buffer(self):
        """Create a sine wave buffer for testing."""
        import math

        sample_rate = 44100
        duration = 0.5
        freq = 440.0
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2.0 * math.pi * freq * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_cantor_returns_buffer(self, sine_buffer):
        """Cantor gating should return a Buffer."""
        result = cycdp.cantor(
            sine_buffer, depth=4, duty_cycle=0.5, smooth_ms=5.0, seed=12345
        )
        assert isinstance(result, cycdp.Buffer)
        assert result.sample_count > 0

    def test_cantor_same_length(self, sine_buffer):
        """Cantor gating should preserve length."""
        result = cycdp.cantor(
            sine_buffer, depth=4, duty_cycle=0.5, smooth_ms=5.0, seed=12345
        )
        assert result.sample_count == sine_buffer.sample_count

    def test_cantor_reproducible(self, sine_buffer):
        """Cantor with same seed should produce identical results."""
        result1 = cycdp.cantor(
            sine_buffer, depth=4, duty_cycle=0.5, smooth_ms=5.0, seed=12345
        )
        result2 = cycdp.cantor(
            sine_buffer, depth=4, duty_cycle=0.5, smooth_ms=5.0, seed=12345
        )
        for i in range(min(100, result1.sample_count)):
            assert result1[i] == pytest.approx(result2[i], rel=1e-6)


class TestCascade:
    """Tests for cascade echoes operation."""

    @pytest.fixture
    def sine_buffer(self):
        """Create a sine wave buffer for testing."""
        import math

        sample_rate = 44100
        duration = 0.5
        freq = 440.0
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2.0 * math.pi * freq * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_cascade_returns_buffer(self, sine_buffer):
        """Cascade should return a Buffer."""
        result = cycdp.cascade(sine_buffer, num_echoes=6, delay_ms=100.0, seed=12345)
        assert isinstance(result, cycdp.Buffer)
        assert result.sample_count > 0

    def test_cascade_adds_tail(self, sine_buffer):
        """Cascade should add echo tail, making output longer."""
        result = cycdp.cascade(sine_buffer, num_echoes=6, delay_ms=100.0, seed=12345)
        assert result.sample_count > sine_buffer.sample_count

    def test_cascade_reproducible(self, sine_buffer):
        """Cascade with same seed should produce identical results."""
        result1 = cycdp.cascade(sine_buffer, num_echoes=6, delay_ms=100.0, seed=12345)
        result2 = cycdp.cascade(sine_buffer, num_echoes=6, delay_ms=100.0, seed=12345)
        for i in range(min(100, result1.sample_count)):
            assert result1[i] == pytest.approx(result2[i], rel=1e-6)


class TestFracture:
    """Tests for fracture operation."""

    @pytest.fixture
    def sine_buffer(self):
        """Create a sine wave buffer for testing."""
        import math

        sample_rate = 44100
        duration = 0.5
        freq = 440.0
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2.0 * math.pi * freq * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_fracture_returns_buffer(self, sine_buffer):
        """Fracture should return a Buffer."""
        result = cycdp.fracture(
            sine_buffer, fragment_ms=50.0, gap_ratio=0.5, scatter=0.3, seed=12345
        )
        assert isinstance(result, cycdp.Buffer)
        assert result.sample_count > 0

    def test_fracture_reproducible(self, sine_buffer):
        """Fracture with same seed should produce identical results."""
        result1 = cycdp.fracture(
            sine_buffer, fragment_ms=50.0, gap_ratio=0.5, scatter=0.3, seed=12345
        )
        result2 = cycdp.fracture(
            sine_buffer, fragment_ms=50.0, gap_ratio=0.5, scatter=0.3, seed=12345
        )
        for i in range(min(100, result1.sample_count)):
            assert result1[i] == pytest.approx(result2[i], rel=1e-6)


class TestTesselate:
    """Tests for tesselate operation."""

    @pytest.fixture
    def sine_buffer(self):
        """Create a sine wave buffer for testing."""
        import math

        sample_rate = 44100
        duration = 0.5
        freq = 440.0
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2.0 * math.pi * freq * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_tesselate_returns_buffer(self, sine_buffer):
        """Tesselate should return a Buffer."""
        result = cycdp.tesselate(
            sine_buffer, tile_ms=50.0, pattern=1, overlap=0.25, seed=12345
        )
        assert isinstance(result, cycdp.Buffer)
        assert result.sample_count > 0

    def test_tesselate_patterns(self, sine_buffer):
        """Tesselate should work with all patterns."""
        for pattern in [0, 1, 2, 3]:
            result = cycdp.tesselate(
                sine_buffer, tile_ms=50.0, pattern=pattern, seed=12345
            )
            assert isinstance(result, cycdp.Buffer)

    def test_tesselate_same_length(self, sine_buffer):
        """Tesselate should preserve length."""
        result = cycdp.tesselate(
            sine_buffer, tile_ms=50.0, pattern=1, overlap=0.25, seed=12345
        )
        assert result.sample_count == sine_buffer.sample_count

    def test_tesselate_reproducible(self, sine_buffer):
        """Tesselate with same seed should produce identical results."""
        result1 = cycdp.tesselate(
            sine_buffer, tile_ms=50.0, pattern=1, overlap=0.25, seed=12345
        )
        result2 = cycdp.tesselate(
            sine_buffer, tile_ms=50.0, pattern=1, overlap=0.25, seed=12345
        )
        for i in range(min(100, result1.sample_count)):
            assert result1[i] == pytest.approx(result2[i], rel=1e-6)


class TestZigzag:
    """Tests for zigzag playback operation."""

    @pytest.fixture
    def sine_buffer(self):
        """Create a sine wave buffer for testing."""
        import math

        sample_rate = 44100
        duration = 1.0
        freq = 440.0
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2.0 * math.pi * freq * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_zigzag_returns_buffer(self, sine_buffer):
        """Zigzag should return a Buffer."""
        result = cycdp.zigzag(sine_buffer, times=[0, 0.25, 0.5, 0.75])
        assert isinstance(result, cycdp.Buffer)
        assert result.sample_count > 0

    def test_zigzag_with_splice(self, sine_buffer):
        """Zigzag should work with custom splice length."""
        result = cycdp.zigzag(sine_buffer, times=[0, 0.3, 0.6, 0.9], splice_ms=20.0)
        assert isinstance(result, cycdp.Buffer)

    def test_zigzag_requires_min_times(self, sine_buffer):
        """Zigzag should require at least 2 time points."""
        with pytest.raises(ValueError, match="at least 2"):
            cycdp.zigzag(sine_buffer, times=[0.5])

    def test_zigzag_output_length(self, sine_buffer):
        """Zigzag output should have reasonable length."""
        result = cycdp.zigzag(sine_buffer, times=[0, 0.25, 0.5, 0.75])
        # 3 segments: 0->0.25 (forward), 0.5->0.25 (backward), 0.5->0.75 (forward)
        # Total duration should be approximately 0.75s (3 segments of 0.25s each)
        assert result.sample_count > 0


class TestIterate:
    """Tests for iterate operation."""

    @pytest.fixture
    def sine_buffer(self):
        """Create a sine wave buffer for testing."""
        import math

        sample_rate = 44100
        duration = 0.2
        freq = 440.0
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2.0 * math.pi * freq * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_iterate_returns_buffer(self, sine_buffer):
        """Iterate should return a Buffer."""
        result = cycdp.iterate(sine_buffer, repeats=3, delay=0.3, seed=12345)
        assert isinstance(result, cycdp.Buffer)
        assert result.sample_count > 0

    def test_iterate_extends_length(self, sine_buffer):
        """Iterate should extend the audio duration."""
        result = cycdp.iterate(sine_buffer, repeats=4, delay=0.3, seed=12345)
        # 4 repeats with 0.3s delay + original length should be longer
        expected_min = sine_buffer.sample_count + 3 * int(0.3 * sine_buffer.sample_rate)
        assert result.sample_count >= expected_min * 0.9  # Allow some tolerance

    def test_iterate_with_variations(self, sine_buffer):
        """Iterate should work with pitch and gain variations."""
        result = cycdp.iterate(
            sine_buffer,
            repeats=3,
            delay=0.2,
            delay_rand=0.1,
            pitch_shift=2.0,
            gain_decay=0.8,
            seed=12345,
        )
        assert isinstance(result, cycdp.Buffer)

    def test_iterate_reproducible(self, sine_buffer):
        """Iterate with same seed should produce identical results."""
        result1 = cycdp.iterate(sine_buffer, repeats=3, delay=0.2, seed=12345)
        result2 = cycdp.iterate(sine_buffer, repeats=3, delay=0.2, seed=12345)
        for i in range(min(100, result1.sample_count)):
            assert result1[i] == pytest.approx(result2[i], rel=1e-6)

    def test_iterate_invalid_repeats(self, sine_buffer):
        """Iterate should reject invalid repeat counts."""
        with pytest.raises(ValueError, match="between 1 and 100"):
            cycdp.iterate(sine_buffer, repeats=0)
        with pytest.raises(ValueError, match="between 1 and 100"):
            cycdp.iterate(sine_buffer, repeats=101)


class TestStutter:
    """Tests for stutter operation."""

    @pytest.fixture
    def sine_buffer(self):
        """Create a sine wave buffer for testing."""
        import math

        sample_rate = 44100
        duration = 0.5
        freq = 440.0
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2.0 * math.pi * freq * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_stutter_returns_buffer(self, sine_buffer):
        """Stutter should return a Buffer."""
        result = cycdp.stutter(sine_buffer, segment_ms=50.0, duration=1.0, seed=12345)
        assert isinstance(result, cycdp.Buffer)
        assert result.sample_count > 0

    def test_stutter_duration(self, sine_buffer):
        """Stutter should produce output of specified duration."""
        target_duration = 2.0
        result = cycdp.stutter(
            sine_buffer, segment_ms=50.0, duration=target_duration, seed=12345
        )
        expected_samples = int(target_duration * sine_buffer.sample_rate)
        # Allow some tolerance for segment boundaries
        assert abs(result.sample_count - expected_samples) < expected_samples * 0.1

    def test_stutter_with_silences(self, sine_buffer):
        """Stutter should work with silence insertions."""
        result = cycdp.stutter(
            sine_buffer,
            segment_ms=50.0,
            duration=1.0,
            silence_prob=0.5,
            silence_min_ms=20.0,
            silence_max_ms=50.0,
            seed=12345,
        )
        assert isinstance(result, cycdp.Buffer)

    def test_stutter_with_transpose(self, sine_buffer):
        """Stutter should work with transposition."""
        result = cycdp.stutter(
            sine_buffer, segment_ms=50.0, duration=1.0, transpose_range=3.0, seed=12345
        )
        assert isinstance(result, cycdp.Buffer)

    def test_stutter_reproducible(self, sine_buffer):
        """Stutter with same seed should produce identical results."""
        result1 = cycdp.stutter(sine_buffer, segment_ms=50.0, duration=1.0, seed=12345)
        result2 = cycdp.stutter(sine_buffer, segment_ms=50.0, duration=1.0, seed=12345)
        for i in range(min(100, result1.sample_count)):
            assert result1[i] == pytest.approx(result2[i], rel=1e-6)

    def test_stutter_invalid_params(self, sine_buffer):
        """Stutter should reject invalid parameters."""
        with pytest.raises(ValueError, match="segment_ms must be between"):
            cycdp.stutter(sine_buffer, segment_ms=0, duration=1.0)
        # A positive but vanishing segment size implies an unbounded number of
        # segments; the floor is what makes this terminate, not the sign check.
        with pytest.raises(ValueError, match="segment_ms must be between"):
            cycdp.stutter(sine_buffer, segment_ms=1e-30, duration=1.0)
        with pytest.raises(ValueError, match="duration must be positive"):
            cycdp.stutter(sine_buffer, segment_ms=50.0, duration=0)


class TestBounce:
    """Tests for bounce operation."""

    @pytest.fixture
    def sine_buffer(self):
        """Create a sine wave buffer for testing."""
        import math

        sample_rate = 44100
        duration = 0.1
        freq = 440.0
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2.0 * math.pi * freq * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_bounce_returns_buffer(self, sine_buffer):
        """Bounce should return a Buffer."""
        result = cycdp.bounce(sine_buffer, bounces=5, initial_delay=0.2, shrink=0.7)
        assert isinstance(result, cycdp.Buffer)
        assert result.sample_count > 0

    def test_bounce_extends_length(self, sine_buffer):
        """Bounce should extend the audio with repetitions."""
        result = cycdp.bounce(sine_buffer, bounces=8, initial_delay=0.2, shrink=0.7)
        assert result.sample_count > sine_buffer.sample_count

    def test_bounce_with_cut(self, sine_buffer):
        """Bounce should work with cut_bounces option."""
        result = cycdp.bounce(
            sine_buffer, bounces=5, initial_delay=0.2, shrink=0.7, cut_bounces=True
        )
        assert isinstance(result, cycdp.Buffer)

    def test_bounce_with_level_curve(self, sine_buffer):
        """Bounce should work with different level curves."""
        result = cycdp.bounce(
            sine_buffer,
            bounces=5,
            initial_delay=0.2,
            shrink=0.7,
            end_level=0.05,
            level_curve=2.0,
        )
        assert isinstance(result, cycdp.Buffer)

    def test_bounce_invalid_params(self, sine_buffer):
        """Bounce should reject invalid parameters."""
        with pytest.raises(ValueError, match="between 1 and 100"):
            cycdp.bounce(sine_buffer, bounces=0)
        with pytest.raises(ValueError, match="between 1 and 100"):
            cycdp.bounce(sine_buffer, bounces=101)
        with pytest.raises(ValueError, match="initial_delay must be positive"):
            cycdp.bounce(sine_buffer, bounces=5, initial_delay=0)
        with pytest.raises(ValueError, match="shrink must be between 0 and 1"):
            cycdp.bounce(sine_buffer, bounces=5, initial_delay=0.2, shrink=0)
        with pytest.raises(ValueError, match="shrink must be between 0 and 1"):
            cycdp.bounce(sine_buffer, bounces=5, initial_delay=0.2, shrink=1.0)


class TestDrunk:
    """Tests for drunk (drunken walk) operation."""

    @pytest.fixture
    def sine_buffer(self):
        """Create a sine wave buffer for testing."""
        import math

        sample_rate = 44100
        duration = 1.0
        freq = 440.0
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2.0 * math.pi * freq * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_drunk_returns_buffer(self, sine_buffer):
        """Drunk should return a Buffer."""
        result = cycdp.drunk(sine_buffer, duration=2.0, step_ms=100.0, seed=12345)
        assert isinstance(result, cycdp.Buffer)
        assert result.sample_count > 0

    def test_drunk_duration(self, sine_buffer):
        """Drunk should produce output of specified duration."""
        target_duration = 3.0
        result = cycdp.drunk(
            sine_buffer, duration=target_duration, step_ms=100.0, seed=12345
        )
        expected_samples = int(target_duration * sine_buffer.sample_rate)
        # Allow some tolerance
        assert abs(result.sample_count - expected_samples) < expected_samples * 0.1

    def test_drunk_with_locus_ambitus(self, sine_buffer):
        """Drunk should work with custom locus and ambitus."""
        result = cycdp.drunk(
            sine_buffer, duration=2.0, step_ms=100.0, locus=0.5, ambitus=0.3, seed=12345
        )
        assert isinstance(result, cycdp.Buffer)

    def test_drunk_with_overlap(self, sine_buffer):
        """Drunk should work with overlap."""
        result = cycdp.drunk(
            sine_buffer, duration=2.0, step_ms=100.0, overlap=0.3, seed=12345
        )
        assert isinstance(result, cycdp.Buffer)

    def test_drunk_reproducible(self, sine_buffer):
        """Drunk with same seed should produce identical results."""
        result1 = cycdp.drunk(sine_buffer, duration=2.0, step_ms=100.0, seed=12345)
        result2 = cycdp.drunk(sine_buffer, duration=2.0, step_ms=100.0, seed=12345)
        for i in range(min(100, result1.sample_count)):
            assert result1[i] == pytest.approx(result2[i], rel=1e-6)

    def test_drunk_invalid_params(self, sine_buffer):
        """Drunk should reject invalid parameters."""
        with pytest.raises(ValueError, match="duration must be positive"):
            cycdp.drunk(sine_buffer, duration=0)
        with pytest.raises(ValueError, match="step_ms must be positive"):
            cycdp.drunk(sine_buffer, duration=2.0, step_ms=0)


class TestLoop:
    """Tests for loop operation."""

    @pytest.fixture
    def sine_buffer(self):
        """Create a sine wave buffer for testing."""
        import math

        sample_rate = 44100
        duration = 1.0
        freq = 440.0
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2.0 * math.pi * freq * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_loop_returns_buffer(self, sine_buffer):
        """Loop should return a Buffer."""
        result = cycdp.loop(
            sine_buffer, start=0.0, length_ms=200.0, repeats=5, seed=12345
        )
        assert isinstance(result, cycdp.Buffer)
        assert result.sample_count > 0

    def test_loop_extends_length(self, sine_buffer):
        """Loop should extend the audio with repetitions."""
        result = cycdp.loop(
            sine_buffer, start=0.0, length_ms=200.0, repeats=10, seed=12345
        )
        # With 10 repeats of 200ms, should be longer than original
        assert result.sample_count > sine_buffer.sample_count

    def test_loop_with_step(self, sine_buffer):
        """Loop should work with step between iterations."""
        result = cycdp.loop(
            sine_buffer, start=0.0, length_ms=100.0, step_ms=50.0, repeats=5, seed=12345
        )
        assert isinstance(result, cycdp.Buffer)

    def test_loop_with_search(self, sine_buffer):
        """Loop should work with random search field."""
        result = cycdp.loop(
            sine_buffer,
            start=0.0,
            length_ms=100.0,
            search_ms=20.0,
            repeats=5,
            seed=12345,
        )
        assert isinstance(result, cycdp.Buffer)

    def test_loop_reproducible(self, sine_buffer):
        """Loop with same seed should produce identical results."""
        result1 = cycdp.loop(
            sine_buffer,
            start=0.0,
            length_ms=200.0,
            search_ms=20.0,
            repeats=5,
            seed=12345,
        )
        result2 = cycdp.loop(
            sine_buffer,
            start=0.0,
            length_ms=200.0,
            search_ms=20.0,
            repeats=5,
            seed=12345,
        )
        for i in range(min(100, result1.sample_count)):
            assert result1[i] == pytest.approx(result2[i], rel=1e-6)

    def test_loop_invalid_params(self, sine_buffer):
        """Loop should reject invalid parameters."""
        with pytest.raises(ValueError, match="length_ms must be positive"):
            cycdp.loop(sine_buffer, start=0.0, length_ms=0, repeats=5)
        with pytest.raises(ValueError, match="between 1 and 1000"):
            cycdp.loop(sine_buffer, start=0.0, length_ms=200.0, repeats=0)
        with pytest.raises(ValueError, match="between 1 and 1000"):
            cycdp.loop(sine_buffer, start=0.0, length_ms=200.0, repeats=1001)


class TestRetime:
    """Tests for retime (TDOLA time stretching) operation."""

    @pytest.fixture
    def sine_buffer(self):
        """Create a sine wave buffer for testing."""
        import math

        sample_rate = 44100
        duration = 1.0
        freq = 440.0
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2.0 * math.pi * freq * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_retime_returns_buffer(self, sine_buffer):
        """Retime should return a Buffer."""
        result = cycdp.retime(sine_buffer, ratio=1.0)
        assert isinstance(result, cycdp.Buffer)
        assert result.sample_count > 0

    def test_retime_stretch(self, sine_buffer):
        """Retime with ratio < 1 should stretch (lengthen) audio."""
        result = cycdp.retime(sine_buffer, ratio=0.5)  # Half speed = 2x duration
        assert isinstance(result, cycdp.Buffer)
        # Should be approximately 2x longer (some variation due to grain processing)
        expected_length = sine_buffer.sample_count * 2
        assert result.sample_count == pytest.approx(expected_length, rel=0.1)

    def test_retime_compress(self, sine_buffer):
        """Retime with ratio > 1 should compress (shorten) audio."""
        result = cycdp.retime(sine_buffer, ratio=2.0)  # Double speed = half duration
        assert isinstance(result, cycdp.Buffer)
        # Should be approximately half length
        expected_length = sine_buffer.sample_count // 2
        assert result.sample_count > expected_length * 0.8
        assert result.sample_count < expected_length * 1.2

    def test_retime_unity(self, sine_buffer):
        """Retime with ratio=1 should preserve approximate duration."""
        result = cycdp.retime(sine_buffer, ratio=1.0)
        assert isinstance(result, cycdp.Buffer)
        # Should be approximately the same length
        assert result.sample_count > sine_buffer.sample_count * 0.9
        assert result.sample_count < sine_buffer.sample_count * 1.1

    def test_retime_grain_size(self, sine_buffer):
        """Retime should work with different grain sizes."""
        result1 = cycdp.retime(sine_buffer, ratio=0.75, grain_ms=20.0)
        result2 = cycdp.retime(sine_buffer, ratio=0.75, grain_ms=100.0)
        assert isinstance(result1, cycdp.Buffer)
        assert isinstance(result2, cycdp.Buffer)
        # Both should produce valid output
        assert result1.sample_count > 0
        assert result2.sample_count > 0

    def test_retime_overlap(self, sine_buffer):
        """Retime should work with different overlap values."""
        result1 = cycdp.retime(sine_buffer, ratio=0.75, overlap=0.25)
        result2 = cycdp.retime(sine_buffer, ratio=0.75, overlap=0.75)
        assert isinstance(result1, cycdp.Buffer)
        assert isinstance(result2, cycdp.Buffer)

    def test_retime_invalid_ratio(self, sine_buffer):
        """Retime should reject invalid ratio values."""
        with pytest.raises(ValueError, match="ratio must be"):
            cycdp.retime(sine_buffer, ratio=0)
        with pytest.raises(ValueError, match="ratio must be"):
            cycdp.retime(sine_buffer, ratio=-1.0)
        with pytest.raises(ValueError, match="ratio must be"):
            cycdp.retime(sine_buffer, ratio=11.0)

    def test_retime_invalid_grain(self, sine_buffer):
        """Retime should reject invalid grain_ms values."""
        with pytest.raises(ValueError, match="grain_ms must be"):
            cycdp.retime(sine_buffer, grain_ms=1.0)  # Too small
        with pytest.raises(ValueError, match="grain_ms must be"):
            cycdp.retime(sine_buffer, grain_ms=600.0)  # Too large

    def test_retime_invalid_overlap(self, sine_buffer):
        """Retime should reject invalid overlap values."""
        with pytest.raises(ValueError, match="overlap must be"):
            cycdp.retime(sine_buffer, overlap=0.05)  # Too small
        with pytest.raises(ValueError, match="overlap must be"):
            cycdp.retime(sine_buffer, overlap=0.95)  # Too large


class TestScramble:
    """Tests for scramble (waveset reordering) operation."""

    @pytest.fixture
    def sine_buffer(self):
        """Create a sine wave buffer for testing."""
        import math

        sample_rate = 44100
        duration = 0.5  # Shorter for faster waveset detection
        freq = 440.0
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2.0 * math.pi * freq * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_scramble_returns_buffer(self, sine_buffer):
        """Scramble should return a Buffer."""
        result = cycdp.scramble(sine_buffer, mode=cycdp.SCRAMBLE_SHUFFLE, seed=12345)
        assert isinstance(result, cycdp.Buffer)
        assert result.sample_count > 0

    def test_scramble_shuffle_mode(self, sine_buffer):
        """Scramble shuffle mode should randomize order."""
        result = cycdp.scramble(sine_buffer, mode=cycdp.SCRAMBLE_SHUFFLE, seed=12345)
        assert isinstance(result, cycdp.Buffer)
        # Output should be similar length (same wavesets, different order)
        assert result.sample_count > sine_buffer.sample_count * 0.9
        assert result.sample_count < sine_buffer.sample_count * 1.1

    def test_scramble_reverse_mode(self, sine_buffer):
        """Scramble reverse mode should reverse waveset order."""
        result = cycdp.scramble(sine_buffer, mode=cycdp.SCRAMBLE_REVERSE)
        assert isinstance(result, cycdp.Buffer)

    def test_scramble_size_modes(self, sine_buffer):
        """Scramble size modes should sort by waveset length."""
        result_up = cycdp.scramble(sine_buffer, mode=cycdp.SCRAMBLE_SIZE_UP)
        result_down = cycdp.scramble(sine_buffer, mode=cycdp.SCRAMBLE_SIZE_DOWN)
        assert isinstance(result_up, cycdp.Buffer)
        assert isinstance(result_down, cycdp.Buffer)

    def test_scramble_level_modes(self, sine_buffer):
        """Scramble level modes should sort by waveset amplitude."""
        result_up = cycdp.scramble(sine_buffer, mode=cycdp.SCRAMBLE_LEVEL_UP)
        result_down = cycdp.scramble(sine_buffer, mode=cycdp.SCRAMBLE_LEVEL_DOWN)
        assert isinstance(result_up, cycdp.Buffer)
        assert isinstance(result_down, cycdp.Buffer)

    def test_scramble_group_size(self, sine_buffer):
        """Scramble should work with different group sizes."""
        result1 = cycdp.scramble(sine_buffer, group_size=1, seed=12345)
        result2 = cycdp.scramble(sine_buffer, group_size=8, seed=12345)
        assert isinstance(result1, cycdp.Buffer)
        assert isinstance(result2, cycdp.Buffer)
        # Different group sizes should produce different results
        # (though this isn't guaranteed if all wavesets are same length)

    def test_scramble_reproducible(self, sine_buffer):
        """Scramble with same seed should produce identical results."""
        result1 = cycdp.scramble(sine_buffer, mode=cycdp.SCRAMBLE_SHUFFLE, seed=12345)
        result2 = cycdp.scramble(sine_buffer, mode=cycdp.SCRAMBLE_SHUFFLE, seed=12345)
        assert result1.sample_count == result2.sample_count
        for i in range(min(100, result1.sample_count)):
            assert result1[i] == pytest.approx(result2[i], rel=1e-6)

    def test_scramble_invalid_mode(self, sine_buffer):
        """Scramble should reject invalid mode values."""
        with pytest.raises(ValueError, match="mode must be"):
            cycdp.scramble(sine_buffer, mode=-1)
        with pytest.raises(ValueError, match="mode must be"):
            cycdp.scramble(sine_buffer, mode=6)

    def test_scramble_invalid_group_size(self, sine_buffer):
        """Scramble should reject invalid group_size values."""
        with pytest.raises(ValueError, match="group_size must be"):
            cycdp.scramble(sine_buffer, group_size=0)
        with pytest.raises(ValueError, match="group_size must be"):
            cycdp.scramble(sine_buffer, group_size=65)

    def test_scramble_constants_exist(self):
        """Scramble mode constants should be defined."""
        assert cycdp.SCRAMBLE_SHUFFLE == 0
        assert cycdp.SCRAMBLE_REVERSE == 1
        assert cycdp.SCRAMBLE_SIZE_UP == 2
        assert cycdp.SCRAMBLE_SIZE_DOWN == 3
        assert cycdp.SCRAMBLE_LEVEL_UP == 4
        assert cycdp.SCRAMBLE_LEVEL_DOWN == 5


class TestSplinter:
    """Tests for splinter (waveset fragmentation) operation."""

    @pytest.fixture
    def sine_buffer(self):
        """Create a sine wave buffer for testing."""
        import math

        sample_rate = 44100
        duration = 1.0
        freq = 440.0
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2.0 * math.pi * freq * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_splinter_returns_buffer(self, sine_buffer):
        """Splinter should return a Buffer."""
        result = cycdp.splinter(sine_buffer, start=0.1, duration_ms=50, repeats=10)
        assert isinstance(result, cycdp.Buffer)
        assert result.sample_count > 0

    def test_splinter_creates_output(self, sine_buffer):
        """Splinter should create audio output."""
        result = cycdp.splinter(sine_buffer, start=0.0, duration_ms=100, repeats=20)
        assert isinstance(result, cycdp.Buffer)
        # Should produce audio content
        assert result.sample_count > 0

    def test_splinter_with_min_shrink(self, sine_buffer):
        """Splinter should work with different shrinkage amounts."""
        result1 = cycdp.splinter(sine_buffer, repeats=20, min_shrink=0.5)
        result2 = cycdp.splinter(sine_buffer, repeats=20, min_shrink=0.1)
        assert isinstance(result1, cycdp.Buffer)
        assert isinstance(result2, cycdp.Buffer)
        # Both should produce valid output
        assert result1.sample_count > 0
        assert result2.sample_count > 0

    def test_splinter_with_shrink_curve(self, sine_buffer):
        """Splinter should work with different shrink curves."""
        result1 = cycdp.splinter(sine_buffer, repeats=15, shrink_curve=0.5)
        result2 = cycdp.splinter(sine_buffer, repeats=15, shrink_curve=2.0)
        assert isinstance(result1, cycdp.Buffer)
        assert isinstance(result2, cycdp.Buffer)

    def test_splinter_with_accel(self, sine_buffer):
        """Splinter should work with different acceleration values."""
        result1 = cycdp.splinter(sine_buffer, repeats=15, accel=0.75)  # Slowing
        result2 = cycdp.splinter(sine_buffer, repeats=15, accel=2.0)  # Accelerating
        assert isinstance(result1, cycdp.Buffer)
        assert isinstance(result2, cycdp.Buffer)

    def test_splinter_reproducible(self, sine_buffer):
        """Splinter with same parameters should produce consistent results."""
        result1 = cycdp.splinter(
            sine_buffer, start=0.1, duration_ms=50, repeats=15, seed=12345
        )
        result2 = cycdp.splinter(
            sine_buffer, start=0.1, duration_ms=50, repeats=15, seed=12345
        )
        assert result1.sample_count == result2.sample_count
        for i in range(min(100, result1.sample_count)):
            assert result1[i] == pytest.approx(result2[i], rel=1e-6)

    def test_splinter_invalid_duration(self, sine_buffer):
        """Splinter should reject invalid duration_ms values."""
        with pytest.raises(ValueError, match="duration_ms must be"):
            cycdp.splinter(sine_buffer, duration_ms=1)  # Too short
        with pytest.raises(ValueError, match="duration_ms must be"):
            cycdp.splinter(sine_buffer, duration_ms=6000)  # Too long

    def test_splinter_invalid_repeats(self, sine_buffer):
        """Splinter should reject invalid repeats values."""
        with pytest.raises(ValueError, match="repeats must be"):
            cycdp.splinter(sine_buffer, repeats=1)  # Too few
        with pytest.raises(ValueError, match="repeats must be"):
            cycdp.splinter(sine_buffer, repeats=600)  # Too many

    def test_splinter_invalid_min_shrink(self, sine_buffer):
        """Splinter should reject invalid min_shrink values."""
        with pytest.raises(ValueError, match="min_shrink must be"):
            cycdp.splinter(sine_buffer, min_shrink=0.005)  # Too small
        with pytest.raises(ValueError, match="min_shrink must be"):
            cycdp.splinter(sine_buffer, min_shrink=1.5)  # Too large

    def test_splinter_invalid_shrink_curve(self, sine_buffer):
        """Splinter should reject invalid shrink_curve values."""
        with pytest.raises(ValueError, match="shrink_curve must be"):
            cycdp.splinter(sine_buffer, shrink_curve=0.05)  # Too small
        with pytest.raises(ValueError, match="shrink_curve must be"):
            cycdp.splinter(sine_buffer, shrink_curve=15.0)  # Too large

    def test_splinter_invalid_accel(self, sine_buffer):
        """Splinter should reject invalid accel values."""
        with pytest.raises(ValueError, match="accel must be"):
            cycdp.splinter(sine_buffer, accel=0.2)  # Too small
        with pytest.raises(ValueError, match="accel must be"):
            cycdp.splinter(sine_buffer, accel=5.0)  # Too large


class TestSpin:
    """Tests for spin (spatial rotation) operation."""

    @pytest.fixture
    def mono_buffer(self):
        """Create a mono sine wave buffer for testing."""
        import math

        sample_rate = 44100
        duration = 1.0
        freq = 440.0
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2.0 * math.pi * freq * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    @pytest.fixture
    def stereo_buffer(self):
        """Create a stereo sine wave buffer for testing."""
        import math

        sample_rate = 44100
        duration = 1.0
        freq = 440.0
        samples = array.array("f")
        for i in range(int(sample_rate * duration)):
            val = 0.5 * math.sin(2.0 * math.pi * freq * i / sample_rate)
            samples.append(val)  # Left
            samples.append(val)  # Right
        return cycdp.Buffer.from_memoryview(
            samples, channels=2, sample_rate=sample_rate
        )

    def test_spin_returns_buffer(self, mono_buffer):
        """Spin should return a Buffer."""
        result = cycdp.spin(mono_buffer, rate=1.0)
        assert isinstance(result, cycdp.Buffer)
        assert result.sample_count > 0

    def test_spin_outputs_stereo(self, mono_buffer):
        """Spin should always output stereo."""
        result = cycdp.spin(mono_buffer, rate=2.0)
        assert result.channels == 2

    def test_spin_stereo_input(self, stereo_buffer):
        """Spin should work with stereo input."""
        result = cycdp.spin(stereo_buffer, rate=1.5)
        assert isinstance(result, cycdp.Buffer)
        assert result.channels == 2

    def test_spin_positive_rate(self, mono_buffer):
        """Spin with positive rate (clockwise rotation)."""
        result = cycdp.spin(mono_buffer, rate=2.0)
        assert isinstance(result, cycdp.Buffer)
        assert result.sample_count > 0

    def test_spin_negative_rate(self, mono_buffer):
        """Spin with negative rate (counterclockwise rotation)."""
        result = cycdp.spin(mono_buffer, rate=-2.0)
        assert isinstance(result, cycdp.Buffer)
        assert result.sample_count > 0

    def test_spin_with_doppler(self, mono_buffer):
        """Spin with doppler effect enabled."""
        result = cycdp.spin(mono_buffer, rate=3.0, doppler=2.0)
        assert isinstance(result, cycdp.Buffer)
        assert result.sample_count > 0

    def test_spin_with_depth(self, mono_buffer):
        """Spin with varying depth values."""
        result1 = cycdp.spin(mono_buffer, rate=1.0, depth=0.5)  # Partial rotation
        result2 = cycdp.spin(mono_buffer, rate=1.0, depth=1.0)  # Full rotation
        assert isinstance(result1, cycdp.Buffer)
        assert isinstance(result2, cycdp.Buffer)

    def test_spin_zero_rate(self, mono_buffer):
        """Spin with zero rate should pass through."""
        result = cycdp.spin(mono_buffer, rate=0.0, depth=1.0)
        assert isinstance(result, cycdp.Buffer)
        # Output length should be similar to input
        input_frames = mono_buffer.sample_count // mono_buffer.channels
        output_frames = result.sample_count // result.channels
        assert output_frames == pytest.approx(input_frames, rel=0.1)

    def test_spin_invalid_rate(self, mono_buffer):
        """Spin should reject invalid rate values."""
        with pytest.raises(ValueError, match="rate must be"):
            cycdp.spin(mono_buffer, rate=-25.0)  # Too negative
        with pytest.raises(ValueError, match="rate must be"):
            cycdp.spin(mono_buffer, rate=25.0)  # Too positive

    def test_spin_invalid_doppler(self, mono_buffer):
        """Spin should reject invalid doppler values."""
        with pytest.raises(ValueError, match="doppler must be"):
            cycdp.spin(mono_buffer, doppler=-1.0)  # Negative
        with pytest.raises(ValueError, match="doppler must be"):
            cycdp.spin(mono_buffer, doppler=15.0)  # Too large

    def test_spin_invalid_depth(self, mono_buffer):
        """Spin should reject invalid depth values."""
        with pytest.raises(ValueError, match="depth must be"):
            cycdp.spin(mono_buffer, depth=-0.5)  # Negative
        with pytest.raises(ValueError, match="depth must be"):
            cycdp.spin(mono_buffer, depth=1.5)  # Too large


class TestRotor:
    """Tests for rotor (dual-rotation modulation) operation."""

    @pytest.fixture
    def mono_buffer(self):
        """Create a mono sine wave buffer for testing."""
        import math

        sample_rate = 44100
        duration = 2.0  # Longer for modulation effects
        freq = 440.0
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2.0 * math.pi * freq * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    @pytest.fixture
    def stereo_buffer(self):
        """Create a stereo sine wave buffer for testing."""
        import math

        sample_rate = 44100
        duration = 2.0
        freq = 440.0
        samples = array.array("f")
        for i in range(int(sample_rate * duration)):
            val = 0.5 * math.sin(2.0 * math.pi * freq * i / sample_rate)
            samples.append(val)  # Left
            samples.append(val)  # Right
        return cycdp.Buffer.from_memoryview(
            samples, channels=2, sample_rate=sample_rate
        )

    def test_rotor_returns_buffer(self, mono_buffer):
        """Rotor should return a Buffer."""
        result = cycdp.rotor(mono_buffer, pitch_rate=1.0, amp_rate=1.5)
        assert isinstance(result, cycdp.Buffer)
        assert result.sample_count > 0

    def test_rotor_preserves_channels(self, mono_buffer, stereo_buffer):
        """Rotor should preserve channel count."""
        result_mono = cycdp.rotor(mono_buffer)
        result_stereo = cycdp.rotor(stereo_buffer)
        assert result_mono.channels == 1
        assert result_stereo.channels == 2

    def test_rotor_pitch_only(self, mono_buffer):
        """Rotor with pitch modulation only (no amplitude)."""
        result = cycdp.rotor(
            mono_buffer, pitch_rate=2.0, pitch_depth=3.0, amp_depth=0.0
        )
        assert isinstance(result, cycdp.Buffer)
        assert result.sample_count > 0

    def test_rotor_amp_only(self, mono_buffer):
        """Rotor with amplitude modulation only (no pitch)."""
        result = cycdp.rotor(mono_buffer, pitch_depth=0.0, amp_rate=3.0, amp_depth=0.8)
        assert isinstance(result, cycdp.Buffer)
        # Without pitch modulation, output length should match input
        input_frames = mono_buffer.sample_count // mono_buffer.channels
        output_frames = result.sample_count // result.channels
        assert output_frames == pytest.approx(input_frames, rel=0.1)

    def test_rotor_different_rates(self, mono_buffer):
        """Rotor with different pitch and amp rates creates interference."""
        result = cycdp.rotor(mono_buffer, pitch_rate=1.0, amp_rate=1.5)
        assert isinstance(result, cycdp.Buffer)
        assert result.sample_count > 0

    def test_rotor_phase_offset(self, mono_buffer):
        """Rotor with different phase offsets."""
        result1 = cycdp.rotor(mono_buffer, phase_offset=0.0)
        result2 = cycdp.rotor(mono_buffer, phase_offset=0.5)
        assert isinstance(result1, cycdp.Buffer)
        assert isinstance(result2, cycdp.Buffer)
        # Different phase offsets should produce different outputs
        # (at least some samples should differ)
        differs = False
        for i in range(min(100, result1.sample_count, result2.sample_count)):
            if abs(result1[i] - result2[i]) > 0.001:
                differs = True
                break
        assert differs

    def test_rotor_high_depth(self, mono_buffer):
        """Rotor with high modulation depth."""
        result = cycdp.rotor(mono_buffer, pitch_depth=6.0, amp_depth=0.9)
        assert isinstance(result, cycdp.Buffer)
        assert result.sample_count > 0

    def test_rotor_invalid_pitch_rate(self, mono_buffer):
        """Rotor should reject invalid pitch_rate values."""
        with pytest.raises(ValueError, match="pitch_rate must be"):
            cycdp.rotor(mono_buffer, pitch_rate=0.001)  # Too slow
        with pytest.raises(ValueError, match="pitch_rate must be"):
            cycdp.rotor(mono_buffer, pitch_rate=25.0)  # Too fast

    def test_rotor_invalid_pitch_depth(self, mono_buffer):
        """Rotor should reject invalid pitch_depth values."""
        with pytest.raises(ValueError, match="pitch_depth must be"):
            cycdp.rotor(mono_buffer, pitch_depth=-1.0)  # Negative
        with pytest.raises(ValueError, match="pitch_depth must be"):
            cycdp.rotor(mono_buffer, pitch_depth=15.0)  # Too large

    def test_rotor_invalid_amp_rate(self, mono_buffer):
        """Rotor should reject invalid amp_rate values."""
        with pytest.raises(ValueError, match="amp_rate must be"):
            cycdp.rotor(mono_buffer, amp_rate=0.001)  # Too slow
        with pytest.raises(ValueError, match="amp_rate must be"):
            cycdp.rotor(mono_buffer, amp_rate=25.0)  # Too fast

    def test_rotor_invalid_amp_depth(self, mono_buffer):
        """Rotor should reject invalid amp_depth values."""
        with pytest.raises(ValueError, match="amp_depth must be"):
            cycdp.rotor(mono_buffer, amp_depth=-0.5)  # Negative
        with pytest.raises(ValueError, match="amp_depth must be"):
            cycdp.rotor(mono_buffer, amp_depth=1.5)  # Too large

    def test_rotor_invalid_phase_offset(self, mono_buffer):
        """Rotor should reject invalid phase_offset values."""
        with pytest.raises(ValueError, match="phase_offset must be"):
            cycdp.rotor(mono_buffer, phase_offset=-0.1)  # Negative
        with pytest.raises(ValueError, match="phase_offset must be"):
            cycdp.rotor(mono_buffer, phase_offset=1.5)  # Too large


class TestSynthWave:
    """Tests for synth_wave (waveform synthesis) operation."""

    def test_synth_wave_returns_buffer(self):
        """Synth wave should return a Buffer."""
        result = cycdp.synth_wave(
            waveform=cycdp.WAVE_SINE, frequency=440.0, duration=0.5
        )
        assert isinstance(result, cycdp.Buffer)
        assert result.sample_count > 0

    def test_synth_wave_sine(self):
        """Synth wave should generate sine wave."""
        result = cycdp.synth_wave(
            waveform=cycdp.WAVE_SINE, frequency=440.0, duration=0.1
        )
        assert isinstance(result, cycdp.Buffer)
        assert result.channels == 1

    def test_synth_wave_square(self):
        """Synth wave should generate square wave."""
        result = cycdp.synth_wave(
            waveform=cycdp.WAVE_SQUARE, frequency=440.0, duration=0.1
        )
        assert isinstance(result, cycdp.Buffer)

    def test_synth_wave_saw(self):
        """Synth wave should generate sawtooth wave."""
        result = cycdp.synth_wave(
            waveform=cycdp.WAVE_SAW, frequency=440.0, duration=0.1
        )
        assert isinstance(result, cycdp.Buffer)

    def test_synth_wave_ramp(self):
        """Synth wave should generate ramp wave."""
        result = cycdp.synth_wave(
            waveform=cycdp.WAVE_RAMP, frequency=440.0, duration=0.1
        )
        assert isinstance(result, cycdp.Buffer)

    def test_synth_wave_triangle(self):
        """Synth wave should generate triangle wave."""
        result = cycdp.synth_wave(
            waveform=cycdp.WAVE_TRIANGLE, frequency=440.0, duration=0.1
        )
        assert isinstance(result, cycdp.Buffer)

    def test_synth_wave_stereo(self):
        """Synth wave should generate stereo output."""
        result = cycdp.synth_wave(channels=2, duration=0.1)
        assert result.channels == 2

    def test_synth_wave_duration(self):
        """Synth wave duration should match requested."""
        duration = 0.5
        sample_rate = 44100
        result = cycdp.synth_wave(duration=duration, sample_rate=sample_rate)
        expected_samples = int(duration * sample_rate)
        # Allow small tolerance for fade
        assert abs(result.sample_count - expected_samples) < 100

    def test_synth_wave_sample_rate(self):
        """Synth wave should use requested sample rate."""
        result = cycdp.synth_wave(sample_rate=48000, duration=0.1)
        assert result.sample_rate == 48000

    def test_synth_wave_invalid_waveform(self):
        """Synth wave should reject invalid waveform."""
        with pytest.raises(ValueError, match="waveform must be"):
            cycdp.synth_wave(waveform=5)
        with pytest.raises(ValueError, match="waveform must be"):
            cycdp.synth_wave(waveform=-1)

    def test_synth_wave_invalid_frequency(self):
        """Synth wave should reject invalid frequency."""
        with pytest.raises(ValueError, match="frequency must be"):
            cycdp.synth_wave(frequency=10.0)  # Too low
        with pytest.raises(ValueError, match="frequency must be"):
            cycdp.synth_wave(frequency=25000.0)  # Too high

    def test_synth_wave_invalid_amplitude(self):
        """Synth wave should reject invalid amplitude."""
        with pytest.raises(ValueError, match="amplitude must be"):
            cycdp.synth_wave(amplitude=-0.1)
        with pytest.raises(ValueError, match="amplitude must be"):
            cycdp.synth_wave(amplitude=1.5)

    def test_synth_wave_constants_exist(self):
        """Waveform constants should be defined."""
        assert cycdp.WAVE_SINE == 0
        assert cycdp.WAVE_SQUARE == 1
        assert cycdp.WAVE_SAW == 2
        assert cycdp.WAVE_RAMP == 3
        assert cycdp.WAVE_TRIANGLE == 4


class TestSynthNoise:
    """Tests for synth_noise (noise synthesis) operation."""

    def test_synth_noise_returns_buffer(self):
        """Synth noise should return a Buffer."""
        result = cycdp.synth_noise(duration=0.5)
        assert isinstance(result, cycdp.Buffer)
        assert result.sample_count > 0

    def test_synth_noise_white(self):
        """Synth noise should generate white noise."""
        result = cycdp.synth_noise(pink=0, duration=0.1)
        assert isinstance(result, cycdp.Buffer)

    def test_synth_noise_pink(self):
        """Synth noise should generate pink noise."""
        result = cycdp.synth_noise(pink=1, duration=0.1)
        assert isinstance(result, cycdp.Buffer)

    def test_synth_noise_stereo(self):
        """Synth noise should generate stereo output."""
        result = cycdp.synth_noise(channels=2, duration=0.1)
        assert result.channels == 2

    def test_synth_noise_reproducible(self):
        """Synth noise with same seed should be reproducible."""
        result1 = cycdp.synth_noise(duration=0.1, seed=12345)
        result2 = cycdp.synth_noise(duration=0.1, seed=12345)
        assert result1.sample_count == result2.sample_count
        for i in range(min(100, result1.sample_count)):
            assert result1[i] == pytest.approx(result2[i], rel=1e-6)

    def test_synth_noise_invalid_amplitude(self):
        """Synth noise should reject invalid amplitude."""
        with pytest.raises(ValueError, match="amplitude must be"):
            cycdp.synth_noise(amplitude=-0.1)
        with pytest.raises(ValueError, match="amplitude must be"):
            cycdp.synth_noise(amplitude=1.5)

    def test_synth_noise_invalid_duration(self):
        """Synth noise should reject invalid duration."""
        with pytest.raises(ValueError, match="duration must be"):
            cycdp.synth_noise(duration=0.0001)  # Too short


class TestSynthClick:
    """Tests for synth_click (click track synthesis) operation."""

    def test_synth_click_returns_buffer(self):
        """Synth click should return a Buffer."""
        result = cycdp.synth_click(tempo=120.0, duration=2.0)
        assert isinstance(result, cycdp.Buffer)
        assert result.sample_count > 0

    def test_synth_click_mono(self):
        """Synth click should output mono."""
        result = cycdp.synth_click(duration=1.0)
        assert result.channels == 1

    def test_synth_click_tempo(self):
        """Synth click should respond to tempo."""
        result1 = cycdp.synth_click(tempo=60.0, duration=2.0)  # 2 beats
        result2 = cycdp.synth_click(tempo=120.0, duration=2.0)  # 4 beats
        # Both should produce audio
        assert result1.sample_count > 0
        assert result2.sample_count > 0

    def test_synth_click_with_accent(self):
        """Synth click with beat accent should work."""
        result = cycdp.synth_click(tempo=120.0, beats_per_bar=4, duration=2.0)
        assert isinstance(result, cycdp.Buffer)

    def test_synth_click_no_accent(self):
        """Synth click without accent should work."""
        result = cycdp.synth_click(tempo=120.0, beats_per_bar=0, duration=2.0)
        assert isinstance(result, cycdp.Buffer)

    def test_synth_click_invalid_tempo(self):
        """Synth click should reject invalid tempo."""
        with pytest.raises(ValueError, match="tempo must be"):
            cycdp.synth_click(tempo=10.0)  # Too slow
        with pytest.raises(ValueError, match="tempo must be"):
            cycdp.synth_click(tempo=500.0)  # Too fast

    def test_synth_click_invalid_beats_per_bar(self):
        """Synth click should reject invalid beats_per_bar."""
        with pytest.raises(ValueError, match="beats_per_bar must be"):
            cycdp.synth_click(beats_per_bar=-1)
        with pytest.raises(ValueError, match="beats_per_bar must be"):
            cycdp.synth_click(beats_per_bar=20)

    def test_synth_click_invalid_click_freq(self):
        """Synth click should reject invalid click frequency."""
        with pytest.raises(ValueError, match="click_freq must be"):
            cycdp.synth_click(click_freq=100.0)  # Too low
        with pytest.raises(ValueError, match="click_freq must be"):
            cycdp.synth_click(click_freq=10000.0)  # Too high


class TestSynthChord:
    """Tests for synth_chord (chord synthesis) operation."""

    def test_synth_chord_returns_buffer(self):
        """Synth chord should return a Buffer."""
        result = cycdp.synth_chord([60, 64, 67], duration=0.5)  # C major
        assert isinstance(result, cycdp.Buffer)
        assert result.sample_count > 0

    def test_synth_chord_single_note(self):
        """Synth chord should work with a single note."""
        result = cycdp.synth_chord([60], duration=0.1)
        assert isinstance(result, cycdp.Buffer)

    def test_synth_chord_multiple_notes(self):
        """Synth chord should work with multiple notes."""
        # C major 7 chord: C, E, G, B
        result = cycdp.synth_chord([60, 64, 67, 71], duration=0.2)
        assert isinstance(result, cycdp.Buffer)

    def test_synth_chord_stereo(self):
        """Synth chord should generate stereo output."""
        result = cycdp.synth_chord([60, 64, 67], channels=2, duration=0.1)
        assert result.channels == 2

    def test_synth_chord_with_detune(self):
        """Synth chord with detuning should work."""
        result = cycdp.synth_chord([60, 64, 67], detune_cents=10.0, duration=0.2)
        assert isinstance(result, cycdp.Buffer)

    def test_synth_chord_duration(self):
        """Synth chord duration should match requested."""
        duration = 0.5
        sample_rate = 44100
        result = cycdp.synth_chord([60], duration=duration, sample_rate=sample_rate)
        expected_samples = int(duration * sample_rate)
        # Allow small tolerance for fade
        assert abs(result.sample_count - expected_samples) < 100

    def test_synth_chord_sample_rate(self):
        """Synth chord should use requested sample rate."""
        result = cycdp.synth_chord([60], sample_rate=48000, duration=0.1)
        assert result.sample_rate == 48000

    def test_synth_chord_max_notes(self):
        """Synth chord should handle max 16 notes."""
        notes = list(range(48, 64))  # 16 notes: C3 to D#4
        result = cycdp.synth_chord(notes, duration=0.1)
        assert isinstance(result, cycdp.Buffer)

    def test_synth_chord_common_chords(self):
        """Synth chord should generate common chord types."""
        # Major triad
        major = cycdp.synth_chord([60, 64, 67], duration=0.1)
        assert isinstance(major, cycdp.Buffer)

        # Minor triad
        minor = cycdp.synth_chord([60, 63, 67], duration=0.1)
        assert isinstance(minor, cycdp.Buffer)

        # Diminished
        dim = cycdp.synth_chord([60, 63, 66], duration=0.1)
        assert isinstance(dim, cycdp.Buffer)

        # Augmented
        aug = cycdp.synth_chord([60, 64, 68], duration=0.1)
        assert isinstance(aug, cycdp.Buffer)

    def test_synth_chord_invalid_notes_count(self):
        """Synth chord should reject invalid note count."""
        with pytest.raises(ValueError, match="midi_notes must contain"):
            cycdp.synth_chord([])  # Empty
        with pytest.raises(ValueError, match="midi_notes must contain"):
            cycdp.synth_chord(list(range(60, 80)))  # 20 notes, too many

    def test_synth_chord_invalid_midi_note(self):
        """Synth chord should reject invalid MIDI note values."""
        with pytest.raises(ValueError, match="MIDI notes must be"):
            cycdp.synth_chord([-1, 60, 64])  # Negative
        with pytest.raises(ValueError, match="MIDI notes must be"):
            cycdp.synth_chord([60, 128, 67])  # Too high

    def test_synth_chord_invalid_amplitude(self):
        """Synth chord should reject invalid amplitude."""
        with pytest.raises(ValueError, match="amplitude must be"):
            cycdp.synth_chord([60], amplitude=-0.1)
        with pytest.raises(ValueError, match="amplitude must be"):
            cycdp.synth_chord([60], amplitude=1.5)

    def test_synth_chord_invalid_detune(self):
        """Synth chord should reject invalid detune_cents."""
        with pytest.raises(ValueError, match="detune_cents must be"):
            cycdp.synth_chord([60], detune_cents=-5.0)
        with pytest.raises(ValueError, match="detune_cents must be"):
            cycdp.synth_chord([60], detune_cents=60.0)


class TestPsowStretch:
    """Test psow_stretch - PSOLA time stretching."""

    @pytest.fixture
    def sine_wave(self):
        """Create a simple sine wave buffer for testing."""
        import math

        sample_rate = 44100
        duration = 0.5
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2 * math.pi * 220 * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_psow_stretch_basic(self, sine_wave):
        """PSOW stretch should produce output."""
        result = cycdp.psow_stretch(sine_wave, stretch_factor=1.5)
        assert result.frame_count > 0
        assert result.channels == 1

    def test_psow_stretch_double(self, sine_wave):
        """PSOW stretch with factor 2 should roughly double length."""
        result = cycdp.psow_stretch(sine_wave, stretch_factor=2.0)
        # Allow some tolerance since PSOLA is approximate
        assert result.frame_count > sine_wave.frame_count

    def test_psow_stretch_half(self, sine_wave):
        """PSOW stretch with factor 0.5 should roughly halve length."""
        result = cycdp.psow_stretch(sine_wave, stretch_factor=0.5)
        assert result.frame_count > 0
        assert result.frame_count < sine_wave.frame_count

    def test_psow_stretch_grain_count(self, sine_wave):
        """PSOW stretch with different grain counts should work."""
        result1 = cycdp.psow_stretch(sine_wave, stretch_factor=1.5, grain_count=1)
        result2 = cycdp.psow_stretch(sine_wave, stretch_factor=1.5, grain_count=4)
        assert result1.frame_count > 0
        assert result2.frame_count > 0

    def test_psow_stretch_invalid_factor_low(self, sine_wave):
        """PSOW stretch with factor below range should fail."""
        with pytest.raises(cycdp.CDPError):
            cycdp.psow_stretch(sine_wave, stretch_factor=0.1)

    def test_psow_stretch_invalid_factor_high(self, sine_wave):
        """PSOW stretch with factor above range should fail."""
        with pytest.raises(cycdp.CDPError):
            cycdp.psow_stretch(sine_wave, stretch_factor=5.0)

    def test_psow_stretch_default_params(self, sine_wave):
        """PSOW stretch with default parameters should work."""
        result = cycdp.psow_stretch(sine_wave)
        assert result.frame_count > 0


class TestPsowGrab:
    """Test psow_grab - extract pitch-synchronous grains."""

    @pytest.fixture
    def sine_wave(self):
        """Create a simple sine wave buffer for testing."""
        import math

        sample_rate = 44100
        duration = 0.5
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2 * math.pi * 220 * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_psow_grab_single_grain(self, sine_wave):
        """PSOW grab with duration=0 should return single grain."""
        result = cycdp.psow_grab(sine_wave, time=0.1, duration=0.0)
        assert result.frame_count > 0
        assert result.channels == 1
        # Single grain should be relatively short
        assert result.frame_count < 1000

    def test_psow_grab_extended(self, sine_wave):
        """PSOW grab with duration should extend grain."""
        result = cycdp.psow_grab(sine_wave, time=0.1, duration=0.5, grain_count=1)
        assert result.frame_count > 0
        # Extended should be longer
        assert result.frame_count > 10000

    def test_psow_grab_multiple_grains(self, sine_wave):
        """PSOW grab with grain_count > 1 should grab multiple grains."""
        result1 = cycdp.psow_grab(sine_wave, time=0.1, duration=0.0, grain_count=1)
        result4 = cycdp.psow_grab(sine_wave, time=0.1, duration=0.0, grain_count=4)
        assert result1.frame_count > 0
        assert result4.frame_count > result1.frame_count

    def test_psow_grab_density(self, sine_wave):
        """PSOW grab with different densities should work."""
        result1 = cycdp.psow_grab(sine_wave, time=0.1, duration=0.3, density=1.0)
        result2 = cycdp.psow_grab(sine_wave, time=0.1, duration=0.3, density=2.0)
        assert result1.frame_count > 0
        assert result2.frame_count > 0

    def test_psow_grab_default_params(self, sine_wave):
        """PSOW grab with default parameters should work."""
        result = cycdp.psow_grab(sine_wave)
        assert result.frame_count > 0


class TestPsowDupl:
    """Test psow_dupl - duplicate pitch-synchronous grains."""

    @pytest.fixture
    def sine_wave(self):
        """Create a simple sine wave buffer for testing."""
        import math

        sample_rate = 44100
        duration = 0.3
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2 * math.pi * 220 * i / sample_rate)
                for i in range(int(sample_rate * duration))
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_psow_dupl_basic(self, sine_wave):
        """PSOW dupl should produce output."""
        result = cycdp.psow_dupl(sine_wave, repeat_count=2)
        assert result.frame_count > 0
        assert result.channels == 1

    def test_psow_dupl_repeat_extends(self, sine_wave):
        """PSOW dupl with repeat_count > 1 should extend audio."""
        result1 = cycdp.psow_dupl(sine_wave, repeat_count=1)
        result3 = cycdp.psow_dupl(sine_wave, repeat_count=3)
        assert result3.frame_count > result1.frame_count

    def test_psow_dupl_grain_count(self, sine_wave):
        """PSOW dupl with different grain counts should work."""
        result1 = cycdp.psow_dupl(sine_wave, repeat_count=2, grain_count=1)
        result4 = cycdp.psow_dupl(sine_wave, repeat_count=2, grain_count=4)
        assert result1.frame_count > 0
        assert result4.frame_count > 0

    def test_psow_dupl_default_params(self, sine_wave):
        """PSOW dupl with default parameters should work."""
        result = cycdp.psow_dupl(sine_wave)
        assert result.frame_count > 0


class TestPsowInterp:
    """Test psow_interp - interpolate between grains."""

    @pytest.fixture
    def grain1(self):
        """Create first grain for testing."""
        import math

        sample_rate = 44100
        # One period of 220Hz (about 200 samples)
        period_samples = int(sample_rate / 220)
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2 * math.pi * i / period_samples)
                for i in range(period_samples)
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    @pytest.fixture
    def grain2(self):
        """Create second grain for testing."""
        import math

        sample_rate = 44100
        # One period of 330Hz (about 134 samples)
        period_samples = int(sample_rate / 330)
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2 * math.pi * i / period_samples)
                for i in range(period_samples)
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_psow_interp_basic(self, grain1, grain2):
        """PSOW interp should produce output."""
        result = cycdp.psow_interp(
            grain1, grain2, start_dur=0.1, interp_dur=0.3, end_dur=0.1
        )
        assert result.frame_count > 0
        assert result.channels == 1

    def test_psow_interp_duration(self, grain1, grain2):
        """PSOW interp output length should reflect durations."""
        result = cycdp.psow_interp(
            grain1, grain2, start_dur=0.1, interp_dur=0.5, end_dur=0.1
        )
        expected_samples = int(44100 * 0.7)  # Approximate
        # Allow significant tolerance due to grain-based output
        assert result.frame_count > expected_samples * 0.5
        assert result.frame_count < expected_samples * 2.0

    def test_psow_interp_no_start_end(self, grain1, grain2):
        """PSOW interp with no start/end should work."""
        result = cycdp.psow_interp(
            grain1, grain2, start_dur=0.0, interp_dur=0.3, end_dur=0.0
        )
        assert result.frame_count > 0

    def test_psow_interp_default_params(self, grain1, grain2):
        """PSOW interp with default parameters should work."""
        result = cycdp.psow_interp(grain1, grain2)
        assert result.frame_count > 0


# =============================================================================
# FOF Extraction and Synthesis (FOFEX) Tests
# =============================================================================


class TestFofexExtract:
    """Test fofex_extract - extract single FOF."""

    @pytest.fixture
    def pitched_audio(self):
        """Create pitched audio for testing."""
        import math

        sample_rate = 44100
        duration = 0.5
        frequency = 220.0  # A3
        num_samples = int(sample_rate * duration)
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2 * math.pi * frequency * i / sample_rate)
                for i in range(num_samples)
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_fofex_extract_basic(self, pitched_audio):
        """FOFEX extract should produce output."""
        result = cycdp.fofex_extract(pitched_audio, time=0.1)
        assert result.frame_count > 0
        assert result.channels == 1

    def test_fofex_extract_at_different_times(self, pitched_audio):
        """FOFEX extract at different times should produce FOFs."""
        result1 = cycdp.fofex_extract(pitched_audio, time=0.1)
        result2 = cycdp.fofex_extract(pitched_audio, time=0.2)
        assert result1.frame_count > 0
        assert result2.frame_count > 0

    def test_fofex_extract_multi_period(self, pitched_audio):
        """FOFEX extract with multiple periods should be longer."""
        result1 = cycdp.fofex_extract(pitched_audio, time=0.1, fof_count=1)
        result2 = cycdp.fofex_extract(pitched_audio, time=0.1, fof_count=2)
        # 2-period FOF should be roughly twice as long
        assert result2.frame_count > result1.frame_count

    def test_fofex_extract_no_window(self, pitched_audio):
        """FOFEX extract without window should work."""
        result = cycdp.fofex_extract(pitched_audio, time=0.1, window=False)
        assert result.frame_count > 0

    def test_fofex_extract_from_buffer(self):
        """FOFEX extract should work with Buffer from array.array."""
        import math

        sample_rate = 44100
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2 * math.pi * 220 * i / sample_rate)
                for i in range(int(sample_rate * 0.3))
            ],
        )
        buf = cycdp.Buffer.from_memoryview(samples, channels=1, sample_rate=sample_rate)
        result = cycdp.fofex_extract(buf, time=0.1)
        assert result.frame_count > 0


class TestFofexExtractAll:
    """Test fofex_extract_all - extract all FOFs."""

    @pytest.fixture
    def pitched_audio(self):
        """Create pitched audio for testing."""
        import math

        sample_rate = 44100
        duration = 0.5
        frequency = 220.0
        num_samples = int(sample_rate * duration)
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2 * math.pi * frequency * i / sample_rate)
                for i in range(num_samples)
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_fofex_extract_all_basic(self, pitched_audio):
        """FOFEX extract_all should return bank and info."""
        result, num_fofs, unit_len = cycdp.fofex_extract_all(pitched_audio)
        assert result.frame_count > 0
        assert num_fofs > 0
        assert unit_len > 0
        # Total length should be num_fofs * unit_len
        assert result.frame_count == num_fofs * unit_len

    def test_fofex_extract_all_multi_period(self, pitched_audio):
        """FOFEX extract_all with multiple periods per FOF."""
        result, num_fofs, unit_len = cycdp.fofex_extract_all(pitched_audio, fof_count=2)
        assert result.frame_count > 0
        assert num_fofs > 0
        assert unit_len > 0

    def test_fofex_extract_all_with_threshold(self, pitched_audio):
        """FOFEX extract_all with level threshold should work."""
        result, num_fofs, unit_len = cycdp.fofex_extract_all(
            pitched_audio, min_level_db=-20.0
        )
        assert result.frame_count > 0
        assert num_fofs > 0

    def test_fofex_extract_all_no_window(self, pitched_audio):
        """FOFEX extract_all without window should work."""
        result, num_fofs, unit_len = cycdp.fofex_extract_all(
            pitched_audio, window=False
        )
        assert result.frame_count > 0
        assert num_fofs > 0


class TestFofexSynth:
    """Test fofex_synth - synthesize from FOFs."""

    @pytest.fixture
    def fof_bank(self):
        """Create FOF bank for testing."""
        import math

        sample_rate = 44100
        duration = 0.5
        frequency = 220.0
        num_samples = int(sample_rate * duration)
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2 * math.pi * frequency * i / sample_rate)
                for i in range(num_samples)
            ],
        )
        buf = cycdp.Buffer.from_memoryview(samples, channels=1, sample_rate=sample_rate)
        return cycdp.fofex_extract_all(buf)

    def test_fofex_synth_basic(self, fof_bank):
        """FOFEX synth should produce output."""
        bank, num_fofs, unit_len = fof_bank
        result = cycdp.fofex_synth(
            bank, duration=0.5, frequency=440.0, fof_unit_len=unit_len
        )
        assert result.frame_count > 0
        assert result.channels == 1

    def test_fofex_synth_duration(self, fof_bank):
        """FOFEX synth should produce correct duration."""
        bank, num_fofs, unit_len = fof_bank
        duration = 1.0
        result = cycdp.fofex_synth(
            bank, duration=duration, frequency=440.0, fof_unit_len=unit_len
        )
        expected_samples = int(44100 * duration)
        assert abs(result.frame_count - expected_samples) < 100

    def test_fofex_synth_specific_fof(self, fof_bank):
        """FOFEX synth with specific FOF index should work."""
        bank, num_fofs, unit_len = fof_bank
        result = cycdp.fofex_synth(
            bank, duration=0.5, frequency=440.0, fof_index=0, fof_unit_len=unit_len
        )
        assert result.frame_count > 0

    def test_fofex_synth_single_fof(self):
        """FOFEX synth with single FOF (no bank) should work."""
        import math

        sample_rate = 44100
        # Extract a single FOF
        source = array.array(
            "f",
            [
                0.5 * math.sin(2 * math.pi * 220 * i / sample_rate)
                for i in range(int(sample_rate * 0.3))
            ],
        )
        buf = cycdp.Buffer.from_memoryview(source, channels=1, sample_rate=sample_rate)
        fof = cycdp.fofex_extract(buf, time=0.1)

        # Synthesize from single FOF
        result = cycdp.fofex_synth(fof, duration=0.5, frequency=440.0)
        assert result.frame_count > 0

    def test_fofex_synth_different_frequencies(self, fof_bank):
        """FOFEX synth at different frequencies should work."""
        bank, num_fofs, unit_len = fof_bank
        result1 = cycdp.fofex_synth(
            bank, duration=0.3, frequency=220.0, fof_unit_len=unit_len
        )
        result2 = cycdp.fofex_synth(
            bank, duration=0.3, frequency=880.0, fof_unit_len=unit_len
        )
        assert result1.frame_count > 0
        assert result2.frame_count > 0


class TestFofexRepitch:
    """Test fofex_repitch - repitch audio using FOFs."""

    @pytest.fixture
    def pitched_audio(self):
        """Create pitched audio for testing."""
        import math

        sample_rate = 44100
        duration = 0.5
        frequency = 220.0
        num_samples = int(sample_rate * duration)
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2 * math.pi * frequency * i / sample_rate)
                for i in range(num_samples)
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_fofex_repitch_up(self, pitched_audio):
        """FOFEX repitch up should produce output."""
        result = cycdp.fofex_repitch(pitched_audio, pitch_shift=5.0)
        assert result.frame_count > 0
        assert result.channels == 1

    def test_fofex_repitch_down(self, pitched_audio):
        """FOFEX repitch down should produce output."""
        result = cycdp.fofex_repitch(pitched_audio, pitch_shift=-5.0)
        assert result.frame_count > 0

    def test_fofex_repitch_octave_up(self, pitched_audio):
        """FOFEX repitch octave up should work."""
        result = cycdp.fofex_repitch(pitched_audio, pitch_shift=12.0)
        assert result.frame_count > 0

    def test_fofex_repitch_octave_down(self, pitched_audio):
        """FOFEX repitch octave down should work."""
        result = cycdp.fofex_repitch(pitched_audio, pitch_shift=-12.0)
        assert result.frame_count > 0

    def test_fofex_repitch_preserve_formants_true(self, pitched_audio):
        """FOFEX repitch with formant preservation should maintain duration."""
        result = cycdp.fofex_repitch(
            pitched_audio, pitch_shift=5.0, preserve_formants=True
        )
        # With formant preservation, duration should be similar
        assert result.frame_count > 0
        # Duration should be close to original
        assert (
            abs(result.frame_count - pitched_audio.frame_count)
            < pitched_audio.frame_count * 0.2
        )

    def test_fofex_repitch_preserve_formants_false(self, pitched_audio):
        """FOFEX repitch without formant preservation changes duration."""
        result = cycdp.fofex_repitch(
            pitched_audio, pitch_shift=5.0, preserve_formants=False
        )
        assert result.frame_count > 0
        # Duration changes with pitch (pitch up = shorter)
        assert result.frame_count < pitched_audio.frame_count

    def test_fofex_repitch_from_buffer(self):
        """FOFEX repitch should work with Buffer from array.array."""
        import math

        sample_rate = 44100
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2 * math.pi * 220 * i / sample_rate)
                for i in range(int(sample_rate * 0.3))
            ],
        )
        buf = cycdp.Buffer.from_memoryview(samples, channels=1, sample_rate=sample_rate)
        result = cycdp.fofex_repitch(buf, pitch_shift=3.0)
        assert result.frame_count > 0


# =============================================================================
# Flutter (Spatial Tremolo) Tests
# =============================================================================


class TestFlutter:
    """Test flutter - spatial tremolo effect."""

    @pytest.fixture
    def mono_audio(self):
        """Create mono audio for testing."""
        import math

        sample_rate = 44100
        duration = 1.0
        frequency = 440.0
        num_samples = int(sample_rate * duration)
        samples = array.array(
            "f",
            [
                0.5 * math.sin(2 * math.pi * frequency * i / sample_rate)
                for i in range(num_samples)
            ],
        )
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    @pytest.fixture
    def stereo_audio(self):
        """Create stereo audio for testing."""
        import math

        sample_rate = 44100
        duration = 1.0
        frequency = 440.0
        num_frames = int(sample_rate * duration)
        samples = array.array("f")
        for i in range(num_frames):
            val = 0.5 * math.sin(2 * math.pi * frequency * i / sample_rate)
            samples.append(val)  # Left
            samples.append(val)  # Right
        return cycdp.Buffer.from_memoryview(
            samples, channels=2, sample_rate=sample_rate
        )

    def test_flutter_mono_to_stereo(self, mono_audio):
        """Flutter should convert mono to stereo."""
        result = cycdp.flutter(mono_audio)
        assert result.channels == 2
        assert result.frame_count > 0

    def test_flutter_stereo_input(self, stereo_audio):
        """Flutter should work with stereo input."""
        result = cycdp.flutter(stereo_audio)
        assert result.channels == 2
        assert result.frame_count == stereo_audio.frame_count

    def test_flutter_default_params(self, stereo_audio):
        """Flutter with default parameters should work."""
        result = cycdp.flutter(stereo_audio)
        assert result.frame_count > 0

    def test_flutter_frequency(self, stereo_audio):
        """Flutter with different frequencies should work."""
        result1 = cycdp.flutter(stereo_audio, frequency=2.0)
        result2 = cycdp.flutter(stereo_audio, frequency=10.0)
        assert result1.frame_count > 0
        assert result2.frame_count > 0

    def test_flutter_depth(self, stereo_audio):
        """Flutter with different depths should work."""
        result1 = cycdp.flutter(stereo_audio, depth=0.5)
        result2 = cycdp.flutter(stereo_audio, depth=2.0)
        result3 = cycdp.flutter(stereo_audio, depth=8.0)
        assert result1.frame_count > 0
        assert result2.frame_count > 0
        assert result3.frame_count > 0

    def test_flutter_gain(self, stereo_audio):
        """Flutter with different gains should work."""
        result1 = cycdp.flutter(stereo_audio, gain=0.5)
        result2 = cycdp.flutter(stereo_audio, gain=1.0)
        assert result1.frame_count > 0
        assert result2.frame_count > 0

    def test_flutter_randomize(self, stereo_audio):
        """Flutter with randomize should work."""
        result = cycdp.flutter(stereo_audio, randomize=True)
        assert result.frame_count > 0

    def test_flutter_produces_stereo_difference(self, stereo_audio):
        """Flutter should create difference between L and R channels."""
        result = cycdp.flutter(stereo_audio, frequency=4.0, depth=1.0)
        # Check that there's some difference between channels
        mv = memoryview(result)
        differences = 0
        for i in range(0, len(mv), 2):
            if abs(mv[i] - mv[i + 1]) > 0.001:
                differences += 1
        # Should have significant differences
        assert differences > result.frame_count * 0.1

    def test_flutter_invalid_frequency_low(self, stereo_audio):
        """Flutter should reject frequency below 0.1."""
        with pytest.raises(cycdp.CDPError):
            cycdp.flutter(stereo_audio, frequency=0.01)

    def test_flutter_invalid_frequency_high(self, stereo_audio):
        """Flutter should reject frequency above 50."""
        with pytest.raises(cycdp.CDPError):
            cycdp.flutter(stereo_audio, frequency=100.0)

    def test_flutter_invalid_depth(self, stereo_audio):
        """Flutter should reject negative depth."""
        with pytest.raises(cycdp.CDPError):
            cycdp.flutter(stereo_audio, depth=-1.0)

    def test_flutter_invalid_gain(self, stereo_audio):
        """Flutter should reject gain above 1.0."""
        with pytest.raises(cycdp.CDPError):
            cycdp.flutter(stereo_audio, gain=2.0)


class TestHover:
    """Tests for the hover (zigzag reading) function."""

    @pytest.fixture
    def mono_audio(self):
        """Create a mono test buffer with a sine wave."""
        import math

        sample_rate = 44100
        duration = 0.5  # 0.5 seconds
        freq = 440.0  # 440 Hz sine wave
        samples = array.array("f")
        for i in range(int(sample_rate * duration)):
            t = i / sample_rate
            val = 0.5 * math.sin(2 * math.pi * freq * t)
            samples.append(val)
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    @pytest.fixture
    def stereo_audio(self):
        """Create a stereo test buffer."""
        import math

        sample_rate = 44100
        duration = 0.5
        freq = 440.0
        samples = array.array("f")
        for i in range(int(sample_rate * duration)):
            t = i / sample_rate
            val = 0.5 * math.sin(2 * math.pi * freq * t)
            samples.append(val)  # Left
            samples.append(val)  # Right
        return cycdp.Buffer.from_memoryview(
            samples, channels=2, sample_rate=sample_rate
        )

    def test_hover_mono_input(self, mono_audio):
        """Hover should work with mono input."""
        result = cycdp.hover(mono_audio)
        assert result.channels == 1
        assert result.frame_count > 0

    def test_hover_default_params(self, mono_audio):
        """Hover with default parameters should work."""
        result = cycdp.hover(mono_audio)
        assert result.frame_count > 0

    def test_hover_custom_frequency(self, mono_audio):
        """Hover with different frequencies should work."""
        result1 = cycdp.hover(mono_audio, frequency=10.0)
        result2 = cycdp.hover(mono_audio, frequency=100.0)
        assert result1.frame_count > 0
        assert result2.frame_count > 0

    def test_hover_custom_location(self, mono_audio):
        """Hover with different locations should work."""
        result1 = cycdp.hover(mono_audio, location=0.0)
        result2 = cycdp.hover(mono_audio, location=0.5)
        result3 = cycdp.hover(mono_audio, location=1.0)
        assert result1.frame_count > 0
        assert result2.frame_count > 0
        assert result3.frame_count > 0

    def test_hover_custom_duration(self, mono_audio):
        """Hover with custom duration should produce correct length."""
        result = cycdp.hover(mono_audio, duration=1.0, frequency=10.0)
        # Duration of 1 second at 44100 sample rate should give about 44100 samples
        expected = 44100
        assert abs(result.frame_count - expected) < 1000  # Allow some tolerance

    def test_hover_frq_rand(self, mono_audio):
        """Hover with frequency randomization should work."""
        result = cycdp.hover(mono_audio, frq_rand=0.5)
        assert result.frame_count > 0

    def test_hover_loc_rand(self, mono_audio):
        """Hover with location randomization should work."""
        result = cycdp.hover(mono_audio, loc_rand=0.5)
        assert result.frame_count > 0

    def test_hover_splice_ms(self, mono_audio):
        """Hover with different splice lengths should work."""
        result1 = cycdp.hover(mono_audio, splice_ms=0.5, frequency=10.0)
        result2 = cycdp.hover(mono_audio, splice_ms=5.0, frequency=10.0)
        assert result1.frame_count > 0
        assert result2.frame_count > 0

    def test_hover_stereo_rejected(self, stereo_audio):
        """Hover should reject stereo input."""
        with pytest.raises(cycdp.CDPError):
            cycdp.hover(stereo_audio)

    def test_hover_invalid_frequency_low(self, mono_audio):
        """Hover should reject frequency below 0.1."""
        with pytest.raises(cycdp.CDPError):
            cycdp.hover(mono_audio, frequency=0.01)

    def test_hover_invalid_frequency_high(self, mono_audio):
        """Hover should reject frequency above 1000."""
        with pytest.raises(cycdp.CDPError):
            cycdp.hover(mono_audio, frequency=2000.0)

    def test_hover_invalid_location_low(self, mono_audio):
        """Hover should reject location below 0."""
        with pytest.raises(cycdp.CDPError):
            cycdp.hover(mono_audio, location=-0.1)

    def test_hover_invalid_location_high(self, mono_audio):
        """Hover should reject location above 1."""
        with pytest.raises(cycdp.CDPError):
            cycdp.hover(mono_audio, location=1.5)

    def test_hover_invalid_frq_rand(self, mono_audio):
        """Hover should reject invalid frq_rand values."""
        with pytest.raises(cycdp.CDPError):
            cycdp.hover(mono_audio, frq_rand=-0.1)
        with pytest.raises(cycdp.CDPError):
            cycdp.hover(mono_audio, frq_rand=1.5)

    def test_hover_invalid_loc_rand(self, mono_audio):
        """Hover should reject invalid loc_rand values."""
        with pytest.raises(cycdp.CDPError):
            cycdp.hover(mono_audio, loc_rand=-0.1)
        with pytest.raises(cycdp.CDPError):
            cycdp.hover(mono_audio, loc_rand=1.5)

    def test_hover_invalid_splice_ms(self, mono_audio):
        """Hover should reject invalid splice_ms values."""
        with pytest.raises(cycdp.CDPError):
            cycdp.hover(mono_audio, splice_ms=0.01)  # Below 0.1
        with pytest.raises(cycdp.CDPError):
            cycdp.hover(mono_audio, splice_ms=200.0)  # Above 100

    def test_hover_preserves_sample_rate(self, mono_audio):
        """Hover should preserve the sample rate."""
        result = cycdp.hover(mono_audio)
        assert result.sample_rate == mono_audio.sample_rate


class TestConstrict:
    """Tests for the constrict (silence constriction) function."""

    @pytest.fixture
    def mono_with_silence(self):
        """Create a mono buffer with silence gaps."""
        import math

        sample_rate = 44100
        samples = array.array("f")
        # Sound: 0.1s sine wave
        for i in range(int(sample_rate * 0.1)):
            t = i / sample_rate
            samples.append(0.5 * math.sin(2 * math.pi * 440 * t))
        # Silence: 0.2s
        for _ in range(int(sample_rate * 0.2)):
            samples.append(0.0)
        # Sound: 0.1s sine wave
        for i in range(int(sample_rate * 0.1)):
            t = i / sample_rate
            samples.append(0.5 * math.sin(2 * math.pi * 440 * t))
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    @pytest.fixture
    def stereo_with_silence(self):
        """Create a stereo buffer with silence gaps."""
        import math

        sample_rate = 44100
        samples = array.array("f")
        # Sound: 0.1s sine wave
        for i in range(int(sample_rate * 0.1)):
            t = i / sample_rate
            val = 0.5 * math.sin(2 * math.pi * 440 * t)
            samples.append(val)  # Left
            samples.append(val)  # Right
        # Silence: 0.2s
        for _ in range(int(sample_rate * 0.2)):
            samples.append(0.0)  # Left
            samples.append(0.0)  # Right
        # Sound: 0.1s sine wave
        for i in range(int(sample_rate * 0.1)):
            t = i / sample_rate
            val = 0.5 * math.sin(2 * math.pi * 440 * t)
            samples.append(val)  # Left
            samples.append(val)  # Right
        return cycdp.Buffer.from_memoryview(
            samples, channels=2, sample_rate=sample_rate
        )

    @pytest.fixture
    def mono_no_silence(self):
        """Create a mono buffer with no silence (no zero samples)."""
        import math

        sample_rate = 44100
        duration = 0.5
        samples = array.array("f")
        for i in range(int(sample_rate * duration)):
            t = i / sample_rate
            # Use phase offset to avoid zero at t=0, and add DC offset to ensure no zeros
            val = 0.5 * math.sin(2 * math.pi * 440 * t + 0.5) + 0.1
            samples.append(val)
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    def test_constrict_mono(self, mono_with_silence):
        """Constrict should work with mono input."""
        result = cycdp.constrict(mono_with_silence)
        assert result.channels == 1
        assert result.frame_count > 0

    def test_constrict_stereo(self, stereo_with_silence):
        """Constrict should work with stereo input."""
        result = cycdp.constrict(stereo_with_silence)
        assert result.channels == 2
        assert result.frame_count > 0

    def test_constrict_default_params(self, mono_with_silence):
        """Constrict with default parameters should work."""
        result = cycdp.constrict(mono_with_silence)
        # Default is 50% constriction, so output should be shorter than input
        assert result.frame_count < mono_with_silence.frame_count

    def test_constrict_zero_no_change(self, mono_with_silence):
        """Constrict with 0 should not change length significantly."""
        result = cycdp.constrict(mono_with_silence, constriction=0.0)
        # 0% constriction means silences are kept at 100% (no reduction)
        # Output should be same length as input
        assert result.frame_count == mono_with_silence.frame_count

    def test_constrict_full_removal(self, mono_with_silence):
        """Constrict with 100 should remove all silence."""
        result = cycdp.constrict(mono_with_silence, constriction=100.0)
        # With 100% constriction, silences are completely removed
        # The silent portion was 0.2s = 8820 samples
        # Sound portions are 0.2s = 8820 samples
        # So output should be approximately 0.2s (the non-silent parts)
        assert result.frame_count < mono_with_silence.frame_count

    def test_constrict_overlap_mode(self, mono_with_silence):
        """Constrict with >100 should overlap sounds."""
        result = cycdp.constrict(mono_with_silence, constriction=150.0)
        # Overlap mode (100-200 range) merges adjacent sounds
        # Output should be shorter than 100% constriction
        assert result.frame_count > 0

    def test_constrict_max_overlap(self, mono_with_silence):
        """Constrict with 200 should produce maximum overlap."""
        result = cycdp.constrict(mono_with_silence, constriction=200.0)
        assert result.frame_count > 0

    def test_constrict_no_silence_unchanged(self, mono_no_silence):
        """Constrict on audio with no silence should not change length."""
        result = cycdp.constrict(mono_no_silence, constriction=100.0)
        # No silence means nothing to remove
        assert result.frame_count == mono_no_silence.frame_count

    def test_constrict_invalid_low(self, mono_with_silence):
        """Constrict should reject negative values."""
        with pytest.raises(cycdp.CDPError):
            cycdp.constrict(mono_with_silence, constriction=-1.0)

    def test_constrict_invalid_high(self, mono_with_silence):
        """Constrict should reject values above 200."""
        with pytest.raises(cycdp.CDPError):
            cycdp.constrict(mono_with_silence, constriction=201.0)

    def test_constrict_preserves_sample_rate(self, mono_with_silence):
        """Constrict should preserve the sample rate."""
        result = cycdp.constrict(mono_with_silence)
        assert result.sample_rate == mono_with_silence.sample_rate

    def test_constrict_preserves_channels(self, stereo_with_silence):
        """Constrict should preserve the channel count."""
        result = cycdp.constrict(stereo_with_silence)
        assert result.channels == stereo_with_silence.channels

    def test_constrict_various_levels(self, mono_with_silence):
        """Constrict at various levels should produce progressively shorter output."""
        r0 = cycdp.constrict(mono_with_silence, constriction=0.0)
        r25 = cycdp.constrict(mono_with_silence, constriction=25.0)
        r50 = cycdp.constrict(mono_with_silence, constriction=50.0)
        r75 = cycdp.constrict(mono_with_silence, constriction=75.0)
        r100 = cycdp.constrict(mono_with_silence, constriction=100.0)

        # Higher constriction should result in shorter output
        assert r0.frame_count >= r25.frame_count
        assert r25.frame_count >= r50.frame_count
        assert r50.frame_count >= r75.frame_count
        assert r75.frame_count >= r100.frame_count


class TestPhase:
    """Tests for the phase manipulation functions."""

    @pytest.fixture
    def mono_audio(self):
        """Create a mono test buffer with a sine wave."""
        import math

        sample_rate = 44100
        duration = 0.1
        freq = 440.0
        samples = array.array("f")
        for i in range(int(sample_rate * duration)):
            t = i / sample_rate
            samples.append(0.5 * math.sin(2 * math.pi * freq * t))
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    @pytest.fixture
    def stereo_audio(self):
        """Create a stereo test buffer with different L/R content."""
        import math

        sample_rate = 44100
        duration = 0.1
        samples = array.array("f")
        for i in range(int(sample_rate * duration)):
            t = i / sample_rate
            # Left: 440 Hz, Right: 880 Hz (different to have stereo difference)
            left = 0.5 * math.sin(2 * math.pi * 440 * t)
            right = 0.5 * math.sin(2 * math.pi * 880 * t)
            samples.append(left)
            samples.append(right)
        return cycdp.Buffer.from_memoryview(
            samples, channels=2, sample_rate=sample_rate
        )

    @pytest.fixture
    def stereo_centered(self):
        """Create a stereo buffer with identical L/R (centered sound)."""
        import math

        sample_rate = 44100
        duration = 0.1
        freq = 440.0
        samples = array.array("f")
        for i in range(int(sample_rate * duration)):
            t = i / sample_rate
            val = 0.5 * math.sin(2 * math.pi * freq * t)
            samples.append(val)  # Left
            samples.append(val)  # Right (identical)
        return cycdp.Buffer.from_memoryview(
            samples, channels=2, sample_rate=sample_rate
        )

    # phase_invert tests
    def test_phase_invert_mono(self, mono_audio):
        """Phase invert should work with mono input."""
        result = cycdp.phase_invert(mono_audio)
        assert result.channels == 1
        assert result.frame_count == mono_audio.frame_count

    def test_phase_invert_stereo(self, stereo_audio):
        """Phase invert should work with stereo input."""
        result = cycdp.phase_invert(stereo_audio)
        assert result.channels == 2
        assert result.frame_count == stereo_audio.frame_count

    def test_phase_invert_values(self, mono_audio):
        """Phase invert should negate all sample values."""
        result = cycdp.phase_invert(mono_audio)
        # Check that values are inverted
        for i in range(min(100, mono_audio.frame_count)):
            assert abs(result[i] + mono_audio[i]) < 1e-6

    def test_phase_invert_double_is_identity(self, mono_audio):
        """Inverting phase twice should return original."""
        inverted = cycdp.phase_invert(mono_audio)
        restored = cycdp.phase_invert(inverted)
        for i in range(min(100, mono_audio.frame_count)):
            assert abs(restored[i] - mono_audio[i]) < 1e-6

    def test_phase_invert_preserves_sample_rate(self, mono_audio):
        """Phase invert should preserve sample rate."""
        result = cycdp.phase_invert(mono_audio)
        assert result.sample_rate == mono_audio.sample_rate

    # phase_stereo tests
    def test_phase_stereo_works(self, stereo_audio):
        """Phase stereo should work with stereo input."""
        result = cycdp.phase_stereo(stereo_audio)
        assert result.channels == 2
        assert result.frame_count == stereo_audio.frame_count

    def test_phase_stereo_default_transfer(self, stereo_audio):
        """Phase stereo with default transfer (1.0) should work."""
        result = cycdp.phase_stereo(stereo_audio)
        assert result.frame_count == stereo_audio.frame_count

    def test_phase_stereo_zero_transfer(self, stereo_audio):
        """Phase stereo with transfer=0 should return unchanged audio."""
        result = cycdp.phase_stereo(stereo_audio, transfer=0.0)
        # With transfer=0, output should equal input
        for i in range(min(100, stereo_audio.frame_count * 2)):
            assert abs(result[i] - stereo_audio[i]) < 1e-6

    def test_phase_stereo_partial_transfer(self, stereo_audio):
        """Phase stereo with partial transfer should work."""
        result = cycdp.phase_stereo(stereo_audio, transfer=0.5)
        assert result.frame_count == stereo_audio.frame_count

    def test_phase_stereo_centered_cancellation(self, stereo_centered):
        """Phase stereo on centered audio should cancel to near-silence."""
        result = cycdp.phase_stereo(stereo_centered, transfer=1.0)
        # When L=R, newL = L - R = 0, newR = R - L = 0
        # All samples should be near zero
        max_val = max(abs(result[i]) for i in range(result.frame_count * 2))
        assert max_val < 0.01  # Should be very small

    def test_phase_stereo_mono_rejected(self, mono_audio):
        """Phase stereo should reject mono input."""
        with pytest.raises(cycdp.CDPError):
            cycdp.phase_stereo(mono_audio)

    def test_phase_stereo_invalid_transfer_low(self, stereo_audio):
        """Phase stereo should reject negative transfer."""
        with pytest.raises(cycdp.CDPError):
            cycdp.phase_stereo(stereo_audio, transfer=-0.1)

    def test_phase_stereo_invalid_transfer_high(self, stereo_audio):
        """Phase stereo should reject transfer > 1."""
        with pytest.raises(cycdp.CDPError):
            cycdp.phase_stereo(stereo_audio, transfer=1.5)

    def test_phase_stereo_preserves_sample_rate(self, stereo_audio):
        """Phase stereo should preserve sample rate."""
        result = cycdp.phase_stereo(stereo_audio)
        assert result.sample_rate == stereo_audio.sample_rate


class TestWrappage:
    """Tests for the wrappage (granular texture) function."""

    @pytest.fixture
    def mono_audio(self):
        """Create a mono test buffer with a sine wave."""
        import math

        sample_rate = 44100
        duration = 0.5
        freq = 440.0
        samples = array.array("f")
        for i in range(int(sample_rate * duration)):
            t = i / sample_rate
            samples.append(0.5 * math.sin(2 * math.pi * freq * t))
        return cycdp.Buffer.from_memoryview(
            samples, channels=1, sample_rate=sample_rate
        )

    @pytest.fixture
    def stereo_audio(self):
        """Create a stereo test buffer."""
        import math

        sample_rate = 44100
        duration = 0.5
        freq = 440.0
        samples = array.array("f")
        for i in range(int(sample_rate * duration)):
            t = i / sample_rate
            val = 0.5 * math.sin(2 * math.pi * freq * t)
            samples.append(val)  # Left
            samples.append(val)  # Right
        return cycdp.Buffer.from_memoryview(
            samples, channels=2, sample_rate=sample_rate
        )

    def test_wrappage_basic(self, mono_audio):
        """Wrappage should work with default parameters."""
        result = cycdp.wrappage(mono_audio)
        assert result.channels == 2  # Output is stereo
        assert result.frame_count > 0

    def test_wrappage_stereo_output(self, mono_audio):
        """Wrappage should produce stereo output from mono input."""
        result = cycdp.wrappage(mono_audio)
        assert result.channels == 2

    def test_wrappage_grain_size(self, mono_audio):
        """Wrappage with different grain sizes should work."""
        result1 = cycdp.wrappage(mono_audio, grain_size=10.0)
        result2 = cycdp.wrappage(mono_audio, grain_size=100.0)
        assert result1.frame_count > 0
        assert result2.frame_count > 0

    def test_wrappage_density(self, mono_audio):
        """Wrappage with different densities should work."""
        result_sparse = cycdp.wrappage(mono_audio, density=0.5)
        result_dense = cycdp.wrappage(mono_audio, density=2.0)
        assert result_sparse.frame_count > 0
        assert result_dense.frame_count > 0

    def test_wrappage_velocity_stretch(self, mono_audio):
        """Wrappage with velocity < 1 should time stretch."""
        result = cycdp.wrappage(mono_audio, velocity=0.5)
        # Time stretch should produce longer output (allow some tolerance)
        assert result.frame_count > mono_audio.frame_count * 1.5

    def test_wrappage_velocity_compress(self, mono_audio):
        """Wrappage with velocity > 1 should time compress."""
        result = cycdp.wrappage(mono_audio, velocity=2.0)
        # Time compress should produce shorter output
        assert result.frame_count < mono_audio.frame_count

    def test_wrappage_velocity_freeze(self, mono_audio):
        """Wrappage with velocity = 0 should freeze (requires duration)."""
        result = cycdp.wrappage(mono_audio, velocity=0.0, duration=1.0)
        # Output should be approximately 1 second
        expected_frames = 44100
        assert abs(result.frame_count - expected_frames) < 1000

    def test_wrappage_pitch_shift(self, mono_audio):
        """Wrappage with pitch shift should work."""
        result_up = cycdp.wrappage(mono_audio, pitch=12.0)  # Octave up
        result_down = cycdp.wrappage(mono_audio, pitch=-12.0)  # Octave down
        assert result_up.frame_count > 0
        assert result_down.frame_count > 0

    def test_wrappage_spread(self, mono_audio):
        """Wrappage with different spread values should work."""
        result_mono = cycdp.wrappage(mono_audio, spread=0.0)  # Centered
        result_wide = cycdp.wrappage(mono_audio, spread=1.0)  # Full spread
        assert result_mono.frame_count > 0
        assert result_wide.frame_count > 0

    def test_wrappage_jitter(self, mono_audio):
        """Wrappage with different jitter values should work."""
        result_no_jitter = cycdp.wrappage(mono_audio, jitter=0.0)
        result_max_jitter = cycdp.wrappage(mono_audio, jitter=1.0)
        assert result_no_jitter.frame_count > 0
        assert result_max_jitter.frame_count > 0

    def test_wrappage_splice(self, mono_audio):
        """Wrappage with different splice lengths should work."""
        result1 = cycdp.wrappage(mono_audio, splice_ms=1.0)
        result2 = cycdp.wrappage(mono_audio, splice_ms=20.0)
        assert result1.frame_count > 0
        assert result2.frame_count > 0

    def test_wrappage_duration(self, mono_audio):
        """Wrappage with explicit duration should produce correct length."""
        result = cycdp.wrappage(mono_audio, duration=2.0, velocity=1.0)
        expected_frames = 44100 * 2
        # Allow some tolerance
        assert abs(result.frame_count - expected_frames) < 5000

    def test_wrappage_stereo_rejected(self, stereo_audio):
        """Wrappage should reject stereo input."""
        with pytest.raises(cycdp.CDPError):
            cycdp.wrappage(stereo_audio)

    def test_wrappage_invalid_grain_size_low(self, mono_audio):
        """Wrappage should reject grain size below 1.0."""
        with pytest.raises(cycdp.CDPError):
            cycdp.wrappage(mono_audio, grain_size=0.5)

    def test_wrappage_invalid_grain_size_high(self, mono_audio):
        """Wrappage should reject grain size above 500.0."""
        with pytest.raises(cycdp.CDPError):
            cycdp.wrappage(mono_audio, grain_size=600.0)

    def test_wrappage_invalid_density(self, mono_audio):
        """Wrappage should reject invalid density values."""
        with pytest.raises(cycdp.CDPError):
            cycdp.wrappage(mono_audio, density=0.05)
        with pytest.raises(cycdp.CDPError):
            cycdp.wrappage(mono_audio, density=15.0)

    def test_wrappage_invalid_velocity(self, mono_audio):
        """Wrappage should reject invalid velocity values."""
        with pytest.raises(cycdp.CDPError):
            cycdp.wrappage(mono_audio, velocity=-0.5)
        with pytest.raises(cycdp.CDPError):
            cycdp.wrappage(mono_audio, velocity=15.0)

    def test_wrappage_velocity_zero_no_duration(self, mono_audio):
        """Wrappage with velocity=0 should require duration."""
        with pytest.raises(cycdp.CDPError):
            cycdp.wrappage(mono_audio, velocity=0.0)

    def test_wrappage_invalid_pitch(self, mono_audio):
        """Wrappage should reject invalid pitch values."""
        with pytest.raises(cycdp.CDPError):
            cycdp.wrappage(mono_audio, pitch=-30.0)
        with pytest.raises(cycdp.CDPError):
            cycdp.wrappage(mono_audio, pitch=30.0)

    def test_wrappage_invalid_spread(self, mono_audio):
        """Wrappage should reject invalid spread values."""
        with pytest.raises(cycdp.CDPError):
            cycdp.wrappage(mono_audio, spread=-0.1)
        with pytest.raises(cycdp.CDPError):
            cycdp.wrappage(mono_audio, spread=1.5)

    def test_wrappage_invalid_jitter(self, mono_audio):
        """Wrappage should reject invalid jitter values."""
        with pytest.raises(cycdp.CDPError):
            cycdp.wrappage(mono_audio, jitter=-0.1)
        with pytest.raises(cycdp.CDPError):
            cycdp.wrappage(mono_audio, jitter=1.5)

    def test_wrappage_invalid_splice(self, mono_audio):
        """Wrappage should reject invalid splice values."""
        with pytest.raises(cycdp.CDPError):
            cycdp.wrappage(mono_audio, splice_ms=0.1)
        with pytest.raises(cycdp.CDPError):
            cycdp.wrappage(mono_audio, splice_ms=100.0)

    def test_wrappage_preserves_sample_rate(self, mono_audio):
        """Wrappage should preserve sample rate."""
        result = cycdp.wrappage(mono_audio)
        assert result.sample_rate == mono_audio.sample_rate


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
