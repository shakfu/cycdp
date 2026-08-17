"""Processing calls must release the GIL and stay correct under contention.

M1: `_core.pyx` had zero `nogil` blocks in 6,425 lines, so every FFT, granular
pass and reverb tail ran holding the interpreter lock. No cycdp operation could
overlap with any other Python work -- which, for a library whose selling point
over the CDP command-line tools is "no subprocess overhead", gave up the one
thing subprocesses did offer.

Releasing the GIL required M6 first (global `srand`/`rand` would have raced)
and a per-thread context: every call writes `ctx->error_msg` and draws from
`ctx->prng_state`, so the previous process-wide singleton would have let
concurrent operations corrupt each other's errors and random streams.

These tests pin both halves: that the lock is genuinely released, and that
concurrency does not change any result.
"""

from __future__ import annotations

import array
import concurrent.futures as cf
import math
import threading
import time
from typing import ClassVar

import pytest

import cycdp

SR = 22050


def _sine(seconds: float = 0.5, sr: int = SR) -> cycdp.Buffer:
    n = int(sr * seconds)
    samples = array.array(
        "f", [0.4 * math.sin(2 * math.pi * 220 * i / sr) for i in range(n)]
    )
    return cycdp.Buffer.from_memoryview(samples, 1, sr)


@pytest.fixture(scope="module")
def audio() -> cycdp.Buffer:
    return _sine()


def _coverage_is_tracing() -> bool:
    """True when the extension is built with Cython line tracing.

    `make coverage` sets CYTHON_TRACE_NOGIL=1, so the trace hook fires inside
    `with nogil:` blocks and re-acquires the GIL on every line. That serialises
    exactly the work these timing tests measure -- parallel speedup drops to
    ~1.2x purely from instrumentation. The GIL behaviour itself is unchanged,
    so it is measured in the ordinary (non-instrumented) builds instead.
    """
    try:
        import coverage
    except ImportError:
        return False
    return coverage.Coverage.current() is not None


def _sanitizer_is_active() -> bool:
    """True when ASan or TSan is loaded into this interpreter.

    Both keep per-thread shadow memory and a quarantine that they never return
    to the OS, so process RSS grows by hundreds of megabytes over a few
    thousand threads regardless of whether the library leaks anything. Any
    RSS-based measurement is meaningless under them -- measured at 212 MB of
    sanitizer bookkeeping against the ~1 MB the leak test is looking for.
    """
    import ctypes

    process = ctypes.CDLL(None)
    for symbol in ("__asan_init", "__tsan_init"):
        try:
            getattr(process, symbol)
        except AttributeError:
            continue
        return True
    return False


class TestGilIsReleased:
    # Threshold set from measurement, not assumption. With a short (~0.06s)
    # call the surrounding Python work dominates the window and a GIL-holding
    # build still measured 0.10-0.19 retention -- so an intuitive "it would be
    # near zero" bound does not discriminate. Lengthening the call so the C
    # section dominates separates the cases cleanly:
    #
    #     GIL released (nogil):  0.92
    #     GIL held (nogil removed, measured by reverting it):  0.03
    #
    # 0.25 sits 8x above the failure case and 3.7x below the pass case. The
    # original 0.4 was chosen without measuring either and failed CI at 0.385
    # on an emulated x86_64 macOS runner.
    MIN_RETAINED = 0.25
    ATTEMPTS = 3

    # Long enough that the C call dominates the measured window.
    INPUT_SECONDS = 4.0
    STRETCH_FACTOR = 8.0

    @staticmethod
    def _measure_retention() -> tuple[float, float]:
        """Python-thread throughput during a DSP call, relative to idle."""
        buf = _sine(TestGilIsReleased.INPUT_SECONDS, 44100)
        counter = {"n": 0}
        stop = threading.Event()

        def spin():
            while not stop.is_set():
                counter["n"] += 1

        thread = threading.Thread(target=spin)
        thread.start()
        try:
            time.sleep(0.3)
            idle_rate = counter["n"] / 0.3

            counter["n"] = 0
            start = time.perf_counter()
            cycdp.time_stretch(buf, TestGilIsReleased.STRETCH_FACTOR)
            elapsed = time.perf_counter() - start
            busy_rate = counter["n"] / elapsed
        finally:
            stop.set()
            thread.join()

        return (busy_rate / idle_rate if idle_rate else 0.0), elapsed

    @pytest.mark.timing
    @pytest.mark.skipif(
        _coverage_is_tracing(),
        reason="Cython line tracing re-acquires the GIL per line; see _coverage_is_tracing",
    )
    def test_python_threads_keep_running_during_dsp(self):
        """A pure-Python thread must not be starved by a DSP call.

        Counts iterations of a Python loop while a long operation runs. Takes
        the best of several attempts: scheduling noise only ever depresses the
        figure, so the maximum is the honest measure of what the runtime can
        achieve.
        """
        results = [self._measure_retention() for _ in range(self.ATTEMPTS)]
        retained, elapsed = max(results, key=lambda r: r[0])

        assert retained > self.MIN_RETAINED, (
            f"a Python thread retained only {retained:.0%} of its throughput "
            f"during a {elapsed:.3f}s DSP call (best of {self.ATTEMPTS}), which "
            f"means the GIL was held for most of it"
        )


