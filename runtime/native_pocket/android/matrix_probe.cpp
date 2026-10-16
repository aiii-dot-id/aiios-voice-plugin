// Reproduce the speech graph's first failing F32 operator without models.
#include "ggml.h"
#include "ggml-alloc.h"
#include "ggml-backend.h"
#include "ggml-cpu.h"
#include "ggml-vulkan.h"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <stdexcept>
#include <vector>

static void require(bool yes, const char *why) {
    if (!yes) throw std::runtime_error(why);
}

static bool run(ggml_backend_t backend, int n, bool view) {
    constexpr int k = 1024, m = 1024;
    const int planes = view ? 3 : 1;
    ggml_init_params params{8 * 1024 * 1024, nullptr, true};
    auto *ctx = ggml_init(params);
    require(ctx != nullptr, "context unavailable");
    auto *base = ggml_new_tensor_2d(ctx, GGML_TYPE_F32, k, m * planes);
    auto *a = view ? ggml_view_2d(ctx, base, k, m, k * sizeof(float),
                                2 * k * m * sizeof(float)) : base;
    auto *b = ggml_new_tensor_2d(ctx, GGML_TYPE_F32, k, n);
    auto *d = ggml_mul_mat(ctx, a, b);
    ggml_mul_mat_set_prec(d, GGML_PREC_F32);
    ggml_set_name(d, view ? "matrix_view_8MiB" : "matrix_contiguous");
    auto *graph = ggml_new_graph(ctx);
    ggml_build_forward_expand(graph, d);
    auto buffer = ggml_backend_alloc_ctx_tensors(ctx, backend);
    require(buffer != nullptr, "buffer unavailable");
    std::vector<float> av(k * m * planes), bv(k * n), result(m * n, 1234.f);
    // Dyadic fractions keep all products and accumulated results exactly
    // representable at this K; no broad speech tolerance masks missing writes.
    for (size_t i = 0; i < av.size(); ++i) av[i] = (int(i % 31) - 15) / 32.f;
    for (size_t i = 0; i < bv.size(); ++i) bv[i] = (int(i % 17) - 8) / 16.f;
    ggml_backend_tensor_set(base, av.data(), 0, av.size() * sizeof(float));
    ggml_backend_tensor_set(b, bv.data(), 0, bv.size() * sizeof(float));
    ggml_backend_tensor_set(d, result.data(), 0, result.size() * sizeof(float));
    const auto start = std::chrono::steady_clock::now();
    require(ggml_backend_graph_compute(backend, graph) == GGML_STATUS_SUCCESS,
            "compute failed");
    ggml_backend_tensor_get(d, result.data(), 0, result.size() * sizeof(float));
    const double ms = std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - start).count();
    size_t bad = 0, sentinel = 0;
    double max_error = 0;
    std::vector<float> reference(m * n);
    std::vector<int> column_bad(n, 0);
    for (int col = 0; col < n; ++col) {
        for (int row = 0; row < m; ++row) {
            double expected = 0;
            const size_t offset = (view ? 2 * k * m : 0) + row * k;
            for (int inner = 0; inner < k; ++inner)
                expected += double(av[offset + inner]) * bv[col * k + inner];
            reference[col * m + row] = float(expected);
            const float got = result[col * m + row];
            const double error = std::abs(double(got) - expected);
            if (!std::isfinite(got) || error > 1e-6) {
                ++bad; ++column_bad[col];
            }
            if (got == 1234.f) ++sentinel;
            max_error = std::max(max_error, std::isfinite(got) ? error : 1e30);
        }
    }
    std::printf("{\"n\":%d,\"view_8MiB\":%s,\"bad\":%zu,\"unwritten\":%zu,"
                "\"max_error\":%.9g,\"gpu_or_cpu_wall_ms\":%.6f,\"column_bad\":[",
                n, view ? "true" : "false", bad, sentinel, max_error, ms);
    for (int col = 0; col < n; ++col)
        std::printf("%s%d", col ? "," : "", column_bad[col]);
    // Do not wait to make an early read pass: the immediate result above
    // remains the gate. A second observation after independent scalar work
    // distinguishes an unwritten result from a result still changing after
    // the backend claimed synchronous completion.
    std::vector<float> later(m * n);
    ggml_backend_tensor_get(d, later.data(), 0, later.size() * sizeof(float));
    size_t changed = 0, later_bad = 0;
    for (size_t i = 0; i < later.size(); ++i) {
        changed += later[i] != result[i];
        later_bad += !std::isfinite(later[i]) || std::abs(later[i]-reference[i]) > 1e-6;
    }
    const double reread_ms = std::chrono::duration<double, std::milli>(
        std::chrono::steady_clock::now() - start).count();
    std::printf("],\"reread_changed\":%zu,\"reread_bad\":%zu,\"reread_at_ms\":%.6f}\n",
                changed, later_bad, reread_ms); std::fflush(stdout);
    ggml_backend_buffer_free(buffer);
    ggml_free(ctx);
    return bad == 0 && later_bad == 0;
}

int main(int argc, char **argv) {
    try {
        require(argc == 2 && (!std::strcmp(argv[1], "cpu") ||
                             !std::strcmp(argv[1], "vulkan")), "cpu|vulkan required");
        const bool cpu = !std::strcmp(argv[1], "cpu");
        auto backend = cpu ? ggml_backend_cpu_init() : ggml_backend_vk_init(0);
        require(backend != nullptr, "backend unavailable");
        if (cpu) ggml_backend_cpu_set_n_threads(backend, 4);
        bool passed = true;
        for (int n : {18, 32, 64, 3}) {
            for (bool view : {false, true}) passed = run(backend, n, view) && passed;
        }
        ggml_backend_free(backend);
        std::printf("{\"matrix_pass\":%s}\n", passed ? "true" : "false");
        return passed ? 0 : 1;
    } catch (const std::exception &e) {
        std::fprintf(stderr, "%s\n", e.what());
        return 2;
    }
}
