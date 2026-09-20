#include "decoder.h"
#include <cmath>
#include <functional>
#include <future>
#include <iostream>
#include <limits>

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
}
int main() {
  try {
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
    refuses([&]{ d.push(1,4,0,frame.data(),1); });
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