class TestConcurrentResultsMatchSequential:
    """Concurrency must not change any output."""

    # seed=0 means "derive from the clock" and is deliberately not
    # reproducible, so reproducibility checks use non-zero seeds.
    SEEDS = tuple(range(1, 9))

    def test_seeded_operations_are_unaffected_by_contention(self, audio):
        expected = {
            seed: cycdp.distort_shuffle(audio, 3, seed=seed).to_bytes()
            for seed in self.SEEDS
        }

        def run(seed):
            return seed, cycdp.distort_shuffle(audio, 3, seed=seed).to_bytes()

        with cf.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(run, list(self.SEEDS) * 6))

        mismatched = [seed for seed, data in results if data != expected[seed]]
        assert not mismatched, (
            f"seeds {sorted(set(mismatched))} produced different output under "
            f"contention -- the per-thread PRNG state is being shared"
        )

    def test_mixed_workload_matches_sequential(self, audio):
        operations = [
            ("time_stretch", lambda: cycdp.time_stretch(audio, 2.0)),
            ("pitch_shift", lambda: cycdp.pitch_shift(audio, 7)),
            ("reverb", lambda: cycdp.reverb(audio, decay_time=0.5)),
            ("brassage", lambda: cycdp.brassage(audio, seed=11)),
            ("wrappage", lambda: cycdp.wrappage(audio, duration=0.3, seed=4)),
        ]
        sequential = {name: fn().to_bytes() for name, fn in operations}

        def run(i):
            name, fn = operations[i % len(operations)]
            return name, fn().to_bytes()

        with cf.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(run, range(50)))

        wrong = {name for name, data in results if data != sequential[name]}
        assert not wrong, f"{sorted(wrong)} produced different output on threads"

    def test_errors_are_isolated_between_threads(self, audio):
        """ctx->error_msg is per-thread, so messages must not interleave."""

        def provoke(i):
            try:
                if i % 2:
                    cycdp.time_stretch(audio, -1.0)
                else:
                    cycdp.read_file("/nonexistent/definitely_not_here.wav")
            except (ValueError, cycdp.CDPError) as exc:
                return type(exc).__name__, str(exc)
            return None

        with cf.ThreadPoolExecutor(max_workers=8) as pool:
            results = [r for r in pool.map(provoke, range(40)) if r]

        assert len(results) == 40, "every call should have raised"
        for kind, message in results:
            if kind == "ValueError":
                assert "positive" in message
            else:
                assert "Cannot open" in message, (
                    f"error message crossed threads: {message!r}"
                )


class TestParallelSpeedup:
    @pytest.mark.timing
    @pytest.mark.skipif(
        _coverage_is_tracing(),
        reason="Cython line tracing re-acquires the GIL per line; see _coverage_is_tracing",
    )
    def test_threads_give_real_speedup(self):
        """Wall-clock must improve with threads, not merely stay level.

        A loose bound (>1.5x on 4 workers): the point is to catch the GIL
        being reintroduced, not to benchmark the machine.
        """
        buf = _sine(1.0, 44100)
        calls = 8

        def work(_):
            return cycdp.time_stretch(buf, 3.0).frame_count

        start = time.perf_counter()
        for i in range(calls):
            work(i)
        sequential = time.perf_counter() - start

        with cf.ThreadPoolExecutor(max_workers=4) as pool:
            start = time.perf_counter()
            list(pool.map(work, range(calls)))
            parallel = time.perf_counter() - start

        speedup = sequential / parallel
        assert speedup > 1.5, (
            f"{calls} calls took {sequential:.3f}s sequentially and "
            f"{parallel:.3f}s on 4 threads ({speedup:.2f}x) -- the GIL is "
            f"probably being held during processing"
        )


