#pragma once
#include <atomic>
#include <array>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <stdexcept>
#include <string>
namespace aii::voice::wire {
struct UnframedWrite : std::runtime_error {
  using std::runtime_error::runtime_error;
};
uint64_t monotonic_ns();
void log_line(const std::string &text);
#ifdef _WIN32
[[noreturn]] void end_process(int code) noexcept;
#endif
int protocol_stdout();
int audio_descriptor(const char *name, bool input);
class Pipe {
public:
  explicit Pipe(int fd);
  Pipe(int fd, std::chrono::milliseconds write_limit, const char *limit_member);
  ~Pipe();
  Pipe(const Pipe &) = delete;
  bool exact(void *, size_t, const std::atomic<bool> &stop,
             bool boundary = false);
  bool line(std::string &, const std::atomic<bool> &stop);
  void write(const void *, size_t, const std::atomic<bool> &stop);
  void interrupt() noexcept;
  bool expired() const;
  std::string expiry() const;
  uint64_t read_operations() const {return read_operations_;}

private:
  int fd_;
  bool writing_;
  std::array<char,16384> read_buffer_{};
  size_t read_begin_=0,read_end_=0;
  uint64_t read_operations_=0;
  size_t read_some(void*,size_t,const std::atomic<bool>&);
  std::atomic<uint64_t> started_{0};
  const uint64_t write_limit_ns_;
  const char *const limit_member_;
#ifdef _WIN32
  void *thread_ = nullptr;
  std::atomic<bool> thread_ready_{false};
  void register_thread();
#endif
};
}
