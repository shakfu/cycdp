# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.3.0]

A minor rather than a patch release: several changes reject input that previously reached the C layer, so a caller relying on the old behaviour will now see a `ValueError` where it used to get a segfault, a hang, or silence. `buf[-1]` also changes meaning, and two spectral operations produce different -- correct -- output. Details under Changed and Fixed.

### Fixed

- Six defects found by the ASan fuzz job on its first CI run, none of which reproduce uninstrumented -- the point of running the sweep under a sanitizer. Two are memory errors:

  - `delay` with a sub-sample delay time computed a zero-length delay line, then read and wrote `delay_buf[ch][write_pos]` from it. `calloc(0, n)` returns a non-NULL zero-byte block, so this silently corrupted the heap without instrumentation. Same shape as the reverb delay-line defect fixed above; the line is now at least one sample.
  - `pitch` with a `min_freq` above the sample rate collapsed the YIN lag range to zero, giving a zero-byte search buffer that `yin_difference` then wrote into. An empty or inverted lag range is now an error naming the range, rather than a search of nothing.

  Two were undefined behaviour on a float-to-int conversion: `morph_bridge_native`'s `offset` and `morph_native`'s `stagger` were cast to `int` without a range check, which x86-64 resolves to `INT_MIN`. Both clamp first. `morph_glide_native` overflowed a signed int in the power-of-2 rounding of `fft_size` (`n *= 2` past 2^30); the size is clamped to the range the analysis accepts before rounding. `bounce` with a large `initial_delay` requested a 500 TB allocation.

  The parameters behind them -- `initial_delay`, `predelay`, `offset`, `stagger`, `min_freq`, `max_freq` -- are now bounded in the Cython layer like the rest, so they fail with a named `ValueError` rather than reaching the C layer at all.

- The fuzz harness prints the failing worker's stderr. It reported which call failed but not why, discarding the sanitizer report that is most of the reason to run the sweep under ASan.

- `tests/test_packaging.py` read the wrong symbol table on ELF. `nm -g` reads `.symtab`, which a shared object need not carry, so on Linux it listed nothing -- and an empty set trivially satisfies "only PyInit is exported", meaning the check passed in CI while measuring nothing. It now uses `nm -D` on ELF and fails outright if no symbols are listed, so a tooling mismatch cannot masquerade as a pass.

- The phase vocoder is ported from CDP's own (`dev/pv/pvoc.c`), which fixes four operations at once and explains why they were broken.

  Our analysis copied each frame straight into the transform buffer. CDP folds it in **rotated by `n mod N`**, which references the phase to absolute time: a partial sitting exactly on a bin centre then shows no advance between hops, so the raw phase difference already *is* the deviation from that centre. Without the rotation every bin advances by `i * expect` per hop, which the analysis has to subtract back out -- self-consistent, but it leaves the bin index carrying no information the synthesis can use. The synthesis correspondingly accumulated the absolute frequency where CDP accumulates only the deviation (`oldOutPhase[i] += freq[i] - i*F`), so bin index and phase accumulator both encoded position and double-counted. Moving amplitude between bins therefore did not move the sound: a translation was coherent only in multiples of `fft_size / (2 * hop)` bins and cancelled everywhere in between.

  A third defect fell out once the first two were fixed: the frequency deviation carried the wrong sign, because mxfft's forward transform uses the opposite convention from the one the formula assumes. Like the others it cancelled on any analyse-then-synthesise path, and it made `freq[]` the reflection of the true frequency about its bin centre.

  What this fixes, none of which any prior test could see:

  - `spectral_shift` works. A +100 Hz shift of a 440 Hz tone lands on 540 Hz within 1 Hz at ~90% of the input level. It previously peaked at 340 Hz with 9% of the energy, and a shift of zero returned near-silence.

  - `spectral_stretch` works, and matches its documented curve to 0.0% at 2, 5, 8 and 11 kHz. It previously left a 2 kHz partial at 1930 Hz having destroyed 87% of the signal.

  - `spectral_fold` recovers 80% of the input level where it produced 8%.

  - `get_partials` reports an isolated tone exactly -- 200, 400, 800 and 1600 Hz all to 0.00% -- where it was biased by up to 15%, and a 300/600/900 harmonic series comes back exactly rather than to within 1%.

  `time_stretch`, `pitch_shift`, the filters, `eq_parametric` and the morph family were correct before and remain correct: pitch shifts land within 0.1%, the filters and EQ are unchanged, and time stretch preserves duration and pitch. Their samples differ because the phase reference moved, which is why this is a minor release. Verified against a fingerprint of all 26 operations on this path.

- Every public function now rejects a non-finite floating-point parameter. Neither layer checked for NaN or Inf: they pass every comparison-based clamp in the C code (`if (x < 0) x = 0;` is false for NaN), and `(size_t)nan` is undefined behaviour. Systematically driving each parameter of all 132 public functions to hostile values produced four crashes -- SIGSEGV in `drunk` and `grain_cloud`, SIGBUS in `crystal` and `wrappage` -- and thirteen calls that never returned. All were reachable from ordinary Python, and all were reachable from the `cycdp` command line, because argparse accepts `nan` for any float option. `scripts/check_validation.py` fails the build if a function is added without a guard.

