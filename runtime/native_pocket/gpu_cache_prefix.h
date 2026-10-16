#pragma once

#include "ggml.h"
#include "ggml-backend.h"

#include <cstring>
#include <atomic>
#include <cstdint>
#include <limits>
#include <stdexcept>
#include <vector>

namespace native_cache_measurement {
inline std::atomic<uint64_t> imports{0}, uploaded_bytes{0}, cleared_bytes{0};
}

inline bool native_is_vulkan_cache(const ggml_tensor* tensor) {
    if (!tensor || !tensor->buffer || tensor->view_src || tensor->type != GGML_TYPE_F32)
        return false;
    const auto type = ggml_backend_buffer_get_type(tensor->buffer);
    const auto device = ggml_backend_buft_get_device(type);
    if (!device) return false;
    const auto registry = ggml_backend_dev_backend_reg(device);
    return registry && std::strcmp(ggml_backend_reg_name(registry), "Vulkan") == 0;
}

// Same fully initialized FP32 cache as prefix + a host-built zero-filled
// vector, without uploading its empty tail. The bound Vulkan implementation
// waits for fillBuffer completion; set_tensor completes the prefix upload.
// No arithmetic, quantization, graph shape, or validity accounting changes.
inline void native_upload_cache_prefix(ggml_tensor* tensor,
                                       const std::vector<float>& prefix,
                                       size_t capacity) {
    if (!tensor || tensor->type != GGML_TYPE_F32 || tensor->view_src ||
        !ggml_is_contiguous(tensor) || !tensor->buffer || !tensor->data ||
        capacity > std::numeric_limits<size_t>::max() / sizeof(float) ||
        prefix.size() > capacity || ggml_nbytes(tensor) != capacity * sizeof(float))
        throw std::runtime_error("cache prefix shape or storage refused");
    const size_t copied = prefix.size() * sizeof(float);
    const size_t tail = (capacity - prefix.size()) * sizeof(float);
    if (tail) ggml_backend_tensor_memset(tensor, 0, copied, tail);
    if (copied) ggml_backend_tensor_set(tensor, prefix.data(), 0, copied);
    native_cache_measurement::uploaded_bytes.fetch_add(copied, std::memory_order_relaxed);
    native_cache_measurement::cleared_bytes.fetch_add(tail, std::memory_order_relaxed);
    native_cache_measurement::imports.fetch_add(1, std::memory_order_relaxed);
}
