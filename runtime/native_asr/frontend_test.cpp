#include "frontend.h"
#include <algorithm>
#include <cmath>
#include <functional>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <vector>
using aii::asr::Frontend;
static_assert(sizeof(Frontend) <= 4096, "frontend coefficients must not consume an embedding thread stack");
void require(bool pass, const char* name) { if (!pass) throw std::runtime_error(name); }
template<class F> void refused(F f, const char* name) {
  bool threw = false;
  try { f(); } catch (const std::exception&) { threw = true; }
  require(threw, name);
}
int main() {
  try {
    std::vector<float> mel(128*257, .001f), zero(1600, 0), unit(1600, 1);
    Frontend silence(mel.data(), mel.size());
    silence.accept(zero.data(), zero.size()); silence.finish();
    const auto features = silence.frames(0, 10);
    require(std::all_of(features.begin(), features.end(), [](float x) {return x == std::log(0x1p-24f);}), "silence additive floor");
    refused([&] { silence.accept(unit.data(), 1); }, "closed input accepted");
    refused([&] { silence.finish(); }, "duplicate finish accepted");
    refused([&] { silence.frames(0, 11); }, "future frames returned");
    refused([&] { silence.frames(0, 0); }, "empty frame range returned");
    Frontend edge(mel.data(), mel.size());
    edge.accept(unit.data(), 255); require(edge.frames_ready() == 0, "premature acoustic context");
    edge.accept(unit.data(), 1); require(edge.frames_ready() == 1, "first complete frame lost");
    auto first = edge.frames(0, 1);
    edge.accept(unit.data(), 544);
    require(first == edge.frames(0, 1), "past frame changed");
    require(edge.frames_ready() == 4, "live edge extent");
    edge.finish(); require(edge.frames_ready() == 5, "final edge not flushed");
    Frontend empty(mel.data(), mel.size());
    refused([&] { empty.finish(); }, "empty finish accepted");
    refused([&] { empty.accept(nullptr, 1); }, "null PCM accepted");
    refused([&] { empty.accept(unit.data(), 0); }, "empty PCM accepted");
    const float nan = std::numeric_limits<float>::quiet_NaN();
    refused([&] { empty.accept(&nan, 1); }, "nonfinite PCM accepted");
    require(empty.samples() == 0, "invalid input mutated state");
    std::vector<float> signal(8333);
    for (size_t i = 0; i < signal.size(); ++i) signal[i] = float(std::sin(i * .023) * .15);
    Frontend whole(mel.data(), mel.size()), split(mel.data(), mel.size());
    whole.accept(signal.data(), signal.size());
    for (size_t i = 0; i < signal.size(); i += 73) split.accept(signal.data()+i, std::min(size_t(73), signal.size()-i));
    whole.finish(); split.finish(); require(whole.frames(0, 52) == split.frames(0, 52), "packetization changes features");
    std::vector<float> dc(128*257, 0), impulse(1600, 0);
    for (size_t m = 0; m < 128; ++m) dc[m*257] = 1;
    impulse[0] = .25f;
    Frontend analytic(dc.data(), dc.size()); analytic.accept(impulse.data(), impulse.size()); analytic.finish();
    const double pi = std::acos(-1.0);
    const double a = (1-std::cos(2*pi*200/399))/2, b = (1-std::cos(2*pi*201/399))/2;
    const double expected = std::log(std::pow(.25*(a-.97*b), 2)+std::pow(2., -24));
    require(std::abs(analytic.frames(0, 1)[0]-expected) < .00001, "first impulse window/preemphasis");
    Frontend capacity(mel.data(), mel.size());
    std::vector<float> batch(32000, 0);
    while (capacity.samples() < Frontend::kCapacity)
      capacity.accept(batch.data(), std::min(batch.size(), Frontend::kCapacity-capacity.samples()));
    refused([&] { capacity.accept(unit.data(), 1); }, "capacity accepted overflow");
    require(capacity.samples() == Frontend::kCapacity, "capacity failure changed samples");
    // Compare every recurrent feature window with full-history arithmetic,
    // then retire only samples that no later window can use.
    std::vector<float> rolling_signal(16000*9+317);
    for(size_t i=0;i<rolling_signal.size();++i) rolling_signal[i]=float(std::sin(i*.0173)*.12+std::cos(i*.0391)*.07);
    Frontend reference(mel.data(),mel.size()),rolling(mel.data(),mel.size());
    for(size_t i=0;i<rolling_signal.size();i+=32000)
      reference.accept(rolling_signal.data()+i,std::min(size_t(32000),rolling_signal.size()-i));
    size_t processed=0,windows=0,peak_retained=0;
    auto compare=[&]{
      while(rolling.frames_ready()>=processed+49) {
        const size_t first=processed?processed-16:0, count=processed?65:49;
        require(rolling.frames(first,count)==reference.frames(first,count),"rolling window changed feature bits");
        processed+=56; ++windows;
        rolling.discard_before((processed-16)*160-257);
        peak_retained=std::max(peak_retained,rolling.retained_samples());
      }
    };
    for(size_t i=0;i<rolling_signal.size();i+=241) {
      rolling.accept(rolling_signal.data()+i,std::min(size_t(241),rolling_signal.size()-i));compare();
    }
    require(windows>12 && peak_retained<12000,"rolling PCM history did not stay bounded");
    refused([&]{rolling.frames(0,1);},"retired feature history silently returned");
    const auto before=rolling.first_sample();
    refused([&]{rolling.discard_before(before-1);},"backward retirement accepted");
    require(rolling.first_sample()==before,"refused retirement changed absolute clock");
    rolling.finish();reference.finish();compare();
    require(rolling.samples()==rolling_signal.size(),"retirement changed source count");
    std::vector<float> huge(1600, std::numeric_limits<float>::max());
    Frontend overflow(mel.data(), mel.size()); overflow.accept(huge.data(), huge.size());
    refused([&] { overflow.frames(0, 1); }, "nonfinite features escaped");
    std::cout << "frontend contracts passed: floor, edges, packetization, first impulse, refusal atomicity, capacity, overflow\n";
    return 0;
  } catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
}
