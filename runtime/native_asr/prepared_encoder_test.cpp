#include "prepared_encoder.h"
#include <cstring>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <functional>

using namespace aii::asr;
namespace fs = std::filesystem;
static void need(bool yes, const char* text) { if(!yes)throw std::runtime_error(text); }
static std::string vi(uint64_t n) {
  std::string s; do { s+=char((n&127)|(n>127?128:0));n>>=7; } while(n);return s;
}
static std::string i(int tag,uint64_t n) { return vi(unsigned(tag)*8)+vi(n); }
static std::string b(int tag,const std::string& s) { return vi(unsigned(tag)*8+2)+vi(s.size())+s; }
static std::string external(const char* name) {
  return i(1,2)+i(1,2)+i(2,1)+b(8,name)+i(14,1)+
    b(13,b(1,"location")+b(2,"weights.bin"))+
    b(13,b(1,"offset")+b(2,"0"))+b(13,b(1,"length")+b(2,"16"));
}
static void put(const fs::path& p,const std::string& s) {
  std::ofstream f(p,std::ios::binary);f.write(s.data(),std::streamsize(s.size()));need(bool(f),"fixture write");
}
static Pin pin(const std::string& s) {return {s.size(),aii::platform::sha256(s.data(),s.size())};}
static void refuses(const std::function<void()>& f,const char* message) {
  bool caught=false;try{f();}catch(const std::exception&){caught=true;}need(caught,message);
}
int main(int argc,char** argv) {
  try {
    need(argc==2,"fresh fixture path required");const fs::path root=argv[1];
    need(fs::create_directory(root),"fixture must be fresh");
    const float raw[]={1,2,3,4};const std::string weights(reinterpret_cast<const char*>(raw),sizeof raw);
    const auto shape=b(2,b(1,i(1,2))+b(1,i(1,2)));
    const auto output=b(1,"sum")+b(2,b(1,i(1,1)+shape));
    const auto node=b(1,"left")+b(1,"right")+b(2,"sum")+b(4,"Add");
    const auto graph=i(1,9)+b(7,b(1,node)+b(2,"verified_copy")+
      b(5,external("left"))+b(5,external("right"))+b(12,output))+b(8,i(2,18));
    const PreparedBinding binding{pin(graph),pin(weights)};
    put(root/"encoder.optimized.onnx",graph);put(root/"weights.bin",weights);
    need(!fs::exists(root/"model.safetensors")&&!fs::exists(root/"weight-view.json"),"source archive leaked into runtime fixture");
    Ort::Env env(ORT_LOGGING_LEVEL_WARNING,"prepared-binding-test");
    Ort::Session session{nullptr};
    {
      PreparedEncoder mapped(root,binding);
      Ort::SessionOptions options;options.SetIntraOpNumThreads(1);options.SetInterOpNumThreads(1);
      options.SetGraphOptimizationLevel(GraphOptimizationLevel::ORT_DISABLE_ALL);
      mapped.attach(options);
      session=Ort::Session(env,mapped.graph.data(),mapped.graph.size(),options);
      mapped.check_unchanged();
    }
    // Prove the initialized session owns its weights, not the retired maps or
    // later path contents. Files cannot be removed during mapped use on Windows.
    need(fs::remove(root/"weights.bin"),"retired mapping still owns the file");
    need(fs::remove(root/"encoder.optimized.onnx"),"retired graph still owns the file");
    const char* name="sum";
    const auto result=session.Run(Ort::RunOptions{nullptr},nullptr,nullptr,0,&name,1);
    const float expected[]={2,4,6,8};
    need(result.size()==1&&result[0].GetTensorTypeAndShapeInfo().GetShape()==std::vector<int64_t>({2,2}),"wrong output geometry");
    need(!std::memcmp(result[0].GetTensorData<float>(),expected,sizeof expected),"initialized session lost copied weights");
    put(root/"encoder.optimized.onnx",graph);put(root/"weights.bin",weights);
    auto corrupt=weights;corrupt[0]^=1;put(root/"weights.bin",corrupt);
    refuses([&]{PreparedEncoder m(root,binding);},"corrupt prepared weights accepted");
    put(root/"weights.bin",weights);corrupt=graph;corrupt.back()^=1;put(root/"encoder.optimized.onnx",corrupt);
    refuses([&]{PreparedEncoder m(root,binding);},"corrupt prepared graph accepted");
    put(root/"encoder.optimized.onnx",graph);put(root/"weights.bin",weights+"x");
    refuses([&]{PreparedEncoder m(root,binding);},"appended weight bytes accepted");
    fs::remove(root/"weights.bin");
    refuses([&]{PreparedEncoder m(root,binding);},"missing weights accepted");
    std::cout<<"prepared artifact: no original files; exact inference after mappings retire; corrupt/missing/appended bytes refused\n";
    return 0;
  }catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}
}
