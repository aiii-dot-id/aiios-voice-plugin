#pragma once
#include "speaker_registry.h"
#include <map>

namespace aii::uid {
using HeardDays=std::map<std::string,uint64_t>;
HeardDays read_heard(const std::string&);
std::string write_heard(const HeardDays&);

struct ExpiryPass {
  RegistryChange registry;
  HeardDays heard;
  std::vector<std::string> expired;
};
ExpiryPass expire_unenrolled(const std::string& registry,const PolicyDocument&,const HeardDays&,
    uint64_t today,uint32_t expiry_days,const Snapshot* enrolled=nullptr);
HeardDays heard_speaker(const SpeakerRegistry&,HeardDays,const std::string& uuid,uint64_t today,
    const Snapshot* enrolled=nullptr);
std::optional<uint64_t> expiry_day(const SpeakerRegistry&,const SpeakerBucket&,const HeardDays&,
    uint32_t expiry_days,const Snapshot* enrolled=nullptr);
}
