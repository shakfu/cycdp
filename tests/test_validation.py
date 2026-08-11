"""Pin the error behaviour of invalid input.

M4: coverage showed 71 of the 293 unexercised lines in _core.pyx were
validation raises -- ValueError, IndexError, MemoryError -- that no test ever
reached. Error paths are exactly where untested code rots: a refactor can drop
a guard and every existing test still passes, because none of them supply bad
input.

These assert the exception type and that the message says something useful, so
a guard cannot be silently removed or degraded into a generic failure.
"""

from __future__ import annotations

import array

import pytest

import cycdp


class TestBufferBounds:
    def test_index_beyond_end_raises(self):
        buf = cycdp.Buffer.create(100, 1, 44100)
        with pytest.raises(IndexError, match="out of range"):
            buf[10**9]

    def test_assignment_beyond_end_raises(self):
        buf = cycdp.Buffer.create(100, 1, 44100)
        with pytest.raises(IndexError, match="out of range"):
            buf[10**9] = 1.0

    def test_last_valid_index_is_accessible(self):
        """The bound must be off-by-one correct, not merely present."""
        buf = cycdp.Buffer.create(100, 1, 44100)
        buf[99] = 0.5
        assert buf[99] == pytest.approx(0.5)
        with pytest.raises(IndexError):
            buf[100]

    def test_negative_indexing_is_not_supported(self):
        """Documents a real wart: buf[-1] does not mean "last sample".

        The index is typed size_t, so a negative value overflows rather than
        wrapping the way every other Python sequence does. Pinned here so the
        behaviour is at least known; changing it would be an API decision.
        """
        buf = cycdp.Buffer.create(100, 1, 44100)
        with pytest.raises(OverflowError):
            buf[-1]


class TestUninitialisedBuffer:
    """A bare Buffer() has no allocation and must refuse work, not segfault."""

    @pytest.mark.parametrize(
        "operation",
        [
            pytest.param(lambda b: b.to_list(), id="to_list"),
            pytest.param(lambda b: b.to_bytes(), id="to_bytes"),
            pytest.param(lambda b: b[0], id="getitem"),
            pytest.param(lambda b: b.__setitem__(0, 1.0), id="setitem"),
            pytest.param(lambda b: memoryview(b), id="memoryview"),
        ],
    )
    def test_rejects_operations(self, operation):
        with pytest.raises(ValueError, match="not initialized"):
            operation(cycdp.Buffer())

    def test_properties_report_zero_rather_than_crashing(self):
        buf = cycdp.Buffer()
        assert buf.sample_count == 0
        assert buf.frame_count == 0
        assert len(buf) == 0


class TestNumericValidation:
    @pytest.mark.parametrize("factor", [0.0, -1.0, -0.5])
    def test_time_stretch_rejects_non_positive_factors(self, factor):
        buf = cycdp.Buffer.create(4096, 1, 44100)
        with pytest.raises(ValueError, match="positive"):
            cycdp.time_stretch(buf, factor)

    @pytest.mark.parametrize("factor", [0.0, -2.0])
    def test_modify_speed_rejects_non_positive_factors(self, factor):
        buf = cycdp.Buffer.create(4096, 1, 44100)
        with pytest.raises(ValueError, match="positive"):
            cycdp.modify_speed(buf, factor)

    def test_normalize_rejects_silence(self):
        """Normalising silence has no defined target gain."""
        with pytest.raises(cycdp.CDPError, match="silent"):
            cycdp.normalize(array.array("f", [0.0] * 64))


class TestCollectionValidation:
    @pytest.mark.parametrize("func", [cycdp.mix, cycdp.concat, cycdp.interleave])
    def test_empty_buffer_list_rejected(self, func):
        with pytest.raises(ValueError, match="empty"):
            func([])

    def test_mix_rejects_mismatched_gain_count(self):
        a = cycdp.Buffer.create(128, 1, 44100)
        b = cycdp.Buffer.create(128, 1, 44100)
        with pytest.raises(ValueError):
            cycdp.mix([a, b], gains=[1.0])


class TestStringParameterValidation:
    def test_write_file_rejects_unknown_format(self, tmp_path):
        buf = cycdp.Buffer.create(128, 1, 44100)
        with pytest.raises(ValueError, match="Invalid format"):
            cycdp.write_file(str(tmp_path / "x.wav"), buf, format="bogus")

    def test_fade_rejects_unknown_curve(self):
        buf = cycdp.Buffer.create(4096, 1, 44100)
        with pytest.raises(ValueError, match="Invalid curve"):
            cycdp.fade_in(buf, 0.01, curve="bogus")

    def test_valid_formats_are_accepted(self, tmp_path):
        """The rejection above must not be over-broad."""
        buf = cycdp.Buffer.create(128, 1, 44100)
        for fmt in ("float", "pcm16", "pcm24"):
            cycdp.write_file(str(tmp_path / f"{fmt}.wav"), buf, format=fmt)


class TestFileErrors:
    def test_reading_a_missing_file_raises_cdperror(self):
        with pytest.raises(cycdp.CDPError) as exc:
            cycdp.read_file("/nonexistent/definitely_not_here.wav")
        assert "Cannot open" in str(exc.value)

    def test_reading_a_non_wav_raises_cdperror(self, tmp_path):
        junk = tmp_path / "junk.wav"
        junk.write_bytes(b"this is not a RIFF header at all")
        with pytest.raises(cycdp.CDPError, match="WAV"):
            cycdp.read_file(str(junk))

    def test_cdperror_exposes_its_code(self):
        with pytest.raises(cycdp.CDPError) as exc:
            cycdp.read_file("/nonexistent/definitely_not_here.wav")
        assert isinstance(exc.value.code, int)
        assert exc.value.code != 0


class TestChannelConstraints:
    """Operations with channel requirements must say so, not misbehave."""

    def test_pan_requires_mono(self):
        stereo = cycdp.to_stereo(cycdp.Buffer.create(1024, 1, 44100))
        with pytest.raises((ValueError, cycdp.CDPError)):
            cycdp.pan(stereo, 0.0)

    def test_mirror_requires_stereo(self):
        mono = cycdp.Buffer.create(1024, 1, 44100)
        with pytest.raises((ValueError, cycdp.CDPError)):
            cycdp.mirror(mono)
