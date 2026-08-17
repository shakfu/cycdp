#!/usr/bin/env bash
#
# Build the extension with AddressSanitizer + UBSan and run the test suite
# under them.
#
# Why this exists: the unchecked-allocation and out-of-bounds class of bug is
# invisible to the test suite. Those tests report a clean pass right up until a
# NULL dereference or overflow takes down the interpreter, and a plain segfault
# tells you nothing about where. ASan turns them into a precise report -- it is
# how the cdp_spectral_time_stretch heap-buffer-overflow was diagnosed.
#
# Python itself is not instrumented, so the sanitizer runtime has to be
# preloaded into the interpreter before the instrumented .so is dlopen'd.
#
# Usage:  scripts/run_sanitizers.sh [extra pytest args...]
#
# Set CYCDP_SAN_COMMAND=fuzz to run scripts/fuzz_api.py under the sanitizer
# instead of the test suite. That combination is the one that pays: the tests
# only ever supply well-formed input, so ASan watches code paths that were
# never in doubt, whereas the fuzzer drives the paths where an out-of-bounds
# read would actually occur. Linux only -- see the DYLD note below, the fuzzer
# runs each call in a subprocess and macOS strips the runtime from children.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"

# MODE=address (default) runs ASan+UBSan; MODE=thread runs ThreadSanitizer,
# which is what validates the GIL-releasing processing calls. The two cannot be
# combined, hence separate builds.
MODE="${CYCDP_SANITIZER:-address}"
BUILD_DIR="${CYCDP_SAN_BUILD_DIR:-build/sanitize-$MODE}"
PYTHON="${CYCDP_PYTHON:-$REPO/.venv/bin/python}"

case "$MODE" in
    address) CMAKE_FLAG="-DCYCDP_SANITIZE=ON" ;;
    thread)  CMAKE_FLAG="-DCYCDP_TSAN=ON" ;;
    *) echo "error: CYCDP_SANITIZER must be 'address' or 'thread'" >&2; exit 1 ;;
esac

if [ ! -x "$PYTHON" ]; then
    echo "error: no interpreter at $PYTHON (run 'make sync', or set CYCDP_PYTHON)" >&2
    exit 1
fi

echo "==> Configuring $MODE-sanitizer build in $BUILD_DIR"
cmake -S . -B "$BUILD_DIR" \
    "$CMAKE_FLAG" \
    -DPython_EXECUTABLE="$PYTHON" \
    -DCMAKE_BUILD_TYPE=RelWithDebInfo \
    >/dev/null

echo "==> Building"
cmake --build "$BUILD_DIR" -j"$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)"

SO="$(find "$BUILD_DIR" -name '_core*.so' -o -name '_core*.pyd' | head -1)"
if [ -z "$SO" ]; then
    echo "error: no built extension found under $BUILD_DIR" >&2
    exit 1
fi

# Install the instrumented module over the one in site-packages, and put the
# original back on exit however this script ends.
#
# Locate the package without importing it: if a previous interrupted run left
# an instrumented .so in place, importing cycdp here would dlopen it before the
# sanitizer runtime is preloaded, and ASan aborts with "Interceptors are not
# working".
SITE_PKG="$("$PYTHON" -c 'import os, sysconfig; print(os.path.join(sysconfig.get_paths()["purelib"], "cycdp"))')"
if [ ! -d "$SITE_PKG" ]; then
    echo "error: cycdp is not installed at $SITE_PKG (run 'make sync')" >&2
    exit 1
fi
# Overwrite the installed extension in place, keeping whatever filename it
# already has, and put the original back on exit however this script ends.
#
# Using the *installed* name rather than the built one is load-bearing. The
# project is installed editable, and scikit-build-core's editable finder
# hardcodes the extension path -- {'cycdp._core': 'cycdp/_core.cpython-313-darwin.so'}
# in _editable_skbc_cycdp.py -- so the module resolves by that exact name and
# no other. Installing under the build's own name instead leaves the mapped
# file missing, and every import dies with a dlopen "no such file" naming a
# path that was never written.
#
# This only became reachable when wheels moved to abi3, because the build
# variants stopped agreeing on a filename: an ordinary build now produces
# _core.abi3.so while a coverage build still produces
# _core.cpython-3XY-<plat>.so.
INSTALLED="$(find "$SITE_PKG" -maxdepth 1 \( -name '_core*.so' -o -name '_core*.pyd' \))"
COUNT="$(printf '%s' "$INSTALLED" | grep -c . || true)"

