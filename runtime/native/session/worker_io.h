#pragma once
#include <atomic>
#include <array>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <stdexcept>
#include <string>
namespace aii::voice::wire {
// Pipe::write throws UnframedWrite when the pipe can no longer be trusted to
// carry whole frames: the write failed after its first byte (part of its frame
// is on the pipe), or it reached its deadline or was interrupted (its reader
// is not taking bytes, and on Windows a cancelled write may have moved some).
// The two are said apart: a write that has had its time says which limit that
// was (Pipe::expiry), and one its owner stopped inside its time names none.
// Its owner retires the pipe. A write refused before its first byte throws an
// ordinary runtime_error: the pipe then holds only whole frames.
struct UnframedWrite : std::runtime_error {
  using std::runtime_error::runtime_error;
};
uint64_t monotonic_ns();
// One line of the log: the text, its newline added, handed to standard error
// by one write. The carrier and its worker write the same descriptor, and
// what reads it cuts it at each newline: a line that leaves in pieces can
// have another writer's bytes between them, and the log then holds two
// lines, neither of them the one that was written. A pipe takes a write of
// up to PIPE_BUF bytes whole, so a line that long or shorter is never parted.
void log_line(const std::string &text);
#ifdef _WIN32
// Ends this process with `code` at once (TerminateProcess). Neither the exit
// handlers and static destructors that exit() runs nor the DLL process-detach
// work that ExitProcess runs (std::_Exit reaches it too) take place; the
// kernel still reclaims the process's memory, handles and GPU allocations.
// The worker calls
// this once its threads are joined and its protocol output is closed, or once
// its own deadline has passed, when nothing it could still do is observable
// outside the process. C stdio is flushed first.
[[noreturn]] void end_process(int code) noexcept;
#endif
int protocol_stdout();
int audio_descriptor(const char *name, bool input);
// One reader OR writer thread per pipe. interrupt() is concurrent and never
// closes a descriptor underneath a synchronous Windows I/O owner.
// Set the stop flag and repeat interrupt() until the owner has retired; one
// cancellation may precede the operation it needs to cancel.
class Pipe {
public:
  // A pipe that is read.
  explicit Pipe(int fd);
  // A pipe that is written, how long one write to it may take before
  // expired() says so, and the name of the limits table's member that states
  // that time. Its owner states both: the worker's are the table's
  // control_write and audio_write (worker_limits.h). None is typed here; it
  // was three seconds for any pipe.
  Pipe(int fd, std::chrono::milliseconds write_limit, const char *limit_member);
  ~Pipe();
  Pipe(const Pipe &) = delete;
  bool exact(void *, size_t, const std::atomic<bool> &stop,
             bool boundary = false);
  bool line(std::string &, const std::atomic<bool> &stop);
  void write(const void *, size_t, const std::atomic<bool> &stop);
  void interrupt() noexcept;
  bool expired() const;
  // What is said of a write to this pipe that has had its time: one sentence,
  // whichever thread sees it first, the one that writes (write throws it) or
  // the owner's (which reads expired()).
  std::string expiry() const;
  uint64_t read_operations() const {return read_operations_;} // reader-owner diagnostic

private:
  int fd_;
  bool writing_;
  std::array<char,16384> read_buffer_{};
  size_t read_begin_=0,read_end_=0;
  uint64_t read_operations_=0;
  size_t read_some(void*,size_t,const std::atomic<bool>&);
  std::atomic<uint64_t> started_{0};
  const uint64_t write_limit_ns_; // nothing, for a pipe that is read
  const char *const limit_member_;
#ifdef _WIN32
  void *thread_ = nullptr;
  std::atomic<bool> thread_ready_{false};
  void register_thread();
#endif
};
} // namespace aii::voice::wire
