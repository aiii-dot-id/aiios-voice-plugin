#include "c_api_internal.h"
#include <atomic>
#include <chrono>
#include <thread>
namespace {
struct FakeRecognizer : aii::voice::Recognizer {
  void begin() override {}
  std::string push(const float*,size_t) override { return "opening words"; }
  std::string finish() override { return "opening words retained"; }
  void reset() override {}
  void cancel() noexcept override {}
};
struct FakeVad : aii::voice::Vad { void reset() override {} float score(const float*) override { return .9f; } };
struct FakeEndpoint : aii::voice::Endpoint {
  double score(uint64_t,const std::vector<float>&) override { return .9; }
  void cancel() noexcept override {}
};
std::atomic<unsigned> cancel_count{0};
struct FakeTts : aii::voice::Synthesizer {
  std::atomic<bool> cancelled{false};uint64_t id=0;unsigned count=0;
  void start(uint64_t generation,const std::string&) override { id=generation;count=0; }
  std::vector<float> next() override {
    if(id==1) {
      while(!cancelled)std::this_thread::sleep_for(std::chrono::milliseconds(1));
      throw aii::voice::Cancelled("cancelled fake inference");
    }
    return count++?std::vector<float>{}:std::vector<float>(960,.25f);
  }
  void reset() override {}
  void cancel(uint64_t) noexcept override { ++cancel_count;cancelled=true; }
};
struct Owner : aii::voice::ModelOwner {
  FakeRecognizer a;FakeVad v;FakeEndpoint e;FakeTts t;
  aii::voice::Recognizer& recognizer() override { return a; }
  aii::voice::Vad& vad() override { return v; }
  aii::voice::Endpoint& endpoint() override { return e; }
  aii::voice::Synthesizer& synthesizer() override { return t; }
  aii_voice_readiness warm() override {return {4,1,"cpu"};}
  std::vector<uint64_t> enrollment_finals() override {return {11,12,13};}
  std::string enroll_selected(const std::string& current,const std::string& id,const std::string& label,const std::vector<uint64_t>& finals) override {
    if(current!="fixture"||id!="person"||label!="Chosen"||finals!=std::vector<uint64_t>{11,12,13})throw std::invalid_argument("test selection changed at C ABI");
    return "canonical candidate fixture";
  }
};
}
extern "C" aii_voice_models* aii_test_models() { return aii::voice::wrap_models(std::make_unique<Owner>()); }
extern "C" unsigned aii_test_cancel_count() { return cancel_count.load(); }
extern "C" void aii_test_yield() { std::this_thread::sleep_for(std::chrono::milliseconds(1)); }
