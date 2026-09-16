// Public-fixture PCM adapter. No microphone/device routing, neural CPU fallback,
// private LiteRT ABI, or borrowed Kotlin model handles. Reuses the tested native
// frontend and the same cancellation fence as the existing native recognizer.
#include <jni.h>
#include <map>
#include <memory>
#include <mutex>
#include "length_pcm_frontend.h"
#include "native_generation_gate.h"

namespace {
using namespace aii::voice::pixel;
struct Owner {
    LengthPcmFrontend frontend;
    GenerationGate gate;
    std::mutex processing;
    explicit Owner(std::vector<float> mel):frontend(std::move(mel)) {}
};
std::mutex registry_mutex;
std::map<jlong,std::shared_ptr<Owner>> registry;
jlong next_owner=0;
std::shared_ptr<Owner> owner(jlong h) {
    std::lock_guard<std::mutex> guard(registry_mutex);
    const auto found=registry.find(h);
    if(found==registry.end())throw std::runtime_error("native PCM owner retired");
    return found->second;
}
void fail(JNIEnv* env,const std::exception& error) {
    env->ThrowNew(env->FindClass("java/lang/IllegalStateException"),error.what());
}
uint64_t generation(jlong g) {
    if(g<=0)throw std::invalid_argument("positive generation required");
    return static_cast<uint64_t>(g);
}
std::vector<float> floats(JNIEnv* env,jfloatArray a,size_t minimum,size_t maximum) {
    if(!a)throw std::invalid_argument("float input missing");
    const auto size=env->GetArrayLength(a);
    if(size<0 || size_t(size)<minimum || size_t(size)>maximum)
        throw std::invalid_argument("float input extent refused");
    std::vector<float> values(size);
    env->GetFloatArrayRegion(a,0,size,values.data());
    if(env->ExceptionCheck())throw std::runtime_error("float input copy failed");
    return values;
}
}
extern "C" JNIEXPORT jlong JNICALL Java_com_google_ai_edge_examples_asr_NativeLengthPcm_open(JNIEnv* env,jclass,jfloatArray mel) {
    try {
        auto instance=std::make_shared<Owner>(floats(env,mel,128*257,128*257));
        std::lock_guard<std::mutex> guard(registry_mutex);
        if(next_owner==INT64_MAX)throw std::runtime_error("native PCM ids exhausted");
        const auto id=++next_owner;registry.emplace(id,std::move(instance));return id;
    } catch(const std::exception& error){fail(env,error);return 0;}
}
extern "C" JNIEXPORT void JNICALL Java_com_google_ai_edge_examples_asr_NativeLengthPcm_begin0(JNIEnv* env,jclass,jlong h,jlong g) {
    try {auto instance=owner(h);
        std::unique_lock<std::mutex> guard(instance->processing,std::try_to_lock);
        if(!guard.owns_lock())throw std::runtime_error("native PCM owner busy");
        instance->gate.admit(generation(g));
    }catch(const std::exception& error){fail(env,error);}
}
extern "C" JNIEXPORT void JNICALL Java_com_google_ai_edge_examples_asr_NativeLengthPcm_check0(JNIEnv* env,jclass,jlong h,jlong g) {
    try {owner(h)->gate.check(generation(g));}catch(const std::exception& error){fail(env,error);}
}
extern "C" JNIEXPORT void JNICALL Java_com_google_ai_edge_examples_asr_NativeLengthPcm_cancel0(JNIEnv* env,jclass,jlong h,jlong g) {
    try {owner(h)->gate.cancel(generation(g));}catch(const std::exception& error){fail(env,error);}
}
extern "C" JNIEXPORT jfloatArray JNICALL Java_com_google_ai_edge_examples_asr_NativeLengthPcm_process0(JNIEnv* env,jclass,jlong h,jlong g,jfloatArray a) {
    try {
        auto instance=owner(h);const auto id=generation(g);
        std::unique_lock<std::mutex> guard(instance->processing,std::try_to_lock);
        if(!guard.owns_lock())throw std::runtime_error("native PCM owner busy");
        instance->gate.check(id);
        const auto pcm=floats(env,a,320,LengthPcmFrontend::capacity);
        auto result=instance->frontend.process(pcm,[&]{instance->gate.check(id);});
        instance->gate.check(id);
        auto out=env->NewFloatArray(result.size());
        if(out)env->SetFloatArrayRegion(out,0,result.size(),result.data());
        return out; // Receiving inference thread checks again before any use.
    }catch(const std::exception& error){fail(env,error);return nullptr;}
}
extern "C" JNIEXPORT void JNICALL Java_com_google_ai_edge_examples_asr_NativeLengthPcm_retire(JNIEnv*,jclass,jlong h) {
    std::shared_ptr<Owner> instance;
    {std::lock_guard<std::mutex> guard(registry_mutex);const auto found=registry.find(h);
     if(found==registry.end())return;instance=std::move(found->second);registry.erase(found);}
    instance->gate.close(); // Pinned calls retain lifetime, but cannot publish.
}
