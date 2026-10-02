#pragma once
#include <algorithm>
#include <cmath>
#include <stdexcept>
#include <vector>

namespace aii::multitalker {
// Matches NeMo SortformerModules.downsample_preds: mean of disjoint eight
// frame windows, partial tail excludes padding. UID uses unpooled activity.
inline std::vector<float> nemotron_targets(const std::vector<float>& fine,size_t frames) {
  if(fine.size()%8 || !frames || frames>128 || fine.size()>frames*8*8)
    throw std::invalid_argument("Nemotron target extent");
  for(float p:fine)if(!std::isfinite(p) || p<0 || p>1)
    throw std::invalid_argument("Nemotron target probability");
  std::vector<float> targets(frames*8,0);
  const auto available=fine.size()/8;
  for(size_t f=0;f<frames;++f) {
    const auto first=f*8,end=std::min(first+8,available);
    if(first>=end)continue;
    for(size_t t=0;t<8;++t) {
      float sum=0;for(size_t i=first;i<end;++i)sum+=fine[i*8+t];
      targets[f*8+t]=sum/float(end-first);
    }
  }
  return targets;
}
}
