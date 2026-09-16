#pragma once

// Paths used for I/O are not physical comparison keys. In a Windows
// AppContainer, the file can be readable while DOS mount-name resolution is
// denied. Resolve the opened object's NT name without asking the mount manager;
// keep a usable DOS/UNC spelling for inference libraries. Never cache authority.
#include <filesystem>
#include <stdexcept>
#include <string>
#include <system_error>
#include <vector>

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#endif

namespace aii::platform {
namespace detail {
constexpr std::size_t max_path_units = 65536;

// Query returns characters excluding the terminator on success, or a required
// capacity on overflow. It must throw the original OS error on failure.
template<class Query> std::wstring bounded_name(Query query) {
    std::size_t capacity = 512;
    while (capacity <= max_path_units) {
        std::vector<wchar_t> buffer(capacity, L'\0');
        const auto count = static_cast<std::size_t>(query(buffer.data(), capacity));
        if (count == 0) throw std::runtime_error("empty physical path result");
        if (count < capacity) {
            if (buffer[count] != L'\0') throw std::runtime_error("unterminated physical path result");
            return std::wstring(buffer.data(), count);
        }
        if (count >= max_path_units) break;
        capacity = count + 1;
    }
    throw std::length_error("physical path exceeds bounded buffer");
}

#ifdef _WIN32
[[noreturn]] inline void win_error(const char* operation) {
    const auto code = GetLastError();
    throw std::system_error(static_cast<int>(code ? code : ERROR_GEN_FAILURE),
                            std::system_category(), operation);
}

class Handle {
public:
    explicit Handle(HANDLE value) : value_(value) {}
    ~Handle() { if (value_ != INVALID_HANDLE_VALUE) CloseHandle(value_); }
    Handle(const Handle&) = delete;
    Handle& operator=(const Handle&) = delete;
    HANDLE get() const { return value_; }
    void close() {
        const auto held = value_;
        if (!CloseHandle(held)) win_error("close physical path handle");
        value_ = INVALID_HANDLE_VALUE;
    }
private:
    HANDLE value_;
};

inline std::wstring dos_input(std::wstring value) {
    if (value.empty() || value.find(L'\0') != std::wstring::npos)
        throw std::invalid_argument("empty or NUL-containing path");
    if (value.rfind(L"\\\\.\\", 0) == 0 || value.rfind(L"\\??\\", 0) == 0)
        throw std::invalid_argument("device namespace is not an input path");
    if (value.rfind(L"\\\\?\\", 0) == 0) {
        const auto rest = value.substr(4);
        if (rest.rfind(L"UNC\\", 0) == 0 || rest.rfind(L"unc\\", 0) == 0)
            return L"\\\\" + rest.substr(4);
        const bool drive = rest.size() >= 3 &&
            ((rest[0] >= L'A' && rest[0] <= L'Z') || (rest[0] >= L'a' && rest[0] <= L'z')) &&
            rest[1] == L':' && rest[2] == L'\\';
        if (!drive) throw std::invalid_argument("unsupported extended input namespace");
        return rest;
    }
    return value;
}

struct Resolved { std::filesystem::path io, physical; };

inline Resolved resolve(const std::filesystem::path& path) {
    const auto input = dos_input(path.native());
    auto full = bounded_name([&](wchar_t* out, std::size_t size) {
        const auto n = GetFullPathNameW(input.c_str(), static_cast<DWORD>(size), out, nullptr);
        if (!n) win_error("absolute I/O path");
        return n;
    });
    // Long paths need their ordinary extended DOS/UNC spelling for I/O. The
    // NT physical comparison name below is never passed to an inference API.
    if (full.size() >= 248) {
        full = full.rfind(L"\\\\", 0) == 0 ? L"\\\\?\\UNC\\" + full.substr(2) : L"\\\\?\\" + full;
    }
    const auto raw = CreateFileW(full.c_str(), FILE_READ_ATTRIBUTES,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, nullptr,
        OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, nullptr);
    if (raw == INVALID_HANDLE_VALUE) win_error("open physical path");
    Handle handle(raw);
    const auto physical = bounded_name([&](wchar_t* out, std::size_t size) {
        const auto n = GetFinalPathNameByHandleW(handle.get(), out, static_cast<DWORD>(size),
                                               FILE_NAME_NORMALIZED | VOLUME_NAME_NT);
        if (!n) win_error("physical NT path");
        return n;
    });
    if (physical.rfind(L"\\Device\\", 0) != 0)
        throw std::runtime_error("unexpected physical volume namespace");
    handle.close();
    return {std::filesystem::path(full), std::filesystem::path(physical)};
}
#endif
} // namespace detail

inline std::filesystem::path existing_io_path(const std::filesystem::path& path) {
#ifdef _WIN32
    return detail::resolve(path).io;
#else
    if (path.empty() || path.native().find('\0') != std::string::npos)
        throw std::invalid_argument("empty or NUL-containing path");
    return std::filesystem::canonical(path);
#endif
}

// Activation-local normalized target name, including the physical volume.
// This is not a durable file identifier, nor permission to open another path.
inline std::string physical_key(const std::filesystem::path& path) {
#ifdef _WIN32
    return detail::resolve(path).physical.generic_u8string();
#else
    return existing_io_path(path).generic_u8string();
#endif
}
} // namespace aii::platform
