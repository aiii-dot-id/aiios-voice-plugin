#include "model_server_control.h"
#include "model_link.h"
#include <atomic>
#include <cerrno>
#include <cstring>
#include <list>
#include <memory>
#include <stdexcept>
#include <thread>
#include <vector>
#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#else
#include <fcntl.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <unistd.h>
#endif

namespace aii::voice {
namespace {
struct Served {
  ChannelEnd end;
  std::unique_ptr<Link> link;
  std::thread thread;
  std::atomic<bool> done{false};
};
void close_end(const ChannelEnd& end) noexcept { close_channel_end(end); }

#ifdef _WIN32
bool receive(HANDLE in, ChannelEnd& got) {
  uint64_t values[2] = {};
  auto* p = reinterpret_cast<char*>(values);
  DWORD need = sizeof values;
  while (need) {
    DWORD n = 0;
    if (!ReadFile(in, p, need, &n, nullptr) || !n) return false;
    p += n; need -= n;
  }
  got = ChannelEnd{intptr_t(values[0]), intptr_t(values[1])};
  return true;
}
bool is_pipe(intptr_t value) {
  const auto h = reinterpret_cast<HANDLE>(value);
  return h && h != INVALID_HANDLE_VALUE && GetFileType(h) == FILE_TYPE_PIPE;
}
#else
constexpr size_t most_descriptors = 8;
void close_on_exec(int fd) {
  const int flags = ::fcntl(fd, F_GETFD);
  if (flags >= 0) (void)::fcntl(fd, F_SETFD, flags | FD_CLOEXEC);
}
bool is_pipe_end(int fd, int access) {
  struct stat st {};
  if (::fstat(fd, &st) != 0 || !S_ISFIFO(st.st_mode)) return false;
  const int flags = ::fcntl(fd, F_GETFL);
  return flags >= 0 && (flags & O_ACCMODE) == access;
}
bool receive(int control, std::vector<int>& got) {
  got.clear();
  char byte = 0;
  iovec io{&byte, 1};
  alignas(cmsghdr) char space[CMSG_SPACE(sizeof(int) * most_descriptors)];
  msghdr m{};
  m.msg_iov = &io; m.msg_iovlen = 1;
  m.msg_control = space; m.msg_controllen = sizeof space;
  ssize_t n;
#ifdef MSG_CMSG_CLOEXEC
  constexpr int flags = MSG_CMSG_CLOEXEC;
#else
  constexpr int flags = 0;
#endif
  do n = ::recvmsg(control, &m, flags); while (n < 0 && errno == EINTR);
  if (n <= 0) return false;
  for (auto* c = CMSG_FIRSTHDR(&m); c; c = CMSG_NXTHDR(&m, c)) {
    if (c->cmsg_level != SOL_SOCKET || c->cmsg_type != SCM_RIGHTS) continue;
    const size_t count = (c->cmsg_len - CMSG_LEN(0)) / sizeof(int);
    for (size_t i = 0; i < count; ++i) {
      int fd;
      std::memcpy(&fd, CMSG_DATA(c) + i * sizeof(int), sizeof fd);
      close_on_exec(fd);
      got.push_back(fd);
    }
  }
  return true;
}
#endif
}

#ifdef _WIN32
bool send_model_channel(ChannelEnd control, void* server, ChannelEnd theirs) {
  HANDLE in = nullptr, out = nullptr;
  if (!DuplicateHandle(GetCurrentProcess(), reinterpret_cast<HANDLE>(theirs.in), static_cast<HANDLE>(server), &in, 0, FALSE,
                       DUPLICATE_SAME_ACCESS) ||
      !DuplicateHandle(GetCurrentProcess(), reinterpret_cast<HANDLE>(theirs.out), static_cast<HANDLE>(server), &out, 0, FALSE,
                       DUPLICATE_SAME_ACCESS))
    throw std::runtime_error("model server: the channel's handles cannot be given to it");
  const uint64_t values[2] = {uint64_t(reinterpret_cast<uintptr_t>(in)), uint64_t(reinterpret_cast<uintptr_t>(out))};
  DWORD n = 0;
  if (!WriteFile(reinterpret_cast<HANDLE>(control.out), values, sizeof values, &n, nullptr) || n != sizeof values)
    throw std::runtime_error("model server control gone");
  char word = 0;
  if (!ReadFile(reinterpret_cast<HANDLE>(control.in), &word, 1, &n, nullptr) || n != 1 || (word != 'y' && word != 'n'))
    throw std::runtime_error("model server control gone");
  return word == 'y';
}
#else
bool send_model_channel(int control, ChannelEnd theirs) {
  char byte = 'c';
  iovec io{&byte, 1};
  const int ends[2] = {int(theirs.in), int(theirs.out)};
  alignas(cmsghdr) char space[CMSG_SPACE(sizeof ends)] = {};
  msghdr m{};
  m.msg_iov = &io; m.msg_iovlen = 1;
  m.msg_control = space; m.msg_controllen = sizeof space;
  auto* c = CMSG_FIRSTHDR(&m);
  c->cmsg_level = SOL_SOCKET; c->cmsg_type = SCM_RIGHTS; c->cmsg_len = CMSG_LEN(sizeof ends);
  std::memcpy(CMSG_DATA(c), ends, sizeof ends);
#ifdef MSG_NOSIGNAL
  constexpr int flags = MSG_NOSIGNAL;
#else
  constexpr int flags = 0;
#endif
  ssize_t n;
  do n = ::sendmsg(control, &m, flags); while (n < 0 && errno == EINTR);
  if (n != 1) throw std::runtime_error("model server control socket gone");
  char word = 0;
  do n = ::read(control, &word, 1); while (n < 0 && errno == EINTR);
  if (n != 1 || (word != 'y' && word != 'n')) throw std::runtime_error("model server control socket gone");
  return word == 'y';
}
#endif

void serve_model_server(StreamFactory& factory, ChannelEnd control, std::chrono::milliseconds retire, HungStream hung) {
#if !defined(_WIN32) && defined(SO_NOSIGPIPE)
  int on = 1;
  (void)::setsockopt(int(control.in), SOL_SOCKET, SO_NOSIGPIPE, &on, sizeof on);
#endif
  std::list<std::unique_ptr<Served>> live;
  const auto reap = [&] {
    for (auto it = live.begin(); it != live.end();) {
      if ((*it)->done) { (*it)->thread.join(); (*it)->link.reset(); it = live.erase(it); }
      else ++it;
    }
  };
  const auto say = [control](char word) {
#ifdef _WIN32
    DWORD n = 0;
    (void)WriteFile(reinterpret_cast<HANDLE>(control.out), &word, 1, &n, nullptr);
#elif defined(MSG_NOSIGNAL)
    (void)::send(int(control.out), &word, 1, MSG_NOSIGNAL);
#else
    (void)::send(int(control.out), &word, 1, 0);
#endif
  };
  const auto serve = [&](ChannelEnd end) {
    auto s = std::make_unique<Served>();
    s->end = end;
    try { s->link = std::make_unique<Link>(end); } catch (...) { close_end(end); say('n'); return; }
    auto* raw = s.get();
    live.push_back(std::move(s));
    raw->thread = std::thread([&factory, raw, retire, hung] {
      serve_model_channel(factory, *raw->link, retire, hung);
      close_end(raw->end);
      raw->done = true;
    });
    say('y');
  };
#ifdef _WIN32
  ChannelEnd got;
  while (receive(reinterpret_cast<HANDLE>(control.in), got)) {
    reap();
    bool ours = got.in == control.in || got.in == control.out || got.out == control.in || got.out == control.out ||
                got.in == got.out;
    for (const auto& s : live)
      ours = ours || got.in == s->end.in || got.in == s->end.out || got.out == s->end.in || got.out == s->end.out;
    if (ours || !is_pipe(got.in) || !is_pipe(got.out)) { say('n'); continue; }
    if (live.size() >= model_server_channel_limit) { close_end(got); say('n'); continue; }
    serve(got);
  }
#else
  std::vector<int> got;
  while (receive(int(control.in), got)) {
    reap();
    for (size_t i = 2; i < got.size(); ++i) ::close(got[i]);
    if (got.size() < 2) { for (int fd : got) ::close(fd); say('n'); continue; }
    const ChannelEnd end{got[0], got[1]};
    if (!is_pipe_end(got[0], O_RDONLY) || !is_pipe_end(got[1], O_WRONLY) || live.size() >= model_server_channel_limit) {
      close_end(end); say('n'); continue;
    }
    serve(end);
  }
#endif
  for (auto& s : live) s->link->end();
  for (auto& s : live) { s->thread.join(); s->link.reset(); }
}
}
