#pragma once
#include "model_channel.h"
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <utility>

namespace aii::voice {
class Link {
 public:
  explicit Link(ChannelEnd);
  ~Link();
  Link(const Link&) = delete;
  Link& operator=(const Link&) = delete;
  bool read_all(void* to, size_t n);
  bool write_all(const char* from, size_t n);
  void end() noexcept;
  void end_writing() noexcept;
  ChannelEnd ends() const { return ends_; }
 private:
  ChannelEnd ends_;
#ifdef _WIN32
  struct SyncIo {
    void* handle = nullptr;
    std::atomic<bool> ended{false}, busy{false};
    std::atomic<void*> owner{nullptr};
    bool begin();
    void done() { busy = false; }
    void end() noexcept;
  };
  SyncIo in_, out_;
#else
  int wake_read_[2] = {-1, -1}, wake_write_[2] = {-1, -1};
#endif
};
std::pair<ChannelEnd, ChannelEnd> model_channel_pipes();
void close_channel_end(const ChannelEnd&) noexcept;
void serve_model_channel(StreamFactory&, Link&, std::chrono::milliseconds retire, HungStream hung);
}
