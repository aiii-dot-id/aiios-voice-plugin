#include "hearing.h"
#include <cstring>
#include <fstream>
#include <iostream>

namespace {
void read(std::istream& in,void* data,size_t count) {
  if(!in.read(static_cast<char*>(data),static_cast<std::streamsize>(count)))
    throw std::runtime_error("truncated capture trace");
}
uint32_t word(std::istream& in) {
  unsigned char b[4];read(in,b,4);
  return uint32_t(b[0])|uint32_t(b[1])<<8|uint32_t(b[2])<<16|uint32_t(b[3])<<24;
}
}
int main(int argc,char** argv) {
  try {
    if(argc!=3)throw std::invalid_argument("graph root and capture trace required");
    std::ifstream input(argv[2],std::ios::binary);char magic[8];read(input,magic,8);
    if(std::memcmp(magic,"AIIMTC01",8))throw std::runtime_error("capture trace format");
    aii::multitalker::Hearing hearing(argv[1]);
    uint32_t epoch=0,records=0,tokens=0,epochs=0;
    std::array<std::vector<uint32_t>,4> cumulative;
    while(input.peek()!=std::char_traits<char>::eof()) {
      const auto next=word(input),frames=word(input),valid=word(input),drop=word(input),final=word(input);
      if(!next || !frames || frames>1024 || final>1)throw std::runtime_error("capture trace extent");
      if(next!=epoch) {hearing.reset(next);epoch=next;++epochs;cumulative={};}
      std::vector<float> features(frames*128);
      for(auto& f:features){auto bits=word(input);std::memcpy(&f,&bits,4);}
      auto updates=hearing.push(epoch,features.data(),frames,valid,drop,final!=0);
      for(const auto& update:updates)for(const auto& token:update.tokens) {
        cumulative[update.track].push_back(static_cast<uint32_t>(token.id));++tokens;
      }
      // Expected tokens arrive only after complete native diarization and ASR.
      // No expected speaker activity or feature/encoder cache is consumed.
      for(size_t track=0;track<4;++track) {
        const auto count=word(input);
        if(count>65536 || count!=cumulative[track].size())
          throw std::runtime_error("capture token count mismatch at record "+std::to_string(records)+" track "+std::to_string(track)+" native "+std::to_string(cumulative[track].size())+" expected "+std::to_string(count));
        for(uint32_t i=0;i<count;++i)if(word(input)!=cumulative[track][i])
          throw std::runtime_error("capture token mismatch at record "+std::to_string(records));
      }
      ++records;
    }
    if(!records || !tokens)throw std::runtime_error("empty capture trace");
    std::cout<<"{\"passed\":true,\"records\":"<<records<<",\"tokens\":"<<tokens
             <<",\"epochs\":"<<epochs<<",\"native_diarization\":true,\"installed\":false}"<<std::endl;
    return 0;
  } catch(const std::exception& e){std::cerr<<e.what()<<std::endl;return 1;}
}
