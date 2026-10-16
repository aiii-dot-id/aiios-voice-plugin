#include "step_kv_batch.h"
#include "ggml-cpu.h"
#include "ggml-vulkan.h"
#include <cstdio>
#include <cstring>
#include <utility>
static void need(bool x,const char* s){if(!x)throw std::runtime_error(s);}
int main(int argc,char** argv){try{
    need(argc==2,"backend required");bool cpu=!std::strcmp(argv[1],"cpu");
    need(cpu||!std::strcmp(argv[1],"vulkan"),"unknown backend");
    auto b=cpu?ggml_backend_cpu_init():ggml_backend_vk_init(0);need(b,"backend absent");
    int index=0;
    for(auto shape:std::vector<std::pair<int,int>>{{1,1},{1,64},{6,1024},{12,1024},{6,4096},{12,65}}){
        const int layers=shape.first,n=shape.second,padding=17;
        auto ctx=ggml_init({4*1024*1024,nullptr,true});need(ctx,"context absent");
        std::vector<ggml_tensor*> src,keys,values,dst,kd,vd,checks;
        auto graph=ggml_new_graph(ctx);
        for(int i=0;i<2*layers;++i){
            auto s=ggml_new_tensor_1d(ctx,GGML_TYPE_F32,n);
            auto d=ggml_new_tensor_1d(ctx,GGML_TYPE_F32,n+2*padding);
            auto v=ggml_view_1d(ctx,d,n,padding*4);
            src.push_back(s);dst.push_back(d);
            (i%2?values:keys).push_back(s);(i%2?vd:kd).push_back(v);
            auto check=ggml_add(ctx,v,v);checks.push_back(check);ggml_build_forward_expand(graph,check);
        }
        auto buffer=ggml_backend_alloc_ctx_tensors(ctx,b);need(buffer,"buffer absent");
        size_t wrong=0,changed=0;
        for(int iteration=0;iteration<3;++iteration){
            std::vector<float> sentinels(n+2*padding,-1234.f),signal(n),read(sentinels.size()),again(read.size()),twice(n);
            for(int i=0;i<2*layers;++i){
                for(int j=0;j<n;++j)signal[j]=float(i*31+j%73+iteration*13)/8.f;
                ggml_backend_tensor_set(src[i],signal.data(),0,n*4);
                ggml_backend_tensor_set(dst[i],sentinels.data(),0,sentinels.size()*4);
            }
            aii_step_kv_batch(b,keys,values,kd,vd);
            // Immediately consume the destinations on the same backend.
            need(ggml_backend_graph_compute(b,graph)==GGML_STATUS_SUCCESS,"consumer failed");
            for(int i=0;i<2*layers;++i){
                ggml_backend_tensor_get(dst[i],read.data(),0,read.size()*4);
                ggml_backend_tensor_get(dst[i],again.data(),0,again.size()*4);
                ggml_backend_tensor_get(checks[i],twice.data(),0,n*4);
                ggml_backend_tensor_get(src[i],signal.data(),0,n*4);
                for(int j=0;j<n+2*padding;++j){
                    float wanted=(j<padding||j>=padding+n)?-1234.f:float(i*31+(j-padding)%73+iteration*13)/8.f;
                    wrong+=read[j]!=wanted;changed+=read[j]!=again[j];
                }
                for(int j=0;j<n;++j){float wanted=float(i*31+j%73+iteration*13)/8.f;
                    wrong+=twice[j]!=wanted*2 || signal[j]!=wanted;}
            }
        }
        bool refused=false;auto invalid=vd;invalid.back()=nullptr;
        try{aii_step_kv_batch(b,keys,values,kd,invalid);}catch(const std::runtime_error&){refused=true;}
        need(wrong==0&&changed==0&&refused,"batch output/storage/fence failure");
        std::printf("{\"case\":%d,\"layers\":%d,\"width\":%d,\"wrong\":%zu,\"reread_changed\":%zu,\"invalid_refused\":true}\n",index++,layers,n,wrong,changed);std::fflush(stdout);
        ggml_backend_buffer_free(buffer);ggml_free(ctx);
    }
    ggml_backend_free(b);std::puts("{\"batch_pass\":true,\"cases\":6,\"iterations\":3}");return 0;
}catch(const std::exception& e){std::fprintf(stderr,"%s\n",e.what());return 2;}}
