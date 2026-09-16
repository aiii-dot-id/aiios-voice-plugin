#include "uid_frontend.h"
#include "kaldi-native-fbank/csrc/feature-fbank.h"
#include <algorithm>
#include <cmath>
#include <vector>

extern "C" int aiii_uid_fbank(const uint8_t *pcm, size_t bytes, int rate,
                               float *output, size_t capacity, size_t *frames,
                               aiii_uid_cancelled cancelled, void *context) {
  if (!frames) return 1;
  *frames = 0;
  if (!pcm || !output || rate != 16000 || bytes % 2 ||
      bytes < 31920 * 2 || bytes > 480000 * 2) return 1;
  const size_t count = bytes / 2;
  const size_t n = 1 + (count - 400) / 160;
  if (capacity < n * 80) return 1;
  auto stopped = [&] { return cancelled && cancelled(context); };
  if (stopped()) return 3;
  try {
    std::vector<float> samples(count);
    double energy = 0;
    size_t clipped = 0;
    for (size_t i = 0; i < count; ++i) {
      int value = int(pcm[2*i]) | (int(pcm[2*i+1]) << 8);
      if (value >= 32768) value -= 65536;
      samples[i] = float(value); // original signed PCM16 scale, not [-1,1]
      double scaled = value / 32768.0;
      energy += scaled * scaled;
      clipped += std::abs(value) >= 32760;
    }
    if (std::sqrt(energy / count) < 0.0003 || double(clipped) / count > 0.1)
      return 2;
    knf::FbankOptions options;
    options.frame_opts.samp_freq = 16000;
    options.frame_opts.dither = 0;
    options.frame_opts.frame_length_ms = 25;
    options.frame_opts.frame_shift_ms = 10;
    options.frame_opts.window_type = "hamming";
    options.frame_opts.preemph_coeff = 0.97f;
    options.frame_opts.remove_dc_offset = true;
    options.frame_opts.snip_edges = true;
    options.mel_opts.num_bins = 80;
    options.use_energy = false;
    knf::FbankComputer computer(options);
    knf::FeatureWindowFunction window(options.frame_opts);
    std::vector<float> result(n * 80), scratch;
    float means[80] = {};
    for (size_t t = 0; t < n; ++t) {
      if (stopped()) return 3;
      // The FFT mutates scratch, including its zero-padded tail. Kaldi's
      // OnlineFbank clears it every frame; ExtractWindow does not do so.
      std::fill(scratch.begin(), scratch.end(), 0);
      knf::ExtractWindow(0, samples, int32_t(t), options.frame_opts,
                         window, &scratch, nullptr);
      computer.Compute(0, 1, &scratch, result.data() + t * 80);
      for (size_t b = 0; b < 80; ++b) means[b] += result[t*80+b];
    }
    for (float &mean : means) mean /= float(n);
    for (size_t t = 0; t < n; ++t) {
      if (stopped()) return 3;
      for (size_t b = 0; b < 80; ++b) {
        float &v = result[t*80+b];
        v -= means[b];
        if (!std::isfinite(v)) return 4;
      }
    }
    if (stopped()) return 3;
    std::copy(result.begin(), result.end(), output);
    *frames = n;
    return 0;
  } catch (...) {
    return 4;
  }
}
