#include "diar_cache.h"
#include <algorithm>
#include <cmath>
#include <limits>
#include <numeric>
#include <stdexcept>

namespace aii::multitalker {
namespace {
void extent(const std::vector<float>& chunk) {
  if(chunk.empty() || chunk.size()%DiarCache::width || chunk.size()>128*DiarCache::width)
    throw std::invalid_argument("diarization chunk extent");
  for(float x:chunk)if(!std::isfinite(x))throw std::invalid_argument("nonfinite diarization chunk");
}
std::vector<size_t> top(const std::vector<float>& scores,size_t k) {
  if(k>scores.size())throw std::invalid_argument("cache selection extent");
  std::vector<size_t> order(scores.size());std::iota(order.begin(),order.end(),0);
  std::partial_sort(order.begin(),order.begin()+k,order.end(),[&](size_t a,size_t b){
    return scores[a]!=scores[b] ? scores[a]>scores[b] : a<b;
  });
  order.resize(k);return order;
}
}
std::vector<float> DiarCache::input(const std::vector<float>& chunk) const {
  extent(chunk);
  auto result=cache_;result.insert(result.end(),fifo_.begin(),fifo_.end());
  result.insert(result.end(),chunk.begin(),chunk.end());return result;
}
std::vector<float> DiarCache::update(const std::vector<float>& chunk,
                                    const std::vector<float>& predictions) {
  extent(chunk);
  const size_t c=cache_.size()/width, f=fifo_.size()/width, n=chunk.size()/width;
  if(predictions.size()!=(c+f+n)*speakers)throw std::invalid_argument("diarization probability extent");
  for(float x:predictions)if(!std::isfinite(x)||x<0||x>1)
    throw std::invalid_argument("invalid diarization probability");
  std::vector<float> current(predictions.begin()+(c+f)*speakers,predictions.end());
  fifo_.insert(fifo_.end(),chunk.begin(),chunk.end());
  if(f+n>fifo_limit) {
    const size_t pop=std::min(f+n,std::max(size_t(144),f+n-fifo_limit));
    const auto begin=predictions.begin()+c*speakers;
    std::array<float,width> sum{};size_t count=0;
    for(size_t i=0;i<pop;++i) {
      float activity=0;for(size_t s=0;s<speakers;++s)activity+=begin[i*speakers+s];
      if(activity<.2f){++count;for(size_t d=0;d<width;++d)sum[d]+=fifo_[i*width+d];}
    }
    if(count) {
      for(size_t d=0;d<width;++d)
        silence_[d]=float((silence_[d]*silent_frames_+sum[d])/(silent_frames_+count));
      silent_frames_+=count;
    }
    cache_.insert(cache_.end(),fifo_.begin(),fifo_.begin()+pop*width);
    if(!compressed_)cache_probs_.assign(predictions.begin(),predictions.begin()+c*speakers);
    cache_probs_.insert(cache_probs_.end(),begin,begin+pop*speakers);
    fifo_.erase(fifo_.begin(),fifo_.begin()+pop*width);
    if(cache_.size()/width>cache_limit)compress();
  }
  if(frames()>cache_limit+fifo_limit)throw std::runtime_error("diarization cache bound");
  return current;
}
void DiarCache::compress() {
  const size_t n=cache_.size()/width, padded=n+3;
  if(n<=cache_limit || cache_probs_.size()!=n*speakers)throw std::runtime_error("cache geometry");
  const float neg=-std::numeric_limits<float>::infinity();
  std::array<std::vector<float>,speakers> scores;
  for(auto& s:scores)s.resize(n);
  for(size_t i=0;i<n;++i) {
    std::array<float,speakers> off{};float sum=0;
    for(size_t s=0;s<speakers;++s){off[s]=std::log(std::max(1-cache_probs_[i*speakers+s],.25f));sum+=off[s];}
    for(size_t s=0;s<speakers;++s) {
      const float p=cache_probs_[i*speakers+s];
      scores[s][i]=p>.5f ? std::log(std::max(p,.25f))-off[s]+sum-float(std::log(.5)) : neg;
    }
  }
  for(auto& score:scores) {
    const auto positive=std::count_if(score.begin(),score.end(),[](float x){return x>0;});
    if(positive>=22)for(auto& x:score)if(x<=0)x=neg;
    for(size_t i=cache_limit;i<n;++i)score[i]+=.05f;
    for(auto i:top(score,33))score[i]-=float(2*std::log(.5));
    for(auto i:top(score,66))score[i]-=float(std::log(.5));
    score.resize(padded,std::numeric_limits<float>::infinity());
  }
  std::vector<float> flat;flat.reserve(padded*speakers);
  for(const auto& score:scores)flat.insert(flat.end(),score.begin(),score.end());
  auto indices=top(flat,cache_limit);
  for(auto& i:indices)if(flat[i]==neg)i=99999;
  std::sort(indices.begin(),indices.end());
  std::vector<float> cache(cache_limit*width),probs(cache_limit*speakers,0);
  for(size_t row=0;row<indices.size();++row) {
    const size_t index=indices[row]%padded;
    const bool disabled=indices[row]==99999 || index>=n;
    std::copy_n(disabled?silence_.data():cache_.data()+index*width,width,cache.data()+row*width);
    if(!disabled)std::copy_n(cache_probs_.data()+index*speakers,speakers,probs.data()+row*speakers);
  }
  cache_=std::move(cache);cache_probs_=std::move(probs);compressed_=true;++compressions_;
}
}