- Parameters that scale an allocation or an iteration count are bounded, not merely checked for finiteness. Several docstrings already promised a range (`retime`'s `grain_ms` says "Range: 5-500", `synth_wave`'s `duration` says "0.001 to 3600") that nothing enforced. Those are enforced now, and the parameters that had no documented range and drove unbounded work -- `time_stretch(factor=1e9)`, `texture_simple(density=1e9)`, `formants(lpc_order=2147483647)`, `cascade(pitch_decay=1e-30)` -- have explicit limits, stated in the error message.

- Out-of-bounds read in `drunk`. When `step_ms` exceeded the input length, the walk's upper clamp evaluated to a negative value that was assigned straight into a `size_t`; the next iteration's bounds check then computed `input_frames - seg_start` with `seg_start` past the end, underflowing to a near-`SIZE_MAX` segment length, and the copy loop read far outside the input.

- Three loops that could run forever. `drunk` and `stutter` both `continue` when a segment is unusable without advancing the output position, so a configuration where no segment is ever usable span indefinitely; both now reject that configuration up front and carry a retry bound. `grain_extend` advanced its write cursor by `grain_len - splice_len`, which is exactly zero at the default 15 ms grain size -- the writer never moved.

- Four saturating fixes in `cdp_granular_ext.c` where `write_pos += g->length - splice_len` could underflow a `size_t` and skip the rest of the output.

- The WAV reader validated the audio format and bit depth but trusted every other header field. A `fmt ` chunk declaring zero channels made the frame size zero and the frame-count division an integer division by zero -- SIGFPE on x86-64, which kills the process; `cdp_buffer_create` rejects zero channels, but one line too late. A declared `data` size larger than the file turned four bytes into an arbitrary allocation (a 100-byte file requesting 4 GB). An absurd-but-positive sample rate survived into every downstream operation, where delay lines and output lengths scale with it. Channel count, sample rate and chunk size are now bounded against the file.

- Chunk skipping used `fseek(f, (long)chunk_size, SEEK_CUR)`. `long` is 32-bit on Windows, so a chunk declaring more than `LONG_MAX` bytes cast to a negative offset and seeked backwards, leaving the chunk scan re-reading the same bytes forever.

- Heap-buffer-overflow in the reverb delay lines at low sample rates. Their length is scaled by `sample_rate / 44100`, so below roughly 800 Hz every Freeverb tuning constant rounds down to zero frames; `calloc(0, n)` returns a non-NULL zero-byte allocation and `comb_process` then reads `buffer[0]` from it. Confirmed with AddressSanitizer, which reports it at `cdp_reverb.c:comb_process`.

- The output conversion copied `lib_buf.length` samples into a buffer sized `(length / channels) * channels`. For a length that is not a whole number of frames those differ, and the copy ran past the end of the destination.

- Writing a buffer containing NaN was undefined behaviour. The PCM converters clamp with `if (v > 1.0f)` / `if (v < -1.0f)`, both false for NaN, so it reached the float-to-integer cast. NaN is written as silence and the infinities clamp to full scale; the float32 path still round-trips them, since the format can represent them.

- The WAV writers truncated the data size to 32 bits without checking, so a buffer above roughly one billion samples produced a corrupt file and a zero exit status. They now refuse.

- Each thread's processing context is freed when the thread exits, via a pthread key destructor (FLS on Windows). A plain thread-local pointer has no such hook and the context was never released: 528 bytes retained for the life of the process, once per thread that ever called in. The header described this as "bounded by thread count", which holds only for a fixed pool -- a thread-per-request server accumulated ~2 MB per 3,000 threads with no limit. `cycdp.release_thread_context()` releases it early for a long-lived thread that is done.

- `cycdp.version()` reported the C library's own hardcoded string, which had drifted to `0.1.0` while the package was `0.2.0`. It is now defined by CMake from the project version, and `__version__` comes from the installed distribution rather than a third literal. A test asserts the two agree; the previous test only checked the string was semver-shaped, which could not catch it.

- `buf[-1]` now means the last sample. The index was typed `size_t`, so a negative value raised `OverflowError` from the argument conversion rather than wrapping the way every other Python sequence does.

- A bare `Buffer()` passed to a processing function reported "Output buffer has invalid channel count" from deep in the C layer. It now says the input buffer is not initialised.

- `Buffer.__getbuffer__` ignored its `flags` argument and always advertised `format`, `shape` and `strides`. An exporter is required to fill in what the consumer asked for and no more: a consumer requesting `PyBUF_SIMPLE` was handed fields it had declared no interest in, and a non-NULL `format` contradicts the itemsize such a consumer is entitled to assume, since a NULL format means unsigned bytes. The field layout now follows CPython's own `PyBuffer_FillInfo`. Nothing can fail -- the memory is contiguous, so every contiguity flag is satisfiable, and always writable, so `PyBUF_WRITABLE` never needs refusing. `memoryview`, `bytes()`, `array.array` and numpy all ask for everything, so the common paths were never affected and are covered by tests to keep it that way.

### Removed

