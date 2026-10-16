#include "depthwise_single_step.h"
#include "ggml-backend.h"
#include "ggml-cpu.h"
#include "ggml-vulkan.h"
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <vector>
static void require(bool ok,const char *why){if(!ok)throw std::runtime_error(why);}
int main(int argc,char **argv){try{
    require(argc==2,"cpu|vulkan required");const bool cpu=!std::strcmp(argv[1],"cpu");
    require(cpu||!std::strcmp(argv[1],"vulkan"),"unknown backend");
    auto backend=cpu?ggml_backend_cpu_init():ggml_backend_vk_init(0);require(backend,"backend absent");
    if(cpu)ggml_backend_cpu_set_n_threads(backend,4);
    int index=0;bool all=true;
    for(int channels:{1,3,17,64,128,256,511,512}){
        const int kernel=32;
        auto ctx=ggml_init({4*1024*1024,nullptr,true});require(ctx,"context absent");
        auto *weight=ggml_new_tensor_3d(ctx,GGML_TYPE_F32,kernel,channels,channels);
        auto *input=ggml_new_tensor_3d(ctx,GGML_TYPE_F32,1,channels,1);
        auto *prepared=ggml_reshape_2d(ctx,ggml_cont(ctx,ggml_permute(ctx,weight,1,2,0,3)),channels,kernel*channels);
        auto *columns=ggml_mul_mat(ctx,prepared,ggml_cont(ctx,ggml_transpose(ctx,input)));
        // This ggml CPU backend has no COL2IM_1D implementation. For exactly
        // one frame there is no overlap/add: columns are already [tap,channel].
        // The Vulkan reference still executes the original production COL2IM.
        auto *reference=cpu ? ggml_reshape_3d(ctx,columns,kernel,channels,1)
                            : ggml_col2im_1d(ctx,columns,16,channels,0);
        auto *direct=aii_depthwise_single_step(ctx,weight,input,channels,kernel);
        auto *old_graph=ggml_new_graph(ctx),*new_graph=ggml_new_graph(ctx);
        ggml_build_forward_expand(old_graph,reference);ggml_build_forward_expand(new_graph,direct);
        auto buffer=ggml_backend_alloc_ctx_tensors(ctx,backend);require(buffer,"buffer absent");
        std::vector<float> dense(size_t(channels)*channels*kernel,0.f),signal(channels),old(channels*kernel),now(old.size()),again(old.size());
        size_t wrong=0,changed=0;float maximum=0;
        for(int r=0;r<3;++r){
            for(int c=0;c<channels;++c){signal[c]=float(c%31-15+r)/8.f;
                for(int k=0;k<kernel;++k)dense[(size_t(c)*channels+c)*kernel+k]=float((c*19+k*11+r)%97-48)/16.f;}
            ggml_backend_tensor_set(weight,dense.data(),0,dense.size()*4);ggml_backend_tensor_set(input,signal.data(),0,signal.size()*4);
            require(ggml_backend_graph_compute(backend,old_graph)==GGML_STATUS_SUCCESS,"reference compute refused");
            ggml_backend_tensor_get(reference,old.data(),0,old.size()*4);
            require(ggml_backend_graph_compute(backend,new_graph)==GGML_STATUS_SUCCESS,"direct compute refused");
            ggml_backend_tensor_get(direct,now.data(),0,now.size()*4);ggml_backend_tensor_get(direct,again.data(),0,again.size()*4);
            for(size_t j=0;j<now.size();++j){
                maximum=std::max(maximum,std::abs(old[j]-now[j]));
                wrong+=!std::isfinite(now[j])||old[j]!=now[j]; // numeric equality permits signed zero
                changed+=std::memcmp(&now[j],&again[j],4)!=0;
            }
        }
        bool refused=false;
        try{aii_depthwise_single_step(ctx,weight,input,channels,kernel-1);}catch(const std::runtime_error&){refused=true;}
        all=all&&wrong==0&&changed==0&&refused;
        std::printf("{\"case\":%d,\"channels\":%d,\"kernel\":32,\"wrong\":%zu,\"reread_changed\":%zu,\"maximum_absolute_error\":%.9g,\"invalid_refused\":%s}\n",index++,channels,wrong,changed,maximum,refused?"true":"false");std::fflush(stdout);
        ggml_backend_buffer_free(buffer);ggml_free(ctx);
    }
    ggml_backend_free(backend);std::printf("{\"depthwise_pass\":%s,\"cases\":8,\"iterations\":3}\n",all?"true":"false");return all?0:1;
}catch(const std::exception&e){std::fprintf(stderr,"%s\n",e.what());return 2;}}
