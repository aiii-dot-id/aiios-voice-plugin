#pragma once
#include "worker_json.h"
#include "worker_limits.h"
#include "c_api.h"
#include <condition_variable>
#include <functional>
#include <map>
#include <mutex>

namespace aii::voice {
// THE HOST'S STORAGE DID NOT ANSWER INSIDE ITS TIME. The carrier says so in
// its reply (HOST_STORAGE_NO_ANSWER, plugin/native/snapshot.go). It is a
// refusal like any other to a caller that does not ask which; a caller that
// does can say "late" where it used to say "unavailable", which reads as
// "there is no enrollment". Nothing was read; a publication that was sent
// says, as before, that its outcome is unresolved.
struct StorageLate : wire::Refused { using wire::Refused::Refused; };
inline constexpr const char* kHostStorageNoAnswer = "HOST_STORAGE_NO_ANSWER";
// Private carrier composition. One storage transaction/page at a time.
// Background readers/writers share the existing deadline; control admission
// never waits here and cancellation wakes both the owner and waiting workers.
class SnapshotBridge {
 public:
  // Fixed private resources; never a caller-provided filesystem path.
  enum class Store { Enrollment, PendingCaptures, Recovery, SpeakerRegistry, Waveform };
  using Send=std::function<void(wire::Json)>;
  explicit SnapshotBridge(Send send={}):send_(std::move(send)){}
  void sender(Send send) {send_=std::move(send);} // before any session opens
  // The limits every wait here goes by (worker_limits.h): what the carrier
  // handed over, set once before any session opens. A table that does not
  // hold is refused.
  void limits(const WorkerLimits& l) {l.validate();limits_=l;}
  const WorkerLimits& limits() const {return limits_;}
  void begin(const std::string& session);
  void cancel() noexcept;
  void accept(const cJSON*);
  // Only an enrollment operation may request typed absence. Ordinary UID
  // reads still throw; they never substitute an invented empty profile.
  std::string read(bool* absent=nullptr,Store store=Store::Enrollment,const std::string& archive={});
  wire::Json publish(const std::string& candidate,const std::string& expected,
      bool absent,const std::string& upload,Store store=Store::Enrollment,const std::string& archive={});
  static aii_voice_result callback(void*,char*,size_t,size_t*) noexcept;
  // What storage is doing, in one look: whether an operation is in flight
  // and how many have ended. A drain reads it, so that storage inside its
  // own limits is neither called a stall nor left out of what has moved.
  struct Activity { bool busy; uint64_t completed; };
  Activity activity();
 private:
  wire::Json page(uint64_t offset,bool digest,std::chrono::steady_clock::time_point deadline,Store,const std::string&);
  // write says the query is a durable write (a stage, the publish) and not
  // a page read: it waits a write's time for the carrier's answer.
  wire::Json exchange(wire::Json query,std::chrono::steady_clock::time_point deadline,Store,const std::string&,bool write=false);
  std::string read_owned(bool*,std::chrono::steady_clock::time_point,Store,const std::string&);
  void acquire(std::chrono::steady_clock::time_point);
  void release() noexcept;
  Send send_;
  WorkerLimits limits_;
  std::mutex mutex_;
  std::condition_variable changed_;
  bool live_=false,busy_=false;
  uint64_t next_=0,pending_=0,epoch_=0,completed_=0;
  std::string session_;
  std::map<uint64_t,std::string> issued_;
  wire::Json reply_=wire::null();
};
}
