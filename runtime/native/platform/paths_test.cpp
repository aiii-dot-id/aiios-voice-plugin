#include "paths.h"
#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <fstream>
#include <iostream>

namespace fs = std::filesystem;
using aii::platform::existing_io_path;
using aii::platform::physical_key;

static void require(bool condition, const char* cause) {
    if (!condition) throw std::runtime_error(cause);
}
template<class F> static void refused(F action) {
    bool threw = false;
    try { action(); } catch (const std::exception&) { threw = true; }
    require(threw, "invalid path/query was accepted");
}
static void bounded_queries() {
    unsigned calls = 0;
    const std::wstring long_name(900, L'x');
    const auto name = aii::platform::detail::bounded_name([&](wchar_t* out, std::size_t cap) {
        ++calls;
        if (cap <= long_name.size()) return long_name.size() + 1;
        std::copy(long_name.begin(), long_name.end(), out);
        return long_name.size();
    });
    require(name == long_name && calls == 2, "bounded name growth failed");
    refused([] { aii::platform::detail::bounded_name([](wchar_t*, std::size_t) { return 65536; }); });
    refused([] { aii::platform::detail::bounded_name([](wchar_t*, std::size_t) { return 0; }); });
    bool denied = false;
    try {
        aii::platform::detail::bounded_name([](wchar_t*, std::size_t) -> unsigned {
            throw std::system_error(std::make_error_code(std::errc::permission_denied));
        });
    } catch (const std::system_error& error) {
        denied = error.code() == std::errc::permission_denied;
    }
    require(denied, "resolution failure lost its cause");
}

static void self_test() {
    bounded_queries();
    const auto suffix = std::chrono::steady_clock::now().time_since_epoch().count();
    const auto root = fs::temp_directory_path() / ("aii-native-paths-" + std::to_string(suffix));
    require(fs::create_directory(root), "test directory already exists");
    struct Cleanup { fs::path path; ~Cleanup() { std::error_code e; fs::remove_all(path, e); } } cleanup{root};
    const auto file = root / "words.txt";
    { std::ofstream out(file); out << "retained"; require(out.good(), "fixture write failed"); }
    const auto io = existing_io_path(file);
    std::string text;
    { std::ifstream in(io); in >> text; }
    require(text == "retained", "resolved I/O path cannot read the original file");
    require(physical_key(file) == physical_key(root / "." / "words.txt"), "lexical aliases differ");
    refused([&] { existing_io_path(root / "missing"); });
    refused([&] { existing_io_path(fs::path(std::string("bad\0path", 8))); });
    refused([] { existing_io_path(fs::path{}); });
    unsigned symlink_skipped = 0;
    std::error_code link_error;
    fs::create_symlink(file, root / "alias", link_error);
    if (link_error) {
#ifdef _WIN32
        symlink_skipped = 1; // separately named; ordinary accounts may lack this privilege.
#else
        throw std::system_error(link_error);
#endif
    } else {
        require(physical_key(root / "alias") == physical_key(file), "target alias differs");
        const auto prior = physical_key(root / "alias");
        { std::ofstream out(root / "other"); out << "other"; }
        fs::remove(root / "alias"); fs::create_symlink(root / "other", root / "alias");
        require(physical_key(root / "alias") != prior, "retarget reused stale physical identity");
    }
#ifdef _WIN32
    refused([] { existing_io_path(L"\\\\?\\GLOBALROOT\\Device\\HarddiskVolume1"); });
    refused([] { existing_io_path(L"\\\\.\\C:\\"); });
    DWORD before = 0, after = 0;
    require(GetProcessHandleCount(GetCurrentProcess(), &before), "initial handle count failed");
    for (int i = 0; i < 100; ++i) {
        require(physical_key(file) == physical_key(io), "physical key drift");
        refused([&] { existing_io_path(root / "missing"); });
    }
    require(GetProcessHandleCount(GetCurrentProcess(), &after) && before == after, "physical handles leaked");
#endif
    std::cout << "native-paths: passed; symlink-skipped=" << symlink_skipped << '\n';
}

// Test utility only, never a voice engine. Under the unchanged wall harness,
// demonstrate the original canonicalization denial and the corrected file read.
static void wall_probe() {
#ifdef _WIN32
    const auto models = aii::platform::detail::bounded_name([](wchar_t* out, std::size_t cap) {
        const auto n = GetEnvironmentVariableW(L"AII_MODELS_DIR", out, static_cast<DWORD>(cap));
        if (!n) aii::platform::detail::win_error("host model path missing");
        return n;
    });
    const auto file = fs::path(models) / "tts" / "tokenizer.model";
    bool old_denied = false;
    try { (void)fs::weakly_canonical(file); }
    catch (const fs::filesystem_error& error) { old_denied = error.code().value() == ERROR_ACCESS_DENIED; }
    require(old_denied, "original weakly_canonical denial did not reproduce");
    const auto path = existing_io_path(file);
    char head[4]{};
    std::ifstream input(path, std::ios::binary);
    input.read(head, sizeof(head));
    require(input.gcount() == sizeof(head), "sandbox-compatible path cannot read tokenizer");
    require(physical_key(file) == physical_key(path), "I/O and physical identity disagree");
    std::cerr << "NATIVE_PATH_PROBE_PASS baseline-denied=true bounded-handle-path-readable=true\n";
    std::cout << "AII_VOICE_READY\n" << std::flush;
    while (std::cin.get() != EOF) {}
#else
    throw std::runtime_error("wall utility is Windows-only");
#endif
}
int main(int argc, char** argv) {
    try {
        if (argc == 2 && std::string(argv[1]) == "--self-test") self_test();
        else if (argc == 1) wall_probe();
        else throw std::runtime_error("unexpected native path test arguments");
        return 0;
    } catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
}
