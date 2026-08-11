#!/usr/bin/env python3
"""Invoke Cython, optionally emitting absolute source paths for coverage.

Cython derives the source path it embeds from the module name, so under a src/
layout it records ``cycdp/_core.pyx``. That path does not resolve from the repo
root where pytest runs, and Cython.Coverage's file_tracer returns None for a
path it cannot stat -- so the extension is silently not traced, and coverage
reports a confident total that describes only cli.py.

Setting ``Options.relative_path_in_code_position_comments = False`` makes Cython
emit the absolute path consistently in all three places that have to agree:

  - the ``__pyx_f`` filename table
  - the packed string constant that becomes ``code.co_filename``
  - the ``/* "<path>":<line> */`` comments the coverage plugin parses

There is no command-line flag for this option, hence this wrapper. It is only
used for coverage builds (`make coverage`); normal builds go through plain
`cython` and are byte-for-byte unaffected, which matters because absolute build
paths must never end up in a released wheel.

Usage:
    run_cython.py --absolute-paths -- <cython args...>
    run_cython.py -- <cython args...>
"""

from __future__ import annotations

import sys


def main(argv: list[str]) -> int:
    args = argv[1:]

    absolute_paths = False
    if args and args[0] == "--absolute-paths":
        absolute_paths = True
        args = args[1:]

    if args and args[0] == "--":
        args = args[1:]

    if not args:
        print("error: no arguments passed through to cython", file=sys.stderr)
        return 2

    from Cython.Compiler import Options

    if absolute_paths:
        # CompilationOptions seeds itself from Options.default_options, not
        # from the module attribute, so the dict is what has to change. Set
        # both: the attribute is what Cython's own docs point at, and pinning
        # only one of them is the kind of thing a Cython upgrade silently
        # breaks. Fail loudly if neither key is recognised.
        key = "relative_path_in_code_position_comments"
        if key not in Options.default_options:
            print(
                f"error: Cython {getattr(__import__('Cython'), '__version__', '?')} "
                f"has no {key!r} in Options.default_options; coverage of "
                f"_core.pyx would silently report nothing. Refusing to build.",
                file=sys.stderr,
            )
            return 1
        Options.default_options[key] = False
        setattr(Options, key, False)

    from Cython.Compiler.Main import main as cython_main

    sys.argv = ["cython", *args]
    cython_main(command_line=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
