#pragma once
#include "allocator.h"
#include <cstdlib>
#include <map>
#include <mutex>

namespace uid_detail {
// Logical allocation limits, not a claim about total driver pool residency.
class BoundedAllocator:public ncnn::Allocator {
  std::mutex mutex;
  std::map<void*,size_t> sizes;
  size_t live=0;
public:
  static constexpr size_t single_limit=512ULL<<20,live_limit=1024ULL<<20;
  void* fastMalloc(size_t size) override {
    std::lock_guard<std::mutex> lock(mutex);
    if(size>single_limit||size>live_limit-live)return nullptr;
    void* p=ncnn::fastMalloc(size);
    if(p){sizes[p]=size;live+=size;}
    return p;
  }
  void fastFree(void* p) override {
    if(!p)return;
    std::lock_guard<std::mutex> lock(mutex);
    const auto at=sizes.find(p);
    if(at==sizes.end())std::abort();
    live-=at->second;sizes.erase(at);ncnn::fastFree(p);
  }
};
class BoundedVkAllocator:public ncnn::VkBlobAllocator {
public:
  explicit BoundedVkAllocator(const ncnn::VulkanDevice* d):ncnn::VkBlobAllocator(d) {}
  ncnn::VkBufferMemory* fastMalloc(size_t size) override {
    if(size>BoundedAllocator::single_limit)return nullptr;
    return ncnn::VkBlobAllocator::fastMalloc(size);
  }
};
} // namespace uid_detail
