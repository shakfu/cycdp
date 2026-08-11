"""Execute the Python examples in README.md.

M8: the README's headline example called `time_stretch(buf, stretch_factor=2.0)`
against an implementation whose parameter is `factor`, so the first code a
reader copies raised TypeError. Nothing checked it.

Documentation is the fourth hand-maintained copy of the API (after _core.pyx,
_core.pyi and cli.py's COMMANDS registry). The other three are now generated or
verified against the implementation; this covers the fourth.

Blocks that need an input file get a generated one, so the examples can be
written the way a reader would actually run them.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import cycdp

REPO = Path(__file__).resolve().parent.parent
README = REPO / "README.md"

# Fenced ```python blocks, paired with their line number in the file.
BLOCK_RE = re.compile(r"```python\n(.*?)```", re.S)


def python_blocks() -> list[tuple[int, str]]:
    src = README.read_text()
    return [
        (src[: m.start()].count("\n") + 1, m.group(1)) for m in BLOCK_RE.finditer(src)
    ]


BLOCKS = python_blocks()


def test_readme_has_python_examples():
    """Guard against the regex silently matching nothing."""
    assert len(BLOCKS) >= 4, f"only found {len(BLOCKS)} python blocks in README.md"


@pytest.mark.parametrize("line,code", BLOCKS, ids=[f"L{line}" for line, _ in BLOCKS])
def test_readme_example_runs(line, code, tmp_path, monkeypatch):
    """Every README Python example must execute against the real API."""
    if "import numpy" in code:
        pytest.importorskip("numpy", reason="numpy is an optional dependency")

    # Examples read "input.wav" and write "output.wav"; run them in a scratch
    # directory with a real input present so they work as written.
    monkeypatch.chdir(tmp_path)
    cycdp.write_file(
        "input.wav",
        cycdp.synth_wave(
            waveform=cycdp.WAVE_SINE,
            frequency=440.0,
            amplitude=0.5,
            duration=0.25,
            sample_rate=44100,
        ),
    )

    try:
        exec(compile(code, f"README.md:{line}", "exec"), {"__name__": "__main__"})
    except Exception as exc:
        pytest.fail(
            f"README.md example at line {line} failed: "
            f"{type(exc).__name__}: {exc}\n\n{code}"
        )
