# CDP Algorithm Integration Guide

This guide explains how to convert CDP algorithms into native library functions for use with libcdp and cycdp.

## Overview

The goal is to implement CDP audio processing algorithms as native C functions that:

- Operate directly on memory buffers (no file I/O)

- Have zero subprocess overhead

- Are accessible from Python via Cython bindings

## Architecture

```text
┌─────────────────────────────────────────────────────────────┐
│                        Python API                           │
│                    cycdp/__init__.py                        │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                     Cython Wrappers                         │
│                   cycdp/_core.pyx                           │
│              (Buffer conversion, error handling)            │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    C Library (cdp_lib)                      │
│    cdp_lib.c, cdp_spectral.c, cdp_envelope.c, etc.          │
│              (Native algorithm implementations)             │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    CDP Infrastructure                       │
│              mxfft.c (FFT), cdp_shim.c (compat)             │
└─────────────────────────────────────────────────────────────┘
```

## Step-by-Step Process

### Step 1: Understand the Algorithm

Before implementing, understand what the CDP algorithm does:

1. Find the original CDP source (usually in `dev/` directory)

2. Identify the core algorithm (ignore file I/O, command-line parsing)

3. Determine input/output requirements

4. Note any parameters and their valid ranges

### Step 2: Add C Declaration (cdp_lib.h)

Add the function declaration to `cdp_lib/cdp_lib.h`:

```c
/*
 * Brief description of what the function does.
 *

 * Args:

 *   ctx: Library context

 *   input: Input audio buffer

 *   param1: Description of parameter

 *   param2: Description of parameter
 *

 * Returns: New buffer with processed audio, or NULL on error.
 */
cdp_lib_buffer* cdp_lib_my_effect(cdp_lib_ctx* ctx,
                                   const cdp_lib_buffer* input,
                                   double param1,
                                   int param2);
```

**Conventions:**

- All functions take `cdp_lib_ctx*` as first parameter

- Input buffers are `const cdp_lib_buffer*`

- Return a newly allocated buffer (caller frees)

- Return NULL on error (set error message in ctx)

### Step 3: Implement in C (cdp_lib.c or new file)

#### Basic Structure

```c
cdp_lib_buffer* cdp_lib_my_effect(cdp_lib_ctx* ctx,
                                   const cdp_lib_buffer* input,
                                   double param1,
                                   int param2) {
    // 1. Validate inputs
    if (ctx == NULL || input == NULL) {
        return NULL;
    }

    if (param1 <= 0) {
        snprintf(ctx->error_msg, sizeof(ctx->error_msg),
                 "param1 must be positive");
        return NULL;
    }

    // 2. Allocate output buffer
    cdp_lib_buffer *output = cdp_lib_buffer_create(
        input->length, input->channels, input->sample_rate);

    if (output == NULL) {
        snprintf(ctx->error_msg, sizeof(ctx->error_msg),
                 "Failed to allocate output buffer");
        return NULL;
    }

    // 3. Process audio
    for (size_t i = 0; i < input->length; i++) {
        output->data[i] = process_sample(input->data[i], param1, param2);
    }

    // 4. Return result
    return output;
}
```

#### For Spectral Processing

Use the spectral analysis/synthesis infrastructure:

```c
cdp_lib_buffer* cdp_lib_spectral_effect(cdp_lib_ctx* ctx,
                                         const cdp_lib_buffer* input,
                                         double param,
                                         int fft_size) {
    if (ctx == NULL || input == NULL) {
        return NULL;
    }

    if (fft_size == 0) fft_size = 1024;  // Default

    // 1. Analyze input (STFT)
    cdp_spectral_data *spectral = cdp_spectral_analyze(
        input->data, input->length,
        input->channels, input->sample_rate,
        fft_size, 3);  // overlap = 3 (75%)

    if (spectral == NULL) {
        snprintf(ctx->error_msg, sizeof(ctx->error_msg),
                 "Spectral analysis failed");
        return NULL;
    }

    // 2. Process spectral data
    int num_bins = spectral->num_bins;
    float freq_per_bin = (float)input->sample_rate / fft_size;

    for (int f = 0; f < spectral->num_frames; f++) {
        float *amp = spectral->frames[f].data;
        float *freq = spectral->frames[f].data + num_bins;

        for (int b = 0; b < num_bins; b++) {
            float bin_freq = b * freq_per_bin;
            // Modify amp[b] and/or freq[b] as needed
            amp[b] *= some_function(bin_freq, param);
        }
    }

    // 3. Synthesize back to audio (ISTFT)
    size_t out_samples;
    float *audio = cdp_spectral_synthesize(spectral, &out_samples);
    cdp_spectral_data_free(spectral);

    if (audio == NULL) {
        snprintf(ctx->error_msg, sizeof(ctx->error_msg),
                 "Spectral synthesis failed");
        return NULL;
    }

    // 4. Create output buffer
    cdp_lib_buffer *output = cdp_lib_buffer_from_data(
        audio, out_samples, 1, input->sample_rate);

    if (output == NULL) {
        free(audio);
        snprintf(ctx->error_msg, sizeof(ctx->error_msg),
                 "Failed to create output buffer");
        return NULL;
    }

    return output;
}
```

#### Spectral Data Structure

```c
typedef struct {
    float *data;        // First half: amplitudes, second half: frequencies
    int num_bins;       // Number of frequency bins (fft_size/2 + 1)
    int fft_size;
    float sample_rate;
} cdp_spectral_frame;

typedef struct {
    cdp_spectral_frame *frames;
    int num_frames;
    int num_bins;
    int fft_size;
    int overlap;
    float sample_rate;
    float frame_time;   // Time between frames in seconds
} cdp_spectral_data;
```

**Important:** For filtering, use bin center frequency (`b * freq_per_bin`), not instantaneous frequency (`freq[b]`). The instantaneous frequency is useful for phase vocoder time-stretching but unreliable for filtering decisions.

### Step 4: Add to CMakeLists.txt (if new file)

If you created a new source file, add it to `cycdp/CMakeLists.txt`:

```cmake
set(CDP_LIB_SOURCES
    ${LIBCDP_DIR}/cdp_lib/cdp_lib.c
    ${LIBCDP_DIR}/cdp_lib/cdp_spectral.c
    ${LIBCDP_DIR}/cdp_lib/cdp_envelope.c
    ${LIBCDP_DIR}/cdp_lib/cdp_my_new_module.c  # Add here
    ...
)
```

### Step 5: Add Cython Declaration (cdp_lib.pxd)

Add to `cycdp/src/cycdp/cdp_lib.pxd`:

```cython
cdef extern from "cdp_lib.h":
    # ... existing declarations ...

    cdp_lib_buffer* cdp_lib_my_effect(cdp_lib_ctx* ctx,
                                       const cdp_lib_buffer* input,
                                       double param1,
                                       int param2)
```

### Step 6: Add Cython Declaration in _core.pyx

Also add the declaration inline in `cycdp/src/cycdp/_core.pyx` under the appropriate `cdef extern from` block:

```cython
cdef extern from "cdp_lib.h":
    # ... existing declarations ...

    cdp_lib_buffer* cdp_lib_my_effect(cdp_lib_ctx* ctx,
                                       const cdp_lib_buffer* input,
                                       double param1,
                                       int param2)
```

### Step 7: Add Python Wrapper (_core.pyx)

Add the Python wrapper function in `cycdp/src/cycdp/_core.pyx`:

```cython
def my_effect(Buffer buf not None, double param1, int param2=10):
    """Apply my effect (native implementation).

    Args:
        buf: Input Buffer.
        param1: Description of param1.
        param2: Description of param2. Default 10.

    Returns:
        New Buffer with processed audio.

    Raises:
        CDPError: If processing fails.
        ValueError: If parameters are invalid.
    """
    # Validate parameters
    if param1 <= 0:
        raise ValueError("param1 must be positive")

    # Get context and convert buffer
    cdef cdp_lib_ctx* ctx = _get_cdp_lib_ctx()
    cdef cdp_lib_buffer* input_buf = _buffer_to_cdp_lib(buf)

    # Call C function
    cdef cdp_lib_buffer* output_buf = cdp_lib_my_effect(
        ctx, input_buf, param1, param2)

    # Free input buffer (we made a copy)
    cdp_lib_buffer_free(input_buf)

    # Check for errors
    if output_buf is NULL:
        error_msg = cdp_lib_get_error(ctx)
        raise CDPError(-1, error_msg.decode('utf-8') if error_msg else "Effect failed")

    # Convert result and free C buffer
    cdef Buffer result = _cdp_lib_to_buffer(output_buf)
    cdp_lib_buffer_free(output_buf)

    return result
```

### Step 8: Export in **init**.py

Add to `cycdp/src/cycdp/__init__.py`:

```python
from cycdp._core import (
    # ... existing imports ...
    my_effect,
)

__all__ = [
    # ... existing exports ...
    "my_effect",
]
```

### Step 9: Add Tests

Add tests in `cycdp/tests/test_pycdp.py`:

```python
class TestMyEffect:
    """Test my_effect function."""

    @pytest.fixture
    def sine_wave(self):
        """Create a simple sine wave buffer for testing."""
        import math
        sample_rate = 44100
        duration = 0.5
        samples = array.array('f', [
            0.5 * math.sin(2 * math.pi * 440 * i / sample_rate)
            for i in range(int(sample_rate * duration))
        ])
        return cycdp.Buffer.from_memoryview(samples, channels=1, sample_rate=sample_rate)

    def test_my_effect_basic(self, sine_wave):
        """Effect should run without error."""
        result = cycdp.my_effect(sine_wave, param1=1.0)
        assert result.frame_count > 0

    def test_my_effect_invalid_param(self, sine_wave):
        """Effect should reject invalid parameters."""
        with pytest.raises(ValueError):
            cycdp.my_effect(sine_wave, param1=-1.0)

    def test_my_effect_behavior(self, sine_wave):
        """Test specific behavior of the effect."""
        result = cycdp.my_effect(sine_wave, param1=2.0)
        # Add assertions about expected behavior
        # e.g., check amplitude, length, frequency content
```

### Step 10: Build and Test

```bash
# Build C library (optional, for testing C code directly)
cd libcdp/cdp_lib/build
cmake ..
make -j4
./test_cdp_lib

# Build and test cycdp
cd cycdp
make build
make test
```

## Common Patterns

### Time-Domain Processing (sample-by-sample)

```c
for (size_t i = 0; i < input->length; i++) {
    output->data[i] = process(input->data[i]);
}
```

### Stereo Processing

```c
if (input->channels == 2) {
    for (size_t i = 0; i < input->length; i += 2) {
        float left = input->data[i];
        float right = input->data[i + 1];
        output->data[i] = process_left(left, right);
        output->data[i + 1] = process_right(left, right);
    }
}
```

### Variable Output Length

```c
// Calculate output length first
size_t out_length = calculate_output_length(input->length, params);

cdp_lib_buffer *output = cdp_lib_buffer_create(
    out_length, input->channels, input->sample_rate);
```

### Effects with Feedback (delay, reverb)

```c
// Allocate delay line
size_t delay_samples = (size_t)(delay_ms * input->sample_rate / 1000.0);
float *delay_line = (float *)calloc(delay_samples, sizeof(float));
size_t delay_pos = 0;

for (size_t i = 0; i < input->length; i++) {
    float delayed = delay_line[delay_pos];
    float in_sample = input->data[i];

    output->data[i] = in_sample + delayed * mix;
    delay_line[delay_pos] = in_sample + delayed * feedback;

    delay_pos = (delay_pos + 1) % delay_samples;
}

free(delay_line);
```