- `cdp_shim.c` and `cdp_io_redirect.c` are no longer compiled -- roughly 750 lines of dead object code that had been linked into every wheel. They implement an approach that was tried and set aside: a fake `sfsys` that would let unmodified CDP program sources run against memory buffers, by `#define`-ing `fgetfbufEx` and the other soundfile entry points to wrappers over a slot table of in-memory "files" (`cdp_sfsys_shim.h`). Had it worked, all ~500 CDP programs would have become available by compiling them rather than rewriting them. Intercepting I/O turned out to be necessary but nowhere near sufficient -- CDP algorithms are `main()` programs with command-line parsing and extensive global state -- so every operation here is an independent port instead, and nothing ever called the shim.

  Leaving it in the build was not harmless. Its state is process-global by design, faithfully mirroring the CDP programs it was meant to host, and the processing paths release the GIL: the first call into it would have reintroduced a data race the per-thread context work exists to prevent. `tests/test_concurrency.py::test_shim_is_not_compiled` now fails if either file returns to `CDP_LIB_SOURCES`, alongside the existing check that nothing calls into them.

  The files stay in the tree as the record of the approach, with the rationale and the obstacles written up in the header comment of `cdp_shim.h`. Reviving the path means solving the hosting problem first; the right ownership model for the slot table follows from that rather than preceding it.

  `errstr`, the diagnostic buffer the vendored FFT writes to, moved from `cdp_shim.c` to `cdp_lib.c`. It was the one symbol in the shim the build actually needed, and defining it here avoids another local divergence from upstream `mxfft.c`.

### Changed

- The C library is compiled with `-Wall -Wextra`. Only the Cython extension had warnings enabled; the 23,000 lines of C where every one of the crashes above lives were built at the compiler's defaults. Turning them on cost nine fixes, all unused variables and one dead function -- and one of them, `write_pos` in `cdp_granular_ext.c`, marked a genuinely dead computation. `CYCDP_WERROR=ON` makes them errors, which CI uses; it is scoped to the hand-written sources, since the Cython-generated C emits sign-compare warnings that are not ours to fix.

- The extension is built with hidden symbol visibility. It exported 199 symbols including `errstr`, `fft_`, `fftmx` and `reals_` -- generic names from the vendored FFT that also exist in FFTPACK-derived libraries. It now exports only `PyInit__core`.

- The per-operation epilogue in `_core.pyx` is a shared helper. Ninety functions repeated the same seven lines -- free the input, translate a NULL result, take ownership of the output -- which is ninety chances to get the ordering wrong, and it had been wrong before: the 0.2.0 ownership fix had to be applied to ninety-seven sites individually. The file is 300 lines shorter.

- `license` is a PEP 639 SPDX expression, and the wheel now carries `LICENSE` and a new `projects/libcdp/NOTICE`. LGPL 2.1 section 6 wants the corresponding source to accompany the object code; the wheel statically links `mxfft.c`, which is Copyright (c) 1983-2023 Trevor Wishart and Composers Desktop Project Ltd. `NOTICE` names the copyright holders and says where the source is.

- The CLI rejects `nan` and `inf` as a usage error at parse time rather than passing them to the library.

- Buffer conversion uses `memcpy` in place of two element-wise loops.

### Added

- `scripts/fuzz_api.py` and the `fuzz` CI job. The suite and the sanitizer jobs both only ever exercise the library with well-formed input, so between them they validated it against exactly the inputs it was designed for. The harness drives every public function's every parameter with NaN, Inf and absurd magnitudes, each call in its own subprocess because the failures it looks for are signals and infinite loops rather than exceptions. It runs under AddressSanitizer in CI, where an out-of-bounds read becomes a precise report. `make fuzz` runs it locally.

- `tests/test_dsp_families.py`: measured behaviour for the six operation families where "correct" can be stated exactly -- synthesis, analysis, phase, spatial, envelope and morphing. Twelve families previously had nothing beyond the cheap invariants, so an operation could return the wrong frequency and the suite would stay green. These assert the harmonic series each waveform is named for, that pitch tracking returns the pitch, that inversion is exact negation, that panning hard left leaves the right channel silent, that tremolo at 5 Hz modulates at 5 Hz, and that a morph's endpoints are its inputs. Behavioural coverage goes from 21 of 133 operations to 46. Writing them is what found the two phase-vocoder defects above.

  Four defects they surfaced but did not fix are recorded as strict xfails rather than omitted, so that fixing one fails the suite and forces the marker off:

  - `spectral_shift` shifts in the wrong direction and loses the energy. A +100 Hz shift of a 440 Hz tone peaks at 340 Hz at 7% of the input level; -100 Hz peaks at 540. Past about two bins nothing coherent survives, with RMS down from 0.345 to 0.030.

  - `spectral_stretch` has the same root cause: a 2x stretch of a 2000 Hz tone leaves the peak at 1930 Hz and destroys 87% of the energy. Both rewrite each bin's stored frequency in place while leaving its amplitude in the original bin, so a frequency the analysis never produced decoheres across the overlap-add rather than relocating. Fixing them means moving amplitude/frequency pairs to the destination bin, and settling the sign convention between the analysis `atan2` and the synthesis -- self-consistent for an unmodified spectrum, which is why the inversion only appears once an offset is added. The operations that interpolate or average analysed spectra (morph, blur, cross-synthesis) stay within the range of values the analysis produced and are unaffected.

  - `morph_glide` and `morph_glide_native` deliver about a quarter of the requested duration.

  - `get_partials` remains biased on an isolated low tone (200 Hz reads as ~231) while being accurate on harmonic content.

