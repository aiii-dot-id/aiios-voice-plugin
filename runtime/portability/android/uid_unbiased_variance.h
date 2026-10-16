// Exact full-utterance variance correction for the bound ResNet152 UID graph.
// ncnn's conversion folded N/(N-1) at export time; N is a runtime extent.
#pragma once
#include "command.h"
#include "layer.h"
#include "pipeline.h"

class AiiUnbiasedVariance : public ncnn::Layer {
public:
  ncnn::Pipeline *pipeline = nullptr;
  AiiUnbiasedVariance() {
    one_blob_only = false;
    support_vulkan = true;
    support_packing = support_any_packing = true;
    support_vulkan_packing = support_vulkan_any_packing = true;
  }
  template <class M> bool valid(const std::vector<M> &b) const {
    if (b.size() != 2) return false;
    const auto &a = b[0], &origin = b[1];
    return (a.dims == 2 || a.dims == 3) &&
           a.w * a.h * a.d * a.c * a.elempack == 10240 &&
           a.elemsize == size_t(a.elempack * 4) &&
           origin.dims == 3 && origin.w >= 25 && origin.w <= 375 &&
           origin.h == 10 && origin.c * origin.elempack == 1024 &&
           origin.elemsize == size_t(origin.elempack * 4);
  }
  int forward(const std::vector<ncnn::Mat> &b, std::vector<ncnn::Mat> &t,
              const ncnn::Option &opt) const override {
    if (!valid(b)) return -1;
    const auto &a = b[0];
    t[0].create_like(a, opt.blob_allocator);
    if (t[0].empty()) return -100;
    const float *x = a;
    float *y = t[0];
    const int spatial = a.w * a.h * a.d;
    for (int c = 0; c < a.c; ++c) {
      for (int i = 0; i < spatial * a.elempack; ++i) {
        const float numerator = x[c * a.cstep * a.elempack + i] * float(b[1].w);
        y[c * t[0].cstep * a.elempack + i] = numerator / float(b[1].w - 1);
      }
    }
    return 0;
  }
  int create_pipeline(const ncnn::Option &opt) override {
    if (!opt.use_vulkan_compute) return 0;
    const char *shader = R"glsl(#version 450
layout(binding=0) readonly buffer X { float x[]; };
layout(binding=1) writeonly buffer Y { float y[]; };
layout(push_constant) uniform parameter {
 uint spatial; uint channels; uint pack; uint xstep; uint ystep; uint n;
} p;
void main() {
 uint i=gl_GlobalInvocationID.x;
 if(i>=p.spatial*p.channels) return;
 uint c=i/p.spatial, s=i%p.spatial;
 uint xi=(c/p.pack)*p.xstep*p.pack+s*p.pack+c%p.pack;
 uint yi=(c/p.pack)*p.ystep*p.pack+s*p.pack+c%p.pack;
 precise float numerator=x[xi]*float(p.n);
 y[yi]=numerator/float(p.n-1);
}
)glsl";
    std::vector<uint32_t> code;
    if (ncnn::compile_spirv_module(shader, opt, code)) return -1;
    pipeline = new ncnn::Pipeline(vkdev);
    pipeline->set_optimal_local_size_xyz(64, 1, 1);
    int status = pipeline->create(code.data(), code.size() * 4, {});
    if (status) return status;
    const auto &info = pipeline->shader_info();
    if (info.push_constant_count != 6 || info.binding_count != 2) {
      NCNN_LOGE("UID_SHADER_CONTRACT_REFUSED constants=%d bindings=%d",
                info.push_constant_count, info.binding_count);
      return -1;
    }
    return 0;
  }
  int destroy_pipeline(const ncnn::Option &) override {
    delete pipeline;
    pipeline = nullptr;
    return 0;
  }
  int forward(const std::vector<ncnn::VkMat> &b, std::vector<ncnn::VkMat> &t,
              ncnn::VkCompute &cmd, const ncnn::Option &opt) const override {
    if (!pipeline || !valid(b)) return -1;
    const auto &a = b[0];
    t[0].create_like(a, opt.blob_vkallocator);
    if (t[0].empty()) return -100;
    std::vector<ncnn::vk_constant_type> p(6);
    p[0].u32 = a.w * a.h * a.d;
    p[1].u32 = a.c * a.elempack;
    p[2].u32 = a.elempack;
    p[3].u32 = a.cstep;
    p[4].u32 = t[0].cstep;
    p[5].u32 = b[1].w;
    ncnn::VkMat dispatcher;
    dispatcher.w = p[0].u32 * p[1].u32;
    dispatcher.h = dispatcher.c = 1;
    cmd.record_pipeline(pipeline, {a, t[0]}, p, dispatcher);
    return 0;
  }
};
DEFINE_LAYER_CREATOR(AiiUnbiasedVariance)
