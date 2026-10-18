#include "fft.h"
#include "pocketfft_hdronly.h"
namespace aii::endpoint {
void fft_frames(const float* frames, std::complex<float>* spectrum) {
  pocketfft::r2c<float>({801,400},{400*4,4},{201*8,8},{1},true,
                       frames,spectrum,1.0f,1);
}
}
