#pragma once
#include "pinned_upload_batch.h"
#include <memory>

// A cache import may exceed one staging allocation. Copy each complete tensor
// into owned storage before its caller reuses scratch, flush at a fixed bound,
// and finish the last group before the importer permits prompt computation.
// No tensor data, zero-filled suffix, shape or arithmetic is omitted.
class NativeCacheUploadBatch {
    static constexpr size_t maximum_ = 64u * 1024u * 1024u;
    ggml_backend_t backend_;
    size_t limit_, bytes_ = 0;
    std::unique_ptr<NativePinnedUploadBatch> batch_;
public:
    explicit NativeCacheUploadBatch(ggml_backend_t backend, size_t limit = maximum_)
        : backend_(backend), limit_(limit) {
        if (!backend_ || !limit_ || limit_ > maximum_)
            throw std::invalid_argument("invalid cache upload budget or backend");
    }
    NativeCacheUploadBatch(const NativeCacheUploadBatch&) = delete;
    NativeCacheUploadBatch& operator=(const NativeCacheUploadBatch&) = delete;
    void finish() {
        if (batch_) {
            batch_->finish();
            batch_.reset();
            bytes_ = 0;
        }
    }
    void f32(ggml_tensor* tensor, const std::vector<float>& values) {
        if (values.empty() || values.size() > limit_ / sizeof(float))
            throw std::invalid_argument("cache tensor exceeds pinned upload budget");
        const size_t bytes = values.size() * sizeof(float);
        if (bytes > limit_ - bytes_) finish();
        if (!batch_) batch_ = std::make_unique<NativePinnedUploadBatch>(backend_);
        batch_->f32(tensor, values); // copies all bytes; never retains caller scratch
        bytes_ += bytes;
    }
};
