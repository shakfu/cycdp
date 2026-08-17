#!/usr/bin/env python3
"""Call every public cycdp function with hostile parameter values.

The test suite exercises the API with well-formed input, and the sanitizer jobs
can only observe the paths the tests take -- so between them they validated the
library against exactly the inputs it was designed for. This closes that gap:
it drives each parameter to the values a caller reaches by typo, by an
unchecked division, or on purpose.

It is what found the four segfaults and thirteen hangs listed under
Unreleased in the CHANGELOG, all
reachable from ordinary Python and from the CLI, which accepts "nan" for any
float option because argparse does.

Every call runs in its own subprocess with a timeout, because the failures this
looks for are signals and infinite loops, not exceptions -- an in-process
harness would simply die. A case is a failure if the worker is killed by a
signal or times out. Any exception is fine: raising is the correct response to
a bad argument, and the point is that nothing gets through to the C layer.

Usage:
    python scripts/fuzz_api.py                # whole API
    python scripts/fuzz_api.py time_stretch   # one function
    python scripts/fuzz_api.py --timeout 60   # slower under a sanitizer

Exit status is 0 when no call crashed or hung.
"""

from __future__ import annotations

import argparse
import ast
import concurrent.futures as cf
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PYI = REPO / "src" / "cycdp" / "_core.pyi"

# Values a float parameter should survive: the non-finite ones that pass every
# comparison-based clamp in C, and finite magnitudes far outside any sane range.
#
# Written as source text rather than values: repr(float("nan")) is "nan",
# which is not a valid literal, so interpolating the list would produce a
# worker that fails to import.
HOSTILE_FLOATS = (
    '[0.0, -1.0, -1e9, 1e9, float("nan"), float("inf"), float("-inf"), 1e-30]'
)
HOSTILE_INTS = "[0, -1, -(2**31), 2**31 - 1, 3, 2**62]"


# ---------------------------------------------------------------------------
# Worker: runs in a subprocess, one function per invocation.
# ---------------------------------------------------------------------------

WORKER_TEMPLATE = r"""
import array, ast, math, sys
import cycdp

SR = 44100
HOSTILE_FLOATS = @@FLOATS@@
HOSTILE_INTS = @@INTS@@


def sine(n=8192, ch=1):
    s = array.array("f", [0.5 * math.sin(2 * math.pi * 440 * i / SR)
                          for i in range(n * ch)])
    return cycdp.Buffer.from_memoryview(s, ch, SR)


def sane(ann):
    if ann == "Buffer":
        return sine()
    if ann == "int":
        return 4
    if ann == "float":
        return 0.5
    if ann == "bool":
        return False
    if ann == "str":
        return "linear"
    if ann.startswith(("list", "Sequence")):
        return [sine(), sine()]
    return None


def hostile(ann):
    if ann == "int":
        return HOSTILE_INTS
    if ann == "float":
        return HOSTILE_FLOATS
    if ann == "bool":
        return [True, False]
    if ann == "str":
        return ["", "bogus"]
    if ann.startswith(("list", "Sequence")):
        return [[], [0.0]]
    return None


fname = sys.argv[1]
tree = ast.parse(open(sys.argv[2]).read())
fn = next((n for n in tree.body
           if isinstance(n, ast.FunctionDef) and n.name == fname), None)
if fn is None:
    print("SKIP not in stub")
    raise SystemExit(0)

args = fn.args.args
defaults = [None] * (len(args) - len(fn.args.defaults)) + list(fn.args.defaults)
params = []
for a, d in zip(args, defaults):
    ann = ast.unparse(a.annotation) if a.annotation else "Any"
    dv = ast.literal_eval(d) if isinstance(d, ast.Constant) else None
    params.append((a.arg, ann, dv, d is not None))

base = {}
for pname, ann, dv, has_default in params:
    if has_default:
        base[pname] = dv
    else:
        v = sane(ann)
        if v is None:
            print(f"SKIP cannot synthesise {pname}: {ann}")
            raise SystemExit(0)
        base[pname] = v

func = getattr(cycdp, fname, None)
if func is None:
    print("SKIP not exported")
    raise SystemExit(0)


def run(label, kwargs):
    sys.stdout.write(f"CASE {fname} {label} ... ")
    sys.stdout.flush()
    try:
        func(**kwargs)
        sys.stdout.write("ok\n")
    except Exception as e:
        sys.stdout.write(f"raised {type(e).__name__}\n")
    sys.stdout.flush()


run("baseline", dict(base))
for pname, ann, dv, _ in params:
    if ann == "Buffer":
        continue
    vals = hostile(ann)
    if not vals:
        continue
    for v in vals:
        kw = dict(base)
        for p2, a2, _d2, hd2 in params:
            if a2 == "Buffer":
                kw[p2] = sine()
            elif a2.startswith("list") and not hd2:
                kw[p2] = [sine(), sine()]
        kw[pname] = v
        run(f"{pname}={v!r}", kw)
"""

