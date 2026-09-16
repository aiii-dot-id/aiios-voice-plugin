// Same-device copies, completed before cache publication or source reuse.
#pragma once
#include "ggml-backend.h"
#include <stdexcept>
#include <vector>
inline void aii_step_kv_batch(ggml_backend_t backend,
        const std::vector<ggml_tensor*>& keys, const std::vector<ggml_tensor*>& values,
        const std::vector<ggml_tensor*>& key_dst, const std::vector<ggml_tensor*>& value_dst) {
    if(!backend || keys.empty() || keys.size()!=values.size() || keys.size()!=key_dst.size() || keys.size()!=value_dst.size())
        throw std::runtime_error("step KV batch census differs");
    auto validate=[](const ggml_tensor* a,const ggml_tensor* b){
        if(!a || !b || !a->buffer || !b->buffer || a->type!=b->type)
            throw std::runtime_error("step KV batch storage differs");
        for(int d=0;d<4;++d)if(a->ne[d]!=b->ne[d] || a->nb[d]!=b->nb[d])
            throw std::runtime_error("step KV batch layout differs");
    };
    // Refuse the whole batch before any write if even the final pair is invalid.
    for(size_t i=0;i<keys.size();++i){validate(keys[i],key_dst[i]);validate(values[i],value_dst[i]);}
    for(size_t i=0;i<keys.size();++i){
        ggml_backend_tensor_copy_async(backend,backend,keys[i],key_dst[i]);
        ggml_backend_tensor_copy_async(backend,backend,values[i],value_dst[i]);
    }
    ggml_backend_synchronize(backend);
}