- `tests/test_invariants.py`: properties applied to every operation rather than to a chosen few. No output sample may be NaN or infinite, no output may be absurdly loud, silence in must give silence out (with each exception listing its reason), and a seeded operation must be reproducible. The operation table is built by introspecting the type stubs, so a new operation is covered without anyone remembering to add it. Most operations previously had only "the result has a nonzero length" asserted about them.

- `scripts/check_validation.py` and a `validation-check` make target, wired into `make qa` and CI.

- `cycdp.release_thread_context()`.

- Regression tests for every crash and hang above, including crafted WAV headers and the reverb's zero-length delay lines.

### Documentation

- The Architecture section of the README described a data path that does not exist. It presented `cdp_lib` as "wrapper modules that call into original CDP8 algorithm code" relying on a shim layer to intercept `sfsys` file I/O. No compiled file includes `sfsys.h`; `cdp_sfsys_shim.h` is included by nothing; no `cdp_io_*` symbol is referenced outside its own translation unit; and the only upstream CDP8 source compiled into the extension is the FFT. Every algorithm is an independent port. The project already knew this -- `tests/test_concurrency.py::test_shim_remains_unreachable` asserts it and `DEV_GUIDE.md` describes the porting workflow correctly -- but the public README had not been updated. This matters beyond tidiness: provenance is the main reason to choose cycdp, and a reader was being told the original algorithms were running.

- The "zero-copy interop" design principle claimed data passes between Python and C without copying. Reading a result out is zero-copy; input is copied once into library-owned memory. Restated, with the actual cost.

- The `*_native` morph functions were documented as "using original CDP algorithm". They are ports of `dev/morph/morph.c` running on this library's own analysis and synthesis, which is what the source file's own header comment says.

## [0.2.0]

### Fixed

- Every FFT-based operation was about 8.9 dB too quiet. Both analysis and synthesis apply a Hann window, but the synthesis normalization divided only by the overlap factor and ignored the window energy, leaving a constant gain of `sum(w^2)/N` = 3/8. Affects the four filters, `eq_parametric`, `time_stretch`, spectral morphing and the experimental spectral operations. Steady-state gain is now unity; the first and last frames still taper, which is inherent to overlap-add.

- Amplitude CLI commands were silently no-ops. The default `-n 0.95` normalization was applied unconditionally, so `gain --gain-factor 0.1` and `--gain-factor 2.0` produced the same output level and `limiter --threshold-db -20` produced a 0.95 peak. Affects `gain`, `gain-db`, `normalize`, `normalize-db`, `limiter`, `compressor`, `gate`, `envelope-follow` and `envelope-apply`; see "Changed" for the new default.

- `eq_parametric` delivers the gain it is asked for. It appeared to track its request non-linearly -- +12 dB measured +3.1 dB and -12 dB measured -20.8 dB -- but that was the constant offset above rather than a separate defect: the slope was always 1:1. A 0 dB EQ is now a no-op, as it should always have been.

- Heap buffer overflow segfaulting `time_stretch()` on inputs at or just above `fft_size`. The frame-interpolation clamp in `cdp_spectral_time_stretch` assumed at least two analysis frames and read past the end of the frame array.

- 38 unchecked allocations in `cdp_analysis.c` and `cdp_reverb.c` that could dereference NULL and crash the interpreter instead of raising.

- Unchecked allocations and a workspace leak on the error path in the vendored FFT (`projects/cpd8/dev/pv/mxfft.c`), which runs once per analysis frame and so affected every spectral operation. Marked with `cycdp patch:` comments for future re-vendoring.

- Randomised operations no longer touch process-global state. `cdp_distort.c` used the C runtime's `srand`/`rand`, so `cycdp.distort_shuffle(buf, seed=42)` silently reseeded `rand()` for every other library in the interpreter; `cdp_wrappage.c`, `cdp_flutter.c` and `cdp_hover.c` each kept their own file-static generator. All four now use the per-context xorshift64 PRNG that the rest of the library already used. Beyond the global-state leak, none of these were reentrant, which would have become a data race as soon as the GIL is released around DSP calls.

- The library context is per-thread rather than a process-wide singleton. Every call writes `ctx->error_msg` and draws from `ctx->prng_state`, so releasing the GIL against a shared context would have let concurrent operations corrupt each other's error reporting and consume each other's seeded random stream. ThreadSanitizer confirms this was not theoretical: with the singleton restored it reports a data race on `ctx->prng_state` at `cdp_distort.c:261`.

- Processing calls release the GIL. `_core.pyx` had zero `nogil` blocks in 6,425 lines, so every FFT, granular pass and reverb tail ran holding the interpreter lock and no cycdp operation could overlap with any other Python work. 98 processing calls are now wrapped; measured 3.4x speedup on four threads, and a pure-Python thread retains ~87% of its throughput during a DSP call where it was previously starved.

