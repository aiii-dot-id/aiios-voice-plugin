// Included inside MimiDecoder's private namespace by the candidate builder.
// One graph for the causal waveform tail, one packed upload/readback per step.
// State still belongs to DecoderState and is reset by the existing session.
class MimiStreamingTailRuntime {
    using Tensor = core::TensorValue;
    ggml_backend_t backend_ = nullptr;
    ggml_context * context_ = nullptr;
    ggml_backend_buffer_t parameters_ = nullptr;
    ggml_gallocr_t allocator_ = nullptr;
    ggml_cgraph * graph_ = nullptr;
    Tensor input_, output_;
    int64_t input_count_ = 0, audio_count_ = 0, state_count_ = 0;
    int64_t cursor_ = 0;
    std::vector<Tensor> next_state_;

    Tensor slice(core::ModuleBuildContext & c, const Tensor & t, int axis,
                 int64_t start, int64_t count) {
        return modules::SliceModule({axis, start, count}).build(c, t);
    }
    Tensor flatten(core::ModuleBuildContext & c, const Tensor & t) {
        // Slicing a BCT history preserves its channel stride. Reshape may not
        // reinterpret that strided view as flat storage; compact it on GPU.
        auto contiguous = t;
        if (!ggml_is_contiguous(t.tensor)) contiguous.tensor = ggml_cont(c.ggml, t.tensor);
        return modules::ReshapeModule({core::TensorShape::from_dims({t.shape.num_elements()})}).build(c, contiguous);
    }
    Tensor state(core::ModuleBuildContext & c, int64_t channels, int64_t frames) {
        auto flat = slice(c, input_, 0, cursor_, channels * frames);
        cursor_ += channels * frames;
        return modules::ReshapeModule({core::TensorShape::from_dims({1, channels, frames})}).build(c, flat);
    }
    Tensor conv(core::ModuleBuildContext & c, Tensor x, const Tensor & weight,
                const std::optional<Tensor> & bias, int64_t out_channels, int64_t kernel) {
        const int64_t channels = x.shape.dims[1], frames = x.shape.dims[2];
        if (kernel > 1) {
            auto history = state(c, channels, kernel - 1);
            x = modules::ConcatModule({2}).build(c, history, x);
            next_state_.push_back(slice(c, x, 2, frames, kernel - 1));
        }
        return modules::Conv1dModule({channels, out_channels, kernel, 1, 0, 1, bias.has_value()})
            .build(c, x, modules::Conv1dWeights{weight, bias});
    }
    Tensor upsample(core::ModuleBuildContext & c, Tensor x, const Tensor & weight,
                    const std::optional<Tensor> & bias, int64_t channels, int stride) {
        const int64_t input_channels = x.shape.dims[1], frames = x.shape.dims[2];
        x = modules::ConvTranspose1dModule({input_channels, channels, stride * 2, stride, 0, 1, bias.has_value()})
            .build(c, x, modules::ConvTranspose1dWeights{weight, bias});
        // Overlap-add the previous bias-free partial exactly once. The new
        // partial starts beyond the overlapped prefix and is bias-free again.
        auto previous = state(c, channels, stride);
        auto prefix = modules::ResidualAddModule().build(c, slice(c, x, 2, 0, stride), previous);
        auto remaining = slice(c, x, 2, stride, frames * stride);
        x = modules::ConcatModule({2}).build(c, prefix, remaining);
        auto partial = slice(c, x, 2, frames * stride, stride);
        if (bias) {
            auto b = modules::ReshapeModule({core::TensorShape::from_dims({1, channels, 1})}).build(c, *bias);
            partial.tensor = ggml_sub(c.ggml, partial.tensor, b.tensor);
        }
        next_state_.push_back(partial);
        return slice(c, x, 2, 0, frames * stride);
    }
    Tensor residual(core::ModuleBuildContext & c, const Tensor & input,
                    const PocketTTSBackendResidualBlockWeights & weights,
                    int64_t channels, int64_t hidden) {
        auto x = modules::EluModule().build(c, input);
        x = conv(c, x, weights.conv1.weight, weights.conv1.bias, hidden, 3);
        x = modules::EluModule().build(c, x);
        x = conv(c, x, weights.conv2.weight, weights.conv2.bias, channels, 1);
        return modules::ResidualAddModule().build(c, input, x);
    }

public:
    MimiStreamingTailRuntime(ggml_backend_t backend, int threads, size_t graph_bytes,
        const MimiDecoderConfig & config, const PocketTTSBackendWeights & weights, int64_t frames)
        : backend_(backend), input_count_(config.hidden_size * frames), audio_count_(frames * 120),
          state_count_(config.hidden_size * 6 + 256 * (6 + 2) + 128 * (5 + 2) + 64 * (4 + 2) + 64 * 2) {
        if (frames <= 0) throw std::runtime_error("streaming tail requires positive frames");
        try {
            context_ = ggml_init({graph_bytes, nullptr, true});
            if (!context_) throw std::runtime_error("streaming tail context allocation failed");
            core::ModuleBuildContext c{context_, "native_streaming_tail", weights.backend_type};
            input_ = core::make_tensor(c, GGML_TYPE_F32, core::TensorShape::from_dims({input_count_ + state_count_}));
            ggml_set_input(input_.tensor);
            auto x = modules::ReshapeModule({core::TensorShape::from_dims({1, config.hidden_size, frames})})
                .build(c, slice(c, input_, 0, 0, input_count_));
            cursor_ = input_count_;
            const auto & w = weights.mimi_decoder;
            x = conv(c, x, w.input_projection.weight, w.input_projection.bias, config.hidden_size, 7);
            x = upsample(c, modules::EluModule().build(c, x), w.stage0_upsample.weight, w.stage0_upsample.bias, 256, 6);
            x = residual(c, x, w.stage0_block, 256, 128);
            x = upsample(c, modules::EluModule().build(c, x), w.stage1_upsample.weight, w.stage1_upsample.bias, 128, 5);
            x = residual(c, x, w.stage1_block, 128, 64);
            x = upsample(c, modules::EluModule().build(c, x), w.stage2_upsample.weight, w.stage2_upsample.bias, 64, 4);
            x = residual(c, x, w.stage2_block, 64, 32);
            x = conv(c, modules::EluModule().build(c, x), w.output_projection.weight, w.output_projection.bias, 1, 3);
            if (cursor_ != input_count_ + state_count_ || x.shape.num_elements() != audio_count_)
                throw std::runtime_error("streaming tail state extent differs");
            output_ = flatten(c, x);
            for (const auto & next : next_state_)
                output_ = modules::ConcatModule({0}).build(c, output_, flatten(c, next));
            ggml_set_output(output_.tensor);
            if (core::is_host_backend(backend_)) {
                parameters_ = ggml_backend_alloc_ctx_tensors(context_, backend_);
                if (!parameters_) throw std::runtime_error("streaming tail parameter allocation failed");
            }
            core::set_backend_threads(backend_, threads);
            graph_ = ggml_new_graph_custom(context_, 32768, false);
            ggml_build_forward_expand(graph_, output_.tensor);
            allocator_ = ggml_gallocr_new(ggml_backend_get_default_buffer_type(backend_));
            if (!allocator_ || !ggml_gallocr_reserve(allocator_, graph_) || !ggml_gallocr_alloc_graph(allocator_, graph_))
                throw std::runtime_error("streaming tail graph allocation failed");
            core::write_tensor_f32(input_, std::vector<float>(static_cast<size_t>(input_count_ + state_count_), 0.0F));
        } catch (...) {
            release_partial_graph_runtime(allocator_, parameters_, context_);
            throw;
        }
    }
    ~MimiStreamingTailRuntime() { release_partial_graph_runtime(allocator_, parameters_, context_); }
    std::vector<float> run(const std::vector<float> & input, DecoderState & s) const {
        if (static_cast<int64_t>(input.size()) != input_count_)
            throw std::runtime_error("streaming tail input extent differs");
        // Resolve on every call: reset replaces DecoderState, so retaining
        // pointers into a prior generation would revive stale convolution state.
        std::vector<std::vector<float> *> states = {&s.input_projection.previous,
            &s.stage_upsamples[0].partial, &s.stage_residual_convs[0][0].previous,
            &s.stage_upsamples[1].partial, &s.stage_residual_convs[1][0].previous,
            &s.stage_upsamples[2].partial, &s.stage_residual_convs[2][0].previous,
            &s.output_projection.previous};
        auto packed = input;
        for (const auto * part : states) packed.insert(packed.end(), part->begin(), part->end());
        if (static_cast<int64_t>(packed.size()) != input_count_ + state_count_)
            throw std::runtime_error("streaming tail state binding differs");
        core::write_tensor_f32(input_, packed);
        if (core::compute_backend_graph(backend_, graph_) != GGML_STATUS_SUCCESS)
            throw std::runtime_error("streaming tail compute failed");
        auto result = core::read_tensor_f32(output_.tensor);
        if (static_cast<int64_t>(result.size()) != audio_count_ + state_count_)
            throw std::runtime_error("streaming tail output extent differs");
        for (float value : result) if (!std::isfinite(value))
            throw std::runtime_error("streaming tail nonfinite audio/state");
        auto cursor = result.begin() + audio_count_;
        for (auto * part : states) {
            std::copy(cursor, cursor + part->size(), part->begin());
            cursor += part->size();
        }
        result.resize(static_cast<size_t>(audio_count_));
        return result;
    }
};
