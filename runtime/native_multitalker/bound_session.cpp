#include "bound_session.h"
#include "../native/platform/readonly_model.h"
#include <array>
#include <memory>
#include <stdexcept>
#include <string_view>
#include <vector>

namespace aii::multitalker {
namespace {
struct Binding { const char* name; uint64_t size; const char* hash; };
// Exact exported graphs and external data in the release model declaration.
// No filename or traversal supplied by an ONNX graph is opened by ORT.
constexpr std::array<Binding,6> graphs{{
  {"asr_decoder",28883328,"73045a7aad5d142460070fd4f7190a49dee4d12bb6087e10349f3e8e1e34354d"},
  {"asr_encoder",2358302,"1e4f5e1ec39f4b77f65dc43b96ba4546bb3f45ec74bed45d26b0456aca88f558"},
  {"asr_joiner",6894734,"51682f5269ac61607c5ec136ca50a4d2297aaf98ad409da0de22eeb8b18b838d"},
  {"asr_preencode",18466234,"f51eb910c441807f2f35dc35e0d1991fb3838fca7f2f68e4b1b0abb21b8f21af"},
  {"diar_preencode",9019445,"7e2174823805aae87c89c3c0639f9230269f9a8679dc0cf7623b251490c5870c"},
  {"diar_classifier",483229256,"8fd6788a2e6c7dc03741820b4b3e565725afc16785fea619f6fd11ad2375e0e0"},
}};
constexpr std::array<Binding,5> shards{{
  {"weights-000.bin",528379904,"b0318093ada916e8e85272c8d6c2089a0784037a5344754b09c232cd11df2b01"},
  {"weights-001.bin",536870912,"076583211d27d17bbdb6207dbb9bcea4b038e1f50e9eb20b7556926085932430"},
  {"weights-002.bin",536870912,"17e8c1009169e915658b90505780d5d560293c85bf3f25fb624ddc844b7ea395"},
  {"weights-003.bin",536870912,"f34239e95da916d022744d254b4eba1453ff2a9e4b381a8ffadd5c214bb50542"},
  {"weights-004.bin",335544320,"85a0ecdf74e8f0bae4be0e8090a7437521438feedbe2d4d6291b177a52b64090"},
}};
}
Ort::Session bound_session(Ort::Env& env, const std::string& root,
                           const char* model, const Ort::SessionOptions& original) {
  const Binding* binding=nullptr;
  for (const auto& b:graphs) if (std::string_view(b.name)==model) binding=&b;
  if (!binding) throw std::invalid_argument("unbound hearing graph");
  // Native profile JSON paths are UTF-8, including non-ASCII user directories.
  const auto directory=std::filesystem::u8path(root)/binding->name;
  platform::ReadonlyModel graph(directory/"model.onnx",binding->size,binding->hash);
  std::vector<std::unique_ptr<platform::ReadonlyModel>> mappings;
  std::vector<std::basic_string<ORTCHAR_T>> names;
  std::vector<char*> buffers;
  std::vector<size_t> lengths;
  auto options=original.Clone();
  if (std::string_view(model)=="asr_encoder") {
    for (const auto& b:shards) {
      mappings.push_back(std::make_unique<platform::ReadonlyModel>(directory/b.name,b.size,b.hash));
      auto& mapped=*mappings.back();
      names.push_back(std::filesystem::path(b.name).native());
      // ORT's API is non-const, but copies external data into graph-owned
      // tensors. The mapping remains read-only and alive through construction.
      buffers.push_back(reinterpret_cast<char*>(const_cast<unsigned char*>(mapped.data())));
      lengths.push_back(mapped.size());
    }
    options.AddExternalInitializersFromFilesInMemory(names,buffers,lengths);
  }
  Ort::Session session(env,graph.data(),graph.size(),options);
  graph.check_unchanged();
  for (const auto& mapped:mappings) mapped->check_unchanged();
  return session;
}
}