class TestNogilCoverage:
    """Static guard: processing calls must stay inside `with nogil:` blocks."""

    def test_processing_calls_are_wrapped(self):
        import re
        from pathlib import Path

        src = (
            Path(__file__).resolve().parent.parent / "src" / "cycdp" / "_core.pyx"
        ).read_text()
        lines = src.split("\n")

        # Buffer management runs under the GIL by design: its arguments read
        # Python attributes.
        allowed_with_gil = {
            "cdp_lib_buffer_create",
            "cdp_lib_buffer_free",
            "cdp_lib_buffer_from_data",
            "cdp_lib_get_error",
            "cdp_lib_init",
            "cdp_lib_thread_ctx",
            "cdp_lib_cleanup",
        }

        unwrapped = []
        for i, line in enumerate(lines):
            m = re.search(r"=\s*(cdp_lib_\w+)\(", line)
            if not m or m.group(1) in allowed_with_gil:
                continue
            # Walk back over the current block looking for `with nogil:`.
            for prev in range(i - 1, max(i - 4, -1), -1):
                if lines[prev].strip() == "with nogil:":
                    break
            else:
                unwrapped.append(f"{i + 1}: {line.strip()}")

        assert not unwrapped, (
            "these processing calls hold the GIL; wrap them in `with nogil:`:\n  "
            + "\n  ".join(unwrapped)
        )


class TestBufferOwnership:
    """M2: C buffers must be released even when conversion raises.

    `_cdp_lib_to_buffer` used to return a Buffer and leave the caller to free
    the C buffer on the *next* line. `Buffer.create` raises MemoryError on
    allocation failure, so that free was skipped and the output buffer -- often
    the larger of the two -- leaked, precisely when memory was already scarce.

    Measured with allocation-failure injection before the fix: 500 induced
    failures of time_stretch grew RSS by 87 MB (~179 KB each, the exact size of
    the output buffer). After: 64 KB, i.e. noise.

    Reproducing that needs a platform-specific allocator interposer, so these
    guard the structure instead.
    """

    @staticmethod
    def _core_source() -> str:
        from pathlib import Path

        return (
            Path(__file__).resolve().parent.parent / "src" / "cycdp" / "_core.pyx"
        ).read_text()

    def test_conversion_helper_frees_on_every_path(self):
        import re

        src = self._core_source()
        m = re.search(
            r"cdef Buffer _take_cdp_lib_buffer\(.*?\n(?=\n\ncdef |\n\ndef )", src, re.S
        )
        assert m, "_take_cdp_lib_buffer not found"
        body = m.group(0)
        assert "finally:" in body, (
            "_take_cdp_lib_buffer must free its buffer in a finally block, or a "
            "raising Buffer.create leaks it"
        )
        assert "cdp_lib_buffer_free(lib_buf)" in body.split("finally:")[1], (
            "the finally block must free the buffer it took ownership of"
        )

    def test_callers_do_not_double_free_the_taken_buffer(self):
        """The helper owns the pointer; freeing it again would be a double free."""
        import re

        lines = self._core_source().split("\n")
        offenders = []
        for i, line in enumerate(lines):
            m = re.search(r"_take_cdp_lib_buffer\((\w+)\)", line)
            if not m or "cdef Buffer _take_cdp_lib_buffer" in line:
                continue
            ptr = m.group(1)
            following = "\n".join(lines[i + 1 : i + 6])
            if re.search(rf"cdp_lib_buffer_free\({ptr}\)", following):
                offenders.append(f"{i + 1}: {line.strip()}")
        assert not offenders, (
            "_take_cdp_lib_buffer already freed these; freeing again is a "
            "double free:\n  " + "\n  ".join(offenders)
        )

    def test_paired_inputs_use_the_safe_helper(self):
        """Two consecutive conversions leak the first if the second raises."""
        import re

        lines = self._core_source().split("\n")
        pattern = re.compile(
            r"^\s*cdef cdp_lib_buffer\* \w+ = _buffer_to_cdp_lib\(\w+\)\s*$"
        )
        offenders = [
            f"{i + 1}: {lines[i].strip()}"
            for i in range(len(lines) - 1)
            if pattern.match(lines[i]) and pattern.match(lines[i + 1])
        ]
        assert not offenders, (
            "convert buffer pairs with _buffer_pair_to_cdp_lib, which releases "
            "the first if the second fails:\n  " + "\n  ".join(offenders)
        )


