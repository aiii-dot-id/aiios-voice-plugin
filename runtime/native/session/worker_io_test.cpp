#include "worker_io.h"
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <stdexcept>
#include <thread>
#include <vector>
#ifdef _WIN32
#define NOMINMAX
#include <fcntl.h>
#include <io.h>
#include <crtdbg.h>
#include <windows.h>
#else
#include <csignal>
#include <unistd.h>
#endif
using aii::voice::wire::Pipe;
using Clock = std::chrono::steady_clock;
#define REQUIRE(x)                                                             \
  do {                                                                         \
    if (!(x)) {                                                                \
      std::fprintf(stderr, "%d: %s\n", __LINE__, #x);                          \
      std::abort();                                                            \
    }                                                                          \
  } while (0)
struct Pair {
  std::unique_ptr<Pipe> in, out;
  // The limit of these tests' own writer; the pipe has none of its own.
  explicit Pair(std::chrono::milliseconds write_limit = std::chrono::seconds(3)) {
    int a, b;
#ifdef _WIN32
    HANDLE read, write;
    REQUIRE(CreatePipe(&read, &write, nullptr, 4096));
    a = _open_osfhandle(intptr_t(read), _O_BINARY | _O_RDONLY);
    b = _open_osfhandle(intptr_t(write), _O_BINARY | _O_WRONLY);
#else
    int pair[2];
    REQUIRE(pipe(pair) == 0);
    a = pair[0];
    b = pair[1];
#endif
    REQUIRE(a >= 0 && b >= 0);
    in = std::make_unique<Pipe>(a);
    out = std::make_unique<Pipe>(b, write_limit, "test_write_ms");
  }
};
#ifdef _WIN32
namespace aii::voice::wire {
std::atomic<bool> hold_read{false}, reached_read{false}, release_read{false};
void before_blocking_read() {
  if (!hold_read) return;
  reached_read = true;
  while (!release_read) std::this_thread::yield();
}
}
double cpu_seconds(HANDLE thread) {
  FILETIME created, exited, kernel, user;
  REQUIRE(GetThreadTimes(thread, &created, &exited, &kernel, &user));
  auto ticks=[](FILETIME t) {return (uint64_t(t.dwHighDateTime)<<32)|t.dwLowDateTime;};
  return double(ticks(kernel)+ticks(user))/10000000.;
}
#endif
int main(int argc, char **argv) {
#ifdef _WIN32
  // A failing falsifier is an exit, not an interactive Windows Error Reporting
  // process that outlives the test. These settings apply only to this test.
  SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX);
  _set_abort_behavior(0, _WRITE_ABORT_MSG | _CALL_REPORTFAULT);
#endif
#ifndef _WIN32
  std::signal(SIGPIPE, SIG_IGN);
#endif
#ifdef _WIN32
  const bool idle = argc == 2 && std::strcmp(argv[1], "idle") == 0;
  const bool race = argc == 2 && std::strcmp(argv[1], "cancel-race") == 0;
  if (idle || race) {
    Pair p;
    std::atomic<bool> stop{false}, done{false}, failed{false};
    aii::voice::wire::hold_read = race;
    std::thread reader([&] {
      char c;
      try {p.in->exact(&c, 1, stop);} catch (...) {failed = true;}
      done = true;
    });
    if (race) {
      const auto limit = Clock::now()+std::chrono::seconds(1);
      while (!aii::voice::wire::reached_read) {
        if (Clock::now()>limit) std::_Exit(76);
        std::this_thread::yield();
      }
      stop = true;
      p.in->interrupt(); // Deliberately too early: no ReadFile is pending yet.
      aii::voice::wire::release_read = true;
    } else {
      std::this_thread::sleep_for(std::chrono::milliseconds(50));
      const auto begin = Clock::now();
      const auto before = cpu_seconds(reader.native_handle());
      std::this_thread::sleep_for(std::chrono::seconds(1));
      const auto used = cpu_seconds(reader.native_handle())-before;
      const auto elapsed = std::chrono::duration<double>(Clock::now()-begin).count();
      std::printf("idle reader: cpu_seconds=%.6f wall_seconds=%.6f mean_cores=%.6f\n", used, elapsed, used/elapsed);
      std::fflush(stdout);
      REQUIRE(!done && used/elapsed <= .05);
      stop = true;
    }
    const auto limit = Clock::now()+std::chrono::seconds(1);
    while (!done) {
      p.in->interrupt();
      if (Clock::now()>limit) std::_Exit(77);
      std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    reader.join();
    REQUIRE(failed);
    if (race) REQUIRE(p.in->read_operations()==1);
    std::puts("native idle/read retirement PASS");
    return 0;
  }
#endif
  const bool deadline = argc == 2 && std::strcmp(argv[1], "deadline") == 0;
  if (deadline) {
    // A write that cannot move expires at the limit its owner gave the pipe,
    // whatever that is. It was three seconds typed in the pipe.
    for (const auto limit : {std::chrono::milliseconds(1500), std::chrono::milliseconds(400)}) {
      Pair p(limit);
      std::atomic<bool> stop{false}, done{false}, failed{false};
      std::vector<char> huge(16 * 1024 * 1024, 'x');
      std::string said;
      const auto begin = Clock::now();
      std::thread writer([&] {
        try {
          p.out->write(huge.data(), huge.size(), stop);
        } catch (const std::exception &e) {
          said = e.what();
          failed = true;
        }
        done = true;
      });
      while (!done) {
        if (p.out->expired())
          p.out->interrupt();
        if (Clock::now() - begin > limit + std::chrono::seconds(1))
          std::_Exit(73);
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
      }
      writer.join();
      REQUIRE(failed && Clock::now() - begin >= limit);
      // What is said of it names the limit and the member its owner gave,
      // and is the one sentence whichever thread sees the deadline: the
      // owner reads it from the pipe, and where the writing thread is the one
      // that sees it (not inside a blocked write) that thread says the same.
      const std::string expiry = "native pipe write expired: " + std::to_string(limit.count()) +
                                 " ms, the time the limits table gives it (test_write_ms)";
      REQUIRE(p.out->expiry() == expiry);
#ifndef _WIN32
      REQUIRE(said == expiry);
#endif
    }
    // A limit that is not named is refused, before the descriptor is touched:
    // an expiry could not say which limit it was.
    for (const char *name : {"", static_cast<const char *>(nullptr)}) {
      bool unnamed = false;
      try {
        Pipe out(-1, std::chrono::milliseconds(400), name);
      } catch (const std::exception &e) {
        unnamed = std::string(e.what()) == "a pipe's write limit must be named";
      }
      REQUIRE(unnamed);
    }
    bool refused = false;
    try {
      (void)Pair(std::chrono::milliseconds(0));
    } catch (const std::exception &) {
      refused = true;
    }
    REQUIRE(refused);
    std::puts(
        "full pipe: deadline faults and retires its actual write owner PASS");
    return 0;
  }
  {
    Pair p;
    std::atomic<bool> stop{false};
    p.out->write("ok\n", 3, stop);
    std::string s;
    REQUIRE(p.in->line(s, stop) && s == "ok");
    p.out.reset();
    REQUIRE(!p.in->line(s, stop));
  }
  {
    Pair p;std::atomic<bool> stop{false};std::string payload(128*1024,'x');payload+="\nnext\nraw";
    std::thread writer([&]{p.out->write(payload.data(),payload.size(),stop);p.out.reset();});
    std::string line;REQUIRE(p.in->line(line,stop) && line==std::string(128*1024,'x'));
    REQUIRE(p.in->line(line,stop) && line=="next");
    char raw[3];REQUIRE(p.in->exact(raw,3,stop) && !std::memcmp(raw,"raw",3));
    REQUIRE(!p.in->line(line,stop));writer.join();
    // Deterministic work bound, not a wall-clock guess that varies with a VM.
    REQUIRE(p.in->read_operations()<512);
  }
  {
    Pair p;std::atomic<bool> stop{false};p.out->write("unfinished",10,stop);p.out.reset();
    std::string line;bool failed=false;try{p.in->line(line,stop);}catch(...){failed=true;}REQUIRE(failed);
  }
  {
    Pair p;
    std::atomic<bool> stop{false};
    p.out->write("x", 1, stop);
    p.out.reset();
    char bytes[2];
    bool failed = false;
    try {
      p.in->exact(bytes, 2, stop, true);
    } catch (...) {
      failed = true;
    }
    REQUIRE(failed);
  }
  {
    Pair p;
    std::atomic<bool> stop{false}, done{false}, failed{false};
    std::thread reader([&] {
      char c;
      try {
        p.in->exact(&c, 1, stop);
      } catch (...) {
        failed = true;
      }
      done = true;
    });
    std::this_thread::sleep_for(std::chrono::milliseconds(5));
    stop = true;
    const auto limit = Clock::now() + std::chrono::seconds(1);
    while (!done) {
      p.in->interrupt();
      if (Clock::now() > limit)
        std::_Exit(74);
      std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    reader.join();
    REQUIRE(failed);
  }
  {
    Pair p;
    std::atomic<bool> stop{false}, done{false}, failed{false};
    std::vector<char> huge(16 * 1024 * 1024, 'x');
    std::string said;
    std::thread writer([&] {
      try {
        p.out->write(huge.data(), huge.size(), stop);
      } catch (const std::exception &e) {
        said = e.what();
        failed = true;
      }
      done = true;
    });
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
    REQUIRE(!done);
    stop = true;
    const auto limit = Clock::now() + std::chrono::seconds(1);
    while (!done) {
      p.out->interrupt();
      if (Clock::now() > limit)
        std::_Exit(75);
      std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    writer.join();
    REQUIRE(failed);
#ifndef _WIN32
    // Stopped by its owner inside its time: not an expiry, and no limit is named.
    REQUIRE(said == "native pipe write interrupted");
#endif
  }
  std::puts("native pipe: exact EOF, truncation, blocked read/write "
            "interruption PASS");
}
