#pragma once
#include <cstddef>
#include <string>
#include <vector>
namespace aii::voice {
std::vector<std::string> split_text(const std::string&, size_t limit=180);
std::string strip_text(const std::string&);
}
