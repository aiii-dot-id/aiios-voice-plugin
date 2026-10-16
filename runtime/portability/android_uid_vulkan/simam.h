// Exact energy term of this model's SimAM attention. No learned parameters.
#pragma once
#include "command.h"
#include "layer.h"
#include "pipeline.h"
#include <cmath>

class AiiSimAMEnergy : public ncnn::Layer {
public:
  int frequency = 0;
  ncnn::Pipeline *pipeline = nullptr;
  AiiSimAMEnergy() {
    one_blob_only = false;
    support_vulkan = true;
    support_packing = support_any_packing = true;
    support_vulkan_packing = support_vulkan_any_packing = true;
  }
  int load_param(const ncnn::ParamDict &p) override {
    frequency = p.get(0, 0);
    return frequency == 80 || frequency == 40 || frequency == 20 ||
                   frequency == 10
               ? 0
               : -1;
  }
  template <class M> bool valid(const std::vector<M> &b) const {
    if (b.size() != 3)
      return false;
    const auto &a = b[0], &v = b[1], &origin = b[2];
    return a.dims == 3 && v.dims == 3 && origin.dims == 3 && a.w >= 1 &&
           a.w <= 3000 && a.h == frequency && a.w * a.h > 1 &&
           a.elemsize == size_t(a.elempack * 4) &&
           v.elemsize == size_t(v.elempack * 4) &&
           a.c * a.elempack == v.c * v.elempack && v.w == 1 && v.h == 1 &&
           origin.w == a.w && origin.h == a.h &&
           origin.c * origin.elempack == a.c * a.elempack;
  }
  int forward(const std::vector<ncnn::Mat> &b, std::vector<ncnn::Mat> &t,
              const ncnn::Option &opt) const override {
    if (!valid(b))
      return -1;
    const auto &a = b[0], &v = b[1];
    t[0].create_like(a, opt.blob_allocator);
    if (t[0].empty())
      return -100;
    const float *x = a;
    const float *sums = v;
    float *out = t[0];
    const int spatial = a.w * a.h;
    for (int c = 0; c < a.c * a.elempack; ++c) {
      const float denom =
          (sums[(c / v.elempack) * v.cstep * v.elempack + c % v.elempack] /
               float(spatial - 1) +
           0.0001f) *
          4.0f;
      for (int s = 0; s < spatial; ++s) {
        size_t i = (c / a.elempack) * a.cstep * a.elempack + s * a.elempack +
                   c % a.elempack;
        size_t j = (c / a.elempack) * t[0].cstep * a.elempack + s * a.elempack +
                   c % a.elempack;
        out[j] = x[i] / denom + 0.5f;
      }
    }
    return 0;
  }
  int create_pipeline(const ncnn::Option &opt) override {
    if (!opt.use_vulkan_compute)
      return 0;
    // ncnn patches the compiled workgroup size. Its pinned SPIR-V reflector
    // recognizes push constants only on a block named exactly "parameter".
    // Validate that reflected contract before allowing any dispatch.
    const char *shader = R"glsl(#version 450
layout(binding=0) readonly buffer X { float x[]; };
layout(binding=1) readonly buffer V { float v[]; };
layout(binding=2) writeonly buffer Y { float y[]; };
layout(push_constant) uniform parameter {
 uint spatial; uint channels; uint pack; uint xstep; uint vpack; uint vstep; uint ystep;
} p;
void main() {
 uint i=gl_GlobalInvocationID.x;
 if (i >= p.spatial*p.channels) return;
 uint c=i/p.spatial, s=i%p.spatial;
 uint xi=(c/p.pack)*p.xstep*p.pack+s*p.pack+c%p.pack;
 uint yi=(c/p.pack)*p.ystep*p.pack+s*p.pack+c%p.pack;
 uint vi=(c/p.vpack)*p.vstep*p.vpack+c%p.vpack;
 precise float variance=v[vi]/float(p.spatial-1);
 precise float shifted=variance+0.0001;
 precise float denom=shifted*4.0;
 precise float energy=x[xi]/denom;
 y[yi]=energy+0.5;
}
)glsl";
    std::vector<uint32_t> code;
    if (ncnn::compile_spirv_module(shader, opt, code))
      return -1;
    pipeline = new ncnn::Pipeline(vkdev);
    pipeline->set_optimal_local_size_xyz(64, 1, 1);
    int status = pipeline->create(code.data(), code.size() * 4, {});
    if (status)
      return status;
    const auto &info = pipeline->shader_info();
    if (info.push_constant_count != 7 || info.binding_count != 3) {
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
    if (!pipeline || !valid(b))
      return -1;
    const auto &a = b[0], &v = b[1];
    t[0].create_like(a, opt.blob_vkallocator);
    if (t[0].empty())
      return -100;
    std::vector<ncnn::vk_constant_type> p(7);
    p[0].u32 = a.w * a.h;
    p[1].u32 = a.c * a.elempack;
    p[2].u32 = a.elempack;
    p[3].u32 = a.cstep;
    p[4].u32 = v.elempack;
    p[5].u32 = v.cstep;
    p[6].u32 = t[0].cstep;
    ncnn::VkMat dispatcher;
    dispatcher.w = p[0].u32 * p[1].u32;
    dispatcher.h = 1;
    dispatcher.c = 1;
    cmd.record_pipeline(pipeline, {a, v, t[0]}, p, dispatcher);
    return 0;
  }
};
DEFINE_LAYER_CREATOR(AiiSimAMEnergy)
