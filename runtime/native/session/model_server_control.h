#pragma once
#include "model_channel.h"
#include <cstddef>

namespace aii::voice {
constexpr size_t model_server_channel_limit = 64;

#ifdef _WIN32
bool send_model_channel(ChannelEnd control, void* server, ChannelEnd theirs);
#else
bool send_model_channel(int control, ChannelEnd theirs);
#endif

void serve_model_server(StreamFactory&, ChannelEnd control,
                        std::chrono::milliseconds retire = std::chrono::milliseconds::zero(), HungStream hung = {});
#ifndef _WIN32
inline void serve_model_server(StreamFactory& factory, int control,
                               std::chrono::milliseconds retire = std::chrono::milliseconds::zero(), HungStream hung = {}) {
  serve_model_server(factory, ChannelEnd{control, control}, retire, std::move(hung));
}
#endif
}
