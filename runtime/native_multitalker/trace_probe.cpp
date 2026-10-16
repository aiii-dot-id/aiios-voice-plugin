#include "onnx_backend.h"
#include <algorithm>
#include <cstring>
#include <fstream>
#include <iostream>

namespace {
void read(std::istream& in, void* data, size_t bytes) {
  if (!in.read(static_cast<char*>(data), static_cast<std::streamsize>(bytes)))
    throw std::runtime_error("truncated decoder trace");
}
uint32_t word(std::istream& in) {
  unsigned char data[4]; read(in,data,4);
  return uint32_t(data[0]) | uint32_t(data[1])<<8 | uint32_t(data[2])<<16 | uint32_t(data[3])<<24;
}
}
int main(int argc, char** argv) {
  try {
    if (argc != 3 && argc != 4) throw std::invalid_argument("graph root and recorded trace required");
    const std::string mutation=argc==4 ? argv[3] : "";
    if (!mutation.empty() && mutation!="--unconditioned" && mutation!="--shared-encoder-cache")
      throw std::invalid_argument("unknown trace falsifier");
    std::ifstream trace(argv[2],std::ios::binary);
    char magic[8]; read(trace,magic,8);
    const bool conditioned=std::memcmp(magic,"AIIMTR02",8)==0;
    if (!conditioned && std::memcmp(magic,"AIIMTR01",8)) throw std::runtime_error("trace format");
    aii::multitalker::OnnxBackend backend(argv[1]);
    aii::multitalker::Decoder decoder(backend);
    std::unique_ptr<aii::multitalker::OnnxEncoder> encoder;
    if(conditioned)encoder=std::make_unique<aii::multitalker::OnnxEncoder>(argv[1]);
    uint32_t current = 0, records = 0, tokens = 0, epochs = 0;
    std::array<std::vector<int64_t>,4> cumulative;
    while (trace.peek() != std::char_traits<char>::eof()) {
      const auto epoch=word(trace), track=word(trace), first=word(trace), frames=word(trace);
      if (!epoch || track>=4 || !frames || frames>128) throw std::runtime_error("trace extent");
      if (epoch != current) {
        decoder.reset(epoch); cumulative={}; current=epoch; ++epochs;
        if(encoder)encoder->reset(epoch);
      }
      uint32_t valid=frames,final_chunk=0;
      if(conditioned) { valid=word(trace); final_chunk=word(trace); if(final_chunk>1)throw std::runtime_error("final flag"); }
      std::vector<float> encoded(frames*aii::multitalker::encoder_width);
      for (auto& value : encoded) { auto bits=word(trace); std::memcpy(&value,&bits,4); }
      if(conditioned) {
        std::vector<float> foreground(frames),background(frames);
        for(auto* target : {&foreground,&background})
          for(auto& value:*target) { auto bits=word(trace);std::memcpy(&value,&bits,4); }
        if(mutation=="--unconditioned") {
          std::fill(foreground.begin(),foreground.end(),1.f);
          std::fill(background.begin(),background.end(),0.f);
        }
        encoded=encoder->push(epoch,mutation=="--shared-encoder-cache" ? 0 : track,
                              encoded.data(),frames,valid,foreground.data(),background.data(),final_chunk!=0);
      }
      // References are read only after inference; the decoder sees no words,
      // expected tokens, enrollment names, or future records.
      const auto output=decoder.push(epoch,track,first,encoded.data(),encoded.size()/aii::multitalker::encoder_width);
      for (const auto& token : output) cumulative[track].push_back(token.id);
      tokens += static_cast<uint32_t>(output.size());
      const auto count=word(trace);
      if (count>65536 || count!=cumulative[track].size())
        throw std::runtime_error("token count mismatch at record "+std::to_string(records));
      for (uint32_t i=0;i<count;++i)
        if (word(trace)!=static_cast<uint64_t>(cumulative[track][i]))
          throw std::runtime_error("token mismatch at record "+std::to_string(records));
      ++records;
    }
    if (!records || !tokens) throw std::runtime_error("empty trace cannot pass");
    std::cout << "{\"passed\":true,\"records\":" << records << ",\"tokens\":" << tokens
              << ",\"epochs\":" << epochs << ",\"conditioned_encoder\":" << (conditioned?"true":"false")
              << ",\"installed\":false}" << std::endl;
    return 0;
  } catch (const std::exception& e) { std::cerr << e.what() << std::endl; return 1; }
}
