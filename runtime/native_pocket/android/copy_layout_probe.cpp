// Independent exact scalar address oracle for F32 copies, including padding.
#include "ggml.h"
#include "ggml-backend.h"
#include "ggml-cpu.h"
#include "ggml-vulkan.h"
#include <array>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <stdexcept>
#include <vector>

static void require(bool ok,const char *s){if(!ok)throw std::runtime_error(s);}
struct Layout {std::array<int64_t,4> n; size_t gap,offset; bool transpose;};
struct Tensor {ggml_tensor *base,*view;};
static Tensor make(ggml_context *ctx,Layout l){
    size_t b1=l.n[0]+l.gap,b2=b1*l.n[1]+l.gap,b3=b2*l.n[2]+l.gap;
    auto *base=ggml_new_tensor_1d(ctx,GGML_TYPE_F32,b3*l.n[3]+l.offset+17);
    auto *view=ggml_view_4d(ctx,base,l.n[0],l.n[1],l.n[2],l.n[3],b1*4,b2*4,b3*4,l.offset*4);
    if(l.transpose)view=ggml_transpose(ctx,view);
    return {base,view};
}
static size_t at(ggml_tensor *v,size_t index){
    size_t offset=v->view_offs;
    for(int d=0;d<4;++d){offset+=(index%v->ne[d])*v->nb[d]; index/=v->ne[d];}
    require(index==0&&offset%4==0,"oracle index invalid");return offset/4;
}
static bool run(ggml_backend_t backend,Layout sl,Layout dl,int id){
    auto *ctx=ggml_init({4*1024*1024,nullptr,true});require(ctx,"context unavailable");
    auto s=make(ctx,sl),d=make(ctx,dl);require(ggml_nelements(s.view)==ggml_nelements(d.view),"shape mismatch");
    auto *node=ggml_cpy(ctx,s.view,d.view);auto *graph=ggml_new_graph(ctx);ggml_build_forward_expand(graph,node);
    auto buf=ggml_backend_alloc_ctx_tensors(ctx,backend);require(buf,"allocation unavailable");
    std::vector<float> source(ggml_nelements(s.base)),expected(ggml_nelements(d.base)),got(expected.size()),later(expected.size());
    size_t bad=0,changed=0;std::vector<double> clocks;
    for(int iteration=0;iteration<8;++iteration){
        for(size_t i=0;i<source.size();++i)source[i]=float(int(i%8191)-4095+iteration)/16.f;
        std::fill(expected.begin(),expected.end(),-12345.f);ggml_backend_tensor_set(d.base,expected.data(),0,expected.size()*4);
        for(size_t i=0;i<size_t(ggml_nelements(s.view));++i){
            size_t si=at(s.view,i),di=at(d.view,i);require(si<source.size()&&di<expected.size(),"oracle bounds");expected[di]=source[si];
        }
        ggml_backend_tensor_set(s.base,source.data(),0,source.size()*4);
        const auto start=std::chrono::steady_clock::now();
        require(ggml_backend_graph_compute(backend,graph)==GGML_STATUS_SUCCESS,"compute failed");
        ggml_backend_tensor_get(d.base,got.data(),0,got.size()*4);
        clocks.push_back(std::chrono::duration<double,std::milli>(std::chrono::steady_clock::now()-start).count());
        for(size_t i=0;i<got.size();++i)bad+=got[i]!=expected[i];
        ggml_backend_tensor_get(d.base,later.data(),0,later.size()*4);
        for(size_t i=0;i<got.size();++i)changed+=got[i]!=later[i];
    }
    std::printf("{\"case\":%d,\"elements\":%lld,\"bad_including_guards\":%zu,\"reread_changed\":%zu,\"wall_ms\":[",id,(long long)ggml_nelements(s.view),bad,changed);
    for(size_t i=0;i<clocks.size();++i)std::printf("%s%.9g",i?",":"",clocks[i]);std::printf("]}\n");std::fflush(stdout);
    ggml_backend_buffer_free(buf);ggml_free(ctx);return !bad&&!changed;
}
int main(int argc,char **argv){try{
    require(argc==2&&(!std::strcmp(argv[1],"cpu")||!std::strcmp(argv[1],"vulkan")),"cpu|vulkan required");
    bool cpu=!std::strcmp(argv[1],"cpu");auto b=cpu?ggml_backend_cpu_init():ggml_backend_vk_init(0);require(b,"backend unavailable");
    if(cpu)ggml_backend_cpu_set_n_threads(b,4);
    bool ok=true;int id=0;
    for(int n:{1,63,64,65,511,512,513,262145}){
        ok=run(b,{{n,1,1,1},0,1,false},{{n,1,1,1},0,7,false},id++)&&ok;
    }
    for(bool trans:{false,true})for(size_t gap:{0u,3u})for(bool destTrans:{false,true}){
        ok=run(b,{{33,17,3,2},gap,1,trans},{{17,33,2,3},gap,7,destTrans},id++)&&ok;
    }
    for(int n:{1,64,513})for(size_t gap:{0u,5u}){
        ok=run(b,{{n,1,16,1},gap,3,true},{{n,16,1,1},0,1,false},id++)&&ok;
    }
    ggml_backend_free(b);std::printf("{\"copy_pass\":%s,\"cases\":%d,\"iterations\":8}\n",ok?"true":"false",id);return ok?0:1;
}catch(const std::exception&e){std::fprintf(stderr,"%s\n",e.what());return 2;}}
