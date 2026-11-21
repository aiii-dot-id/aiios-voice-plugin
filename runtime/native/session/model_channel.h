#pragma once
#include "session.h"
#include "speaker_embedder.h"
#include "admission.h"
#include <chrono>
#include <cstdint>
#include <functional>
#include <memory>
#include <string>

namespace aii::voice {
enum class StreamKind : uint8_t { Speech = 1, Hearing = 2, Endpoint = 3, Speaker = 4 };

struct ChannelEnd { intptr_t in = -1, out = -1; };

struct StreamFactory {
  virtual ~StreamFactory() = default;
  virtual std::unique_ptr<Synthesizer> speech() = 0;
  virtual std::unique_ptr<Recognizer> recognizer() = 0;
  virtual std::unique_ptr<Endpoint> endpoint() = 0;
  virtual std::unique_ptr<SpeakerEmbedder> speaker() { throw std::logic_error("model server: no speaker model"); }
  virtual std::string describe() { return "{}"; }
  virtual Admission* admission() { return nullptr; }
};

using HungStream = std::function<void(const std::string& what)>;
void serve_model_channel(StreamFactory&, ChannelEnd,
                         std::chrono::milliseconds retire = std::chrono::milliseconds::zero(), HungStream hung = {});
size_t model_channel_unjoined_threads();

class ModelChannel {
 public:
  explicit ModelChannel(ChannelEnd);
  ~ModelChannel();
  ModelChannel(const ModelChannel&) = delete;
  ModelChannel& operator=(const ModelChannel&) = delete;
  std::unique_ptr<Synthesizer> speech();
  std::unique_ptr<Recognizer> recognizer();
  std::unique_ptr<Endpoint> endpoint();
  std::unique_ptr<SpeakerEmbedder> speaker();
  std::string describe();
  bool lost() const;
  Admission::Answer admit();
  void release();
  struct Impl;
 private:
  std::shared_ptr<Impl> p_;
};
}
