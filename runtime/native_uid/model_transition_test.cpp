#include "enrollment.h"
#include "speaker_registry.h"
#include <iostream>
#include <stdexcept>
using namespace aii::uid;
void check(bool x){if(!x)throw std::runtime_error("model transition contract failed");}
template<class F>void refuses(F f,const char* reason){
  try{f();}catch(const std::exception& e){check(std::string(e.what()).find(reason)!=std::string::npos);return;}
  throw std::runtime_error("required refusal missing");
}
PolicyDocument policy(const std::string& binding){return read_policy(
  "{\"embedding_binding\":\""+binding+"\",\"calibration_sha256\":\""+std::string(64,'a')+
  "\",\"minimum_enrollment_samples\":1,\"threshold\":0.4,\"minimum_margin\":0.14}");}
Sample sample(char digest,size_t dimensions,size_t axis=0){Vector v(dimensions);v.at(axis)=1;return {std::string(64,digest),v};}
int main(){try{
  const auto old=policy(std::string(64,'b')),next=policy(ecapa_binding);
  const auto before=write_snapshot({old.policy,7,{{"s1","Speaker One",{sample('1',256),sample('2',256)}}}},old);
  const auto changed=prepare_reembedding(before,old,next,{sample('1',192),sample('2',192)});
  const auto restored=read_snapshot(changed.snapshot,next);
  check(restored.revision==8&&restored.speakers[0].id=="s1"&&restored.speakers[0].label=="Speaker One");
  check(restored.speakers[0].samples[0].embedding.size()==192);
  check(identify(restored,next.policy,sample('3',192).embedding,ecapa_binding).speaker_id=="s1");
  refuses([&]{read_snapshot(before,next);},"snapshot policy binding");refuses([&]{read_snapshot(changed.snapshot,old);},"snapshot policy binding");
  refuses([&]{identify(restored,next.policy,Vector(256,0),ecapa_binding);},"embedding dimension/model");
  refuses([&]{identify(restored,next.policy,{},ecapa_binding);},"embedding dimension/model");
  refuses([&]{prepare_reembedding(before,old,next,{sample('1',192)});},"original recording unavailable");
  refuses([&]{prepare_reembedding(before,old,next,{sample('1',192),sample('1',192)});},"duplicate reembedding evidence");
  refuses([&]{prepare_reembedding(before,old,next,{sample('1',192),sample('2',192),sample('3',192)});},"unrelated recording");
  refuses([&]{prepare_reembedding(before,old,next,{sample('1',256),sample('2',256)});},"embedding dimension/model");
  check(read_snapshot(before,old).revision==7); // original is unchanged
  check(decode_vector(encode_vector(sample('1',192).embedding),192)==sample('1',192).embedding);
  refuses([&]{decode_vector(encode_vector(sample('1',192).embedding));},"snapshot embedding extent");
  const std::string uuid="00000000-0000-4000-8000-000000000001";
  auto empty=write_registry({0,{old.policy,0,{}},{}},old);
  auto admitted=observe_speaker(empty,old,0,uuid,sample('1',256),ProfileAdmission::Corroborated,sample('2',256));
  auto labelled=associate_speaker(admitted.document,old,1,uuid,"Speaker One","colleague");
  auto moved=prepare_registry_reembedding(labelled.document,old,next,2,{sample('1',192),sample('2',192)});
  auto registry=read_registry(moved.document,next);
  check(registry.revision==3&&registry.buckets.size()==1&&registry.buckets[0].uuid==uuid);
  check(registry.buckets[0].associations[0].label=="Speaker One"&&registry.buckets[0].associations[0].external_id=="colleague");
  // Read/identify from reconstructed bytes, as a fresh process does. Session
  // churn does not mint, merge, adapt or consume a new durable speaker UUID.
  for(int i=0;i<20;++i){
    auto found=observe_speaker(moved.document,next,3,"00000000-0000-4000-8000-000000000002",sample('3',192));
    check(found.uuid==uuid&&found.document==moved.document);
  }
  auto unknown=observe_speaker(moved.document,next,3,"00000000-0000-4000-8000-000000000002",sample('4',192,1));
  check(unknown.uuid.empty()&&unknown.document==moved.document);
  refuses([&]{prepare_registry_reembedding(labelled.document,old,next,1,{sample('1',192),sample('2',192)});},"stale speaker registry revision");
  refuses([&]{read_registry(moved.document,old);},"snapshot policy binding");
  // The same recordings may back an anonymous profile and its later named
  // enrollment. One regenerated evidence set preserves that shared identity.
  const auto paired_enrollment=write_snapshot({old.policy,7,{{"s1","Speaker One",
      {sample('1',256),sample('2',256)}}}},old);
  const auto bound=bind_enrolled_speaker(labelled.document,old,2,uuid,
      read_snapshot(paired_enrollment,old).speakers[0],uuid);
  const auto pair=prepare_speaker_model_transition(paired_enrollment,bound.document,old,next,
      7,bound.revision,{sample('1',192),sample('2',192)});
  const auto pair_registry=read_registry(pair.registry.document,next);
  const auto pair_enrolled=read_snapshot(pair.enrollment.snapshot,next);
  check(pair_registry.buckets[0].uuid==uuid&&pair_registry.buckets[0].associations[0].label=="Speaker One");
  check(bound_enrollment(pair_registry.buckets[0],pair_enrolled)->id=="s1");
  check(pair.enrollment.base_sha256==changed.base_sha256);
  refuses([&]{prepare_speaker_model_transition(paired_enrollment,bound.document,old,next,
      6,bound.revision,{sample('1',192),sample('2',192)});},"stale enrollment revision");
  refuses([&]{prepare_speaker_model_transition(paired_enrollment,bound.document,old,next,
      7,bound.revision-1,{sample('1',192),sample('2',192)});},"stale speaker registry revision");
  refuses([&]{prepare_speaker_model_transition(paired_enrollment,bound.document,old,next,
      7,bound.revision,{sample('1',192)});},"original recording unavailable");
  refuses([&]{prepare_speaker_model_transition(paired_enrollment,bound.document,old,next,
      7,bound.revision,{sample('1',192),sample('2',192),sample('3',192)});},"unrelated recording");
  refuses([&]{prepare_speaker_model_transition(paired_enrollment,bound.document,old,next,
      7,bound.revision,{sample('1',192),sample('1',192)});},"duplicate reembedding evidence");
  refuses([&]{prepare_speaker_model_transition(paired_enrollment,bound.document,old,next,
      7,bound.revision,{sample('1',256),sample('2',256)});},"embedding dimension/model");
  check(read_registry(bound.document,old).revision==bound.revision);
  std::cout<<"MODEL_SPACE_TRANSITION_PASS\n";
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
