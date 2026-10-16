// Independent state-machine check using the real Session/PauseGate owners.
// Model outputs are replayed from training evidence. This is not inference,
// recognition, interruption latency, or physical-audio qualification.
#include "session.h"
#include <algorithm>
#include <array>
#include <chrono>
#include <fstream>
#include <iostream>
#include <map>
#include <stdexcept>
#include <thread>
#include <tuple>
#include <vector>
using namespace aii::voice;
using namespace std::chrono_literals;
using Boundary=std::tuple<uint64_t,uint64_t,std::string>;
void need(bool test,const char* message){if(!test)throw std::runtime_error(message);}
struct Asr final:Recognizer {
  uint64_t next=0;bool began=false;
  void begin()override {began=false;}
  std::string push(const float* p,size_t n)override {
    need(n==512,"probe ASR block changed");
    const auto index=uint64_t(p[0]-1);
    if(began)need(index==next,"actual Session skipped or replayed ASR audio inside a turn");
    for(size_t i=0;i<n;++i)need(p[i]==p[0],"probe PCM block changed");
    next=index+1;began=true;return "nonempty recognition stub";
  }
  std::string finish()override{return "nonempty recognition stub";}
  void reset()override{}
  void cancel()noexcept override{}
};
struct Detector final:Vad {
  std::vector<float> probabilities;
  size_t cursor=0;
  void reset()override{cursor=0;}
  float score(const float*)override{need(cursor<probabilities.size(),"VAD replay exhausted");return probabilities[cursor++];}
};
struct End final:Endpoint {
  std::map<std::pair<uint64_t,uint64_t>,double> values;
  double score(uint64_t,const std::vector<float>& pcm)override {
    need(!pcm.empty() && pcm.size()%512==0,"endpoint replay PCM invalid");
    const auto start=uint64_t(pcm.front()-1)*512;
    for(size_t i=0;i<pcm.size();++i)
      need(pcm[i]==float((start+i)/512+1),"endpoint received wrong context, duplicate or future audio");
    const auto found=values.find({start,start+pcm.size()});
    need(found!=values.end(),"actual Session queried a context absent from projection");return found->second;
  }
  void cancel()noexcept override{}
};
struct Tts final:Synthesizer {
  void start(uint64_t,const std::string&)override{throw std::runtime_error("unexpected synthesis");}
  std::vector<float> next()override{throw std::runtime_error("unexpected synthesis");}
  void reset()override{}
  void cancel(uint64_t)noexcept override{}
};
int main(int argc,char** argv) {
  try {
    need(argc==2,"input required");std::ifstream input(argv[1]);need(bool(input),"input unavailable");
    size_t cases=0, total_boundaries=0;std::string id;
    while(input>>id) {
      size_t n=0,q=0,b=0;input>>n>>q>>b;need(n && n<=16000 && q<=1024 && b<=512,"input counts invalid");
      Detector v;v.probabilities.resize(n);for(auto& p:v.probabilities)input>>p;
      End e;for(size_t i=0;i<q;++i){uint64_t s=0,z=0;double p=0;input>>s>>z>>p;need(e.values.emplace(std::make_pair(s,z),p).second,"duplicate query");}
      std::vector<Boundary> expected,observed;
      for(size_t i=0;i<b;++i){uint64_t s=0,z=0;std::string r;input>>s>>z>>r;expected.emplace_back(s,z,r);}
      need(bool(input),"truncated input");
      Asr a;Tts t;Session session(a,v,e,t);
      const auto deadline=std::chrono::steady_clock::now()+10s;
      auto pump=[&]{
        need(std::chrono::steady_clock::now()<deadline,"probe case deadline");
        Event event;while(session.event(event))
          if(event.kind=="turn_committed" && event.text!="finish_input")observed.emplace_back(event.start,event.end,event.text);
        auto s=session.status();if(!s.error.empty())throw std::runtime_error(id+": "+s.error);return s;
      };
      for(size_t i=0;i<n;++i) {
        std::array<float,512> block;block.fill(float(i+1));
        while(!session.feed(i*512,block.data(),block.size())){pump();std::this_thread::yield();}
        while(pump().recognized<(i+1)*512)std::this_thread::yield();
      }
      session.finish_input(n*512);while(!pump().input_finished)std::this_thread::yield();
      session.close(false);need(session.wait_closed(1000),"session owners did not retire");pump();
      need(v.cursor==n,"VAD did not consume all blocks");
      if(observed!=expected)throw std::runtime_error(id+": projection differs from actual Session boundaries");
      total_boundaries+=observed.size();++cases;
    }
    need(input.eof() && cases==343,"training case coverage differs");
    std::cout<<"{\"passed\":true,\"cases\":"<<cases<<",\"boundaries\":"<<total_boundaries<<",\"all_session_owners_retired\":true}\n";
    return 0;
  }catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}
}