if [ "$COUNT" -eq 0 ]; then
    echo "error: no built extension installed in $SITE_PKG (run 'make sync')" >&2
    exit 1
fi
if [ "$COUNT" -gt 1 ]; then
    # Python imports exactly one of these and ignores the rest, so carrying on
    # would instrument a module the interpreter never loads.
    echo "error: $COUNT extensions installed in $SITE_PKG:" >&2
    printf '  %s\n' $INSTALLED >&2
    echo "Remove the extras and reinstall: uv sync --dev --no-cache --reinstall-package cycdp" >&2
    exit 1
fi

TARGET="$INSTALLED"
BACKUP="$(mktemp -t cycdp_core_backup.XXXXXX)"

restore() {
    if [ -s "$BACKUP" ]; then
        cp "$BACKUP" "$TARGET"
        echo "==> Restored the original (non-instrumented) extension"
    fi
    rm -f "$BACKUP"
}
trap restore EXIT

cp "$TARGET" "$BACKUP"
cp "$SO" "$TARGET"

# detect_leaks=0: CPython does not free its interpreter state at exit, so leak
# detection reports thousands of false positives that drown out real findings.
# Memory *errors* -- overflows, use-after-free, NULL derefs -- are still caught.
export ASAN_OPTIONS="detect_leaks=0:detect_container_overflow=0:abort_on_error=0"
export UBSAN_OPTIONS="print_stacktrace=1"
export TSAN_OPTIONS="halt_on_error=0:second_deadlock_stack=1"

case "$(uname -s)" in
    Darwin)
        if [ "$MODE" = "thread" ]; then
            RUNTIME="$(clang -print-file-name=libclang_rt.tsan_osx_dynamic.dylib)"
        else
            RUNTIME="$(clang -print-file-name=libclang_rt.asan_osx_dynamic.dylib)"
        fi
        export DYLD_INSERT_LIBRARIES="$RUNTIME"
        # macOS strips DYLD_* when spawning a child process, so tests that
        # re-exec Python would load the instrumented module without the
        # runtime and die with "Interceptors are not working". Those
        # subprocess tests are covered by the ordinary (non-sanitizer) CI run,
        # and by the Linux sanitizer job, where LD_PRELOAD is inherited.
        EXTRA_ARGS=(
            --deselect tests/test_cli.py::TestEntryPoint
            --deselect tests/test_rng.py::TestNoProcessGlobalState
        )
        ;;
    Linux)
        if [ "$MODE" = "thread" ]; then
            RUNTIME="$(gcc -print-file-name=libtsan.so 2>/dev/null || true)"
            if [ -z "$RUNTIME" ] || [ "$RUNTIME" = "libtsan.so" ]; then
                RUNTIME="$(clang -print-file-name=libclang_rt.tsan-x86_64.so)"
            fi
        else
            RUNTIME="$(gcc -print-file-name=libasan.so 2>/dev/null || true)"
            if [ -z "$RUNTIME" ] || [ "$RUNTIME" = "libasan.so" ]; then
                RUNTIME="$(clang -print-file-name=libclang_rt.asan-x86_64.so)"
            fi
        fi
        export LD_PRELOAD="$RUNTIME"
        # LD_PRELOAD is inherited by children, so subprocess tests work here.
        EXTRA_ARGS=()
        ;;
    *)
        echo "error: sanitizer runs are not wired up for $(uname -s)" >&2
        exit 1
        ;;
esac

echo "==> Sanitizer runtime: $RUNTIME"

if [ "${CYCDP_SAN_COMMAND:-pytest}" = "fuzz" ]; then
    if [ "$(uname -s)" != "Linux" ]; then
        echo "error: CYCDP_SAN_COMMAND=fuzz needs LD_PRELOAD inheritance (Linux only)" >&2
        exit 1
    fi
    echo "==> Fuzzing the API under the sanitizer"
    # A longer per-function timeout than the bare run: instrumented code is
    # several times slower, and a timeout here would read as a hang.
    "$PYTHON" scripts/fuzz_api.py --timeout 180 --jobs 2 "$@"
else
    echo "==> Running tests"
    "$PYTHON" -m pytest tests/ -q -p no:cacheprovider "${EXTRA_ARGS[@]}" "$@"
fi