- C buffers are no longer leaked when conversion fails. The helper that turns a processing result into a `Buffer` left the caller to free the C buffer on the following line, but `Buffer.create` raises `MemoryError` on allocation failure, so that free was skipped -- leaking the output buffer, usually the larger of the two, exactly when memory was already scarce. It now takes ownership and frees on every path, across 97 call sites. Measured with allocation-failure injection: 500 induced failures of `time_stretch` previously grew RSS by 87 MB (~179 KB each, the exact size of the output buffer); now 64 KB, i.e. noise.

- Two-input operations no longer leak the first buffer if the second conversion fails. `envelope_apply`, `morph`, `morph_glide`, `cross_synth`, the three native morph variants and `psow_interp` converted both inputs on consecutive lines; they now use a helper that releases the first if the second raises.

- Type stubs (`_core.pyi`) disagreed with the implementation in 60 of 129 functions -- wrong parameter names, arity and defaults. Since the package ships `py.typed`, type checkers rejected correct calls and accepted incorrect ones. Stubs are now generated from `_core.pyx`.

- Stub return types for `pitch`, `formants`, `get_partials` and `fofex_extract_all` were wrong. The first three return dicts and the fourth a `(Buffer, int, int)` tuple; all four were declared otherwise. These were inherited from the hand-written stub when stub generation was introduced, so the generator faithfully reproduced them. Return types are now checked against what the compiled functions actually return.

- `Buffer` did not declare the buffer protocol or iteration in the stubs, so type checkers rejected `peak(buf)`, `memoryview(buf)` and `for x in buf`, all of which work at runtime.

- `phase_invert()` no longer rejects raw float32 buffers. It was defined twice in `_core.pyx` and the second definition silently shadowed the first, so the documented `phase_invert(samples, sample_rate=...)` form raised `TypeError`. Both call styles now work through a single definition.

- `wrappage()` accepts a `seed`. It previously hardcoded seed 42 in a file-static generator, making it the only granular operation whose randomisation could not be controlled while every sibling took a seed. Defaults to 0 (derive from the clock), matching `brassage`, `freeze` and `grain_cloud`.

- The README's headline Python example called `time_stretch(buf, stretch_factor=2.0)`; the parameter is `factor`. The first example a reader copies raised `TypeError`.

- The thread-safety claim in `projects/libcdp/README.md` said "thread-safe design (no global state)" and was false when written: the bindings held a process-wide context, `cdp_distort.c` used global `srand`/`rand`, and three modules kept file-static generators. It is now accurate, and states precisely what is guaranteed (one context per thread, no shared mutable state on any processing path), what is not (a context shared between threads; `errstr`, which the vendored FFT writes on allocation-failure paths only), and what is out of scope (`cdp_shim.c`, whose process-wide I/O state is unreachable from the library).

- CLI errors no longer print a raw traceback. An unreadable file or a malformed `--markers` value now produces `Error: ...` and exit 1, matching how the same file already handled a missing input. `CYCDP_TRACEBACK=1` restores the traceback for debugging.

- The CI build matrix tested Python 3.9, below the `requires-python = ">=3.10"` floor. uv resolves a supported interpreter regardless, so the job passed while its name claimed coverage it did not have, and 3.10 -- the actual floor, where wheels are most likely to break -- was never built. The matrix is now `[3.10, 3.14]`.

- Windows wheels were built with cibuildwheel v2.23 while Linux and macOS used v3.3.1.

- Duplicate `phase_invert` entry in `__all__`.

### Changed

- CLI output normalization is now per-command. The amplitude commands listed above are exempt by default; all others keep the `0.95` default. Passing `-n/--normalize` explicitly still forces normalization for any command, and `--no-normalize` is unchanged. Scripts relying on amplitude commands emitting a 0.95 peak will see different output levels.

- **Output levels change for FFT-based operations.** Correcting the synthesis normalization (see Fixed) makes the four filters, `eq_parametric`, `time_stretch`, spectral morphing and the experimental spectral operations about 8.9 dB louder -- which is the level they should always have produced. Python API callers who compensated for the old attenuation, or who chained several of these operations, will need to re-check their gain staging. CLI users are largely unaffected, since output is normalized by default.

- Coverage now measures the Cython core. It previously reported 94% while measuring only `cli.py`, with `_core.pyx` contributing zero statements. `make coverage` rebuilds with line tracing and reports 2,315 measured statements against the previous 335 (`_core.pyx` at 86%, 87% overall).

- CI type-checks all of `src/cycdp/` rather than only `__init__.py`, and lint, format and type-check now cover `scripts/`. Coverage moved out of the `qa` job into a dedicated `coverage` job.

- `make qa` runs `format` first so lint and tests see the formatted tree, and now includes `stubs-check`.

- Tightened duration tolerances that were too wide to catch a regression. `time_stretch` was asserted only to land within 50-250% of the requested duration and is now held to 5% (measured error is 2-3%); `retime` moved from 20% to 10%.

### Added

- `make sanitize` and a matching CI job, building with AddressSanitizer and UndefinedBehaviorSanitizer and running the suite under them. This class of bug is invisible to the ordinary suite, which passes cleanly until a NULL dereference or overflow takes down the interpreter; the `time_stretch` overflow above was found this way.

- `make stubs` regenerates `_core.pyi` from `_core.pyx`; `make stubs-check` fails on stale stubs and runs in CI.

