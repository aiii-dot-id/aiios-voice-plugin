#include "nemotron_targets.h"
#include "speaker_evidence.h"
#include <iostream>
#include <limits>
using namespace aii::multitalker;
void check(bool b){if(!b)throw std::runtime_error("Nemotron target contract");}
template<class F>void refuses(F f){bool failed=false;try{f();}catch(const std::invalid_argument&){failed=true;}check(failed);}
int main(){try{
  std::vector<float> fine(11*8,0);
  for(size_t i=0;i<8;++i)fine[i*8+7]=1;
  for(size_t i=8;i<11;++i)fine[i*8+6]=.75f;
  auto coarse=nemotron_targets(fine,2);
  check(coarse.size()==16&&coarse[7]==1&&coarse[14]==.75f&&coarse[15]==0);
  // A brief second speaker is not averaged out of UID evidence.
  SpeakerEvidence evidence(8,160);
  fine.assign(500*8,0);for(size_t i=0;i<500;++i)fine[i*8+7]=.99f;
  fine[250*8]=.2f;
  evidence.push(0,fine);auto spans=evidence.finish(80000);
  check(spans.size()==1&&spans[0].track==7&&spans[0].end<=40000-2560);
  check(evidence.regions().size()==1&&evidence.regions()[0].start>40000);
  // All channels compete, including channel seven when channel zero is known.
  SpeakerEvidence overlap(8,160);
  fine.assign(500*8,0);for(size_t i=0;i<500;++i){fine[i*8]=1;fine[i*8+7]=.11f;}
  overlap.push(0,fine);check(overlap.finish(80000).empty());
  refuses([&]{nemotron_targets({0,1},1);});
  refuses([&]{nemotron_targets(std::vector<float>(65,0),1);});
  fine.assign(8,0);fine[7]=std::numeric_limits<float>::quiet_NaN();
  refuses([&]{nemotron_targets(fine,1);});
  refuses([&]{SpeakerEvidence bad(8,1280);});
  std::cout<<"Nemotron alignment and evidence contracts passed\n";return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
