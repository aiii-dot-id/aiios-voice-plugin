from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_build_is_cuda_only_and_compiles_only_the_selected_model():
    s=(ROOT/'runtime/native_pocket/windows_cuda/CMakeLists.txt').read_text()
    assert 'STREQUAL "61-real"' in s
    for declaration in ['set(ENGINE_ENABLE_CUDA ON','set(ENGINE_ENABLE_VULKAN OFF','set(AUDIOCPP_MODEL_SET custom','set(AUDIOCPP_MODELS pocket_tts','set(GGML_BACKEND_DL OFF']:
        assert declaration in s
