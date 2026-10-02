#pragma once
#include <cstddef>
#include <string>
#include <vector>
namespace aii::voice {
// Strict UTF-8; same 8,000-character and 32..512 segment limits as the
// existing speech service. Concatenating every part returns the exact input.
std::vector<std::string> split_text(const std::string&, size_t limit=180);
std::string strip_text(const std::string&);
}
