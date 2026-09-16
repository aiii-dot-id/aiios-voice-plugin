#pragma once
#include "ggml-backend.h"

// A batch owns completion, not tensor storage. All source/destination views
// must remain alive until finish (or exceptional retirement) returns.
// Only the explicitly qualified Vulkan path opts in; other backends retain
// their exact synchronous tensor-copy behavior.
class NativeCacheCopyBatch {
    ggml_backend_t backend_;
    bool enabled_;
    bool pending_ = false;
public:
    NativeCacheCopyBatch(ggml_backend_t backend, bool enabled)
        : backend_(backend), enabled_(enabled) {}
    NativeCacheCopyBatch(const NativeCacheCopyBatch&) = delete;
    NativeCacheCopyBatch& operator=(const NativeCacheCopyBatch&) = delete;
    ~NativeCacheCopyBatch() noexcept { finish(); }
    void copy(const ggml_tensor* source, ggml_tensor* destination) {
        if (enabled_) {
            // Set before dispatch: if a backend throws after partial enqueue,
            // the scope still fences it before graph-owned tensors retire.
            pending_ = true;
            ggml_backend_tensor_copy_async(backend_, backend_, source, destination);
        } else {
            ggml_backend_tensor_copy(source, destination);
        }
    }
    void finish() {
        if (pending_) {
            ggml_backend_synchronize(backend_);
            pending_ = false;
        }
    }
};
