// Private Vulkan transfer descriptor. No tensor data or graph is allocated.
#pragma once
#include "ggml.h"
#include "ggml-backend.h"
#include <cstddef>
#include <limits>
#include <stdexcept>

inline ggml_tensor native_cache_destination(const ggml_tensor & prototype,
                                             size_t slot, size_t capacity) {
    if (!prototype.view_src || prototype.view_offs != 0 ||
        !prototype.view_src->buffer || !prototype.view_src->data ||
        prototype.type != GGML_TYPE_F32 || slot >= capacity) {
        throw std::runtime_error("native cache transfer descriptor refused");
    }
    const size_t bytes = ggml_nbytes(&prototype);
    const size_t available = ggml_nbytes(prototype.view_src);
    if (!bytes || capacity > std::numeric_limits<size_t>::max() / bytes ||
        capacity * bytes != available) {
        throw std::runtime_error("native cache transfer extent differs");
    }
    // The original descriptor's layout is unchanged. Re-initialize the view
    // with the public API, so data AND view_offs agree. Vulkan's init callback
    // retains no pointer/resources; the synchronous copy consumes this value
    // before it leaves the model-owner thread's stack.
    ggml_tensor destination = prototype;
    destination.buffer = nullptr;
    destination.data = nullptr;
    destination.view_offs = slot * bytes;
    if (ggml_backend_view_init(&destination) != GGML_STATUS_SUCCESS) {
        throw std::runtime_error("native cache transfer initialization failed");
    }
    return destination;
}