### LFO Modulation (chorus, flanger, tremolo)

```c
double phase = 0.0;
double phase_inc = 2.0 * M_PI * rate / input->sample_rate;

for (size_t i = 0; i < input->length; i++) {
    float lfo = (float)sin(phase);
    // Use lfo to modulate something
    phase += phase_inc;
    if (phase >= 2.0 * M_PI) phase -= 2.0 * M_PI;
}
```

## Common Pitfalls

### 1. Forgetting to Free Memory

Always free allocated memory on all code paths:

```c
cdp_spectral_data *spectral = cdp_spectral_analyze(...);
if (spectral == NULL) {
    return NULL;  // OK - nothing to free
}

float *audio = cdp_spectral_synthesize(spectral, &out_samples);
cdp_spectral_data_free(spectral);  // Free spectral data

if (audio == NULL) {
    return NULL;  // spectral already freed
}

cdp_lib_buffer *output = cdp_lib_buffer_from_data(audio, ...);
if (output == NULL) {
    free(audio);  // Don't forget this!
    return NULL;
}
```

### 2. Using Instantaneous Frequency for Filtering

**Wrong:**

```c
if (freq[b] > cutoff) {  // freq[b] is instantaneous frequency
    amp[b] *= attenuation;
}
```

**Correct:**

```c
float bin_freq = b * freq_per_bin;  // Use bin center frequency
if (bin_freq > cutoff) {
    amp[b] *= attenuation;
}
```

### 3. Not Handling Mono/Stereo Correctly

Check channel count and handle appropriately:

```c
if (input->channels > 1) {
    // Convert to mono or process each channel
}
```

### 4. Buffer Overflow with Variable-Length Output

Always calculate output size before allocating:

```c
// Don't assume output length equals input length
size_t out_length = (size_t)(input->length * stretch_factor) + extra_samples;
```

### 5. Missing Parameter Validation

Validate all parameters in both C and Python:

```c
// C validation
if (param < 0 || param > 1) {
    snprintf(ctx->error_msg, sizeof(ctx->error_msg),
             "param must be 0.0 to 1.0");
    return NULL;
}
```

```python
# Python validation (in _core.pyx)
if param < 0 or param > 1:
    raise ValueError("param must be 0.0 to 1.0")
```

## File Reference

| File | Purpose |
|------|---------|
| `libcdp/cdp_lib/cdp_lib.h` | C function declarations |
| `libcdp/cdp_lib/cdp_lib.c` | Main C implementations |
| `libcdp/cdp_lib/cdp_spectral.h` | Spectral processing declarations |
| `libcdp/cdp_lib/cdp_spectral.c` | STFT/ISTFT and spectral transforms |
| `libcdp/cdp_lib/cdp_envelope.h/c` | Envelope operations |
| `libcdp/cdp_lib/cdp_distort.h/c` | Distortion effects |
| `libcdp/cdp_lib/cdp_reverb.h/c` | Reverb implementation |
| `libcdp/cdp_lib/cdp_granular.h/c` | Granular synthesis |
| `cycdp/src/cycdp/cdp_lib.pxd` | Cython declarations (for .pxd imports) |
| `cycdp/src/cycdp/_core.pyx` | Cython wrappers + inline declarations |
| `cycdp/src/cycdp/__init__.py` | Python exports |
| `cycdp/tests/test_pycdp.py` | Test suite |
| `cycdp/CMakeLists.txt` | Build configuration |

## Testing Checklist

- [ ] Function runs without error on valid input

- [ ] Function raises appropriate errors on invalid input

- [ ] Output has expected length (for variable-length effects)

- [ ] Output has expected characteristics (amplitude, frequency content)

- [ ] Memory is properly freed (run with valgrind if available)

- [ ] Works with both mono and stereo input (if applicable)

- [ ] Edge cases handled (empty input, very short input, extreme parameters)
