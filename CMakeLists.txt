cmake_minimum_required(VERSION 3.15...3.31)

# scikit-build-core sets these; a direct `cmake -S . -B ...` does not, and the
# sanitizer and -Werror builds configure that way. Falling back to pyproject.toml
# keeps one source for the version: without this the direct builds produced an
# empty PROJECT_VERSION, and cdp_version() reported an empty string.
if(NOT DEFINED SKBUILD_PROJECT_NAME OR SKBUILD_PROJECT_NAME STREQUAL "")
    set(SKBUILD_PROJECT_NAME "cycdp")
endif()
if(NOT DEFINED SKBUILD_PROJECT_VERSION OR SKBUILD_PROJECT_VERSION STREQUAL "")
    file(READ "${CMAKE_CURRENT_SOURCE_DIR}/pyproject.toml" _pyproject)
    string(REGEX MATCH "\nversion = \"([^\"]+)\"" _ "${_pyproject}")
    if(NOT CMAKE_MATCH_1)
        message(FATAL_ERROR "cannot read project version from pyproject.toml")
    endif()
    set(SKBUILD_PROJECT_VERSION "${CMAKE_MATCH_1}")
endif()

project(${SKBUILD_PROJECT_NAME} VERSION ${SKBUILD_PROJECT_VERSION} LANGUAGES C)

