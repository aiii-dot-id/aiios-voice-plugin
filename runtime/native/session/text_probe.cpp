// Test-only length-framed partition output; this is not the engine transport.
#include "text.h"
#include <iostream>
#include <iterator>
#include <stdexcept>
int main(int argc,char** argv) {
  try {
    const auto limit=argc==2?std::stoul(argv[1]):180;
    std::string input;
    char c;
    while(std::cin.get(c)) {
      if(input.size()>=32001)throw std::invalid_argument("test input byte bound");
      input+=c;
    }
    for(const auto& part:aii::voice::split_text(input,limit)) {
      const auto n=part.size();
      for(int shift=24;shift>=0;shift-=8)std::cout.put(static_cast<char>((n>>shift)&255));
      std::cout.write(part.data(),part.size());
    }
    if(!std::cout)throw std::runtime_error("test output refused");
  } catch(const std::exception& e) {std::cerr<<e.what()<<'\n';return 1;}
}
