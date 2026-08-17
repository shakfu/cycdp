/*
 * CDP Library Interface - Core Implementation
 *
 * Context management, buffer operations, and shared utilities.
 */

#include "cdp_lib_internal.h"

/* The vendored FFT (projects/cpd8/dev/pv/mxfft.c) writes its diagnostics into
 * this global, declared `extern char errstr[]` by CDP's globcon.h. Nothing in
 * this library reads it -- the FFT only writes on allocation-failure and
 * bad-parameter paths, and those return a failure code the callers act on --
 * but the symbol has to exist for mxfft.c to link.
 *
 * It lived in cdp_shim.c until that file was dropped from the build. Keeping
 * it here rather than patching mxfft.c avoids one more divergence from
 * upstream to carry across a re-vendoring. Hidden visibility keeps the
 * generic name out of the extension's symbol table. */
char errstr[2400];

/* =========================================================================
 * Context Management
 * ========================================================================= */

cdp_lib_ctx* cdp_lib_init(void) {
    cdp_lib_ctx* ctx = (cdp_lib_ctx*)calloc(1, sizeof(cdp_lib_ctx));
    if (ctx == NULL) {
        return NULL;
    }

    ctx->initialized = 1;
    cdp_lib_seed(ctx, 0);  /* Initialize PRNG with time-based seed */
    return ctx;
}

/* Thread-local context storage. See cdp_lib_thread_ctx() in cdp_lib.h.
 *
 * Two mechanisms, deliberately:
 *
 *   - A plain thread-local pointer is the fast path. Every processing call
 *     goes through cdp_lib_thread_ctx(), so the common case must be a load and
 *     a branch, not a pthread_getspecific().
 *
 *   - A pthread key (FLS on Windows) exists only for its destructor, which the
 *     runtime invokes when a thread exits. A plain thread-local has no such
 *     hook, and the context was previously never freed at all: one 528-byte
 *     allocation per thread that ever called into the library, retained for
 *     the life of the process. That is fine for a fixed worker pool and an
 *     unbounded leak for anything that creates a thread per request --
 *     measured at ~2 MB per 3,000 short-lived threads, growing without limit.
 */
#if defined(_MSC_VER)
#  define CDP_THREAD_LOCAL __declspec(thread)
#elif defined(__STDC_VERSION__) && __STDC_VERSION__ >= 201112L && !defined(__STDC_NO_THREADS__)
#  define CDP_THREAD_LOCAL _Thread_local
#else
#  define CDP_THREAD_LOCAL __thread
#endif

static CDP_THREAD_LOCAL cdp_lib_ctx* cdp_tls_ctx = NULL;

#if defined(_WIN32)

#include <windows.h>

static DWORD cdp_fls_index = FLS_OUT_OF_INDEXES;
static INIT_ONCE cdp_fls_once = INIT_ONCE_STATIC_INIT;

static void WINAPI cdp_tls_destroy(void* p) {
    free(p);
}

static BOOL CALLBACK cdp_fls_init(PINIT_ONCE once, PVOID param, PVOID* ctx) {
    (void)once; (void)param; (void)ctx;
    cdp_fls_index = FlsAlloc(cdp_tls_destroy);
    return TRUE;
}

/* Returns 0 if the context will be freed at thread exit, -1 otherwise. */
static int cdp_tls_register(cdp_lib_ctx* ctx) {
    InitOnceExecuteOnce(&cdp_fls_once, cdp_fls_init, NULL, NULL);
    if (cdp_fls_index == FLS_OUT_OF_INDEXES) return -1;
    return FlsSetValue(cdp_fls_index, ctx) ? 0 : -1;
}

static void cdp_tls_unregister(void) {
    if (cdp_fls_index != FLS_OUT_OF_INDEXES) {
        FlsSetValue(cdp_fls_index, NULL);
    }
}

#else

#include <pthread.h>

static pthread_key_t cdp_tls_key;
static pthread_once_t cdp_tls_once = PTHREAD_ONCE_INIT;
static int cdp_tls_key_ok = 0;

static void cdp_tls_destroy(void* p) {
    free(p);
}

static void cdp_tls_make_key(void) {
    cdp_tls_key_ok = (pthread_key_create(&cdp_tls_key, cdp_tls_destroy) == 0);
}

