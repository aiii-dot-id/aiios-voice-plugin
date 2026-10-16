// Attention layout proof: exact CPU oracle, permuted singleton, real strided
// controls, batch broadcasting and immediate/double readback. No model loading.
#include "ggml.h"
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

static void require(bool b,const char *s) { if(!b) throw std::runtime_error(s); }
struct Case { int k,m,n,ab,bb; bool permuted_a,permuted_b,view; bool gapped=false; };
static size_t index(const ggml_tensor *t,int k,int row,int batch) {
    return (k*t->nb[0]+row*t->nb[1]+batch*t->nb[2]+t->view_offs)/sizeof(float);
}
static bool run(ggml_backend_t backend,Case c,int id) {
    auto *ctx=ggml_init({8*1024*1024,nullptr,true});require(ctx,"context unavailable");
    auto *a_base=ggml_new_tensor_3d(ctx,GGML_TYPE_F32,c.k,c.permuted_a?c.ab:c.m,c.permuted_a?c.m:c.ab);
    auto *a=c.permuted_a?ggml_permute(ctx,a_base,0,2,1,3):a_base;
    require(!c.gapped || (c.permuted_b&&!c.view),"unsupported gapped fixture");
    auto *b_base=ggml_new_tensor_3d(ctx,GGML_TYPE_F32,c.k,c.permuted_b?c.bb*(c.gapped?2:1):c.n,(c.permuted_b?c.n:c.bb)+(c.view?2:0));
    auto *b_view=c.view?ggml_view_3d(ctx,b_base,c.k,c.permuted_b?c.bb:c.n,c.permuted_b?c.n:c.bb,
        b_base->nb[1],b_base->nb[2],2*b_base->nb[2]):b_base;
    if(c.gapped)b_view=ggml_view_3d(ctx,b_base,c.k,c.bb,c.n,2*b_base->nb[1],b_base->nb[2],0);
    auto *b=c.permuted_b?ggml_permute(ctx,b_view,0,2,1,3):b_view;
    require(a->ne[0]==c.k&&a->ne[1]==c.m&&a->ne[2]==c.ab,"A shape mismatch");
    require(b->ne[0]==c.k&&b->ne[1]==c.n&&b->ne[2]==c.bb,"B shape mismatch");
    auto *d=ggml_mul_mat(ctx,a,b);ggml_mul_mat_set_prec(d,GGML_PREC_F32);
    auto *graph=ggml_new_graph(ctx);ggml_build_forward_expand(graph,d);
    auto buffer=ggml_backend_alloc_ctx_tensors(ctx,backend);require(buffer,"allocation unavailable");
    std::vector<float> av(ggml_nelements(a_base)),bv(ggml_nelements(b_base));
    std::vector<float> ref(ggml_nelements(d)),got(ref.size()),later(ref.size());
    for(size_t i=0;i<av.size();++i)av[i]=(int(i%31)-15)/32.f;
    for(size_t i=0;i<bv.size();++i)bv[i]=(int(i%17)-8)/16.f;
    for(int batch=0;batch<c.bb;++batch)for(int col=0;col<c.n;++col)for(int row=0;row<c.m;++row) {
        double sum=0;
        for(int k=0;k<c.k;++k) {
            const auto ai=index(a,k,row,batch/(c.bb/c.ab)),bi=index(b,k,col,batch);
            require(ai<av.size()&&bi<bv.size(),"oracle index out of bounds");
            sum+=double(av[ai])*bv[bi];
        }
        ref[(size_t(batch)*c.n+col)*c.m+row]=float(sum);
    }
    ggml_backend_tensor_set(a_base,av.data(),0,av.size()*4);ggml_backend_tensor_set(b_base,bv.data(),0,bv.size()*4);
    size_t bad=0,unwritten=0,changed=0;double worst=0;std::vector<double> clocks;
    for(int iteration=0;iteration<8;++iteration) {
        std::fill(got.begin(),got.end(),1234.f);ggml_backend_tensor_set(d,got.data(),0,got.size()*4);
        const auto start=std::chrono::steady_clock::now();
        require(ggml_backend_graph_compute(backend,graph)==GGML_STATUS_SUCCESS,"compute failed");
        ggml_backend_tensor_get(d,got.data(),0,got.size()*4);
        clocks.push_back(std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-start).count());
        for(size_t i=0;i<got.size();++i) {
            const double e=std::isfinite(got[i])?std::abs(double(got[i])-ref[i]):1e30;
            bad+=e>1e-6;unwritten+=got[i]==1234.f;worst=std::max(worst,e);
        }
        ggml_backend_tensor_get(d,later.data(),0,later.size()*4);
        for(size_t i=0;i<got.size();++i)changed+=got[i]!=later[i];
    }
    std::printf("{\"case\":%d,\"k\":%d,\"m\":%d,\"n\":%d,\"a_batches\":%d,\"b_batches\":%d,\"a_permuted\":%s,\"b_permuted\":%s,\"view\":%s,\"b_contiguous\":%s,\"b_strides\":[%zu,%zu,%zu,%zu],\"bad\":%zu,\"unwritten\":%zu,\"reread_changed\":%zu,\"max_error\":%.9g,\"wall_ms\":[",id,c.k,c.m,c.n,c.ab,c.bb,c.permuted_a?"true":"false",c.permuted_b?"true":"false",c.view?"true":"false",ggml_is_contiguous(b)?"true":"false",b->nb[0],b->nb[1],b->nb[2],b->nb[3],bad,unwritten,changed,worst);
    for(size_t i=0;i<clocks.size();++i)std::printf("%s%.9g",i?",":"",clocks[i]);
    std::printf("]}\n");std::fflush(stdout);
    ggml_backend_buffer_free(buffer);ggml_free(ctx);return !bad&&!unwritten&&!changed;
}
int main(int argc,char**argv) {
    try {
        require(argc==2&&(!std::strcmp(argv[1],"cpu")||!std::strcmp(argv[1],"vulkan")),"cpu|vulkan required");
        bool cpu=!std::strcmp(argv[1],"cpu");auto backend=cpu?ggml_backend_cpu_init():ggml_backend_vk_init(0);
        require(backend,"required backend unavailable");if(cpu)ggml_backend_cpu_set_n_threads(backend,4);
        std::vector<Case> cases;
        for(int n:{1,2,50})for(bool perm:{false,true})cases.push_back({64,n==50?176:1677,n,16,16,false,perm,false});
        cases.insert(cases.end(),{{64,1677,1,16,16,false,true,true},{33,35,1,2,4,false,true,true},
            {64,176,1,16,16,true,true,false},{64,176,2,16,16,true,true,false},
            {1024,1024,1,1,1,false,false,false},{64,1677,1,1,16,false,true,false},
            {64,1677,1,16,16,false,true,false,true}});
        bool passed=true;int id=0;for(auto c:cases)passed=run(backend,c,id++)&&passed;
        ggml_backend_free(backend);std::printf("{\"matrix_pass\":%s,\"cases\":%d}\n",passed?"true":"false",id);
        return passed?0:1;
    } catch(const std::exception&e) {std::fprintf(stderr,"%s\n",e.what());return 2;}
}
