"""Guard the coverage instrumentation itself.

M3: coverage used to report 94% while measuring only cli.py. _core.pyx -- 6,400
lines and the entire library -- contributed zero statements, because a Cython
extension is invisible to coverage.py unless it is compiled with line tracing
*and* the pieces below all line up. Every one of them fails silently: the build
succeeds, the tests pass, and the report is simply missing the module while
still printing a confident total.

The moving parts, any of which can break on a Cython or CPython upgrade:

  - linetrace=True + binding=True Cython directives
  - CYTHON_TRACE=1 at C compile time
  - CYTHON_USE_SYS_MONITORING=0, or Cython instruments via PEP 669 on
    Python 3.12+ and coverage's plugin mechanism never sees the events
  - the embedded source path resolving from the repo root
  - the Cython.Coverage plugin being registered

Rather than assert on each, this asserts the observable outcome: when coverage
is running, _core.pyx must actually be measured.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PYX = REPO / "src" / "cycdp" / "_core.pyx"


def active_coverage():
    """Return the running Coverage instance, or None if not measuring."""
    try:
        import coverage
    except ImportError:
        return None
    return coverage.Coverage.current()


def test_core_pyx_is_measured_when_coverage_is_running():
    """If coverage is on, the Cython core must be in the report.

    Skipped when not running under coverage, so the ordinary `make test` path
    is unaffected.
    """
    cov = active_coverage()
    if cov is None:
        pytest.skip("not running under coverage")

    # Exercise a Cython code path so there is something to record even if this
    # test somehow runs first.
    import array

    import cycdp

    cycdp.gain(array.array("f", [0.5] * 64), gain_factor=2.0)

    measured = {Path(f).resolve() for f in cov.get_data().measured_files()}

    assert PYX.resolve() in measured, (
        "coverage is running but src/cycdp/_core.pyx is not being measured, so "
        "the reported percentage describes only the pure-Python files and "
        "understates nothing while hiding 6,400 lines. Rebuild with "
        "`make coverage` (CYCDP_COVERAGE=ON). If that is what you did, the "
        "Cython/CPython tracing contract has changed -- see CMakeLists.txt "
        "and scripts/run_cython.py.\n"
        f"measured: {sorted(str(m) for m in measured)}"
    )


def test_measured_core_has_a_plausible_statement_count():
    """A trivially small line count means the line map failed to build."""
    cov = active_coverage()
    if cov is None:
        pytest.skip("not running under coverage")

    data = cov.get_data()
    match = next(
        (f for f in data.measured_files() if Path(f).name == "_core.pyx"), None
    )
    if match is None:
        pytest.skip("covered by test_core_pyx_is_measured_when_coverage_is_running")

    executed = len(data.lines(match) or ())
    assert executed > 200, (
        f"_core.pyx is measured but only {executed} lines were recorded; the "
        f"Cython line map is probably not being parsed from the generated C file"
    )
