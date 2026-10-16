// File adapter for public, hash-bound numerical fixtures; never live audio.
#include "length_pcm_frontend.h"
#include <fstream>
#include <iostream>
std::vector<float> read(const char* path,size_t maximum) {
    std::ifstream in(path,std::ios::binary|std::ios::ate);
    if(!in)throw std::runtime_error("fixture unavailable");
    const auto n=in.tellg();
    if(n<=0 || n%4 || size_t(n)>maximum*4)throw std::runtime_error("fixture extent");
    std::vector<float> data(size_t(n)/4);in.seekg(0);in.read(reinterpret_cast<char*>(data.data()),n);
    if(!in)throw std::runtime_error("fixture incomplete");return data;
}
int main(int argc,char** argv) {try {
    if(argc!=4)throw std::runtime_error("mel.f32 pcm.f32 output.f32 required");
    aii::voice::pixel::LengthPcmFrontend front(read(argv[1],128*257));
    auto pcm=read(argv[2],320000);auto features=front.process(pcm);
    std::ofstream out(argv[3],std::ios::binary);out.write(reinterpret_cast<const char*>(features.data()),features.size()*4);
    if(!out)throw std::runtime_error("feature write failed");
    std::cout<<"{\"valid_frames\":"<<pcm.size()/160<<",\"features\":"<<features.size()<<"}\n";
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
