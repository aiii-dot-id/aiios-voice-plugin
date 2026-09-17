#pragma once
#include "net.h"
#include "ncnn_memory.h"
#include "../portability/android/uid_unbiased_variance.h"
#include <array>
#include <atomic>
#include <functional>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>

namespace uid_detail {
struct NcnnCancelled:std::runtime_error {
  NcnnCancelled():std::runtime_error("UID utterance cancelled at native dispatch fence") {}
};
struct DispatchFence {
  std::function<bool()> stopped;
  bool refused=false; // embedding worker only; control changes the owner's atomic fence
  bool admit() { if(stopped && stopped()) {refused=true;return false;} return true; }
};
inline constexpr int cancelled_dispatch=-911;

// Delegate the pinned implementation unchanged. A stopped request cannot enqueue
// the next heavy operation. This does not pretend to preempt a submitted kernel.
class FencedLayer:public ncnn::Layer {
  std::unique_ptr<ncnn::Layer> inner;
  DispatchFence* fence;
  void flags() {
    one_blob_only=inner->one_blob_only;support_inplace=inner->support_inplace;
    support_vulkan=inner->support_vulkan;support_packing=inner->support_packing;
    support_bf16_storage=inner->support_bf16_storage;
    support_fp16_storage=inner->support_fp16_storage;support_int8_storage=inner->support_int8_storage;
    support_tensor_storage=inner->support_tensor_storage;support_vulkan_packing=inner->support_vulkan_packing;
    support_any_packing=inner->support_any_packing;support_vulkan_any_packing=inner->support_vulkan_any_packing;
    featmask=inner->featmask;
  }
public:
  FencedLayer(const char* kind,bool gpu,DispatchFence* f):
      inner(gpu?ncnn::create_layer_vulkan(kind):ncnn::create_layer_cpu(kind)),fence(f) {
    if(!inner)throw std::runtime_error("bound native UID layer unavailable");
    flags();
  }
  int load_param(const ncnn::ParamDict& p) override {inner->vkdev=vkdev;int r=inner->load_param(p);flags();return r;}
  int load_model(const ncnn::ModelBin& m) override {inner->vkdev=vkdev;return inner->load_model(m);}
  int create_pipeline(const ncnn::Option& o) override {
    inner->vkdev=vkdev;inner->bottom_shapes=bottom_shapes;inner->top_shapes=top_shapes;
    int r=inner->create_pipeline(o);flags();return r;
  }
  int destroy_pipeline(const ncnn::Option& o) override {return inner->destroy_pipeline(o);}
  int upload_model(ncnn::VkTransfer& c,const ncnn::Option& o) override {return inner->upload_model(c,o);}
  int forward(const ncnn::Mat& b,ncnn::Mat& t,const ncnn::Option& o) const override {
    return fence->admit()?inner->forward(b,t,o):cancelled_dispatch;
  }
  int forward(const std::vector<ncnn::Mat>& b,std::vector<ncnn::Mat>& t,const ncnn::Option& o) const override {
    return fence->admit()?inner->forward(b,t,o):cancelled_dispatch;
  }
  int forward(const ncnn::VkMat& b,ncnn::VkMat& t,ncnn::VkCompute& c,const ncnn::Option& o) const override {
    return fence->admit()?inner->forward(b,t,c,o):cancelled_dispatch;
  }
  int forward(const std::vector<ncnn::VkMat>& b,std::vector<ncnn::VkMat>& t,ncnn::VkCompute& c,const ncnn::Option& o) const override {
    return fence->admit()?inner->forward(b,t,c,o):cancelled_dispatch;
  }
};

class NcnnBackend {
  std::vector<unsigned char> weights;
  std::string graph;
  DispatchFence fence;
  struct Factory {DispatchFence* fence;bool gpu;const char* type;};
  Factory conv,linear;
  BoundedAllocator allocator;
  std::unique_ptr<BoundedVkAllocator> gpu_allocator;
  ncnn::Net net; // destroyed before the buffers, factories and fence
  static ncnn::Layer* make(void* p) noexcept {
    try {auto* f=static_cast<Factory*>(p);return new FencedLayer(f->type,f->gpu,f->fence);}
    catch(...) {return nullptr;} // report through ncnn's creator-refusal path
  }
public:
  NcnnBackend(const void* param,size_t param_size,const void* data,size_t data_size,const std::string& backend):
      weights(static_cast<const unsigned char*>(data),static_cast<const unsigned char*>(data)+data_size),
      graph(static_cast<const char*>(param),param_size),
      conv{&fence,backend=="ncnn-vulkan","Convolution"},linear{&fence,backend=="ncnn-vulkan","InnerProduct"} {
    if(backend!="ncnn-cpu"&&backend!="ncnn-vulkan")throw std::invalid_argument("explicit native UID backend required");
    if(graph.find('\0')!=std::string::npos)throw std::invalid_argument("native UID graph contains NUL");
    const bool gpu=conv.gpu;
    if(gpu&&ncnn::get_gpu_count()!=1)throw std::runtime_error("one real UID Vulkan GPU required; no CPU fallback");
    if(gpu)gpu_allocator=std::make_unique<BoundedVkAllocator>(ncnn::get_gpu_device(0));
    net.opt.num_threads=2;net.opt.use_vulkan_compute=gpu;
    net.opt.use_fp16_packed=net.opt.use_fp16_storage=net.opt.use_fp16_arithmetic=false;
    net.opt.use_bf16_storage=net.opt.use_int8_inference=net.opt.use_int8_storage=net.opt.use_int8_arithmetic=false;
    net.opt.use_winograd_convolution=false;net.opt.use_tensor_storage=false;
    net.opt.blob_allocator=net.opt.workspace_allocator=&allocator;
    if(gpu)net.set_vulkan_device(0);
    if(net.register_custom_layer("Convolution",make,nullptr,&conv)||
       net.register_custom_layer("InnerProduct",make,nullptr,&linear)||
       net.register_custom_layer("AiiUnbiasedVariance",AiiUnbiasedVariance_layer_creator))
      throw std::runtime_error("native UID layer registration failed");
    if(net.load_param_mem(graph.c_str())||net.load_model(weights.data())!=weights.size())
      throw std::runtime_error("native UID graph/weights refused");
    size_t heavy=0,variance=0;
    for(const auto* l:net.layers()) {
      if(l->type=="Convolution"||l->type=="InnerProduct") {
        ++heavy;if(gpu&&!l->support_vulkan)throw std::runtime_error("native UID heavy operation lacks GPU support");
      }
      if(l->type=="AiiUnbiasedVariance")++variance;
    }
    if(net.layers().size()!=324||heavy!=156||variance!=1)
      throw std::runtime_error("bound native UID graph structure changed");
  }
  std::array<float,256> run(std::vector<float>& features,size_t frames,std::function<bool()> stopped) {
    if(frames<198||frames>3000||features.size()!=frames*80)throw std::runtime_error("native UID full context shape differs");
    fence.stopped=std::move(stopped);fence.refused=false;
    struct Clear {DispatchFence& f;~Clear(){f.stopped={};}} clear{fence};
    if(!fence.admit())throw NcnnCancelled();
    ncnn::Mat input(80,int(frames),features.data(),size_t(4)),out;
    auto ex=net.create_extractor();
    if(gpu_allocator) {
      ex.set_blob_vkallocator(gpu_allocator.get());ex.set_workspace_vkallocator(gpu_allocator.get());
    }
    if(ex.input("in0",input))throw std::runtime_error("native UID input refused");
    const int rc=ex.extract("out0",out);
    if(rc==cancelled_dispatch&&fence.refused)throw NcnnCancelled();
    if(rc)throw std::runtime_error("native UID inference failed: "+std::to_string(rc));
    if(out.dims!=1||out.w!=256||out.elempack!=1||out.elemsize!=4)
      throw std::runtime_error("native UID output geometry changed");
    std::array<float,256> result;std::copy_n(static_cast<const float*>(out),256,result.begin());return result;
  }
};
} // namespace uid_detail
