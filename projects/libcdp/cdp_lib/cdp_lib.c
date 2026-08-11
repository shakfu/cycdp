/*
 * CDP Library Interface - Core Implementation
 *
 * Context management, buffer operations, and shared utilities.
 */

#include "cdp_lib_internal.h"

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

/* Thread-local context storage. See cdp_lib_thread_ctx() in cdp_lib.h. */
#if defined(_MSC_VER)
#  define CDP_THREAD_LOCAL __declspec(thread)
#elif defined(__STDC_VERSION__) && __STDC_VERSION__ >= 201112L && !defined(__STDC_NO_THREADS__)
#  define CDP_THREAD_LOCAL _Thread_local
#else
#  define CDP_THREAD_LOCAL __thread
#endif

static CDP_THREAD_LOCAL cdp_lib_ctx* cdp_tls_ctx = NULL;

cdp_lib_ctx* cdp_lib_thread_ctx(void) {
    if (cdp_tls_ctx == NULL) {
        cdp_tls_ctx = cdp_lib_init();
    }
    return cdp_tls_ctx;
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
