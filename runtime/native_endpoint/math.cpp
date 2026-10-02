#include "math.h"
#if defined(__aarch64__)
#include <arm_neon.h>
#endif
#include "sleef.h"
namespace aii::endpoint {
float reference_log10(float value) {
#if defined(__aarch64__)
  return vgetq_lane_f32(Sleef_log10f4_u10advsimd(vdupq_n_f32(value)),0);
#else
  // Each target must prove its actual reference math; this is not a claim
  // that a scalar implementation matches an untested x86 SIMD reference.
  return Sleef_log10f_u10(value);
#endif
}
void reference_log10_four(const float* values, float* results) {
#if defined(__aarch64__)
  vst1q_f32(results,Sleef_log10f4_u10advsimd(vld1q_f32(values)));
#else
  for(int i=0;i<4;++i)results[i]=reference_log10(values[i]);
#endif
}
}
