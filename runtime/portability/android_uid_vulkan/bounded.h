// Private qualification guard. No giant converted broadcast reaches malloc.
#pragma once
#include "allocator.h"
#include <cstdio>
#include <map>
#include <mutex>

class BoundedAllocator : public ncnn::Allocator {
public:
  static constexpr size_t single_limit = 512ULL << 20;
  static constexpr size_t live_limit = 1024ULL << 20;
  size_t peak = 0, refusals = 0;
  void *fastMalloc(size_t size) override {
    std::lock_guard<std::mutex> lock(mu);
    if (size > single_limit || size > live_limit - live) {
      ++refusals;
      std::fprintf(stderr, "UID_ALLOCATION_REFUSED bytes=%zu live=%zu\n", size,
                   live);
      return nullptr;
    }
    void *ptr = ncnn::fastMalloc(size);
    if (ptr) {
      sizes[ptr] = size;
      live += size;
      if (live > peak)
        peak = live;
    }
    return ptr;
  }
  void fastFree(void *ptr) override {
    if (!ptr)
      return;
    std::lock_guard<std::mutex> lock(mu);
    auto it = sizes.find(ptr);
    if (it == sizes.end())
      std::abort();
    live -= it->second;
    sizes.erase(it);
    ncnn::fastFree(ptr);
  }

private:
  std::mutex mu;
  size_t live = 0;
  std::map<void *, size_t> sizes;
};

// Bound logical requests as well as CPU allocations. Underlying Vulkan pools
// may retain blocks; this is not a claim that total driver memory is bounded.
class BoundedVkAllocator : public ncnn::VkBlobAllocator {
public:
  explicit BoundedVkAllocator(const ncnn::VulkanDevice *d)
      : ncnn::VkBlobAllocator(d) {}
  ncnn::VkBufferMemory *fastMalloc(size_t size) override {
    if (size > BoundedAllocator::single_limit) {
      std::fprintf(stderr, "UID_VK_ALLOCATION_REFUSED bytes=%zu\n", size);
      return nullptr;
    }
    return ncnn::VkBlobAllocator::fastMalloc(size);
  }
};