set(CMAKE_EXPORT_COMPILE_COMMANDS ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

# The static libraries are linked into one Python extension and nothing else
# needs their symbols, so keep them out of the module's dynamic symbol table.
# Without this the wheel exported `errstr`, `fft_`, `fftmx` and `reals_` --
# generic names from the vendored FFT that also exist in FFTPACK-derived
# libraries. CPython uses RTLD_LOCAL so a collision needs another extension
# loaded with RTLD_GLOBAL, but the fix costs nothing and removes the question.
# _PyInit__core keeps its visibility: Cython marks it explicitly.
set(CMAKE_C_VISIBILITY_PRESET hidden)
set(CMAKE_VISIBILITY_INLINES_HIDDEN ON)

find_package(Python REQUIRED COMPONENTS Interpreter Development.Module)

# =============================================================================
# Warnings
# =============================================================================
# These apply to every target below, not just the extension.
#
# For a long time only `_core` was built with -Wall -Wextra, leaving the 23,000
# lines of C in `cdp` and `cdp_lib` compiled at the compiler's defaults -- and
# that is where the crashes live. Turning them on cost nine fixes, all unused
# variables, one of which (`write_pos` in cdp_granular_ext.c) marked a genuine
# dead computation.
#
# Not -Werror by default: a new compiler release introducing a warning should
# not break every user building from an sdist. CYCDP_WERROR=ON turns them into
# errors and is what CI uses.
#
# It applies to `cdp` and `cdp_lib` only, never to `_core`: that target is
# built from Cython-generated C, which emits sign-compare warnings we have no
# way to fix and no business failing on.
if(MSVC)
    add_compile_options(/W4)
else()
    add_compile_options(-Wall -Wextra)
endif()

option(CYCDP_WERROR "Treat warnings in the hand-written C as errors" OFF)

# =============================================================================
# Sanitizers
# =============================================================================
# CYCDP_SANITIZE builds the C layer and the extension with AddressSanitizer and
# UndefinedBehaviorSanitizer. This is how the unchecked-allocation class of bug
# (H3) is kept closed: those crashes are invisible to the test suite, which
# reports a clean pass right up until a NULL dereference takes down the
# interpreter. See `make sanitize` and the sanitize CI job.
#
# Never enable for released wheels: it is slow and changes the ABI expectations
# of the process it loads into.
option(CYCDP_SANITIZE "Build with AddressSanitizer and UBSan" OFF)
# ThreadSanitizer is a separate build: TSan and ASan cannot be combined, and
# TSan is what validates the GIL-releasing processing calls (M1). It caught the
# ctx->prng_state race when the library context was still a shared singleton.
option(CYCDP_TSAN "Build with ThreadSanitizer" OFF)

if(CYCDP_SANITIZE AND CYCDP_TSAN)
    message(FATAL_ERROR "CYCDP_SANITIZE and CYCDP_TSAN are mutually exclusive")
endif()

if(CYCDP_TSAN)
    if(MSVC)
        message(FATAL_ERROR "CYCDP_TSAN is not supported with MSVC")
    endif()
    add_compile_options(-fsanitize=thread -fno-omit-frame-pointer -g)
    add_link_options(-fsanitize=thread)
endif()

if(CYCDP_SANITIZE)
    if(MSVC)
        message(FATAL_ERROR "CYCDP_SANITIZE is not supported with MSVC")
    endif()
    # -fno-omit-frame-pointer keeps stack traces readable; -g gives them line
    # numbers. -fno-sanitize-recover makes UBSan findings fail the run instead
    # of printing and continuing, which is what makes this useful in CI.
    add_compile_options(
        -fsanitize=address,undefined
        -fno-sanitize-recover=all
        -fno-omit-frame-pointer
        -g
    )
    add_link_options(-fsanitize=address,undefined)
endif()

# =============================================================================
# Build libcdp (the C library)
# =============================================================================
set(LIBCDP_DIR "${CMAKE_CURRENT_SOURCE_DIR}/projects/libcdp")
set(CDP_DEV_DIR "${CMAKE_CURRENT_SOURCE_DIR}/projects/cpd8/dev")

# libcdp source files
set(LIBCDP_SOURCES
    ${LIBCDP_DIR}/src/error.c
    ${LIBCDP_DIR}/src/context.c
    ${LIBCDP_DIR}/src/buffer.c
    ${LIBCDP_DIR}/src/gain.c
    ${LIBCDP_DIR}/src/io.c
    ${LIBCDP_DIR}/src/channel.c
    ${LIBCDP_DIR}/src/mix.c
    ${LIBCDP_DIR}/src/spatial.c
    ${LIBCDP_DIR}/src/utils.c
)

# Build libcdp as a static library
add_library(cdp STATIC ${LIBCDP_SOURCES})
target_include_directories(cdp PUBLIC ${LIBCDP_DIR}/include)
# cdp_version() reports the project version rather than a separate literal that
# has to be remembered. They had already diverged (0.1.0 against 0.2.0).
target_compile_definitions(cdp PRIVATE
    CDP_VERSION_STRING="${PROJECT_VERSION}")
if(CYCDP_WERROR AND NOT MSVC)
    target_compile_options(cdp PRIVATE -Werror)
endif()
if(UNIX)
    target_link_libraries(cdp PUBLIC m)
endif()

# =============================================================================
# Build CDP processing library (native spectral operations)
# =============================================================================

# CDP library wrapper sources.
#
# cdp_shim.c and cdp_io_redirect.c are deliberately absent. They implement an
# abandoned strategy: a fake sfsys that would let unmodified CDP program
# sources run against memory buffers, by #define-ing fgetfbufEx and friends to
# wrappers over a slot table of in-memory "files". Intercepting I/O turned out
# to be necessary but nowhere near sufficient -- CDP algorithms are main()
# programs with command-line parsing and extensive global state -- so every
# operation here is an independent port instead (see DEV_GUIDE.md), and
# nothing ever called the shim.
#
# They were still compiled and linked into every wheel: ~750 lines of dead
# object code carrying process-global mutable state, one call away from
# undoing the per-thread context work, since the processing paths release the
# GIL. The files remain in the tree as the record of the approach; see the
# header comment in cdp_shim.h.
set(CDP_LIB_SOURCES
    ${LIBCDP_DIR}/cdp_lib/cdp_lib.c
    ${LIBCDP_DIR}/cdp_lib/cdp_spectral.c
    ${LIBCDP_DIR}/cdp_lib/cdp_envelope.c
    ${LIBCDP_DIR}/cdp_lib/cdp_distort.c
    ${LIBCDP_DIR}/cdp_lib/cdp_reverb.c
    ${LIBCDP_DIR}/cdp_lib/cdp_granular.c
    ${LIBCDP_DIR}/cdp_lib/cdp_granular_ext.c
    ${LIBCDP_DIR}/cdp_lib/cdp_analysis.c
    ${LIBCDP_DIR}/cdp_lib/cdp_filters.c
    ${LIBCDP_DIR}/cdp_lib/cdp_transform.c
    ${LIBCDP_DIR}/cdp_lib/cdp_effects.c
    ${LIBCDP_DIR}/cdp_lib/cdp_dynamics.c
    ${LIBCDP_DIR}/cdp_lib/cdp_morph.c
    ${LIBCDP_DIR}/cdp_lib/cdp_morph_native.c
    ${LIBCDP_DIR}/cdp_lib/cdp_experimental.c
    ${LIBCDP_DIR}/cdp_lib/cdp_playback.c
    ${LIBCDP_DIR}/cdp_lib/cdp_synth.c
    ${LIBCDP_DIR}/cdp_lib/cdp_psow.c
    ${LIBCDP_DIR}/cdp_lib/cdp_fofex.c
    ${LIBCDP_DIR}/cdp_lib/cdp_flutter.c
    ${LIBCDP_DIR}/cdp_lib/cdp_hover.c
    ${LIBCDP_DIR}/cdp_lib/cdp_constrict.c
    ${LIBCDP_DIR}/cdp_lib/cdp_phase.c
    ${LIBCDP_DIR}/cdp_lib/cdp_wrappage.c
)

# CDP dev sources needed (FFT)
set(CDP_FFT_SOURCES
    ${CDP_DEV_DIR}/pv/mxfft.c
)

# Build as static library
add_library(cdp_lib STATIC ${CDP_LIB_SOURCES} ${CDP_FFT_SOURCES})
target_include_directories(cdp_lib PUBLIC
    ${LIBCDP_DIR}/cdp_lib
    ${LIBCDP_DIR}/src
    ${CDP_DEV_DIR}/newinclude
    ${CDP_DEV_DIR}/include
)
if(APPLE)
    target_compile_definitions(cdp_lib PRIVATE unix __MAC__ MAC)
elseif(UNIX)
    target_compile_definitions(cdp_lib PRIVATE unix linux _X86_)
endif()
if(UNIX)
    target_link_libraries(cdp_lib PUBLIC m)
endif()
# cdp_lib.c registers a pthread key destructor so each thread's context is
# freed when the thread exits. glibc 2.34+ folds libpthread into libc, but
# older glibc and the BSDs still need the explicit link.
find_package(Threads REQUIRED)
target_link_libraries(cdp_lib PUBLIC Threads::Threads)
if(CYCDP_WERROR AND NOT MSVC)
    # mxfft.c is vendored upstream CDP code; hold our own sources to the bar,
    # not someone else's.
    set_source_files_properties(${CDP_LIB_SOURCES} PROPERTIES
        COMPILE_OPTIONS "-Werror")
endif()

# =============================================================================
# Build Cython extension
# =============================================================================
# CYCDP_COVERAGE builds the extension with Cython line tracing so that
# coverage.py can see _core.pyx. It roughly halves execution speed and adds a
# trace call per line, so it is OFF by default and must never be used for
# released wheels. See `make coverage`.
option(CYCDP_COVERAGE "Instrument the Cython extension for coverage.py" OFF)

if(CYCDP_COVERAGE)
    # Cython.Coverage locates the generated C file next to the .pyx to rebuild
    # its line map, so emit it into the source tree (gitignored) rather than
    # the throwaway build directory. --absolute-paths makes the embedded source
    # path resolvable from the repo root; see scripts/run_cython.py.
    set(CYTHON_OUTPUT "${CMAKE_CURRENT_SOURCE_DIR}/src/cycdp/_core.c")
    set(CYTHON_DRIVER_ARGS --absolute-paths --)
    set(CYTHON_EXTRA_ARGS --directive linetrace=True --directive binding=True)
else()
    set(CYTHON_OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/_core.c")
    set(CYTHON_DRIVER_ARGS --)
    set(CYTHON_EXTRA_ARGS)
endif()

# WORKING_DIRECTORY matters for coverage builds: Cython writes the embedded
# source path relative to its own cwd, which would otherwise be scikit-build's
# deep temporary build directory and produce a machine-specific "../../.."
# chain. Running from the source root yields a plain "src/cycdp/_core.pyx".
add_custom_command(
    OUTPUT "${CYTHON_OUTPUT}"
    COMMAND Python::Interpreter
        "${CMAKE_CURRENT_SOURCE_DIR}/scripts/run_cython.py"
        ${CYTHON_DRIVER_ARGS}
        "src/cycdp/_core.pyx"
        ${CYTHON_EXTRA_ARGS} --output-file "${CYTHON_OUTPUT}"
    WORKING_DIRECTORY "${CMAKE_CURRENT_SOURCE_DIR}"
    DEPENDS src/cycdp/_core.pyx scripts/run_cython.py
)

python_add_library(_core MODULE "${CYTHON_OUTPUT}" WITH_SOABI)

if(CYCDP_COVERAGE)
    # CYTHON_TRACE_NOGIL implies CYTHON_TRACE and also traces nogil blocks.
    #
    # CYTHON_USE_SYS_MONITORING=0 is the load-bearing part on Python 3.12+.
    # Cython otherwise instruments via PEP 669 sys.monitoring, but coverage.py
    # only supports file_tracer plugins (which is how Cython.Coverage maps
    # .pyx lines) on its settrace-based cores. The two never meet, and the
    # symptom is silent: the build succeeds, tests pass, and _core.pyx simply
    # never appears in the report. Forcing the legacy trace path makes Cython
    # emit settrace-compatible hooks that the plugin can see.
    target_compile_definitions(_core PRIVATE
        CYTHON_TRACE=1
        CYTHON_TRACE_NOGIL=1
        CYTHON_USE_SYS_MONITORING=0
    )
endif()

# Link against libcdp and cdp_lib
target_link_libraries(_core PRIVATE cdp cdp_lib)
target_include_directories(_core PRIVATE
    ${LIBCDP_DIR}/include
    ${LIBCDP_DIR}/cdp_lib
)

install(TARGETS _core DESTINATION cycdp)
