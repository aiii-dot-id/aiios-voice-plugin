// Real-model component caller: no microphone, playback, networking or SDK.
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <future>
#include <iomanip>
#include <iostream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

extern "C" {
void *nv_create_bound(const char *, const char *, const char *, int, char *, size_t) noexcept;
int nv_start(void *, uint64_t, const char *, uint32_t, int, const char *, char *, size_t) noexcept;
int nv_next(void *, uint64_t, float *, size_t, size_t *, char *, size_t) noexcept;
int nv_cancel(void *, uint64_t) noexcept;
uint32_t nv_state(void *) noexcept;
int nv_reset(void *, uint64_t, char *, size_t) noexcept;
int nv_destroy(void *) noexcept;
}
using Clock = std::chrono::steady_clock;
double ms(Clock::time_point t) { return std::chrono::duration<double, std::milli>(Clock::now()-t).count(); }
void require(bool ok, const std::string &why) { if (!ok) throw std::runtime_error(why); }
struct Engine {
  void *handle = nullptr;
  uint64_t generation = 0;
  char error[2048]{};
  ~Engine() { if (handle) { nv_cancel(handle, generation); nv_reset(handle, generation, error, sizeof error); nv_destroy(handle); } }
  void start(const std::string &text, const std::string &noise, int limit = 750) {
    ++generation;
    require(nv_start(handle, generation, text.c_str(), 20260908, limit, noise.c_str(), error, sizeof error)==0, error);
  }
  std::vector<float> collect(const std::string &text, const std::string &noise,
                             const std::filesystem::path &out, size_t expected) {
    const auto begun = Clock::now();
    start(text, noise);
    double first = -1;
    std::vector<float> all;
    bool ended = false;
    int chunks = 0;
    for (int i=0; i<751; ++i) {
      float pcm[1920]; size_t n=0;
      int code = nv_next(handle, generation, pcm, 1920, &n, error, sizeof error);
      require(code>=0, error);
      if (!code) { require(n==0, "end carries PCM"); ended=true; break; }
      require(code==1 && n>0 && n<=1920, "invalid audio result");
      if (first<0) first=ms(begun);
      for (size_t j=0; j<n; ++j) require(std::isfinite(pcm[j]), "nonfinite PCM");
      all.insert(all.end(), pcm, pcm+n); ++chunks;
    }
    const double elapsed=ms(begun);
    // Preserve failed PCM before the independent reference verdict.
    std::ofstream f(out, std::ios::binary); f.write(reinterpret_cast<const char *>(all.data()), all.size()*sizeof(float));
    f.close(); require(bool(f), "PCM write failed");
    std::cout << "{\"event\":\"audio\",\"file\":" << std::quoted(out.filename().string())
      << ",\"samples\":" << all.size() << ",\"expected\":" << expected
      << ",\"natural_end\":" << (ended?"true":"false") << ",\"chunks\":" << chunks
      << ",\"first_pcm_ms\":" << first << ",\"wall_ms\":" << elapsed
      << ",\"rtf\":" << elapsed/(all.size()/24.0) << "}" << std::endl;
    require(ended && all.size()==expected, "exact reference tail differs");
    require((nv_state(handle)&11)==0, "engine not idle after complete stream");
    return all;
  }
};
int main(int argc, char **argv) {
  try {
    require(argc==5, "usage: probe assets inputs fresh-output cpu|vulkan|metal");
    std::filesystem::path assets=argv[1], inputs=argv[2], out=argv[3];
    const std::string backend=argv[4];
    require(backend=="cpu" || backend=="vulkan" || backend=="metal", "explicit backend required");
    require(std::filesystem::create_directory(out), "output must not exist");
    std::vector<std::string> texts;
    std::ifstream listing(inputs/"texts.txt");
    for(std::string line;std::getline(listing,line);) texts.push_back(line);
    require(texts.size()==4, "four frozen cases required");
    const size_t expected[]={42240,61440,97920,78720};
    auto noise=[&](int i){return (inputs/std::to_string(i)/"noise.f32").string();};
    Engine e;
    const auto began=Clock::now();
    e.handle=nv_create_bound(assets.c_str(), (assets/"config.yaml").c_str(), backend.c_str(), 4, e.error, sizeof e.error);
    require(e.handle, e.error);
    std::cout<<"{\"event\":\"ready\",\"backend_requested\":"<<std::quoted(backend)
      <<",\"load_ms\":"<<ms(began)<<"}"<<std::endl;
    for(int i=0;i<4;++i) e.collect(texts[i],noise(i),out/("case-"+std::to_string(i)+".f32"),expected[i]);
    auto baseline=e.collect(texts[0],noise(0),out/"pre-cancel.f32",expected[0]);
    e.start(texts[0],noise(0));
    const auto old=e.generation;
    std::atomic<bool> returned{false};
    auto pending=std::async(std::launch::async,[&]{
      float pcm[1920];size_t n=0;
      int code=nv_next(e.handle,old,pcm,1920,&n,e.error,sizeof e.error);
      returned=true;
      return std::pair<int,size_t>{code,n};
    });
    auto wait_start=Clock::now();
    while(!(nv_state(e.handle)&1) && !returned && ms(wait_start)<10000) std::this_thread::yield();
    const bool active=(nv_state(e.handle)&1) && !returned;
    const auto cancel_start=Clock::now();
    const int ack=nv_cancel(e.handle,old);
    const double ack_ms=ms(cancel_start);
    const bool pending_after_ack=!returned;
    auto stale=pending.get();
    const double retired_ms=ms(cancel_start);
    std::cout<<"{\"event\":\"cancel\",\"inference_active\":"<<(active?"true":"false")
      <<",\"pending_after_ack\":"<<(pending_after_ack?"true":"false")<<",\"ack_ms\":"<<ack_ms
      <<",\"retired_ms\":"<<retired_ms<<",\"code\":"<<stale.first<<",\"samples\":"<<stale.second<<"}"<<std::endl;
    require(active && pending_after_ack && ack==0 && stale.first==-2 && stale.second==0, "in-flight cancellation proof failed");
    require(nv_reset(e.handle,old,e.error,sizeof e.error)==0, e.error);
    // A stale-generation cancel must not affect recovery.
    require(nv_cancel(e.handle,old)==0, "old cancel rejected");
    auto recovery=e.collect(texts[0],noise(0),out/"recovery.f32",expected[0]);
    require(baseline==recovery, "same-history recovery PCM differs");
    e.start(texts[0],noise(0),2);
    size_t total=0;bool error=false;
    for(int i=0;i<4;++i) {
      float pcm[1920];size_t n=0;
      int code=nv_next(e.handle,e.generation,pcm,1920,&n,e.error,sizeof e.error);
      total+=n;
      if(code<0){error=true;break;}
      require(code==1, "frame exhaustion falsely certified natural completion");
    }
    require(error && (nv_state(e.handle)&8), "frame limit must fault engine");
    std::cout<<"{\"event\":\"frame_limit_refused\",\"samples\":"<<total<<"}"<<std::endl;
    require(nv_destroy(e.handle)==0, "owner failed to retire");e.handle=nullptr;
    std::cout<<"{\"event\":\"complete\",\"component_control_pass\":true}"<<std::endl;
    return 0;
  } catch(const std::exception &e) { std::cerr<<"PROOF_FAILED: "<<e.what()<<std::endl; return 1; }
}
