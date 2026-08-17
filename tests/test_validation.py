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
import contextlib
import ctypes
import math

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

    def test_negative_indexing_counts_from_the_end(self):
        """buf[-1] is the last sample, as it is for every Python sequence.

        The index was typed size_t, so a negative value raised OverflowError
        from the argument conversion rather than wrapping, and the only way to
        reach the tail was buf[len(buf) - 1].
        """
        buf = cycdp.Buffer.create(100, 1, 44100)
        buf[99] = 0.5
        buf[0] = 0.25
        assert buf[-1] == pytest.approx(0.5)
        assert buf[-100] == pytest.approx(0.25)

        buf[-1] = 0.75
        assert buf[99] == pytest.approx(0.75)

    @pytest.mark.parametrize("index", [-101, -(10**9)])
    def test_negative_index_past_the_start_raises(self, index):
        """Wrapping must be bounded at both ends, not only the top."""
        buf = cycdp.Buffer.create(100, 1, 44100)
        with pytest.raises(IndexError, match="out of range"):
            buf[index]
        with pytest.raises(IndexError, match="out of range"):
            buf[index] = 1.0


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


class TestNonFiniteParametersRejected:
    """NaN and Inf must never reach the C layer.

    They pass every comparison-based clamp in the C code -- `if (x < 0) x = 0;`
    is false for NaN -- and `(size_t)nan` is undefined behaviour. Before these
    guards existed, fuzzing produced four segfaults and thirteen hangs from
    ordinary calls. Each case below crashed or hung the interpreter.
    """

    @pytest.fixture
    def buf(self):
        return cycdp.Buffer.create(8192, 1, 44100)

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
    @pytest.mark.parametrize(
        "call",
        [
            pytest.param(
                lambda b, v: cycdp.grain_cloud(b, grainsize_ms=v),
                id="grain_cloud.grainsize_ms",
            ),
            pytest.param(
                lambda b, v: cycdp.wrappage(b, grain_size=v), id="wrappage.grain_size"
            ),
            pytest.param(
                lambda b, v: cycdp.stutter(b, segment_ms=v), id="stutter.segment_ms"
            ),
            pytest.param(lambda b, v: cycdp.drunk(b, step_ms=v), id="drunk.step_ms"),
            pytest.param(
                lambda b, v: cycdp.crystal(b, pitch_scatter=v),
                id="crystal.pitch_scatter",
            ),
            pytest.param(
                lambda b, v: cycdp.time_stretch(b, v), id="time_stretch.factor"
            ),
            pytest.param(
                lambda b, v: cycdp.psow_grab(b, duration=v), id="psow_grab.duration"
            ),
            pytest.param(
                lambda b, v: cycdp.grain_extend(b, start_time=v),
                id="grain_extend.start_time",
            ),
        ],
    )
    def test_rejected_with_a_named_parameter(self, buf, call, bad):
        with pytest.raises(ValueError) as exc:
            call(buf, bad)
        # The message must name the offending parameter; a bare "invalid
        # argument" would leave a CLI user with nothing to act on.
        assert "must be" in str(exc.value)

    def test_every_public_float_parameter_is_guarded(self):
        """The generated guards must cover the whole API, not a sample.

        scripts/check_validation.py is the same check CI runs; calling it here
        means a new operation added without a guard fails the suite too, not
        only the lint job.
        """
        import subprocess
        import sys
        from pathlib import Path

        repo = Path(__file__).resolve().parent.parent
        r = subprocess.run(
            [sys.executable, str(repo / "scripts" / "check_validation.py")],
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0, r.stderr


class TestUnboundedParametersRejected:
    """Finite but absurd magnitudes must fail fast, not exhaust the machine.

    Each of these previously either crashed (drunk, crystal) or ran without
    terminating: the value scales an allocation or an iteration count, and
    nothing bounded it.
    """

    @pytest.fixture
    def buf(self):
        return cycdp.Buffer.create(8192, 1, 44100)

    @pytest.mark.parametrize(
        "call",
        [
            pytest.param(lambda b: cycdp.drunk(b, step_ms=1e9), id="drunk.step_ms"),
            pytest.param(
                lambda b: cycdp.crystal(b, pitch_scatter=1e9),
                id="crystal.pitch_scatter",
            ),
            pytest.param(
                lambda b: cycdp.time_stretch(b, 1e9), id="time_stretch.factor"
            ),
            pytest.param(
                lambda b: cycdp.texture_simple(b, density=1e9),
                id="texture_simple.density",
            ),
            pytest.param(
                lambda b: cycdp.texture_multi(b, density=1e9),
                id="texture_multi.density",
            ),
            pytest.param(
                lambda b: cycdp.cantor(b, smooth_ms=1e9), id="cantor.smooth_ms"
            ),
            pytest.param(lambda b: cycdp.loop(b, splice_ms=1e9), id="loop.splice_ms"),
            pytest.param(
                lambda b: cycdp.morph_glide(b, b, duration=1e9),
                id="morph_glide.duration",
            ),
            pytest.param(
                lambda b: cycdp.morph_glide_native(b, b, duration=1e9),
                id="morph_glide_native.duration",
            ),
            pytest.param(
                lambda b: cycdp.formants(b, lpc_order=2**31 - 1),
                id="formants.lpc_order",
            ),
            pytest.param(
                lambda b: cycdp.cascade(b, pitch_decay=1e-30), id="cascade.pitch_decay"
            ),
            pytest.param(
                lambda b: cycdp.grain_cloud(b, grainsize_ms=1e-30),
                id="grain_cloud.grainsize_ms",
            ),
            pytest.param(
                lambda b: cycdp.iterate(b, delay_rand=1e9), id="iterate.delay_rand"
            ),
        ],
    )
    def test_rejected(self, buf, call):
        with pytest.raises(ValueError):
            call(buf)

    def test_grain_extend_terminates_on_a_degenerate_segment(self):
        """A zero-length segment must not spin in the overlap-add loop.

        The advance was `grain_len - splice_len`, which is exactly zero at the
        default 15 ms grain size, so the writer never moved.
        """
        buf = cycdp.Buffer.create(8192, 1, 44100)
        result = cycdp.grain_extend(buf, end_time=1e-30)
        assert result.frame_count > 0

    def test_generous_values_are_still_accepted(self):
        """The bounds must not be so tight they reject real use."""
        buf = cycdp.Buffer.create(44100, 1, 44100)
        assert cycdp.time_stretch(buf, 8.0).frame_count > buf.frame_count
        assert cycdp.stutter(buf, segment_ms=10.0, duration=2.0).frame_count > 0
        assert cycdp.texture_simple(buf, duration=2.0, density=200.0).frame_count > 0


def _wav(channels=1, sample_rate=44100, bits=16, fmt=1, data_size=None, data=b""):
    """Build a WAV file with arbitrary header fields, valid or not."""
    import struct

    if data_size is None:
        data_size = len(data)
    block_align = max(channels * bits // 8, 1)
    fmt_chunk = struct.pack(
        "<HHIIHH",
        fmt,
        channels,
        sample_rate,
        (sample_rate * block_align) & 0xFFFFFFFF,
        block_align & 0xFFFF,
        bits,
    )
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt_chunk)) + fmt_chunk
    body += b"data" + struct.pack("<I", data_size) + data
    return b"RIFF" + struct.pack("<I", len(body)) + body


