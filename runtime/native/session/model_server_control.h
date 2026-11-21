#pragma once
#include "model_channel.h"
#include <cstddef>
#include <cstdint>
#include <functional>
#include <stdexcept>
#include <string>
#include <vector>

namespace aii::voice {
constexpr size_t model_server_channel_ceiling = 64;

struct DescriptorBudget {
  std::uint64_t open = 0, need = 0, soft = 0, hard = 0;
  bool raised = false;
};
constexpr std::uint64_t model_server_descriptors_per_channel = 6, model_server_descriptors_per_message = 8;
struct DescriptorShortfall : std::runtime_error {
  using std::runtime_error::runtime_error;
};
constexpr std::uint64_t descriptor_scan_bound = 65536;
struct DescriptorCount {
  std::uint64_t open = 0;
  bool complete = false;
  std::uint64_t scanned = 0, free_below = 0;
};
#ifndef _WIN32
std::uint64_t descriptor_scan_limit(std::uint64_t soft);
DescriptorCount count_from_listing(const std::vector<std::string>& names, long own, bool read_failed);
DescriptorCount count_descriptors(bool listing = true);
DescriptorBudget budget_for(const DescriptorCount& count, std::uint64_t soft, std::uint64_t hard, size_t channels);
DescriptorBudget reserve_model_server_descriptors(size_t channels);
#endif
void reserve_then_ready(size_t channels, const std::function<void(const DescriptorBudget&)>& ready);

#ifdef _WIN32
bool send_model_channel(ChannelEnd control, void* server, ChannelEnd theirs);
#else
bool send_model_channel(int control, ChannelEnd theirs);
#endif

void serve_model_server(StreamFactory&, ChannelEnd control, size_t channels,
                        std::chrono::milliseconds retire = std::chrono::milliseconds::zero(), HungStream hung = {});
#ifndef _WIN32
inline void serve_model_server(StreamFactory& factory, int control, size_t channels,
                               std::chrono::milliseconds retire = std::chrono::milliseconds::zero(), HungStream hung = {}) {
  serve_model_server(factory, ChannelEnd{control, control}, channels, retire, std::move(hung));
}
#endif
}
