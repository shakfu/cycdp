/*
 * CDP Shim Layer - Memory buffer I/O for CDP processing functions.
 *
 * NOT BUILT. Kept as the record of an approach that was tried and abandoned.
 * Neither this file nor cdp_io_redirect.c is in CDP_LIB_SOURCES, and
 * tests/test_concurrency.py::test_shim_remains_unreachable fails if anything
 * starts calling into them.
 *
 * What it was for
 * ---------------
 * CDP's programs are built around sfsys, its soundfile library: every
 * algorithm reads with fgetfbufEx, seeks with sndseekEx, writes with
 * fputfbufEx. The idea was to satisfy those calls from memory instead of the
 * filesystem, so that an unmodified CDP source file could be compiled with
 * -DCDP_LIBRARY_MODE (see cdp_sfsys_shim.h, which #defines the sfsys entry
 * points to wrappers over the slot table in cdp_io_redirect.c) and called
 * in-process. That would have made all ~500 CDP programs available by
 * compiling them rather than rewriting them, with output identical to the
 * originals.
 *
 * Why it was abandoned
 * --------------------
 * Intercepting I/O is necessary but nowhere near sufficient. CDP algorithms
 * are main() programs: command-line parsing, extensive global state, and the
 * dataptr/datalist parameter machinery (the bare forward declaration of
 * dataptr below is a fossil of that attempt). Porting each algorithm's core
 * loop proved cheaper, so every operation in cdp_lib/ is an independent
 * reimplementation -- see DEV_GUIDE.md, whose step 2 says to ignore the file
 * I/O and command-line parsing entirely.
 *
 * If this is ever revived
 * -----------------------
 * The design is deliberately process-global because CDP itself is (see
 * g_cdp_shim below). That is now a blocker rather than a faithful choice: the
 * Python bindings release the GIL around every processing call, so a single
 * global slot table would be a data race the moment two threads used it.
 * Making the state thread-local is necessary but, again, not sufficient --
 * the hosting problem above has to be solved first, and the right ownership
 * model will follow from whatever solves it.
 */

#ifndef CDP_SHIM_H
#define CDP_SHIM_H

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Forward declaration - actual structure defined in structures.h */
typedef struct datalist *dataptr;

/*
 * Memory buffer descriptor for I/O redirection.
 */
typedef struct cdp_membuf {
    float *data;           /* Sample data */
    size_t capacity;       /* Total capacity in samples */
    size_t length;         /* Actual data length in samples */
    size_t position;       /* Current read/write position */
    int channels;
    int sample_rate;
} cdp_membuf;

/*
 * CDP shim context - holds state for memory-based I/O.
 */
typedef struct cdp_shim_ctx {
    cdp_membuf *input;     /* Input buffer(s) */
    int input_count;       /* Number of input buffers */
    cdp_membuf *output;    /* Output buffer */
    int initialized;
} cdp_shim_ctx;

/* Global shim context (CDP uses global state) */
extern cdp_shim_ctx *g_cdp_shim;

/*
 * Initialize the shim layer.
 * Must be called before any CDP processing.
 */
int cdp_shim_init(void);

/*
 * Clean up the shim layer.
 */
void cdp_shim_cleanup(void);

/*
 * Set input buffer for processing.
 * The shim will redirect file reads to this buffer.
 */
int cdp_shim_set_input(float *data, size_t length, int channels, int sample_rate);

/*
 * Set output buffer for processing.
 * The shim will redirect file writes to this buffer.
 * If capacity is 0, the shim will allocate as needed.
 */
int cdp_shim_set_output(float *data, size_t capacity, int channels, int sample_rate);

/*
 * Get output buffer after processing.
 * Returns the number of samples written.
 */
size_t cdp_shim_get_output(float **data, int *channels, int *sample_rate);

/*
 * Shim I/O functions - these replace sfsys functions.
 */

/* Replacement for sndopenEx - returns fake file descriptor */
int shim_sndopenEx(const char *name, int auto_scale, int access);

/* Replacement for sndcreat_formatted */
int shim_sndcreat_formatted(const char *fn, int size, int stype,
                            int channels, int srate, int mode);

/* Replacement for sndcloseEx */
int shim_sndcloseEx(int sfd);

/* Replacement for fgetfbufEx - read samples from input buffer */
int shim_fgetfbufEx(float *fp, int count, int sfd, int expect_floats);

/* Replacement for fputfbufEx - write samples to output buffer */
int shim_fputfbufEx(float *fp, int count, int sfd);

/* Replacement for sndseekEx */
int shim_sndseekEx(int sfd, int dist, int whence);

/* Replacement for sndsizeEx */
int shim_sndsizeEx(int sfd);

/* File descriptor constants for shim */
#define SHIM_INPUT_FD       1000    /* Legacy single input FD */
#define SHIM_OUTPUT_FD      1001
#define SHIM_INPUT_FD_BASE  10000   /* Base FD for multi-input slots */
#define SHIM_TEMP_FD_BASE   12000   /* Base FD for temporary buffers */
#define SHIM_MAX_INPUT_SLOTS 16

/*
 * Multi-input support API
 *
 * These functions allow registering multiple input buffers for algorithms
 * that require 2+ inputs (e.g., morph operations).
 */

/*
 * Register input buffer at a specific slot (0-15).
 * Returns a fake file descriptor for this slot.
 */
int cdp_shim_set_input_slot(int slot, float *data, size_t length,
                            int channels, int sample_rate);

/*
 * Get the file descriptor for a registered input slot.
 * Returns -1 if slot is not registered.
 */
int cdp_shim_get_input_fd(int slot);

/*
 * Get the membuf for a given file descriptor.
 * Returns NULL if FD is not valid.
 */
cdp_membuf* cdp_shim_get_membuf(int fd);

/*
 * Create a temporary buffer (e.g., for specbridge offset padding).
 * Returns a fake file descriptor.
 */
int cdp_shim_create_temp(int channels, int sample_rate);

/*
 * Free a temporary buffer by file descriptor.
 */
void cdp_shim_free_temp(int fd);

/*
 * Reset read position for a slot to beginning.
 */
void cdp_shim_reset_slot(int slot);

/*
 * Reset all slots and clear state for a new processing run.
 */
void cdp_shim_reset_all(void);

#ifdef __cplusplus
}
#endif

#endif /* CDP_SHIM_H */
