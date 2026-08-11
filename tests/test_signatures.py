"""Assert that the shipped type stubs describe the module that actually exists.

The package sets py.typed, so src/cycdp/_core.pyi is what every downstream type
checker sees. It was previously hand-maintained and had drifted from _core.pyx
in 60 of 129 functions -- wrong parameter names, wrong arity, wrong defaults --
which made mypy reject correct calls and accept incorrect ones.

The stub is now generated (scripts/gen_stubs.py). These tests are the guard: if
the stub, the Cython source, and the compiled module ever disagree again, the
suite fails instead of the users finding out.
"""

from __future__ import annotations

import ast
import inspect
import subprocess
import sys
from pathlib import Path

import pytest

import cycdp
import cycdp._core as core

REPO = Path(__file__).resolve().parent.parent
PYI = REPO / "src" / "cycdp" / "_core.pyi"
GEN = REPO / "scripts" / "gen_stubs.py"

# Functions whose runtime signature inspect cannot recover, if any ever appear.
NO_INTROSPECTION: set[str] = set()


def stub_functions() -> dict[str, ast.FunctionDef]:
    """Module-level function stubs declared in _core.pyi."""
    tree = ast.parse(PYI.read_text())
    return {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}


def stub_params(node: ast.FunctionDef) -> list[tuple[str, str | None]]:
    """(name, default_source) for each parameter of a stub function."""
    args = node.args.args
    defaults: list[ast.expr | None] = [None] * (len(args) - len(node.args.defaults))
    defaults += list(node.args.defaults)
    return [
        (a.arg, ast.unparse(d) if d is not None else None)
        for a, d in zip(args, defaults)
    ]


def runtime_params(func) -> list[tuple[str, object]]:
    """(name, default) for each parameter of a compiled function."""
    sig = inspect.signature(func)
    return [
        (p.name, None if p.default is inspect.Parameter.empty else p.default)
        for p in sig.parameters.values()
    ]


STUBS = stub_functions()
RUNTIME_NAMES = sorted(
    n for n in STUBS if callable(getattr(core, n, None)) and n not in NO_INTROSPECTION
)


class TestStubCoverage:
    def test_stub_is_not_empty(self):
        """Guard against a parsing accident silently emptying the suite."""
        assert len(STUBS) > 100

    def test_every_stub_exists_at_runtime(self):
        """A stub for a function that does not exist misleads type checkers."""
        missing = [n for n in STUBS if not hasattr(core, n)]
        assert missing == [], f"stubbed but absent from the module: {missing}"

    def test_every_exported_function_is_stubbed(self):
        """Anything in __all__ that is a function needs a stub."""
        unstubbed = []
        for name in cycdp.__all__:
            obj = getattr(cycdp, name, None)
            if inspect.isbuiltin(obj) or inspect.isfunction(obj):
                if name not in STUBS:
                    unstubbed.append(name)
        assert unstubbed == [], f"exported but not stubbed: {unstubbed}"

    def test_no_duplicate_stub_definitions(self):
        """A repeated def silently shadows the earlier one."""
        tree = ast.parse(PYI.read_text())
        names = [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]
        dupes = {n for n in names if names.count(n) > 1}
        assert dupes == set(), f"duplicated in the stub: {sorted(dupes)}"


class TestStubMatchesRuntime:
    """The core invariant: stub signature == compiled signature."""

    @pytest.mark.parametrize("name", RUNTIME_NAMES)
    def test_parameter_names_and_order(self, name):
        stub = [p for p, _ in stub_params(STUBS[name])]
        real = [p for p, _ in runtime_params(getattr(core, name))]
        assert stub == real, (
            f"{name}: stub declares {stub}, module has {real}. "
            f"Run `make stubs` to regenerate."
        )

    @pytest.mark.parametrize("name", RUNTIME_NAMES)
    def test_defaults_match(self, name):
        stub = stub_params(STUBS[name])
        real = runtime_params(getattr(core, name))

        for (pname, stub_default), (_, real_default) in zip(stub, real):
            if stub_default is None:
                assert real_default is None, (
                    f"{name}({pname}): stub says required, module has "
                    f"default {real_default!r}"
                )
                continue

            assert real_default is not None or stub_default == "None", (
                f"{name}({pname}): stub has default {stub_default}, module has none"
            )

            # Stub defaults are source text; compare by value where possible so
            # that constants (WAVE_SINE) compare equal to their int value.
            try:
                expected = eval(stub_default, vars(cycdp))  # noqa: S307
            except Exception:  # pragma: no cover - malformed stub default
                pytest.fail(f"{name}({pname}): unparseable default {stub_default!r}")

            assert expected == real_default, (
                f"{name}({pname}): stub default {stub_default} == {expected!r}, "
                f"module has {real_default!r}"
            )


class TestNoDuplicateDefinitions:
    """A function defined twice in _core.pyx has one definition silently dead.

    phase_invert was defined twice, so the documented memoryview form was
    unreachable and every caller silently got the Buffer-only version.
    """

    def test_pyx_defines_each_function_once(self):
        import re

        src = (REPO / "src" / "cycdp" / "_core.pyx").read_text()
        names = re.findall(r"^def (\w+)\(", src, re.M)
        dupes = sorted({n for n in names if names.count(n) > 1})
        assert dupes == [], (
            f"defined more than once in _core.pyx (the later definition wins "
            f"and the earlier is dead code): {dupes}"
        )

    def test_all_has_no_duplicates(self):
        dupes = sorted({n for n in cycdp.__all__ if cycdp.__all__.count(n) > 1})
        assert dupes == [], f"duplicated in __all__: {dupes}"


class TestStubIsUpToDate:
    def test_generator_output_matches_committed_stub(self):
        """The committed stub must be exactly what the generator produces."""
        result = subprocess.run(
            [sys.executable, str(GEN), "--check"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"{result.stdout}{result.stderr}\n"
            f"The committed _core.pyi differs from what scripts/gen_stubs.py "
            f"generates from _core.pyx. Run `make stubs`."
        )


class TestPhaseInvertAcceptsBothForms:
    """Regression guard for the shadowed-definition bug."""

    def test_accepts_raw_buffer(self):
        import array

        result = cycdp.phase_invert(array.array("f", [0.5, -0.25]))
        assert result.to_list() == pytest.approx([-0.5, 0.25])

    def test_accepts_buffer_object(self):
        import array

        buf = cycdp.Buffer.from_memoryview(array.array("f", [0.5, -0.25]), 1, 44100)
        result = cycdp.phase_invert(buf)
        assert result.to_list() == pytest.approx([-0.5, 0.25])

    def test_sample_rate_is_honoured_for_raw_buffers(self):
        import array

        result = cycdp.phase_invert(array.array("f", [0.5]), sample_rate=22050)
        assert result.sample_rate == 22050
