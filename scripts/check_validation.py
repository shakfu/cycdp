#!/usr/bin/env python3
"""Fail if a public function in _core.pyx has an unguarded float parameter.

Every `double` parameter of every module-level `def` must be checked by one of
`_finite()`, `_in_range()` or `_at_most()` before the function does any work.
This is not style enforcement. NaN and Inf reach the C layer unchanged, where
`(size_t)nan` is undefined behaviour and every comparison-based clamp
(`if (x < 0) x = 0;`) silently lets them through. Fuzzing the API before these
guards existed produced four segfaults and thirteen hangs, all reachable from
ordinary Python and from the CLI, which accepts "nan" for any float option.

The failure mode this guards against is a *new* operation being added without a
check: the existing ones would all still pass, and the gap would only show up
as a crash in someone else's process.

Usage:
    make validation-check     # or: python scripts/check_validation.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PYX = REPO / "src" / "cycdp" / "_core.pyx"

GUARD = re.compile(r"^\s+_(?:finite|in_range|at_most)\((\w+),", re.M)

# `Context`/`Buffer` plumbing that takes no user-facing float, plus the two
# pure conversion helpers, which are total functions over the reals: passing
# NaN to gain_to_db() returns NaN, which is the right answer, not a crash.
EXEMPT = {"gain_to_db", "db_to_gain"}


def functions(src: str):
    """Yield (name, params, body) for each module-level `def`."""
    for m in re.finditer(r"^def (\w+)\(", src, re.M):
        name = m.group(1)
        i = m.end() - 1
        depth = 0
        j = i
        while j < len(src):
            if src[j] == "(":
                depth += 1
            elif src[j] == ")":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        sig = src[i + 1 : j]

        nxt = src.find("\ndef ", j)
        body = src[j : nxt if nxt != -1 else len(src)]

        parts, depth2, cur = [], 0, ""
        for ch in sig:
            if ch in "([{":
                depth2 += 1
            elif ch in ")]}":
                depth2 -= 1
            if ch == "," and depth2 == 0:
                parts.append(cur)
                cur = ""
            else:
                cur += ch
        if cur.strip():
            parts.append(cur)

        params = []
        for p in parts:
            p = " ".join(p.split())
            lhs = p.split("=", 1)[0].replace("not None", "").strip()
            toks = lhs.split()
            if len(toks) >= 2:
                params.append((" ".join(toks[:-1]), toks[-1]))
        yield name, params, body


def main() -> int:
    src = PYX.read_text()
    problems = []

    for name, params, body in functions(src):
        if name in EXEMPT:
            continue
        guarded = set(GUARD.findall(body))
        for ctype, pname in params:
            if ctype != "double":
                continue
            if pname not in guarded:
                problems.append(
                    f"  {name}({pname}): no _finite/_in_range/_at_most call"
                )

    if problems:
        print(
            f"error: {len(problems)} float parameter(s) in "
            f"{PYX.relative_to(REPO)} reach the C layer unvalidated:\n"
            + "\n".join(sorted(problems))
            + '\n\nAdd _finite(p, "p") at the top of the function body, or '
            "_in_range(...)/_at_most(...) if the value scales an allocation "
            "or a loop count.",
            file=sys.stderr,
        )
        return 1

    total = sum(
        1 for _, params, _ in functions(src) for ctype, _ in params if ctype == "double"
    )
    print(f"all {total} float parameters in {PYX.relative_to(REPO)} are guarded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
