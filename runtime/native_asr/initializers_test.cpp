// Small sealed fixtures exercise the same loader; no production pin is changed.
#include "initializers.h"
#include <chrono>
#include <cstring>
#include <fstream>
#include <functional>
#include <iostream>
#include <random>
#include <stdexcept>
#ifdef _WIN32
#define NOMINMAX
#include <windows.h>
#else
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>
#endif
namespace fs = std::filesystem;
using aii::asr::Binding;
using aii::platform::ReadonlyModel;
using aii::platform::sha256;
void require(bool b, const std::string &reason) {
  if (!b)
    throw std::runtime_error(reason);
}
template <class F> void rejects(const char *name, F call, const char *reason) {
  bool refused = false;
  try {
    call();
  } catch (const std::exception &e) {
    refused = true;
    require(std::string(e.what()).find(reason) != std::string::npos,
            std::string(name) + " wrong rejection: " + e.what());
  }
  require(refused, std::string(name) + " accepted invalid model");
  std::cout << name << ": refused by " << reason << '\n';
}
void replace(std::string &s, const std::string &a, const std::string &b) {
  const auto i = s.find(a);
  require(i != std::string::npos, "fixture mutation target absent");
  s.replace(i, a.size(), b);
}
void write(const fs::path &p, const std::string &bytes) {
  std::ofstream f(p, std::ios::binary | std::ios::trunc);
  f.write(bytes.data(), std::streamsize(bytes.size()));
  f.close();
  require(bool(f), "fixture write failed");
}
aii::asr::Pin pin(const std::string &s) {
  return {s.size(), sha256(s.data(), s.size())};
}
struct Fixture {
  fs::path root;
  std::string graph = "test graph bytes", checkpoint, index;
  Binding binding;
  Fixture(const fs::path &parent, const std::string &name,
          const std::function<void(std::string &)> &header_change = {},
          const std::function<void(std::string &)> &index_change = {}) {
    root = parent / name;
    require(fs::create_directory(root), "fresh fixture required");
    std::string header =
        R"({"a":{"dtype":"F32","shape":[2,2],"data_offsets":[0,16]},"b":{"dtype":"F32","shape":[2,2],"data_offsets":[16,32]}})";
    if (header_change)
      header_change(header);
    while (header.size() % 8)
      header += ' ';
    for (unsigned i = 0; i < 8; ++i)
      checkpoint += char(uint64_t(header.size()) >> (8 * i));
    checkpoint += header;
    const float values[] = {1, 2, 3, 4, 5, 6, 7, 8};
    checkpoint.append(reinterpret_cast<const char *>(values), sizeof values);
    const auto cp = pin(checkpoint);
    index = "{\"checkpoint_sha256\":\"" + cp.sha +
            "\",\"checkpoint_bytes\":" + std::to_string(cp.bytes) +
            ",\"initializers\":[{\"initializer\":\"left\",\"reference\":\"a\","
            "\"transform\":\"identity\",\"offset\":" +
            std::to_string(8 + header.size()) +
            ",\"bytes\":16},{\"initializer\":\"right\",\"reference\":\"b\","
            "\"transform\":\"transpose\",\"offset\":" +
            std::to_string(24 + header.size()) + ",\"bytes\":16}]}";
    if (index_change)
      index_change(index);
    binding = {pin(graph), pin(index), cp, 2, 1};
    write(root / "encoder.onnx", graph);
    write(root / "weight-view.json", index);
    write(root / "model.safetensors", checkpoint);
  }
  void open() {
    aii::asr::Initializers values(root, binding);
    values.check_unchanged();
  }
};
void session_owns_initializer_copy(Ort::Env &env, const fs::path &root) {
  // ONNX IR 9 / opset 18: Add(left, right/reference_storage) -> sum.
  // Both inputs are external float[2,2] initializers. The deliberately absent
  // external filename must never be opened: the sealed fixture supplies them.
  // Literal protobuf keeps this native test independent of Python/onnx tools.
  static const unsigned char graph[] = {
      8,9,58,130,2,10,41,10,4,108,101,102,116,10,23,114,105,103,104,116,47,
      114,101,102,101,114,101,110,99,101,95,115,116,111,114,97,103,101,18,3,
      115,117,109,34,3,65,100,100,18,13,99,111,112,121,95,108,105,102,101,
      116,105,109,101,42,77,8,2,8,2,16,1,66,4,108,101,102,116,112,1,106,
      34,10,8,108,111,99,97,116,105,111,110,18,22,109,117,115,116,45,110,
      111,116,45,98,101,45,111,112,101,110,101,100,46,98,105,110,106,11,
      10,6,111,102,102,115,101,116,18,1,48,106,12,10,6,108,101,110,103,
      116,104,18,2,49,54,42,96,8,2,8,2,16,1,66,23,114,105,103,104,116,
      47,114,101,102,101,114,101,110,99,101,95,115,116,111,114,97,103,101,
      112,1,106,34,10,8,108,111,99,97,116,105,111,110,18,22,109,117,115,
      116,45,110,111,116,45,98,101,45,111,112,101,110,101,100,46,98,105,
      110,106,11,10,6,111,102,102,115,101,116,18,1,48,106,12,10,6,108,
      101,110,103,116,104,18,2,49,54,98,21,10,3,115,117,109,18,14,10,
      12,8,1,18,8,10,2,8,2,10,2,8,2,66,2,16,18};
  Fixture fixture(root, "session-copy");
  fixture.graph.assign(reinterpret_cast<const char *>(graph), sizeof graph);
  fixture.binding.encoder = pin(fixture.graph);
  write(fixture.root / "encoder.onnx", fixture.graph);
  Ort::Session session{nullptr};
  {
    aii::asr::Initializers values(fixture.root, fixture.binding);
    Ort::SessionOptions options;
    options.SetIntraOpNumThreads(1);
    options.SetInterOpNumThreads(1);
    // Do not let constant folding turn the test into a stored output tensor.
    options.SetGraphOptimizationLevel(GraphOptimizationLevel::ORT_DISABLE_ALL);
    values.attach(options);
    session = Ort::Session(env, values.encoder.data(), values.encoder.size(), options);
    values.check_unchanged();
  } // Both the referring options and every original mapping are gone.
  const char *output = "sum";
  auto results = session.Run(Ort::RunOptions{nullptr}, nullptr, nullptr, 0, &output, 1);
  require(results.size() == 1 && results[0].IsTensor(), "copied initializer output absent");
  auto info = results[0].GetTensorTypeAndShapeInfo();
  require(info.GetElementType() == ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT &&
              info.GetShape() == std::vector<int64_t>{2, 2},
          "copied initializer output shape/type differs");
  const float expected[] = {6, 8, 10, 12};
  require(std::memcmp(results[0].GetTensorData<float>(), expected, sizeof expected) == 0,
          "session lost initializer values after original mappings retired");
  std::cout << "session owns external initializer copy after mappings retire: passed\n";
}
size_t handles() {
#ifdef _WIN32
  DWORD n = 0;
  require(GetProcessHandleCount(GetCurrentProcess(), &n),
          "handle census failed");
  return n;
#else
  size_t n = 0;
  for (int fd = 0; fd < 8192; ++fd)
    if (fcntl(fd, F_GETFD) >= 0)
      ++n;
  return n;
#endif
}
void readonly(const ReadonlyModel &map) {
#ifdef _WIN32
  MEMORY_BASIC_INFORMATION info{};
  require(VirtualQuery(map.data(), &info, sizeof info) == sizeof info &&
              info.Protect == PAGE_READONLY,
          "mapped weights writable");
#else
  // Attempt the forbidden write in an isolated child, never the test owner.
  const pid_t child = fork();
  require(child >= 0, "write probe fork failed");
  if (!child) {
    rlimit limit{0, 0};
    setrlimit(RLIMIT_CORE, &limit);
    *const_cast<volatile unsigned char *>(map.data()) = 7;
    _exit(0);
  }
  int status = 0;
  require(waitpid(child, &status, 0) == child, "write probe wait failed");
  require(WIFSIGNALED(status) &&
              (WTERMSIG(status) == SIGSEGV || WTERMSIG(status) == SIGBUS),
          "mapped weights writable");
#endif
}
int main() {
  fs::path root;
  try {
    Ort::Env env(ORT_LOGGING_LEVEL_ERROR, "sealed-fixture");
    env.DisableTelemetryEvents();
    root = fs::temp_directory_path() /
           ("aii-sealed-" + std::to_string(std::random_device{}()) + "-" +
            std::to_string(
                std::chrono::steady_clock::now().time_since_epoch().count()));
    require(fs::create_directory(root), "fresh test root required");
    for (const auto &v :
         {std::pair<std::string, std::string>{
              "", "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852"
                  "b855"},
          {"abc",
           "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"},
          {std::string(1000000, 'a'), "cdc76e5c9914fb9281a1c7e284d73e67f1809a48"
                                      "a497200e046d39ccc7112cd0"}}) {
      require(sha256(v.first.data(), v.first.size()) == v.second,
              "native SHA vector differs");
      require(aii::platform::portable_sha256(v.first.data(), v.first.size()) ==
                  v.second,
              "portable SHA vector differs");
    }
    std::string multi(8 * 1024 * 1024 + 31, 'q');
    require(sha256(multi.data(), multi.size()) ==
                aii::platform::portable_sha256(multi.data(), multi.size()),
            "chunk-boundary SHA differs");
    rejects("null hash", [] { sha256(nullptr, 1); }, "missing hash");
    rejects(
        "null portable hash",
        [] { aii::platform::portable_sha256(nullptr, 1); }, "missing hash");
    Fixture good(root, "good");
    {
      aii::asr::Initializers values(good.root, good.binding);
      require(values.names ==
                  std::vector<std::string>{"left", "right/reference_storage"},
              "tensor identity differs");
      require(values.values[0].GetTensorData<float>()[0] == 1 &&
                  values.values[1].GetTensorData<float>()[3] == 8,
              "tensor span differs");
      require(values.values[1].GetTensorTypeAndShapeInfo().GetShape() ==
                  std::vector<int64_t>{2, 2},
              "original tensor shape lost");
      Ort::SessionOptions options;
      values.attach(options);
      readonly(values.checkpoint);
      values.check_unchanged();
    }
    session_owns_initializer_copy(env, root);
    const auto baseline = handles();
    for (int i = 0; i < 40; ++i)
      good.open();
    require(handles() == baseline, "successful model mappings leaked handles");
    struct Bad {
      const char *name, *from, *to, *reason;
      bool header;
    };
    for (const auto &b :
         {Bad{"dtype", "\"F32\"", "\"F16\"", "dtype differs", true},
          {"shape", "[2,2]", "[2,3]", "span exceeds", true},
          {"negative", "[2,2]", "[-2,2]", "whole number", true},
          {"fraction", "[2,2]", "[2.5,2]", "whole number", true},
          {"overflow", "[2,2]", "[9007199254740991,2]", "shape overflow", true},
          {"offset", "[0,16]", "[0,99999]", "span exceeds", true},
          {"reversed", "[0,16]", "[16,0]", "span exceeds", true},
          {"duplicate-key", "\"dtype\":\"F32\"",
           "\"dtype\":\"F32\",\"dtype\":\"F32\"", "duplicate", true},
          {"nul-name", "\"a\":", "\"a\\u0000\":", "NUL", true},
          {"missing-tensor", "\"reference\":\"a\"", "\"reference\":\"absent\"",
           "missing", false},
          {"unproved-transform", "\"identity\"", "\"flip\"", "unproved", false},
          {"duplicate-ref", "\"reference\":\"b\"", "\"reference\":\"a\"",
           "duplicate", false},
          {"bytes-mismatch", "\"bytes\":16", "\"bytes\":12", "span/layout",
           false},
          {"missing-array", "\"initializers\":", "\"other\":", "missing",
           false}}) {
      const auto mutate = [&](std::string &s) { replace(s, b.from, b.to); };
      Fixture f(root, b.name,
                b.header ? std::function<void(std::string &)>(mutate) : nullptr,
                b.header ? nullptr
                         : std::function<void(std::string &)>(mutate));
      rejects(b.name, [&] { f.open(); }, b.reason);
    }
    Fixture count(root, "count");
    count.binding.count = 3;
    rejects("census", [&] { count.open(); }, "census");
    Fixture transpose(root, "transpose");
    transpose.binding.transposes = 0;
    rejects("transpose census", [&] { transpose.open(); }, "transpose census");
    Fixture collision(root, "collision", {}, [](std::string &s) {
      replace(s, "\"left\"", "\"right/reference_storage\"");
    });
    rejects("effective name collision", [&] { collision.open(); }, "duplicate");
    Fixture binding(root, "binding", {}, [](std::string &s) {
      replace(s, "\"checkpoint_sha256\":\"", "\"checkpoint_sha256\":\"bad");
    });
    rejects(
        "checkpoint binding", [&] { binding.open(); }, "checkpoint binding");
    for (const auto *name :
         {"encoder.onnx", "weight-view.json", "model.safetensors"}) {
      Fixture f(root, std::string("corrupt-") + name);
      const std::string &original =
          std::string(name) == "encoder.onnx"       ? f.graph
          : std::string(name) == "weight-view.json" ? f.index
                                                    : f.checkpoint;
      auto corrupt = original;
      corrupt.back() ^= 1;
      write(f.root / name, corrupt);
      const auto before = handles();
      for (int i = 0; i < 20; ++i)
        rejects(name, [&] { f.open(); }, "SHA256");
      require(handles() == before, "refused model mappings leaked handles");
    }
    const auto p = good.root / "encoder.onnx";
    rejects(
        "wrong size",
        [&] {
          ReadonlyModel m(p, good.graph.size() + 1, good.binding.encoder.sha);
        },
        "extent");
    rejects(
        "directory",
        [&] { ReadonlyModel m(good.root, 1, good.binding.encoder.sha); }, "");
#ifndef _WIN32
    fs::create_symlink(p, root / "symlink");
    rejects(
        "symlink",
        [&] {
          ReadonlyModel m(root / "symlink", good.graph.size(),
                          good.binding.encoder.sha);
        },
        "open");
#ifdef __ANDROID__
    // The physical Pixel's shell policy refuses creation of FIFO inodes.
    // Exercise the same non-regular-file rejection with an existing device;
    // FIFO-specific nonblocking behavior is proved on Mac and Ubuntu, not here.
    rejects("nonregular device", [&] { ReadonlyModel m("/dev/null", 1, good.binding.encoder.sha); }, "type/extent");
    std::cout << "Android nonregular specimen: device file; no FIFO claim\n";
#else
    require(mkfifo((root / "fifo").c_str(), 0600) == 0, "FIFO fixture failed");
    rejects(
        "FIFO",
        [&] { ReadonlyModel m(root / "fifo", 1, good.binding.encoder.sha); },
        "type/extent");
#endif
    {
      ReadonlyModel m(p, good.graph.size(), good.binding.encoder.sha);
      auto altered = good.graph;
      altered.back() ^= 1;
      write(p, altered);
      rejects(
          "changed opened file", [&] { m.check_unchanged(); },
          "changed during use");
    }
#else
    {
      ReadonlyModel m(p, good.graph.size(), good.binding.encoder.sha);
      HANDLE other =
          CreateFileW(p.c_str(), GENERIC_WRITE,
                      FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                      nullptr, OPEN_EXISTING, 0, nullptr);
      require(other == INVALID_HANDLE_VALUE &&
                  GetLastError() == ERROR_SHARING_VIOLATION,
              "mapped file allowed concurrent writer");
    }
    HANDLE retired =
        CreateFileW(p.c_str(), GENERIC_WRITE,
                    FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                    nullptr, OPEN_EXISTING, 0, nullptr);
    require(retired != INVALID_HANDLE_VALUE, "mapping not retired");
    CloseHandle(retired);
#endif
    // Only this invocation's freshly created, bounded fixture tree is removed.
    fs::remove_all(root);
    std::cout << "sealed initializer contracts passed\n";
    return 0;
  } catch (const std::exception &e) {
    std::cerr << e.what() << " (fixtures retained at " << root << ")\n";
    return 1;
  }
}
