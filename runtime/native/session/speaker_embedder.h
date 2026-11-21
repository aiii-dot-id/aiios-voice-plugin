#pragma once
#include "../../native_uid/identity.h"
#include <cstdint>
#include <memory>
#include <string>
#include <vector>

namespace aii::voice {
struct SpeakerModel;
struct SpeakerEmbedder {
  virtual ~SpeakerEmbedder() = default;
  virtual aii::uid::Vector embed(const std::vector<uint8_t>& pcm16, size_t dimensions) = 0;
  virtual void cancel() noexcept = 0;
  virtual void resume() = 0;
};
std::shared_ptr<SpeakerModel> load_speaker_model(const std::string& path, const std::string& backend,
                                                 const std::string& embedding_binding);
std::unique_ptr<SpeakerEmbedder> local_speaker_embedder(std::shared_ptr<SpeakerModel>);
}