class TestMalformedWavHeaders:
    """cdp_read_file parses untrusted input; header fields are not assumptions.

    Zero channels made the frame size zero and the frame-count division an
    integer division by zero -- SIGFPE on x86-64, which kills the process. An
    unbounded declared data size turned four bytes into an arbitrary
    allocation. An absurd sample rate survived into every downstream operation,
    where delay lines and output lengths scale with it.
    """

    def _read(self, tmp_path, payload, name="crafted.wav"):
        p = tmp_path / name
        p.write_bytes(payload)
        return cycdp.read_file(str(p))

    def test_zero_channels_rejected(self, tmp_path):
        with pytest.raises(cycdp.CDPError, match="channel count"):
            self._read(tmp_path, _wav(channels=0, data=b"\x00" * 16))

    def test_absurd_channel_count_rejected(self, tmp_path):
        with pytest.raises(cycdp.CDPError, match="channel count"):
            self._read(tmp_path, _wav(channels=0xFFFF, data=b"\x00" * 16))

    @pytest.mark.parametrize("rate", [0, 0xFFFFFFFF, 2_000_000_000])
    def test_out_of_range_sample_rate_rejected(self, tmp_path, rate):
        with pytest.raises(cycdp.CDPError, match="sample rate"):
            self._read(tmp_path, _wav(sample_rate=rate, data=b"\x00" * 16))

    def test_data_size_larger_than_the_file_rejected(self, tmp_path):
        """The check must happen before the allocation, not after the read."""
        with pytest.raises(cycdp.CDPError, match=r"only .* remain"):
            self._read(tmp_path, _wav(data_size=0xFFFFFFF0, data=b"\x00" * 16))

    def test_a_well_formed_file_still_reads(self, tmp_path):
        """The rejections above must not be over-broad."""
        src = cycdp.synth_wave(waveform=cycdp.WAVE_SINE, frequency=440.0, duration=0.05)
        path = tmp_path / "good.wav"
        cycdp.write_file(str(path), src)
        back = cycdp.read_file(str(path))
        assert back.frame_count == src.frame_count
        assert back.sample_rate == src.sample_rate

    @pytest.mark.parametrize("channels", [1, 2, 6, 64])
    def test_channel_counts_up_to_the_limit_are_accepted(self, tmp_path, channels):
        payload = _wav(channels=channels, data=b"\x00" * (2 * channels * 4))
        assert self._read(tmp_path, payload).channels == channels


