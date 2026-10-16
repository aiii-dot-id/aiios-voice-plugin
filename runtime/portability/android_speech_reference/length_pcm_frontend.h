#pragma once
#include "frontend.h"
#include <algorithm>
#include <cmath>
#include <functional>
#include <stdexcept>
#include <vector>

namespace aii::voice::pixel {
// The length-aware HF checkpoint uses the existing native 400-sample symmetric
// Hann/zero-padded spectral transform, but requires per-utterance sample
// normalization. The old Google five-second frontend is NOT interchangeable.
// Coefficients are the pinned checkpoint processor's float32 Slaney filters.
class LengthPcmFrontend {
    std::vector<float> mel_;
    // Reproduce the pinned ARM float32 processor's contiguous-axis reduction:
    // four lanes, four interleaved accumulators, 16-group chunks, then tail
    // before lanes. Order matters for near-constant spectra: a sequential sum
    // accumulates drift which normalization amplifies by its small epsilon.
    // The bounded 2001-frame domain needs only two chunk levels. Include the
    // processor's masked final FFT frame in grouping, although its value is 0.
    template<class Value> static float processor_sum(size_t n,const Value& value) {
        float partial[4][4]{}, chunks[4][4]{};
        const auto vectors=n/4,groups=vectors/4;
        for(size_t g=0;g<groups;++g) {
            for(size_t row=0;row<4;++row)for(size_t lane=0;lane<4;++lane)
                partial[row][lane]+=value(g*16+row*4+lane);
            if((g+1)%16==0)for(size_t row=0;row<4;++row)for(size_t lane=0;lane<4;++lane) {
                chunks[row][lane]+=partial[row][lane];partial[row][lane]=0;
            }
        }
        for(size_t row=0;row<4;++row)for(size_t lane=0;lane<4;++lane)partial[row][lane]+=chunks[row][lane];
        for(size_t v=groups*4;v<vectors;++v)for(size_t lane=0;lane<4;++lane)partial[0][lane]+=value(v*4+lane);
        for(size_t row=1;row<4;++row)for(size_t lane=0;lane<4;++lane)partial[0][lane]+=partial[row][lane];
        float sum=0;
        for(size_t i=vectors*4;i<n;++i)sum+=value(i);
        for(size_t lane=0;lane<4;++lane)sum+=partial[0][lane];
        return sum;
    }
public:
    static constexpr size_t capacity=320000, feature_frames=2000, channels=128;
    explicit LengthPcmFrontend(std::vector<float> mel):mel_(std::move(mel)) {
        aii::asr::Frontend validate(mel_.data(),mel_.size());
    }
    static void validate(const std::vector<float>& pcm) {
        // Two real frames are necessary for sample variance (N-1). A stream
        // owner buffers shorter input; this complete-window call never pads
        // short input into invented speech or truncates oversized input.
        if(pcm.size()<320 || pcm.size()>capacity)
            throw std::invalid_argument("length-aware PCM requires 320..320000 samples at 16 kHz");
        if(!std::all_of(pcm.begin(),pcm.end(),[](float v){return std::isfinite(v);}))
            throw std::invalid_argument("nonfinite length-aware PCM");
    }
    std::vector<float> process(const std::vector<float>& pcm,const std::function<void()>& checkpoint=[] {}) const {
        validate(pcm);checkpoint();
        aii::asr::Frontend spectral(mel_.data(),mel_.size());
        for(size_t offset=0;offset<pcm.size();) {
            checkpoint();const auto n=std::min(size_t(32000),pcm.size()-offset);
            spectral.accept(pcm.data()+offset,n);offset+=n;
        }
        spectral.finish();const auto valid=pcm.size()/160;
        if(spectral.frames_ready()!=valid)throw std::runtime_error("feature clock mismatch");
        std::vector<float> result(feature_frames*channels,0);
        for(size_t first=0;first<valid;) {
            checkpoint();const auto n=std::min(size_t(16),valid-first);
            auto frames=spectral.frames(first,n);
            std::copy(frames.begin(),frames.end(),result.begin()+first*channels);first+=n;
        }
        for(size_t m=0;m<channels;++m) {
            checkpoint();
            const float sum=processor_sum(valid+1,[&](size_t t){return t<valid?result[t*channels+m]:0.0f;});
            const float mean=sum/static_cast<float>(valid);
            const float squares=processor_sum(valid+1,[&](size_t t){
                if(t>=valid)return 0.0f;
                const float d=result[t*channels+m]-mean;return d*d;
            });
            const float deviation=std::sqrt(squares/static_cast<float>(valid-1))+1e-5f;
            for(size_t t=0;t<valid;++t) {
                float value=(result[t*channels+m]-mean)/deviation;
                if(!std::isfinite(value))throw std::runtime_error("nonfinite length-aware feature");
                result[t*channels+m]=value;
            }
        }
        checkpoint();return result;
    }
};
}
