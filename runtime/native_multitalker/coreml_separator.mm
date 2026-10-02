#include "coreml_separator.h"
#import <CoreML/CoreML.h>
#import <Foundation/Foundation.h>
#include <cmath>
#include <stdexcept>

namespace aii::multitalker {
namespace {
void require(bool ok,NSError* error,const char* reason) {
  if(!ok)throw std::runtime_error(error?error.localizedDescription.UTF8String:reason);
}
void array_shape(MLMultiArray* array,NSArray<NSNumber*>* shape) {
  require(array&&array.dataType==MLMultiArrayDataTypeFloat32&&[array.shape isEqualToArray:shape],nil,
          "separator tensor signature");
}
}
struct CoreMLSeparator::Impl {
  NSMutableArray<MLModel*>* models;
  std::atomic<bool> cancelled{false},running{false};
  bool cpu=false;
  Impl(const std::string& root,bool cpu_only) {@autoreleasepool {
    cpu=cpu_only;
    require(!root.empty()&&root.find('\0')==std::string::npos,nil,"separator model directory");
    NSString* base=[[NSString alloc] initWithBytes:root.data() length:root.size() encoding:NSUTF8StringEncoding];
    require(base!=nil,nil,"separator directory encoding");models=[NSMutableArray new];
    for(unsigned i=0;i<8;++i) {
      NSString* path=[base stringByAppendingPathComponent:[NSString stringWithFormat:@"stage-%u.mlmodelc",i]];
      MLModelConfiguration* config=[MLModelConfiguration new];
      config.computeUnits=cpu_only?MLComputeUnitsCPUOnly:MLComputeUnitsCPUAndGPU;
      NSError* error=nil;MLModel* model=[MLModel modelWithContentsOfURL:[NSURL fileURLWithPath:path] configuration:config error:&error];
      require(model!=nil,error,"separator model load");
      NSSet* inputs=[NSSet setWithArray:model.modelDescription.inputDescriptionsByName.allKeys];
      NSSet* outputs=[NSSet setWithArray:model.modelDescription.outputDescriptionsByName.allKeys];
      require([inputs isEqualToSet:[NSSet setWithArray:i?@[@"pcm",@"state_in"]:@[@"pcm"]]]&&
              [outputs isEqualToSet:[NSSet setWithObject:i==7?@"sources":@"state_out"]],nil,
              "separator stage signature");
      for(MLFeatureDescription* desc in model.modelDescription.inputDescriptionsByName.allValues)
        require(desc.type==MLFeatureTypeMultiArray&&desc.multiArrayConstraint.dataType==MLMultiArrayDataTypeFloat32,nil,
                "separator stage input type");
      for(MLFeatureDescription* desc in model.modelDescription.outputDescriptionsByName.allValues)
        require(desc.type==MLFeatureTypeMultiArray&&desc.multiArrayConstraint.dataType==MLMultiArrayDataTypeFloat32,nil,
                "separator stage output type");
      [models addObject:model];
    }
  }}
  void alive() const {if(cancelled.load())throw aii::voice::Cancelled("separator cancelled");}
};
CoreMLSeparator::CoreMLSeparator(const std::string& root,bool cpu):p_(std::make_unique<Impl>(root,cpu)){}
CoreMLSeparator::~CoreMLSeparator()=default;
std::string CoreMLSeparator::provider() const {return p_->cpu?"CoreMLCPUOnly":"CoreMLCPUAndGPU";}
void CoreMLSeparator::open() {
  if(p_->running.load())throw std::runtime_error("separator inference has not retired");
  p_->cancelled.store(false);
}
void CoreMLSeparator::cancel() noexcept {p_->cancelled.store(true);}
Waveforms CoreMLSeparator::separate(const std::vector<float>& pcm) {@autoreleasepool {
  if(pcm.size()<32000||pcm.size()>80003)throw std::invalid_argument("separator input extent");
  for(float value:pcm)if(!std::isfinite(value)||std::abs(value)>1)throw std::invalid_argument("separator input PCM");
  if(p_->running.exchange(true))throw std::runtime_error("separator concurrent inference");
  struct Retire {std::atomic<bool>& running;~Retire(){running.store(false);}} retire{p_->running};
  p_->alive();NSError* error=nil;const NSInteger n=static_cast<NSInteger>(pcm.size());
  MLMultiArray* input=[[MLMultiArray alloc] initWithDataPointer:const_cast<float*>(pcm.data()) shape:@[@1,@(n)]
    dataType:MLMultiArrayDataTypeFloat32 strides:@[@(n),@1] deallocator:nil error:&error];
  require(input!=nil,error,"separator input allocation");
  MLMultiArray* state=nil;const NSInteger frames=(n-16)/8+1;
  for(NSUInteger i=0;i<p_->models.count;++i) {
    p_->alive();NSMutableDictionary* fields=[NSMutableDictionary dictionaryWithObject:[MLFeatureValue featureValueWithMultiArray:input] forKey:@"pcm"];
    if(i)fields[@"state_in"]=[MLFeatureValue featureValueWithMultiArray:state];
    MLDictionaryFeatureProvider* features=[[MLDictionaryFeatureProvider alloc] initWithDictionary:fields error:&error];
    require(features!=nil,error,"separator features");p_->alive();
    id<MLFeatureProvider> prediction=[p_->models[i] predictionFromFeatures:features error:&error];
    p_->alive();require(prediction!=nil,error,"separator prediction");
    state=[prediction featureValueForName:i==7?@"sources":@"state_out"].multiArrayValue;
    array_shape(state,i==7?@[@1,@2,@(n)]:@[@1,@(frames),@512]);
  }
  Waveforms raw{{std::vector<float>(pcm.size()),std::vector<float>(pcm.size())}};
  __block bool valid=true;
  // Core ML arrays need not be contiguous. Keep the returned owner alive and
  // use its strides instead of copying a borrowed Python/NumPy view.
  auto* destination=&raw;
  [state getBytesWithHandler:^(const void* bytes,NSInteger size) {
    const auto* data=static_cast<const float*>(bytes);
    const size_t s1=state.strides[1].unsignedLongLongValue,s2=state.strides[2].unsignedLongLongValue;
    const size_t available=size>0?static_cast<size_t>(size)/sizeof(float):0;
    if(!available||s1>=available||s2>(available-1-s1)/(pcm.size()-1)){valid=false;return;}
    for(size_t c=0;c<2;++c)for(size_t i=0;i<pcm.size();++i) {
      float value=data[c*s1+i*s2];valid&=std::isfinite(value);(*destination)[c][i]=value;
    }
  }];
  require(valid,nil,"separator output extent or nonfinite values");p_->alive();
  auto normalized=normalize_sources(pcm,std::move(raw));p_->alive();return normalized;
}}
}
