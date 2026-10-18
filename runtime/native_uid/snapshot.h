#pragma once
#include "identity.h"
#include <string_view>
namespace aii::uid {
struct PolicyDocument { Policy policy; std::string canonical; };
PolicyDocument read_policy(const std::string&);
Snapshot read_snapshot(const std::string&,const PolicyDocument&);
std::string write_snapshot(const Snapshot&,const PolicyDocument&);
std::string encode_base64(std::string_view);
std::string decode_base64(std::string_view,size_t maximum);
std::string encode_vector(const Vector&);
Vector decode_vector(std::string_view,size_t dimensions=256);
}
