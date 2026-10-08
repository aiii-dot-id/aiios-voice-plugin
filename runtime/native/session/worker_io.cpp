#include "worker_io.h"
#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <memory>
#include <stdexcept>
#include <thread>
#ifdef _WIN32
#define NOMINMAX
#include <fcntl.h>
#include <io.h>
#include <windows.h>
#else
#include <cerrno>
#include <csignal>
#include <fcntl.h>
#include <poll.h>
#include <unistd.h>
#endif
namespace aii::voice::wire {
#if defined(_WIN32) && defined(AII_PIPE_TEST_HOOK)
void before_blocking_read();
#endif
uint64_t monotonic_ns() {
  return uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(
                      std::chrono::steady_clock::now().time_since_epoch())
                      .count());
}
#ifdef _WIN32
void end_process(int code) noexcept {
  std::fflush(nullptr);
  TerminateProcess(GetCurrentProcess(), UINT(code));
  std::_Exit(code); // not reached: a process that terminates itself does not return
}
#endif
int protocol_stdout() {
#ifdef _WIN32
  _setmode(0, _O_BINARY);
  _setmode(1, _O_BINARY);
  int fd = _dup(1);
  if (fd < 0 || _dup2(2, 1))
    throw std::runtime_error("private stdout duplication failed");
#else
  std::signal(SIGPIPE, SIG_IGN);
  int fd = dup(1);
  if (fd < 0 || dup2(2, 1) < 0)
    throw std::runtime_error("private stdout duplication failed");
#endif
  return fd;
}
int audio_descriptor(const char *name, bool input) {
#ifdef _WIN32
  char *snapshot = nullptr;
  size_t length = 0;
  if (_dupenv_s(&snapshot, &length, name))
    throw std::runtime_error("cannot read inherited audio handle");
  std::unique_ptr<char, decltype(&std::free)> owned(snapshot, std::free);
  const char *s = owned.get();
#else
  const char *s = std::getenv(name);
#endif
  if (!s || !*s || *s == '-')
    throw std::runtime_error("inherited audio handle required");
  char *end = nullptr;
  errno = 0;
  const auto value = std::strtoull(s, &end, 10);
  if (errno || !end || *end)
    throw std::runtime_error("invalid inherited audio handle");
#ifdef _WIN32
  if (value > uint64_t(std::numeric_limits<intptr_t>::max()))
    throw std::runtime_error("audio handle overflow");
  const int fd = _open_osfhandle(intptr_t(value),
                                 _O_BINARY | (input ? _O_RDONLY : _O_WRONLY));
#else
  (void)input;
  if (value > uint64_t(std::numeric_limits<int>::max()))
    throw std::runtime_error("audio descriptor overflow");
  const int fd = int(value);
#endif
  if (fd < 0)
    throw std::runtime_error("audio handle adoption failed");
  return fd;
}
namespace {
// A limit of nothing would call every write expired at its first check.
uint64_t write_limit_ns(std::chrono::milliseconds limit) {
  if (limit <= std::chrono::milliseconds::zero())
    throw std::runtime_error("a pipe's write limit must be more than nothing");
  return uint64_t(
      std::chrono::duration_cast<std::chrono::nanoseconds>(limit).count());
}
// Both kinds of pipe are read and written without blocking in the kernel.
void nonblocking(int fd) {
#ifndef _WIN32
  const int flags = fcntl(fd, F_GETFL);
  if (flags < 0 || fcntl(fd, F_SETFL, flags | O_NONBLOCK) < 0)
    throw std::runtime_error("cannot make native pipe nonblocking");
#else
  (void)fd;
#endif
}
} // namespace
Pipe::Pipe(int fd) : fd_(fd), writing_(false), write_limit_ns_(0), limit_member_("") {
  nonblocking(fd_);
}
Pipe::Pipe(int fd, std::chrono::milliseconds write_limit, const char *limit_member)
    : fd_(fd), writing_(true), write_limit_ns_(write_limit_ns(write_limit)), limit_member_(limit_member) {
  if (!limit_member || !*limit_member)
    throw std::runtime_error("a pipe's write limit must be named");
  nonblocking(fd_);
}
Pipe::~Pipe() {
#ifdef _WIN32
  _close(fd_);
  if (thread_)
    CloseHandle(static_cast<HANDLE>(thread_));
#else
  close(fd_);
#endif
}
#ifdef _WIN32
void Pipe::register_thread() {
  if (thread_ready_)
    return;
  HANDLE h = nullptr;
  if (!DuplicateHandle(GetCurrentProcess(), GetCurrentThread(),
                       GetCurrentProcess(), &h, THREAD_TERMINATE, FALSE, 0))
    throw std::runtime_error("cannot retain I/O owner thread");
  thread_ = h;
  thread_ready_ = true;
}
#endif
size_t Pipe::read_some(void *target,size_t n,const std::atomic<bool>& stop) {
  for(;;) {
    if (stop)
      throw std::runtime_error("input owner interrupted");
#ifdef _WIN32
    register_thread();
    const auto h = reinterpret_cast<HANDLE>(_get_osfhandle(fd_));
#ifdef AII_PIPE_TEST_HOOK
    aii::voice::wire::before_blocking_read();
#endif
    // Anonymous pipes support synchronous reads. Let the kernel wait for data
    // instead of waking each millisecond. The owner repeats interrupt() until
    // retirement: cancellation before ReadFile starts must not strand a reader.
    DWORD got = 0;
    ++read_operations_;
    if (!ReadFile(h, target, DWORD(std::min<size_t>(n, MAXDWORD)), &got, nullptr)) {
      if (GetLastError() == ERROR_BROKEN_PIPE)
        return 0;
      throw std::runtime_error("native pipe read failed");
    }
    const auto count = got;
#else
    pollfd fd{fd_, POLLIN, 0};
    const int ready = poll(&fd, 1, 10);
    if (ready < 0 && errno == EINTR)
      continue;
    if (ready < 0)
      throw std::runtime_error("native input poll failed");
    if (!ready)
      continue;
    ++read_operations_;
    const auto count = read(fd_, target, n);
    if (count < 0 && (errno == EAGAIN || errno == EINTR))
      continue;
    if (count < 0)
      throw std::runtime_error("native pipe read failed");
#endif
    return size_t(count);
  }
}
bool Pipe::exact(void *target, size_t n, const std::atomic<bool> &stop,
                 bool boundary) {
  auto* p=static_cast<unsigned char*>(target);size_t done=0;
  while(done<n) {
    if(stop)throw std::runtime_error("input owner interrupted");
    size_t count=0;
    if(read_begin_<read_end_) {
      count=std::min(n-done,read_end_-read_begin_);
      std::memcpy(p+done,read_buffer_.data()+read_begin_,count);read_begin_+=count;
    } else count=read_some(p+done,n-done,stop);
    if (!count) {
      if (!done && boundary)
        return false;
      throw std::runtime_error("truncated native pipe input");
    }
    done += size_t(count);
  }
  return true;
}
bool Pipe::line(std::string &line, const std::atomic<bool> &stop) {
  line.clear();
  for(;;) {
    if(stop)throw std::runtime_error("input owner interrupted");
    if(read_begin_==read_end_) {
      read_begin_=0;read_end_=read_some(read_buffer_.data(),read_buffer_.size(),stop);
      if(!read_end_) {
        if(line.empty())return false;
        throw std::runtime_error("truncated private control line");
      }
    }
    const char* begin=read_buffer_.data()+read_begin_;
    const auto available=read_end_-read_begin_;
    const auto* end=static_cast<const char*>(std::memchr(begin,'\n',available));
    const size_t count=end?size_t(end-begin):available;
    if(count>1024*1024-line.size())throw std::runtime_error("private control line exceeded bound");
    line.append(begin,count);read_begin_+=count+(end?1:0);
    if(end)return true;
  }
}
void Pipe::write(const void *source, size_t n, const std::atomic<bool> &stop) {
  if (!writing_)
    throw std::runtime_error("pipe has no output owner");
  started_ = monotonic_ns();
  try {
    size_t done = 0;
    const auto *p = static_cast<const unsigned char *>(source);
    // After the first byte, a failure leaves part of this write on the pipe.
    const auto refuse = [&done](const char *why) {
      if (done)
        throw UnframedWrite(std::string(why) + " mid-frame");
      throw std::runtime_error(why);
    };
    while (done < n) {
      // Its deadline first: a write that has had its time is said as that,
      // whatever else is stopping it.
      if (expired())
        throw UnframedWrite(expiry());
      if (stop)
        throw UnframedWrite("native pipe write interrupted");
#ifdef _WIN32
      register_thread();
      DWORD count = 0;
      if (!WriteFile(reinterpret_cast<HANDLE>(_get_osfhandle(fd_)), p + done,
                     DWORD(n - done), &count, nullptr)) {
        done += count; // a cancelled write may have moved some bytes
        refuse("native pipe write failed");
      }
#else
      pollfd fd{fd_, POLLOUT, 0};
      const int ready = poll(&fd, 1, 10);
      if (ready < 0 && errno == EINTR)
        continue;
      if (ready < 0)
        refuse("native output poll failed");
      if (!ready)
        continue;
      const auto count = ::write(fd_, p + done, n - done);
      if (count < 0 && (errno == EAGAIN || errno == EINTR))
        continue;
      if (count < 0)
        refuse("native pipe write failed");
#endif
      if (!count)
        refuse("native pipe write made no progress");
      done += size_t(count);
    }
    started_ = 0;
  } catch (...) {
    started_ = 0;
    throw;
  }
}
void Pipe::interrupt() noexcept {
#ifdef _WIN32
  if (thread_ready_)
    CancelSynchronousIo(static_cast<HANDLE>(thread_));
#endif
}
bool Pipe::expired() const {
  const auto start = started_.load();
  return start && monotonic_ns() - start > write_limit_ns_;
}
std::string Pipe::expiry() const {
  return "native pipe write expired: " + std::to_string(write_limit_ns_ / 1000000) +
         " ms, the time the limits table gives it (" + limit_member_ + ")";
}
void log_line(const std::string &text) {
  const std::string line = text + '\n';
#ifdef _WIN32
  (void)_write(2, line.data(), static_cast<unsigned>(line.size()));
#else
  for (size_t done = 0; done < line.size();) {
    const auto count = ::write(2, line.data() + done, line.size() - done);
    if (count > 0)
      done += size_t(count);
    else if (count == 0 || errno != EINTR)
      return; // standard error takes no more, and there is nowhere else to say so
  }
#endif
}
} // namespace aii::voice::wire
