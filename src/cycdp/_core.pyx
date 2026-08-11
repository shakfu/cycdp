# cython: language_level=3
# cython: boundscheck=False
# cython: wraparound=False
"""
cycdp Cython extension module - CDP audio processing bindings.

Uses memoryviews and buffer protocol for zero-copy interop with
numpy, array.array, and other buffer-compatible objects.
"""

from libc.stddef cimport size_t
from libc.stdlib cimport malloc, free
from libc.string cimport memcpy
from cpython.buffer cimport PyBUF_FORMAT, PyBUF_WRITABLE
from cpython.mem cimport PyMem_Malloc, PyMem_Free

# =============================================================================
# C declarations from cdp.h
# =============================================================================
cdef extern from "cdp.h":
    # Error codes
    ctypedef enum cdp_error:
        CDP_OK
        CDP_CONTINUE
        CDP_ERROR_GOAL_FAILED
        CDP_ERROR_INVALID_ARG
        CDP_ERROR_DATA
        CDP_ERROR_MEMORY
        CDP_ERROR_IO
        CDP_ERROR_FORMAT
        CDP_ERROR_STATE
        CDP_ERROR_NOT_FOUND
        CDP_ERROR_INTERNAL

    # Flags
    ctypedef enum cdp_flags:
        CDP_FLAG_NONE
        CDP_FLAG_CLIP
        CDP_FLAG_NORMALIZE
        CDP_FLAG_PRESERVE_PEAK

    # Audio info
    ctypedef struct cdp_audio_info:
        int sample_rate
        int channels
        size_t frame_count
        size_t sample_count

    # Buffer
    ctypedef struct cdp_buffer:
        float* samples
        size_t sample_count
        size_t capacity
        cdp_audio_info info
        int owns_memory

    # Breakpoint
    ctypedef struct cdp_breakpoint:
        double time
        double value

    # Peak info
    ctypedef struct cdp_peak:
        float level
        size_t position

    # Context (opaque)
    ctypedef struct cdp_context:
        pass

    # Version
    const char* cdp_version()

    # Context management
    cdp_context* cdp_context_create()
    void cdp_context_destroy(cdp_context* ctx)
    cdp_error cdp_get_error(const cdp_context* ctx)
    const char* cdp_get_error_message(const cdp_context* ctx)
    void cdp_clear_error(cdp_context* ctx)

    # Buffer management
    cdp_buffer* cdp_buffer_create(size_t frame_count, int channels, int sample_rate)
    cdp_buffer* cdp_buffer_wrap(float* samples, size_t sample_count,
                                int channels, int sample_rate)
    cdp_buffer* cdp_buffer_copy(const float* samples, size_t sample_count,
                                int channels, int sample_rate)
    void cdp_buffer_destroy(cdp_buffer* buf)
    cdp_error cdp_buffer_resize(cdp_buffer* buf, size_t new_frame_count)
    void cdp_buffer_clear(cdp_buffer* buf)

    # Gain operations
    cdp_error cdp_gain(cdp_context* ctx, cdp_buffer* buf, double gain, cdp_flags flags)
    cdp_error cdp_gain_db(cdp_context* ctx, cdp_buffer* buf, double gain_db, cdp_flags flags)
    cdp_error cdp_gain_envelope(cdp_context* ctx, cdp_buffer* buf,
                                const cdp_breakpoint* points, size_t point_count,
                                cdp_flags flags)
    double cdp_find_peak(cdp_context* ctx, const cdp_buffer* buf, cdp_peak* peak)
    cdp_error cdp_normalize(cdp_context* ctx, cdp_buffer* buf, double target_level)
    cdp_error cdp_normalize_db(cdp_context* ctx, cdp_buffer* buf, double target_db)
    cdp_error cdp_phase_invert(cdp_context* ctx, cdp_buffer* buf)

    # File I/O
    cdp_buffer* cdp_read_file(cdp_context* ctx, const char* path)
    cdp_error cdp_write_file(cdp_context* ctx, const char* path, const cdp_buffer* buf)
    cdp_error cdp_write_file_pcm16(cdp_context* ctx, const char* path, const cdp_buffer* buf)
    cdp_error cdp_write_file_pcm24(cdp_context* ctx, const char* path, const cdp_buffer* buf)

    # Spatial/panning operations
    cdp_buffer* cdp_pan(cdp_context* ctx, const cdp_buffer* buf, double position)
    cdp_buffer* cdp_pan_envelope(cdp_context* ctx, const cdp_buffer* buf,
                                  const cdp_breakpoint* points, size_t point_count)
    cdp_buffer* cdp_mirror(cdp_context* ctx, const cdp_buffer* buf)
    cdp_buffer* cdp_narrow(cdp_context* ctx, const cdp_buffer* buf, double width)

    # Mixing operations
    cdp_buffer* cdp_mix2(cdp_context* ctx, const cdp_buffer* a, const cdp_buffer* b,
                         double gain_a, double gain_b)
    cdp_buffer* cdp_mix(cdp_context* ctx, cdp_buffer** buffers, const double* gains,
                        int count)

    # Channel operations
    cdp_buffer* cdp_to_mono(cdp_context* ctx, const cdp_buffer* buf)
    cdp_buffer* cdp_to_stereo(cdp_context* ctx, const cdp_buffer* buf)
    cdp_buffer* cdp_extract_channel(cdp_context* ctx, const cdp_buffer* buf, int channel)
    cdp_buffer* cdp_merge_channels(cdp_context* ctx, const cdp_buffer* left,
                                    const cdp_buffer* right)
    cdp_buffer** cdp_split_channels(cdp_context* ctx, const cdp_buffer* buf,
                                     int* out_num_channels)
    cdp_buffer* cdp_interleave(cdp_context* ctx, cdp_buffer** buffers, int num_channels)

    # Buffer utilities
    cdp_buffer* cdp_reverse(cdp_context* ctx, const cdp_buffer* buf)
    cdp_error cdp_fade_in(cdp_context* ctx, cdp_buffer* buf, double duration, int fade_type)
    cdp_error cdp_fade_out(cdp_context* ctx, cdp_buffer* buf, double duration, int fade_type)
    cdp_buffer* cdp_concat(cdp_context* ctx, cdp_buffer** buffers, int count)

    # Conversion utilities
    double cdp_gain_to_db(double gain)
    double cdp_db_to_gain(double db)

    # Error string
    const char* cdp_error_string(cdp_error err)


# =============================================================================
# Python exports
# =============================================================================

# Export constants
FLAG_NONE = CDP_FLAG_NONE
FLAG_CLIP = CDP_FLAG_CLIP


class CDPError(Exception):
    """Exception raised for CDP library errors."""
    def __init__(self, code, message):
        self.code = code
        super().__init__(f"CDP error {code}: {message}")


cpdef str version():
    """Get CDP library version string."""
    return cdp_version().decode('utf-8')


cpdef double gain_to_db(double gain):
    """Convert linear gain to decibels."""
    return cdp_gain_to_db(gain)


cpdef double db_to_gain(double db):
    """Convert decibels to linear gain."""
    return cdp_db_to_gain(db)


# =============================================================================
# Context class
# =============================================================================
cdef class Context:
    """CDP processing context."""
    cdef cdp_context* _ctx

    def __cinit__(self):
        self._ctx = cdp_context_create()
        if self._ctx is NULL:
            raise MemoryError("Failed to create CDP context")

    def __dealloc__(self):
        if self._ctx is not NULL:
            cdp_context_destroy(self._ctx)
            self._ctx = NULL

    cpdef int get_error(self):
        """Get last error code."""
        return cdp_get_error(self._ctx)

    cpdef str get_error_message(self):
        """Get last error message."""
        return cdp_get_error_message(self._ctx).decode('utf-8')

    cpdef void clear_error(self):
        """Clear error state."""
        cdp_clear_error(self._ctx)

    cdef void _check_error(self, cdp_error err) except *:
        """Raise exception if error occurred."""
        if err < 0:
            msg = cdp_get_error_message(self._ctx).decode('utf-8')
            raise CDPError(err, msg)

    cdef cdp_context* ptr(self):
        """Get raw pointer (for internal use)."""
        return self._ctx


# =============================================================================
# Buffer class with buffer protocol support
# =============================================================================
cdef class Buffer:
    """CDP audio buffer with Python buffer protocol support.

    Can be created from any object supporting the buffer protocol
    (numpy arrays, array.array, memoryview, bytes, etc.)
    """
    cdef cdp_buffer* _buf
    cdef bint _owns_buffer
    cdef Py_ssize_t _shape[1]
    cdef Py_ssize_t _strides[1]

    def __cinit__(self):
        self._buf = NULL
        self._owns_buffer = False

    def __dealloc__(self):
        if self._owns_buffer and self._buf is not NULL:
            cdp_buffer_destroy(self._buf)
            self._buf = NULL

    @staticmethod
    def create(size_t frame_count, int channels=1, int sample_rate=44100):
        """Create a new buffer with allocated memory."""
        cdef Buffer buf = Buffer()
        buf._buf = cdp_buffer_create(frame_count, channels, sample_rate)
        if buf._buf is NULL:
            raise MemoryError("Failed to create buffer")
        buf._owns_buffer = True
        return buf

    @staticmethod
    def from_memoryview(float[::1] samples not None,
                        int channels=1, int sample_rate=44100):
        """Create a buffer by copying from a memoryview."""
        cdef Buffer buf = Buffer()
        cdef size_t sample_count = samples.shape[0]

        buf._buf = cdp_buffer_copy(&samples[0], sample_count,
                                   channels, sample_rate)
        if buf._buf is NULL:
            raise MemoryError("Failed to copy buffer")

        buf._owns_buffer = True
        return buf

    def to_list(self):
        """Convert buffer to Python list."""
        if self._buf is NULL:
            raise ValueError("Buffer is not initialized")

        cdef list result = []
        cdef size_t i
        for i in range(self._buf.sample_count):
            result.append(self._buf.samples[i])
        return result

    def to_bytes(self):
        """Convert buffer to bytes (raw float32 data)."""
        if self._buf is NULL:
            raise ValueError("Buffer is not initialized")

        cdef size_t nbytes = self._buf.sample_count * sizeof(float)
        return (<char*>self._buf.samples)[:nbytes]

    def __getbuffer__(self, Py_buffer *buffer, int flags):
        """Support buffer protocol for zero-copy access."""
        if self._buf is NULL:
            raise ValueError("Buffer is not initialized")

        self._shape[0] = <Py_ssize_t>self._buf.sample_count
        self._strides[0] = <Py_ssize_t>sizeof(float)

        buffer.buf = <void*>self._buf.samples
        buffer.format = 'f'  # float32
        buffer.internal = NULL
        buffer.itemsize = sizeof(float)
        buffer.len = self._buf.sample_count * sizeof(float)
        buffer.ndim = 1
        buffer.obj = self
        buffer.readonly = 0  # Always writable since we own the memory
        buffer.shape = self._shape
        buffer.strides = self._strides
        buffer.suboffsets = NULL

    def __releasebuffer__(self, Py_buffer *buffer):
        pass

    @property
    def sample_count(self):
        if self._buf is NULL:
            return 0
        return self._buf.sample_count

    @property
    def frame_count(self):
        if self._buf is NULL:
            return 0
        return self._buf.info.frame_count

    @property
    def channels(self):
        if self._buf is NULL:
            return 0
        return self._buf.info.channels

    @property
    def sample_rate(self):
        if self._buf is NULL:
            return 0
        return self._buf.info.sample_rate

    def __len__(self):
        if self._buf is NULL:
            return 0
        return self._buf.sample_count

    def __getitem__(self, size_t index):
        if self._buf is NULL:
            raise ValueError("Buffer is not initialized")
        if index >= self._buf.sample_count:
            raise IndexError("Index out of range")
        return self._buf.samples[index]

    def __setitem__(self, size_t index, float value):
        if self._buf is NULL:
            raise ValueError("Buffer is not initialized")
        if index >= self._buf.sample_count:
            raise IndexError("Index out of range")
        self._buf.samples[index] = value

    def clear(self):
        """Set all samples to zero."""
        if self._buf is not NULL:
            cdp_buffer_clear(self._buf)

    cdef cdp_buffer* ptr(self):
        """Get raw pointer (for internal use)."""
        return self._buf


# =============================================================================
# Processing functions (low-level, work with Buffer objects)
# =============================================================================

def apply_gain(Context ctx, Buffer buf, double gain, bint clip=False):
    """Apply gain to buffer (in-place)."""
    cdef cdp_flags flags = CDP_FLAG_CLIP if clip else CDP_FLAG_NONE
    cdef cdp_error err = cdp_gain(ctx.ptr(), buf.ptr(), gain, flags)
    ctx._check_error(err)


def apply_gain_db(Context ctx, Buffer buf, double gain_db, bint clip=False):
    """Apply gain in dB to buffer (in-place)."""
    cdef cdp_flags flags = CDP_FLAG_CLIP if clip else CDP_FLAG_NONE
    cdef cdp_error err = cdp_gain_db(ctx.ptr(), buf.ptr(), gain_db, flags)
    ctx._check_error(err)


def apply_normalize(Context ctx, Buffer buf, double target_level=1.0):
    """Normalize buffer to target level (in-place)."""
    cdef cdp_error err = cdp_normalize(ctx.ptr(), buf.ptr(), target_level)
    ctx._check_error(err)


def apply_normalize_db(Context ctx, Buffer buf, double target_db=0.0):
    """Normalize buffer to target dB level (in-place)."""
    cdef cdp_error err = cdp_normalize_db(ctx.ptr(), buf.ptr(), target_db)
    ctx._check_error(err)


def apply_phase_invert(Context ctx, Buffer buf):
    """Invert phase of buffer (in-place)."""
    cdef cdp_error err = cdp_phase_invert(ctx.ptr(), buf.ptr())
    ctx._check_error(err)


def get_peak(Context ctx, Buffer buf):
    """Find peak level in buffer. Returns (level, frame_position)."""
    cdef cdp_peak peak
    cdef double level = cdp_find_peak(ctx.ptr(), buf.ptr(), &peak)
    if level < 0:
        ctx._check_error(<cdp_error>-1)
    return (peak.level, peak.position)


# =============================================================================
# High-level functions using memoryviews (work with any buffer protocol object)
# =============================================================================

def gain(float[::1] samples not None, double gain_factor=1.0,
         int sample_rate=44100, bint clip=False):
    """Apply gain to audio samples.

    Args:
        samples: Input samples (any float32 buffer: numpy array, array.array('f'), etc.)
        gain_factor: Linear gain (1.0 = unity, 2.0 = +6dB).
        sample_rate: Sample rate in Hz.
        clip: If True, clip output to [-1.0, 1.0].

    Returns:
        Buffer with processed samples (supports buffer protocol).
    """
    cdef Context ctx = Context()
    cdef Buffer buf = Buffer.from_memoryview(samples, 1, sample_rate)

    apply_gain(ctx, buf, gain_factor, clip)
    return buf


def gain_db(float[::1] samples not None, double db=0.0,
            int sample_rate=44100, bint clip=False):
    """Apply gain in decibels to audio samples.

    Args:
        samples: Input samples (any float32 buffer).
        db: Gain in dB (0 = unity, +6 = double, -6 = half).
        sample_rate: Sample rate in Hz.
        clip: If True, clip output to [-1.0, 1.0].

    Returns:
        Buffer with processed samples.
    """
    cdef Context ctx = Context()
    cdef Buffer buf = Buffer.from_memoryview(samples, 1, sample_rate)

    apply_gain_db(ctx, buf, db, clip)
    return buf


def normalize(float[::1] samples not None, double target=1.0,
              int sample_rate=44100):
    """Normalize audio to target peak level.

    Args:
        samples: Input samples (any float32 buffer).
        target: Target peak level (0.0 to 1.0).
        sample_rate: Sample rate in Hz.

    Returns:
        Buffer with normalized samples.

    Raises:
        CDPError: If audio is silent.
    """
    cdef Context ctx = Context()
    cdef Buffer buf = Buffer.from_memoryview(samples, 1, sample_rate)

    apply_normalize(ctx, buf, target)
    return buf


def normalize_db(float[::1] samples not None, double target_db=0.0,
                 int sample_rate=44100):
    """Normalize audio to target peak level in dB.

    Args:
        samples: Input samples (any float32 buffer).
        target_db: Target peak in dB (0 = 0dBFS, -3 = -3dBFS).
        sample_rate: Sample rate in Hz.

    Returns:
        Buffer with normalized samples.
    """
    cdef Context ctx = Context()
    cdef Buffer buf = Buffer.from_memoryview(samples, 1, sample_rate)

    apply_normalize_db(ctx, buf, target_db)
    return buf


# NOTE: phase_invert accepts both a raw float32 buffer and a Buffer, so it is
# defined once in the phase-manipulation section below (it needs the cdp_lib
# declarations that appear later in this file). It used to be defined twice --
# here and again below -- which silently made this definition unreachable.


def peak(float[::1] samples not None, int sample_rate=44100):
    """Find peak level in audio samples.

    Args:
        samples: Input samples (any float32 buffer).
        sample_rate: Sample rate in Hz.

    Returns:
        Tuple of (peak_level, sample_position).
    """
    cdef Context ctx = Context()
    cdef Buffer buf = Buffer.from_memoryview(samples, 1, sample_rate)

    return get_peak(ctx, buf)


# =============================================================================
# File I/O functions
# =============================================================================

def read_file(str path not None):
    """Read an audio file into a Buffer.

    Currently supports WAV files (16/24/32-bit PCM and 32-bit float).

    Args:
        path: Path to audio file.

    Returns:
        Buffer containing audio data.

    Raises:
        CDPError: If file cannot be read.
    """
    cdef Context ctx = Context()
    cdef bytes path_bytes = path.encode('utf-8')
    cdef cdp_buffer* c_buf = cdp_read_file(ctx.ptr(), path_bytes)

    if c_buf is NULL:
        msg = ctx.get_error_message()
        raise CDPError(ctx.get_error(), msg)

    # Wrap the C buffer in a Python Buffer object
    cdef Buffer buf = Buffer()
    buf._buf = c_buf
    buf._owns_buffer = True
    return buf


def write_file(str path not None, Buffer buf not None, str format="float"):
    """Write a Buffer to an audio file.

    Args:
        path: Path to output file.
        buf: Buffer to write.
        format: Output format - "float" (32-bit float), "pcm16", or "pcm24".

    Raises:
        CDPError: If file cannot be written.
        ValueError: If format is invalid.
    """
    cdef Context ctx = Context()
    cdef bytes path_bytes = path.encode('utf-8')
    cdef cdp_error err

    if format == "float":
        err = cdp_write_file(ctx.ptr(), path_bytes, buf.ptr())
    elif format == "pcm16":
        err = cdp_write_file_pcm16(ctx.ptr(), path_bytes, buf.ptr())
    elif format == "pcm24":
        err = cdp_write_file_pcm24(ctx.ptr(), path_bytes, buf.ptr())
    else:
        raise ValueError(f"Invalid format: {format}. Use 'float', 'pcm16', or 'pcm24'.")

    ctx._check_error(err)


# =============================================================================
# Spatial/panning operations
# =============================================================================

def pan(Buffer buf not None, double position=0.0):
    """Pan a mono buffer to stereo with a static pan position.

    Uses CDP's geometric panning model for natural sound positioning.

    Args:
        buf: Input buffer (must be mono).
        position: Pan position: -1.0 = left, 0.0 = center, +1.0 = right.
                  Values beyond -1/+1 simulate sound beyond speakers.

    Returns:
        New stereo Buffer.

    Raises:
        CDPError: If input is not mono.
    """
    cdef Context ctx = Context()
    cdef cdp_buffer* c_result = cdp_pan(ctx.ptr(), buf.ptr(), position)

    if c_result is NULL:
        msg = ctx.get_error_message()
        raise CDPError(ctx.get_error(), msg)

    cdef Buffer result = Buffer()
    result._buf = c_result
    result._owns_buffer = True
    return result


def pan_envelope(Buffer buf not None, list points not None):
    """Pan a mono buffer to stereo with time-varying position.

    Uses CDP's geometric panning model with breakpoint-based automation.

    Args:
        buf: Input buffer (must be mono).
        points: List of (time, position) tuples defining the pan envelope.
                Times are in seconds, positions are -1.0 (left) to +1.0 (right).
                Example: [(0.0, -1.0), (1.0, 0.0), (2.0, 1.0)] pans left to right.

    Returns:
        New stereo Buffer.

    Raises:
        CDPError: If input is not mono.
        ValueError: If points list is empty.
    """
    if len(points) == 0:
        raise ValueError("Points list cannot be empty")

    cdef Context ctx = Context()
    cdef size_t point_count = len(points)
    cdef cdp_breakpoint* c_points = <cdp_breakpoint*>malloc(point_count * sizeof(cdp_breakpoint))

    if c_points is NULL:
        raise MemoryError("Failed to allocate breakpoints")

    cdef size_t i
    for i in range(point_count):
        c_points[i].time = points[i][0]
        c_points[i].value = points[i][1]

    cdef cdp_buffer* c_result = cdp_pan_envelope(ctx.ptr(), buf.ptr(), c_points, point_count)
    free(c_points)

    if c_result is NULL:
        msg = ctx.get_error_message()
        raise CDPError(ctx.get_error(), msg)

    cdef Buffer result = Buffer()
    result._buf = c_result
    result._owns_buffer = True
    return result


def mirror(Buffer buf not None):
    """Mirror (swap) left and right channels of a stereo buffer.

    Args:
        buf: Input buffer (must be stereo).

    Returns:
        New Buffer with swapped channels.

    Raises:
        CDPError: If input is not stereo.
    """
    cdef Context ctx = Context()
    cdef cdp_buffer* c_result = cdp_mirror(ctx.ptr(), buf.ptr())

    if c_result is NULL:
        msg = ctx.get_error_message()
        raise CDPError(ctx.get_error(), msg)

    cdef Buffer result = Buffer()
    result._buf = c_result
    result._owns_buffer = True
    return result


