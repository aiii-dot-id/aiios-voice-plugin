// Immediate-read, tail, view and batch proof for the isolated N=1 shader.
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

static void require(bool b, const char *s) { if (!b) throw std::runtime_error(s); }
struct Case { int k, m, n, ab, bb; bool view; };
static bool run(ggml_backend_t backend, const Case c, int id) {
    const size_t plane = size_t(c.k)*c.m;
    const size_t offset = c.view ? 2*plane : 0;
    const int planes = c.ab + (c.view ? 2 : 0);
    auto *ctx = ggml_init({8*1024*1024, nullptr, true});
    require(ctx != nullptr, "context unavailable");
    auto *base = ggml_new_tensor_3d(ctx, GGML_TYPE_F32, c.k, c.m, planes);
    auto *a = c.view ? ggml_view_3d(ctx, base, c.k, c.m, c.ab,
        size_t(c.k)*sizeof(float), plane*sizeof(float), offset*sizeof(float)) : base;
    auto *b = ggml_new_tensor_3d(ctx, GGML_TYPE_F32, c.k, c.n, c.bb);
    auto *d = ggml_mul_mat(ctx, a, b);
    ggml_mul_mat_set_prec(d, GGML_PREC_F32);
    ggml_set_name(d, "narrow_matrix_gate");
    auto *graph = ggml_new_graph(ctx); ggml_build_forward_expand(graph, d);
    auto buffer = ggml_backend_alloc_ctx_tensors(ctx, backend);
    require(buffer != nullptr, "buffer unavailable");
    std::vector<float> av(plane*planes), bv(size_t(c.k)*c.n*c.bb);
    std::vector<float> reference(size_t(c.m)*c.n*c.bb), got(reference.size()), later(reference.size());
    for (size_t i=0; i<av.size(); ++i) av[i] = (int(i%31)-15)/32.f;
    for (size_t i=0; i<bv.size(); ++i) bv[i] = (int(i%17)-8)/16.f;
    for (int batch=0; batch<c.bb; ++batch)
        for (int col=0; col<c.n; ++col)
            for (int row=0; row<c.m; ++row) {
                double sum=0;
                const size_t ai = offset + size_t(batch/(c.bb/c.ab))*plane + size_t(row)*c.k;
                const size_t bi = (size_t(batch)*c.n+col)*c.k;
                for (int k=0; k<c.k; ++k) sum += double(av[ai+k])*bv[bi+k];
                reference[(size_t(batch)*c.n+col)*c.m+row] = float(sum);
            }
    ggml_backend_tensor_set(base, av.data(), 0, av.size()*sizeof(float));
    ggml_backend_tensor_set(b, bv.data(), 0, bv.size()*sizeof(float));
    size_t bad=0, changed=0, unwritten=0;
    double max_error=0;
    std::vector<double> clocks;
    for (int iteration=0; iteration<8; ++iteration) {
        std::fill(got.begin(), got.end(), 1234.f);
        ggml_backend_tensor_set(d, got.data(), 0, got.size()*sizeof(float));
        const auto start=std::chrono::steady_clock::now();
        require(ggml_backend_graph_compute(backend, graph)==GGML_STATUS_SUCCESS, "compute failed");
        ggml_backend_tensor_get(d, got.data(), 0, got.size()*sizeof(float));
        clocks.push_back(std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-start).count());
        for (size_t i=0; i<got.size(); ++i) {
            double error=std::isfinite(got[i]) ? std::abs(double(got[i])-reference[i]) : 1e30;
            bad += error>1e-6; unwritten += got[i]==1234.f;
            max_error=std::max(max_error,error);
        }
        ggml_backend_tensor_get(d, later.data(), 0, later.size()*sizeof(float));
        for (size_t i=0; i<got.size(); ++i) changed += later[i]!=got[i];
    }
    std::printf("{\"case\":%d,\"k\":%d,\"m\":%d,\"n\":%d,\"a_batches\":%d,\"b_batches\":%d,\"view\":%s,\"elements\":%zu,\"bad\":%zu,\"unwritten\":%zu,\"reread_changed\":%zu,\"max_error\":%.9g,\"wall_ms\":[",
        id,c.k,c.m,c.n,c.ab,c.bb,c.view?"true":"false",got.size(),bad,unwritten,changed,max_error);
    for (size_t i=0; i<clocks.size(); ++i) std::printf("%s%.9g",i?",":"",clocks[i]);
    std::printf("]}\n"); std::fflush(stdout);
    ggml_backend_buffer_free(buffer); ggml_free(ctx);
    return bad==0 && unwritten==0 && changed==0;
}
int main(int argc,char **argv) {
    try {
        require(argc==2 && (!std::strcmp(argv[1],"cpu") || !std::strcmp(argv[1],"vulkan")), "cpu|vulkan required");
        const bool cpu=!std::strcmp(argv[1],"cpu");
        auto backend=cpu?ggml_backend_cpu_init():ggml_backend_vk_init(0);
        require(backend!=nullptr,"required backend unavailable");
        if(cpu) ggml_backend_cpu_set_n_threads(backend,4);
        std::vector<Case> cases;
        for(int n:{1,3,18,32,64}) for(bool view:{false,true}) cases.push_back({1024,1024,n,1,1,view});
        cases.insert(cases.end(),{{1031,1033,1,1,1,false},{33,35,1,1,1,true},
            {4096,256,1,1,1,false},{1024,1024,1,1,3,false},{33,35,1,2,4,true}});
        bool passed=true; int id=0;
        for(const auto c:cases) passed=run(backend,c,id++) && passed;
        ggml_backend_free(backend);
        std::printf("{\"matrix_pass\":%s,\"cases\":%d}\n",passed?"true":"false",id);
        return passed?0:1;
    } catch(const std::exception &e) { std::fprintf(stderr,"%s\n",e.what()); return 2; }
}