- `tests/test_signatures.py`, asserting that the committed stubs match the compiled module, that no function is defined twice in `_core.pyx`, and that `__all__` has no duplicates. Independent of the generator, so drift fails the suite even if the generator is never run.

- `tests/test_coverage_setup.py`, which fails when coverage runs without the Cython core being measured, so the instrumentation cannot regress back into reporting a number that describes only the pure-Python files.

- Regression tests for the CLI normalization policy, both `phase_invert` call styles, and `time_stretch` on short inputs.

- `CYCDP_COVERAGE` and `CYCDP_SANITIZE` CMake options, both off by default and neither suitable for released wheels.

- `cython` in the dev dependency group, required at test time by `Cython.Coverage` to map `.pyx` lines.

- Ruff, mypy and pytest configuration in `pyproject.toml`. Both linters previously ran at their defaults, which is why the wrong stub return types and the `Buffer` protocol gaps went unreported. Ruff now also runs import sorting, bugbear, pyupgrade and comprehension rules; mypy runs with `check_untyped_defs`; pytest treats warnings as errors and rejects unknown config keys and marks.

- `tests/test_readme.py` executes every Python example in README.md against the real API, so documentation rot fails the suite. Documentation was the fourth hand-maintained copy of the API after `_core.pyx`, `_core.pyi` and the CLI registry; the other three are now generated or verified.

- `tests/test_packaging.py` keeps the four declarations of supported Python versions in agreement -- `requires-python`, the classifiers, the CI build matrix and `CIBW_BUILD` -- and fails if the cibuildwheel action version is skewed across OS jobs.

- Return-type verification, in two layers. `tests/test_signatures.py` calls every function whose declared return is not a plain `Buffer` and checks the result, and fails if such a function is added without a runtime case. That covers where the mistakes were, but leaves the ~118 functions declared `-> Buffer` merely assumed, so `tests/conftest.py` additionally records what every public function actually returns during the run and fails the session on any contradiction with the stub. All 132 declared functions are exercised by the suite, so the recording is complete rather than partial.

- A Concurrency section in README.md, with a runnable `ThreadPoolExecutor` example. The example is executed by the test suite like every other README snippet.

- `make tsan` and a matching CI job running the suite under ThreadSanitizer, plus a `CYCDP_TSAN` CMake option. ASan cannot detect data races and the two sanitizers cannot be combined, so this is a separate build; it is what validates the GIL-releasing calls.

- `tests/test_concurrency.py` covering both halves of the GIL change: that the lock is genuinely released (a Python thread keeps its throughput during a DSP call; four threads give real speedup), and that concurrency changes no result (seeded operations reproduce under contention, a mixed workload matches its sequential output, and error messages do not cross threads). A static guard also fails if a processing call is left outside a `with nogil:` block.

- `tests/test_rng.py` pinning the three properties that matter for randomised operations: a fixed seed reproduces, different seeds differ, and no call perturbs the process-global `rand()` sequence (checked in a subprocess so test ordering cannot mask a regression). Two static guards reject any reintroduction of `srand`/`rand` or of file-static PRNG state in the C sources; the first of these found the `cdp_flutter.c` and `cdp_hover.c` generators, which the original review had missed.

- `tests/conftest.py` with shared signal fixtures and analysis helpers: a Goertzel single-bin DFT, RMS, peak and duration measures. No numpy dependency; the suite still runs on `array.array` alone.

- `tests/test_dsp_behaviour.py` (46 tests) asserting what operations do to the signal rather than that they merely return something: filters separate passband from stopband by more than 40 dB, `pitch_shift` moves the fundamental to the expected frequency and vacates the original, `ring_mod` produces symmetric sidebands with the carrier suppressed, `limiter` enforces its ceiling, higher compression ratios compress harder, and gain and normalize hit their targets to within 0.01%. Each bound was measured against the implementation first, then verified to fail when fed the output of a deliberately wrong operation.

- `tests/test_validation.py` (28 tests) covering the invalid-input paths -- out-of-range indices, uninitialised buffers, non-positive stretch factors, empty buffer lists, unknown format and curve names, missing and malformed files. These were 71 of the previously unexercised lines in `_core.pyx`.

## [0.1.2]

### Added

- Command-line interface accessible via `cycdp` console script and `python3 -m cycdp`

  - All 100+ audio processing functions exposed as flat subcommands (e.g. `cycdp time-stretch`, `cycdp reverb`)

  - Four input modes: single-file processing, dual-file processing (morph, mix), synthesis (no input), and analysis (text/JSON/CSV output)

  - `cycdp list [category]` for category-grouped command discovery

  - `cycdp info <file>` for audio file metadata

  - `cycdp version` for version information

  - Global options: `-o/--output` (file or directory), `-n/--normalize`, `--no-normalize`, `--format`

  - Automatic output naming when `-o` is omitted (`<input_stem>_<command>.wav`)

  - Category-grouped `--help` output via custom formatter

- Type stubs (`_core.pyi`) for IDE autocompletion and type checking

- PyPI publication metadata (URLs, classifiers, keywords)

### Fixed

- Buffer overflow in `cdp_flutter.c` mono-to-stereo conversion causing segfaults

- Variable Length Array (VLA) compatibility for MSVC in `io.c`

- `M_PI` undefined error for MSVC in `utils.c`

