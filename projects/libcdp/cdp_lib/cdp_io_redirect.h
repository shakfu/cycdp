/*
 * CDP I/O Redirect Layer
 *
 * NOT BUILT. The slot table behind the abandoned sfsys shim; see the header
 * comment in cdp_shim.h for what the approach was and why it was dropped.
 *
 * This layer intercepts CDP's file I/O calls and redirects them
 * to memory buffers when running in library mode.
 *
 * When CDP_LIBRARY_MODE is defined, the I/O functions in sfsys
 * are redirected through this layer.
 */

#ifndef CDP_IO_REDIRECT_H
#define CDP_IO_REDIRECT_H

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/*
 * Buffer slot for I/O redirection.
 * Each "file" opened gets a slot.
 */
typedef struct cdp_io_slot {
    int in_use;
    int is_input;          /* 1 = input, 0 = output */
    float *data;           /* Sample data */
    size_t capacity;       /* Buffer capacity in samples */
    size_t length;         /* Data length in samples */
    size_t position;       /* Current position */
    int channels;
    int sample_rate;
    int sample_type;       /* SAMP_FLOAT, etc. */
    int owns_data;         /* 1 if we allocated it */
} cdp_io_slot;

#define CDP_MAX_IO_SLOTS 16

/*
 * Global I/O state
 */
typedef struct cdp_io_state {
    int initialized;
    int library_mode;      /* 1 = redirect I/O, 0 = normal file I/O */
    cdp_io_slot slots[CDP_MAX_IO_SLOTS];
    int next_fd;           /* Next fake file descriptor */
    char error_msg[256];
} cdp_io_state;

extern cdp_io_state g_cdp_io;

/*
 * Initialize the I/O redirect layer.
 */
int cdp_io_init(void);

/*
 * Cleanup the I/O redirect layer.
 */
void cdp_io_cleanup(void);

/*
 * Enable/disable library mode (memory I/O redirection).
 */
void cdp_io_set_library_mode(int enabled);

/*
 * Register an input buffer.
 * Returns a fake file descriptor.
 */
int cdp_io_register_input(float *data, size_t length, int channels, int sample_rate);

/*
 * Register an output buffer.
 * If data is NULL, will allocate as needed.
 * Returns a fake file descriptor.
 */
int cdp_io_register_output(float *data, size_t capacity, int channels, int sample_rate);

/*
 * Get output data after processing.
 */
size_t cdp_io_get_output(int fd, float **data, int *channels, int *sample_rate);

/*
 * Close a slot and free resources.
 */
void cdp_io_close_slot(int fd);

/*
 * Redirected I/O functions (called by sfsys when in library mode)
 */
int cdp_io_read(int fd, float *buf, int count);
int cdp_io_write(int fd, float *buf, int count);
int cdp_io_seek(int fd, int offset, int whence);
int cdp_io_tell(int fd);
int cdp_io_size(int fd);

/*
 * Check if fd is a redirected slot
 */
int cdp_io_is_redirected(int fd);

/*
 * Get slot for fd
 */
cdp_io_slot* cdp_io_get_slot(int fd);

#ifdef __cplusplus
}
#endif

#endif /* CDP_IO_REDIRECT_H */
