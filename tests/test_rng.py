"""Randomised operations must be seedable, reproducible, and self-contained.

M6: three RNG strategies coexisted in the C layer. Most modules used the
per-context xorshift64 PRNG (`ctx->prng_state`), which is correct.
`cdp_distort.c` used the C runtime's global `srand`/`rand`, and
`cdp_wrappage.c` used a file-static generator hardcoded to seed 42.

The global-`rand()` case was the real problem. `srand()` mutates process-wide
state shared with every other library in the address space, so calling
`cycdp.distort_shuffle(buf, seed=42)` silently reseeded `rand()` for anything
else in the interpreter that used it. It is also non-reentrant, so it would
have become a data race the moment the GIL is released around DSP calls (M1).

All three now share the context PRNG. These tests pin the three properties
that matter: same seed reproduces, different seeds differ, and no process-wide
state is touched.
"""

from __future__ import annotations

import array
import math
import subprocess
import sys
import textwrap

import pytest

import cycdp

SR = 22050


@pytest.fixture(scope="module")
def audio() -> cycdp.Buffer:
    samples = array.array(
        "f", [0.4 * math.sin(2 * math.pi * 220 * i / SR) for i in range(SR // 2)]
    )
    return cycdp.Buffer.from_memoryview(samples, 1, SR)


# (name, callable taking (buf, seed)) for every operation whose randomisation
# was previously on global or file-static state.
CONVERTED = [
    ("distort_shuffle", lambda b, s: cycdp.distort_shuffle(b, 3, seed=s)),
    (
        "distort_mark",
        lambda b, s: cycdp.distort_mark(b, [0.05, 0.1, 0.15], random=0.5, seed=s),
    ),
    ("wrappage", lambda b, s: cycdp.wrappage(b, duration=0.3, seed=s)),
]


class TestSeedingIsReproducible:
    @pytest.mark.parametrize("name,call", CONVERTED, ids=[n for n, _ in CONVERTED])
    def test_same_seed_gives_identical_output(self, audio, name, call):
        assert call(audio, 42).to_bytes() == call(audio, 42).to_bytes(), (
            f"{name} is not reproducible with a fixed seed"
        )

    @pytest.mark.parametrize("name,call", CONVERTED, ids=[n for n, _ in CONVERTED])
    def test_different_seeds_give_different_output(self, audio, name, call):
        assert call(audio, 42).to_bytes() != call(audio, 99).to_bytes(), (
            f"{name} ignores its seed -- output is identical for 42 and 99"
        )


class TestWrappageIsSeedable:
    """wrappage previously hardcoded seed 42 and exposed no seed parameter.

    It was the only granular operation whose randomisation could not be
    controlled, while every sibling took a seed.
    """

    def test_seed_parameter_exists(self):
        import inspect

        assert "seed" in inspect.signature(cycdp.wrappage).parameters

    def test_default_seed_matches_the_other_granular_operations(self):
        import inspect

        default = inspect.signature(cycdp.wrappage).parameters["seed"].default
        assert default == 0, (
            "seed should default to 0 (derive from clock), as brassage, "
            "freeze and grain_cloud do"
        )


class TestNoProcessGlobalState:
    """No operation may perturb the C runtime's global RNG.

    Run in a subprocess so the check is unaffected by anything else in this
    session, and so a regression cannot be masked by test ordering.
    """

    @pytest.mark.parametrize(
        "name,expr",
        [
            ("distort_shuffle", "cycdp.distort_shuffle(b, 3, seed=42)"),
            (
                "distort_mark",
                "cycdp.distort_mark(b, [0.05, 0.1, 0.15], random=0.5, seed=7)",
            ),
            ("wrappage", "cycdp.wrappage(b, duration=0.3, seed=5)"),
            ("brassage", "cycdp.brassage(b, seed=3)"),
        ],
    )
    def test_global_rand_sequence_is_untouched(self, name, expr):
        script = textwrap.dedent(f"""
            import ctypes, ctypes.util, array, math
            import cycdp

            libc = ctypes.CDLL(ctypes.util.find_library("c"))
            libc.rand.restype = ctypes.c_int

            sr = {SR}
            s = array.array("f", [0.4 * math.sin(2*math.pi*220*i/sr)
                                  for i in range(sr // 2)])
            b = cycdp.Buffer.from_memoryview(s, 1, sr)

            libc.srand(1234)
            before = [libc.rand() for _ in range(3)]

            libc.srand(1234)
            {expr}
            after = [libc.rand() for _ in range(3)]

            print("MATCH" if before == after else f"DRIFT {{before}} != {{after}}")
        """)
        result = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "MATCH", (
            f"{name} perturbed the process-global rand() sequence: "
            f"{result.stdout.strip()}"
        )


class TestNoGlobalRngInTheCLayer:
    """Static guard: the C sources must not reintroduce srand()/rand()."""

    @staticmethod
    def _strip_comments(text: str) -> list[str]:
        """Blank out C comments, preserving line numbering.

        Line-by-line splitting on "/*" is not enough: a block comment spanning
        several lines leaves its continuation lines looking like code, and this
        file's own explanatory comments mention srand()/rand().
        """
        out, in_block = [], False
        for line in text.split("\n"):
            buf, i = [], 0
            while i < len(line):
                if in_block:
                    end = line.find("*/", i)
                    if end == -1:
                        i = len(line)
                    else:
                        in_block = False
                        i = end + 2
                elif line.startswith("/*", i):
                    in_block = True
                    i += 2
                elif line.startswith("//", i):
                    break
                else:
                    buf.append(line[i])
                    i += 1
            out.append("".join(buf))
        return out

    def test_no_srand_or_rand_calls(self):
        import re
        from pathlib import Path

        repo = Path(__file__).resolve().parent.parent
        offenders = []
        for src in sorted((repo / "projects" / "libcdp").rglob("*.c")):
            lines = self._strip_comments(src.read_text(errors="replace"))
            for i, code in enumerate(lines, 1):
                if re.search(r"\bsrand\s*\(|(?<![_\w])rand\s*\(\s*\)", code):
                    offenders.append(f"{src.relative_to(repo)}:{i}: {code.strip()}")

        assert offenders == [], (
            "the C runtime's global RNG is back; use the context PRNG "
            "(cdp_lib_seed / cdp_lib_random) instead:\n  " + "\n  ".join(offenders)
        )

    def test_no_file_static_prng_state(self):
        import re
        from pathlib import Path

        repo = Path(__file__).resolve().parent.parent
        offenders = []
        for src in sorted((repo / "projects" / "libcdp").rglob("*.c")):
            for i, line in enumerate(src.read_text(errors="replace").split("\n"), 1):
                if re.match(
                    r"\s*static\s+(unsigned\s+int|uint\d+_t)\s+\w*(seed|rng|state)\w*\s*=",
                    line,
                ):
                    offenders.append(f"{src.relative_to(repo)}:{i}: {line.strip()}")

        assert offenders == [], (
            "file-static PRNG state is not per-call seedable and is not "
            "reentrant:\n  " + "\n  ".join(offenders)
        )
