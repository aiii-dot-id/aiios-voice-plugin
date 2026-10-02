#include "recognizer.h"
#include <fstream>
#include <iostream>
#include <stdexcept>

namespace {
void check(bool value,const char* why){if(!value)throw std::runtime_error(why);}
std::vector<float> floats(const char* path,size_t limit) {
  std::ifstream f(path,std::ios::binary|std::ios::ate);const auto n=f.tellg();
  check(f&&n>0&&n%4==0&&uint64_t(n)<=limit*4,"float input extent");
  std::vector<float> out(size_t(n)/4);f.seekg(0);
  check(bool(f.read(reinterpret_cast<char*>(out.data()),n)),"float input read");return out;
}
std::vector<std::string> vocabulary(const char* path) {
  // Exactly 1024 length-prefixed UTF-8 strings (uint32 little endian).
  // The driver projects the model's bound tokens.json without substitutions.
  std::ifstream f(path,std::ios::binary);std::vector<std::string> out;
  for(size_t i=0;i<1024;++i) {
    unsigned char bytes[4]{};check(bool(f.read(reinterpret_cast<char*>(bytes),4)),"token length read");
    const auto n=uint32_t(bytes[0])|(uint32_t(bytes[1])<<8)|(uint32_t(bytes[2])<<16)|(uint32_t(bytes[3])<<24);
    check(n<=1024,"token length bound");std::string word(n,'\0');
    check(bool(f.read(word.data(),n)),"token read");out.push_back(std::move(word));
  }
  check(f.peek()==std::char_traits<char>::eof(),"trailing vocabulary bytes");return out;
}
}
int main(int argc,char** argv){try{
  check(argc>=7,"graph root, mel, framed tokens, Nemotron model, GPU and float recordings required");
  const auto mel=floats(argv[2],128*257);
  const auto tokens=vocabulary(argv[3]);
  aii::multitalker::NemotronConfig config{argv[4],std::stoi(argv[5])};
  const int first=std::string(argv[6])=="--refine-evidence"?7:6;
  config.refine_evidence=first==7;check(argc>first,"float recordings required");
  aii::multitalker::Recognizer recognizer(argv[1],mel.data(),mel.size(),tokens,config);
  for(int i=first;i<argc;++i) {
    const auto pcm=floats(argv[i],16000*60);recognizer.open();recognizer.begin();
    for(size_t offset=0;offset<pcm.size();offset+=997)
      recognizer.push(pcm.data()+offset,std::min(size_t(997),pcm.size()-offset));
    recognizer.finish();const auto rows=recognizer.segments();
    std::cout<<"{\"case\":"<<i-first<<",\"input_samples\":"<<pcm.size()<<",\"segments\":[";
    for(size_t j=0;j<rows.size();++j) {
      const auto& row=rows[j];if(j)std::cout<<',';
      std::cout<<"{\"track\":\""<<row.track<<"\",\"text_bytes\":"<<row.text.size()
        <<",\"start\":"<<row.start<<",\"end\":"<<row.end
        <<",\"identity_samples\":"<<row.evidence.size()
        <<",\"reason\":\""<<row.evidence_unavailable<<"\"}";
    }
    std::cout<<"]}"<<std::endl;recognizer.reset();
  }
  return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