class TestWritingNonFiniteSamples:
    """Converting NaN to an integer is undefined behaviour.

    The PCM writers clamped with `if (v > 1.0f)` / `if (v < -1.0f)`, and both
    comparisons are false for NaN, so it reached the cast. Parameter guards now
    keep NaN out of buffers produced by cycdp, but a caller can still hand one
    in through `Buffer.from_memoryview`, and file writing must not be undefined
    behaviour for any input.
    """

    @pytest.fixture
    def buf(self):
        samples = array.array(
            "f", [0.5, float("nan"), float("inf"), float("-inf"), -0.5]
        )
        return cycdp.Buffer.from_memoryview(samples, 1, 44100)

    @pytest.mark.parametrize("fmt", ["pcm16", "pcm24"])
    def test_pcm_output_is_finite_and_in_range(self, tmp_path, buf, fmt):
        path = tmp_path / f"{fmt}.wav"
        cycdp.write_file(str(path), buf, format=fmt)

        values = cycdp.read_file(str(path)).to_list()
        assert all(math.isfinite(v) for v in values), values
        assert all(-1.0 <= v <= 1.0 for v in values), values
        # NaN has no sample value it means; silence is the substitute.
        assert values[1] == pytest.approx(0.0, abs=1e-4)
        # The infinities clamp to full scale rather than wrapping.
        assert values[2] == pytest.approx(1.0, abs=1e-4)
        assert values[3] == pytest.approx(-1.0, abs=1e-4)
        # Ordinary samples are untouched.
        assert values[0] == pytest.approx(0.5, abs=1e-4)

    def test_float_output_preserves_them(self, tmp_path, buf):
        """float32 can represent NaN, so substituting would be lossy."""
        path = tmp_path / "float.wav"
        cycdp.write_file(str(path), buf, format="float")
        values = cycdp.read_file(str(path)).to_list()
        assert math.isnan(values[1])
        assert values[2] == math.inf


class TestExtremeSampleRates:
    """Operations whose internals scale with the sample rate must survive both ends.

    Not reachable through read_file any more (the reader bounds the rate), but
    Buffer.create takes it directly, and an embedder using the C API has no
    Python layer at all.
    """

    @pytest.mark.parametrize("rate", [1, 100, 8000, 44100, 192000])
    def test_reverb_delay_lines_survive(self, rate):
        """Freeverb's tuning constants are scaled by rate/44100.

        Below roughly 800 Hz every constant rounds down to zero frames.
        calloc(0, n) hands back a non-NULL zero-byte allocation, and
        comb_process then reads buffer[0] from it -- a heap overflow that only
        ASan sees. Confirmed: reverting the one-frame floor makes the ASan job
        report `heap-buffer-overflow ... comb_process cdp_reverb.c`.
        """
        buf = cycdp.Buffer.create(2048, 1, rate)
        assert cycdp.reverb(buf, decay_time=0.2).frame_count > 0


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


