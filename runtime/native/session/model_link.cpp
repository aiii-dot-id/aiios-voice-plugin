#include "model_link.h"
#include <stdexcept>
#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <algorithm>
#else
#include <errno.h>
#include <fcntl.h>
#include <climits>
#include <csignal>
#include <poll.h>
#include <unistd.h>
#endif

namespace aii::voice {
#ifdef _WIN32
Link::Link(ChannelEnd ends) : ends_(ends) {
  in_.handle = reinterpret_cast<HANDLE>(ends.in);
  out_.handle = reinterpret_cast<HANDLE>(ends.out);
  if (!in_.handle || in_.handle == INVALID_HANDLE_VALUE || !out_.handle || out_.handle == INVALID_HANDLE_VALUE)
    throw std::invalid_argument("model channel: a pipe handle each way required");
}
Link::~Link() {
  for (auto* io : {&in_, &out_})
    if (auto* owner = io->owner.load()) CloseHandle(static_cast<HANDLE>(owner));
}
bool Link::SyncIo::begin() {
  if (!owner.load()) {
    HANDLE self = nullptr;
    if (!DuplicateHandle(GetCurrentProcess(), GetCurrentThread(), GetCurrentProcess(), &self, THREAD_TERMINATE, FALSE, 0))
      return false;
    owner = self;
  }
  busy = true;
  if (ended) { busy = false; return false; }
  return true;
}
void Link::SyncIo::end() noexcept {
  ended = true;
  while (busy) {
    if (auto* thread = owner.load()) CancelSynchronousIo(static_cast<HANDLE>(thread));
    SwitchToThread();
  }
}
bool Link::read_all(void* to, size_t n) {
  auto* p = static_cast<char*>(to);
  while (n) {
    if (!in_.begin()) return false;
    DWORD got = 0;
    const BOOL ok = ReadFile(static_cast<HANDLE>(in_.handle), p, DWORD(std::min<size_t>(n, MAXDWORD)), &got, nullptr);
    in_.done();
    if (!ok || !got) return false;
    p += got; n -= got;
  }
  return true;
}
bool Link::write_all(const char* from, size_t n) {
  while (n) {
    if (!out_.begin()) return false;
    DWORD put = 0;
    const BOOL ok = WriteFile(static_cast<HANDLE>(out_.handle), from, DWORD(std::min<size_t>(n, MAXDWORD)), &put, nullptr);
    out_.done();
    if (!ok || !put) return false;
    from += put; n -= put;
  }
  return true;
}
void Link::end() noexcept { in_.end(); out_.end(); }
void Link::end_writing() noexcept { out_.end(); }
#else
namespace {
void close_on_exec(int fd) { (void)::fcntl(fd, F_SETFD, ::fcntl(fd, F_GETFD) | FD_CLOEXEC); }
bool non_blocking(int fd) {
  const int flags = ::fcntl(fd, F_GETFL);
  return flags >= 0 && ::fcntl(fd, F_SETFL, flags | O_NONBLOCK) == 0;
}
}
Link::Link(ChannelEnd ends) : ends_(ends) {
  if (ends.in < 0 || ends.out < 0 || ends.in > INT32_MAX || ends.out > INT32_MAX || ends.in == ends.out)
    throw std::invalid_argument("model channel: a pipe each way required");
  struct sigaction now {};
  if (::sigaction(SIGPIPE, nullptr, &now) != 0 || now.sa_handler != SIG_IGN)
    throw std::logic_error("model channel: SIGPIPE must be ignored by a process that holds a link");
  if (!non_blocking(int(ends.in)) || !non_blocking(int(ends.out)))
    throw std::runtime_error("model channel: a pipe cannot be made non-blocking");
  for (int* wake : {wake_read_, wake_write_}) {
    if (::pipe(wake) != 0) {
      for (int fd : {wake_read_[0], wake_read_[1], wake_write_[0], wake_write_[1]}) if (fd >= 0) ::close(fd);
      throw std::runtime_error("model channel: no wake pipe");
    }
    close_on_exec(wake[0]); close_on_exec(wake[1]);
  }
}
Link::~Link() { for (int fd : {wake_read_[0], wake_read_[1], wake_write_[0], wake_write_[1]}) ::close(fd); }
bool Link::read_all(void* to, size_t n) {
  const int fd = int(ends_.in);
  auto* p = static_cast<char*>(to);
  while (n) {
    pollfd waits[2] = {{fd, POLLIN, 0}, {wake_read_[0], POLLIN, 0}};
    if (::poll(waits, 2, -1) < 0) { if (errno == EINTR) continue; return false; }
    if (waits[1].revents) return false;
    const auto got = ::read(fd, p, n);
    if (got < 0 && (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK)) continue;
    if (got <= 0) return false;
    p += got; n -= size_t(got);
  }
  return true;
}
bool Link::write_all(const char* p, size_t n) {
  const int fd = int(ends_.out);
  while (n) {
    pollfd waits[2] = {{fd, POLLOUT, 0}, {wake_write_[0], POLLIN, 0}};
    if (::poll(waits, 2, -1) < 0) { if (errno == EINTR) continue; return false; }
    if (waits[1].revents) return false;
    const auto put = ::write(fd, p, n);
    if (put > 0) { p += put; n -= size_t(put); continue; }
    if (put < 0 && (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK)) continue;
    return false;
  }
  return true;
}
void Link::end() noexcept {
  const char byte = 'e';
  (void)!::write(wake_read_[1], &byte, 1);
  end_writing();
}
void Link::end_writing() noexcept {
  const char byte = 'e';
  (void)!::write(wake_write_[1], &byte, 1);
}
#endif

#ifdef _WIN32
std::pair<ChannelEnd, ChannelEnd> model_channel_pipes() {
  HANDLE up_read = nullptr, up_write = nullptr, down_read = nullptr, down_write = nullptr;
  if (!CreatePipe(&up_read, &up_write, nullptr, 0)) throw std::runtime_error("model channel: no pipe");
  if (!CreatePipe(&down_read, &down_write, nullptr, 0)) {
    CloseHandle(up_read); CloseHandle(up_write);
    throw std::runtime_error("model channel: no pipe");
  }
  return {ChannelEnd{intptr_t(down_read), intptr_t(up_write)}, ChannelEnd{intptr_t(up_read), intptr_t(down_write)}};
}
void close_channel_end(const ChannelEnd& end) noexcept {
  CloseHandle(reinterpret_cast<HANDLE>(end.in));
  if (end.out != end.in) CloseHandle(reinterpret_cast<HANDLE>(end.out));
}
#else
std::pair<ChannelEnd, ChannelEnd> model_channel_pipes() {
  int up[2] = {-1, -1}, down[2] = {-1, -1};
  if (::pipe(up) != 0) throw std::runtime_error("model channel: no pipe");
  if (::pipe(down) != 0) { ::close(up[0]); ::close(up[1]); throw std::runtime_error("model channel: no pipe"); }
  for (int fd : {up[0], up[1], down[0], down[1]}) close_on_exec(fd);
  return {ChannelEnd{down[0], up[1]}, ChannelEnd{up[0], down[1]}};
}
void close_channel_end(const ChannelEnd& end) noexcept {
  if (end.in >= 0) ::close(int(end.in));
  if (end.out >= 0 && end.out != end.in) ::close(int(end.out));
}
#endif
}
