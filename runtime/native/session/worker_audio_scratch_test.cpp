#include "worker_audio_scratch.h"
#include <algorithm>
#include <iostream>
#include <stdexcept>

namespace {
size_t allocations=0, deallocations=0;
template<class T> struct Counted {
  using value_type=T;
  Counted()=default;
  template<class U> Counted(const Counted<U>&) {}
  T* allocate(size_t n) { ++allocations; return std::allocator<T>{}.allocate(n); }
  void deallocate(T* p,size_t n) { ++deallocations;std::allocator<T>{}.deallocate(p,n); }
  template<class U> bool operator==(const Counted<U>&) const { return true; }
  template<class U> bool operator!=(const Counted<U>&) const { return false; }
};
void require(bool value,const char* text) { if(!value)throw std::runtime_error(text); }
}
int main() {
  try {
    {
      aii::voice::wire::AudioScratch<Counted<float>> scratch;
      require(allocations==1 && scratch.size()==120000,"scratch must allocate exactly once");
      auto* first=scratch.data();first[0]=.25f;first[119999]=-.5f;
      for(size_t i=0;i<100000;++i) {
        require(scratch.data()==first && allocations==1,"empty polls reallocated scratch");
        require(first[0]==.25f && first[119999]==-.5f,"empty polls cleared scratch");
      }
      auto pending=scratch.copy(scratch.size());
      require(pending.size()==120000 && pending.front()==.25f && pending.back()==-.5f,"full bound lost samples");
      std::fill(first,first+scratch.size(),.75f);
      require(pending.front()==.25f && pending.back()==-.5f,"pending output aliases scratch");
      auto tail=scratch.copy(7);require(tail.size()==7 && tail.back()==.75f,"short tail extent changed");
      require(scratch.copy(0).empty(),"END replayed old audio");
      bool refused=false;try{scratch.copy(120001);}catch(const std::runtime_error&){refused=true;}
      require(refused,"oversized output accepted");
      require(allocations==1,"output copy moved/resized scratch");
    }
    require(deallocations==1,"scratch did not retire");
    std::cout<<"100000 empty polls: one allocation, no clearing; pending audio/tail/END ownership PASS\n";
    return 0;
  } catch(const std::exception& e) {std::cerr<<e.what()<<'\n';return 1;}
}
