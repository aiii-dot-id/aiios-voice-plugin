// Explicit recorded numerical/lifecycle gate, not a model-free CTest.
#include "coreml_separator.h"
#include <chrono>
#include <cmath>
#include <cstring>
#include <fstream>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <thread>
namespace {
using Clock=std::chrono::steady_clock;
void check(bool ok,const char* why){if(!ok)throw std::runtime_error(why);}
std::vector<float> read(const std::string& file,size_t n) {
  std::ifstream f(file,std::ios::binary|std::ios::ate);
  check(f&&f.tellg()==static_cast<std::streamoff>(4*n),"fixture extent");
  std::vector<float> out(n);f.seekg(0);check(bool(f.read(reinterpret_cast<char*>(out.data()),4*n)),"fixture read");return out;
}
double compare(const aii::multitalker::Waveforms& actual,const aii::multitalker::Waveforms& expected){
  double difference=0,norm=0;
  for(size_t c=0;c<2;++c){check(actual[c].size()==expected[c].size(),"waveform extent");
    for(size_t i=0;i<actual[c].size();++i){check(std::isfinite(actual[c][i]),"nonfinite source");double d=double(actual[c][i])-expected[c][i];difference+=d*d;norm+=double(expected[c][i])*expected[c][i];}}
  return std::sqrt(difference/std::max(norm,1e-24));
}
}
int main(int argc,char** argv){try{
  check(argc==4,"compiled directory fixture directory cpu|gpu required");
  const std::string units=argv[3];check(units=="cpu"||units=="gpu","unknown compute selection");
  aii::multitalker::CoreMLSeparator owner(argv[1],units=="cpu");
  auto invalid=[&](std::vector<float> x){bool refused=false;try{owner.separate(x);}catch(const std::invalid_argument&){refused=true;}check(refused,"invalid PCM admitted");};
  invalid({});invalid(std::vector<float>(31999));invalid(std::vector<float>(80004));
  auto bad=std::vector<float>(32000);bad[5]=std::numeric_limits<float>::quiet_NaN();invalid(bad);bad[5]=1.01f;invalid(bad);
  for(size_t n:{72000,32000,32776,80000,80003,72000}) {
    const std::string suffix=std::to_string(n)+".f32";
    const auto pcm=read(std::string(argv[2])+"/input-"+suffix,n),raw=read(std::string(argv[2])+"/reference-"+suffix,2*n);
    auto expected=aii::multitalker::normalize_sources(pcm,{{std::vector<float>(raw.begin(),raw.begin()+n),std::vector<float>(raw.begin()+n,raw.end())}});
    owner.open();auto start=Clock::now();const double error=compare(owner.separate(pcm),expected);
    check(error<=2e-4,"native normalized waveform parity");
    std::cout<<"{\"kind\":\"parity\",\"samples\":"<<n<<",\"relative_l2\":"<<error<<",\"seconds\":"<<std::chrono::duration<double>(Clock::now()-start).count()<<"}"<<std::endl;
    owner.cancel();bool cancelled=false;try{owner.separate(pcm);}catch(const aii::voice::Cancelled&){cancelled=true;}
    check(cancelled,"pre-cancelled call returned sources");owner.open();
    if(units=="cpu")continue;
    for(unsigned delay:{5u,100u,250u}) {
      Clock::time_point requested,retired;bool active_cancel=false;std::exception_ptr failure;
      std::thread interrupt([&]{std::this_thread::sleep_for(std::chrono::milliseconds(delay));requested=Clock::now();owner.cancel();});
      try{owner.separate(pcm);}catch(const aii::voice::Cancelled&){active_cancel=true;}catch(...){failure=std::current_exception();}
      retired=Clock::now();interrupt.join();if(failure)std::rethrow_exception(failure);
      const double milliseconds=std::chrono::duration<double,std::milli>(retired-requested).count();
      check(active_cancel&&milliseconds>=0&&milliseconds<200,"active cancellation/retirement failed");
      owner.open();check(compare(owner.separate(pcm),expected)<=2e-4,"same-owner recovery parity");
      std::cout<<"{\"kind\":\"cancel_recover\",\"samples\":"<<n<<",\"delay_ms\":"<<delay<<",\"retirement_ms\":"<<milliseconds<<"}"<<std::endl;
    }
  }
  std::cout<<"{\"passed\":true,\"installed\":false}"<<std::endl;return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<std::endl;return 1;}}
