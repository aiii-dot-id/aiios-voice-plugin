#include "pending_model.h"
#include <atomic>
#include <chrono>
#include <future>
#include <iostream>
#include <stdexcept>
#include <thread>

using namespace std::chrono_literals;
void check(bool value, const char* message) { if (!value) throw std::runtime_error(message); }
struct Model {
  std::atomic<int>& alive;
  explicit Model(std::atomic<int>& n): alive(n) { ++alive; }
  ~Model() { --alive; }
};
int main() {
 try {
  for (int i=0;i<50;++i) {
    std::atomic<int> alive{0};
    std::promise<void> entered, release;
    auto began=entered.get_future(); auto gate=release.get_future().share();
    {
      aii::voice::PendingModel<Model> owner([&] {
        entered.set_value(); gate.wait(); return std::make_unique<Model>(alive);
      });
      check(began.wait_for(2s)==std::future_status::ready,"construction was deferred, not concurrent");
      check(alive==0,"model published before construction");
      release.set_value();
      auto* first=&owner.get();
      check(first==&owner.get() && alive==1,"owner not resolved exactly once");
    }
    check(alive==0,"resolved model leaked");
  }
  // Simulate a sibling constructor throwing while this one is still running.
  // The outer scope must not retire before the unclaimed model is destroyed.
  std::atomic<int> alive{0};
  std::promise<void> entered, release, unwinding;
  auto gate=release.get_future().share(); auto started=entered.get_future();
  auto retired=std::async(std::launch::async,[&] {
    try {
      aii::voice::PendingModel<Model> owner([&] {
        entered.set_value(); gate.wait(); return std::make_unique<Model>(alive);
      });
      started.wait(); unwinding.set_value(); throw std::runtime_error("sibling failed");
    } catch(const std::runtime_error&) {}
  });
  unwinding.get_future().wait();
  check(retired.wait_for(20ms)==std::future_status::timeout,"unwinding detached a live model load");
  release.set_value(); retired.get(); check(alive==0,"unclaimed model leaked");
  aii::voice::PendingModel<Model> failed([]()->std::unique_ptr<Model> {
    throw std::runtime_error("precise model failure");
  });
  bool reason=false;try {failed.get();}catch(const std::runtime_error& e){reason=std::string(e.what())=="precise model failure";}
  check(reason,"model failure swallowed");
  bool retry_refused=false;try {failed.get();}catch(const std::runtime_error&){retry_refused=true;}
  check(retry_refused,"failed model exposed as valid");
  aii::voice::PendingModel<Model> empty([]{return std::unique_ptr<Model>{};});
  bool null_refused=false;try {empty.get();}catch(const std::runtime_error&){null_refused=true;}
  check(null_refused,"null model accepted");
  std::cout<<"parallel construction, one resolution, failure, and retirement pass\n";
 }catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}
}
