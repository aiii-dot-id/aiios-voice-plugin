#include "model_server_control.h"
#include "model_link.h"
#include <algorithm>
#include <atomic>
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <list>
#include <string>
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
#include <dirent.h>
#include <fcntl.h>
#include <sys/resource.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <unistd.h>
#endif

namespace aii::voice {
namespace {
void say_refused(const char* what) {
  std::string reason(what ? what : "unknown");
  for (auto& c : reason)
    if (c == '"' || c == '\\' || static_cast<unsigned char>(c) < 0x20) c = ' ';
  std::fprintf(stderr, "AII_VOICE_MODEL_SERVER {\"event\":\"channel_refused\",\"reason\":\"%s\"}\n", reason.c_str());
  std::fflush(stderr);
}
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

#ifndef _WIN32
std::uint64_t descriptor_scan_limit(std::uint64_t soft) { return std::min(soft, descriptor_scan_bound); }

DescriptorCount count_from_listing(const std::vector<std::string>& names, long own, bool read_failed) {
  DescriptorCount c;
  if (read_failed) return c;
  for (const auto& name : names) {
    if (name.empty() || name[0] < '0' || name[0] > '9') continue;
    char* end = nullptr;
    const long fd = std::strtol(name.c_str(), &end, 10);
    if (*end || fd == own) continue;
    ++c.open;
  }
  c.complete = true;
  return c;
}

namespace {
std::uint64_t soft_limit() {
  rlimit limit{};
  if (::getrlimit(RLIMIT_NOFILE, &limit) != 0) throw std::runtime_error("model server: its open-file limit cannot be read");
  return limit.rlim_cur == RLIM_INFINITY ? UINT64_MAX : std::uint64_t(limit.rlim_cur);
}
DescriptorCount scan_descriptors(std::uint64_t soft) {
  DescriptorCount c;
  c.scanned = descriptor_scan_limit(soft);
  for (std::uint64_t fd = 0; fd < c.scanned; ++fd) {
    errno = 0;
    if (::fcntl(int(fd), F_GETFD) == -1 && errno == EBADF) ++c.free_below;
    else ++c.open;
  }
  c.complete = c.scanned >= soft;
  return c;
}
}

DescriptorCount count_descriptors(bool listing) {
  if (listing) {
    if (DIR* d = ::opendir("/dev/fd")) {
      const long own = ::dirfd(d);
      std::vector<std::string> names;
      bool failed = false;
      for (;;) {
        errno = 0;
        const dirent* e = ::readdir(d);
        if (!e) { failed = errno != 0; break; }
        names.emplace_back(e->d_name);
      }
      ::closedir(d);
      const auto c = count_from_listing(names, own, failed);
      if (c.complete) return c;
    }
  }
  return scan_descriptors(soft_limit());
}

DescriptorBudget budget_for(const DescriptorCount& count, std::uint64_t soft, std::uint64_t hard, size_t channels) {
  const std::uint64_t want = model_server_descriptors_per_channel * channels + model_server_descriptors_per_message;
  DescriptorBudget b;
  b.open = count.open;
  b.need = count.open + want;
  b.soft = soft;
  b.hard = hard;
  if (!count.complete && count.free_below < want)
    throw DescriptorShortfall("model server: its open descriptors could not all be counted (a scan of " +
                              std::to_string(count.scanned) + "), and it saw only " + std::to_string(count.free_below) +
                              " free, fewer than the " + std::to_string(want) + " its " + std::to_string(channels) +
                              " channels (model_server_channels) need");
  if (b.hard < b.need)
    throw DescriptorShortfall("model server: its " + std::to_string(channels) + " channels (model_server_channels) need " +
                              std::to_string(b.need) + " open descriptors, and this process's hard open-file limit is " +
                              std::to_string(b.hard) + "; raise the hard limit or state fewer model_server_channels");
  return b;
}

DescriptorBudget reserve_model_server_descriptors(size_t channels) {
  rlimit limit{};
  if (::getrlimit(RLIMIT_NOFILE, &limit) != 0) throw std::runtime_error("model server: its open-file limit cannot be read");
  const auto wide = [](rlim_t v) { return v == RLIM_INFINITY ? UINT64_MAX : std::uint64_t(v); };
  DescriptorBudget b = budget_for(count_descriptors(), wide(limit.rlim_cur), wide(limit.rlim_max), channels);
  if (b.soft < b.need) {
    rlimit raised = limit;
    raised.rlim_cur = rlim_t(b.need);
    if (::setrlimit(RLIMIT_NOFILE, &raised) != 0)
      throw DescriptorShortfall("model server: its open-file limit could not be raised from " + std::to_string(b.soft) + " to " +
                                std::to_string(b.need) + ": " + std::strerror(errno));
    b.soft = b.need;
    b.raised = true;
  }
  return b;
}
#endif

void reserve_then_ready(size_t channels, const std::function<void(const DescriptorBudget&)>& ready) {
#ifdef _WIN32
  (void)channels;
  ready(DescriptorBudget{});
#else
  ready(reserve_model_server_descriptors(channels));
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

void serve_model_server(StreamFactory& factory, ChannelEnd control, size_t channels, std::chrono::milliseconds retire,
                        HungStream hung) {
#ifndef _WIN32
  (void)reserve_model_server_descriptors(channels);
#endif
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
    try {
      s->link = std::make_unique<Link>(end);
    } catch (const std::exception& e) {
      say_refused(e.what()); close_end(end); say('n'); return;
    } catch (...) {
      say_refused(nullptr); close_end(end); say('n'); return;
    }
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
    if (live.size() >= channels) { close_end(got); say('n'); continue; }
    serve(got);
  }
#else
  std::vector<int> got;
  while (receive(int(control.in), got)) {
    reap();
    for (size_t i = 2; i < got.size(); ++i) ::close(got[i]);
    if (got.size() < 2) { for (int fd : got) ::close(fd); say('n'); continue; }
    const ChannelEnd end{got[0], got[1]};
    if (!is_pipe_end(got[0], O_RDONLY) || !is_pipe_end(got[1], O_WRONLY) || live.size() >= channels) {
      close_end(end); say('n'); continue;
    }
    serve(end);
  }
#endif
  for (auto& s : live) s->link->end();
  for (auto& s : live) { s->thread.join(); s->link.reset(); }
}
}
