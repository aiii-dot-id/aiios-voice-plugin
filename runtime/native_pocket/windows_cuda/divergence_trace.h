#pragma once
// Isolated fixture build only. No installed ABI, model or arithmetic changes.
#include <cstdio>
#include <cstdint>
#include <cstring>
#include <stdexcept>
#include <string>
#include <vector>

inline void nv_trace_values(const char* stage, const std::vector<float>& values) {
    if (values.empty() || values.size() > 16384) throw std::runtime_error("trace vector bound");
    std::string line("NV_TRACE "); line += stage;
    line += " " + std::to_string(values.size());
    line.reserve(line.size() + values.size() * 9 + 1);
    for (float value : values) {
        uint32_t bits; std::memcpy(&bits, &value, sizeof(bits));
        char hex[10]; std::snprintf(hex, sizeof(hex), " %08x", unsigned(bits));
        line += hex;
    }
    line += '\n';
    if (std::fwrite(line.data(), 1, line.size(), stderr) != line.size())
        throw std::runtime_error("trace write failed");
}
