#include "decoder.h"
#include <cmath>
#include <functional>
#include <future>
#include <iostream>
#include <algorithm>
#include <limits>
#include <map>
#include <memory>
#include <string>

using namespace aii::multitalker;
namespace {
void require(bool value) { if (!value) throw std::runtime_error("decoder contract failed"); }
void refuses(const std::function<void()>& action) {
  bool refused=false;
  try { action(); } catch (const std::exception&) { refused=true; }
  require(refused);
}
struct Mock : Backend {
  int predicts=0, classes=0;
  bool emit=true, broken=false;
  std::function<void()> callback;
  std::vector<int64_t> seen;
  Prediction predict(int64_t token, const State& state) override {
    ++predicts; seen.push_back(token);
    Prediction out; out.next=state; out.next.hidden[0]+=1;
    out.values[0]=out.next.hidden[0];
    return out;
  }
  int64_t classify(const float*, const Prediction&) override {
    ++classes;
    if (callback) callback();
    if (broken) throw std::runtime_error("backend failed");
    const bool now=emit; emit=!emit;
    return now ? 17 : blank_token;
  }
  void cancel() noexcept override {}
  void reopen() override {}
};
struct Stalled final : Mock {
  std::promise<void> entered, release;
  Prediction predict(int64_t token,const State& state) override {
    entered.set_value(); release.get_future().wait();
    return Mock::predict(token,state);
  }
};
// A scripted model for the term-following decoder. A step is named by the
// frame's first value and the last piece emitted; the script gives the scores
// of the pieces it cares about there, everything else far below and blank at
// zero. classify is the highest score of the same script, so the plain
// decoder and the leaning one are judged by one model.
struct Scripted : Backend {
  std::map<std::pair<int,int64_t>,std::map<int64_t,float>> script;
  int classes=0,scored=0;
  Prediction predict(int64_t token,const State& state) override {
    Prediction out; out.next=state; out.values[0]=float(token); return out;
  }
  std::array<float,blank_token+1> at(const float* frame,const Prediction& p) const {
    std::array<float,blank_token+1> s; s.fill(-50); s[blank_token]=0;
    const auto found=script.find({int(frame[0]),int64_t(p.values[0])});
    if(found!=script.end())for(const auto& row:found->second)s[size_t(row.first)]=row.second;
    return s;
  }
  int64_t classify(const float* frame,const Prediction& p) override {
    ++classes; const auto s=at(frame,p);
    return std::max_element(s.begin(),s.end())-s.begin();
  }
  bool scores(const float* frame,const Prediction& p,std::array<float,blank_token+1>& out) override {
    ++scored; out=at(frame,p); return true;
  }
  void cancel() noexcept override {}
  void reopen() override {}
};
// Pieces: 5 " D", 6 "al", 7 "e", 8 " dell", 9 "ome", 10 " s", 11 "ol", 12 " New", 13 " York", 14 " yo", 15 "rk", 16 " d", 17 ",".
std::vector<std::string> pieces() {
  std::vector<std::string> v(1024);
  for(size_t i=0;i<v.size();++i)v[i]="#"+std::to_string(i);
  v[5]=" D";v[6]="al";v[7]="e";v[8]=" dell";v[9]="ome";v[10]=" s";v[11]="ol";v[12]=" New";v[13]=" York";v[14]=" yo";v[15]="rk";v[16]=" d";v[17]=",";v[18]=" ";v[19]="D";v[20]="Q";
  return v;
}
std::vector<int64_t> ids(const std::vector<Token>& tokens){std::vector<int64_t> v;for(const auto& t:tokens)v.push_back(t.id);return v;}
std::vector<Token> run(Scripted& model,std::shared_ptr<const TermBoost> terms,int frames,bool one_call=false) {
  Decoder d(model); d.boost(std::move(terms)); d.reset(1);
  std::vector<Token> all; std::vector<float> data(size_t(frames)*encoder_width,0);
  for(int f=0;f<frames;++f)data[size_t(f)*encoder_width]=float(f);
  if(one_call){auto part=d.push(1,0,0,data.data(),size_t(frames),true);return part;}
  for(int f=0;f<frames;++f){auto part=d.push(1,0,uint64_t(f),data.data()+size_t(f)*encoder_width,1,f+1==frames);all.insert(all.end(),part.begin(),part.end());}
  return all;
}
void terms() {
  const auto vocabulary=pieces();
  const auto list=[&](std::vector<std::string> written,float margin=3,float budget=6){return std::make_shared<const TermBoost>(vocabulary,written,margin,budget);};
  // The list itself: any cut of a term into pieces is a path; case is ignored;
  // a term the pieces cannot spell, a malformed one and a repeated one refuse.
  require(list({"Dale","new york","SOL"})->size()==3 && list({})->empty());
  for(const auto& bad:std::vector<std::vector<std::string>>{{"zzz"},{""},{"a,b"},{" dale"},{"dale "},{"d  n"},{"dale","DALE"},{"'"}})
    refuses([&]{list(bad);});
  refuses([&]{TermBoost(vocabulary,{"dale"},-1,6);}); refuses([&]{TermBoost(vocabulary,{"dale"},3,2);});
  // A term is spelled as written where the pieces allow it: "Dale" begins
  // with the capital piece only, "dale" with the small one, and "new york",
  // which the pieces cannot spell as written, without regard to case.
  require(list({"Dale"})->starts().size()==1 && list({"Dale"})->starts()[0].token==5);
  require(list({"dale"})->starts().size()==1 && list({"dale"})->starts()[0].token==16);
  require(list({"new york"})->starts().size()==1 && list({"new york"})->starts()[0].token==12);
  // The bare word-start piece does not begin a term another piece can begin
  // ("Dale" could also be cut " "+"D"+"al"+"n"); it does where nothing else can.
  for(const auto& start:list({"Dale"})->starts())require(start.token!=18);
  require(list({"Q"})->starts().size()==1 && list({"Q"})->starts()[0].token==18);
  require(list({"Dale"})->continues(6) && list({"Dale"})->continues(9) && !list({"Dale"})->continues(5) && !list({"Dale"})->continues(17) && !list({"Dale"})->continues(blank_token));

  // The model writes " dell" where "Dale" was said: " D" is close behind it,
  // and on a path that took " D" the rest of the name is close behind blank.
  Scripted heard;
  heard.script[{0,blank_token}]={{8,10},{5,8.5f}};
  heard.script[{0,5}]={{6,-0.5f}};
  heard.script[{1,6}]={{7,-1}};
  const auto plain=run(heard,nullptr,3);
  require(ids(plain)==std::vector<int64_t>{8} && heard.scored==0); // no list: the classifying loop, untouched
  auto leaned=run(heard,list({"Dale"}),3);
  require(ids(leaned)==std::vector<int64_t>({5,6,7}) && leaned[0].frame==0 && leaned[1].frame==0 && leaned[2].frame==1);
  require(ids(run(heard,list({"Dale"}),3,true))==std::vector<int64_t>({5,6,7}));
  require(ids(run(heard,list({"sol"}),3))==ids(plain)); // another term changes nothing here

  // Withheld until the term stands: spelled out, and the next thing written
  // begins another word (here) or is punctuation.
  for(const int64_t after:{int64_t(10),int64_t(17)}) {
    Scripted then=heard; then.script[{2,8}]={{after,5}};
    Decoder d(then); d.boost(list({"Dale"})); d.reset(1);
    std::array<float,encoder_width> f0{},f1{},f2{}; f1[0]=1; f2[0]=2;
    require(d.push(1,0,0,f0.data(),1).empty() && d.push(1,0,1,f1.data(),1).empty());
    require(ids(d.push(1,0,2,f2.data(),1))==std::vector<int64_t>({5,6,7,after}));
  }
  // The word goes on past the term ("Dale" in "Daleome"): not the term.
  {
    Scripted longer=heard; longer.script[{2,7}]={{9,5}}; longer.script[{2,8}]={{9,5}};
    require(ids(run(longer,nullptr,3))==std::vector<int64_t>({8,9}));
    require(ids(run(longer,list({"Dale"}),3))==std::vector<int64_t>({8,9}));
  }
  // Everything the decoder writes after the term is its own, piece for
  // piece: the term takes the place of one word and changes nothing else.
  {
    Scripted on=heard; on.script[{2,8}]={{10,5}}; on.script[{2,10}]={{9,4}}; on.script[{3,9}]={{17,3}};
    require(ids(run(on,nullptr,5))==std::vector<int64_t>({8,10,9,17}));
    const auto with=run(on,list({"Dale"}),5);
    require(ids(with)==std::vector<int64_t>({5,6,7,10,9,17}) && with[3].frame==2 && with[5].frame==3);
    require(ids(run(on,list({"Dale"}),5,true))==std::vector<int64_t>({5,6,7,10,9,17}));
  }
  // A term begins only where the decoder begins a word: not in place of a
  // piece that goes on with a word, and not in place of a mark.
  for(const int64_t own:{int64_t(9),int64_t(17)}) {
    Scripted mid; mid.script[{0,blank_token}]={{own,5},{5,4.5f}}; mid.script[{0,5}]={{6,-0.5f}}; mid.script[{0,6}]={{7,-0.5f}};
    require(ids(run(mid,list({"Dale"}),3))==std::vector<int64_t>{own} && ids(run(mid,nullptr,3))==std::vector<int64_t>{own});
  }
  // A term never begins out of silence, however close its first piece is.
  {
    Scripted quiet; quiet.script[{0,blank_token}]={{5,-0.5f}}; quiet.script[{0,5}]={{6,-0.5f}}; quiet.script[{0,6}]={{7,-0.5f}};
    require(run(quiet,list({"Dale"}),3).empty() && run(quiet,nullptr,3).empty());
  }
  // The term does not complete: the output is exactly the plain decoder's.
  Scripted other=heard; other.script[{1,6}]={{9,5}}; // after " D","al" the model insists on another piece
  other.script[{1,8}]={{9,5}};                       // which the plain path also writes after " dell"
  require(ids(run(other,nullptr,3))==std::vector<int64_t>({8,9}));
  require(ids(run(other,list({"Dale"}),3))==std::vector<int64_t>({8,9}));
  require(ids(run(other,list({"Dale"}),3,true))==std::vector<int64_t>({8,9}));
  // A piece too far behind is not taken, and neither is a term whose pieces
  // are each close enough but together cost more than the budget.
  Scripted far=heard; far.script[{0,blank_token}]={{8,10},{5,6.5f}};
  require(ids(run(far,list({"Dale"}),3))==ids(plain));
  Scripted costly=heard; costly.script[{0,blank_token}]={{8,10},{5,7.5f}}; costly.script[{0,5}]={{6,-2.5f}}; costly.script[{1,6}]={{7,-2.5f}};
  require(ids(run(costly,list({"Dale"},3,6),3))==ids(plain));
  require(ids(run(costly,list({"Dale"},3,8),3))==std::vector<int64_t>({5,6,7}));
  // The utterance ends inside a term: nothing stays withheld, and what
  // leaves is the plain decoder's output.
  require(ids(run(heard,list({"Dale"}),1))==ids(run(heard,nullptr,1)));
  {
    Decoder d(heard); d.boost(list({"Dale"})); d.reset(1);
    std::array<float,encoder_width> f0{};
    require(d.push(1,0,0,f0.data(),1).empty());
    require(ids(d.finish(1,0))==std::vector<int64_t>{8} && d.finish(1,0).empty() && d.finish(1,3).empty());
    refuses([&]{d.finish(2,0);});
  }
  // A term nobody finishes is given up after the bound, at plain output.
  {
    Decoder d(heard); d.boost(list({"Dale"})); d.reset(1);
    std::vector<Token> all; std::array<float,encoder_width> f{};
    for(uint64_t i=0;i<Decoder::match_frames+2;++i){f[0]=i?9:0;auto part=d.push(1,0,i,f.data(),1);all.insert(all.end(),part.begin(),part.end());if(i<Decoder::match_frames)require(part.empty());}
    require(ids(all)==std::vector<int64_t>{8} && all[0].frame==0);
  }
  // When the model spells the term itself nothing is changed, and when it
  // starts like the term and goes elsewhere its own pieces are released as they were.
  Scripted own; own.script[{0,blank_token}]={{5,9}}; own.script[{0,5}]={{6,8}}; own.script[{0,6}]={{7,7}};
  require(ids(run(own,list({"Dale"}),2))==std::vector<int64_t>({5,6,7}) && ids(run(own,nullptr,2))==std::vector<int64_t>({5,6,7}));
  Scripted elsewhere; elsewhere.script[{0,blank_token}]={{5,9}}; elsewhere.script[{0,5}]={{9,8}};
  require(ids(run(elsewhere,list({"Dale"}),2))==std::vector<int64_t>({5,9}) && ids(run(elsewhere,nullptr,2))==std::vector<int64_t>({5,9}));
  // Two words, and either cut of the second.
  Scripted city; city.script[{0,blank_token}]={{12,5}}; city.script[{1,12}]={{14,-1}}; city.script[{1,14}]={{15,-1}};
  require(ids(run(city,list({"new york"}),3))==std::vector<int64_t>({12,14,15}));
  // When the decoder writes both words itself, the term waits for both and
  // takes their place; a mark between them ends it as the decoder's own.
  Scripted both; both.script[{0,blank_token}]={{12,5}}; both.script[{1,12}]={{13,5}}; both.script[{3,13}]={{10,5}};
  require(ids(run(both,list({"new york"}),5))==std::vector<int64_t>({12,13,10}) && ids(run(both,nullptr,5))==std::vector<int64_t>({12,13,10}));
  Scripted apart; apart.script[{0,blank_token}]={{12,5}}; apart.script[{1,12}]={{17,5}}; apart.script[{2,17}]={{13,5}};
  require(ids(run(apart,list({"new york"}),4))==ids(run(apart,nullptr,4)));
  // A speaker taken up while a term is being followed keeps it.
  {
    Decoder d(heard); d.boost(list({"Dale"})); d.reset(1);
    std::array<float,encoder_width> f0{},f1{}; f1[0]=1;
    require(d.push(1,dormant_track,0,f0.data(),1).empty());
    d.clone_track(1,dormant_track,7);
    require(ids(d.push(1,7,1,f1.data(),1,true))==std::vector<int64_t>({5,6,7}));
    require(ids(d.push(1,dormant_track,1,f1.data(),1,true))==std::vector<int64_t>({5,6,7}));
  }
  // The list takes effect at a reset, never inside an utterance.
  {
    Decoder d(heard); d.reset(1);
    std::array<float,encoder_width> f0{};
    d.boost(list({"Dale"}));
    require(ids(d.push(1,0,0,f0.data(),1,true))==std::vector<int64_t>{8});
    d.reset(2); std::array<float,encoder_width> f1{}; f1[0]=1;
    require(d.push(2,0,0,f0.data(),1).empty() && ids(d.push(2,0,1,f1.data(),1,true))==std::vector<int64_t>({5,6,7}));
    d.boost(nullptr); d.reset(3);
    require(ids(d.push(3,0,0,f0.data(),1))==std::vector<int64_t>{8});
  }
}
}
int main() {
  try {
    terms();
    std::array<float,encoder_width> frame{};
    Mock m; Decoder d(m);
    refuses([&]{ d.push(0,0,0,frame.data(),1); });
    d.reset(1);
    auto a=d.push(1,0,0,frame.data(),1);
    require(a.size()==1 && a[0].id==17 && a[0].frame==0);
    // A different track starts from blank, not from the previous person's word.
    m.emit=true; auto b=d.push(1,1,0,frame.data(),1);
    require(b.size()==1 && m.seen[2]==blank_token);
    // Track 0 retains its own state across a gap and a call on track 1.
    m.emit=true; auto c=d.push(1,0,50,frame.data(),1);
    require(c.size()==1 && c[0].frame==50 && m.seen.back()==17);
    refuses([&]{ d.push(1,0,50,frame.data(),1); });
    refuses([&]{ d.push(1,state_count,0,frame.data(),1); });
    refuses([&]{ d.push(2,0,0,frame.data(),1); });
    refuses([&]{ d.reset(1); });
    refuses([&]{ d.push(1,0,UINT64_MAX,frame.data(),1); });
    frame[0]=std::numeric_limits<float>::quiet_NaN();
    refuses([&]{ d.push(1,0,60,frame.data(),1); });
    frame[0]=0;
    d.reset(2); m.emit=false;
    auto before=m.predicts;
    require(d.push(2,0,0,frame.data(),1).empty());
    m.emit=false; require(d.push(2,0,1,frame.data(),1).empty());
    require(m.predicts==before+1); // blank does not commit or recompute LSTM state
    m.broken=true;
    refuses([&]{ d.push(2,0,2,frame.data(),1); });
    m.broken=false;
    refuses([&]{ d.push(2,0,2,frame.data(),1); }); // partial failure cannot retry
    d.reset(3); m.callback=[&]{d.cancel();};
    refuses([&]{ d.push(3,0,0,frame.data(),1); });
    m.callback={};
    refuses([&]{ d.push(3,1,0,frame.data(),1); });
    d.reset(4); m.emit=true;
    require(d.push(4,0,0,frame.data(),1).size()==1);
    // Cloning never-active state must preserve the exact prediction/clock,
    // and never overwrite a recognizer which already owns a speaker.
    d.reset(5);m.emit=true;
    require(d.push(5,dormant_track,0,frame.data(),1).size()==1);
    d.clone_track(5,dormant_track,7);
    m.emit=true;auto cloned=d.push(5,7,1,frame.data(),1);
    require(cloned.size()==1&&cloned[0].frame==1&&m.seen.back()==17);
    refuses([&]{d.clone_track(5,dormant_track,7);});
    refuses([&]{d.clone_track(4,dormant_track,6);});
    refuses([&]{d.clone_track(5,dormant_track,dormant_track);});
    d.cancel();refuses([&]{d.clone_track(5,dormant_track,6);});
    Stalled stalled; Decoder live(stalled);live.reset(1);
    auto worker=std::async(std::launch::async,[&]{
      refuses([&]{live.push(1,0,0,frame.data(),1);});
    });
    const auto ready=stalled.entered.get_future().wait_for(std::chrono::seconds(2));
    auto interrupt=std::async(std::launch::async,[&]{live.cancel();});
    const auto prompt=interrupt.wait_for(std::chrono::seconds(1));
    stalled.release.set_value(); // always release before asserting or unwinding
    interrupt.get();worker.get();
    require(ready==std::future_status::ready && prompt==std::future_status::ready && stalled.classes==0);
    std::cout << "multitalker decoder contracts passed\n";
    return 0;
  } catch (const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
}
