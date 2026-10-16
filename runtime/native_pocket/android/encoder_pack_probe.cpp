// Compare the exact production preparation against the original ggml graph.
#include "encoder_weight_pack.h"
#include "ggml.h"
#include "ggml-backend.h"
#include "ggml-cpu.h"
#include "ggml-vulkan.h"
#include <array>
#include <cstdio>
#include <cstring>
#include <vector>
static void require(bool ok,const char *why){if(!ok)throw std::runtime_error(why);}
int main(int argc,char **argv){try{
    require(argc==2,"cpu|vulkan required");
    const bool cpu=!std::strcmp(argv[1],"cpu");
    require(cpu||!std::strcmp(argv[1],"vulkan"),"unknown backend");
    auto backend=cpu?ggml_backend_cpu_init():ggml_backend_vk_init(0);require(backend,"backend absent");
    if(cpu)ggml_backend_cpu_set_n_threads(backend,4);
    int index=0;bool all=true;
    for(auto shape:std::vector<std::array<size_t,3>>{{1,1,1},{3,5,7},{1,8,32},{8,1,32},{512,512,32},{512,256,12},{256,128,10},{128,64,8}}){
        const size_t ci=shape[0],co=shape[1],k=shape[2],n=ci*co*k;
        auto ctx=ggml_init({4*1024*1024,nullptr,true});require(ctx,"context absent");
        auto *weight=ggml_new_tensor_3d(ctx,GGML_TYPE_F32,k,co,ci);
        auto *old=ggml_cont(ctx,ggml_permute(ctx,weight,1,2,0,3));
        auto *graph=ggml_new_graph(ctx);ggml_build_forward_expand(graph,old);
        auto buffer=ggml_backend_alloc_ctx_tensors(ctx,backend);require(buffer,"buffer absent");
        size_t wrong=0,changed=0,source_changed=0;
        for(int repeat=0;repeat<3;++repeat){
            std::vector<float> input(n),got(n),again(n),source_after(n);
            for(size_t j=0;j<n;++j){
                uint32_t bits=0x3f000000u|uint32_t((j*271+repeat*821)%0x7fffff);
                if(j%29==0)bits=0x80000000u; // preserve signed zero as well
                std::memcpy(&input[j],&bits,4);
            }
            const auto packed=aii_voice::pack_encoder_weights(input,ci,co,k);
            ggml_backend_tensor_set(weight,input.data(),0,n*4);
            require(ggml_backend_graph_compute(backend,graph)==GGML_STATUS_SUCCESS,"compute refused");
            ggml_backend_tensor_get(old,got.data(),0,n*4);
            ggml_backend_tensor_get(old,again.data(),0,n*4);
            ggml_backend_tensor_get(weight,source_after.data(),0,n*4);
            for(size_t j=0;j<n;++j){
                wrong+=std::memcmp(&packed[j],&got[j],4)!=0;
                changed+=std::memcmp(&got[j],&again[j],4)!=0;
                source_changed+=std::memcmp(&input[j],&source_after[j],4)!=0;
            }
        }
        all=all&&wrong==0&&changed==0&&source_changed==0;
        std::printf("{\"case\":%d,\"shape\":[%zu,%zu,%zu],\"elements\":%zu,\"wrong\":%zu,\"reread_changed\":%zu,\"source_changed\":%zu}\n",index++,ci,co,k,n,wrong,changed,source_changed);std::fflush(stdout);
        ggml_backend_buffer_free(buffer);ggml_free(ctx);
    }
    ggml_backend_free(backend);
    std::printf("{\"layout_pass\":%s,\"cases\":8,\"iterations\":3}\n",all?"true":"false");return all?0:1;
}catch(const std::exception&e){std::fprintf(stderr,"%s\n",e.what());return 2;}}
