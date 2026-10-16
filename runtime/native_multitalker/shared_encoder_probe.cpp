#include "onnx_backend.h"
#include <cmath>
#include <iostream>
#include <stdexcept>

namespace {
void check(bool ok,const char* why){if(!ok)throw std::runtime_error(why);}
}
// Bound real-model probe, intentionally absent from model-free source tests.
// Compare alternating independent contexts to an unshared reference session.
int main(int argc,char** argv){try{
  using aii::multitalker::EncoderExecution;
  using aii::multitalker::OnnxEncoder;
  check(argc==2,"shared encoder probe requires the bound graph root");
  EncoderExecution execution;
  auto weights=OnnxEncoder::load_weights(argv[1],execution);
  std::weak_ptr<aii::multitalker::EncoderWeights> lifetime=weights;
  execution.weights=weights;
  const size_t frames=32;
  std::vector<float> a(frames*aii::multitalker::encoder_width),b(a.size());
  std::vector<float> foreground(frames,1),background(frames,0);
  for(size_t i=0;i<a.size();++i){
    a[i]=static_cast<float>(std::sin(static_cast<double>(i)*.013)*.25);
    b[i]=static_cast<float>(std::cos(static_cast<double>(i)*.017)*.2);
  }
  auto push=[&](OnnxEncoder& encoder,uint64_t epoch,const std::vector<float>& values){
    return encoder.push(epoch,0,values.data(),frames,frames,
        foreground.data(),background.data(),false);
  };
  {
    OnnxEncoder first(argv[1],execution),second(argv[1],execution),reference(argv[1]);
    check(weights.use_count()==4,"two contexts did not retain the explicit weight owner");
    first.reset(1);second.reset(1);reference.reset(1);
    const auto a1=push(reference,1,a),a2=push(reference,1,b);
    check(push(first,1,a)==a1,"shared first context differs from unshared output");
    reference.reset(2);
    const auto b1=push(reference,2,b),b2=push(reference,2,a);
    check(push(second,1,b)==b1,"shared second context inherited the first cache");
    check(push(first,1,b)==a2,"second context changed the first recurrent cache");
    first.cancel();
    bool cancelled=false;
    try{push(first,1,a);}catch(const std::runtime_error&){cancelled=true;}
    check(cancelled,"cancelled context kept inferring");
    check(push(second,1,a)==b2,"one context's cancellation reached its sibling");
    first.reset(2);reference.reset(3);
    check(push(first,2,a)==push(reference,3,a),"shared context did not recover after reset");
    for(int change=0;change<4;++change){
      auto changed=execution;std::string root=argv[1];
      if(change==0)root+="-different";
      if(change==1)changed.threads=3;
      if(change==2)changed.cuda_device=0;
      if(change==3)changed.profile_prefix="different-profile";
      bool refused=false;
      try{OnnxEncoder invalid(root,changed);}
      catch(const std::invalid_argument& e){refused=std::string(e.what())=="shared encoder model binding differs";}
      check(refused,"shared owner accepted a different model or execution binding");
    }
    weights.reset();execution.weights.reset();
    check(!lifetime.expired(),"weight owner ended while recognizers remained alive");
  }
  check(lifetime.expired(),"weight owner leaked into a process-global cache");
  std::cout<<"{\"passed\":true,\"contexts\":2,\"binding_refusals\":4,"
      "\"recurrent_state_isolated\":true,\"cancellation_isolated\":true,"
      "\"reset_recovered\":true,\"weights_retired\":true}\n";
  return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
