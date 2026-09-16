#include "readonly_model.h"
#include "startup_trace.h"
#include "../vendor/picosha2/picosha2.h"
#include <algorithm>
#include <array>
#include <cerrno>
#include <limits>
#include <stdexcept>
#include <system_error>
#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
// clang-format off: bcrypt.h needs the Win32 types from windows.h first.
#include <windows.h>
#include <bcrypt.h>
// clang-format on
#else
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>
#endif
#ifdef __APPLE__
#include <CommonCrypto/CommonDigest.h>
#endif
namespace aii::platform {
namespace {
[[maybe_unused]] std::string hex(const unsigned char *bytes) {
  constexpr char digits[] = "0123456789abcdef";
  std::string s;
  s.reserve(64);
  for (size_t i = 0; i < 32; ++i) {
    s += digits[bytes[i] >> 4];
    s += digits[bytes[i] & 15];
  }
  return s;
}
[[noreturn]] void failed(const char *operation) {
#ifdef _WIN32
  const auto code = GetLastError();
  throw std::system_error(int(code ? code : ERROR_GEN_FAILURE),
                          std::system_category(), operation);
#else
  const int code = errno;
  throw std::system_error(code ? code : EIO, std::generic_category(),
                          operation);
#endif
}
} // namespace
std::string portable_sha256(const void *data, size_t bytes) {
  if (bytes && !data)
    throw std::invalid_argument("missing hash bytes");
  const auto *p = bytes ? static_cast<const unsigned char *>(data)
                        : reinterpret_cast<const unsigned char *>("");
  return picosha2::hash256_hex_string(p, p + bytes);
}
std::string sha256(const void *data, size_t bytes) {
  StartupSpan timing("bound_model_sha256","native-model-hash-profile");
  const auto *p = static_cast<const unsigned char *>(data);
  if (bytes && !p)
    throw std::invalid_argument("missing hash bytes");
#if defined(__APPLE__)
  CC_SHA256_CTX ctx;
  std::array<unsigned char, 32> digest{};
  if (!CC_SHA256_Init(&ctx))
    throw std::runtime_error("SHA256 initialization failed");
  for (size_t i = 0; i < bytes;) {
    const auto n = std::min(bytes - i, size_t(8 * 1024 * 1024));
    if (!CC_SHA256_Update(&ctx, p + i, CC_LONG(n)))
      throw std::runtime_error("SHA256 update failed");
    i += n;
  }
  if (!CC_SHA256_Final(digest.data(), &ctx))
    throw std::runtime_error("SHA256 finish failed");
  return hex(digest.data());
#elif defined(_WIN32)
  struct Hash {
    BCRYPT_ALG_HANDLE algorithm = nullptr;
    BCRYPT_HASH_HANDLE value = nullptr;
    ~Hash() {
      if (value)
        BCryptDestroyHash(value);
      if (algorithm)
        BCryptCloseAlgorithmProvider(algorithm, 0);
    }
  } hash;
  const auto check = [](NTSTATUS s) {
    if (s < 0)
      throw std::system_error(int(s), std::system_category(), "native SHA256");
  };
  check(BCryptOpenAlgorithmProvider(&hash.algorithm, BCRYPT_SHA256_ALGORITHM,
                                    nullptr, 0));
  check(
      BCryptCreateHash(hash.algorithm, &hash.value, nullptr, 0, nullptr, 0, 0));
  for (size_t i = 0; i < bytes;) {
    const auto n = std::min(bytes - i, size_t(8 * 1024 * 1024));
    check(BCryptHashData(hash.value, const_cast<PUCHAR>(p + i), ULONG(n), 0));
    i += n;
  }
  std::array<unsigned char, 32> digest{};
  check(BCryptFinishHash(hash.value, digest.data(), ULONG(digest.size()), 0));
  return hex(digest.data());
#else
  return portable_sha256(data, bytes);
#endif
}
struct ReadonlyModel::State {
  const unsigned char *view = nullptr;
  size_t size = 0;
#ifdef _WIN32
  HANDLE file = INVALID_HANDLE_VALUE, mapping = nullptr;
  BY_HANDLE_FILE_INFORMATION before{};
  ~State() {
    if (view && !UnmapViewOfFile(view))
      std::terminate();
    if (mapping && !CloseHandle(mapping))
      std::terminate();
    if (file != INVALID_HANDLE_VALUE && !CloseHandle(file))
      std::terminate();
  }
#else
  int file = -1;
  struct stat before{};
  ~State() {
    if (view && munmap(const_cast<unsigned char *>(view), size))
      std::terminate();
    if (file >= 0)
      close(file);
  }
#endif
};
ReadonlyModel::ReadonlyModel(const std::filesystem::path &path, uint64_t size,
                             const std::string &digest)
    : state_(std::make_unique<State>()) {
  if (!size || size > size_t(-1) || digest.size() != 64)
    throw std::invalid_argument("bound model extent/hash required");
  auto &s = *state_;
  s.size = size_t(size);
#ifdef _WIN32
  // OPEN_REPARSE_POINT rejects the link itself. Denying share-write/delete
  // keeps this exact file stable throughout its mapping/session lifetime.
  s.file = CreateFileW(
      path.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING,
      FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_SEQUENTIAL_SCAN, nullptr);
  if (s.file == INVALID_HANDLE_VALUE)
    failed("open bound model");
  if (!GetFileInformationByHandle(s.file, &s.before))
    failed("inspect bound model");
  if (GetFileType(s.file) != FILE_TYPE_DISK ||
      (s.before.dwFileAttributes &
       (FILE_ATTRIBUTE_REPARSE_POINT | FILE_ATTRIBUTE_DIRECTORY)) ||
      (uint64_t(s.before.nFileSizeHigh) << 32 | s.before.nFileSizeLow) != size)
    throw std::runtime_error("bound model type/extent differs");
  s.mapping = CreateFileMappingW(s.file, nullptr, PAGE_READONLY, 0, 0, nullptr);
  if (!s.mapping)
    failed("map bound model");
  s.view = static_cast<const unsigned char *>(
      MapViewOfFile(s.mapping, FILE_MAP_READ, 0, 0, 0));
  if (!s.view)
    failed("view bound model");
#else
  s.file = open(path.c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
  if (s.file < 0)
    failed("open bound model");
  if (fstat(s.file, &s.before))
    failed("inspect bound model");
  if (!S_ISREG(s.before.st_mode) || s.before.st_size < 0 ||
      uint64_t(s.before.st_size) != size)
    throw std::runtime_error("bound model type/extent differs");
  const auto view = mmap(nullptr, s.size, PROT_READ, MAP_PRIVATE, s.file, 0);
  if (view == MAP_FAILED)
    failed("map bound model");
  s.view = static_cast<const unsigned char *>(view);
#endif
  if (sha256(s.view, s.size) != digest)
    throw std::runtime_error("bound model SHA256 differs: " +
                             path.filename().u8string());
  check_unchanged();
}
ReadonlyModel::~ReadonlyModel() = default;
const unsigned char *ReadonlyModel::data() const noexcept {
  return state_->view;
}
size_t ReadonlyModel::size() const noexcept { return state_->size; }
void ReadonlyModel::check_unchanged() const {
  const auto &s = *state_;
#ifdef _WIN32
  BY_HANDLE_FILE_INFORMATION after{};
  if (!GetFileInformationByHandle(s.file, &after))
    failed("read back bound model identity");
  if (after.dwVolumeSerialNumber != s.before.dwVolumeSerialNumber ||
      after.nFileIndexHigh != s.before.nFileIndexHigh ||
      after.nFileIndexLow != s.before.nFileIndexLow ||
      after.nFileSizeHigh != s.before.nFileSizeHigh ||
      after.nFileSizeLow != s.before.nFileSizeLow ||
      CompareFileTime(&after.ftLastWriteTime, &s.before.ftLastWriteTime) != 0)
    throw std::runtime_error("bound model changed during use");
#else
  struct stat after{};
  if (fstat(s.file, &after))
    failed("read back bound model identity");
#ifdef __APPLE__
  const bool time_changed =
      after.st_mtimespec.tv_sec != s.before.st_mtimespec.tv_sec ||
      after.st_mtimespec.tv_nsec != s.before.st_mtimespec.tv_nsec ||
      after.st_ctimespec.tv_sec != s.before.st_ctimespec.tv_sec ||
      after.st_ctimespec.tv_nsec != s.before.st_ctimespec.tv_nsec;
#else
  const bool time_changed = after.st_mtim.tv_sec != s.before.st_mtim.tv_sec ||
                            after.st_mtim.tv_nsec != s.before.st_mtim.tv_nsec ||
                            after.st_ctim.tv_sec != s.before.st_ctim.tv_sec ||
                            after.st_ctim.tv_nsec != s.before.st_ctim.tv_nsec;
#endif
  if (after.st_dev != s.before.st_dev || after.st_ino != s.before.st_ino ||
      after.st_size != s.before.st_size || time_changed)
    throw std::runtime_error("bound model changed during use");
#endif
}
} // namespace aii::platform
