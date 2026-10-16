// Reuse the exact upstream descriptor and tensor loader in this translation
// unit. The compiled source is hash-bound by our builder. Only resource paths
// differ: model data stays read-only and configuration remains runtime-owned.
#include "assets.cpp"
#include "engine/models/pocket_tts/session.h"

std::unique_ptr<engine::runtime::IVoiceTaskSession> nv_bound_session(
    const std::filesystem::path & root, const std::filesystem::path & config,
    const engine::runtime::SessionOptions & options) {
    using namespace engine;
    assets::ResourceBundle resources(root);
    resources.add_file("tokenizer", root / "tokenizer.model");
    resources.add_file("config", config);
    resources.add_tensor_source("weights", root / "model.safetensors");
    auto manifest = std::make_shared<models::pocket_tts::PocketTTSAssets>(
        models::pocket_tts::load_assets_from_resources(std::move(resources), "english"));
    return std::make_unique<models::pocket_tts::PocketTTSSession>(
        runtime::TaskSpec{runtime::VoiceTaskKind::Tts, runtime::RunMode::Streaming},
        options, std::move(manifest), root);
}