static int cdp_tls_register(cdp_lib_ctx* ctx) {
    pthread_once(&cdp_tls_once, cdp_tls_make_key);
    if (!cdp_tls_key_ok) return -1;
    return pthread_setspecific(cdp_tls_key, ctx) == 0 ? 0 : -1;
}

static void cdp_tls_unregister(void) {
    if (cdp_tls_key_ok) {
        pthread_setspecific(cdp_tls_key, NULL);
    }
}

#endif

cdp_lib_ctx* cdp_lib_thread_ctx(void) {
    if (cdp_tls_ctx == NULL) {
        cdp_lib_ctx* ctx = cdp_lib_init();
        if (ctx == NULL) return NULL;
        if (cdp_tls_register(ctx) != 0) {
            /* No destructor hook available. Still hand back a working context
             * -- refusing to process because cleanup cannot be automated would
             * be the worse failure -- but this thread's context will leak, as
             * every thread's did before the hook existed. */
            cdp_tls_ctx = ctx;
            return ctx;
        }
        cdp_tls_ctx = ctx;
    }
    return cdp_tls_ctx;
}

void cdp_lib_release_thread_ctx(void) {
    if (cdp_tls_ctx == NULL) return;
    cdp_tls_unregister();  /* so the destructor does not free it twice */
    free(cdp_tls_ctx);
    cdp_tls_ctx = NULL;
}

void cdp_lib_cleanup(cdp_lib_ctx* ctx) {
    if (ctx == NULL) return;
    free(ctx);
}

const char* cdp_lib_get_error(cdp_lib_ctx* ctx) {
    if (ctx == NULL) return "Context is NULL";
    return ctx->error_msg;
}

/* =========================================================================
 * Buffer Management
 * ========================================================================= */

cdp_lib_buffer* cdp_lib_buffer_create(size_t length, int channels, int sample_rate) {
    cdp_lib_buffer* buf = (cdp_lib_buffer*)calloc(1, sizeof(cdp_lib_buffer));
    if (buf == NULL) return NULL;

    buf->data = (float*)calloc(length, sizeof(float));
    if (buf->data == NULL) {
        free(buf);
        return NULL;
    }

    buf->length = length;
    buf->channels = channels;
    buf->sample_rate = sample_rate;

    return buf;
}

cdp_lib_buffer* cdp_lib_buffer_from_data(float *data, size_t length,
                                          int channels, int sample_rate) {
    cdp_lib_buffer* buf = (cdp_lib_buffer*)calloc(1, sizeof(cdp_lib_buffer));
    if (buf == NULL) return NULL;

    buf->data = data;
    buf->length = length;
    buf->channels = channels;
    buf->sample_rate = sample_rate;

    return buf;
}

void cdp_lib_buffer_free(cdp_lib_buffer* buf) {
    if (buf == NULL) return;
    if (buf->data) free(buf->data);
    free(buf);
}

/* =========================================================================
 * Shared Utilities
 * ========================================================================= */

/*
 * Convert buffer to mono if needed (exported for use by other modules)
 */
cdp_lib_buffer* cdp_lib_to_mono(cdp_lib_ctx* ctx, const cdp_lib_buffer* input) {
    if (input->channels == 1) {
        /* Already mono - make a copy */
        cdp_lib_buffer* output = cdp_lib_buffer_create(
            input->length, 1, input->sample_rate);
        if (output == NULL) {
            snprintf(ctx->error_msg, sizeof(ctx->error_msg),
                     "Failed to allocate buffer");
            return NULL;
        }
        memcpy(output->data, input->data, input->length * sizeof(float));
        return output;
    }

    /* Convert to mono by averaging channels */
    size_t frames = input->length / input->channels;
    cdp_lib_buffer* output = cdp_lib_buffer_create(frames, 1, input->sample_rate);
    if (output == NULL) {
        snprintf(ctx->error_msg, sizeof(ctx->error_msg),
                 "Failed to allocate buffer");
        return NULL;
    }

    for (size_t i = 0; i < frames; i++) {
        float sum = 0;
        for (int ch = 0; ch < input->channels; ch++) {
            sum += input->data[i * input->channels + ch];
        }
        output->data[i] = sum / input->channels;
    }

    return output;
}
