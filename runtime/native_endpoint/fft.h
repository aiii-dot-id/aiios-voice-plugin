#pragma once
#include <complex>
namespace aii::endpoint {
void fft_frames(const float* frames, std::complex<float>* spectrum);
}
