#include "enrollment.h"
#include "../native/vendor/picosha2/picosha2.h"
#include <algorithm>
#include <cmath>
#include <set>
#include <stdexcept>

namespace aii::uid {
namespace {
void require(bool ok,const char* reason) {
  if(!ok)throw std::invalid_argument(reason);
}
void valid_id(const std::string& id) {
  const std::string alnum="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
  require(!id.empty()&&id.size()<=96&&alnum.find(id[0])!=std::string::npos&&
      id.find_first_not_of(alnum+"_.-")==std::string::npos,"invalid speaker ID");
}
void advance(Snapshot& value) {
  require(value.revision<INT64_MAX,"enrollment revision exhausted");
  ++value.revision;
}
PreparedEnrollment finish(const std::string& base,Snapshot value,const PolicyDocument& policy) {
  // Validation includes global evidence uniqueness, per-speaker/count bounds,
  // Unicode labels, unit vectors and a usable centroid. The full round trip
  // also holds the existing canonical codec's byte and structure bounds.
  auto bytes=write_snapshot(value,policy);
  (void)read_snapshot(bytes,policy);
  return {picosha2::hash256_hex_string(base),std::move(bytes),value.revision};
}
}
PreparedEnrollment prepare_enrollment(const std::string& current,const PolicyDocument& policy,
    const std::string& speaker_id,const std::string& label,const std::vector<Sample>& recordings,
    const std::string& embedding_binding) {
  auto next=read_snapshot(current,policy); // failure is NEVER empty enrollment
  valid_id(speaker_id);
  require(embedding_binding==policy.policy.embedding_binding,"embedding model/frontend binding differs");
  require(!recordings.empty()&&recordings.size()<=8,"bounded enrollment recordings required");
  auto selected=std::lower_bound(next.speakers.begin(),next.speakers.end(),speaker_id,
      [](const Speaker& s,const std::string& id){return s.id<id;});
  if(selected!=next.speakers.end()&&selected->id==speaker_id)
    require(selected->label==label,"existing speaker label differs; no silent identity replacement");
  else {
    require(next.speakers.size()<256,"speaker limit reached");
    selected=next.speakers.insert(selected,Speaker{speaker_id,label,{}});
  }
  require(selected->samples.size()+recordings.size()<=8,"speaker enrollment sample limit reached");
  std::set<std::string> hashes;
  for(const auto& s:next.speakers)for(const auto& sample:s.samples)hashes.insert(sample.audio_sha256);
  for(auto sample:recordings) {
    require(hashes.insert(sample.audio_sha256).second,"duplicate enrollment evidence refused");
    double norm=0;
    for(double x:sample.embedding){require(std::isfinite(x),"nonfinite enrollment embedding");norm+=x*x;}
    require(std::isfinite(norm)&&norm>=1e-24,"invalid enrollment embedding norm");
    norm=std::sqrt(norm);
    for(auto& x:sample.embedding)x/=norm;
    selected->samples.push_back(std::move(sample));
  }
  std::sort(selected->samples.begin(),selected->samples.end(),
      [](const Sample& a,const Sample& b){return a.audio_sha256<b.audio_sha256;});
  advance(next); // exactly once, after the entire selected batch is prepared
  return finish(current,std::move(next),policy);
}
PreparedEnrollment prepare_removal(const std::string& current,const PolicyDocument& policy,
    const std::string& speaker_id) {
  auto next=read_snapshot(current,policy);valid_id(speaker_id);
  auto selected=std::find_if(next.speakers.begin(),next.speakers.end(),
      [&](const Speaker& s){return s.id==speaker_id;});
  if(selected!=next.speakers.end()){next.speakers.erase(selected);advance(next);}
  return finish(current,std::move(next),policy);
}
PreparedEnrollment prepare_reset(const std::string& current,const PolicyDocument& policy) {
  auto next=read_snapshot(current,policy);next.speakers.clear();advance(next);
  return finish(current,std::move(next),policy);
}
PreparedEnrollment prepare_guided_policy_transition(const std::string& current,
    const PolicyDocument& previous,const PolicyDocument& guided) {
  // Re-parse the bound documents so a mismatched in-memory PolicyDocument
  // cannot supply one policy for checks and another for serialized output.
  const auto from=read_policy(previous.canonical),to=read_policy(guided.canonical);
  auto next=read_snapshot(current,from);
  require(from.policy.minimum_enrollment_samples==3&&to.policy.minimum_enrollment_samples==1,
      "only the three-recording to guided-recording policy transition is supported");
  require(from.policy.embedding_binding==to.policy.embedding_binding&&
      from.policy.threshold==to.policy.threshold&&from.policy.minimum_margin==to.policy.minimum_margin,
      "guided enrollment transition must preserve model and matching thresholds");
  require(from.policy.calibration_sha256!=to.policy.calibration_sha256,
      "guided enrollment requires a separately bound calibration");
  for(const auto& speaker:next.speakers)
    if(speaker.samples.size()<from.policy.minimum_enrollment_samples)
      throw std::invalid_argument("incomplete existing enrollment requires operator review: "+speaker.id);
  next.policy=to.policy;advance(next);
  return finish(current,std::move(next),to);
}
}