WORKER = WORKER_TEMPLATE.replace("@@FLOATS@@", HOSTILE_FLOATS).replace(
    "@@INTS@@", HOSTILE_INTS
)


def stub_functions() -> list[str]:
    tree = ast.parse(PYI.read_text())
    return [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]


def run_one(name: str, worker: Path, timeout: int, scratch: Path):
    """Returns (name, failure_kind, last_case, case_count, diagnostics).

    `diagnostics` is the worker's stderr, which is where a sanitizer writes its
    report. Discarding it made the CI job say only which call failed and not
    why, which is most of the value of running the sweep under ASan at all.
    """
    try:
        r = subprocess.run(
            [sys.executable, str(worker), name, str(PYI)],
            capture_output=True,
            text=True,
            timeout=timeout,
            # In a scratch directory, not the repo: the hostile values for a
            # `str` parameter include plausible-looking filenames, and
            # write_file() takes one, so the sweep drops WAV files wherever it
            # is run from.
            cwd=scratch,
        )
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or "") if isinstance(e.stdout, str) else ""
        return name, "TIMEOUT", last_case(out), count_cases(out), ""
    kind = None
    if r.returncode < 0:
        kind = f"SIGNAL {-r.returncode}"
    elif r.returncode != 0:
        kind = f"EXIT {r.returncode}"
    return name, kind, last_case(r.stdout), count_cases(r.stdout), r.stderr or ""


def last_case(out: str) -> str:
    lines = [ln for ln in out.splitlines() if ln.startswith("CASE ")]
    return lines[-1].split(" ... ")[0] if lines else "(no case reached)"


def count_cases(out: str) -> int:
    return sum(1 for ln in out.splitlines() if ln.startswith("CASE "))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("functions", nargs="*", help="limit to these functions")
    ap.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="seconds per function (raise under a sanitizer)",
    )
    ap.add_argument("--jobs", type=int, default=4)
    args = ap.parse_args()

    targets = args.functions or stub_functions()

    worker = REPO / "build" / ".fuzz_worker.py"
    worker.parent.mkdir(parents=True, exist_ok=True)
    worker.write_text(WORKER)

    scratch = Path(tempfile.mkdtemp(prefix="cycdp-fuzz-"))

    failures, total = [], 0
    with cf.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [
            pool.submit(run_one, n, worker, args.timeout, scratch) for n in targets
        ]
        for fut in cf.as_completed(futures):
            name, kind, case, cases, diagnostics = fut.result()
            total += cases
            if kind:
                failures.append((name, kind, case, diagnostics))
                print(f"FAIL {name}: {kind} at {case}", flush=True)

    shutil.rmtree(scratch, ignore_errors=True)

    print(f"\n{total} calls across {len(targets)} functions")
    if failures:
        print(f"{len(failures)} function(s) crashed or hung:")
        for name, kind, case, _ in sorted(failures):
            print(f"  {name}: {kind} at {case}")

        # The report, not just the verdict. Under a sanitizer this is the
        # stack trace naming the offending line.
        for name, kind, case, diagnostics in sorted(failures):
            if not diagnostics.strip():
                continue
            print(f"\n----- {name} ({kind} at {case}) -----")
            lines = diagnostics.strip().splitlines()
            for line in lines[:40]:
                print(f"  {line}")
            if len(lines) > 40:
                print(f"  ... {len(lines) - 40} more lines")
        return 1
    print("no crashes, no hangs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
