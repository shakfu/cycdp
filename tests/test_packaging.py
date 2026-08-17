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


class TestExtensionSymbolVisibility:
    """The built module must export its init function and nothing else.

    The static C libraries are linked into `_core` and nothing else needs their
    symbols, but they used to be exported anyway -- 199 of them, including
    `errstr`, `fft_`, `fftmx` and `reals_`. Those are generic names from the
    vendored FFT that also appear in FFTPACK-derived libraries. CPython loads
    extensions with RTLD_LOCAL so a collision needs another extension loaded
    with RTLD_GLOBAL in the same process, but there is no reason to leave the
    possibility open.
    """

    def _exported(self):
        """Symbols the built extension publishes to the rest of the process.

        The flags differ by object format and it matters which table is read.
        On ELF the exports live in the *dynamic* symbol table, and `nm -g`
        reads `.symtab`, which a shared object need not carry at all -- so the
        obvious invocation returns nothing on Linux. That is worse than an
        error: an empty set silently satisfies "only PyInit is exported", and
        this check passed in CI for a release cycle while measuring nothing.
        Hence -D there, and the emptiness guard below.
        """
        import subprocess

        import cycdp._core as core

        so = Path(core.__file__)
        if sys.platform == "win32":
            pytest.skip("no nm on Windows; DLL exports are governed differently")

        # -D: dynamic symbols (ELF). BSD/llvm nm on macOS has no -D and reads
        # the Mach-O symbol table with -gU, which is the equivalent view.
        flags = ["-gU"] if sys.platform == "darwin" else ["-D", "--defined-only"]
        try:
            out = subprocess.run(
                ["nm", *flags, str(so)],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
        except (FileNotFoundError, subprocess.CalledProcessError):
            pytest.skip("nm unavailable")

        symbols = {
            line.split()[-1].lstrip("_") for line in out.splitlines() if line.strip()
        }
        assert symbols, (
            f"nm {' '.join(flags)} listed no symbols at all for {so.name}. The "
            f"checks below would pass vacuously, so fail here instead: the "
            f"wrong symbol table is being read for this object format."
        )
        return symbols

    def test_only_the_module_init_is_exported(self):
        exported = self._exported()
        # Compiler and runtime bookkeeping symbols vary by platform; only our
        # own C is interesting here.
        ours = {s for s in exported if not s.startswith("_")}
        assert ours <= {"PyInit__core"}, (
            f"the extension exports {sorted(ours - {'PyInit__core'})} into the "
            f"process's symbol table; build with hidden visibility so only the "
            f"module init escapes"
        )

    def test_the_module_init_is_still_exported(self):
        """Hiding everything would produce a module that cannot be imported."""
        assert "PyInit__core" in self._exported()


class TestLicenseFilesAreDistributed:
    """LGPL 2.1 section 6 wants the source to accompany the object code.

    The wheel statically links mxfft.c, which is Copyright (c) 1983-2023
    Trevor Wishart and Composers Desktop Project Ltd. The usual mitigation is
    that the sdist is published alongside, but a wheel that names neither the
    copyright holders nor where to get the source leaves the recipient with
    nothing. NOTICE does both, and must actually ship.
    """

    # Paths come from pyproject rather than being hardcoded here, so moving a
    # license file fails the build (license-files stops resolving) rather than
    # silently dropping it from the wheel while these tests keep passing
    # against a stale path. NOTICE currently sits beside the C library it is
    # mostly about, at projects/libcdp/.
    @staticmethod
    def declared_license_files() -> list[str]:
        m = re.search(r"^license-files = \[(.*?)\]", PYPROJECT.read_text(), re.M)
        assert m, "pyproject.toml declares no license-files"
        entries = re.findall(r'"([^"]+)"', m.group(1))
        assert entries, f"could not parse license-files from {m.group(1)!r}"
        return entries

    def notice_path(self) -> Path:
        matches = [e for e in self.declared_license_files() if Path(e).name == "NOTICE"]
        assert len(matches) == 1, f"expected exactly one NOTICE entry, got {matches}"
        return REPO / matches[0]

    def test_pyproject_declares_both_license_files(self):
        entries = self.declared_license_files()
        names = {Path(e).name for e in entries}
        assert names == {"LICENSE", "NOTICE"}, (
            f"pyproject.toml must declare both files so they land in the wheel; "
            f"found {entries}"
        )

    def test_the_declared_license_files_exist(self):
        """A path that does not resolve fails the build, but late and obscurely."""
        for entry in self.declared_license_files():
            assert (REPO / entry).is_file(), (
                f"license-files names a missing path: {entry}"
            )

    def test_notice_names_the_upstream_copyright_and_the_source(self):
        notice = self.notice_path().read_text()
        assert "Composers Desktop Project" in notice
        assert "mxfft.c" in notice
        assert "sdist" in notice.lower()

    def test_the_spdx_expression_has_no_competing_classifier(self):
        """PEP 639 forbids a 'License ::' classifier beside an SPDX expression.

        The build backend rejects the combination outright, so this is really a
        guard against someone re-adding the classifier and discovering it only
        at release time.
        """
        text = PYPROJECT.read_text()
        if 'license = "' in text:
            classifiers = [
                line
                for line in text.splitlines()
                if '"License ::' in line and not line.lstrip().startswith("#")
            ]
            assert not classifiers, (
                f"remove the license classifier(s) {classifiers}; they cannot "
                f"coexist with an SPDX license expression"
            )
