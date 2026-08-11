/*
 * CDP Wrappage - Granular Texture with Spatial Distribution
 *
 * Granular reconstitution with stereo spatial spreading.
 * Based on CDP's wrappage/sausage algorithms.
 */

#ifndef CDP_WRAPPAGE_H
#define CDP_WRAPPAGE_H

#include "cdp_lib.h"

/*
 * Apply wrappage effect - granular texture with stereo spatial distribution.
 *
 * Extracts grains from the input and redistributes them spatially across
 * a stereo field, creating textural transformations with optional time
 * stretching and pitch shifting.
 *
 * Parameters:
 *   ctx        - Processing context
 *   input      - Input buffer (must be mono)
 *   grain_size - Size of each grain in milliseconds (1.0 to 500.0). Default 50.0.
 *   density    - Grain overlap factor (0.1 to 10.0). Default 1.0.
 *                <1 = gaps between grains, 1 = no overlap, >1 = overlapping
 *   velocity   - Speed of advance through input (0.0 to 10.0). Default 1.0.
 *                <1 = time stretch, 1 = normal, >1 = time compress
 *                0 = freeze (requires duration parameter)
 *   pitch      - Pitch shift in semitones (-24.0 to 24.0). Default 0.0.
 *   spread     - Stereo spread (0.0 to 1.0). Default 1.0.
 *                0 = mono center, 1 = full stereo spread
 *   jitter     - Random variation of grain position (0.0 to 1.0). Default 0.1.
 *   splice_ms  - Splice length at grain boundaries in ms (0.5 to 50.0). Default 5.0.
 *   duration   - Output duration in seconds. Default 0.0 (auto, based on input).
 *                Must be set if velocity is 0.
 *
 * Returns:
 *   New stereo buffer with wrappage effect applied, or NULL on error.
 */
cdp_lib_buffer* cdp_lib_wrappage(cdp_lib_ctx* ctx,
                                  const cdp_lib_buffer* input,
                                  double grain_size,
                                  double density,
                                  double velocity,
                                  double pitch,
                                  double spread,
                                  double jitter,
                                  double splice_ms,
                                  double duration,
                                  unsigned int seed);

#endif /* CDP_WRAPPAGE_H */