def narrow(Buffer buf not None, double width=1.0):
    """Narrow or widen stereo image.

    Args:
        buf: Input buffer (must be stereo).
        width: Stereo width: 0.0 = mono, 1.0 = unchanged, >1.0 = wider.

    Returns:
        New Buffer with adjusted stereo width.

    Raises:
        CDPError: If input is not stereo or width is negative.
    """
    cdef Context ctx = Context()
    cdef cdp_buffer* c_result = cdp_narrow(ctx.ptr(), buf.ptr(), width)

    if c_result is NULL:
        msg = ctx.get_error_message()
        raise CDPError(ctx.get_error(), msg)

    cdef Buffer result = Buffer()
    result._buf = c_result
    result._owns_buffer = True
    return result


# =============================================================================
# Mixing operations
# =============================================================================

def mix2(Buffer a not None, Buffer b not None, double gain_a=1.0, double gain_b=1.0):
    """Mix two buffers together with optional gains.

    Args:
        a: First buffer.
        b: Second buffer (must have same channels and sample rate).
        gain_a: Gain for first buffer (default 1.0).
        gain_b: Gain for second buffer (default 1.0).

    Returns:
        New Buffer containing the mix. Length is max of both inputs.

    Raises:
        CDPError: If buffers are incompatible.
    """
    cdef Context ctx = Context()
    cdef cdp_buffer* c_result = cdp_mix2(ctx.ptr(), a.ptr(), b.ptr(), gain_a, gain_b)

    if c_result is NULL:
        msg = ctx.get_error_message()
        raise CDPError(ctx.get_error(), msg)

    cdef Buffer result = Buffer()
    result._buf = c_result
    result._owns_buffer = True
    return result


def mix(list buffers not None, list gains=None):
    """Mix multiple buffers together with optional gains.

    Args:
        buffers: List of Buffers to mix (all must have same channels/rate).
        gains: Optional list of gains (one per buffer). Default is unity gain.

    Returns:
        New Buffer containing the mix. Length is max of all inputs.

    Raises:
        CDPError: If buffers are incompatible.
        ValueError: If buffers list is empty or gains length doesn't match.
    """
    if len(buffers) == 0:
        raise ValueError("Buffer list cannot be empty")

    if gains is not None and len(gains) != len(buffers):
        raise ValueError("Gains list must have same length as buffers list")

    cdef Context ctx = Context()
    cdef int count = len(buffers)
    cdef int i
    cdef Buffer buf
    cdef cdp_buffer** c_buffers = <cdp_buffer**>malloc(count * sizeof(cdp_buffer*))
    cdef double* c_gains = NULL

    if c_buffers is NULL:
        raise MemoryError("Failed to allocate buffer array")

    if gains is not None:
        c_gains = <double*>malloc(count * sizeof(double))
        if c_gains is NULL:
            free(c_buffers)
            raise MemoryError("Failed to allocate gains array")
        for i in range(count):
            c_gains[i] = gains[i]

    for i in range(count):
        buf = buffers[i]
        c_buffers[i] = buf.ptr()

    cdef cdp_buffer* c_result = cdp_mix(ctx.ptr(), c_buffers, c_gains, count)

    free(c_buffers)
    if c_gains is not NULL:
        free(c_gains)

    if c_result is NULL:
        msg = ctx.get_error_message()
        raise CDPError(ctx.get_error(), msg)

    cdef Buffer result = Buffer()
    result._buf = c_result
    result._owns_buffer = True
    return result


# =============================================================================
# Buffer utilities
# =============================================================================

def reverse(Buffer buf not None):
    """Reverse audio buffer.

    Args:
        buf: Input buffer.

    Returns:
        New Buffer with reversed audio.
    """
    cdef Context ctx = Context()
    cdef cdp_buffer* c_result = cdp_reverse(ctx.ptr(), buf.ptr())

    if c_result is NULL:
        msg = ctx.get_error_message()
        raise CDPError(ctx.get_error(), msg)

    cdef Buffer result = Buffer()
    result._buf = c_result
    result._owns_buffer = True
    return result


def fade_in(Buffer buf not None, double duration, str curve="linear"):
    """Apply fade in to buffer (in-place).

    Args:
        buf: Buffer to process (modified in-place).
        duration: Fade duration in seconds.
        curve: "linear" or "exponential" (equal power).

    Returns:
        The same buffer (for chaining).

    Raises:
        ValueError: If curve type is invalid.
    """
    cdef int fade_type
    if curve == "linear":
        fade_type = 0
    elif curve == "exponential":
        fade_type = 1
    else:
        raise ValueError(f"Invalid curve: {curve}. Use 'linear' or 'exponential'.")

    cdef Context ctx = Context()
    cdef cdp_error err = cdp_fade_in(ctx.ptr(), buf.ptr(), duration, fade_type)
    ctx._check_error(err)
    return buf


def fade_out(Buffer buf not None, double duration, str curve="linear"):
    """Apply fade out to buffer (in-place).

    Args:
        buf: Buffer to process (modified in-place).
        duration: Fade duration in seconds.
        curve: "linear" or "exponential" (equal power).

    Returns:
        The same buffer (for chaining).

    Raises:
        ValueError: If curve type is invalid.
    """
    cdef int fade_type
    if curve == "linear":
        fade_type = 0
    elif curve == "exponential":
        fade_type = 1
    else:
        raise ValueError(f"Invalid curve: {curve}. Use 'linear' or 'exponential'.")

    cdef Context ctx = Context()
    cdef cdp_error err = cdp_fade_out(ctx.ptr(), buf.ptr(), duration, fade_type)
    ctx._check_error(err)
    return buf


def concat(list buffers not None):
    """Concatenate multiple buffers into one.

    Args:
        buffers: List of Buffers (all must have same channels/rate).

    Returns:
        New concatenated Buffer.

    Raises:
        CDPError: If buffers are incompatible.
        ValueError: If list is empty.
    """
    if len(buffers) == 0:
        raise ValueError("Buffer list cannot be empty")

    cdef Context ctx = Context()
    cdef int count = len(buffers)
    cdef cdp_buffer** c_buffers = <cdp_buffer**>malloc(count * sizeof(cdp_buffer*))

    if c_buffers is NULL:
        raise MemoryError("Failed to allocate buffer array")

    cdef int i
    cdef Buffer b
    for i in range(count):
        b = buffers[i]
        c_buffers[i] = b.ptr()

    cdef cdp_buffer* c_result = cdp_concat(ctx.ptr(), c_buffers, count)
    free(c_buffers)

    if c_result is NULL:
        msg = ctx.get_error_message()
        raise CDPError(ctx.get_error(), msg)

    cdef Buffer result = Buffer()
    result._buf = c_result
    result._owns_buffer = True
    return result


# =============================================================================
# Channel operations
# =============================================================================

def to_mono(Buffer buf not None):
    """Convert multi-channel buffer to mono by averaging all channels.

    Args:
        buf: Input buffer (any channel count).

    Returns:
        New mono Buffer.

    Raises:
        CDPError: On error.
    """
    cdef Context ctx = Context()
    cdef cdp_buffer* c_result = cdp_to_mono(ctx.ptr(), buf.ptr())

    if c_result is NULL:
        msg = ctx.get_error_message()
        raise CDPError(ctx.get_error(), msg)

    cdef Buffer result = Buffer()
    result._buf = c_result
    result._owns_buffer = True
    return result


def to_stereo(Buffer buf not None):
    """Convert mono buffer to stereo by duplicating the channel.

    Args:
        buf: Input buffer (must be mono).

    Returns:
        New stereo Buffer.

    Raises:
        CDPError: If input is not mono.
    """
    cdef Context ctx = Context()
    cdef cdp_buffer* c_result = cdp_to_stereo(ctx.ptr(), buf.ptr())

    if c_result is NULL:
        msg = ctx.get_error_message()
        raise CDPError(ctx.get_error(), msg)

    cdef Buffer result = Buffer()
    result._buf = c_result
    result._owns_buffer = True
    return result


def extract_channel(Buffer buf not None, int channel):
    """Extract a single channel from a multi-channel buffer.

    Args:
        buf: Input buffer.
        channel: Channel index (0-based, 0=left, 1=right for stereo).

    Returns:
        New mono Buffer containing the extracted channel.

    Raises:
        CDPError: If channel index is out of range.
    """
    cdef Context ctx = Context()
    cdef cdp_buffer* c_result = cdp_extract_channel(ctx.ptr(), buf.ptr(), channel)

    if c_result is NULL:
        msg = ctx.get_error_message()
        raise CDPError(ctx.get_error(), msg)

    cdef Buffer result = Buffer()
    result._buf = c_result
    result._owns_buffer = True
    return result


def merge_channels(Buffer left not None, Buffer right not None):
    """Merge two mono buffers into a stereo buffer.

    Args:
        left: Left channel buffer (must be mono).
        right: Right channel buffer (must be mono, same length and sample rate).

    Returns:
        New stereo Buffer.

    Raises:
        CDPError: If inputs are not compatible mono buffers.
    """
    cdef Context ctx = Context()
    cdef cdp_buffer* c_result = cdp_merge_channels(ctx.ptr(), left.ptr(), right.ptr())

    if c_result is NULL:
        msg = ctx.get_error_message()
        raise CDPError(ctx.get_error(), msg)

    cdef Buffer result = Buffer()
    result._buf = c_result
    result._owns_buffer = True
    return result


def split_channels(Buffer buf not None):
    """Split a multi-channel buffer into separate mono buffers.

    Args:
        buf: Input buffer.

    Returns:
        List of mono Buffers (one per channel).

    Raises:
        CDPError: On error.
    """
    cdef Context ctx = Context()
    cdef int num_channels = 0
    cdef cdp_buffer** c_buffers = cdp_split_channels(ctx.ptr(), buf.ptr(), &num_channels)

    if c_buffers is NULL:
        msg = ctx.get_error_message()
        raise CDPError(ctx.get_error(), msg)

    # Wrap each buffer in a Python Buffer object
    cdef list result = []
    cdef Buffer pybuf
    cdef int i

    for i in range(num_channels):
        pybuf = Buffer()
        pybuf._buf = c_buffers[i]
        pybuf._owns_buffer = True
        result.append(pybuf)

    # Free the array (but not the buffers - they're now owned by Python)
    free(c_buffers)

    return result


def interleave(list buffers not None):
    """Interleave multiple mono buffers into a single multi-channel buffer.

    Args:
        buffers: List of mono Buffers (all must have same length and sample rate).

    Returns:
        New interleaved multi-channel Buffer.

    Raises:
        CDPError: If buffers are not compatible.
        ValueError: If list is empty.
    """
    if len(buffers) == 0:
        raise ValueError("Buffer list cannot be empty")

    cdef Context ctx = Context()
    cdef int num_channels = len(buffers)
    cdef cdp_buffer** c_buffers = <cdp_buffer**>malloc(num_channels * sizeof(cdp_buffer*))

    if c_buffers is NULL:
        raise MemoryError("Failed to allocate buffer array")

    cdef int i
    cdef Buffer b
    for i in range(num_channels):
        b = buffers[i]
        c_buffers[i] = b.ptr()

    cdef cdp_buffer* c_result = cdp_interleave(ctx.ptr(), c_buffers, num_channels)
    free(c_buffers)

    if c_result is NULL:
        msg = ctx.get_error_message()
        raise CDPError(ctx.get_error(), msg)

    cdef Buffer result = Buffer()
    result._buf = c_result
    result._owns_buffer = True
    return result


# =============================================================================
# CDP Library - Native spectral processing (no subprocess)
# =============================================================================

cdef extern from "cdp_lib.h" nogil:
    ctypedef struct cdp_lib_ctx:
        pass

    ctypedef struct cdp_lib_buffer:
        float *data
        size_t length
        int channels
        int sample_rate

    cdp_lib_ctx* cdp_lib_init()
    cdp_lib_ctx* cdp_lib_thread_ctx()
    void cdp_lib_cleanup(cdp_lib_ctx* ctx)
    const char* cdp_lib_get_error(cdp_lib_ctx* ctx)

    cdp_lib_buffer* cdp_lib_buffer_create(size_t length, int channels, int sample_rate)
    cdp_lib_buffer* cdp_lib_buffer_from_data(float *data, size_t length,
                                              int channels, int sample_rate)
    void cdp_lib_buffer_free(cdp_lib_buffer* buf)

    cdp_lib_buffer* cdp_lib_time_stretch(cdp_lib_ctx* ctx,
                                          const cdp_lib_buffer* input,
                                          double factor,
                                          int fft_size,
                                          int overlap)

    cdp_lib_buffer* cdp_lib_spectral_blur(cdp_lib_ctx* ctx,
                                           const cdp_lib_buffer* input,
                                           double blur_time,
                                           int fft_size)

    cdp_lib_buffer* cdp_lib_loudness(cdp_lib_ctx* ctx,
                                      const cdp_lib_buffer* input,
                                      double gain_db)

    cdp_lib_buffer* cdp_lib_speed(cdp_lib_ctx* ctx,
                                   const cdp_lib_buffer* input,
                                   double speed)

    # Spectral operations
    cdp_lib_buffer* cdp_lib_pitch_shift(cdp_lib_ctx* ctx,
                                         const cdp_lib_buffer* input,
                                         double semitones,
                                         int fft_size)

    cdp_lib_buffer* cdp_lib_spectral_shift(cdp_lib_ctx* ctx,
                                            const cdp_lib_buffer* input,
                                            double shift_hz,
                                            int fft_size)

    cdp_lib_buffer* cdp_lib_spectral_stretch(cdp_lib_ctx* ctx,
                                              const cdp_lib_buffer* input,
                                              double max_stretch,
                                              double freq_divide,
                                              double exponent,
                                              int fft_size)

    cdp_lib_buffer* cdp_lib_filter_lowpass(cdp_lib_ctx* ctx,
                                            const cdp_lib_buffer* input,
                                            double cutoff_freq,
                                            double attenuation_db,
                                            int fft_size)

    cdp_lib_buffer* cdp_lib_filter_highpass(cdp_lib_ctx* ctx,
                                             const cdp_lib_buffer* input,
                                             double cutoff_freq,
                                             double attenuation_db,
                                             int fft_size)

    cdp_lib_buffer* cdp_lib_filter_bandpass(cdp_lib_ctx* ctx,
                                             const cdp_lib_buffer* input,
                                             double low_freq,
                                             double high_freq,
                                             double attenuation_db,
                                             int fft_size)

    cdp_lib_buffer* cdp_lib_filter_notch(cdp_lib_ctx* ctx,
                                          const cdp_lib_buffer* input,
                                          double center_freq,
                                          double width_hz,
                                          double attenuation_db,
                                          int fft_size)

    cdp_lib_buffer* cdp_lib_gate(cdp_lib_ctx* ctx,
                                  const cdp_lib_buffer* input,
                                  double threshold_db,
                                  double attack_ms,
                                  double release_ms,
                                  double hold_ms)

    cdp_lib_buffer* cdp_lib_bitcrush(cdp_lib_ctx* ctx,
                                      const cdp_lib_buffer* input,
                                      int bit_depth,
                                      int downsample)

    cdp_lib_buffer* cdp_lib_ring_mod(cdp_lib_ctx* ctx,
                                      const cdp_lib_buffer* input,
                                      double freq,
                                      double mix)

    cdp_lib_buffer* cdp_lib_delay(cdp_lib_ctx* ctx,
                                   const cdp_lib_buffer* input,
                                   double delay_ms,
                                   double feedback,
                                   double mix)

    cdp_lib_buffer* cdp_lib_chorus(cdp_lib_ctx* ctx,
                                    const cdp_lib_buffer* input,
                                    double rate,
                                    double depth_ms,
                                    double mix)

    cdp_lib_buffer* cdp_lib_flanger(cdp_lib_ctx* ctx,
                                     const cdp_lib_buffer* input,
                                     double rate,
                                     double depth_ms,
                                     double feedback,
                                     double mix)

    cdp_lib_buffer* cdp_lib_eq_parametric(cdp_lib_ctx* ctx,
                                           const cdp_lib_buffer* input,
                                           double center_freq,
                                           double gain_db,
                                           double q,
                                           int fft_size)

    cdp_lib_buffer* cdp_lib_envelope_follow(cdp_lib_ctx* ctx,
                                             const cdp_lib_buffer* input,
                                             double attack_ms,
                                             double release_ms,
                                             int mode)

    cdp_lib_buffer* cdp_lib_envelope_apply(cdp_lib_ctx* ctx,
                                            const cdp_lib_buffer* input,
                                            const cdp_lib_buffer* envelope,
                                            double depth)

    cdp_lib_buffer* cdp_lib_compressor(cdp_lib_ctx* ctx,
                                        const cdp_lib_buffer* input,
                                        double threshold_db,
                                        double ratio,
                                        double attack_ms,
                                        double release_ms,
                                        double makeup_gain_db)

    cdp_lib_buffer* cdp_lib_limiter(cdp_lib_ctx* ctx,
                                     const cdp_lib_buffer* input,
                                     double threshold_db,
                                     double attack_ms,
                                     double release_ms)

    cdp_lib_buffer* cdp_lib_morph(cdp_lib_ctx* ctx,
                                   const cdp_lib_buffer* input1,
                                   const cdp_lib_buffer* input2,
                                   double morph_start,
                                   double morph_end,
                                   double exponent,
                                   int fft_size)

    cdp_lib_buffer* cdp_lib_morph_glide(cdp_lib_ctx* ctx,
                                         const cdp_lib_buffer* input1,
                                         const cdp_lib_buffer* input2,
                                         double duration,
                                         int fft_size)

    cdp_lib_buffer* cdp_lib_cross_synth(cdp_lib_ctx* ctx,
                                         const cdp_lib_buffer* input1,
                                         const cdp_lib_buffer* input2,
                                         int mode,
                                         double mix,
                                         int fft_size)

cdef extern from "cdp_morph_native.h" nogil:
    # Native morph wrappers (original CDP algorithms)
    cdp_lib_buffer* cdp_morph_glide_native(cdp_lib_ctx* ctx,
                                            const cdp_lib_buffer* input1,
                                            const cdp_lib_buffer* input2,
                                            double duration,
                                            int fft_size)

    cdp_lib_buffer* cdp_morph_bridge_native(cdp_lib_ctx* ctx,
                                             const cdp_lib_buffer* input1,
                                             const cdp_lib_buffer* input2,
                                             int mode,
                                             double offset,
                                             double interp_start,
                                             double interp_end,
                                             int fft_size)

    cdp_lib_buffer* cdp_morph_morph_native(cdp_lib_ctx* ctx,
                                            const cdp_lib_buffer* input1,
                                            const cdp_lib_buffer* input2,
                                            int mode,
                                            double amp_start,
                                            double amp_end,
                                            double freq_start,
                                            double freq_end,
                                            double amp_exp,
                                            double freq_exp,
                                            double stagger,
                                            int fft_size)

cdef extern from "cdp_lib.h" nogil:
    # Analysis data structures
    ctypedef struct cdp_pitch_data:
        float *pitch
        float *confidence
        int num_frames
        float frame_time
        float sample_rate

    ctypedef struct cdp_formant_data:
        float *f1
        float *f2
        float *f3
        float *f4
        float *b1
        float *b2
        float *b3
        float *b4
        int num_frames
        float frame_time
        float sample_rate

    ctypedef struct cdp_partial_track:
        float *freq
        float *amp
        int start_frame
        int end_frame
        int num_frames

    ctypedef struct cdp_partial_data:
        cdp_partial_track *tracks
        int num_tracks
        int total_frames
        float frame_time
        float sample_rate
        int fft_size

    # Analysis memory management
    void cdp_pitch_data_free(cdp_pitch_data* data)
    void cdp_formant_data_free(cdp_formant_data* data)
    void cdp_partial_data_free(cdp_partial_data* data)

    # Analysis functions (high-level API)
    cdp_pitch_data* cdp_lib_pitch(cdp_lib_ctx* ctx,
                                   const cdp_lib_buffer* input,
                                   double min_freq,
                                   double max_freq,
                                   int frame_size,
                                   int hop_size)

    cdp_formant_data* cdp_lib_formants(cdp_lib_ctx* ctx,
                                        const cdp_lib_buffer* input,
                                        int lpc_order,
                                        int frame_size,
                                        int hop_size)

    cdp_partial_data* cdp_lib_get_partials(cdp_lib_ctx* ctx,
                                            const cdp_lib_buffer* input,
                                            double min_amp_db,
                                            int max_partials,
                                            double freq_tolerance,
                                            int fft_size,
                                            int hop_size)

cdef extern from "cdp_envelope.h" nogil:
    int CDP_FADE_LINEAR
    int CDP_FADE_EXPONENTIAL

    cdp_lib_buffer* cdp_lib_dovetail(cdp_lib_ctx* ctx,
                                      const cdp_lib_buffer* input,
                                      double fade_in_dur,
                                      double fade_out_dur,
                                      int fade_in_type,
                                      int fade_out_type)

    cdp_lib_buffer* cdp_lib_tremolo(cdp_lib_ctx* ctx,
                                     const cdp_lib_buffer* input,
                                     double freq,
                                     double depth,
                                     double gain)

    cdp_lib_buffer* cdp_lib_attack(cdp_lib_ctx* ctx,
                                    const cdp_lib_buffer* input,
                                    double attack_gain,
                                    double attack_time)