- Linux build failure by enabling position-independent code

## [0.1.1]

### Added (Demos)

**Synthesis Demos (01-07)** - Generate test sounds and demonstrate API:

- `01_basic_operations.py` - Buffers, gain, fades, panning, mixing

- `02_effects_and_processing.py` - Delay, reverb, modulation, distortion, filters

- `03_spectral_processing.py` - Blur, focus, time stretch, pitch shift, freeze

- `04_granular_synthesis.py` - Brassage, wrappage, grain manipulation

- `05_pitch_synchronous.py` - PSOW stretch/grab, FOF repitch, hover

- `06_creative_techniques.py` - Effect chains and sound design recipes

- `07_morphing.py` - Spectral morph, glide, cross-synthesis

**FX Processing Demos (fx01-fx07)** - CLI tools for processing real audio:

- `fx01_time_and_pitch.py` - Time stretch, pitch shift, spectral shift

- `fx02_spectral_effects.py` - Blur, focus, fold, freeze effects

- `fx03_granular.py` - Brassage, wrappage, grain operations

- `fx04_reverb_delay_mod.py` - Reverb, delay, tremolo, chorus, flanger, ring mod

- `fx05_distortion_dynamics.py` - Waveset distortion, bitcrush, filters, compression

- `fx06_psow_fof.py` - PSOW stretch/grab, FOF repitch, hover

- `fx07_creative_chains.py` - Complex effect chains (ambient, industrial, shimmer, etc.)

All FX demos accept `input.wav -o output_dir/` arguments.

**Makefile targets:**

- `make demos` - Run all demos, output to `build/`

- `make demos-clean` - Remove generated WAV files

### Added (CDP Algorithm Ports)

**Analysis:**

- `pitch` - pitch tracking using YIN algorithm (CDP: pitch)

- `formants` - formant analysis using LPC (CDP: formants)

- `get_partials` - partial/harmonic tracking (CDP: get_partials)

**Spectral Processing:**

- `spectral_focus` - super-Gaussian frequency enhancement (CDP: focus)

- `spectral_hilite` - boost spectral peaks above threshold (CDP: hilite)

- `spectral_fold` - fold spectrum at frequency for metallic effects (CDP: specfold)

- `spectral_clean` - spectral noise gate (CDP: speclean)

**Experimental/Chaos:**

- `strange` - Lorenz attractor chaotic modulation (CDP: strange)

- `brownian` - random walk modulation of pitch/amp/filter (CDP: brownian)

- `crystal` - crystalline textures with decaying echoes (CDP: crystal)

- `fractal` - recursive wavecycle overlay with pitch ratio and decay (CDP: fractal)

- `quirk` - probabilistic reverse/dropout transformations (CDP: quirk)

- `chirikov` - standard map chaotic modulation (CDP: chirikov)

- `cantor` - Cantor set fractal gating pattern (CDP: cantor)

- `cascade` - cascading echoes with pitch/amp/filter decay (CDP: cascade)

- `fracture` - fragment and scatter audio with gaps (CDP: fracture)

- `tesselate` - tile-based pattern transformations (CDP: tesselate)

**Playback/Time Manipulation:**

- `zigzag` - alternating forward/backward playback through time points (CDP: zigzag)

- `iterate` - repeated playback with pitch/gain variations (CDP: iterate)

- `stutter` - segment-based stuttering with silence inserts (CDP: stutter)

- `bounce` - bouncing ball effect with accelerating repeats (CDP: bounce)

- `drunk` - "drunk walk" random navigation through audio (CDP: drunk)

- `loop` - looping with crossfades and variations (CDP: loop)

- `retime` - time-domain time stretching/compression using TDOLA (CDP: retime)

- `scramble` - waveset reordering (shuffle, reverse, by size/level) (CDP: scramble)

- `splinter` - fragmenting effect with shrinking repeats (CDP: splinter)

- `hover` - zigzag reading at specified frequency for hovering pitch effect (CDP: hover)

- `constrict` - shorten or remove silent sections (CDP: constrict)

- `phase_invert` - invert phase of audio signal (CDP: phase mode 1)

- `phase_stereo` - enhance stereo separation via phase subtraction (CDP: phase mode 2)

- `wrappage` - granular texture with stereo spatial distribution (CDP: wrappage)

**Spatial Effects:**

- `spin` - rotate audio around stereo field with optional doppler (CDP: spin)

- `rotor` - dual-rotation modulation creating interference patterns (CDP: rotor)

- `flutter` - spatial tremolo with loudness modulation alternating L/R (CDP: flutter)

**Extended Granular:**

- `grain_reorder` - reorder detected grains (shuffle, reverse, rotate) (CDP: grain)

- `grain_rerhythm` - change timing/rhythm of grains (CDP: grain)

- `grain_reverse` - reverse individual grains in place (CDP: grain)

- `grain_timewarp` - time-stretch/compress grain spacing (CDP: grain)

- `grain_repitch` - pitch-shift grains with interpolation (CDP: grain)

- `grain_position` - reposition grains in stereo field (CDP: grain)

- `grain_omit` - probabilistically omit grains (CDP: grain)

- `grain_duplicate` - duplicate grains with variations (CDP: grain)

**Granular/Texture:**

