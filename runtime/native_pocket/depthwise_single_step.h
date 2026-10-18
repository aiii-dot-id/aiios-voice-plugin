#pragma once
#include "ggml.h"
#include <stdexcept>

inline ggml_tensor * aii_depthwise_single_step(ggml_context *ctx,
        ggml_tensor *dense_weight, ggml_tensor *input, int64_t channels,
        int64_t kernel) {
    if (!ctx || !dense_weight || !input || channels<=0 || kernel<=0 ||
        dense_weight->type!=GGML_TYPE_F32 || input->type!=GGML_TYPE_F32 ||
        dense_weight->ne[0]!=kernel || dense_weight->ne[1]!=channels ||
        dense_weight->ne[2]!=channels || dense_weight->ne[3]!=1 ||
        input->ne[0]!=1 || input->ne[1]!=channels || input->ne[2]!=1 || input->ne[3]!=1 ||
        !ggml_is_contiguous(dense_weight) || !ggml_is_contiguous(input)) {
        throw std::runtime_error("depthwise single-frame contract differs");
    }
    auto *diagonal=ggml_view_2d(ctx,dense_weight,kernel,channels,
                              dense_weight->nb[1]+dense_weight->nb[2],0);
    auto *products=ggml_mul(ctx,diagonal,input);
    return ggml_reshape_3d(ctx,products,kernel,channels,1);
}
