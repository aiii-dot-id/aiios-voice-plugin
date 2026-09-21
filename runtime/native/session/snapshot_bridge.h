#pragma once
#include "worker_json.h"
#include "c_api.h"
#include <condition_variable>
#include <functional>
#include <map>
#include <mutex>

namespace aii::voice {
// Private carrier composition. One UID reader, one bounded page outstanding.
// Admission never waits here; cancel wakes the reader without inference locks.
class SnapshotBridge {
 public:
  // Two fixed private resources; never a caller-provided filesystem path.
  enum class Store { Enrollment, PendingCaptures, Recovery, SpeakerRegistry };
  using Send=std::function<void(wire::Json)>;
  explicit SnapshotBridge(Send send={}):send_(std::move(send)){}
  void sender(Send send) {send_=std::move(send);} // before any session opens
  void begin(const std::string& session);
  void cancel() noexcept;
  void accept(const cJSON*);
  // Only an enrollment operation may request typed absence. Ordinary UID
  // reads still throw; they never substitute an invented empty profile.
  std::string read(bool* absent=nullptr,Store store=Store::Enrollment,const std::string& archive={});
  wire::Json publish(const std::string& candidate,const std::string& expected,
      bool absent,const std::string& upload,Store store=Store::Enrollment,const std::string& archive={});
  static aii_voice_result callback(void*,char*,size_t,size_t*) noexcept;
 private:
  wire::Json page(uint64_t offset,bool digest,std::chrono::steady_clock::time_point deadline,Store,const std::string&);
  wire::Json exchange(wire::Json query,std::chrono::steady_clock::time_point deadline,Store,const std::string&);
  std::string read_owned(bool*,std::chrono::steady_clock::time_point,Store,const std::string&);
  void acquire();
  void release() noexcept;
  Send send_;
  std::mutex mutex_;
  std::condition_variable changed_;
  bool live_=false,busy_=false;
  uint64_t next_=0,pending_=0;
  std::string session_;
  std::map<uint64_t,std::string> issued_;
  wire::Json reply_=wire::null();
};
}
