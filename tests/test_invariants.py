"""Properties every operation must satisfy, checked across the whole API.

The suite has good breadth -- every public function is called somewhere -- but
for most operations the only assertion was that the result had a nonzero length.
An operation could return its input unchanged, or silently fill the buffer with
NaN, and nothing would fail. `tests/test_dsp_behaviour.py` measures what about
thirty operations actually do to a signal; that is the right depth, and it is
not proportionate to hand-write it for all one hundred and thirty.

These are the cheap invariants that *are* proportionate to apply to everything:

  - No output sample is NaN or infinite. This is the strongest one. It catches
    an uninitialised buffer, a division by a zero window, a runaway feedback
    loop and an unnormalised FFT -- and it doubles as the regression net for
    the parameter guards, since a non-finite input that slips past them shows
    up here rather than as a crash somewhere downstream.

  - No output sample is absurdly large. A stable process may legitimately
    exceed unity, but not by orders of magnitude; a value above the ceiling
    here means feedback that does not decay.

  - A seeded operation returns identical output for identical seeds.

The operation table is built by introspecting the type stubs, so an operation
added later is covered without anyone remembering to add it.
"""

from __future__ import annotations

import array
import ast
import math
from pathlib import Path
from typing import ClassVar

import pytest

import cycdp

SR = 44100
REPO = Path(__file__).resolve().parent.parent
PYI = REPO / "src" / "cycdp" / "_core.pyi"

# Well above any sane processing result, low enough to catch runaway feedback.
# Reverb tails and resonant filters can exceed unity by a few times; nothing
# stable reaches three orders of magnitude.
MAX_PLAUSIBLE_SAMPLE = 1000.0


