#include "../native/session/echo_input.h"
#include <iostream>
#include <algorithm>
#include <stdexcept>

int main() {
  for(size_t length=1;length<=160*3+159;length++) {
    for(size_t frame:{1,127,160,480}) {
      aii::voice::EchoInput input(1);
      std::vector<float> got,want(length);
      for(size_t i=0;i<length;i++)want[i]=float(int(i%50)-25)/100.f;
      for(size_t start=0;start<length;) {
        const auto n=std::min(frame,length-start);std::vector<float> pairs(n*2);
        for(size_t i=0;i<n;i++)pairs[i*2]=want[start+i];
        auto output=input.feed(start,pairs.data(),n);got.insert(got.end(),output.begin(),output.end());start+=n;
      }
      auto tail=input.finish();got.insert(got.end(),tail.begin(),tail.end());
      if(got!=want||!input.finish().empty())throw std::runtime_error("short tail or no-reference silence changed microphone");
    }
  }
  std::cout<<"paired adapter: 2556 arbitrary-fragment and short-tail cases passed\n";
}