cdef extern from "cdp_distort.h" nogil:
    cdp_lib_buffer* cdp_lib_distort_overload(cdp_lib_ctx* ctx,
                                              const cdp_lib_buffer* input,
                                              double clip_level,
                                              double depth)

    cdp_lib_buffer* cdp_lib_distort_reverse(cdp_lib_ctx* ctx,
                                             const cdp_lib_buffer* input,
                                             int cycle_count)

    cdp_lib_buffer* cdp_lib_distort_fractal(cdp_lib_ctx* ctx,
                                             const cdp_lib_buffer* input,
                                             double scaling,
                                             double loudness)

    cdp_lib_buffer* cdp_lib_distort_shuffle(cdp_lib_ctx* ctx,
                                             const cdp_lib_buffer* input,
                                             int chunk_count,
                                             unsigned int seed)

    cdp_lib_buffer* cdp_lib_distort_cut(cdp_lib_ctx* ctx,
                                         const cdp_lib_buffer* input,
                                         int cycle_count,
                                         int cycle_step,
                                         double exponent,
                                         double min_level)

    cdp_lib_buffer* cdp_lib_distort_mark(cdp_lib_ctx* ctx,
                                          const cdp_lib_buffer* input,
                                          const double* markers,
                                          int marker_count,
                                          double unit_ms,
                                          double stretch,
                                          double random,
                                          int flip_phase,
                                          unsigned int seed)

    cdp_lib_buffer* cdp_lib_distort_repeat(cdp_lib_ctx* ctx,
                                            const cdp_lib_buffer* input,
                                            int multiplier,
                                            int cycle_count,
                                            int skip_cycles,
                                            double splice_ms,
                                            int mode)

    cdp_lib_buffer* cdp_lib_distort_shift(cdp_lib_ctx* ctx,
                                           const cdp_lib_buffer* input,
                                           int group_size,
                                           int shift,
                                           int mode)

    cdp_lib_buffer* cdp_lib_distort_warp(cdp_lib_ctx* ctx,
                                          const cdp_lib_buffer* input,
                                          double warp,
                                          int mode,
                                          int waveset_count)

cdef extern from "cdp_reverb.h" nogil:
    cdp_lib_buffer* cdp_lib_reverb(cdp_lib_ctx* ctx,
                                    const cdp_lib_buffer* input,
                                    double mix,
                                    double decay_time,
                                    double damping,
                                    double lpfreq,
                                    double predelay)

cdef extern from "cdp_granular.h" nogil:
    cdp_lib_buffer* cdp_lib_brassage(cdp_lib_ctx* ctx,
                                      const cdp_lib_buffer* input,
                                      double velocity,
                                      double density,
                                      double grainsize_ms,
                                      double scatter,
                                      double pitch_shift,
                                      double amp_variation,
                                      unsigned int seed)

    cdp_lib_buffer* cdp_lib_freeze(cdp_lib_ctx* ctx,
                                    const cdp_lib_buffer* input,
                                    double start_time,
                                    double end_time,
                                    double duration,
                                    double delay,
                                    double randomize,
                                    double pitch_scatter,
                                    double amp_cut,
                                    double gain,
                                    unsigned int seed)

    cdp_lib_buffer* cdp_lib_grain_cloud(cdp_lib_ctx* ctx,
                                         const cdp_lib_buffer* input,
                                         double gate,
                                         double grainsize_ms,
                                         double density,
                                         double duration,
                                         double scatter,
                                         unsigned int seed)

    cdp_lib_buffer* cdp_lib_grain_extend(cdp_lib_ctx* ctx,
                                          const cdp_lib_buffer* input,
                                          double grainsize_ms,
                                          double trough,
                                          double extension,
                                          double start_time,
                                          double end_time,
                                          unsigned int seed)

    cdp_lib_buffer* cdp_lib_texture_simple(cdp_lib_ctx* ctx,
                                            const cdp_lib_buffer* input,
                                            double duration,
                                            double density,
                                            double pitch_range,
                                            double amp_range,
                                            double spatial_range,
                                            unsigned int seed)

    cdp_lib_buffer* cdp_lib_texture_multi(cdp_lib_ctx* ctx,
                                           const cdp_lib_buffer* input,
                                           double duration,
                                           double density,
                                           int group_size,
                                           double group_spread,
                                           double pitch_range,
                                           double pitch_center,
                                           double amp_decay,
                                           unsigned int seed)

cdef extern from "cdp_granular_ext.h" nogil:
    # Extended granular operations
    cdp_lib_buffer* cdp_lib_grain_reorder(cdp_lib_ctx* ctx,
                                           const cdp_lib_buffer* input,
                                           const int* order,
                                           size_t order_count,
                                           double gate,
                                           double grainsize_ms,
                                           unsigned int seed)

    cdp_lib_buffer* cdp_lib_grain_rerhythm(cdp_lib_ctx* ctx,
                                            const cdp_lib_buffer* input,
                                            const double* times,
                                            size_t time_count,
                                            const double* ratios,
                                            size_t ratio_count,
                                            double gate,
                                            double grainsize_ms,
                                            unsigned int seed)

    cdp_lib_buffer* cdp_lib_grain_reverse(cdp_lib_ctx* ctx,
                                           const cdp_lib_buffer* input,
                                           double gate,
                                           double grainsize_ms)

    cdp_lib_buffer* cdp_lib_grain_timewarp(cdp_lib_ctx* ctx,
                                            const cdp_lib_buffer* input,
                                            double stretch,
                                            const double* stretch_curve,
                                            size_t curve_points,
                                            double gate,
                                            double grainsize_ms)

    cdp_lib_buffer* cdp_lib_grain_repitch(cdp_lib_ctx* ctx,
                                           const cdp_lib_buffer* input,
                                           double pitch_semitones,
                                           const double* pitch_curve,
                                           size_t curve_points,
                                           double gate,
                                           double grainsize_ms)

    cdp_lib_buffer* cdp_lib_grain_position(cdp_lib_ctx* ctx,
                                            const cdp_lib_buffer* input,
                                            const double* positions,
                                            size_t position_count,
                                            double duration,
                                            double gate,
                                            double grainsize_ms)

    cdp_lib_buffer* cdp_lib_grain_omit(cdp_lib_ctx* ctx,
                                        const cdp_lib_buffer* input,
                                        int keep,
                                        int out_of,
                                        double gate,
                                        double grainsize_ms,
                                        unsigned int seed)

    cdp_lib_buffer* cdp_lib_grain_duplicate(cdp_lib_ctx* ctx,
                                             const cdp_lib_buffer* input,
                                             int repeats,
                                             double gate,
                                             double grainsize_ms,
                                             unsigned int seed)

cdef extern from "cdp_granular.h" nogil:
    # Spectral operations
    cdp_lib_buffer* cdp_lib_spectral_focus(cdp_lib_ctx* ctx,
                                            const cdp_lib_buffer* input,
                                            double center_freq,
                                            double bandwidth,
                                            double gain_db,
                                            int fft_size)

    cdp_lib_buffer* cdp_lib_spectral_hilite(cdp_lib_ctx* ctx,
                                             const cdp_lib_buffer* input,
                                             double threshold_db,
                                             double boost_db,
                                             int fft_size)

    cdp_lib_buffer* cdp_lib_spectral_fold(cdp_lib_ctx* ctx,
                                           const cdp_lib_buffer* input,
                                           double fold_freq,
                                           int fft_size)

    cdp_lib_buffer* cdp_lib_spectral_clean(cdp_lib_ctx* ctx,
                                            const cdp_lib_buffer* input,
                                            double threshold_db,
                                            int fft_size)

cdef extern from "cdp_experimental.h" nogil:
    # Experimental operations
    cdp_lib_buffer* cdp_lib_strange(cdp_lib_ctx* ctx,
                                     const cdp_lib_buffer* input,
                                     double chaos_amount,
                                     double rate,
                                     unsigned int seed)

    cdp_lib_buffer* cdp_lib_brownian(cdp_lib_ctx* ctx,
                                      const cdp_lib_buffer* input,
                                      double step_size,
                                      double smoothing,
                                      int target,
                                      unsigned int seed)

    cdp_lib_buffer* cdp_lib_crystal(cdp_lib_ctx* ctx,
                                     const cdp_lib_buffer* input,
                                     double density,
                                     double decay,
                                     double pitch_scatter,
                                     unsigned int seed)

    cdp_lib_buffer* cdp_lib_fractal(cdp_lib_ctx* ctx,
                                     const cdp_lib_buffer* input,
                                     int depth,
                                     double pitch_ratio,
                                     double decay,
                                     unsigned int seed)

    cdp_lib_buffer* cdp_lib_quirk(cdp_lib_ctx* ctx,
                                   const cdp_lib_buffer* input,
                                   double probability,
                                   double intensity,
                                   int mode,
                                   unsigned int seed)

    cdp_lib_buffer* cdp_lib_chirikov(cdp_lib_ctx* ctx,
                                      const cdp_lib_buffer* input,
                                      double k_param,
                                      double mod_depth,
                                      double rate,
                                      unsigned int seed)

    cdp_lib_buffer* cdp_lib_cantor(cdp_lib_ctx* ctx,
                                    const cdp_lib_buffer* input,
                                    int depth,
                                    double duty_cycle,
                                    double smooth_ms,
                                    unsigned int seed)

    cdp_lib_buffer* cdp_lib_cascade(cdp_lib_ctx* ctx,
                                     const cdp_lib_buffer* input,
                                     int num_echoes,
                                     double delay_ms,
                                     double pitch_decay,
                                     double amp_decay,
                                     double filter_decay,
                                     unsigned int seed)

    cdp_lib_buffer* cdp_lib_fracture(cdp_lib_ctx* ctx,
                                      const cdp_lib_buffer* input,
                                      double fragment_ms,
                                      double gap_ratio,
                                      double scatter,
                                      unsigned int seed)

    cdp_lib_buffer* cdp_lib_tesselate(cdp_lib_ctx* ctx,
                                       const cdp_lib_buffer* input,
                                       double tile_ms,
                                       int pattern,
                                       double overlap,
                                       double transform,
                                       unsigned int seed)

cdef extern from "cdp_playback.h" nogil:
    # Playback/Time manipulation operations
    cdp_lib_buffer* cdp_lib_zigzag(cdp_lib_ctx* ctx,
                                    const cdp_lib_buffer* input,
                                    const double* times,
                                    int num_times,
                                    double splice_ms)

    cdp_lib_buffer* cdp_lib_iterate(cdp_lib_ctx* ctx,
                                     const cdp_lib_buffer* input,
                                     int repeats,
                                     double delay,
                                     double delay_rand,
                                     double pitch_shift,
                                     double gain_decay,
                                     unsigned int seed)

    cdp_lib_buffer* cdp_lib_stutter(cdp_lib_ctx* ctx,
                                     const cdp_lib_buffer* input,
                                     double segment_ms,
                                     double duration,
                                     double silence_prob,
                                     double silence_min_ms,
                                     double silence_max_ms,
                                     double transpose_range,
                                     unsigned int seed)

    cdp_lib_buffer* cdp_lib_bounce(cdp_lib_ctx* ctx,
                                    const cdp_lib_buffer* input,
                                    int bounces,
                                    double initial_delay,
                                    double shrink,
                                    double end_level,
                                    double level_curve,
                                    int cut_bounces)

    cdp_lib_buffer* cdp_lib_drunk(cdp_lib_ctx* ctx,
                                   const cdp_lib_buffer* input,
                                   double duration,
                                   double step_ms,
                                   double step_rand,
                                   double locus,
                                   double ambitus,
                                   double overlap,
                                   double splice_ms,
                                   unsigned int seed)

    cdp_lib_buffer* cdp_lib_loop(cdp_lib_ctx* ctx,
                                  const cdp_lib_buffer* input,
                                  double start,
                                  double length_ms,
                                  double step_ms,
                                  double search_ms,
                                  int repeats,
                                  double splice_ms,
                                  unsigned int seed)

    cdp_lib_buffer* cdp_lib_retime(cdp_lib_ctx* ctx,
                                    const cdp_lib_buffer* input,
                                    double ratio,
                                    double grain_ms,
                                    double overlap)

    cdp_lib_buffer* cdp_lib_scramble(cdp_lib_ctx* ctx,
                                      const cdp_lib_buffer* input,
                                      int mode,
                                      int group_size,
                                      unsigned int seed)

    cdp_lib_buffer* cdp_lib_splinter(cdp_lib_ctx* ctx,
                                      const cdp_lib_buffer* input,
                                      double start,
                                      double duration_ms,
                                      int repeats,
                                      double min_shrink,
                                      double shrink_curve,
                                      double accel,
                                      unsigned int seed)

    cdp_lib_buffer* cdp_lib_spin(cdp_lib_ctx* ctx,
                                  const cdp_lib_buffer* input,
                                  double rate,
                                  double doppler,
                                  double depth)

    cdp_lib_buffer* cdp_lib_rotor(cdp_lib_ctx* ctx,
                                   const cdp_lib_buffer* input,
                                   double pitch_rate,
                                   double pitch_depth,
                                   double amp_rate,
                                   double amp_depth,
                                   double phase_offset)


cdef extern from "cdp_synth.h" nogil:
    cdp_lib_buffer* cdp_lib_synth_wave(cdp_lib_ctx* ctx,
                                        int waveform,
                                        double frequency,
                                        double amplitude,
                                        double duration,
                                        int sample_rate,
                                        int channels)

    cdp_lib_buffer* cdp_lib_synth_noise(cdp_lib_ctx* ctx,
                                         int pink,
                                         double amplitude,
                                         double duration,
                                         int sample_rate,
                                         int channels,
                                         unsigned int seed)

    cdp_lib_buffer* cdp_lib_synth_click(cdp_lib_ctx* ctx,
                                         double tempo,
                                         int beats_per_bar,
                                         double duration,
                                         double click_freq,
                                         double click_dur_ms,
                                         int sample_rate)

    cdp_lib_buffer* cdp_lib_synth_chord(cdp_lib_ctx* ctx,
                                         const double* midi_notes,
                                         int num_notes,
                                         double amplitude,
                                         double duration,
                                         double detune_cents,
                                         int sample_rate,
                                         int channels)


cdef extern from "cdp_psow.h" nogil:
    cdp_lib_buffer* cdp_lib_psow_stretch(cdp_lib_ctx* ctx,
                                          const cdp_lib_buffer* input,
                                          double stretch_factor,
                                          int grain_count)

    cdp_lib_buffer* cdp_lib_psow_grab(cdp_lib_ctx* ctx,
                                       const cdp_lib_buffer* input,
                                       double time,
                                       double duration,
                                       int grain_count,
                                       double density)

    cdp_lib_buffer* cdp_lib_psow_dupl(cdp_lib_ctx* ctx,
                                       const cdp_lib_buffer* input,
                                       int repeat_count,
                                       int grain_count)

    cdp_lib_buffer* cdp_lib_psow_interp(cdp_lib_ctx* ctx,
                                         const cdp_lib_buffer* grain1,
                                         const cdp_lib_buffer* grain2,
                                         double start_dur,
                                         double interp_dur,
                                         double end_dur)


cdef extern from "cdp_fofex.h" nogil:
    cdp_lib_buffer* cdp_lib_fofex_extract(cdp_lib_ctx* ctx,
                                           const cdp_lib_buffer* input,
                                           double time,
                                           int fof_count,
                                           int window)

    cdp_lib_buffer* cdp_lib_fofex_extract_all(cdp_lib_ctx* ctx,
                                               const cdp_lib_buffer* input,
                                               int fof_count,
                                               double min_level_db,
                                               int window,
                                               int* fof_info)

    cdp_lib_buffer* cdp_lib_fofex_synth(cdp_lib_ctx* ctx,
                                         const cdp_lib_buffer* fof,
                                         double duration,
                                         double frequency,
                                         double amplitude,
                                         int fof_index,
                                         int fof_unit_len)

    cdp_lib_buffer* cdp_lib_fofex_repitch(cdp_lib_ctx* ctx,
                                           const cdp_lib_buffer* input,
                                           double pitch_shift,
                                           int preserve_formants)


cdef extern from "cdp_flutter.h" nogil:
    cdp_lib_buffer* cdp_lib_flutter(cdp_lib_ctx* ctx,
                                     const cdp_lib_buffer* input,
                                     double frequency,
                                     double depth,
                                     double gain,
                                     int randomize)

    cdp_lib_buffer* cdp_lib_flutter_multi(cdp_lib_ctx* ctx,
                                           const cdp_lib_buffer* input,
                                           double frequency,
                                           double depth,
                                           double gain,
                                           const int* channel_sets,
                                           int randomize)


cdef extern from "cdp_hover.h" nogil:
    cdp_lib_buffer* cdp_lib_hover(cdp_lib_ctx* ctx,
                                   const cdp_lib_buffer* input,
                                   double frequency,
                                   double location,
                                   double frq_rand,
                                   double loc_rand,
                                   double splice_ms,
                                   double duration)


cdef extern from "cdp_constrict.h" nogil:
    cdp_lib_buffer* cdp_lib_constrict(cdp_lib_ctx* ctx,
                                       const cdp_lib_buffer* input,
                                       double constriction)


cdef extern from "cdp_phase.h" nogil:
    cdp_lib_buffer* cdp_lib_phase_invert(cdp_lib_ctx* ctx,
                                          const cdp_lib_buffer* input)
    cdp_lib_buffer* cdp_lib_phase_stereo(cdp_lib_ctx* ctx,
                                          const cdp_lib_buffer* input,
                                          double transfer)


cdef extern from "cdp_wrappage.h" nogil:
    cdp_lib_buffer* cdp_lib_wrappage(cdp_lib_ctx* ctx,
                                      const cdp_lib_buffer* input,
                                      double grain_size,
                                      double density,
                                      double velocity,
                                      double pitch,
                                      double spread,
                                      double jitter,
                                      double splice_ms,
                                      double duration,
                                      unsigned int seed)


# Waveform type constants
WAVE_SINE = 0
WAVE_SQUARE = 1
WAVE_SAW = 2
WAVE_RAMP = 3
WAVE_TRIANGLE = 4

# Global CDP library context (lazily initialized)
cdef cdp_lib_ctx* _get_cdp_lib_ctx() except NULL:
    """Get this thread's CDP library context.

    Per-thread, not a shared singleton: processing calls release the GIL, and
    every call writes ctx->error_msg and draws from ctx->prng_state. Sharing
    one context across threads would let concurrent operations corrupt each
    other's error reporting and consume each other's seeded random stream.
    """
    cdef cdp_lib_ctx* ctx
    with nogil:
        ctx = cdp_lib_thread_ctx()
    if ctx is NULL:
        raise MemoryError("Failed to initialize CDP library")
    return ctx


cdef cdp_lib_buffer* _buffer_to_cdp_lib(Buffer buf) except NULL:
    """Convert a cycdp Buffer to a cdp_lib_buffer."""
    cdef cdp_lib_buffer* lib_buf = cdp_lib_buffer_create(
        buf.sample_count, buf.channels, buf.sample_rate)
    if lib_buf is NULL:
        raise MemoryError("Failed to create CDP library buffer")

    # Copy data
    cdef size_t i
    for i in range(buf.sample_count):
        lib_buf.data[i] = buf._buf.samples[i]

    return lib_buf


cdef int _buffer_pair_to_cdp_lib(Buffer first, Buffer second,
                                 cdp_lib_buffer** out_first,
                                 cdp_lib_buffer** out_second) except -1:
    """Convert two Buffers, releasing the first if the second fails.

    Two-input operations previously converted both on consecutive lines, so a
    MemoryError on the second leaked the first.
    """
    out_first[0] = _buffer_to_cdp_lib(first)
    try:
        out_second[0] = _buffer_to_cdp_lib(second)
    except:
        cdp_lib_buffer_free(out_first[0])
        out_first[0] = NULL
        raise
    return 0


cdef Buffer _take_cdp_lib_buffer(cdp_lib_buffer* lib_buf):
    """Convert a cdp_lib_buffer to a cycdp Buffer, taking ownership of it.

    lib_buf is freed on every path, including when Buffer.create raises
    MemoryError. Callers previously freed it on the line *after* the
    conversion, so a failed allocation leaked the output buffer -- which is
    often the larger of the two, and which happens exactly when memory is
    already scarce.

    Pass ownership and do not free the pointer afterwards.
    """
    cdef Buffer result
    cdef size_t i
    try:
        if lib_buf is NULL:
            raise CDPError(-1, "No output buffer produced")
        if lib_buf.channels <= 0:
            raise CDPError(-1, "Output buffer has invalid channel count")

        result = Buffer.create(
            lib_buf.length // lib_buf.channels,
            lib_buf.channels,
            lib_buf.sample_rate)

        # Copy data
        for i in range(lib_buf.length):
            result._buf.samples[i] = lib_buf.data[i]

        return result
    finally:
        cdp_lib_buffer_free(lib_buf)