- `grain_cloud` - grain cloud generation from amplitude-detected grains (CDP: grain)

- `grain_extend` - extend duration using grain repetition (CDP: grainex extend)

- `texture_simple` - simple texture layering (CDP: texture SIMPLE_TEX)

- `texture_multi` - multi-layer grouped texture (CDP: texture GROUPS)

**Morphing/Cross-synthesis:**

- `morph` - spectral interpolation between two sounds (CDP: SPECMORPH)

- `morph_glide` - simple spectral glide between two sounds (CDP: SPECGLIDE)

- `cross_synth` - combine amp from one sound with freq from another (CDP: combine)

- `morph_glide_native` - native CDP specglide wrapper (original algorithm)

- `morph_bridge_native` - native CDP specbridge wrapper (original algorithm)

- `morph_native` - native CDP specmorph wrapper (original algorithm)

**Synthesis:**

- `synth_wave` - waveform synthesis (sine, square, saw, ramp, triangle) (CDP: wave)

- `synth_noise` - noise generation (white, pink) (CDP: synth noise)

- `synth_click` - click/metronome track generation (CDP: click)

- `synth_chord` - chord synthesis from MIDI pitch list (CDP: multi_syn)

**Pitch-Synchronous Operations (PSOW):**

- `psow_stretch` - time-stretch while preserving pitch using PSOLA (CDP: psow stretch)

- `psow_grab` - extract pitch-synchronous grains from a position (CDP: psow grab)

- `psow_dupl` - duplicate grains for time-stretching (CDP: psow dupl)

- `psow_interp` - interpolate between two grains (CDP: psow interp)

**FOF Extraction and Synthesis (FOFEX):**

- `fofex_extract` - extract single FOF at specified time (CDP: fofex)

- `fofex_extract_all` - extract all FOFs to uniform-length bank (CDP: fofex)

- `fofex_synth` - synthesize audio from FOFs at target pitch (CDP: fofex)

- `fofex_repitch` - repitch audio with optional formant preservation (CDP: fofex)

**Distortion:**

- `distort_cut` - waveset segmentation with decaying envelope (CDP: distcut)

- `distort_mark` - interpolate between waveset groups at time markers (CDP: distmark)

- `distort_repeat` - time-stretch by repeating wavecycles (CDP: distrep)

- `distort_shift` - shift/swap half-wavecycle groups (CDP: distshift)

- `distort_warp` - progressive warp distortion with modular sample folding (CDP: distwarp)

**Filtering:**

- `filter_bandpass` - spectral bandpass filter

- `filter_notch` - spectral notch (band-reject) filter

### Added (Non-CDP Additions)

Standard DSP functions not derived from CDP algorithms:

**EQ:**

- `eq_parametric` - parametric equalizer with center frequency, gain, and Q factor

**Dynamics:**

- `gate` - noise gate with attack/release/hold envelope

- `compressor` - dynamic range compression with threshold, ratio, attack/release

- `limiter` - peak limiting with attack/release

- `envelope_follow` - extract amplitude envelope (peak or RMS mode)

- `envelope_apply` - apply envelope to sound with depth control

**Effects:**

- `bitcrush` - bit depth and sample rate reduction

- `ring_mod` - ring modulation with carrier frequency

- `delay` - feedback delay with mix control

- `chorus` - modulated delay (LFO-based)

- `flanger` - short modulated delay with feedback

### Added (Constants)

- Waveform types: `WAVE_SINE`, `WAVE_SQUARE`, `WAVE_SAW`, `WAVE_RAMP`, `WAVE_TRIANGLE`

- Scramble modes: `SCRAMBLE_SHUFFLE`, `SCRAMBLE_REVERSE`, `SCRAMBLE_SIZE_UP`, `SCRAMBLE_SIZE_DOWN`, `SCRAMBLE_LEVEL_UP`, `SCRAMBLE_LEVEL_DOWN`

### Fixed

- Phase vocoder frequency calculation now uses correct hop size

- Spectral filters now use bin center frequency for accurate filtering

## [0.1.0]

### Added

- Native CDP library integration (no subprocess overhead)

- **Spectral processing:** `time_stretch`, `spectral_blur`, `modify_speed`, `pitch_shift`, `spectral_shift`, `spectral_stretch`, `filter_lowpass`, `filter_highpass`

- **Envelope operations:** `dovetail`, `tremolo`, `attack`

- **Distortion:** `distort_overload`, `distort_reverse`, `distort_fractal`, `distort_shuffle`

- **Reverb:** `reverb` - FDN reverb (8 comb + 4 allpass filters)

- **Granular:** `brassage`, `freeze`

- **Core operations:** `gain`, `gain_db`, `normalize`, `normalize_db`, `phase_invert`

- **Spatial:** `pan`, `pan_envelope`, `mirror`, `narrow`

- **Mixing:** `mix`, `mix2`

- **Buffer utilities:** `reverse`, `fade_in`, `fade_out`, `concat`

- **Channel operations:** `to_mono`, `to_stereo`, `extract_channel`, `merge_channels`, `split_channels`, `interleave`

- **File I/O:** `read_file`, `write_file` (WAV: float, PCM16, PCM24)

- Cython bindings with zero-copy buffer protocol support

- Comprehensive test suite
