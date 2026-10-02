#pragma once
#include "ggml-backend.h"
#include <cstdint>
#include <cstring>
#include <stdexcept>
#include <vector>

// Private Vulkan setup helper. Keep every original initialization, but copy
// from one backend-owned host buffer and fence once before any computation.
// Unlike ordinary vector storage, this buffer is recognized as pinned by the
// Vulkan backend, so set_tensor_async need not fall back to one blocking
// staging-buffer submission per tensor. No tensor layout or arithmetic changes.
class NativePinnedUploadBatch {
    struct Entry { ggml_tensor* tensor; size_t offset; size_t bytes; };
    static constexpr size_t limit_ = 64u * 1024u * 1024u;
    ggml_backend_t backend_;
    ggml_backend_buffer_t pinned_ = nullptr;
    std::vector<uint8_t> bytes_;
    std::vector<Entry> entries_;
    bool pending_ = false;
    bool finished_ = false;

    void retire() {
        if (pending_) {
            ggml_backend_synchronize(backend_);
            pending_ = false;
        }
        if (pinned_) {
            ggml_backend_buffer_free(pinned_);
            pinned_ = nullptr;
        }
    }
    template<class T>
    void add(ggml_tensor* tensor, const std::vector<T>& values, ggml_type type) {
        if (finished_ || !tensor || tensor->type != type || values.empty()
            || values.size() > limit_ / sizeof(T))
            throw std::invalid_argument("invalid bounded pinned tensor upload");
        const size_t size = values.size() * sizeof(T);
        if (size != ggml_nbytes(tensor) || size > limit_ - bytes_.size())
            throw std::invalid_argument("pinned upload shape or byte limit differs");
        const size_t offset = bytes_.size();
        bytes_.resize(offset + size);
        std::memcpy(bytes_.data() + offset, values.data(), size);
        entries_.push_back({tensor, offset, size});
    }
public:
    explicit NativePinnedUploadBatch(ggml_backend_t backend) : backend_(backend) {}
    NativePinnedUploadBatch(const NativePinnedUploadBatch&) = delete;
    NativePinnedUploadBatch& operator=(const NativePinnedUploadBatch&) = delete;
    ~NativePinnedUploadBatch() noexcept { retire(); }
    void f32(ggml_tensor* tensor, const std::vector<float>& data) { add(tensor, data, GGML_TYPE_F32); }
    void i32(ggml_tensor* tensor, const std::vector<int32_t>& data) { add(tensor, data, GGML_TYPE_I32); }
    void finish() {
        if (finished_) return;
        if (entries_.empty()) { finished_ = true; return; }
        auto device = ggml_backend_get_device(backend_);
        auto type = device ? ggml_backend_dev_host_buffer_type(device) : nullptr;
        if (!type || !(pinned_ = ggml_backend_buft_alloc_buffer(type, bytes_.size())))
            throw std::runtime_error("Vulkan pinned upload buffer unavailable");
        auto* base = static_cast<uint8_t*>(ggml_backend_buffer_get_base(pinned_));
        if (!base || ggml_backend_buffer_get_type(pinned_) != type)
            throw std::runtime_error("Vulkan pinned upload buffer binding differs");
        std::memcpy(base, bytes_.data(), bytes_.size());
        for (const auto& e : entries_) {
            // A backend may throw after partial enqueue. Unwind still waits
            // before releasing the source buffer or the graph's destinations.
            pending_ = true;
            ggml_backend_tensor_set_async(backend_, e.tensor, base + e.offset, 0, e.bytes);
        }
        retire();
        finished_ = true;
    }
};
