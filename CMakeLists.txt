cmake_minimum_required(VERSION 3.15...3.31)
project(${SKBUILD_PROJECT_NAME} VERSION ${SKBUILD_PROJECT_VERSION} LANGUAGES C)

set(CMAKE_EXPORT_COMPILE_COMMANDS ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

find_package(Python REQUIRED COMPONENTS Interpreter Development.Module)

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
if(UNIX)
    target_link_libraries(cdp PUBLIC m)
endif()

# =============================================================================
# Build CDP processing library (native spectral operations)
# =============================================================================

# CDP library wrapper sources
set(CDP_LIB_SOURCES
    ${LIBCDP_DIR}/cdp_lib/cdp_lib.c
    ${LIBCDP_DIR}/cdp_lib/cdp_spectral.c
    ${LIBCDP_DIR}/cdp_lib/cdp_io_redirect.c
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
    ${LIBCDP_DIR}/cdp_lib/cdp_shim.c
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

if(MSVC)
    target_compile_options(_core PRIVATE /W4)
else()
    target_compile_options(_core PRIVATE -Wall -Wextra)
endif()

install(TARGETS _core DESTINATION cycdp)