class TestBufferProtocolHonoursFlags:
    """An exporter must fill in what the consumer asked for, and nothing else.

    `Buffer.__getbuffer__` ignored its `flags` argument and always advertised
    format, shape and strides. Most consumers ask for everything, so this was
    invisible in practice -- but a consumer requesting PyBUF_SIMPLE got fields
    it had declared no interest in, and a non-NULL `format` contradicts the
    itemsize such a consumer is entitled to assume, since a NULL format means
    unsigned bytes.

    These go through PyObject_GetBuffer directly, because there is no way to
    request a specific flag combination from pure Python.
    """

    # From CPython's object.h.
    PyBUF_SIMPLE = 0
    PyBUF_WRITABLE = 0x0001
    PyBUF_FORMAT = 0x0004
    PyBUF_ND = 0x0008
    PyBUF_STRIDES = 0x0010 | PyBUF_ND
    PyBUF_C_CONTIGUOUS = 0x0020 | PyBUF_STRIDES
    PyBUF_F_CONTIGUOUS = 0x0040 | PyBUF_STRIDES
    PyBUF_ANY_CONTIGUOUS = 0x0080 | PyBUF_STRIDES
    PyBUF_FULL_RO = (0x0100 | PyBUF_STRIDES) | PyBUF_FORMAT

    class _PyBuffer(ctypes.Structure):
        _fields_ = [
            ("buf", ctypes.c_void_p),
            ("obj", ctypes.c_void_p),
            ("len", ctypes.c_ssize_t),
            ("itemsize", ctypes.c_ssize_t),
            ("readonly", ctypes.c_int),
            ("ndim", ctypes.c_int),
            ("format", ctypes.c_char_p),
            ("shape", ctypes.POINTER(ctypes.c_ssize_t)),
            ("strides", ctypes.POINTER(ctypes.c_ssize_t)),
            ("suboffsets", ctypes.POINTER(ctypes.c_ssize_t)),
            ("internal", ctypes.c_void_p),
        ]

    @contextlib.contextmanager
    def _view(self, buf, flags):
        view = self._PyBuffer()
        rc = ctypes.pythonapi.PyObject_GetBuffer(
            ctypes.py_object(buf), ctypes.byref(view), ctypes.c_int(flags)
        )
        if rc != 0:
            raise BufferError(f"PyObject_GetBuffer failed for flags {flags:#x}")
        try:
            yield view
        finally:
            ctypes.pythonapi.PyBuffer_Release(ctypes.byref(view))

    @pytest.fixture
    def buf(self):
        b = cycdp.Buffer.create(100, 1, 44100)
        b[0] = 0.5
        return b

    def test_simple_request_gets_no_format_shape_or_strides(self, buf):
        with self._view(buf, self.PyBUF_SIMPLE) as v:
            assert v.format is None
            assert not v.shape
            assert not v.strides
            # The data itself must still be there and correctly sized.
            assert v.len == 100 * 4
            assert v.buf

    def test_nd_request_gets_shape_but_not_strides(self, buf):
        with self._view(buf, self.PyBUF_ND) as v:
            assert not v.strides
            assert v.shape
            assert v.shape[0] == 100

    def test_strides_request_gets_both(self, buf):
        with self._view(buf, self.PyBUF_STRIDES) as v:
            assert v.shape[0] == 100
            assert v.strides[0] == 4

    def test_format_is_only_supplied_when_requested(self, buf):
        with self._view(buf, self.PyBUF_ND) as v:
            assert v.format is None
        with self._view(buf, self.PyBUF_ND | self.PyBUF_FORMAT) as v:
            assert v.format == b"f"

    @pytest.mark.parametrize(
        "name",
        ["PyBUF_C_CONTIGUOUS", "PyBUF_F_CONTIGUOUS", "PyBUF_ANY_CONTIGUOUS"],
    )
    def test_every_contiguity_request_succeeds(self, buf, name):
        """A 1-D contiguous buffer satisfies all three."""
        with self._view(buf, getattr(self, name)) as v:
            assert v.len == 100 * 4

    def test_writable_request_succeeds(self, buf):
        """The memory is ours and is writable, so this never needs refusing."""
        with self._view(buf, self.PyBUF_WRITABLE) as v:
            assert v.readonly == 0

    def test_invariant_fields_are_always_set(self, buf):
        for flags in (
            self.PyBUF_SIMPLE,
            self.PyBUF_ND,
            self.PyBUF_STRIDES,
            self.PyBUF_FULL_RO,
        ):
            with self._view(buf, flags) as v:
                assert v.itemsize == 4
                assert v.len == 100 * 4
                assert v.ndim == 1
                assert not v.suboffsets, "the buffer is contiguous, not indirect"
                # len must stay consistent with shape when shape is given.
                if v.shape:
                    assert v.len == v.shape[0] * v.itemsize

    def test_memoryview_still_works(self, buf):
        """memoryview() asks for PyBUF_FULL_RO; the common path must not regress."""
        mv = memoryview(buf)
        assert mv.format == "f"
        assert mv.itemsize == 4
        assert mv.shape == (100,)
        assert mv.strides == (4,)
        assert mv.ndim == 1
        assert not mv.readonly
        assert mv[0] == pytest.approx(0.5)