def time_stretch(Buffer buf not None, double factor, int fft_size=1024, int overlap=3):
    """Time-stretch audio without changing pitch (native implementation).

    Uses phase vocoder for high-quality time stretching.
    This is a native implementation - no subprocess overhead.

    Args:
        buf: Input Buffer.
        factor: Stretch factor (2.0 = twice as long, 0.5 = half as long).
        fft_size: FFT window size (power of 2, 256-8192). Default 1024.
        overlap: Overlap factor (1-4). Default 3.

    Returns:
        New Buffer with time-stretched audio.

    Raises:
        CDPError: If processing fails.
    """
    if factor <= 0:
        raise ValueError("Stretch factor must be positive")

    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_time_stretch(
            ctx, input_buf, factor, fft_size, overlap)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Time stretch failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def spectral_blur(Buffer buf not None, double blur_time, int fft_size=1024):
    """Apply spectral blur (native implementation).

    Averages the spectrum over time, creating a smeared effect.
    This is a native implementation - no subprocess overhead.

    Args:
        buf: Input Buffer.
        blur_time: Time to blur over in seconds.
        fft_size: FFT window size. Default 1024.

    Returns:
        New Buffer with blurred audio.

    Raises:
        CDPError: If processing fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_spectral_blur(
            ctx, input_buf, blur_time, fft_size)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Spectral blur failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def modify_speed(Buffer buf not None, double speed_factor):
    """Change playback speed (native implementation).

    Changes both pitch and duration.
    This is a native implementation - no subprocess overhead.

    Args:
        buf: Input Buffer.
        speed_factor: Speed multiplier (2.0 = double speed/octave up).

    Returns:
        New Buffer with modified speed.

    Raises:
        CDPError: If processing fails.
    """
    if speed_factor <= 0:
        raise ValueError("Speed factor must be positive")

    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_speed(ctx, input_buf, speed_factor)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Speed change failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


# =============================================================================
# Native spectral operations
# =============================================================================

def pitch_shift(Buffer buf not None, double semitones, int fft_size=1024):
    """Pitch shift without changing duration (native implementation).

    Args:
        buf: Input Buffer.
        semitones: Pitch shift in semitones (positive = up, negative = down).
        fft_size: FFT window size. Default 1024.

    Returns:
        New Buffer with pitch-shifted audio.

    Raises:
        CDPError: If processing fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_pitch_shift(ctx, input_buf, semitones, fft_size)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Pitch shift failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def spectral_shift(Buffer buf not None, double shift_hz, int fft_size=1024):
    """Shift all frequencies by a fixed Hz offset (native implementation).

    Unlike pitch shift, this adds a constant Hz value, creating inharmonic effects.

    Args:
        buf: Input Buffer.
        shift_hz: Frequency offset in Hz (positive = up, negative = down).
        fft_size: FFT window size. Default 1024.

    Returns:
        New Buffer with shifted frequencies.

    Raises:
        CDPError: If processing fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_spectral_shift(ctx, input_buf, shift_hz, fft_size)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Spectral shift failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def spectral_stretch(Buffer buf not None, double max_stretch,
                            double freq_divide=1000.0, double exponent=1.0, int fft_size=1024):
    """Stretch frequencies differentially (native implementation).

    Higher frequencies get stretched more, creating inharmonic effects.

    Args:
        buf: Input Buffer.
        max_stretch: Maximum transposition ratio for highest frequencies.
        freq_divide: Frequency below which no stretching occurs. Default 1000 Hz.
        exponent: Stretching curve (1.0 = linear). Default 1.0.
        fft_size: FFT window size. Default 1024.

    Returns:
        New Buffer with stretched spectrum.

    Raises:
        CDPError: If processing fails.
    """
    if max_stretch <= 0:
        raise ValueError("max_stretch must be positive")

    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_spectral_stretch(
            ctx, input_buf, max_stretch, freq_divide, exponent, fft_size)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Spectral stretch failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def filter_lowpass(Buffer buf not None, double cutoff_freq,
                          double attenuation_db=-60, int fft_size=1024):
    """Apply lowpass filter (native implementation).

    Args:
        buf: Input Buffer.
        cutoff_freq: Cutoff frequency in Hz.
        attenuation_db: Attenuation in dB (negative). Default -60.
        fft_size: FFT window size. Default 1024.

    Returns:
        New Buffer with filtered audio.

    Raises:
        CDPError: If processing fails.
    """
    if cutoff_freq <= 0:
        raise ValueError("cutoff_freq must be positive")

    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_filter_lowpass(
            ctx, input_buf, cutoff_freq, attenuation_db, fft_size)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Lowpass filter failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def filter_highpass(Buffer buf not None, double cutoff_freq,
                           double attenuation_db=-60, int fft_size=1024):
    """Apply highpass filter (native implementation).

    Args:
        buf: Input Buffer.
        cutoff_freq: Cutoff frequency in Hz.
        attenuation_db: Attenuation in dB (negative). Default -60.
        fft_size: FFT window size. Default 1024.

    Returns:
        New Buffer with filtered audio.

    Raises:
        CDPError: If processing fails.
    """
    if cutoff_freq <= 0:
        raise ValueError("cutoff_freq must be positive")

    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_filter_highpass(
            ctx, input_buf, cutoff_freq, attenuation_db, fft_size)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Highpass filter failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def filter_bandpass(Buffer buf not None, double low_freq, double high_freq,
                    double attenuation_db=-60, int fft_size=1024):
    """Apply bandpass filter (native implementation).

    Args:
        buf: Input Buffer.
        low_freq: Low cutoff frequency in Hz.
        high_freq: High cutoff frequency in Hz.
        attenuation_db: Attenuation in dB (negative). Default -60.
        fft_size: FFT window size. Default 1024.

    Returns:
        New Buffer with filtered audio.

    Raises:
        CDPError: If processing fails.
    """
    if low_freq <= 0:
        raise ValueError("low_freq must be positive")
    if high_freq <= low_freq:
        raise ValueError("high_freq must be greater than low_freq")

    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_filter_bandpass(
            ctx, input_buf, low_freq, high_freq, attenuation_db, fft_size)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Bandpass filter failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def filter_notch(Buffer buf not None, double center_freq, double width_hz,
                 double attenuation_db=-60, int fft_size=1024):
    """Apply notch (band-reject) filter (native implementation).

    Args:
        buf: Input Buffer.
        center_freq: Center frequency to notch out in Hz.
        width_hz: Width of the notch in Hz.
        attenuation_db: Attenuation in dB (negative). Default -60.
        fft_size: FFT window size. Default 1024.

    Returns:
        New Buffer with filtered audio.

    Raises:
        CDPError: If processing fails.
    """
    if center_freq <= 0:
        raise ValueError("center_freq must be positive")
    if width_hz <= 0:
        raise ValueError("width_hz must be positive")

    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_filter_notch(
            ctx, input_buf, center_freq, width_hz, attenuation_db, fft_size)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Notch filter failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def gate(Buffer buf not None, double threshold_db, double attack_ms=1.0,
         double release_ms=50.0, double hold_ms=10.0):
    """Apply noise gate (native implementation).

    Silences audio below threshold with attack/release envelope.

    Args:
        buf: Input Buffer.
        threshold_db: Threshold in dB (e.g., -40).
        attack_ms: Attack time in milliseconds. Default 1.0.
        release_ms: Release time in milliseconds. Default 50.0.
        hold_ms: Hold time before release in milliseconds. Default 10.0.

    Returns:
        New Buffer with gated audio.

    Raises:
        CDPError: If processing fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_gate(
            ctx, input_buf, threshold_db, attack_ms, release_ms, hold_ms)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Gate failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def bitcrush(Buffer buf not None, int bit_depth=8, int downsample=1):
    """Apply bitcrusher effect (native implementation).

    Reduces bit depth and/or sample rate for lo-fi effects.

    Args:
        buf: Input Buffer.
        bit_depth: Target bit depth (1-16, 16 = no reduction). Default 8.
        downsample: Downsample factor (1 = none, 2 = half rate). Default 1.

    Returns:
        New Buffer with crushed audio.

    Raises:
        CDPError: If processing fails.
    """
    if bit_depth < 1 or bit_depth > 16:
        raise ValueError("bit_depth must be 1-16")
    if downsample < 1:
        raise ValueError("downsample must be >= 1")

    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_bitcrush(
            ctx, input_buf, bit_depth, downsample)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Bitcrush failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def ring_mod(Buffer buf not None, double freq, double mix=1.0):
    """Apply ring modulation (native implementation).

    Multiplies signal by a carrier frequency.

    Args:
        buf: Input Buffer.
        freq: Carrier frequency in Hz.
        mix: Dry/wet mix (0.0 = dry, 1.0 = wet). Default 1.0.

    Returns:
        New Buffer with ring-modulated audio.

    Raises:
        CDPError: If processing fails.
    """
    if freq <= 0:
        raise ValueError("freq must be positive")
    if mix < 0 or mix > 1:
        raise ValueError("mix must be 0.0-1.0")

    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_ring_mod(ctx, input_buf, freq, mix)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Ring modulation failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def delay(Buffer buf not None, double delay_ms, double feedback=0.3, double mix=0.5):
    """Apply delay effect with feedback (native implementation).

    Args:
        buf: Input Buffer.
        delay_ms: Delay time in milliseconds.
        feedback: Feedback amount (0.0 to <1.0). Default 0.3.
        mix: Dry/wet mix (0.0 = dry, 1.0 = wet). Default 0.5.

    Returns:
        New Buffer with delayed audio.

    Raises:
        CDPError: If processing fails.
    """
    if delay_ms <= 0:
        raise ValueError("delay_ms must be positive")
    if feedback < 0 or feedback >= 1:
        raise ValueError("feedback must be 0.0 to <1.0")
    if mix < 0 or mix > 1:
        raise ValueError("mix must be 0.0-1.0")

    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_delay(ctx, input_buf, delay_ms, feedback, mix)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Delay failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def chorus(Buffer buf not None, double rate=1.5, double depth_ms=5.0, double mix=0.5):
    """Apply chorus effect (native implementation).

    Modulated delay for thickening sounds.

    Args:
        buf: Input Buffer.
        rate: LFO rate in Hz (typically 0.5-5). Default 1.5.
        depth_ms: Modulation depth in milliseconds (typically 1-20). Default 5.0.
        mix: Dry/wet mix (0.0 = dry, 1.0 = wet). Default 0.5.

    Returns:
        New Buffer with chorus effect.

    Raises:
        CDPError: If processing fails.
    """
    if rate <= 0:
        raise ValueError("rate must be positive")
    if depth_ms <= 0:
        raise ValueError("depth_ms must be positive")
    if mix < 0 or mix > 1:
        raise ValueError("mix must be 0.0-1.0")

    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_chorus(ctx, input_buf, rate, depth_ms, mix)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Chorus failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def flanger(Buffer buf not None, double rate=0.5, double depth_ms=3.0,
            double feedback=0.5, double mix=0.5):
    """Apply flanger effect (native implementation).

    Short modulated delay with feedback for sweeping comb filter.

    Args:
        buf: Input Buffer.
        rate: LFO rate in Hz (typically 0.1-2). Default 0.5.
        depth_ms: Modulation depth in milliseconds (typically 1-10). Default 3.0.
        feedback: Feedback amount (-0.95 to 0.95). Default 0.5.
        mix: Dry/wet mix (0.0 = dry, 1.0 = wet). Default 0.5.

    Returns:
        New Buffer with flanger effect.

    Raises:
        CDPError: If processing fails.
    """
    if rate <= 0:
        raise ValueError("rate must be positive")
    if depth_ms <= 0:
        raise ValueError("depth_ms must be positive")
    if feedback < -0.95 or feedback > 0.95:
        raise ValueError("feedback must be -0.95 to 0.95")
    if mix < 0 or mix > 1:
        raise ValueError("mix must be 0.0-1.0")

    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_flanger(
            ctx, input_buf, rate, depth_ms, feedback, mix)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Flanger failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


# =============================================================================
# EQ and Dynamics
# =============================================================================

