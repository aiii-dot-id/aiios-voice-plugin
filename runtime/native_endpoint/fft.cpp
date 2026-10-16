#include "fft.h"
#include "pocketfft_hdronly.h"
namespace aii::endpoint {
void fft_frames(const float* frames, std::complex<float>* spectrum) {
  // Reference Torch uses batched float32 R2C; batching matters for the
  // implementation's SIMD path. Keep its 801 frames, then discard the last.
  pocketfft::r2c<float>({801,400},{400*4,4},{201*8,8},{1},true,
                       frames,spectrum,1.0f,1);
}
}
