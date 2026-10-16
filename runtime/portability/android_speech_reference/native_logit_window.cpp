#include <jni.h>
#include <cstdint>
#include <vector>
#include "litert/cc/litert_tensor_buffer.h"

// Diagnostic only. Kotlin's handle is a C++ TensorBuffer*, NOT its C handle.
// Headers are bound to v2.2.0's commit and the exact Maven JNI/API libraries
// are checked by the builder. The owning decoder alone accesses this buffer;
// it closes it only after decode retires. No pointer escapes this invocation.
namespace {
void fail(JNIEnv* env, const char* text) {
    env->ThrowNew(env->FindClass("java/lang/IllegalStateException"), text);
}
}

extern "C" JNIEXPORT jfloatArray JNICALL
Java_com_google_ai_edge_examples_asr_NativeLogits_readWindow(
    JNIEnv* env, jclass, jobject object, jint offset, jint count, jint expected) {
    if (!object || offset < 0 || count <= 0 || expected <= 0 ||
        static_cast<int64_t>(offset) + count > expected || count > 8198) {
        fail(env, "Invalid logit window"); return nullptr;
    }
    jclass handle_class = env->FindClass("com/google/ai/edge/litert/JniHandle");
    if (!handle_class || env->ExceptionCheck()) return nullptr;
    jmethodID alive = env->GetMethodID(handle_class, "assertNotDestroyed", "()V");
    jfieldID handle = env->GetFieldID(handle_class, "handle", "J");
    if (!alive || !handle || env->ExceptionCheck()) return nullptr;
    env->CallVoidMethod(object, alive);
    if (env->ExceptionCheck()) return nullptr;
    auto* buffer = reinterpret_cast<litert::TensorBuffer*>(env->GetLongField(object, handle));
    if (!buffer || env->ExceptionCheck()) { fail(env, "Missing bound tensor"); return nullptr; }
    auto type = buffer->TensorType();
    if (!type) { fail(env, "Tensor type read failed"); return nullptr; }
    auto elements = type->Layout().NumElements();
    if (!elements || *elements != expected || type->ElementType() != litert::ElementType::Float32) {
        fail(env, "Bound tensor layout changed"); return nullptr;
    }
    std::vector<float> copied(count);
    auto locked = buffer->Lock(litert::TensorBuffer::LockMode::kRead);
    if (!locked) { fail(env, "Tensor read lock failed"); return nullptr; }
    // Copy only the requested token's vocabulary and duration logits to normal
    // memory. Check unlock before publishing any result to the decoder.
    const float* source = static_cast<const float*>(*locked) + offset;
    std::memcpy(copied.data(), source, copied.size() * sizeof(float));
    auto unlocked = buffer->Unlock();
    if (!unlocked) { fail(env, "Tensor read unlock failed"); return nullptr; }
    jfloatArray result = env->NewFloatArray(count);
    if (!result) return nullptr;
    env->SetFloatArrayRegion(result, 0, count, copied.data());
    return result;
}
