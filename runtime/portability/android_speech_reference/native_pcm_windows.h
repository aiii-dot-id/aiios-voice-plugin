#pragma once
#include <algorithm>
#include <cmath>
#include <cstdint>
#include <limits>
#include <stdexcept>
#include <vector>

namespace aii::voice::pixel {
// Inference-owner-only bounded ring. Scheduling depends on the source sample
// clock, never the size/timing of input packets. Consumer owns each materialized
// window; its exception faults this stream. No transcript merging is done here.
class PcmWindows {
public:
    struct Window {uint64_t start,end;std::vector<float> pcm;};
    static constexpr size_t capacity=80000,step=16000;
private:
    std::vector<float> ring_=std::vector<float>(capacity);
    uint64_t received_=0,emitted_=0,next_=step;
    bool ended_=false,failed_=false;
    Window window() const {
        const uint64_t start=received_>capacity?received_-capacity:0;
        Window w{start,received_,std::vector<float>(static_cast<size_t>(received_-start))};
        for(size_t i=0;i<w.pcm.size();++i)w.pcm[i]=ring_[(start+i)%capacity];
        return w;
    }
    void check() const {if(failed_)throw std::runtime_error("PCM window stream faulted");}
public:
    uint64_t received() const{return received_;}
    uint64_t emitted() const{return emitted_;}
    size_t retained() const{return static_cast<size_t>(std::min<uint64_t>(received_,capacity));}
    template<class Consume> void accept(uint64_t start,const float* pcm,size_t n,Consume consume) {
        check();if(ended_)throw std::runtime_error("PCM window input finished");
        if(start!=received_ || !pcm || !n || n>32000 || n>std::numeric_limits<uint64_t>::max()-received_)
            throw std::invalid_argument("PCM window gap, overlap, extent or clock overflow");
        for(size_t i=0;i<n;++i)if(!std::isfinite(pcm[i]))throw std::invalid_argument("nonfinite PCM packet");
        size_t offset=0;
        try {
            while(offset<n){
                const size_t take=static_cast<size_t>(std::min<uint64_t>(n-offset,next_-received_));
                for(size_t i=0;i<take;++i)ring_[(received_+i)%capacity]=pcm[offset+i];
                received_+=take;offset+=take;
                if(received_==next_){
                    consume(window());emitted_=received_;
                    if(next_>std::numeric_limits<uint64_t>::max()-step)throw std::overflow_error("PCM window clock exhausted");
                    next_+=step;
                }
            }
        }catch(...){failed_=true;throw;}
    }
    template<class Consume> void finish(uint64_t cutoff,Consume consume) {
        check();if(cutoff!=received_)throw std::invalid_argument("PCM Finish cutoff differs from consumed input");
        if(ended_)return;
        try {if(received_!=emitted_){consume(window());emitted_=received_;}ended_=true;}
        catch(...){failed_=true;throw;}
    }
};
}
