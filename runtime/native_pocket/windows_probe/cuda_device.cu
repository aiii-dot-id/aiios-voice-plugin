// Hardware execution prerequisite; this does not claim speech qualification.
#include <cuda_runtime.h>
#include <cmath>
#include <cstdio>
#include <stdexcept>
#include <vector>
static void ck(cudaError_t e) {if(e!=cudaSuccess)throw std::runtime_error(cudaGetErrorString(e));}
__global__ void value(float *out,int n,int offset) {
  const int i=int(blockIdx.x*blockDim.x+threadIdx.x);
  if(i<n)out[i]=float(3*i+offset);
}
int main() {
  try {
    int runtime=0,driver=0;ck(cudaRuntimeGetVersion(&runtime));ck(cudaDriverGetVersion(&driver));
    cudaDeviceProp p{};ck(cudaGetDeviceProperties(&p,0));
    if(p.major!=6||p.minor!=1)throw std::runtime_error("unexpected GPU architecture");
    ck(cudaSetDevice(0));cudaStream_t stream;ck(cudaStreamCreate(&stream));
    constexpr int n=4096;float *gpu=nullptr;ck(cudaMalloc(&gpu,n*sizeof(float)));
    std::vector<float> cpu(n,-1);
    value<<<16,256,0,stream>>>(gpu,n,7);ck(cudaGetLastError());ck(cudaStreamSynchronize(stream));
    ck(cudaMemcpy(cpu.data(),gpu,n*sizeof(float),cudaMemcpyDeviceToHost));
    for(int i=0;i<n;++i)if(cpu[i]!=float(3*i+7))throw std::runtime_error("direct kernel output differs");
    cudaGraph_t graph;cudaGraphExec_t executable;
    ck(cudaStreamBeginCapture(stream,cudaStreamCaptureModeThreadLocal));
    value<<<16,256,0,stream>>>(gpu,n,19);ck(cudaGetLastError());ck(cudaStreamEndCapture(stream,&graph));
    ck(cudaGraphInstantiate(&executable,graph,nullptr,nullptr,0));
    for(int i=0;i<3;++i)ck(cudaGraphLaunch(executable,stream));
    ck(cudaStreamSynchronize(stream));ck(cudaMemcpy(cpu.data(),gpu,n*sizeof(float),cudaMemcpyDeviceToHost));
    for(int i=0;i<n;++i)if(cpu[i]!=float(3*i+19))throw std::runtime_error("captured kernel output differs");
    ck(cudaGraphExecDestroy(executable));ck(cudaGraphDestroy(graph));ck(cudaFree(gpu));ck(cudaStreamDestroy(stream));
    std::printf("{\"passed\":true,\"device\":\"%s\",\"major\":%d,\"minor\":%d,\"runtime\":%d,\"driver\":%d,\"direct_values\":4096,\"captured_values\":4096,\"graph_launches\":3,\"speech_qualified\":false}\n",p.name,p.major,p.minor,runtime,driver);
    return 0;
  } catch(const std::exception& e) {std::fprintf(stderr,"CUDA device proof: %s\n",e.what());return 1;}
}
