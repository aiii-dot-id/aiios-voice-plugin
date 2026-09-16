// Private candidate: fixed hot-step storage, reusable prompt-only scratch.
#pragma once
#include "persistent_graph_storage.h"
#include <unordered_set>

inline ggml_backend_buffer_t native_allocate_fixed_step_storage(
        ggml_context * context, ggml_cgraph * step, ggml_backend_t backend) {
    struct Owner {
        ggml_context * metadata = nullptr;
        ggml_backend_buffer_t buffer = nullptr;
        ~Owner() {
            if (buffer) ggml_backend_buffer_free(buffer);
            if (metadata) ggml_free(metadata);
        }
    } owner;
    std::vector<std::pair<ggml_tensor *, ggml_tensor *>> selected;
    try {
        if (!step || ggml_graph_n_nodes(step) <= 0) return nullptr;
        std::unordered_set<ggml_tensor *> step_roots;
        for (int i = 0; i < ggml_graph_n_nodes(step); ++i) {
            auto * tensor = ggml_graph_node(step, i);
            while (tensor->view_src) tensor = tensor->view_src;
            step_roots.insert(tensor);
        }
        for (auto * t = ggml_get_first_tensor(context); t; t = ggml_get_next_tensor(context, t)) {
            if (!t->data && !t->view_src && (t->op == GGML_OP_NONE || step_roots.count(t))) {
                if (t->buffer || !ggml_is_contiguous(t)) return nullptr;
                selected.emplace_back(t, nullptr);
            }
        }
        if (selected.empty() || selected.size() > std::numeric_limits<size_t>::max() / ggml_tensor_overhead()) return nullptr;
        owner.metadata = ggml_init({selected.size() * ggml_tensor_overhead(), nullptr, true});
        if (!owner.metadata) return nullptr;
        for (auto & pair : selected) pair.second = ggml_dup_tensor(owner.metadata, pair.first);
        owner.buffer = ggml_backend_alloc_ctx_tensors(owner.metadata, backend);
        if (!owner.buffer) return nullptr;
        for (auto & pair : selected) {
            if (ggml_backend_tensor_alloc(pair.second->buffer, pair.first, pair.second->data) != GGML_STATUS_SUCCESS) {
                for (auto & item : selected) { item.first->data = nullptr; item.first->buffer = nullptr; }
                return nullptr;
            }
        }
        return std::exchange(owner.buffer, nullptr);
    } catch (...) {
        for (auto & pair : selected) { pair.first->data = nullptr; pair.first->buffer = nullptr; }
        return nullptr;
    }
}
