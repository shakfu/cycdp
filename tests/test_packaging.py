"""Keep the declarations of supported Python versions in agreement.

Supported versions are stated in four places, and they had drifted: the CI
build matrix listed 3.9, below the `requires-python = ">=3.10"` floor. uv
honours requires-python and silently resolved a newer interpreter, so the job
passed while its name claimed coverage it did not have -- and 3.10, the actual
floor, was never built or tested.

Parsed with regex rather than PyYAML/tomllib-plus-a-YAML-dep so this adds no
dependency; the patterns are narrow and fail loudly if a file is restructured.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PYPROJECT = REPO / "pyproject.toml"
CI = REPO / ".github" / "workflows" / "ci.yml"
PUBLISH = REPO / ".github" / "workflows" / "build-publish.yml"


def version_tuple(text: str) -> tuple[int, int]:
    major, minor = text.split(".")[:2]
    return int(major), int(minor)


@pytest.fixture(scope="module")
def requires_python() -> tuple[int, int]:
    """The floor from pyproject's requires-python."""
    if sys.version_info >= (3, 11):
        import tomllib

        data = tomllib.loads(PYPROJECT.read_text())
        spec = data["project"]["requires-python"]
    else:  # pragma: no cover - only on the oldest supported interpreter
        m = re.search(r'^requires-python\s*=\s*"([^"]+)"', PYPROJECT.read_text(), re.M)
        assert m, "could not find requires-python in pyproject.toml"
        spec = m.group(1)

    m = re.search(r">=\s*(\d+\.\d+)", spec)
    assert m, f"expected a >= floor in requires-python, got {spec!r}"
    return version_tuple(m.group(1))


@pytest.fixture(scope="module")
def classifier_versions() -> list[tuple[int, int]]:
    text = PYPROJECT.read_text()
    found = re.findall(r'"Programming Language :: Python :: (\d+\.\d+)"', text)
    assert found, "no versioned Python classifiers found in pyproject.toml"
    return sorted(version_tuple(v) for v in found)


@pytest.fixture(scope="module")
def ci_matrix_versions() -> list[tuple[int, int]]:
    m = re.search(r"^\s*python-version:\s*\[([^\]]+)\]", CI.read_text(), re.M)
    assert m, "could not find the build matrix python-version list in ci.yml"
    found = re.findall(r'"(\d+\.\d+)"', m.group(1))
    assert found, f"no versions parsed from {m.group(1)!r}"
    return sorted(version_tuple(v) for v in found)


@pytest.fixture(scope="module")
def cibuildwheel_versions() -> list[tuple[int, int]]:
    m = re.search(r"^\s*CIBW_BUILD:\s*\"([^\"]+)\"", PUBLISH.read_text(), re.M)
    assert m, "could not find CIBW_BUILD in build-publish.yml"
    found = re.findall(r"cp(\d)(\d+)-", m.group(1))
    assert found, f"no cpXY tags parsed from {m.group(1)!r}"
    return sorted((int(a), int(b)) for a, b in found)


def fmt(v: tuple[int, int]) -> str:
    return f"{v[0]}.{v[1]}"


class TestPythonSupportIsConsistent:
    def test_ci_matrix_does_not_go_below_the_floor(
        self, ci_matrix_versions, requires_python
    ):
        below = [v for v in ci_matrix_versions if v < requires_python]
        assert not below, (
            f"CI build matrix includes {[fmt(v) for v in below]}, below "
            f"requires-python >= {fmt(requires_python)}. uv resolves a newer "
            f"interpreter instead, so the job passes without testing what its "
            f"name claims."
        )

    def test_ci_matrix_builds_the_floor(self, ci_matrix_versions, requires_python):
        """The oldest supported version is where wheels actually break."""
        assert requires_python in ci_matrix_versions, (
            f"requires-python floor {fmt(requires_python)} is not in the CI "
            f"build matrix {[fmt(v) for v in ci_matrix_versions]}"
        )

    def test_classifiers_start_at_the_floor(self, classifier_versions, requires_python):
        assert classifier_versions[0] == requires_python, (
            f"lowest Python classifier is {fmt(classifier_versions[0])} but "
            f"requires-python floor is {fmt(requires_python)}"
        )

    def test_cibuildwheel_matches_the_classifiers(
        self, cibuildwheel_versions, classifier_versions
    ):
        assert cibuildwheel_versions == classifier_versions, (
            f"CIBW_BUILD targets {[fmt(v) for v in cibuildwheel_versions]} but "
            f"pyproject classifiers declare "
            f"{[fmt(v) for v in classifier_versions]}; released wheels would "
            f"not match the advertised support"
        )

    def test_ci_matrix_covers_the_ceiling(
        self, ci_matrix_versions, classifier_versions
    ):
        assert ci_matrix_versions[-1] == classifier_versions[-1], (
            f"CI builds up to {fmt(ci_matrix_versions[-1])} but the newest "
            f"declared version is {fmt(classifier_versions[-1])}"
        )


class TestWorkflowHygiene:
    def test_cibuildwheel_action_versions_agree(self):
        """A version skew across OS jobs means wheels build differently."""
        versions = set(re.findall(r"pypa/cibuildwheel@(v[\d.]+)", PUBLISH.read_text()))
        assert len(versions) == 1, (
            f"build-publish.yml pins multiple cibuildwheel versions: {sorted(versions)}"
        )
