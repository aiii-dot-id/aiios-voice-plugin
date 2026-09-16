// Normalization/preemphasis ported from Google's MelSpectroProcessor.
// Copyright 2026 Google LLC
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy at http://www.apache.org/licenses/LICENSE-2.0
// Unless required by law or agreed in writing, distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and limitations.
#pragma once
#include <algorithm>
#include <array>
#include <cmath>
#include <complex>
#include <functional>
#include <stdexcept>
#include <vector>

namespace aii::voice::pixel {
// Native implementation of the bound Pixel model's frontend, not the desktop
// Nemotron frontend. Reference math: SparseMelSpectrogram.java and the pinned
// Apache-2.0 Google MelSpectroProcessor (preemphasis and sample normalization).
// No inverse FFT, reconstruction, audio devices, model runtime or Java dependency.
class PcmFrontend {
    static constexpr size_t fft_size=512,mels=128,hop=160;
    static constexpr double pi=3.1415926535897932384626433832795;
    struct Term {size_t bin;double weight;};
    std::array<double,fft_size> window_{};
    std::array<std::vector<Term>,mels> filters_;
    static void fft(std::array<std::complex<double>,fft_size>& v) {
        for(size_t i=1,j=0;i<v.size();++i){
            size_t bit=v.size()>>1;
            for(;j&bit;bit>>=1)j^=bit;
            j^=bit;if(i<j)std::swap(v[i],v[j]);
        }
        for(size_t width=2;width<=v.size();width<<=1){
            const auto root=std::polar(1.0,-2*pi/static_cast<double>(width));
            for(size_t start=0;start<v.size();start+=width){
                std::complex<double> w(1,0);
                for(size_t k=0;k<width/2;++k){
                    const auto left=v[start+k],right=w*v[start+k+width/2];
                    v[start+k]=left+right;v[start+k+width/2]=left-right;w*=root;
                }
            }
        }
    }
public:
    PcmFrontend() {
        for(size_t i=0;i<fft_size;++i)window_[i]=.5-.5*std::cos(2*pi*i/fft_size);
        const double step=200.0/3,threshold=1000.0/step,log_step=std::log(6.4)/27;
        const double max_mel=threshold+std::log(8.0)/log_step;
        std::array<double,mels+2> frequencies{};
        for(size_t i=0;i<frequencies.size();++i){
            const double mel=max_mel/(frequencies.size()-1)*i;
            frequencies[i]=mel<threshold?step*mel:1000*std::exp(log_step*(mel-threshold));
        }
        for(size_t m=0;m<mels;++m){
            const double norm=2/(frequencies[m+2]-frequencies[m]);
            for(size_t k=0;k<=fft_size/2;++k){
                const double hz=8000.0/(fft_size/2)*k;
                const double lower=-(frequencies[m]-hz)/(frequencies[m+1]-frequencies[m]);
                const double upper=(frequencies[m+2]-hz)/(frequencies[m+2]-frequencies[m+1]);
                double value=0;
                if(lower>upper && upper>0)value=upper;
                else if(lower<upper && lower>0)value=lower;
                value*=norm;
                if(value!=0)filters_[m].push_back({k,value});
            }
        }
    }
    static void validate(const std::vector<float>& pcm) {
        if(pcm.size()<258 || pcm.size()>80000)
            throw std::invalid_argument("PCM requires uncut 258..80000 samples at 16 kHz");
        for(float sample:pcm)if(!std::isfinite(sample))throw std::invalid_argument("nonfinite PCM");
    }
    std::vector<float> process(const std::vector<float>& pcm,const std::function<void()>& checkpoint=[] {}) const {
        validate(pcm);checkpoint();
        std::vector<float> emphasized(pcm.size());emphasized[0]=pcm[0];
        for(size_t i=1;i<pcm.size();++i)emphasized[i]=pcm[i]-.97f*pcm[i-1];
        std::vector<double> padded(pcm.size()+fft_size);
        for(size_t i=0;i<pcm.size();++i)padded[256+i]=emphasized[i];
        for(size_t i=0;i<256;++i){padded[255-i]=emphasized[i+1];padded[256+pcm.size()+i]=emphasized[pcm.size()-2-i];}
        const size_t valid=pcm.size()/hop;
        std::vector<float> result(mels*500,0);
        for(size_t t=0;t<valid;++t){
            checkpoint();std::array<std::complex<double>,fft_size> spectrum{};
            for(size_t k=0;k<fft_size;++k)spectrum[k]=window_[k]*padded[t*hop+k];
            fft(spectrum);std::array<double,257> power{};
            for(size_t k=0;k<power.size();++k){
                const double r=spectrum[k].real(),i=spectrum[k].imag();
                const double magnitude=std::sqrt(r*r+i*i);power[k]=magnitude*magnitude;
                if(!std::isfinite(power[k]))throw std::runtime_error("nonfinite spectrum");
            }
            for(size_t m=0;m<mels;++m){
                float sum=0;
                for(const auto& term:filters_[m])sum=static_cast<float>(sum+term.weight*power[term.bin]);
                result[m*500+t]=static_cast<float>(std::log(static_cast<double>(sum+0x1p-24f)));
            }
        }
        for(size_t m=0;m<mels;++m){
            checkpoint();const size_t start=m*500;float sum=0;
            for(size_t t=0;t<valid;++t)sum+=result[start+t];
            const float mean=sum/static_cast<float>(valid);float squares=0;
            for(size_t t=0;t<valid;++t){const float d=result[start+t]-mean;squares+=d*d;}
            const float variance=squares/static_cast<float>(std::max(size_t(1),valid-1));
            const float sd=static_cast<float>(std::sqrt(static_cast<double>(variance)))+1e-5f;
            for(size_t t=0;t<valid;++t){
                float value=(result[start+t]-mean)/sd;
                if(!std::isfinite(value))throw std::runtime_error("nonfinite normalized feature");
                result[start+t]=value;
            }
        }
        checkpoint();return result;
    }
};
}