class TestThreadContextLifetime:
    """Each thread's context must be released when the thread exits.

    A plain thread-local pointer has no destructor hook, so the context -- 528
    bytes of error buffer and PRNG state -- used to be retained for the life of
    the process, once per thread that ever called in. Harmless for a fixed
    worker pool; an unbounded leak for a thread-per-request server. The fix is
    a pthread key destructor (FLS on Windows).
    """

    THREADS = 2000

    def _peak_rss_kb(self):
        import resource
        import sys

        r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # macOS reports bytes, Linux kilobytes.
        return r // 1024 if sys.platform == "darwin" else r

    @pytest.mark.skipif(
        _sanitizer_is_active(),
        reason="sanitizer shadow memory swamps the measurement; see _sanitizer_is_active",
    )
    def test_short_lived_threads_do_not_accumulate_contexts(self, audio):
        pytest.importorskip("resource", reason="POSIX-only measurement")
        import gc

        def work():
            cycdp.bitcrush(audio)

        # Warm up: the first calls fault in pages that would otherwise be
        # counted against the measured window.
        for _ in range(20):
            t = threading.Thread(target=work)
            t.start()
            t.join()

        gc.collect()
        before = self._peak_rss_kb()
        for _ in range(self.THREADS):
            t = threading.Thread(target=work)
            t.start()
            t.join()
        gc.collect()
        growth = self._peak_rss_kb() - before

        # ru_maxrss is a high-water mark, so this only ever detects growth --
        # exactly what a leak is. One leaked context per thread would be about
        # 1 MB at this thread count; the bound is set well below that and well
        # above the ~80 KB of allocator noise actually observed.
        leaked_kb = self.THREADS * 528 // 1024
        assert growth < leaked_kb // 2, (
            f"peak RSS grew {growth} KB over {self.THREADS} short-lived "
            f"threads; leaking one context per thread would be about "
            f"{leaked_kb} KB, so the thread-exit destructor is not firing"
        )

    def test_release_thread_context_is_safe_to_call_anytime(self):
        """Including with no context, twice, and before further work."""
        cycdp.release_thread_context()
        cycdp.release_thread_context()
        buf = cycdp.Buffer.create(1024, 1, 44100)
        assert cycdp.bitcrush(buf).frame_count > 0
        cycdp.release_thread_context()
        # A fresh context must be created transparently.
        assert cycdp.bitcrush(buf).frame_count > 0

    def test_release_does_not_break_seeded_reproducibility(self):
        """A seeded operation is seeded per call, not per context."""
        buf = cycdp.Buffer.create(8192, 1, 44100)
        first = cycdp.distort_shuffle(buf, chunk_count=16, seed=42).to_list()
        cycdp.release_thread_context()
        second = cycdp.distort_shuffle(buf, chunk_count=16, seed=42).to_list()
        assert first == second


