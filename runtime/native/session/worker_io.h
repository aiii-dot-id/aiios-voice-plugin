#pragma once
#include <atomic>
#include <array>
#include <cstddef>
#include <cstdint>
#include <string>
namespace aii::voice::wire {
uint64_t monotonic_ns();
int protocol_stdout();
int audio_descriptor(const char *name, bool input);
// One reader OR writer thread per pipe. interrupt() is concurrent and never
// closes a descriptor underneath a synchronous Windows I/O owner.
// Set the stop flag and repeat interrupt() until the owner has retired; one
// cancellation may precede the operation it needs to cancel.
class Pipe {
public:
  explicit Pipe(int fd, bool writing = false);
  ~Pipe();
  Pipe(const Pipe &) = delete;
  bool exact(void *, size_t, const std::atomic<bool> &stop,
             bool boundary = false);
  bool line(std::string &, const std::atomic<bool> &stop);
  void write(const void *, size_t, const std::atomic<bool> &stop);
  void interrupt() noexcept;
  bool expired() const;
  uint64_t read_operations() const {return read_operations_;} // reader-owner diagnostic

private:
  int fd_;
  bool writing_;
  std::array<char,16384> read_buffer_{};
  size_t read_begin_=0,read_end_=0;
  uint64_t read_operations_=0;
  size_t read_some(void*,size_t,const std::atomic<bool>&);
  std::atomic<uint64_t> started_{0};
#ifdef _WIN32
  void *thread_ = nullptr;
  std::atomic<bool> thread_ready_{false};
  void register_thread();
#endif
};
} // namespace aii::voice::wire