def eq_parametric(Buffer buf not None, double center_freq, double gain_db,
                  double q=1.0, int fft_size=1024):
    """Apply parametric EQ (native implementation).

    Boost or cut at a specific frequency with adjustable bandwidth (Q).

    Args:
        buf: Input Buffer.
        center_freq: Center frequency in Hz.
        gain_db: Gain in dB (positive = boost, negative = cut).
        q: Q factor (0.1-10, higher = narrower bandwidth). Default 1.0.
        fft_size: FFT window size. Default 1024.

    Returns:
        New Buffer with EQ applied.

    Raises:
        CDPError: If processing fails.
    """
    if center_freq <= 0:
        raise ValueError("center_freq must be positive")
    if q <= 0:
        raise ValueError("q must be positive")

    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_eq_parametric(
            ctx, input_buf, center_freq, gain_db, q, fft_size)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Parametric EQ failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def envelope_follow(Buffer buf not None, double attack_ms=10.0,
                    double release_ms=100.0, str mode="peak"):
    """Extract amplitude envelope from audio (native implementation).

    Uses peak or RMS detection with attack/release smoothing.

    Args:
        buf: Input Buffer.
        attack_ms: Attack time in milliseconds. Default 10.0.
        release_ms: Release time in milliseconds. Default 100.0.
        mode: "peak" or "rms". Default "peak".

    Returns:
        New Buffer containing envelope values (mono).

    Raises:
        CDPError: If processing fails.
    """
    if attack_ms < 0:
        raise ValueError("attack_ms must be non-negative")
    if release_ms < 0:
        raise ValueError("release_ms must be non-negative")

    cdef int mode_int = 0 if mode == "peak" else 1

    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_envelope_follow(
            ctx, input_buf, attack_ms, release_ms, mode_int)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Envelope follow failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def envelope_apply(Buffer buf not None, Buffer envelope not None, double depth=1.0):
    """Apply an envelope to audio (native implementation).

    Multiplies audio by envelope values (amplitude modulation).

    Args:
        buf: Input Buffer to process.
        envelope: Envelope Buffer (from envelope_follow or generated).
        depth: Modulation depth (0.0 = no effect, 1.0 = full). Default 1.0.

    Returns:
        New Buffer with envelope applied.

    Raises:
        CDPError: If processing fails.
    """
    if depth < 0 or depth > 1:
        raise ValueError("depth must be 0.0 to 1.0")

    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf
    cdef cdp_lib_buffer* env_buf
    _buffer_pair_to_cdp_lib(buf, envelope, &input_buf, &env_buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_envelope_apply(
            ctx, input_buf, env_buf, depth)

    cdp_lib_buffer_free(input_buf)
    cdp_lib_buffer_free(env_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Envelope apply failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def compressor(Buffer buf not None, double threshold_db=-20.0, double ratio=4.0,
               double attack_ms=10.0, double release_ms=100.0, double makeup_gain_db=0.0):
    """Apply dynamic range compression (native implementation).

    Reduces the volume of audio above the threshold.

    Args:
        buf: Input Buffer.
        threshold_db: Level above which compression starts. Default -20.0.
        ratio: Compression ratio (e.g., 4.0 = 4:1). Default 4.0.
        attack_ms: Attack time in milliseconds. Default 10.0.
        release_ms: Release time in milliseconds. Default 100.0.
        makeup_gain_db: Makeup gain in dB. Default 0.0.

    Returns:
        New Buffer with compression applied.

    Raises:
        CDPError: If processing fails.
    """
    if ratio < 1.0:
        raise ValueError("ratio must be >= 1.0")
    if attack_ms < 0:
        raise ValueError("attack_ms must be non-negative")
    if release_ms < 0:
        raise ValueError("release_ms must be non-negative")

    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_compressor(
            ctx, input_buf, threshold_db, ratio, attack_ms, release_ms, makeup_gain_db)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Compressor failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def limiter(Buffer buf not None, double threshold_db=-0.1,
            double attack_ms=0.0, double release_ms=50.0):
    """Apply limiting to prevent clipping (native implementation).

    Prevents audio from exceeding the threshold.

    Args:
        buf: Input Buffer.
        threshold_db: Maximum level in dB. Default -0.1.
        attack_ms: Attack time (0 = hard limiting). Default 0.0.
        release_ms: Release time in milliseconds. Default 50.0.

    Returns:
        New Buffer with limiting applied.

    Raises:
        CDPError: If processing fails.
    """
    if attack_ms < 0:
        raise ValueError("attack_ms must be non-negative")
    if release_ms < 0:
        raise ValueError("release_ms must be non-negative")

    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_limiter(
            ctx, input_buf, threshold_db, attack_ms, release_ms)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Limiter failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


# =============================================================================
# Native envelope operations
# =============================================================================

def dovetail(Buffer buf not None, double fade_in_dur, double fade_out_dur,
                    str fade_type="exponential"):
    """Apply fade-in and fade-out envelopes (native implementation).

    Args:
        buf: Input Buffer.
        fade_in_dur: Fade-in duration in seconds.
        fade_out_dur: Fade-out duration in seconds.
        fade_type: "linear" or "exponential" (default).

    Returns:
        New Buffer with fades applied.

    Raises:
        CDPError: If processing fails.
        ValueError: If fade_type is invalid.
    """
    cdef int fade_in_type, fade_out_type

    if fade_type == "linear":
        fade_in_type = CDP_FADE_LINEAR
        fade_out_type = CDP_FADE_LINEAR
    elif fade_type == "exponential":
        fade_in_type = CDP_FADE_EXPONENTIAL
        fade_out_type = CDP_FADE_EXPONENTIAL
    else:
        raise ValueError(f"Invalid fade_type: {fade_type}. Use 'linear' or 'exponential'.")

    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_dovetail(
            ctx, input_buf, fade_in_dur, fade_out_dur, fade_in_type, fade_out_type)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Dovetail failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def tremolo(Buffer buf not None, double freq, double depth, double gain=1.0):
    """Apply tremolo / amplitude modulation (native implementation).

    Args:
        buf: Input Buffer.
        freq: Tremolo frequency in Hz (0 to 500).
        depth: Modulation depth (0.0 = none, 1.0 = full).
        gain: Output gain multiplier. Default 1.0.

    Returns:
        New Buffer with tremolo applied.

    Raises:
        CDPError: If processing fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_tremolo(ctx, input_buf, freq, depth, gain)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Tremolo failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def attack(Buffer buf not None, double attack_gain, double attack_time):
    """Modify the attack portion of a sound (native implementation).

    Args:
        buf: Input Buffer.
        attack_gain: Gain multiplier for attack (e.g., 2.0 = double).
        attack_time: Duration of attack region in seconds.

    Returns:
        New Buffer with modified attack.

    Raises:
        CDPError: If processing fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_attack(ctx, input_buf, attack_gain, attack_time)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Attack modification failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


# =============================================================================
# Native distortion operations
# =============================================================================

def distort_overload(Buffer buf not None, double clip_level, double depth=0.5):
    """Apply clipping distortion (native implementation).

    Args:
        buf: Input Buffer (will be converted to mono if stereo).
        clip_level: Clipping threshold (0.0 to 1.0).
        depth: Distortion depth (0 = hard clip, 1 = soft clip). Default 0.5.

    Returns:
        New mono Buffer with distortion applied.

    Raises:
        CDPError: If processing fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_distort_overload(ctx, input_buf, clip_level, depth)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Distort overload failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def distort_reverse(Buffer buf not None, int cycle_count):
    """Reverse groups of wavecycles (native implementation).

    Args:
        buf: Input Buffer (will be converted to mono if stereo).
        cycle_count: Number of cycles in each reversed group.

    Returns:
        New mono Buffer with reversed cycles.

    Raises:
        CDPError: If processing fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_distort_reverse(ctx, input_buf, cycle_count)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Distort reverse failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def distort_fractal(Buffer buf not None, double scaling, double loudness=1.0):
    """Apply fractal distortion (native implementation).

    Adds harmonic complexity by recursively overlaying wavecycle patterns.

    Args:
        buf: Input Buffer (will be converted to mono if stereo).
        scaling: Fractal scaling factor (affects harmonic content).
        loudness: Output loudness (0.0 to 1.0). Default 1.0.

    Returns:
        New mono Buffer with fractal distortion.

    Raises:
        CDPError: If processing fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_distort_fractal(ctx, input_buf, scaling, loudness)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Distort fractal failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def distort_shuffle(Buffer buf not None, int chunk_count, unsigned int seed=0):
    """Shuffle segments of audio (native implementation).

    Args:
        buf: Input Buffer (will be converted to mono if stereo).
        chunk_count: Number of chunks to divide the audio into.
        seed: Random seed (0 = time-based). Default 0.

    Returns:
        New mono Buffer with shuffled segments.

    Raises:
        CDPError: If processing fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_distort_shuffle(ctx, input_buf, chunk_count, seed)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Distort shuffle failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def distort_cut(Buffer buf not None, int cycle_count=4, int cycle_step=4,
                double exponent=1.0, double min_level=0.0):
    """Cut sound into waveset segments with decaying envelope.

    Divides audio into segments based on zero-crossing waveset boundaries
    and applies a decaying envelope to each segment. Creates a "cut-up"
    distortion effect.

    Args:
        buf: Input Buffer (will be converted to mono if stereo).
        cycle_count: Number of wavesets (half-cycles) per segment (1-100). Default 4.
        cycle_step: Number of wavesets to step between segment starts (1-100). Default 4.
                    If equal to cycle_count, segments are contiguous.
                    If less, segments overlap. If greater, there are gaps.
        exponent: Envelope decay exponent (0.1 to 10.0). Default 1.0.
                  1.0 = linear decay, >1 = faster initial decay, <1 = slower decay.
        min_level: Minimum output level to keep (0.0 to 1.0, 0 = keep all). Default 0.0.
                   Segments below this level are removed.

    Returns:
        New mono Buffer with cut segments.

    Raises:
        CDPError: If processing fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_distort_cut(
            ctx, input_buf, cycle_count, cycle_step, exponent, min_level
        )

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Distort cut failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def distort_mark(Buffer buf not None, markers not None, double unit_ms=10.0,
                 double stretch=1.0, double random=0.0, bint flip_phase=False,
                 unsigned int seed=0):
    """Interpolate between waveset-groups at marked time positions.

    Finds waveset groups at specified time markers and morphs between them,
    creating smooth transitions with optional phase flipping and randomization.

    Args:
        buf: Input Buffer (will be converted to mono if stereo).
        markers: List/array of time positions in seconds where waveset groups are located.
                 Must have at least 2 markers in ascending order.
        unit_ms: Approximate size of waveset group to find at each marker (1.0 to 100.0 ms).
                 Default 10.0.
        stretch: Time stretch factor for output (0.5 to 2.0, 1.0 = no stretch). Default 1.0.
        random: Randomize waveset durations (0.0 to 1.0, 0 = no randomization). Default 0.0.
        flip_phase: If True, flip phase of alternate interpolated wavesets. Default False.
        seed: Random seed (0 = time-based). Default 0.

    Returns:
        New mono Buffer with interpolated wavesets.

    Raises:
        CDPError: If processing fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    # Convert markers to C array
    cdef int marker_count = len(markers)
    cdef double* marker_array = <double*>malloc(marker_count * sizeof(double))
    if marker_array is NULL:
        cdp_lib_buffer_free(input_buf)
        raise MemoryError("Failed to allocate marker array")

    cdef int i
    for i in range(marker_count):
        marker_array[i] = markers[i]

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_distort_mark(
            ctx, input_buf, marker_array, marker_count, unit_ms, stretch, random,
            1 if flip_phase else 0, seed
        )

    free(marker_array)
    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Distort mark failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def distort_repeat(Buffer buf not None, int multiplier=2, int cycle_count=1,
                   int skip_cycles=0, double splice_ms=15.0, int mode=0):
    """Time-stretch by repeating wavecycles.

    Detects wavecycles and repeats groups of them to create time-stretching
    or rhythmic stuttering effects.

    Args:
        buf: Input Buffer (will be converted to mono if stereo).
        multiplier: Number of times to repeat each wavecycle group (1-100). Default 2.
        cycle_count: Number of wavecycles in each repeated group (1-100). Default 1.
        skip_cycles: Number of cycles to skip at start of file (0 or more). Default 0.
        splice_ms: Splice length in milliseconds for smooth transitions (1.0 to 50.0).
                   Default 15.0.
        mode: 0 = time-stretch (output longer), 1 = maintain time (skip ahead after repeats).
              Default 0.

    Returns:
        New mono Buffer with repeated wavecycles.

    Raises:
        CDPError: If processing fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_distort_repeat(
            ctx, input_buf, multiplier, cycle_count, skip_cycles, splice_ms, mode
        )

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Distort repeat failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def distort_shift(Buffer buf not None, int group_size=1, int shift=1, int mode=0):
    """Shift or swap half-wavecycle groups for phase distortion.

    Detects half-wavecycles (zero crossings) and either shifts alternate
    groups forward in time, or swaps adjacent groups.

    Args:
        buf: Input Buffer (will be converted to mono if stereo).
        group_size: Number of half-wavecycles per group (1-50). Default 1.
                    1 = single half-wavesets, 2 = waveset + half, etc.
        shift: For mode 0: number of groups to shift forward (1-50, with wrap-around).
               Ignored in mode 1. Default 1.
        mode: 0 = shift alternate groups forward, 1 = swap adjacent groups. Default 0.

    Returns:
        New mono Buffer with shifted/swapped wavecycles.

    Raises:
        CDPError: If processing fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_distort_shift(
            ctx, input_buf, group_size, shift, mode
        )

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Distort shift failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def distort_warp(Buffer buf not None, double warp=0.001, int mode=0, int waveset_count=1):
    """Apply progressive warp distortion with modular sample folding.

    Creates unique distortion by progressively warping sample values through
    a modular folding pattern. The warp increment is applied per-sample (mode 0)
    or per-waveset group (mode 1).

    Args:
        buf: Input Buffer. Mode 1 requires mono; stereo will be converted.
        warp: Progressive sample multiplier (0.0001 to 0.1). Default 0.001.
              Controls the rate of warping progression.
        mode: 0 = warp increment per sample (works with stereo),
              1 = warp increment per waveset group (mono only). Default 0.
        waveset_count: For mode 1: after how many wavesets does warp increment (1-256).
                       Ignored in mode 0. Default 1.

    Returns:
        New Buffer with warp distortion applied.

    Raises:
        CDPError: If processing fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_distort_warp(
            ctx, input_buf, warp, mode, waveset_count
        )

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Distort warp failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


# =============================================================================
# Native reverb
# =============================================================================

def reverb(Buffer buf not None, double mix=0.5, double decay_time=2.0,
                  double damping=0.5, double lpfreq=8000.0, double predelay=0.0):
    """Apply reverb (native implementation).

    Uses a Feedback Delay Network (8 combs + 4 allpass) for dense reverb.

    Args:
        buf: Input Buffer.
        mix: Dry/wet balance (0.0 = dry, 1.0 = wet). Default 0.5.
        decay_time: Reverb decay time in seconds (RT60). Default 2.0.
        damping: High frequency damping (0.0 to 1.0). Default 0.5.
        lpfreq: Lowpass filter cutoff in Hz. Default 8000.
        predelay: Pre-delay time in milliseconds. Default 0.

    Returns:
        New stereo Buffer with reverb applied (includes tail).

    Raises:
        CDPError: If processing fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_reverb(
            ctx, input_buf, mix, decay_time, damping, lpfreq, predelay)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Reverb failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


# =============================================================================
# Native granular operations
# =============================================================================

def brassage(Buffer buf not None, double velocity=1.0, double density=1.0,
                    double grainsize_ms=50.0, double scatter=0.0,
                    double pitch_shift=0.0, double amp_variation=0.0,
                    unsigned int seed=0):
    """Apply granular brassage (native implementation).

    Breaks sound into grains and reassembles with optional modifications.

    Args:
        buf: Input Buffer.
        velocity: Playback speed through source (1.0 = normal). Default 1.0.
        density: Grain density multiplier. Default 1.0.
        grainsize_ms: Grain size in milliseconds. Default 50.
        scatter: Time scatter of grains (0.0 to 1.0). Default 0.
        pitch_shift: Pitch shift per grain in semitones. Default 0.
        amp_variation: Random amplitude variation (0.0 to 1.0). Default 0.
        seed: Random seed (0 = use time). Default 0.

    Returns:
        New Buffer with brassage applied.

    Raises:
        CDPError: If processing fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_brassage(
            ctx, input_buf, velocity, density, grainsize_ms,
            scatter, pitch_shift, amp_variation, seed)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Brassage failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def freeze(Buffer buf not None, double start_time, double end_time,
                  double duration, double delay=0.05, double randomize=0.2,
                  double pitch_scatter=0.0, double amp_cut=0.1, double gain=1.0,
                  unsigned int seed=0):
    """Freeze a segment of audio by repeated iteration (native implementation).

    Creates a sustained texture by repeating a frozen segment with variations.

    Args:
        buf: Input Buffer.
        start_time: Start of segment to freeze (seconds).
        end_time: End of segment to freeze (seconds).
        duration: Output duration in seconds.
        delay: Average delay between iterations. Default 0.05.
        randomize: Delay time randomization (0.0 to 1.0). Default 0.2.
        pitch_scatter: Max random pitch shift in semitones (0 to 12). Default 0.
        amp_cut: Max random amplitude reduction (0.0 to 1.0). Default 0.1.
        gain: Gain adjustment for frozen segment. Default 1.0.
        seed: Random seed (0 = use time). Default 0.

    Returns:
        New Buffer with frozen audio.

    Raises:
        CDPError: If processing fails.
    """
    if end_time <= start_time:
        raise ValueError("end_time must be greater than start_time")
    if duration <= 0:
        raise ValueError("duration must be positive")

    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_freeze(
            ctx, input_buf, start_time, end_time, duration,
            delay, randomize, pitch_scatter, amp_cut, gain, seed)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Freeze failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def grain_cloud(Buffer buf not None, double gate=0.1, double grainsize_ms=50.0,
                double density=10.0, double duration=0.0, double scatter=0.3,
                unsigned int seed=0):
    """Generate grain cloud from source audio (CDP: grain).

    Extracts grains based on amplitude threshold and generates a cloud
    by placing them at random or regular intervals.

    Args:
        buf: Input Buffer.
        gate: Amplitude threshold for grain detection (0.0 to 1.0). Default 0.1.
        grainsize_ms: Target grain size in milliseconds. Default 50.0.
        density: Grain density (grains per second). Default 10.0.
        duration: Output duration in seconds (0 = same as input). Default 0.
        scatter: Position scatter amount (0.0 to 1.0). Default 0.3.
        seed: Random seed (0 = use time). Default 0.

    Returns:
        New Buffer with grain cloud.

    Raises:
        CDPError: If processing fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_grain_cloud(
            ctx, input_buf, gate, grainsize_ms, density, duration, scatter, seed)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Grain cloud failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def grain_extend(Buffer buf not None, double grainsize_ms=15.0, double trough=0.3,
                 double extension=1.0, double start_time=0.0, double end_time=0.0,
                 unsigned int seed=0):
    """Extend audio duration using grain repetition (CDP: grainex extend).

    Finds grains in source and extends duration by repeating grains
    with variations.

    Args:
        buf: Input Buffer.
        grainsize_ms: Window size to detect grains (milliseconds). Default 15.0.
        trough: Acceptable trough height relative to peaks (0.0 to 1.0). Default 0.3.
        extension: How much duration to add (seconds). Default 1.0.
        start_time: Start of grain material in source (seconds). Default 0.0.
        end_time: End of grain material in source (seconds, 0 = end). Default 0.0.
        seed: Random seed (0 = use time). Default 0.

    Returns:
        New Buffer with extended audio.

    Raises:
        CDPError: If processing fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_grain_extend(
            ctx, input_buf, grainsize_ms, trough, extension, start_time, end_time, seed)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Grain extend failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def texture_simple(Buffer buf not None, double duration=5.0, double density=5.0,
                   double pitch_range=6.0, double amp_range=0.3,
                   double spatial_range=0.8, unsigned int seed=0):
    """Generate simple texture (CDP: texture SIMPLE_TEX).

    Creates texture by layering source at multiple transpositions
    and time offsets.

    Args:
        buf: Input Buffer.
        duration: Output duration in seconds. Default 5.0.
        density: Events per second. Default 5.0.
        pitch_range: Pitch range in semitones (symmetric around 0). Default 6.0.
        amp_range: Amplitude variation (0.0 to 1.0). Default 0.3.
        spatial_range: Stereo spread (0.0 to 1.0, 0 = mono center). Default 0.8.
        seed: Random seed (0 = use time). Default 0.

    Returns:
        New stereo Buffer with texture.

    Raises:
        CDPError: If processing fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_texture_simple(
            ctx, input_buf, duration, density, pitch_range, amp_range, spatial_range, seed)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Texture simple failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def texture_multi(Buffer buf not None, double duration=5.0, double density=2.0,
                  int group_size=4, double group_spread=0.2,
                  double pitch_range=8.0, double pitch_center=0.0,
                  double amp_decay=0.3, unsigned int seed=0):
    """Generate multi-layer texture (CDP: texture GROUPS).

    Creates complex texture with grouped events and decorations.

    Args:
        buf: Input Buffer.
        duration: Output duration in seconds. Default 5.0.
        density: Groups per second. Default 2.0.
        group_size: Average notes per group (1-16). Default 4.
        group_spread: Time spread within group (seconds). Default 0.2.
        pitch_range: Pitch range in semitones. Default 8.0.
        pitch_center: Center pitch offset in semitones. Default 0.0.
        amp_decay: Amplitude decay through group (0.0 to 1.0). Default 0.3.
        seed: Random seed (0 = use time). Default 0.

    Returns:
        New stereo Buffer with multi-layer texture.

    Raises:
        CDPError: If processing fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_texture_multi(
            ctx, input_buf, duration, density, group_size, group_spread,
            pitch_range, pitch_center, amp_decay, seed)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Texture multi failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


# =============================================================================
# Extended Granular Operations
# =============================================================================

def grain_reorder(Buffer buf not None, order=None, double gate=0.1,
                  double grainsize_ms=50.0, unsigned int seed=0):
    """Reorder grains in audio (CDP: GRAIN REORDER).

    Detects grains using amplitude gating, then rearranges them according
    to the provided order array, or shuffles randomly if no order given.

    Args:
        buf: Input Buffer.
        order: List of grain indices for new order (None = shuffle). Default None.
        gate: Amplitude threshold for grain detection (0-1). Default 0.1.
        grainsize_ms: Typical grain size in ms. Default 50.0.
        seed: Random seed for shuffling (0 = use time). Default 0.

    Returns:
        New Buffer with reordered grains.

    Raises:
        CDPError: If processing fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef int* order_ptr = NULL
    cdef size_t order_count = 0

    # Convert Python list to C array if provided
    cdef list order_list
    cdef int[:] order_view
    import array
    if order is not None:
        order_list = list(order)
        order_count = len(order_list)
        order_arr = array.array('i', order_list)
        order_view = order_arr
        order_ptr = &order_view[0]

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_grain_reorder(
            ctx, input_buf, order_ptr, order_count, gate, grainsize_ms, seed)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Grain reorder failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def grain_rerhythm(Buffer buf not None, times=None, ratios=None,
                   double gate=0.1, double grainsize_ms=50.0,
                   unsigned int seed=0):
    """Change the timing between grains (CDP: GRAIN RERHYTHM).

    Detects grains and repositions them according to new timing values
    or inter-grain duration ratios.

    Args:
        buf: Input Buffer.
        times: List of new grain start times in seconds (None = use ratios). Default None.
        ratios: List of inter-grain duration ratios (None = use times). Default None.
        gate: Amplitude threshold for grain detection (0-1). Default 0.1.
        grainsize_ms: Typical grain size in ms. Default 50.0.
        seed: Random seed for variation. Default 0.

    Returns:
        New Buffer with rerhythmed grains.

    Raises:
        CDPError: If processing fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef double* times_ptr = NULL
    cdef size_t time_count = 0
    cdef double* ratios_ptr = NULL
    cdef size_t ratio_count = 0

    cdef double[:] times_view
    cdef double[:] ratios_view
    import array

    if times is not None:
        times_list = list(times)
        time_count = len(times_list)
        times_arr = array.array('d', times_list)
        times_view = times_arr
        times_ptr = &times_view[0]

    if ratios is not None:
        ratios_list = list(ratios)
        ratio_count = len(ratios_list)
        ratios_arr = array.array('d', ratios_list)
        ratios_view = ratios_arr
        ratios_ptr = &ratios_view[0]

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_grain_rerhythm(
            ctx, input_buf, times_ptr, time_count, ratios_ptr, ratio_count,
            gate, grainsize_ms, seed)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Grain rerhythm failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def grain_reverse(Buffer buf not None, double gate=0.1, double grainsize_ms=50.0):
    """Reverse the order of grains (CDP: GRAIN REVERSE).

    Detects grains and outputs them in reverse order. Individual grains
    are NOT reversed internally.

    Args:
        buf: Input Buffer.
        gate: Amplitude threshold for grain detection (0-1). Default 0.1.
        grainsize_ms: Typical grain size in ms. Default 50.0.

    Returns:
        New Buffer with reversed grain order.

    Raises:
        CDPError: If processing fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_grain_reverse(
            ctx, input_buf, gate, grainsize_ms)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Grain reverse failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def grain_timewarp(Buffer buf not None, double stretch=1.0, stretch_curve=None,
                   double gate=0.1, double grainsize_ms=50.0):
    """Non-linear time stretch of grain sequence (CDP: GRAIN TIMEWARP).

    Stretches or compresses the timing between grains according to a
    stretch factor or time-varying curve.

    Args:
        buf: Input Buffer.
        stretch: Uniform stretch factor (>1 = slower, <1 = faster). Default 1.0.
        stretch_curve: List of (time, stretch) pairs for varying stretch. Default None.
        gate: Amplitude threshold for grain detection (0-1). Default 0.1.
        grainsize_ms: Typical grain size in ms. Default 50.0.

    Returns:
        New Buffer with time-warped grains.

    Raises:
        CDPError: If processing fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef double* curve_ptr = NULL
    cdef size_t curve_points = 0

    cdef double[:] curve_view
    import array

    if stretch_curve is not None:
        # Flatten list of tuples to single array [t0, v0, t1, v1, ...]
        curve_list = []
        for t, v in stretch_curve:
            curve_list.append(float(t))
            curve_list.append(float(v))
        curve_points = len(stretch_curve)
        curve_arr = array.array('d', curve_list)
        curve_view = curve_arr
        curve_ptr = &curve_view[0]

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_grain_timewarp(
            ctx, input_buf, stretch, curve_ptr, curve_points, gate, grainsize_ms)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Grain timewarp failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def grain_repitch(Buffer buf not None, double pitch_semitones=0.0, pitch_curve=None,
                  double gate=0.1, double grainsize_ms=50.0):
    """Variable pitch shifting per grain (CDP: GRAIN REPITCH).

    Time-stretches individual grains to change their pitch without
    affecting overall timing.

    Args:
        buf: Input Buffer.
        pitch_semitones: Uniform pitch shift in semitones. Default 0.0.
        pitch_curve: List of (time, semitones) pairs for varying pitch. Default None.
        gate: Amplitude threshold for grain detection (0-1). Default 0.1.
        grainsize_ms: Typical grain size in ms. Default 50.0.

    Returns:
        New Buffer with repitched grains.

    Raises:
        CDPError: If processing fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef double* curve_ptr = NULL
    cdef size_t curve_points = 0

    cdef double[:] curve_view
    import array

    if pitch_curve is not None:
        curve_list = []
        for t, v in pitch_curve:
            curve_list.append(float(t))
            curve_list.append(float(v))
        curve_points = len(pitch_curve)
        curve_arr = array.array('d', curve_list)
        curve_view = curve_arr
        curve_ptr = &curve_view[0]

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_grain_repitch(
            ctx, input_buf, pitch_semitones, curve_ptr, curve_points, gate, grainsize_ms)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Grain repitch failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def grain_position(Buffer buf not None, positions=None, double duration=0.0,
                   double gate=0.1, double grainsize_ms=50.0):
    """Position grains at specific times (CDP: GRAIN POSITION).

    Places grains at specified positions in the output.

    Args:
        buf: Input Buffer.
        positions: List of output positions in seconds. Default None.
        duration: Total output duration (0 = auto). Default 0.0.
        gate: Amplitude threshold for grain detection (0-1). Default 0.1.
        grainsize_ms: Typical grain size in ms. Default 50.0.

    Returns:
        New Buffer with repositioned grains.

    Raises:
        CDPError: If processing fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef double* pos_ptr = NULL
    cdef size_t pos_count = 0

    cdef double[:] pos_view
    import array

    if positions is not None:
        pos_list = [float(p) for p in positions]
        pos_count = len(pos_list)
        pos_arr = array.array('d', pos_list)
        pos_view = pos_arr
        pos_ptr = &pos_view[0]

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_grain_position(
            ctx, input_buf, pos_ptr, pos_count, duration, gate, grainsize_ms)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Grain position failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def grain_omit(Buffer buf not None, int keep=1, int out_of=2,
               double gate=0.1, double grainsize_ms=50.0,
               unsigned int seed=0):
    """Selectively remove grains (CDP: GRAIN OMIT).

    Keeps only specified grains from the input.

    Args:
        buf: Input Buffer.
        keep: Number of grains to keep out of each group. Default 1.
        out_of: Group size (e.g., keep 1 out_of 3). Default 2.
        gate: Amplitude threshold for grain detection (0-1). Default 0.1.
        grainsize_ms: Typical grain size in ms. Default 50.0.
        seed: Random seed (0 = use time). Default 0.

    Returns:
        New Buffer with filtered grains.

    Raises:
        CDPError: If processing fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_grain_omit(
            ctx, input_buf, keep, out_of, gate, grainsize_ms, seed)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Grain omit failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def grain_duplicate(Buffer buf not None, int repeats=2,
                    double gate=0.1, double grainsize_ms=50.0,
                    unsigned int seed=0):
    """Repeat grains (CDP: GRAIN DUPLICATE).

    Duplicates each grain a specified number of times.

    Args:
        buf: Input Buffer.
        repeats: Number of times to repeat each grain. Default 2.
        gate: Amplitude threshold for grain detection (0-1). Default 0.1.
        grainsize_ms: Typical grain size in ms. Default 50.0.
        seed: Random seed for variation (0 = use time). Default 0.

    Returns:
        New Buffer with duplicated grains.

    Raises:
        CDPError: If processing fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_grain_duplicate(
            ctx, input_buf, repeats, gate, grainsize_ms, seed)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Grain duplicate failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


# =============================================================================
# Spectral Operations
# =============================================================================

def spectral_focus(Buffer buf not None, double center_freq=1000.0,
                   double bandwidth=200.0, double gain_db=6.0, int fft_size=1024):
    """Enhance frequencies around a center point with super-Gaussian curve.

    Uses a sharper curve (exponent 4) than standard parametric EQ for
    more precise frequency focusing.

    Args:
        buf: Input Buffer.
        center_freq: Center frequency in Hz. Default 1000.0.
        bandwidth: Bandwidth in Hz (half-power width). Default 200.0.
        gain_db: Gain in dB (can be negative to attenuate). Default 6.0.
        fft_size: FFT window size. Default 1024.

    Returns:
        New Buffer with focused frequencies.

    Raises:
        CDPError: If processing fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_spectral_focus(
            ctx, input_buf, center_freq, bandwidth, gain_db, fft_size)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Spectral focus failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def spectral_hilite(Buffer buf not None, double threshold_db=-20.0,
                    double boost_db=6.0, int fft_size=1024):
    """Boost spectral peaks above threshold.

    Detects local maxima in each spectral frame and boosts them selectively,
    emphasizing the harmonic structure.

    Args:
        buf: Input Buffer.
        threshold_db: Only boost peaks above this level (relative to frame peak). Default -20.0.
        boost_db: Amount to boost peaks in dB. Default 6.0.
        fft_size: FFT window size. Default 1024.

    Returns:
        New Buffer with highlighted peaks.

    Raises:
        CDPError: If processing fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_spectral_hilite(
            ctx, input_buf, threshold_db, boost_db, fft_size)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Spectral hilite failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def spectral_fold(Buffer buf not None, double fold_freq=2000.0, int fft_size=1024):
    """Fold spectrum at frequency (creates metallic, inharmonic effects).

    Frequencies above the fold point are mirrored back down, creating
    complex inharmonic textures with a metallic quality.

    Args:
        buf: Input Buffer.
        fold_freq: Frequency at which to fold the spectrum. Default 2000.0.
        fft_size: FFT window size. Default 1024.

    Returns:
        New Buffer with folded spectrum.

    Raises:
        CDPError: If processing fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_spectral_fold(
            ctx, input_buf, fold_freq, fft_size)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Spectral fold failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def spectral_clean(Buffer buf not None, double threshold_db=-40.0, int fft_size=1024):
    """Spectral noise gate - zero bins below per-frame threshold.

    Removes low-level noise and artifacts by zeroing spectral bins
    below a threshold relative to the frame's peak amplitude.

    Args:
        buf: Input Buffer.
        threshold_db: Threshold in dB below frame peak. Default -40.0.
        fft_size: FFT window size. Default 1024.

    Returns:
        New Buffer with cleaned spectrum.

    Raises:
        CDPError: If processing fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_spectral_clean(
            ctx, input_buf, threshold_db, fft_size)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Spectral clean failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


# =============================================================================
# Experimental Operations
# =============================================================================

def strange(Buffer buf not None, double chaos_amount=0.5, double rate=2.0,
            unsigned int seed=0):
    """Strange attractor (Lorenz) modulation.

    Uses the Lorenz attractor to chaotically modulate pitch and amplitude.
    Creates complex, evolving timbral changes that are deterministic but
    appear chaotic. Same seed produces identical results.

    Args:
        buf: Input Buffer.
        chaos_amount: Amount of chaotic modulation (0.0 to 1.0). Default 0.5.
        rate: Speed of chaotic evolution (typically 0.1 to 10.0). Default 2.0.
        seed: Random seed for initial attractor state (0 = use time). Default 0.

    Returns:
        New Buffer with chaotic modulation.

    Raises:
        CDPError: If processing fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_strange(
            ctx, input_buf, chaos_amount, rate, seed)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Strange modulation failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def brownian(Buffer buf not None, double step_size=0.1, double smoothing=0.9,
             int target=0, unsigned int seed=0):
    """Brownian (random walk) modulation.

    Applies random walk modulation to pitch, amplitude, or filter cutoff.
    Creates organic, drifting parameter changes.

    Args:
        buf: Input Buffer.
        step_size: Maximum step size per frame (in target units). Default 0.1.
        smoothing: Smoothing factor (0.0 to 1.0, higher = smoother). Default 0.9.
        target: Modulation target: 0=pitch (semitones), 1=amp (dB), 2=filter (Hz). Default 0.
        seed: Random seed for reproducibility (0 = use time). Default 0.

    Returns:
        New Buffer with random walk modulation.

    Raises:
        CDPError: If processing fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_brownian(
            ctx, input_buf, step_size, smoothing, target, seed)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Brownian modulation failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def crystal(Buffer buf not None, double density=50.0, double decay=0.5,
            double pitch_scatter=2.0, unsigned int seed=0):
    """Crystal textures - granular with decaying echoes.

    Extracts small grains and creates shimmering, crystalline textures
    through multiple decaying echo layers with pitch scatter.

    Args:
        buf: Input Buffer.
        density: Grain density (grains per second, typically 20-200). Default 50.0.
        decay: Echo decay time in seconds. Default 0.5.
        pitch_scatter: Random pitch variation in semitones. Default 2.0.
        seed: Random seed for reproducibility (0 = use time). Default 0.

    Returns:
        New Buffer with crystalline texture (may be longer than input due to decay).

    Raises:
        CDPError: If processing fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_crystal(
            ctx, input_buf, density, decay, pitch_scatter, seed)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Crystal texture failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def fractal(Buffer buf not None, int depth=3, double pitch_ratio=0.5,
            double decay=0.7, unsigned int seed=0):
    """Fractal processing - self-similar recursive layering.

    Creates fractal textures by recursively layering pitch-shifted copies
    of the input at decreasing amplitudes, creating self-similar structures.

    Args:
        buf: Input Buffer.
        depth: Recursion depth (1-6, higher = more layers). Default 3.
        pitch_ratio: Pitch ratio between layers (e.g., 0.5 for octave down). Default 0.5.
        decay: Amplitude decay per layer (0.0 to 1.0). Default 0.7.
        seed: Random seed for timing variations (0 = use time). Default 0.

    Returns:
        New Buffer with fractal processing.

    Raises:
        CDPError: If processing fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_fractal(
            ctx, input_buf, depth, pitch_ratio, decay, seed)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Fractal processing failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def quirk(Buffer buf not None, double probability=0.3, double intensity=0.5,
          int mode=2, unsigned int seed=0):
    """Quirk - unpredictable glitchy transformations.

    Applies random, unexpected pitch and timing shifts with probability-based
    triggers. Creates glitchy, surprising audio artifacts.

    Args:
        buf: Input Buffer.
        probability: Probability of quirk occurring (0.0 to 1.0). Default 0.3.
        intensity: Intensity of quirks (0.0 to 1.0). Default 0.5.
        mode: 0=pitch quirks, 1=timing quirks, 2=both. Default 2.
        seed: Random seed for reproducibility (0 = use time). Default 0.

    Returns:
        New Buffer with quirk effects (length may vary due to timing quirks).

    Raises:
        CDPError: If processing fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_quirk(
            ctx, input_buf, probability, intensity, mode, seed)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Quirk processing failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def chirikov(Buffer buf not None, double k_param=2.0, double mod_depth=0.5,
             double rate=2.0, unsigned int seed=0):
    """Chirikov map modulation - chaotic standard map.

    Uses the Chirikov standard map (a classic chaotic system) to modulate
    pitch and amplitude. Different from Lorenz - produces more periodic
    chaos with islands of stability.

    Args:
        buf: Input Buffer.
        k_param: Chirikov K parameter (0.5 to 10.0, chaos increases with K). Default 2.0.
        mod_depth: Modulation depth (0.0 to 1.0). Default 0.5.
        rate: Rate of map iteration. Default 2.0.
        seed: Random seed for initial conditions (0 = use time). Default 0.

    Returns:
        New Buffer with Chirikov modulation.

    Raises:
        CDPError: If processing fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_chirikov(
            ctx, input_buf, k_param, mod_depth, rate, seed)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Chirikov modulation failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def cantor(Buffer buf not None, int depth=4, double duty_cycle=0.5,
           double smooth_ms=5.0, unsigned int seed=0):
    """Cantor set gating - fractal silence pattern.

    Applies a Cantor set pattern as a gating function, recursively removing
    middle thirds of audio segments to create fractally-structured silences.

    Args:
        buf: Input Buffer.
        depth: Recursion depth (1-8, higher = finer fractal detail). Default 4.
        duty_cycle: Proportion of audio kept vs silenced (0.0 to 1.0). Default 0.5.
        smooth_ms: Crossfade time in ms to smooth transitions. Default 5.0.
        seed: Random seed for variation (0 = use time). Default 0.

    Returns:
        New Buffer with Cantor gating.

    Raises:
        CDPError: If processing fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_cantor(
            ctx, input_buf, depth, duty_cycle, smooth_ms, seed)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Cantor gating failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def cascade(Buffer buf not None, int num_echoes=6, double delay_ms=100.0,
            double pitch_decay=0.95, double amp_decay=0.7, double filter_decay=0.8,
            unsigned int seed=0):
    """Cascade - cascading echoes with progressive transformation.

    Creates cascading delays where each echo is progressively transformed
    (pitch shifted down, filtered darker, amplitude reduced).

    Args:
        buf: Input Buffer.
        num_echoes: Number of cascade stages (1-12). Default 6.
        delay_ms: Base delay time in milliseconds. Default 100.0.
        pitch_decay: Pitch ratio per stage (e.g., 0.95 = 5% down each stage). Default 0.95.
        amp_decay: Amplitude decay per stage (0.0 to 1.0). Default 0.7.
        filter_decay: Filter cutoff decay per stage (0.0 to 1.0). Default 0.8.
        seed: Random seed for timing jitter (0 = use time). Default 0.

    Returns:
        New Buffer with cascading echoes (longer than input).

    Raises:
        CDPError: If processing fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_cascade(
            ctx, input_buf, num_echoes, delay_ms, pitch_decay, amp_decay, filter_decay, seed)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Cascade processing failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def fracture(Buffer buf not None, double fragment_ms=50.0, double gap_ratio=0.5,
             double scatter=0.3, unsigned int seed=0):
    """Fracture - break audio into fragments with gaps.

    Breaks the audio into fragments with random gaps and optional
    reordering. Creates broken, fractured textures.

    Args:
        buf: Input Buffer.
        fragment_ms: Average fragment size in milliseconds. Default 50.0.
        gap_ratio: Ratio of gaps to fragments (0.0 to 2.0). Default 0.5.
        scatter: Amount of fragment reordering (0.0 = none, 1.0 = full shuffle). Default 0.3.
        seed: Random seed for reproducibility (0 = use time). Default 0.

    Returns:
        New Buffer with fractured audio (length varies with gap_ratio).

    Raises:
        CDPError: If processing fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_fracture(
            ctx, input_buf, fragment_ms, gap_ratio, scatter, seed)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Fracture processing failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def tesselate(Buffer buf not None, double tile_ms=50.0, int pattern=1,
              double overlap=0.25, double transform=0.3, unsigned int seed=0):
    """Tesselate - tile audio segments in patterns.

    Divides audio into tiles and arranges them in patterns with optional
    transformations per tile (pitch, amplitude, reverse).

    Args:
        buf: Input Buffer.
        tile_ms: Tile size in milliseconds. Default 50.0.
        pattern: Pattern mode: 0=repeat, 1=mirror, 2=rotate, 3=random. Default 1.
        overlap: Tile overlap ratio (0.0 to 0.5). Default 0.25.
        transform: Transform intensity (0.0 to 1.0). Default 0.3.
        seed: Random seed for random pattern mode (0 = use time). Default 0.

    Returns:
        New Buffer with tessellated audio.

    Raises:
        CDPError: If processing fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_tesselate(
            ctx, input_buf, tile_ms, pattern, overlap, transform, seed)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Tesselate processing failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def morph(Buffer buf1 not None, Buffer buf2 not None,
          double morph_start=0.0, double morph_end=1.0,
          double exponent=1.0, int fft_size=1024):
    """Spectral morph between two sounds (CDP: SPECMORPH).

    Interpolates amplitude and frequency between two sounds over time.
    Amplitude interpolation is linear; frequency interpolation is exponential.

    Args:
        buf1: First input Buffer (source).
        buf2: Second input Buffer (target).
        morph_start: Time when morphing begins (0.0 to 1.0 of duration). Default 0.0.
        morph_end: Time when morphing ends (0.0 to 1.0 of duration). Default 1.0.
        exponent: Interpolation curve (1.0 = linear, <1 = fast start, >1 = slow start). Default 1.0.
        fft_size: FFT window size. Default 1024.

    Returns:
        New Buffer with morphed audio.

    Raises:
        CDPError: If processing fails.
    """
    if buf1.sample_rate != buf2.sample_rate:
        raise ValueError("Sample rates must match for morphing")

    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input1_buf
    cdef cdp_lib_buffer* input2_buf
    _buffer_pair_to_cdp_lib(buf1, buf2, &input1_buf, &input2_buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_morph(
            ctx, input1_buf, input2_buf, morph_start, morph_end, exponent, fft_size)

    cdp_lib_buffer_free(input1_buf)
    cdp_lib_buffer_free(input2_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Morph failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def morph_glide(Buffer buf1 not None, Buffer buf2 not None,
                double duration=1.0, int fft_size=1024):
    """Simple spectral glide between two sounds (CDP: SPECGLIDE).

    Creates a linear glide from one spectrum to another over the specified duration.

    Args:
        buf1: First input Buffer.
        buf2: Second input Buffer.
        duration: Output duration in seconds. Default 1.0.
        fft_size: FFT window size. Default 1024.

    Returns:
        New Buffer with glided audio.

    Raises:
        CDPError: If processing fails.
    """
    if buf1.sample_rate != buf2.sample_rate:
        raise ValueError("Sample rates must match for glide")

    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input1_buf
    cdef cdp_lib_buffer* input2_buf
    _buffer_pair_to_cdp_lib(buf1, buf2, &input1_buf, &input2_buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_morph_glide(
            ctx, input1_buf, input2_buf, duration, fft_size)

    cdp_lib_buffer_free(input1_buf)
    cdp_lib_buffer_free(input2_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Morph glide failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def cross_synth(Buffer buf1 not None, Buffer buf2 not None,
                int mode=0, double mix=1.0, int fft_size=1024):
    """Cross-synthesis: combine amplitude from one sound with frequencies from another.

    Args:
        buf1: First input Buffer (amplitude source by default).
        buf2: Second input Buffer (frequency source by default).
        mode: 0 = amp from buf1, freq from buf2 (default)
              1 = amp from buf2, freq from buf1
        mix: Mix between original and cross-synthesized (0.0 = original, 1.0 = full cross). Default 1.0.
        fft_size: FFT window size. Default 1024.

    Returns:
        New Buffer with cross-synthesized audio.

    Raises:
        CDPError: If processing fails.
    """
    if buf1.sample_rate != buf2.sample_rate:
        raise ValueError("Sample rates must match for cross-synthesis")

    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input1_buf
    cdef cdp_lib_buffer* input2_buf
    _buffer_pair_to_cdp_lib(buf1, buf2, &input1_buf, &input2_buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_cross_synth(
            ctx, input1_buf, input2_buf, mode, mix, fft_size)

    cdp_lib_buffer_free(input1_buf)
    cdp_lib_buffer_free(input2_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Cross-synthesis failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


# =============================================================================
# Native morph functions (original CDP algorithms)
# =============================================================================

def morph_glide_native(Buffer buf1 not None, Buffer buf2 not None,
                       double duration=1.0, int fft_size=1024):
    """Spectral glide using original CDP algorithm (CDP: SPECGLIDE).

    Creates a smooth glide between two spectral frames - one from each input.
    The original algorithm reads single windows from each file and interpolates
    amplitudes linearly while frequencies follow an exponential curve.

    Args:
        buf1: First input Buffer.
        buf2: Second input Buffer.
        duration: Output duration in seconds. Default 1.0.
        fft_size: FFT window size (power of 2). Default 1024.

    Returns:
        New Buffer with glided audio.

    Raises:
        CDPError: If processing fails.
    """
    if buf1.sample_rate != buf2.sample_rate:
        raise ValueError("Sample rates must match for glide")

    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input1_buf
    cdef cdp_lib_buffer* input2_buf
    _buffer_pair_to_cdp_lib(buf1, buf2, &input1_buf, &input2_buf)

    cdef cdp_lib_buffer* output_buf = cdp_morph_glide_native(
        ctx, input1_buf, input2_buf, duration, fft_size)

    cdp_lib_buffer_free(input1_buf)
    cdp_lib_buffer_free(input2_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Native morph glide failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def morph_bridge_native(Buffer buf1 not None, Buffer buf2 not None,
                        int mode=0, double offset=0.0,
                        double interp_start=0.0, double interp_end=1.0,
                        int fft_size=1024):
    """Spectral bridge using original CDP algorithm (CDP: SPECBRIDGE).

    Creates a bridge (crossfade) between two spectral files with control over
    normalization mode and interpolation timing.

    Args:
        buf1: First input Buffer.
        buf2: Second input Buffer.
        mode: Bridge normalization mode (0-5):
              0 = No normalization
              1 = Normalize to minimum level
              2 = Normalize to file 1 level
              3 = Normalize to file 2 level
              4 = Progressive normalization 1->2
              5 = Progressive normalization 2->1
        offset: Time offset (seconds) to start file2 relative to file1. Default 0.0.
        interp_start: Normalized position (0-1) where interpolation starts. Default 0.0.
        interp_end: Normalized position (0-1) where interpolation ends. Default 1.0.
        fft_size: FFT window size (power of 2). Default 1024.

    Returns:
        New Buffer with bridged audio.

    Raises:
        CDPError: If processing fails.
    """
    if buf1.sample_rate != buf2.sample_rate:
        raise ValueError("Sample rates must match for bridge")

    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input1_buf
    cdef cdp_lib_buffer* input2_buf
    _buffer_pair_to_cdp_lib(buf1, buf2, &input1_buf, &input2_buf)

    cdef cdp_lib_buffer* output_buf = cdp_morph_bridge_native(
        ctx, input1_buf, input2_buf, mode, offset, interp_start, interp_end, fft_size)

    cdp_lib_buffer_free(input1_buf)
    cdp_lib_buffer_free(input2_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Native morph bridge failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def morph_native(Buffer buf1 not None, Buffer buf2 not None,
                 int mode=0,
                 double amp_start=0.0, double amp_end=1.0,
                 double freq_start=0.0, double freq_end=1.0,
                 double amp_exp=1.0, double freq_exp=1.0,
                 double stagger=0.0, int fft_size=1024):
    """Full spectral morph using original CDP algorithm (CDP: SPECMORPH).

    Full spectral morphing with separate control over amplitude and frequency
    interpolation timing. Supports multiple interpolation curves.

    Args:
        buf1: First input Buffer.
        buf2: Second input Buffer.
        mode: Interpolation mode:
              0 = Linear interpolation
              1 = Cosine interpolation (smoother)
        amp_start: Normalized time (0-1) where amplitude morph starts. Default 0.0.
        amp_end: Normalized time (0-1) where amplitude morph ends. Default 1.0.
        freq_start: Normalized time (0-1) where frequency morph starts. Default 0.0.
        freq_end: Normalized time (0-1) where frequency morph ends. Default 1.0.
        amp_exp: Exponent for amplitude curve (1=linear, >1=slow start). Default 1.0.
        freq_exp: Exponent for frequency curve. Default 1.0.
        stagger: Time offset (seconds) between file2 and output. Default 0.0.
        fft_size: FFT window size (power of 2). Default 1024.

    Returns:
        New Buffer with morphed audio.

    Raises:
        CDPError: If processing fails.
    """
    if buf1.sample_rate != buf2.sample_rate:
        raise ValueError("Sample rates must match for morph")

    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input1_buf
    cdef cdp_lib_buffer* input2_buf
    _buffer_pair_to_cdp_lib(buf1, buf2, &input1_buf, &input2_buf)

    cdef cdp_lib_buffer* output_buf = cdp_morph_morph_native(
        ctx, input1_buf, input2_buf, mode,
        amp_start, amp_end, freq_start, freq_end,
        amp_exp, freq_exp, stagger, fft_size)

    cdp_lib_buffer_free(input1_buf)
    cdp_lib_buffer_free(input2_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Native morph failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


# =============================================================================
# Analysis functions
# =============================================================================

def pitch(Buffer buf not None, double min_freq=50.0, double max_freq=2000.0,
          int frame_size=2048, int hop_size=512):
    """Extract pitch contour from audio using YIN algorithm.

    Uses autocorrelation-based pitch detection. Returns pitch in Hz for each
    analysis frame, with 0 indicating unvoiced segments.

    Args:
        buf: Input Buffer.
        min_freq: Minimum expected frequency in Hz. Default 50.
        max_freq: Maximum expected frequency in Hz. Default 2000.
        frame_size: Analysis frame size in samples. Default 2048.
        hop_size: Hop size in samples. Default 512.

    Returns:
        Dictionary with:
            - 'pitch': list of pitch values in Hz (0 = unvoiced)
            - 'confidence': list of confidence values (0-1)
            - 'num_frames': number of analysis frames
            - 'frame_time': time between frames in seconds
            - 'sample_rate': original sample rate

    Raises:
        CDPError: If processing fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_pitch_data* result
    with nogil:
        result = cdp_lib_pitch(
            ctx, input_buf, min_freq, max_freq, frame_size, hop_size)

    cdp_lib_buffer_free(input_buf)

    if result is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Pitch analysis failed")

    # Convert to Python lists
    cdef int i
    pitch_list = [result.pitch[i] for i in range(result.num_frames)]
    confidence_list = [result.confidence[i] for i in range(result.num_frames)]

    output = {
        'pitch': pitch_list,
        'confidence': confidence_list,
        'num_frames': result.num_frames,
        'frame_time': result.frame_time,
        'sample_rate': result.sample_rate
    }

    cdp_pitch_data_free(result)
    return output


def formants(Buffer buf not None, int lpc_order=12, int frame_size=1024, int hop_size=256):
    """Extract formant frequencies from audio using LPC analysis.

    Uses Linear Predictive Coding to estimate formant frequencies.
    Returns up to 4 formants (F1-F4) with bandwidths for each frame.

    Args:
        buf: Input Buffer.
        lpc_order: LPC order (higher = more formants but less stable). Default 12.
        frame_size: Analysis frame size in samples. Default 1024.
        hop_size: Hop size in samples. Default 256.

    Returns:
        Dictionary with:
            - 'f1', 'f2', 'f3', 'f4': lists of formant frequencies in Hz
            - 'b1', 'b2', 'b3', 'b4': lists of formant bandwidths in Hz
            - 'num_frames': number of analysis frames
            - 'frame_time': time between frames in seconds
            - 'sample_rate': original sample rate

    Raises:
        CDPError: If processing fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_formant_data* result
    with nogil:
        result = cdp_lib_formants(
            ctx, input_buf, lpc_order, frame_size, hop_size)

    cdp_lib_buffer_free(input_buf)

    if result is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Formant analysis failed")

    # Convert to Python lists
    cdef int i
    cdef int n = result.num_frames
    f1_list = [result.f1[i] for i in range(n)]
    f2_list = [result.f2[i] for i in range(n)]
    f3_list = [result.f3[i] for i in range(n)]
    f4_list = [result.f4[i] for i in range(n)]
    b1_list = [result.b1[i] for i in range(n)]
    b2_list = [result.b2[i] for i in range(n)]
    b3_list = [result.b3[i] for i in range(n)]
    b4_list = [result.b4[i] for i in range(n)]

    output = {
        'f1': f1_list, 'f2': f2_list, 'f3': f3_list, 'f4': f4_list,
        'b1': b1_list, 'b2': b2_list, 'b3': b3_list, 'b4': b4_list,
        'num_frames': result.num_frames,
        'frame_time': result.frame_time,
        'sample_rate': result.sample_rate
    }

    cdp_formant_data_free(result)
    return output


def get_partials(Buffer buf not None, double min_amp_db=-60.0, int max_partials=100,
                 double freq_tolerance=50.0, int fft_size=2048, int hop_size=512):
    """Extract sinusoidal partials from audio using peak tracking.

    Performs spectral analysis and tracks peaks over time.
    Each partial is a continuous frequency/amplitude trajectory.

    Args:
        buf: Input Buffer.
        min_amp_db: Minimum amplitude in dB to consider as partial. Default -60.
        max_partials: Maximum number of partials to track. Default 100.
        freq_tolerance: Frequency tolerance for track continuation in Hz. Default 50.
        fft_size: FFT size for analysis. Default 2048.
        hop_size: Hop size in samples. Default 512.

    Returns:
        Dictionary with:
            - 'tracks': list of partial track dictionaries, each containing:
                - 'freq': list of frequency values
                - 'amp': list of amplitude values
                - 'start_frame': frame index where track starts
                - 'end_frame': frame index where track ends
            - 'num_tracks': number of partial tracks
            - 'total_frames': total number of analysis frames
            - 'frame_time': time between frames in seconds
            - 'sample_rate': original sample rate
            - 'fft_size': FFT size used

    Raises:
        CDPError: If processing fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_partial_data* result
    with nogil:
        result = cdp_lib_get_partials(
            ctx, input_buf, min_amp_db, max_partials, freq_tolerance, fft_size, hop_size)

    cdp_lib_buffer_free(input_buf)

    if result is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Partial tracking failed")

    # Convert to Python lists
    cdef int i, j
    cdef cdp_partial_track* track
    tracks_list = []
    for i in range(result.num_tracks):
        track = &result.tracks[i]
        track_dict = {
            'freq': [track.freq[j] for j in range(track.num_frames)],
            'amp': [track.amp[j] for j in range(track.num_frames)],
            'start_frame': track.start_frame,
            'end_frame': track.end_frame
        }
        tracks_list.append(track_dict)

    output = {
        'tracks': tracks_list,
        'num_tracks': result.num_tracks,
        'total_frames': result.total_frames,
        'frame_time': result.frame_time,
        'sample_rate': result.sample_rate,
        'fft_size': result.fft_size
    }

    cdp_partial_data_free(result)
    return output


# =============================================================================
# Playback/Time manipulation operations
# =============================================================================

def zigzag(Buffer buf not None, times not None, double splice_ms=15.0):
    """Zigzag playback - alternates between forward and backward playback.

    Plays audio forward then backward through specified time segments,
    creating a zigzag pattern through the sound.

    Args:
        buf: Input Buffer.
        times: List/array of time points in seconds (defines segment boundaries).
               Must have at least 2 elements.
        splice_ms: Crossfade splice length in milliseconds. Default 15.0.

    Returns:
        New Buffer with zigzag playback.

    Raises:
        CDPError: If processing fails.
        ValueError: If times has fewer than 2 elements.

    Example:
        times = [0, 1, 2, 3] plays 0->1 forward, 2->1 backward, 2->3 forward
    """
    import array

    # Convert times to a C array
    cdef double[::1] times_view
    if hasattr(times, '__iter__'):
        times_list = list(times)
    else:
        times_list = [times]

    if len(times_list) < 2:
        raise ValueError("times must have at least 2 elements")

    times_arr = array.array('d', times_list)
    times_view = times_arr

    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef int n_times = len(times_list)
    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_zigzag(
            ctx, input_buf, &times_view[0], n_times, splice_ms)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Zigzag processing failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def iterate(Buffer buf not None, int repeats=4, double delay=0.5,
            double delay_rand=0.0, double pitch_shift=0.0,
            double gain_decay=1.0, unsigned int seed=0):
    """Iterate - repeat audio with variations.

    Creates multiple iterations/copies of the sound with optional pitch shift
    and amplitude decay. Each iteration can be delayed and modified.

    Args:
        buf: Input Buffer.
        repeats: Number of repetitions (1-100). Default 4.
        delay: Delay between iterations in seconds. Default 0.5.
        delay_rand: Random variation in delay (0.0 to 1.0). Default 0.0.
        pitch_shift: Max pitch shift per iteration in semitones (+/- range). Default 0.0.
        gain_decay: Amplitude decay per iteration (0.5 = halve each time, 1.0 = no decay). Default 1.0.
        seed: Random seed (0 = use time). Default 0.

    Returns:
        New Buffer with iterated audio.

    Raises:
        CDPError: If processing fails.
    """
    if repeats < 1 or repeats > 100:
        raise ValueError("repeats must be between 1 and 100")

    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_iterate(
            ctx, input_buf, repeats, delay, delay_rand, pitch_shift, gain_decay, seed)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Iterate processing failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def stutter(Buffer buf not None, double segment_ms=100.0, double duration=5.0,
            double silence_prob=0.2, double silence_min_ms=10.0,
            double silence_max_ms=100.0, double transpose_range=0.0,
            unsigned int seed=0):
    """Stutter - segment-based stuttering effect.

    Cuts audio into segments and randomly repeats/reorders them with
    optional silence insertions and transposition.

    Args:
        buf: Input Buffer.
        segment_ms: Average segment size in milliseconds. Default 100.0.
        duration: Output duration in seconds. Default 5.0.
        silence_prob: Probability of silence insert between segments (0.0 to 1.0). Default 0.2.
        silence_min_ms: Minimum silence duration in milliseconds. Default 10.0.
        silence_max_ms: Maximum silence duration in milliseconds. Default 100.0.
        transpose_range: Max transposition in semitones (+/- range, 0 = none). Default 0.0.
        seed: Random seed (0 = use time). Default 0.

    Returns:
        New Buffer with stutter effect.

    Raises:
        CDPError: If processing fails.
    """
    if segment_ms <= 0:
        raise ValueError("segment_ms must be positive")
    if duration <= 0:
        raise ValueError("duration must be positive")

    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_stutter(
            ctx, input_buf, segment_ms, duration, silence_prob,
            silence_min_ms, silence_max_ms, transpose_range, seed)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Stutter processing failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def bounce(Buffer buf not None, int bounces=8, double initial_delay=0.5,
           double shrink=0.7, double end_level=0.1, double level_curve=1.0,
           bint cut_bounces=False):
    """Bounce - bouncing ball effect.

    Creates accelerating repetitions of audio, like a bouncing ball
    with decreasing time between bounces and decreasing amplitude.

    Args:
        buf: Input Buffer.
        bounces: Number of bounces (1-100). Default 8.
        initial_delay: Initial delay between bounces in seconds. Default 0.5.
        shrink: How much to shrink delay each bounce (0.5 = halve, 0.9 = 10% shorter). Default 0.7.
        end_level: Final amplitude level (0.0 to 1.0). Default 0.1.
        level_curve: Level decay curve (1.0 = linear, <1 = fast decay, >1 = slow decay). Default 1.0.
        cut_bounces: If True, cut each bounce to fit; if False, allow overlap. Default False.

    Returns:
        New Buffer with bounce effect.

    Raises:
        CDPError: If processing fails.
    """
    if bounces < 1 or bounces > 100:
        raise ValueError("bounces must be between 1 and 100")
    if initial_delay <= 0:
        raise ValueError("initial_delay must be positive")
    if shrink <= 0 or shrink >= 1:
        raise ValueError("shrink must be between 0 and 1 (exclusive)")

    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_bounce(
            ctx, input_buf, bounces, initial_delay, shrink, end_level, level_curve, cut_bounces)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Bounce processing failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def drunk(Buffer buf not None, double duration=5.0, double step_ms=100.0,
          double step_rand=0.3, double locus=0.0, double ambitus=0.0,
          double overlap=0.1, double splice_ms=15.0, unsigned int seed=0):
    """Drunk - drunken walk random navigation through audio.

    Randomly navigates through the audio, taking random steps forward
    or backward within a configurable range (ambitus) around a center
    point (locus). Creates unpredictable, wandering playback.

    Args:
        buf: Input Buffer.
        duration: Output duration in seconds. Default 5.0.
        step_ms: Average step size in milliseconds. Default 100.0.
        step_rand: Random variation in step (0.0 to 1.0). Default 0.3.
        locus: Center position in seconds (navigation centers around this). Default 0.0.
               If 0, uses middle of the input.
        ambitus: Maximum range of movement in seconds. Default 0.0.
                 If 0, uses half the input duration.
        overlap: Overlap between segments (0.0 to 0.9). Default 0.1.
        splice_ms: Crossfade splice length in milliseconds. Default 15.0.
        seed: Random seed (0 = use time). Default 0.

    Returns:
        New Buffer with drunk walk playback.

    Raises:
        CDPError: If processing fails.
    """
    if duration <= 0:
        raise ValueError("duration must be positive")
    if step_ms <= 0:
        raise ValueError("step_ms must be positive")

    # Default locus to middle of input if not specified
    cdef double actual_locus = locus
    cdef double actual_ambitus = ambitus
    input_dur = buf.sample_count / buf.channels / buf.sample_rate
    if actual_locus <= 0:
        actual_locus = input_dur / 2
    if actual_ambitus <= 0:
        actual_ambitus = input_dur / 2

    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_drunk(
            ctx, input_buf, duration, step_ms, step_rand, actual_locus, actual_ambitus,
            overlap, splice_ms, seed)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Drunk processing failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def loop(Buffer buf not None, double start=0.0, double length_ms=500.0,
         double step_ms=0.0, double search_ms=0.0, int repeats=4,
         double splice_ms=15.0, unsigned int seed=0):
    """Loop - loop a section of audio with variations.

    Repeats a section of audio multiple times, optionally stepping
    through the file and adding random variation to loop start points.

    Args:
        buf: Input Buffer.
        start: Loop start position in seconds. Default 0.0.
        length_ms: Loop length in milliseconds. Default 500.0.
        step_ms: Step between loop iterations in milliseconds (0 = no stepping). Default 0.0.
        search_ms: Random search field for loop start in milliseconds (0 = no variation). Default 0.0.
        repeats: Number of loop repetitions (1-1000). Default 4.
        splice_ms: Crossfade splice length in milliseconds. Default 15.0.
        seed: Random seed (0 = use time). Default 0.

    Returns:
        New Buffer with looped audio.

    Raises:
        CDPError: If processing fails.
    """
    if length_ms <= 0:
        raise ValueError("length_ms must be positive")
    if repeats < 1 or repeats > 1000:
        raise ValueError("repeats must be between 1 and 1000")

    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_loop(
            ctx, input_buf, start, length_ms, step_ms, search_ms, repeats, splice_ms, seed)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Loop processing failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def retime(buf not None, double ratio=1.0, double grain_ms=50.0, double overlap=0.5):
    """
    Time-domain time stretching/compression using overlap-add (TDOLA).

    Changes the duration of audio without changing pitch using grain-based
    processing with crossfade blending for smooth results.

    Args:
        buf: Input audio buffer (Buffer or numpy array).
        ratio: Time ratio (default 1.0).
            - ratio < 1.0: Slower playback, longer output (0.5 = half speed, 2x duration)
            - ratio > 1.0: Faster playback, shorter output (2.0 = double speed, half duration)
        grain_ms: Grain size in milliseconds (default 50.0). Range: 5-500.
            Smaller grains preserve more detail but may sound choppy.
            Larger grains are smoother but may smear transients.
        overlap: Overlap factor between grains (default 0.5). Range: 0.1-0.9.
            Higher overlap = smoother but more processing.

    Returns:
        Buffer: New buffer with retimed audio.

    Raises:
        CDPError: If processing fails.
        ValueError: If parameters are out of valid range.

    Note:
        For high-quality time stretching with better transient preservation,
        consider using the spectral time_stretch function instead.

    Example:
        >>> # Slow down to half speed (2x duration)
        >>> result = cycdp.retime(audio, ratio=0.5)
        >>> # Speed up to double speed (half duration)
        >>> result = cycdp.retime(audio, ratio=2.0)
    """
    if ratio <= 0 or ratio > 10:
        raise ValueError("ratio must be > 0 and <= 10")
    if grain_ms < 5 or grain_ms > 500:
        raise ValueError("grain_ms must be between 5 and 500")
    if overlap < 0.1 or overlap > 0.9:
        raise ValueError("overlap must be between 0.1 and 0.9")

    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_retime(
            ctx, input_buf, ratio, grain_ms, overlap)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Retime processing failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


# Scramble mode constants
SCRAMBLE_SHUFFLE = 0
SCRAMBLE_REVERSE = 1
SCRAMBLE_SIZE_UP = 2
SCRAMBLE_SIZE_DOWN = 3
SCRAMBLE_LEVEL_UP = 4
SCRAMBLE_LEVEL_DOWN = 5


def scramble(buf not None, int mode=0, int group_size=2, unsigned int seed=0):
    """
    Reorder wavesets (zero-crossing segments) in audio.

    Detects wavesets based on zero crossings and reorders them according to
    the specified mode. Creates glitchy, granular, or sorted textures.

    Args:
        buf: Input audio buffer (Buffer or numpy array). Mono recommended;
            stereo uses left channel for waveset detection.
        mode: Reordering mode (default 0 = shuffle):
            - 0 (SCRAMBLE_SHUFFLE): Random order
            - 1 (SCRAMBLE_REVERSE): Reverse order
            - 2 (SCRAMBLE_SIZE_UP): Smallest to largest (rising pitch effect)
            - 3 (SCRAMBLE_SIZE_DOWN): Largest to smallest (falling pitch effect)
            - 4 (SCRAMBLE_LEVEL_UP): Quietest to loudest
            - 5 (SCRAMBLE_LEVEL_DOWN): Loudest to quietest
        group_size: Number of half-cycles per waveset group (default 2). Range: 1-64.
            Larger values create longer segments.
        seed: Random seed for shuffle mode (default 0 = use time).

    Returns:
        Buffer: New buffer with scrambled wavesets.

    Raises:
        CDPError: If processing fails.
        ValueError: If parameters are out of valid range.

    Example:
        >>> # Shuffle wavesets randomly
        >>> result = cycdp.scramble(audio, mode=cycdp.SCRAMBLE_SHUFFLE)
        >>> # Sort by size for rising pitch effect
        >>> result = cycdp.scramble(audio, mode=cycdp.SCRAMBLE_SIZE_UP)
    """
    if mode < 0 or mode > 5:
        raise ValueError("mode must be 0-5")
    if group_size < 1 or group_size > 64:
        raise ValueError("group_size must be between 1 and 64")

    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_scramble(
            ctx, input_buf, mode, group_size, seed)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Scramble processing failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def splinter(buf not None, double start=0.0, double duration_ms=50.0, int repeats=20,
             double min_shrink=0.1, double shrink_curve=1.0, double accel=1.5,
             unsigned int seed=0):
    """
    Create fragmenting/splintering effect by shrinking and repeating a segment.

    Takes a segment of audio and creates a "splintering" effect by progressively
    shrinking and repeating it. Creates percussive, granular fragmentation effects.

    Args:
        buf: Input audio buffer (Buffer or numpy array).
        start: Start position of segment to splinter in seconds (default 0.0).
        duration_ms: Duration of segment in milliseconds (default 50.0). Range: 5-5000.
        repeats: Number of repetitions (default 20). Range: 2-500.
        min_shrink: Minimum shrinkage factor at end (default 0.1). Range: 0.01-1.0.
            0.1 = final segment is 10% of original length.
        shrink_curve: Shrinkage curve (default 1.0). Range: 0.1-10.0.
            1.0 = linear shrinkage
            >1 = faster shrink at start, slower at end
            <1 = slower shrink at start, faster at end
        accel: Acceleration of repetition rate (default 1.5). Range: 0.5-4.0.
            >1 = repetitions get faster (accelerando)
            <1 = repetitions get slower (decelerando)
            1.0 = constant rate
        seed: Random seed (default 0 = use time, >0 for reproducible results).

    Returns:
        Buffer: New buffer with splintered audio.

    Raises:
        CDPError: If processing fails.
        ValueError: If parameters are out of valid range.

    Example:
        >>> # Create splintering effect from start of audio
        >>> result = cycdp.splinter(audio, start=0.5, duration_ms=100, repeats=30)
        >>> # Aggressive splintering with fast shrinkage
        >>> result = cycdp.splinter(audio, duration_ms=50, repeats=50, min_shrink=0.05)
    """
    if duration_ms < 5 or duration_ms > 5000:
        raise ValueError("duration_ms must be between 5 and 5000")
    if repeats < 2 or repeats > 500:
        raise ValueError("repeats must be between 2 and 500")
    if min_shrink < 0.01 or min_shrink > 1.0:
        raise ValueError("min_shrink must be between 0.01 and 1.0")
    if shrink_curve < 0.1 or shrink_curve > 10.0:
        raise ValueError("shrink_curve must be between 0.1 and 10.0")
    if accel < 0.5 or accel > 4.0:
        raise ValueError("accel must be between 0.5 and 4.0")

    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_splinter(
            ctx, input_buf, start, duration_ms, repeats, min_shrink, shrink_curve, accel, seed)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Splinter processing failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def spin(buf, double rate=1.0, double doppler=0.0, double depth=1.0):
    """
    Rotate audio around the stereo field.

    Creates a spinning/rotating spatial effect by continuously panning audio
    around the stereo field. Can include doppler pitch shift for realism.

    Args:
        buf: Input audio buffer (mono or stereo)
        rate: Spin rate in Hz (cycles per second). Range: -20 to +20.
              Positive = clockwise, negative = counterclockwise.
        doppler: Doppler pitch shift in semitones (0-12, default 0).
                 0 = no doppler, higher values = more pitch variation.
        depth: Depth of panning (0.0 to 1.0, default 1.0).
               1.0 = full rotation, 0.5 = partial rotation.

    Returns:
        Buffer: New stereo buffer with spinning audio.
    """
    if rate < -20.0 or rate > 20.0:
        raise ValueError("rate must be between -20 and 20 Hz")
    if doppler < 0.0 or doppler > 12.0:
        raise ValueError("doppler must be between 0 and 12 semitones")
    if depth < 0.0 or depth > 1.0:
        raise ValueError("depth must be between 0.0 and 1.0")

    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_spin(ctx, input_buf, rate, doppler, depth)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Spin processing failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def rotor(buf, double pitch_rate=1.0, double pitch_depth=2.0,
          double amp_rate=1.5, double amp_depth=0.5, double phase_offset=0.0):
    """
    Apply dual-rotation modulation effect.

    Applies two independent rotating modulators (pitch and amplitude) with
    different cycle lengths, creating evolving interference patterns.
    Inspired by CDP's rotor synthesis concept of rotating "armatures".

    Args:
        buf: Input audio buffer
        pitch_rate: Pitch modulation rate in Hz (0.01 to 20). Cycles per second.
        pitch_depth: Pitch modulation depth in semitones (0 to 12).
        amp_rate: Amplitude modulation rate in Hz (0.01 to 20). Cycles per second.
        amp_depth: Amplitude modulation depth (0.0 to 1.0). 1.0 = full tremolo.
        phase_offset: Initial phase offset between modulators (0.0 to 1.0).
                      Creates different interference patterns.

    Returns:
        Buffer: New buffer with rotor modulation.

    Note: When pitch_rate and amp_rate have different values, the combined
    effect creates evolving patterns that cycle through various combinations
    of pitch and amplitude modulation over time.
    """
    if pitch_rate < 0.01 or pitch_rate > 20.0:
        raise ValueError("pitch_rate must be between 0.01 and 20 Hz")
    if pitch_depth < 0.0 or pitch_depth > 12.0:
        raise ValueError("pitch_depth must be between 0 and 12 semitones")
    if amp_rate < 0.01 or amp_rate > 20.0:
        raise ValueError("amp_rate must be between 0.01 and 20 Hz")
    if amp_depth < 0.0 or amp_depth > 1.0:
        raise ValueError("amp_depth must be between 0.0 and 1.0")
    if phase_offset < 0.0 or phase_offset > 1.0:
        raise ValueError("phase_offset must be between 0.0 and 1.0")

    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_rotor(
            ctx, input_buf, pitch_rate, pitch_depth, amp_rate, amp_depth, phase_offset)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Rotor processing failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def synth_wave(int waveform=WAVE_SINE, double frequency=440.0, double amplitude=0.8,
               double duration=1.0, int sample_rate=44100, int channels=1):
    """
    Generate basic waveforms.

    Synthesizes standard waveforms (sine, square, saw, ramp, triangle)
    at specified frequency and duration.

    Args:
        waveform: Waveform type (WAVE_SINE, WAVE_SQUARE, WAVE_SAW, WAVE_RAMP, WAVE_TRIANGLE)
        frequency: Frequency in Hz (20 to 20000)
        amplitude: Peak amplitude (0.0 to 1.0)
        duration: Duration in seconds (0.001 to 3600)
        sample_rate: Sample rate in Hz (default 44100)
        channels: Number of output channels (1 or 2)

    Returns:
        Buffer: New buffer with synthesized waveform.
    """
    if waveform < 0 or waveform > 4:
        raise ValueError("waveform must be 0-4 (WAVE_SINE, WAVE_SQUARE, WAVE_SAW, WAVE_RAMP, WAVE_TRIANGLE)")
    if frequency < 20.0 or frequency > 20000.0:
        raise ValueError("frequency must be between 20 and 20000 Hz")
    if amplitude < 0.0 or amplitude > 1.0:
        raise ValueError("amplitude must be between 0.0 and 1.0")
    if duration < 0.001 or duration > 3600.0:
        raise ValueError("duration must be between 0.001 and 3600 seconds")
    if sample_rate < 8000 or sample_rate > 192000:
        raise ValueError("sample_rate must be between 8000 and 192000")
    if channels < 1 or channels > 2:
        raise ValueError("channels must be 1 or 2")

    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_synth_wave(
            ctx, waveform, frequency, amplitude, duration, sample_rate, channels)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Synth wave failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def synth_noise(int pink=0, double amplitude=0.8, double duration=1.0,
                int sample_rate=44100, int channels=1, unsigned int seed=0):
    """
    Generate noise.

    Synthesizes white or pink noise at specified duration.

    Args:
        pink: If non-zero, generate pink noise; otherwise white noise
        amplitude: Peak amplitude (0.0 to 1.0)
        duration: Duration in seconds (0.001 to 3600)
        sample_rate: Sample rate in Hz (default 44100)
        channels: Number of output channels (1 or 2)
        seed: Random seed (0 = use time)

    Returns:
        Buffer: New buffer with synthesized noise.
    """
    if amplitude < 0.0 or amplitude > 1.0:
        raise ValueError("amplitude must be between 0.0 and 1.0")
    if duration < 0.001 or duration > 3600.0:
        raise ValueError("duration must be between 0.001 and 3600 seconds")
    if sample_rate < 8000 or sample_rate > 192000:
        raise ValueError("sample_rate must be between 8000 and 192000")
    if channels < 1 or channels > 2:
        raise ValueError("channels must be 1 or 2")

    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_synth_noise(
            ctx, pink, amplitude, duration, sample_rate, channels, seed)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Synth noise failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def synth_click(double tempo=120.0, int beats_per_bar=4, double duration=10.0,
                double click_freq=1000.0, double click_dur_ms=10.0, int sample_rate=44100):
    """
    Generate click track.

    Synthesizes a click track at specified tempo and duration.

    Args:
        tempo: Tempo in BPM (20 to 400)
        beats_per_bar: Beats per bar for accent pattern (1 to 16, 0 = no accent)
        duration: Duration in seconds (0.1 to 3600)
        click_freq: Click frequency in Hz (200 to 8000, default 1000)
        click_dur_ms: Click duration in milliseconds (1 to 100, default 10)
        sample_rate: Sample rate in Hz (default 44100)

    Returns:
        Buffer: New mono buffer with click track.
    """
    if tempo < 20.0 or tempo > 400.0:
        raise ValueError("tempo must be between 20 and 400 BPM")
    if beats_per_bar < 0 or beats_per_bar > 16:
        raise ValueError("beats_per_bar must be between 0 and 16")
    if duration < 0.1 or duration > 3600.0:
        raise ValueError("duration must be between 0.1 and 3600 seconds")
    if click_freq < 200.0 or click_freq > 8000.0:
        raise ValueError("click_freq must be between 200 and 8000 Hz")
    if click_dur_ms < 1.0 or click_dur_ms > 100.0:
        raise ValueError("click_dur_ms must be between 1 and 100 milliseconds")
    if sample_rate < 8000 or sample_rate > 192000:
        raise ValueError("sample_rate must be between 8000 and 192000")

    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_synth_click(
            ctx, tempo, beats_per_bar, duration, click_freq, click_dur_ms, sample_rate)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Synth click failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def synth_chord(midi_notes, double amplitude=0.8, double duration=1.0,
                double detune_cents=0.0, int sample_rate=44100, int channels=1):
    """
    Generate chord from MIDI notes.

    Synthesizes a chord by mixing multiple sine waves at specified MIDI pitches.
    Includes optional detuning for a richer, more natural sound.

    Args:
        midi_notes: List/tuple of MIDI note numbers (0-127, where 60 = middle C)
                    Common notes: C4=60, D4=62, E4=64, F4=65, G4=67, A4=69, B4=71
        amplitude: Peak amplitude (0.0 to 1.0)
        duration: Duration in seconds (0.001 to 3600)
        detune_cents: Detuning amount in cents (0-50, default 0)
                      Adds slight pitch variations between notes for richer sound
        sample_rate: Sample rate in Hz (default 44100)
        channels: Number of output channels (1 or 2)

    Returns:
        Buffer: New buffer with synthesized chord.

    Example:
        # C major chord (C4, E4, G4)
        chord = synth_chord([60, 64, 67], duration=2.0)

        # A minor chord with detuning for richer sound
        chord = synth_chord([69, 72, 76], detune_cents=5.0)
    """
    # Convert input to list if needed
    if not hasattr(midi_notes, '__len__'):
        midi_notes = [midi_notes]

    cdef int num_notes = len(midi_notes)
    if num_notes < 1 or num_notes > 16:
        raise ValueError("midi_notes must contain 1 to 16 notes")
    if amplitude < 0.0 or amplitude > 1.0:
        raise ValueError("amplitude must be between 0.0 and 1.0")
    if duration < 0.001 or duration > 3600.0:
        raise ValueError("duration must be between 0.001 and 3600 seconds")
    if detune_cents < 0.0 or detune_cents > 50.0:
        raise ValueError("detune_cents must be between 0 and 50")
    if sample_rate < 8000 or sample_rate > 192000:
        raise ValueError("sample_rate must be between 8000 and 192000")
    if channels < 1 or channels > 2:
        raise ValueError("channels must be 1 or 2")

    # Validate MIDI note range
    for note in midi_notes:
        if note < 0 or note > 127:
            raise ValueError("MIDI notes must be between 0 and 127")

    # Convert to C array
    cdef double[16] notes_array
    cdef int i
    for i in range(num_notes):
        notes_array[i] = float(midi_notes[i])

    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_synth_chord(
            ctx, notes_array, num_notes, amplitude, duration, detune_cents, sample_rate, channels)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Synth chord failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


# =============================================================================
# Pitch-Synchronous Operations (PSOW)
# =============================================================================

def psow_stretch(Buffer buf not None, double stretch_factor=1.0, int grain_count=1):
    """Time-stretch audio while preserving pitch using PSOLA.

    Uses pitch-synchronous overlap-add to stretch or compress time without
    affecting pitch. Grains are extracted at pitch-period boundaries and
    repositioned with overlap-add.

    Args:
        buf: Input Buffer (mono or stereo, will be converted to mono).
        stretch_factor: Time stretch ratio (0.25 to 4.0). Default 1.0.
                        0.5 = half duration, 2.0 = double duration.
        grain_count: Number of consecutive grains to keep together (1-8).
                     Higher values preserve more local coherence. Default 1.

    Returns:
        New mono Buffer with time-stretched audio.

    Raises:
        CDPError: If processing fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_psow_stretch(
            ctx, input_buf, stretch_factor, grain_count
        )

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "PSOW stretch failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def psow_grab(Buffer buf not None, double time=0.0, double duration=0.0,
              int grain_count=1, double density=1.0):
    """Extract pitch-synchronous grains from a position in the audio.

    Grabs one or more pitch periods (grains/FOFs) from the specified time
    position and optionally extends them to create a sustained sound.

    Args:
        buf: Input Buffer (mono or stereo, will be converted to mono).
        time: Time in seconds at which to grab grains. Default 0.0.
        duration: Output duration in seconds. Default 0 (grab single grain).
        grain_count: Number of consecutive grains to grab (1-16). Default 1.
        density: Overlap density for output (0.25 to 4.0). Default 1.0.
                 1.0 = grains follow without overlap
                 2.0 = grains overlap by 2x (can transpose up octave)
                 0.5 = grains separated by gaps

    Returns:
        New mono Buffer with grabbed/extended grains.

    Raises:
        CDPError: If processing fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_psow_grab(
            ctx, input_buf, time, duration, grain_count, density
        )

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "PSOW grab failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def psow_dupl(Buffer buf not None, int repeat_count=2, int grain_count=1):
    """Duplicate pitch-synchronous grains for time-stretching.

    Time-stretches by repeating each group of grains a specified number of times.
    Creates a more rhythmic/stuttered stretch than psow_stretch.

    Args:
        buf: Input Buffer (mono or stereo, will be converted to mono).
        repeat_count: Number of times to repeat each grain group (1-8). Default 2.
        grain_count: Number of grains per group (1-8). Default 1.

    Returns:
        New mono Buffer with duplicated grains.

    Raises:
        CDPError: If processing fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_psow_dupl(
            ctx, input_buf, repeat_count, grain_count
        )

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "PSOW dupl failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def psow_interp(Buffer grain1 not None, Buffer grain2 not None,
                double start_dur=0.1, double interp_dur=0.5, double end_dur=0.1):
    """Interpolate between two pitch-synchronous grains.

    Creates a morphing sound by interpolating between two single-grain sounds.
    Input sounds should ideally be single grains extracted using psow_grab
    with duration=0.

    Args:
        grain1: First grain Buffer (single pitch period).
        grain2: Second grain Buffer (single pitch period).
        start_dur: Duration to sustain initial grain (seconds). Default 0.1.
        interp_dur: Duration of interpolation (seconds). Default 0.5.
        end_dur: Duration to sustain final grain (seconds). Default 0.1.

    Returns:
        New mono Buffer with interpolated result.

    Raises:
        CDPError: If processing fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* grain1_buf
    cdef cdp_lib_buffer* grain2_buf
    _buffer_pair_to_cdp_lib(grain1, grain2, &grain1_buf, &grain2_buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_psow_interp(
            ctx, grain1_buf, grain2_buf, start_dur, interp_dur, end_dur
        )

    cdp_lib_buffer_free(grain1_buf)
    cdp_lib_buffer_free(grain2_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "PSOW interp failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


# =============================================================================
# FOF Extraction and Synthesis (FOFEX)
# =============================================================================

def fofex_extract(Buffer buf not None, double time, int fof_count=1, bint window=True):
    """
    Extract a single FOF (pitch-synchronous grain) at a specified time.

    FOFs (Formant Wave Functions) are pitch-synchronous grains that can be
    used for formant-preserving pitch manipulation.

    Args:
        buf: Input Buffer (mono or stereo, will be converted to mono).
        time: Time in seconds at which to extract the FOF.
        fof_count: Number of pitch periods to include (1-8). Default 1.
        window: If True, apply raised cosine window to FOF edges. Default True.

    Returns:
        New mono Buffer containing the extracted FOF.

    Raises:
        CDPError: If extraction fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_fofex_extract(
            ctx, input_buf, time, fof_count, 1 if window else 0
        )

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "FOFEX extract failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def fofex_extract_all(Buffer buf not None, int fof_count=1, double min_level_db=0.0,
                      bint window=True):
    """
    Extract all FOFs from audio file.

    Analyzes the entire file and extracts all pitch-synchronous FOFs,
    returning them as a bank (concatenated buffer with uniform-length FOFs).

    Args:
        buf: Input Buffer (mono or stereo, will be converted to mono).
        fof_count: Number of pitch periods per FOF (1-4). Default 1.
        min_level_db: Minimum level in dB below peak to accept FOFs.
                      0 = keep all, -40 = reject quiet FOFs. Default 0.
        window: If True, apply raised cosine window to FOF edges. Default True.

    Returns:
        Tuple of (Buffer, num_fofs, unit_length):
            Buffer: Contains all FOFs concatenated (each zero-padded to uniform length).
            num_fofs: Number of FOFs extracted.
            unit_length: Samples per FOF unit.

    Raises:
        CDPError: If extraction fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)
    cdef int fof_info[2]
    fof_info[0] = 0
    fof_info[1] = 0

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_fofex_extract_all(
            ctx, input_buf, fof_count, min_level_db, 1 if window else 0, fof_info
        )

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "FOFEX extract_all failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return (result, fof_info[0], fof_info[1])


def fofex_synth(Buffer fof_bank not None, double duration, double frequency,
                double amplitude=0.8, int fof_index=-1, int fof_unit_len=0):
    """
    Synthesize audio using extracted FOFs.

    Creates new audio by repeating FOFs at a specified pitch frequency.

    Args:
        fof_bank: FOF Buffer (single FOF or bank from fofex_extract_all).
        duration: Output duration in seconds.
        frequency: Target pitch frequency in Hz (20-5000).
        amplitude: Output amplitude (0.0-1.0). Default 0.8.
        fof_index: Which FOF to use if fof_bank is a bank.
                   -1 = average all FOFs, 0+ = specific FOF index. Default -1.
        fof_unit_len: Samples per FOF if fof_bank is a bank (from fofex_extract_all).
                      0 = fof_bank is a single FOF. Default 0.

    Returns:
        New mono Buffer with synthesized audio.

    Raises:
        CDPError: If synthesis fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* fof_buf = _buffer_to_cdp_lib(fof_bank)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_fofex_synth(
            ctx, fof_buf, duration, frequency, amplitude, fof_index, fof_unit_len
        )

    cdp_lib_buffer_free(fof_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "FOFEX synth failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


def fofex_repitch(Buffer buf not None, double pitch_shift, bint preserve_formants=True):
    """
    Resynthesize audio with modified pitch using FOFs.

    Pitch-shifts audio while optionally preserving formant characteristics.

    Args:
        buf: Input Buffer (mono or stereo, will be converted to mono).
        pitch_shift: Pitch shift in semitones (-24 to +24).
        preserve_formants: If True, formants are preserved (PSOLA-style).
                          If False, formants shift with pitch (resampling).
                          Default True.

    Returns:
        New mono Buffer with pitch-shifted audio.

    Raises:
        CDPError: If processing fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_fofex_repitch(
            ctx, input_buf, pitch_shift, 1 if preserve_formants else 0
        )

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "FOFEX repitch failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


# =============================================================================
# Flutter - Spatial Tremolo Effect
# =============================================================================

def flutter(Buffer buf not None, double frequency=4.0, double depth=1.0,
            double gain=1.0, bint randomize=False):
    """
    Apply flutter (spatial tremolo) effect.

    Creates a tremolo that alternates between left and right channels,
    producing a spatial movement effect where the sound appears to
    move between speakers.

    Args:
        buf: Input Buffer (mono or stereo). Mono is converted to stereo.
        frequency: Tremolo frequency in Hz (0.1 to 50.0). Default 4.0.
        depth: Tremolo depth (0.0 to 16.0). Default 1.0.
               1.0 = full depth (troughs reach silence).
               >1.0 = narrower peaks, sharper transitions.
        gain: Overall output gain (0.0 to 1.0). Default 1.0.
        randomize: If True, randomize L/R order after each cycle. Default False.

    Returns:
        New stereo Buffer with flutter effect applied.

    Raises:
        CDPError: If processing fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_flutter(
            ctx, input_buf, frequency, depth, gain, 1 if randomize else 0
        )

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Flutter failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


# =============================================================================
# Hover - Zigzag Reading / Pitch Hovering Effect
# =============================================================================

def hover(Buffer buf not None, double frequency=440.0, double location=0.5,
          double frq_rand=0.1, double loc_rand=0.1, double splice_ms=1.0,
          double duration=0.0):
    """
    Apply hover effect - zigzag reading at specified frequency.

    Reads through the audio file with a zigzag motion at the specified
    frequency, creating a hovering or vibrato-like pitch effect.

    At each oscillation cycle:
    - Reads forward (zig) for some samples
    - Then reads backward (zag) for the remainder
    - Applies crossfade splicing at the boundaries

    The zigzag width is determined by frequency:
    - At 44100 Hz sample rate and 1 Hz frequency: reads 22050 samples
      forward then 22050 back
    - At 10 Hz: reads 2205 forward and 2205 back

    Args:
        buf: Input Buffer (must be mono).
        frequency: Rate of zigzag oscillation in Hz (0.1 to 1000.0). Default 440.0.
                   Lower values create wider zigzag reads (slower pitch wobble).
                   Higher values create narrower reads (faster pitch wobble).
        location: Position in source file, normalized (0.0 to 1.0). Default 0.5.
                  0.0 = start, 0.5 = middle, 1.0 = end.
        frq_rand: Random variation of frequency (0.0 to 1.0). Default 0.1.
        loc_rand: Random variation of location (0.0 to 1.0). Default 0.1.
        splice_ms: Splice length at zig/zag boundaries in milliseconds (0.1 to 100.0).
                   Default 1.0.
        duration: Output duration in seconds. Default 0.0 (same as input).

    Returns:
        New mono Buffer with hover effect applied.

    Raises:
        CDPError: If processing fails (e.g., non-mono input).
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_hover(
            ctx, input_buf, frequency, location, frq_rand, loc_rand, splice_ms, duration
        )

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Hover failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


# =============================================================================
# Constrict - Silence Constriction Effect
# =============================================================================

def constrict(Buffer buf not None, double constriction=50.0):
    """
    Apply constrict effect - shorten or remove silent sections.

    Scans through audio looking for zero-value samples and reduces
    or removes these silent sections based on the constriction parameter.

    This is useful for tightening up audio with pauses or for creative
    effects where sounds are pushed together.

    Args:
        buf: Input Buffer.
        constriction: Percentage of silence removal (0.0 to 200.0). Default 50.0.
                      0-100: Shorten zero-sections by that percentage.
                             e.g., 50 = silences are 50% shorter
                             0 = no change, 100 = silences removed entirely
                      100-200: Overlap sounds on either side of silence.
                               Sounds merge/blend together.
                               e.g., 150 = 50% overlap of adjacent sounds

    Returns:
        New Buffer with constricted silences.

    Raises:
        CDPError: If processing fails.
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_constrict(ctx, input_buf, constriction)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Constrict failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


# =============================================================================
# Phase - Phase Manipulation Effects
# =============================================================================

def phase_invert(samples not None, int sample_rate=44100):
    """
    Invert the phase of an audio signal.

    Multiplies all samples by -1, effectively flipping the waveform
    upside down. This is sometimes called "polarity inversion".

    Phase inversion is useful for:
    - Correcting out-of-phase recordings
    - Creative sound design
    - Combining with the original to cancel common elements

    Accepts either a Buffer or any float32 buffer (numpy array,
    array.array('f'), memoryview), matching the other high-level
    functions such as gain() and normalize().

    Args:
        samples: Input Buffer, or any float32 buffer (mono or stereo).
        sample_rate: Sample rate in Hz. Only used when samples is a raw
            buffer; ignored when samples is a Buffer, which carries its own.

    Returns:
        New Buffer with inverted phase.

    Raises:
        CDPError: If processing fails.
    """
    cdef cdp_lib_ctx* ctx
    cdef cdp_lib_buffer* input_buf
    cdef cdp_lib_buffer* output_buf
    cdef Buffer result
    cdef Context py_ctx

    if not isinstance(samples, Buffer):
        # Raw float32 buffer: convert and invert in place via libcdp.
        py_ctx = Context()
        result = Buffer.from_memoryview(samples, 1, sample_rate)
        apply_phase_invert(py_ctx, result)
        return result

    ctx = _get_cdp_lib_ctx()
    input_buf = _buffer_to_cdp_lib(<Buffer>samples)

    with nogil:
        output_buf = cdp_lib_phase_invert(ctx, input_buf)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Phase invert failed")

    result = _take_cdp_lib_buffer(output_buf)

    return result


def phase_stereo(Buffer buf not None, double transfer=1.0):
    """
    Enhance stereo separation using phase subtraction.

    Enhances the stereo image by subtracting a portion of each channel
    from the other, emphasizing differences between left and right.

    For each sample pair:
      newLeft = L - (transfer * R)
      newRight = R - (transfer * L)

    This removes elements that are identical in both channels (centered
    sounds) while preserving elements that differ between channels
    (panned sounds). The output is automatically normalized to preserve
    the original maximum level.

    Args:
        buf: Input Buffer (must be stereo).
        transfer: Amount of signal used in phase-cancellation (0.0 to 1.0).
                  Default 1.0.
                  0 = no change (passthrough)
                  1 = maximum stereo enhancement (full cancellation)

    Returns:
        New stereo Buffer with enhanced separation.

    Raises:
        CDPError: If processing fails (e.g., non-stereo input).
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_phase_stereo(ctx, input_buf, transfer)

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Phase stereo failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result


# =============================================================================
# Wrappage - Granular Texture with Spatial Distribution
# =============================================================================

def wrappage(Buffer buf not None, double grain_size=50.0, double density=1.0,
             double velocity=1.0, double pitch=0.0, double spread=1.0,
             double jitter=0.1, double splice_ms=5.0, double duration=0.0,
             unsigned int seed=0):
    """
    Apply wrappage effect - granular texture with stereo spatial distribution.

    Extracts grains from the input and redistributes them spatially across
    a stereo field, creating textural transformations with optional time
    stretching and pitch shifting.

    This is useful for:
    - Creating dense, cloud-like textures from sounds
    - Time stretching while maintaining pitch
    - Pitch shifting while maintaining duration
    - Spatial widening of mono sources
    - Freeze effects (velocity=0)

    Args:
        buf: Input Buffer (must be mono).
        grain_size: Size of each grain in milliseconds (1.0 to 500.0). Default 50.0.
        density: Grain overlap factor (0.1 to 10.0). Default 1.0.
                 <1 = gaps between grains (sparse texture)
                 1 = grains touch but don't overlap
                 >1 = overlapping grains (dense texture)
        velocity: Speed of advance through input (0.0 to 10.0). Default 1.0.
                  <1 = time stretch (slower)
                  1 = normal speed
                  >1 = time compress (faster)
                  0 = freeze (requires duration parameter)
        pitch: Pitch shift in semitones (-24.0 to 24.0). Default 0.0.
        spread: Stereo spread (0.0 to 1.0). Default 1.0.
                0 = mono center
                1 = full stereo spread (grains randomly distributed L/R)
        jitter: Random variation of grain position (0.0 to 1.0). Default 0.1.
                Adds natural variation to prevent mechanical artifacts.
        splice_ms: Splice length at grain boundaries in ms (0.5 to 50.0). Default 5.0.
                   Longer splices = smoother but less defined grains.
        duration: Output duration in seconds. Default 0.0 (automatic).
                  Must be specified if velocity is 0.
        seed: Random seed (default 0 = derive from the clock, >0 for
              reproducible results), matching the other granular operations.

    Returns:
        New stereo Buffer with wrappage effect applied.

    Raises:
        CDPError: If processing fails (e.g., non-mono input, invalid parameters).
    """
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    cdef cdp_lib_buffer* output_buf
    with nogil:
        output_buf = cdp_lib_wrappage(
            ctx, input_buf, grain_size, density, velocity, pitch,
            spread, jitter, splice_ms, duration, seed
        )

    cdp_lib_buffer_free(input_buf)

    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Wrappage failed")

    cdef Buffer result = _take_cdp_lib_buffer(output_buf)

    return result