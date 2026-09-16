#include "identity.h"
#include <algorithm>
#include <cmath>
#include <set>
#include <stdexcept>

namespace aii::uid {
namespace {
void require(bool ok,const char* why) { if(!ok) throw std::invalid_argument(why); }
bool sha(const std::string& s) {
  return s.size()==64 && s.find_first_not_of("0123456789abcdef")==std::string::npos;
}
double norm(const Vector& v) {
  double sum=0;
  for(const auto x:v) { require(std::isfinite(x),"nonfinite UID embedding"); sum+=x*x; }
  require(std::isfinite(sum) && sum>=1e-24,"invalid UID embedding norm");
  return std::sqrt(sum);
}
Vector unit(Vector v) { const auto n=norm(v); for(auto& x:v)x/=n; return v; }
bool label(const std::string& s) {
  try {
    const auto points=unicode_scalars(s);
    return !points.empty()&&points.size()<=128&&std::all_of(points.begin(),points.end(),[](uint32_t cp){return cp>=32;});
  }catch(const std::invalid_argument&){return false;}
}
void policy(const Policy& p) {
  require(sha(p.embedding_binding)&&sha(p.calibration_sha256)&&sha(p.fingerprint),"UID policy binding missing");
  require(std::isfinite(p.threshold)&&p.threshold>=-1&&p.threshold<=1,"invalid UID threshold");
  require(std::isfinite(p.minimum_margin)&&p.minimum_margin>=0&&p.minimum_margin<=2,"invalid UID margin");
  require(p.minimum_enrollment_samples>=1&&p.minimum_enrollment_samples<=8,"invalid enrollment minimum");
}
Vector centroid(const Speaker& s) {
  Vector v{};
  for(const auto& sample:s.samples) for(size_t i=0;i<v.size();++i)v[i]+=sample.embedding[i];
  // Match the established mean-then-normalize decision path, including its
  // double precision; samples themselves are never normalized on read.
  for(auto& x:v)x/=s.samples.size();
  return unit(v);
}
}
std::vector<uint32_t> unicode_scalars(const std::string& s) {
  std::vector<uint32_t> points;
  for(size_t i=0;i<s.size();) {
    const auto b=static_cast<unsigned char>(s[i++]);
    uint32_t cp=b; unsigned tail=0; uint32_t low=0;
    if(b>=0xc2 && b<=0xdf) {cp=b&31;tail=1;low=0x80;}
    else if(b>=0xe0 && b<=0xef) {cp=b&15;tail=2;low=0x800;}
    else if(b>=0xf0 && b<=0xf4) {cp=b&7;tail=3;low=0x10000;}
    else if(b>=0x80)throw std::invalid_argument("invalid UTF-8");
    for(unsigned j=0;j<tail;++j) {
      if(i==s.size())throw std::invalid_argument("truncated UTF-8");
      const auto c=static_cast<unsigned char>(s[i++]);
      if((c&0xc0)!=0x80)throw std::invalid_argument("invalid UTF-8 continuation");
      cp=(cp<<6)|(c&63);
    }
    if(cp<low || cp>0x10ffff || (cp>=0xd800 && cp<=0xdfff))throw std::invalid_argument("invalid Unicode scalar");
    points.push_back(cp);
  }
  return points;
}
void validate(const Snapshot& s,const Policy& expected) {
  policy(expected);policy(s.policy);
  const auto& p=s.policy;
  require(p.embedding_binding==expected.embedding_binding&&p.calibration_sha256==expected.calibration_sha256&&
          p.fingerprint==expected.fingerprint&&p.threshold==expected.threshold&&p.minimum_margin==expected.minimum_margin&&
          p.minimum_enrollment_samples==expected.minimum_enrollment_samples,"enrollment policy/model binding differs");
  require(s.revision<=INT64_MAX && s.speakers.size()<=256,"invalid enrollment revision/count");
  require(s.speakers.empty()||s.revision>0,"enrollment requires positive revision");
  std::set<std::string> audio;
  std::string previous;
  for(const auto& speaker:s.speakers) {
    const auto& id=speaker.id;
    const std::string alnum="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
    require(!id.empty()&&id.size()<=96&&alnum.find(id[0])!=std::string::npos&&
            id.find_first_not_of(alnum+"_.-")==std::string::npos&&id>previous,"invalid/unsorted speaker ID");
    previous=id;
    require(label(speaker.label),"invalid enrollment label");
    require(!speaker.samples.empty()&&speaker.samples.size()<=8,"invalid enrollment sample count");
    std::string last;
    for(const auto& sample:speaker.samples) {
      require(sha(sample.audio_sha256)&&sample.audio_sha256>last&&audio.insert(sample.audio_sha256).second,
              "invalid/duplicate/unsorted enrollment audio");
      last=sample.audio_sha256;
      require(std::abs(norm(sample.embedding)-1)<=1e-6,"invalid enrollment unit vector");
    }
    (void)centroid(speaker);
  }
}
Decision identify(const Snapshot& s,const Policy& expected,const Vector& embedding,const std::string& binding) {
  validate(s,expected);
  require(binding==expected.embedding_binding,"embedding model/frontend binding differs");
  const auto query=unit(embedding);
  Decision result{"unknown","","","no_enrollments",std::nullopt,std::nullopt,s.revision,expected.fingerprint};
  if(s.speakers.empty())return result;
  std::vector<std::pair<double,size_t>> scores;
  for(size_t j=0;j<s.speakers.size();++j) {
    const auto center=centroid(s.speakers[j]);double dot=0;
    for(size_t i=0;i<center.size();++i)dot+=query[i]*center[i];
    scores.emplace_back(std::clamp(dot,-1.,1.),j);
  }
  std::sort(scores.begin(),scores.end(),[](const auto& a,const auto& b){
    return a.first==b.first?a.second<b.second:a.first>b.first;
  });
  const auto& winner=s.speakers[scores[0].second];result.score=scores[0].first;
  if(scores.size()>1)result.margin=scores[0].first-scores[1].first;
  if(winner.samples.size()<expected.minimum_enrollment_samples)result.reason="insufficient_enrollment";
  else if(*result.score<expected.threshold)result.reason="below_acceptance_threshold";
  else if(result.margin&&(*result.margin<=0||*result.margin<expected.minimum_margin)) {
    result.outcome="ambiguous";result.reason="insufficient_separation";
  } else {
    result.outcome="known";result.reason="accepted";result.speaker_id=winner.id;result.label=winner.label;
  }
  return result;
}
}