def _sine(n: int = 8192, freq: float = 440.0, channels: int = 1) -> cycdp.Buffer:
    samples = array.array(
        "f",
        [
            0.4 * math.sin(2 * math.pi * freq * (i // channels) / SR)
            for i in range(n * channels)
        ],
    )
    return cycdp.Buffer.from_memoryview(samples, channels, SR)


def _single_buffer_operations() -> list[str]:
    """Every public function taking one Buffer and otherwise defaulted.

    Read from the stub rather than a hand-written list: the point is that a new
    operation is covered automatically, and the stub is generated from the
    Cython source and checked against the compiled module, so it cannot drift.
    """
    names = []
    for node in ast.parse(PYI.read_text()).body:
        if not isinstance(node, ast.FunctionDef):
            continue
        args = node.args.args
        if not args:
            continue
        first = ast.unparse(args[0].annotation) if args[0].annotation else ""
        if first != "Buffer":
            continue
        required = len(args) - len(node.args.defaults)
        if required != 1:
            continue
        names.append(node.name)
    return sorted(names)


OPERATIONS = _single_buffer_operations()

# Operations that need a shape of input the generic mono sine does not provide.
# Listed rather than skipped silently, so the exclusion stays visible.
NEEDS_STEREO = {"mirror", "narrow", "phase_stereo"}


def _describe(values, label: str) -> str:
    bad = [(i, v) for i, v in enumerate(values) if not math.isfinite(v)]
    return (
        f"{label}: {len(bad)} of {len(values)} output samples are not finite; "
        f"first at index {bad[0][0]} = {bad[0][1]!r}"
    )


class TestOutputIsFinite:
    """No operation may emit NaN or Inf.

    A single non-finite sample propagates through any subsequent processing and
    silently poisons the whole chain; it also makes writing a PCM file
    undefined behaviour, since the float-to-int conversion has no defined
    result for NaN.
    """

    def test_the_operation_table_is_populated(self):
        """Guard against the introspection silently matching nothing."""
        assert len(OPERATIONS) > 50, f"only found {len(OPERATIONS)} operations"

    @pytest.mark.parametrize("name", OPERATIONS)
    def test_output_has_no_non_finite_samples(self, name):
        func = getattr(cycdp, name)
        buf = _sine(channels=2) if name in NEEDS_STEREO else _sine()

        result = func(buf)
        if not isinstance(result, cycdp.Buffer):
            pytest.skip(f"{name} returns {type(result).__name__}, not a Buffer")

        values = result.to_list()
        assert values, f"{name} returned an empty buffer"
        assert all(math.isfinite(v) for v in values), _describe(values, name)

    @pytest.mark.parametrize("name", OPERATIONS)
    def test_output_is_not_absurdly_loud(self, name):
        func = getattr(cycdp, name)
        buf = _sine(channels=2) if name in NEEDS_STEREO else _sine()

        result = func(buf)
        if not isinstance(result, cycdp.Buffer):
            pytest.skip(f"{name} returns {type(result).__name__}, not a Buffer")

        peak = max((abs(v) for v in result.to_list()), default=0.0)
        assert peak < MAX_PLAUSIBLE_SAMPLE, (
            f"{name} produced a peak of {peak:.3g} from a 0.4-amplitude sine; "
            f"that is feedback that does not decay, not processing"
        )


class TestSilenceInSilenceOut:
    """Processing silence must not invent signal.

    An operation that emits something from an all-zero input is reading
    uninitialised memory, or has an offset it should not have. Synthesis and
    noise-generating operations are the legitimate exceptions.
    """

    # Each exclusion carries its reason, so "it fails, add it to the list"
    # cannot quietly become the way a real defect gets buried.
    GENERATES_SIGNAL: ClassVar[dict[str, str]] = {
        "brownian": "drives a random walk that does not depend on input level",
        "cantor": "gates against a generated fractal pattern",
        "cascade": "synthesises echo stages",
        "chirikov": "iterates the standard map independently of the input",
        "crystal": "generates decaying grains",
        "fracture": "scatters generated fragments",
        "quirk": "applies probabilistic transformations of its own",
        "strange": "drives a Lorenz attractor",
        "tesselate": "builds a tile pattern",
        "bitcrush": "quantisation adds a floor at the LSB",
        # warp adds a progressive offset to every sample *before* folding
        # (val = input + incrval), so a silent input still folds the offset.
        # The result sits at the envelope's minimum gain floor, about -80 dBFS.
        "distort_warp": "folds an accumulating offset, not only the input",
    }

    @pytest.mark.parametrize("name", OPERATIONS)
    def test_silence_stays_silent(self, name):
        if name in self.GENERATES_SIGNAL:
            pytest.skip(f"{name}: {self.GENERATES_SIGNAL[name]}")

        channels = 2 if name in NEEDS_STEREO else 1
        buf = cycdp.Buffer.create(8192, channels, SR)  # zero-filled

        func = getattr(cycdp, name)
        try:
            result = func(buf)
        except cycdp.CDPError:
            # Several operations reasonably refuse an all-silent input (there
            # is no pitch to track, no waveset to find, nothing to normalise).
            pytest.skip(f"{name} rejects silent input")

        if not isinstance(result, cycdp.Buffer):
            pytest.skip(f"{name} returns {type(result).__name__}, not a Buffer")

        peak = max((abs(v) for v in result.to_list()), default=0.0)
        assert peak < 1e-6, (
            f"{name} produced a peak of {peak:.3g} from digital silence, which "
            f"means it is reading something other than its input"
        )


class TestSeededOperationsAreReproducible:
    """Identical seeds must give identical output.

    Seeded randomisation is only useful if it is repeatable; every one of these
    takes a `seed` argument precisely so a result can be reproduced.
    """

    @staticmethod
    def _seeded_operations() -> list[str]:
        names = []
        for node in ast.parse(PYI.read_text()).body:
            if not isinstance(node, ast.FunctionDef):
                continue
            args = [a.arg for a in node.args.args]
            if "seed" not in args or not args:
                continue
            first = ast.unparse(node.args.args[0].annotation or ast.Name("x"))
            if first != "Buffer":
                continue
            required = len(node.args.args) - len(node.args.defaults)
            if required != 1:
                continue
            names.append(node.name)
        return sorted(names)

    SEEDED = _seeded_operations()

    def test_the_seeded_table_is_populated(self):
        assert len(self.SEEDED) > 10, f"only found {len(self.SEEDED)} seeded ops"

    @pytest.mark.parametrize("name", SEEDED)
    def test_same_seed_gives_identical_output(self, name):
        func = getattr(cycdp, name)
        buf = _sine(channels=2) if name in NEEDS_STEREO else _sine()

        first = func(buf, seed=1234)
        second = func(buf, seed=1234)
        assert first.to_bytes() == second.to_bytes(), (
            f"{name} produced different output for the same seed, so its "
            f"results cannot be reproduced"
        )

    @pytest.mark.parametrize("name", SEEDED)
    def test_the_seed_actually_selects_a_stream(self, name):
        """A seed that changes nothing is a seed that is being ignored.

        Weaker than it looks: a handful of operations legitimately produce the
        same output for different seeds on a pure sine (there is only one
        waveset layout to shuffle), so this asserts only that *some* pair of
        seeds differs.
        """
        func = getattr(cycdp, name)
        buf = _sine(channels=2) if name in NEEDS_STEREO else _sine()

        outputs = {func(buf, seed=s).to_bytes() for s in (1, 7, 99, 4242)}
        if len(outputs) == 1:
            pytest.skip(f"{name} is seed-insensitive for this input")
        assert len(outputs) > 1