class TestThreadSafetyClaimHolds:
    """M7: back the guarantee documented in projects/libcdp/README.md.

    That README claimed "thread-safe design (no global state)" while the
    bindings held a shared context, cdp_distort.c used global srand/rand, and
    three modules kept file-static generators. The claim is accurate now, and
    these tests are what keep it accurate: new mutable file-scope state on a
    processing path silently reintroduces exactly what was fixed.
    """

    # Documented exceptions, each recorded in the README's "Not guaranteed"
    # and "Not applicable" lists.
    ALLOWED: ClassVar[set[tuple[str, str]]] = {
        # Per-thread by construction.
        ("cdp_lib.c", "cdp_tls_ctx"),
        # The pthread key (FLS index on Windows) whose destructor frees each
        # thread's context at thread exit. Written once under pthread_once /
        # InitOnceExecuteOnce and read-only afterwards, so it is shared but not
        # raced. Documented in projects/libcdp/README.md.
        ("cdp_lib.c", "cdp_tls_key"),
        ("cdp_lib.c", "cdp_tls_once"),
        ("cdp_lib.c", "cdp_tls_key_ok"),
        ("cdp_lib.c", "cdp_fls_index"),
        ("cdp_lib.c", "cdp_fls_once"),
        # Process-wide I/O slots. Unreachable: nothing outside cdp_shim.c
        # calls into it. Would need thread-local storage before being used.
        ("cdp_shim.c", "g_input_slots"),
        ("cdp_shim.c", "g_output_buf"),
        ("cdp_shim.c", "g_temp_slots"),
        ("cdp_shim.c", "g_temp_slot_used"),
        ("cdp_shim.c", "g_input_buf"),
    }

    def test_no_undocumented_mutable_file_scope_state(self):
        import re
        from pathlib import Path

        repo = Path(__file__).resolve().parent.parent
        found = set()
        for src in sorted((repo / "projects" / "libcdp").rglob("*.c")):
            if "test" in src.name:
                continue
            text = re.sub(r"/\*.*?\*/", "", src.read_text(errors="replace"), flags=re.S)
            for line in text.split("\n"):
                m = re.match(
                    r"^static\s+(?!.*\().*?(\w+)\s*(?:\[[^\]]*\])?\s*(?:=|;)", line
                )
                if not m:
                    continue
                if re.search(r"\bconst\b", line):
                    continue  # read-only, cannot race
                found.add((src.name, m.group(1)))

        undocumented = sorted(found - self.ALLOWED)
        assert not undocumented, (
            "new mutable file-scope state in the C layer. Processing paths must "
            "keep per-context state; if this is genuinely unreachable or "
            "read-only, add it to ALLOWED here and to the 'Not guaranteed' list "
            "in projects/libcdp/README.md:\n  "
            + "\n  ".join(f"{f}: {v}" for f, v in undocumented)
        )

    def test_shim_remains_unreachable(self):
        """The shim's globals are only tolerable while nothing calls them."""
        import re
        from pathlib import Path

        repo = Path(__file__).resolve().parent.parent
        callers = []
        entry = re.compile(
            r"\b(cdp_shim_init|cdp_shim_set_input|cdp_shim_set_output|"
            r"cdp_shim_get_output|shim_sndopenEx|shim_fgetfbufEx)\s*\("
        )
        for src in sorted((repo / "projects" / "libcdp").rglob("*.c")):
            if src.name in ("cdp_shim.c",) or "test" in src.name:
                continue
            text = re.sub(r"/\*.*?\*/", "", src.read_text(errors="replace"), flags=re.S)
            if entry.search(text):
                callers.append(src.name)

        assert not callers, (
            f"{callers} now call into cdp_shim.c, whose I/O slot state is "
            f"process-wide. Give it thread-local storage before wiring it up, "
            f"and update the thread-safety section of projects/libcdp/README.md"
        )

    def test_shim_is_not_compiled(self):
        """The abandoned shim must stay out of the build.

        Unreachable code is only harmless while it is also unreachable at link
        time -- and it was linked into every wheel for a long time while
        nothing called it. Dropping it from CDP_LIB_SOURCES is what makes the
        process-global slot table a non-issue rather than a latent one; this
        fails if either file is added back.
        """
        from pathlib import Path

        cmake = (Path(__file__).resolve().parent.parent / "CMakeLists.txt").read_text()
        # Strip comments: the source list is preceded by an explanation that
        # names both files.
        body = "\n".join(
            line for line in cmake.splitlines() if not line.lstrip().startswith("#")
        )
        compiled = [
            name for name in ("cdp_shim.c", "cdp_io_redirect.c") if name in body
        ]
        assert not compiled, (
            f"{compiled} is back in the build. It implements an abandoned "
            f"sfsys-interception strategy and carries process-global I/O "
            f"state; see the header comment in cdp_shim.h before reviving it."
        )

    def test_readme_documents_the_guarantee(self):
        from pathlib import Path

        readme = (
            Path(__file__).resolve().parent.parent / "projects" / "libcdp" / "README.md"
        ).read_text()
        assert "## Thread safety" in readme, (
            "the guarantee must be stated explicitly, not implied by a bullet"
        )
        # The Features list must not carry the old unqualified claim. The
        # Thread safety section quotes it deliberately when explaining what was
        # wrong, so only the bullet list is checked.
        features = readme.split("## Features")[1].split("##")[0]
        assert "no global state" not in features, (
            "the old unqualified claim is back in the feature list; it was "
            "false when written and is still too strong -- see the caveats in "
            "the Thread safety section"
        )
