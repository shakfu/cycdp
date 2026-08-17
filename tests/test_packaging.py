"""Keep the declarations of supported Python versions in agreement.

Supported versions are stated in six places now, and they have drifted before:
the CI build matrix once listed 3.9, below the then-current
`requires-python = ">=3.10"` floor. uv honours requires-python and silently
resolved a newer interpreter, so the job passed while its name claimed coverage
it did not have -- and the actual floor was never built or tested.

Since the move to abi3 the floor carries more weight, because it is no longer
just metadata. It is the version the extension is compiled against
(`USE_SABI` in CMakeLists.txt), the tag the wheel carries (`wheel.py-api`), and
the version pip enforces at install time. A wheel tagged cp311-abi3 installs on
3.11 and refuses below it, so a floor that disagrees with the compiled ABI
level is not a documentation error -- it is an ImportError for whoever installs
at the boundary.

Parsed with regex rather than PyYAML/tomllib-plus-a-YAML-dep so this adds no
dependency; the patterns are narrow and fail loudly if a file is restructured.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PYPROJECT = REPO / "pyproject.toml"
CI = REPO / ".github" / "workflows" / "ci.yml"
PUBLISH = REPO / ".github" / "workflows" / "build-publish.yml"
CMAKELISTS = REPO / "CMakeLists.txt"


def version_tuple(text: str) -> tuple[int, int]:
    major, minor = text.split(".")[:2]
    return int(major), int(minor)


@pytest.fixture(scope="module")
def requires_python() -> tuple[int, int]:
    """The floor from pyproject's requires-python."""
    # tomllib is stdlib from 3.11, which is now the floor, so there is no
    # longer a regex fallback for interpreters that lack it.
    data = tomllib.loads(PYPROJECT.read_text())
    spec = data["project"]["requires-python"]

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


@pytest.fixture(scope="module")
def abi3_target() -> tuple[int, int]:
    """The stable-ABI floor from scikit-build's wheel.py-api."""
    m = re.search(r'^wheel\.py-api\s*=\s*"cp(\d)(\d+)"', PYPROJECT.read_text(), re.M)
    assert m, "could not find wheel.py-api in pyproject.toml"
    return int(m.group(1)), int(m.group(2))


@pytest.fixture(scope="module")
def cmake_sabi_version() -> tuple[int, int]:
    """The version passed to USE_SABI in CMakeLists.txt."""
    m = re.search(r"USE_SABI\s+(\d+)\.(\d+)", CMAKELISTS.read_text())
    assert m, "could not find USE_SABI in CMakeLists.txt"
    return int(m.group(1)), int(m.group(2))


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

    def test_cibuildwheel_builds_only_the_abi3_target(
        self, cibuildwheel_versions, abi3_target
    ):
        """Under abi3 one build covers every later interpreter.

        This used to require CIBW_BUILD to enumerate exactly the classifier
        versions, which was right while each version got its own wheel. It is
        wrong now: building cp312 as well as cp311 would upload a redundant
        wheel that shadows the abi3 one on that version, so the artifact users
        install would no longer be the artifact CI exercised most.
        """
        assert cibuildwheel_versions == [abi3_target], (
            f"CIBW_BUILD targets {[fmt(v) for v in cibuildwheel_versions]} but "
            f"wheel.py-api declares abi3 from {fmt(abi3_target)}; it should "
            f"build that one version and nothing else"
        )

    def test_abi3_target_is_the_supported_floor(
        self, abi3_target, requires_python, classifier_versions
    ):
        """A wheel tagged cp3Y-abi3 refuses to install below 3.Y.

        So the abi3 target and the advertised floor are the same number seen
        from two directions -- if they drift, either pip rejects installs the
        metadata promised, or the metadata disclaims versions that work.
        """
        assert abi3_target == requires_python, (
            f"wheel.py-api is cp{abi3_target[0]}{abi3_target[1]} but "
            f"requires-python floor is {fmt(requires_python)}"
        )
        assert classifier_versions[0] == abi3_target, (
            f"lowest classifier is {fmt(classifier_versions[0])} but the abi3 "
            f"target is {fmt(abi3_target)}"
        )

    def test_cmake_sabi_matches_wheel_py_api(self, cmake_sabi_version, abi3_target):
        """The compiled Py_LIMITED_API level must match the wheel's tag.

        These are set in different files by different tools and nothing else
        connects them. If CMake compiled against 3.12 while the wheel claimed
        cp311-abi3, the wheel would install on 3.11 and fail at import.
        """
        assert cmake_sabi_version == abi3_target, (
            f"CMakeLists.txt builds USE_SABI {fmt(cmake_sabi_version)} but "
            f"pyproject declares wheel.py-api cp{abi3_target[0]}{abi3_target[1]}"
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


class TestExactlyOneExtensionIsInstalled:
    """Two `_core*.so` in one package means the tests measure the wrong one.

    Build variants stopped sharing a filename when wheels moved to abi3: an
    ordinary build installs `_core.abi3.so`, a coverage build installs
    `_core.cpython-3XY-<plat>.so`. Reinstalling therefore no longer overwrites
    the previous variant, it sits alongside it -- and the version-specific
    suffix comes first in `importlib.machinery.EXTENSION_SUFFIXES`, so the
    stale file wins every import.

    That failure is entirely silent: the suite passes, against the wrong
    artifact. This is the same hazard `tests/test_coverage_setup.py` guards for
    instrumentation, one level down.

    Only the count is asserted, not which ABI is installed. The filename cannot
    answer that: the project is installed editable and scikit-build-core's
    finder pins one path at install time, so a later build of a different ABI
    overwrites those bytes while the name stays as it was. A coverage-named
    file holding an abi3 build is a normal intermediate state, not a defect.
    """

    def test_only_one_core_extension_exists(self):
        import cycdp._core as core

        pkg = Path(core.__file__).parent
        found = sorted(
            p.name
            for p in pkg.iterdir()
            if p.name.startswith("_core") and p.suffix in {".so", ".pyd"}
        )
        assert len(found) == 1, (
            f"{len(found)} extension modules installed in {pkg}: {found}. "
            f"Python imports {Path(core.__file__).name} and ignores the rest, "
            f"so the suite may be exercising a stale build. Remove the extras "
            f"and reinstall with `uv sync --dev --no-cache`."
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
