"""Shared fixtures and signal-analysis helpers.

M4: the suite had breadth but little depth -- 48% of assertions were
existence-only (`result.sample_count > 0`), and where numeric assertions did
exist the tolerances were wide enough to admit real breakage (time_stretch was
asserted only to land within 50-250% of the requested duration, while the
implementation is accurate to 2%).

These helpers make it cheap to assert what an operation actually did to the
signal: how much energy sits at a given frequency, what the overall level is,
and where the fundamental moved to. They use a Goertzel filter rather than a
full FFT so there is no numpy dependency -- the suite deliberately runs on
array.array alone.
"""

from __future__ import annotations

import array
import ast
import functools
import math
from pathlib import Path

import pytest

import cycdp

SR = 44100

REPO = Path(__file__).resolve().parent.parent


# =============================================================================
# Return-type recording
# =============================================================================
#
# tests/test_signatures.py verifies stub return types by calling, by hand, every
# function whose declared return is not a plain `Buffer` -- on the reasoning
# that the mistakes live there. That reasoning was right (all four wrong
# entries were in that set) but it leaves the ~118 functions declared
# `-> Buffer` merely assumed.
#
# Rather than hand-write calls for all of them, this records what every public
# function actually returned during the suite. Coverage shows the suite calls
# every one, so the recording is complete, and the check below turns "assumed"
# into "observed" for free.

_OBSERVED_RETURNS: dict[str, set[str]] = {}

# Base type each stub annotation resolves to.
_ANNOTATION_BASE = {
    "None": "NoneType",
    "dict": "dict",
    "list": "list",
    "tuple": "tuple",
    "Buffer": "Buffer",
    "str": "str",
    "float": "float",
    "int": "int",
}


def _record(name, func):
    # functools.wraps sets __wrapped__, so inspect.signature() follows through
    # to the real signature. Without it the wrapper reports (*args, **kwargs)
    # and any test introspecting cycdp.<func> silently sees the wrong thing.
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        result = func(*args, **kwargs)
        _OBSERVED_RETURNS.setdefault(name, set()).add(type(result).__name__)
        return result

    return wrapper


def pytest_configure(config):
    """Wrap public cycdp functions so their return types can be observed.

    Runs before test modules are imported, so tests calling `cycdp.foo()` go
    through the wrapper. Introspection tests read `cycdp._core` directly and
    are unaffected.
    """
    for name in cycdp.__all__:
        obj = getattr(cycdp, name, None)
        if callable(obj) and not isinstance(obj, type):
            setattr(cycdp, name, _record(name, obj))


def pytest_sessionfinish(session, exitstatus):
    """Fail the run if any observed return type contradicts the stub."""
    if exitstatus != 0 or not _OBSERVED_RETURNS:
        return

    pyi = (REPO / "src" / "cycdp" / "_core.pyi").read_text()
    declared = {
        node.name: ast.unparse(node.returns)
        for node in ast.parse(pyi).body
        if isinstance(node, ast.FunctionDef) and node.returns is not None
    }

    problems = []
    for name, annotation in sorted(declared.items()):
        seen = _OBSERVED_RETURNS.get(name)
        if not seen:
            continue
        head = annotation.split("[")[0]
        expected = _ANNOTATION_BASE.get(head, head)
        if seen != {expected}:
            problems.append(
                f"  {name}: stub declares '{annotation}' but returned {sorted(seen)}"
            )

    if problems:
        session.exitstatus = 1
        print(
            "\nERROR: stub return types contradict what the functions returned "
            "during this run:\n" + "\n".join(problems) + "\n\n"
            "Update RETURNS in scripts/gen_stubs.py and run `make stubs`.",
        )


# =============================================================================
# Signal construction
# =============================================================================


def make_sine(
    freq: float = 440.0,
    duration: float = 0.5,
    sample_rate: int = SR,
    amplitude: float = 0.5,
) -> cycdp.Buffer:
    """A mono sine wave Buffer."""
    n = int(sample_rate * duration)
    samples = array.array(
        "f",
        [
            amplitude * math.sin(2.0 * math.pi * freq * i / sample_rate)
            for i in range(n)
        ],
    )
    return cycdp.Buffer.from_memoryview(samples, 1, sample_rate)


def make_tones(
    freqs,
    duration: float = 0.5,
    sample_rate: int = SR,
    amplitude: float = 0.4,
) -> cycdp.Buffer:
    """A sum of equal-amplitude sine waves, for filter tests."""
    n = int(sample_rate * duration)
    samples = array.array(
        "f",
        [
            amplitude
            * sum(math.sin(2.0 * math.pi * f * i / sample_rate) for f in freqs)
            / len(freqs)
            for i in range(n)
        ],
    )
    return cycdp.Buffer.from_memoryview(samples, 1, sample_rate)


# =============================================================================
# Measurement
# =============================================================================


def to_mono_list(buf: cycdp.Buffer) -> list[float]:
    """Flatten a Buffer to a mono list, averaging channels."""
    samples = buf.to_list()
    channels = buf.channels
    if channels == 1:
        return samples
    return [
        sum(samples[i : i + channels]) / channels
        for i in range(0, len(samples) - channels + 1, channels)
    ]


def goertzel(samples: list[float], freq: float, sample_rate: int = SR) -> float:
    """Magnitude of `samples` at `freq`, normalised by length.

    A single-bin DFT via the Goertzel recurrence: cheaper and clearer than a
    full FFT when the question is "how much energy is at this one frequency",
    which is what filter and modulation assertions need. The recurrence is used
    rather than accumulating a rotating phasor because it stays numerically
    stable across the tens of thousands of samples these tests feed it.
    """
    n = len(samples)
    if n == 0:
        return 0.0
    coeff = 2.0 * math.cos(2.0 * math.pi * freq / sample_rate)
    s_prev = s_prev2 = 0.0
    for x in samples:
        s_prev2, s_prev = s_prev, x + coeff * s_prev - s_prev2
    power = s_prev2 * s_prev2 + s_prev * s_prev - coeff * s_prev * s_prev2
    return math.sqrt(max(power, 0.0)) / n


def energy_at(buf: cycdp.Buffer, freq: float) -> float:
    """Magnitude of a Buffer at a given frequency."""
    return goertzel(to_mono_list(buf), freq, buf.sample_rate)


def rms(buf: cycdp.Buffer) -> float:
    samples = to_mono_list(buf)
    if not samples:
        return 0.0
    return math.sqrt(sum(s * s for s in samples) / len(samples))


def peak_level(buf: cycdp.Buffer) -> float:
    samples = to_mono_list(buf)
    return max((abs(s) for s in samples), default=0.0)


def duration_seconds(buf: cycdp.Buffer) -> float:
    return buf.frame_count / buf.sample_rate


def db(ratio: float) -> float:
    """Linear ratio to dB, floored so silence does not produce -inf."""
    return 20.0 * math.log10(max(ratio, 1e-12))


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sine() -> cycdp.Buffer:
    """440 Hz, 0.5 s, mono, amplitude 0.5."""
    return make_sine()


@pytest.fixture
def sine_factory():
    """Build sines with arbitrary parameters."""
    return make_sine


@pytest.fixture
def tones_factory():
    """Build multi-tone signals."""
    return make_tones


@pytest.fixture
def low_high() -> cycdp.Buffer:
    """200 Hz + 3000 Hz, well separated for filter tests."""
    return make_tones([200.0, 3000.0])
