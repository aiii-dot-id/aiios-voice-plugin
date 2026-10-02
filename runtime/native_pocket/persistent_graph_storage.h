// Private Vulkan candidate: persistent leaves, allocator-owned intermediates.
#pragma once
#include "ggml.h"
#include "ggml-backend.h"
#include "ggml-alloc.h"
#include <limits>
#include <utility>
#include <vector>

inline void native_retain_graph_value(ggml_tensor * tensor) {
    // A view flag alone cannot retain the allocation it aliases.
    while (tensor->view_src) tensor = tensor->view_src;
    ggml_set_output(tensor);
}

inline bool native_bind_ready_view(ggml_tensor * tensor) {
    if (!tensor->view_src || tensor->buffer) return true;
    if (!tensor->view_src->data || !tensor->view_src->buffer) return false;
    if (ggml_backend_view_init(tensor) == GGML_STATUS_SUCCESS) return true;
    tensor->buffer = nullptr;
    tensor->data = nullptr;
    return false;
}

inline ggml_backend_buffer_t native_allocate_persistent_leaves(
        ggml_context * graph, ggml_backend_t backend) {
    struct Owner {
        ggml_context * metadata = nullptr;
        ggml_backend_buffer_t buffer = nullptr;
        ~Owner() {
            if (buffer) ggml_backend_buffer_free(buffer);
            if (metadata) ggml_free(metadata);
        }
    } owner;
    std::vector<std::pair<ggml_tensor *, ggml_tensor *>> leaves;
    try {
        for (auto * t = ggml_get_first_tensor(graph); t; t = ggml_get_next_tensor(graph, t)) {
            if (!t->data && !t->view_src && t->op == GGML_OP_NONE) {
                if (t->buffer || !ggml_is_contiguous(t)) return nullptr;
                leaves.emplace_back(t, nullptr);
            }
        }
        if (leaves.empty() || leaves.size() > std::numeric_limits<size_t>::max() / ggml_tensor_overhead()) return nullptr;
        owner.metadata = ggml_init({leaves.size() * ggml_tensor_overhead(), nullptr, true});
        if (!owner.metadata) return nullptr;
        for (auto & pair : leaves) pair.second = ggml_dup_tensor(owner.metadata, pair.first);
        // The standard allocator supplies alignment, backend sizing and split
        // buffers. This context contains only aliases of the persistent leaves.
        owner.buffer = ggml_backend_alloc_ctx_tensors(owner.metadata, backend);
        if (!owner.buffer) return nullptr;
        for (auto & pair : leaves) {
            if (ggml_backend_tensor_alloc(pair.second->buffer, pair.first, pair.second->data) != GGML_STATUS_SUCCESS) {
                for (auto & item : leaves) { item.first->data = nullptr; item.first->buffer = nullptr; }
                return nullptr;
            }
        }
        return std::exchange(owner.buffer, nullptr);
    } catch (...) {
        // No original tensor is touched until all metadata/backend allocation
        // succeeds. Restore any partially bound originals before buffer free.
        for (auto & pair : leaves) { pair.first->data = nullptr; pair.first->buffer = nullptr; }
        return nullptr;
    }
}

inline bool native_bind_persistent_views(ggml_context * graph) {
    for (auto * t = ggml_get_first_tensor(graph); t; t = ggml_get_next_tensor(graph, t)) {
        if (t->view_src && t->view_src->data && t->view_src->buffer && !native_bind_ready_view(t)) return false;
    }
    return true;
}
